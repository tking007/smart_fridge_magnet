package org.smartfridgemagnet.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import org.smartfridgemagnet.ui.theme.AccentColor
import org.smartfridgemagnet.ui.theme.Pill
import org.smartfridgemagnet.ui.theme.Spacing
import org.smartfridgemagnet.ui.theme.accents

/** 状态徽标语义。 */
enum class BadgeTone { Neutral, Success, Warning, Info, Danger }

@Composable
private fun BadgeTone.colors(): AccentColor = when (this) {
    BadgeTone.Success -> MaterialTheme.accents.success
    BadgeTone.Warning -> MaterialTheme.accents.warning
    BadgeTone.Info -> MaterialTheme.accents.info
    BadgeTone.Danger -> MaterialTheme.accents.danger
    BadgeTone.Neutral -> AccentColor(
        solid = MaterialTheme.colorScheme.outline,
        container = MaterialTheme.accents.track,
        onContainer = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/**
 * 状态徽标：小圆点 + 文案的胶囊，用于「已是最新 / 临期 / 需修复」等结论标注。
 */
@Composable
fun StatusBadge(
    text: String,
    tone: BadgeTone = BadgeTone.Neutral,
    modifier: Modifier = Modifier,
) {
    val c = tone.colors()
    Row(
        modifier = modifier
            .clip(Pill)
            .background(c.container)
            .padding(horizontal = 10.dp, vertical = 5.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(6.dp).clip(CircleShape).background(c.solid))
        Text(text, style = MaterialTheme.typography.labelMedium, color = c.onContainer)
    }
}

/** 轻量筛选胶囊：选中时填墨色，未选中为轨道灰。 */
@Composable
fun AppChip(
    text: String,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val scheme = MaterialTheme.colorScheme
    Row(
        modifier = modifier
            .clip(Pill)
            .background(if (selected) scheme.primary else MaterialTheme.accents.track)
            .clickable(onClick = onClick)
            .padding(horizontal = Spacing.lg, vertical = Spacing.sm),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text,
            style = MaterialTheme.typography.titleSmall,
            color = if (selected) scheme.onPrimary else scheme.onSurfaceVariant,
        )
    }
}
