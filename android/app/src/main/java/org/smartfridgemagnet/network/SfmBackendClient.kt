package org.smartfridgemagnet.network

import android.util.Log
import com.google.gson.Gson
import com.google.gson.JsonElement
import com.google.gson.JsonNull
import com.google.gson.JsonObject
import com.google.gson.JsonSyntaxException
import org.smartfridgemagnet.data.Ingredient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

/**
 * 冰箱贴后端配置。
 *
 * @param baseUrl 形如 https://host（不带尾斜杠，由 LocalRepository.saveSfmBaseUrl 保证）
 * @param appToken 业务侧 app_token（对应后端 SFM_API_TOKEN）；与设备侧 device_token 是
 *                 两套互不复用的凭据 —— 前者给 App 用，后者烧进设备用于拉画面。
 * @param deviceId 目标设备编号，决定画面发布到哪台设备
 */
data class SfmBackendConfig(
    val baseUrl: String,
    val appToken: String,
    val deviceId: String
) {
    val configured: Boolean get() = baseUrl.isNotBlank() && baseUrl.startsWith("https://")
}

/**
 * 「库里还没有已生成的菜谱」——GET /api/recommend/latest 用 404 + code 4004 表达的业务态。
 *
 * 单独立一个异常类型而不是复用 IOException：这不是错误，调用方要据此展示「点击生成」入口。
 * 都塞进 IOException 就只能靠比对错误文案来区分。
 */
class PlanNotFoundException(message: String) : IOException(message)

/**
 * 冰箱贴后端 HTTP 客户端。App 里唯一的模型入口 —— 识别、菜谱、改单、语音都经它。
 *
 * 用裸 OkHttp + Gson 而不引入 Retrofit：接口数量少、没有复杂的路径参数与转换需求。
 *
 * 配置用 @Volatile 可变字段而非构造参数：持有方（MainActivity 的 remember）缓存的是实例
 * 引用，替换实例对它不可见，会导致「保存设置后必须重启 App 才生效」。请求在
 * Dispatchers.IO 发出、设置在主线程保存，故需 volatile。
 */
class SfmBackendClient(config: SfmBackendConfig) {

    companion object {
        private const val TAG = "SfmBackendClient"

        /**
         * 读超时给到 60s：后端渲染一张菜单画面要跑 Pillow 绘制 + .film 编码，且是同步接口。
         * 超时设太短会把「正在渲染」误判成失败并让用户重试，反而加重后端负载。
         */
        private const val CONNECT_TIMEOUT_SEC = 15L
        private const val READ_TIMEOUT_SEC = 60L

        /**
         * 会打模型的接口用这个读超时（秒）。
         *
         * 60 秒不够：`POST /api/recommend` 实测 63.3 秒、`/api/meal-plan/revise` 实测 68.0 秒，
         * 后端都返回 200 而 App 侧已按 socket timeout 判失败。
         *
         * 300 秒按后端上界推：`ai_client.CHAT_TIMEOUT_SECONDS` 是 120 秒，加上约束重试
         * （最多两轮）与渲染落库开销，客户端必须明显宽于它 —— 两侧取同值时谁先超时取决于
         * 调度，同一故障会有两种表现。
         */
        private const val MODEL_READ_TIMEOUT_SEC = 300L

        /** `.film` 定长（跨端契约，见 docs/contracts/README.md）。 */
        const val FILM_SIZE = 209120

        /** 后端「库里没有已生成的菜谱」的业务码（随 404 一起返回）。 */
        private const val CODE_PLAN_NOT_FOUND = 4004

        /**
         * App 侧对话统一用这个 session_id。
         *
         * 与后端自己用的 "default"（/api/chat 缺省）和 "profile"（/api/profile）分开，只为让
         * conversation_history 日志表能看出哪些话是 App 发的。**它不带来任何上下文**，见 [chat]。
         */
        private const val CHAT_SESSION_ID = "android-app"
    }

    @Volatile
    var config: SfmBackendConfig = config
        private set

    /** 更新配置（保存设置后调用），立即对后续请求生效。 */
    fun updateConfig(newConfig: SfmBackendConfig) {
        config = newConfig
    }

    private val gson = Gson()
    private val client = OkHttpClient.Builder()
        .connectTimeout(CONNECT_TIMEOUT_SEC, TimeUnit.SECONDS)
        .readTimeout(READ_TIMEOUT_SEC, TimeUnit.SECONDS)
        .writeTimeout(READ_TIMEOUT_SEC, TimeUnit.SECONDS)
        .build()

    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()

    // ==================== 库存 ====================

    /** 拉取全部库存（后端分页，这里一次取够常见规模）。 */
    suspend fun fetchInventory(pageSize: Int = 200): Result<List<Ingredient>> =
        fetchInventoryRaw(pageSize).map { items -> items.map { it.toIngredient() } }

    /**
     * 拉取全部库存，保留后端原始形态（含 id）。
     *
     * 删除与改量都需要后端 id，而本地 [Ingredient] 没有 id，所以这些操作必须先按名称+单位
     * 在这份原始列表里找回 id。见 [InventorySync]。
     */
    suspend fun fetchInventoryRaw(pageSize: Int = 200): Result<List<InventoryItemDto>> =
        request<InventoryListDto>("GET", "/api/inventory?format=full&page_size=$pageSize")
            .map { it.items }

    /** 新增单条库存。 */
    suspend fun addInventoryItem(ingredient: Ingredient): Result<Int> =
        request<InventoryAddResponseDto>(
            "POST", "/api/inventory",
            body = gson.toJson(ingredient.toInventoryItemDto())
        ).mapCatching {
            if (!it.success) throw IOException(it.message ?: "添加失败")
            it.itemId
        }

    /**
     * 批量新增库存（原子：任一失败后端整体回滚）。
     *
     * 识别一张冰箱照片往往一次产出十几条，逐条 POST 既慢又可能半途失败留下脏数据。
     * 后端在部分失败时返回 **400 且 success=false**，所以不能只看 HTTP 状态码 ——
     * request() 会把 4xx 的响应体一并带出，这里再按 success 字段判定。
     */
    suspend fun addInventoryItems(ingredients: List<Ingredient>): Result<Int> {
        if (ingredients.isEmpty()) return Result.success(0)
        val payload = InventoryBatchRequestDto(ingredients.map { it.toInventoryItemDto() })
        return request<InventoryBatchResponseDto>(
            "POST", "/api/inventory/batch",
            body = gson.toJson(payload),
            allowErrorBody = true
        ).mapCatching {
            if (!it.success) {
                throw IOException(it.message ?: "批量添加失败（成功 ${it.added} / 失败 ${it.failed}）")
            }
            it.added
        }
    }

