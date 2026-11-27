package com.garminanalysis.app.data

import android.content.Context
import com.garminanalysis.app.Clock
import com.garminanalysis.app.coach.AthleteProfile
import com.garminanalysis.app.sync.SyncPolicy
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.UUID
import kotlin.math.round

/**
 * Local config, Personal API keys, and cached health data under [Context.getFilesDir].
 * Layout: data/secrets.json, tokens/, cache/.
 */
class Storage(context: Context) {
    class StorageError(message: String) : Exception(message)

    private val root: File = File(context.applicationContext.filesDir, "data").also { it.mkdirs() }

    companion object {
        const val CHAT_HISTORY_CAP = 40
        private val USER_ID = Regex("^[a-f0-9]{32}$")
    }

    private fun secretsPath(): File = File(root, "secrets.json")

    private fun ensurePrivateDir(path: File) {
        path.mkdirs()
    }

    private fun writePrivateJson(path: File, payload: Any) {
        ensurePrivateDir(path.parentFile!!)
        val text = when (payload) {
            is JSONObject -> payload.toString(2)
            is JSONArray -> payload.toString(2)
            else -> payload.toString()
        } + "\n"
        val tmp = File(path.parentFile, path.name + ".tmp")
        try {
            tmp.writeText(text, Charsets.UTF_8)
            if (path.exists()) path.delete()
            if (!tmp.renameTo(path)) {
                tmp.copyTo(path, overwrite = true)
                tmp.delete()
            }
        } catch (e: Exception) {
            tmp.delete()
            throw e
        }
    }

    private fun readJsonObject(path: File): JSONObject {
        return JSONObject(path.readText(Charsets.UTF_8))
    }

    private fun emptySecrets(): JSONObject =
        JSONObject()
            .put("deepseek_api_key", "")
            .put("active_user_id", JSONObject.NULL)
            .put("users", JSONObject())
            .put("sync_min_interval_minutes", SyncPolicy.DEFAULT_MIN_INTERVAL_MINUTES)
            .put("sync_daily_cap", SyncPolicy.DEFAULT_DAILY_CAP)

    fun loadSecrets(): JSONObject {
        val path = secretsPath()
        if (!path.exists()) return emptySecrets()
        val data = try {
            readJsonObject(path)
        } catch (e: JSONException) {
            throw StorageError("data/secrets.json 无法解析，已停止写入以免覆盖。")
        } catch (e: Exception) {
            throw StorageError("无法读取 data/secrets.json。")
        }
        if (!data.has("deepseek_api_key")) data.put("deepseek_api_key", "")
        if (!data.has("active_user_id")) data.put("active_user_id", JSONObject.NULL)
        data.put(
            "sync_min_interval_minutes",
            SyncPolicy.clampMinIntervalMinutes(
                data.opt("sync_min_interval_minutes") ?: SyncPolicy.DEFAULT_MIN_INTERVAL_MINUTES,
            ),
        )
        data.put(
            "sync_daily_cap",
            SyncPolicy.clampDailyCap(data.opt("sync_daily_cap") ?: SyncPolicy.DEFAULT_DAILY_CAP),
        )
        if (data.optJSONObject("users") == null) data.put("users", JSONObject())
        return data
    }

    fun saveSecrets(data: JSONObject) {
        writePrivateJson(secretsPath(), data)
    }

    fun assertUserId(userId: String): String {
        if (!USER_ID.matches(userId)) throw StorageError("账号编号不正确。")
        return userId
    }

    fun publicUser(user: JSONObject): JSONObject {
        val key = user.optString("personal_api_key", "").trim()
        var region = user.optString("region", "global").trim().lowercase()
        if (region !in setOf("global", "cn")) region = "global"
        return JSONObject()
            .put("id", user.opt("id"))
            .put("display_name", user.opt("display_name"))
            .put("label", user.optString("label", ""))
            .put("has_personal_api_key", key.isNotEmpty())
            .put("region", region)
            .put("sex", user.optString("sex", "unknown").ifEmpty { "unknown" })
            .put("age", user.opt("age") ?: JSONObject.NULL)
            .put("hr_max", user.opt("hr_max") ?: JSONObject.NULL)
    }

