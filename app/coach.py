"""Optional DeepSeek coaching. Sends computed metrics only, never secrets."""

from __future__ import annotations

import json
import os
from typing import Any

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

SAMPLE_REASON = "示例数据只在本机计算，不会发给 DeepSeek。"
MISSING_REASON = "未配置 DeepSeek API Key，模型建议不可用。本地计算的报告仍然有效。"
FAILED_REASON = "模型建议不可用（请求失败）。本地计算的报告仍然有效。"
EMPTY_REASON = "模型建议不可用（空回复）。本地计算的报告仍然有效。"
PENDING_REASON = "正在根据已计算的指标向 DeepSeek 请求建议…"
CHAT_SAMPLE_REASON = "当前是示例数据，教练对话不会发给 DeepSeek。请先同步佳明数据。"
CHAT_MISSING_REASON = "未配置 DeepSeek API Key。请到「设置」里保存密钥后再问教练。"
CHAT_FAILED_REASON = "教练回复失败。请稍后再试；本地报告仍然可用。"
CHAT_EMPTY_REASON = "教练没有返回内容。请换个问法再试。"

SYSTEM_PROMPT = (
    "你是耐力运动的训练顾问。用户消息是本地已经算好的指标 JSON，不是原始账号，也不是医疗记录。"
    "请用简体中文写 4 到 7 条具体建议，每条都引用 JSON 里的数字。"
    "不要编造 JSON 里没有的测量值，不要索要账号、密码或密钥，不要把建议说成诊断。"
    "null 表示没有这项数据。负荷单位以 load_method_label 为准，不要把 CTL 说成 TrainingPeaks 的 TSS，"
    "除非原文明确写了 TSS。"
)

CHAT_SYSTEM_PROMPT = (
    "你是耐力运动的本地 AI 教练。用户会基于本机已算好的生理与训练摘要提问。"
    "请始终用简体中文回答。"
    "当用户问当天计划或今日练什么时，给出可执行的当天安排，并引用摘要里的 CTL/ATL/TSB/ACWR、"
    "近期 RHR/HRV/睡眠/Body Battery/压力与训练量数字。"
    "当用户问一周计划时，给出 7 天结构（含休息日），强度与量要贴合当前疲劳与恢复信号。"
    "不要编造摘要里没有的测量值；null/缺失就明说数据不足。"
    "不要索要或重复账号、密码、API Key、令牌；不要把建议说成医疗诊断。"
    "负荷口径以摘要中的「负荷口径」为准；不要把佳明活动训练负荷说成 TrainingPeaks 的 TSS，"
    "除非摘要明确写了 TSS。"
    "每条关键建议后附一句简短免责声明：这是训练启发式，不是医疗意见。"
)

MAX_CHAT_USER_CHARS = 2000
MAX_CHAT_HISTORY_TO_MODEL = 8

SUMMARY_KEYS = (
    "ctl",
    "atl",
    "tsb",
    "acwr",
    "acute_load",
    "chronic_load",
    "history_days",
    "load_method",
    "load_method_label",
    "weekly_hours",
    "previous_weekly_hours",
    "weekly_km",
    "previous_weekly_km",
    "weekly_load",
    "rhr_7d",
    "rhr_baseline",
    "rhr_delta",
    "hrv_3d",
    "hrv_baseline",
    "hrv_ratio",
    "hrv_last",
    "hrv_band_low",
    "hrv_band_high",
    "sleep_7d_hours",
    "sleep_last_hours",
    "body_battery",
    "stress_7d",
    "steps_7d",
    "hr_max",
    "hr_max_source",
)

GARMIN_KEYS = (
    "vo2max_running",
    "vo2max_cycling",
    "training_status_label",
    "weekly_training_load",
    "load_tunnel_min",
    "load_tunnel_max",
    "acute_load",
    "chronic_load",
)


class CoachError(Exception):
    pass


def network_allowed() -> bool:
    return os.environ.get("GARMIN_FITNESS_DISABLE_NETWORK") != "1"


