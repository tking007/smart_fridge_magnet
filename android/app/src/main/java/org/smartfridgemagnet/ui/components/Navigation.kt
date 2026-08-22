package org.smartfridgemagnet.ui.components

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.ui.graphics.graphicsLayer
import org.smartfridgemagnet.ui.theme.Motion
import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.border
import androidx.compose.material3.ColorScheme
import androidx.compose.ui.graphics.luminance
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import org.smartfridgemagnet.ui.theme.Pill
import org.smartfridgemagnet.ui.theme.Spacing
import org.smartfridgemagnet.ui.theme.accents

/** 底部 dock 的一项。 */
data class NavItem(
    val label: String,
    val icon: ImageVector,
)

/**
 * 底部 dock：半透明悬浮胶囊 + 右侧独立的圆形主操作键。
 *
 * 不做成通栏的底栏是为了让它读起来像一个「控件」而不是屏幕边界，内容可以从它下面透出去；
 * 主操作键独立在胶囊外，因为它不是第五个 tab —— 点它不切换页面。
 *
 * 是**半透明 + 高光描边**，不是真的背景模糊：Compose 原生没有 backdrop blur
 * （`Modifier.blur` 模糊的是自身内容），真正的背景模糊需要 Android 12+ 的 `RenderEffect`
 * 或引入 Haze 之类的库。半透明已足以给出「浮在内容之上」的层次。
 *
 * @param action 主操作键；不传则只显示胶囊。
 */
@Composable
fun AppDock(
    items: List<NavItem>,
    selectedIndex: Int,
    onSelect: (Int) -> Unit,
    modifier: Modifier = Modifier,
    action: DockAction? = null,
) {
    val scheme = MaterialTheme.colorScheme
    // 偏高的不透明度是刻意的：Compose 没有背景模糊，透得太狠时背后的文字会直接糊在图标上，
    // 那不像磨砂玻璃，只像忘了填背景色。留一点透光交代层次就够。
    val glass = scheme.surface.copy(alpha = if (scheme.isLight()) 0.90f else 0.92f)
    // 顶部高光描边：iOS 那种玻璃感很大一部分来自这条比底色更亮的细边。
    val rim = if (scheme.isLight()) Color.White.copy(alpha = 0.72f)
    else Color.White.copy(alpha = 0.16f)

    Row(
        modifier = modifier
            .fillMaxWidth()
            .navigationBarsPadding()
            .padding(horizontal = Spacing.lg, vertical = Spacing.md),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(Spacing.md),
    ) {
        Surface(
            modifier = Modifier
                .weight(1f)
                .border(1.dp, rim, Pill),
            shape = Pill,
            color = glass,
            shadowElevation = 12.dp,
        ) {
            Row(
                Modifier.padding(horizontal = 6.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                items.forEachIndexed { index, item ->
                    DockTab(
                        item = item,
                        selected = index == selectedIndex,
                        onClick = { onSelect(index) },
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }
        if (action != null) {
            DockActionButton(action, rim)
        }
    }
}

/** 浅色主题判定：`surface` 比 `onSurface` 亮就是浅色。避免把 darkTheme 再传一遍。 */
private fun ColorScheme.isLight(): Boolean = surface.luminance() > onSurface.luminance()

/** dock 右侧的主操作键。 */
data class DockAction(
    val icon: ImageVector,
    val contentDescription: String,
    val onClick: () -> Unit,
)

@Composable
private fun DockActionButton(action: DockAction, rim: Color) {
    val interaction = remember { MutableInteractionSource() }
    Surface(
        modifier = Modifier
            .size(56.dp)
            .pressFeedback(interaction, scaleDown = 0.92f)
            .clip(CircleShape)
            .border(1.dp, rim, CircleShape)
            .clickable(
                interactionSource = interaction,
                indication = null,
                onClick = action.onClick,
            ),
        shape = CircleShape,
        // 主操作键保持接近实心：它是这一屏的唯一主操作，透下去会削弱它的存在感。
        color = MaterialTheme.colorScheme.primary.copy(alpha = 0.96f),
        shadowElevation = 12.dp,
    ) {
        Box(contentAlignment = Alignment.Center) {
            Icon(
                action.icon,
                contentDescription = action.contentDescription,
                modifier = Modifier.size(24.dp),
                tint = MaterialTheme.accents.brand.solid,
            )
        }
    }
}

@Composable
private fun DockTab(
    item: NavItem,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val bg by animateColorAsState(
        if (selected) MaterialTheme.accents.brand.container else Color.Transparent,
        animationSpec = Motion.standard(),
        label = "dock-tab-bg",
    )
    val fg by animateColorAsState(
        if (selected) MaterialTheme.accents.brand.onContainer
        else MaterialTheme.colorScheme.onSurfaceVariant,
        animationSpec = Motion.standard(),
        label = "dock-tab-fg",
    )
    val tint by animateColorAsState(
        if (selected) MaterialTheme.accents.brand.solid else fg,
        animationSpec = Motion.standard(),
        label = "dock-tab-tint",
    )
    // 激活项图标略放大：药丸底色变化本身不够醒目，尺寸变化让「切过来了」这件事被看见。
    val iconScale by animateFloatAsState(
        targetValue = if (selected) 1.1f else 1f,
        animationSpec = Motion.press(),
        label = "dock-tab-icon",
    )
    val interaction = remember { MutableInteractionSource() }
    Column(
        modifier = modifier
            .pressFeedback(interaction, scaleDown = 0.94f)
            .clip(Pill)
            .background(bg)
            .clickable(
                interactionSource = interaction,
                indication = null,
                onClick = onClick,
            )
            .padding(vertical = 7.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Icon(
            item.icon,
            contentDescription = item.label,
            modifier = Modifier
                .size(21.dp)
                .graphicsLayer { scaleX = iconScale; scaleY = iconScale },
            tint = tint,
        )
        Text(item.label, style = MaterialTheme.typography.labelSmall, color = fg)
    }
}

/**
 * 页面头部：小灰日期行 + 大标题 + 副标题，右侧可选操作。
 *
 * 替代 TopAppBar：标题放进内容流里才能做到 28sp 大字，TopAppBar 的高度限制做不到。
 */
@Composable
fun HeroHeader(
    title: String,
    modifier: Modifier = Modifier,
    overline: String? = null,
    subtitle: String? = null,
    action: (@Composable () -> Unit)? = null,
) {
    Column(
        modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(Spacing.xs),
    ) {
        if (overline != null || action != null) {
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    overline.orEmpty(),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.weight(1f),
                )
                action?.invoke()
            }
        }
        Text(title, style = MaterialTheme.typography.displayMedium)
        if (subtitle != null) {
            Text(
                subtitle,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
