package org.smartfridgemagnet.ui

import org.smartfridgemagnet.film.FilmDecoder

/**
 * 手绘涂鸦的「归一化坐标 ⇄ 像素坐标」换算。
 *
 * 屏幕画布是任意像素尺寸（跟手机宽度、系统字号、导航栏都有关），而导出必须精确是 792×528。
 * 这中间的换算是整个功能里唯一「算错了也不报错、只有把图传到设备上才看得出来」的地方，
 * 因此抽成不依赖 Compose 与 Android framework 的纯函数，好让 JVM 单测直接覆盖
 * （见 DoodleGeometryTest）。
 *
 * 笔画存归一化坐标而不是直接画到 Bitmap：显示与导出各自按自己的尺寸重绘同一份数据，没有
 * 缩放误差与二次采样的模糊；撤销也就是「弹出最后一笔」，不需要保留位图历史。
 */
internal object DoodleGeometry {

    /**
     * 线宽的像素下限。
     *
     * `Paint.strokeWidth = 0` 在 Android 上是「hairline」特殊语义（恒 1 物理像素、不参与
     * 变换），而滑块拖到最小时算出来的值可能小于 1，导出线宽会与预览不一致。钳到 1 让两边都
     * 走同一条普通描边路径。
     */
    const val MIN_STROKE_WIDTH_PX = 1f

    /** 导出画布宽（= `.film` 画布宽，跨端契约见 docs/contracts/README.md）。 */
    val exportWidth: Int get() = FilmDecoder.WIDTH

    /** 导出画布高。 */
    val exportHeight: Int get() = FilmDecoder.HEIGHT

    /**
     * 触摸点像素 → 归一化 `0f..1f`。
     *
     * **钳制到 [0,1]**：手指可以滑出画布边界（`detectDrag` 之后的 position 会给出负数或超过
     * size 的值），不钳的话导出时会往 Bitmap 外面画，Android 的 Canvas 静默裁掉。
     *
     * @param extent 该轴上画布的像素长度；`<= 0` 表示还没测量完，此时返回 0 而不是除出
     *        NaN/Infinity（NaN 会一路传进 Path，画出整块空白且不抛异常）
     */
    fun normalize(pixel: Float, extent: Float): Float =
        if (extent <= 0f) 0f else (pixel / extent).coerceIn(0f, 1f)

    /** 归一化坐标 → 目标画布像素坐标。x 传宽、y 传高。 */
    fun toPixel(norm: Float, extent: Int): Float = norm * extent

    /**
     * 归一化线宽 → 目标画布像素线宽。
     *
     * **一律按宽度换算**，y 方向不参与：线宽是各向同性的量，按宽高分别算会得出两个值而描边
     * 只接受一个。按宽度算能保证「屏幕上占画布 1/50 宽的笔，在 792 像素上也占 1/50」。
     */
    fun strokeWidthPx(widthNorm: Float, targetWidth: Int): Float =
        (widthNorm * targetWidth).coerceAtLeast(MIN_STROKE_WIDTH_PX)
}
