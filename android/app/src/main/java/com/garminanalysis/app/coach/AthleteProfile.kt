package com.garminanalysis.app.coach

import com.garminanalysis.app.Clock
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

/**
 * Local athlete profile: AI-maintained from coach chat, optional manual edit.
 * Stored only under cache; capped summary + recent timeline for DeepSeek context.
 */
object AthleteProfile {
    val FIELD_CAPS = mapOf(
        "goals" to 400,
        "preferred_sports" to 200,
        "availability" to 300,
        "injuries_limitations" to 400,
        "feelings_notes" to 600,
        "about_me" to 800,
    )

    val COACH_FIELD_CAPS = mapOf(
        "goals" to 220,
        "preferred_sports" to 120,
        "availability" to 160,
        "injuries_limitations" to 220,
        "feelings_notes" to 280,
        "about_me" to 320,
    )

    const val COACH_PROFILE_CHAR_BUDGET = 700
    const val COACH_TIMELINE_CHAR_BUDGET = 500
    const val TIMELINE_NOTE_CAP = 240
    const val TIMELINE_KEEP = 60
    const val TIMELINE_INJECT = 8

    const val PROFILE_MARKER = "---PROFILE_UPDATE---"

    private val PROFILE_MARKER_RE = Regex(
        """\n*\s*---PROFILE_UPDATE---\s*\n(?<body>\{.*\})\s*\Z""",
        setOf(RegexOption.DOT_MATCHES_ALL),
    )

    private val EMPTY_FIELDS: Map<String, String> = FIELD_CAPS.keys.associateWith { "" }

    private fun cleanText(value: Any?, limit: Int): String {
        var text = (value?.toString() ?: "").replace("\r\n", "\n").replace("\r", "\n")
        val lines = text.split("\n").map { it.trimEnd() }
        var cleaned = lines.joinToString("\n").trim()
        if (cleaned.length > limit) cleaned = cleaned.take(limit).trimEnd()
        return cleaned
    }

    private fun fieldsFrom(raw: JSONObject?): JSONObject {
        val source = raw ?: JSONObject()
        val out = JSONObject()
        for ((key, limit) in FIELD_CAPS) {
            out.put(key, cleanText(source.opt(key), limit))
        }
        return out
    }

    fun emptyStore(): JSONObject {
        val current = JSONObject()
        for ((key, value) in EMPTY_FIELDS) current.put(key, value)
        current.put("updated_at", JSONObject.NULL)
        current.put("source", JSONObject.NULL)
        return JSONObject()
            .put("current", current)
            .put("timeline", JSONArray())
    }

    fun normalizeStore(raw: JSONObject?): JSONObject {
        if (raw == null) return emptyStore()
        if (raw.has("current") || raw.has("timeline")) {
            val currentRaw = raw.optJSONObject("current") ?: JSONObject()
            val fields = fieldsFrom(currentRaw)
            val updatedAt = currentRaw.optString("updated_at", "")
            fields.put("updated_at", if (updatedAt.isNotEmpty()) updatedAt else JSONObject.NULL)
            val source = currentRaw.optString("source", "")
            fields.put("source", if (source in setOf("ai", "manual")) source else JSONObject.NULL)
            val timeline = JSONArray()
            val rawTimeline = raw.optJSONArray("timeline") ?: JSONArray()
            for (i in 0 until rawTimeline.length()) {
                val parsed = normalizeTimelineEntry(rawTimeline.optJSONObject(i))
                if (parsed != null) timeline.put(parsed)
            }
            val start = maxOf(0, timeline.length() - TIMELINE_KEEP)
            val capped = JSONArray()
            for (i in start until timeline.length()) capped.put(timeline.get(i))
            return JSONObject().put("current", fields).put("timeline", capped)
        }
        // Legacy flat file
        val fields = fieldsFrom(raw)
        val updatedAt = raw.optString("updated_at", "")
        fields.put("updated_at", if (updatedAt.isNotEmpty()) updatedAt else JSONObject.NULL)
        val hasContent = FIELD_CAPS.keys.any { fields.optString(it, "").isNotEmpty() }
        fields.put("source", if (hasContent) "manual" else JSONObject.NULL)
        return JSONObject().put("current", fields).put("timeline", JSONArray())
    }