    /** 删除库存项。 */
    suspend fun deleteInventoryItem(itemId: Int): Result<Unit> =
        request<Map<String, Any>>("DELETE", "/api/inventory/$itemId").map { }

    /** 用本地权威值覆盖一条云端库存；显式 null 用于清除云端已有的日期或克重。 */
    suspend fun updateInventoryItem(itemId: Int, ingredient: Ingredient): Result<Unit> {
        val item = ingredient.toInventoryItemDto()
        val payload = JsonObject().apply {
            addProperty("category", item.category)
            addProperty("quantity", item.quantity)
            addProperty("unit", item.unit)
            add("weight_grams", item.weightGrams?.let { gson.toJsonTree(it) } ?: JsonNull.INSTANCE)
            add("production_date", item.productionDate?.let { gson.toJsonTree(it) } ?: JsonNull.INSTANCE)
            add("shelf_life_days", item.shelfLifeDays?.let { gson.toJsonTree(it) } ?: JsonNull.INSTANCE)
            add("expiry_date", item.expiryDate?.let { gson.toJsonTree(it) } ?: JsonNull.INSTANCE)
        }
        return request<Map<String, Any>>(
            "PUT", "/api/inventory/$itemId",
            body = gson.toJson(payload)
        ).map { }
    }

    // ==================== 识别 ====================

    /**
     * 让后端识别冰箱照片里的食材。
     *
     * App 不直接连接模型服务；统一经用户配置的后端完成「拍照 → 识别 → 入库 → 推荐」，
     * 避免把模型凭据放进 APK，也让受限网络环境下的访问策略集中在后端处理。
     *
     * @param autoSave 默认 false：App 侧流程是「识别 → 用户确认/改数量 → 入库」，让后端
     *        顺手写库会导致同一批食材入两次。
     */
    suspend fun recognize(imageJpeg: ByteArray, autoSave: Boolean = false): Result<RecognizeResponseDto> =
        withContext(Dispatchers.IO) {
            val cfg = config
            if (!cfg.configured) {
                return@withContext Result.failure(
                    IllegalStateException("尚未配置后端地址，请到设置中填写")
                )
            }
            if (imageJpeg.isEmpty()) {
                return@withContext Result.failure(IllegalArgumentException("图片为空"))
            }
            try {
                // multipart 而不是 JSON+base64：后端 /api/recognize 收的是 request.files['image']，
                // 而且 base64 会把体积放大 1/3，图片本来就是这条请求里最大的部分。
                val body = MultipartBody.Builder()
                    .setType(MultipartBody.FORM)
                    .addFormDataPart(
                        "image", "fridge.jpg",
                        imageJpeg.toRequestBody("image/jpeg".toMediaType())
                    )
                    .addFormDataPart("auto_save", autoSave.toString())
                    .build()

                val builder = Request.Builder()
                    .url("${cfg.baseUrl}/api/recognize")
                    .post(body)
                if (cfg.appToken.isNotBlank()) {
                    builder.addHeader("Authorization", "Bearer ${cfg.appToken}")
                }

                // 视觉模型也走 ai_client 的 120 秒上限，客户端必须比它宽：糊照片或高
                // 分辨率图会让识别明显变慢，而 60 秒超时的表现是「识别失败」。
                client.newBuilder()
                    .readTimeout(MODEL_READ_TIMEOUT_SEC, TimeUnit.SECONDS)
                    .build()
                    .newCall(builder.build()).execute().use { response ->
                    val text = response.body?.string() ?: ""
                    // 识别失败时后端用 400/500 + message 说明原因（图片太模糊、模型没返回等），
                    // 只看状态码会把最有用的那句话丢掉。
                    if (text.isBlank()) {
                        return@withContext Result.failure(
                            IOException(describeHttpError(response.code, ""))
                        )
                    }
                    val dto = gson.fromJson(text, RecognizeResponseDto::class.java)
                    if (!response.isSuccessful || !dto.success) {
                        val reason = dto.message?.takeIf { it.isNotBlank() }
                            ?: dto.error?.takeIf { it.isNotBlank() }
                            ?: describeHttpError(response.code, text)
                        return@withContext Result.failure(IOException(reason))
                    }
                    Result.success(dto)
                }
            } catch (e: JsonSyntaxException) {
                Log.e(TAG, "recognize: bad JSON", e)
                Result.failure(IOException("响应不是合法 JSON，请检查后端地址是否正确"))
            } catch (e: Exception) {
                Log.e(TAG, "recognize failed", e)
                Result.failure(e)
            }
        }

    // ==================== 画面渲染与发布 ====================

    /**
     * 让后端渲染「今日总览」并发布为新版本。
     *
     * 渲染逻辑只有一份、在后端（见 跨端契约 架构不变量）：后端 film_encoder 已与网页
     * 调试工具做过逐字节 SHA-256 交叉验证，App 自己再实现一遍编码就多了一处会漂移的实现。
     *
     * @param dishes 形如 "午餐|红烧肉（40分钟）"，竖线前是分类标签
     */
    suspend fun renderOverview(
        title: String = "今日菜单",
        date: String? = null,
        dishes: List<String>,
        expiring: String? = null
    ): Result<RenderResponseDto> = renderInternal(
        RenderRequestDto(
            kind = "overview",
            title = title,
            date = date,
            dishes = dishes,
            expiring = expiring
        )
    )

    /** 渲染某一餐的详细做法（kind = breakfast / lunch / dinner）。 */
    suspend fun renderMealDetail(kind: String, lines: List<String>): Result<RenderResponseDto> =
        renderInternal(RenderRequestDto(kind = kind, lines = lines))

