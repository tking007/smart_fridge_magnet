package org.smartfridgemagnet.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.compose.BackHandler
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.animation.Crossfade
import org.smartfridgemagnet.ui.theme.Motion
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.outlined.Devices
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.RestaurantMenu
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.Brush
import androidx.compose.material.icons.outlined.CameraAlt
import org.smartfridgemagnet.ui.components.AppDock
import org.smartfridgemagnet.ui.components.DockAction
import org.smartfridgemagnet.ui.components.NavItem
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bluetooth
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import org.smartfridgemagnet.data.*
import org.smartfridgemagnet.ui.theme.AppTheme
import org.smartfridgemagnet.ui.viewmodel.FamilyViewModel
import android.graphics.Bitmap
import org.smartfridgemagnet.FridgeMagnetApp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import kotlinx.coroutines.yield
import androidx.compose.runtime.rememberCoroutineScope
import org.smartfridgemagnet.network.PlanDishDto
import org.smartfridgemagnet.network.ChatAction
import org.smartfridgemagnet.network.StreamEvent
import org.smartfridgemagnet.network.PlanNotFoundException
import org.smartfridgemagnet.network.RecommendLatestDto
import org.smartfridgemagnet.network.RevisePlanDto
import org.smartfridgemagnet.network.SfmBackendClient
import org.smartfridgemagnet.network.SyncStatusDto
import org.smartfridgemagnet.network.InventoryReconcileResult
import org.smartfridgemagnet.network.dishDetails
import org.smartfridgemagnet.network.dishNames
import org.smartfridgemagnet.network.inventorySyncScope
import org.smartfridgemagnet.network.missingForShownPlan
import org.smartfridgemagnet.network.replyText
import org.smartfridgemagnet.network.shouldHydrateInventory
import org.smartfridgemagnet.network.toIngredient
import java.net.SocketTimeoutException
import java.io.IOException

class MainActivity : ComponentActivity() {

    private val requiredPermissions: Array<String>
        get() {
            val perms = mutableListOf(
                Manifest.permission.CAMERA,
                Manifest.permission.ACCESS_FINE_LOCATION
            )
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                perms.add(Manifest.permission.BLUETOOTH_CONNECT)
                perms.add(Manifest.permission.BLUETOOTH_SCAN)
            }
            return perms.toTypedArray()
        }

    private var onAllPermissionsGranted: () -> Unit = {}
    private var onPermissionsDenied: (List<String>) -> Unit = {}

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { permissions ->
        val denied = permissions.filterValues { !it }.keys.toList()
        if (denied.isEmpty()) {
            onAllPermissionsGranted()
        } else {
            // 必须有 else：不给反馈的话用户点「拍照」或「扫描设备」后界面毫无反应，
            // 看起来像按钮坏了。要说清缺哪一项、去哪儿给。
            onPermissionsDenied(denied)
        }
    }

    /** 把系统权限名翻译成用户在设置里看到的名字。 */
    private fun permissionLabel(permission: String): String = when (permission) {
        Manifest.permission.CAMERA -> "相机"
        Manifest.permission.ACCESS_FINE_LOCATION,
        Manifest.permission.ACCESS_COARSE_LOCATION -> "位置信息"
        Manifest.permission.BLUETOOTH_CONNECT,
        Manifest.permission.BLUETOOTH_SCAN -> "附近的设备"
        Manifest.permission.RECORD_AUDIO -> "麦克风"
        else -> permission.substringAfterLast('.')
    }

    /** 打开本应用的系统设置页 —— 用户拒过一次后系统不再弹框，只能去设置里改。 */
    fun openAppSettings() {
        startActivity(
            android.content.Intent(
                android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                android.net.Uri.fromParts("package", packageName, null)
            )
        )
    }

    fun ensurePermissions(onGranted: () -> Unit, onDenied: (String) -> Unit = {}) {
        val missing = requiredPermissions.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) {
            onGranted()
        } else {
            onAllPermissionsGranted = onGranted
            onPermissionsDenied = { denied ->
                onDenied(
                    "缺少${denied.map(::permissionLabel).distinct().joinToString("、")}权限。" +
                        "请在系统设置 → 应用权限里允许后重试。"
                )
            }
            permissionLauncher.launch(missing.toTypedArray())
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ensurePermissions(onGranted = {})

        setContent {
            AppTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    FridgeMagnetApp(activity = this)
                }
            }
        }
    }
}

