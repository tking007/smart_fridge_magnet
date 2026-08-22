package org.smartfridgemagnet.ui.theme

import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.Easing
import androidx.compose.animation.core.FiniteAnimationSpec
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween

/**
 * 动效 token。时长与曲线统一在这里定，业务代码不写裸数字 —— 各处随手填 200ms / 300ms 时，
 * 同一个界面上的动画会互相错拍，观感上就是「有的快有的慢」。
 */
object Motion {
    /** 按压、勾选这类即时反馈。超过这个时长会让人觉得按钮「粘手」。 */
    const val QUICK_MS = 110

    /** 颜色、尺寸、显隐等常规过渡。 */
    const val STANDARD_MS = 220

    /** 页面切换与大块内容进出。 */
    const val PAGE_MS = 300

    /** 起步快、收尾缓，适合大多数「东西移动到位」的动画。 */
    val Emphasized: Easing = CubicBezierEasing(0.2f, 0f, 0f, 1f)

    /** 进入用的减速曲线。 */
    val Decelerate: Easing = CubicBezierEasing(0f, 0f, 0.2f, 1f)

    fun <T> quick(): FiniteAnimationSpec<T> = tween(QUICK_MS, easing = Emphasized)

    fun <T> standard(): FiniteAnimationSpec<T> = tween(STANDARD_MS, easing = Emphasized)

    fun <T> page(): FiniteAnimationSpec<T> = tween(PAGE_MS, easing = Decelerate)

    /**
     * 按压回弹。用弹簧而不是 tween：手指抬起时的那一下回弹是「柔软」的主要来源，
     * 匀速回到原尺寸会显得生硬。阻尼略低于临界，给一点点过冲。
     */
    fun <T> press(): FiniteAnimationSpec<T> = spring(
        dampingRatio = 0.55f,
        stiffness = Spring.StiffnessMediumLow,
    )
}
