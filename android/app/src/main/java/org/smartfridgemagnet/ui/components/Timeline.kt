package org.smartfridgemagnet.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import org.smartfridgemagnet.ui.theme.Spacing

/**
 * 时间轴的一个节点。
 *
 * @param done 已发生画实心点，未发生画空心圈 —— 不读文字也能扫出进度到哪一步。
 * @param tint 覆盖节点颜色（失败节点用橙或红）。
 */
data class TimelineNode(
    val done: Boolean = true,
    val tint: Color? = null,
)

/**
 * 竖向时间轴：左侧圆点 + 连接线，右侧任意内容。
 *
 * 三餐进度、唤醒时段、设备同步记录共用它，让「时间序列」在全 App 是同一种视觉表达。
 * 行高由内容决定（IntrinsicSize.Min），连接线自动补满圆点到行底的剩余高度。
 */
@Composable
fun Timeline(
    nodes: List<TimelineNode>,
    modifier: Modifier = Modifier,
    itemSpacing: Dp = Spacing.lg,
    content: @Composable (index: Int) -> Unit,
) {
    Column(modifier.fillMaxWidth()) {
        nodes.forEachIndexed { index, node ->
            val isLast = index == nodes.lastIndex
            Row(
                Modifier
                    .fillMaxWidth()
                    .height(IntrinsicSize.Min)
            ) {
                TimelineRail(node = node, drawLine = !isLast)
                Box(
                    Modifier
                        .weight(1f)
                        .padding(start = Spacing.md, bottom = if (isLast) 0.dp else itemSpacing)
                ) { content(index) }
            }
        }
    }
}

@Composable
private fun TimelineRail(node: TimelineNode, drawLine: Boolean) {
    val color = node.tint ?: MaterialTheme.colorScheme.onSurface
    Column(
        modifier = Modifier.width(12.dp).fillMaxHeight(),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(Modifier.height(6.dp))
        if (node.done) {
            Box(Modifier.size(10.dp).clip(CircleShape).background(color))
        } else {
            Box(
                Modifier
                    .size(10.dp)
                    .clip(CircleShape)
                    .background(MaterialTheme.colorScheme.surface)
                    .border(2.dp, MaterialTheme.colorScheme.outline, CircleShape)
            )
        }
        if (drawLine) {
            Box(
                Modifier
                    .padding(vertical = 4.dp)
                    .width(2.dp)
                    .weight(1f)
                    .background(MaterialTheme.colorScheme.outlineVariant)
            )
        }
    }
}
