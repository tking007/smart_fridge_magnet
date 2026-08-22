package org.smartfridgemagnet.data

import org.smartfridgemagnet.R

/**
 * 食材名 → 图标资源。
 *
 * 是**数据映射**而不是某一屏的私有细节：库存列表出现在首页、食材确认页与菜谱页，图标只有首页
 * 认得的话，同一样「番茄」会在三处显示成三种样子，而这种不一致不会报错。
 *
 * 同名不同写法（`猪肉` / `猪肉(肥廋)`、`三文鱼` / `生三文鱼`）刻意各占一项指向同一张图：识别
 * 结果与用户手输的写法都不受控，做前缀或模糊匹配反而会把「牛油果」匹配到「牛肉」上。
 */
private val FOOD_ICONS: Map<String, Int> = mapOf(
    "上海青" to R.drawable.food_000, "全脂牛奶" to R.drawable.food_001,
    "内酯豆腐" to R.drawable.food_002, "冬瓜" to R.drawable.food_003,
    "包菜" to R.drawable.food_004, "千张" to R.drawable.food_005,
    "南瓜" to R.drawable.food_006, "卷心菜" to R.drawable.food_007,
    "口蘑" to R.drawable.food_008, "圣女果" to R.drawable.food_009,
    "培根" to R.drawable.food_010, "基围虾" to R.drawable.food_011,
    "夏威夷果" to R.drawable.food_012, "大头菜" to R.drawable.food_013,
    "大葱" to R.drawable.food_014, "奶酪" to R.drawable.food_015,
    "姜" to R.drawable.food_016, "娃娃菜" to R.drawable.food_017,
    "婆婆丁" to R.drawable.food_018, "小番茄" to R.drawable.food_019,
    "小白菜" to R.drawable.food_020, "小米辣" to R.drawable.food_021,
    "山药" to R.drawable.food_022, "山葵" to R.drawable.food_023,
    "排骨" to R.drawable.food_024, "杏仁" to R.drawable.food_025,
    "柿子" to R.drawable.food_026, "核桃肉" to R.drawable.food_027,
    "榛子" to R.drawable.food_028, "橄榄油" to R.drawable.food_029,
    "毛豆" to R.drawable.food_030, "油菜" to R.drawable.food_031,
    "油豆腐" to R.drawable.food_032, "油麦菜" to R.drawable.food_033,
    "洋葱" to R.drawable.food_034, "海带" to R.drawable.food_035,
    "火腿" to R.drawable.food_036, "熟西兰花" to R.drawable.food_037,
    "牛奶" to R.drawable.food_038, "牛排" to R.drawable.food_039,
    "牛油果" to R.drawable.food_040, "牛肉" to R.drawable.food_041,
    "牛肝菌" to R.drawable.food_042, "猪肉" to R.drawable.food_043,
    "猪肉(肥廋)" to R.drawable.food_043, "瓜子" to R.drawable.food_044,
    "三文鱼" to R.drawable.food_045, "生三文鱼" to R.drawable.food_045,
    "生胡萝卜" to R.drawable.food_046, "生菜" to R.drawable.food_047,
    "番茄" to R.drawable.food_048, "白玉米" to R.drawable.food_049,
    "白糖" to R.drawable.food_050, "白菜" to R.drawable.food_051,
    "白萝卜" to R.drawable.food_052, "秋葵" to R.drawable.food_053,
    "竹笋" to R.drawable.food_054, "紫薯" to R.drawable.food_055,
    "红枣" to R.drawable.food_056, "红薯" to R.drawable.food_057,
    "红豆" to R.drawable.food_058, "胡萝卜" to R.drawable.food_059,
    "腐竹" to R.drawable.food_060, "芋头" to R.drawable.food_061,
    "芝麻酱" to R.drawable.food_062, "花卷" to R.drawable.food_063,
    "花生" to R.drawable.food_064, "花菜" to R.drawable.food_065,
    "苤蓝" to R.drawable.food_066, "苦瓜" to R.drawable.food_067,
    "苦苣" to R.drawable.food_068, "茭白" to R.drawable.food_069,
    "莲藕" to R.drawable.food_070, "莴笋" to R.drawable.food_071,
    "菜心" to R.drawable.food_072, "菠菜" to R.drawable.food_073,
    "萝卜" to R.drawable.food_074, "蒜" to R.drawable.food_075,
    "蒜苔" to R.drawable.food_076, "蒜苗" to R.drawable.food_077,
    "薄荷" to R.drawable.food_078, "蘑菇" to R.drawable.food_079,
    "虾仁" to R.drawable.food_080, "蜂蜜" to R.drawable.food_081,
    "西兰花" to R.drawable.food_082, "西芹" to R.drawable.food_083,
    "西葫芦" to R.drawable.food_084, "豆皮" to R.drawable.food_085,
    "豆腐" to R.drawable.food_086, "豆芽" to R.drawable.food_087,
    "豆角" to R.drawable.food_088, "豌豆" to R.drawable.food_089,
    "贝贝南瓜" to R.drawable.food_090, "金针菇" to R.drawable.food_091,
    "银耳" to R.drawable.food_092, "雪莲" to R.drawable.food_093,
    "青椒" to R.drawable.food_094, "青菜" to R.drawable.food_095,
    "韭菜" to R.drawable.food_096, "鲑鱼(三文鱼)" to R.drawable.food_097,
    "鲜玉米" to R.drawable.food_098, "鸡肉" to R.drawable.food_099,
    "鸡胸肉" to R.drawable.food_100, "鸡蛋" to R.drawable.food_101,
    "鸡蛋白" to R.drawable.food_102, "黄油" to R.drawable.food_103,
    "黄玉米" to R.drawable.food_104, "黄瓜" to R.drawable.food_105,
    "黄豆" to R.drawable.food_106, "黑芝麻" to R.drawable.food_107,
    "黑芝麻糊粉" to R.drawable.food_108, "黑豆" to R.drawable.food_109,
    "土豆" to R.drawable.food_110, "茄子" to R.drawable.food_111,
    "苹果" to R.drawable.food_112, "香蕉" to R.drawable.food_113,
    "橙子" to R.drawable.food_114, "梨" to R.drawable.food_115,
    "葡萄" to R.drawable.food_116, "草莓" to R.drawable.food_117,
    "西瓜" to R.drawable.food_118, "柠檬" to R.drawable.food_119,
    "桃子" to R.drawable.food_120, "蓝莓" to R.drawable.food_121,
    "大米" to R.drawable.food_122, "米饭" to R.drawable.food_123,
    "面条" to R.drawable.food_124, "面包" to R.drawable.food_125,
    "燕麦" to R.drawable.food_126, "面粉" to R.drawable.food_127,
    "酸奶" to R.drawable.food_128, "豆浆" to R.drawable.food_129,
    "羊肉" to R.drawable.food_130, "鱼" to R.drawable.food_131,
    "鸭肉" to R.drawable.food_132, "饮用水" to R.drawable.food_133,
)

