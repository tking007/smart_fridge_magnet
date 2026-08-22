package org.smartfridgemagnet.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import org.smartfridgemagnet.ui.theme.Pill
import org.smartfridgemagnet.ui.theme.accents

/**
 * 按钮语义变体。
 *
 * - [Primary] 墨色实心，一屏只应有一个，用于该屏的唯一主操作
 * - [Secondary] 品牌绿浅底 + 深绿字。**不能用 surface 白底** —— 它经常放在白卡里，
 *   白底白字等于隐形
 * - [Ghost] 无底色，用于卡片右上角的「管理 / 全部」
 * - [Destructive] 红色实心，删除与「修复异常」
 */
enum class AppButtonVariant { Primary, Secondary, Ghost, Destructive }

/** 按钮尺寸。[Large] 是全宽主操作的高度。 */
enum class AppButtonSize { Large, Medium, Small }

/**
 * 统一按钮：胶囊形，`loading` 时显示转圈并禁用点击。
 */
@Composable
fun AppButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    variant: AppButtonVariant = AppButtonVariant.Primary,
    size: AppButtonSize = AppButtonSize.Large,
    enabled: Boolean = true,
    loading: Boolean = false,
    leadingIcon: ImageVector? = null,
) {
    val height = when (size) {
        AppButtonSize.Large -> 54.dp
        AppButtonSize.Medium -> 46.dp
        AppButtonSize.Small -> 38.dp
    }
    val hPadding = when (size) {
        AppButtonSize.Large -> 24.dp
        AppButtonSize.Medium -> 20.dp
        AppButtonSize.Small -> 14.dp
    }
    val textStyle = when (size) {
        AppButtonSize.Large -> MaterialTheme.typography.labelLarge
        else -> MaterialTheme.typography.titleSmall
    }
    val iconSize = if (size == AppButtonSize.Small) 16.dp else 19.dp

    val content: @Composable () -> Unit = {
        Row(
            horizontalArrangement = Arrangement.spacedBy(9.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (loading) {
                CircularProgressIndicator(
                    modifier = Modifier.size(iconSize),
                    strokeWidth = 2.dp,
                    color = LocalContentColor.current,
                )
            } else if (leadingIcon != null) {
                Icon(leadingIcon, contentDescription = null, modifier = Modifier.size(iconSize))
            }
            Text(text, style = textStyle)
        }
    }
    val clickEnabled = enabled && !loading
    val shaped = modifier.heightIn(min = height)
    val padding = ButtonDefaults.ContentPadding.let {
        androidx.compose.foundation.layout.PaddingValues(horizontal = hPadding, vertical = 0.dp)
    }

    when (variant) {
        AppButtonVariant.Primary -> Button(
            onClick = onClick, modifier = shaped, enabled = clickEnabled,
            shape = Pill, contentPadding = padding,
        ) { content() }

        AppButtonVariant.Secondary -> Button(
            onClick = onClick, modifier = shaped, enabled = clickEnabled,
            shape = Pill, contentPadding = padding,
            colors = ButtonDefaults.buttonColors(
                containerColor = MaterialTheme.accents.brand.container,
                contentColor = MaterialTheme.accents.brand.onContainer,
            ),
        ) { content() }

        AppButtonVariant.Destructive -> Button(
            onClick = onClick, modifier = shaped, enabled = clickEnabled,
            shape = Pill, contentPadding = padding,
            colors = ButtonDefaults.buttonColors(
                containerColor = MaterialTheme.accents.danger.solid,
                contentColor = MaterialTheme.colorScheme.onPrimary,
            ),
        ) { content() }

        AppButtonVariant.Ghost -> TextButton(
            onClick = onClick, modifier = modifier, enabled = clickEnabled,
            shape = Pill,
        ) {
            Text(
                text,
                style = MaterialTheme.typography.titleSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}
