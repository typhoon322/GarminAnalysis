"""Parsers for Garmin payloads. No live account."""

from app.normalize import (
    apply_body_battery,
    apply_heart_rate,
    apply_hrv,
    apply_sleep,
    apply_summaries,
    apply_training,
    normalize_activities,
)


def test_activity_ignores_nested_split_and_scales_milliseconds():
    payload = [
        {
            "activityId": 7,
            "activityName": "晨跑",
            "activityType": {"typeKey": "running"},
            "startTimeLocal": "2026-11-01 07:00:00",
            "duration": 3_600_000,
            "distance": 10000,
            "averageHR": 140,
            "maxHR": 168,
            "activityTrainingLoad": 80,
            "aerobicTrainingEffect": 3.1,
            "splits": [{"duration": 1800}],
        }
    ]
    rows = normalize_activities(payload)
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-11-01"
    assert rows[0]["duration_s"] == 3600
    assert rows[0]["distance_m"] == 10000
    assert rows[0]["training_load"] == 80
    assert rows[0]["type"] == "running"


def test_hrv_sleep_rhr_and_battery_shapes():
    days = {}
    apply_hrv(
        days,
        {
            "hrvSummaries": [
                {
                    "calendarDate": "2026-11-01",
                    "lastNightAvg": 48,
                    "weeklyAvg": 99,
                    "baseline": {"balancedLow": 40, "balancedUpper": 60},
                }
            ]
        },
    )
    apply_sleep(
        days,
        {"dailySleepDTOs": [{"calendarDate": "2026-11-01", "sleepTimeSeconds": 27000, "sleepScores": {"overall": {"value": 82}}}]},
    )
    apply_sleep(days, {"dailySleepDTO": {"calendarDate": "2026-11-02", "sleepTimeSeconds": 27_000_000, "sleepScores": {"overall": {"value": 70}}}})
    apply_heart_rate(days, [{"calendarDate": "2026-11-01", "restingHeartRate": 51, "maxHeartRate": 168}])
    apply_body_battery(days, [{"date": "2026-11-01", "bodyBatteryValuesArray": [[100, 70], [200, 42]]}])

    assert days["2026-11-01"]["hrv"] == 48
    assert days["2026-11-01"]["hrv_baseline_low"] == 40
    assert days["2026-11-01"]["hrv_baseline_high"] == 60
    assert days["2026-11-01"]["sleep_seconds"] == 27000
    assert days["2026-11-01"]["sleep_score"] == 82
    assert days["2026-11-02"]["sleep_seconds"] == 27000
    assert days["2026-11-01"]["resting_hr"] == 51
    assert days["2026-11-01"]["body_battery"] == 42


def test_training_status_and_vo2():
    profile = {"sex": "unknown"}
    apply_training(
        profile,
        {
            "mostRecentVO2Max": {"generic": {"vo2MaxPreciseValue": 49.2}, "cycling": {"vo2MaxValue": 45}},
            "latestTrainingStatusData": {
                "123": {
                    "weeklyTrainingLoad": 640,
                    "trainingStatus": "PRODUCTIVE",
                    "loadTunnelMin": 400,
                    "loadTunnelMax": 700,
                    "acuteTrainingLoadDTO": {"dailyTrainingLoadAcute": 70, "dailyTrainingLoadChronic": 55},
                }
            },
        },
        None,
    )
    assert profile["vo2max_running"] == 49.2
    assert profile["vo2max_cycling"] == 45
    assert profile["training_status"] == "PRODUCTIVE"
    assert profile["weekly_training_load"] == 640
    assert profile["load_tunnel_min"] == 400
    assert profile["garmin_acute_load"] == 70


def test_personal_api_nested_daily_summary():
    days = {}
    apply_summaries(
        days,
        {
            "data": [
                {
                    "calendarDate": "2026-11-20",
                    "heartRate": {"restingValue": 52, "maxValue": 168},
                    "stress": {"avgLevel": 28},
                    "movement": {"steps": {"goal": 7500, "value": 9000}},
                    "bodyBattery": {"latestValue": 61, "endOfDayValue": 55},
                }
            ]
        },
    )
    assert days["2026-11-20"]["resting_hr"] == 52
    assert days["2026-11-20"]["max_hr"] == 168
    assert days["2026-11-20"]["stress"] == 28
    assert days["2026-11-20"]["steps"] == 9000
    assert days["2026-11-20"]["body_battery"] == 61


def test_personal_api_stress_stats_nested_values():
    from app.normalize import apply_stress

    days = {}
    apply_stress(
        days,
        [{"calendarDate": "2026-11-20", "values": {"overallStressLevel": 28}}],
    )
    assert days["2026-11-20"]["stress"] == 28
