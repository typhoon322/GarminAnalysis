package com.garminanalysis.app.analysis

import com.garminanalysis.app.Clock
import java.time.LocalDate
import java.time.format.DateTimeParseException
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.round
import kotlin.math.roundToInt
import org.json.JSONArray
import org.json.JSONObject

/**
 * Training-load and recovery metrics.
 *
 * Formulas live next to the constants they use. The numbers are coaching
 * heuristics with cited origins, not clinical cut-points and not fake precision.
 */
object Analysis {

    const val CTL_TAU = 42.0
    const val ATL_TAU = 7.0

    const val TSB_VERY_FRESH = 25.0
    const val TSB_FRESH = 5.0
    const val TSB_NEUTRAL_LOW = -10.0
    const val TSB_HIGH_FATIGUE = -30.0

    const val ACWR_ACUTE_DAYS = 7
    const val ACWR_CHRONIC_DAYS = 28
    const val ACWR_LOW = 0.8
    const val ACWR_HIGH = 1.3
    const val ACWR_SPIKE = 1.5

    const val RHR_RISE_BPM = 5.0
    const val HRV_DROP_RATIO = 0.90
    const val SLEEP_MEAN_HOURS = 7.0
    const val SLEEP_NIGHT_HOURS = 6.0
    const val LOW_HR_FRAC = 0.80
    const val HIGH_HR_FRAC = 0.90
    const val STRESS_ELEVATED = 51.0
    const val BATTERY_LOW = 25.0
    const val MIN_DAYS_FOR_FATIGUE_FLAG = 21
    const val DURATION_PROXY_RPE = 5.0

    val METHOD_LABELS: Map<String, String> = mapOf(
        "garmin_training_load" to "佳明活动训练负荷（不是 TrainingPeaks 的 TSS）",
        "banister_trimp" to "Banister TRIMP",
        "edwards_trimp" to "Edwards 分区 TRIMP（按平均心率把整段放进一个区）",
        "duration_proxy" to "时长×5 的粗代理（没有心率，也没有佳明负荷）",
        "mixed" to "混合口径",
        "none" to "没有负荷",
    )

    val INTENSITY_LABELS: Map<String, String> = mapOf(
        "hr" to "按活动平均心率粗分",
        "te" to "按佳明有氧训练效果粗分",
        "mixed" to "心率和训练效果混用",
    )

    val STATUS_ZH: Map<String, String> = mapOf(
        "PRODUCTIVE" to "有成效",
        "MAINTAINING" to "维持",
        "RECOVERY" to "恢复",
        "UNPRODUCTIVE" to "成效不足",
        "DETRAINING" to "体能下降",
        "OVERREACHING" to "过度",
        "PEAKING" to "巅峰",
        "NO_STATUS" to "无状态",
        "PAUSED" to "暂停",
    )

    val SPORT_ZH: Map<String, String> = mapOf(
        "running" to "跑步",
        "trail_running" to "越野跑",
        "treadmill_running" to "跑步机",
        "cycling" to "骑行",
        "road_biking" to "公路骑行",
        "mountain_biking" to "山地骑行",
        "virtual_ride" to "室内骑行",
        "swimming" to "游泳",
        "lap_swimming" to "泳池游泳",
        "open_water_swimming" to "公开水域",
        "walking" to "步行",
        "hiking" to "徒步",
        "strength_training" to "力量",
        "fitness_equipment" to "器械",
        "yoga" to "瑜伽",
        "indoor_cardio" to "室内有氧",
        "elliptical" to "椭圆机",
        "stair_climbing" to "爬楼",
        "rowing" to "划船",
        "indoor_rowing" to "室内划船",
    )

    val RANGE_KEYS = listOf("today", "7", "30", "90", "all")

    val RANGE_DAYS: Map<String, Int?> = mapOf(
        "today" to 1,
        "7" to 7,
        "30" to 30,
        "90" to 90,
        "all" to null,
    )

    val RANGE_LABELS: Map<String, String> = mapOf(
        "today" to "今天",
        "7" to "近 7 天",
        "30" to "近 30 天",
        "90" to "近 90 天",
        "all" to "全部",
    )

    fun toFloat(value: Any?): Double? {
        if (value == null || value == JSONObject.NULL) return null
        if (value is Boolean) return null
        return when (value) {
            is Number -> {
                val number = value.toDouble()
                if (number.isNaN() || number.isInfinite()) null else number
            }
            is String -> {
                val number = value.toDoubleOrNull() ?: return null
                if (number.isNaN() || number.isInfinite()) null else number
            }
            else -> null
        }
    }

    fun fmtNum(value: Any?, digits: Int = 1): String {
        val number = toFloat(value) ?: return "—"
        if (abs(number - round(number)) < 0.05) {
            return round(number).toInt().toString()
        }
        return "%.${digits}f".format(number)
    }

    fun fmtAcwr(value: Any?): String {
        val number = toFloat(value) ?: return "—"
        return "%.2f".format(number)
    }

    private fun mean(values: List<Double>): Double? {
        if (values.isEmpty()) return null
        return values.sum() / values.size
    }

    private fun median(values: List<Double>): Double {
        val sorted = values.sorted()
        val n = sorted.size
        return if (n % 2 == 1) sorted[n / 2] else (sorted[n / 2 - 1] + sorted[n / 2]) / 2.0
    }

    private fun parseDate(value: Any?): LocalDate? {
        if (value !is String || value.length < 10) return null
        return try {
            LocalDate.parse(value.substring(0, 10))
        } catch (_: DateTimeParseException) {
            null
        }
    }

    private fun dateRange(start: LocalDate, end: LocalDate): List<LocalDate> {
        val days = mutableListOf<LocalDate>()
        var cursor = start
        while (!cursor.isAfter(end)) {
            days.add(cursor)
            cursor = cursor.plusDays(1)
        }
        return days
    }

    private fun jsonPut(obj: JSONObject, key: String, value: Any?) {
        when (value) {
            null -> obj.put(key, JSONObject.NULL)
            is Double -> {
                if (value.isNaN() || value.isInfinite()) obj.put(key, JSONObject.NULL)
                else obj.put(key, value)
            }
            is Float -> {
                if (value.isNaN() || value.isInfinite()) obj.put(key, JSONObject.NULL)
                else obj.put(key, value.toDouble())
            }
            else -> obj.put(key, value)
        }
    }

    private fun jsonGet(obj: JSONObject?, key: String): Any? {
        if (obj == null || !obj.has(key) || obj.isNull(key)) return null
        return obj.opt(key)
    }

    private fun asObject(value: Any?): JSONObject? = value as? JSONObject
    private fun asArray(value: Any?): JSONArray? = value as? JSONArray
    private fun pct0(ratio: Double): String = "${(ratio * 100).roundToInt()}%"
    private fun signed1(value: Double): String = "%+.1f".format(value)

    fun pmcSeries(
        loads: List<Double>,
        ctl0: Double = 0.0,
        atl0: Double = 0.0,
    ): Map<String, List<Double>> {
        val ctl = mutableListOf<Double>()
        val atl = mutableListOf<Double>()
        val tsb = mutableListOf<Double>()
        var fitness = ctl0
        var fatigue = atl0
        for (load in loads) {
            tsb.add(fitness - fatigue)
            fitness += (load - fitness) / CTL_TAU
            fatigue += (load - fatigue) / ATL_TAU
            ctl.add(fitness)
            atl.add(fatigue)
        }
        return mapOf("ctl" to ctl, "atl" to atl, "tsb" to tsb)
    }

