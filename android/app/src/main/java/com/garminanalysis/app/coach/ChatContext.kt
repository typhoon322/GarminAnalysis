package com.garminanalysis.app.coach

import com.garminanalysis.app.Clock
import com.garminanalysis.app.analysis.Analysis
import org.json.JSONArray
import org.json.JSONObject

/**
 * Compressed coaching context for DeepSeek chat.
 * Builds a structured Chinese+JSON summary from computed aggregates only.
 */
object ChatContext {
    const val RECENT_SESSIONS_CAP = 5

    val CONTEXT_DOC =
        "本上下文已压缩：只含负荷/生理聚合、近7天与近28天量、警报、最多 " +
            "$RECENT_SESSIONS_CAP 条最近课次的标题与负荷，以及本机个人画像最新摘要与近期时间线。" +
            "不含原始活动列表、不含逐日全序列、不含账号/密码/API Key。"

    private fun num(value: Any?): Any? {
        if (value == null || value == JSONObject.NULL) return JSONObject.NULL
        if (value is Boolean) return value
        if (value is Number) return value
        return value
    }

    private fun optNum(obj: JSONObject?, key: String): Any? {
        if (obj == null || !obj.has(key) || obj.isNull(key)) return JSONObject.NULL
        return num(obj.opt(key))
    }

    private fun volumeBlock(summary: JSONObject?, hoursKey: String, kmKey: String, loadKey: String): JSONObject {
        return JSONObject()
            .put("小时", optNum(summary, hoursKey))
            .put("公里", optNum(summary, kmKey))
            .put("负荷合计", optNum(summary, loadKey))
    }

    private fun intensityBlock(intensity: JSONObject?): Any {
        if (intensity == null) return JSONObject.NULL
        return JSONObject()
            .put("低占比", optNum(intensity, "low"))
            .put("中占比", optNum(intensity, "mid"))
            .put("高占比", optNum(intensity, "high"))
            .put("计入分钟", optNum(intensity, "minutes"))
            .put("口径", intensity.opt("method_label") ?: JSONObject.NULL)
    }

    private fun recentSessions(bundle: JSONObject, cap: Int = RECENT_SESSIONS_CAP): JSONArray {
        data class Row(val stamp: String, val obj: JSONObject)
        val rows = mutableListOf<Row>()
        val activities = bundle.optJSONArray("activities") ?: JSONArray()
        for (i in 0 until activities.length()) {
            val raw = activities.optJSONObject(i) ?: continue
            val stamp = raw.optString("date", "").take(10)
            if (stamp.length < 10) continue
            var name = raw.optString("name", "未命名").trim().take(40)
            if (name.isEmpty()) name = "未命名"
            val loadValue: Any = try {
                if (!raw.has("training_load") || raw.isNull("training_load")) {
                    JSONObject.NULL
                } else {
                    Math.round(raw.getDouble("training_load") * 10.0) / 10.0
                }
            } catch (_: Exception) {
                JSONObject.NULL
            }
            val sportRaw = raw.optString("sport", "").ifEmpty { raw.optString("type", "") }.trim().take(24)
            val obj = JSONObject()
                .put("日期", stamp)
                .put("名称", name)
                .put("负荷", loadValue)
            if (sportRaw.isNotEmpty()) obj.put("运动", sportRaw)
            rows.add(Row(stamp, obj))
        }
        rows.sortByDescending { it.stamp }
        val out = JSONArray()
        for (row in rows.take(maxOf(0, cap))) out.put(row.obj)
        return out
    }

