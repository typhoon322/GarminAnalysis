"""Turn Garmin Connect payloads into the bundle shape analyze() expects.

The live JSON varies by firmware and library version, so extractors look for
known field names and ignore the rest. They do not invent values.
"""

from __future__ import annotations

import math
from datetime import datetime


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or math.isinf(number):
        return None
    return number


def _day(value) -> str | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    text = value[:10]
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


def _date_of(node: dict) -> str | None:
    for key in ("calendarDate", "date", "summaryDate", "calendarDay"):
        stamp = _day(node.get(key))
        if stamp:
            return stamp
    for key in ("dailySleepDTO", "hrvSummary"):
        nested = node.get(key)
        if isinstance(nested, dict):
            stamp = _date_of(nested)
            if stamp:
                return stamp
    start = node.get("startTimeLocal") or node.get("startTimeGMT")
    if isinstance(start, str):
        return _day(start.replace("T", " "))
    return None


def _walk(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from _walk(value)


def _first(node: dict, keys: tuple[str, ...]):
    for key in keys:
        value = _num(node.get(key))
        if value is not None:
            return value
    return None


def normalize_activities(payload) -> list[dict]:
    rows = []
    seen = set()
    for node in _walk(payload):
        stamp = _date_of(node)
        if stamp is None:
            continue
        duration = _num(node.get("duration"))
        if duration is None:
            duration = _num(node.get("durationInSeconds"))
        if duration is None:
            continue
        # Activity list duration is seconds. A value past ~11 days is treated
        # as milliseconds (a 1-hour session in ms is 3_600_000).
        if duration > 1_000_000:
            duration = duration / 1000.0
        if duration <= 0 or duration > 48 * 3600:
            continue
        # Skip nested splits that have a duration but are not the activity itself.
        if (
            node.get("activityId") is None
            and node.get("activityUUID") is None
            and "activityType" not in node
            and "activityName" not in node
            and "activityTypeKey" not in node
        ):
            continue
        type_node = node.get("activityType")
        if isinstance(type_node, dict):
            sport = str(type_node.get("typeKey") or type_node.get("typeId") or "other")
        elif isinstance(type_node, str):
            sport = type_node
        else:
            sport = str(node.get("activityTypeKey") or "other")
        activity_id = node.get("activityId") or node.get("activityUUID")
        if activity_id is not None:
            if activity_id in seen:
                continue
            seen.add(activity_id)
        distance = _num(node.get("distance"))
        if distance is None:
            distance = _num(node.get("distanceInMeters")) or 0.0
        rows.append(
            {
                "id": activity_id,
                "name": str(node.get("activityName") or node.get("name") or ""),
                "type": sport,
                "date": stamp,
                "duration_s": duration,
                "distance_m": max(0.0, distance),
                "avg_hr": _first(node, ("averageHR", "avgHr", "averageHeartRate")),
                "max_hr": _first(node, ("maxHR", "maxHr", "maxHeartRate")),
                "training_load": _first(node, ("activityTrainingLoad", "trainingLoad")),
                "aerobic_te": _first(node, ("aerobicTrainingEffect", "aerobicTrainingEffectValue")),
            }
        )
    return rows


def _sleep_score(source: dict):
    scores = source.get("sleepScores")
    if isinstance(scores, dict):
        overall = scores.get("overall")
        if isinstance(overall, dict):
            value = _num(overall.get("value"))
            if value is not None:
                return value
        if isinstance(overall, (int, float)) and not isinstance(overall, bool):
            return float(overall)
    return _first(source, ("sleepScore", "overallScore"))


def _apply(days: dict[str, dict], payload, extractor) -> None:
    if payload is None:
        return
    for node in _walk(payload):
        stamp = _date_of(node)
        if not stamp:
            continue
        fields = extractor(node)
        if not fields:
            continue
        row = days.setdefault(stamp, {"date": stamp})
        for key, value in fields.items():
            if value is not None:
                row[key] = value


def _hrv_fields(node: dict) -> dict:
    source = node.get("hrvSummary") if isinstance(node.get("hrvSummary"), dict) else node
    # Nightly average only. weeklyAvg is a different window and would blur the 3-night check.
    value = _first(source, ("lastNightAvg", "hrvValue"))
    if value is None or value <= 0 or value > 300:
        return {}
    baseline = source.get("baseline") if isinstance(source.get("baseline"), dict) else {}
    low = _num(baseline.get("balancedLow")) if baseline else None
    high = _num(baseline.get("balancedUpper")) if baseline else None
    if low is None:
        low = _first(source, ("baselineLow", "balancedLow"))
    if high is None:
        high = _first(source, ("baselineUpper", "balancedUpper"))
    fields = {"hrv": value}
    if low is not None:
        fields["hrv_baseline_low"] = low
    if high is not None:
        fields["hrv_baseline_high"] = high
    return fields


def _sleep_fields(node: dict) -> dict:
    source = node.get("dailySleepDTO") if isinstance(node.get("dailySleepDTO"), dict) else node
    seconds = _first(source, ("sleepTimeSeconds", "sleepTimeInSeconds"))
    if seconds is None:
        return {}
    if seconds > 100_000:
        seconds = seconds / 1000.0
    if seconds <= 0 or seconds > 24 * 3600:
        return {}
    fields = {"sleep_seconds": seconds}
    score = _sleep_score(source)
    if score is not None and 0 <= score <= 100:
        fields["sleep_score"] = score
    return fields


def _rhr_fields(node: dict) -> dict:
    value = _first(node, ("restingHeartRate", "restingHR"))
    if value is None or value <= 25 or value > 120:
        return {}
    fields = {"resting_hr": value}
    maximum = _first(node, ("maxHeartRate", "maxHR"))
    if maximum is not None and 80 <= maximum <= 230:
        fields["max_hr"] = maximum
    return fields


def _stress_fields(node: dict) -> dict:
    value = _first(node, ("overallStressLevel", "averageStressLevel", "avgStressLevel"))
    if value is None or value < 0 or value > 100:
        return {}
    return {"stress": value}


def _steps_fields(node: dict) -> dict:
    value = _first(node, ("totalSteps", "steps"))
    if value is None or value < 0 or value > 200_000:
        return {}
    return {"steps": value}


def _battery_fields(node: dict) -> dict:
    level = None
    series = node.get("bodyBatteryValuesArray")
    if isinstance(series, list) and series:
        last = series[-1]
        if isinstance(last, (list, tuple)) and len(last) >= 2:
            level = _num(last[1])
        elif isinstance(last, dict):
            level = _num(last.get("value") if last.get("value") is not None else last.get("level"))
    if level is None:
        level = _first(
            node,
            (
                "bodyBatteryMostRecentValue",
                "endOfDayBodyBattery",
                "bodyBattery",
                "bodyBatteryHigh",
            ),
        )
    if level is None or level < 0 or level > 100:
        return {}
    return {"body_battery": level}


def _summary_fields(node: dict) -> dict:
    fields = {}
    fields.update(_rhr_fields(node))
    fields.update(_stress_fields(node))
    fields.update(_steps_fields(node))
    fields.update(_battery_fields(node))
    return fields


def apply_hrv(days: dict[str, dict], payload) -> None:
    _apply(days, payload, _hrv_fields)


def apply_sleep(days: dict[str, dict], payload) -> None:
    _apply(days, payload, _sleep_fields)


def apply_heart_rate(days: dict[str, dict], payload) -> None:
    _apply(days, payload, _rhr_fields)


def apply_stress(days: dict[str, dict], payload) -> None:
    _apply(days, payload, _stress_fields)


def apply_steps(days: dict[str, dict], payload) -> None:
    _apply(days, payload, _steps_fields)


def apply_body_battery(days: dict[str, dict], payload) -> None:
    _apply(days, payload, _battery_fields)


def apply_summaries(days: dict[str, dict], payload) -> None:
    _apply(days, payload, _summary_fields)


def _find_labeled_vo2(payload) -> dict:
    found = {"running": None, "cycling": None}

    def visit(node, parent: str) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item, parent)
            return
        if not isinstance(node, dict):
            return
        precise = _num(node.get("vo2MaxPreciseValue"))
        rough = _num(node.get("vo2MaxValue"))
        value = precise if precise is not None else rough
        if value is not None and 10 <= value <= 90:
            key = parent.lower()
            if "cycl" in key or "bike" in key:
                found["cycling"] = found["cycling"] if found["cycling"] is not None else value
            elif key in {"generic", "running"} or "run" in key:
                found["running"] = found["running"] if found["running"] is not None else value
            elif found["running"] is None:
                found["running"] = value
        for key, child in node.items():
            if isinstance(child, (dict, list)):
                visit(child, str(key))

    visit(payload, "")
    return found