    fun acwrSeries(loads: List<Double>): List<Double?> {
        val out = mutableListOf<Double?>()
        for (index in loads.indices) {
            if (index + 1 < ACWR_CHRONIC_DAYS) {
                out.add(null)
                continue
            }
            val chronic = loads.subList(index + 1 - ACWR_CHRONIC_DAYS, index + 1)
            val acute = loads.subList(index + 1 - ACWR_ACUTE_DAYS, index + 1)
            val chronicMean = chronic.sum() / ACWR_CHRONIC_DAYS
            if (chronicMean <= 0) {
                out.add(null)
                continue
            }
            out.add((acute.sum() / ACWR_ACUTE_DAYS) / chronicMean)
        }
        return out
    }

    fun banisterTrimp(
        durationMin: Double,
        hrAvg: Double,
        hrRest: Double,
        hrMax: Double,
        sex: String,
    ): Double {
        val span = hrMax - hrRest
        if (durationMin <= 0 || span <= 0) return 0.0
        val hrr = ((hrAvg - hrRest) / span).coerceIn(0.0, 1.0)
        return if (sex == "female") {
            durationMin * hrr * 0.86 * exp(1.67 * hrr)
        } else {
            durationMin * hrr * 0.64 * exp(1.92 * hrr)
        }
    }

    fun edwardsTrimp(durationMin: Double, hrAvg: Double, hrMax: Double): Double {
        if (durationMin <= 0 || hrMax <= 0) return 0.0
        val frac = hrAvg / hrMax
        val weight = when {
            frac < 0.50 -> 0
            frac < 0.60 -> 1
            frac < 0.70 -> 2
            frac < 0.80 -> 3
            frac < 0.90 -> 4
            else -> 5
        }
        return durationMin * weight
    }

    fun sessionLoad(
        activity: JSONObject,
        sex: String,
        hrRest: Double?,
        hrMax: Double?,
    ): Pair<Double, String> {
        val garmin = toFloat(jsonGet(activity, "training_load"))
        if (garmin != null) return maxOf(0.0, garmin) to "garmin_training_load"
        val durationMin = maxOf(0.0, toFloat(jsonGet(activity, "duration_s")) ?: 0.0) / 60.0
        val avg = toFloat(jsonGet(activity, "avg_hr"))
        if (
            avg != null && avg != 0.0 &&
            hrMax != null && hrRest != null &&
            sex in setOf("male", "female") &&
            hrMax > hrRest
        ) {
            return banisterTrimp(durationMin, avg, hrRest, hrMax, sex) to "banister_trimp"
        }
        if (avg != null && avg != 0.0 && hrMax != null) {
            return edwardsTrimp(durationMin, avg, hrMax) to "edwards_trimp"
        }
        if (durationMin <= 0) return 0.0 to "none"
        return durationMin * DURATION_PROXY_RPE to "duration_proxy"
    }

    private fun sportName(typeKey: String?): String {
        if (typeKey != null && typeKey in SPORT_ZH) return SPORT_ZH.getValue(typeKey)
        val text = (typeKey ?: "other").replace('_', ' ')
        return text.ifEmpty { "其他" }
    }

    private fun statusLabel(status: Any?): String? {
        if (status == null || status == JSONObject.NULL || status == "") return null
        if (status is Number) return "状态码 ${status.toInt()}"
        val text = status.toString()
        return STATUS_ZH[text.uppercase()] ?: text
    }

    fun resolveHrMax(profile: JSONObject, activities: List<JSONObject>): Pair<Double?, String?> {
        val stated = toFloat(jsonGet(profile, "hr_max"))
        if (stated != null && stated > 0) return stated to "用户填写"
        val observedVals = activities.mapNotNull { toFloat(jsonGet(it, "max_hr")) }.filter { it > 0 }
        val observed = observedVals.maxOrNull()
        val age = toFloat(jsonGet(profile, "age"))
        val tanaka = if (age != null && age > 0) 208.0 - 0.7 * age else null
        if (tanaka != null && observed != null) {
            return if (observed > tanaka) {
                observed to "活动最高心率高于年龄估算，采用活动最高心率"
            } else {
                tanaka to "Tanaka 公式 208 − 0.7 × 年龄"
            }
        }
        if (tanaka != null) return tanaka to "Tanaka 公式 208 − 0.7 × 年龄"
        if (observed != null) return observed to "活动里见到的最高心率（可能低于真实最大心率）"
        return null to null
    }

    private fun classify(activity: JSONObject, hrMax: Double?): Pair<String?, String?> {
        val avg = toFloat(jsonGet(activity, "avg_hr"))
        if (avg != null && avg != 0.0 && hrMax != null) {
            val frac = avg / hrMax
            return when {
                frac < LOW_HR_FRAC -> "low" to "hr"
                frac >= HIGH_HR_FRAC -> "high" to "hr"
                else -> "mid" to "hr"
            }
        }
        val effect = toFloat(jsonGet(activity, "aerobic_te")) ?: return null to null
        return when {
            effect < 3.0 -> "low" to "te"
            effect < 4.0 -> "mid" to "te"
            else -> "high" to "te"
        }
    }

    private fun <T> tail(values: List<T?>, size: Int): List<T> =
        values.takeLast(size).filterNotNull()

    fun rhrStatus(values: List<Double?>): JSONObject {
        val recentVals = tail(values, 7)
        val prior = if (values.size > 7) {
            values.dropLast(7).takeLast(28).filterNotNull()
        } else {
            emptyList()
        }
        val recent = mean(recentVals)
        val baseline = mean(prior)
        val delta = if (recent == null || baseline == null) null else recent - baseline
        val elevated =
            delta != null && delta >= RHR_RISE_BPM && recentVals.size >= 3 && prior.size >= 7
        return JSONObject().apply {
            jsonPut(this, "recent", recent)
            jsonPut(this, "baseline", baseline)
            jsonPut(this, "delta", delta)
            put("elevated", elevated)
        }
    }

    fun hrvStatus(values: List<Double?>): JSONObject {
        val validIdx = values.indices.filter { values[it] != null }
        fun empty(): JSONObject = JSONObject().apply {
            jsonPut(this, "recent", null)
            jsonPut(this, "baseline", null)
            jsonPut(this, "ratio", null)
            put("suppressed", false)
            put("stale", validIdx.isEmpty())
            jsonPut(this, "last", null)
        }
        if (validIdx.size < 3) return empty()
        val last3 = validIdx.takeLast(3)
        val recentVals = last3.map { values[it]!! }
        val recent = mean(recentVals)
        val last = recentVals.last()
        val stale = (values.size - 1 - last3.last()) > 3
        val priorIdx = validIdx.filter { it < last3.first() }
        val prior = priorIdx.takeLast(28).map { values[it]!! }
        if (stale || prior.size < 7 || prior.isEmpty()) {
            return JSONObject().apply {
                jsonPut(this, "recent", recent)
                jsonPut(this, "baseline", null)
                jsonPut(this, "ratio", null)
                put("suppressed", false)
                put("stale", stale)
                jsonPut(this, "last", last)
            }
        }
        val baseline = median(prior)
        val ratio = if (baseline <= 0 || recent == null) null else recent / baseline
        val suppressed = ratio != null && ratio < HRV_DROP_RATIO
        return JSONObject().apply {
            jsonPut(this, "recent", recent)
            jsonPut(this, "baseline", baseline)
            jsonPut(this, "ratio", ratio)
            put("suppressed", suppressed)
            put("stale", false)
            jsonPut(this, "last", last)
        }
    }

