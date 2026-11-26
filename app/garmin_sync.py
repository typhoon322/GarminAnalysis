"""Read the signed-in user's own Garmin Connect data.

Official Garmin Health API does not accept a consumer username and password.
This uses the garminconnect library, which logs in the same way as the
Garmin Connect phone app. Credentials are used only for that login.
"""

from __future__ import annotations

import inspect
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from app.clock import iso_sh, today_sh
from app.coach import network_allowed
from app.normalize import (
    apply_body_battery,
    apply_heart_rate,
    apply_hrv,
    apply_profile_demographics,
    apply_sleep,
    apply_steps,
    apply_stress,
    apply_summaries,
    apply_training,
    normalize_activities,
)

SYNC_DAYS = 90


def _scrub(text: str, secrets: list[str]) -> str:
    cleaned = text or ""
    for secret in secrets:
        if secret and len(str(secret)) >= 3:
            cleaned = cleaned.replace(str(secret), "***")
    return cleaned[:240]


def _fail(message: str, *, needs_mfa: bool = False, client_state=None) -> dict:
    return {
        "ok": False,
        "needs_mfa": needs_mfa,
        "message": message,
        "bundle": None,
        "client_state": client_state,
    }


def _ok(bundle: dict, message: str) -> dict:
    return {"ok": True, "needs_mfa": False, "message": message, "bundle": bundle, "client_state": None}


def public_sync_result(result: dict) -> dict:
    """Strip login state before anything is sent to the browser."""
    return {
        "ok": bool(result.get("ok")),
        "needs_mfa": bool(result.get("needs_mfa")),
        "message": result.get("message") or "",
    }


def _blocked() -> dict | None:
    if network_allowed():
        return None
    return _fail("当前环境禁止连接佳明。")


def _make_client(email: str | None, password: str | None, is_cn: bool, return_mfa: bool):
    from garminconnect import Garmin

    params = inspect.signature(Garmin.__init__).parameters
    kwargs = {}
    if "is_cn" in params:
        kwargs["is_cn"] = bool(is_cn)
    if return_mfa and "return_on_mfa" in params:
        kwargs["return_on_mfa"] = True
    if email is None and password is None:
        try:
            return Garmin(**kwargs) if kwargs else Garmin()
        except TypeError:
            return Garmin(is_cn=bool(is_cn)) if "is_cn" in params else Garmin()
    return Garmin(email, password, **kwargs)


def _login(client, token_dir: Path):
    token_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(token_dir, 0o700)
    except OSError:
        pass
    signature = inspect.signature(client.login)
    if "tokenstore" in signature.parameters:
        try:
            return client.login(tokenstore=str(token_dir))
        except TypeError:
            return client.login(str(token_dir))
    return client.login()


def _needs_mfa(result) -> object | None:
    if isinstance(result, tuple) and result and result[0] == "needs_mfa":
        state = result[1]
        if isinstance(state, str):
            try:
                state = json.loads(state)
            except json.JSONDecodeError:
                return state
        return state
    return None


def _persist_tokens(client, token_dir: Path) -> None:
    """Write garmin_tokens.json. login() already dumps; resume_login does not."""
    token_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(token_dir, 0o700)
    except OSError:
        pass
    inner = getattr(client, "client", None)
    dump = getattr(inner, "dump", None)
    if callable(dump):
        try:
            dump(str(token_dir))
            return
        except Exception:
            pass
    for name in ("save_tokens", "store_tokens", "dump"):
        fn = getattr(client, name, None)
        if not callable(fn):
            continue
        try:
            fn(str(token_dir))
            return
        except TypeError:
            continue
        except Exception:
            continue


def _call(client, secrets: list[str], name: str, *args):
    fn = getattr(client, name, None)
    if not callable(fn):
        return None, f"{name} 不在当前 garminconnect 版本里"
    try:
        return fn(*args), None
    except Exception as exc:
        detail = _scrub(str(exc), secrets)
        return None, f"{name} 失败：{type(exc).__name__} {detail}".strip()[:180]


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _end_date(client) -> date:
    fn = getattr(client, "today", None)
    if callable(fn):
        try:
            stamp = _as_date(fn())
            if stamp:
                return stamp
        except Exception:
            pass
    return today_sh()


def _profile_from_user(user: dict) -> dict:
    return {
        "sex": user.get("sex") or "unknown",
        "age": user.get("age"),
        "hr_max": user.get("hr_max"),
    }