    /**
     * 取一张渲染好的 `.film` 字节，**不在后端发布版本**。
     *
     * 给 BLE 直传用：字节由 App 自己写进设备，云端不需要留版本记录 —— 走发布版接口会让
     * 设备下次联网又把同一张图下载一遍。渲染只在后端一份（跨端契约），BLE 与 Wi-Fi
     * 两条通道显示的是同一张图。
     */
    suspend fun renderFilmBytes(
        title: String = "今日菜单",
        date: String? = null,
        dishes: List<String>,
        expiring: String? = null
    ): Result<ByteArray> = withContext(Dispatchers.IO) {
        val cfg = config
        if (!cfg.configured) {
            return@withContext Result.failure(
                IllegalStateException("尚未配置后端地址，请到设置中填写")
            )
        }
        try {
            val payload = gson.toJson(
                RenderRequestDto(
                    deviceId = cfg.deviceId,
                    kind = "overview", title = title, date = date,
                    dishes = dishes, expiring = expiring
                )
            )
            val builder = Request.Builder()
                .url("${cfg.baseUrl}/api/render/film")
                .post(payload.toRequestBody(jsonMediaType))
            if (cfg.appToken.isNotBlank()) {
                builder.addHeader("Authorization", "Bearer ${cfg.appToken}")
            }

            client.newCall(builder.build()).execute().use { response ->
                if (!response.isSuccessful) {
                    val text = response.body?.string() ?: ""
                    return@withContext Result.failure(
                        IOException(describeHttpError(response.code, text))
                    )
                }
                val bytes = response.body?.bytes()
                    ?: return@withContext Result.failure(IOException("响应为空"))
                // 长度就地校验：固件对定长是硬约束（见 docs/contracts/README.md），长度不对
                // 时它静默拒收并保留旧画面，App 侧只看得到「发送成功」。
                if (bytes.size != FILM_SIZE) {
                    return@withContext Result.failure(
                        IOException("后端返回的 .film 长度异常：${bytes.size}，应为 $FILM_SIZE")
                    )
                }
                Result.success(bytes)
            }
        } catch (e: Exception) {
            Log.e(TAG, "renderFilmBytes failed", e)
            Result.failure(e)
        }
    }

    /**
     * 把一张任意图片（手绘涂鸦 / 相册照片）渲染成 `.film` 字节，**不在后端发布版本**。
     *
     * 与 [renderFilmBytes] 同属「只渲染不发布」，给 BLE 直传用。走后端而不是 App 本地量化：
     * 六色最近色量化只有后端一份实现（跨端契约），且后端顺带做了两件容易漏的事 ——
     * EXIF 方向纠正（手机照片普遍带 orientation）与 alpha 合成到白底（丢 alpha 会让透明
     * 像素变黑）。
     *
     * @param imageBytes 原图字节，PNG / JPEG / WebP 等 Pillow 能解码的格式
     * @param mimeType 原图 MIME，用于 multipart 的 Content-Type
     * @param fit 缩放策略：`cover`（默认，等比填满后居中裁切）/ `contain`（等比缩放留白底）
     *        / `stretch`（直接拉伸）。默认 cover 与后端一致 —— contain 的留白在六色电子纸上
     *        是一大片死白
     */
    suspend fun renderImageFilm(
        imageBytes: ByteArray,
        mimeType: String,
        fit: String = "cover"
    ): Result<ByteArray> = withContext(Dispatchers.IO) {
        val cfg = config
        if (!cfg.configured) {
            return@withContext Result.failure(
                IllegalStateException("尚未配置后端地址，请到设置中填写")
            )
        }
        if (imageBytes.isEmpty()) {
            return@withContext Result.failure(IllegalArgumentException("图片为空"))
        }
        try {
            // multipart 而不是 JSON+base64：与 /api/recognize 一致（客户端少维护一套编码），
            // 且 base64 会把这条请求里最大的一块再放大 1/3。
            val body = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart(
                    // 文件名只需**非空**（后端把空 filename 当「未选择文件」退 400），
                    // 具体叫什么无关：Pillow 按内容嗅探格式，不看扩展名。
                    "image", "custom.img",
                    imageBytes.toRequestBody(mimeType.toMediaType())
                )
                .addFormDataPart("fit", fit)
                .build()

            val builder = Request.Builder()
                .url("${cfg.baseUrl}/api/render/image")
                .post(body)
            if (cfg.appToken.isNotBlank()) {
                builder.addHeader("Authorization", "Bearer ${cfg.appToken}")
            }

            client.newCall(builder.build()).execute().use { response ->
                if (!response.isSuccessful) {
                    // 正常响应是二进制，失败时才吐 JSON（缺字段 / 解码失败 / fit 非法都是
                    // 400 + error），用 describeApiError 把那句话取出来。
                    val text = response.body?.string() ?: ""
                    return@withContext Result.failure(
                        IOException(describeApiError(response.code, text))
                    )
                }
                val bytes = response.body?.bytes()
                    ?: return@withContext Result.failure(IOException("响应为空"))
                // 长度就地校验：固件对定长是硬约束（见 docs/contracts/README.md），长度不对
                // 时它静默拒收并保留旧画面，App 侧只看得到「发送成功」。
                if (bytes.size != FILM_SIZE) {
                    return@withContext Result.failure(
                        IOException("后端返回的 .film 长度异常：${bytes.size}，应为 $FILM_SIZE")
                    )
                }
                Result.success(bytes)
            }
        } catch (e: Exception) {
            Log.e(TAG, "renderImageFilm failed", e)
            Result.failure(e)
        }
    }

