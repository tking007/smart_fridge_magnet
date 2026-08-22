package org.smartfridgemagnet.data

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import java.nio.charset.StandardCharsets
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * 本地数据仓库
 * 使用 SharedPreferences + Gson 持久化数据
 */
class LocalRepository(context: Context) {

    private val prefs: SharedPreferences = context.getSharedPreferences(
        "fridge_magnet_prefs", Context.MODE_PRIVATE
    )
    private val gson = Gson()

    companion object {
        private const val KEY_FAMILY_MEMBERS = "family_members"
        private const val KEY_FRIDGE_INVENTORY = "fridge_inventory"

        // 旧版本 App 直连模型时留下的凭据键，只用于 clearLegacyModelCredentials 清理。
        private const val LEGACY_KEY_AI_API_KEY = "ai_api_key"
        private const val LEGACY_KEY_AI_BASE_URL = "ai_base_url"
        private const val LEGACY_KEY_ZHIPU_API_KEY = "zhipu_api_key"

        private const val KEYSTORE_PROVIDER = "AndroidKeyStore"
        private const val SECRET_KEY_ALIAS = "fridge_magnet_preferences_v1"
        private const val SECRET_PREFIX = "enc:v1:"

        // ---- 后端（智能冰箱贴自有后端，与 AI/智谱是不同服务）----
        private const val KEY_SFM_BASE_URL = "sfm_base_url"
        private const val KEY_SFM_APP_TOKEN = "sfm_app_token"
        private const val KEY_SFM_DEVICE_ID = "sfm_device_id"

        // ---- 设备配网参数 ----
        private const val KEY_PROV_SSID = "prov_wifi_ssid"
        private const val KEY_PROV_PASSWORD = "prov_wifi_password"
        private const val KEY_PROV_DEVICE_TOKEN = "prov_device_token"

        // ---- 上次连接过的设备（用于自动重连）----
        private const val KEY_LAST_DEVICE_ADDR = "last_device_address"
        private const val KEY_LAST_DEVICE_NAME = "last_device_name"

        // ---- 库存上行失败的痕迹 ----
        private const val KEY_INVENTORY_SYNC_ERROR = "inventory_sync_error"
        private const val KEY_INVENTORY_NEEDS_RECONCILE = "inventory_needs_reconcile"
        // 单值键只用于迁移已安装过中间版本的 App。
        private const val KEY_INVENTORY_HYDRATED_SCOPE = "inventory_hydrated_scope"
        private const val KEY_INVENTORY_HYDRATED_SCOPES = "inventory_hydrated_scopes_v2"

        // ---- 开发者选项（设置页里那组联调开关是否可见）----

        private const val DEFAULT_SFM_DEVICE_ID = "dev001"

        /** Empty until the user supplies the address of their own backend. */
        private const val DEFAULT_SFM_BASE_URL = ""
    }

    // ==================== 后端配置 ====================
    // 后端是本项目自有服务（库存、菜谱、画面渲染与下发），凭据独立保存。

    fun saveSfmBaseUrl(url: String) {
        prefs.edit().putString(KEY_SFM_BASE_URL, normalizeHttpsBaseUrl(url)).apply()
    }

    /** 后端基址；未配置时返回空串，让调用方明确引导用户完成设置。 */
    fun getSfmBaseUrl(): String =
        (prefs.getString(KEY_SFM_BASE_URL, DEFAULT_SFM_BASE_URL) ?: DEFAULT_SFM_BASE_URL)
            .trim().trimEnd('/')

    fun saveSfmAppToken(token: String): Result<Unit> = runCatching {
        saveSecret(KEY_SFM_APP_TOKEN, token.trim())
    }

    fun getSfmAppToken(): String = getSecret(KEY_SFM_APP_TOKEN) ?: ""

    fun saveSfmDeviceId(deviceId: String) {
        prefs.edit().putString(KEY_SFM_DEVICE_ID, deviceId.trim()).apply()
    }

    fun getSfmDeviceId(): String =
        prefs.getString(KEY_SFM_DEVICE_ID, DEFAULT_SFM_DEVICE_ID) ?: DEFAULT_SFM_DEVICE_ID

