"""Metrics tests. They use fixtures only — no Garmin account and no network."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from app.analysis import (
    ACWR_SPIKE,
    analyze,
    acwr_series,
    banister_trimp,
    pmc_series,
    session_load,
)
from app.sample_data import build_sample_bundle


def test_pmc_two_days_of_42():
    # Day 1 starts from 0: TSB 0, CTL 42/42 = 1, ATL 42/7 = 6.
    # Day 2 TSB is yesterday's CTL − ATL = 1 − 6.
    out = pmc_series([42.0, 42.0])
    assert out["tsb"][0] == pytest.approx(0.0)
    assert out["ctl"][0] == pytest.approx(1.0)
    assert out["atl"][0] == pytest.approx(6.0)
    assert out["tsb"][1] == pytest.approx(-5.0)
    assert out["ctl"][1] == pytest.approx(1.0 + 41.0 / 42.0)
    assert out["atl"][1] == pytest.approx(6.0 + 36.0 / 7.0)


def test_pmc_empty():
    assert pmc_series([]) == {"ctl": [], "atl": [], "tsb": []}


def test_acwr_flat_ratio_is_one_and_spike_is_1_6():
    flat = acwr_series([10.0] * 28)
    assert flat[26] is None
    assert flat[27] == pytest.approx(1.0)

    spiked = [10.0] * 21 + [20.0] * 7
    # Chronic mean = (21*10 + 7*20) / 28 = 12.5; acute mean = 20; ratio = 1.6.
    assert acwr_series(spiked)[-1] == pytest.approx(1.6)
    assert acwr_series(spiked)[-1] > ACWR_SPIKE


def test_acwr_zero_chronic_is_none():
    assert acwr_series([0.0] * 28)[-1] is None


def test_banister_male_constants_and_edwards_fallback():
    hrr = (150 - 50) / (190 - 50)
    expected = 60 * hrr * 0.64 * math.exp(1.92 * hrr)
    assert banister_trimp(60, 150, 50, 190, "male") == pytest.approx(expected)
    assert expected == pytest.approx(108.1, rel=0.02)
    assert banister_trimp(60, 150, 50, 190, "female") != pytest.approx(expected)

    garmin, method = session_load(
        {"training_load": 42, "duration_s": 3600, "avg_hr": 150},
        sex="male",
        hr_rest=50,
        hr_max=190,
    )
    assert garmin == 42
    assert method == "garmin_training_load"

    edwards, method = session_load(
        {"duration_s": 3600, "avg_hr": 150},
        sex="unknown",
        hr_rest=50,
        hr_max=190,
    )
    assert method == "edwards_trimp"
    assert edwards == pytest.approx(180.0)

    proxy, method = session_load(
        {"duration_s": 1800},
        sex="unknown",
        hr_rest=None,
        hr_max=None,
    )
    assert method == "duration_proxy"
    assert proxy == pytest.approx(150.0)


def _bundle(loads, *, sleep=7.8, rhr=50, hrv=55, sex="unknown", age=None, hr_max=None, avg_hr=140):
    start = date(2026, 1, 1)
    days = []
    activities = []
    for index, load in enumerate(loads):
        day = start + timedelta(days=index)
        sleep_h = sleep[index] if isinstance(sleep, list) else sleep
        resting = rhr[index] if isinstance(rhr, list) else rhr
        hrv_value = hrv[index] if isinstance(hrv, list) else hrv
        days.append(
            {
                "date": day.isoformat(),
                "resting_hr": resting,
                "hrv": hrv_value,
                "sleep_seconds": None if sleep_h is None else sleep_h * 3600,
                "body_battery": 60,
                "stress": 20,
                "steps": 8000,
            }
        )
        if load:
            activities.append(
                {
                    "id": index,
                    "name": "run",
                    "type": "running",
                    "date": day.isoformat(),
                    "duration_s": 3600,
                    "distance_m": 10000,
                    "avg_hr": avg_hr,
                    "max_hr": None,
                    "training_load": load,
                    "aerobic_te": None,
                }
            )
    return {
        "sample": False,
        "profile": {"sex": sex, "age": age, "hr_max": hr_max},
        "activities": activities,
        "days": days,
    }


def _ids(report):
    return {item["id"] for item in report["flags"]}


def test_recovery_flag_when_load_spike_meets_short_sleep():
    loads = [30.0] * 40 + [90.0] * 7
    sleep = [7.8] * 43 + [5.0] * 4
    report = analyze(_bundle(loads, sleep=sleep))
    flags = _ids(report)
    assert "load_spike" in flags
    assert "sleep_short" in flags
    assert "recovery_needed" in flags
    assert report["summary"]["acwr"] == pytest.approx(2.0, abs=0.01)
    detail = next(item["detail"] for item in report["flags"] if item["id"] == "recovery_needed")
    assert "6.2" in detail
    assert "2.00" in detail


def test_short_sleep_alone_does_not_raise_recovery_flag():
    loads = [30.0] * 90
    sleep = [7.8] * 86 + [5.0] * 4
    report = analyze(_bundle(loads, sleep=sleep))
    flags = _ids(report)
    assert "sleep_short" in flags
    assert "recovery_needed" not in flags
    assert report["summary"]["acwr"] == pytest.approx(1.0, abs=0.02)
    assert report["summary"]["tsb"] > -10


def test_hrv_and_rhr_flags_use_personal_baseline():
    hrv = [60.0] * 87 + [50.0, 50.0, 50.0]
    hrv_report = analyze(_bundle([30.0] * 90, hrv=hrv))
    assert "hrv_suppressed" in _ids(hrv_report)
    assert "recovery_needed" not in _ids(hrv_report)

    rhr = [50.0] * 83 + [56.0] * 7
    rhr_report = analyze(_bundle([30.0] * 90, rhr=rhr))
    assert "rhr_elevated" in _ids(rhr_report)
    assert rhr_report["summary"]["rhr_delta"] >= 5


def test_tanaka_hr_max_when_user_did_not_set_one():
    report = analyze(
        {
            "sample": False,
            "profile": {"sex": "unknown", "age": 40},
            "activities": [
                {
                    "id": 1,
                    "name": "run",
                    "type": "running",
                    "date": "2026-01-01",
                    "duration_s": 3600,
                    "distance_m": 10000,
                    "avg_hr": 140,
                    "max_hr": None,
                    "training_load": None,
                    "aerobic_te": None,
                }
            ],
            "days": [{"date": "2026-01-01", "resting_hr": 50, "sleep_seconds": 8 * 3600}],
        }
    )
    assert report["summary"]["hr_max"] == pytest.approx(180.0)
    assert report["summary"]["load_method"] == "edwards_trimp"


def test_sample_report_is_labeled_and_grounded():
    report = analyze(build_sample_bundle(date(2026, 11, 26)))
    assert report["sample"] is True
    flags = _ids(report)
    assert "recovery_needed" in flags
    assert "load_spike" in flags
    acwr = report["summary"]["acwr"]
    assert acwr > ACWR_SPIKE
    text = "\n".join(report["suggestions"])
    assert f"{acwr:.2f}" in text
    assert "CTL" in text
    assert report["summary"]["intensity"]["low"] > 0.7
    assert "示例" in report["notes"][0]
