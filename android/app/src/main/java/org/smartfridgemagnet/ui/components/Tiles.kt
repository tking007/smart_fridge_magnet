package org.smartfridgemagnet.ui.components

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import org.smartfridgemagnet.ui.theme.AccentColor
import org.smartfridgemagnet.ui.theme.Spacing

/** 图标底板：圆角方块 + 居中图标，用于磁贴与列表前导位。 */
@Composable
fun IconBadge(
    icon: ImageVector,
    modifier: Modifier = Modifier,
    size: androidx.compose.ui.unit.Dp = 42.dp,
    container: Color? = null,
    tint: Color? = null,
) {
    Box(
        modifier = modifier
            .size(size)
            .clip(MaterialTheme.shapes.medium)
            .background(container ?: MaterialTheme.colorScheme.surfaceVariant),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            icon,
            contentDescription = null,
            modifier = Modifier.size(size * 0.5f),
            tint = tint ?: MaterialTheme.colorScheme.onSurface,
        )
    }
}

/**
 * 入口磁贴：图标底板 + 标题 + 说明。
 *
 * @param tone 图标底板配色；不传则用中性灰。给每个入口一个颜色，网格才不会读成一片灰。
 * @param wide 占满整行时图标与文字并排并在右侧给出箭头；否则竖排，用于 2 列网格。
 */
@Composable
fun ActionTile(
    icon: ImageVector,
    title: String,
    subtitle: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    tone: AccentColor? = null,
    wide: Boolean = false,
    trailing: (@Composable () -> Unit)? = null,
) {
    AppCard(
        modifier = modifier,
        contentPadding = PaddingValues(Spacing.lg),
        onClick = onClick,
    ) {
        val badge: @Composable () -> Unit = {
            IconBadge(
                icon = icon,
                container = tone?.container,
                tint = tone?.solid,
            )
        }
        val labels: @Composable () -> Unit = {
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(title, style = MaterialTheme.typography.titleSmall, maxLines = 1)
                Text(
                    subtitle,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
        if (wide) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(Spacing.md),
            ) {
                badge()
                Box(Modifier.weight(1f)) { labels() }
                trailing?.invoke()
            }
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
                badge()
                labels()
            }
        }
    }
}

/**
 * 食材磁贴：小图标 + 名称 + 数量，2 列铺开。
 *
 * @param tone 临期等需要整块提示时传入底色。
 */
@Composable
fun FoodTile(
    name: String,
    quantity: String,
    modifier: Modifier = Modifier,
    iconRes: Int? = null,
    iconTone: Color? = null,
    fallbackDotColor: Color? = null,
    tone: Color? = null,
    onClick: (() -> Unit)? = null,
    trailing: (@Composable () -> Unit)? = null,
) {
    AppCard(
        modifier = modifier,
        contentPadding = PaddingValues(Spacing.md),
        tone = tone,
        onClick = onClick,
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(Spacing.md),
        ) {
            Box(
                modifier = Modifier
                    .size(38.dp)
                    .clip(MaterialTheme.shapes.small)
                    .background(iconTone ?: MaterialTheme.colorScheme.surfaceVariant),
                contentAlignment = Alignment.Center,
            ) {
                if (iconRes != null) {
                    Image(
                        painter = painterResource(iconRes),
                        contentDescription = null,
                        modifier = Modifier.size(22.dp),
                    )
                } else {
                    Box(
                        Modifier
                            .size(9.dp)
                            .clip(MaterialTheme.shapes.extraSmall)
                            .background(fallbackDotColor ?: MaterialTheme.colorScheme.outline)
                    )
                }
            }
            Column(
                modifier = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(1.dp),
            ) {
                Text(
                    name,
                    style = MaterialTheme.typography.titleSmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    quantity,
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            trailing?.invoke()
        }
    }
}

/**
 * 媒体卡：顶部色块/图片 + 底部文字。用于菜品与版式预览。
 */
@Composable
fun MediaCard(
    title: String,
    subtitle: String,
    modifier: Modifier = Modifier,
    imageRes: Int? = null,
    coverTone: Color? = null,
    coverHeight: androidx.compose.ui.unit.Dp = 104.dp,
    onClick: (() -> Unit)? = null,
    coverContent: (@Composable () -> Unit)? = null,
) {
    AppCard(
        modifier = modifier,
        contentPadding = PaddingValues(0.dp),
        onClick = onClick,
    ) {
        Column {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(coverHeight)
                    .background(coverTone ?: MaterialTheme.colorScheme.surfaceVariant),
                contentAlignment = Alignment.Center,
            ) {
                when {
                    imageRes != null -> Image(
                        painter = painterResource(imageRes),
                        contentDescription = null,
                        modifier = Modifier.size(coverHeight * 0.44f),
                        contentScale = ContentScale.Fit,
                    )
                    coverContent != null -> coverContent()
                }
            }
            Column(
                Modifier.padding(horizontal = Spacing.md, vertical = Spacing.md),
                verticalArrangement = Arrangement.spacedBy(3.dp),
            ) {
                Text(
                    title,
                    style = MaterialTheme.typography.titleSmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    subtitle,
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}