def fetch_bundle(client, user: dict, secrets: list[str]) -> tuple[dict, list[str]]:
    end = _end_date(client)
    start = end - timedelta(days=SYNC_DAYS - 1)
    start_s, end_s = start.isoformat(), end.isoformat()
    warnings: list[str] = []
    days: dict[str, dict] = {}

    def take(name: str, *args):
        payload, err = _call(client, secrets, name, *args)
        if err:
            warnings.append(err)
        return payload

    activities_payload = take("get_activities_by_date", start_s, end_s)
    activities = normalize_activities(activities_payload) if activities_payload is not None else []
    apply_summaries(days, take("get_daily_summaries", start_s, end_s))
    apply_heart_rate(days, take("get_daily_heart_rate_stats", start_s, end_s))
    apply_hrv(days, take("get_hrv_data_range", start_s, end_s))
    apply_sleep(days, take("get_sleep_daily", start_s, end_s))
    battery = take("get_body_battery", start_s, end_s)
    if battery is None:
        battery = take("get_daily_body_battery_stats", start_s, end_s)
    apply_body_battery(days, battery)
    apply_stress(days, take("get_daily_stress_stats", start_s, end_s))
    apply_steps(days, take("get_daily_steps", start_s, end_s))

    profile = _profile_from_user(user)
    personal = take("get_personal_information")
    apply_profile_demographics(profile, personal)
    # The local form wins when the user already filled sex, age, or HRmax.
    if user.get("sex") in {"male", "female"}:
        profile["sex"] = user["sex"]
    if user.get("age"):
        profile["age"] = user["age"]
    if user.get("hr_max"):
        profile["hr_max"] = user["hr_max"]

    status_payload = take("get_training_status", end_s)
    vo2_payload = take("get_max_metrics_latest", end_s)
    apply_training(profile, status_payload, vo2_payload)
    name = getattr(client, "full_name", None)
    if isinstance(name, str) and name.strip():
        profile["garmin_name"] = name.strip()[:80]

    bundle = {
        "sample": False,
        "fetched_at": iso_sh(),
        "warnings": warnings[:12],
        "profile": profile,
        "activities": activities,
        "days": [days[key] for key in sorted(days)],
    }
    return bundle, warnings


def _login_error(exc: Exception, secrets: list[str]) -> dict:
    detail = _scrub(str(exc), secrets)
    if detail:
        return _fail(f"登录失败：{type(exc).__name__}。{detail}")
    return _fail(f"登录失败：{type(exc).__name__}。")


def sync_account(user: dict, token_dir: Path) -> dict:
    blocked = _blocked()
    if blocked:
        return blocked
    email = str(user.get("email") or "")
    password = str(user.get("password") or "")
    secrets = [password, email]
    if not email or not password:
        return _fail("这个账号在本机没有密码。请在页面里重新保存。")
    try:
        client = _make_client(email, password, bool(user.get("is_cn")), True)
        result = _login(client, token_dir)
    except Exception as exc:
        return _login_error(exc, secrets)
    state = _needs_mfa(result)
    if state is not None:
        return _fail("佳明要求验证码。填入短信或邮箱里的一次性验证码后再同步。", needs_mfa=True, client_state=state)
    try:
        bundle, warnings = fetch_bundle(client, user, secrets)
        _persist_tokens(client, token_dir)
    except Exception as exc:
        return _fail("登录后读取数据失败：" + _scrub(f"{type(exc).__name__} {exc}", secrets))
    if not bundle["activities"] and not bundle["days"] and warnings:
        return _fail("已登录，但活动、睡眠、心率这些接口都没有返回数据。" + warnings[0])
    return _ok(bundle, f"已同步最近 {SYNC_DAYS} 天。")


def resume_account(user: dict, client_state, code: str, token_dir: Path) -> dict:
    blocked = _blocked()
    if blocked:
        return blocked
    token = str(code or "").strip()
    if not token:
        return _fail("请填写验证码。")
    email = str(user.get("email") or "")
    password = str(user.get("password") or "")
    secrets = [password, email, token]
    try:
        client = _make_client(None, None, bool(user.get("is_cn")), False)
        client.resume_login(client_state, token)
    except Exception as exc:
        return _fail("验证码没有通过：" + _scrub(f"{type(exc).__name__} {exc}", secrets))
    try:
        bundle, warnings = fetch_bundle(client, user, secrets)
        _persist_tokens(client, token_dir)
    except Exception as exc:
        return _fail("验证之后读取数据失败：" + _scrub(f"{type(exc).__name__} {exc}", secrets))
    if not bundle["activities"] and not bundle["days"] and warnings:
        return _fail("已登录，但没有取到可用数据。" + warnings[0])
    return _ok(bundle, f"已同步最近 {SYNC_DAYS} 天。")