@Composable
fun FridgeMagnetApp(activity: MainActivity) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val app = context.applicationContext as FridgeMagnetApp
    val repository = remember { app.getRepository() }
    val familyViewModel: FamilyViewModel = viewModel()
    val members by familyViewModel.members.collectAsState()
    val coroutineScope = rememberCoroutineScope()

    // 用 rememberSaveable 而不是 remember：remember 只活在 Composition 里，屏幕旋转、切后台
    // 被系统回收、开了「不保留活动」都会让它归零，用户拍完照识别出的十几样食材转一下手机
    // 就全没了。识别结果与菜谱都是花一次模型调用换来的数据，一并存住。
    var currentScreen by rememberSaveable { mutableStateOf("home") }
    var recognizedIngredients by rememberSaveable(stateSaver = IngredientListSaver) {
        mutableStateOf(emptyList())
    }
    var recipeResult by rememberSaveable(stateSaver = RecipeResultSaver) {
        mutableStateOf<RecipeResult?>(null)
    }
    // 方案的来源与时效（后端读来的才有；本地模型现场生成的没有这些元信息，为 null）。
    // 跟着 recipeResult 一起持久化：否则旋转后菜单还在、「几分钟前生成」却消失了。
    var recipePlanMeta by rememberSaveable(stateSaver = RecipePlanMetaSaver) {
        mutableStateOf<RecipePlanMeta?>(null)
    }
    // isLoadingRecipe 刻意不持久化：进程重建后那个协程已经不存在了，把 true 恢复回来只会让
    // 页面永远转圈。回到 false 则显示「没有内容」，用户可以重试。
    var isLoadingRecipe by remember { mutableStateOf(false) }
    // isRegeneratingRecipe 同理不持久化。
    var isRegeneratingRecipe by remember { mutableStateOf(false) }
    /** 当前菜谱长任务的真实阶段；后端不是流式接口，因此不伪造百分比。 */
    var recipeProgressText by remember { mutableStateOf<String?>(null) }
    /** 流式生成时模型思考的累积文本；空串表示没有可展示的思考。 */
    var recipeThinking by remember { mutableStateOf("") }
    /** 生成成功后还要重新读取落库菜单，读完这一跳才算整个任务完成。 */
    var announceRecipeAfterLoad by remember { mutableStateOf(false) }
    // 三类错误刻意分开，因为生命周期与归属不同：
    //   errorMessage   —— 一次性的全局提示（Snackbar），任何页面都要能看见；
    //   recognizeError —— 识别页的**终态**，页面上除了它没有别的内容，需要一个「重试」出口；
    //   recipeError    —— 菜谱页的终态，同上。
    // 共用一个 errorMessage 会让从食材页触发的推荐失败看不到任何提示。
    var errorMessage by remember { mutableStateOf<String?>(null) }
    // 两个终态错误跟着页面一起存：否则旋转后错误提示消失、页面却还停在那一屏。
    var recognizeError by rememberSaveable { mutableStateOf<String?>(null) }
    var recipeError by rememberSaveable { mutableStateOf<String?>(null) }
    val snackbarHostState = remember { SnackbarHostState() }

    // showSnackbar 会挂起直到提示消失，正好用来在展示完成后清空状态，
    // 否则同一条消息不会再次触发（key 没变）。
    LaunchedEffect(errorMessage) {
        val message = errorMessage ?: return@LaunchedEffect
        snackbarHostState.showSnackbar(message)
        errorMessage = null
    }
    var editingMember by remember { mutableStateOf<FamilyMember?>(null) }
    var showAddIngredientDialog by remember { mutableStateOf(false) }
    /** 确认页可能来自相机/条码，也可能来自首页手动录入；返回时要回到真实入口。 */
    var ingredientBackScreen by rememberSaveable { mutableStateOf("camera") }
    // 设备状态不由这里托管：它只在设备控制台里用，且必须由 BLE 实时查询得到。
    // 提到外层 remember 会导致「离开页面回来还显示旧状态」，而设备可能早已断连。
    var editingIngredient by remember { mutableStateOf<Pair<Ingredient, Int>?>(null) }
    var showInventoryBlePrompt by remember { mutableStateOf(false) }
    var pendingInventoryBlePush by remember { mutableStateOf(false) }

    // migrateInventory 已在 FridgeMagnetApp.onCreate 中执行，这里读取修复后的数据
    var fridgeInventory by remember { mutableStateOf(repository.getFridgeInventory()) }

    // 首页只展示云端已经知道的设备摘要；BLE 实时控制仍集中在设备页，避免两套连接状态漂移。
    var homeSyncStatus by remember { mutableStateOf<SyncStatusDto?>(null) }
    var homeDeviceLoading by remember { mutableStateOf(false) }
    var homeDeviceError by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(currentScreen) {
        if (currentScreen != "home") return@LaunchedEffect
        val client = app.sfmBackendClient
        if (!client.config.configured || client.config.deviceId.isBlank()) {
            homeSyncStatus = null
            homeDeviceError = null
            homeDeviceLoading = false
            return@LaunchedEffect
        }
        homeDeviceLoading = true
        repository.getProvisionDeviceToken().takeIf { it.isNotBlank() }?.let {
            client.registerDeviceToken(it)
        }
        client.fetchSyncStatus().fold(
            onSuccess = {
                homeSyncStatus = it
                homeDeviceError = null
            },
            onFailure = {
                homeSyncStatus = null
                homeDeviceError = it.message ?: "设备状态读取失败"
            }
        )
        homeDeviceLoading = false
    }

    // 库存上行失败的痕迹。从持久化里读，因为它要跨进程重启一直显示到真的同步成功为止 ——
    // 后端库存没更新的后果是推荐一直按旧食材配菜，而那件事在界面上没有任何别的线索。
    var inventorySyncError by remember { mutableStateOf(repository.getInventorySyncError()) }
    var inventoryHydrating by remember { mutableStateOf(false) }
    var inventoryHydrationError by rememberSaveable { mutableStateOf<String?>(null) }
    var inventoryHydrationErrorScope by rememberSaveable { mutableStateOf("") }
    var inventoryReconciledScope by rememberSaveable { mutableStateOf("") }
    // 新安装没有本地库存，但云端 SQLite 会跨安装保留。每个后端作用域只恢复一次：
    // 恢复成功后，即使用户后来明确删空本地，也不会再把云端旧数据自动搬回来。
    LaunchedEffect(
        currentScreen,
        app.sfmBackendClient.config.baseUrl,
    ) {
        if (currentScreen != "home") return@LaunchedEffect
        val client = app.sfmBackendClient
        val syncScope = inventorySyncScope(client.config.baseUrl)
        if (!client.config.configured || syncScope.isEmpty()) {
            inventoryHydrationError = null
            inventoryHydrationErrorScope = ""
            return@LaunchedEffect
        }
        // 同步可能在库存页或识别页失败，只写了持久化痕迹；回首页时直接读 Repository，
        // 不依赖另一个 Compose effect 的执行顺序。
        val pendingSyncError = repository.getInventorySyncError()
        if (inventorySyncError != pendingSyncError) inventorySyncError = pendingSyncError
        if (inventoryHydrationErrorScope != syncScope) {
            inventoryHydrationError = null
            inventoryHydrationErrorScope = ""
        }

        // 设置页的“补齐本地库存”直接写 Repository；返回首页时必须刷新 Compose 状态。
        val storedInventory = repository.getFridgeInventory()
        if (fridgeInventory != storedInventory) fridgeInventory = storedInventory

        // 判据见 shouldReconcileInventoryNow()：欠不欠一次对账，而不是有没有错误文案。
        if (!org.smartfridgemagnet.network.shouldReconcileInventoryNow(
                reconciledScope = inventoryReconciledScope,
                currentScope = syncScope,
                needsReconcile = repository.inventoryNeedsReconcile(),
            )
        ) {
            return@LaunchedEffect
        }

        inventoryHydrating = true
        try {
            synchronizeInventoryWithBackend(client, repository).fold(
                onSuccess = { outcome ->
                    fridgeInventory = repository.getFridgeInventory()
                    inventoryHydrationError = null
                    inventoryHydrationErrorScope = ""
                    repository.clearInventoryNeedsReconcile()
                    inventorySyncError = ""
                    inventoryReconciledScope = syncScope
                    errorMessage = when {
                        outcome.restored > 0 -> "已从云端恢复 ${outcome.restored} 项库存"
                        outcome.reconciled.deleted > 0 ->
                            "已按本地库存清理云端 ${outcome.reconciled.deleted} 条旧记录"
                        outcome.reconciled.added > 0 ->
                            "已补同步 ${outcome.reconciled.added} 项食材到云端"
                        else -> errorMessage
                    }
                },
                onFailure = { e ->
                    val reason = e.message ?: "未知错误"
                    inventoryHydrationError = "库存初始化或对账失败：$reason"
                    inventoryHydrationErrorScope = syncScope
                    repository.markInventoryNeedsReconcile(reason)
                    inventorySyncError = reason
                }
            )
        } finally {
            inventoryHydrating = false
        }
    }

    // ==================== 菜谱：读取与生成是两条路 ====================
    //
    // 读取（loadRecipeFromBackend）走 /api/recommend/latest，只查库、毫秒级，是菜谱页
    // **唯一的数据来源**；生成（regenerateRecipe / generateRecipeLocally）必打一次模型、
    // 实测约 25 秒，只在用户显式要求时执行。后端的餐前定时任务已把当天方案生成好并落库。

    /** 从后端读取已生成的三餐方案。 */
    val loadRecipeFromBackend: () -> Unit = {
        isLoadingRecipe = true
        recipeError = null
        coroutineScope.launch {
            var loadedGeneratedRecipe = false
            when (val outcome = loadLatestPlans(app.sfmBackendClient)) {
                is PlanLoadOutcome.Loaded -> {
                    recipeResult = outcome.recipe
                    recipePlanMeta = outcome.meta
                    recipeError = null
                    loadedGeneratedRecipe = true
                }
                // 三餐都还没生成：这是正常业务态，交给页面显示空态 + 「立即生成」，
                // 不在这里自动去生成 —— 自动生成会让"打开页面"重新变成一次 25 秒等待。
                PlanLoadOutcome.Empty -> {
                    recipeResult = null
                    recipePlanMeta = null
                    // 生成请求失败后会补读一次，库仍为空时要保留真正的生成错误。
                    if (!announceRecipeAfterLoad) recipeError = null
                }
                is PlanLoadOutcome.Failed -> {
                    recipeResult = null
                    recipePlanMeta = null
                    recipeError = outcome.message
                }
            }
            isLoadingRecipe = false
            if (announceRecipeAfterLoad) {
                announceRecipeAfterLoad = false
                isRegeneratingRecipe = false
                recipeProgressText = null
                if (loadedGeneratedRecipe) errorMessage = "菜谱已生成，可随时查看"
            }
        }
    }

    /** 显式重新生成：只经后端。 */
    val regenerateRecipe: () -> Unit = {
        val client = app.sfmBackendClient
        if (!client.config.configured) {
            recipeError = "还没配置后端地址，无法生成菜谱。请到设置页填写后端地址与 Token。"
            isRegeneratingRecipe = false
        } else if (!isRegeneratingRecipe) {
            // 生成期间屏蔽重复触发：25 秒的静默容易让人以为没点上，
            // 重复点只会让后端多打几次模型（每次都是钱和 25 秒）。
            isRegeneratingRecipe = true
            recipeProgressText = "正在提交生成请求"
            recipeThinking = ""
            recipeError = null
            coroutineScope.launch {
                yield()
                val mealKey = currentMealKey()
                // 走流式而不是 /api/recommend：流式给的是后端真实阶段与模型思考，
                // 也因此不需要「超时后去查有没有偷偷生成成功」那段兜底。
                val thinking = StringBuilder()
                var failure: String? = null
                var done = false
                client.streamRecommend(
                    mealType = mealKey,
                    // 0 人时传 null：后端在人数缺失时会自己问，传 0 只会配出一份没人吃的菜。
                    peopleCount = members.size.takeIf { it > 0 } ?: 0
                ).collect { event ->
                    when (event) {
                        is StreamEvent.Stage -> recipeProgressText = event.text
                        is StreamEvent.Reasoning -> {
                            thinking.append(event.delta)
                            recipeThinking = thinking.toString()
                        }
                        // 正文是那份 JSON 菜单本身，逐字显示一段 JSON 对用户没有意义。
                        is StreamEvent.Content -> Unit
                        is StreamEvent.Done -> {
                            if (event.payload.action == ChatAction.RECOMMEND) {
                                done = true
                            } else {
                                failure = event.payload.replyText().ifBlank {
                                    "后端没有生成新菜单"
                                }
                            }
                        }
                        is StreamEvent.Failed -> failure = event.message
                    }
                }
                if (done) {
                    recipeProgressText = "生成完成，正在读取已保存菜单"
                    announceRecipeAfterLoad = true
                    // 刻意不用流里那份结果直接渲染，而是重新读一次：读取路径是页面唯一的
                    // 数据来源，两条渲染路径迟早漂移。
                    loadRecipeFromBackend()
                } else {
                    recipeError = failure ?: "菜谱生成失败"
                    isRegeneratingRecipe = false
                    recipeProgressText = null
                }
                recipeThinking = ""
            }
        }
    }

    // 自然语言改单同样会调用 AI，且实测可达 60 多秒。任务放在顶层作用域中，离开改单页
    // 后不会因页面离开 Composition 而被取消；结果保存在这里，返回页面仍能看到 diff。
    var revisionPending by remember { mutableStateOf<RevisePlanDto?>(null) }
    var revisionBusy by remember { mutableStateOf(false) }
    var revisionProgressText by remember { mutableStateOf<String?>(null) }
    var revisionError by remember { mutableStateOf<String?>(null) }
    val startRevision: (String, String) -> Unit = { mealKey, request ->
        if (!revisionBusy) {
            revisionPending = null
            revisionError = null
            revisionBusy = true
            revisionProgressText = "正在提交改单请求"
            coroutineScope.launch {
                yield()
                revisionProgressText = "请求已提交，AI 正在调整菜单"
                app.sfmBackendClient.revisePlan(mealKey, request).fold(
                    onSuccess = { dto ->
                        if (dto.success) {
                            revisionPending = dto
                            errorMessage = "菜单调整建议已生成，返回调整页查看差异"
                        } else {
                            revisionError = dto.error ?: "没能改成，换个说法再试一次"
                            errorMessage = revisionError
                        }
                        revisionBusy = false
                        revisionProgressText = null
                    },
                    onFailure = { e ->
                        revisionError = e.message ?: "改单请求失败"
                        errorMessage = revisionError
                        revisionBusy = false
                        revisionProgressText = null
                    }
                )
            }
        }
    }

    LaunchedEffect(
        pendingInventoryBlePush,
        recipeResult,
        isLoadingRecipe,
        isRegeneratingRecipe,
        recipeError
    ) {
        if (!pendingInventoryBlePush || isLoadingRecipe || isRegeneratingRecipe) {
            return@LaunchedEffect
        }
        if (recipeResult != null) {
            pendingInventoryBlePush = false
            currentScreen = "transfer"
        } else if (recipeError != null) {
            pendingInventoryBlePush = false
        }
    }

    BackHandler(enabled = currentScreen != "home") {
        when (currentScreen) {
            // 根屏（菜谱 / 设备 / 画面）与从首页进入的二级页都回首页。
            "camera", "family", "recipe", "transfer", "device", "custom" ->
                currentScreen = "home"
            "ingredients" -> currentScreen = ingredientBackScreen
            // 返回菜谱页而非首页：对话是从菜谱页发起的「这份菜单我不满意」，回到菜谱页
            // 才接得上 —— 而且刚下发完的方案要在那一页才看得到。
            "chat" -> currentScreen = "recipe"
            // 这三个都是从设备页进来的，回首页会让人以为退出了流程。设置已从 dock 移进
            // 设备页，所以它的返回目标也跟着变。
            "provision", "layout", "settings" -> currentScreen = "device"
            else -> currentScreen = "home"
        }
    }

    if (editingMember != null) {
        EditMemberDialog(
            member = editingMember!!,
            onDismiss = { editingMember = null },
            onConfirm = { updatedMember ->
                familyViewModel.updateMember(updatedMember)
                editingMember = null
            }
        )
    }

    // dock 只在 4 个根屏出现；二级页（拍照 / 配网 / 对话…）铺满全屏，由各自的返回逻辑
    // 回到来路 —— 给它们一个「切走」的入口会让进行中的流程被静默丢弃。
    //
    // 第 4 位放「自定义画面」而不是设置：它是与拍照识别并列的日常主流程，而设置一次性配完
    // 就不再碰。设置改从设备页进入 —— 要调的东西本来就都和设备相关。
    val rootScreens = listOf("home", "recipe", "device", "custom")
    val navItems = listOf(
        NavItem("首页", Icons.Outlined.Home),
        NavItem("菜谱", Icons.Outlined.RestaurantMenu),
        NavItem("设备", Icons.Outlined.Devices),
        NavItem("画面", Icons.Outlined.Brush),
    )
    val openRoot: (Int) -> Unit = { index ->
        val target = rootScreens[index]
        currentScreen = target
        // tab 一律进得去，未配置后端时由页面自己给引导 —— 拦住不跳会让人以为 tab 坏了。
        if (target == "recipe") {
            if (app.sfmBackendClient.config.configured) {
                loadRecipeFromBackend()
            } else {
                recipeError = "还没配置后端地址，无法读取或生成菜谱。请到「设备 → 应用设置」填写后端地址与 Token。"
            }
        }
    }

    // dock 悬浮在内容之上而不是把内容挤上去：它是一个控件，不是屏幕边界。
    // 根屏内容底部各自留出 DOCK_CLEARANCE 的空隙。
    Box(Modifier.fillMaxSize()) {
        // 屏幕切换走淡入淡出而不是直接跳变：dock 上四个 tab 互切时，硬切让人分不清
        // 是换了页面还是同一页刷新了一下。
        Crossfade(
            targetState = currentScreen,
            animationSpec = Motion.page(),
            label = "screen",
        ) { screen ->
            when (screen) {
                "home" -> {
                    HomeScreen(
                        members = members,
                        ingredients = fridgeInventory,
                        inventoryLoading = inventoryHydrating,
                        backendConfigured = app.sfmBackendClient.config.configured,
                        deviceId = app.sfmBackendClient.config.deviceId,
                        deviceStatus = homeSyncStatus,
                        deviceStatusLoading = homeDeviceLoading,
                        deviceStatusError = homeDeviceError,
                        onCameraClick = { currentScreen = "camera" },
                        onFamilyClick = { currentScreen = "family" },
                        // 菜谱不在首页给入口：它是 dock 的一个 tab，一次点击就到，
                        // 首页再摆一个跳同一处的磁贴只是让人多认一遍。
                        onTransferClick = { currentScreen = "device" },
                        onSettingsClick = { currentScreen = "settings" },
                        onEditMember = { member -> editingMember = member },
                        onAddIngredient = { showAddIngredientDialog = true },
                        onDeleteIngredient = { index ->
                            val removed = fridgeInventory.getOrNull(index)
                            repository.deleteIngredient(index)
                            fridgeInventory = repository.getFridgeInventory()
                            // 删除同样要回传后端：只双写「新增」会让后端库存只增不减，而餐前
                            // 自动推荐读的正是后端那份，会拿早就吃掉的食材去配菜。
                            if (removed != null) {
                                syncDeleteToBackend(app, coroutineScope, repository, removed) { errorMessage = it }
                            }
                        },
                        onEditIngredient = { index ->
                            val item = fridgeInventory.getOrNull(index)
                            if (item != null) {
                                editingIngredient = item to index
                            }
                        },
                        inventorySyncError = inventoryHydrationError ?: inventorySyncError,
                        onRetryInventorySync = {
                            coroutineScope.launch {
                                inventoryHydrating = true
                                try {
                                    synchronizeInventoryWithBackend(
                                        app.sfmBackendClient, repository
                                    ).fold(
                                        onSuccess = { outcome ->
                                            fridgeInventory = repository.getFridgeInventory()
                                            inventoryReconciledScope = inventorySyncScope(
                                                app.sfmBackendClient.config.baseUrl
                                            )
                                            inventoryHydrationError = null
                                            inventoryHydrationErrorScope = ""
                                            repository.clearInventoryNeedsReconcile()
                                            inventorySyncError = ""
                                            errorMessage = "后端库存已与本地一致" +
                                                "（新增 ${outcome.reconciled.added}、" +
                                                "更新 ${outcome.reconciled.updated}、" +
                                                "清理 ${outcome.reconciled.deleted}）"
                                        },
                                        onFailure = { e ->
                                            val reason = e.message ?: "同步失败"
                                            repository.markInventoryNeedsReconcile(reason)
                                            inventorySyncError = reason
                                            errorMessage = "重试仍失败：$reason"
                                        }
                                    )
                                } finally {
                                    inventoryHydrating = false
                                }
                            }
                        }
                    )
                }

                "camera" -> {
                    CameraScreen(
                        onBack = { currentScreen = "home" },
                        onPhotoConfirmed = { bitmap ->
                            ingredientBackScreen = "camera"
                            isLoadingRecipe = true
                            recognizeError = null
                            currentScreen = "ingredients"
                            coroutineScope.launch {
                                identifyIngredients(app, bitmap).fold(
                                    onSuccess = { ingredients ->
                                        // 合并而不是整体赋值：扫条码加进来的商品也在这个列表里，
                                        // 直接赋值会在用户接着拍一张照时把它们无声抹掉。
                                        // 识别结果排在前面 —— 同名时它带着数量与单位，信息更全。
                                        recognizedIngredients =
                                            (ingredients + recognizedIngredients).distinctBy { it.name }
                                        isLoadingRecipe = false
                                    },
                                    onFailure = { e ->
                                        recognizeError = e.message ?: "识别失败"
                                        isLoadingRecipe = false
                                    }
                                )
                            }
                        },
                        // 条码入口只在配了后端时给出：条码变成商品名要靠后端问模型，没有后端时
                        // 这个按钮点下去只能报错 —— 那不如不显示。
                        onBarcodeIngredient = if (app.sfmBackendClient.config.configured) {
                            { ingredient ->
                                ingredientBackScreen = "camera"
                                // 落进待确认列表并**立刻跳到确认页**：只有确认页渲染这个列表，
                                // 少了这一跳扫出来的商品在界面上等于不存在。仍要走「确认食材」这
                                // 一步 —— 条码认出的商品数量恒为 1、也没有保质期，都需用户过一眼。
                                recognizedIngredients =
                                    (recognizedIngredients + ingredient).distinctBy { it.name }
                                // 清掉上一次拍照识别的错误态，否则确认页整屏显示「识别失败」。
                                recognizeError = null
                                isLoadingRecipe = false
                                currentScreen = "ingredients"
                            }
                        } else null,
                        onLookupBarcode = if (app.sfmBackendClient.config.configured) {
                            { code -> app.sfmBackendClient.lookupBarcode(code).getOrNull() }
                        } else null
                    )
                }

                "ingredients" -> {
                    if (isLoadingRecipe) {
                        Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                CircularProgressIndicator()
                                Spacer(modifier = Modifier.height(16.dp))
                                Text("AI 正在识别食材...")
                            }
                        }
                    } else if (recognizeError != null) {
                        Box(modifier = Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
                            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                Text("识别失败", style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.error)
                                Spacer(modifier = Modifier.height(8.dp))
                                Text(recognizeError!!, style = MaterialTheme.typography.bodyMedium)
                                Spacer(modifier = Modifier.height(16.dp))
                                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                    Button(onClick = {
                                        recognizeError = null
                                        currentScreen = "camera"
                                    }, shape = RoundedCornerShape(12.dp)) { Text("重新拍照") }
                                    // 识别不通时仍要能把食材记进冰箱，否则整条流程在这里彻底堵死
                                    OutlinedButton(onClick = {
                                        recognizeError = null
                                        currentScreen = "home"
                                        showAddIngredientDialog = true
                                    }, shape = RoundedCornerShape(12.dp)) { Text("手动添加") }
                                }
                            }
                        }
                    } else {
                        IngredientScreen(
                            ingredients = recognizedIngredients,
                            existingInventory = fridgeInventory,
                            onBack = { currentScreen = ingredientBackScreen },
                            onConfirm = { ingredients ->
                                // 仅存入冰箱，不生成菜谱
                                recognizedIngredients = ingredients
                                repository.addIngredients(ingredients)
                                fridgeInventory = repository.getFridgeInventory()
                                // 顺带同步到后端。刻意「本地先落、网络后补」：库存是本地优先的数据，
                                // 没网时也必须能记进冰箱 —— 让网络失败回滚本地写入，等于把可用功能
                                // 绑死在网络上。失败只提示，不影响已保存的结果。
                                syncInventoryToBackend(app, coroutineScope, context, ingredients) { synced ->
                                    if (synced) showInventoryBlePrompt = true
                                }
                            },
                            onRecommend = { ingredients, extraInventory ->
                                // 生成菜谱（可能合并冰箱食材）
                                val allIngredients = if (extraInventory != null) {
                                    (ingredients + extraInventory).distinctBy { it.name }
                                } else {
                                    ingredients
                                }
                                // 存入冰箱（如果还没存过的话）
                                // 注意：如果用户先点了"确认食材"再点"AI推荐"，不能重复存
                                // 这里通过检查冰箱是否已有这些食材来避免重复
                                val currentInventory = repository.getFridgeInventory()
                                val newIngredients = ingredients.filter { new ->
                                    currentInventory.none { it.name == new.name && it.quantityUnit == new.quantityUnit }
                                }
                                if (newIngredients.isNotEmpty()) {
                                    repository.addIngredients(newIngredients)
                                }
                                fridgeInventory = repository.getFridgeInventory()
                                // 跳转菜谱页。这里走的是**生成**路径而不是读取：用户刚在食材页
                                // 明确点了「AI 推荐」，那是一次显式的生成意图 —— 读一份可能是
                                // 早上生成的旧方案回来，等于无视了他刚刚新增的这批食材。
                                recipeResult = null
                                recipePlanMeta = null
                                recipeError = null
                                currentScreen = "recipe"
                                if (!app.sfmBackendClient.config.configured) {
                                    recipeError = "还没配置后端地址，无法生成菜谱。" +
                                        "请到设置页填写后端地址与 Token。"
                                } else if (newIngredients.isNotEmpty()) {
                                    // 先把新食材推上去再生成：后端拿的是它自己那份库存，
                                    // 同步没成功就生成，配出来的菜不会包含刚拍的这批东西。
                                    syncInventoryToBackend(
                                        app, coroutineScope, context, newIngredients
                                    ) { synced ->
                                        if (synced) regenerateRecipe()
                                        else recipeError = "新食材没能同步到后端，菜谱可能不含它们。" +
                                            "请检查网络后重试。"
                                    }
                                } else {
                                    regenerateRecipe()
                                }
                            },
                            onDeleteIngredient = { index ->
                                recognizedIngredients = recognizedIngredients.toMutableList().apply { removeAt(index) }
                            },
                            onAddIngredient = { ingredient ->
                                recognizedIngredients = recognizedIngredients + ingredient
                            },
                            // 保质期编辑也要镜像上来。确认页的列表以这份数据播种，不镜像的话用户填的
                            // 日期只活在那个页面里 —— 返回相机页再进来就没了，而且没有任何提示。
                            onUpdateIngredient = { index, ingredient ->
                                if (index in recognizedIngredients.indices) {
                                    recognizedIngredients = recognizedIngredients.toMutableList()
                                        .apply { this[index] = ingredient }
                                }
                            }
                        )
                    }
                }

                "family" -> {
                    FamilyScreen(viewModel = familyViewModel, onBack = { currentScreen = "home" })
                }

                "recipe" -> {
                    RecipeScreen(
                        recipe = recipeResult,
                        members = members,
                        isLoading = isLoadingRecipe,
                        errorText = recipeError,
                        meta = recipePlanMeta,
                        isRegenerating = isRegeneratingRecipe,
                        progressText = recipeProgressText,
                        thinkingText = recipeThinking,
                        onRegenerate = regenerateRecipe,
                        // 入口常在，不按 configured 隐藏：对话页自己会在未配置后端时给引导，
                        // 与版式页、自定义画面页一致。按配置隐藏入口反而让人找不到功能在哪。
                        onOpenChat = { currentScreen = "chat" },
                        onSendToDisplay = {
                            // 发送到电子纸：生成 .film 文件，跳转传输页面
                            if (recipeResult != null) {
                                currentScreen = "transfer"
                            }
                        },
                        onShare = {
                            // 系统分享：把菜谱文本分享出去
                            if (recipeResult != null) {
                                val text = buildString {
                                    appendLine("🍳 今日菜谱推荐")
                                    appendLine()
                                    if (recipeResult!!.breakfast.isNotEmpty()) {
                                        appendLine("🌅 早餐：${recipeResult!!.breakfast.joinToString("、")}")
                                    }
                                    if (recipeResult!!.lunch.isNotEmpty()) {
                                        appendLine("☀️ 午餐：${recipeResult!!.lunch.joinToString("、")}")
                                    }
                                    if (recipeResult!!.dinner.isNotEmpty()) {
                                        appendLine("🌙 晚餐：${recipeResult!!.dinner.joinToString("、")}")
                                    }
                                    val adjustments = recipeResult!!.memberAdjustments
                                    if (!adjustments.isNullOrEmpty()) {
                                        appendLine()
                                        appendLine("👨‍👩‍👧‍👦 个性化建议：")
                                        adjustments.forEach { (name, tips) ->
                                            appendLine("  $name：${tips.joinToString("；")}")
                                        }
                                    }
                                    appendLine()
                                    appendLine("—— 智能冰箱贴 AI 推荐")
                                }
                                val intent = android.content.Intent(android.content.Intent.ACTION_SEND).apply {
                                    type = "text/plain"
                                    putExtra(android.content.Intent.EXTRA_TEXT, text)
                                    putExtra(android.content.Intent.EXTRA_SUBJECT, "今日菜谱推荐")
                                }
                                context.startActivity(android.content.Intent.createChooser(intent, "分享菜谱"))
                            }
                        }
                    )
                }

                "settings" -> {
                    SettingsScreen(
                        repository = repository,
                        // 保存后留在设置页：保存一个配置项不是「完成设置」，用户往往接着还要改下一项。
                        onSfmSaved = {
                            app.reloadBackendConfig()
                            familyViewModel.syncToBackend()
                        }
                    )
                }

                "transfer" -> {
                    // 菜谱发送页面：选择 BLE 或 WiFi 传输
                    RecipeTransferScreen(
                        recipe = recipeResult,
                        onBack = { currentScreen = "home" },
                        onDone = { currentScreen = "home" },
                        onOpenSettings = { currentScreen = "settings" }
                    )
                }

                "device" -> {
                    // 设备控制台：连接、状态、配网入口、同步、画面管理、休眠都在这一页。
                    DeviceConsoleScreen(
                        repository = repository,
                        onProvisionWifi = { currentScreen = "provision" },
                        onOpenLayoutPicker = { currentScreen = "layout" },
                        onOpenSettings = { currentScreen = "settings" }
                    )
                }

                "provision" -> {
                    WifiProvisionScreen(
                        repository = repository,
                        onBack = { currentScreen = "device" },
                        onOpenSettings = { currentScreen = "settings" }
                    )
                }

                "layout" -> {
                    // 版式挂在设备页下面而不是全局设置页：它回答的是"这台设备的画面长什么样"，
                    // 属于设备设置。
                    LayoutPickerScreen(onBack = { currentScreen = "device" })
                }

                "chat" -> {
                    // 对话调整菜单。onPlanUpdated 只重新读取、不跳页：用户还要看下发结果那句
                    // 原文（可能是「该餐次近期已下发过」），跳走就看不到了。读取是毫秒级的，
                    // 等他返回菜谱页时那边已经是新的。
                    RecipeChatScreen(
                        onBack = { currentScreen = "recipe" },
                        onPlanUpdated = loadRecipeFromBackend,
                        revisionPending = revisionPending,
                        revisionBusy = revisionBusy,
                        revisionProgressText = revisionProgressText,
                        revisionError = revisionError,
                        onRevise = startRevision,
                        onDiscardRevision = {
                            revisionPending = null
                            revisionError = null
                        }
                    )
                }

                "custom" -> {
                    // 手绘涂鸦 / 相册照片 → 电子纸。onOpenDevice 指向设备页而不是在这一页内嵌
                    // 一套连接 UI：连接、扫描、自动重连那一整套已经在设备控制台里做完了，
                    // 复制一份只会多一处会漂移的实现。
                    CustomFilmScreen(
                        onOpenDevice = { currentScreen = "device" }
                    )
                }
            }
        }
        if (currentScreen in rootScreens) {
            AppDock(
                items = navItems,
                selectedIndex = rootScreens.indexOf(currentScreen),
                onSelect = openRoot,
                modifier = Modifier.align(Alignment.BottomCenter),
                // 主操作键不是第五个 tab：点它拍照识别食材，不切换页面。
                action = DockAction(
                    icon = Icons.Outlined.CameraAlt,
                    contentDescription = "拍照识别食材",
                    onClick = { currentScreen = "camera" },
                ),
            )
        }
    }

    // 编辑食材弹窗
    if (editingIngredient != null) {
        val (ingredient, index) = editingIngredient!!
        EditIngredientDialog(
            ingredient = ingredient,
            onDismiss = { editingIngredient = null },
            onSave = { updated ->
                repository.updateIngredient(index, updated)
                fridgeInventory = repository.getFridgeInventory()
                editingIngredient = null
                syncUpdateToBackend(app, coroutineScope, repository, updated) { errorMessage = it }
            },
            onDelete = {
                repository.deleteIngredient(index)
                fridgeInventory = repository.getFridgeInventory()
                editingIngredient = null
                syncDeleteToBackend(app, coroutineScope, repository, ingredient) { errorMessage = it }
            }
        )
    }

    // 手动添加食材弹窗
    if (showAddIngredientDialog) {
        AddIngredientDialog(
            onDismiss = { showAddIngredientDialog = false },
            onAdd = { added ->
                showAddIngredientDialog = false
                recognizedIngredients = added
                recognizeError = null
                ingredientBackScreen = "home"
                currentScreen = "ingredients"
            }
        )
    }

    if (showInventoryBlePrompt) {
        AlertDialog(
            onDismissRequest = { showInventoryBlePrompt = false },
            icon = { Icon(Icons.Default.Bluetooth, contentDescription = null) },
            title = { Text("库存与菜谱已更新") },
            text = { Text("是否立即通过蓝牙推送到冰箱贴？") },
            confirmButton = {
                TextButton(onClick = {
                    showInventoryBlePrompt = false
                    recipeResult = null
                    recipePlanMeta = null
                    recipeError = null
                    pendingInventoryBlePush = true
                    currentScreen = "recipe"
                    regenerateRecipe()
                }) { Text("生成并推送") }
            },
            dismissButton = {
                TextButton(onClick = { showInventoryBlePrompt = false }) { Text("稍后") }
            }
        )
    }

    // 全局提示。放在页面之后、作为同一个 Surface 内容槽的兄弟节点，因此叠在当前页之上，
    // 与 currentScreen 无关 —— 这正是修掉「非首页时提示永不出现」的关键。
    // 用 Snackbar 而不是 AlertDialog：这些是一次性通知（同步失败、库存为空），
    // 不需要用户确认，弹模态框反而打断操作。
    Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.BottomCenter) {
        SnackbarHost(hostState = snackbarHostState)
    }
}