    // ==================== 设备配网参数 ====================
    // 在 App 侧存一份的理由：固件把配网参数持久化在 NVS 并会开机自动重连，但参数结构体的字段
    // 容量变化时（开发期改固件后重烧）NVS 记录长度随之改变，旧记录会被判为不兼容而整份重置为
    // 出厂默认（wifi_enable=0、SSID 清空）。留一份副本即可一键重新下发，不必手输那串 88 字符
    // 的 manifest_url。
    //
    // manifest_url 不单独存：它由后端基址 + device_id 推导（见 buildManifestUrl），两处各存
    // 一份必然漂移。

    /**
     * 保存手输的 Wi-Fi 凭据。**在下发之前调用**：配网失败（密码错、信号弱）时用户不必重输。
     *
     * device_token 刻意不在这里保存，见 [saveProvisionDeviceToken]。
     *
     * 返回 [Result] 而不是直接抛：加密依赖 AndroidKeyStore，它在「用户刚改过锁屏密码导致密钥
     * 失效」「厂商 ROM 的 Keystore 异常」时会抛，而调用点都是 UI 的 onClick，异常直穿 Compose
     * 就是崩溃。
     */
    fun saveProvisionWifi(ssid: String, password: String): Result<Unit> = runCatching {
        val passwordEncrypted = encryptSecret(KEY_PROV_PASSWORD, password)
        check(prefs.edit()
            .putString(KEY_PROV_SSID, ssid)
            .putString(KEY_PROV_PASSWORD, passwordEncrypted)
            .commit()) { "无法安全保存配网凭据" }
    }

    /**
     * 保存 device_token。**只能在 BLE 写入设备成功之后调用。**
     *
     * 与 Wi-Fi 凭据相反：这份记录的唯一含义是「设备里现在就是这个值」，别处（「登记设备令牌」、
     * 「用保存的参数重新配网」）都拿它当设备的真实状态用。写入前就存的话，一次失败的配网会让
     * 本地记着一个设备根本不知道的值，故障从「未登记」变成更难查的「与设备不一致」。
     *
     * token 是随机生成的，重输成本为零。
     */
    fun saveProvisionDeviceToken(deviceToken: String): Result<Unit> = runCatching {
        val tokenEncrypted = encryptSecret(KEY_PROV_DEVICE_TOKEN, deviceToken)
        check(prefs.edit()
            .putString(KEY_PROV_DEVICE_TOKEN, tokenEncrypted)
            .commit()) { "无法安全保存设备令牌" }
    }

    fun getProvisionSsid(): String = prefs.getString(KEY_PROV_SSID, "") ?: ""

    fun getProvisionPassword(): String = getSecret(KEY_PROV_PASSWORD) ?: ""

    fun getProvisionDeviceToken(): String = getSecret(KEY_PROV_DEVICE_TOKEN) ?: ""

    /**
     * 由后端基址与 device_id 推导设备要拉取的 manifest 地址。
     *
     * 固件要求它必须是 https（manifest_parse.c 只接受 https，明文会被直接拒收），
     * 且长度上限 127 字节。基址未配置时返回空串，让上层给出「先配置后端地址」的提示，
     * 而不是拼出一个 https:///api/... 这样的畸形 URL 再由固件报一个费解的错。
     */
    fun buildManifestUrl(): String {
        val base = getSfmBaseUrl()
        if (!base.startsWith("https://")) return ""
        return "$base/api/devices/${getSfmDeviceId()}/display/manifest"
    }

    // ==================== 上次连接的设备 ====================
    // 保存最近设备地址后可直接 connectGatt，避免每次先扫描并让用户重新辨认设备。

    fun saveLastDevice(address: String, name: String) {
        prefs.edit()
            .putString(KEY_LAST_DEVICE_ADDR, address)
            .putString(KEY_LAST_DEVICE_NAME, name)
            .apply()
    }

    fun getLastDeviceAddress(): String = prefs.getString(KEY_LAST_DEVICE_ADDR, "") ?: ""

    fun getLastDeviceName(): String = prefs.getString(KEY_LAST_DEVICE_NAME, "") ?: ""

    fun clearLastDevice() {
        prefs.edit().remove(KEY_LAST_DEVICE_ADDR).remove(KEY_LAST_DEVICE_NAME).apply()
    }

