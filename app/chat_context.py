"""Compressed coaching context for DeepSeek chat.

Builds a structured Chinese+JSON summary from computed aggregates only.
Never includes raw activity lists, full daily series, or secrets.
"""

from __future__ import annotations

from typing import Any

from app.analysis import analyze
from app.clock import iso_sh, today_sh

RECENT_SESSIONS_CAP = 5
CONTEXT_DOC = (
    "本上下文已压缩：只含负荷/生理聚合、近7天与近28天量、警报、最多 "
    f"{RECENT_SESSIONS_CAP} 条最近课次的标题与负荷，以及本机个人画像最新摘要与近期时间线。"
    "不含原始活动列表、不含逐日全序列、不含账号/密码/API Key。"
)


def _num(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    return value


def _volume_block(summary: dict, *, hours_key: str, km_key: str, load_key: str) -> dict:
    return {
        "小时": _num(summary.get(hours_key)),
        "公里": _num(summary.get(km_key)),
        "负荷合计": _num(summary.get(load_key)),
    }


def _intensity_block(intensity: dict | None) -> dict | None:
    if not isinstance(intensity, dict):
        return None
    return {
        "低占比": _num(intensity.get("low")),
        "中占比": _num(intensity.get("mid")),
        "高占比": _num(intensity.get("high")),
        "计入分钟": _num(intensity.get("minutes")),
        "口径": intensity.get("method_label"),
    }


def _recent_sessions(bundle: dict, *, cap: int = RECENT_SESSIONS_CAP) -> list[dict]:
    rows: list[tuple[str, dict]] = []
    for raw in bundle.get("activities") or []:
        if not isinstance(raw, dict):
            continue
        stamp = str(raw.get("date") or "")[:10]
        if len(stamp) < 10:
            continue
        name = str(raw.get("name") or "未命名").strip()[:40] or "未命名"
        load = raw.get("training_load")
        try:
            load_value = None if load is None else round(float(load), 1)
        except (TypeError, ValueError):
            load_value = None
        sport = str(raw.get("sport") or raw.get("type") or "").strip()[:24] or None
        rows.append(
            (
                stamp,
                {
                    "日期": stamp,
                    "名称": name,
                    "负荷": load_value,
                    "运动": sport,
                },
            )
        )
    rows.sort(key=lambda item: item[0], reverse=True)
    # Newest first, capped; drop helper key.
    out = []
    for _, row in rows[: max(0, cap)]:
        if row.get("运动") is None:
            row = {k: v for k, v in row.items() if k != "运动"}
        out.append(row)
    return out


def build_chat_context(
    bundle: dict,
    *,
    recent_cap: int = RECENT_SESSIONS_CAP,
    athlete_profile: dict | None = None,
) -> dict[str, Any]:
    """Return the structured payload sent to the model (no secrets)."""
    from app.athlete_profile import coach_profile_summary

    report28 = analyze(bundle, range_key="30")
    report7 = analyze(bundle, range_key="7")
    summary28 = report28.get("summary") if isinstance(report28.get("summary"), dict) else {}
    summary7 = report7.get("summary") if isinstance(report7.get("summary"), dict) else {}
    # Prefer full-history PMC numbers from the longer window (same end date).
    summary = summary28 or summary7
    intensity7 = _intensity_block(summary7.get("intensity") if isinstance(summary7, dict) else None)
    intensity28 = _intensity_block(summary.get("intensity") if isinstance(summary, dict) else None)
    flags = []
    for item in report28.get("flags") or []:
        if not isinstance(item, dict):
            continue
        flags.append(
            {
                "级别": item.get("level"),
                "标题": item.get("title"),
                "说明": item.get("detail"),
                "id": item.get("id"),
            }
        )
    window = report28.get("window") if isinstance(report28.get("window"), dict) else {}
    sample = bool(bundle.get("sample"))
    context = {
        "摘要说明": CONTEXT_DOC,
        "生成时间": iso_sh(),
        "数据来源": "示例数据（非佳明账号）" if sample else "本机佳明同步缓存",
        "同步时间": None if sample else bundle.get("fetched_at"),
        "历史跨度天": summary.get("history_days"),
        "窗口": {
            "近28天起": window.get("start"),
            "截止": window.get("end"),
            "今天": today_sh().isoformat(),
        },
        "负荷状态": {
            "CTL": _num(summary.get("ctl")),
            "ATL": _num(summary.get("atl")),
            "TSB": _num(summary.get("tsb")),
            "ACWR": _num(summary.get("acwr")),
            "急性负荷7日均": _num(summary.get("acute_load")),
            "慢性负荷28日均": _num(summary.get("chronic_load")),
            "负荷口径": summary.get("load_method_label"),
        },
        "生理对比基线": {
            "静息心率_近7日": _num(summary.get("rhr_7d")),
            "静息心率_基线": _num(summary.get("rhr_baseline")),
            "静息心率_差值": _num(summary.get("rhr_delta")),
            "静息心率_升高标记": bool(summary.get("rhr_elevated")),
            "HRV_近3次": _num(summary.get("hrv_3d")),
            "HRV_基线中位数": _num(summary.get("hrv_baseline")),
            "HRV_比值": _num(summary.get("hrv_ratio")),
            "HRV_最近一夜": _num(summary.get("hrv_last")),
            "HRV_个人带下沿": _num(summary.get("hrv_band_low")),
            "HRV_个人带上沿": _num(summary.get("hrv_band_high")),
            "HRV_受抑标记": bool(summary.get("hrv_suppressed")),
            "睡眠_近7日小时": _num(summary.get("sleep_7d_hours")),
            "睡眠_最近一夜小时": _num(summary.get("sleep_last_hours")),
            "睡眠_偏短标记": bool(summary.get("sleep_short")),
            "BodyBattery_最近": _num(summary.get("body_battery")),
            "压力_近7日": _num(summary.get("stress_7d")),
            "步数_近7日均": _num(summary.get("steps_7d")),
        },
        "训练量": {
            "近7天": {
                **_volume_block(
                    summary7,
                    hours_key="weekly_hours",
                    km_key="weekly_km",
                    load_key="weekly_load",
                ),
                "强度": intensity7,
            },
            "近28天": {
                **_volume_block(
                    summary,
                    hours_key="range_hours",
                    km_key="range_km",
                    load_key="range_load",
                ),
                "强度": intensity28,
            },
            "再往前7天对照": {
                "小时": _num(summary.get("previous_weekly_hours")),
                "公里": _num(summary.get("previous_weekly_km")),
            },
        },
        "关键警报": flags[:12],
        "最近几课": _recent_sessions(bundle, cap=recent_cap),
        "本地建议摘要": [
            line for line in (report28.get("suggestions") or [])[:6] if isinstance(line, str) and "@" not in line
        ],
    }
    personal = coach_profile_summary(athlete_profile)
    if personal:
        context["个人描述"] = personal
    return context


def context_has_forbidden_keys(payload: dict) -> list[str]:
    """Test helper: scan serialized context for secret-ish field names."""
    text = str(payload).lower()
    hits = []
    for needle in (
        "password",
        "personal_api_key",
        "api_key",
        "authorization",
        "bearer",
        "access_token",
        "deepseek",
    ):
        if needle in text:
            hits.append(needle)
    return hits
