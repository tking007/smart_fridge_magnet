package org.smartfridgemagnet.film

import android.graphics.Bitmap

/**
 * 把 `.film` 字节解码成可显示的位图。**只读不写** —— 编码一律由后端产出。
 *
 * 这里是**解码**，不违反「渲染只有后端一份」（跨端契约）：它不产生任何要发给设备的字节，
 * 只把后端已产出的那份字节展开成像素，从而让预览显示的就是设备真正会收到的那份字节。若拿原图
 * 缩略图去画预览，用户看到彩照而设备上是六色量化后的色块，差距大到会被当成「传坏了」。
 *
 * 判断标准：**这里绝不能出现「RGB → 索引」的量化逻辑**。一旦加进一个 `rgbToIndex()`，
 * 编码器就回来了。
 *
 * 格式（跨端契约，见 docs/contracts/README.md）：32 字节文件头（这里只跳过，不解析 ——
 * 按头里的值走会让一份被改坏的头把画面画歪而无人察觉）+ 每像素 4bit 的索引数据。
 */
object FilmDecoder {

    /** 智能冰箱贴六色电子纸画布尺寸（792×528，横向）。 */
    const val WIDTH = 792
    const val HEIGHT = 528

    /** 文件头长度，像素数据紧随其后。 */
    const val HEADER_SIZE = 32

    /**
     * `.film` 定长 209120 = 32 + 792*528/2。
     *
     * 写成推导式而不是字面量：三个常量任一被改动时长度会跟着变，不会留下一个「看着对」但与
     * 实际排布不符的硬编码值。
     */
    const val FILM_SIZE = HEADER_SIZE + WIDTH * HEIGHT / 2

    /**
     * 调色板（索引 0..5 → ARGB）。
     *
     * 绿色是 `(41, 204, 20)` 而**不是纯绿**：这是后端 / 网页调试工具 / 固件三端一致的既有取值，
     * 量化结果依赖它。改成纯绿会让预览与设备实际显示的颜色不同。
     */
    private val PALETTE_ARGB = intArrayOf(
        0xFF000000.toInt(),   // 0 黑
        0xFFFFFFFF.toInt(),   // 1 白
        0xFFFFFF00.toInt(),   // 2 黄
        0xFFFF0000.toInt(),   // 3 红
        0xFF0000FF.toInt(),   // 4 蓝
        0xFF29CC14.toInt()    // 5 绿 = (41, 204, 20)
    )

    /** 越界索引的回落色（白），与后端 `index_to_rgb()` 和固件解码侧行为一致。 */
    private val FALLBACK_ARGB = PALETTE_ARGB[1]

    /**
     * 调色板的只读视图（索引 0..5 → ARGB），供 UI 侧取**笔刷颜色**用。
     *
     * 手绘涂鸦的笔刷只允许取这 6 个精确值，于是后端的六色最近色量化对它是**恒等映射**。
     * 在 UI 里另抄一份调色板会出现「App 里的绿是纯绿、设备上是 (41,204,20)」这类偏差。
     *
     * 暴露成 [List] 而不是把 [PALETTE_ARGB] 改成 internal：`IntArray` 是可变的。
     *
     * 这仍然是**解码方向**（索引 → 颜色），不是量化。
     */
    internal val PALETTE: List<Int> = PALETTE_ARGB.toList()

    /** 白色在调色板里的索引。橡皮擦本质是白色笔，导出时的底色也是它。 */
    internal const val PALETTE_INDEX_WHITE = 1

    /**
     * `.film` -> 位图；长度不对返回 null。
     *
     * 用 [Bitmap.createBitmap] 一次成图而不是逐像素 `setPixel`：后者要跑 41.8 万次
     * JNI 调用，在中端机上是秒级卡顿，而这是个预览路径。
     */
    fun toBitmap(film: ByteArray): Bitmap? {
        val pixels = toPixels(film) ?: return null
        return Bitmap.createBitmap(pixels, WIDTH, HEIGHT, Bitmap.Config.ARGB_8888)
    }

    /**
     * `.film` -> ARGB 像素数组（row-major，长度 792*528）；长度不对返回 null。
     *
     * 从 [toBitmap] 里抽出来是为了**能被 JVM 单测覆盖**：[Bitmap] 是 Android framework 类，
     * 单测里调用它只会抛 `RuntimeException: Stub!`，而真正会写错的是半字节的高低位顺序和
     * row-major 的下标算法，那一步不需要 Android。
     *
     * 长度不是 [FILM_SIZE] 就返回 null 而不是尽力解半张：输入只有一个来源（后端渲染端点的
     * 响应体），长度不对说明拿到的根本不是一份 `.film`（多半是错误页或被截断的响应）。
     */
    internal fun toPixels(film: ByteArray): IntArray? {
        if (film.size != FILM_SIZE) return null

        val pixels = IntArray(WIDTH * HEIGHT)
        var lin = 0
        // 每字节两个像素：偶数 lin 在**高 4 位**、奇数在低 4 位。写反不会报错，只会让画面按
        // 2 像素为单位左右互换。与后端 build_film() 的
        // `pixels[lin >> 1] = (high << 4) | low` 严格互逆。
        while (lin < pixels.size) {
            val packed = film[HEADER_SIZE + (lin shr 1)].toInt() and 0xFF
            pixels[lin] = argbOf(packed shr 4)          // 偶数像素：高 4 位
            pixels[lin + 1] = argbOf(packed and 0x0F)   // 奇数像素：低 4 位
            lin += 2
        }
        return pixels
    }

    /** 索引 -> ARGB。半字节能取到 0..15，>5 的部分回落白色。 */
    private fun argbOf(index: Int): Int =
        if (index < PALETTE_ARGB.size) PALETTE_ARGB[index] else FALLBACK_ARGB
}
