package org.smartfridgemagnet.network

import com.google.gson.Gson
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.annotations.SerializedName
import org.smartfridgemagnet.data.Freshness
import org.smartfridgemagnet.data.Ingredient
import org.smartfridgemagnet.data.IngredientCategory

/**
 * 后端 API 的数据传输对象（DTO）与本地模型的映射。
 *
 * 不直接复用 data/Models.kt 的原因：
 *   1) 本地模型是为 UI 设计的（quantity 是 "3个" 这种显示文本，freshness/category 是 Kotlin
 *      enum），后端用结构化字段（quantity: Double + unit: String，category 是中文字符串）。
 *   2) 本地 enum 若直接参与 Gson 序列化会按 Kotlin 常量名输出（"VEGETABLE"），而后端存的是
 *      中文分类名（"蔬菜"）—— 不建映射就静默错位：请求成功、分类却全变成「其他」。
 *   3) DTO 独立后，后端字段增减不会波及 UI 层。
 */

// ==================== 库存 ====================

/** 后端单条库存项。字段对应 fridge_inventory 表。 */
data class InventoryItemDto(
    val id: Int = 0,
    val name: String = "",
    val category: String = "",
    val quantity: Double = 0.0,
    val unit: String = "个",
    @SerializedName("weight_grams")
    val weightGrams: Double? = null,
    @SerializedName("production_date")
    val productionDate: String? = null,
    @SerializedName("shelf_life_days")
    val shelfLifeDays: Int? = null,
    @SerializedName("expiry_date")
    val expiryDate: String? = null,
    val confidence: String? = null,
    /** 距过期天数，由后端计算；无保质期时为 null。 */
    @SerializedName("days_left")
    val daysLeft: Int? = null,
    val version: Int = 1
)

/** GET /api/inventory?format=full 的响应。 */
data class InventoryListDto(
    val items: List<InventoryItemDto> = emptyList(),
    val total: Int = 0,
    val page: Int = 1,
    @SerializedName("page_size")
    val pageSize: Int = 50,
    @SerializedName("total_pages")
    val totalPages: Int = 0,
    @SerializedName("expiring_soon")
    val expiringSoon: List<InventoryItemDto> = emptyList()
)

/** POST /api/inventory 的响应。 */
data class InventoryAddResponseDto(
    val success: Boolean = false,
    @SerializedName("item_id")
    val itemId: Int = 0,
    val message: String? = null
)

/** POST /api/inventory/batch 的请求体。 */
data class InventoryBatchRequestDto(
    val items: List<InventoryItemDto>
)

/** POST /api/inventory/batch 的响应。部分失败时后端返回 400 且 success=false。 */
data class InventoryBatchResponseDto(
    val success: Boolean = false,
    val message: String? = null,
    val total: Int = 0,
    val added: Int = 0,
    val failed: Int = 0
)

// ==================== 识别 ====================

/**
 * POST /api/recognize 的响应。
 *
 * items 是本次识别到的全部食材；new_items / duplicates 是后端按名称与库存做的去重划分
 * （auto_save=false 时不会真的写库）。App 侧只用 items —— 去重在用户确认那一步做，因为本地
 * 库存才是用户实际看到的那份。
 *
 * 复用 InventoryItemDto：识别结果字段与库存项完全同形。
 */
data class RecognizeResponseDto(
    val success: Boolean = false,
    val items: List<InventoryItemDto> = emptyList(),
    @SerializedName("new_items")
    val newItems: List<InventoryItemDto> = emptyList(),
    val duplicates: List<InventoryItemDto> = emptyList(),
    val message: String? = null,
    /** 识别失败时后端才会给（图片质量不合格 / 模型异常）。 */
    val error: String? = null,
    @SerializedName("need_confirm")
    val needConfirm: Boolean = false
)

// ==================== 画面渲染与发布 ====================

/**
 * POST /api/devices/{id}/display/render 的请求体。
 *
 * kind=overview 用 title/date/dishes/expiring；kind=breakfast|lunch|dinner 用 lines。
 * 两组字段互斥，由后端按 kind 取用，因此都可空。
 */
data class RenderRequestDto(
    @SerializedName("device_id")
    val deviceId: String? = null,
    val kind: String = "overview",
    val title: String? = null,
    val date: String? = null,
    /** 形如 "午餐|红烧肉（40分钟）"，竖线前为分类标签。 */
    val dishes: List<String>? = null,
    val expiring: String? = null,
    val lines: List<String>? = null
)

/**
 * 渲染发布的响应：成功即产生一个新版本，设备下次同步就会拉到。
 *
 * 字段以真实响应核对过（success/version/kind/sha256/size/device_id）。error 仅在失败时出现。
 */
data class RenderResponseDto(
    val success: Boolean = false,
    val version: Int = 0,
    val kind: String? = null,
    val sha256: String? = null,
    /** 恒为 209120（.film 定长，见 docs/contracts/README.md），可作为一致性自检。 */
    val size: Int = 0,
    @SerializedName("device_id")
    val deviceId: String? = null,
    val error: String? = null
)

