"""Garmin Connect Personal API client (API Key → bearer token → pull).

Uses documented Personal API auth and the published connectapi resource paths
from Garmin's Personal API surface / official Python reference client.
"""

from __future__ import annotations

import base64
import json
from datetime import date, timedelta
from time import time
from typing import Any

import httpx

from app import storage
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
BASE_URL_GLOBAL = "https://personal.connectapi.garmin.com"
BASE_URL_CN = "https://personal.connectapi.garmin.cn"
# Back-compat alias used by tests and callers.
BASE_URL = BASE_URL_GLOBAL
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
# Match Garmin's official Personal API client: refresh when under 1 hour remains.
TOKEN_REFRESH_WINDOW_SECONDS = 60 * 60
REQUEST_TIMEOUT = 45.0
ACTIVITY_PAGE_LIMIT = 40
ACTIVITY_CHUNK_DAYS = 28
SLEEP_CHUNK_DAYS = 28
HRV_CHUNK_DAYS = 28
BATTERY_CHUNK_DAYS = 28
STRESS_CHUNK_DAYS = 28
STEPS_CHUNK_DAYS = 28


def base_url_for(user: dict | None = None) -> str:
    region = str((user or {}).get("region") or "global").strip().lower()
    if region in {"cn", "china", "garmin.cn"}:
        return BASE_URL_CN
    return BASE_URL_GLOBAL


def _scrub(text: str, secrets: list[str]) -> str:
    cleaned = text or ""
    for secret in secrets:
        if secret and len(str(secret)) >= 3:
            cleaned = cleaned.replace(str(secret), "***")
    return cleaned[:240]


def _fail(message: str) -> dict:
    return {"ok": False, "message": message, "bundle": None}


def _ok(bundle: dict, message: str) -> dict:
    return {"ok": True, "message": message, "bundle": bundle}


def public_sync_result(result: dict) -> dict:
    """Strip anything that must not reach the browser."""
    return {
        "ok": bool(result.get("ok")),
        "message": result.get("message") or "",
    }


def _blocked() -> dict | None:
    if network_allowed():
        return None
    return _fail("当前环境禁止连接佳明。")


def _jwt_expiration(access_token: str) -> float:
    try:
        encoded = access_token.split(".")[1]
        payload = json.loads(
            base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
        )
        return float(payload["exp"])
    except (IndexError, KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ValueError("令牌响应不是带有 exp 的 JWT。") from exc


def _profile_from_user(user: dict) -> dict:
    return {
        "sex": user.get("sex") or "unknown",
        "age": user.get("age"),
        "hr_max": user.get("hr_max"),
    }


def _daterange_chunks(start: date, end: date, max_days: int) -> list[tuple[date, date]]:
    if end < start:
        return []
    chunks: list[tuple[date, date]] = []
    cursor = start
    step = max(1, max_days)
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=step - 1))
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _unwrap_payload(payload: Any) -> Any:
    """Personal API sometimes wraps lists in {data|hrvSummaries|...}."""
    if not isinstance(payload, dict):
        return payload
    for key in ("data", "hrvSummaries", "dailySleepDTOs", "activities", "activityList"):
        inner = payload.get(key)
        if isinstance(inner, list):
            return inner
    return payload


def _is_optional_missing(err: str | None) -> bool:
    if not err:
        return False
    return "HTTP 404" in err or "NOT_FOUND" in err


class PersonalApiClient:
    """Thin httpx client for Personal API token + connectapi GETs."""

    def __init__(
        self,
        access_token: str,
        *,
        base_url: str = BASE_URL_GLOBAL,
        timeout: float = REQUEST_TIMEOUT,
    ):
        self.access_token = access_token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {access_token}",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PersonalApiClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get(self, path: str, *, params: dict | None = None) -> Any:
        response = self._client.get(path, params=params)
        if response.status_code == 204:
            return None
        if not response.is_success:
            detail = (response.text or "")[:180]
            raise RuntimeError(f"HTTP {response.status_code} {path}: {detail}")
        if not response.content:
            return None
        return response.json()