    fun listUsers(): JSONArray {
        val data = loadSecrets()
        val usersObj = data.getJSONObject("users")
        val list = mutableListOf<JSONObject>()
        val keys = usersObj.keys()
        while (keys.hasNext()) {
            val user = usersObj.optJSONObject(keys.next()) ?: continue
            list.add(user)
        }
        list.sortBy { it.optString("created_at", "") }
        return JSONArray().also { arr -> list.forEach { arr.put(publicUser(it)) } }
    }

    fun activeUser(): JSONObject? {
        val data = loadSecrets()
        if (data.isNull("active_user_id")) return null
        val userId = data.optString("active_user_id", "")
        if (userId.isEmpty()) return null
        return data.getJSONObject("users").optJSONObject(userId)
    }

    fun setActiveUser(userId: String): JSONObject {
        val id = assertUserId(userId)
        val data = loadSecrets()
        val users = data.getJSONObject("users")
        if (!users.has(id)) throw StorageError("没有这个账号。")
        data.put("active_user_id", id)
        saveSecrets(data)
        return publicUser(users.getJSONObject(id))
    }

    private fun cleanName(name: String): String =
        name.split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ").take(40)

    private fun optionalNumber(
        value: Any?,
        name: String,
        low: Double,
        high: Double,
        asInt: Boolean,
    ): Number? {
        if (value == null || value == JSONObject.NULL || (value is String && value.isEmpty())) {
            return null
        }
        val number = when (value) {
            is Number -> value.toDouble()
            is String -> value.toDoubleOrNull()
                ?: throw StorageError("$name 不是数字。")
            else -> throw StorageError("$name 不是数字。")
        }
        if (number.isNaN() || number < low || number > high) {
            throw StorageError("$name 超出合理范围。")
        }
        return if (asInt) round(number).toInt() else (round(number * 10) / 10.0)
    }

    fun addUser(
        displayName: String,
        personalApiKey: String,
        label: String = "",
        region: String = "global",
        sex: String = "unknown",
        age: Any? = null,
        hrMax: Any? = null,
    ): JSONObject {
        val name = cleanName(displayName)
        if (name.isEmpty()) throw StorageError("请填写配置名称。")
        val key = personalApiKey.trim()
        if (key.isEmpty()) throw StorageError("请填写 Garmin Personal API Key。密钥只保存在本机。")
        if (key.length > 500) throw StorageError("Personal API Key 太长。")
        val labelText = label.split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ").take(60)
        var regionValue = region.trim().lowercase()
        if (regionValue in setOf("china", "garmin.cn")) regionValue = "cn"
        if (regionValue !in setOf("global", "cn")) throw StorageError("地区请选国际区或中国区。")
        if (sex !in setOf("male", "female", "unknown")) throw StorageError("性别请选男、女，或留空。")
        val ageValue = optionalNumber(age, "年龄", 10.0, 100.0, true)
        val hrValue = optionalNumber(hrMax, "最大心率", 100.0, 230.0, true)
        val data = loadSecrets()
        val users = data.getJSONObject("users")
        var existing: JSONObject? = null
        val iter = users.keys()
        while (iter.hasNext()) {
            val user = users.optJSONObject(iter.next()) ?: continue
            if (user.optString("display_name", "").equals(name, ignoreCase = true)) {
                existing = user
                break
            }
        }
        val now = Clock.isoSh()
        val record: JSONObject
        if (existing == null) {
            val userId = UUID.randomUUID().toString().replace("-", "")
            record = JSONObject()
                .put("id", userId)
                .put("display_name", name)
                .put("label", labelText)
                .put("personal_api_key", key)
                .put("region", regionValue)
                .put("sex", sex)
                .put("age", ageValue ?: JSONObject.NULL)
                .put("hr_max", hrValue ?: JSONObject.NULL)
                .put("created_at", now)
                .put("updated_at", now)
            users.put(userId, record)
        } else {
            existing.put("display_name", name)
            existing.put("label", labelText)
            existing.put("personal_api_key", key)
            existing.put("region", regionValue)
            existing.put("sex", sex)
            existing.put("age", ageValue ?: JSONObject.NULL)
            existing.put("hr_max", hrValue ?: JSONObject.NULL)
            existing.put("updated_at", now)
            for (legacy in listOf("email", "password", "is_cn")) {
                existing.remove(legacy)
            }
            record = existing
            clearBearer(record.getString("id"))
        }
        data.put("active_user_id", record.getString("id"))
        saveSecrets(data)
        return publicUser(record)
    }

