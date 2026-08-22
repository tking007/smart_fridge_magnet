package org.smartfridgemagnet.film

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * `.film` 解码的契约测试。
 *
 * 测 [FilmDecoder.toPixels]（字节 → ARGB 数组）而不是 `toBitmap`：`Bitmap` 是 Android framework 类，
 * JVM 单测里调用它只会抛 `RuntimeException: Stub!`，而真正会写错的两处 —— 半字节的高低位顺序、
 * 越界索引的回落 —— 都不需要 Android。
 *
 * 高低位写反不会报错，只会让画面按 2 像素为单位左右互换；而这个解码器的全部意义就是「预览显示的
 * 就是设备真正会收到的那份字节」，一旦它与后端的编码侧不互逆，差异就再也不会被发现。
 */
class FilmDecoderTest {

    /** 后端 build_header() 的等价物：这里只用到"头占 32 字节"，内容不参与解码。 */
    private fun filmOf(vararg packedPixelBytes: Int): ByteArray {
        val film = ByteArray(FilmDecoder.FILM_SIZE)
        packedPixelBytes.forEachIndexed { i, b ->
            film[FilmDecoder.HEADER_SIZE + i] = b.toByte()
        }
        return film
    }

    private val BLACK = 0xFF000000.toInt()
    private val WHITE = 0xFFFFFFFF.toInt()
    private val YELLOW = 0xFFFFFF00.toInt()
    private val RED = 0xFFFF0000.toInt()
    private val BLUE = 0xFF0000FF.toInt()
    private val GREEN = 0xFF29CC14.toInt()

    @Test
    fun `定长常量与跨端契约一致`() {
        assertEquals(792, FilmDecoder.WIDTH)
        assertEquals(528, FilmDecoder.HEIGHT)
        assertEquals(32, FilmDecoder.HEADER_SIZE)
        // 209120 = 32 + 792*528/2，与后端 FILM_TOTAL_SIZE、固件 film_validate.h 同值
        assertEquals(209120, FilmDecoder.FILM_SIZE)
    }

    /**
     * 长度不对返回 null 而不是尽力解半张。
     *
     * 这里的输入只有一个来源（后端渲染端点的响应体），长度不对说明拿到的根本不是一份
     * `.film`（多半是错误页或被截断的响应），画出半张图会让人以为是渲染出了问题。
     */
    @Test
    fun `长度不是 209120 返回 null`() {
        assertNull(FilmDecoder.toPixels(ByteArray(0)))
        assertNull(FilmDecoder.toPixels(ByteArray(FilmDecoder.FILM_SIZE - 1)))
        assertNull(FilmDecoder.toPixels(ByteArray(FilmDecoder.FILM_SIZE + 1)))
        assertNull("只有文件头也不行", FilmDecoder.toPixels(ByteArray(FilmDecoder.HEADER_SIZE)))
        assertNotNull(FilmDecoder.toPixels(ByteArray(FilmDecoder.FILM_SIZE)))
    }

    /**
     * 偶数像素取**高 4 位**、奇数取低 4 位。
     *
     * 与后端 `build_film()` 的 `pixels[lin >> 1] = (high << 4) | low` 严格互逆。
     * 写反的话下面这串会解成 白/黑 红/黄 …… 即每两个像素左右互换。
     */
    @Test
    fun `半字节高低位顺序正确`() {
        // 0x01 -> 索引 0(黑) 与 1(白)；0x23 -> 2(黄) 与 3(红)；0x45 -> 4(蓝) 与 5(绿)
        val pixels = FilmDecoder.toPixels(filmOf(0x01, 0x23, 0x45))
        assertNotNull(pixels)
        assertEquals(BLACK, pixels!![0])
        assertEquals(WHITE, pixels[1])
        assertEquals(YELLOW, pixels[2])
        assertEquals(RED, pixels[3])
        assertEquals(BLUE, pixels[4])
        assertEquals(GREEN, pixels[5])
    }

    /**
     * 调色板六个颜色的 ARGB 值。
     *
     * 绿色是 `(41, 204, 20)` = `0xFF29CC14` 而**不是纯绿** —— 后端 / 网页调试工具 / 固件三端一致的
     * 既有取值，量化结果依赖它。写成纯绿会让预览与设备显示的颜色不同。
     */
    @Test
    fun `调色板六色的 ARGB 取值`() {
        // 0x0F 的低半字节 15 已越界，只用高半字节验证每个索引
        val expected = listOf(BLACK, WHITE, YELLOW, RED, BLUE, GREEN)
        expected.forEachIndexed { index, argb ->
            val pixels = FilmDecoder.toPixels(filmOf(index shl 4))!!
            assertEquals("索引 $index 的颜色", argb, pixels[0])
        }
        assertEquals(0xFF29CC14.toInt(), GREEN)
        assertEquals(41, (GREEN shr 16) and 0xFF)
        assertEquals(204, (GREEN shr 8) and 0xFF)
        assertEquals(20, GREEN and 0xFF)
    }

    /** 索引 >5 回落白色，与后端 `index_to_rgb()` 和固件解码侧行为一致。 */
    @Test
    fun `越界索引回落白色`() {
        // 0x67 -> 6 与 7；0x89 -> 8 与 9；0xFF -> 15 与 15
        val pixels = FilmDecoder.toPixels(filmOf(0x67, 0x89, 0xFF))!!
        (0..5).forEach { assertEquals("第 $it 个像素应回落白色", WHITE, pixels[it]) }
    }

    /**
     * row-major `lin = y * 792 + x`：全零字节流（索引 0）解出的整幅都是黑，
     * 且像素数恰好铺满一屏。若下标算法把宽高写反，长度对不上会在这里暴露。
     */
    @Test
    fun `像素数恰好铺满一屏且按 row-major 排布`() {
        val pixels = FilmDecoder.toPixels(ByteArray(FilmDecoder.FILM_SIZE))!!
        assertEquals(792 * 528, pixels.size)
        assertEquals(BLACK, pixels[0])
        assertEquals(BLACK, pixels[pixels.size - 1])

        // 把 (x=1, y=1) 那个像素单独染成绿：lin = 1*792 + 1 = 793（奇数 -> 低 4 位）
        val film = ByteArray(FilmDecoder.FILM_SIZE)
        film[FilmDecoder.HEADER_SIZE + (793 shr 1)] = 0x05
        val one = FilmDecoder.toPixels(film)!!
        assertEquals(GREEN, one[793])
        assertEquals("同一字节的高半字节仍是索引 0", BLACK, one[792])
        assertEquals(BLACK, one[794])
    }

    /** 文件头只跳过、不参与解码：改头里的字节不应影响任何像素。 */
    @Test
    fun `文件头不参与解码`() {
        val film = filmOf(0x45)
        val baseline = FilmDecoder.toPixels(film)!!
        for (i in 0 until FilmDecoder.HEADER_SIZE) {
            film[i] = 0x5A
        }
        val after = FilmDecoder.toPixels(film)!!
        assertEquals(baseline[0], after[0])
        assertEquals(baseline[1], after[1])
    }
}
