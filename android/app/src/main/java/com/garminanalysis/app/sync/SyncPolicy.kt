package com.garminanalysis.app.sync

import com.garminanalysis.app.Clock
import java.time.LocalDate
import java.time.ZonedDateTime
import java.time.format.DateTimeParseException
import org.json.JSONArray
import org.json.JSONObject

/**
 * Sync rate limits and incremental bundle merge.
 *
 * Limits are local guards against hammering Garmin Personal API.
 * Defaults: 20 minutes between syncs, 12 syncs per Shanghai calendar day.
 */
object SyncPolicy {
    const val DEFAULT_MIN_INTERVAL_MINUTES = 20
    const val DEFAULT_DAILY_CAP = 12
    val MIN_INTERVAL_BOUNDS = 5 to 180
    val DAILY_CAP_BOUNDS = 1 to 48
    const val BACKFILL_DAYS = 90
    const val DELTA_OVERLAP_DAYS = 2

    // Light throttle for DeepSeek coach chat (not Garmin).
    const val DEFAULT_COACH_MIN_INTERVAL_SECONDS = 20
    const val DEFAULT_COACH_DAILY_CAP = 40
    val COACH_INTERVAL_BOUNDS = 5 to 600
    val COACH_DAILY_CAP_BOUNDS = 5 to 200

    data class SyncWindow(
        val start: LocalDate,
        val end: LocalDate,
        val mode: String,
    )

    fun clampMinIntervalMinutes(value: Any?): Int {
        val number = when (value) {
            is Number -> value.toInt()
            is String -> value.toIntOrNull() ?: DEFAULT_MIN_INTERVAL_MINUTES
            else -> DEFAULT_MIN_INTERVAL_MINUTES
        }
        val (low, high) = MIN_INTERVAL_BOUNDS
        return number.coerceIn(low, high)
    }

    fun clampDailyCap(value: Any?): Int {
        val number = when (value) {
            is Number -> value.toInt()
            is String -> value.toIntOrNull() ?: DEFAULT_DAILY_CAP
            else -> DEFAULT_DAILY_CAP
        }
        val (low, high) = DAILY_CAP_BOUNDS
        return number.coerceIn(low, high)
    }

    private fun parseIso(value: String?): ZonedDateTime? {
        if (value.isNullOrBlank()) return null
        return try {
            val moment = ZonedDateTime.parse(value.trim())
            moment.withZoneSameInstant(Clock.SHANGHAI)
        } catch (_: DateTimeParseException) {
            try {
                // Offset datetime without zone name, or local datetime.
                val parsed = java.time.OffsetDateTime.parse(value.trim())
                parsed.atZoneSameInstant(Clock.SHANGHAI)
            } catch (_: DateTimeParseException) {
                try {
                    val local = java.time.LocalDateTime.parse(value.trim())
                    local.atZone(Clock.SHANGHAI)
                } catch (_: DateTimeParseException) {
                    null
                }
            }
        }
    }

    private fun parseDay(value: Any?): LocalDate? {
        when (value) {
            is LocalDate -> return value
            is ZonedDateTime -> return value.withZoneSameInstant(Clock.SHANGHAI).toLocalDate()
            is java.time.OffsetDateTime -> return value.atZoneSameInstant(Clock.SHANGHAI).toLocalDate()
            is String -> {
                if (value.length < 10) return null
                return try {
                    LocalDate.parse(value.substring(0, 10))
                } catch (_: DateTimeParseException) {
                    null
                }
            }
            else -> return null
        }
    }

