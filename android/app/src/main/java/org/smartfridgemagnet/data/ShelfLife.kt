package org.smartfridgemagnet.data

import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException

/**
 * 保质期的换算与默认值。
 *
 * 让用户填「保质期天数」而不是到期日：包装上印的就是「保质期 12 个月」加生产日期，要求填到期日
 * 等于让用户自己做一次日期加法，算错了没有任何地方会提示。
 *
 * 按类别给默认值是因为绝大多数入库场景（一把青菜、几个鸡蛋）没有包装也没有印刷日期。空的保质期
 * 会让临期提醒和「优先使用快过期食材」这两条链路对它完全失效。
 */
object ShelfLife {

    /** 后端与本地统一的日期格式（`YYYY-MM-DD`，对齐 `fridge_inventory` 两个日期列）。 */
    private val FORMATTER: DateTimeFormatter = DateTimeFormatter.ISO_LOCAL_DATE

    /** 保质期天数的可接受范围。上界 3650 天（10 年）足够覆盖罐头与干货。 */
    const val MIN_DAYS = 1
    const val MAX_DAYS = 3650

    /**
     * 按类别的默认保质期天数（冷藏存放）。
     *
     * 这些数字刻意偏保守（宁可提醒早一点）：提醒早了用户看一眼发现还能吃，代价是一次多余
     * 的关注；提醒晚了则是已经坏掉的东西还被推荐进菜单里。
     */
    fun defaultDaysFor(category: IngredientCategory): Int = when (category) {
        // 叶菜三五天就蔫，这里取偏短的一端
        IngredientCategory.VEGETABLE -> 5
        IngredientCategory.FRUIT -> 14
        // 冷藏生肉的安全窗口很短
        IngredientCategory.MEAT -> 3
        IngredientCategory.SEAFOOD -> 2
        IngredientCategory.EGG -> 30
        IngredientCategory.DAIRY -> 7
        IngredientCategory.SOY -> 5
        // 米面干货
        IngredientCategory.STAPLE -> 180
        IngredientCategory.SEASONING -> 365
        IngredientCategory.BEVERAGE -> 90
        IngredientCategory.SNACK -> 90
        IngredientCategory.FROZEN -> 180
        IngredientCategory.OTHER -> 7
    }

    /** 把日期格式化成 `YYYY-MM-DD`。 */
    fun format(date: LocalDate): String = date.format(FORMATTER)

    /**
     * 解析 `YYYY-MM-DD`；格式不对时返回 null 而不抛异常。
     *
     * 宽容解析是必要的：这些字符串一部分来自后端、一部分来自本地旧存档，格式错一个就让
     * 整个库存列表崩掉的代价远大于少显示一个日期。
     */
    fun parse(text: String?): LocalDate? {
        if (text.isNullOrBlank()) return null
        return try {
            LocalDate.parse(text.trim(), FORMATTER)
        } catch (_: DateTimeParseException) {
            null
        }
    }

    /**
     * 由生产日期与保质期天数算出到期日。
     *
     * @param production 生产日期；null 表示「今天买的/今天做的」，按 [today] 起算
     * @param days 保质期天数，会被夹到 [[MIN_DAYS], [MAX_DAYS]]
     */
    fun expiryFrom(production: LocalDate?, days: Int, today: LocalDate): String {
        val base = production ?: today
        val safeDays = days.coerceIn(MIN_DAYS, MAX_DAYS)
        return format(base.plusDays(safeDays.toLong()))
    }

    /**
     * 距到期还有多少天。已过期为负数，无法解析或未填时为 null。
     *
     * 与后端 `days_left` 同义，好让两边的新鲜度判定落在同一口径上。
     */
    fun daysLeft(expiryDate: String?, today: LocalDate): Int? {
        val expiry = parse(expiryDate) ?: return null
        return (expiry.toEpochDay() - today.toEpochDay()).toInt()
    }

    /**
     * 由到期日推导新鲜度。
     *
     * 阈值必须与 `InventoryItemDto.toIngredient()` 完全一致（≤3 临期、≤7 一般），否则
     * 同一批食材在「刚识别出来的列表」里和「从后端读回的库存」里会显示成不同的新鲜度，
     * 而两处看起来都没错。
     */
    fun freshnessOf(expiryDate: String?, today: LocalDate): Freshness {
        val left = daysLeft(expiryDate, today) ?: return Freshness.UNKNOWN
        return when {
            left <= 3 -> Freshness.EXPIRING
            left <= 7 -> Freshness.NORMAL
            else -> Freshness.FRESH
        }
    }

    /** 把剩余天数说成人话，供列表上的一行副文本使用。 */
    fun describe(expiryDate: String?, today: LocalDate): String {
        val left = daysLeft(expiryDate, today) ?: return "未设置保质期"
        return when {
            left < 0 -> "已过期 ${-left} 天"
            left == 0 -> "今天到期"
            else -> "还有 $left 天（$expiryDate）"
        }
    }
}
