package org.smartfridgemagnet.ui

import org.smartfridgemagnet.film.FilmDecoder
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 手绘涂鸦「归一化坐标 ⇄ 像素坐标」换算的测试。
 *
 * 这是自定义画面功能里唯一「算错了也不报错、只有把图传到设备上才看得出来」的一处：手机上那块画布
 * 是任意像素尺寸，导出必须精确是 792×528，而两边跑的是同一份归一化数据。三种典型错误都不抛异常、
 * 都不在手机预览里显形 ——
 *
 * - x/y 用错常量（都乘 792）→ 手机正常，设备上画面被竖向拉伸，拉伸量还取决于手机屏幕宽度；
 * - 线宽按高度而不是宽度换算 → 差 1.5 倍；
 * - 不钳制越界坐标 → 导出的 Path 落到 Bitmap 外被 Canvas 静默裁掉，表现为设备上那一笔突然断掉。
 */
class DoodleGeometryTest {

    // ==================== 归一化（触摸 → 0..1）====================

    @Test
    fun `归一化按各自那一轴的长度算`() {
        assertEquals(0f, DoodleGeometry.normalize(0f, 400f), 0f)
        assertEquals(0.25f, DoodleGeometry.normalize(100f, 400f), 1e-6f)
        assertEquals(1f, DoodleGeometry.normalize(400f, 400f), 0f)
    }

    /**
     * 越界要钳到 [0,1]。
     *
     * 手指可以滑出画布边界（手势回调给出的 position 会是负数或超过 size 的值），
     * 不钳的话导出时会往 Bitmap 外面画 —— Canvas 静默裁掉，不报任何错。
     */
    @Test
    fun `越界坐标被钳到 0 与 1`() {
        assertEquals(0f, DoodleGeometry.normalize(-50f, 400f), 0f)
        assertEquals(1f, DoodleGeometry.normalize(999f, 400f), 0f)
    }

    /**
     * 画布长度为 0（还没测量完）时返回 0，而不是除出 NaN/Infinity。
     *
     * NaN 会一路传进 Path，画出整块空白且不抛异常 —— 现象是"这一笔没了"。
     */
    @Test
    fun `画布尺寸为零时不产生 NaN`() {
        val zero = DoodleGeometry.normalize(10f, 0f)
        assertEquals(0f, zero, 0f)
        assertTrue(!zero.isNaN())
        assertTrue(!DoodleGeometry.normalize(10f, -1f).isNaN())
    }

    // ==================== 反算（0..1 → 792×528 像素）====================

    @Test
    fun `导出尺寸就是 film 画布尺寸`() {
        assertEquals(792, DoodleGeometry.exportWidth)
        assertEquals(528, DoodleGeometry.exportHeight)
        // 与解码侧同一个来源，不是各写一份字面量
        assertEquals(FilmDecoder.WIDTH, DoodleGeometry.exportWidth)
        assertEquals(FilmDecoder.HEIGHT, DoodleGeometry.exportHeight)
    }

    /**
     * x 乘宽、y 乘高。**这条是本文件存在的主要理由** —— 两轴用同一个常量在手机上看不出来。
     */
    @Test
    fun `x 与 y 各按自己那一轴的长度反算`() {
        assertEquals(0f, DoodleGeometry.toPixel(0f, DoodleGeometry.exportWidth), 0f)
        assertEquals(792f, DoodleGeometry.toPixel(1f, DoodleGeometry.exportWidth), 0f)
        assertEquals(396f, DoodleGeometry.toPixel(0.5f, DoodleGeometry.exportWidth), 0f)

        assertEquals(528f, DoodleGeometry.toPixel(1f, DoodleGeometry.exportHeight), 0f)
        assertEquals(264f, DoodleGeometry.toPixel(0.5f, DoodleGeometry.exportHeight), 0f)

        // 中心点：若两轴共用宽度常量，y 会算成 396 而不是 264
        assertEquals(
            396f to 264f,
            DoodleGeometry.toPixel(0.5f, DoodleGeometry.exportWidth) to
                DoodleGeometry.toPixel(0.5f, DoodleGeometry.exportHeight)
        )
    }

    /**
     * 同一尺寸下 normalize 与 toPixel 互逆。
     *
     * 这保证了「屏幕上画在哪里，屏幕上就重绘在哪里」—— 显示路径与导出路径共用这两个函数，
     * 互逆性一破，跟手的笔迹就会偏离手指。
     */
    @Test
    fun `同一尺寸下归一化与反算互逆`() {
        val canvasWidth = 1080f
        listOf(0f, 1f, 137f, 539.5f, 1079f, 1080f).forEach { px ->
            val roundTrip = DoodleGeometry.toPixel(
                DoodleGeometry.normalize(px, canvasWidth),
                canvasWidth.toInt()
            )
            assertEquals("px=$px", px, roundTrip, 1e-3f)
        }
    }

    /**
     * 手机屏幕上画布中点 → 导出画布中点，与手机屏幕的实际像素尺寸无关。
     *
     * 这正是"存归一化坐标"这个设计要买到的性质：换台手机、转屏、改系统字号都不影响
     * 导出结果。
     */
    @Test
    fun `不同手机画布尺寸导出同一个位置`() {
        listOf(720f, 1080f, 1440f).forEach { screenWidth ->
            val norm = DoodleGeometry.normalize(screenWidth / 2f, screenWidth)
            assertEquals(
                "screenWidth=$screenWidth",
                396f,
                DoodleGeometry.toPixel(norm, DoodleGeometry.exportWidth),
                1e-3f
            )
        }
    }

    // ==================== 线宽 ====================

    /** 线宽**一律按宽度**换算：描边只接受一个值，按宽高分别算会得出两个。 */
    @Test
    fun `线宽按目标宽度换算`() {
        assertEquals(7.92f, DoodleGeometry.strokeWidthPx(0.01f, DoodleGeometry.exportWidth), 1e-4f)
        assertEquals(39.6f, DoodleGeometry.strokeWidthPx(0.05f, DoodleGeometry.exportWidth), 1e-3f)
    }

    /**
     * 钳到 1 像素以上。
     *
     * `Paint.strokeWidth = 0` 在 Android 上是 hairline 特殊语义（恒 1 物理像素、不参与
     * 变换），会让导出线宽与预览不一致。
     */
    @Test
    fun `线宽有 1 像素下限`() {
        assertEquals(
            DoodleGeometry.MIN_STROKE_WIDTH_PX,
            DoodleGeometry.strokeWidthPx(0f, DoodleGeometry.exportWidth),
            0f
        )
        assertEquals(
            DoodleGeometry.MIN_STROKE_WIDTH_PX,
            DoodleGeometry.strokeWidthPx(0.0001f, DoodleGeometry.exportWidth),
            0f
        )
    }

    /**
     * 屏幕上占画布 1/50 宽的笔，在 792 像素上也占 1/50。
     *
     * 这条是"笔看着一样粗"的形式化表述：比例不守恒就会出现"手机上画的粗线，设备上变成
     * 细线"，而那种偏差没有任何报错。
     */
    @Test
    fun `线宽占画布宽度的比例与目标尺寸无关`() {
        val widthNorm = 1f / 50f
        listOf(720, 1080, DoodleGeometry.exportWidth).forEach { target ->
            assertEquals(
                "target=$target",
                target / 50f,
                DoodleGeometry.strokeWidthPx(widthNorm, target),
                1e-3f
            )
        }
    }
}
