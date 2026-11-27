package com.garminanalysis.app.ui.components

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import org.json.JSONArray

/** Multi-series line chart from JSONArray numeric series (nulls become gaps). */
@Composable
fun LineChart(
    title: String,
    series: List<Pair<String, JSONArray>>,
    colors: List<Color>,
    modifier: Modifier = Modifier,
) {
    Column(modifier = modifier.fillMaxWidth()) {
        Text(title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(bottom = 4.dp))
        LineChartCanvas(series = series, colors = colors)
    }
}

@Composable
fun LineChart(
    values: JSONArray,
    color: Color,
    modifier: Modifier = Modifier,
    title: String = "",
) {
    LineChart(
        title = title,
        series = listOf("" to values),
        colors = listOf(color),
        modifier = modifier,
    )
}

@Composable
private fun LineChartCanvas(
    series: List<Pair<String, JSONArray>>,
    colors: List<Color>,
    modifier: Modifier = Modifier,
) {
    Canvas(modifier = modifier.fillMaxWidth().height(160.dp)) {
        val width = size.width
        val height = size.height
        val pad = 8f
        fun valuesOf(arr: JSONArray): List<Float?> {
            return (0 until arr.length()).map {
                if (arr.isNull(it)) null else arr.optDouble(it).toFloat()
            }
        }
        val all = series.flatMap { valuesOf(it.second) }.filterNotNull()
        if (all.isEmpty()) return@Canvas
        val minV = all.minOrNull()!!
        val maxV = all.maxOrNull()!!
        val span = (maxV - minV).takeIf { it > 1e-3f } ?: 1f
        series.forEachIndexed { idx, (_, arr) ->
            val vals = valuesOf(arr)
            if (vals.isEmpty()) return@forEachIndexed
            val color = colors.getOrElse(idx) { Color.Gray }
            val path = Path()
            var started = false
            val n = vals.size.coerceAtLeast(1)
            vals.forEachIndexed { i, v ->
                if (v == null) {
                    started = false
                    return@forEachIndexed
                }
                val x = pad + (width - 2 * pad) * i / (n - 1).coerceAtLeast(1)
                val y = height - pad - (v - minV) / span * (height - 2 * pad)
                if (!started) {
                    path.moveTo(x, y)
                    started = true
                } else {
                    path.lineTo(x, y)
                }
            }
            drawPath(path, color = color, style = Stroke(width = 3f, cap = StrokeCap.Round))
        }
        drawLine(Color.LightGray, Offset(pad, height - pad), Offset(width - pad, height - pad), strokeWidth = 1f)
    }
}