    fun sleepStatus(hours: List<Double?>): JSONObject {
        var last: Double? = null
        for (value in hours.takeLast(2).asReversed()) {
            if (value != null) {
                last = value
                break
            }
        }
        val recentVals = tail(hours, 7)
        val mean7 = if (recentVals.size >= 3) mean(recentVals) else null
        val short =
            (mean7 != null && mean7 < SLEEP_MEAN_HOURS) ||
                (last != null && last < SLEEP_NIGHT_HOURS)
        return JSONObject().apply {
            jsonPut(this, "mean7", mean7)
            jsonPut(this, "last", last)
            put("short", short)
        }
    }

    private fun roundVal(value: Any?, digits: Int): Double? {
        val number = toFloat(value) ?: return null
        val factor = Math.pow(10.0, digits.toDouble())
        return round(number * factor) / factor
    }

    private fun tsbWords(tsb: Double?): String {
        if (tsb == null) return "没有足够的负荷，TSB 算不出来。"
        return when {
            tsb > TSB_VERY_FRESH ->
                "TSB 高于 +25，常用说法是很轻松；如果已经轻松了很多天，体能可能在往下走。"
            tsb >= TSB_FRESH ->
                "TSB 在 +5 到 +25，常用说法是比较轻松，适合质量课或比赛。"
            tsb >= TSB_NEUTRAL_LOW ->
                "TSB 在 -10 到 +5，常用说法是中性，处在正常训练区间。"
            tsb >= TSB_HIGH_FATIGUE ->
                "TSB 在 -30 到 -10，常用说法是有些累，短时间可以，不宜长期停在这里。"
            else -> "TSB 低于 -30，本工具把它标为高疲劳。"
        }
    }

    private fun acwrWords(acwr: Double?): String {
        if (acwr == null) return ""
        return when {
            acwr > ACWR_SPIKE -> "这高于本工具的尖峰线。"
            acwr > ACWR_HIGH -> "这高于常引区间的上沿，但还没到本工具的尖峰线。"
            acwr < ACWR_LOW -> "这低于常引区间的下沿。"
            else -> "这落在常引区间里面。"
        }
    }


    private fun nextStep(summary: JSONObject, flagIds: Set<String>): String {
        val tsbS = fmtNum(jsonGet(summary, "tsb"))
        val acwrS = fmtAcwr(jsonGet(summary, "acwr"))
        if ("recovery_needed" in flagIds) {
            return "今天宜减量。TSB 是 $tsbS，ACWR 是 $acwrS，" +
                "并且睡眠、静息心率、HRV 里至少有一项偏离你自己的基线。" +
                "先做轻松有氧，睡眠靠近每晚 ${SLEEP_MEAN_HOURS.toInt()} 小时，再加强度。"
        }
        if ("high_fatigue" in flagIds) {
            return "TSB $tsbS 低于 ${TSB_HIGH_FATIGUE.toInt()}。接下来几天减少负荷，" +
                "等 TSB 回到 ${TSB_NEUTRAL_LOW.toInt()} 以上再排质量课。"
        }
        if ("load_spike" in flagIds) {
            return "ACWR $acwrS 高于 ${"%.1f".format(ACWR_SPIKE)}。近 7 日平均负荷 ${fmtNum(jsonGet(summary, "acute_load"))}，" +
                "近 28 日平均 ${fmtNum(jsonGet(summary, "chronic_load"))}。后面几天把日负荷放在慢性平均附近或以下。"
        }
        if (flagIds.intersect(setOf("rhr_elevated", "hrv_suppressed", "sleep_short")).isNotEmpty()) {
            return "负荷先不要加。TSB $tsbS，ACWR $acwrS。今天把质量课换成轻松有氧，看下一次静息心率和睡眠。"
        }
        if ("load_low" in flagIds) {
            return "ACWR $acwrS 低于 ${"%.1f".format(ACWR_LOW)}。如果主观感觉正常，本周时长可以小幅加上去，" +
                "一次增加大约一成就够，不必跳很远。"
        }
        if (jsonGet(summary, "acwr") == null || jsonGet(summary, "tsb") == null) {
            return "记录还太短，先按能恢复的时长练，等满 28 天再看 ACWR。"
        }
        return "没有触发恢复或负荷警报。TSB $tsbS，ACWR $acwrS。" +
            "可以按最近 7 天的时长继续，质量课仍放在你感觉睡够的日子。"
    }

    private fun suggestions(summary: JSONObject, flagIds: Set<String>): List<String> {
        val lines = mutableListOf<String>()
        if (jsonGet(summary, "ctl") == null) {
            lines.add("没有活动负荷，算不出 CTL、ATL、TSB。")
        } else {
            lines.add(
                "CTL ${fmtNum(jsonGet(summary, "ctl"))}，ATL ${fmtNum(jsonGet(summary, "atl"))}，" +
                    "TSB ${fmtNum(jsonGet(summary, "tsb"))}（前一天的 CTL 减前一天的 ATL）。" +
                    tsbWords(toFloat(jsonGet(summary, "tsb"))),
            )
        }
        if (jsonGet(summary, "acwr") == null) {
            lines.add(
                "ACWR 需要连续 $ACWR_CHRONIC_DAYS 天负荷。当前这段有 ${summary.optInt("history_days", 0)} 天。",
            )
        } else {
            lines.add(
                "ACWR ${fmtAcwr(jsonGet(summary, "acwr"))}" +
                    "（近 7 日平均负荷 ${fmtNum(jsonGet(summary, "acute_load"))}，" +
                    "近 28 日平均负荷 ${fmtNum(jsonGet(summary, "chronic_load"))}）。" +
                    "文献里常提到的区间大约是 ${"%.1f".format(ACWR_LOW)}–${"%.1f".format(ACWR_HIGH)}；" +
                    "本工具把高于 ${"%.1f".format(ACWR_SPIKE)} 标成尖峰。" +
                    acwrWords(toFloat(jsonGet(summary, "acwr"))),
            )
        }
        if (jsonGet(summary, "weekly_hours") != null) {
            var previous = ""
            if (jsonGet(summary, "previous_weekly_hours") != null) {
                val prevH = toFloat(jsonGet(summary, "previous_weekly_hours"))!!
                val prevKm = toFloat(jsonGet(summary, "previous_weekly_km")) ?: 0.0
                previous = "再往前 7 天是 ${"%.1f".format(prevH)} 小时、${"%.1f".format(prevKm)} 公里。"
            }
            val wh = toFloat(jsonGet(summary, "weekly_hours"))!!
            val wk = toFloat(jsonGet(summary, "weekly_km")) ?: 0.0
            lines.add("最近 7 天训练 ${"%.1f".format(wh)} 小时、${"%.1f".format(wk)} 公里。$previous")
        }
        if (jsonGet(summary, "rhr_7d") != null && jsonGet(summary, "rhr_baseline") != null) {
            val delta = toFloat(jsonGet(summary, "rhr_delta")) ?: 0.0
            lines.add(
                "静息心率近 7 日 ${fmtNum(jsonGet(summary, "rhr_7d"))} bpm，" +
                    "再往前的个人基线 ${fmtNum(jsonGet(summary, "rhr_baseline"))} bpm" +
                    "（相差 ${signed1(delta)}）。",
            )
        }
        if (jsonGet(summary, "hrv_3d") != null && jsonGet(summary, "hrv_baseline") != null) {
            val ratio = toFloat(jsonGet(summary, "hrv_ratio"))
            val ratioText = if (ratio == null) "—" else pct0(ratio)
            var band = ""
            val last = toFloat(jsonGet(summary, "hrv_last"))
            val low = toFloat(jsonGet(summary, "hrv_band_low"))
            if (last != null && low != null && last < low) {
                band = "最近一夜 ${fmtNum(last)}，低于佳明基线下沿 ${fmtNum(low)}。"
            }
            lines.add(
                "HRV 近 3 次 ${fmtNum(jsonGet(summary, "hrv_3d"))}，基线中位数 ${fmtNum(jsonGet(summary, "hrv_baseline"))}" +
                    "（相当于基线的 $ratioText）。$band",
            )
        }
        if (jsonGet(summary, "sleep_7d_hours") != null || jsonGet(summary, "sleep_last_hours") != null) {
            val meanText = if (jsonGet(summary, "sleep_7d_hours") != null) {
                "近 7 日平均 ${"%.1f".format(toFloat(jsonGet(summary, "sleep_7d_hours")))} 小时。"
            } else {
                ""
            }
            val lastText = if (jsonGet(summary, "sleep_last_hours") != null) {
                "最近一夜 ${"%.1f".format(toFloat(jsonGet(summary, "sleep_last_hours")))} 小时。"
            } else {
                ""
            }
            lines.add("睡眠$meanText$lastText")
        }
        val intensity = asObject(jsonGet(summary, "intensity"))
        if (intensity != null) {
            lines.add(
                "强度时间大约是低 ${pct0(intensity.getDouble("low"))}、中 ${pct0(intensity.getDouble("mid"))}、" +
                    "高 ${pct0(intensity.getDouble("high"))}" +
                    "（${intensity.getString("method_label")}，共 ${intensity.getDouble("minutes").toInt()} 分钟）。" +
                    "极化训练常被说成大约 80% 低强度、20% 高强度，这是经验描述，不是你的个人阈值。",
            )
        }
        lines.add(nextStep(summary, flagIds))
        return lines
    }