    /**
     * Return (start, end, mode) where mode is "backfill" or "delta".
     */
    fun syncWindow(
        existing: JSONObject?,
        today: LocalDate? = null,
        backfillDays: Int = BACKFILL_DAYS,
        overlapDays: Int = DELTA_OVERLAP_DAYS,
    ): SyncWindow {
        val end = today ?: Clock.todaySh()
        if (existing == null) {
            val start = end.minusDays((maxOf(1, backfillDays) - 1).toLong())
            return SyncWindow(start, end, "backfill")
        }

        val anchors = mutableListOf<LocalDate>()
        val fetchedRaw = if (existing.isNull("fetched_at")) null else existing.optString("fetched_at", null)
        val fetched = parseIso(fetchedRaw)
        if (fetched != null) anchors.add(fetched.toLocalDate())

        val days = existing.optJSONArray("days")
        if (days != null) {
            for (i in 0 until days.length()) {
                val row = days.optJSONObject(i) ?: continue
                parseDay(row.opt("date"))?.let { anchors.add(it) }
            }
        }
        val activities = existing.optJSONArray("activities")
        if (activities != null) {
            for (i in 0 until activities.length()) {
                val row = activities.optJSONObject(i) ?: continue
                parseDay(row.opt("date"))?.let { anchors.add(it) }
            }
        }
        if (anchors.isEmpty()) {
            val start = end.minusDays((maxOf(1, backfillDays) - 1).toLong())
            return SyncWindow(start, end, "backfill")
        }

        val latest = anchors.maxOrNull()!!
        var start = latest.minusDays(maxOf(0, overlapDays).toLong())
        if (start.isAfter(end)) start = end
        val earliest = end.minusDays((maxOf(1, backfillDays) - 1).toLong())
        if (start.isBefore(earliest)) start = earliest
        return SyncWindow(start, end, "delta")
    }

    private fun activityKey(row: JSONObject): String {
        val activityId = row.opt("id")
        if (activityId != null && activityId != JSONObject.NULL && activityId.toString().trim().isNotEmpty()) {
            return "id:$activityId"
        }
        val date = row.opt("date")?.toString() ?: ""
        val name = row.opt("name")?.toString() ?: ""
        val duration = if (row.isNull("duration_s")) "" else row.opt("duration_s").toString()
        val distance = if (row.isNull("distance_m")) "" else row.opt("distance_m").toString()
        return "fallback:$date|$name|$duration|$distance"
    }

    fun mergeActivities(existing: JSONArray?, incoming: JSONArray?): JSONArray {
        val merged = linkedMapOf<String, JSONObject>()
        for (source in listOf(existing, incoming)) {
            if (source == null) continue
            for (i in 0 until source.length()) {
                val raw = source.optJSONObject(i) ?: continue
                val key = activityKey(raw)
                // Copy so later mutations don't alias callers.
                merged[key] = JSONObject(raw.toString())
            }
        }
        val rows = merged.values.toMutableList()
        rows.sortWith(
            compareBy(
                { it.optString("date", "") },
                { it.opt("id")?.toString() ?: "" },
            ),
        )
        val out = JSONArray()
        rows.forEach { out.put(it) }
        return out
    }

    fun mergeDays(existing: JSONArray?, incoming: JSONArray?): JSONArray {
        val byDate = linkedMapOf<String, JSONObject>()
        for (source in listOf(existing, incoming)) {
            if (source == null) continue
            for (i in 0 until source.length()) {
                val raw = source.optJSONObject(i) ?: continue
                val stamp = raw.opt("date")
                if (stamp !is String || stamp.length < 10) continue
                val key = stamp.substring(0, 10)
                val current = byDate.getOrPut(key) { JSONObject().put("date", key) }
                val keys = raw.keys()
                while (keys.hasNext()) {
                    val field = keys.next()
                    if (field == "date") continue
                    val value = raw.opt(field)
                    if (value != null && value != JSONObject.NULL) {
                        current.put(field, value)
                    }
                }
            }
        }
        val out = JSONArray()
        for (key in byDate.keys.sorted()) {
            out.put(byDate.getValue(key))
        }
        return out
    }