private val FOOD_ICON_ALIASES = mapOf(
    "西红柿" to "番茄",
    "马铃薯" to "土豆", "洋芋" to "土豆",
    "土鸡蛋" to "鸡蛋", "鲜鸡蛋" to "鸡蛋",
    "纯牛奶" to "牛奶", "鲜牛奶" to "牛奶",
    "低脂牛奶" to "牛奶", "脱脂牛奶" to "牛奶",
    "猪里脊" to "猪肉", "五花肉" to "猪肉", "猪肉馅" to "猪肉",
    "鸡腿" to "鸡肉", "鸡翅" to "鸡肉", "鸡块" to "鸡肉",
    "牛腩" to "牛肉", "牛肉片" to "牛肉",
    "鲜虾" to "基围虾", "大虾" to "基围虾", "对虾" to "基围虾", "虾" to "基围虾",
    "香菇" to "蘑菇", "芹菜" to "西芹",
    "圆白菜" to "包菜", "甘蓝" to "包菜", "菜花" to "花菜",
    "嫩豆腐" to "豆腐", "老豆腐" to "豆腐", "北豆腐" to "豆腐",
    "小葱" to "大葱", "香葱" to "大葱", "大蒜" to "蒜", "蒜头" to "蒜",
    "红富士苹果" to "苹果", "脐橙" to "橙子", "雪梨" to "梨",
    "挂面" to "面条", "吐司" to "面包", "原味酸奶" to "酸奶",
    "羊肉片" to "羊肉", "鲫鱼" to "鱼", "鲈鱼" to "鱼", "草鱼" to "鱼",
    "鸭胸肉" to "鸭肉", "饮用纯净水" to "饮用水",
    "纯净水" to "饮用水", "矿泉水" to "饮用水"
)

private val SAFE_PREFIXES = listOf("新鲜", "冷冻", "速冻", "有机")

data class FoodIconItem(val name: String, val iconRes: Int)

/** 手动新增页展示的权威图标目录；同资源的识别别名不会重复占格。 */
val foodIconItems: List<FoodIconItem> = FOOD_ICONS.entries
    .distinctBy { it.value }
    .map { FoodIconItem(it.key, it.value) }

/** 这个食材有没有配图；null 表示没有，调用方改用新鲜度色点。 */
fun foodIconRes(name: String): Int? {
    val key = name.trim()
    FOOD_ICONS[key]?.let { return it }
    FOOD_ICON_ALIASES[key]?.let { return FOOD_ICONS[it] }
    val withoutPrefix = SAFE_PREFIXES.firstOrNull(key::startsWith)
        ?.let { key.removePrefix(it).trim() }
        ?: return null
    return FOOD_ICONS[withoutPrefix]
        ?: FOOD_ICON_ALIASES[withoutPrefix]?.let(FOOD_ICONS::get)
}
