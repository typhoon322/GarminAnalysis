"""Built-in demo series so the page can draw charts before a Garmin login.

The last week is intentionally heavy, with a higher resting heart rate and
short sleep, so the recovery flags are visible. It is not a real person.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.clock import today_sh

SAMPLE_DAYS = 70
BASE_LOAD = [70, 45, 0, 80, 25, 110, 0]
SPIKE_LOAD = [150, 140, 160, 150, 170, 180, 100]
SPIKE_STRESS = [55, 58, 52, 60, 49, 57, 54]


def planned_load(index: int, total: int = SAMPLE_DAYS) -> float:
    if index >= total - 7:
        return float(SPIKE_LOAD[index - (total - 7)])
    return float(BASE_LOAD[index % 7])


def _activity(day: date, suffix: str, name: str, sport: str, minutes: int, avg_hr: int, max_hr: int, load: float, effect: float) -> dict:
    speed = 500 if sport == "cycling" else 160
    return {
        "id": f"sample-{day.isoformat()}-{suffix}",
        "name": name,
        "type": sport,
        "date": day.isoformat(),
        "duration_s": minutes * 60,
        "distance_m": speed * minutes,
        "avg_hr": avg_hr,
        "max_hr": max_hr,
        "training_load": load,
        "aerobic_te": effect,
    }


def _sessions(index: int, total: int, day: date, load: float) -> list[dict]:
    if load <= 0:
        return []
    kind = index % 7
    spike = index >= total - 7
    if kind == 5 or (spike and kind in {0, 3}):
        hard = round(min(40.0, load * 0.3), 2)
        easy = round(load - hard, 2)
        rows = []
        if easy > 0:
            rows.append(_activity(day, "easy", "轻松跑", "running", 50, 132, 150, easy, 2.6))
        if hard > 0:
            rows.append(_activity(day, "hard", "间歇", "running", 20, 176, 184, hard, 4.2))
        return rows
    if kind == 3:
        return [_activity(day, "tempo", "节奏跑", "running", 30, 162, 172, load, 3.4)]
    if kind == 1:
        minutes = 70 if spike else 45
        avg = 138 if spike else 125
        return [_activity(day, "bike", "轻松骑行", "cycling", minutes, avg, 160, load, 2.4)]
    return [_activity(day, "easy", "轻松跑", "running", 48, 132, 148, load, 2.5)]


def _wellness(index: int, total: int, day: date) -> dict:
    if index >= total - 7:
        resting = 57
        stress = SPIKE_STRESS[index - (total - 7)]
    else:
        resting = 50 + (index % 3) - 1
        stress = 28
    if index >= total - 3:
        hrv = 44
    else:
        hrv = 56
    if index >= total - 4:
        sleep_hours = 5.4
        score = 61
    else:
        sleep_hours = 7.6
        score = 82
    if index == total - 1:
        battery = 22
    elif index >= total - 3:
        battery = 30
    else:
        battery = 68
    return {
        "date": day.isoformat(),
        "resting_hr": resting,
        "hrv": hrv,
        "hrv_baseline_low": 48,
        "hrv_baseline_high": 66,
        "sleep_seconds": int(sleep_hours * 3600),
        "sleep_score": score,
        "body_battery": battery,
        "stress": stress,
        "steps": 9000 + (index % 5) * 400,
    }


def build_sample_bundle(end: date | None = None) -> dict:
    end = end or today_sh()
    total = SAMPLE_DAYS
    start = end - timedelta(days=total - 1)
    activities = []
    days = []
    for index in range(total):
        day = start + timedelta(days=index)
        load = planned_load(index, total)
        activities.extend(_sessions(index, total, day, load))
        days.append(_wellness(index, total, day))
    weekly = sum(planned_load(index, total) for index in range(total - 7, total))
    return {
        "sample": True,
        "fetched_at": None,
        "warnings": [],
        "profile": {
            "label": "示例跑步者",
            "sex": "male",
            "age": 36,
            "hr_max": 188,
            "hr_rest": 50,
            "vo2max_running": 48,
            "vo2max_cycling": 44,
            "training_status": "PRODUCTIVE",
            "weekly_training_load": weekly,
            "load_tunnel_min": 300,
            "load_tunnel_max": 600,
        },
        "activities": activities,
        "days": days,
    }
