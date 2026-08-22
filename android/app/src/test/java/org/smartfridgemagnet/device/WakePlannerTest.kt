package org.smartfridgemagnet.device

import org.smartfridgemagnet.network.MealScheduleItemDto
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * 唤醒点推算的测试。
 *
 * 输入是后端给的用餐时间，输出直接决定设备**几点醒来联网**，而算错的三种表现都不报错：
 *   - 越界（跨零点减出负数）→ [org.smartfridgemagnet.transfer.BleProtocol.buildWakeSchedule]
 *     抛异常，「设置唤醒计划」永远失败，原因只在 logcat 里；
 *   - 静默退化成 0 → 设备半夜醒一次，纯耗电；
 *   - 算漏 → 该醒时不醒，饭点前的新菜单要等下一个兜底周期才上屏。
 *
 * 真机验证这三件事要以小时计（改后端时间 → 等饭点 → 看设备）。
 */
class WakePlannerTest {

    // ==================== parseHhMm ====================

    @Test
    fun `parseHhMm 解析正常时间`() {
        assertEquals(450, WakePlanner.parseHhMm("07:30"))
        assertEquals(0, WakePlanner.parseHhMm("00:00"))
        assertEquals(1439, WakePlanner.parseHhMm("23:59"))
        assertEquals(720, WakePlanner.parseHhMm("12:00"))
        // 不带前导零与带空白都能容纳：后端 meal_time 来自数据库列，历史数据不一定规整
        assertEquals(450, WakePlanner.parseHhMm("7:30"))
        assertEquals(450, WakePlanner.parseHhMm("  07:30  "))
    }

    /** 带秒的形式按前两段解析。后端不产出这种格式，但历史数据里出现过，宁可读对也不丢掉。 */
    @Test
    fun `parseHhMm 容忍带秒的时间串`() {
        assertEquals(450, WakePlanner.parseHhMm("7:30:00"))
    }

    /**
     * **格式错必须返回 null，绝不能是 0**。
     *
     * 0 是合法的 00:00 —— 退化成 0 会把"这一餐时间没填对"变成"设备半夜两点醒一次"，
     * 而这两种情况在 UI 上长得完全一样，没有任何一处会报错。
     */
    @Test
    fun `parseHhMm 对非法输入返回 null 而不是 0`() {
        listOf(
            "abc",       // 完全不是时间
            "",          // 空串
            "   ",       // 只有空白
            "0730",      // 缺分隔符
            "25:00",     // 小时越界
            "07:60",     // 分钟越界
            "-1:00",     // 负小时
            "07:-5",     // 负分钟
            "七:三十",    // 中文数字
            "07：30"     // 全角冒号（手输时很容易打出来）
        ).forEach { bad ->
            assertNull("\"$bad\" 应解析失败并返回 null，返回 0 会静默变成半夜唤醒", WakePlanner.parseHhMm(bad))
        }
    }

    // ==================== normalizeMinute ====================

    /**
     * 跨零点是这里唯一真正的算术风险：`00:10 - 30min = -20`，正确语义是前一天的 23:40。
     * 不折回的话会被 buildWakeSchedule 判非法直接抛。
     */
    @Test
    fun `normalizeMinute 把负数折回前一天`() {
        assertEquals("00:10 提前 30 分钟应是前一天 23:40", 1420, WakePlanner.normalizeMinute(-20))
        assertEquals(1439, WakePlanner.normalizeMinute(-1))
        assertEquals(0, WakePlanner.normalizeMinute(-1440))
        // 提前量大于一天时也必须落回合法域（后端只校验非负，没有上界）
        assertEquals(1420, WakePlanner.normalizeMinute(-20 - 1440))
    }

    @Test
    fun `normalizeMinute 把超出一天的值折回次日`() {
        assertEquals(0, WakePlanner.normalizeMinute(1440))
        assertEquals(10, WakePlanner.normalizeMinute(1450))
        assertEquals(1439, WakePlanner.normalizeMinute(1439))
    }

    @Test
    fun `normalizeMinute 不改动合法值`() {
        listOf(0, 1, 450, 720, 1080, 1439).forEach {
            assertEquals(it, WakePlanner.normalizeMinute(it))
        }
    }

    @Test
    fun `computeDailyWakeSlots 只生成每日更新时间一个槽位`() {
        assertEquals(listOf(390), WakePlanner.computeDailyWakeSlots("06:30"))
        assertEquals(listOf(0), WakePlanner.computeDailyWakeSlots("00:00"))
        assertEquals(emptyList<Int>(), WakePlanner.computeDailyWakeSlots("24:00"))
        assertEquals(emptyList<Int>(), WakePlanner.computeDailyWakeSlots("bad"))
    }

    // ==================== computeWakeSlots ====================

    @Test
    fun `computeWakeSlots 按提前量推算并升序输出`() {
        val slots = WakePlanner.computeWakeSlots(
            listOf(meal("breakfast", "07:30"), meal("lunch", "12:00"), meal("dinner", "18:30")),
            leadMinutes = 30
        )
        // 07:00 / 11:30 / 18:00
        assertEquals(listOf(420, 690, 1080), slots)
    }

    @Test
    fun `computeWakeSlots 输入乱序时输出仍升序`() {
        val slots = WakePlanner.computeWakeSlots(
            listOf(meal("dinner", "18:30"), meal("breakfast", "07:30"), meal("lunch", "12:00")),
            leadMinutes = 30
        )
        assertEquals(listOf(420, 690, 1080), slots)
    }

