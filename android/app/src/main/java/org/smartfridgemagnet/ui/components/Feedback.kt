package org.smartfridgemagnet.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.draw.clip
import org.smartfridgemagnet.ui.theme.Pill
import org.smartfridgemagnet.ui.theme.accents
import org.smartfridgemagnet.ui.theme.Spacing

/** 分区小标题：列表/表单上方的一行强调标题。 */
@Composable
fun SectionHeader(text: String, modifier: Modifier = Modifier) {
    Text(
        text,
        modifier = modifier.padding(horizontal = Spacing.xs, vertical = Spacing.xs),
        style = MaterialTheme.typography.titleSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/** 辅助说明：解释某项设置/操作的一小段灰字。 */
@Composable
fun Hint(text: String, modifier: Modifier = Modifier) {
    Text(
        text,
        modifier = modifier,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/** 空态：图标 + 标题 + 说明 + 可选操作按钮。页面没有内容时用它替代空白。 */
@Composable
fun EmptyState(
    icon: ImageVector,
    title: String,
    modifier: Modifier = Modifier,
    description: String? = null,
    actionText: String? = null,
    onAction: (() -> Unit)? = null,
) {
    Column(
        modifier = modifier.fillMaxWidth().padding(Spacing.xxl),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(Spacing.md),
    ) {
        Icon(
            icon, contentDescription = null,
            modifier = Modifier.size(48.dp),
            tint = MaterialTheme.colorScheme.outline,
        )
        Text(
            title,
            style = MaterialTheme.typography.titleMedium,
            textAlign = TextAlign.Center,
        )
        if (description != null) {
            Text(
                description,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )
        }
        if (actionText != null && onAction != null) {
            AppButton(
                text = actionText,
                onClick = onAction,
                variant = AppButtonVariant.Secondary,
                size = AppButtonSize.Medium,
            )
        }
    }
}

/** 加载态：转圈 + 一行文案。长任务用它替代裸转圈，让用户知道在等什么。 */
@Composable
fun LoadingState(
    text: String,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.fillMaxWidth().padding(Spacing.xxl),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(Spacing.md),
    ) {
        CircularProgressIndicator(strokeWidth = 3.dp)
        Text(
            text,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
    }
}

/** 行内提示条：图标 + 文案，用于错误/警告/信息的轻量条幅（配色由调用方给容器色）。 */
@Composable
fun InlineNotice(
    icon: ImageVector,
    text: String,
    modifier: Modifier = Modifier,
    contentPadding: PaddingValues = PaddingValues(Spacing.md),
) {
    Row(
        modifier = modifier.fillMaxWidth().padding(contentPadding),
        horizontalArrangement = Arrangement.spacedBy(Spacing.sm),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, contentDescription = null, modifier = Modifier.size(18.dp))
        Text(text, style = MaterialTheme.typography.bodySmall)
    }
}


/**
 * 流式思考视图：阶段结论 + 逐字到达的模型思考。
 *
 * 一次配菜要二十多秒，静默会被当成卡死；逐字滚动的思考是「它真的在动」最便宜的证据。
 *
 * 只保留尾部 [tailChars] 个字符：思考全文能到几千字，全量渲染会把整屏顶开。
 */
@Composable
fun ThinkingCard(
    stage: String?,
    thinking: String,
    modifier: Modifier = Modifier,
    tailChars: Int = 1200,
) {
    val scroll = rememberScrollState()
    val tail = remember(thinking) {
        if (thinking.length <= tailChars) thinking else thinking.takeLast(tailChars)
    }
    // 新内容到达即滚到底，否则逐字增长的文本会停在开头，看起来像卡住了。
    LaunchedEffect(tail) { scroll.animateScrollTo(scroll.maxValue) }

    AppCard(modifier = modifier) {
        Column(
            modifier = Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.spacedBy(Spacing.md)
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    stage ?: "正在思考",
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.weight(1f),
                )
                StatusBadge("进行中", BadgeTone.Info)
            }
            if (tail.isNotBlank()) {
                Text(
                    tail,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f)
                        .verticalScroll(scroll),
                )
            } else {
                Spacer(Modifier.weight(1f))
            }
            LinearProgressIndicator(
                modifier = Modifier.fillMaxWidth().clip(Pill),
                color = MaterialTheme.accents.info.solid,
                trackColor = MaterialTheme.accents.track,
            )
        }
    }
}
