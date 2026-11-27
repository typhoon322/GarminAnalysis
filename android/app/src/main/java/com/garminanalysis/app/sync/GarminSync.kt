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
import kotlin.math.max

/**
 * Garmin Connect Personal API client (API Key → bearer token → pull).
 * Blocking OkHttp calls — invoke from an IO dispatcher.
 */
class GarminSync(private val storage: Storage) {
    companion object {
        const val BASE_URL_GLOBAL = "https://personal.connectapi.garmin.com"
        const val BASE_URL_CN = "https://personal.connectapi.garmin.cn"
        const val BASE_URL = BASE_URL_GLOBAL
        const val TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
        const val TOKEN_REFRESH_WINDOW_SECONDS = 60 * 60
        const val REQUEST_TIMEOUT_SECONDS = 45L
        const val ACTIVITY_PAGE_LIMIT = 40
        const val ACTIVITY_CHUNK_DAYS = 28
        const val SLEEP_CHUNK_DAYS = 28
        const val HRV_CHUNK_DAYS = 28
        const val BATTERY_CHUNK_DAYS = 28
        const val STRESS_CHUNK_DAYS = 28
        const val STEPS_CHUNK_DAYS = 28
        val SYNC_DAYS = SyncPolicy.BACKFILL_DAYS
    }

    fun baseUrlFor(user: JSONObject?): String {
        val region = (user?.optString("region", "global") ?: "global").trim().lowercase()
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

    fun jwtExpiration(accessToken: String): Double {
        try {
            val encoded = accessToken.split(".")[1]
            val pad = (4 - encoded.length % 4) % 4
            val padded = encoded + "=".repeat(pad)
            val json = String(Base64.decode(padded, Base64.URL_SAFE or Base64.NO_WRAP), Charsets.UTF_8)
            return JSONObject(json).getDouble("exp")
        } catch (e: Exception) {
            throw IllegalArgumentException("令牌响应不是带有 exp 的 JWT。", e)
        }
    }

    private fun profileFromUser(user: JSONObject): JSONObject =
        JSONObject()
            .put("sex", user.optString("sex", "unknown").ifEmpty { "unknown" })
            .put("age", user.opt("age") ?: JSONObject.NULL)
            .put("hr_max", user.opt("hr_max") ?: JSONObject.NULL)

    private fun daterangeChunks(start: LocalDate, end: LocalDate, maxDays: Int): List<Pair<LocalDate, LocalDate>> {
        if (end.isBefore(start)) return emptyList()
        val chunks = mutableListOf<Pair<LocalDate, LocalDate>>()
        var cursor = start
        val step = max(1, maxDays)
        while (!cursor.isAfter(end)) {
            val chunkEnd = minOf(end, cursor.plusDays((step - 1).toLong()))
            chunks.add(cursor to chunkEnd)
            cursor = chunkEnd.plusDays(1)
        }
        return chunks
    }

    private fun unwrapPayload(payload: Any?): Any? {
        if (payload !is JSONObject) return payload
        for (key in listOf("data", "hrvSummaries", "dailySleepDTOs", "activities", "activityList")) {
            val inner = payload.opt(key)
            if (inner is JSONArray) return inner
        }
        return payload
    }

    private fun isOptionalMissing(err: String?): Boolean {
        if (err.isNullOrEmpty()) return false
        return "HTTP 404" in err || "NOT_FOUND" in err
    }

    private fun parseJsonBody(body: String?): Any? {
        if (body.isNullOrBlank()) return null
        val trimmed = body.trim()
        return when {
            trimmed.startsWith("[") -> JSONArray(trimmed)
            trimmed.startsWith("{") -> JSONObject(trimmed)
            else -> trimmed
        }
    }

    inner class PersonalApiClient(
        accessToken: String,
        baseUrl: String = BASE_URL_GLOBAL,
        timeoutSeconds: Long = REQUEST_TIMEOUT_SECONDS,
    ) : AutoCloseable {
        private val base = baseUrl.trimEnd('/')
        private val client = OkHttpClient.Builder()
            .connectTimeout(timeoutSeconds, TimeUnit.SECONDS)
            .readTimeout(timeoutSeconds, TimeUnit.SECONDS)
            .writeTimeout(timeoutSeconds, TimeUnit.SECONDS)
            .build()
        private val authHeader = "Bearer $accessToken"

        fun get(path: String, params: Map<String, String>? = null): Any? {
            var urlBuilder = "$base$path".toHttpUrl().newBuilder()
            params?.forEach { (k, v) -> urlBuilder = urlBuilder.addQueryParameter(k, v) }
            val request = Request.Builder()
                .url(urlBuilder.build())
                .header("Accept", "application/json")
                .header("Authorization", authHeader)
                .get()
                .build()
            client.newCall(request).execute().use { response ->
                if (response.code == 204) return null
                val text = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    throw RuntimeException("HTTP ${response.code} $path: ${text.take(180)}")
                }
                if (text.isEmpty()) return null
                return parseJsonBody(text)
            }
        }

        override fun close() {
            // OkHttpClient shares a pool; no per-client close required for short-lived use.
        }
    }