    private fun flag(flagId: String, level: String, title: String, detail: String): JSONObject =
        JSONObject().apply {
            put("id", flagId)
            put("level", level)
            put("title", title)
            put("detail", detail)
        }


    private fun buildFlags(summary: JSONObject): List<JSONObject> {
        val flags = mutableListOf<JSONObject>()
        val history = summary.optInt("history_days", 0)
        val tsb = toFloat(jsonGet(summary, "tsb"))
        val acwr = toFloat(jsonGet(summary, "acwr"))
        val signals = mutableListOf<String>()
        if (summary.optBoolean("hrv_suppressed")) {
            signals.add("HRV 近 3 次是基线的 ${pct0(toFloat(jsonGet(summary, "hrv_ratio")) ?: 0.0)}")
        }
        if (summary.optBoolean("rhr_elevated")) {
            signals.add("静息心率比基线高 ${"%.1f".format(toFloat(jsonGet(summary, "rhr_delta")) ?: 0.0)} bpm")
        }
        if (summary.optBoolean("sleep_short")) {
            val sleep7 = toFloat(jsonGet(summary, "sleep_7d_hours"))
            if (sleep7 != null && sleep7 < SLEEP_MEAN_HOURS) {
                signals.add("睡眠近 7 日 ${"%.1f".format(sleep7)} 小时")
            } else {
                val sleepLast = toFloat(jsonGet(summary, "sleep_last_hours"))
                if (sleepLast != null) signals.add("最近一夜睡眠 ${"%.1f".format(sleepLast)} 小时")
            }
        }
        val strain = mutableListOf<String>()
        if (tsb != null && tsb < TSB_NEUTRAL_LOW) strain.add("TSB ${fmtNum(tsb)}")
        if (acwr != null && acwr > ACWR_HIGH) strain.add("ACWR ${fmtAcwr(acwr)}")
        if (signals.isNotEmpty() && strain.isNotEmpty()) {
            flags.add(
                flag(
                    "recovery_needed",
                    "alert",
                    "恢复不足",
                    "恢复信号：" + signals.joinToString("、") + "。同时负荷偏高：" + strain.joinToString("、") + "。",
                ),
            )
        }
        if (history >= MIN_DAYS_FOR_FATIGUE_FLAG && tsb != null && tsb < TSB_HIGH_FATIGUE) {
            flags.add(
                flag(
                    "high_fatigue",
                    "alert",
                    "高疲劳",
                    "TSB ${fmtNum(tsb)}，低于 ${TSB_HIGH_FATIGUE.toInt()}。这条线是教练常用的整数，不是临床界值。",
                ),
            )
        }
        if (acwr != null && acwr > ACWR_SPIKE) {
            flags.add(
                flag(
                    "load_spike",
                    "warning",
                    "负荷尖峰",
                    "ACWR ${fmtAcwr(acwr)}，高于 ${"%.1f".format(ACWR_SPIKE)}。" +
                        "近 7 日平均负荷 ${fmtNum(jsonGet(summary, "acute_load"))}，" +
                        "近 28 日平均 ${fmtNum(jsonGet(summary, "chronic_load"))}。",
                ),
            )
        } else if (acwr != null && acwr > ACWR_HIGH) {
            flags.add(
                flag(
                    "load_rising",
                    "info",
                    "负荷偏高",
                    "ACWR ${fmtAcwr(acwr)}，高于常引区间上沿 ${"%.1f".format(ACWR_HIGH)}，还没到尖峰线 ${"%.1f".format(ACWR_SPIKE)}。",
                ),
            )
        } else if (acwr != null && acwr < ACWR_LOW && (toFloat(jsonGet(summary, "chronic_load")) ?: 0.0) > 1) {
            flags.add(
                flag(
                    "load_low",
                    "info",
                    "负荷偏低",
                    "ACWR ${fmtAcwr(acwr)}，低于 ${"%.1f".format(ACWR_LOW)}。近 28 日平均负荷 ${fmtNum(jsonGet(summary, "chronic_load"))}。",
                ),
            )
        }
        if (summary.optBoolean("rhr_elevated")) {
            flags.add(
                flag(
                    "rhr_elevated",
                    "warning",
                    "静息心率升高",
                    "近 7 日 ${fmtNum(jsonGet(summary, "rhr_7d"))} bpm，基线 ${fmtNum(jsonGet(summary, "rhr_baseline"))} bpm，" +
                        "相差 ${signed1(toFloat(jsonGet(summary, "rhr_delta")) ?: 0.0)}。标记线是升高达到 ${RHR_RISE_BPM.toInt()} bpm。",
                ),
            )
        }
        if (summary.optBoolean("hrv_suppressed")) {
            flags.add(
                flag(
                    "hrv_suppressed",
                    "warning",
                    "HRV 低于个人基线",
                    "近 3 次 ${fmtNum(jsonGet(summary, "hrv_3d"))}，前序中位数 ${fmtNum(jsonGet(summary, "hrv_baseline"))}，" +
                        "相当于基线的 ${pct0(toFloat(jsonGet(summary, "hrv_ratio")) ?: 0.0)}。标记线是 ${pct0(HRV_DROP_RATIO)}。",
                ),
            )
        }
        if (summary.optBoolean("sleep_short")) {
            flags.add(
                flag(
                    "sleep_short",
                    "warning",
                    "睡眠偏短",
                    "近 7 日平均 ${fmtNum(jsonGet(summary, "sleep_7d_hours"), 1)} 小时，" +
                        "最近一夜 ${fmtNum(jsonGet(summary, "sleep_last_hours"), 1)} 小时。" +
                        "标记线是 7 日均值短于 ${SLEEP_MEAN_HOURS.toInt()} 小时，或一夜短于 ${SLEEP_NIGHT_HOURS.toInt()} 小时。",
                ),
            )
        }
        val garmin = asObject(jsonGet(summary, "garmin")) ?: JSONObject()
        val weekly = toFloat(jsonGet(garmin, "weekly_training_load"))
        val low = toFloat(jsonGet(garmin, "load_tunnel_min"))
        val high = toFloat(jsonGet(garmin, "load_tunnel_max"))
        if (weekly != null && low != null && high != null && (weekly < low || weekly > high)) {
            flags.add(
                flag(
                    "garmin_load_outside",
                    "warning",
                    "周负荷超出设备区间",
                    "佳明周训练负荷 ${fmtNum(weekly)}，设备区间 ${fmtNum(low)}–${fmtNum(high)}。" +
                        "区间来自佳明训练状态，不是本工具算的 CTL。",
                ),
            )
        }
        val stress7d = toFloat(jsonGet(summary, "stress_7d"))
        if (stress7d != null && stress7d >= STRESS_ELEVATED) {
            flags.add(
                flag(
                    "stress_high",
                    "info",
                    "压力偏高",
                    "近 7 日压力均值 ${fmtNum(stress7d)}。佳明这套分数里，51 以上大致落在中等及以上。",
                ),
            )
        }
        val bodyBattery = toFloat(jsonGet(summary, "body_battery"))
        if (bodyBattery != null && bodyBattery < BATTERY_LOW) {
            flags.add(
                flag(
                    "battery_low",
                    "info",
                    "Body Battery 偏低",
                    "最近读数 ${fmtNum(bodyBattery)}。本工具把低于 ${BATTERY_LOW.toInt()} 标出来，这是取整的提示线。",
                ),
            )
        }
        val intensity = asObject(jsonGet(summary, "intensity"))
        if (
            intensity != null &&
            intensity.getDouble("minutes") >= 60 &&
            intensity.getDouble("mid") > 0.40 &&
            intensity.getDouble("low") < 0.70
        ) {
            flags.add(
                flag(
                    "not_polarized",
                    "info",
                    "强度不像极化分布",
                    "低强度 ${pct0(intensity.getDouble("low"))}，中等 ${pct0(intensity.getDouble("mid"))}，" +
                        "高强度 ${pct0(intensity.getDouble("high"))}。" +
                        "中等超过 40% 且低强度不到 70% 时，看起来不像常说的 80/20。这是描述，不是处方。",
                ),
            )
        }
        if (
            history >= ACWR_CHRONIC_DAYS &&
            tsb != null &&
            tsb > TSB_VERY_FRESH &&
            (acwr == null || acwr <= ACWR_HIGH) &&
            flags.none { it.getString("level") in setOf("alert", "warning") }
        ) {
            flags.add(
                flag(
                    "fresh",
                    "info",
                    "状态很轻松",
                    "TSB ${fmtNum(tsb)}，高于 +25。如果已经连续很多天很轻松，体能可能在掉。",
                ),
            )
        }
        val order = mapOf("alert" to 0, "warning" to 1, "info" to 2)
        flags.sortBy { order[it.getString("level")] ?: 9 }
        return flags
    }

