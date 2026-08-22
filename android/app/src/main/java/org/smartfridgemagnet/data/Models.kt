package org.smartfridgemagnet.data

import com.google.gson.annotations.SerializedName

/**
 * 食材数据模型
 */
data class Ingredient(
    val name: String,
    val quantity: String,         // 显示用文本，如 "3个" 或 "300g"
    val quantityAmount: Double,   // 数值
    val quantityUnit: String,     // 件数单位或 "ml"；重量单独放在 weightGrams
    val freshness: Freshness,
    val category: IngredientCategory,
    /**
     * 到期日 `YYYY-MM-DD`；null = 未设置。
     *
     * 带默认值是为了向后兼容：本地库存是 Gson 序列化进 SharedPreferences 的，旧存档里没有这两个
     * 键，反序列化后就是 null —— 那正是「未设置」的正确含义。
     *
     * [freshness] 保留而不是改成派生属性：它在没有日期时仍要能表达一个值（识别结果里模型有时会说
     * 「看起来不太新鲜」），且 data class 的 copy/equals 依赖它是个真字段。有日期时两者的口径由
     * [org.smartfridgemagnet.data.ShelfLife.freshnessOf] 统一。
     */
    val expiryDate: String? = null,
    /** 生产日期 `YYYY-MM-DD`；null = 未填。只用于推算 [expiryDate]，不参与判定。 */
    val productionDate: String? = null,
    /** 净重（克）；可与个数同时存在，菜谱计算优先使用它。 */
    val weightGrams: Double? = null,
    /** 包装标注的保存时长；与生产日期/到期日任意两个可推第三个。 */
    val shelfLifeDays: Int? = null,
    /** 日期或保存时长来自识别/条码估计，确认页需提示用户核对。 */
    val recognitionDerived: Boolean = false,
    /** 后端识别可信度；手动录入或旧存档为 null。 */
    val recognitionConfidence: String? = null
)

enum class Freshness {
    FRESH,      // 新鲜
    NORMAL,     // 7天内到期
    EXPIRING,   // 即将过期
    UNKNOWN     // 未提供保质期
}

enum class AmountMode { COUNT, WEIGHT, VOLUME }

enum class IngredientCategory(val backendName: String, val displayLabel: String) {
    VEGETABLE("蔬菜", "🥬 蔬菜"),
    FRUIT("水果", "🍎 水果"),
    MEAT("肉类", "🥩 肉类"),
    SEAFOOD("海鲜", "🐟 海鲜"),
    EGG("蛋类", "🥚 蛋类"),
    @SerializedName(value = "DAIRY", alternate = ["EGG_DAIRY"])
    DAIRY("乳制品", "🥛 乳制品"),
    SOY("豆制品", "🫘 豆制品"),
    @SerializedName(value = "STAPLE", alternate = ["GRAIN"])
    STAPLE("主食", "🍚 主食"),
    SEASONING("调味品", "🧂 调味品"),
    BEVERAGE("饮料", "🥤 饮料"),
    SNACK("零食", "🍪 零食"),
    FROZEN("冷冻食品", "🧊 冷冻食品"),
    OTHER("其他", "📦 其他")
}

/** 本地唯一分类器；后端返回的明确分类不经过这里。 */
fun classifyIngredient(name: String): IngredientCategory {
    val value = name.trim()
    fun has(vararg words: String) = words.any(value::contains)
    return when {
        has("可乐", "汽水", "果汁", "啤酒", "矿泉水", "纯净水", "饮用水", "饮料", "茶饮", "咖啡") -> IngredientCategory.BEVERAGE
        has("薯片", "饼干", "糖果", "巧克力", "瓜子", "坚果", "零食") -> IngredientCategory.SNACK
        has("冻", "冰淇淋", "速冻") -> IngredientCategory.FROZEN
        has("虾", "蟹", "鱼", "三文鱼", "贝", "海带") -> IngredientCategory.SEAFOOD
        has("猪肉", "牛肉", "羊肉", "鸡肉", "鸭肉", "排骨", "培根", "香肠", "火腿", "牛排") -> IngredientCategory.MEAT
        has("鸡蛋", "鸭蛋", "蛋") -> IngredientCategory.EGG
        has("牛奶", "酸奶", "奶酪", "芝士", "黄油", "乳酪") -> IngredientCategory.DAIRY
        has("豆腐", "豆皮", "腐竹", "豆浆", "黄豆", "豆制") -> IngredientCategory.SOY
        has("苹果", "香蕉", "橙", "梨", "桃", "葡萄", "草莓", "蓝莓", "西瓜", "柠檬", "芒果", "柿子", "水果") -> IngredientCategory.FRUIT
        has("米", "面", "粉", "燕麦", "馒头", "面包", "饺子", "花卷", "玉米", "主食") -> IngredientCategory.STAPLE
        has("酱", "盐", "糖", "醋", "油", "蜂蜜", "芝麻", "调味") -> IngredientCategory.SEASONING
        has("菜", "瓜", "萝卜", "番茄", "土豆", "葱", "姜", "蒜", "蘑菇", "笋", "藕") -> IngredientCategory.VEGETABLE
        else -> IngredientCategory.OTHER
    }
}