/** GET /api/devices/{id}/display/manifest 的响应（诊断用；设备自己也会拉这个）。 */
data class ManifestDto(
    val version: Int = 0,
    @SerializedName("film_url")
    val filmUrl: String? = null,
    val sha256: String? = null,
    val size: Int = 0,
    val force: Boolean = false,
    /**
     * 一版里的全部槽位（today / breakfast / lunch / dinner）。
     *
     * 默认 emptyList() 不是可有可无的兜底：后端在 manifest 序列化后超过固件上限 2048 字节时
     * 会主动丢掉 items 只留旧字段（超限时固件拒收整份 manifest，等于设备完全停止更新）。
     * 所以「没有 items」是一条正常路径。
     *
     * 旧字段 film_url/size/sha256 同时保留，指向 items 里的主图（总览优先），让只解析这三个
     * 字段的旧固件继续按单张工作。
     */
    val items: List<ManifestItemDto> = emptyList()
)

/** manifest 里的一个槽位。name 是设备落盘用的文件名，固件按文件名决定按时段切图。 */
data class ManifestItemDto(
    val slot: String = "",
    val name: String = "",
    val url: String? = null,
    val size: Int = 0,
    val sha256: String? = null
)

/** 后端 /api/health 的精简视图，仅取联调需要的部分。 */
data class HealthDto(
    val status: String? = null,
    val checks: Map<String, Any>? = null
)

/**
 * 后端失败响应的公共形状：`{"success": false, "error": "...", "code": 1001}`。
 *
 * 单独立一个是为了那些**正常响应不是 JSON** 的接口（如 /api/render/preview 返回 PNG）：
 * 它们失败时才吐 JSON，没有别的 DTO 能顺带承接。
 */
data class ApiErrorDto(
    val success: Boolean = false,
    val error: String? = null,
    val code: Int? = null
)

// ==================== 菜谱方案 ====================
//
// 下面这组 DTO 有一个共同约定：**每个字段都必须带默认值**。
// Gson 反序列化 Kotlin data class 时，只有在全部构造参数都有默认值、编译器合成出无参构造器的
// 情况下才会走它并保留默认值；否则 Gson 用 Unsafe 直接分配对象，声明成非空的 List 会实打实地
// 拿到 null，遍历时才 NPE 且崩在离解析很远的地方。加字段时给默认值是正确性要求，不是风格。

/** 方案里一道菜的一味配料。amount 是 "500g" 这类显示文本，不做结构化拆分。 */
data class PlanIngredientDto(
    val name: String = "",
    val amount: String = ""
)

/** 方案里的一道菜。除 name/ingredients 外都由模型选填，故一律可空。 */
data class PlanDishDto(
    val name: String = "",
    val ingredients: List<PlanIngredientDto> = emptyList(),
    @SerializedName("cooking_time")
    val cookingTime: String? = null,
    val difficulty: String? = null,
    @SerializedName("nutrition_highlight")
    val nutritionHighlight: String? = null,
    val steps: List<String> = emptyList(),
    /** 按家庭画像里的忌口需要省掉的配料。 */
    @SerializedName("skip_ingredients")
    val skipIngredients: List<String> = emptyList(),
    @SerializedName("skip_reason")
    val skipReason: String? = null
)

/** 一份完整方案（A 或 B）。 */
data class MealPlanDto(
    val dishes: List<PlanDishDto> = emptyList(),
    @SerializedName("total_time")
    val totalTime: String? = null,
    @SerializedName("total_difficulty")
    val totalDifficulty: String? = null
)

/**
 * GET /api/recommend/latest 的响应：读库里**已经生成好**的最近一份方案，不打模型。
 *
 * 库里一份都没有时后端用 **404 + success:false + code:4004** 表达，这是正常业务态而非错误 ——
 * code 必须留在 DTO 里，调用方据此区分「还没生成」与「真出错」。
 *
 * plan_a / plan_b 可空：模型只给出一份方案时另一份是 null。
 * age_minutes 可空：created_at 解析不出来时后端不敢断言方案有多旧。
 */
data class RecommendLatestDto(
    val success: Boolean = false,
    @SerializedName("plan_id")
    val planId: Int = 0,
    @SerializedName("meal_type")
    val mealType: String? = null,
    @SerializedName("meal_label")
    val mealLabel: String? = null,
    @SerializedName("people_count")
    val peopleCount: Int? = null,
    @SerializedName("created_at")
    val createdAt: String? = null,
    /** 用户已选过哪一份（"A" / "B"）；未选时为 null。 */
    @SerializedName("selected_plan")
    val selectedPlan: String? = null,
    @SerializedName("age_minutes")
    val ageMinutes: Int? = null,
    /** 后端按 12 小时判定；true 时 UI 应提示「可能不是今天的」，但仍照常展示。 */
    val stale: Boolean = false,
    @SerializedName("plan_a")
    val planA: MealPlanDto? = null,
    @SerializedName("plan_b")
    val planB: MealPlanDto? = null,
    /**
     * 每套方案里**不在冰箱库存**、也不属于基础调料的食材，形如
     * `{"plan_a": ["香菇"], "plan_b": []}`。
     *
     * 后端在每次读取时按当前库存重算（不随方案落库），所以它反映的是此刻的冰箱实况。
     * 需要显示出来：prompt 里那条「主料只能来自库存」是软约束，模型可以无视。
     */
    @SerializedName("missing_ingredients")
    val missingIngredients: Map<String, List<String>>? = null,
    val error: String? = null,
    val code: Int? = null
)