def _training_records(payload) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    blob = payload.get("latestTrainingStatusData") or payload.get("mostRecentTrainingStatus") or payload
    records = []

    def consider(node: dict) -> None:
        if any(key in node for key in ("trainingStatus", "weeklyTrainingLoad", "trainingStatusPhrase")):
            records.append(node)

    if isinstance(blob, dict):
        consider(blob)
        for value in blob.values():
            if isinstance(value, dict):
                consider(value)
                for inner in value.values():
                    if isinstance(inner, dict):
                        consider(inner)
    return records


def apply_training(profile: dict, status_payload, vo2_payload) -> None:
    vo2 = _find_labeled_vo2(vo2_payload if vo2_payload is not None else status_payload)
    if status_payload is not None and (vo2["running"] is None or vo2["cycling"] is None):
        extra = _find_labeled_vo2(status_payload)
        vo2["running"] = vo2["running"] if vo2["running"] is not None else extra["running"]
        vo2["cycling"] = vo2["cycling"] if vo2["cycling"] is not None else extra["cycling"]
    if vo2["running"] is not None:
        profile["vo2max_running"] = vo2["running"]
    if vo2["cycling"] is not None:
        profile["vo2max_cycling"] = vo2["cycling"]
    records = _training_records(status_payload)
    if not records:
        return
    chosen = records[-1]
    status = chosen.get("trainingStatus")
    if status is None:
        status = chosen.get("trainingStatusPhrase")
    if status is not None:
        profile["training_status"] = status
    weekly = _num(chosen.get("weeklyTrainingLoad"))
    if weekly is not None:
        profile["weekly_training_load"] = weekly
    low = _num(chosen.get("loadTunnelMin"))
    high = _num(chosen.get("loadTunnelMax"))
    if low is not None:
        profile["load_tunnel_min"] = low
    if high is not None:
        profile["load_tunnel_max"] = high
    acute = chosen.get("acuteTrainingLoadDTO")
    if isinstance(acute, dict):
        acute_load = _num(acute.get("dailyTrainingLoadAcute"))
        chronic_load = _num(acute.get("dailyTrainingLoadChronic"))
        if acute_load is not None:
            profile["garmin_acute_load"] = acute_load
        if chronic_load is not None:
            profile["garmin_chronic_load"] = chronic_load


def apply_profile_demographics(profile: dict, payload) -> None:
    """Fill sex or age only when the local profile left them blank."""
    if not payload:
        return
    gender = None
    birth = None
    for node in _walk(payload):
        if gender is None:
            raw = node.get("gender") or node.get("genderType")
            if isinstance(raw, str):
                gender = raw
        if birth is None:
            raw_birth = node.get("birthDate") or node.get("birthdate")
            if isinstance(raw_birth, str):
                birth = raw_birth
    if profile.get("sex") in {None, "", "unknown"} and isinstance(gender, str):
        token = gender.strip().upper()
        if token in {"M", "MALE"}:
            profile["sex"] = "male"
        elif token in {"F", "FEMALE"}:
            profile["sex"] = "female"
    if profile.get("age") is None and isinstance(birth, str) and len(birth) >= 10:
        try:
            born = datetime.strptime(birth[:10], "%Y-%m-%d").date()
        except ValueError:
            return
        today = datetime.now().date()
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        if 10 <= age <= 100:
            profile["age"] = age
