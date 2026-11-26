"""Sync rate limits and incremental bundle merge.

Limits are local guards against hammering Garmin Personal API.
Defaults: 20 minutes between syncs, 12 syncs per Shanghai calendar day.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.clock import SHANGHAI, iso_sh, now_sh, today_sh

DEFAULT_MIN_INTERVAL_MINUTES = 20
DEFAULT_DAILY_CAP = 12
MIN_INTERVAL_BOUNDS = (5, 180)
DAILY_CAP_BOUNDS = (1, 48)
BACKFILL_DAYS = 90
DELTA_OVERLAP_DAYS = 2


def clamp_min_interval_minutes(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = DEFAULT_MIN_INTERVAL_MINUTES
    low, high = MIN_INTERVAL_BOUNDS
    return max(low, min(high, number))


def clamp_daily_cap(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = DEFAULT_DAILY_CAP
    low, high = DAILY_CAP_BOUNDS
    return max(low, min(high, number))


def _parse_iso(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=SHANGHAI)
    return moment.astimezone(SHANGHAI)


def _parse_day(value) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.astimezone(SHANGHAI).date() if value.tzinfo else value.date()
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def sync_window(
    *,
    existing: dict | None,
    today: date | None = None,
    backfill_days: int = BACKFILL_DAYS,
    overlap_days: int = DELTA_OVERLAP_DAYS,
) -> tuple[date, date, str]:
    """Return (start, end, mode) where mode is 'backfill' or 'delta'."""
    end = today or today_sh()
    if not existing or not isinstance(existing, dict):
        start = end - timedelta(days=max(1, backfill_days) - 1)
        return start, end, "backfill"

    anchors: list[date] = []
    fetched = _parse_iso(existing.get("fetched_at"))
    if fetched:
        anchors.append(fetched.date())
    for row in existing.get("days") or []:
        if isinstance(row, dict):
            stamp = _parse_day(row.get("date"))
            if stamp:
                anchors.append(stamp)
    for row in existing.get("activities") or []:
        if isinstance(row, dict):
            stamp = _parse_day(row.get("date"))
            if stamp:
                anchors.append(stamp)
    if not anchors:
        start = end - timedelta(days=max(1, backfill_days) - 1)
        return start, end, "backfill"

    latest = max(anchors)
    start = latest - timedelta(days=max(0, overlap_days))
    if start > end:
        start = end
    # Keep delta windows modest; never wider than a backfill.
    earliest = end - timedelta(days=max(1, backfill_days) - 1)
    if start < earliest:
        start = earliest
    return start, end, "delta"


def _activity_key(row: dict) -> str:
    activity_id = row.get("id")
    if activity_id is not None and str(activity_id).strip() != "":
        return f"id:{activity_id}"
    return "fallback:{date}|{name}|{duration}|{distance}".format(
        date=row.get("date") or "",
        name=row.get("name") or "",
        duration=row.get("duration_s") if row.get("duration_s") is not None else "",
        distance=row.get("distance_m") if row.get("distance_m") is not None else "",
    )


def merge_activities(existing: list | None, incoming: list | None) -> list[dict]:
    merged: dict[str, dict] = {}
    order: list[str] = []
    for source in (existing or [], incoming or []):
        for raw in source:
            if not isinstance(raw, dict):
                continue
            key = _activity_key(raw)
            if key not in merged:
                order.append(key)
            merged[key] = dict(raw)
    rows = [merged[key] for key in order]
    rows.sort(key=lambda item: (str(item.get("date") or ""), str(item.get("id") or "")))
    return rows


def merge_days(existing: list | None, incoming: list | None) -> list[dict]:
    by_date: dict[str, dict] = {}
    for source in (existing or [], incoming or []):
        for raw in source:
            if not isinstance(raw, dict):
                continue
            stamp = raw.get("date")
            if not isinstance(stamp, str) or len(stamp) < 10:
                continue
            key = stamp[:10]
            current = by_date.setdefault(key, {"date": key})
            for field, value in raw.items():
                if field == "date":
                    continue
                if value is not None:
                    current[field] = value
    return [by_date[key] for key in sorted(by_date)]


def merge_profile(existing: dict | None, incoming: dict | None) -> dict:
    out: dict[str, Any] = {}
    if isinstance(existing, dict):
        out.update(existing)
    if isinstance(incoming, dict):
        for key, value in incoming.items():
            if value is not None and value != "":
                out[key] = value
    return out


def merge_bundles(existing: dict | None, incoming: dict) -> dict:
    """Merge a delta fetch into the local cache. Incoming wins on overlapping fields."""
    base = existing if isinstance(existing, dict) else {}
    incoming = incoming if isinstance(incoming, dict) else {}
    warnings = incoming.get("warnings")
    if not isinstance(warnings, list):
        warnings = list(base.get("warnings") or [])[:12]
    return {
        "sample": False,
        "fetched_at": incoming.get("fetched_at") or iso_sh(),
        "sync_mode": incoming.get("sync_mode") or base.get("sync_mode"),
        "sync_start": incoming.get("sync_start"),
        "sync_end": incoming.get("sync_end"),
        "warnings": warnings[:12],
        "profile": merge_profile(base.get("profile") if isinstance(base.get("profile"), dict) else {}, incoming.get("profile")),
        "activities": merge_activities(base.get("activities"), incoming.get("activities")),
        "days": merge_days(base.get("days"), incoming.get("days")),
    }


def evaluate_rate_limit(
    *,
    last_sync_at: str | None,
    sync_day: str | None,
    sync_count_today: int,
    min_interval_minutes: int = DEFAULT_MIN_INTERVAL_MINUTES,
    daily_cap: int = DEFAULT_DAILY_CAP,
    now: datetime | None = None,
) -> dict:
    """Return whether a sync is allowed and how long until the next one."""
    moment = now or now_sh()
    today = moment.astimezone(SHANGHAI).date().isoformat()
    interval = clamp_min_interval_minutes(min_interval_minutes)
    cap = clamp_daily_cap(daily_cap)
    count = int(sync_count_today or 0)
    if sync_day != today:
        count = 0
    cooldown = 0
    last = _parse_iso(last_sync_at)
    if last is not None:
        elapsed = (moment - last).total_seconds()
        needed = interval * 60
        if elapsed < needed:
            cooldown = int(max(1, needed - elapsed))
    over_daily = count >= cap
    can_sync = cooldown <= 0 and not over_daily
    message = ""
    if not can_sync:
        if cooldown > 0:
            minutes = max(1, (cooldown + 59) // 60)
            message = (
                f"同步过于频繁。为避免触发佳明接口限制，请至少间隔 {interval} 分钟。"
                f"还可等待约 {minutes} 分钟后再试。"
            )
        else:
            message = (
                f"今天同步次数已达上限（{cap} 次/天，按北京时间计）。"
                "请明天再试，或在设置里略微提高每日上限（仍建议保持保守）。"
            )
    return {
        "can_sync": can_sync,
        "cooldown_seconds": cooldown if not over_daily else 0,
        "syncs_today": count,
        "sync_day": today,
        "min_interval_minutes": interval,
        "daily_cap": cap,
        "last_sync_at": last_sync_at,
        "message": message,
        "rate_limited": not can_sync,
    }


def next_sync_counters(
    *,
    sync_day: str | None,
    sync_count_today: int,
    now: datetime | None = None,
) -> tuple[str, int]:
    moment = now or now_sh()
    today = moment.astimezone(SHANGHAI).date().isoformat()
    count = int(sync_count_today or 0)
    if sync_day != today:
        return today, 1
    return today, count + 1