    fun deleteUser(userId: String) {
        val id = assertUserId(userId)
        val data = loadSecrets()
        val users = data.getJSONObject("users")
        if (!users.has(id)) throw StorageError("没有这个账号。")
        users.remove(id)
        if (data.optString("active_user_id", "") == id) {
            val remaining = users.keys().asSequence().toList().sorted()
            if (remaining.isEmpty()) {
                data.put("active_user_id", JSONObject.NULL)
            } else {
                data.put("active_user_id", remaining.first())
            }
        }
        saveSecrets(data)
        File(root, "cache/$id").deleteRecursively()
        File(root, "tokens/$id").deleteRecursively()
        File(root, "mfa/$id.json").delete()
    }

    fun getApiKey(): String {
        val path = secretsPath()
        if (path.exists()) {
            return loadSecrets().optString("deepseek_api_key", "").trim()
        }
        return System.getenv("DEEPSEEK_API_KEY")?.trim().orEmpty()
    }

    fun deepseekSource(): String {
        val path = secretsPath()
        if (path.exists()) {
            return if (loadSecrets().optString("deepseek_api_key", "").trim().isNotEmpty()) "file" else "missing"
        }
        return if (!System.getenv("DEEPSEEK_API_KEY").isNullOrBlank()) "env" else "missing"
    }

    fun setApiKey(apiKey: String?): String {
        val text = (apiKey ?: "").trim()
        if (text.length > 300) throw StorageError("密钥太长。")
        val data = loadSecrets()
        data.put("deepseek_api_key", text)
        saveSecrets(data)
        return if (text.isNotEmpty()) "file" else "missing"
    }

    private fun tokenDir(userId: String): File {
        val path = File(root, "tokens/${assertUserId(userId)}")
        ensurePrivateDir(path)
        return path
    }

    private fun bearerPath(userId: String): File = File(tokenDir(userId), "bearer.json")

    fun apiKeyFingerprint(apiKey: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val hash = digest.digest(apiKey.toByteArray(Charsets.UTF_8))
        return hash.joinToString("") { "%02x".format(it) }
    }

    fun loadBearer(userId: String, apiKey: String): JSONObject? {
        val path = bearerPath(userId)
        if (!path.isFile) return null
        val data = try {
            readJsonObject(path)
        } catch (_: Exception) {
            return null
        }
        if (data.optString("fingerprint", "") != apiKeyFingerprint(apiKey)) return null
        val token = data.optString("access_token", "").trim()
        val expiresAt = try {
            data.getDouble("expires_at")
        } catch (_: Exception) {
            return null
        }
        if (token.isEmpty()) return null
        return JSONObject()
            .put("access_token", token)
            .put("expires_at", expiresAt)
    }

    fun saveBearer(userId: String, apiKey: String, accessToken: String, expiresAt: Double) {
        writePrivateJson(
            bearerPath(userId),
            JSONObject()
                .put("fingerprint", apiKeyFingerprint(apiKey))
                .put("access_token", accessToken)
                .put("expires_at", expiresAt),
        )
    }

    fun clearBearer(userId: String) {
        bearerPath(userId).delete()
    }

    private fun bundlePath(userId: String): File =
        File(root, "cache/${assertUserId(userId)}/bundle.json")

    fun loadBundle(userId: String): JSONObject? {
        val path = bundlePath(userId)
        if (!path.isFile) return null
        try {
            return readJsonObject(path)
        } catch (e: Exception) {
            throw StorageError("已同步的数据文件无法读取。")
        }
    }

    fun saveBundle(userId: String, bundle: JSONObject) {
        writePrivateJson(bundlePath(userId), bundle)
    }

    private fun athleteProfilePath(userId: String): File =
        File(root, "cache/${assertUserId(userId)}/athlete_profile.json")

    fun loadAthleteProfile(userId: String): JSONObject {
        val path = athleteProfilePath(userId)
        if (!path.isFile) return AthleteProfile.emptyStore()
        val data = try {
            readJsonObject(path)
        } catch (e: Exception) {
            throw StorageError("个人描述文件无法读取。")
        }
        return AthleteProfile.normalizeStore(data)
    }

