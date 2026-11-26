"""Unit tests for incremental merge and local rate limits (no live Garmin)."""

from __future__ import annotations

from datetime import date, datetime

from app.clock import SHANGHAI
from app.sync_policy import (
    evaluate_rate_limit,
    merge_activities,
    merge_bundles,
    merge_days,
    next_sync_counters,
    sync_window,
)


def test_merge_activities_by_id_keeps_older_and_updates_overlap():
    existing = [
        {"id": "a1", "date": "2026-11-01", "name": "旧跑", "training_load": 40},
        {"id": "a2", "date": "2026-11-10", "name": "旧骑", "training_load": 50},
    ]
    incoming = [
        {"id": "a2", "date": "2026-11-10", "name": "新骑", "training_load": 70},
        {"id": "a3", "date": "2026-11-25", "name": "新跑", "training_load": 90},
    ]
    merged = merge_activities(existing, incoming)
    by_id = {row["id"]: row for row in merged}
    assert set(by_id) == {"a1", "a2", "a3"}
    assert by_id["a1"]["name"] == "旧跑"
    assert by_id["a2"]["name"] == "新骑"
    assert by_id["a2"]["training_load"] == 70
    assert by_id["a3"]["training_load"] == 90


def test_merge_days_by_date_preserves_history_and_fills_fields():
    existing = [
        {"date": "2026-11-01", "steps": 5000, "resting_hr": 55},
        {"date": "2026-11-20", "steps": 8000},
    ]
    incoming = [
        {"date": "2026-11-20", "steps": 9000, "hrv": 48},
        {"date": "2026-11-25", "steps": 10000},
    ]
    merged = merge_days(existing, incoming)
    by_date = {row["date"]: row for row in merged}
    assert list(by_date) == ["2026-11-01", "2026-11-20", "2026-11-25"]
    assert by_date["2026-11-01"]["resting_hr"] == 55
    assert by_date["2026-11-20"]["steps"] == 9000
    assert by_date["2026-11-20"]["hrv"] == 48
    assert by_date["2026-11-25"]["steps"] == 10000


def test_merge_bundles_preserves_older_history():
    existing = {
        "fetched_at": "2026-11-20T10:00:00+08:00",
        "profile": {"garmin_name": "旧名", "sex": "male"},
        "activities": [{"id": "old", "date": "2026-09-01", "name": "九月跑"}],
        "days": [{"date": "2026-09-01", "steps": 1000}],
        "warnings": ["旧警告"],
    }
    incoming = {
        "fetched_at": "2026-11-26T12:00:00+08:00",
        "sync_mode": "delta",
        "sync_start": "2026-11-24",
        "sync_end": "2026-11-26",
        "profile": {"garmin_name": "新名"},
        "activities": [{"id": "new", "date": "2026-11-25", "name": "增量跑"}],
        "days": [{"date": "2026-11-25", "steps": 12000}],
        "warnings": ["新警告"],
    }
    merged = merge_bundles(existing, incoming)
    assert merged["fetched_at"] == "2026-11-26T12:00:00+08:00"
    assert merged["profile"]["garmin_name"] == "新名"
    assert merged["profile"]["sex"] == "male"
    assert {row["id"] for row in merged["activities"]} == {"old", "new"}
    assert {row["date"] for row in merged["days"]} == {"2026-09-01", "2026-11-25"}
    assert merged["warnings"] == ["新警告"]


def test_first_sync_uses_backfill_window():
    start, end, mode = sync_window(existing=None, today=date(2026, 11, 26), backfill_days=90, overlap_days=2)
    assert mode == "backfill"
    assert end == date(2026, 11, 26)
    assert start == date(2026, 8, 29)
    assert (end - start).days + 1 == 90


def test_later_sync_uses_short_delta_with_overlap():
    existing = {
        "fetched_at": "2026-11-24T18:00:00+08:00",
        "days": [{"date": "2026-11-24", "steps": 1}],
        "activities": [],
    }
    start, end, mode = sync_window(existing=existing, today=date(2026, 11, 26), backfill_days=90, overlap_days=2)
    assert mode == "delta"
    assert end == date(2026, 11, 26)
    assert start == date(2026, 11, 22)


def test_rate_limit_blocks_too_soon_with_chinese_message():
    now = datetime(2026, 11, 26, 12, 0, tzinfo=SHANGHAI)
    status = evaluate_rate_limit(
        last_sync_at="2026-11-26T11:50:00+08:00",
        sync_day="2026-11-26",
        sync_count_today=1,
        min_interval_minutes=20,
        daily_cap=12,
        now=now,
    )
    assert status["can_sync"] is False
    assert status["rate_limited"] is True
    assert status["cooldown_seconds"] > 0
    assert "过于频繁" in status["message"]
    assert "20" in status["message"]


def test_rate_limit_blocks_daily_cap():
    now = datetime(2026, 11, 26, 23, 0, tzinfo=SHANGHAI)
    status = evaluate_rate_limit(
        last_sync_at="2026-11-26T08:00:00+08:00",
        sync_day="2026-11-26",
        sync_count_today=12,
        min_interval_minutes=20,
        daily_cap=12,
        now=now,
    )
    assert status["can_sync"] is False
    assert "上限" in status["message"]


def test_rate_limit_resets_on_new_shanghai_day():
    now = datetime(2026, 11, 27, 0, 30, tzinfo=SHANGHAI)
    status = evaluate_rate_limit(
        last_sync_at="2026-11-26T08:00:00+08:00",
        sync_day="2026-11-26",
        sync_count_today=12,
        min_interval_minutes=20,
        daily_cap=12,
        now=now,
    )
    assert status["can_sync"] is True
    assert status["syncs_today"] == 0
    day, count = next_sync_counters(sync_day="2026-11-26", sync_count_today=12, now=now)
    assert day == "2026-11-27"
    assert count == 1