    /**
     * 把一段录音转成文字（后端 `POST /api/asr` → 配置的语音模型）。
     *
     * @param wavBytes WAV 字节（16-bit PCM + 44 字节头，见 audio/WavWriter）。
     *                 **只能是 wav 或 mp3** —— ASR 的硬限制，后端按内容嗅探并会拒掉其他格式。
     * @param language `auto`（默认）/ `zh` / `en`。中文场景显式给 `zh`，官方文档说这样识别更准。
     *
     * 走后端而不直连 AI：直连要把 AI_API_KEY 放进 APK。App 里现存的直连路径（识别、
     * 菜谱）是待收敛的技术债（跨端契约）。
     *
     * 空串不当失败返回：后端回 200 + `empty: true`，原样透传给调用方，由 UI 显示
     * 「没听到内容」。
     */
    suspend fun transcribeAudio(
        wavBytes: ByteArray,
        language: String = "zh"
    ): Result<AsrResponseDto> = withContext(Dispatchers.IO) {
        val cfg = config
        if (!cfg.configured) {
            return@withContext Result.failure(
                IllegalStateException("尚未配置后端地址，请到设置中填写")
            )
        }
        if (wavBytes.isEmpty()) {
            return@withContext Result.failure(IllegalArgumentException("录音为空"))
        }
        try {
            val body = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addFormDataPart(
                    // 文件名只需非空；后端按内容嗅探格式，不看扩展名。
                    "audio", "voice.wav",
                    wavBytes.toRequestBody("audio/wav".toMediaType())
                )
                .addFormDataPart("language", language)
                .build()

            val builder = Request.Builder()
                .url("${cfg.baseUrl}/api/asr")
                .post(body)
            if (cfg.appToken.isNotBlank()) {
                builder.addHeader("Authorization", "Bearer ${cfg.appToken}")
            }

            client.newCall(builder.build()).execute().use { response ->
                val text = response.body?.string() ?: ""
                if (!response.isSuccessful) {
                    // 失败时后端给的是 JSON + error 字段（400 输入不合法 / 502 上游问题 /
                    // 503 未配置 Key），describeApiError 把那句话取出来。
                    return@withContext Result.failure(
                        IOException(describeApiError(response.code, text))
                    )
                }
                val dto = gson.fromJson(text, AsrResponseDto::class.java)
                    ?: return@withContext Result.failure(IOException("响应为空"))
                if (!dto.success) {
                    return@withContext Result.failure(IOException(dto.error ?: "语音识别失败"))
                }
                Result.success(dto)
            }
        } catch (e: Exception) {
            Log.e(TAG, "transcribeAudio failed", e)
            Result.failure(e)
        }
    }

    private suspend fun renderInternal(req: RenderRequestDto): Result<RenderResponseDto> =
        request<RenderResponseDto>(
            "POST", "/api/devices/${config.deviceId}/display/render",
            body = gson.toJson(req),
            allowErrorBody = true
        ).mapCatching {
            if (!it.success) throw IOException(it.error ?: "渲染失败")
            it
        }

    /**
     * 让设备下一次同步时无条件更新（一次性）。
     *
     * 设备本地版本号与后端一致时固件会判定「已是最新」而不下载。重新部署后端后版本号可能
     * 从 1 重新开始，此时即使发布了新画面设备也不会拉 —— 这个开关绕过版本比较。
     */
    suspend fun forceNextSync(): Result<Unit> =
        request<Map<String, Any>>(
            "POST", "/api/devices/${config.deviceId}/display/force", body = "{}"
        ).map { }

    /** 读取 manifest（联调诊断用：确认 film_url 是否为 https、版本是否已更新）。 */
    suspend fun fetchManifest(): Result<ManifestDto> =
        request("GET", "/api/devices/${config.deviceId}/display/manifest")

    /**
     * 把设备令牌登记到后端（`POST /api/devices/{id}/token`）。
     *
     * 必须与 BLE `0x42` 写进设备的值**完全一致**：后端拿它校验设备拉 manifest 与 .film 时的
     * `Authorization: Bearer`，两边不一致或后端未登记，设备每次拉取都会拿到 401。
     *
     * 少这一步的故障形态很误导：后端日志显示发布成功、App 强制刷新也返回 200，唯独设备停在
     * 旧画面，线索只有容器日志里那行 `Device auth rejected: token not registered`。
     *
     * 后端在未登记时 fail-closed（直接 401），所以配网必须把 BLE 写入与登记当成一件事的
     * 两半，任一半失败都要如实告诉用户。
     */
    suspend fun registerDeviceToken(deviceToken: String): Result<Unit> =
        request<Map<String, Any>>(
            "POST", "/api/devices/${config.deviceId}/token",
            body = gson.toJson(mapOf("device_token" to deviceToken))
        ).map { }

    /** 后端存活探测。用于设置页的「测试连接」，不碰数据库、最轻量。 */
    suspend fun ping(): Result<Unit> = request<Map<String, Any>>("GET", "/healthz").map { }

    // ==================== 改菜单 ====================

    /**
     * 说一句话改某一餐的菜单（`POST /api/meal-plan/revise`）。**只算不写**。
     *
     * 返回的 [RevisePlanDto.plan] 要原样交给 [applyRevisedPlan] —— 让「确认」再调一次
     * revise 的话，第二次的结果与用户看过的 diff 不是同一份，确认按钮就变成了抽奖。
     */
    suspend fun revisePlan(mealType: String, instruction: String): Result<RevisePlanDto> =
        request<RevisePlanDto>(
            "POST", "/api/meal-plan/revise",
            body = gson.toJson(
                mapOf("meal_type" to mealType, "instruction" to instruction)
            ),
            // 「这一餐还没有菜单」「改后命中过敏源」都是正常业务态，后端用 success=false
            // 表达；按状态码抛掉就只能显示一句通用网络错误。
            allowErrorBody = true,
            // 这一步要打模型，真机实测 68 秒，默认 60 秒会让后端成功、App 报失败。
            readTimeoutSec = MODEL_READ_TIMEOUT_SEC
        )

    /**
     * 把改好的方案落库（`POST /api/meal-plan/apply`）。不调模型。
     *
     * @param plan 必须是 [revisePlan] 原样返回的那份 —— 不要自己拼一个，也不要经强类型 DTO
     *             转一圈：未声明的字段（steps / ingredients 明细）会静默丢失，落库的菜谱
     *             就没有做法了。
     * @param basePlanId [RevisePlanDto.basePlanId] 原样回传。后端据此判断这一餐在用户看
     *             diff 期间有没有被别的写入顶掉，冲突时回 409 + `reason=stale_base`。
     *
     * **失败不抛异常**：`stale_base`（并发冲突，重改一次即可）与 `allergen_blocked`、
     * `invalid_plan`（数据本身不能用）要引导到不同的下一步，调用方需要看到 `reason`。
     */
    suspend fun applyRevisedPlan(
        mealType: String,
        plan: JsonElement,
        peopleCount: Int? = null,
        basePlanId: Int? = null
    ): Result<MealPlanWriteDto> {
        val body = JsonObject().apply {
            addProperty("meal_type", mealType)
            add("plan", plan)
            peopleCount?.let { addProperty("people_count", it) }
            basePlanId?.let { addProperty("base_plan_id", it) }
        }
        return request<MealPlanWriteDto>(
            "POST", "/api/meal-plan/apply",
            body = gson.toJson(body),
            allowErrorBody = true
        )
    }