/**
 * 识别结果与菜谱的 [Saver]：经 JSON 存进 saved instance state。
 *
 * 这两个类型不是 Parcelable，rememberSaveable 默认存不了。走 JSON 而不是加 Parcelable：本地
 * 持久化（LocalRepository）用的已经是 Gson，同一份数据两套序列化会在字段增减时不同步。
 *
 * Bundle 有大小限制（TransactionTooLargeException 约 1MB 级），这里存的只有十几条食材名与
 * 几行菜名。
 */
private val IngredientListSaver: Saver<List<Ingredient>, String> = Saver(
    save = { com.google.gson.Gson().toJson(it) },
    restore = {
        runCatching {
            com.google.gson.Gson().fromJson<List<Ingredient>>(
                it, object : com.google.gson.reflect.TypeToken<List<Ingredient>>() {}.type
            )
        }.getOrNull() ?: emptyList()
    }
)

private val RecipeResultSaver: Saver<RecipeResult?, String> = Saver(
    // Saver 不允许 save 返回 null（那表示"不保存"），所以用空串表达"当时没有菜谱"
    save = { if (it == null) "" else com.google.gson.Gson().toJson(it) },
    restore = {
        if (it.isBlank()) null
        else runCatching {
            com.google.gson.Gson().fromJson(it, RecipeResult::class.java)
        }.getOrNull()
    }
)

