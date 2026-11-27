package com.garminanalysis.app.coach

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** Optional DeepSeek coaching. Sends computed metrics only, never secrets. */
object Coach {
    const val DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
    const val DEEPSEEK_MODEL = "deepseek-chat"

    const val SAMPLE_REASON = "示例数据只在本机计算，不会发给 DeepSeek。"
    const val MISSING_REASON = "未配置 DeepSeek API Key，模型建议不可用。本地计算的报告仍然有效。"
    const val FAILED_REASON = "模型建议不可用（请求失败）。本地计算的报告仍然有效。"
    const val EMPTY_REASON = "模型建议不可用（空回复）。本地计算的报告仍然有效。"
    const val PENDING_REASON = "正在根据已计算的指标向 DeepSeek 请求建议…"
    const val CHAT_SAMPLE_REASON = "当前是示例数据，教练对话不会发给 DeepSeek。请先同步佳明数据。"
    const val CHAT_MISSING_REASON = "未配置 DeepSeek API Key。请到「设置」里保存密钥后再问教练。"
    const val CHAT_FAILED_REASON = "教练回复失败。请稍后再试；本地报告仍然可用。"
    const val CHAT_EMPTY_REASON = "教练没有返回内容。请换个问法再试。"

    val SYSTEM_PROMPT =
        "你是耐力运动的训练顾问。用户消息是本地已经算好的指标 JSON，不是原始账号，也不是医疗记录。" +
            "请用简体中文写 4 到 7 条具体建议，每条都引用 JSON 里的数字。" +
            "不要编造 JSON 里没有的测量值，不要索要账号、密码或密钥，不要把建议说成诊断。" +
            "null 表示没有这项数据。负荷单位以 load_method_label 为准，不要把 CTL 说成 TrainingPeaks 的 TSS，" +
            "除非原文明确写了 TSS。"

    val CHAT_SYSTEM_PROMPT =
        "你是耐力运动的本地 AI 教练。用户会基于本机已算好的生理与训练摘要提问。" +
            "请始终用简体中文回答。" +
            "当用户问当天计划或今日练什么时，给出可执行的当天安排，并引用摘要里的 CTL/ATL/TSB/ACWR、" +
            "近期 RHR/HRV/睡眠/Body Battery/压力与训练量数字。" +
            "当用户问一周计划时，给出 7 天结构（含休息日），强度与量要贴合当前疲劳与恢复信号。" +
            "若摘要含「个人描述」，把它当作长期本机画像（含最新摘要与近期时间线）：" +
            "目标、偏好、可用时间、伤病限制、主观感受/RPE；安排计划时优先尊重伤病与可用时间，" +
            "并结合时间线观察身体/感受变化，但仍以生理数字为准，不要编造用户没说的病史。" +
            "不要编造摘要里没有的测量值；null/缺失就明说数据不足。" +
            "不要索要或重复账号、密码、API Key、令牌；不要把建议说成医疗诊断。" +
            "负荷口径以摘要中的「负荷口径」为准；不要把佳明活动训练负荷说成 TrainingPeaks 的 TSS，" +
            "除非摘要明确写了 TSS。" +
            "每条关键建议后附一句简短免责声明：这是训练启发式，不是医疗意见。" +
            "另外：从本轮用户话里抽取可写入长期画像的信息。正文回答结束后，单独另起一行输出：\n" +
            "---PROFILE_UPDATE---\n" +
            "然后一行 JSON 对象，字段：changed(bool)，goals，preferred_sports，availability，" +
            "injuries_limitations，feelings_notes，about_me，timeline_note。" +
            "无变化时输出 {\"changed\":false}；有变化时只填需要更新的字段，未提及的字段用 null，" +
            "timeline_note 用一句中文记录本次主观感受或约束变化。" +
            "JSON 不要包在代码块里；用户看不见这段标记，但必须输出以便本机存档。"

    const val MAX_CHAT_USER_CHARS = 2000
    const val MAX_CHAT_HISTORY_TO_MODEL = 8

    private val SUMMARY_KEYS = listOf(
        "ctl", "atl", "tsb", "acwr", "acute_load", "chronic_load", "history_days",
        "load_method", "load_method_label", "weekly_hours", "previous_weekly_hours",
        "weekly_km", "previous_weekly_km", "weekly_load", "rhr_7d", "rhr_baseline",
        "rhr_delta", "hrv_3d", "hrv_baseline", "hrv_ratio", "hrv_last", "hrv_band_low",
        "hrv_band_high", "sleep_7d_hours", "sleep_last_hours", "body_battery",
        "stress_7d", "steps_7d", "hr_max", "hr_max_source",
    )