    // ==================== 库存上行失败的痕迹 ====================
    //
    // 持久化一条失败记录而不是只弹 Toast：库存上行失败是静默的、后果又是间接的。Token 填错时
    // `POST /api/inventory/batch` 连续 401，每次只闪过一个 Toast，而后端库存从此停住、餐前推荐
    // 一直拿早就吃完的食材配菜；用户的抱怨会是「推荐的菜不是按冰箱现有食材来的」。
    //
    // 记下来之后，首页可以一直显示「有食材没同步到后端」并给出重试，直到真的同步成功。

    /** 记下一次库存上行失败（覆盖上一条：用户要知道的是「现在还没同步」，不是历史清单）。 */
    fun setInventorySyncError(message: String) {
        prefs.edit().putString(KEY_INVENTORY_SYNC_ERROR, message).apply()
    }

    /** 上一次库存上行失败的原因；没有未同步的内容时返回空串。 */
    fun getInventorySyncError(): String = prefs.getString(KEY_INVENTORY_SYNC_ERROR, "") ?: ""

    fun clearInventorySyncError() {
        prefs.edit().remove(KEY_INVENTORY_SYNC_ERROR).apply()
    }

    // ==================== 待对账标记 ====================
    //
    // 除错误文案外还要一个独立布尔标记的理由：
    //
    // 错误文案会被「任意一次成功的新增」清掉 —— 本地库存是全量来源，失败的新增都在那一批里。
    // 这个理由对新增成立，对**删除**不成立：后端多出来的那一条，再怎么新增也去不掉。于是有一条
    // 静默的不一致链：删除同步失败 → 留下警示 → 用户加了别的食材、新增成功 → 警示被清掉 →
    // 后端那件早就吃完的食材还在继续参与配菜，从此没有任何出口。
    //
    // 所以「需要一次全量对账」必须是独立状态，**只有真的跑完对账才能清**。
    //
    // 只存一个标记而不是操作日志：本地库存本身就是完整的目标状态，一次全量对账覆盖任意多次
    // 失败的增删改；操作日志还要处理顺序、冲突与无界增长。

    /** 标记「后端与本地可能不一致，需要一次全量对账」。 */
    fun markInventoryNeedsReconcile(reason: String) {
        prefs.edit()
            .putBoolean(KEY_INVENTORY_NEEDS_RECONCILE, true)
            .putString(KEY_INVENTORY_SYNC_ERROR, reason)
            .apply()
    }

    /** 是否还欠一次全量对账。 */
    fun inventoryNeedsReconcile(): Boolean =
        prefs.getBoolean(KEY_INVENTORY_NEEDS_RECONCILE, false)

    /**
     * 对账成功后清除标记与错误文案。
     *
     * 刻意只在这里清：新增成功不能代表删除也同步上去了（见上面那段说明）。
     */
    fun clearInventoryNeedsReconcile() {
        prefs.edit()
            .remove(KEY_INVENTORY_NEEDS_RECONCILE)
            .remove(KEY_INVENTORY_SYNC_ERROR)
            .apply()
    }

    /** 已完成首次库存恢复的后端作用域；不包含任何鉴权凭据。 */
    fun getInventoryHydratedScopes(): Set<String> {
        val scopes = prefs.getStringSet(KEY_INVENTORY_HYDRATED_SCOPES, emptySet())
            .orEmpty().toSet()
        val legacy = prefs.getString(KEY_INVENTORY_HYDRATED_SCOPE, "")
            .orEmpty().substringBefore('\n').trimEnd('/')
        return if (legacy.isBlank()) scopes else scopes + legacy
    }

    fun markInventoryHydrated(scope: String) {
        require(scope.isNotBlank()) { "库存同步作用域不能为空" }
        prefs.edit()
            .putStringSet(KEY_INVENTORY_HYDRATED_SCOPES, getInventoryHydratedScopes() + scope)
            .remove(KEY_INVENTORY_HYDRATED_SCOPE)
            .apply()
    }

    // ==================== 旧版本遗留的模型凭据清理 ====================

