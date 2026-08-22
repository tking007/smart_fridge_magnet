package org.smartfridgemagnet.device

import org.smartfridgemagnet.network.MealScheduleItemDto
import org.smartfridgemagnet.transfer.BleProtocol

/**
 * 由用餐时间推算设备唤醒时刻。
 *
 * 纯计算：输入是后端给的用餐时间表与提前量，输出是一串「当地时间的分钟数」，不依赖 Compose、
 * Context 或协程，因此可由 JVM 单测覆盖。算错的后果都不会报错 —— 算偏了让设备在没人做饭的时刻
 * 醒来联网下载、算漏了让饭点前的新菜单等到下一个兜底周期才上屏、算成越界值则
 * [BleProtocol.buildWakeSchedule] 判非法直接抛，而原因只出现在 logcat 里。
 *
 * 跨零点是这里唯一真正的算术风险：`00:10 - 30min` 必须变成前一天的 `23:40`，
 * 而不是 `-20`。见 [normalizeMinute]。
 */
object WakePlanner {

    /**
     * 把 `"HH:MM"` 解析成当地分钟数。
     *
     * 格式不对返回 null 而不是 0 —— 0 是合法的 `00:00`，静默退化会把「这一餐时间没填对」
     * 变成「设备半夜两点醒一次」，而两者在 UI 上长得一样。
     */
    fun parseHhMm(text: String): Int? {
        val parts = text.trim().split(":")
        if (parts.size < 2) return null
        val hour = parts[0].trim().toIntOrNull() ?: return null
        val minute = parts[1].trim().toIntOrNull() ?: return null
        if (hour !in 0..23 || minute !in 0..59) return null
        return hour * 60 + minute
    }

    /**
     * 把可能越界的分钟数折回 `[0, 1439]`。
     *
     * **跨零点必须处理**：`00:10 - 30min = -20` 会被 [BleProtocol.buildWakeSchedule] 判为非法
     * 并抛异常（它校验 slot ∈ [0,1439]），正确语义是前一天的 23:40。
     *
     * 用双取模而不是 `if (m < 0) m + 1440`：提前量若被配成大于一天的值（后端只校验非负），
     * 单次加法补不回来。
     */
    fun normalizeMinute(minute: Int): Int {
        val day = BleProtocol.WAKE_SLOT_MAX_MINUTE + 1
        return ((minute % day) + day) % day
    }

    /** 每日更新时间对应唯一的联网唤醒点；非法时间不下发任何槽位。 */
    fun computeDailyWakeSlots(dailyPublishTime: String): List<Int> =
        listOfNotNull(parseHhMm(dailyPublishTime))

    /**
     * 由用餐时间与提前量算出唤醒点，升序去重。
     *
     * 几条刻意的取舍：
     *   - `enabled == 0` 的餐次排除：停用的那一餐不该占唤醒名额。
     *   - 时间格式非法的餐次**跳过**而不是退化成 0（见 [parseHhMm]）。
     *   - 去重：重复的唤醒点不会让设备多醒一次，只会白占 6 个名额里的一个。
     *   - 截到 [BleProtocol.WAKE_SCHEDULE_MAX_SLOTS]：不截断的话超限会在
     *     [BleProtocol.buildWakeSchedule] 里抛异常，报错点离原因很远。
     */
    fun computeWakeSlots(items: List<MealScheduleItemDto>, leadMinutes: Int): List<Int> =
        items.filter { it.enabled != 0 }
            .mapNotNull { parseHhMm(it.mealTime) }
            .map { normalizeMinute(it - leadMinutes) }
            .distinct()
            .sorted()
            .take(BleProtocol.WAKE_SCHEDULE_MAX_SLOTS)

    /**
     * 设置页给用户选的提前量档位（分钟）。
     *
     * 只给三档而不是自由输入：这个数唯一影响的是「后端算完菜单没有」和「多耗一点电」，
     * 自由输入框会让用户以为这里存在需要精调的东西。
     *
     * 默认 30 分钟与固件出厂唤醒时刻同源（见 embedded 侧 service_param.h 的
     * SFM_PARAM_DEFAULT_WAKE_SLOT_*：三餐时间 − 30 分钟 + 5 分钟余量）。
     */
    val LEAD_CHOICES = listOf(15, 30, 45)
    const val LEAD_DEFAULT = 30

    /**
     * 把任意提前量吸附到最近的一档。
     *
     * 后端存的可能是任意合法值。落在三档之外时吸附而不是显示成「三档都没选中」——
     * 那会让用户以为设置丢了。
     */
    fun nearestLeadChoice(minutes: Int): Int =
        LEAD_CHOICES.minByOrNull { kotlin.math.abs(it - minutes) } ?: LEAD_DEFAULT
}