    /**
     * 从某一餐删掉一道菜（`POST /api/meal-plan/remove-dish`）。不调模型。
     *
     * 删除在后端做而不是本地改完再提交整份方案：客户端的 DTO 只声明了要显示的字段，
     * 回传时 steps 等会丢。后端手里有完整数据。
     */
    suspend fun removeDish(mealType: String, dishName: String): Result<MealPlanWriteDto> =
        request<MealPlanWriteDto>(
            "POST", "/api/meal-plan/remove-dish",
            body = gson.toJson(
                mapOf("meal_type" to mealType, "dish_name" to dishName)
            ),
            // 「这是最后一道菜」是业务态，要按文案引导而不是报错
            allowErrorBody = true
        )

    // ==================== 画面预览 ====================

    /**
     * 构造某张已发布画面的 PNG 预览地址（`GET /api/devices/{id}/display/preview.png`）。
     *
     * @param name 一套画面里的某一张（如 `1_today.film`）；null = 该版本的单张
     * @param version 版本号；null = 最新版本
     * @param width 缩放宽度（列表缩略图用）；null = 原尺寸 792x528
     * @param orientation `portrait` 时按设备实体方向旋转；null 保持旧版横向行为
     * @return 完整 URL；后端未配置时返回 null
     *
     * 返回 URL 而不是 ByteArray：图片交给 Coil 加载才能拿到内存/磁盘缓存与生命周期管理。
     * BLE 直传上去的自定义图后端没有副本，这个地址会 404，调用方据此显示占位。
     */
    fun previewUrl(
        name: String? = null,
        version: Int? = null,
        width: Int? = null,
        orientation: String? = null
    ): String? {
        if (!config.configured) return null
        val params = buildList {
            version?.let { add("version=$it") }
            name?.takeIf { it.isNotBlank() }?.let {
                add("name=${URLEncoder.encode(it, "UTF-8")}")
            }
            width?.let { add("w=$it") }
            orientation?.takeIf { it.isNotBlank() }?.let {
                add("orientation=${URLEncoder.encode(it, "UTF-8")}")
            }
        }
        val query = if (params.isEmpty()) "" else "?" + params.joinToString("&")
        return "${config.baseUrl}/api/devices/${config.deviceId}/display/preview.png$query"
    }

    /**
     * 预览请求要带的鉴权头值，形如 `Bearer xxx`；未配置 Token 时为 null。
     *
     * 暴露它是因为图片由 Coil 发出，用的是它自己的 HTTP 客户端，这个头必须由调用方转交。
     * 不要拿它去手搓请求（会绕过本类的超时与错误处理）。
     */
    fun authorizationHeader(): String? =
        config.appToken.takeIf { it.isNotBlank() }?.let { "Bearer $it" }

    /**
     * 条码查商品（`GET /api/barcode/{code}`）。
     *
     * 必须 allowErrorBody：「认不出这个条码」是正常业务态，后端用 success=false 表达而不是
     * HTTP 错误码；按状态码抛掉只能显示一句通用网络错误，而该做的是提示改用拍照识别。
     */
    suspend fun lookupBarcode(code: String): Result<BarcodeLookupDto> =
        request<BarcodeLookupDto>(
            "GET", "/api/barcode/${URLEncoder.encode(code, "UTF-8")}",
            allowErrorBody = true,
            // 缓存未命中时这一步要问模型（后端上限 120 秒）。
            readTimeoutSec = MODEL_READ_TIMEOUT_SEC
        )

    // ==================== 家庭画像 ====================

    /**
     * 用 App 的成员列表整体替换后端家庭画像。
     *
     * 后端的家庭画像只能通过自然语言对话写入，不上行的话用户在 App 里登记的过敏与忌口对
     * 推荐毫无影响，且两侧都不报错。
     *
     * 语义是整体替换（PUT）：删掉一位成员或去掉一条忌口后必须立刻生效。
     * 详见后端 `db.replace_members()`。
     */
    suspend fun replaceProfileMembers(
        members: List<ProfileMemberDto>
    ): Result<ProfileMembersResponseDto> =
        request<ProfileMembersResponseDto>(
            "PUT", "/api/profile/members",
            body = gson.toJson(ProfileMembersRequestDto(members)),
            allowErrorBody = true
        ).mapCatching {
            if (!it.success) throw IOException(it.error ?: "同步家庭画像失败")
            it
        }

    // ==================== 菜谱方案 ====================

    /**
     * 读取**已经生成并落库**的最近一份方案，不打模型（因而是毫秒级）。
     *
     * 与 [streamRecommend] 的分工是这条链路的关键：菜谱页打开时只该读、不该生成。生成每次
     * 必打一次模型（实测约 25 秒）且给出的菜品都不一样，放在页面打开路径上就成了
     * 「菜单怎么又变了」。换菜单应当是一次显式操作。
     *
     * 必须 allowErrorBody：本接口用 **404** 表达「库里还没有菜谱」，这是正常业务态；按 HTTP
     * 码抛掉会显示成「接口不存在（404）：请检查后端地址与设备编号」，纯误导。
     *
     * @return 失败时若为「暂无方案」，异常是 [PlanNotFoundException]，调用方应据此展示
     *         生成入口而不是报错。
     */
    suspend fun fetchLatestPlan(mealType: String? = null): Result<RecommendLatestDto> {
        val query = mealType?.takeIf { it.isNotBlank() }
            ?.let { "?meal_type=${URLEncoder.encode(it, "UTF-8")}" }
            ?: ""
        // 餐次可能是"午餐"这类中文，必须编码后再拼进 query。
        return request<RecommendLatestDto>(
            "GET", "/api/recommend/latest$query",
            allowErrorBody = true
        ).mapCatching { dto ->
            if (dto.success) return@mapCatching dto
            val reason = dto.error?.takeIf { it.isNotBlank() } ?: "暂无已生成的菜谱"
            if (dto.code == CODE_PLAN_NOT_FOUND) throw PlanNotFoundException(reason)
            throw IOException(reason)
        }
    }