/**
 * 取**当前展示的那一套**方案的缺料清单。
 *
 * 口径必须与 [dishNames] 一致（A 优先、A 为空回落 B）：屏幕上列的是 A 的菜、缺料却报 B 的，
 * 用户会照着买一堆用不上的东西，而两边都不报错。故不提供「按 key 取」的公开入口。
 *
 * 返回 **null 表示后端没算过这套方案**（旧版本后端不下发这个字段），空列表才是「算过、不缺」。
 * 都返回空列表会把「没数据」显示成「三餐都能用现有食材做」。
 */
fun RecommendLatestDto.missingForShownPlan(): List<String>? {
    val map = missingIngredients ?: return null
    val key = if (planA != null) "plan_a" else "plan_b"
    // 缺这个 key 同样算「没算过」：后端只为实际存在 dishes 的方案产出条目。
    return map[key]
}

/** POST /api/recommend 成功分支里的 data。失败分支没有这一段。 */
data class RecommendDataDto(
    @SerializedName("plan_id")
    val planId: Int = 0,
    @SerializedName("meal_type")
    val mealType: String? = null,
    @SerializedName("people_count")
    val peopleCount: Int? = null,
    @SerializedName("plan_a")
    val planA: MealPlanDto? = null,
    @SerializedName("plan_b")
    val planB: MealPlanDto? = null
)


// ==================== 对话 ====================

/**
 * POST /api/chat 返回的 action 取值全集（核对自 backend/src/agent.py 的各个 handler）。
 *
 * 只有 [RECOMMEND] 会产生**新方案并落库**，因而只有它能被 `GET /api/recommend/latest` 与餐前
 * 下发读到。其余取值都只是一段说明文字。
 *
 * 走本地快路径时（见 [ChatResponseDto]）一个 action 都不会有，判分支前要先接受 action 为 null。
 */
object ChatAction {
    /** 生成成功且已落库，data 是方案。 */
    const val RECOMMEND = "recommend"

    /** 后端要人数（家庭成员为空且话里没数字），没生成。 */
    const val ASK_PEOPLE_COUNT = "ask_people_count"

    /** 说了「确认」但没说选 A 还是 B。 */
    const val ASK_SELECTION = "ask_selection"

    /** 库存是空的，没法配菜。 */
    const val INVENTORY_EMPTY = "inventory_empty"

    /** 所有候选菜都触发过敏约束，没生成方案。 */
    const val RECOMMEND_ALLERGEN_BLOCKED = "recommend_allergen_blocked"

    /** 所有候选菜都超出库存约束，没生成方案。 */
    const val RECOMMEND_INVENTORY_BLOCKED = "recommend_inventory_blocked"

    /** 库存清单查询结果，**data 是数组**。 */
    const val INVENTORY_QUERY = "inventory_query"

    const val RECOMMEND_ERROR = "recommend_error"
    const val PROFILE_UPDATE = "profile_update"
    const val PROFILE_ERROR = "profile_error"

    /** 已选定方案并扣减库存，**data 是数组**。 */
    const val CONFIRM = "confirm"
    const val CONFIRM_ERROR = "confirm_error"
    const val DEDUCT_ERROR = "deduct_error"
    const val NO_PENDING_PLAN = "no_pending_plan"
    const val HELP = "help"

    /** 意图判成闲聊，回复由模型自由生成。 */
    const val CHAT = "chat"
}

/**
 * POST /api/chat 的响应。
 *
 * 这个接口有**两种响应形状**，取决于后端有没有走本地快路径
 * （backend/src/app.py 里先问 intent_parser.is_simple_query）：
 *
 *   本地命中：`{"handled_locally": true, "intent": {...}, "response": "文本", "results": [...]}`
 *             —— 回复文本在 `response` 里，没有 action，也不打模型（毫秒级）。
 *   走 AI  ：`{"handled_locally": false, "reply": "文本", "action": "...", "data": ...}`
 *             —— 回复文本在 `reply` 里。
 *
 * 取文本一律用 [replyText]，不要在 UI 里自己挑字段 —— 挑错的那一半路径会显示成空气泡。
 *
 * `data` 声明成 [JsonElement] 而不是具体类型：它的形状**随 action 变** —— action=recommend
 * 时是对象（方案），action=inventory_query / confirm 时是数组（食材列表、扣减结果）。声明成
 * 对象类型会让库存查询那条路径抛 JsonSyntaxException。真正的解析交给 [recommendPlan]。
 */
data class ChatResponseDto(
    val reply: String? = null,
    val response: String? = null,
    val action: String? = null,
    @SerializedName("handled_locally")
    val handledLocally: Boolean = false,
    val data: JsonElement? = null,
    /** 只在 400（请求里缺 message）时出现。 */
    val error: String? = null
)

/**
 * 取这条回复的文本，兼容两种响应形状。
 *
 * 返回空串是**一条真实路径**：本地快路径在 `parsed["type"] == "inventory"` 且过滤条件不是
 * 「即将过期」时压根不往 `response` 里写东西，后端返回的就是空串。UI 必须自己准备替代文案。
 */
fun ChatResponseDto.replyText(): String =
    reply?.takeIf { it.isNotBlank() } ?: response?.takeIf { it.isNotBlank() } ?: ""

/** 解析 data 用的 Gson。Gson 无状态且线程安全，共用一个实例即可。 */
private val chatDataGson = Gson()