    fun buildChatContext(
        bundle: JSONObject,
        recentCap: Int = RECENT_SESSIONS_CAP,
        athleteProfile: JSONObject? = null,
    ): JSONObject {
        val report28 = Analysis.analyze(bundle, "30")
        val report7 = Analysis.analyze(bundle, "7")
        val summary28 = report28.optJSONObject("summary") ?: JSONObject()
        val summary7 = report7.optJSONObject("summary") ?: JSONObject()
        val summary = if (summary28.length() > 0) summary28 else summary7
        val intensity7 = intensityBlock(summary7.optJSONObject("intensity"))
        val intensity28 = intensityBlock(summary.optJSONObject("intensity"))
        val flags = JSONArray()
        val rawFlags = report28.optJSONArray("flags") ?: JSONArray()
        for (i in 0 until rawFlags.length()) {
            val item = rawFlags.optJSONObject(i) ?: continue
            flags.put(
                JSONObject()
                    .put("级别", item.opt("level"))
                    .put("标题", item.opt("title"))
                    .put("说明", item.opt("detail"))
                    .put("id", item.opt("id")),
            )
        }
        val window = report28.optJSONObject("window") ?: JSONObject()
        val sample = bundle.optBoolean("sample", false)
        val suggestions = JSONArray()
        val rawSuggestions = report28.optJSONArray("suggestions") ?: JSONArray()
        var count = 0
        for (i in 0 until rawSuggestions.length()) {
            if (count >= 6) break
            val line = rawSuggestions.optString(i, "")
            if (line.isNotEmpty() && "@" !in line) {
                suggestions.put(line)
                count++
            }
        }
        val flagSlice = JSONArray()
        for (i in 0 until minOf(12, flags.length())) flagSlice.put(flags.get(i))

        val vol7 = volumeBlock(summary7, "weekly_hours", "weekly_km", "weekly_load")
        vol7.put("强度", intensity7)
        val vol28 = volumeBlock(summary, "range_hours", "range_km", "range_load")
        vol28.put("强度", intensity28)

        val context = JSONObject()
            .put("摘要说明", CONTEXT_DOC)
            .put("生成时间", Clock.isoSh())
            .put("数据来源", if (sample) "示例数据（非佳明账号）" else "本机佳明同步缓存")
            .put(
                "同步时间",
                if (sample) JSONObject.NULL else (bundle.opt("fetched_at") ?: JSONObject.NULL),
            )
            .put("历史跨度天", optNum(summary, "history_days"))
            .put(
                "窗口",
                JSONObject()
                    .put("近28天起", window.opt("start") ?: JSONObject.NULL)
                    .put("截止", window.opt("end") ?: JSONObject.NULL)
                    .put("今天", Clock.todaySh().toString()),
            )
            .put(
                "负荷状态",
                JSONObject()
                    .put("CTL", optNum(summary, "ctl"))
                    .put("ATL", optNum(summary, "atl"))
                    .put("TSB", optNum(summary, "tsb"))
                    .put("ACWR", optNum(summary, "acwr"))
                    .put("急性负荷7日均", optNum(summary, "acute_load"))
                    .put("慢性负荷28日均", optNum(summary, "chronic_load"))
                    .put("负荷口径", summary.opt("load_method_label") ?: JSONObject.NULL),
            )
            .put(
                "生理对比基线",
                JSONObject()
                    .put("静息心率_近7日", optNum(summary, "rhr_7d"))
                    .put("静息心率_基线", optNum(summary, "rhr_baseline"))
                    .put("静息心率_差值", optNum(summary, "rhr_delta"))
                    .put("静息心率_升高标记", summary.optBoolean("rhr_elevated", false))
                    .put("HRV_近3次", optNum(summary, "hrv_3d"))
                    .put("HRV_基线中位数", optNum(summary, "hrv_baseline"))
                    .put("HRV_比值", optNum(summary, "hrv_ratio"))
                    .put("HRV_最近一夜", optNum(summary, "hrv_last"))
                    .put("HRV_个人带下沿", optNum(summary, "hrv_band_low"))
                    .put("HRV_个人带上沿", optNum(summary, "hrv_band_high"))
                    .put("HRV_受抑标记", summary.optBoolean("hrv_suppressed", false))
                    .put("睡眠_近7日小时", optNum(summary, "sleep_7d_hours"))
                    .put("睡眠_最近一夜小时", optNum(summary, "sleep_last_hours"))
                    .put("睡眠_偏短标记", summary.optBoolean("sleep_short", false))
                    .put("BodyBattery_最近", optNum(summary, "body_battery"))
                    .put("压力_近7日", optNum(summary, "stress_7d"))
                    .put("步数_近7日均", optNum(summary, "steps_7d")),
            )
            .put(
                "训练量",
                JSONObject()
                    .put("近7天", vol7)
                    .put("近28天", vol28)
                    .put(
                        "再往前7天对照",
                        JSONObject()
                            .put("小时", optNum(summary, "previous_weekly_hours"))
                            .put("公里", optNum(summary, "previous_weekly_km")),
                    ),
            )
            .put("关键警报", flagSlice)
            .put("最近几课", recentSessions(bundle, recentCap))
            .put("本地建议摘要", suggestions)

        val personal = AthleteProfile.coachProfileSummary(athleteProfile)
        if (personal != null) context.put("个人描述", personal)
        return context
    }

    fun contextHasForbiddenKeys(payload: JSONObject): List<String> {
        val text = payload.toString().lowercase()
        val hits = mutableListOf<String>()
        for (needle in listOf(
            "password", "personal_api_key", "api_key", "authorization",
            "bearer", "access_token", "deepseek",
        )) {
            if (needle in text) hits.add(needle)
        }
        return hits
    }
}
