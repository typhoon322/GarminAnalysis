package com.garminanalysis.app.ui.data

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.garminanalysis.app.AppViewModel
import com.garminanalysis.app.analysis.Analysis
import com.garminanalysis.app.ui.components.LineChart
import com.garminanalysis.app.ui.theme.Alert
import com.garminanalysis.app.ui.theme.Forest
import com.garminanalysis.app.ui.theme.Info
import com.garminanalysis.app.ui.theme.Teal
import com.garminanalysis.app.ui.theme.Warning
import org.json.JSONArray
import org.json.JSONObject

@Composable
fun DataScreen(vm: AppViewModel, modifier: Modifier = Modifier) {
    val report = vm.report
    val summary = report.optJSONObject("summary") ?: JSONObject()
    val series = report.optJSONObject("series") ?: JSONObject()
    val flags = report.optJSONArray("flags") ?: JSONArray()
    val suggestions = report.optJSONArray("suggestions") ?: JSONArray()
    val ranges = report.optJSONArray("available_ranges").takeUnless { it == null || it.length() == 0 }
        ?: JSONArray().apply {
            listOf("today" to "今天", "7" to "近 7 天", "30" to "近 30 天", "90" to "近 90 天")
                .forEach { (key, label) -> put(JSONObject().put("key", key).put("label", label)) }
        }
    val sample = report.optBoolean("sample")

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            "佳明体能健康分析",
            style = MaterialTheme.typography.headlineMedium,
            color = Forest,
        )
        Text(
            if (sample) "当前为示例数据（尚未同步佳明）" else "本机佳明同步缓存",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Row(
            Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            for (i in 0 until ranges.length()) {
                val item = ranges.optJSONObject(i) ?: continue
                val key = item.optString("key")
                FilterChip(
                    selected = vm.rangeKey == key,
                    onClick = { vm.setRange(key) },
                    label = { Text(item.optString("label")) },
                )
            }
        }
        Button(
            onClick = { vm.sync() },
            enabled = !vm.syncBusy,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(if (vm.syncBusy) "同步中…" else "从佳明同步")
        }
        if (vm.syncState.optBoolean("rate_limited")) {
            Text(vm.syncState.optString("message"), color = Warning)
        }

        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Metric("CTL", Analysis.fmtNum(summary.opt("ctl")))
            Metric("ATL", Analysis.fmtNum(summary.opt("atl")))
            Metric("TSB", Analysis.fmtNum(summary.opt("tsb")))
            Metric("ACWR", Analysis.fmtAcwr(summary.opt("acwr")))
        }

        if (flags.length() > 0) {
            Text("警报", style = MaterialTheme.typography.titleLarge, color = Forest)
            for (i in 0 until flags.length()) {
                val f = flags.optJSONObject(i) ?: continue
                val color = when (f.optString("level")) {
                    "alert" -> Alert
                    "warning" -> Warning
                    else -> Info
                }
                Text(f.optString("title"), color = color, style = MaterialTheme.typography.titleMedium)
                Text(f.optString("detail"), style = MaterialTheme.typography.bodyMedium)
            }
        }

        if (suggestions.length() > 0) {
            Text("建议", style = MaterialTheme.typography.titleLarge, color = Forest)
            for (i in 0 until suggestions.length()) {
                Text("· ${suggestions.optString(i)}")
            }
        }

        LineChart(
            title = "负荷 / CTL / ATL / TSB",
            series = listOf(
                "load" to (series.optJSONArray("load") ?: JSONArray()),
                "ctl" to (series.optJSONArray("ctl") ?: JSONArray()),
                "atl" to (series.optJSONArray("atl") ?: JSONArray()),
                "tsb" to (series.optJSONArray("tsb") ?: JSONArray()),
            ),
            colors = listOf(Color(0xFF607D8B), Forest, Teal, Warning),
        )
        Spacer(Modifier.height(8.dp))
        LineChart(
            title = "静息心率 / HRV / 睡眠",
            series = listOf(
                "rhr" to (series.optJSONArray("rhr") ?: JSONArray()),
                "hrv" to (series.optJSONArray("hrv") ?: JSONArray()),
                "sleep" to (series.optJSONArray("sleep_hours") ?: JSONArray()),
            ),
            colors = listOf(Alert, Teal, Forest),
        )
        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun Metric(label: String, value: String) {
    Column {
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.titleLarge, color = Forest)
    }
}