/**
 * 只在这条回复确实产生了新方案时把 data 解析成方案，否则返回 null。
 *
 * 先卡 action 再卡 JsonObject 是两道独立的保险：action 保证语义（只有 recommend 才落库），
 * JsonObject 保证形状（inventory_query / confirm 的 data 是数组，硬解会抛异常）。
 */
fun ChatResponseDto.recommendPlan(): RecommendDataDto? {
    if (action != ChatAction.RECOMMEND) return null
    val obj = data as? JsonObject ?: return null
    return runCatching { chatDataGson.fromJson(obj, RecommendDataDto::class.java) }.getOrNull()
}

// ==================== 用餐时间表 ====================

/**
 * GET / PUT /api/meal-schedule 的响应（两者同结构，PUT 的返回值可直接刷 UI）。
 *
 * schedule 长度是 0~3：未配置过的餐次不会出现在数组里，不能按下标当作早/午/晚。
 *
 * success / error / code 只在 PUT 校验失败（400）时出现，成功响应里没有 success 字段 ——
 * 因此判失败要用 `success == false || error != null`，不能用 `!success`。
 */
data class MealScheduleDto(
    val schedule: List<MealScheduleItemDto> = emptyList(),
    /** 后端最终采用的发布时间，仅供展示与兼容旧客户端。 */
    @SerializedName("daily_publish_time")
    val dailyPublishTime: String = "06:30",
    /** 设备在饭点前取图的提前量。 */
    @SerializedName("lead_minutes")
    val leadMinutes: Int = 0,
    @SerializedName("auto_publish")
    val autoPublish: Boolean = false,
    /** `lead_minutes` 的合法下界 / 上界。 */
    @SerializedName("lead_minutes_min")
    val leadMinutesMin: Int = 5,
    @SerializedName("lead_minutes_max")
    val leadMinutesMax: Int = 240,
    val success: Boolean? = null,
    val error: String? = null,
    val code: Int? = null
)

/**
 * 时间表里的一项。
 *
 * enabled 是 **int 0/1** 而不是 boolean（后端直出数据库列），声明成 Boolean 会让
 * Gson 解析失败。目前没有任何入口能把某一餐停用，所以它实际恒为 1。
 */
data class MealScheduleItemDto(
    @SerializedName("meal_type")
    val mealType: String = "",
    val label: String? = null,
    @SerializedName("meal_time")
    val mealTime: String = "",
    val enabled: Int = 1
)

/**
 * PUT /api/meal-schedule 的请求体。各项都可选，但**至少要给一个**（后端对空请求体退 400）。
 *
 * 后端是整体替换且原子的：任一项不合法则一个字节都不写。
 *
 * 四个字段都可空并默认 null，是为了借 Gson **默认不序列化 null 字段**的行为表达「这一项不改」。
 * 后端用 `'key' in data` 判断给没给，显式发 `"auto_publish": null` 会被当成「给了一个非布尔值」
 * 而整体退 400。
 *
 * 反过来，只改每日更新时间时也不该顺手回传整份时间表：那份数据可能是几分钟前拉的。
 */
data class MealSchedulePutDto(
    val schedule: List<MealScheduleTimeDto>? = null,
    @SerializedName("daily_publish_time")
    val dailyPublishTime: String? = null,
    @SerializedName("lead_minutes")
    val leadMinutes: Int? = null,
    @SerializedName("auto_publish")
    val autoPublish: Boolean? = null
)

/** PUT 时只发这两个字段：label 由后端按 meal_type 给，enabled 后端不接受修改。 */
data class MealScheduleTimeDto(
    @SerializedName("meal_type")
    val mealType: String = "",
    @SerializedName("meal_time")
    val mealTime: String = ""
)

// ==================== 版式与设备偏好 ====================

/** GET /api/render/layouts 的响应。 */
data class LayoutListDto(
    val layouts: List<LayoutChoiceDto> = emptyList(),
    @SerializedName("option_choices")
    val optionChoices: LayoutOptionChoicesDto = LayoutOptionChoicesDto(),
    @SerializedName("option_defaults")
    val optionDefaults: Map<String, String> = emptyMap()
)

data class LayoutOptionValueDto(
    val key: String = "",
    val label: String = ""
)

data class LayoutOptionChoicesDto(
    val accent: List<LayoutOptionValueDto> = emptyList(),
    @SerializedName("font_size")
    val fontSize: List<LayoutOptionValueDto> = emptyList(),
    val density: List<LayoutOptionValueDto> = emptyList(),
    val separator: List<LayoutOptionValueDto> = emptyList()
)

/**
 * 一个可选版式。orientation 只有 landscape / portrait 两种取值。
 *
 * portrait 表示设备使用时需要旋转 90 度；预览端点可用 natural=1 返回自然竖向 PNG。
 */
data class LayoutChoiceDto(
    val key: String = "",
    val label: String = "",
    val description: String? = null,
    val orientation: String = "landscape"
)

/**
 * GET / PUT /api/devices/{id}/prefs 的响应。
 *
 * layout_choices 与版式清单接口同源，设备详情页一次往返就能把选择器画完整。
 *
 * success 只在 PUT 成功时出现（GET 没有这个字段），error / code 只在 400 时出现，两者都不能
 * 当作「必有」来判定。
 */