    fun mergeProfile(existing: JSONObject?, incoming: JSONObject?): JSONObject {
        val out = JSONObject()
        if (existing != null) {
            val keys = existing.keys()
            while (keys.hasNext()) {
                val key = keys.next()
                out.put(key, existing.opt(key))
            }
        }
        if (incoming != null) {
            val keys = incoming.keys()
            while (keys.hasNext()) {
                val key = keys.next()
                val value = incoming.opt(key)
                if (value != null && value != JSONObject.NULL && value != "") {
                    out.put(key, value)
                }
            }
        }
        return out
    }

    /** Merge a delta fetch into the local cache. Incoming wins on overlapping fields. */
    fun mergeBundles(existing: JSONObject?, incoming: JSONObject): JSONObject {
        val base = existing ?: JSONObject()
        val warningsRaw = incoming.opt("warnings")
        val warnings = when {
            warningsRaw is JSONArray -> warningsRaw
            else -> {
                val baseWarnings = base.optJSONArray("warnings") ?: JSONArray()
                val trimmed = JSONArray()
                for (i in 0 until minOf(12, baseWarnings.length())) {
                    trimmed.put(baseWarnings.opt(i))
                }
                trimmed
            }
        }
        val trimmedWarnings = JSONArray()
        for (i in 0 until minOf(12, warnings.length())) {
            trimmedWarnings.put(warnings.opt(i))
        }
        val baseProfile = base.optJSONObject("profile") ?: JSONObject()
        val incomingProfile = incoming.optJSONObject("profile")
        return JSONObject().apply {
            put("sample", false)
            put(
                "fetched_at",
                if (incoming.isNull("fetched_at") || incoming.opt("fetched_at") == null) {
                    Clock.isoSh()
                } else {
                    incoming.opt("fetched_at")
                },
            )
            val syncMode = if (!incoming.isNull("sync_mode")) {
                incoming.opt("sync_mode")
            } else {
                base.opt("sync_mode")
            }
            if (syncMode == null || syncMode == JSONObject.NULL) put("sync_mode", JSONObject.NULL)
            else put("sync_mode", syncMode)
            if (incoming.has("sync_start")) put("sync_start", incoming.opt("sync_start"))
            else put("sync_start", JSONObject.NULL)
            if (incoming.has("sync_end")) put("sync_end", incoming.opt("sync_end"))
            else put("sync_end", JSONObject.NULL)
            put("warnings", trimmedWarnings)
            put("profile", mergeProfile(baseProfile, incomingProfile))
            put("activities", mergeActivities(base.optJSONArray("activities"), incoming.optJSONArray("activities")))
            put("days", mergeDays(base.optJSONArray("days"), incoming.optJSONArray("days")))
        }
    }

    /** Return whether a sync is allowed and how long until the next one. */
    fun evaluateRateLimit(
        lastSyncAt: String?,
        syncDay: String?,
        syncCountToday: Int,
        minIntervalMinutes: Int = DEFAULT_MIN_INTERVAL_MINUTES,
        dailyCap: Int = DEFAULT_DAILY_CAP,
        now: ZonedDateTime? = null,
    ): JSONObject {
        val moment = now ?: Clock.nowSh()
        val today = moment.withZoneSameInstant(Clock.SHANGHAI).toLocalDate().toString()
        val interval = clampMinIntervalMinutes(minIntervalMinutes)
        val cap = clampDailyCap(dailyCap)
        var count = syncCountToday
        if (syncDay != today) count = 0
        var cooldown = 0
        val last = parseIso(lastSyncAt)
        if (last != null) {
            val elapsed = java.time.Duration.between(last, moment).seconds
            val needed = interval * 60L
            if (elapsed < needed) {
                cooldown = maxOf(1, (needed - elapsed).toInt())
            }
        }
        val overDaily = count >= cap
        val canSync = cooldown <= 0 && !overDaily
        var message = ""
        if (!canSync) {
            message = if (cooldown > 0) {
                val minutes = maxOf(1, (cooldown + 59) / 60)
                "同步过于频繁。为避免触发佳明接口限制，请至少间隔 $interval 分钟。" +
                    "还可等待约 $minutes 分钟后再试。"
            } else {
                "今天同步次数已达上限（$cap 次/天，按北京时间计）。" +
                    "请明天再试，或在设置里略微提高每日上限（仍建议保持保守）。"
            }
        }
        return JSONObject().apply {
            put("can_sync", canSync)
            put("cooldown_seconds", if (!overDaily) cooldown else 0)
            put("syncs_today", count)
            put("sync_day", today)
            put("min_interval_minutes", interval)
            put("daily_cap", cap)
            if (lastSyncAt == null) put("last_sync_at", JSONObject.NULL) else put("last_sync_at", lastSyncAt)
            put("message", message)
            put("rate_limited", !canSync)
        }
    }

