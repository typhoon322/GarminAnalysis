package com.garminanalysis.app.sync

import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.time.format.DateTimeParseException

/**
 * Turn Garmin Connect payloads into the bundle shape analyze() expects.
 * Extractors look for known field names and ignore the rest; they do not invent values.
 */
object Normalize {
    private fun num(value: Any?): Double? {
        if (value == null || value == JSONObject.NULL || value is Boolean) return null
        val number = when (value) {
            is Number -> value.toDouble()
            is String -> value.toDoubleOrNull() ?: return null
            else -> return null
        }
        if (number.isNaN() || number.isInfinite()) return null
        return number
    }

    private fun day(value: Any?): String? {
        if (value !is String || value.length < 10) return null
        val text = value.take(10)
        return try {
            LocalDate.parse(text)
            text
        } catch (_: DateTimeParseException) {
            null
        }
    }

    private fun dateOf(node: JSONObject): String? {
        for (key in listOf("calendarDate", "date", "summaryDate", "calendarDay")) {
            val stamp = day(node.opt(key))
            if (stamp != null) return stamp
        }
        for (key in listOf("dailySleepDTO", "hrvSummary")) {
            val nested = node.optJSONObject(key)
            if (nested != null) {
                val stamp = dateOf(nested)
                if (stamp != null) return stamp
            }
        }
        val start = node.opt("startTimeLocal") ?: node.opt("startTimeGMT")
        if (start is String) {
            return day(start.replace("T", " "))
        }
        return null
    }

    private fun walk(node: Any?, yield: (JSONObject) -> Unit) {
        when (node) {
            is JSONArray -> {
                for (i in 0 until node.length()) walk(node.opt(i), yield)
            }
            is JSONObject -> {
                yield(node)
                val keys = node.keys()
                while (keys.hasNext()) {
                    val value = node.opt(keys.next())
                    if (value is JSONObject || value is JSONArray) walk(value, yield)
                }
            }
        }
    }

    private fun walkCollect(payload: Any?): Sequence<JSONObject> = sequence {
        val collected = mutableListOf<JSONObject>()
        walk(payload) { collected.add(it) }
        yieldAll(collected)
    }

    private fun first(node: JSONObject, keys: List<String>): Double? {
        for (key in keys) {
            val value = num(node.opt(key))
            if (value != null) return value
        }
        return null
    }

    fun normalizeActivities(payload: Any?): JSONArray {
        val rows = JSONArray()
        val seen = mutableSetOf<Any>()
        for (node in walkCollect(payload)) {
            val stamp = dateOf(node) ?: continue
            var duration = num(node.opt("duration"))
            if (duration == null) duration = num(node.opt("durationInSeconds"))
            if (duration == null) continue
            if (duration > 1_000_000) duration /= 1000.0
            if (duration <= 0 || duration > 48 * 3600) continue
            if (
                (!node.has("activityId") || node.isNull("activityId")) &&
                (!node.has("activityUUID") || node.isNull("activityUUID")) &&
                !node.has("activityType") &&
                !node.has("activityName") &&
                !node.has("activityTypeKey")
            ) {
                continue
            }
            val typeNode = node.opt("activityType")
            val sport = when (typeNode) {
                is JSONObject -> {
                    typeNode.opt("typeKey")?.toString()
                        ?: typeNode.opt("typeId")?.toString()
                        ?: "other"
                }
                is String -> typeNode
                else -> node.opt("activityTypeKey")?.toString() ?: "other"
            }
            val activityId = when {
                node.has("activityId") && !node.isNull("activityId") -> node.opt("activityId")
                node.has("activityUUID") && !node.isNull("activityUUID") -> node.opt("activityUUID")
                else -> null
            }
            if (activityId != null) {
                if (activityId in seen) continue
                seen.add(activityId)
            }
            var distance = num(node.opt("distance"))
            if (distance == null) distance = num(node.opt("distanceInMeters")) ?: 0.0
            fun putNum(obj: JSONObject, key: String, v: Double?) {
                if (v != null) obj.put(key, v) else obj.put(key, JSONObject.NULL)
            }
            val row = JSONObject()
                .put("id", activityId ?: JSONObject.NULL)
                .put("name", (node.opt("activityName") ?: node.opt("name") ?: "").toString())
                .put("type", sport)
                .put("date", stamp)
                .put("duration_s", duration)
                .put("distance_m", maxOf(0.0, distance))
            putNum(row, "avg_hr", first(node, listOf("averageHR", "avgHr", "averageHeartRate")))
            putNum(row, "max_hr", first(node, listOf("maxHR", "maxHr", "maxHeartRate")))
            putNum(row, "training_load", first(node, listOf("activityTrainingLoad", "trainingLoad")))
            putNum(row, "aerobic_te", first(node, listOf("aerobicTrainingEffect", "aerobicTrainingEffectValue")))
            rows.put(row)
        }
        return rows
    }