data class DevicePrefsDto(
    @SerializedName("device_id")
    val deviceId: String = "",
    val layout: String = "",
    @SerializedName("layout_options")
    val layoutOptions: Map<String, String> = emptyMap(),
    @SerializedName("layout_choices")
    val layoutChoices: List<LayoutChoiceDto> = emptyList(),
    @SerializedName("layout_option_choices")
    val layoutOptionChoices: LayoutOptionChoicesDto = LayoutOptionChoicesDto(),
    @SerializedName("display_updated")
    val displayUpdated: Boolean = false,
    @SerializedName("display_update_detail")
    val displayUpdateDetail: String? = null,
    @SerializedName("preferences_saved")
    val preferencesSaved: Boolean? = null,
    val success: Boolean? = null,
    val error: String? = null,
    val code: Int? = null
)

// ==================== 同步状态 ====================

/**
 * GET /api/devices/{id}/sync-status 的响应。
 *
 * 路径**不在 /display/ 下**，与 manifest / render 那一组不同级。
 *
 * 所有时间字段都按字符串处理，不解析成 Date：后端这些值格式不统一，UTC 的
 * "Y-m-d H:M:S"（SQLite CURRENT_TIMESTAMP）与带微秒的本地 ISO 并存，用单一 pattern 解析
 * 必然在其中一种上抛异常，而这些值只是给人看的。
 */
data class SyncStatusDto(
    @SerializedName("device_id")
    val deviceId: String = "",
    /** 从未发布过画面时为 0。 */
    @SerializedName("latest_version")
    val latestVersion: Int = 0,
    @SerializedName("latest_upload_time")
    val latestUploadTime: String? = null,
    /** 指向该版本的主图（总览优先）。 */
    @SerializedName("latest_sha256")
    val latestSha256: String? = null,
    /** 这一版包含哪些槽位。只有 today 一项说明是单张发布，不会按时段自动切图。 */
    val slots: List<String> = emptyList(),
    /**
     * 这一版每个槽位对应的**文件名**，用于请求预览图。
     *
     * 文件名由后端交出而不是 App 自己拼：`1_today.film` 这套命名是跨端契约
     * （见 docs/contracts/README.md），App 再抄一份就是第三处副本。
     */
    @SerializedName("slot_files")
    val slotFiles: List<SlotFileDto> = emptyList(),
    @SerializedName("last_manifest_query")
    val lastManifestQuery: String? = null,
    /**
     * 后端有没有登记这台设备的 `device_token`。
     *
     * **false 意味着整条下发链路是断的**：设备每次拉 manifest 都会被 401 挡住，而 App 这边
     * 看到的是「发布成功、版本号自增、同步状态正常」，屏幕却停在旧画面。这个字段是 App 能
     * 自己发现这件事的唯一途径，拿到 false 时必须显著提示并给出登记入口。
     */
    @SerializedName("device_token_registered")
    val deviceTokenRegistered: Boolean = true,
    /**
     * 这台设备最近一次联系后端距今多少分钟；从未联系过时为 null。
     *
     * 由**后端计算**，App 不自己解析时间串：库里 UTC 的 `Y-m-d H:M:S` 与本地 ISO 并存，
     * 客户端用单一 pattern 必然在其中一种上错 8 小时，而这个数字要驱动「设备是否在线」。
     *
     * 它包含被 401 拒绝的请求：被拒绝的设备同样证明它联网了，而那正是最需要这个结论的场景。
     */
    @SerializedName("device_last_contact_minutes")
    val deviceLastContactMinutes: Int? = null,
    @SerializedName("recent_events")
    val recentEvents: List<SyncEventDto> = emptyList(),
    @SerializedName("device_status")
    val deviceStatus: DeviceReportedStatusDto? = null
)

/** 一个槽位与它在设备上的文件名。 */
data class SlotFileDto(
    val slot: String = "",
    val name: String = ""
)

/** 一条设备同步日志（manifest_query / download / apply / auth_failed 等）。 */
data class SyncEventDto(
    val event: String = "",
    val version: Int? = null,
    val detail: String? = null,
    val time: String? = null
)

/**
 * 设备自报状态。**全部字段都可能为 null** —— 设备从未上报过时后端就是这么返回的。
 *
 * 有了它，看电量不必去连蓝牙（设备平时深睡、并不持续广播）。
 */
data class DeviceReportedStatusDto(
    @SerializedName("battery_level")
    val batteryLevel: Int? = null,
    @SerializedName("firmware_version")
    val firmwareVersion: String? = null,
    @SerializedName("status_reported_at")
    val statusReportedAt: String? = null,
    @SerializedName("last_seen")
    val lastSeen: String? = null,
    @SerializedName("applied_version")
    val appliedVersion: Int? = null,
    @SerializedName("sleep_mode")
    val sleepMode: Boolean? = null,
    @SerializedName("wake_slots")
    val wakeSlots: List<Int>? = null,
    @SerializedName("wake_fallback_minutes")
    val wakeFallbackMinutes: Int? = null,
    @SerializedName("poll_interval_seconds")
    val pollIntervalSeconds: Int? = null,
    @SerializedName("status_age_minutes")
    val statusAgeMinutes: Int? = null
)