/** 方案元信息的 [Saver]，写法与 [RecipeResultSaver] 一致（空串 = 当时没有元信息）。 */
private val RecipePlanMetaSaver: Saver<RecipePlanMeta?, String> = Saver(
    save = { if (it == null) "" else com.google.gson.Gson().toJson(it) },
    restore = {
        if (it.isBlank()) null
        else runCatching {
            com.google.gson.Gson().fromJson(it, RecipePlanMeta::class.java)
        }.getOrNull()
    }
)

// ==================== 后端方案读取 ====================

/**
 * 读取三餐方案的结果。
 *
 * 用三态而不是 `Result<RecipeResult?>`：「还没生成」与「出错了」在页面上是完全不同的两屏
 * （一个给「立即生成」按钮，一个给错误原因），Result 只能表达成功/失败两态。
 */
private sealed interface PlanLoadOutcome {
    data class Loaded(val recipe: RecipeResult, val meta: RecipePlanMeta) : PlanLoadOutcome
    /** 三餐**全部**都还没生成。正常业务态，不是错误。 */
    object Empty : PlanLoadOutcome
    data class Failed(val message: String) : PlanLoadOutcome
}

/** 餐次键与中文标签。顺序即页面展示顺序，也是 [loadLatestPlans] 的并发顺序。 */
private val MEAL_KEYS = listOf(
    "breakfast" to "早餐",
    "lunch" to "午餐",
    "dinner" to "晚餐"
)