    fun methodNotes(): JSONArray {
        val notes = JSONArray()
        fun add(title: String, text: String) {
            notes.put(JSONObject().put("title", title).put("text", text))
        }
        add(
            "CTL / ATL / TSB",
            "CTL 与 ATL 是日负荷的指数加权平均，时间常数 42 天和 7 天" +
                "（TrainingPeaks / Coggan 的体能管理图）。某一天的 TSB = 前一天的 CTL − 前一天的 ATL。" +
                "从这段数据的第一天以 0 起步，更早的训练没有算进来。",
        )
        add(
            "TSB 怎么读",
            "高于 +25 很轻松；+5 到 +25 较轻松；-10 到 +5 中性；-30 到 -10 累；低于 -30 高疲劳。" +
                "这些是教练常用的整数分区，不是临床界值。",
        )
        add(
            "ACWR",
            "ACWR = 近 $ACWR_ACUTE_DAYS 天平均日负荷 / 近 $ACWR_CHRONIC_DAYS 天平均日负荷" +
                "（耦合算法）。Hulin、Gabbett 2016 一系文献常把大约 " +
                "${"%.1f".format(ACWR_LOW)}–${"%.1f".format(ACWR_HIGH)} 叫做 sweet spot。" +
                "本工具满 $ACWR_CHRONIC_DAYS 天才计算；高于 ${"%.1f".format(ACWR_SPIKE)} 标为尖峰，" +
                "低于 ${"%.1f".format(ACWR_LOW)} 标为偏低。" +
                "后来的研究指出这个比值会被数学结构带偏，所以它只是负荷跳变的提示，不是受伤概率。",
        )
        add(
            "负荷怎么来",
            "有佳明 activityTrainingLoad 时用它，它不是 TrainingPeaks 的 TSS。" +
                "否则在知道性别、平均心率、静息心率和最大心率时用 Banister TRIMP" +
                "（男：分钟 × HRr × 0.64 × e^(1.92×HRr)；女：分钟 × HRr × 0.86 × e^(1.67×HRr)）。" +
                "性别未填时用 Edwards 五区 TRIMP，按平均心率把整段放进一个区。" +
                "都没有时用时长分钟 × 5，只是占位。",
        )
        add(
            "静息心率与 HRV",
            "静息心率：近 7 天均值对比再往前最多 28 天的均值，升高达到 ${RHR_RISE_BPM.toInt()} bpm 才标记。" +
                "HRV：近 3 次均值对比再往前最多 28 次的中位数，低于 ${pct0(HRV_DROP_RATIO)} 才标记。" +
                "HRV 按佳明夜间平均值理解（通常是毫秒 RMSSD），不再换算。",
        )
        add(
            "睡眠与强度",
            "睡眠：近 7 天平均短于 ${SLEEP_MEAN_HOURS.toInt()} 小时，或最近一夜短于 ${SLEEP_NIGHT_HOURS.toInt()} 小时，就标记。" +
                "强度：平均心率低于 ${pct0(LOW_HR_FRAC)} HRmax 算低，达到或高于 ${pct0(HIGH_HR_FRAC)} HRmax 算高。" +
                "这是整段课用一个平均心率，不是逐秒分区，也不是实验室测的通气阈。" +
                "没有心率时，有氧训练效果低于 3.0 算低，3.0 到 4.0 算中，达到 4.0 算高。",
        )
        return notes
    }

    fun normalizeRangeKey(value: Any?): String {
        val raw = (value?.toString() ?: "90").trim().lowercase()
        if (raw in setOf("1", "day", "today")) return "today"
        if (raw in RANGE_KEYS) return raw
        return "90"
    }

    fun availableRanges(historyDays: Int): JSONArray {
        val keys = mutableListOf("today", "7", "30", "90")
        if (historyDays > 90) keys.add("all")
        val out = JSONArray()
        for (key in keys) {
            val days = RANGE_DAYS[key]
            out.put(
                JSONObject().apply {
                    put("key", key)
                    put("label", RANGE_LABELS[key])
                    if (days == null) put("days", JSONObject.NULL) else put("days", days)
                },
            )
        }
        return out
    }