/** 只迁移已知旧格式，不用启发式分类器覆盖用户或后端保存的明确分类。 */
internal fun migrateStoredIngredient(ingredient: Ingredient): Ingredient {
    val legacyWeightUnit = ingredient.quantityUnit.trim().lowercase() in setOf("g", "克")
    val fixedCategory = when {
        ingredient.category == IngredientCategory.BEVERAGE && ingredient.name.contains("牛奶") ->
            IngredientCategory.DAIRY
        ingredient.category == IngredientCategory.SEASONING && ingredient.name.contains("油豆腐") ->
            IngredientCategory.SOY
        else -> ingredient.category
    }
    val fixedFreshness = if (
        ingredient.freshness == Freshness.NORMAL && ingredient.expiryDate.isNullOrBlank()
    ) Freshness.UNKNOWN else ingredient.freshness
    if (!legacyWeightUnit && fixedCategory == ingredient.category &&
        fixedFreshness == ingredient.freshness) return ingredient
    return ingredient.copy(
        quantityAmount = if (legacyWeightUnit) 0.0 else ingredient.quantityAmount,
        quantityUnit = if (legacyWeightUnit) "个" else ingredient.quantityUnit,
        weightGrams = ingredient.weightGrams
            ?: ingredient.quantityAmount.takeIf { legacyWeightUnit && it > 0 },
        category = fixedCategory,
        freshness = fixedFreshness
    )
}

/** 编辑数量时只保留用户选择的计量方式，避免多个事实来源。 */
internal fun Ingredient.withEditedAmount(
    mode: AmountMode,
    value: Double,
    unit: String = if (mode == AmountMode.VOLUME) "ml" else "个"
): Ingredient {
    require(value > 0) { "数量必须大于 0" }
    val canonicalValue = if (mode == AmountMode.VOLUME && unit == "L") value * 1000 else value
    val canonicalUnit = if (mode == AmountMode.VOLUME) "ml" else unit
    val display = when (mode) {
        AmountMode.WEIGHT -> "${value.toDisplayNumber()}g"
        AmountMode.VOLUME -> canonicalValue.toVolumeDisplay()
        AmountMode.COUNT -> "${value.toDisplayNumber()}$canonicalUnit"
    }
    return copy(
        quantity = display,
        quantityAmount = if (mode == AmountMode.WEIGHT) 0.0 else canonicalValue,
        quantityUnit = canonicalUnit,
        weightGrams = value.takeIf { mode == AmountMode.WEIGHT }
    )
}

fun Ingredient.amountMode(): AmountMode = when {
    weightGrams != null && quantityAmount <= 0 -> AmountMode.WEIGHT
    quantityUnit.equals("ml", ignoreCase = true) -> AmountMode.VOLUME
    else -> AmountMode.COUNT
}

private fun Double.toDisplayNumber(): String =
    if (this % 1.0 == 0.0) toInt().toString() else toString()

internal fun Double.toVolumeDisplay(): String = when {
    this >= 1000 -> "${(this / 1000).toDisplayNumber()}L"
    else -> "${toDisplayNumber()}ml"
}

/**
 * 家庭成员数据模型
 */
data class FamilyMember(
    val id: String,
    val name: String,
    val avatar: String,
    val role: MemberRole,
    val goal: HealthGoal,
    val preferences: DietaryPreferences
)

enum class MemberRole {
    CHILD,      // 儿童
    ADULT,      // 成人
    ELDER,      // 老人
    PREGNANT    // 孕妇
}