    private fun normalizeTimelineEntry(item: JSONObject?): JSONObject? {
        if (item == null) return null
        val at = item.optString("at", "").trim()
        if (at.isEmpty()) return null
        val note = cleanText(item.opt("note"), TIMELINE_NOTE_CAP)
        val sourceRaw = item.optString("source", "")
        val source = if (sourceRaw in setOf("ai", "manual")) sourceRaw else "ai"
        val snapshotSource = item.optJSONObject("snapshot") ?: item
        return JSONObject()
            .put("at", at)
            .put("source", source)
            .put("note", note)
            .put("snapshot", fieldsFrom(snapshotSource))
    }

    fun normalizeAthleteProfile(raw: JSONObject?): JSONObject {
        val store = normalizeStore(raw)
        return JSONObject(store.getJSONObject("current").toString())
    }

    fun publicAthleteProfile(storeOrProfile: JSONObject?): JSONObject {
        val store = normalizeStore(storeOrProfile)
        val current = store.getJSONObject("current")
        val timeline = store.getJSONArray("timeline")
        val start = maxOf(0, timeline.length() - 12)
        val publicTimeline = JSONArray()
        for (i in start until timeline.length()) {
            val item = timeline.getJSONObject(i)
            publicTimeline.put(
                JSONObject()
                    .put("at", item.opt("at"))
                    .put("source", item.opt("source"))
                    .put("note", item.opt("note")),
            )
        }
        return JSONObject()
            .put("goals", current.optString("goals", ""))
            .put("preferred_sports", current.optString("preferred_sports", ""))
            .put("availability", current.optString("availability", ""))
            .put("injuries_limitations", current.optString("injuries_limitations", ""))
            .put("feelings_notes", current.optString("feelings_notes", ""))
            .put("about_me", current.optString("about_me", ""))
            .put("updated_at", current.opt("updated_at") ?: JSONObject.NULL)
            .put("source", current.opt("source") ?: JSONObject.NULL)
            .put("has_content", FIELD_CAPS.keys.any { current.optString(it, "").isNotEmpty() })
            .put("timeline", publicTimeline)
            .put("maintained_by", "ai_default")
    }

    fun applyAthleteProfileUpdate(existing: JSONObject?, body: JSONObject?): JSONObject {
        val store = normalizeStore(existing)
        val incoming = body ?: JSONObject()
        val current = JSONObject(store.getJSONObject("current").toString())
        for ((key, limit) in FIELD_CAPS) {
            if (incoming.has(key)) {
                current.put(key, cleanText(incoming.opt(key), limit))
            }
        }
        val stamp = Clock.isoSh()
        current.put("updated_at", stamp)
        current.put("source", "manual")
        var note = cleanText(incoming.opt("timeline_note") ?: "手动更新个人描述", TIMELINE_NOTE_CAP)
        if (note.isEmpty()) note = "手动更新个人描述"
        val snapshot = JSONObject()
        for (key in FIELD_CAPS.keys) snapshot.put(key, current.optString(key, ""))
        val entry = JSONObject()
            .put("at", stamp)
            .put("source", "manual")
            .put("note", note)
            .put("snapshot", snapshot)
        val timeline = store.optJSONArray("timeline") ?: JSONArray()
        timeline.put(entry)
        val start = maxOf(0, timeline.length() - TIMELINE_KEEP)
        val capped = JSONArray()
        for (i in start until timeline.length()) capped.put(timeline.get(i))
        return JSONObject().put("current", current).put("timeline", capped)
    }

    fun mergeAiExtraction(existing: JSONObject?, extraction: JSONObject?): JSONObject? {
        if (extraction == null) return null
        if (extraction.has("changed") && !extraction.optBoolean("changed")) return null
        val store = normalizeStore(existing)
        val current = JSONObject(store.getJSONObject("current").toString())
        var changed = false
        for ((key, limit) in FIELD_CAPS) {
            if (!extraction.has(key) || extraction.isNull(key)) continue
            val cleaned = cleanText(extraction.opt(key), limit)
            if (cleaned != current.optString(key, "")) {
                current.put(key, cleaned)
                changed = true
            }
        }
        val note = cleanText(
            extraction.opt("timeline_note") ?: extraction.opt("note"),
            TIMELINE_NOTE_CAP,
        )
        if (!changed && note.isEmpty()) return null
        val stamp = Clock.isoSh()
        current.put("updated_at", stamp)
        current.put("source", "ai")
        val snapshot = JSONObject()
        for (key in FIELD_CAPS.keys) snapshot.put(key, current.optString(key, ""))
        val entry = JSONObject()
            .put("at", stamp)
            .put("source", "ai")
            .put("note", if (note.isNotEmpty()) note else "对话中更新了个人描述")
            .put("snapshot", snapshot)
        val timeline = store.optJSONArray("timeline") ?: JSONArray()
        timeline.put(entry)
        val start = maxOf(0, timeline.length() - TIMELINE_KEEP)
        val capped = JSONArray()
        for (i in start until timeline.length()) capped.put(timeline.get(i))
        return JSONObject().put("current", current).put("timeline", capped)
    }