/**
 * POST /api/scheduler/trigger/publish 的响应。
 *
 * 两处与直觉相反、必须按字段判而不能按状态码判：
 *   1) `success:true` + `skipped:true` 表示「该餐次近期已下发过，按幂等规则跳过」，
 *      这不是失败，detail 里有给人看的说明。
 *   2) `success:false` 走的是 **HTTP 500**，连「餐次名拼错」这种参数问题也是 500。
 */
data class PublishTriggerDto(
    val success: Boolean = false,
    /** 归一化后的餐次键（breakfast / lunch / dinner）。 */
    val meal: String? = null,
    /** 本次下发的目标设备编号。 */
    val devices: List<String> = emptyList(),
    /** 每台设备的下发结果描述，仅成功且未跳过时出现。 */
    val published: List<String> = emptyList(),
    val skipped: Boolean = false,
    val detail: String? = null,
    val error: String? = null
)

// ==================== 映射 ====================

/**
 * 后端允许的分类**封闭集合**（GET /api/inventory/categories 的 13 项）。
 *
 * 后端对分类做严格校验，未知值直接返回 400（错误码 3004），而批量新增是原子的 —— 一条
 * 分类不合法会让整批上传失败（表现为「单条能加、多条一起就全失败」）。toBackendCategory()
 * 据此把任何拼错或将来新增的本地分类退化成「其他」。
 */
private val BACKEND_VALID_CATEGORIES = IngredientCategory.entries.map { it.backendName }.toSet()

/**
 * 本地分类 -> 后端分类名。
 *
 * 本地分类比后端粗：EGG_DAIRY 一个枚举对应后端的「蛋类」与「乳制品」两项，无法无损映射，
 * 这里取「蛋类」。修法是给本地 IngredientCategory 拆分枚举，属独立改动，不在 DTO 层伪造精度。
 */
private val CATEGORY_ALIASES = mapOf(
    "水产" to "海鲜", "奶制品" to "乳制品", "蛋奶" to "蛋类",
    "谷物" to "主食", "米面" to "主食", "调料" to "调味品", "冷冻" to "冷冻食品"
)

/** 转后端分类，并保证结果一定在后端的合法集合内。 */
fun IngredientCategory.toBackendCategory(): String {
    val mapped = backendName
    return if (mapped in BACKEND_VALID_CATEGORIES) mapped else "其他"
}

fun String?.toIngredientCategory(): IngredientCategory {
    val normalized = CATEGORY_ALIASES[this?.trim()] ?: this?.trim()
    return IngredientCategory.entries.firstOrNull { it.backendName == normalized }
        ?: IngredientCategory.OTHER
}

/**
 * 后端库存项 -> 本地食材。
 *
 * 新鲜度由 days_left 推导而非另存一个字段：后端只有日期、没有「新鲜度」概念，让它成为展示期
 * 的派生值就不会出现「日期已过但仍显示新鲜」。阈值 3 天与后端 expiring_soon 判定一致
 * （app.py 里 days_left <= 3）。
 */
fun InventoryItemDto.toIngredient(recognitionDerived: Boolean = false): Ingredient {
    val freshness = when {
        daysLeft == null -> Freshness.UNKNOWN
        daysLeft <= 3 -> Freshness.EXPIRING
        daysLeft <= 7 -> Freshness.NORMAL
        else -> Freshness.FRESH
    }
    // 数量文本按整数显示：库存都是「3个」「500g」这类，显示 3.0 个反而奇怪。
    val normalizedUnit = unit.trim().lowercase()
    val legacyWeightUnit = normalizedUnit in setOf("g", "克", "kg", "千克", "公斤")
    val volumeMl = when (normalizedUnit) {
        "l", "升" -> quantity * 1000
        "ml", "毫升" -> quantity
        else -> null
    }
    val countAmount = when {
        legacyWeightUnit -> 0.0
        volumeMl != null -> volumeMl
        else -> quantity
    }
    val effectiveWeight = weightGrams ?: when (normalizedUnit) {
        "kg", "千克", "公斤" -> quantity.takeIf { it > 0 }?.times(1000)
        "g", "克" -> quantity.takeIf { it > 0 }
        else -> null
    }
    val amountText = if (countAmount % 1.0 == 0.0) countAmount.toInt().toString() else countAmount.toString()
    val quantityText = when {
        volumeMl != null && volumeMl >= 1000 -> "${(volumeMl / 1000).toDisplayNumber()}L"
        volumeMl != null -> "${volumeMl.toDisplayNumber()}ml"
        countAmount > 0 && effectiveWeight != null -> "$amountText$unit · ${effectiveWeight.toDisplayNumber()}g"
        countAmount > 0 -> "$amountText$unit"
        effectiveWeight != null -> "${effectiveWeight.toDisplayNumber()}g"
        else -> "0$unit"
    }
    return Ingredient(
        name = name,
        quantity = quantityText,
        quantityAmount = countAmount,
        quantityUnit = when {
            legacyWeightUnit -> "个"
            volumeMl != null -> "ml"
            else -> unit
        },
        freshness = freshness,
        category = category.toIngredientCategory(),
        // 日期原样带回来，别只留派生的 freshness：库存页要显示「还有几天」，
        // 而从 freshness 反推日期不可能（三档对应区间，不是具体某天）。
        expiryDate = expiryDate,
        productionDate = productionDate,
        weightGrams = effectiveWeight,
        shelfLifeDays = shelfLifeDays,
        recognitionDerived = recognitionDerived,
        recognitionConfidence = confidence?.takeIf { it in setOf("高", "中", "低") }
    )
}