    fun saveAthleteProfile(userId: String, profile: JSONObject): JSONObject {
        assertUserId(userId)
        val cleaned = AthleteProfile.normalizeStore(profile)
        writePrivateJson(athleteProfilePath(userId), cleaned)
        return AthleteProfile.publicAthleteProfile(cleaned)
    }

    fun getSyncPolicy(): JSONObject {
        val data = loadSecrets()
        return JSONObject()
            .put(
                "min_interval_minutes",
                SyncPolicy.clampMinIntervalMinutes(
                    data.opt("sync_min_interval_minutes") ?: SyncPolicy.DEFAULT_MIN_INTERVAL_MINUTES,
                ),
            )
            .put(
                "daily_cap",
                SyncPolicy.clampDailyCap(data.opt("sync_daily_cap") ?: SyncPolicy.DEFAULT_DAILY_CAP),
            )
    }

    fun setSyncPolicy(minIntervalMinutes: Any? = null, dailyCap: Any? = null): JSONObject {
        val data = loadSecrets()
        if (minIntervalMinutes != null) {
            data.put("sync_min_interval_minutes", SyncPolicy.clampMinIntervalMinutes(minIntervalMinutes))
        }
        if (dailyCap != null) {
            data.put("sync_daily_cap", SyncPolicy.clampDailyCap(dailyCap))
        }
        saveSecrets(data)
        return getSyncPolicy()
    }

    fun syncStateForUser(userId: String?): JSONObject {
        val policy = getSyncPolicy()
        var lastSyncAt: String? = null
        var syncDay: String? = null
        var syncCountToday = 0
        if (!userId.isNullOrEmpty()) {
            val id = assertUserId(userId)
            val data = loadSecrets()
            val user = data.getJSONObject("users").optJSONObject(id)
            if (user != null) {
                lastSyncAt = user.optString("last_sync_at", "").ifEmpty { null }
                syncDay = user.optString("sync_day", "").ifEmpty { null }
                syncCountToday = user.optInt("sync_count_today", 0)
            }
            val bundle = try {
                loadBundle(id)
            } catch (_: StorageError) {
                null
            }
            if (lastSyncAt == null && bundle != null) {
                lastSyncAt = bundle.optString("fetched_at", "").ifEmpty { null }
            }
        }
        return SyncPolicy.evaluateRateLimit(
            lastSyncAt = lastSyncAt,
            syncDay = syncDay,
            syncCountToday = syncCountToday,
            minIntervalMinutes = policy.getInt("min_interval_minutes"),
            dailyCap = policy.getInt("daily_cap"),
        )
    }

    fun recordSyncAttempt(userId: String, at: String? = null): JSONObject {
        val id = assertUserId(userId)
        val data = loadSecrets()
        val user = data.getJSONObject("users").optJSONObject(id)
            ?: throw StorageError("没有这个账号。")
        val stamp = at ?: Clock.isoSh()
        val (day, count) = SyncPolicy.nextSyncCounters(
            syncDay = user.optString("sync_day", "").ifEmpty { null },
            syncCountToday = user.optInt("sync_count_today", 0),
        )
        user.put("last_sync_at", stamp)
        user.put("sync_day", day)
        user.put("sync_count_today", count)
        user.put("updated_at", stamp)
        saveSecrets(data)
        return syncStateForUser(id)
    }

    private fun chatPath(userId: String): File =
        File(root, "cache/${assertUserId(userId)}/chat.json")

    fun loadChat(userId: String): JSONObject {
        val path = chatPath(userId)
        if (!path.isFile) {
            return JSONObject().put("messages", JSONArray()).put("updated_at", JSONObject.NULL)
        }
        val data = try {
            readJsonObject(path)
        } catch (e: Exception) {
            throw StorageError("教练对话记录无法读取。")
        }
        val messages = data.optJSONArray("messages") ?: JSONArray()
        val cleaned = JSONArray()
        for (i in 0 until messages.length()) {
            val item = messages.optJSONObject(i) ?: continue
            val role = item.optString("role", "")
            if (role !in setOf("user", "assistant")) continue
            if (!item.has("content") || item.isNull("content")) continue
            val content = item.optString("content", "")
            val row = JSONObject().put("role", role).put("content", content)
            val at = item.optString("at", "")
            row.put("at", if (at.isNotEmpty()) at else JSONObject.NULL)
            cleaned.put(row)
        }
        val start = maxOf(0, cleaned.length() - CHAT_HISTORY_CAP)
        val capped = JSONArray()
        for (i in start until cleaned.length()) capped.put(cleaned.get(i))
        return JSONObject()
            .put("messages", capped)
            .put("updated_at", data.opt("updated_at") ?: JSONObject.NULL)
    }