def exchange_token(
    api_key: str,
    *,
    base_url: str = BASE_URL_GLOBAL,
    timeout: float = REQUEST_TIMEOUT,
) -> tuple[str, float]:
    """POST ApiKey → raw JWT body. Returns (access_token, expires_at)."""
    url = f"{base_url.rstrip('/')}{TOKEN_PATH}"
    response = httpx.post(
        url,
        headers={"Accept": "*/*", "Authorization": f"ApiKey {api_key}"},
        timeout=timeout,
    )
    if not response.is_success:
        detail = _scrub(response.text or "", [api_key])
        raise RuntimeError(f"换取访问令牌失败：HTTP {response.status_code} {detail}".strip())
    access_token = (response.text or "").strip()
    if not access_token:
        raise RuntimeError("换取访问令牌失败：响应为空。")
    expires_at = _jwt_expiration(access_token)
    return access_token, expires_at


def obtain_access_token(user: dict, *, force_refresh: bool = False, now: float | None = None) -> str:
    api_key = str(user.get("personal_api_key") or "").strip()
    if not api_key:
        raise RuntimeError("这个配置没有 Personal API Key。请在页面里重新保存。")
    clock = time() if now is None else now
    if not force_refresh:
        cached = storage.load_bearer(user["id"], api_key)
        if cached and cached["expires_at"] - clock > TOKEN_REFRESH_WINDOW_SECONDS:
            return cached["access_token"]
    access_token, expires_at = exchange_token(api_key, base_url=base_url_for(user))
    storage.save_bearer(user["id"], api_key, access_token, expires_at)
    return access_token


def _call(client: PersonalApiClient, secrets: list[str], label: str, path: str, params: dict | None = None):
    try:
        return client.get(path, params=params), None
    except Exception as exc:
        detail = _scrub(f"{type(exc).__name__} {exc}", secrets)
        return None, f"{label} 失败：{detail}".strip()[:180]


def _fetch_chunked(
    client: PersonalApiClient,
    secrets: list[str],
    label: str,
    start: date,
    end: date,
    max_days: int,
    path_for,
) -> tuple[Any, list[str]]:
    warnings: list[str] = []
    collected: list = []
    for chunk_start, chunk_end in _daterange_chunks(start, end, max_days):
        path, params = path_for(chunk_start, chunk_end)
        payload, err = _call(client, secrets, label, path, params)
        if err:
            warnings.append(err)
            continue
        if payload is None:
            continue
        payload = _unwrap_payload(payload)
        if isinstance(payload, list):
            collected.extend(payload)
        else:
            collected.append(payload)
    if not collected:
        return None, warnings
    if len(collected) == 1 and not isinstance(collected[0], list):
        return collected[0], warnings
    return collected, warnings