    private val GARMIN_KEYS = listOf(
        "vo2max_running", "vo2max_cycling", "training_status_label",
        "weekly_training_load", "load_tunnel_min", "load_tunnel_max",
        "acute_load", "chronic_load",
    )

    class CoachError(message: String) : Exception(message)

    @Volatile
    var networkAllowedOverride: Boolean? = null

    fun networkAllowed(): Boolean {
        networkAllowedOverride?.let { return it }
        return System.getProperty("GARMIN_FITNESS_DISABLE_NETWORK") != "1" &&
            System.getenv("GARMIN_FITNESS_DISABLE_NETWORK") != "1"
    }

    fun buildCoachPayload(report: JSONObject): JSONObject {
        val summary = report.optJSONObject("summary") ?: JSONObject()
        val garmin = summary.optJSONObject("garmin") ?: JSONObject()
        val intensity = summary.optJSONObject("intensity")
        val summaryOut = JSONObject()
        for (key in SUMMARY_KEYS) {
            summaryOut.put(key, summary.opt(key) ?: JSONObject.NULL)
        }
        val garminOut = JSONObject()
        for (key in GARMIN_KEYS) {
            garminOut.put(key, garmin.opt(key) ?: JSONObject.NULL)
        }
        val intensityOut: Any = if (intensity == null) {
            JSONObject.NULL
        } else {
            JSONObject()
                .put("low", intensity.opt("low"))
                .put("mid", intensity.opt("mid"))
                .put("high", intensity.opt("high"))
                .put("minutes", intensity.opt("minutes"))
                .put("method_label", intensity.opt("method_label"))
        }
        val flags = JSONArray()
        val rawFlags = report.optJSONArray("flags") ?: JSONArray()
        for (i in 0 until rawFlags.length()) {
            val item = rawFlags.optJSONObject(i) ?: continue
            flags.put(
                JSONObject()
                    .put("id", item.opt("id"))
                    .put("level", item.opt("level"))
                    .put("title", item.opt("title"))
                    .put("detail", item.opt("detail")),
            )
        }
        fun filterLines(key: String): JSONArray {
            val out = JSONArray()
            val arr = report.optJSONArray(key) ?: JSONArray()
            for (i in 0 until arr.length()) {
                val line = arr.optString(i, "")
                if (line.isNotEmpty() && "@" !in line) out.put(line)
            }
            return out
        }
        return JSONObject()
            .put("window", report.opt("window") ?: JSONObject.NULL)
            .put("summary", summaryOut)
            .put("garmin_device", garminOut)
            .put("intensity", intensityOut)
            .put("flags", flags)
            .put("suggestions", filterLines("suggestions"))
            .put("notes", filterLines("notes"))
    }

