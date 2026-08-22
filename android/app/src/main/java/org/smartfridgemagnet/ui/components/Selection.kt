package org.smartfridgemagnet.ui.components

import org.smartfridgemagnet.ui.components.pressableCard
import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import org.smartfridgemagnet.ui.theme.Pill
import org.smartfridgemagnet.ui.theme.Spacing
import org.smartfridgemagnet.ui.theme.accents

/**
 * 分段控件：灰色轨道上滑动的白色胶囊。
 *
 * 用于「方案 A / 方案 B」「日 / 周 / 月」这类互斥且数量固定（2–5 项）的切换。
 * 超过 5 项改用 [OptionSheet]，否则每项会窄到读不出来。
 */
@Composable
fun SegmentedControl(
    options: List<String>,
    selectedIndex: Int,
    onSelect: (Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clip(Pill)
            .background(MaterialTheme.accents.track)
            .padding(3.dp),
        horizontalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        options.forEachIndexed { index, label ->
            val selected = index == selectedIndex
            val bg by animateColorAsState(
                if (selected) MaterialTheme.colorScheme.surface else Color.Transparent,
                label = "segment-bg",
            )
            Box(
                modifier = Modifier
                    .weight(1f)
                    .clip(Pill)
                    .background(bg)
                    .clickable { onSelect(index) }
                    .padding(vertical = 9.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    label,
                    style = MaterialTheme.typography.titleSmall,
                    color = if (selected) MaterialTheme.colorScheme.onSurface
                    else MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    textAlign = TextAlign.Center,
                )
            }
        }
    }
}

/** 周历条的一格。 */
data class DayCell(
    val weekday: String,
    val day: Int,
    val today: Boolean = false,
    /** 该日有安排（如已排三餐），用浅色底标出。 */
    val marked: Boolean = false,
)

/**
 * 周历条：一周 7 格，今天填成墨色实心，有安排的日子用浅绿底。
 */
@Composable
fun WeekStrip(
    days: List<DayCell>,
    modifier: Modifier = Modifier,
    onSelect: ((Int) -> Unit)? = null,
) {
    Row(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(5.dp),
    ) {
        days.forEachIndexed { index, cell ->
            val bg = when {
                cell.today -> MaterialTheme.colorScheme.primary
                cell.marked -> MaterialTheme.accents.success.container
                else -> MaterialTheme.accents.track
            }
            val fg = when {
                cell.today -> MaterialTheme.colorScheme.onPrimary
                cell.marked -> MaterialTheme.accents.success.onContainer
                else -> MaterialTheme.colorScheme.onSurface
            }
            Column(
                modifier = Modifier
                    .weight(1f)
                    .clip(MaterialTheme.shapes.medium)
                    .background(bg)
                    .let { m -> if (onSelect != null) m.pressableCard({ onSelect(index) }, scaleDown = 0.94f) else m }
                    .padding(vertical = 9.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(3.dp),
            ) {
                Text(
                    cell.weekday,
                    style = MaterialTheme.typography.labelSmall,
                    color = fg.copy(alpha = 0.65f),
                )
                Text(
                    cell.day.toString(),
                    style = MaterialTheme.typography.titleMedium,
                    color = fg,
                )
            }
        }
    }
}

/** 开关：开启时为墨色，与主按钮同色，表示「生效中」。 */
@Composable
fun AppSwitch(
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    Switch(
        checked = checked,
        onCheckedChange = onCheckedChange,
        modifier = modifier,
        enabled = enabled,
        colors = SwitchDefaults.colors(
            checkedThumbColor = MaterialTheme.colorScheme.onPrimary,
            checkedTrackColor = MaterialTheme.colorScheme.primary,
            checkedBorderColor = MaterialTheme.colorScheme.primary,
            uncheckedThumbColor = MaterialTheme.colorScheme.surface,
            uncheckedTrackColor = MaterialTheme.accents.track,
            uncheckedBorderColor = MaterialTheme.accents.track,
        ),
    )
}

/**
 * 单选行：标题（+ 说明）+ 右侧勾。选中项整行换浅底。
 * 用于底部抽屉里的选项列表与「版式 / 外观」这类枚举设置。
 */
@Composable
fun OptionRow(
    title: String,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clip(MaterialTheme.shapes.medium)
            .background(
                if (selected) MaterialTheme.accents.track else Color.Transparent
            )
            .pressableCard(onClick, scaleDown = 0.99f)
            .padding(horizontal = Spacing.lg, vertical = Spacing.md),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(
            Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            Text(title, style = MaterialTheme.typography.titleSmall)
            if (subtitle != null) {
                Text(
                    subtitle,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        if (selected) {
            Icon(
                Icons.Outlined.Check,
                contentDescription = null,
                modifier = Modifier.size(20.dp),
                tint = MaterialTheme.colorScheme.onSurface,
            )
        }
    }
}