    /**
     * 擦掉旧版本留在本机的 AI / 智谱 API Key 与地址。
     *
     * 模型调用已全部收敛到后端，App 不再需要任何模型凭据；但删掉存取代码不会让已装机用户
     * SharedPreferences 里那三条记录消失。每次启动顺手清一次（键不存在时是空操作）。
     */
    fun clearLegacyModelCredentials() {
        prefs.edit()
            .remove(LEGACY_KEY_AI_API_KEY)
            .remove(LEGACY_KEY_AI_BASE_URL)
            .remove(LEGACY_KEY_ZHIPU_API_KEY)
            .apply()
    }

    private fun normalizeHttpsBaseUrl(url: String): String {
        val normalized = url.trim().trimEnd('/')
        require(normalized.isEmpty() || normalized.startsWith("https://")) {
            "服务地址必须以 https:// 开头"
        }
        return normalized
    }

    private fun saveSecret(prefKey: String, value: String) {
        val editor = prefs.edit()
        if (value.isEmpty()) {
            editor.remove(prefKey)
        } else {
            editor.putString(prefKey, encryptSecret(prefKey, value))
        }
        check(editor.commit()) { "无法安全保存凭据" }
    }

    private fun getSecret(prefKey: String): String? {
        val stored = prefs.getString(prefKey, null) ?: return null
        if (stored.startsWith(SECRET_PREFIX)) {
            return runCatching { decryptSecret(prefKey, stored) }.getOrNull()
        }

        // 旧版本明文只用于这次迁移；密文同步落盘成功后才把值交给调用方。
        return runCatching {
            val encrypted = encryptSecret(prefKey, stored)
            check(prefs.edit().putString(prefKey, encrypted).commit()) {
                "无法迁移历史明文凭据"
            }
            stored
        }.getOrElse {
            // Keystore 不可用时宁可要求用户重填，也不继续保留明文凭据。
            check(prefs.edit().remove(prefKey).commit()) { "无法删除历史明文凭据" }
            null
        }
    }

    private fun getOrCreateSecretKey(): SecretKey {
        val keyStore = KeyStore.getInstance(KEYSTORE_PROVIDER).apply { load(null) }
        (keyStore.getKey(SECRET_KEY_ALIAS, null) as? SecretKey)?.let { return it }

        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE_PROVIDER)
        generator.init(
            KeyGenParameterSpec.Builder(
                SECRET_KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build()
        )
        return generator.generateKey()
    }