    // ==================== 对话 ====================

    /**
     * 和后端的对话引擎说一句话（POST /api/chat）。
     *
     * **可能很慢**：命中推荐意图时内部要打一次模型（实测约 25 秒，真机出现过 60 秒以上），
     * 故用 [MODEL_READ_TIMEOUT_SEC]。库存查询那条分支不打模型（毫秒级），同一个接口耗时能差
     * 三个数量级，UI 的进度提示要按最坏情况写。
     *
     * **单轮无状态**：后端把每句话写进 conversation_history，但没有任何 handler 读回那张表，
     * 所有 prompt 只拼当前这一句加上库存/画像上下文。因此这里不接受历史消息参数。
     * session_id 只影响那张日志表按谁归档。
     *
     * @param message 用户原话。空串就地拒绝，不去换一个后端的 400。
     */
    suspend fun chat(
        message: String,
        sessionId: String = CHAT_SESSION_ID
    ): Result<ChatResponseDto> {
        val text = message.trim()
        if (text.isEmpty()) {
            return Result.failure(IllegalArgumentException("请先输入一句话"))
        }
        return request<ChatResponseDto>(
            "POST", "/api/chat",
            body = gson.toJson(mapOf("message" to text, "session_id" to sessionId)),
            // 唯一的 4xx 是「缺 message」（400 + {"error": ...}）。本地已挡住那种情况，但真
            // 收到时把后端那句话带出来，比抛一个 "HTTP 400" 有用。
            allowErrorBody = true,
            readTimeoutSec = MODEL_READ_TIMEOUT_SEC
        ).mapCatching { dto ->
            dto.error?.takeIf { it.isNotBlank() }?.let { throw IOException(it) }
            dto
        }
    }

    // ==================== 流式对话与菜谱 ====================

    /**
     * 流式对话。与 [chat] 同一条后端逻辑，只是逐块吐出思考与正文。
     *
     * 用流式而非等一个完整响应：一次生成要二十多秒，静默等待会被当成卡死。
     */
    fun streamChat(
        message: String,
        sessionId: String = CHAT_SESSION_ID
    ): Flow<StreamEvent> {
        val text = message.trim()
        if (text.isEmpty()) {
            return flowOf(StreamEvent.Failed("请先输入一句话"))
        }
        return sseFlow(
            "/api/chat/stream",
            gson.toJson(mapOf("message" to text, "session_id" to sessionId))
        )
    }

    /** 流式生成菜谱。`userInput` 为空表示「按库存自己配」。 */
    fun streamRecommend(
        mealType: String,
        peopleCount: Int,
        userInput: String? = null
    ): Flow<StreamEvent> = sseFlow(
        "/api/recommend/stream",
        gson.toJson(
            buildMap {
                put("meal_type", mealType)
                put("people_count", peopleCount)
                userInput?.takeIf { it.isNotBlank() }?.let { put("user_input", it) }
            }
        )
    )