    fun saveChat(userId: String, messages: JSONArray): JSONObject {
        val stamp = Clock.isoSh()
        val start = maxOf(0, messages.length() - CHAT_HISTORY_CAP)
        val capped = JSONArray()
        for (i in start until messages.length()) capped.put(messages.get(i))
        val payload = JSONObject().put("updated_at", stamp).put("messages", capped)
        writePrivateJson(chatPath(userId), payload)
        return payload
    }

    fun clearChat(userId: String) {
        val path = chatPath(userId)
        if (path.exists() && !path.delete()) {
            throw StorageError("无法清除教练对话。")
        }
    }

    fun appendChatTurn(userId: String, userText: String, assistantText: String): JSONObject {
        val chat = loadChat(userId)
        val stamp = Clock.isoSh()
        val messages = chat.optJSONArray("messages") ?: JSONArray()
        messages.put(JSONObject().put("role", "user").put("content", userText).put("at", stamp))
        messages.put(JSONObject().put("role", "assistant").put("content", assistantText).put("at", stamp))
        return saveChat(userId, messages)
    }

    fun getCoachPolicy(): JSONObject {
        val data = loadSecrets()
        return JSONObject()
            .put(
                "min_interval_seconds",
                SyncPolicy.clampCoachIntervalSeconds(
                    data.opt("coach_min_interval_seconds")
                        ?: SyncPolicy.DEFAULT_COACH_MIN_INTERVAL_SECONDS,
                ),
            )
            .put(
                "daily_cap",
                SyncPolicy.clampCoachDailyCap(
                    data.opt("coach_daily_cap") ?: SyncPolicy.DEFAULT_COACH_DAILY_CAP,
                ),
            )
    }

    fun coachStateForUser(userId: String?): JSONObject {
        val policy = getCoachPolicy()
        var lastCoachAt: String? = null
        var coachDay: String? = null
        var coachCountToday = 0
        if (!userId.isNullOrEmpty()) {
            val id = assertUserId(userId)
            val data = loadSecrets()
            val user = data.getJSONObject("users").optJSONObject(id)
            if (user != null) {
                lastCoachAt = user.optString("last_coach_at", "").ifEmpty { null }
                coachDay = user.optString("coach_day", "").ifEmpty { null }
                coachCountToday = user.optInt("coach_count_today", 0)
            }
        }
        return SyncPolicy.evaluateCoachRateLimit(
            lastCoachAt = lastCoachAt,
            coachDay = coachDay,
            coachCountToday = coachCountToday,
            minIntervalSeconds = policy.getInt("min_interval_seconds"),
            dailyCap = policy.getInt("daily_cap"),
        )
    }

    fun recordCoachAttempt(userId: String, at: String? = null): JSONObject {
        val id = assertUserId(userId)
        val data = loadSecrets()
        val user = data.getJSONObject("users").optJSONObject(id)
            ?: throw StorageError("没有这个账号。")
        val stamp = at ?: Clock.isoSh()
        val (day, count) = SyncPolicy.nextSyncCounters(
            syncDay = user.optString("coach_day", "").ifEmpty { null },
            syncCountToday = user.optInt("coach_count_today", 0),
        )
        user.put("last_coach_at", stamp)
        user.put("coach_day", day)
        user.put("coach_count_today", count)
        user.put("updated_at", stamp)
        saveSecrets(data)
        return coachStateForUser(id)
    }

    /** Full user record including personal_api_key (for sync). */
    fun getUser(userId: String): JSONObject? {
        val id = assertUserId(userId)
        return loadSecrets().getJSONObject("users").optJSONObject(id)
    }
}
