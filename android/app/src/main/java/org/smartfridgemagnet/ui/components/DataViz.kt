package org.smartfridgemagnet.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.Canvas
import androidx.compose.ui.graphics.PathEffect
import org.smartfridgemagnet.ui.theme.Pill
import org.smartfridgemagnet.ui.theme.Spacing
import org.smartfridgemagnet.ui.theme.accents

/** 一个统计位：小灰标签 + 大数字 + 可选单位后缀。 */
data class Stat(
    val label: String,
    val value: String,
    val unit: String? = null,
    val tint: Color? = null,
)

/**
 * 大数字组：把 2–3 个关键数值并排摆成一行，用细竖线分隔。
 *
 * 数值走 displaySmall（等宽数位），标签压到 labelMedium 灰色 —— 让人一眼看到数字而不是标签。
 */
@Composable
fun StatRow(
    stats: List<Stat>,
    modifier: Modifier = Modifier,
) {
    Row(modifier = modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        stats.forEachIndexed { index, stat ->
            if (index > 0) {
                Box(
                    Modifier
                        .padding(horizontal = Spacing.md)
                        .width(1.dp)
                        .height(38.dp)
                        .background(MaterialTheme.accents.track)
                )
            }
            StatCell(stat, Modifier.weight(1f))
        }
    }
}

/** 单个统计位。数值过长（如「刚刚」）时自动降一档字号，避免折行。 */
@Composable
fun StatCell(stat: Stat, modifier: Modifier = Modifier) {
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(
            stat.label,
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
        )
        Row(verticalAlignment = Alignment.Bottom) {
            Text(
                stat.value,
                style = if (stat.value.length > 3) MaterialTheme.typography.headlineLarge
                else MaterialTheme.typography.displaySmall,
                color = stat.tint ?: MaterialTheme.colorScheme.onSurface,
                maxLines = 1,
            )
            if (stat.unit != null) {
                Text(
                    stat.unit,
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.padding(start = 2.dp, bottom = 3.dp),
                )
            }
        }
    }
}

/** 细进度轨：电量、好感度、流式进度。 */
@Composable
fun ProgressTrack(
    fraction: Float,
    modifier: Modifier = Modifier,
    color: Color? = null,
    height: Dp = 8.dp,
) {
    Box(
        modifier
            .fillMaxWidth()
            .height(height)
            .clip(Pill)
            .background(MaterialTheme.accents.track)
    ) {
        Box(
            Modifier
                .fillMaxWidth(fraction.coerceIn(0f, 1f))
                .fillMaxHeight()
                .clip(Pill)
                .background(color ?: MaterialTheme.colorScheme.primary)
        )
    }
}

/** 环形图的一段。 */
data class DonutSlice(val value: Float, val color: Color, val label: String)

/**
 * 环形图：构成比例 + 中心文案。
 *
 * 全部 slice 之和为 0 时只画底环，不会除零。
 */
@Composable
fun DonutChart(
    slices: List<DonutSlice>,
    modifier: Modifier = Modifier,
    size: Dp = 92.dp,
    strokeWidth: Dp = 13.dp,
    center: (@Composable () -> Unit)? = null,
) {
    val track = MaterialTheme.accents.track
    Box(modifier.size(size), contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxWidth().fillMaxHeight()) {
            val stroke = strokeWidth.toPx()
            val inset = stroke / 2
            val arcSize = Size(this.size.width - stroke, this.size.height - stroke)
            drawArc(
                color = track, startAngle = 0f, sweepAngle = 360f, useCenter = false,
                topLeft = Offset(inset, inset), size = arcSize,
                style = Stroke(width = stroke),
            )
            val total = slices.sumOf { it.value.toDouble() }.toFloat()
            if (total <= 0f) return@Canvas
            var start = -90f
            slices.forEach { slice ->
                val sweep = slice.value / total * 360f
                drawArc(
                    color = slice.color, startAngle = start, sweepAngle = sweep, useCenter = false,
                    topLeft = Offset(inset, inset), size = arcSize,
                    style = Stroke(width = stroke, cap = StrokeCap.Butt),
                )
                start += sweep
            }
        }
        center?.invoke()
    }
}

/** 环形图图例：色点 + 名称 + 数值。 */
@Composable
fun DonutLegend(slices: List<DonutSlice>, modifier: Modifier = Modifier) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(Spacing.sm)) {
        slices.forEach { slice ->
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(Spacing.sm),
            ) {
                Box(Modifier.size(8.dp).clip(CircleShape).background(slice.color))
                Text(
                    slice.label,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    slice.value.toInt().toString(),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

/** 柱状图的一根。 */
data class Bar(val value: Float, val label: String, val highlight: Boolean = false)

/**
 * 柱状图：可选虚线均值参考线。
 *
 * 最高柱决定比例；全为 0 时所有柱画成最小高度而不是消失。
 */
@Composable
fun BarChart(
    bars: List<Bar>,
    modifier: Modifier = Modifier,
    height: Dp = 68.dp,
    color: Color? = null,
    highlightColor: Color? = null,
    showAverage: Boolean = false,
) {
    if (bars.isEmpty()) return
    val max = bars.maxOf { it.value }.coerceAtLeast(0.0001f)
    val average = bars.map { it.value }.average().toFloat()
    val barColor = color ?: MaterialTheme.accents.brand.solid
    val hiColor = highlightColor ?: MaterialTheme.accents.warning.solid
    val guide = MaterialTheme.colorScheme.onSurfaceVariant

    Column(modifier, verticalArrangement = Arrangement.spacedBy(Spacing.sm)) {
        Box(Modifier.fillMaxWidth().height(height)) {
            Row(
                Modifier.fillMaxWidth().fillMaxHeight(),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.Bottom,
            ) {
                bars.forEach { bar ->
                    Box(
                        Modifier
                            .weight(1f)
                            .fillMaxHeight((bar.value / max).coerceIn(0.06f, 1f))
                            .clip(RoundedCornerShape(topStart = 5.dp, topEnd = 5.dp, bottomStart = 3.dp, bottomEnd = 3.dp))
                            .background(if (bar.highlight) hiColor else barColor)
                    )
                }
            }
            if (showAverage && average > 0f) {
                Canvas(Modifier.fillMaxWidth().fillMaxHeight()) {
                    val y = this.size.height * (1f - (average / max).coerceIn(0f, 1f))
                    drawLine(
                        color = guide,
                        start = Offset(0f, y),
                        end = Offset(this.size.width, y),
                        strokeWidth = 1.dp.toPx(),
                        pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 6f)),
                    )
                }
            }
        }
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            bars.forEach { bar ->
                Text(
                    bar.label,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.weight(1f),
                    maxLines = 1,
                )
            }
        }
    }
}