    fun exchangeToken(
        apiKey: String,
        baseUrl: String = BASE_URL_GLOBAL,
        timeoutSeconds: Long = REQUEST_TIMEOUT_SECONDS,
    ): Pair<String, Double> {
        val url = "${baseUrl.trimEnd('/')}$TOKEN_PATH"
        val client = OkHttpClient.Builder()
            .connectTimeout(timeoutSeconds, TimeUnit.SECONDS)
            .readTimeout(timeoutSeconds, TimeUnit.SECONDS)
            .build()
        val request = Request.Builder()
            .url(url)
            .header("Accept", "*/*")
            .header("Authorization", "ApiKey $apiKey")
            .post(ByteArray(0).toRequestBody(null))
            .build()
        client.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty().trim()
            if (!response.isSuccessful) {
                val detail = scrub(text, listOf(apiKey))
                throw RuntimeException("换取访问令牌失败：HTTP ${response.code} $detail".trim())
            }
            if (text.isEmpty()) throw RuntimeException("换取访问令牌失败：响应为空。")
            val expiresAt = jwtExpiration(text)
            return text to expiresAt
        }
    }

    fun obtainAccessToken(
        user: JSONObject,
        forceRefresh: Boolean = false,
        now: Double? = null,
    ): String {
        val apiKey = user.optString("personal_api_key", "").trim()
        if (apiKey.isEmpty()) {
            throw RuntimeException("这个配置没有 Personal API Key。请在页面里重新保存。")
        }
        val clock = now ?: (System.currentTimeMillis() / 1000.0)
        if (!forceRefresh) {
            val cached = storage.loadBearer(user.getString("id"), apiKey)
            if (cached != null && cached.getDouble("expires_at") - clock > TOKEN_REFRESH_WINDOW_SECONDS) {
                return cached.getString("access_token")
            }
        }
        val (accessToken, expiresAt) = exchangeToken(apiKey, baseUrlFor(user))
        storage.saveBearer(user.getString("id"), apiKey, accessToken, expiresAt)
        return accessToken
    }

    private fun call(
        client: PersonalApiClient,
        secrets: List<String>,
        label: String,
        path: String,
        params: Map<String, String>? = null,
    ): Pair<Any?, String?> {
        return try {
            client.get(path, params) to null
        } catch (exc: Exception) {
            val detail = scrub("${exc.javaClass.simpleName} $exc", secrets)
            null to "${label} 失败：$detail".trim().take(180)
        }
    }

    private fun fetchChunked(
        client: PersonalApiClient,
        secrets: List<String>,
        label: String,
        start: LocalDate,
        end: LocalDate,
        maxDays: Int,
        pathFor: (LocalDate, LocalDate) -> Pair<String, Map<String, String>?>,
    ): Pair<Any?, List<String>> {
        val warnings = mutableListOf<String>()
        val collected = mutableListOf<Any>()
        for ((chunkStart, chunkEnd) in daterangeChunks(start, end, maxDays)) {
            val (path, params) = pathFor(chunkStart, chunkEnd)
            val (payload, err) = call(client, secrets, label, path, params)
            if (err != null) {
                warnings.add(err)
                continue
            }
            if (payload == null) continue
            val unwrapped = unwrapPayload(payload)
            if (unwrapped is JSONArray) {
                for (i in 0 until unwrapped.length()) collected.add(unwrapped.get(i))
            } else if (unwrapped != null) {
                collected.add(unwrapped)
            }
        }
        if (collected.isEmpty()) return null to warnings
        if (collected.size == 1 && collected[0] !is JSONArray) return collected[0] to warnings
        return JSONArray().also { arr -> collected.forEach { arr.put(it) } } to warnings
    }

    private fun searchActivitiesWindow(
        client: PersonalApiClient,
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
            val label = "activities ${start}..${end}@$offset"
            val path = "/activitylist-service/activities/search/activities"
            val (payload, err) = call(client, secrets, label, path, params)
            if (err != null) {
                if ("HTTP 500" in err && start.isBefore(end)) {
                    val midDays = java.time.temporal.ChronoUnit.DAYS.between(start, end) / 2
                    var midDate = start.plusDays(midDays)
                    if (!midDate.isAfter(start)) midDate = start
                    if (!midDate.isBefore(end)) {
                        warnings.add(err)
                        return rows to warnings
                    }
                    val (left, leftW) = searchActivitiesWindow(client, secrets, start, midDate)
                    val (right, rightW) = searchActivitiesWindow(
                        client, secrets, midDate.plusDays(1), end,
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
            val batch = JSONArray()
            when (unwrapped) {
                is JSONArray -> for (i in 0 until unwrapped.length()) batch.put(unwrapped.get(i))
                is JSONObject -> batch.put(unwrapped)
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

    fun fetchActivities(
        client: PersonalApiClient,
        secrets: List<String>,
        start: LocalDate,
        end: LocalDate,
    ): Pair<JSONArray, List<String>> {
        val warnings = mutableListOf<String>()
        val allRows = JSONArray()
        for ((chunkStart, chunkEnd) in daterangeChunks(start, end, ACTIVITY_CHUNK_DAYS)) {
            val (rows, chunkWarnings) = searchActivitiesWindow(client, secrets, chunkStart, chunkEnd)
            for (i in 0 until rows.length()) allRows.put(rows.get(i))
            warnings.addAll(chunkWarnings)
        }
        return allRows to warnings
    }

    private fun newestActivityDate(client: PersonalApiClient, secrets: List<String>): String? {
        val (payload, err) = call(
            client, secrets, "activities-latest",
            "/activitylist-service/activities/search/activities",
            mapOf("start" to "0", "limit" to "5"),
        )
        if (err != null || payload == null) return null
        val rows = Normalize.normalizeActivities(unwrapPayload(payload))
        if (rows.length() == 0) return null
        val dates = mutableSetOf<String>()
        for (i in 0 until rows.length()) {
            val d = rows.getJSONObject(i).optString("date", "")
            if (d.isNotEmpty()) dates.add(d)
        }
        return dates.maxOrNull()
    }

    fun fetchBundle(
        client: PersonalApiClient,
        user: JSONObject,
        secrets: List<String>,
        start: LocalDate? = null,
        end: LocalDate? = null,
        syncMode: String = "backfill",
    ): Pair<JSONObject, List<String>> {
        var endDate = end ?: Clock.todaySh()
        var startDate = start ?: endDate.minusDays((SYNC_DAYS - 1).toLong())
        if (startDate.isAfter(endDate)) startDate = endDate
        val windowDays = java.time.temporal.ChronoUnit.DAYS.between(startDate, endDate) + 1
        val warnings = mutableListOf<String>()

        val (activitiesPayload, actWarnings) = fetchActivities(client, secrets, startDate, endDate)
        warnings.addAll(actWarnings)
        val activities = if (activitiesPayload.length() > 0) {
            Normalize.normalizeActivities(activitiesPayload)
        } else {
            JSONArray()
        }

        val days = mutableMapOf<String, JSONObject>()

        val (summaries, sumWarnings) = fetchChunked(
            client, secrets, "daily-summaries", startDate, endDate, 365,
        ) { s, e ->
            "/usersummary-service/v2/dailysummaries" to mapOf(
                "startDate" to s.toString(), "endDate" to e.toString(),
            )
        }
        warnings.addAll(sumWarnings)
        Normalize.applySummaries(days, summaries)

        val (sleepPayload, sleepWarnings) = fetchChunked(
            client, secrets, "sleep-summaries", startDate, endDate, SLEEP_CHUNK_DAYS,
        ) { s, e ->
            "/sleep-service/sleep/dailySleepSummariesByDate" to mapOf(
                "startDate" to s.toString(), "endDate" to e.toString(),
            )
        }
        warnings.addAll(sleepWarnings)
        Normalize.applySleep(days, sleepPayload)

        val (hrvPayload, hrvWarnings) = fetchChunked(
            client, secrets, "hrv-summaries", startDate, endDate, HRV_CHUNK_DAYS,
        ) { s, e ->
            "/hrv-service/hrv/daily/${s}/${e}" to null
        }
        warnings.addAll(hrvWarnings)
        Normalize.applyHrv(days, hrvPayload)

        val (batteryPayload, batteryWarnings) = fetchChunked(
            client, secrets, "body-battery", startDate, endDate, BATTERY_CHUNK_DAYS,
        ) { s, e ->
            "/wellness-service/wellness/bodyBattery/reports/daily" to mapOf(
                "startDate" to s.toString(), "endDate" to e.toString(),
            )
        }
        warnings.addAll(batteryWarnings)
        Normalize.applyBodyBattery(days, batteryPayload)

        val (stressPayload, stressWarnings) = fetchChunked(
            client, secrets, "stress", startDate, endDate, STRESS_CHUNK_DAYS,
        ) { s, e ->
            "/usersummary-service/stats/stress/daily/${s}/${e}" to null
        }
        warnings.addAll(stressWarnings)
        Normalize.applyStress(days, stressPayload)

        val (stepsPayload, stepsWarnings) = fetchChunked(
            client, secrets, "steps", startDate, endDate, STEPS_CHUNK_DAYS,
        ) { s, e ->
            "/usersummary-service/stats/steps/daily/${s}/${e}" to null
        }
        warnings.addAll(stepsWarnings)
        Normalize.applySteps(days, stepsPayload)

        if (days.values.none { it.has("resting_hr") && !it.isNull("resting_hr") }) {
            val hrRows = JSONArray()
            val hrStart = maxOf(startDate, endDate.minusDays(13))
            for ((day, _) in daterangeChunks(hrStart, endDate, 1)) {
                val stamp = day.toString()
                val (payload, err) = call(
                    client, secrets, "heart-rate",
                    "/wellness-service/wellness/dailyHeartRate",
                    mapOf("date" to stamp),
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
            client, secrets, "profile-settings", "/userprofile-service/userprofile/settings",
        )
        if (settingsErr != null) warnings.add(settingsErr)
        Normalize.applyProfileDemographics(profile, settings)
        val userSex = user.optString("sex", "")
        if (userSex in setOf("male", "female")) profile.put("sex", userSex)
        if (user.has("age") && !user.isNull("age")) profile.put("age", user.opt("age"))
        if (user.has("hr_max") && !user.isNull("hr_max")) profile.put("hr_max", user.opt("hr_max"))

        val (vo2Payload, vo2Err) = call(
            client, secrets, "vo2-max",
            "/metrics-service/metrics/maxmet/daily/${startDate}/${endDate}",
        )
        if (vo2Err != null && !isOptionalMissing(vo2Err)) warnings.add(vo2Err)

        Normalize.applyTraining(profile, null, vo2Payload)

        if (settings is JSONObject) {
            for (key in listOf("displayName", "userDisplayName", "fullName", "userName")) {
                val value = settings.optString(key, "")
                if (value.isNotBlank()) {
                    profile.put("garmin_name", value.trim().take(80))
                    break
                }
            }
        }

        if (activities.length() == 0 && days.isEmpty()) {
            val newest = newestActivityDate(client, secrets)
            if (newest != null) {
                warnings.add(
                    "最近 $windowDays 天接口返回为空；账号上较新的活动日期是 $newest。" +
                        "请确认手表已同步到发放此 Personal API Key 的 Connect 账号；" +
                        "中国区 Key 请选择中国区端点。",
                )
            }
        }

        val warnArr = JSONArray()
        for (w in warnings.take(12)) warnArr.put(w)
        val bundle = JSONObject()
            .put("sample", false)
            .put("fetched_at", Clock.isoSh())
            .put("sync_mode", syncMode)
            .put("sync_start", startDate.toString())
            .put("sync_end", endDate.toString())
            .put("warnings", warnArr)
            .put("profile", profile)
            .put("activities", activities)
            .put("days", Normalize.daysToArray(days))
        return bundle to warnings
    }

    fun syncAccount(
        user: JSONObject,
        existing: JSONObject? = null,
        skipRateLimit: Boolean = false,
    ): JSONObject {
        blocked()?.let { return it }
        val apiKey = user.optString("personal_api_key", "").trim()
        val secrets = mutableListOf(apiKey)
        if (apiKey.isEmpty()) {
            return fail("这个配置没有 Personal API Key。请在页面里重新保存。")
        }

        val userIdForRate = user.optString("id", "").ifEmpty { null }
        val rate = if (skipRateLimit) null else storage.syncStateForUser(userIdForRate)
        if (rate != null && rate.optBoolean("rate_limited", false)) {
            return JSONObject()
                .put("ok", false)
                .put("message", rate.optString("message", "同步过于频繁。"))
                .put("bundle", JSONObject.NULL)
                .put("rate_limited", true)
                .put("retry_after_seconds", rate.optInt("cooldown_seconds", 0))
                .put("cooldown_seconds", rate.optInt("cooldown_seconds", 0))
                .put("last_sync_at", rate.opt("last_sync_at"))
                .put("min_interval_minutes", rate.opt("min_interval_minutes"))
                .put("daily_cap", rate.opt("daily_cap"))
                .put("syncs_today", rate.opt("syncs_today"))
                .put("can_sync", false)
        }

        var existingBundle = existing
        if (existingBundle == null && user.has("id") && !user.isNull("id")) {
            existingBundle = try {
                storage.loadBundle(user.getString("id"))
            } catch (_: Storage.StorageError) {
                null
            }
        }

        val window = SyncPolicy.syncWindow(
            existing = existingBundle,
            backfillDays = SyncPolicy.BACKFILL_DAYS,
            overlapDays = SyncPolicy.DELTA_OVERLAP_DAYS,
        )
        val start = window.start
        val end = window.end
        val mode = window.mode

        val accessToken = try {
            obtainAccessToken(user)
        } catch (exc: Exception) {
            return fail("换取访问令牌失败：" + scrub("${exc.javaClass.simpleName} $exc", secrets))
        }
        secrets.add(accessToken)

        if (!skipRateLimit && user.has("id") && !user.isNull("id")) {
            try {
                storage.recordSyncAttempt(user.getString("id"))
            } catch (_: Storage.StorageError) {
            }
        }

        val (incoming, warnings) = try {
            PersonalApiClient(accessToken, baseUrlFor(user)).use { client ->
                fetchBundle(client, user, secrets, start, end, mode)
            }
        } catch (exc: Exception) {
            return fail("读取数据失败：" + scrub("${exc.javaClass.simpleName} $exc", secrets))
        }

        val bundle = if (existingBundle != null) {
            SyncPolicy.mergeBundles(existingBundle, incoming)
        } else {
            incoming
        }
        val hard = warnings.filter { "失败" in it && !isOptionalMissing(it) }
        val windowLabel = "$start 至 $end"
        val resultMeta = JSONObject()
            .put("sync_mode", mode)
            .put("sync_start", start.toString())
            .put("sync_end", end.toString())

        fun withMeta(result: JSONObject): JSONObject {
            val keys = resultMeta.keys()
            while (keys.hasNext()) {
                val k = keys.next()
                result.put(k, resultMeta.get(k))
            }
            return result
        }

        val hasActivities = bundle.optJSONArray("activities")?.length() ?: 0
        val hasDays = bundle.optJSONArray("days")?.length() ?: 0
        if (hasActivities == 0 && hasDays == 0) {
            if (hard.isNotEmpty()) {
                return withMeta(fail("已认证，但活动、睡眠、心率这些接口读取失败。" + hard[0]))
            }
            val hint = warnings.firstOrNull { "较新的活动日期" in it }
            if (hint != null) {
                return withMeta(ok(bundle, "同步完成，但窗口 $windowLabel 没有数据。$hint"))
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