    private fun sleepScore(source: JSONObject): Double? {
        val scores = source.optJSONObject("sleepScores")
        if (scores != null) {
            val overall = scores.opt("overall")
            if (overall is JSONObject) {
                val value = num(overall.opt("value"))
                if (value != null) return value
            }
            if (overall is Number) return overall.toDouble()
        }
        return first(source, listOf("sleepScore", "overallScore"))
    }

    private fun apply(
        days: MutableMap<String, JSONObject>,
        payload: Any?,
        extractor: (JSONObject) -> Map<String, Any?>,
    ) {
        if (payload == null) return
        for (node in walkCollect(payload)) {
            val stamp = dateOf(node) ?: continue
            val fields = extractor(node)
            if (fields.isEmpty()) continue
            val row = days.getOrPut(stamp) { JSONObject().put("date", stamp) }
            for ((key, value) in fields) {
                if (value != null) row.put(key, value)
            }
        }
    }

    private fun hrvFields(node: JSONObject): Map<String, Any?> {
        val source = node.optJSONObject("hrvSummary") ?: node
        val value = first(source, listOf("lastNightAvg", "hrvValue"))
        if (value == null || value <= 0 || value > 300) return emptyMap()
        val baseline = source.optJSONObject("baseline")
        var low = if (baseline != null) num(baseline.opt("balancedLow")) else null
        var high = if (baseline != null) num(baseline.opt("balancedUpper")) else null
        if (low == null) low = first(source, listOf("baselineLow", "balancedLow"))
        if (high == null) high = first(source, listOf("baselineUpper", "balancedUpper"))
        val fields = mutableMapOf<String, Any?>("hrv" to value)
        if (low != null) fields["hrv_baseline_low"] = low
        if (high != null) fields["hrv_baseline_high"] = high
        return fields
    }

    private fun sleepFields(node: JSONObject): Map<String, Any?> {
        val source = node.optJSONObject("dailySleepDTO") ?: node
        var seconds = first(source, listOf("sleepTimeSeconds", "sleepTimeInSeconds")) ?: return emptyMap()
        if (seconds > 100_000) seconds /= 1000.0
        if (seconds <= 0 || seconds > 24 * 3600) return emptyMap()
        val fields = mutableMapOf<String, Any?>("sleep_seconds" to seconds)
        val score = sleepScore(source)
        if (score != null && score in 0.0..100.0) fields["sleep_score"] = score
        return fields
    }

    private fun nestedNum(node: JSONObject, path: List<String>): Double? {
        var current: Any? = node
        for (key in path) {
            if (current !is JSONObject) return null
            current = current.opt(key)
        }
        return num(current)
    }

    private fun rhrFields(node: JSONObject): Map<String, Any?> {
        var value = first(node, listOf("restingHeartRate", "restingHR"))
        if (value == null) value = nestedNum(node, listOf("heartRate", "restingValue"))
        if (value == null || value <= 25 || value > 120) return emptyMap()
        val fields = mutableMapOf<String, Any?>("resting_hr" to value)
        var maximum = first(node, listOf("maxHeartRate", "maxHR"))
        if (maximum == null) maximum = nestedNum(node, listOf("heartRate", "maxValue"))
        if (maximum != null && maximum in 80.0..230.0) fields["max_hr"] = maximum
        return fields
    }