    private fun encryptSecret(prefKey: String, value: String): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, getOrCreateSecretKey())
        cipher.updateAAD(prefKey.toByteArray(StandardCharsets.UTF_8))
        val ciphertext = cipher.doFinal(value.toByteArray(StandardCharsets.UTF_8))
        val iv = Base64.encodeToString(cipher.iv, Base64.NO_WRAP)
        val payload = Base64.encodeToString(ciphertext, Base64.NO_WRAP)
        return "$SECRET_PREFIX$iv:$payload"
    }

    private fun decryptSecret(prefKey: String, stored: String): String {
        val parts = stored.split(':', limit = 4)
        require(parts.size == 4 && parts[0] == "enc" && parts[1] == "v1") {
            "不支持的凭据格式"
        }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(
            Cipher.DECRYPT_MODE,
            getOrCreateSecretKey(),
            GCMParameterSpec(128, Base64.decode(parts[2], Base64.NO_WRAP))
        )
        cipher.updateAAD(prefKey.toByteArray(StandardCharsets.UTF_8))
        return String(cipher.doFinal(Base64.decode(parts[3], Base64.NO_WRAP)), StandardCharsets.UTF_8)
    }

    // ==================== 家庭成员 ====================

    fun saveFamilyMembers(members: List<FamilyMember>) {
        val json = gson.toJson(members)
        prefs.edit().putString(KEY_FAMILY_MEMBERS, json).apply()
    }

    fun getFamilyMembers(): List<FamilyMember> {
        val json = prefs.getString(KEY_FAMILY_MEMBERS, null)
        return if (json != null) {
            val type = object : TypeToken<List<FamilyMember>>() {}.type
            gson.fromJson(json, type)
        } else {
            getDefaultFamilyMembers()
        }
    }

    fun addFamilyMember(member: FamilyMember) {
        val members = getFamilyMembers().toMutableList()
        members.add(member)
        saveFamilyMembers(members)
    }

    fun updateFamilyMember(member: FamilyMember) {
        val members = getFamilyMembers().toMutableList()
        val index = members.indexOfFirst { it.id == member.id }
        if (index != -1) {
            members[index] = member
            saveFamilyMembers(members)
        }
    }

    fun deleteFamilyMember(memberId: String) {
        val members = getFamilyMembers().toMutableList()
        members.removeAll { it.id == memberId }
        saveFamilyMembers(members)
    }

    /**
     * 预置3个默认家庭成员
     */
    private fun getDefaultFamilyMembers(): List<FamilyMember> {
        return listOf(
            FamilyMember(
                id = "member_001",
                name = "小明",
                avatar = "👦",
                role = MemberRole.CHILD,
                goal = HealthGoal.BALANCED,
                preferences = DietaryPreferences(
                    cuisine = "家常",
                    spiceLevel = SpiceLevel.NONE,
                    allergies = emptyList(),
                    dislikes = emptyList()
                )
            ),
            FamilyMember(
                id = "member_002",
                name = "妈妈",
                avatar = "👩",
                role = MemberRole.ADULT,
                goal = HealthGoal.LOSE_FAT,
                preferences = DietaryPreferences(
                    cuisine = "家常",
                    spiceLevel = SpiceLevel.MILD,
                    allergies = emptyList(),
                    dislikes = emptyList()
                )
            ),
            FamilyMember(
                id = "member_003",
                name = "爷爷",
                avatar = "👴",
                role = MemberRole.ELDER,
                goal = HealthGoal.CONTROL_SUGAR,
                preferences = DietaryPreferences(
                    cuisine = "家常",
                    spiceLevel = SpiceLevel.NONE,
                    allergies = emptyList(),
                    dislikes = emptyList()
                )
            )
        )
    }

    // ==================== 冰箱食材库存 ====================

    fun getFridgeInventory(): List<Ingredient> {
        val json = prefs.getString(KEY_FRIDGE_INVENTORY, null)
        return if (json != null) {
            val type = object : TypeToken<List<Ingredient>>() {}.type
            gson.fromJson(json, type)
        } else {
            emptyList()
        }
    }

    fun saveFridgeInventory(ingredients: List<Ingredient>) {
        val json = gson.toJson(ingredients)
        prefs.edit().putString(KEY_FRIDGE_INVENTORY, json).apply()
    }

    fun addIngredients(newIngredients: List<Ingredient>) {
        val inventory = getFridgeInventory().toMutableList()
        for (new in newIngredients) {
            // 找同名同单位的条目
            val existingIdx = inventory.indexOfFirst { it.name == new.name && it.quantityUnit == new.quantityUnit }
            if (existingIdx != -1) {
                // 同名同单位：数量累加
                val existing = inventory[existingIdx]
                val merged = existing.copy(
                    quantityAmount = existing.quantityAmount + new.quantityAmount,
                    quantity = "${(existing.quantityAmount + new.quantityAmount).toInt()}${existing.quantityUnit}",
                    weightGrams = listOfNotNull(existing.weightGrams, new.weightGrams)
                        .takeIf { it.isNotEmpty() }?.sum(),
                    freshness = listOf(existing.freshness, new.freshness).minByOrNull { it.ordinal } ?: existing.freshness
                )
                inventory[existingIdx] = merged
            } else {
                // 新食材 或 同名不同单位：直接添加（不覆盖）
                inventory.add(new)
            }
        }
        saveFridgeInventory(inventory)
    }

    fun updateIngredient(index: Int, ingredient: Ingredient) {
        val inventory = getFridgeInventory().toMutableList()
        if (index in inventory.indices) {
            inventory[index] = ingredient
            saveFridgeInventory(inventory)
        }
    }

    fun deleteIngredient(index: Int) {
        val inventory = getFridgeInventory().toMutableList()
        if (index in inventory.indices) {
            inventory.removeAt(index)
            saveFridgeInventory(inventory)
        }
    }

    /** 迁移已知旧存档格式；明确分类保持原样。 */
    fun migrateInventory() {
        val inventory = getFridgeInventory()
        if (inventory.isEmpty()) return
        val migrated = inventory.map(::migrateStoredIngredient)
        if (migrated != inventory) saveFridgeInventory(migrated)
    }
}
