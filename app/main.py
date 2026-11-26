"""Local page for Garmin fitness and health analysis. Binds to 127.0.0.1."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response

from app import garmin_sync, storage
from app.analysis import analyze, normalize_range_key
from app.athlete_profile import apply_athlete_profile_update, public_athlete_profile
from app.chat_context import build_chat_context
from app.clock import iso_sh
from app.coach import (
    CHAT_EMPTY_REASON,
    CHAT_FAILED_REASON,
    CHAT_MISSING_REASON,
    CHAT_SAMPLE_REASON,
    CoachError,
    chat_complete,
    coaching_for,
    offline_coaching,
    sanitize_user_message,
)
from app.preview import metric_domains, render_report_html
from app.sample_data import build_sample_bundle

TEMPLATE = Path(__file__).resolve().parent / "templates" / "index.html"


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"

    def render(self, content) -> bytes:
        return json.dumps(content, ensure_ascii=False, allow_nan=False).encode("utf-8")


app = FastAPI(title="佳明体能健康分析", docs_url=None, redoc_url=None, default_response_class=UTF8JSONResponse)


@app.exception_handler(storage.StorageError)
async def storage_error(_request, exc: storage.StorageError):
    return UTF8JSONResponse({"ok": False, "message": str(exc)}, status_code=400)


def _view() -> dict:
    user = storage.active_user()
    bundle = None
    if user:
        bundle = storage.load_bundle(user["id"])
    if bundle:
        return {"viewing": "garmin", "bundle": bundle, "user": user}
    return {"viewing": "sample", "bundle": build_sample_bundle(), "user": user}


def _status(view: dict | None = None) -> dict:
    view = view or _view()
    user = view["user"]
    public = storage.public_user(user) if user else None
    synced = None
    garmin_name = None
    if view["viewing"] == "garmin":
        synced = view["bundle"].get("fetched_at")
        garmin_name = (view["bundle"].get("profile") or {}).get("garmin_name")
    sync = storage.sync_state_for_user(user["id"] if user else None)
    if synced and not sync.get("last_sync_at"):
        sync = {**sync, "last_sync_at": synced}
    return {
        "viewing": view["viewing"],
        "active_user": public,
        "users": storage.list_users(),
        "deepseek": storage.deepseek_source(),
        "last_sync": synced or sync.get("last_sync_at"),
        "garmin_name": garmin_name,
        "data_dir": str(storage.data_dir()),
        "generated_at": iso_sh(),
        "sync": sync,
        "sync_policy": storage.get_sync_policy(),
        "deepseek_ready": storage.deepseek_source() != "missing",
        "coach": storage.coach_state_for_user(user["id"] if user else None),
        "coach_policy": storage.get_coach_policy(),
        "athlete_profile": (
            public_athlete_profile(storage.load_athlete_profile(user["id"])) if user else public_athlete_profile(None)
        ),
    }


def _report_for(view: dict, range_key: str | None = "90") -> dict:
    report = analyze(view["bundle"], range_key=range_key)
    report["viewing"] = view["viewing"]
    report["domains"] = metric_domains(report.get("summary") or {}, report.get("range_label"))
    if view["user"]:
        report["active_user_label"] = view["user"].get("display_name") or view["user"].get("label")
    else:
        report["active_user_label"] = None
    return report


_PLOTLY_JS = ""


def _plotly_js_path() -> str:
    global _PLOTLY_JS
    if _PLOTLY_JS and Path(_PLOTLY_JS).is_file():
        return _PLOTLY_JS
    try:
        import plotly
    except ImportError:
        return ""
    root = Path(plotly.__file__).resolve().parent
    candidates = [
        root / "package_data" / "plotly.min.js",
        root / "package_data" / "plotly.js",
    ]
    for path in candidates:
        if path.is_file() and path.stat().st_size > 10_000:
            _PLOTLY_JS = str(path)
            return _PLOTLY_JS
    for path in sorted(root.rglob("plotly.min.js")):
        if path.is_file() and path.stat().st_size > 10_000:
            _PLOTLY_JS = str(path)
            return _PLOTLY_JS
    return ""


def _page(range_key: str = "90") -> str:
    view = _view()
    report = _report_for(view, range_key=range_key)
    sample = bool(view["bundle"].get("sample"))
    api_key = "" if sample else storage.get_api_key()
    fetch_coach = (not sample) and bool(api_key)
    coach = offline_coaching(sample, api_key, pending=fetch_coach)
    report_html = render_report_html(
        report,
        coach,
        synced_at=view["bundle"].get("fetched_at"),
        warnings=view["bundle"].get("warnings") or [],
    )
    status = _status(view)
    boot = {
        "fetch_coach": fetch_coach,
        "report": report,
        "series": report["series"],
        "intensity": (report.get("summary") or {}).get("intensity"),
        "status": status,
        "synced_at": view["bundle"].get("fetched_at"),
        "warnings": view["bundle"].get("warnings") or [],
        "coach": coach,
    }
    blob = json.dumps(boot, ensure_ascii=False).replace("<", "\\u003c")
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("@@REPORT_HTML@@", report_html)
    html = html.replace("@@BOOTSTRAP_JSON@@", blob)
    return html


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    range_key = normalize_range_key(request.query_params.get("range"))
    return HTMLResponse(_page(range_key), headers={"Cache-Control": "no-store"})


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/status")
def status():
    return _status()


@app.get("/api/users")
def users():
    return {"users": storage.list_users(), "active_user_id": (storage.active_user() or {}).get("id")}


@app.post("/api/users")
def create_user(body: dict):
    if not isinstance(body, dict):
        raise storage.StorageError("请求格式不对。")
    user = storage.add_user(
        display_name=str(body.get("display_name") or ""),
        personal_api_key=str(body.get("personal_api_key") or ""),
        label=str(body.get("label") or ""),
        region=str(body.get("region") or "global"),
        sex=str(body.get("sex") or "unknown"),
        age=body.get("age"),
        hr_max=body.get("hr_max"),
    )
    return {"ok": True, "user": user, "message": "已保存在本机，并设为当前配置。"}


@app.post("/api/users/select")
def select_user(body: dict):
    if not isinstance(body, dict):
        raise storage.StorageError("请求格式不对。")
    user = storage.set_active_user(str(body.get("id") or ""))
    return {"ok": True, "user": user}


@app.delete("/api/users/{user_id}")
def remove_user(user_id: str):
    storage.delete_user(user_id)
    return {"ok": True, "message": "已删除本机保存的配置、密钥缓存和数据缓存。佳明云端数据没有动。"}


@app.post("/api/settings/deepseek")
def save_deepseek(body: dict):
    if not isinstance(body, dict):
        raise storage.StorageError("请求格式不对。")
    source = storage.set_api_key(str(body.get("api_key") or ""))
    return {"ok": True, "deepseek": source, "message": "已保存在本机。" if source == "file" else "已清除本机密钥。"}


@app.post("/api/settings/sync")
def save_sync_policy(body: dict):
    if not isinstance(body, dict):
        raise storage.StorageError("请求格式不对。")
    policy = storage.set_sync_policy(
        min_interval_minutes=body.get("min_interval_minutes"),
        daily_cap=body.get("daily_cap"),
    )
    return {
        "ok": True,
        "sync_policy": policy,
        "sync": storage.sync_state_for_user((storage.active_user() or {}).get("id")),
        "message": (
            f"已保存同步限制：最短间隔 {policy['min_interval_minutes']} 分钟，"
            f"每日上限 {policy['daily_cap']} 次。"
        ),
    }


@app.get("/api/report")
def report(request: Request):
    """Deterministic report. This route never calls Garmin or DeepSeek."""
    view = _view()
    range_key = normalize_range_key(request.query_params.get("range"))
    payload = _report_for(view, range_key=range_key)
    sample = bool(view["bundle"].get("sample"))
    payload["coaching"] = offline_coaching(sample, None if sample else storage.get_api_key(), pending=False)
    payload["sync_warnings"] = view["bundle"].get("warnings") or []
    return payload


@app.get("/api/coach")
def coach():
    view = _view()
    sample = bool(view["bundle"].get("sample"))
    report_body = _report_for(view)
    api_key = None if sample else storage.get_api_key()
    return coaching_for(sample, report_body, api_key)


@app.post("/api/sync")
def sync():
    user = storage.active_user()
    if not user:
        raise storage.StorageError("还没有当前配置。先在页面里保存 Personal API Key。")
    result = garmin_sync.sync_account(user)
    if result.get("ok") and result.get("bundle"):
        storage.save_bundle(user["id"], result["bundle"])
    public = garmin_sync.public_sync_result(result)
    # Always refresh rate-limit status for the UI after a sync attempt.
    public["sync"] = storage.sync_state_for_user(user["id"])
    return public


def _public_chat(user_id: str | None) -> dict:
    messages = []
    profile = public_athlete_profile(None)
    if user_id:
        messages = storage.load_chat(user_id).get("messages") or []
        profile = public_athlete_profile(storage.load_athlete_profile(user_id))
    coach = storage.coach_state_for_user(user_id)
    deepseek = storage.deepseek_source()
    view = _view()
    sample = bool(view["bundle"].get("sample"))
    ready = (not sample) and deepseek != "missing" and bool(user_id)
    reason = None
    if sample:
        reason = CHAT_SAMPLE_REASON
    elif not user_id:
        reason = "还没有当前配置。请先在设置里保存 Personal API Key。"
    elif deepseek == "missing":
        reason = CHAT_MISSING_REASON
    return {
        "ok": True,
        "messages": messages,
        "ready": ready,
        "reason": reason,
        "sample": sample,
        "deepseek": deepseek,
        "coach": coach,
        "coach_policy": storage.get_coach_policy(),
        "athlete_profile": profile,
    }


@app.get("/api/chat")
def chat_history():
    user = storage.active_user()
    return _public_chat(user["id"] if user else None)


@app.delete("/api/chat")
def chat_clear():
    user = storage.active_user()
    if not user:
        raise storage.StorageError("还没有当前配置。")
    storage.clear_chat(user["id"])
    return _public_chat(user["id"])


@app.post("/api/chat")
def chat_send(body: dict):
    if not isinstance(body, dict):
        raise storage.StorageError("请求格式不对。")
    user = storage.active_user()
    if not user:
        raise storage.StorageError("还没有当前配置。先在设置里保存 Personal API Key。")
    message = sanitize_user_message(str(body.get("message") or ""))
    if not message:
        raise storage.StorageError("请先输入问题。")

    view = _view()
    sample = bool(view["bundle"].get("sample"))
    api_key = storage.get_api_key()
    public = _public_chat(user["id"])
    if sample:
        return {**public, "ok": False, "message": CHAT_SAMPLE_REASON}
    if not api_key:
        return {**public, "ok": False, "message": CHAT_MISSING_REASON}

    coach_state = storage.coach_state_for_user(user["id"])
    if coach_state.get("rate_limited"):
        return {
            **public,
            "ok": False,
            "message": coach_state.get("message") or "提问过快。",
            "rate_limited": True,
            "coach": coach_state,
        }

    athlete = storage.load_athlete_profile(user["id"])
    context = build_chat_context(view["bundle"], athlete_profile=athlete)
    history = storage.load_chat(user["id"]).get("messages") or []
    # Count the attempt before the network call so retries cannot spam.
    storage.record_coach_attempt(user["id"])
    try:
        reply = chat_complete(api_key, context=context, history=history, user_message=message)
    except CoachError as exc:
        detail = str(exc)
        if detail == "请求失败":
            fail_message = CHAT_FAILED_REASON
        elif detail.startswith("HTTP"):
            fail_message = f"教练回复失败（{detail}）。本地报告仍然可用。"
        else:
            fail_message = detail if detail else CHAT_FAILED_REASON
        return {**_public_chat(user["id"]), "ok": False, "message": fail_message}
    except Exception:
        return {**_public_chat(user["id"]), "ok": False, "message": CHAT_FAILED_REASON}

    if not reply:
        return {**_public_chat(user["id"]), "ok": False, "message": CHAT_EMPTY_REASON}

    storage.append_chat_turn(user["id"], user_text=message, assistant_text=reply)
    public = _public_chat(user["id"])
    return {**public, "ok": True, "reply": reply, "message": "已回复"}


@app.get("/api/athlete-profile")
def athlete_profile_get():
    user = storage.active_user()
    if not user:
        return {"ok": True, "athlete_profile": public_athlete_profile(None), "editable": False}
    return {
        "ok": True,
        "athlete_profile": public_athlete_profile(storage.load_athlete_profile(user["id"])),
        "editable": True,
    }


@app.post("/api/athlete-profile")
def athlete_profile_save(body: dict):
    if not isinstance(body, dict):
        raise storage.StorageError("请求格式不对。")
    user = storage.active_user()
    if not user:
        raise storage.StorageError("还没有当前配置。先在设置里保存配置。")
    current = storage.load_athlete_profile(user["id"])
    updated = apply_athlete_profile_update(current, body)
    public = storage.save_athlete_profile(user["id"], updated)
    return {"ok": True, "athlete_profile": public, "message": "个人描述已保存在本机。"}


@app.get("/static/plotly.min.js")
def plotly_js():
    path = _plotly_js_path()
    if not path:
        return PlainTextResponse("未找到本地 plotly.min.js。请确认已安装 plotly 包。", status_code=404)
    return FileResponse(path, media_type="application/javascript", headers={"Cache-Control": "public, max-age=86400"})


def main() -> None:
    import os

    import uvicorn

    port = int(os.environ.get("PORT") or "8765")
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    main()
