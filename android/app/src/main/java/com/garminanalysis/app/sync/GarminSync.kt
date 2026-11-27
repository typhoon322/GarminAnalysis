package com.garminanalysis.app.sync

import android.util.Base64
import com.garminanalysis.app.Clock
import com.garminanalysis.app.coach.Coach
import com.garminanalysis.app.data.Storage
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.util.concurrent.TimeUnit

/**
 * Garmin Connect Personal API client (API Key → bearer token → pull).
 * Port of garmin_sync.py using OkHttp 4.x.
 */
object GarminSync {
    const val BASE_URL_GLOBAL = "https://personal.connectapi.garmin.com"
    const val BASE_URL_CN = "https://personal.connectapi.garmin.cn"
    const val BASE_URL = BASE_URL_GLOBAL
    const val TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
    const val TOKEN_REFRESH_WINDOW_SECONDS = 60 * 60
    const val REQUEST_TIMEOUT_SEC = 45L
    const val ACTIVITY_PAGE_LIMIT = 40
    const val ACTIVITY_CHUNK_DAYS = 28
    const val SLEEP_CHUNK_DAYS = 28
    const val HRV_CHUNK_DAYS = 28
    const val BATTERY_CHUNK_DAYS = 28
    const val STRESS_CHUNK_DAYS = 28
    const val STEPS_CHUNK_DAYS = 28

    private val SYNC_DAYS = SyncPolicy.BACKFILL_DAYS

    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(REQUEST_TIMEOUT_SEC, TimeUnit.SECONDS)
        .readTimeout(REQUEST_TIMEOUT_SEC, TimeUnit.SECONDS)
        .writeTimeout(REQUEST_TIMEOUT_SEC, TimeUnit.SECONDS)
        .build()

    fun baseUrlFor(user: JSONObject?): String {
        val region = (user?.optString("region") ?: "global").trim().lowercase()
        return if (region in setOf("cn", "china", "garmin.cn")) BASE_URL_CN else BASE_URL_GLOBAL
    }

    private fun scrub(text: String, secrets: List<String>): String {
        var cleaned = text
        for (secret in secrets) {
            if (secret.length >= 3) cleaned = cleaned.replace(secret, "***")
        }
        return cleaned.take(240)
    }

    private fun fail(message: String): JSONObject =
        JSONObject().put("ok", false).put("message", message).put("bundle", JSONObject.NULL)

    private fun ok(bundle: JSONObject, message: String): JSONObject =
        JSONObject().put("ok", true).put("message", message).put("bundle", bundle)

    fun publicSyncResult(result: JSONObject): JSONObject {
        val payload = JSONObject()
            .put("ok", result.optBoolean("ok", false))
            .put("message", result.optString("message", ""))
        for (key in listOf(
            "rate_limited", "retry_after_seconds", "cooldown_seconds", "last_sync_at",
            "sync_mode", "sync_start", "sync_end", "min_interval_minutes", "daily_cap",
            "syncs_today", "can_sync",
        )) {
            if (result.has(key)) payload.put(key, result.opt(key))
        }
        return payload
    }

    private fun blocked(): JSONObject? {
        if (Coach.networkAllowed()) return null
        return fail("当前环境禁止连接佳明。")
    }

    private fun jwtExpiration(accessToken: String): Double {
        try {
            val encoded = accessToken.split(".").getOrNull(1)
                ?: throw IllegalArgumentException("missing payload")
            val pad = "=".repeat((4 - encoded.length % 4) % 4)
            val json = String(
                Base64.decode(encoded + pad, Base64.URL_SAFE or Base64.NO_WRAP),
                Charsets.UTF_8,
            )
            val payload = JSONObject(json)
            if (!payload.has("exp")) throw IllegalArgumentException("missing exp")
            return payload.getDouble("exp")
        } catch (e: Exception) {
            throw IllegalArgumentException("令牌响应不是带有 exp 的 JWT。", e)
        }
    }