/**
 * 本地食材 -> 后端库存项。
 *
 * id 恒为 0（新增由后端分配）。
 *
 * 日期只在**用户真的填过**时才上传，绝不从 freshness 反推：反推出的日期会驱动临期提醒与
 * 「优先使用快过期食材」两条链路。留空则后端明确按「无保质期」处理。
 */
fun Ingredient.toInventoryItemDto(): InventoryItemDto = InventoryItemDto(
    name = name,
    category = category.toBackendCategory(),
    quantity = quantityAmount,
    unit = quantityUnit,
    weightGrams = weightGrams,
    productionDate = productionDate?.takeIf { it.isNotBlank() },
    shelfLifeDays = shelfLifeDays,
    expiryDate = expiryDate?.takeIf { it.isNotBlank() },
    confidence = recognitionConfidence
)

private fun Double.toDisplayNumber(): String = if (this % 1.0 == 0.0) toInt().toString() else toString()
/**
 * 屏幕上展示的是哪一套方案：A 优先，A 缺失时回落 B。
 *
 * 抽出来给 [dishNames] 与 [dishDetails] 共用：两处对「这份方案里有哪几道菜」的答案必须同源，
 * 否则点开某道菜看到的是另一道菜的步骤，而没有任何一处会报错。
 */
private fun RecommendLatestDto.shownPlan(): MealPlanDto? = planA ?: planB

/**
 * 取一份方案里的菜名列表。plan_a 为空时回落 plan_b。
 *
 * 只取 name，**绝不把 cooking_time 拼进去**：菜名是 UI 里查做法的索引键（见 [dishDetails]），
 * 带上时长就对不上了。要展示时长单独取 cookingTime。
 *
 * 用 orEmpty() 而不是直接遍历：字段虽声明为非空并带默认值，但后端存的是模型原样输出的 JSON，
 * 显式 null 会被 Gson 塞进非空字段（见本文件「菜谱方案」一节）。
 */
fun RecommendLatestDto.dishNames(): List<String> {
    val plan = shownPlan() ?: return emptyList()
    return plan.dishes.orEmpty()
        .map { it.name.orEmpty().trim() }
        .filter { it.isNotEmpty() }
}

/**
 * 菜名 → 做法，取自与 [dishNames] **同一套方案**。
 *
 * 做法页据此直接展示步骤，不再另打一次模型：后端生成菜单时已把 steps/ingredients 一起下发。
 *
 * 同名菜只保留第一道，与列表顺序一致。
 */
fun RecommendLatestDto.dishDetails(): Map<String, PlanDishDto> {
    val plan = shownPlan() ?: return emptyMap()
    val details = LinkedHashMap<String, PlanDishDto>()
    plan.dishes.orEmpty().forEach { dish ->
        val name = dish.name.orEmpty().trim()
        if (name.isNotEmpty()) details.putIfAbsent(name, dish)
    }
    return details
}

/**
 * 取对话刚生成的那份方案里的菜名。口径与上面那个重载**必须一致**（A 优先、只取 name）：
 * 对话页做摘要、菜谱页做列表，两处答案不一致会让人以为下发的和刚看到的不是同一份。
 */
fun RecommendDataDto.dishNames(): List<String> {
    val plan = planA ?: planB ?: return emptyList()
    return plan.dishes.orEmpty()
        .map { it.name.orEmpty().trim() }
        .filter { it.isNotEmpty() }
}

// ==================== 语音识别 ====================

/**
 * `POST /api/asr` 的响应。
 *
 * @param empty 后端明确告诉我们「识别出的是空串」。这不是错误：按住又立刻松手、或全程静音
 *              都会走到这里，后端回 200。客户端据此显示「没听到内容，再说一次」而不是失败提示。
 *              以后端这一个字段为准，不要自己判 `text.isEmpty()`（会有两处口径）。
 * @param durationMs 服务端花在识别上的耗时（不含上传）。只用于日志与排查。
 */
data class AsrResponseDto(
    @SerializedName("success") val success: Boolean = false,
    @SerializedName("text") val text: String = "",
    @SerializedName("empty") val empty: Boolean = false,
    @SerializedName("language") val language: String = "",
    @SerializedName("duration_ms") val durationMs: Int = 0,
    @SerializedName("error") val error: String? = null
)

// ==================== 家庭画像同步 ====================
/**
 * `PUT /api/profile/members` 的一位成员。
 *
 * 字段名对齐后端 `family_profile` 表，而不是 App 的 [org.smartfridgemagnet.data.FamilyMember]
 * —— 两侧的模型本来就不同（App 有头像和角色，后端有身高体重），映射在
 * [org.smartfridgemagnet.data.toProfileMemberDto] 里一处完成。
 *
 * 忌口分成三个字段而不是一个，是因为后端 prompt 对它们的处置不同：主料忌口整道菜换掉、
 * 配料忌口保留菜但标注去掉、口味忌口调整做法。
 */