def _search_activities_window(
    client: PersonalApiClient,
    secrets: list[str],
    start: date,
    end: date,
) -> tuple[list, list[str]]:
    warnings: list[str] = []
    rows: list = []
    offset = 0
    while True:
        params = {
            "start": offset,
            "limit": ACTIVITY_PAGE_LIMIT,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
        }
        # Build a stable label without putting query secrets into logs.
        label = f"activities {start.isoformat()}..{end.isoformat()}@{offset}"
        path = "/activitylist-service/activities/search/activities"
        payload, err = _call(client, secrets, label, path, params)
        if err:
            # Known Personal API quirk: busy windows can 500; split the range.
            if "HTTP 500" in err and start < end:
                mid = start + (end - start) / 2
                mid_date = mid if isinstance(mid, date) else start + timedelta(days=(end - start).days // 2)
                if mid_date <= start:
                    mid_date = start
                if mid_date >= end:
                    warnings.append(err)
                    return rows, warnings
                left, left_w = _search_activities_window(client, secrets, start, mid_date)
                right, right_w = _search_activities_window(
                    client, secrets, mid_date + timedelta(days=1), end
                )
                return left + right, warnings + left_w + right_w
            warnings.append(err)
            break
        payload = _unwrap_payload(payload)
        batch = []
        if isinstance(payload, list):
            batch = payload
        elif isinstance(payload, dict):
            batch = [payload]
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < ACTIVITY_PAGE_LIMIT:
            break
        offset += ACTIVITY_PAGE_LIMIT
        if offset > 500:
            warnings.append("活动列表分页过多，已截断。")
            break
    return rows, warnings


def fetch_activities(client: PersonalApiClient, secrets: list[str], start: date, end: date) -> tuple[list, list[str]]:
    warnings: list[str] = []
    all_rows: list = []
    for chunk_start, chunk_end in _daterange_chunks(start, end, ACTIVITY_CHUNK_DAYS):
        rows, chunk_warnings = _search_activities_window(client, secrets, chunk_start, chunk_end)
        all_rows.extend(rows)
        warnings.extend(chunk_warnings)
    return all_rows, warnings


def _newest_activity_date(client: PersonalApiClient, secrets: list[str]) -> str | None:
    """Peek recent activities without a date filter (Personal API default)."""
    payload, err = _call(
        client,
        secrets,
        "activities-latest",
        "/activitylist-service/activities/search/activities",
        {"start": 0, "limit": 5},
    )
    if err or payload is None:
        return None
    rows = normalize_activities(_unwrap_payload(payload))
    if not rows:
        return None
    dates = sorted({row["date"] for row in rows if row.get("date")})
    return dates[-1] if dates else None


def fetch_bundle(client: PersonalApiClient, user: dict, secrets: list[str]) -> tuple[dict, list[str]]:
    end = today_sh()
    start = end - timedelta(days=SYNC_DAYS - 1)
    warnings: list[str] = []

    activities_payload, act_warnings = fetch_activities(client, secrets, start, end)
    warnings.extend(act_warnings)
    activities = normalize_activities(activities_payload) if activities_payload else []

    days: dict[str, dict] = {}

    summaries, sum_warnings = _fetch_chunked(
        client,
        secrets,
        "daily-summaries",
        start,
        end,
        365,
        lambda s, e: (
            "/usersummary-service/v2/dailysummaries",
            {"startDate": s.isoformat(), "endDate": e.isoformat()},
        ),
    )
    warnings.extend(sum_warnings)
    apply_summaries(days, summaries)

    sleep_payload, sleep_warnings = _fetch_chunked(
        client,
        secrets,
        "sleep-summaries",
        start,
        end,
        SLEEP_CHUNK_DAYS,
        lambda s, e: (
            "/sleep-service/sleep/dailySleepSummariesByDate",
            {"startDate": s.isoformat(), "endDate": e.isoformat()},
        ),
    )
    warnings.extend(sleep_warnings)
    apply_sleep(days, sleep_payload)

    hrv_payload, hrv_warnings = _fetch_chunked(
        client,
        secrets,
        "hrv-summaries",
        start,
        end,
        HRV_CHUNK_DAYS,
        lambda s, e: (f"/hrv-service/hrv/daily/{s.isoformat()}/{e.isoformat()}", None),
    )
    warnings.extend(hrv_warnings)
    apply_hrv(days, hrv_payload)

    battery_payload, battery_warnings = _fetch_chunked(
        client,
        secrets,
        "body-battery",
        start,
        end,
        BATTERY_CHUNK_DAYS,
        lambda s, e: (
            "/wellness-service/wellness/bodyBattery/reports/daily",
            {"startDate": s.isoformat(), "endDate": e.isoformat()},
        ),
    )
    warnings.extend(battery_warnings)
    apply_body_battery(days, battery_payload)

    stress_payload, stress_warnings = _fetch_chunked(
        client,
        secrets,
        "stress",
        start,
        end,
        STRESS_CHUNK_DAYS,
        lambda s, e: (f"/usersummary-service/stats/stress/daily/{s.isoformat()}/{e.isoformat()}", None),
    )
    warnings.extend(stress_warnings)
    apply_stress(days, stress_payload)

    steps_payload, steps_warnings = _fetch_chunked(
        client,
        secrets,
        "steps",
        start,
        end,
        STEPS_CHUNK_DAYS,
        lambda s, e: (f"/usersummary-service/stats/steps/daily/{s.isoformat()}/{e.isoformat()}", None),
    )
    warnings.extend(steps_warnings)
    apply_steps(days, steps_payload)

    # Optional per-day heart rate when summaries did not carry resting HR.
    if not any(row.get("resting_hr") is not None for row in days.values()):
        hr_rows = []
        for day in _daterange_chunks(end - timedelta(days=13), end, 1):
            stamp = day[0].isoformat()
            payload, err = _call(
                client,
                secrets,
                "heart-rate",
                "/wellness-service/wellness/dailyHeartRate",
                {"date": stamp},
            )
            if err:
                warnings.append(err)
                break
            if payload is not None:
                hr_rows.append(payload)
        apply_heart_rate(days, hr_rows)

    profile = _profile_from_user(user)
    settings, settings_err = _call(
        client, secrets, "profile-settings", "/userprofile-service/userprofile/settings"
    )
    if settings_err:
        warnings.append(settings_err)
    apply_profile_demographics(profile, settings)
    if user.get("sex") in {"male", "female"}:
        profile["sex"] = user["sex"]
    if user.get("age"):
        profile["age"] = user["age"]
    if user.get("hr_max"):
        profile["hr_max"] = user["hr_max"]

    # VO2 max is on the published Personal API surface (metrics maxmet daily).
    vo2_payload, vo2_err = _call(
        client,
        secrets,
        "vo2-max",
        f"/metrics-service/metrics/maxmet/daily/{start.isoformat()}/{end.isoformat()}",
    )
    if vo2_err and not _is_optional_missing(vo2_err):
        warnings.append(vo2_err)

    # Training status is not on the Personal API surface (live 404 NOT_FOUND). Skip quietly.
    status_payload = None
    apply_training(profile, status_payload, vo2_payload)

    if isinstance(settings, dict):
        for key in ("displayName", "userDisplayName", "fullName", "userName"):
            value = settings.get(key)
            if isinstance(value, str) and value.strip():
                profile["garmin_name"] = value.strip()[:80]
                break

    if not activities and not days:
        newest = _newest_activity_date(client, secrets)
        if newest:
            warnings.append(
                f"最近 {SYNC_DAYS} 天接口返回为空；账号上较新的活动日期是 {newest}。"
                "请确认手表已同步到创建此 Personal API Key 的 Connect 账号；"
                "中国区 Key 请选择中国区端点。"
            )

    bundle = {
        "sample": False,
        "fetched_at": iso_sh(),
        "warnings": warnings[:12],
        "profile": profile,
        "activities": activities,
        "days": [days[key] for key in sorted(days)],
    }
    return bundle, warnings


def sync_account(user: dict) -> dict:
    blocked = _blocked()
    if blocked:
        return blocked
    api_key = str(user.get("personal_api_key") or "").strip()
    secrets = [api_key]
    if not api_key:
        return _fail("这个配置没有 Personal API Key。请在页面里重新保存。")
    try:
        access_token = obtain_access_token(user)
        secrets.append(access_token)
    except Exception as exc:
        return _fail("换取访问令牌失败：" + _scrub(f"{type(exc).__name__} {exc}", secrets))
    try:
        with PersonalApiClient(access_token, base_url=base_url_for(user)) as client:
            bundle, warnings = fetch_bundle(client, user, secrets)
    except Exception as exc:
        return _fail("读取数据失败：" + _scrub(f"{type(exc).__name__} {exc}", secrets))

    hard = [w for w in warnings if "失败" in w and not _is_optional_missing(w)]
    if not bundle["activities"] and not bundle["days"]:
        if hard:
            return _fail("已认证，但活动、睡眠、心率这些接口读取失败。" + hard[0])
        hint = next((w for w in warnings if "较新的活动日期" in w), None)
        if hint:
            return _ok(bundle, f"同步完成，但最近 {SYNC_DAYS} 天没有数据。" + hint)
        return _ok(bundle, f"同步完成，但最近 {SYNC_DAYS} 天佳明没有返回活动或健康数据。")
    return _ok(bundle, f"已同步最近 {SYNC_DAYS} 天。")