enum class HealthGoal {
    BALANCED,   // 均衡营养
    LOSE_FAT,   // 减脂
    GAIN_MUSCLE,// 增肌
    CONTROL_SUGAR, // 控糖
    CONTROL_SALT   // 控盐
}

data class DietaryPreferences(
    val cuisine: String = "家常",
    val spiceLevel: SpiceLevel = SpiceLevel.MILD,
    val allergies: List<String> = emptyList(),
    val dislikes: List<String> = emptyList()
)

enum class SpiceLevel {
    NONE, MILD, MEDIUM, HOT
}

/**
 * 把一行输入拆成食材列表。
 *
 * 分隔符收得宽（中英文逗号、顿号、分号、半角与全角空格）：用错的后果不是格式不好看，而是整串被
 * 当成**一个**食材名 —— 「花生、芒果」变成一条匹配不到任何真实食材的过敏源，于是过敏校验静默
 * 失效、推荐照常给出含花生的菜。
 *
 * 放在 data 层：它是纯转换逻辑，与 [FamilyMember.toProfileMemberDto] 同属「输入 → 后端字段」，
 * 且需要能被单测覆盖。
 */
fun parseFoodList(text: String): List<String> =
    text.split('、', '，', ',', '；', ';', ' ', '\u3000')
        .map { it.trim() }
        .filter { it.isNotEmpty() }
        .distinct()

/**
 * 把 App 的成员模型映射成后端家庭画像的一条记录。
 *
 * 两侧模型本来就不同：App 有头像、角色、健康目标，后端有身高体重与三类忌口。映射集中在这一个
 * 函数里，散落到调用点后某处漏映射 allergies 的表现是「推荐没避开过敏源」，而那不会报错。
 *
 * 几个刻意的取舍：
 * - `dislikes` 映射到 `dislikes_main`（主料忌口，整道菜换掉）：App 的输入提示写的是「不吃的
 *   食材」，用户填的就是主料级别的东西。
 * - 不辣（[SpiceLevel.NONE]）映射成口味忌口「辣」，后端对口味忌口的处置是「调整做法」。中辣与
 *   重辣不映射：那是偏好而非禁忌，塞进忌口会让模型反过来避开辣菜。
 * - 角色与健康目标合并进 `health_notes`：后端把它原样放进 prompt，「老年人，控盐」这种自然语言
 *   描述比任何编码都好用。
 */
fun FamilyMember.toProfileMemberDto(): org.smartfridgemagnet.network.ProfileMemberDto {
    val roleText = when (role) {
        MemberRole.CHILD -> "儿童"
        MemberRole.ADULT -> "成人"
        MemberRole.ELDER -> "老年人"
        MemberRole.PREGNANT -> "孕妇"
    }
    val goalText = when (goal) {
        HealthGoal.BALANCED -> "均衡营养"
        HealthGoal.LOSE_FAT -> "减脂中，少油少糖"
        HealthGoal.GAIN_MUSCLE -> "增肌，需要高蛋白"
        HealthGoal.CONTROL_SUGAR -> "控糖，少糖少精制碳水"
        HealthGoal.CONTROL_SALT -> "控盐，少盐少腌制"
    }
    return org.smartfridgemagnet.network.ProfileMemberDto(
        name = name.trim(),
        allergies = preferences.allergies.map { it.trim() }.filter { it.isNotEmpty() },
        dislikesMain = preferences.dislikes.map { it.trim() }.filter { it.isNotEmpty() },
        dislikesTaste = if (preferences.spiceLevel == SpiceLevel.NONE) listOf("辣") else emptyList(),
        healthNotes = "$roleText，$goalText"
    )
}

/**
 * 菜谱数据模型
 */
data class Recipe(
    val id: String,
    val name: String,
    val mealType: MealType,
    val ingredients: List<RecipeIngredient>,
    val instructions: List<String>,
    val nutrition: NutritionInfo
)

enum class MealType {
    BREAKFAST, LUNCH, DINNER, SNACK
}

data class RecipeIngredient(
    val name: String,
    val amount: String
)

data class NutritionInfo(
    val calories: Int,
    val proteinG: Float,
    val carbsG: Float,
    val fatG: Float
)

/**
 * 每日推荐结果
 */
data class DailyRecommendation(
    val date: String,
    val meals: Map<MealType, List<Recipe>>,
    val memberAdjustments: Map<String, MemberAdjustment>
)

data class MemberAdjustment(
    val memberId: String,
    val adjustments: List<String>,
    val totalCalories: Int
)
