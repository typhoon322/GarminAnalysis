package com.garminanalysis.app.data

import com.garminanalysis.app.Clock
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate

/** Built-in demo series before Garmin sync. Port of sample_data.py */
object SampleData {
    const val SAMPLE_DAYS = 70
    private val BASE_LOAD = listOf(70.0, 45.0, 0.0, 80.0, 25.0, 110.0, 0.0)
    private val SPIKE_LOAD = listOf(150.0, 140.0, 160.0, 150.0, 170.0, 180.0, 100.0)
    private val SPIKE_STRESS = listOf(55.0, 58.0, 52.0, 60.0, 49.0, 57.0, 54.0)

    fun plannedLoad(index: Int, total: Int = SAMPLE_DAYS): Double {
        if (index >= total - 7) return SPIKE_LOAD[index - (total - 7)]
        return BASE_LOAD[index % 7]
    }

    private fun activity(
        day: LocalDate, suffix: String, name: String, sport: String,
        minutes: Int, avgHr: Int, maxHr: Int, load: Double, effect: Double,
    ): JSONObject {
        val speed = if (sport == "cycling") 500 else 160
        return JSONObject()
            .put("id", "sample-${day}-$suffix")
            .put("name", name)
            .put("type", sport)
            .put("date", day.toString())
            .put("duration_s", minutes * 60)
            .put("distance_m", speed * minutes)
            .put("avg_hr", avgHr)
            .put("max_hr", maxHr)
            .put("training_load", load)
            .put("aerobic_te", effect)
    }

    private fun sessions(index: Int, total: Int, day: LocalDate, load: Double): JSONArray {
        val out = JSONArray()
        if (load <= 0) return out
        val kind = index % 7
        val spike = index >= total - 7
        if (kind == 5 || (spike && kind in setOf(0, 3))) {
            val hard = minOf(40.0, load * 0.3).let { Math.round(it * 100.0) / 100.0 }
            val easy = Math.round((load - hard) * 100.0) / 100.0
            if (easy > 0) out.put(activity(day, "easy", "轻松跑", "running", 50, 132, 150, easy, 2.6))
            if (hard > 0) out.put(activity(day, "hard", "间歇", "running", 20, 176, 184, hard, 4.2))
            return out
        }
        if (kind == 3) {
            out.put(activity(day, "tempo", "节奏跑", "running", 30, 162, 172, load, 3.4))
            return out
        }
        if (kind == 1) {
            val minutes = if (spike) 70 else 45
            val avg = if (spike) 138 else 125
            out.put(activity(day, "bike", "轻松骑行", "cycling", minutes, avg, 160, load, 2.4))
            return out
        }
        out.put(activity(day, "easy", "轻松跑", "running", 48, 132, 148, load, 2.5))
        return out
    }

    private fun wellness(index: Int, total: Int, day: LocalDate): JSONObject {
        val resting = if (index >= total - 7) 57 else 50 + (index % 3) - 1
        val stress = if (index >= total - 7) SPIKE_STRESS[index - (total - 7)] else 28.0
        val hrv = if (index >= total - 3) 44 else 56
        val sleepHours = if (index >= total - 4) 5.4 else 7.6
        val score = if (index >= total - 4) 61 else 82
        val battery = when {
            index == total - 1 -> 22
            index >= total - 3 -> 30
            else -> 68
        }
        return JSONObject()
            .put("date", day.toString())
            .put("resting_hr", resting)
            .put("hrv", hrv)
            .put("hrv_baseline_low", 48)
            .put("hrv_baseline_high", 66)
            .put("sleep_seconds", (sleepHours * 3600).toInt())
            .put("sleep_score", score)
            .put("body_battery", battery)
            .put("stress", stress)
            .put("steps", 9000 + (index % 5) * 400)
    }

    fun buildSampleBundle(end: LocalDate? = null): JSONObject {
        val endDay = end ?: Clock.todaySh()
        val total = SAMPLE_DAYS
        val start = endDay.minusDays((total - 1).toLong())
        val activities = JSONArray()
        val days = JSONArray()
        for (index in 0 until total) {
            val day = start.plusDays(index.toLong())
            val load = plannedLoad(index, total)
            val sessions = sessions(index, total, day, load)
            for (i in 0 until sessions.length()) activities.put(sessions.getJSONObject(i))
            days.put(wellness(index, total, day))
        }
        val weekly = (total - 7 until total).sumOf { plannedLoad(it, total) }
        return JSONObject()
            .put("sample", true)
            .put("fetched_at", JSONObject.NULL)
            .put("warnings", JSONArray())
            .put(
                "profile",
                JSONObject()
                    .put("label", "示例跑步者")
                    .put("sex", "male")
                    .put("age", 36)
                    .put("hr_max", 188)
                    .put("hr_rest", 50)
                    .put("vo2max_running", 48)
                    .put("vo2max_cycling", 44)
                    .put("training_status", "PRODUCTIVE")
                    .put("weekly_training_load", weekly)
                    .put("load_tunnel_min", 300)
                    .put("load_tunnel_max", 600),
            )
            .put("activities", activities)
            .put("days", days)
    }
}