    private fun emptyReport(sample: Boolean, rangeKey: String = "90"): JSONObject {
        val key = normalizeRangeKey(rangeKey)
        val summary = JSONObject().apply {
            jsonPut(this, "ctl", null)
            jsonPut(this, "atl", null)
            jsonPut(this, "tsb", null)
            jsonPut(this, "acwr", null)
            jsonPut(this, "acute_load", null)
            jsonPut(this, "chronic_load", null)
            put("history_days", 0)
            put("load_method", "none")
            put("load_method_label", METHOD_LABELS["none"])
            put("load_method_shares", JSONObject())
            jsonPut(this, "weekly_hours", null)
            jsonPut(this, "previous_weekly_hours", null)
            jsonPut(this, "weekly_km", null)
            jsonPut(this, "previous_weekly_km", null)
            jsonPut(this, "weekly_load", null)
            jsonPut(this, "rhr_7d", null)
            jsonPut(this, "rhr_baseline", null)
            jsonPut(this, "rhr_delta", null)
            put("rhr_elevated", false)
            jsonPut(this, "hrv_3d", null)
            jsonPut(this, "hrv_baseline", null)
            jsonPut(this, "hrv_ratio", null)
            jsonPut(this, "hrv_last", null)
            jsonPut(this, "hrv_band_low", null)
            jsonPut(this, "hrv_band_high", null)
            put("hrv_suppressed", false)
            jsonPut(this, "sleep_7d_hours", null)
            jsonPut(this, "sleep_last_hours", null)
            put("sleep_short", false)
            jsonPut(this, "body_battery", null)
            jsonPut(this, "stress_7d", null)
            jsonPut(this, "steps_7d", null)
            jsonPut(this, "hr_max", null)
            jsonPut(this, "hr_max_source", null)
            jsonPut(this, "intensity", null)
            put("garmin", JSONObject())
        }
        val series = JSONObject().apply {
            put("dates", JSONArray())
            put("load", JSONArray())
            put("ctl", JSONArray())
            put("atl", JSONArray())
            put("tsb", JSONArray())
            put("acwr", JSONArray())
            put("rhr", JSONArray())
            put("hrv", JSONArray())
            put("hrv_low", JSONArray())
            put("hrv_high", JSONArray())
            put("sleep_hours", JSONArray())
            put("body_battery", JSONArray())
            put("stress", JSONArray())
            put("weeks", JSONArray())
        }
        val notes = JSONArray()
        if (!sample) notes.put("CTL/ATL 从这段数据的第一天以 0 起步。")
        else notes.put("这是示例数据，不是佳明账号里的记录。")
        return JSONObject().apply {
            put("sample", sample)
            put("generated_at", Clock.isoSh())
            put("range", key)
            put("range_label", RANGE_LABELS[key])
            put("available_ranges", availableRanges(0))
            jsonPut(this, "window", null)
            put("summary", summary)
            put("flags", JSONArray())
            put("suggestions", JSONArray().put("没有可计算的日期。同步之后这里会用你自己的活动重算。"))
            put("notes", notes)
            put("series", series)
            put("methods", methodNotes())
        }
    }

    private fun <T> sliceSeries(values: List<T>, startIdx: Int): List<T> = values.drop(startIdx)

    private fun dedupeActivities(activities: JSONArray?): List<JSONObject> {
        val kept = mutableListOf<JSONObject>()
        val seen = mutableSetOf<Any>()
        if (activities == null) return kept
        for (i in 0 until activities.length()) {
            val raw = activities.opt(i)
            if (raw !is JSONObject) continue
            if (parseDate(jsonGet(raw, "date")) == null) continue
            val marker = jsonGet(raw, "id")
            if (marker != null && marker != JSONObject.NULL) {
                if (marker in seen) continue
                seen.add(marker)
            }
            kept.add(raw)
        }
        return kept
    }

    private fun mergeDays(days: JSONArray?): Map<LocalDate, JSONObject> {
        val merged = linkedMapOf<LocalDate, JSONObject>()
        if (days == null) return merged
        for (i in 0 until days.length()) {
            val raw = days.opt(i)
            if (raw !is JSONObject) continue
            val stamp = parseDate(jsonGet(raw, "date")) ?: continue
            val current = merged.getOrPut(stamp) { JSONObject() }
            val keys = raw.keys()
            while (keys.hasNext()) {
                val key = keys.next()
                if (key == "date") continue
                val value = raw.opt(key)
                if (value != null && value != JSONObject.NULL) current.put(key, value)
            }
        }
        return merged
    }

    private fun garminBlock(profile: JSONObject): JSONObject {
        val status = jsonGet(profile, "training_status")
        return JSONObject().apply {
            jsonPut(this, "vo2max_running", roundVal(jsonGet(profile, "vo2max_running"), 1))
            jsonPut(this, "vo2max_cycling", roundVal(jsonGet(profile, "vo2max_cycling"), 1))
            jsonPut(this, "training_status", status)
            jsonPut(this, "training_status_label", statusLabel(status))
            jsonPut(this, "weekly_training_load", roundVal(jsonGet(profile, "weekly_training_load"), 1))
            jsonPut(this, "load_tunnel_min", roundVal(jsonGet(profile, "load_tunnel_min"), 1))
            jsonPut(this, "load_tunnel_max", roundVal(jsonGet(profile, "load_tunnel_max"), 1))
            jsonPut(this, "acute_load", roundVal(jsonGet(profile, "garmin_acute_load"), 1))
            jsonPut(this, "chronic_load", roundVal(jsonGet(profile, "garmin_chronic_load"), 1))
        }
    }

    private fun windowHours(
        activities: List<JSONObject>,
        start: LocalDate,
        end: LocalDate,
    ): Pair<Double, Double> {
        var seconds = 0.0
        var meters = 0.0
        for (activity in activities) {
            val stamp = parseDate(jsonGet(activity, "date")) ?: continue
            if (stamp.isBefore(start) || stamp.isAfter(end)) continue
            seconds += maxOf(0.0, toFloat(jsonGet(activity, "duration_s")) ?: 0.0)
            meters += maxOf(0.0, toFloat(jsonGet(activity, "distance_m")) ?: 0.0)
        }
        return seconds / 3600.0 to meters / 1000.0
    }

    private fun weeks(dates: List<LocalDate>, activities: List<JSONObject>): JSONArray {
        if (dates.isEmpty()) return JSONArray()
        // Monday-based weeks (ISO): dayOfWeek Monday=1 ... Sunday=7
        val first = dates.first().minusDays(dates.first().dayOfWeek.value.toLong() - 1L)
        val last = dates.last().minusDays(dates.last().dayOfWeek.value.toLong() - 1L)
        val mondays = mutableListOf<LocalDate>()
        var cursor = first
        while (!cursor.isAfter(last)) {
            mondays.add(cursor)
            cursor = cursor.plusDays(7)
        }
        val weekMondays = mondays.takeLast(8)
        val buckets = weekMondays.associateWith { mutableMapOf<String, Double>() }.toMutableMap()
        val km = weekMondays.associateWith { 0.0 }.toMutableMap()
        for (activity in activities) {
            val stamp = parseDate(jsonGet(activity, "date")) ?: continue
            val monday = stamp.minusDays(stamp.dayOfWeek.value.toLong() - 1L)
            if (monday !in buckets) continue
            val sport = sportName(jsonGet(activity, "type")?.toString())
            val hours = maxOf(0.0, toFloat(jsonGet(activity, "duration_s")) ?: 0.0) / 3600.0
            buckets.getValue(monday)[sport] = (buckets.getValue(monday)[sport] ?: 0.0) + hours
            km[monday] = km.getValue(monday) + maxOf(0.0, toFloat(jsonGet(activity, "distance_m")) ?: 0.0) / 1000.0
        }
        val rows = JSONArray()
        for (monday in weekMondays) {
            val sports = JSONObject()
            for ((name, value) in buckets.getValue(monday)) {
                if (value > 0) sports.put(name, roundVal(value, 2)!!)
            }
            rows.put(
                JSONObject().apply {
                    put("start", monday.toString())
                    put("label", "%02d/%02d".format(monday.monthValue, monday.dayOfMonth))
                    put("sports", sports)
                    put("km", roundVal(km.getValue(monday), 1)!!)
                },
            )
        }
        return rows
    }