    data class SplitReply(val reply: String, val extraction: JSONObject?)

    fun splitCoachReply(rawText: String?): SplitReply {
        val text = (rawText ?: "").trim()
        if (text.isEmpty()) return SplitReply("", null)
        val match = PROFILE_MARKER_RE.find(text)
        if (match != null) {
            val reply = text.substring(0, match.range.first).trim()
            return try {
                val payload = JSONObject(match.groups["body"]!!.value)
                SplitReply(reply, payload)
            } catch (_: JSONException) {
                SplitReply(text, null)
            }
        }
        if (PROFILE_MARKER in text) {
            val idx = text.lastIndexOf(PROFILE_MARKER)
            val head = text.substring(0, idx).trim()
            val tail = text.substring(idx + PROFILE_MARKER.length).trim()
            return try {
                val payload = JSONObject(tail)
                SplitReply(head, payload)
            } catch (_: JSONException) {
                SplitReply(text, null)
            }
        }
        return SplitReply(text, null)
    }

    fun coachProfileSummary(storeOrProfile: JSONObject?): JSONObject? {
        val store = normalizeStore(storeOrProfile)
        val current = store.getJSONObject("current")
        val timeline = store.getJSONArray("timeline")
        val hasFields = FIELD_CAPS.keys.any { current.optString(it, "").isNotEmpty() }
        if (!hasFields && timeline.length() == 0) return null

        val pieces = listOf(
            Triple("训练目标", "goals", COACH_FIELD_CAPS["goals"]!!),
            Triple("偏好运动", "preferred_sports", COACH_FIELD_CAPS["preferred_sports"]!!),
            Triple("可训练时间", "availability", COACH_FIELD_CAPS["availability"]!!),
            Triple("伤病与限制", "injuries_limitations", COACH_FIELD_CAPS["injuries_limitations"]!!),
            Triple("主观感受与RPE", "feelings_notes", COACH_FIELD_CAPS["feelings_notes"]!!),
            Triple("对我的描述", "about_me", COACH_FIELD_CAPS["about_me"]!!),
        )
        val block = JSONObject()
        var used = 0
        for ((label, key, limit) in pieces) {
            var value = cleanText(current.opt(key), limit)
            if (value.isEmpty()) continue
            val remaining = COACH_PROFILE_CHAR_BUDGET - used
            if (remaining <= 0) break
            if (value.length > remaining) value = value.take(remaining).trimEnd()
            if (value.isEmpty()) break
            block.put(label, value)
            used += value.length
        }

        val timelineOut = JSONArray()
        var tUsed = 0
        val injectStart = maxOf(0, timeline.length() - TIMELINE_INJECT)
        val recent = mutableListOf<JSONObject>()
        for (i in injectStart until timeline.length()) recent.add(timeline.getJSONObject(i))
        for (item in recent.asReversed()) {
            val note = item.optString("note", "")
            var line = "${item.optString("at", "")} · ${item.optString("source", "")} · $note"
            line = cleanText(line, 160)
            if (line.isEmpty()) continue
            if (tUsed + line.length > COACH_TIMELINE_CHAR_BUDGET) break
            timelineOut.put(
                JSONObject()
                    .put("时间", item.opt("at"))
                    .put("来源", item.opt("source"))
                    .put("笔记", item.opt("note")),
            )
            tUsed += line.length
        }
        // reverse back to chronological
        val chronological = JSONArray()
        for (i in timelineOut.length() - 1 downTo 0) chronological.put(timelineOut.get(i))

        if (block.length() == 0 && chronological.length() == 0) return null
        val source = current.optString("source", "")
        return JSONObject()
            .put(
                "说明",
                "长期本机运动员画像：默认由教练对话自动抽取更新；用户也可手动改。" +
                    "含最新摘要与近期时间线条目，便于观察身体/感受变化。不是医疗记录，不含密钥。",
            )
            .put("维护方式", if (source.isNotEmpty()) source else "未知")
            .put("更新时间", current.opt("updated_at") ?: JSONObject.NULL)
            .put("最新摘要", block)
            .put("近期时间线", chronological)
            .put("字符占用", used + tUsed)
    }
}