data class ProfileMemberDto(
    @SerializedName("name") val name: String,
    /** 过敏源。后端会把它汇总置顶到 prompt 最前，并在生成后校验、命中就重试。 */
    @SerializedName("allergies") val allergies: List<String> = emptyList(),
    @SerializedName("dislikes_main") val dislikesMain: List<String> = emptyList(),
    @SerializedName("dislikes_ingredient") val dislikesIngredient: List<String> = emptyList(),
    @SerializedName("dislikes_taste") val dislikesTaste: List<String> = emptyList(),
    @SerializedName("health_notes") val healthNotes: String = ""
)

data class ProfileMembersRequestDto(
    @SerializedName("members") val members: List<ProfileMemberDto>
)

/**
 * `PUT /api/profile/members` 的响应。
 *
 * @param allergens 后端汇总后的全家过敏源。回显它是为了让 App 能**自证同步成功** ——
 *                  只看 HTTP 200 无法确认后端真的收下了过敏源。
 */
data class ProfileMembersResponseDto(
    @SerializedName("success") val success: Boolean = false,
    @SerializedName("member_count") val memberCount: Int = 0,
    @SerializedName("allergens") val allergens: List<String> = emptyList(),
    @SerializedName("error") val error: String? = null
)

// ==================== 条码查询 ====================
/**
 * `GET /api/barcode/{code}` 的响应。数据源是 Open Food Facts。
 *
 * @param success 查到了才为 true。**查不到时后端不编名字** —— 编出来的名字会被原样写进
 *                库存并进推荐 prompt，比查不到糟得多。客户端据此回落到拍照识别。
 * @param reason 失败原因：
 *   - `invalid`：不是 8–14 位数字
 *   - `not_in_database`：商品库里没有（重试无用，该拍照）
 *   - `lookup_failed`：商品库暂时连不上（**重试有意义**，与上一条必须区别对待）
 *   - `disabled`：条码查询功能被关掉了（用户可选择整体下掉这个功能）
 * @param needsConfirmation 恒为 true，且必须被尊重：Open Food Facts 的中文覆盖差（返回的名字
 *   多为英文），且它是众包库、查询端会补零归一 —— 扫错一位的条码可能返回一个看似合理的商品名。
 *   本 App 的扫码结果统一进「确认食材」页，已满足这个要求。
 * @param quantityHint 商品规格（如 "555ml"），仅作填写提示，不直接当数量。
 * @param cached 本次是不是缓存命中。只用于日志。
 */
data class BarcodeLookupDto(
    @SerializedName("success") val success: Boolean = false,
    @SerializedName("barcode") val barcode: String = "",
    @SerializedName("name") val name: String? = null,
    @SerializedName("category") val category: String? = null,
    @SerializedName("shelf_life_days") val shelfLifeDays: Int? = null,
    @SerializedName("quantity_hint") val quantityHint: String? = null,
    @SerializedName("needs_confirmation") val needsConfirmation: Boolean = true,
    @SerializedName("cached") val cached: Boolean = false,
    @SerializedName("source") val source: String? = null,
    @SerializedName("reason") val reason: String? = null,
    @SerializedName("message") val message: String? = null
)


// ==================== 改菜单 ====================
/**
 * `POST /api/meal-plan/revise` 的响应（说一句话改菜单，**只算不写**）。
 *
 * @param plan 改后的方案，**原样保留后端给的 JSON 结构**（含 ingredients / steps 等）。
 *             确认时要一字不改地回传给 `/api/meal-plan/apply` —— 用强类型 DTO 接一遍再序列化
 *             回去会让未声明的字段静默丢失，落库的菜谱就没有做法步骤了。
 * @param reason 失败原因：`no_plan`（这一餐还没有菜单）/ `allergen_blocked`（改后命中过敏源）。
 *               两者引导不同（去生成 vs 换个说法），要能区分。
 */
data class RevisePlanDto(
    @SerializedName("success") val success: Boolean = false,
    @SerializedName("meal_type") val mealType: String? = null,
    @SerializedName("people_count") val peopleCount: Int? = null,
    @SerializedName("before") val before: List<String> = emptyList(),
    @SerializedName("after") val after: List<String> = emptyList(),
    @SerializedName("added") val added: List<String> = emptyList(),
    @SerializedName("removed") val removed: List<String> = emptyList(),
    @SerializedName("plan") val plan: JsonElement? = null,
    @SerializedName("missing_ingredients") val missingIngredients: List<String> = emptyList(),
    @SerializedName("violations") val violations: List<String> = emptyList(),
    /**
     * 改动的基准记录 id，确认时必须原样回传（乐观并发令牌）。
     *
     * 不带它去 apply，用户看 diff 这几十秒里发生的任何写入（定时餐前推荐、另一台手机、他自己
     * 刚删的那道菜）都会被静默盖掉，而两边都提示成功。
     */
    @SerializedName("base_plan_id") val basePlanId: Int? = null,
    @SerializedName("reason") val reason: String? = null,
    @SerializedName("error") val error: String? = null
)

/** `POST /api/meal-plan/apply` 与 `/remove-dish` 的响应。 */
data class MealPlanWriteDto(
    @SerializedName("success") val success: Boolean = false,
    @SerializedName("meal_type") val mealType: String? = null,
    @SerializedName("plan_id") val planId: Int? = null,
    @SerializedName("dishes") val dishes: List<String> = emptyList(),
    @SerializedName("missing_ingredients") val missingIngredients: List<String> = emptyList(),
    @SerializedName("reason") val reason: String? = null,
    @SerializedName("error") val error: String? = null
)