/**
 * 并发读取三餐已生成的方案。
 *
 * **必须并发**：三个请求之间没有依赖，串行会把一次「毫秒级读取」拖成三倍延迟。
 *
 * [PlanNotFoundException] 表示那一餐还没生成，按空列表处理并记进 missingMealLabels。
 * 只有三餐**都**没生成才是 Empty。
 */
private suspend fun loadLatestPlans(client: SfmBackendClient): PlanLoadOutcome = coroutineScope {
    val results = MEAL_KEYS.map { (key, _) ->
        async { client.fetchLatestPlan(key) }
    }.map { it.await() }

    val dishes = mutableListOf<List<String>>()
    val details = mutableMapOf<String, Map<String, PlanDishDto>>()
    val missing = mutableListOf<String>()
    val present = mutableListOf<RecommendLatestDto>()
    var hardError: String? = null

    results.forEachIndexed { index, result ->
        val label = MEAL_KEYS[index].second
        result.fold(
            onSuccess = { dto ->
                val names = dto.dishNames()
                dishes.add(names)
                // 做法随菜单一起收下：做法页读它即可，不必再打一次模型。
                // dishDetails() 与 dishNames() 共用同一套方案，两者不会错位。
                details[MEAL_KEYS[index].first] = dto.dishDetails()
                if (names.isEmpty()) missing.add(label) else present.add(dto)
            },
            onFailure = { e ->
                dishes.add(emptyList())
                if (e is PlanNotFoundException) {
                    missing.add(label)
                } else if (hardError == null) {
                    // 网络不通、鉴权失败这类：三条请求走的是同一个后端，第一条的原因就是
                    // 全部的原因，重复三遍同一句话没有信息量。
                    hardError = e.message ?: "读取菜谱失败"
                }
            }
        )
    }

    hardError?.let { return@coroutineScope PlanLoadOutcome.Failed(it) }
    if (present.isEmpty()) return@coroutineScope PlanLoadOutcome.Empty

    PlanLoadOutcome.Loaded(
        recipe = RecipeResult(
            breakfast = dishes[0],
            lunch = dishes[1],
            dinner = dishes[2],
            // 后端方案里没有"按成员的个性化建议"这一层（忌口是在生成时就避开的，
            // 体现在 skip_ingredients 上），置空而不是编几条建议出来。
            memberAdjustments = null,
            dishDetails = details
        ),
        meta = metaOf(present, missing)
    )
}