    fun exchangeToken(apiKey: String, baseUrl: String = BASE_URL_GLOBAL): Pair<String, Double> {
        val request = Request.Builder()
            .url(baseUrl.trimEnd('/') + TOKEN_PATH)
            .header("Accept", "*/*")
            .header("Authorization", "ApiKey $apiKey")
            .post(ByteArray(0).toRequestBody(null))
            .build()
        client.newCall(request).execute().use { response ->
            val bodyText = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                val detail = scrub(bodyText, listOf(apiKey))
                throw RuntimeException("换取访问令牌失败：HTTP ${response.code} $detail".trim())
            }
            val accessToken = bodyText.trim()
            if (accessToken.isEmpty()) throw RuntimeException("换取访问令牌失败：响应为空。")
            return accessToken to jwtExpiration(accessToken)
        }
    }

    fun obtainAccessToken(
        user: JSONObject,
        storage: Storage,
        forceRefresh: Boolean = false,
        now: Double? = null,
    ): String {
        val apiKey = user.optString("personal_api_key", "").trim()
        if (apiKey.isEmpty()) {
            throw RuntimeException("这个配置没有 Personal API Key。请在页面里重新保存。")
        }
        val clock = now ?: (System.currentTimeMillis() / 1000.0)
        val userId = user.optString("id", "")
        if (!forceRefresh && userId.isNotEmpty()) {
            val cached = storage.loadBearer(userId, apiKey)
            if (cached != null) {
                val expiresAt = cached.optDouble("expires_at", 0.0)
                if (expiresAt - clock > TOKEN_REFRESH_WINDOW_SECONDS) {
                    return cached.getString("access_token")
                }
            }
        }
        val (accessToken, expiresAt) = exchangeToken(apiKey, baseUrlFor(user))
        if (userId.isNotEmpty()) {
            storage.saveBearer(userId, apiKey, accessToken, expiresAt)
        }
        return accessToken
    }

    private fun unwrapPayload(payload: Any?): Any? {
        if (payload !is JSONObject) return payload
        for (key in listOf("data", "hrvSummaries", "dailySleepDTOs", "activities", "activityList")) {
            val inner = payload.opt(key)
            if (inner is JSONArray) return inner
        }
        return payload
    }

    private fun apiGet(
        baseUrl: String,
        accessToken: String,
        path: String,
        query: Map<String, String>? = null,
    ): Any? {
        val builder = (baseUrl.trimEnd('/') + path).toHttpUrl().newBuilder()
        if (query != null) {
            for ((k, v) in query) builder.addQueryParameter(k, v)
        }
        val request = Request.Builder()
            .url(builder.build())
            .header("Accept", "application/json")
            .header("Authorization", "Bearer $accessToken")
            .get()
            .build()
        client.newCall(request).execute().use { response ->
            if (response.code == 204) return null
            val body = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw RuntimeException("HTTP ${response.code} $path: ${body.take(180)}")
            }
            if (body.isBlank()) return null
            val trimmed = body.trim()
            return when {
                trimmed.startsWith("[") -> JSONArray(trimmed)
                trimmed.startsWith("{") -> JSONObject(trimmed)
                else -> trimmed
            }
        }
    }

    private fun call(
        baseUrl: String,
        accessToken: String,
        secrets: List<String>,
        label: String,
        path: String,
        query: Map<String, String>? = null,
    ): Pair<Any?, String?> {
        return try {
            apiGet(baseUrl, accessToken, path, query) to null
        } catch (e: Exception) {
            val detail = scrub("${e.javaClass.simpleName} $e", secrets)
            null to "$label 失败：$detail".trim().take(180)
        }
    }

    private fun daterangeChunks(start: LocalDate, end: LocalDate, maxDays: Int): List<Pair<LocalDate, LocalDate>> {
        if (end.isBefore(start)) return emptyList()
        val chunks = mutableListOf<Pair<LocalDate, LocalDate>>()
        var cursor = start
        val step = maxOf(1, maxDays)
        while (!cursor.isAfter(end)) {
            val chunkEnd = minOf(end, cursor.plusDays((step - 1).toLong()))
            chunks.add(cursor to chunkEnd)
            cursor = chunkEnd.plusDays(1)
        }
        return chunks
    }

    private fun searchActivitiesWindow(
        baseUrl: String,
        accessToken: String,
        secrets: List<String>,
        start: LocalDate,
        end: LocalDate,
    ): Pair<JSONArray, List<String>> {
        val warnings = mutableListOf<String>()
        val rows = JSONArray()
        var offset = 0
        while (true) {
            val params = mapOf(
                "start" to offset.toString(),
                "limit" to ACTIVITY_PAGE_LIMIT.toString(),
                "startDate" to start.toString(),
                "endDate" to end.toString(),
            )
            val label = "activities ${start}..$end@$offset"
            val path = "/activitylist-service/activities/search/activities"
            val (payload, err) = call(baseUrl, accessToken, secrets, label, path, params)
            if (err != null) {
                if ("HTTP 500" in err && start.isBefore(end)) {
                    val mid = start.plusDays((end.toEpochDay() - start.toEpochDay()) / 2)
                    if (!mid.isAfter(start) || !mid.isBefore(end)) {
                        warnings.add(err)
                        return rows to warnings
                    }
                    val (left, leftW) = searchActivitiesWindow(baseUrl, accessToken, secrets, start, mid)
                    val (right, rightW) = searchActivitiesWindow(
                        baseUrl, accessToken, secrets, mid.plusDays(1), end,
                    )
                    val merged = JSONArray()
                    for (i in 0 until left.length()) merged.put(left.get(i))
                    for (i in 0 until right.length()) merged.put(right.get(i))
                    return merged to (warnings + leftW + rightW)
                }
                warnings.add(err)
                break
            }
            val unwrapped = unwrapPayload(payload)
            val batch = when (unwrapped) {
                is JSONArray -> unwrapped
                is JSONObject -> JSONArray().put(unwrapped)
                else -> JSONArray()
            }
            if (batch.length() == 0) break
            for (i in 0 until batch.length()) rows.put(batch.get(i))
            if (batch.length() < ACTIVITY_PAGE_LIMIT) break
            offset += ACTIVITY_PAGE_LIMIT
            if (offset > 500) {
                warnings.add("活动列表分页过多，已截断。")
                break
            }
        }
        return rows to warnings
    }

    private fun fetchActivities(
        baseUrl: String,
        accessToken: String,
        secrets: List<String>,
        start: LocalDate,
        end: LocalDate,
    ): Pair<JSONArray, List<String>> {
        val warnings = mutableListOf<String>()
        val all = JSONArray()
        for ((cs, ce) in daterangeChunks(start, end, ACTIVITY_CHUNK_DAYS)) {
            val (rows, w) = searchActivitiesWindow(baseUrl, accessToken, secrets, cs, ce)
            for (i in 0 until rows.length()) all.put(rows.get(i))
            warnings.addAll(w)
        }
        return all to warnings
    }

    private fun fetchChunked(
        baseUrl: String,
        accessToken: String,
        secrets: List<String>,
        label: String,
        start: LocalDate,
        end: LocalDate,
        maxDays: Int,
        pathFor: (LocalDate, LocalDate) -> Pair<String, Map<String, String>?>,
    ): Pair<Any?, List<String>> {
        val warnings = mutableListOf<String>()
        val collected = JSONArray()
        for ((cs, ce) in daterangeChunks(start, end, maxDays)) {
            val (path, params) = pathFor(cs, ce)
            val (payload, err) = call(baseUrl, accessToken, secrets, label, path, params)
            if (err != null) {
                warnings.add(err)
                continue
            }
            if (payload == null) continue
            val unwrapped = unwrapPayload(payload)
            when (unwrapped) {
                is JSONArray -> for (i in 0 until unwrapped.length()) collected.put(unwrapped.get(i))
                else -> collected.put(unwrapped)
            }
        }
        if (collected.length() == 0) return null to warnings
        if (collected.length() == 1 && collected.opt(0) !is JSONArray) {
            return collected.opt(0) to warnings
        }
        return collected to warnings
    }

    private fun newestActivityDate(
        baseUrl: String,
        accessToken: String,
        secrets: List<String>,
    ): String? {
        val (payload, err) = call(
            baseUrl,
            accessToken,
            secrets,
            "activities-latest",
            "/activitylist-service/activities/search/activities",
            mapOf("start" to "0", "limit" to "5"),
        )
        if (err != null || payload == null) return null
        val rows = Normalize.normalizeActivities(unwrapPayload(payload))
        if (rows.length() == 0) return null
        val dates = sortedSetOf<String>()
        for (i in 0 until rows.length()) {
            val stamp = rows.getJSONObject(i).optString("date", "")
            if (stamp.isNotEmpty()) dates.add(stamp)
        }
        return dates.lastOrNull()
    }

    private fun profileFromUser(user: JSONObject): JSONObject =
        JSONObject()
            .put("sex", user.optString("sex", "").ifEmpty { "unknown" })
            .put("age", if (!user.has("age") || user.isNull("age")) JSONObject.NULL else user.opt("age"))
            .put("hr_max", if (!user.has("hr_max") || user.isNull("hr_max")) JSONObject.NULL else user.opt("hr_max"))

    private fun isOptionalMissing(err: String?): Boolean =
        err != null && ("HTTP 404" in err || "NOT_FOUND" in err)

    fun fetchBundle(
        accessToken: String,
        user: JSONObject,
        secrets: List<String>,
        start: LocalDate? = null,
        end: LocalDate? = null,
        syncMode: String = "backfill",
    ): Pair<JSONObject, List<String>> {
        var endDay = end ?: Clock.todaySh()
        var startDay = start ?: endDay.minusDays((SYNC_DAYS - 1).toLong())
        if (startDay.isAfter(endDay)) startDay = endDay
        val baseUrl = baseUrlFor(user)
        val warnings = mutableListOf<String>()
        val windowDays = (endDay.toEpochDay() - startDay.toEpochDay() + 1).toInt()

        val (activitiesPayload, actWarnings) = fetchActivities(baseUrl, accessToken, secrets, startDay, endDay)
        warnings.addAll(actWarnings)
        val activities = if (activitiesPayload.length() > 0) {
            Normalize.normalizeActivities(activitiesPayload)
        } else {
            JSONArray()
        }

        val days = linkedMapOf<String, JSONObject>()

        val (summaries, sumW) = fetchChunked(
            baseUrl, accessToken, secrets, "daily-summaries", startDay, endDay, 365,
        ) { s, e ->
            "/usersummary-service/v2/dailysummaries" to mapOf(
                "startDate" to s.toString(),
                "endDate" to e.toString(),
            )
        }
        warnings.addAll(sumW)
        Normalize.applySummaries(days, summaries)

        val (sleepPayload, sleepW) = fetchChunked(
            baseUrl, accessToken, secrets, "sleep-summaries", startDay, endDay, SLEEP_CHUNK_DAYS,
        ) { s, e ->
            "/sleep-service/sleep/dailySleepSummariesByDate" to mapOf(
                "startDate" to s.toString(),
                "endDate" to e.toString(),
            )
        }
        warnings.addAll(sleepW)
        Normalize.applySleep(days, sleepPayload)

        val (hrvPayload, hrvW) = fetchChunked(
            baseUrl, accessToken, secrets, "hrv-summaries", startDay, endDay, HRV_CHUNK_DAYS,
        ) { s, e -> "/hrv-service/hrv/daily/$s/$e" to null }
        warnings.addAll(hrvW)
        Normalize.applyHrv(days, hrvPayload)

        val (batteryPayload, batteryW) = fetchChunked(
            baseUrl, accessToken, secrets, "body-battery", startDay, endDay, BATTERY_CHUNK_DAYS,
        ) { s, e ->
            "/wellness-service/wellness/bodyBattery/reports/daily" to mapOf(
                "startDate" to s.toString(),
                "endDate" to e.toString(),
            )
        }
        warnings.addAll(batteryW)
        Normalize.applyBodyBattery(days, batteryPayload)

        val (stressPayload, stressW) = fetchChunked(
            baseUrl, accessToken, secrets, "stress", startDay, endDay, STRESS_CHUNK_DAYS,
        ) { s, e -> "/usersummary-service/stats/stress/daily/$s/$e" to null }
        warnings.addAll(stressW)
        Normalize.applyStress(days, stressPayload)

        val (stepsPayload, stepsW) = fetchChunked(
            baseUrl, accessToken, secrets, "steps", startDay, endDay, STEPS_CHUNK_DAYS,
        ) { s, e -> "/usersummary-service/stats/steps/daily/$s/$e" to null }
        warnings.addAll(stepsW)
        Normalize.applySteps(days, stepsPayload)

        val hasResting = days.values.any { row -> row.has("resting_hr") && !row.isNull("resting_hr") }
        if (!hasResting) {
            val hrRows = JSONArray()
            val hrStart = maxOf(startDay, endDay.minusDays(13))
            for ((day, _) in daterangeChunks(hrStart, endDay, 1)) {
                val (payload, err) = call(
                    baseUrl, accessToken, secrets, "heart-rate",
                    "/wellness-service/wellness/dailyHeartRate",
                    mapOf("date" to day.toString()),
                )
                if (err != null) {
                    warnings.add(err)
                    break
                }
                if (payload != null) hrRows.put(payload)
            }
            Normalize.applyHeartRate(days, hrRows)
        }

        val profile = profileFromUser(user)
        val (settings, settingsErr) = call(
            baseUrl, accessToken, secrets, "profile-settings",
            "/userprofile-service/userprofile/settings",
        )
        if (settingsErr != null) warnings.add(settingsErr)
        Normalize.applyProfileDemographics(profile, settings)
        val sex = user.optString("sex", "")
        if (sex in setOf("male", "female")) profile.put("sex", sex)
        if (user.has("age") && !user.isNull("age")) profile.put("age", user.opt("age"))
        if (user.has("hr_max") && !user.isNull("hr_max")) profile.put("hr_max", user.opt("hr_max"))

        val (vo2Payload, vo2Err) = call(
            baseUrl, accessToken, secrets, "vo2-max",
            "/metrics-service/metrics/maxmet/daily/$startDay/$endDay",
        )
        if (vo2Err != null && !isOptionalMissing(vo2Err)) warnings.add(vo2Err)
        Normalize.applyTraining(profile, null, vo2Payload)

        if (settings is JSONObject) {
            for (key in listOf("displayName", "userDisplayName", "fullName", "userName")) {
                val value = settings.opt(key)
                if (value is String && value.trim().isNotEmpty()) {
                    profile.put("garmin_name", value.trim().take(80))
                    break
                }
            }
        }

        if (activities.length() == 0 && days.isEmpty()) {
            val newest = newestActivityDate(baseUrl, accessToken, secrets)
            if (newest != null) {
                warnings.add(
                    "最近 $windowDays 天接口返回为空；账号上较新的活动日期是 $newest。" +
                        "请确认手表已同步到绑定此 Personal API Key 的 Connect 账号；" +
                        "中国区 Key 请选择中国区端点。",
                )
            }
        }

        val bundle = JSONObject()
            .put("sample", false)
            .put("fetched_at", Clock.isoSh())
            .put("sync_mode", syncMode)
            .put("sync_start", startDay.toString())
            .put("sync_end", endDay.toString())
            .put("warnings", JSONArray(warnings.take(12)))
            .put("profile", profile)
            .put("activities", activities)
            .put("days", Normalize.daysToArray(days))
        return bundle to warnings
    }

    fun syncAccount(
        user: JSONObject,
        storage: Storage,
        existing: JSONObject? = null,
        skipRateLimit: Boolean = false,
    ): JSONObject {
        blocked()?.let { return it }

        val apiKey = user.optString("personal_api_key", "").trim()
        val secrets = mutableListOf(apiKey)
        if (apiKey.isEmpty()) {
            return fail("这个配置没有 Personal API Key。请在页面里重新保存。")
        }

        val userId = user.optString("id", "").ifEmpty { null }
        if (!skipRateLimit) {
            val rate = storage.syncStateForUser(userId)
            if (rate.optBoolean("rate_limited", false)) {
                return JSONObject()
                    .put("ok", false)
                    .put("message", rate.optString("message", "").ifEmpty { "同步过于频繁。" })
                    .put("bundle", JSONObject.NULL)
                    .put("rate_limited", true)
                    .put("retry_after_seconds", rate.opt("cooldown_seconds") ?: 0)
                    .put("cooldown_seconds", rate.opt("cooldown_seconds") ?: 0)
                    .put("last_sync_at", rate.opt("last_sync_at") ?: JSONObject.NULL)
                    .put("min_interval_minutes", rate.opt("min_interval_minutes"))
                    .put("daily_cap", rate.opt("daily_cap"))
                    .put("syncs_today", rate.opt("syncs_today"))
                    .put("can_sync", false)
            }
        }

        var cache = existing
        if (cache == null && !userId.isNullOrEmpty()) {
            cache = try {
                storage.loadBundle(userId)
            } catch (_: Storage.StorageError) {
                null
            }
        }

        val window = SyncPolicy.syncWindow(
            existing = cache,
            backfillDays = SyncPolicy.BACKFILL_DAYS,
            overlapDays = SyncPolicy.DELTA_OVERLAP_DAYS,
        )
        val start = window.start
        val end = window.end
        val mode = window.mode

        val accessToken = try {
            obtainAccessToken(user, storage)
        } catch (e: Exception) {
            return fail("换取访问令牌失败：" + scrub("${e.javaClass.simpleName} $e", secrets))
        }
        secrets.add(accessToken)

        if (!skipRateLimit && !userId.isNullOrEmpty()) {
            try {
                storage.recordSyncAttempt(userId)
            } catch (_: Storage.StorageError) {
                // ignore
            }
        }

        val (incoming, warnings) = try {
            fetchBundle(accessToken, user, secrets, start, end, mode)
        } catch (e: Exception) {
            return fail("读取数据失败：" + scrub("${e.javaClass.simpleName} $e", secrets))
        }

        val bundle = if (cache != null) SyncPolicy.mergeBundles(cache, incoming) else incoming
        val hard = warnings.filter { "失败" in it && !isOptionalMissing(it) }
        val windowLabel = "$start 至 $end"

        fun withMeta(result: JSONObject): JSONObject {
            result.put("sync_mode", mode)
            result.put("sync_start", start.toString())
            result.put("sync_end", end.toString())
            return result
        }

        val days = bundle.optJSONArray("days") ?: JSONArray()
        val acts = bundle.optJSONArray("activities") ?: JSONArray()
        if (acts.length() == 0 && days.length() == 0) {
            if (hard.isNotEmpty()) {
                return withMeta(fail("已认证，但活动、睡眠、心率这些接口读取失败。" + hard[0]))
            }
            val hint = warnings.firstOrNull { "较新的活动日期" in it }
            if (hint != null) {
                return withMeta(ok(bundle, "同步完成，但窗口 $windowLabel 没有数据。" + hint))
            }
            return withMeta(ok(bundle, "同步完成，但窗口 $windowLabel 佳明没有返回活动或健康数据。"))
        }

        val message = if (mode == "backfill") {
            "已完成首次回填（$windowLabel），并写入本机缓存。"
        } else {
            "已增量同步（$windowLabel，含约 ${SyncPolicy.DELTA_OVERLAP_DAYS} 天重叠以防延迟上传），" +
                "并合并进本机缓存。"
        }
        return withMeta(ok(bundle, message))
    }
}