    fun nextSyncCounters(
        syncDay: String?,
        syncCountToday: Int,
        now: ZonedDateTime? = null,
    ): Pair<String, Int> {
        val moment = now ?: Clock.nowSh()
        val today = moment.withZoneSameInstant(Clock.SHANGHAI).toLocalDate().toString()
        val count = syncCountToday
        return if (syncDay != today) today to 1 else today to (count + 1)
    }

    fun clampCoachIntervalSeconds(value: Any?): Int {
        val number = when (value) {
            is Number -> value.toInt()
            is String -> value.toIntOrNull() ?: DEFAULT_COACH_MIN_INTERVAL_SECONDS
            else -> DEFAULT_COACH_MIN_INTERVAL_SECONDS
        }
        val (low, high) = COACH_INTERVAL_BOUNDS
        return number.coerceIn(low, high)
    }

    fun clampCoachDailyCap(value: Any?): Int {
        val number = when (value) {
            is Number -> value.toInt()
            is String -> value.toIntOrNull() ?: DEFAULT_COACH_DAILY_CAP
            else -> DEFAULT_COACH_DAILY_CAP
        }
        val (low, high) = COACH_DAILY_CAP_BOUNDS
        return number.coerceIn(low, high)
    }

    fun evaluateCoachRateLimit(
        lastCoachAt: String?,
        coachDay: String?,
        coachCountToday: Int,
        minIntervalSeconds: Int = DEFAULT_COACH_MIN_INTERVAL_SECONDS,
        dailyCap: Int = DEFAULT_COACH_DAILY_CAP,
        now: ZonedDateTime? = null,
    ): JSONObject {
        val moment = now ?: Clock.nowSh()
        val today = moment.withZoneSameInstant(Clock.SHANGHAI).toLocalDate().toString()
        val interval = clampCoachIntervalSeconds(minIntervalSeconds)
        val cap = clampCoachDailyCap(dailyCap)
        var count = coachCountToday
        if (coachDay != today) count = 0
        var cooldown = 0
        val last = parseIso(lastCoachAt)
        if (last != null) {
            val elapsed = java.time.Duration.between(last, moment).seconds
            if (elapsed < interval) {
                cooldown = maxOf(1, (interval - elapsed).toInt())
            }
        }
        val overDaily = count >= cap
        val canChat = cooldown <= 0 && !overDaily
        var message = ""
        if (!canChat) {
            message = if (cooldown > 0) {
                "提问过快。请稍等约 $cooldown 秒后再问教练。"
            } else {
                "今天教练对话次数已达上限（$cap 次，按北京时间计）。请明天再试。"
            }
        }
        return JSONObject().apply {
            put("can_chat", canChat)
            put("rate_limited", !canChat)
            put("cooldown_seconds", if (!overDaily) cooldown else 0)
            put("chats_today", count)
            put("coach_day", today)
            put("min_interval_seconds", interval)
            put("daily_cap", cap)
            if (lastCoachAt == null) put("last_coach_at", JSONObject.NULL) else put("last_coach_at", lastCoachAt)
            put("message", message)
        }
    }
}