/**
 * 从已取到的方案里挑一份作为「这份菜单有多新」的代表，并组装元信息。
 *
 * 取**最新**那一份（ageMinutes 最小），不做「任一为旧就算旧」：后者会让每天傍晚都弹一次
 * 「可能不是今天的」（早餐方案确实已过 12 小时），而当晚的晚餐方案是几分钟前刚生成的。
 *
 * ageMinutes 为 null 的（后端解析不出 created_at）排在最后。
 */
private fun metaOf(present: List<RecommendLatestDto>, missing: List<String>): RecipePlanMeta {
    val representative = present.minByOrNull { it.ageMinutes ?: Int.MAX_VALUE } ?: present.first()
    val perMealMissing = present.map { it.missingForShownPlan() }
    return RecipePlanMeta(
        ageMinutes = representative.ageMinutes,
        createdAtRaw = representative.createdAt,
        stale = representative.stale,
        missingMealLabels = missing.toList(),
        // 三餐的缺料合起来去重：采购是一次性行为，按餐次分成三份只会让人自己再合并一遍。
        // 每一餐都只取"屏幕上展示的那一套方案"的缺料，口径与 dishNames() 一致。
        // 三餐**都**没有这个字段时给 null（后端版本旧 / 没算过），让界面整节不显示 ——
        // 空列表会被显示成"都能用现有食材做"，那是替后端下了一个它没做过的结论。
        shoppingList = perMealMissing.takeIf { list -> list.any { it != null } }
            ?.filterNotNull()?.flatten()?.distinct()
    )
}

/**
 * 按本地钟点推断「现在关心的是哪一餐」。
 *
 * 判据必须与后端 `_guess_meal_key_by_clock()` 完全一致（10 点前早餐、14 点前午餐、其余晚餐）：
 * 不一致会出现「App 说在生成午餐、后端生成的是晚餐」，而两边都不报错。
 *
 * 用本地时间：这是在回答「站在冰箱前的人此刻想看哪一餐」。
 */
private fun currentMealKey(): String {
    val hour = java.util.Calendar.getInstance().get(java.util.Calendar.HOUR_OF_DAY)
    return when {
        hour < 10 -> "breakfast"
        hour < 14 -> "lunch"
        else -> "dinner"
    }
}