    /**
     * 消费一条 `text/event-stream`。
     *
     * **必须用 `source().readUtf8Line()` 而不是把整个 body 转成字符串再切行**：一是响应永远
     * 不结束，取整体等于永远不返回；二是它在字节层缓冲后再解 UTF-8，中文被切在两个 TCP
     * 分块之间时不会碎掉。
     *
     * 坏块计数后一次性写日志，不静默跳过。
     */
    private fun sseFlow(path: String, body: String): Flow<StreamEvent> = flow {
        val cfg = config
        if (!cfg.configured) {
            emit(StreamEvent.Failed("还没配置后端地址与 Token"))
            return@flow
        }
        val streamClient = client.newBuilder()
            // 流式连接整体可以很长，但**两个分块之间**的间隔不该超过这个值。
            .readTimeout(MODEL_READ_TIMEOUT_SEC, TimeUnit.SECONDS)
            .build()
        val request = Request.Builder()
            .url(cfg.baseUrl.trimEnd('/') + path)
            .addHeader("Authorization", "Bearer ${cfg.appToken}")
            .addHeader("Accept", "text/event-stream")
            .post(body.toRequestBody(jsonMediaType))
            .build()

        var dropped = 0
        var sawDone = false
        try {
            streamClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    // 复用非流式那套文案：401 这类问题的原因与是不是流式无关。
                    val detail = runCatching { response.body?.string().orEmpty() }.getOrDefault("")
                    emit(StreamEvent.Failed(describeHttpError(response.code, detail)))
                    sawDone = true
                    return@use
                }
                val source = response.body?.source()
                if (source == null) {
                    emit(StreamEvent.Failed("后端没有返回内容"))
                    sawDone = true
                    return@use
                }
                while (true) {
                    val line = source.readUtf8Line() ?: break
                    when (val parsed = parseSseLine(line)) {
                        SseLine.Skip -> continue
                        SseLine.Bad -> dropped++
                        is SseLine.Event -> {
                            emit(parsed.event)
                            if (parsed.event is StreamEvent.Done ||
                                parsed.event is StreamEvent.Failed
                            ) {
                                sawDone = true
                                break
                            }
                        }
                    }
                }
            }
        } catch (e: IOException) {
            emit(StreamEvent.Failed(e.message ?: "连接中断"))
            sawDone = true
        }
        if (dropped > 0) {
            Log.w(TAG, "流式响应有 $dropped 个分块解析失败（$path）")
        }
        if (!sawDone) {
            // 流结束了却没有 result，也没有 error：后端进程被杀、反向代理超时都会这样。
            // 不补一个事件，UI 会永远停在「正在生成」。
            emit(StreamEvent.Failed("生成中断，请重试"))
        }
    }.flowOn(Dispatchers.IO)


    // ==================== 用餐时间表 ====================

    /** 读取用餐时间与自动下发参数。 */
    suspend fun fetchMealSchedule(): Result<MealScheduleDto> =
        request("GET", "/api/meal-schedule")

    /**
     * 更新用餐时间 / 每日更新时间 / 自动下发开关。
     *
     * 三个参数都可选，但**至少要给一个** —— 全为 null 时就地失败，那种请求一定是调用方的 bug。
     *
     * 后端是**全量替换且原子**的：任一项不合法就整体拒绝、一个字节都不写。所以失败后不必回滚
     * 本地草稿；也因此传 [items] 时不能只发改动的那一餐 —— 只发一餐会把另两餐清掉。
     *
     * 校验错误文案已写明是第几项、哪里不合法，直接透传给用户。
     *
     * @param leadMinutes 合法域由 [MealScheduleDto.leadMinutesMin] / [MealScheduleDto.leadMinutesMax]
     *        给出（随 GET 一起返回）。这里不做本地校验：范围是后端的口径，客户端再抄一份必然漂移。
     */
    suspend fun updateMealSchedule(
        items: List<MealScheduleTimeDto>? = null,
        dailyPublishTime: String? = null,
        leadMinutes: Int? = null,
        autoPublish: Boolean? = null
    ): Result<MealScheduleDto> {
        if (items == null && dailyPublishTime == null && leadMinutes == null && autoPublish == null) {
            return Result.failure(IllegalArgumentException("没有要保存的内容"))
        }
        return request<MealScheduleDto>(
            "PUT", "/api/meal-schedule",
            body = gson.toJson(
                MealSchedulePutDto(
                    schedule = items,
                    dailyPublishTime = dailyPublishTime,
                    leadMinutes = leadMinutes,
                    autoPublish = autoPublish
                )
            ),
            allowErrorBody = true
        ).mapCatching { dto ->
            if (dto.success == false || dto.error != null) {
                throw IOException(dto.error?.takeIf { it.isNotBlank() } ?: "用餐时间保存失败")
            }
            dto
        }
    }

    // ==================== 版式与设备偏好 ====================

    /** 读取该设备的展示偏好（含可选版式清单，一次往返即可画完选择器）。 */
    suspend fun fetchDevicePrefs(): Result<DevicePrefsDto> =
        request("GET", "/api/devices/${config.deviceId}/prefs")

    /**
     * 设置展示版式；已有菜单时后端会同步重渲染并发布完整四张画面。
     *
     * @param republish 版式没有变化也照发一次，用于人的显式重试。
     */
    suspend fun updateDeviceLayout(
        layout: String,
        options: Map<String, String>,
        republish: Boolean = false
    ): Result<DevicePrefsDto> =
        request<DevicePrefsDto>(
            "PUT", "/api/devices/${config.deviceId}/prefs",
            body = gson.toJson(
                mapOf(
                    "layout" to layout,
                    "layout_options" to options,
                    "republish" to republish
                )
            ),
            allowErrorBody = true,
            allowServerErrorBody = true
        ).mapCatching { dto ->
            if (dto.success == false || dto.error != null) {
                throw IOException(dto.error?.takeIf { it.isNotBlank() } ?: "版式设置失败")
            }
            dto
        }

    /** 列出可选版式。不挂在设备下：还没绑定设备时（开箱引导、纯预览）也要能列出来。 */
    suspend fun fetchLayouts(): Result<LayoutListDto> =
        request("GET", "/api/render/layouts")

    /**
     * 取某个版式的预览图（**PNG 字节**，可直接交给图片控件解码）。
     *
     * 不走 [request]：那个函数按 JSON 解析响应体，这里正常响应是二进制。
     *
     * 后端返回 PNG 而不是 `.film`：`.film` 是给固件的 4bit 索引格式，图片控件显示不了。
     */
    suspend fun fetchLayoutPreview(
        layout: String,
        kind: String = "overview",
        options: Map<String, String> = emptyMap()
    ): Result<ByteArray> = withContext(Dispatchers.IO) {
        val cfg = config
        if (!cfg.configured) {
            return@withContext Result.failure(
                IllegalStateException("尚未配置后端地址，请到设置中填写")
            )
        }
        try {
            val query = "?layout=${URLEncoder.encode(layout, "UTF-8")}" +
                    "&kind=${URLEncoder.encode(kind, "UTF-8")}&natural=1" +
                    options.toSortedMap().entries.joinToString("") { (key, value) ->
                        "&${URLEncoder.encode(key, "UTF-8")}" +
                            "=${URLEncoder.encode(value, "UTF-8")}"
                    }
            val builder = Request.Builder().url("${cfg.baseUrl}/api/render/preview$query").get()
            if (cfg.appToken.isNotBlank()) {
                builder.addHeader("Authorization", "Bearer ${cfg.appToken}")
            }

            client.newCall(builder.build()).execute().use { response ->
                if (!response.isSuccessful) {
                    // 失败时后端吐的是 JSON（未知 layout 走 400 并列出可选值），把 error
                    // 那句话取出来。
                    val text = response.body?.string() ?: ""
                    return@withContext Result.failure(
                        IOException(describeApiError(response.code, text))
                    )
                }
                val bytes = response.body?.bytes()
                    ?: return@withContext Result.failure(IOException("响应为空"))
                if (bytes.isEmpty()) {
                    return@withContext Result.failure(IOException("预览图为空"))
                }
                Result.success(bytes)
            }
        } catch (e: Exception) {
            Log.e(TAG, "fetchLayoutPreview failed", e)
            Result.failure(e)
        }
    }

    // ==================== 同步状态与手动下发 ====================

    /**
     * 读取设备同步状态（含设备自报的电量与固件版本）。
     *
     * 路径不在 `/display/` 下，与 manifest / render 那一组不同级。
     */
    suspend fun fetchSyncStatus(): Result<SyncStatusDto> =
        request("GET", "/api/devices/${config.deviceId}/sync-status")

    /**
     * 手动触发一次整套画面下发（渲染当天菜单并发布为新版本）。
     *
     * 自动下发每天只有一个窗口，这个入口让改动可以立刻验证，不必等到次日更新时间。
     *
     * 必须 allowServerErrorBody：后端在 `success:false` 时走的是 **HTTP 500**，连「餐次名
     * 拼错」这种参数问题也是 500，allowErrorBody 的 4xx 区间覆盖不到它。
     *
     * @param force true 时让后端无视幂等检查、照发一版。**人按的按钮应当传 true**：幂等是
     *        为了约束自动任务，回一句「近期已下发过」对用户就是什么都没发生。默认 false 以
     *        保持既有调用方的幂等语义。
     * @return 一句给人看的结果说明。`skipped` 分支（该餐次近期已下发过）是**成功**，返回的是
     *         后端 detail 原文而不是错误；传 force=true 时后端不会再走那个分支。
     */
    suspend fun triggerPublish(
        mealType: String? = null,
        force: Boolean = false
    ): Result<String> {
        val deviceId = config.deviceId.trim()
        if (deviceId.isEmpty()) {
            return Result.failure(IllegalStateException("尚未绑定设备编号，请先到设置中填写"))
        }
        val payload = mutableMapOf<String, Any>()
        mealType?.takeIf { it.isNotBlank() }?.let { payload["meal_type"] = it }
        payload["device_id"] = deviceId
        // 只在 true 时带上：后端默认 false，省一个字段也省掉「老后端不认 force」的风险。
        if (force) payload["force"] = true
        return request<PublishTriggerDto>(
            "POST", "/api/scheduler/trigger/publish",
            body = gson.toJson(payload),
            allowServerErrorBody = true,
            readTimeoutSec = MODEL_READ_TIMEOUT_SEC
        ).mapCatching { dto ->
            if (!dto.success) {
                throw IOException(dto.error?.takeIf { it.isNotBlank() } ?: "画面下发失败")
            }
            if (dto.skipped) {
                return@mapCatching dto.detail?.takeIf { it.isNotBlank() }
                    ?: "该餐次近期已下发过，未重复发布"
            }
            val done = dto.published.filter { it.isNotBlank() }
            if (done.isEmpty()) "已下发（${dto.devices.size} 台设备）"
            else "已下发：${done.joinToString("；")}"
        }
    }

    // ==================== 内部 ====================

    /**
     * 发一个请求并把响应体解析成 [T]。
     *
     * @param allowErrorBody 4xx 时是否仍解析响应体。后端有几个接口（批量添加、渲染）
     *        用 400 + `success:false` 表达业务失败并在体里给出原因。
     * @param allowServerErrorBody 5xx 时是否仍解析响应体。单独一个参数而不是放宽
     *        [allowErrorBody] 的区间：放宽会让既有调用方在真正的服务端故障（500 HTML 错误页）
     *        上从「HTTP 500」变成「响应不是合法 JSON」。发布触发与版式更新需要它。
     * @param readTimeoutSec 覆盖默认读超时。会打模型的接口必须传 [MODEL_READ_TIMEOUT_SEC]。
     *        用 per-call 覆盖而不是抬高全局超时，否则一个真正卡死的普通请求也要转很久才报错。
     */
    private suspend inline fun <reified T> request(
        method: String,
        path: String,
        body: String? = null,
        allowErrorBody: Boolean = false,
        allowServerErrorBody: Boolean = false,
        readTimeoutSec: Long? = null
    ): Result<T> = withContext(Dispatchers.IO) {
        val cfg = config
        if (!cfg.configured) {
            return@withContext Result.failure(IllegalStateException("尚未配置后端地址，请到设置中填写"))
        }
        try {
            val builder = Request.Builder().url("${cfg.baseUrl}$path")
            // token 为空时不带鉴权头：后端在未配置 SFM_API_TOKEN 时会放行（仅本机开发），
            // 带一个空 Bearer 反而会被判为无效凭据。
            if (cfg.appToken.isNotBlank()) {
                builder.addHeader("Authorization", "Bearer ${cfg.appToken}")
            }
            when (method) {
                "GET" -> builder.get()
                "DELETE" -> builder.delete()
                "POST" -> builder.post((body ?: "{}").toRequestBody(jsonMediaType))
                "PUT" -> builder.put((body ?: "{}").toRequestBody(jsonMediaType))
                else -> throw IllegalArgumentException("不支持的方法 $method")
            }

            // newBuilder() 复用连接池与调度器，只换超时。
            val caller = if (readTimeoutSec == null) client
            else client.newBuilder().readTimeout(readTimeoutSec, TimeUnit.SECONDS).build()

            caller.newCall(builder.build()).execute().use { response ->
                val text = response.body?.string() ?: ""
                val bodyStillUsable = (allowErrorBody && response.code in 400..499) ||
                        (allowServerErrorBody && response.code in 500..599)
                if (!response.isSuccessful && !bodyStillUsable) {
                    Log.w(TAG, "$method $path -> HTTP ${response.code}")
                    return@withContext Result.failure(
                        IOException(describeHttpError(response.code, text))
                    )
                }
                if (text.isBlank()) {
                    return@withContext Result.failure(IOException("响应为空"))
                }
                Result.success(gson.fromJson(text, T::class.java))
            }
        } catch (e: JsonSyntaxException) {
            // 返回了非 JSON —— 多见于把基址填成了网关/门户页面，此时会收到一段 HTML。
            Log.e(TAG, "$method $path: bad JSON", e)
            Result.failure(IOException("响应不是合法 JSON，请检查后端地址是否正确"))
        } catch (e: Exception) {
            Log.e(TAG, "$method $path failed", e)
            Result.failure(e)
        }
    }

    /**
     * 优先用后端 JSON 里的 error 文案，取不到再退回状态码描述。
     *
     * 给那些**正常响应不是 JSON** 的接口用（预览 PNG）：它们失败时才吐 JSON，没有别的 DTO
     * 顺带承接。
     */
    private fun describeApiError(code: Int, body: String): String {
        val fromJson = try {
            gson.fromJson(body, ApiErrorDto::class.java)?.error?.takeIf { it.isNotBlank() }
        } catch (e: JsonSyntaxException) {
            // 不是 JSON（网关 HTML 页之类），交给状态码描述处理即可，不必上报。
            Log.w(TAG, "describeApiError: body is not JSON (${e.message})")
            null
        }
        return fromJson ?: describeHttpError(code, body)
    }

    /** 把常见状态码翻译成可执行的提示，而不是只丢一个数字给用户。 */
    private fun describeHttpError(code: Int, body: String): String {
        val brief = body.take(200)
        return when (code) {
            401 -> "鉴权失败（401）：后端 Token 与服务端 SFM_API_TOKEN 不一致"
            404 -> "接口不存在（404）：请检查后端地址与设备编号 —— $brief"
            413 -> "请求体过大（413）"
            else -> "HTTP $code：$brief"
        }
    }
}
