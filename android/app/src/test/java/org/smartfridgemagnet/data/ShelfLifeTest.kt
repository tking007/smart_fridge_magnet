package org.smartfridgemagnet.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

/**
 * 保质期换算的契约测试。
 *
 * 保质期驱动两条真实行为：临期提醒，以及推荐时「优先使用快过期的食材」。算错一天只会让提醒早晚
 * 一天，而**格式写错**会让后端把整条日期当成未设置，那两条链路彻底失效且界面上看不出区别。
 *
 * 另一个必须钉住的点是新鲜度阈值要与 `InventoryItemDto.toIngredient()` 一致，否则同一批食材在
 * 「刚识别的列表」与「从后端读回的库存」里显示成不同的新鲜度。
 */
class ShelfLifeTest {

    private val today = LocalDate.of(2026, 7, 30)

    @Test
    fun `到期日按生产日期加天数算`() {
        // 有生产日期：从它起算
        assertEquals(
            "2026-08-04",
            ShelfLife.expiryFrom(LocalDate.of(2026, 7, 30), 5, today)
        )
        // 生产日期在过去：到期日也在过去，必须如实算出来（这正是"买到临期品"的情形）
        assertEquals(
            "2026-07-27",
            ShelfLife.expiryFrom(LocalDate.of(2026, 7, 20), 7, today)
        )
        // 无生产日期：按今天起算
        assertEquals("2026-08-06", ShelfLife.expiryFrom(null, 7, today))
    }

    @Test
    fun `天数被夹到合法区间`() {
        // 0 与负数会算出「今天或更早到期」，那不是用户的意思，夹到下界
        assertEquals(
            ShelfLife.expiryFrom(null, ShelfLife.MIN_DAYS, today),
            ShelfLife.expiryFrom(null, 0, today)
        )
        assertEquals(
            ShelfLife.expiryFrom(null, ShelfLife.MIN_DAYS, today),
            ShelfLife.expiryFrom(null, -100, today)
        )
        // 上界：防止一个手滑的 99999 算出几百年后的日期
        assertEquals(
            ShelfLife.expiryFrom(null, ShelfLife.MAX_DAYS, today),
            ShelfLife.expiryFrom(null, 999999, today)
        )
    }

    @Test
    fun `格式化输出恒为 ISO 日期`() {
        // 后端 fridge_inventory 的两个日期列按 YYYY-MM-DD 解析，补零不能省 ——
        // "2026-8-4" 会被后端判为非法而整条丢弃。
        assertEquals("2026-08-04", ShelfLife.format(LocalDate.of(2026, 8, 4)))
        assertTrue(ShelfLife.expiryFrom(null, 5, today).matches(Regex("""\d{4}-\d{2}-\d{2}""")))
    }

    @Test
    fun `解析非法输入返回 null 而不抛异常`() {
        // 这些串一部分来自后端、一部分来自本地旧存档；一个格式错就让库存列表崩掉，
        // 代价远大于少显示一个日期。
        listOf(null, "", "   ", "2026-8-4", "not a date", "2026-13-01", "2026-02-30").forEach {
            assertNull("输入 <$it> 应解析为 null", ShelfLife.parse(it))
        }
        assertEquals(LocalDate.of(2026, 7, 30), ShelfLife.parse(" 2026-07-30 "))
    }

    @Test
    fun `剩余天数含已过期的负值`() {
        assertEquals(5, ShelfLife.daysLeft("2026-08-04", today))
        assertEquals(0, ShelfLife.daysLeft("2026-07-30", today))
        assertEquals(-3, ShelfLife.daysLeft("2026-07-27", today))
        assertNull(ShelfLife.daysLeft(null, today))
        assertNull(ShelfLife.daysLeft("坏数据", today))
    }

    /** 阈值必须与 InventoryItemDto.toIngredient() 完全一致（≤3 临期、≤7 一般）。 */
    @Test
    fun `新鲜度阈值与后端读回口径一致`() {
        assertEquals(Freshness.EXPIRING, ShelfLife.freshnessOf("2026-07-27", today))  // -3
        assertEquals(Freshness.EXPIRING, ShelfLife.freshnessOf("2026-07-30", today))  // 0
        assertEquals(Freshness.EXPIRING, ShelfLife.freshnessOf("2026-08-02", today))  // 3
        assertEquals(Freshness.NORMAL, ShelfLife.freshnessOf("2026-08-03", today))    // 4
        assertEquals(Freshness.NORMAL, ShelfLife.freshnessOf("2026-08-06", today))    // 7
        assertEquals(Freshness.FRESH, ShelfLife.freshnessOf("2026-08-07", today))     // 8
        // 无日期不做臆测，与后端 daysLeft == null 的处理相同
        assertEquals(Freshness.UNKNOWN, ShelfLife.freshnessOf(null, today))
    }

    @Test
    fun `每个类别都有正数默认天数`() {
        // 少一个分支会让那个类别的默认值变成 0 天，进而被夹到 1 天 ——
        // 表现是「刚放进冰箱就临期」。
        IngredientCategory.entries.forEach { category ->
            val days = ShelfLife.defaultDaysFor(category)
            assertTrue("$category 的默认保质期应为正数，实际 $days", days >= ShelfLife.MIN_DAYS)
            assertTrue("$category 的默认保质期不应超过上界", days <= ShelfLife.MAX_DAYS)
        }
        // 生鲜必须短于干货，否则默认值毫无意义
        assertTrue(
            ShelfLife.defaultDaysFor(IngredientCategory.MEAT) <
                ShelfLife.defaultDaysFor(IngredientCategory.STAPLE)
        )
    }

    @Test
    fun `描述文本区分未设置、已过期与今天到期`() {
        assertEquals("未设置保质期", ShelfLife.describe(null, today))
        assertEquals("今天到期", ShelfLife.describe("2026-07-30", today))
        assertEquals("已过期 3 天", ShelfLife.describe("2026-07-27", today))
        assertTrue(ShelfLife.describe("2026-08-06", today).startsWith("还有 7 天"))
    }
}