/**
 * 编辑食材弹窗
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun EditIngredientDialog(
    ingredient: Ingredient,
    onDismiss: () -> Unit,
    onSave: (Ingredient) -> Unit,
    onDelete: () -> Unit
) {
    var mode by remember(ingredient) { mutableStateOf(ingredient.amountMode()) }
    var countUnit by remember(ingredient) {
        mutableStateOf(ingredient.quantityUnit.takeIf { it in listOf("个", "瓶", "盒", "袋", "包") } ?: "个")
    }
    var volumeUnit by remember(ingredient) {
        mutableStateOf(if (ingredient.quantityAmount >= 1000) "L" else "ml")
    }
    var countText by remember(ingredient) {
        mutableStateOf(ingredient.quantityAmount.takeIf { it > 0 }?.toDisplayNumber().orEmpty())
    }
    var weightText by remember(ingredient) {
        mutableStateOf(ingredient.weightGrams?.toDisplayNumber().orEmpty())
    }
    var volumeText by remember(ingredient) {
        mutableStateOf(
            if (ingredient.amountMode() == AmountMode.VOLUME) {
                val value = if (ingredient.quantityAmount >= 1000) {
                    ingredient.quantityAmount / 1000
                } else ingredient.quantityAmount
                value.toDisplayNumber()
            } else "500"
        )
    }
    val count = countText.toDoubleOrNull()
    val weight = weightText.toDoubleOrNull()
    val volume = volumeText.toDoubleOrNull()
    val activeValue = when (mode) {
        AmountMode.COUNT -> count
        AmountMode.WEIGHT -> weight
        AmountMode.VOLUME -> volume
    }
    val valid = activeValue != null && activeValue > 0

    AlertDialog(
        onDismissRequest = onDismiss,
        shape = RoundedCornerShape(20.dp),
        tonalElevation = 6.dp,
        title = { Text("编辑 ${ingredient.name}") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                    listOf(
                        AmountMode.COUNT to "按件",
                        AmountMode.WEIGHT to "按重量",
                        AmountMode.VOLUME to "按容量"
                    ).forEachIndexed { index, (choice, label) ->
                        SegmentedButton(
                            selected = mode == choice,
                            onClick = { mode = choice },
                            shape = SegmentedButtonDefaults.itemShape(index, 3),
                            label = { Text(label) }
                        )
                    }
                }
                when (mode) {
                    AmountMode.WEIGHT -> {
                    OutlinedTextField(
                        value = weightText,
                        onValueChange = { weightText = it.numericInput() },
                        label = { Text("重量") },
                        suffix = { Text("g") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                    }
                    AmountMode.COUNT -> {
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        listOf("个", "瓶", "盒", "袋", "包").forEach { unit ->
                            FilterChip(
                                selected = countUnit == unit,
                                onClick = { countUnit = unit },
                                label = { Text(unit) }
                            )
                        }
                    }
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        IconButton(onClick = {
                            countText = maxOf(1.0, (count ?: 1.0) - 1.0).toDisplayNumber()
                        }) {
                            Icon(Icons.Default.Remove, contentDescription = "减少一个")
                        }
                        OutlinedTextField(
                            value = countText,
                            onValueChange = { countText = it.numericInput() },
                            label = { Text("数量") },
                            suffix = { Text(countUnit) },
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                            singleLine = true,
                            modifier = Modifier.weight(1f)
                        )
                        IconButton(onClick = {
                            countText = ((count ?: 0.0) + 1.0).toDisplayNumber()
                        }) {
                            Icon(Icons.Default.Add, contentDescription = "增加一个")
                        }
                    }
                    }
                    AmountMode.VOLUME -> {
                        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            listOf("ml", "L").forEach { unit ->
                                FilterChip(
                                    selected = volumeUnit == unit,
                                    onClick = { volumeUnit = unit },
                                    label = { Text(unit) }
                                )
                            }
                        }
                        OutlinedTextField(
                            value = volumeText,
                            onValueChange = { volumeText = it.numericInput() },
                            label = { Text("容量") },
                            suffix = { Text(volumeUnit) },
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth()
                        )
                    }
                }
                if (!valid) {
                    Text(
                        when (mode) {
                            AmountMode.COUNT -> "请输入数量"
                            AmountMode.WEIGHT -> "请输入重量"
                            AmountMode.VOLUME -> "请输入容量"
                        },
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.labelSmall
                    )
                }
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    val unit = when (mode) {
                        AmountMode.COUNT -> countUnit
                        AmountMode.WEIGHT -> "g"
                        AmountMode.VOLUME -> volumeUnit
                    }
                    onSave(ingredient.withEditedAmount(mode, activeValue!!, unit))
                },
                enabled = valid,
                shape = RoundedCornerShape(12.dp)
            ) { Text("完成") }
        },
        dismissButton = {
            // 删除用带边框的错误色按钮与取消区分开：两个等权重的 TextButton 并排容易误点。
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(
                    onClick = onDelete,
                    shape = RoundedCornerShape(12.dp),
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.error),
                    colors = ButtonDefaults.outlinedButtonColors(
                        contentColor = MaterialTheme.colorScheme.error
                    )
                ) { Text("删除") }
                TextButton(onClick = onDismiss) { Text("取消") }
            }
        }
    )
}

private fun String.numericInput(): String = filterIndexed { index, char ->
    char.isDigit() || (char == '.' && index > 0 && count { it == '.' } == 1)
}.take(8)

private fun Double.toDisplayNumber(): String = if (this % 1.0 == 0.0) toInt().toString() else toString()

/**
 * 识别冰箱照片，只经后端 `/api/recognize`。
 *
 * 后端返回的是结构化库存项（分类由后端判定、数量拆成数值+单位），直接走 DTO 映射，
 * App 侧不再猜分类、不再解析数量文本。
 */
private suspend fun identifyIngredients(
    app: FridgeMagnetApp,
    bitmap: Bitmap
): Result<List<Ingredient>> {
    val client = app.sfmBackendClient
    if (!client.config.configured) {
        return Result.failure(Exception(
            "还没配置后端地址，无法识别。请到设置页填写后端地址与 Token。"
        ))
    }

    // auto_save=false：入库由用户在下一屏确认后统一执行，让后端顺手写库会入两次
    val jpeg = withContext(Dispatchers.Default) { bitmapToJpegBytes(bitmap) }
    val result = client.recognize(jpeg, autoSave = false)
    val dto = result.getOrNull()
        ?: return Result.failure(Exception(classifyRecognizeFailure(result.exceptionOrNull())))

    val items = dto.items.ifEmpty { dto.newItems }
    if (items.isNotEmpty()) {
        return Result.success(items.map { it.toIngredient(recognitionDerived = true) })
    }
    // 请求成功但一样都没认出来：这不是网络问题，交给分类器判成「图片质量」类。
    return Result.failure(Exception(classifyRecognizeFailure(IOException(
        dto.message?.takeIf { it.isNotBlank() }
            ?: dto.error?.takeIf { it.isNotBlank() } ?: "未识别到食材"
    ))))
}

/**
 * 把识别失败归成三类，各给一条**用户真能执行**的动作。
 *
 * 优先级：鉴权 > 网络 > 图片质量。图片质量放最后，因为只有请求确实打通、模型确实看过图时
 * 它才是真结论 —— 网络不通时说「重拍一张」是把用户往错方向引。
 */
private fun classifyRecognizeFailure(error: Throwable?): String {
    if (error == null) {
        return "没认出食材。请重拍一张：光线充足、食材摊开不要叠放、镜头对准冰箱内部。"
    }
    if (isAuthFailure(error)) {
        return "后端鉴权失败：Token 与服务端不一致。请到设置页检查后端 Token 后重试。"
    }
    if (isNetworkFailure(error)) {
        return "连不上后端。请检查手机网络；若网络正常，再到设置页核对后端地址是否填对。"
    }
    val hint = if (mentionsImageQuality(error.message ?: "")) "图片质量不足。" else "没认出食材。"
    return hint + "请重拍一张：光线充足、食材摊开不要叠放、镜头对准冰箱内部。"
}

/**
 * 判断是不是「网络/服务不可达」。
 *
 * 既看异常类型也看消息文本：SfmBackendClient 会把底层异常重新包成 IOException 并只保留消息
 * （类型信息在那一步就丢了），而 OkHttp 的连接类异常又确实是 UnknownHostException /
 * ConnectException / SocketTimeoutException。两种判据都留着才能覆盖两种到达方式。
 */
private fun isNetworkFailure(error: Throwable): Boolean {
    var cause: Throwable? = error
    var depth = 0
    while (cause != null && depth < 5) {   // 限深，避免自引用 cause 造成死循环
        if (cause is java.net.UnknownHostException ||
            cause is java.net.ConnectException ||
            cause is java.net.SocketTimeoutException
        ) return true
        cause = cause.cause
        depth++
    }
    val msg = error.message ?: return false
    return msg.contains("timeout", ignoreCase = true) ||
        msg.contains("超时") ||
        msg.contains("Unable to resolve host", ignoreCase = true) ||
        msg.contains("Failed to connect", ignoreCase = true)
}

/** 后端把 401 翻译成了一句固定文案（见 SfmBackendClient.describeHttpError），按它识别。 */
private fun isAuthFailure(error: Throwable): Boolean {
    val msg = error.message ?: return false
    return msg.contains("鉴权失败") || msg.contains("401")
}