def build_coach_payload(report: dict) -> dict:
    """Whitelist computed fields. Drop anything that could carry an account."""
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    garmin = summary.get("garmin") if isinstance(summary.get("garmin"), dict) else {}
    intensity = summary.get("intensity") if isinstance(summary.get("intensity"), dict) else None
    payload = {
        "window": report.get("window"),
        "summary": {key: summary.get(key) for key in SUMMARY_KEYS},
        "garmin_device": {key: garmin.get(key) for key in GARMIN_KEYS},
        "intensity": None
        if not intensity
        else {
            "low": intensity.get("low"),
            "mid": intensity.get("mid"),
            "high": intensity.get("high"),
            "minutes": intensity.get("minutes"),
            "method_label": intensity.get("method_label"),
        },
        "flags": [
            {"id": item.get("id"), "level": item.get("level"), "title": item.get("title"), "detail": item.get("detail")}
            for item in report.get("flags") or []
            if isinstance(item, dict)
        ],
        "suggestions": [line for line in report.get("suggestions") or [] if isinstance(line, str) and "@" not in line],
        "notes": [line for line in report.get("notes") or [] if isinstance(line, str) and "@" not in line],
    }
    return payload


def _post_chat(api_key: str, messages: list[dict[str, str]], *, temperature: float = 0.3) -> str:
    if not network_allowed():
        raise CoachError("当前环境禁止访问网络")
    import httpx

    body = {
        "model": DEEPSEEK_MODEL,
        "temperature": temperature,
        "messages": messages,
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
            )
    except Exception as exc:
        raise CoachError("请求失败") from exc
    if response.status_code != 200:
        raise CoachError(f"HTTP {response.status_code}")
    try:
        data = response.json()
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise CoachError("回复无法解析") from exc
    return str(text or "").strip()


def complete(api_key: str, payload: dict) -> str:
    return _post_chat(
        api_key,
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "下面是已经算好的指标 JSON。请只根据这些数字写建议。\n"
                + json.dumps(payload, ensure_ascii=False),
            },
        ],
        temperature=0.3,
    )


def sanitize_user_message(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) > MAX_CHAT_USER_CHARS:
        cleaned = cleaned[:MAX_CHAT_USER_CHARS].rstrip()
    return cleaned


def chat_complete(
    api_key: str,
    *,
    context: dict,
    history: list[dict],
    user_message: str,
) -> str:
    """Multi-turn coach reply. Context is injected each turn; secrets never included."""
    message = sanitize_user_message(user_message)
    if not message:
        raise CoachError("请先输入问题")
    messages: list[dict[str, str]] = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    messages.append(
        {
            "role": "user",
            "content": (
                "下面是本机压缩后的训练与生理摘要 JSON（不是原始活动列表）。"
                "之后的对话请只依据这些数字与用户问题作答。\n"
                + json.dumps(context, ensure_ascii=False)
            ),
        }
    )
    messages.append(
        {
            "role": "assistant",
            "content": "已收到摘要。我会用简体中文回答，引用其中的数字，并在安全相关建议后注明这不是医疗诊断。",
        }
    )
    trimmed: list[dict[str, Any]] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"}:
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        trimmed.append({"role": role, "content": content.strip()[:MAX_CHAT_USER_CHARS]})
    if len(trimmed) > MAX_CHAT_HISTORY_TO_MODEL:
        trimmed = trimmed[-MAX_CHAT_HISTORY_TO_MODEL:]
    messages.extend(trimmed)
    messages.append({"role": "user", "content": message})
    return _post_chat(api_key, messages, temperature=0.4)


def offline_coaching(sample: bool, api_key: str | None, *, pending: bool = False) -> dict:
    if sample:
        return {"available": False, "text": None, "reason": SAMPLE_REASON}
    if not api_key:
        return {"available": False, "text": None, "reason": MISSING_REASON}
    if pending:
        return {"available": False, "text": None, "reason": PENDING_REASON}
    return {
        "available": False,
        "text": None,
        "reason": "本次响应没有调用模型。已同步的真实数据会在页面里单独请求 DeepSeek。",
    }


def coaching_for(sample: bool, report: dict, api_key: str | None) -> dict:
    """Call DeepSeek only for a real synced bundle when a key exists."""
    if sample:
        return offline_coaching(True, None)
    if not api_key:
        return offline_coaching(False, None)
    try:
        text = complete(api_key, build_coach_payload(report))
    except CoachError as exc:
        reason = FAILED_REASON if str(exc) == "请求失败" else f"模型建议不可用（{exc}）。本地计算的报告仍然有效。"
        return {"available": False, "text": None, "reason": reason}
    except Exception:
        return {"available": False, "text": None, "reason": FAILED_REASON}
    if not text:
        return {"available": False, "text": None, "reason": EMPTY_REASON}
    return {"available": True, "text": text, "reason": None}
