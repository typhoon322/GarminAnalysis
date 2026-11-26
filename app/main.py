"""Local page for Garmin fitness and health analysis. Binds to 127.0.0.1."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response

from app import garmin_sync, storage
from app.analysis import analyze
from app.clock import iso_sh
from app.coach import coaching_for, offline_coaching
from app.preview import render_report_html
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
    return {
        "viewing": view["viewing"],
        "active_user": public,
        "users": storage.list_users(),
        "needs_mfa": bool(user and storage.has_mfa(user["id"])),
        "deepseek": storage.deepseek_source(),
        "last_sync": synced,
        "garmin_name": garmin_name,
        "data_dir": str(storage.data_dir()),
        "generated_at": iso_sh(),
    }


def _report_for(view: dict) -> dict:
    report = analyze(view["bundle"])
    report["viewing"] = view["viewing"]
    if view["user"]:
        report["active_user_label"] = view["user"].get("display_name") or view["user"].get("email")
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


def _page() -> str:
    view = _view()
    report = _report_for(view)
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
        "series": report["series"],
        "intensity": (report.get("summary") or {}).get("intensity"),
        "status": status,
    }
    blob = json.dumps(boot, ensure_ascii=False).replace("<", "\\u003c")
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("@@REPORT_HTML@@", report_html)
    html = html.replace("@@BOOTSTRAP_JSON@@", blob)
    return html


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(_page(), headers={"Cache-Control": "no-store"})


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
        email=str(body.get("email") or ""),
        password=str(body.get("password") or ""),
        display_name=str(body.get("display_name") or ""),
        is_cn=bool(body.get("is_cn")),
        sex=str(body.get("sex") or "unknown"),
        age=body.get("age"),
        hr_max=body.get("hr_max"),
    )
    return {"ok": True, "user": user, "message": "已保存在本机，并设为当前账号。"}


@app.post("/api/users/select")
def select_user(body: dict):
    if not isinstance(body, dict):
        raise storage.StorageError("请求格式不对。")
    user = storage.set_active_user(str(body.get("id") or ""))
    return {"ok": True, "user": user}


@app.delete("/api/users/{user_id}")
def remove_user(user_id: str):
    storage.delete_user(user_id)
    return {"ok": True, "message": "已删除本机保存的账号、密码和缓存。佳明云端数据没有动。"}


@app.post("/api/settings/deepseek")
def save_deepseek(body: dict):
    if not isinstance(body, dict):
        raise storage.StorageError("请求格式不对。")
    source = storage.set_api_key(str(body.get("api_key") or ""))
    return {"ok": True, "deepseek": source, "message": "已保存在本机。" if source == "file" else "已清除本机密钥。"}


@app.get("/api/report")
def report():
    """Deterministic report. This route never calls Garmin or DeepSeek."""
    view = _view()
    payload = _report_for(view)
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
        raise storage.StorageError("还没有当前账号。先在页面里保存邮箱和密码。")
    result = garmin_sync.sync_account(user, storage.token_dir(user["id"]))
    if result.get("needs_mfa") and result.get("client_state") is not None:
        storage.save_mfa(user["id"], result["client_state"])
    elif result.get("ok") and result.get("bundle"):
        storage.clear_mfa(user["id"])
        storage.save_bundle(user["id"], result["bundle"])
    return garmin_sync.public_sync_result(result)


@app.post("/api/mfa")
def mfa(body: dict):
    if not isinstance(body, dict):
        raise storage.StorageError("请求格式不对。")
    user = storage.active_user()
    if not user:
        raise storage.StorageError("还没有当前账号。")
    state = storage.load_mfa(user["id"])
    if state is None:
        raise storage.StorageError("没有待完成的验证码登录。请重新同步。")
    result = garmin_sync.resume_account(user, state, str(body.get("code") or ""), storage.token_dir(user["id"]))
    if result.get("ok") and result.get("bundle"):
        storage.clear_mfa(user["id"])
        storage.save_bundle(user["id"], result["bundle"])
    return garmin_sync.public_sync_result(result)


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
