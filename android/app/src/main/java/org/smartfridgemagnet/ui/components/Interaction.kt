package org.smartfridgemagnet.ui.components

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.graphics.graphicsLayer
import org.smartfridgemagnet.ui.theme.Motion

/**
 * 可按压：按下时轻微缩小并降一点不透明度，抬起时弹回。
 *
 * 涟漪只说明「点到了」，缩放说明的是「这一整块是一个可按的东西」—— 卡片越大，单靠涟漪越难把它
 * 和静态内容区分开。幅度刻意很小（默认 2%）：再大会像按钮往屏幕里陷。
 *
 * @param scaleDown 按下时缩到的比例。整块卡片用默认值，小图标按钮可以给得更明显一点。
 */
fun Modifier.pressFeedback(
    interactionSource: MutableInteractionSource,
    scaleDown: Float = 0.98f,
    pressedAlpha: Float = 0.92f,
): Modifier = composed {
    val pressed by interactionSource.collectIsPressedAsState()
    val scale by animateFloatAsState(
        targetValue = if (pressed) scaleDown else 1f,
        animationSpec = Motion.press(),
        label = "press-scale",
    )
    val alpha by animateFloatAsState(
        targetValue = if (pressed) pressedAlpha else 1f,
        animationSpec = Motion.quick(),
        label = "press-alpha",
    )
    graphicsLayer {
        scaleX = scale
        scaleY = scale
        this.alpha = alpha
    }
}

/**
 * 整块可点击 + 按压缩放，且**不带涟漪**。
 *
 * 卡片上的涟漪会被大圆角裁出一个方形扩散，看起来像渲染错误；缩放本身已经是足够的反馈。
 * 小控件仍应使用带涟漪的标准 `clickable`。
 */
@Composable
fun Modifier.pressableCard(
    onClick: () -> Unit,
    enabled: Boolean = true,
    scaleDown: Float = 0.98f,
): Modifier {
    val interaction = remember { MutableInteractionSource() }
    return this
        .pressFeedback(interaction, scaleDown = scaleDown)
        .clickable(
            interactionSource = interaction,
            indication = null,
            enabled = enabled,
            onClick = onClick,
        )
}
