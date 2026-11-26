"""Training-load and recovery metrics.

Formulas live next to the constants they use. The numbers are coaching
heuristics with cited origins, not clinical cut-points and not fake precision.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import median

from app.clock import iso_sh

# TrainingPeaks / Coggan performance-management chart.
# CTL (fitness) time constant 42 days, ATL (fatigue) 7 days.
# TSB (form) on a day is the previous day's CTL minus the previous day's ATL.
CTL_TAU = 42.0
ATL_TAU = 7.0

# TSB bands are coaching round numbers, not clinical cut-points.
TSB_VERY_FRESH = 25.0
TSB_FRESH = 5.0
TSB_NEUTRAL_LOW = -10.0
TSB_HIGH_FATIGUE = -30.0

# Coupled acute:chronic workload ratio (Hulin et al.; Gabbett 2016, BJSM).
# The often-cited "sweet spot" is about 0.8–1.3. This app flags > 1.5 as a spike.
# Later work (Impellizzeri and others) showed the coupled ratio can mislead.
# Treat it as a load-jump hint, not an injury probability.
ACWR_ACUTE_DAYS = 7
ACWR_CHRONIC_DAYS = 28
ACWR_LOW = 0.8
ACWR_HIGH = 1.3
ACWR_SPIKE = 1.5

# Resting HR: flag a rise of at least 5 bpm versus the person's own prior mean.
# A few bpm is the practical monitoring range in Buchheit's HR reviews;
# 5 bpm is a round heuristic, not a diagnosis.
RHR_RISE_BPM = 5.0

# HRV: 3-reading mean versus the median of up to 28 preceding nights.
# Flag under 90% of that median. Day-to-day RMSSD scatter is often around
# this size (Plews et al. on the smallest worthwhile change), so 90% is a
# "worth a look" line, not a clinical threshold. Values are Garmin's nightly
# average, usually RMSSD in milliseconds, and are not rescaled.
HRV_DROP_RATIO = 0.90

# Adult sleep is often cited around 7–9 hours (National Sleep Foundation).
# Flag a 7-day mean under 7 h, or one night under 6 h.
SLEEP_MEAN_HOURS = 7.0
SLEEP_NIGHT_HOURS = 6.0

# Seiler's polarized split is roughly 80% below VT1 and 20% above VT2.
# Without lactate, HR < 80% HRmax is low and HR >= 90% HRmax is high.
# A whole activity is bucketed by its average HR, which is coarser than
# second-by-second time in zone.
LOW_HR_FRAC = 0.80
HIGH_HR_FRAC = 0.90

# Garmin's own stress bands are about 0–25 rest, 26–50 low, 51–75 medium,
# 76–100 high. Body Battery is a 0–100 product score; under 25 is this
# app's round "low" line, not a published danger cut.
STRESS_ELEVATED = 51.0
BATTERY_LOW = 25.0

# CTL/ATL start at 0, and ATL rises first. Don't call that high fatigue
# until a few weeks exist. ACWR already needs 28 days.
MIN_DAYS_FOR_FATIGUE_FLAG = 21

# Last-resort load: minutes × 5, a flat placeholder in the shape of session
# RPE (RPE 5). Do not compare it with TSS or with someone else's TRIMP.
DURATION_PROXY_RPE = 5.0

METHOD_LABELS = {
    "garmin_training_load": "佳明活动训练负荷（不是 TrainingPeaks 的 TSS）",
    "banister_trimp": "Banister TRIMP",
    "edwards_trimp": "Edwards 分区 TRIMP（按平均心率把整段放进一个区）",
    "duration_proxy": "时长×5 的粗代理（没有心率，也没有佳明负荷）",
    "mixed": "混合口径",
    "none": "没有负荷",
}

INTENSITY_LABELS = {
    "hr": "按活动平均心率粗分",
    "te": "按佳明有氧训练效果粗分",
    "mixed": "心率和训练效果混用",
}

STATUS_ZH = {
    "PRODUCTIVE": "有成效",
    "MAINTAINING": "维持",
    "RECOVERY": "恢复",
    "UNPRODUCTIVE": "成效不足",
    "DETRAINING": "体能下降",
    "OVERREACHING": "过度",
    "PEAKING": "巅峰",
    "NO_STATUS": "无状态",
    "PAUSED": "暂停",
}

SPORT_ZH = {
    "running": "跑步",
    "trail_running": "越野跑",
    "treadmill_running": "跑步机",
    "cycling": "骑行",
    "road_biking": "公路骑行",
    "mountain_biking": "山地骑行",
    "virtual_ride": "室内骑行",
    "swimming": "游泳",
    "lap_swimming": "泳池游泳",
    "open_water_swimming": "公开水域",
    "walking": "步行",
    "hiking": "徒步",
    "strength_training": "力量",
    "fitness_equipment": "器械",
    "yoga": "瑜伽",
    "indoor_cardio": "室内有氧",
    "elliptical": "椭圆机",
    "stair_climbing": "爬楼",
    "rowing": "划船",
    "indoor_rowing": "室内划船",
}


def _float(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or math.isinf(number):
        return None
    return number


def fmt_num(value, digits: int = 1) -> str:
    number = _float(value)
    if number is None:
        return "—"
    if abs(number - round(number)) < 0.05:
        return str(int(round(number)))
    return f"{number:.{digits}f}"


def fmt_acwr(value) -> str:
    number = _float(value)
    if number is None:
        return "—"
    return f"{number:.2f}"


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _parse_date(value) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _daterange(start: date, end: date) -> list[date]:
    days = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def pmc_series(loads: list[float], ctl0: float = 0.0, atl0: float = 0.0) -> dict[str, list[float]]:
    """CTL, ATL, and TSB aligned to each load.

    ctl_t = ctl_(t-1) + (load_t - ctl_(t-1)) / 42
    atl_t = atl_(t-1) + (load_t - atl_(t-1)) / 7
    tsb_t = ctl_(t-1) - atl_(t-1)
    """
    ctl: list[float] = []
    atl: list[float] = []
    tsb: list[float] = []
    fitness = float(ctl0)
    fatigue = float(atl0)
    for load in loads:
        tsb.append(fitness - fatigue)
        fitness = fitness + (load - fitness) / CTL_TAU
        fatigue = fatigue + (load - fatigue) / ATL_TAU
        ctl.append(fitness)
        atl.append(fatigue)
    return {"ctl": ctl, "atl": atl, "tsb": tsb}


def acwr_series(loads: list[float]) -> list[float | None]:
    """Coupled ACWR = mean(last 7 daily loads) / mean(last 28 daily loads).

    None until 28 days exist, and None when the chronic mean is 0.
    """
    out: list[float | None] = []
    for index in range(len(loads)):
        if index + 1 < ACWR_CHRONIC_DAYS:
            out.append(None)
            continue
        chronic = loads[index + 1 - ACWR_CHRONIC_DAYS : index + 1]
        acute = loads[index + 1 - ACWR_ACUTE_DAYS : index + 1]
        chronic_mean = sum(chronic) / ACWR_CHRONIC_DAYS
        if chronic_mean <= 0:
            out.append(None)
            continue
        out.append((sum(acute) / ACWR_ACUTE_DAYS) / chronic_mean)
    return out


def banister_trimp(duration_min: float, hr_avg: float, hr_rest: float, hr_max: float, sex: str) -> float:
    """Banister TRIMP from average heart rate.

    HRr = (HR_avg - HR_rest) / (HR_max - HR_rest)
    male:   minutes * HRr * 0.64 * exp(1.92 * HRr)
    female: minutes * HRr * 0.86 * exp(1.67 * HRr)

    The sex-specific constants are the ones commonly reproduced from the
    Banister impulse-response literature. This is not a beat-by-beat integral.
    """
    span = hr_max - hr_rest
    if duration_min <= 0 or span <= 0:
        return 0.0
    hrr = (hr_avg - hr_rest) / span
    hrr = min(1.0, max(0.0, hrr))
    if sex == "female":
        return duration_min * hrr * 0.86 * math.exp(1.67 * hrr)
    return duration_min * hrr * 0.64 * math.exp(1.92 * hrr)


def edwards_trimp(duration_min: float, hr_avg: float, hr_max: float) -> float:
    """Edwards zone TRIMP (1993), sex-neutral.

    Weight 1 through 5 for 50–60% HRmax up to 90–100%. Below 50% adds 0.
    The whole activity sits in the zone of its average heart rate.
    """
    if duration_min <= 0 or hr_max <= 0:
        return 0.0
    frac = hr_avg / hr_max
    if frac < 0.50:
        weight = 0
    elif frac < 0.60:
        weight = 1
    elif frac < 0.70:
        weight = 2
    elif frac < 0.80:
        weight = 3
    elif frac < 0.90:
        weight = 4
    else:
        weight = 5
    return duration_min * weight


def session_load(activity: dict, *, sex: str, hr_rest: float | None, hr_max: float | None) -> tuple[float, str]:
    """Prefer Garmin's activityTrainingLoad. Do not mix it into a fake TSS.

    Otherwise Banister when sex, resting HR, and HRmax are all known;
    otherwise Edwards from average HR; otherwise minutes × 5.
    """
    garmin = _float(activity.get("training_load"))
    if garmin is not None:
        return max(0.0, garmin), "garmin_training_load"
    duration_min = max(0.0, _float(activity.get("duration_s")) or 0.0) / 60.0
    avg = _float(activity.get("avg_hr"))
    if avg and hr_max and hr_rest and sex in {"male", "female"} and hr_max > hr_rest:
        return banister_trimp(duration_min, avg, hr_rest, hr_max, sex), "banister_trimp"
    if avg and hr_max:
        return edwards_trimp(duration_min, avg, hr_max), "edwards_trimp"
    if duration_min <= 0:
        return 0.0, "none"
    return duration_min * DURATION_PROXY_RPE, "duration_proxy"


def _sport_name(type_key: str) -> str:
    if type_key in SPORT_ZH:
        return SPORT_ZH[type_key]
    text = (type_key or "other").replace("_", " ")
    return text or "其他"


def _status_label(status) -> str | None:
    if status is None or status == "":
        return None
    if isinstance(status, (int, float)) and not isinstance(status, bool):
        return f"状态码 {int(status)}"
    text = str(status)
    return STATUS_ZH.get(text.upper(), text)


def resolve_hr_max(profile: dict, activities: list[dict]) -> tuple[float | None, str | None]:
    """User value, else Tanaka 208 − 0.7×age, else the highest HR seen.

    If an activity max exceeds the Tanaka estimate, keep the higher one.
    A seen maximum can still be short of true HRmax; the label says so
    when that is the only source.
    """
    stated = _float(profile.get("hr_max"))
    if stated and stated > 0:
        return stated, "用户填写"
    observed_vals = [value for value in (_float(item.get("max_hr")) for item in activities) if value and value > 0]
    observed = max(observed_vals) if observed_vals else None
    age = _float(profile.get("age"))
    tanaka = 208.0 - 0.7 * age if age and age > 0 else None
    if tanaka and observed:
        if observed > tanaka:
            return observed, "活动最高心率高于年龄估算，采用活动最高心率"
        return tanaka, "Tanaka 公式 208 − 0.7 × 年龄"
    if tanaka:
        return tanaka, "Tanaka 公式 208 − 0.7 × 年龄"
    if observed:
        return observed, "活动里见到的最高心率（可能低于真实最大心率）"
    return None, None


def _classify(activity: dict, hr_max: float | None) -> tuple[str | None, str | None]:
    avg = _float(activity.get("avg_hr"))
    if avg and hr_max:
        frac = avg / hr_max
        if frac < LOW_HR_FRAC:
            return "low", "hr"
        if frac >= HIGH_HR_FRAC:
            return "high", "hr"
        return "mid", "hr"
    # Garmin aerobic training effect is about 1 minor, 2 maintaining,
    # 3 improving, 4 highly impacting. Buckets: <3 low, <4 mid, else high.
    effect = _float(activity.get("aerobic_te"))
    if effect is None:
        return None, None
    if effect < 3.0:
        return "low", "te"
    if effect < 4.0:
        return "mid", "te"
    return "high", "te"


def _tail(values: list, size: int) -> list:
    return [item for item in values[-size:] if item is not None]


def rhr_status(values: list[float | None]) -> dict:
    recent_vals = _tail(values, 7)
    prior = [item for item in values[:-7][-28:] if item is not None] if len(values) > 7 else []
    recent = _mean(recent_vals)
    baseline = _mean(prior)
    delta = None if recent is None or baseline is None else recent - baseline
    elevated = (
        delta is not None
        and delta >= RHR_RISE_BPM
        and len(recent_vals) >= 3
        and len(prior) >= 7
    )
    return {"recent": recent, "baseline": baseline, "delta": delta, "elevated": elevated}


def hrv_status(values: list[float | None]) -> dict:
    valid_idx = [index for index, value in enumerate(values) if value is not None]
    empty = {"recent": None, "baseline": None, "ratio": None, "suppressed": False, "stale": not valid_idx, "last": None}
    if len(valid_idx) < 3:
        return empty
    last3 = valid_idx[-3:]
    recent_vals = [values[index] for index in last3]
    recent = _mean(recent_vals)
    last = recent_vals[-1]
    stale = (len(values) - 1 - last3[-1]) > 3
    prior_idx = [index for index in valid_idx if index < last3[0]]
    prior = [values[index] for index in prior_idx[-28:]]
    if stale or len(prior) < 7 or not prior:
        return {"recent": recent, "baseline": None, "ratio": None, "suppressed": False, "stale": stale, "last": last}
    baseline = float(median(prior))
    ratio = None if baseline <= 0 or recent is None else recent / baseline
    suppressed = ratio is not None and ratio < HRV_DROP_RATIO
    return {
        "recent": recent,
        "baseline": baseline,
        "ratio": ratio,
        "suppressed": suppressed,
        "stale": False,
        "last": last,
    }


def sleep_status(hours: list[float | None]) -> dict:
    last = None
    for value in reversed(hours[-2:]):
        if value is not None:
            last = value
            break
    recent_vals = _tail(hours, 7)
    mean7 = _mean(recent_vals) if len(recent_vals) >= 3 else None
    short = (mean7 is not None and mean7 < SLEEP_MEAN_HOURS) or (last is not None and last < SLEEP_NIGHT_HOURS)
    return {"mean7": mean7, "last": last, "short": short}


def _round(value, digits: int):
    number = _float(value)
    if number is None:
        return None
    return round(number, digits)


def _tsb_words(tsb: float | None) -> str:
    if tsb is None:
        return "没有足够的负荷，TSB 算不出来。"
    if tsb > TSB_VERY_FRESH:
        return "TSB 高于 +25，常用说法是很轻松；如果已经轻松了很多天，体能可能在往下走。"
    if tsb >= TSB_FRESH:
        return "TSB 在 +5 到 +25，常用说法是比较轻松，适合质量课或比赛。"
    if tsb >= TSB_NEUTRAL_LOW:
        return "TSB 在 -10 到 +5，常用说法是中性，处在正常训练区间。"
    if tsb >= TSB_HIGH_FATIGUE:
        return "TSB 在 -30 到 -10，常用说法是有些累，短时间可以，不宜长期停在这里。"
    return "TSB 低于 -30，本工具把它标为高疲劳。"


def _acwr_words(acwr: float | None) -> str:
    if acwr is None:
        return ""
    if acwr > ACWR_SPIKE:
        return "这高于本工具的尖峰线。"
    if acwr > ACWR_HIGH:
        return "这高于常引区间的上沿，但还没到本工具的尖峰线。"
    if acwr < ACWR_LOW:
        return "这低于常引区间的下沿。"
    return "这落在常引区间里面。"


def _next_step(summary: dict, flag_ids: set[str]) -> str:
    tsb_s = fmt_num(summary.get("tsb"))
    acwr_s = fmt_acwr(summary.get("acwr"))
    if "recovery_needed" in flag_ids:
        return (
            f"今天宜减量。TSB 是 {tsb_s}，ACWR 是 {acwr_s}，"
            "并且睡眠、静息心率、HRV 里至少有一项偏离你自己的基线。"
            f"先做轻松有氧，睡眠靠近每晚 {SLEEP_MEAN_HOURS:.0f} 小时，再加强度。"
        )
    if "high_fatigue" in flag_ids:
        return (
            f"TSB {tsb_s} 低于 {TSB_HIGH_FATIGUE:.0f}。接下来几天减少负荷，"
            f"等 TSB 回到 {TSB_NEUTRAL_LOW:.0f} 以上再排质量课。"
        )
    if "load_spike" in flag_ids:
        return (
            f"ACWR {acwr_s} 高于 {ACWR_SPIKE:.1f}。近 7 日平均负荷 {fmt_num(summary.get('acute_load'))}，"
            f"近 28 日平均 {fmt_num(summary.get('chronic_load'))}。后面几天把日负荷放在慢性平均附近或以下。"
        )
    if flag_ids & {"rhr_elevated", "hrv_suppressed", "sleep_short"}:
        return f"负荷先不要加。TSB {tsb_s}，ACWR {acwr_s}。今天把质量课换成轻松有氧，看下一次静息心率和睡眠。"
    if "load_low" in flag_ids:
        return (
            f"ACWR {acwr_s} 低于 {ACWR_LOW:.1f}。如果主观感觉正常，本周时长可以小幅加上去，"
            "一次增加大约一成就够，不必跳很远。"
        )
    if summary.get("acwr") is None or summary.get("tsb") is None:
        return "记录还太短，先按能恢复的时长练，等满 28 天再看 ACWR。"
    return (
        f"没有触发恢复或负荷警报。TSB {tsb_s}，ACWR {acwr_s}。"
        "可以按最近 7 天的时长继续，质量课仍放在你感觉睡够的日子。"
    )


def _suggestions(summary: dict, flag_ids: set[str]) -> list[str]:
    lines = []
    if summary.get("ctl") is None:
        lines.append("没有活动负荷，算不出 CTL、ATL、TSB。")
    else:
        lines.append(
            f"CTL {fmt_num(summary.get('ctl'))}，ATL {fmt_num(summary.get('atl'))}，"
            f"TSB {fmt_num(summary.get('tsb'))}（前一天的 CTL 减前一天的 ATL）。{_tsb_words(summary.get('tsb'))}"
        )
    if summary.get("acwr") is None:
        lines.append(
            f"ACWR 需要连续 {ACWR_CHRONIC_DAYS} 天负荷。当前这段有 {summary.get('history_days') or 0} 天。"
        )
    else:
        lines.append(
            f"ACWR {fmt_acwr(summary.get('acwr'))}"
            f"（近 7 日平均负荷 {fmt_num(summary.get('acute_load'))}，"
            f"近 28 日平均负荷 {fmt_num(summary.get('chronic_load'))}）。"
            f"文献里常提到的区间大约是 {ACWR_LOW:.1f}–{ACWR_HIGH:.1f}；"
            f"本工具把高于 {ACWR_SPIKE:.1f} 标成尖峰。{_acwr_words(summary.get('acwr'))}"
        )
    if summary.get("weekly_hours") is not None:
        previous = ""
        if summary.get("previous_weekly_hours") is not None:
            previous = (
                f"再往前 7 天是 {summary['previous_weekly_hours']:.1f} 小时、"
                f"{summary.get('previous_weekly_km') or 0:.1f} 公里。"
            )
        lines.append(
            f"最近 7 天训练 {summary['weekly_hours']:.1f} 小时、{summary.get('weekly_km') or 0:.1f} 公里。{previous}"
        )
    if summary.get("rhr_7d") is not None and summary.get("rhr_baseline") is not None:
        lines.append(
            f"静息心率近 7 日 {fmt_num(summary['rhr_7d'])} bpm，"
            f"再往前的个人基线 {fmt_num(summary['rhr_baseline'])} bpm"
            f"（相差 {summary.get('rhr_delta') or 0:+.1f}）。"
        )
    if summary.get("hrv_3d") is not None and summary.get("hrv_baseline") is not None:
        ratio = summary.get("hrv_ratio")
        ratio_text = "—" if ratio is None else f"{ratio:.0%}"
        band = ""
        last = summary.get("hrv_last")
        low = summary.get("hrv_band_low")
        if last is not None and low is not None and last < low:
            band = f"最近一夜 {fmt_num(last)}，低于佳明基线下沿 {fmt_num(low)}。"
        lines.append(
            f"HRV 近 3 次 {fmt_num(summary['hrv_3d'])}，基线中位数 {fmt_num(summary['hrv_baseline'])}"
            f"（相当于基线的 {ratio_text}）。{band}"
        )
    if summary.get("sleep_7d_hours") is not None or summary.get("sleep_last_hours") is not None:
        mean_text = (
            f"近 7 日平均 {summary['sleep_7d_hours']:.1f} 小时。"
            if summary.get("sleep_7d_hours") is not None
            else ""
        )
        last_text = (
            f"最近一夜 {summary['sleep_last_hours']:.1f} 小时。"
            if summary.get("sleep_last_hours") is not None
            else ""
        )
        lines.append(f"睡眠{mean_text}{last_text}")
    intensity = summary.get("intensity")
    if intensity:
        lines.append(
            f"强度时间大约是低 {intensity['low']:.0%}、中 {intensity['mid']:.0%}、高 {intensity['high']:.0%}"
            f"（{intensity['method_label']}，共 {intensity['minutes']:.0f} 分钟）。"
            "极化训练常被说成大约 80% 低强度、20% 高强度，这是经验描述，不是你的个人阈值。"
        )
    lines.append(_next_step(summary, flag_ids))
    return lines


def _flag(flag_id: str, level: str, title: str, detail: str) -> dict:
    return {"id": flag_id, "level": level, "title": title, "detail": detail}


def _build_flags(summary: dict) -> list[dict]:
    flags = []
    history = summary.get("history_days") or 0
    tsb = summary.get("tsb")
    acwr = summary.get("acwr")
    signals = []
    if summary.get("hrv_suppressed"):
        signals.append(f"HRV 近 3 次是基线的 {summary.get('hrv_ratio') or 0:.0%}")
    if summary.get("rhr_elevated"):
        signals.append(f"静息心率比基线高 {summary.get('rhr_delta') or 0:.1f} bpm")
    if summary.get("sleep_short"):
        if summary.get("sleep_7d_hours") is not None and summary["sleep_7d_hours"] < SLEEP_MEAN_HOURS:
            signals.append(f"睡眠近 7 日 {summary['sleep_7d_hours']:.1f} 小时")
        elif summary.get("sleep_last_hours") is not None:
            signals.append(f"最近一夜睡眠 {summary['sleep_last_hours']:.1f} 小时")
    strain = []
    if tsb is not None and tsb < TSB_NEUTRAL_LOW:
        strain.append(f"TSB {fmt_num(tsb)}")
    if acwr is not None and acwr > ACWR_HIGH:
        strain.append(f"ACWR {fmt_acwr(acwr)}")
    if signals and strain:
        flags.append(
            _flag(
                "recovery_needed",
                "alert",
                "恢复不足",
                "恢复信号：" + "、".join(signals) + "。同时负荷偏高：" + "、".join(strain) + "。",
            )
        )
    if history >= MIN_DAYS_FOR_FATIGUE_FLAG and tsb is not None and tsb < TSB_HIGH_FATIGUE:
        flags.append(
            _flag(
                "high_fatigue",
                "alert",
                "高疲劳",
                f"TSB {fmt_num(tsb)}，低于 {TSB_HIGH_FATIGUE:.0f}。这条线是教练常用的整数，不是临床界值。",
            )
        )
    if acwr is not None and acwr > ACWR_SPIKE:
        flags.append(
            _flag(
                "load_spike",
                "warning",
                "负荷尖峰",
                f"ACWR {fmt_acwr(acwr)}，高于 {ACWR_SPIKE:.1f}。"
                f"近 7 日平均负荷 {fmt_num(summary.get('acute_load'))}，"
                f"近 28 日平均 {fmt_num(summary.get('chronic_load'))}。",
            )
        )
    elif acwr is not None and acwr > ACWR_HIGH:
        flags.append(
            _flag(
                "load_rising",
                "info",
                "负荷偏高",
                f"ACWR {fmt_acwr(acwr)}，高于常引区间上沿 {ACWR_HIGH:.1f}，还没到尖峰线 {ACWR_SPIKE:.1f}。",
            )
        )
    elif (
        acwr is not None
        and acwr < ACWR_LOW
        and (summary.get("chronic_load") or 0) > 1
    ):
        flags.append(
            _flag(
                "load_low",
                "info",
                "负荷偏低",
                f"ACWR {fmt_acwr(acwr)}，低于 {ACWR_LOW:.1f}。近 28 日平均负荷 {fmt_num(summary.get('chronic_load'))}。",
            )
        )
    if summary.get("rhr_elevated"):
        flags.append(
            _flag(
                "rhr_elevated",
                "warning",
                "静息心率升高",
                f"近 7 日 {fmt_num(summary.get('rhr_7d'))} bpm，基线 {fmt_num(summary.get('rhr_baseline'))} bpm，"
                f"相差 {summary.get('rhr_delta') or 0:+.1f}。标记线是升高达到 {RHR_RISE_BPM:.0f} bpm。",
            )
        )
    if summary.get("hrv_suppressed"):
        flags.append(
            _flag(
                "hrv_suppressed",
                "warning",
                "HRV 低于个人基线",
                f"近 3 次 {fmt_num(summary.get('hrv_3d'))}，前序中位数 {fmt_num(summary.get('hrv_baseline'))}，"
                f"相当于基线的 {summary.get('hrv_ratio') or 0:.0%}。标记线是 {HRV_DROP_RATIO:.0%}。",
            )
        )
    if summary.get("sleep_short"):
        flags.append(
            _flag(
                "sleep_short",
                "warning",
                "睡眠偏短",
                f"近 7 日平均 {fmt_num(summary.get('sleep_7d_hours'), 1)} 小时，"
                f"最近一夜 {fmt_num(summary.get('sleep_last_hours'), 1)} 小时。"
                f"标记线是 7 日均值短于 {SLEEP_MEAN_HOURS:.0f} 小时，或一夜短于 {SLEEP_NIGHT_HOURS:.0f} 小时。",
            )
        )
    garmin = summary.get("garmin") or {}
    weekly = _float(garmin.get("weekly_training_load"))
    low = _float(garmin.get("load_tunnel_min"))
    high = _float(garmin.get("load_tunnel_max"))
    if weekly is not None and low is not None and high is not None and (weekly < low or weekly > high):
        flags.append(
            _flag(
                "garmin_load_outside",
                "warning",
                "周负荷超出设备区间",
                f"佳明周训练负荷 {fmt_num(weekly)}，设备区间 {fmt_num(low)}–{fmt_num(high)}。"
                "区间来自佳明训练状态，不是本工具算的 CTL。",
            )
        )
    if summary.get("stress_7d") is not None and summary["stress_7d"] >= STRESS_ELEVATED:
        flags.append(
            _flag(
                "stress_high",
                "info",
                "压力偏高",
                f"近 7 日压力均值 {fmt_num(summary['stress_7d'])}。佳明这套分数里，51 以上大致落在中等及以上。",
            )
        )
    if summary.get("body_battery") is not None and summary["body_battery"] < BATTERY_LOW:
        flags.append(
            _flag(
                "battery_low",
                "info",
                "Body Battery 偏低",
                f"最近读数 {fmt_num(summary['body_battery'])}。本工具把低于 {BATTERY_LOW:.0f} 标出来，这是取整的提示线。",
            )
        )
    intensity = summary.get("intensity")
    if intensity and intensity["minutes"] >= 60 and intensity["mid"] > 0.40 and intensity["low"] < 0.70:
        flags.append(
            _flag(
                "not_polarized",
                "info",
                "强度不像极化分布",
                f"低强度 {intensity['low']:.0%}，中等 {intensity['mid']:.0%}，高强度 {intensity['high']:.0%}。"
                "中等超过 40% 且低强度不到 70% 时，看起来不像常说的 80/20。这是描述，不是处方。",
            )
        )
    if (
        history >= ACWR_CHRONIC_DAYS
        and tsb is not None
        and tsb > TSB_VERY_FRESH
        and (acwr is None or acwr <= ACWR_HIGH)
        and not any(item["level"] in {"alert", "warning"} for item in flags)
    ):
        flags.append(
            _flag(
                "fresh",
                "info",
                "状态很轻松",
                f"TSB {fmt_num(tsb)}，高于 +25。如果已经连续很多天很轻松，体能可能在掉。",
            )
        )
    order = {"alert": 0, "warning": 1, "info": 2}
    flags.sort(key=lambda item: order.get(item["level"], 9))
    return flags


def method_notes() -> list[dict]:
    return [
        {
            "title": "CTL / ATL / TSB",
            "text": (
                "CTL 与 ATL 是日负荷的指数加权平均，时间常数 42 天和 7 天"
                "（TrainingPeaks / Coggan 的体能管理图）。某一天的 TSB = 前一天的 CTL − 前一天的 ATL。"
                "从这段数据的第一天以 0 起步，更早的训练没有算进来。"
            ),
        },
        {
            "title": "TSB 怎么读",
            "text": (
                "高于 +25 很轻松；+5 到 +25 较轻松；-10 到 +5 中性；-30 到 -10 累；低于 -30 高疲劳。"
                "这些是教练常用的整数分区，不是临床界值。"
            ),
        },
        {
            "title": "ACWR",
            "text": (
                f"ACWR = 近 {ACWR_ACUTE_DAYS} 天平均日负荷 / 近 {ACWR_CHRONIC_DAYS} 天平均日负荷"
                "（耦合算法）。Hulin、Gabbett 2016 一系文献常把大约 "
                f"{ACWR_LOW:.1f}–{ACWR_HIGH:.1f} 叫做 sweet spot。"
                f"本工具满 {ACWR_CHRONIC_DAYS} 天才计算；高于 {ACWR_SPIKE:.1f} 标为尖峰，低于 {ACWR_LOW:.1f} 标为偏低。"
                "后来的研究指出这个比值会被数学结构带偏，所以它只是负荷跳变的提示，不是受伤概率。"
            ),
        },
        {
            "title": "负荷怎么来",
            "text": (
                "有佳明 activityTrainingLoad 时用它，它不是 TrainingPeaks 的 TSS。"
                "否则在知道性别、平均心率、静息心率和最大心率时用 Banister TRIMP"
                "（男：分钟 × HRr × 0.64 × e^(1.92×HRr)；女：分钟 × HRr × 0.86 × e^(1.67×HRr)）。"
                "性别未填时用 Edwards 五区 TRIMP，按平均心率把整段放进一个区。"
                "都没有时用时长分钟 × 5，只是占位。"
            ),
        },
        {
            "title": "静息心率与 HRV",
            "text": (
                f"静息心率：近 7 天均值对比再往前最多 28 天的均值，升高达到 {RHR_RISE_BPM:.0f} bpm 才标记。"
                f"HRV：近 3 次均值对比再往前最多 28 次的中位数，低于 {HRV_DROP_RATIO:.0%} 才标记。"
                "HRV 按佳明夜间平均值理解（通常是毫秒 RMSSD），不再换算。"
            ),
        },
        {
            "title": "睡眠与强度",
            "text": (
                f"睡眠：近 7 天平均短于 {SLEEP_MEAN_HOURS:.0f} 小时，或最近一夜短于 {SLEEP_NIGHT_HOURS:.0f} 小时，就标记。"
                f"强度：平均心率低于 {LOW_HR_FRAC:.0%} HRmax 算低，达到或高于 {HIGH_HR_FRAC:.0%} HRmax 算高。"
                "这是整段课用一个平均心率，不是逐秒分区，也不是实验室测的通气阈。"
                "没有心率时，有氧训练效果低于 3.0 算低，3.0 到 4.0 算中，达到 4.0 算高。"
            ),
        },
    ]


RANGE_KEYS = ("today", "7", "30", "90", "all")
RANGE_DAYS = {"today": 1, "7": 7, "30": 30, "90": 90, "all": None}
RANGE_LABELS = {
    "today": "今天",
    "7": "近 7 天",
    "30": "近 30 天",
    "90": "近 90 天",
    "all": "全部",
}


def normalize_range_key(value) -> str:
    raw = str(value or "90").strip().lower()
    if raw in {"1", "day", "today"}:
        return "today"
    if raw in RANGE_KEYS:
        return raw
    return "90"


def available_ranges(history_days: int) -> list[dict]:
    keys = ["today", "7", "30", "90"]
    if history_days > 90:
        keys.append("all")
    return [{"key": key, "label": RANGE_LABELS[key], "days": RANGE_DAYS[key]} for key in keys]


def _empty_report(sample: bool, range_key: str = "90") -> dict:
    key = normalize_range_key(range_key)
    return {
        "sample": sample,
        "generated_at": iso_sh(),
        "range": key,
        "range_label": RANGE_LABELS[key],
        "available_ranges": available_ranges(0),
        "window": None,
        "summary": {
            "ctl": None,
            "atl": None,
            "tsb": None,
            "acwr": None,
            "acute_load": None,
            "chronic_load": None,
            "history_days": 0,
            "load_method": "none",
            "load_method_label": METHOD_LABELS["none"],
            "load_method_shares": {},
            "weekly_hours": None,
            "previous_weekly_hours": None,
            "weekly_km": None,
            "previous_weekly_km": None,
            "weekly_load": None,
            "rhr_7d": None,
            "rhr_baseline": None,
            "rhr_delta": None,
            "rhr_elevated": False,
            "hrv_3d": None,
            "hrv_baseline": None,
            "hrv_ratio": None,
            "hrv_last": None,
            "hrv_band_low": None,
            "hrv_band_high": None,
            "hrv_suppressed": False,
            "sleep_7d_hours": None,
            "sleep_last_hours": None,
            "sleep_short": False,
            "body_battery": None,
            "stress_7d": None,
            "steps_7d": None,
            "hr_max": None,
            "hr_max_source": None,
            "intensity": None,
            "garmin": {},
        },
        "flags": [],
        "suggestions": ["没有可计算的日期。同步之后这里会用你自己的活动重算。"],
        "notes": ["CTL/ATL 从这段数据的第一天以 0 起步。"] if not sample else [
            "这是示例数据，不是佳明账号里的记录。",
        ],
        "series": {
            "dates": [],
            "load": [],
            "ctl": [],
            "atl": [],
            "tsb": [],
            "acwr": [],
            "rhr": [],
            "hrv": [],
            "hrv_low": [],
            "hrv_high": [],
            "sleep_hours": [],
            "body_battery": [],
            "stress": [],
            "weeks": [],
        },
        "methods": method_notes(),
    }


def _slice_series(values: list, start_idx: int) -> list:
    return list(values[start_idx:])


def _dedupe_activities(activities: list) -> list[dict]:
    kept = []
    seen = set()
    for raw in activities:
        if not isinstance(raw, dict):
            continue
        if _parse_date(raw.get("date")) is None:
            continue
        marker = raw.get("id")
        if marker is not None:
            if marker in seen:
                continue
            seen.add(marker)
        kept.append(raw)
    return kept


def _merge_days(days: list) -> dict[date, dict]:
    merged: dict[date, dict] = {}
    for raw in days or []:
        if not isinstance(raw, dict):
            continue
        stamp = _parse_date(raw.get("date"))
        if stamp is None:
            continue
        current = merged.setdefault(stamp, {})
        for key, value in raw.items():
            if key == "date" or value is None:
                continue
            current[key] = value
    return merged


def _garmin_block(profile: dict) -> dict:
    status = profile.get("training_status")
    return {
        "vo2max_running": _round(profile.get("vo2max_running"), 1),
        "vo2max_cycling": _round(profile.get("vo2max_cycling"), 1),
        "training_status": status,
        "training_status_label": _status_label(status),
        "weekly_training_load": _round(profile.get("weekly_training_load"), 1),
        "load_tunnel_min": _round(profile.get("load_tunnel_min"), 1),
        "load_tunnel_max": _round(profile.get("load_tunnel_max"), 1),
        "acute_load": _round(profile.get("garmin_acute_load"), 1),
        "chronic_load": _round(profile.get("garmin_chronic_load"), 1),
    }


def _window_hours(activities: list[dict], start: date, end: date) -> tuple[float, float]:
    seconds = 0.0
    meters = 0.0
    for activity in activities:
        stamp = _parse_date(activity.get("date"))
        if stamp is None or stamp < start or stamp > end:
            continue
        seconds += max(0.0, _float(activity.get("duration_s")) or 0.0)
        meters += max(0.0, _float(activity.get("distance_m")) or 0.0)
    return seconds / 3600.0, meters / 1000.0


def _weeks(dates: list[date], activities: list[dict]) -> list[dict]:
    if not dates:
        return []
    first = dates[0] - timedelta(days=dates[0].weekday())
    last = dates[-1] - timedelta(days=dates[-1].weekday())
    mondays = []
    cursor = first
    while cursor <= last:
        mondays.append(cursor)
        cursor += timedelta(days=7)
    mondays = mondays[-8:]
    buckets = {monday: {} for monday in mondays}
    km = {monday: 0.0 for monday in mondays}
    for activity in activities:
        stamp = _parse_date(activity.get("date"))
        if stamp is None:
            continue
        monday = stamp - timedelta(days=stamp.weekday())
        if monday not in buckets:
            continue
        sport = _sport_name(str(activity.get("type") or "other"))
        hours = max(0.0, _float(activity.get("duration_s")) or 0.0) / 3600.0
        buckets[monday][sport] = buckets[monday].get(sport, 0.0) + hours
        km[monday] += max(0.0, _float(activity.get("distance_m")) or 0.0) / 1000.0
    rows = []
    for monday in mondays:
        sports = {name: round(value, 2) for name, value in buckets[monday].items() if value > 0}
        rows.append(
            {
                "start": monday.isoformat(),
                "label": f"{monday.month:02d}/{monday.day:02d}",
                "sports": sports,
                "km": round(km[monday], 1),
            }
        )
    return rows


def _hr_rest_for(day: dict, profile: dict, fallback: float | None) -> float | None:
    value = _float(day.get("resting_hr"))
    if value and value > 0:
        return value
    stated = _float(profile.get("hr_rest"))
    if stated and stated > 0:
        return stated
    return fallback


def analyze(bundle: dict, range_key: str | None = "90") -> dict:
    sample = bool(bundle.get("sample"))
    range_key = normalize_range_key(range_key)
    profile = bundle.get("profile") if isinstance(bundle.get("profile"), dict) else {}
    activities = _dedupe_activities(bundle.get("activities") or [])
    day_map = _merge_days(bundle.get("days") or [])
    stamps = set(day_map)
    for activity in activities:
        stamp = _parse_date(activity.get("date"))
        if stamp is not None:
            stamps.add(stamp)
    if not stamps:
        return _empty_report(sample, range_key)
    ordered = _daterange(min(stamps), max(stamps))
    by_day: dict[date, list[dict]] = {stamp: [] for stamp in ordered}
    for activity in activities:
        stamp = _parse_date(activity.get("date"))
        if stamp in by_day:
            by_day[stamp].append(activity)
    hr_sources = list(activities)
    for day in day_map.values():
        if _float(day.get("max_hr")):
            hr_sources.append({"max_hr": day.get("max_hr")})
    hr_max, hr_source = resolve_hr_max(profile, hr_sources)
    rest_values = [value for value in (_float(day_map.get(stamp, {}).get("resting_hr")) for stamp in ordered) if value]
    rest_fallback = float(median(rest_values)) if rest_values else None
    sex = profile.get("sex") or "unknown"
    loads: list[float] = []
    method_counts: dict[str, int] = {}
    for stamp in ordered:
        day = day_map.get(stamp, {})
        rest = _hr_rest_for(day, profile, rest_fallback)
        total = 0.0
        for activity in by_day[stamp]:
            value, method = session_load(activity, sex=sex, hr_rest=rest, hr_max=hr_max)
            if method != "none":
                method_counts[method] = method_counts.get(method, 0) + 1
            total += value
        loads.append(total)
    pmc = pmc_series(loads)
    ratios = acwr_series(loads)
    rhr_series = [_float(day_map.get(stamp, {}).get("resting_hr")) for stamp in ordered]
    hrv_series = [_float(day_map.get(stamp, {}).get("hrv")) for stamp in ordered]
    hrv_low_series = [_float(day_map.get(stamp, {}).get("hrv_baseline_low")) for stamp in ordered]
    hrv_high_series = [_float(day_map.get(stamp, {}).get("hrv_baseline_high")) for stamp in ordered]
    sleep_series = []
    for stamp in ordered:
        seconds = _float(day_map.get(stamp, {}).get("sleep_seconds"))
        sleep_series.append(None if seconds is None else seconds / 3600.0)
    battery_series = [_float(day_map.get(stamp, {}).get("body_battery")) for stamp in ordered]
    stress_series = [_float(day_map.get(stamp, {}).get("stress")) for stamp in ordered]
    steps_series = [_float(day_map.get(stamp, {}).get("steps")) for stamp in ordered]

    rhr = rhr_status(rhr_series)
    hrv = hrv_status(hrv_series)
    sleep = sleep_status(sleep_series)
    stress_vals = _tail(stress_series, 7)
    stress_mean = _mean(stress_vals) if len(stress_vals) >= 3 else None
    step_vals = _tail(steps_series, 7)
    steps_mean = _mean(step_vals) if step_vals else None
    battery = None
    for value in reversed(battery_series[-2:]):
        if value is not None:
            battery = value
            break
    band_low = next((value for value in reversed(hrv_low_series) if value is not None), None)
    band_high = next((value for value in reversed(hrv_high_series) if value is not None), None)

    if not method_counts:
        load_method = "none"
    elif len(method_counts) == 1:
        load_method = next(iter(method_counts))
    else:
        load_method = "mixed"
    share_total = sum(method_counts.values()) or 1
    shares = {key: round(value / share_total, 2) for key, value in method_counts.items()}

    end = ordered[-1]
    span = RANGE_DAYS[range_key]
    if span is None:
        start_idx = 0
        view_start = ordered[0]
    else:
        start_idx = max(0, len(ordered) - span)
        view_start = ordered[start_idx]
    view_dates = ordered[start_idx:]
    view_loads = loads[start_idx:]

    view_activities = []
    for activity in activities:
        stamp = _parse_date(activity.get("date"))
        if stamp is not None and view_start <= stamp <= end:
            view_activities.append(activity)

    minutes = {"low": 0.0, "mid": 0.0, "high": 0.0}
    how_used = set()
    classified_seconds = 0.0
    all_seconds = 0.0
    for activity in view_activities:
        all_seconds += max(0.0, _float(activity.get("duration_s")) or 0.0)
        bucket, how = _classify(activity, hr_max)
        if bucket is None or how is None:
            continue
        seconds = max(0.0, _float(activity.get("duration_s")) or 0.0)
        minutes[bucket] += seconds / 60.0
        classified_seconds += seconds
        how_used.add(how)
    minute_total = sum(minutes.values())
    if minute_total > 0:
        if how_used == {"hr"}:
            how = "hr"
        elif how_used == {"te"}:
            how = "te"
        else:
            how = "mixed"
        intensity = {
            "low": round(minutes["low"] / minute_total, 4),
            "mid": round(minutes["mid"] / minute_total, 4),
            "high": round(minutes["high"] / minute_total, 4),
            "minutes": round(minute_total, 1),
            "method": how,
            "method_label": INTENSITY_LABELS[how],
        }
    else:
        intensity = None

    range_hours, range_km = _window_hours(activities, view_start, end)
    # Keep a fixed 7-day comparison strip ending at the same end date.
    week_start = end - timedelta(days=6)
    prev_end = week_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=6)
    weekly_hours, weekly_km = _window_hours(activities, week_start, end)
    if prev_start >= ordered[0]:
        previous_hours, previous_km = _window_hours(activities, prev_start, prev_end)
    else:
        previous_hours, previous_km = None, None
    acute = _mean(loads[-7:]) if len(loads) >= 7 else None
    chronic = _mean(loads[-28:]) if len(loads) >= 28 else None
    weekly_load = sum(loads[-7:]) if len(loads) >= 7 else None
    range_load = sum(view_loads) if view_loads else None

    summary = {
        "ctl": _round(pmc["ctl"][-1], 1),
        "atl": _round(pmc["atl"][-1], 1),
        "tsb": _round(pmc["tsb"][-1], 1),
        "acwr": _round(ratios[-1], 2),
        "acute_load": _round(acute, 1),
        "chronic_load": _round(chronic, 1),
        "history_days": len(ordered),
        "range_days": len(view_dates),
        "load_method": load_method,
        "load_method_label": METHOD_LABELS[load_method],
        "load_method_shares": shares,
        "range_hours": _round(range_hours, 1),
        "range_km": _round(range_km, 1),
        "range_load": _round(range_load, 1),
        "weekly_hours": _round(weekly_hours, 1),
        "previous_weekly_hours": _round(previous_hours, 1),
        "weekly_km": _round(weekly_km, 1),
        "previous_weekly_km": _round(previous_km, 1),
        "weekly_load": _round(weekly_load, 1),
        "rhr_7d": _round(rhr["recent"], 1),
        "rhr_baseline": _round(rhr["baseline"], 1),
        "rhr_delta": _round(rhr["delta"], 1),
        "rhr_elevated": bool(rhr["elevated"]),
        "hrv_3d": _round(hrv["recent"], 1),
        "hrv_baseline": _round(hrv["baseline"], 1),
        "hrv_ratio": _round(hrv["ratio"], 3),
        "hrv_last": _round(hrv["last"], 1),
        "hrv_band_low": _round(band_low, 1),
        "hrv_band_high": _round(band_high, 1),
        "hrv_suppressed": bool(hrv["suppressed"]),
        "sleep_7d_hours": _round(sleep["mean7"], 2),
        "sleep_last_hours": _round(sleep["last"], 2),
        "sleep_short": bool(sleep["short"]),
        "body_battery": _round(battery, 1),
        "stress_7d": _round(stress_mean, 1),
        "steps_7d": _round(steps_mean, 0),
        "hr_max": _round(hr_max, 1),
        "hr_max_source": hr_source,
        "intensity": intensity,
        "garmin": _garmin_block(profile),
    }
    flags = _build_flags(summary)
    flag_ids = {item["id"] for item in flags}
    notes = []
    if sample:
        notes.append(
            "这是示例数据，不是佳明账号里的记录。数字里故意放了一次负荷升高、静息心率上移和几晚睡眠偏短，用来把警报画出来。"
        )
    notes.append(
        f"CTL/ATL 从全部 {len(ordered)} 天历史的第一天以 0 起步，图表按所选时间范围截取。"
        "更早的训练没有包括在内，开头几周的 CTL 会偏低。"
    )
    notes.append(f"负荷口径：{METHOD_LABELS[load_method]}。")
    if load_method == "mixed":
        parts = [f"{METHOD_LABELS[key]} {share:.0%}" for key, share in shares.items()]
        notes.append("不同活动的口径不一样：" + "，".join(parts) + "。CTL 的绝对值不要和单一口径硬比。")
    if hr_source and intensity and intensity["method"] in {"hr", "mixed"}:
        notes.append(f"强度分区用的最大心率是 {fmt_num(hr_max)}（{hr_source}）。")
    if all_seconds > 0 and classified_seconds / all_seconds < 0.5:
        notes.append("所选范围内有一半以上的训练时长没有心率，也没有有氧训练效果，没有进入强度分布。")
    if hrv["stale"]:
        notes.append("最近几天没有 HRV，基线比较没有更新。")

    def series_round(values, digits=2):
        return [None if value is None else round(float(value), digits) for value in _slice_series(values, start_idx)]

    return {
        "sample": sample,
        "generated_at": iso_sh(),
        "range": range_key,
        "range_label": RANGE_LABELS[range_key],
        "available_ranges": available_ranges(len(ordered)),
        "window": {
            "start": view_start.isoformat(),
            "end": end.isoformat(),
            "days": len(view_dates),
            "history_days": len(ordered),
            "history_start": ordered[0].isoformat(),
        },
        "summary": summary,
        "flags": flags,
        "suggestions": _suggestions(summary, flag_ids),
        "notes": notes,
        "series": {
            "dates": [stamp.isoformat() for stamp in view_dates],
            "load": series_round(loads, 2),
            "ctl": series_round(pmc["ctl"], 2),
            "atl": series_round(pmc["atl"], 2),
            "tsb": series_round(pmc["tsb"], 2),
            "acwr": [
                None if value is None else round(value, 3)
                for value in _slice_series(ratios, start_idx)
            ],
            "rhr": series_round(rhr_series, 1),
            "hrv": series_round(hrv_series, 1),
            "hrv_low": series_round(hrv_low_series, 1),
            "hrv_high": series_round(hrv_high_series, 1),
            "sleep_hours": series_round(sleep_series, 2),
            "body_battery": series_round(battery_series, 1),
            "stress": series_round(stress_series, 1),
            "weeks": _weeks(view_dates, view_activities),
        },
        "methods": method_notes(),
    }