    private fun stressFields(node: JSONObject): Map<String, Any?> {
        var value = first(node, listOf("overallStressLevel", "averageStressLevel", "avgStressLevel"))
        if (value == null) value = nestedNum(node, listOf("stress", "avgLevel"))
        if (value == null) value = nestedNum(node, listOf("values", "overallStressLevel"))
        if (value == null || value < 0 || value > 100) return emptyMap()
        return mapOf("stress" to value)
    }

    private fun stepsFields(node: JSONObject): Map<String, Any?> {
        var value = first(node, listOf("totalSteps"))
        if (value == null) value = nestedNum(node, listOf("movement", "steps", "value"))
        if (value == null) value = nestedNum(node, listOf("steps", "totalSteps"))
        if (value == null) {
            val raw = node.opt("steps")
            if (raw !is JSONObject) value = num(raw)
        }
        if (value == null || value < 0 || value > 200_000) return emptyMap()
        return mapOf("steps" to value)
    }

    private fun batteryFields(node: JSONObject): Map<String, Any?> {
        var level: Double? = null
        val series = node.optJSONArray("bodyBatteryValuesArray")
        if (series != null && series.length() > 0) {
            val last = series.opt(series.length() - 1)
            when (last) {
                is JSONArray -> if (last.length() >= 2) level = num(last.opt(1))
                is JSONObject -> level = num(last.opt("value") ?: last.opt("level"))
            }
        }
        if (level == null) {
            val battery = node.opt("bodyBattery")
            when {
                battery is JSONObject -> {
                    level = first(
                        battery,
                        listOf("latestValue", "endOfDayValue", "bodyBatteryMostRecentValue", "chargedValue"),
                    )
                }
                battery !is JSONArray -> level = num(battery)
            }
        }
        if (level == null) {
            level = first(
                node,
                listOf("bodyBatteryMostRecentValue", "endOfDayBodyBattery", "bodyBatteryHigh"),
            )
        }
        if (level == null || level < 0 || level > 100) return emptyMap()
        return mapOf("body_battery" to level)
    }

    private fun summaryFields(node: JSONObject): Map<String, Any?> {
        val fields = mutableMapOf<String, Any?>()
        fields.putAll(rhrFields(node))
        fields.putAll(stressFields(node))
        fields.putAll(stepsFields(node))
        fields.putAll(batteryFields(node))
        return fields
    }

    fun applyHrv(days: MutableMap<String, JSONObject>, payload: Any?) =
        apply(days, payload, ::hrvFields)

    fun applySleep(days: MutableMap<String, JSONObject>, payload: Any?) =
        apply(days, payload, ::sleepFields)

    fun applyHeartRate(days: MutableMap<String, JSONObject>, payload: Any?) =
        apply(days, payload, ::rhrFields)

    fun applyStress(days: MutableMap<String, JSONObject>, payload: Any?) =
        apply(days, payload, ::stressFields)

    fun applySteps(days: MutableMap<String, JSONObject>, payload: Any?) =
        apply(days, payload, ::stepsFields)

    fun applyBodyBattery(days: MutableMap<String, JSONObject>, payload: Any?) =
        apply(days, payload, ::batteryFields)

    fun applySummaries(days: MutableMap<String, JSONObject>, payload: Any?) =
        apply(days, payload, ::summaryFields)

    private fun findLabeledVo2(payload: Any?): Map<String, Double?> {
        val found = mutableMapOf<String, Double?>("running" to null, "cycling" to null)

        fun visit(node: Any?, parent: String) {
            when (node) {
                is JSONArray -> {
                    for (i in 0 until node.length()) visit(node.opt(i), parent)
                }
                is JSONObject -> {
                    val precise = num(node.opt("vo2MaxPreciseValue"))
                    val rough = num(node.opt("vo2MaxValue"))
                    val value = precise ?: rough
                    if (value != null && value in 10.0..90.0) {
                        val key = parent.lowercase()
                        when {
                            "cycl" in key || "bike" in key -> {
                                if (found["cycling"] == null) found["cycling"] = value
                            }
                            key in setOf("generic", "running") || "run" in key -> {
                                if (found["running"] == null) found["running"] = value
                            }
                            found["running"] == null -> found["running"] = value
                        }
                    }
                    val keys = node.keys()
                    while (keys.hasNext()) {
                        val childKey = keys.next()
                        val child = node.opt(childKey)
                        if (child is JSONObject || child is JSONArray) visit(child, childKey)
                    }
                }
            }
        }
        visit(payload, "")
        return found
    }