    /** 跨零点：这是整个文件存在的主要理由。 */
    @Test
    fun `computeWakeSlots 处理跨零点的餐次`() {
        val slots = WakePlanner.computeWakeSlots(listOf(meal("breakfast", "00:10")), leadMinutes = 30)
        assertEquals("00:10 提前 30 分钟 = 前一天 23:40 = 1420", listOf(1420), slots)
    }

    /** 重复的唤醒点不会让设备多醒一次，只会白占 6 个名额里的一个。 */
    @Test
    fun `computeWakeSlots 去掉重复时刻`() {
        val slots = WakePlanner.computeWakeSlots(
            listOf(meal("lunch", "12:00"), meal("dinner", "12:00")),
            leadMinutes = 30
        )
        assertEquals(listOf(690), slots)
    }

    @Test
    fun `computeWakeSlots 排除停用的餐次`() {
        val slots = WakePlanner.computeWakeSlots(
            listOf(
                meal("breakfast", "07:30", enabled = 0),
                meal("lunch", "12:00"),
                meal("dinner", "18:30", enabled = 0)
            ),
            leadMinutes = 30
        )
        assertEquals(listOf(690), slots)
    }

    /** 时间格式非法的餐次被**跳过**，不能变成 0 混进结果里。 */
    @Test
    fun `computeWakeSlots 跳过时间非法的餐次`() {
        val slots = WakePlanner.computeWakeSlots(
            listOf(meal("breakfast", "呃"), meal("lunch", "12:00"), meal("dinner", "")),
            leadMinutes = 30
        )
        assertEquals("非法时间应被跳过而不是变成 0（会排在最前面成为半夜唤醒）", listOf(690), slots)
    }

    @Test
    fun `computeWakeSlots 全部非法时返回空列表`() {
        val slots = WakePlanner.computeWakeSlots(
            listOf(meal("breakfast", ""), meal("lunch", "99:99")),
            leadMinutes = 30
        )
        assertEquals(emptyList<Int>(), slots)
        assertEquals(emptyList<Int>(), WakePlanner.computeWakeSlots(emptyList(), 30))
    }

    /** 超上限要在这里截断；留到 buildWakeSchedule 才抛，报错点离原因已经很远。 */
    @Test
    fun `computeWakeSlots 超过六项时截断到六项`() {
        val items = listOf(
            "06:30", "07:30", "09:00", "12:00", "15:00", "18:30", "21:00", "23:00"
        ).mapIndexed { i, time -> meal("meal$i", time) }
        assertEquals(8, items.size)

        val slots = WakePlanner.computeWakeSlots(items, leadMinutes = 0)
        assertEquals(6, slots.size)
        // 截断发生在排序之后，所以留下的是最早的 6 个
        assertEquals(listOf(390, 450, 540, 720, 900, 1110), slots)
    }

    @Test
    fun `computeWakeSlots 提前量为零时即为用餐时刻`() {
        val slots = WakePlanner.computeWakeSlots(
            listOf(meal("breakfast", "07:30"), meal("lunch", "12:00")),
            leadMinutes = 0
        )
        assertEquals(listOf(450, 720), slots)
    }

    // ── 提前量档位（Task 19：设置页只让用户填三餐时间 + 提前多久）──

    @Test
    fun `默认提前量与固件出厂唤醒时刻同源`() {
        // 固件出厂三槽 = 三餐时间 − 30 分钟 + 5 分钟余量（service_param.h）。
        // 两边不一致时，用户不改任何设置就会看到「设备醒的时间和 App 说的不一样」。
        assertEquals(30, WakePlanner.LEAD_DEFAULT)
        assertTrue(WakePlanner.LEAD_DEFAULT in WakePlanner.LEAD_CHOICES)
    }

    @Test
    fun `三档之外的提前量吸附到最近一档`() {
        // 后端存的可能是任意合法值（旧版本有自由输入控件）。落在档位之外时必须吸附，
        // 否则界面上三档都不选中，用户会以为设置丢了而重新保存一遍。
        assertEquals(15, WakePlanner.nearestLeadChoice(5))
        assertEquals(15, WakePlanner.nearestLeadChoice(20))
        assertEquals(30, WakePlanner.nearestLeadChoice(23))
        assertEquals(30, WakePlanner.nearestLeadChoice(37))
        assertEquals(45, WakePlanner.nearestLeadChoice(40))
        assertEquals(45, WakePlanner.nearestLeadChoice(240))
    }

    @Test
    fun `档位本身吸附到自己`() {
        WakePlanner.LEAD_CHOICES.forEach { choice ->
            assertEquals(choice, WakePlanner.nearestLeadChoice(choice))
        }
    }

    @Test
    fun `三餐加默认提前量算出三个槽位`() {
        val slots = WakePlanner.computeWakeSlots(
            listOf(meal("breakfast", "07:30"), meal("lunch", "12:00"), meal("dinner", "18:30")),
            WakePlanner.LEAD_DEFAULT
        )

        // 07:00 / 11:30 / 18:00
        assertEquals(listOf(7 * 60, 11 * 60 + 30, 18 * 60), slots)
    }

    private fun meal(mealType: String, time: String, enabled: Int = 1) =
        MealScheduleItemDto(mealType = mealType, label = null, mealTime = time, enabled = enabled)
}