    private val httpClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(60, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()

    private fun postChat(apiKey: String, messages: JSONArray, temperature: Double = 0.3): String {
        if (!networkAllowed()) throw CoachError("当前环境禁止访问网络")
        val body = JSONObject()
            .put("model", DEEPSEEK_MODEL)
            .put("temperature", temperature)
            .put("messages", messages)
        val request = Request.Builder()
            .url(DEEPSEEK_URL)
            .header("Authorization", "Bearer $apiKey")
            .header("Content-Type", "application/json")
            .post(body.toString().toRequestBody("application/json; charset=utf-8".toMediaType()))
            .build()
        val response = try {
            httpClient.newCall(request).execute()
        } catch (_: Exception) {
            throw CoachError("请求失败")
        }
        response.use { resp ->
            if (resp.code != 200) throw CoachError("HTTP ${resp.code}")
            val textBody = resp.body?.string().orEmpty()
            return try {
                val data = JSONObject(textBody)
                data.getJSONArray("choices")
                    .getJSONObject(0)
                    .getJSONObject("message")
                    .optString("content", "")
                    .trim()
            } catch (_: Exception) {
                throw CoachError("回复无法解析")
            }
        }
    }

    fun complete(apiKey: String, payload: JSONObject): String {
        val messages = JSONArray()
            .put(JSONObject().put("role", "system").put("content", SYSTEM_PROMPT))
            .put(
                JSONObject().put("role", "user").put(
                    "content",
                    "下面是已经算好的指标 JSON。请只根据这些数字写建议。\n" + payload.toString(),
                ),
            )
        return postChat(apiKey, messages, 0.3)
    }

    fun sanitizeUserMessage(text: String?): String {
        var cleaned = (text ?: "").split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
        if (cleaned.length > MAX_CHAT_USER_CHARS) {
            cleaned = cleaned.take(MAX_CHAT_USER_CHARS).trimEnd()
        }
        return cleaned
    }

    fun chatComplete(
        apiKey: String,
        context: JSONObject,
        history: JSONArray?,
        userMessage: String,
    ): String {
        val message = sanitizeUserMessage(userMessage)
        if (message.isEmpty()) throw CoachError("请先输入问题")
        val messages = JSONArray()
        messages.put(JSONObject().put("role", "system").put("content", CHAT_SYSTEM_PROMPT))
        messages.put(
            JSONObject().put("role", "user").put(
                "content",
                "下面是本机压缩后的训练与生理摘要 JSON（不是原始活动列表）。" +
                    "之后的对话请只依据这些数字与用户问题作答。\n" + context.toString(),
            ),
        )
        messages.put(
            JSONObject().put("role", "assistant").put(
                "content",
                "已收到摘要与个人画像时间线。我会用简体中文回答并引用数字；" +
                    "若用户提到目标/感受/伤病等，会在文末按约定输出 PROFILE_UPDATE JSON。",
            ),
        )
        val trimmed = JSONArray()
        val hist = history ?: JSONArray()
        for (i in 0 until hist.length()) {
            val item = hist.optJSONObject(i) ?: continue
            val role = item.optString("role", "")
            val content = item.optString("content", "")
            if (role !in setOf("user", "assistant")) continue
            if (content.isBlank()) continue
            trimmed.put(
                JSONObject()
                    .put("role", role)
                    .put("content", content.trim().take(MAX_CHAT_USER_CHARS)),
            )
        }
        val start = maxOf(0, trimmed.length() - MAX_CHAT_HISTORY_TO_MODEL)
        for (i in start until trimmed.length()) messages.put(trimmed.get(i))
        messages.put(JSONObject().put("role", "user").put("content", message))
        return postChat(apiKey, messages, 0.4)
    }

    fun offlineCoaching(sample: Boolean, apiKey: String?, pending: Boolean = false): JSONObject {
        return when {
            sample -> JSONObject()
                .put("available", false)
                .put("text", JSONObject.NULL)
                .put("reason", SAMPLE_REASON)
            apiKey.isNullOrEmpty() -> JSONObject()
                .put("available", false)
                .put("text", JSONObject.NULL)
                .put("reason", MISSING_REASON)
            pending -> JSONObject()
                .put("available", false)
                .put("text", JSONObject.NULL)
                .put("reason", PENDING_REASON)
            else -> JSONObject()
                .put("available", false)
                .put("text", JSONObject.NULL)
                .put("reason", "本次响应没有调用模型。已同步的真实数据会在页面里单独请求 DeepSeek。")
        }
    }

    fun coachingFor(sample: Boolean, report: JSONObject, apiKey: String?): JSONObject {
        if (sample) return offlineCoaching(true, null)
        if (apiKey.isNullOrEmpty()) return offlineCoaching(false, null)
        val text = try {
            complete(apiKey, buildCoachPayload(report))
        } catch (e: CoachError) {
            val reason = if (e.message == "请求失败") {
                FAILED_REASON
            } else {
                "模型建议不可用（${e.message}）。本地计算的报告仍然有效。"
            }
            return JSONObject()
                .put("available", false)
                .put("text", JSONObject.NULL)
                .put("reason", reason)
        } catch (_: Exception) {
            return JSONObject()
                .put("available", false)
                .put("text", JSONObject.NULL)
                .put("reason", FAILED_REASON)
        }
        if (text.isEmpty()) {
            return JSONObject()
                .put("available", false)
                .put("text", JSONObject.NULL)
                .put("reason", EMPTY_REASON)
        }
        return JSONObject()
            .put("available", true)
            .put("text", text)
            .put("reason", JSONObject.NULL)
    }
}