/** 后端/模型在图片不合格时会在 message 里提到这些词。 */
private fun mentionsImageQuality(message: String): Boolean =
    message.contains("图片") || message.contains("模糊") ||
        message.contains("清晰") || message.contains("质量") ||
        message.contains("光线")

/**
 * 把新增食材同步到后端库存，best-effort。
 *
 * 「本地先落、网络后补」是刻意的：库存是本地优先的数据，没网时也必须能记进冰箱。因此这里
 * 只做提示，不改变已保存的本地状态。
 *
 * 未配置后端时静默跳过。
 */
private fun syncInventoryToBackend(
    app: FridgeMagnetApp,
    scope: kotlinx.coroutines.CoroutineScope,
    context: android.content.Context,
    ingredients: List<org.smartfridgemagnet.data.Ingredient>,
    onComplete: (Boolean) -> Unit = {}
) {
    val client = app.sfmBackendClient
    if (!client.config.configured || ingredients.isEmpty()) {
        onComplete(false)
        return
    }
    val repository = LocalRepository(context)
    scope.launch {
        org.smartfridgemagnet.network.InventorySync.pushAdded(client, ingredients).fold(
            onSuccess = { added ->
                // 只清错误文案，**不清待对账标记**：这一批推的是本地全部食材，
                // 所以之前失败的新增确实已经补上；但后端多出来的条目（删除同步失败
                // 留下的）再怎么新增也去不掉，那个不一致只有全量对账能收敛。
                // 早先这里调的是 clearInventorySyncError()，于是「删除没同步」的警示
                // 会被一次成功的新增擦掉，不一致从此没有出口。
                if (org.smartfridgemagnet.network.mayClearSyncErrorAfterPush(
                        repository.inventoryNeedsReconcile())
                ) {
                    repository.clearInventorySyncError()
                } else {
                    retryPendingInventoryReconcile(client, repository)
                }
                android.widget.Toast.makeText(
                    context, "已同步 $added 项食材到后端", android.widget.Toast.LENGTH_SHORT
                ).show()
                onComplete(true)
            },
            onFailure = { e ->
                // 明确说明本地已保存，避免用户以为食材没记上而重复添加。
                // 同时**留下持久痕迹**：一个一闪而过的 Toast 撑不起这条链路 ——
                // 后端库存没更新的后果是餐前推荐一直拿旧食材配菜，而那个现象与
                // 几分钟前那个 Toast 之间毫无线索可循（真机上就这么发生过）。
                repository.markInventoryNeedsReconcile(e.message ?: "同步后端失败")
                android.widget.Toast.makeText(
                    context, "已存入本地，同步后端失败：${e.message}", android.widget.Toast.LENGTH_LONG
                ).show()
                onComplete(false)
            }
        )
    }
}

/** 从后端补入本地缺失库存，并只在请求与本地落盘都成功后标记首次恢复完成。 */
internal suspend fun hydrateInventoryFromBackend(
    client: SfmBackendClient,
    repository: LocalRepository,
    localInventory: List<Ingredient>
): Result<List<Ingredient>> {
    val syncScope = inventorySyncScope(client.config.baseUrl)
    if (syncScope.isEmpty()) {
        return Result.failure(IllegalStateException("后端地址或设备编号未配置"))
    }
    return org.smartfridgemagnet.network.InventorySync.pullMissing(client, localInventory)
        .mapCatching { missing ->
            if (missing.isNotEmpty()) repository.addIngredients(missing)
            repository.markInventoryHydrated(syncScope)
            missing
        }
}

internal data class InventoryBackendSyncResult(
    val restored: Int,
    val reconciled: InventoryReconcileResult,
)

/** 首次空库存先恢复；此后始终以 App 本地库存为权威完成云端全量对账。 */
internal suspend fun synchronizeInventoryWithBackend(
    client: SfmBackendClient,
    repository: LocalRepository,
): Result<InventoryBackendSyncResult> = runCatching {
    val syncScope = inventorySyncScope(client.config.baseUrl)
    require(client.config.configured && syncScope.isNotEmpty()) {
        "后端地址或设备编号未配置"
    }

    var localInventory = repository.getFridgeInventory()
    var restored = 0
    var hydratedScopes = repository.getInventoryHydratedScopes()
    if (shouldHydrateInventory(localInventory.isEmpty(), syncScope, hydratedScopes)) {
        restored = hydrateInventoryFromBackend(client, repository, localInventory)
            .getOrThrow().size
        localInventory = repository.getFridgeInventory()
        hydratedScopes = repository.getInventoryHydratedScopes()
    } else if (localInventory.isNotEmpty() && syncScope !in hydratedScopes) {
        repository.markInventoryHydrated(syncScope)
        hydratedScopes = repository.getInventoryHydratedScopes()
    }

    check(org.smartfridgemagnet.network.canReconcileInventory(
        localInventoryEmpty = localInventory.isEmpty(),
        currentScope = syncScope,
        hydratedScopes = hydratedScopes,
    )) { "首次云端库存恢复尚未完成" }

    val reconciled = org.smartfridgemagnet.network.InventorySync
        .reconcileBackendToLocal(client, localInventory)
        .getOrThrow()
    InventoryBackendSyncResult(restored, reconciled)
}

/**
 * 若还欠一次全量对账，趁网络确知可用时补跑一次。
 *
 * 调用点都在「某次后端写入刚成功」之后：那一刻网络是通的，是收敛残留最好的时机。不另起后台
 * 任务或网络监听 —— 用户下一次碰库存本来就会经过这些路径。
 *
 * 失败静默：标记还在，下一次成功写入或冷启动会再试。
 */
private suspend fun retryPendingInventoryReconcile(
    client: SfmBackendClient,
    repository: LocalRepository,
) {
    if (!repository.inventoryNeedsReconcile() || !client.config.configured) return
    org.smartfridgemagnet.network.InventorySync
        .reconcileBackendToLocal(client, repository.getFridgeInventory())
        .onSuccess { repository.clearInventoryNeedsReconcile() }
}

/**
 * 把「删除食材」同步到后端，best-effort。
 *
 * 后端库存是餐前自动推荐的输入（scheduler 读它去配菜）。只同步新增不同步删除，后端那份就只
 * 增不减 —— 冰箱贴上会出现用早就吃完的食材配出来的菜，而 App 里看不出任何异常。
 *
 * 失败只提示、不回滚本地删除：本地才是权威，且用户明确执行过删除操作。
 */
private fun syncDeleteToBackend(
    app: FridgeMagnetApp,
    scope: kotlinx.coroutines.CoroutineScope,
    repository: LocalRepository,
    ingredient: Ingredient,
    notify: (String) -> Unit
) {
    val client = app.sfmBackendClient
    if (!client.config.configured) return
    scope.launch {
        org.smartfridgemagnet.network.InventorySync.pushDeleted(client, ingredient)
            .onSuccess { retryPendingInventoryReconcile(client, repository) }
            .onFailure { e ->
                // 删除没同步上去的后果与新增相反但同样具体：后端库存里留着早就吃完的东西，
                // 餐前推荐会拿它配菜。所以同样留下持久痕迹，而不只是一句提示。
                // 标记待对账而不只是记文案：后端多出来的这一条只有全量对账能删掉，
                // 而对账要跑就得有人知道「还欠一次」。
                repository.markInventoryNeedsReconcile(
                    "删除「${ingredient.name}」未同步：${e.message}"
                )
                notify("已从本地删除，后端未同步：${e.message}")
            }
    }
}

/** 把「改数量」同步到后端，best-effort。理由同 [syncDeleteToBackend]。 */
private fun syncUpdateToBackend(
    app: FridgeMagnetApp,
    scope: kotlinx.coroutines.CoroutineScope,
    repository: LocalRepository,
    ingredient: Ingredient,
    notify: (String) -> Unit
) {
    val client = app.sfmBackendClient
    if (!client.config.configured) return
    scope.launch {
        org.smartfridgemagnet.network.InventorySync.pushUpdated(client, ingredient)
            .onSuccess { retryPendingInventoryReconcile(client, repository) }
            .onFailure { e ->
                repository.markInventoryNeedsReconcile(
                    "修改「${ingredient.name}」未同步：${e.message}"
                )
                notify("已在本地保存，后端未同步：${e.message}")
            }
    }
}