    private fun trainingRecords(payload: Any?): List<JSONObject> {
        if (payload !is JSONObject) return emptyList()
        val blob = payload.optJSONObject("latestTrainingStatusData")
            ?: payload.optJSONObject("mostRecentTrainingStatus")
            ?: payload
        val records = mutableListOf<JSONObject>()

        fun consider(node: JSONObject) {
            if (node.has("trainingStatus") || node.has("weeklyTrainingLoad") || node.has("trainingStatusPhrase")) {
                records.add(node)
            }
        }
        consider(blob)
        val keys = blob.keys()
        while (keys.hasNext()) {
            val value = blob.opt(keys.next())
            if (value is JSONObject) {
                consider(value)
                val innerKeys = value.keys()
                while (innerKeys.hasNext()) {
                    val inner = value.opt(innerKeys.next())
                    if (inner is JSONObject) consider(inner)
                }
            }
        }
        return records
    }

    fun applyTraining(profile: JSONObject, statusPayload: Any?, vo2Payload: Any?) {
        val vo2 = findLabeledVo2(vo2Payload ?: statusPayload).toMutableMap()
        if (statusPayload != null && (vo2["running"] == null || vo2["cycling"] == null)) {
            val extra = findLabeledVo2(statusPayload)
            if (vo2["running"] == null) vo2["running"] = extra["running"]
            if (vo2["cycling"] == null) vo2["cycling"] = extra["cycling"]
        }
        vo2["running"]?.let { profile.put("vo2max_running", it) }
        vo2["cycling"]?.let { profile.put("vo2max_cycling", it) }
        val records = trainingRecords(statusPayload)
        if (records.isEmpty()) return
        val chosen = records.last()
        var status = chosen.opt("trainingStatus")
        if (status == null || status == JSONObject.NULL) status = chosen.opt("trainingStatusPhrase")
        if (status != null && status != JSONObject.NULL) profile.put("training_status", status)
        num(chosen.opt("weeklyTrainingLoad"))?.let { profile.put("weekly_training_load", it) }
        num(chosen.opt("loadTunnelMin"))?.let { profile.put("load_tunnel_min", it) }
        num(chosen.opt("loadTunnelMax"))?.let { profile.put("load_tunnel_max", it) }
        val acute = chosen.optJSONObject("acuteTrainingLoadDTO")
        if (acute != null) {
            num(acute.opt("dailyTrainingLoadAcute"))?.let { profile.put("garmin_acute_load", it) }
            num(acute.opt("dailyTrainingLoadChronic"))?.let { profile.put("garmin_chronic_load", it) }
        }
    }

    fun applyProfileDemographics(profile: JSONObject, payload: Any?) {
        if (payload == null) return
        var gender: String? = null
        var birth: String? = null
        for (node in walkCollect(payload)) {
            if (gender == null) {
                val raw = node.opt("gender") ?: node.opt("genderType")
                if (raw is String) gender = raw
            }
            if (birth == null) {
                val rawBirth = node.opt("birthDate") ?: node.opt("birthdate")
                if (rawBirth is String) birth = rawBirth
            }
        }
        val sex = profile.optString("sex", "unknown")
        if (sex in setOf("", "unknown") && gender != null) {
            when (gender.trim().uppercase()) {
                "M", "MALE" -> profile.put("sex", "male")
                "F", "FEMALE" -> profile.put("sex", "female")
            }
        }
        if ((!profile.has("age") || profile.isNull("age")) && birth != null && birth.length >= 10) {
            val born = try {
                LocalDate.parse(birth.take(10))
            } catch (_: DateTimeParseException) {
                return
            }
            val today = LocalDate.now()
            var age = today.year - born.year
            if (today.monthValue < born.monthValue ||
                (today.monthValue == born.monthValue && today.dayOfMonth < born.dayOfMonth)
            ) {
                age -= 1
            }
            if (age in 10..100) profile.put("age", age)
        }
    }

    fun daysToArray(days: Map<String, JSONObject>): JSONArray {
        return JSONArray().also { arr ->
            days.keys.sorted().forEach { key -> arr.put(days[key]) }
        }
    }
}