    private fun hrRestFor(day: JSONObject, profile: JSONObject, fallback: Double?): Double? {
        val value = toFloat(jsonGet(day, "resting_hr"))
        if (value != null && value > 0) return value
        val stated = toFloat(jsonGet(profile, "hr_rest"))
        if (stated != null && stated > 0) return stated
        return fallback
    }

    private fun nullableNumberArray(values: List<Double?>, digits: Int): JSONArray {
        val arr = JSONArray()
        for (value in values) {
            if (value == null) arr.put(JSONObject.NULL)
            else arr.put(roundVal(value, digits)!!)
        }
        return arr
    }

    private fun stringArray(values: List<String>): JSONArray {
        val arr = JSONArray()
        values.forEach { arr.put(it) }
        return arr
    }

    private fun flagArray(flags: List<JSONObject>): JSONArray {
        val arr = JSONArray()
        flags.forEach { arr.put(it) }
        return arr
    }

    fun analyze(bundle: JSONObject, rangeKey: String? = "90"): JSONObject {
        val sample = bundle.optBoolean("sample", false)
        val normalizedRange = normalizeRangeKey(rangeKey)
        val profile = asObject(jsonGet(bundle, "profile")) ?: JSONObject()
        val activities = dedupeActivities(asArray(jsonGet(bundle, "activities")))
        val dayMap = mergeDays(asArray(jsonGet(bundle, "days")))
        val stamps = dayMap.keys.toMutableSet()
        for (activity in activities) {
            parseDate(jsonGet(activity, "date"))?.let { stamps.add(it) }
        }
        if (stamps.isEmpty()) return emptyReport(sample, normalizedRange)

        val ordered = dateRange(stamps.minOrNull()!!, stamps.maxOrNull()!!)
        val byDay = ordered.associateWith { mutableListOf<JSONObject>() }.toMutableMap()
        for (activity in activities) {
            val stamp = parseDate(jsonGet(activity, "date"))
            if (stamp != null && stamp in byDay) byDay.getValue(stamp).add(activity)
        }
        val hrSources = activities.toMutableList()
        for (day in dayMap.values) {
            if (toFloat(jsonGet(day, "max_hr")) != null) {
                hrSources.add(JSONObject().put("max_hr", jsonGet(day, "max_hr")))
            }
        }
        val (hrMax, hrSource) = resolveHrMax(profile, hrSources)
        val restValues = ordered.mapNotNull { stamp -> toFloat(jsonGet(dayMap[stamp], "resting_hr")) }
        val restFallback = if (restValues.isNotEmpty()) median(restValues) else null
        val sex = jsonGet(profile, "sex")?.toString() ?: "unknown"
        val loads = mutableListOf<Double>()
        val methodCounts = mutableMapOf<String, Int>()
        for (stamp in ordered) {
            val day = dayMap[stamp] ?: JSONObject()
            val rest = hrRestFor(day, profile, restFallback)
            var total = 0.0
            for (activity in byDay.getValue(stamp)) {
                val (value, method) = sessionLoad(activity, sex, rest, hrMax)
                if (method != "none") methodCounts[method] = (methodCounts[method] ?: 0) + 1
                total += value
            }
            loads.add(total)
        }
        val pmc = pmcSeries(loads)
        val ratios = acwrSeries(loads)
        val rhrSeries = ordered.map { toFloat(jsonGet(dayMap[it], "resting_hr")) }
        val hrvSeries = ordered.map { toFloat(jsonGet(dayMap[it], "hrv")) }
        val hrvLowSeries = ordered.map { toFloat(jsonGet(dayMap[it], "hrv_baseline_low")) }
        val hrvHighSeries = ordered.map { toFloat(jsonGet(dayMap[it], "hrv_baseline_high")) }
        val sleepSeries = ordered.map { stamp ->
            val seconds = toFloat(jsonGet(dayMap[stamp], "sleep_seconds"))
            if (seconds == null) null else seconds / 3600.0
        }
        val batterySeries = ordered.map { toFloat(jsonGet(dayMap[it], "body_battery")) }
        val stressSeries = ordered.map { toFloat(jsonGet(dayMap[it], "stress")) }
        val stepsSeries = ordered.map { toFloat(jsonGet(dayMap[it], "steps")) }

        val rhr = rhrStatus(rhrSeries)
        val hrv = hrvStatus(hrvSeries)
        val sleep = sleepStatus(sleepSeries)
        val stressVals = tail(stressSeries, 7)
        val stressMean = if (stressVals.size >= 3) mean(stressVals) else null
        val stepVals = tail(stepsSeries, 7)
        val stepsMean = if (stepVals.isNotEmpty()) mean(stepVals) else null
        var battery: Double? = null
        for (value in batterySeries.takeLast(2).asReversed()) {
            if (value != null) {
                battery = value
                break
            }
        }
        val bandLow = hrvLowSeries.asReversed().firstOrNull { it != null }
        val bandHigh = hrvHighSeries.asReversed().firstOrNull { it != null }

        val loadMethod = when {
            methodCounts.isEmpty() -> "none"
            methodCounts.size == 1 -> methodCounts.keys.first()
            else -> "mixed"
        }
        val shareTotal = methodCounts.values.sum().coerceAtLeast(1)
        val shares = JSONObject()
        for ((key, value) in methodCounts) {
            shares.put(key, roundVal(value.toDouble() / shareTotal, 2)!!)
        }

        val end = ordered.last()
        val span = RANGE_DAYS[normalizedRange]
        val startIdx: Int
        val viewStart: LocalDate
        if (span == null) {
            startIdx = 0
            viewStart = ordered.first()
        } else {
            startIdx = maxOf(0, ordered.size - span)
            viewStart = ordered[startIdx]
        }
        val viewDates = ordered.drop(startIdx)
        val viewLoads = loads.drop(startIdx)

        val viewActivities = activities.filter { activity ->
            val stamp = parseDate(jsonGet(activity, "date"))
            stamp != null && !stamp.isBefore(viewStart) && !stamp.isAfter(end)
        }

        val minutes = mutableMapOf("low" to 0.0, "mid" to 0.0, "high" to 0.0)
        val howUsed = mutableSetOf<String>()
        var classifiedSeconds = 0.0
        var allSeconds = 0.0
        for (activity in viewActivities) {
            allSeconds += maxOf(0.0, toFloat(jsonGet(activity, "duration_s")) ?: 0.0)
            val (bucket, how) = classify(activity, hrMax)
            if (bucket == null || how == null) continue
            val seconds = maxOf(0.0, toFloat(jsonGet(activity, "duration_s")) ?: 0.0)
            minutes[bucket] = minutes.getValue(bucket) + seconds / 60.0
            classifiedSeconds += seconds
            howUsed.add(how)
        }
        val minuteTotal = minutes.values.sum()
        val intensity: JSONObject? = if (minuteTotal > 0) {
            val how = when (howUsed) {
                setOf("hr") -> "hr"
                setOf("te") -> "te"
                else -> "mixed"
            }
            JSONObject().apply {
                put("low", roundVal(minutes.getValue("low") / minuteTotal, 4)!!)
                put("mid", roundVal(minutes.getValue("mid") / minuteTotal, 4)!!)
                put("high", roundVal(minutes.getValue("high") / minuteTotal, 4)!!)
                put("minutes", roundVal(minuteTotal, 1)!!)
                put("method", how)
                put("method_label", INTENSITY_LABELS[how])
            }
        } else null

        val (rangeHours, rangeKm) = windowHours(activities, viewStart, end)
        val weekStart = end.minusDays(6)
        val prevEnd = weekStart.minusDays(1)
        val prevStart = prevEnd.minusDays(6)
        val (weeklyHours, weeklyKm) = windowHours(activities, weekStart, end)
        val (previousHours, previousKm) = if (!prevStart.isBefore(ordered.first())) {
            windowHours(activities, prevStart, prevEnd)
        } else null to null
        val acute = if (loads.size >= 7) mean(loads.takeLast(7)) else null
        val chronic = if (loads.size >= 28) mean(loads.takeLast(28)) else null
        val weeklyLoad = if (loads.size >= 7) loads.takeLast(7).sum() else null
        val rangeLoad = if (viewLoads.isNotEmpty()) viewLoads.sum() else null

        val summary = JSONObject().apply {
            jsonPut(this, "ctl", roundVal(pmc.getValue("ctl").last(), 1))
            jsonPut(this, "atl", roundVal(pmc.getValue("atl").last(), 1))
            jsonPut(this, "tsb", roundVal(pmc.getValue("tsb").last(), 1))
            jsonPut(this, "acwr", roundVal(ratios.last(), 2))
            jsonPut(this, "acute_load", roundVal(acute, 1))
            jsonPut(this, "chronic_load", roundVal(chronic, 1))
            put("history_days", ordered.size)
            put("range_days", viewDates.size)
            put("load_method", loadMethod)
            put("load_method_label", METHOD_LABELS[loadMethod])
            put("load_method_shares", shares)
            jsonPut(this, "range_hours", roundVal(rangeHours, 1))
            jsonPut(this, "range_km", roundVal(rangeKm, 1))
            jsonPut(this, "range_load", roundVal(rangeLoad, 1))
            jsonPut(this, "weekly_hours", roundVal(weeklyHours, 1))
            jsonPut(this, "previous_weekly_hours", roundVal(previousHours, 1))
            jsonPut(this, "weekly_km", roundVal(weeklyKm, 1))
            jsonPut(this, "previous_weekly_km", roundVal(previousKm, 1))
            jsonPut(this, "weekly_load", roundVal(weeklyLoad, 1))
            jsonPut(this, "rhr_7d", roundVal(jsonGet(rhr, "recent"), 1))
            jsonPut(this, "rhr_baseline", roundVal(jsonGet(rhr, "baseline"), 1))
            jsonPut(this, "rhr_delta", roundVal(jsonGet(rhr, "delta"), 1))
            put("rhr_elevated", rhr.optBoolean("elevated"))
            jsonPut(this, "hrv_3d", roundVal(jsonGet(hrv, "recent"), 1))
            jsonPut(this, "hrv_baseline", roundVal(jsonGet(hrv, "baseline"), 1))
            jsonPut(this, "hrv_ratio", roundVal(jsonGet(hrv, "ratio"), 3))
            jsonPut(this, "hrv_last", roundVal(jsonGet(hrv, "last"), 1))
            jsonPut(this, "hrv_band_low", roundVal(bandLow, 1))
            jsonPut(this, "hrv_band_high", roundVal(bandHigh, 1))
            put("hrv_suppressed", hrv.optBoolean("suppressed"))
            jsonPut(this, "sleep_7d_hours", roundVal(jsonGet(sleep, "mean7"), 2))
            jsonPut(this, "sleep_last_hours", roundVal(jsonGet(sleep, "last"), 2))
            put("sleep_short", sleep.optBoolean("short"))
            jsonPut(this, "body_battery", roundVal(battery, 1))
            jsonPut(this, "stress_7d", roundVal(stressMean, 1))
            jsonPut(this, "steps_7d", roundVal(stepsMean, 0))
            jsonPut(this, "hr_max", roundVal(hrMax, 1))
            jsonPut(this, "hr_max_source", hrSource)
            jsonPut(this, "intensity", intensity)
            put("garmin", garminBlock(profile))
        }

        val flags = buildFlags(summary)
        val flagIds = flags.map { it.getString("id") }.toSet()
        val notes = JSONArray()
        if (sample) {
            notes.put(
                "这是示例数据，不是佳明账号里的记录。数字里故意放了一次负荷升高、静息心率上移和几晚睡眠偏短，用来把警报画出来。",
            )
        }
        notes.put(
            "CTL/ATL 从全部 ${ordered.size} 天历史的第一天以 0 起步，图表按所选时间范围截取。" +
                "更早的训练没有包括在内，开头几周的 CTL 会偏低。",
        )
        notes.put("负荷口径：${METHOD_LABELS[loadMethod]}。")
        if (loadMethod == "mixed") {
            val parts = mutableListOf<String>()
            val keys = shares.keys()
            while (keys.hasNext()) {
                val key = keys.next()
                parts.add("${METHOD_LABELS[key]} ${pct0(shares.getDouble(key))}")
            }
            notes.put("不同活动的口径不一样：" + parts.joinToString("，") + "。CTL 的绝对值不要和单一口径硬比。")
        }
        if (hrSource != null && intensity != null && intensity.getString("method") in setOf("hr", "mixed")) {
            notes.put("强度分区用的最大心率是 ${fmtNum(hrMax)}（$hrSource）。")
        }
        if (allSeconds > 0 && classifiedSeconds / allSeconds < 0.5) {
            notes.put("所选范围内有一半以上的训练时长没有心率，也没有有氧训练效果，没有进入强度分布。")
        }
        if (hrv.optBoolean("stale")) notes.put("最近几天没有 HRV，基线比较没有更新。")

        fun seriesRound(values: List<Double?>, digits: Int = 2): JSONArray =
            nullableNumberArray(sliceSeries(values, startIdx), digits)

        fun seriesRoundNonNull(values: List<Double>, digits: Int = 2): JSONArray =
            nullableNumberArray(sliceSeries(values.map { it as Double? }, startIdx), digits)

        val acwrView = JSONArray()
        for (value in sliceSeries(ratios, startIdx)) {
            if (value == null) acwrView.put(JSONObject.NULL)
            else acwrView.put(roundVal(value, 3)!!)
        }

        val suggestionsArr = JSONArray()
        suggestions(summary, flagIds).forEach { suggestionsArr.put(it) }

        return JSONObject().apply {
            put("sample", sample)
            put("generated_at", Clock.isoSh())
            put("range", normalizedRange)
            put("range_label", RANGE_LABELS[normalizedRange])
            put("available_ranges", availableRanges(ordered.size))
            put(
                "window",
                JSONObject().apply {
                    put("start", viewStart.toString())
                    put("end", end.toString())
                    put("days", viewDates.size)
                    put("history_days", ordered.size)
                    put("history_start", ordered.first().toString())
                },
            )
            put("summary", summary)
            put("flags", flagArray(flags))
            put("suggestions", suggestionsArr)
            put("notes", notes)
            put(
                "series",
                JSONObject().apply {
                    put("dates", stringArray(viewDates.map { it.toString() }))
                    put("load", seriesRoundNonNull(loads, 2))
                    put("ctl", seriesRoundNonNull(pmc.getValue("ctl"), 2))
                    put("atl", seriesRoundNonNull(pmc.getValue("atl"), 2))
                    put("tsb", seriesRoundNonNull(pmc.getValue("tsb"), 2))
                    put("acwr", acwrView)
                    put("rhr", seriesRound(rhrSeries, 1))
                    put("hrv", seriesRound(hrvSeries, 1))
                    put("hrv_low", seriesRound(hrvLowSeries, 1))
                    put("hrv_high", seriesRound(hrvHighSeries, 1))
                    put("sleep_hours", seriesRound(sleepSeries, 2))
                    put("body_battery", seriesRound(batterySeries, 1))
                    put("stress", seriesRound(stressSeries, 1))
                    put("weeks", weeks(viewDates, viewActivities))
                },
            )
            put("methods", methodNotes())
        }
    }

}
