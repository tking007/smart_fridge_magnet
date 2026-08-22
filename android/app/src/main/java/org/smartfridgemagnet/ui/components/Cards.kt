package org.smartfridgemagnet.ui.components

import androidx.compose.animation.animateContentSize
import androidx.compose.animation.animateColorAsState
import androidx.compose.runtime.getValue
import org.smartfridgemagnet.ui.theme.Motion
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import org.smartfridgemagnet.ui.theme.Spacing

/**
 * 统一卡片：纯白（深色模式为浅一档的灰）+ 大圆角 + **无边框无投影**。
 * 层次来自它与页面底色的色阶差，所以卡片不能与 background 同色。
 *
 * @param onClick 给了就整块可点，并带按压缩放反馈。**不要在外部自己套 clickable** ——
 *   卡片上的涟漪会被大圆角裁出方形扩散，看起来像渲染错误。
 * @param tone 需要整块染色时传入（如临期橙、异常红）；默认跟随 surface。
 */
@Composable
fun AppCard(
    modifier: Modifier = Modifier,
    contentPadding: PaddingValues = PaddingValues(Spacing.lg),
    tone: Color? = null,
    contentColor: Color? = null,
    onClick: (() -> Unit)? = null,
    pressedScale: Float = 0.98f,
    content: @Composable () -> Unit,
) {
    val container by animateColorAsState(
        tone ?: MaterialTheme.colorScheme.surface,
        animationSpec = Motion.standard(),
        label = "card-tone",
    )
    Card(
        modifier = modifier
            .fillMaxWidth()
            .let {
                if (onClick != null) it.pressableCard(onClick, scaleDown = pressedScale) else it
            },
        shape = MaterialTheme.shapes.large,
        colors = CardDefaults.cardColors(
            containerColor = container,
            contentColor = contentColor ?: MaterialTheme.colorScheme.onSurface,
        ),
        elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
    ) {
        // 内容随尺寸变化平滑过渡：卡片里多出一行（如新增一条提示）时直接跳变很突兀。
        Column(
            Modifier
                .animateContentSize(animationSpec = Motion.standard())
                .padding(contentPadding)
        ) { content() }
    }
}

/** 带标题的分区卡片：标题 + 可选副标题 + 内容。 */
@Composable
fun SectionCard(
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    content: @Composable () -> Unit,
) {
    AppCard(modifier = modifier) {
        Column(verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
            Column(verticalArrangement = Arrangement.spacedBy(Spacing.xs)) {
                Text(title, style = MaterialTheme.typography.titleMedium)
                if (subtitle != null) {
                    Text(
                        subtitle,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            content()
        }
    }
}

/**
 * 分区标题行：左侧大标题，右侧可选操作。
 * 卡片外的标题（不在卡片内），与卡片一起构成「标题 + 内容块」的节奏。
 */
@Composable
fun SectionTitle(
    text: String,
    modifier: Modifier = Modifier,
    trailing: (@Composable () -> Unit)? = null,
) {
    Row(
        modifier = modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text,
            style = MaterialTheme.typography.headlineMedium,
            modifier = Modifier.weight(1f),
        )
        trailing?.invoke()
    }
}
