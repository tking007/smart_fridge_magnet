package org.smartfridgemagnet.ui

import org.smartfridgemagnet.ui.theme.accents
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.Composable
import androidx.compose.material3.MaterialTheme
import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Article
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.automirrored.filled.ViewQuilt
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.SubcomposeAsyncImage
import org.smartfridgemagnet.ui.components.AppSheet
import coil.request.ImageRequest
import org.smartfridgemagnet.FridgeMagnetApp
import androidx.compose.material.icons.filled.Settings
import org.smartfridgemagnet.data.LocalRepository
import org.smartfridgemagnet.ui.theme.categoryAccent
import org.smartfridgemagnet.ui.theme.Spacing
import org.smartfridgemagnet.ui.theme.Motion
import org.smartfridgemagnet.ui.components.IconBadge
import org.smartfridgemagnet.ui.components.pressableCard
import org.smartfridgemagnet.data.WifiLinkState
import org.smartfridgemagnet.data.describeMinutesAgo
import org.smartfridgemagnet.data.wifiLinkStateOf
import org.smartfridgemagnet.device.WakePlanner
import org.smartfridgemagnet.network.MealScheduleDto
import org.smartfridgemagnet.network.SfmBackendClient
import org.smartfridgemagnet.network.SyncStatusDto
import org.smartfridgemagnet.transfer.BleProtocol
import org.smartfridgemagnet.transfer.BleTransfer
import org.smartfridgemagnet.transfer.ConnectionState
import org.smartfridgemagnet.transfer.DeviceInfo
import kotlinx.coroutines.launch

/**
 * 设备页：**常驻连接卡 + 一级入口列表 + 二级页**。
 *
 * 用入口列表而不是一条长卡片流：七组功能平铺要滚八屏，代价不是「要滚」而是**看不见目录**。
 * 列表把「这台设备能调什么」压进一屏，每行带一句当前值（电量、置顶状态、休眠开着没有），
 * 多数时候看一眼列表就不必点进去。
 *
 * 连接卡常驻在两级页面上：这一页大半功能要先连上 BLE，连接状态若只在一级列表可见，用户在
 * 二级页点了个没反应的按钮会以为功能坏了。二级页显示精简版（状态点 + 一行字），扫描与断开
 * 仍只在一级列表 —— 那是建立连接的唯一入口。
 *
 * 依赖 BLE 的控件在未连接时显示但禁用，不整块隐藏：这里要表达的是「连上就能用」。
 *
 * 屏幕文案只留「这是什么」「我该做什么」「改了会怎样」，每处不超过两行。
 *
 * 菜谱下发**不放这里**：那是「做菜」主线的收尾动作，留在 RecipeTransferScreen 里。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DeviceConsoleScreen(
    repository: LocalRepository,
    onProvisionWifi: () -> Unit,
    onOpenLayoutPicker: () -> Unit,
    /** 通往应用设置。设置不在 dock 里，这一行是它唯一的入口。 */
    onOpenSettings: () -> Unit
) {
    val context = LocalContext.current
    val app = context.applicationContext as FridgeMagnetApp
    val bleTransfer = remember { app.bleTransfer }
    val client = remember { app.sfmBackendClient }
    val scope = rememberCoroutineScope()

    val connectionState by bleTransfer.stateFlow.collectAsState()
    val discoveredDevices by bleTransfer.discoveredDevices.collectAsState()
    val connected = connectionState is ConnectionState.Connected

    val state = remember { DeviceConsoleState() }

    // 外设超时、走远或设备自行休眠也会断连，不只会经过页面里的“断开”按钮。
    // 一旦连接消失就清掉 BLE 回读，避免把上一次连接的数据继续标成实时状态。
    LaunchedEffect(connected) {
        if (!connected) state.clearDeviceReadouts()
    }

    // 离开这一页就停扫。BleTransfer 是 Application 级单例，扫描会活过页面 ——
    // 而 Android 有「30 秒内 5 次扫描」的节流：进出几次后扫描会**静默失败**，
    // 表现是列表永远空、按钮停在「停止扫描」，看不出是被系统限流了。
    DisposableEffect(Unit) {
        onDispose {
            bleTransfer.stopScan()
            state.scanning = false
        }
    }

    /** null = 停在一级入口列表。与设置页同一套结构，不进全局 currentScreen。 */
    var section by remember { mutableStateOf<DeviceSection?>(null) }
    val pageOffset = with(LocalDensity.current) { Spacing.md.roundToPx() }
    BackHandler(enabled = section != null) { section = null }

    /** 统一包装：置忙、执行、记日志、复位。避免每个按钮各写一遍模板。 */
    fun run(label: String, block: suspend () -> String) {
        if (state.busy) return
        state.busy = true
        scope.launch {
            state.addLog("→ $label")
            val result = try {
                block()
            } catch (e: Exception) {
                "✗ 异常：${e.message}"
            }
            state.addLog(result)
            state.busy = false
        }
    }

    /** 云端视角刷新。**刻意与 BLE 无关**：设备平时深睡不广播，这条路是常态可用的那条。 */
    suspend fun refreshCloud() {
        if (!client.config.configured) {
            state.cloudError = "尚未配置后端地址"
            return
        }
        state.cloudLoading = true
        repository.getProvisionDeviceToken().takeIf { it.isNotBlank() }?.let {
            client.registerDeviceToken(it)
        }
        client.fetchSyncStatus().fold(
            onSuccess = { state.syncStatus = it; state.cloudError = null },
            onFailure = { state.cloudError = it.message ?: "读取云端记录失败" }
        )
        // 用餐时间用于算唤醒点，与同步状态一起刷；失败不覆盖上面的错误文案。
        client.fetchMealSchedule().onSuccess { state.mealSchedule = it }
        state.cloudLoading = false
    }

    /** BLE 侧状态刷新（含唤醒计划、轮询周期、置顶配置回读）。未连接时什么都不做。 */
    suspend fun refreshDevice() {
        if (!bleTransfer.isConnected()) return
        state.status = bleTransfer.queryStatus()
        state.sleepMode = state.status?.sleepMode ?: bleTransfer.querySleepMode()
        state.sleepRead = true
        state.deviceWake = bleTransfer.queryWakeSchedule()
        state.wakeRead = true
        state.deviceWake?.let { state.fallbackMinutes = it.fallbackMinutes }
        state.pollSeconds = bleTransfer.queryPollIntervalSeconds()
        state.pinned = bleTransfer.queryPinnedFilm()
        state.pinnedRead = true
    }

    // 进页面自动重连上次的设备。固件没有 BLE 绑定，系统不会自动重连，只能这样做。
    // 只尝试一次、失败退回手动扫描 —— 不重试，避免设备不在附近时每次进页面都卡 15 秒。
    LaunchedEffect(Unit) {
        val last = repository.getLastDeviceAddress()
        if (!connected && last.isNotBlank()) {
            state.busy = true
            state.addLog("→ 自动连接上次的设备 $last")
            val ok = bleTransfer.connectToAddress(last, repository.getLastDeviceName())
            state.addLog(if (ok) "✓ 已连接" else "✗ 自动连接失败，请手动扫描")
            if (ok) refreshDevice()
            state.busy = false
        }
    }

    // 云端记录独立拉取，不等 BLE：连不上蓝牙时它是唯一能看到电量与同步历史的地方。
    LaunchedEffect(Unit) { refreshCloud() }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text(section?.title ?: "设备") },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background
                ),
                navigationIcon = {
                    // 一级列表不给返回箭头：设备本身是底部 dock 的一个 tab，箭头与 dock 重复。
                    // 只有二级页才需要它回到入口列表。
                    if (section != null) {
                        IconButton(onClick = { section = null }) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                        }
                    }
                },
                actions = {
                    IconButton(
                        onClick = {
                            run("刷新") {
                                refreshCloud()
                                refreshDevice()
                                if (connected) "✓ 云端与设备状态已刷新"
                                else "✓ 云端记录已刷新（未连蓝牙，设备状态跳过）"
                            }
                        },
                        // 未连接也能刷：云端那部分不需要蓝牙。
                        enabled = !state.busy
                    ) {
                        Icon(Icons.Default.Refresh, contentDescription = "刷新")
                    }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = Spacing.lg)
                .padding(top = Spacing.md, bottom = Spacing.dockClearance),
            verticalArrangement = Arrangement.spacedBy(Spacing.md)
        ) {
            AnimatedContent(
                targetState = section,
                modifier = Modifier.fillMaxWidth(),
                transitionSpec = {
                    (fadeIn(Motion.standard()) +
                        slideInHorizontally(Motion.standard()) { pageOffset }) togetherWith
                        (fadeOut(Motion.standard()) +
                            slideOutHorizontally(Motion.standard()) { -pageOffset })
                },
                label = "device-section"
            ) { current ->
                Column(verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
                    if (current == null) {
                // 设备凭据未恢复是**整条下发链路断开**，放在最上面而不是收进二级页：
                // 这个故障的所有其他表现都是"一切正常"（发布成功、版本号自增、同步状态正常），
                // 唯一的异常就是屏幕不更新，用户没有任何线索能想到是令牌问题。
                if (state.syncStatus?.deviceTokenRegistered == false) {
                    DeviceTokenAlert(
                        lastContactMinutes = state.syncStatus?.deviceLastContactMinutes,
                        savedToken = repository.getProvisionDeviceToken(),
                        busy = state.busy,
                        onRegister = {
                            val token = repository.getProvisionDeviceToken()
                            run("修复设备连接") {
                                client.registerDeviceToken(token).fold(
                                    onSuccess = {
                                        // 立刻回读一次：这个警示要在成功后自己消失，
                                        // 否则用户不知道到底修好了没有。
                                        refreshCloud()
                                        "✓ 设备连接已修复"
                                    },
                                    onFailure = { "✗ 修复失败：${it.message}" }
                                )
                            }
                        },
                        onProvision = onProvisionWifi
                    )
                }

                ConnectionCard(
                    connected = connected,
                    connectionState = connectionState,
                    deviceName = repository.getLastDeviceName(),
                    // Wi-Fi 判据是 BLE 回读 + 后端证据两路（见 wifiLinkStateOf）。
                    // 这里只把 BLE 那一路交出去，后端那一路走下面的 lastContactMinutes。
                    wifiConnected = state.status?.wifiConnected,
                    // 电量 BLE 优先、云端上报兜底：不连蓝牙时它是唯一可见的硬件指标。
                    batteryPercent = state.status?.batteryPercent
                        ?: state.syncStatus?.deviceStatus?.batteryLevel,
                    lastContactMinutes = state.syncStatus?.deviceLastContactMinutes,
                    discovered = discoveredDevices,
                    scanning = state.scanning,
                    busy = state.busy,
                    onToggleScan = {
                        if (state.scanning) {
                            bleTransfer.stopScan(); state.scanning = false
                        } else {
                            bleTransfer.startScan(); state.scanning = true
                            state.addLog("→ 开始扫描")
                        }
                    },
                    onConnect = { dev ->
                        bleTransfer.stopScan(); state.scanning = false
                        run("连接 ${dev.name}") {
                            if (bleTransfer.connectToAddress(dev.address, dev.name)) {
                                // 连上才记住：记一个连不上的地址，只会让下次进页面白等一次超时。
                                repository.saveLastDevice(dev.address, dev.name)
                                refreshDevice()
                                "✓ 已连接并读取状态"
                            } else "✗ 连接失败"
                        }
                    },
                    onDisconnect = {
                        run("断开连接") {
                            bleTransfer.disconnect()
                            state.clearDeviceReadouts()
                            "✓ 已断开"
                        }
                    }
                )

                DeviceIndex(
                    state = state,
                    connected = connected,
                    settingsSummary = client.config.baseUrl.ifBlank { "未配置后端地址" },
                    onOpen = { section = it },
                    onOpenSettings = onOpenSettings
                )
                    } else {
                ConnectionStrip(
                    connected = connected,
                    wifiConnected = state.status?.wifiConnected.takeIf { connected },
                    batteryPercent = state.status?.batteryPercent.takeIf { connected }
                        ?: state.syncStatus?.deviceStatus?.batteryLevel,
                    lastContactMinutes = state.syncStatus?.deviceLastContactMinutes
                )

                        when (current) {
                    DeviceSection.STATUS -> StatusSection(
                        state = state,
                        connected = connected,
                        onRetryCloud = { run("刷新云端记录") { refreshCloud(); "✓ 已刷新" } }
                    )

                    DeviceSection.SCREEN -> ScreenSection(
                        state = state,
                        connected = connected,
                        transfer = bleTransfer,
                        run = ::run,
                        onOpenLayoutPicker = onOpenLayoutPicker,
                        client = client
                    )

                    DeviceSection.NETWORK -> NetworkSection(
                        state = state,
                        connected = connected,
                        repository = repository,
                        client = client,
                        transfer = bleTransfer,
                        run = ::run,
                        refreshCloud = { refreshCloud() },
                        refreshDevice = { refreshDevice() },
                        onProvisionWifi = onProvisionWifi
                    )

                    DeviceSection.HARDWARE -> HardwareSection(
                        state = state,
                        connected = connected,
                        transfer = bleTransfer,
                        run = ::run
                    )

                    DeviceSection.LOG -> LogSection(state = state)
                        }
                    }
                }
            }

            if (state.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
        }
    }
}

// ==================== 页面状态 ====================

/**
 * 设备页的全部可变状态。
 *
 * 收进一个 holder 而不是在 [DeviceConsoleScreen] 里摆十六个 `var`：二级页拆成独立 Composable
 * 后逐个透传会让函数签名膨胀到十几个参数，而「哪个值是设备回读的、哪个是本地待发的」就只能
 * 靠命名区分（[deviceWake] 是设备那份、[fallbackMinutes] 是待下发的选择）。
 *
 * 放在这一层的另一个原因：二级页来回切时不能丢。放进二级页内部的 `remember`，用户返回列表
 * 再进来，文件列表就空了。
 */
private class DeviceConsoleState {
    var scanning by mutableStateOf(false)
    var busy by mutableStateOf(false)
    var log by mutableStateOf(listOf<String>())

    // ---- BLE 回读 ----
    var status by mutableStateOf<BleProtocol.DeviceStatus?>(null)
    var files by mutableStateOf<List<BleProtocol.DeviceFile>>(emptyList())
    var filesRead by mutableStateOf(false)
    var fileListError by mutableStateOf<String?>(null)

    /**
     * 设备回读到的休眠开关。null + [sleepRead] 为 true 表示读失败。
     *
     * [sleepRead] 不是冗余：`Boolean?` 只有三个取值，而这里要表达四种状态
     * （未查询 / 查询失败 / 已开 / 已关）。缺了它，「还没查」与「查失败」都是 null，UI 只能
     * 一律显示「读取中」，一次失败的查询会让状态永远停在读取中。
     */
    var sleepMode by mutableStateOf<Boolean?>(null)
    var sleepRead by mutableStateOf(false)

    /** 设备回读到的置顶配置。null + [pinnedRead] 为 true 表示读失败。 */
    var pinned by mutableStateOf<BleProtocol.PinnedFilm?>(null)
    var pinnedRead by mutableStateOf(false)

    /** 设备回读到的唤醒时间表。null + [wakeRead] 为 true 表示读失败。 */
    var deviceWake by mutableStateOf<BleProtocol.WakeSchedule?>(null)
    var wakeRead by mutableStateOf(false)

    /** 设备回读到的轮询周期（秒）。 */
    var pollSeconds by mutableStateOf<Int?>(null)

    // ---- 待下发的选择 ----
    /** 兜底周期（分钟）。默认 1440 = 24 小时，与出厂固件一致。 */
    var fallbackMinutes by mutableIntStateOf(DEFAULT_FALLBACK_MINUTES)

    // ---- 云端视角（不依赖 BLE）----
    var syncStatus by mutableStateOf<SyncStatusDto?>(null)
    var cloudError by mutableStateOf<String?>(null)
    var cloudLoading by mutableStateOf(false)
    var mealSchedule by mutableStateOf<MealScheduleDto?>(null)

    fun addLog(msg: String) {
        // 新的在前：设备操作的结果通常是即刻要看的，让最新一条永远在可见区域。
        log = (listOf(msg) + log).take(LOG_LIMIT)
    }

    /** 断开连接后清掉所有 BLE 回读值 —— 留着会让人以为看到的是当前设备的实况。 */
    fun clearDeviceReadouts() {
        status = null
        sleepMode = null
        sleepRead = false
        files = emptyList()
        filesRead = false
        fileListError = null
        pinned = null
        pinnedRead = false
        deviceWake = null
        wakeRead = false
        pollSeconds = null
    }
}

// ==================== 一级入口列表 ====================

/** 设备页的五个分组。title 同时用作二级页标题，两处不会写出两个名字。 */
private enum class DeviceSection(val title: String, val icon: ImageVector) {
    STATUS("状态总览", Icons.Default.MonitorHeart),
    SCREEN("画面管理", Icons.Default.Image),
    NETWORK("联网与同步", Icons.Default.CloudSync),
    HARDWARE("硬件配置", Icons.Default.Tune),
    LOG("操作日志", Icons.AutoMirrored.Filled.Article)
}

@Composable
private fun DeviceIndex(
    state: DeviceConsoleState,
    connected: Boolean,
    settingsSummary: String,
    onOpen: (DeviceSection) -> Unit,
    onOpenSettings: () -> Unit
) {
    SectionCard {
        DeviceEntryRow(DeviceSection.STATUS, statusSummary(state, connected)) {
            onOpen(DeviceSection.STATUS)
        }
        HorizontalDivider()
        DeviceEntryRow(DeviceSection.SCREEN, screenSummary(state, connected)) {
            onOpen(DeviceSection.SCREEN)
        }
        HorizontalDivider()
        DeviceEntryRow(DeviceSection.NETWORK, networkSummary(state, connected)) {
            onOpen(DeviceSection.NETWORK)
        }
        HorizontalDivider()
        DeviceEntryRow(DeviceSection.HARDWARE, hardwareSummary(state, connected)) {
            onOpen(DeviceSection.HARDWARE)
        }
        HorizontalDivider()
        DeviceEntryRow(
            DeviceSection.LOG,
            if (state.log.isEmpty()) "暂无操作记录" else "${state.log.size} 条 · ${state.log.first()}"
        ) { onOpen(DeviceSection.LOG) }
        HorizontalDivider()
        // 应用设置跳的是**另一个屏幕**，不是这一页的 section —— 所以单独一行，不进
        // DeviceSection 枚举（进了枚举就得给它编一个 section 内容）。
        SettingsEntryRowInDevice(summary = settingsSummary, onClick = onOpenSettings)
    }
}

/** 设备页底部通往应用设置的一行。样式与上面几行一致，只是目的地是另一个屏幕。 */
@Composable
private fun SettingsEntryRowInDevice(summary: String, onClick: () -> Unit) {
    val accent = categoryAccent(DeviceSection.entries.size)
    Row(
        Modifier
            .fillMaxWidth()
            .pressableCard(onClick = onClick, scaleDown = 0.99f)
            .padding(vertical = Spacing.md),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(Spacing.md)
    ) {
        IconBadge(
            icon = Icons.Default.Settings,
            size = 38.dp,
            container = accent.container,
            tint = accent.solid
        )
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text("应用设置", style = MaterialTheme.typography.titleSmall)
            Text(
                summary,
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
        Icon(
            Icons.AutoMirrored.Filled.KeyboardArrowRight, contentDescription = null,
            modifier = Modifier.size(20.dp),
            tint = MaterialTheme.colorScheme.outline
        )
    }
}

/**
 * 一级列表的一行：图标 + 标题 + 一行摘要 + 右侧箭头。
 *
 * 摘要**限一行**。允许折行的话最长的那一项会把整行撑到三行高，于是五个入口占满一屏，
 * "一眼看完全部分类"这个唯一的好处就没了。
 */
@Composable
private fun DeviceEntryRow(section: DeviceSection, summary: String, onClick: () -> Unit) {
    // 每个分组一个颜色：整列同色图标会读成一片灰，扫不出自己要找哪一行。
    val accent = categoryAccent(section.ordinal)
    Row(
        Modifier
            .fillMaxWidth()
            .pressableCard(onClick = onClick, scaleDown = 0.99f)
            .padding(vertical = Spacing.md),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(Spacing.md)
    ) {
        IconBadge(
            icon = section.icon,
            size = 38.dp,
            container = accent.container,
            tint = accent.solid
        )
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(section.title, style = MaterialTheme.typography.titleSmall)
            Text(
                summary,
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis
            )
        }
        Icon(
            Icons.AutoMirrored.Filled.KeyboardArrowRight, contentDescription = null,
            modifier = Modifier.size(20.dp),
            tint = MaterialTheme.colorScheme.outline
        )
    }
}

// 摘要各自取"这一组里最该先知道的那个值"。刻意优先用云端数据：设备平时深睡，
// 未连蓝牙是常态，摘要若一律显示"未读取"，这一行就白占位置了。

private fun statusSummary(state: DeviceConsoleState, connected: Boolean): String {
    state.cloudError?.let { return "云端读取失败：$it" }
    val cloud = state.syncStatus ?: return if (state.cloudLoading) "正在读取…" else "尚未读到云端记录"
    val battery = state.status?.batteryPercent?.takeIf { connected }?.let { "电量 $it%（实时）" }
        ?: cloud.deviceStatus?.batteryLevel?.let { "电量 $it%（上报）" }
        ?: "电量未知"
    val version = if (cloud.latestVersion > 0) "云端 v${cloud.latestVersion}" else "云端无画面"
    return "$battery · $version" + if (connected) "" else " · 未连蓝牙"
}

private fun screenSummary(state: DeviceConsoleState, connected: Boolean): String {
    if (!connected) return "连接后可查看与管理设备上的画面"
    val pin = state.pinned
    val pinText = when {
        !state.pinnedRead -> "置顶未读取"
        pin == null -> "置顶读取失败"
        pin.mode == BleProtocol.PinMode.OFF -> "未置顶"
        else -> "置顶：${pin.describe()}"
    }
    val fileText = if (state.files.isEmpty()) "文件未查询" else "${state.files.size} 个文件"
    return "$pinText · $fileText"
}

private fun networkSummary(state: DeviceConsoleState, connected: Boolean): String {
    val wifi = state.status?.takeIf { connected }
        ?.let { if (it.wifiConnected) "Wi-Fi 已连接" else "Wi-Fi 未连接" }
        ?: if (connected) "Wi-Fi 未读取" else "连接后可配网"
    val pulled = state.syncStatus?.lastManifestQuery?.takeIf { it.isNotBlank() }
        ?.let { "上次取图 $it" } ?: "设备从未来取图"
    return "$wifi · $pulled"
}

private fun hardwareSummary(state: DeviceConsoleState, connected: Boolean): String {
    if (!connected) return "休眠、唤醒计划与轮询周期，需先连接"
    // 三种情况分开说：没查过、查了没回、查到了。都写「未读取」会让一次失败的查询
    // 看起来像还没开始查，用户只会一直等。
    val sleep = state.sleepMode?.let { if (it) "休眠开" else "休眠关" }
        ?: if (state.sleepRead) "休眠读取失败" else "休眠未读取"
    val wake = state.deviceWake?.let {
        if (it.slotMinutes.isEmpty()) "无每日唤醒点" else "${it.slotMinutes.size} 个唤醒点"
    } ?: "唤醒计划未读取"
    val poll = state.pollSeconds?.let { "轮询 ${formatSeconds(it)}" } ?: "轮询未读取"
    return "$sleep · $wake · $poll"
}

// ==================== 连接卡 / 连接条 ====================

@Composable
private fun ConnectionCard(
    connected: Boolean,
    connectionState: ConnectionState,
    deviceName: String,
    wifiConnected: Boolean?,
    batteryPercent: Int?,
    lastContactMinutes: Int?,
    discovered: List<DeviceInfo>,
    scanning: Boolean,
    busy: Boolean,
    onToggleScan: () -> Unit,
    onConnect: (DeviceInfo) -> Unit,
    onDisconnect: () -> Unit
) {
    SectionCard {
        // 设备名退到第二行、三个状态图标占第一行：进这一页最先要判断的是「设备现在什么情况」，
        // 而设备名在只有一台设备时不携带信息。
        DeviceStatusIcons(
            bleConnected = connected,
            wifiConnected = wifiConnected,
            batteryPercent = batteryPercent,
            lastContactMinutes = lastContactMinutes
        )
        Spacer(Modifier.height(6.dp))
        Text(
            if (connected) deviceName.ifBlank { "设备" } else "未连接蓝牙",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        if (connectionState is ConnectionState.Error) {
            Text(
                connectionState.message,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(top = 4.dp)
            )
        }
        if (!connected) {
            // 保留这两句：设备扫不到时最容易被当成坏了，而"按侧键唤醒"这个动作
            // 用户无从推断。电量能不连蓝牙看到这件事同样只能明说。
            Hint("设备平时深度休眠、不广播蓝牙，扫不到时按一下设备侧键再试。\n电量与同步记录不连蓝牙也能看（状态总览）。")
        }

        Spacer(Modifier.height(12.dp))

        if (!connected) {
            Button(onClick = onToggleScan, modifier = Modifier.fillMaxWidth(), enabled = !busy) {
                Icon(Icons.Default.Bluetooth, null, Modifier.size(18.dp))
                Spacer(Modifier.width(6.dp))
                Text(if (scanning) "停止扫描" else "扫描设备")
            }
            discovered.forEach { dev ->
                Spacer(Modifier.height(8.dp))
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(dev.name, style = MaterialTheme.typography.bodyMedium)
                        Text(
                            "${dev.address}  RSSI ${dev.rssi}",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                    Button(onClick = { onConnect(dev) }, enabled = !busy) { Text("连接") }
                }
            }
        } else {
            OutlinedButton(
                onClick = onDisconnect,
                modifier = Modifier.fillMaxWidth(),
                enabled = !busy
            ) {
                Icon(Icons.Default.BluetoothDisabled, null, Modifier.size(18.dp))
                Spacer(Modifier.width(6.dp))
                Text("断开连接")
            }
        }
    }
}

/** 二级页顶部的精简硬件状态。与一级卡同一套图标，只是去掉扫描与断开按钮。 */
@Composable
private fun ConnectionStrip(
    connected: Boolean,
    wifiConnected: Boolean?,
    batteryPercent: Int?,
    lastContactMinutes: Int?
) {
    Surface(
        color = if (connected) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.accents.warning.container,
        shape = MaterialTheme.shapes.small,
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
            DeviceStatusIcons(
                bleConnected = connected,
                wifiConnected = wifiConnected,
                batteryPercent = batteryPercent,
                lastContactMinutes = lastContactMinutes
            )
            // 这一句只在未连接时有用：它解释了为什么下面的按钮是灰的。
            if (!connected) {
                Spacer(Modifier.height(4.dp))
                Text(
                    "需要蓝牙的操作已禁用",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}

@Composable
private fun StatusDot(connected: Boolean) {
    Box(
        Modifier
            .size(10.dp)
            .clip(CircleShape)
            .background(if (connected) OK_COLOR else MaterialTheme.colorScheme.outline)
    )
}

/**
 * 槽位名说成人话。未知槽位原样返回而不是显示「其他」——后端将来加了新槽位时，
 * 原样显示至少还能看出是什么，而「其他」把信息抹掉了。
 */
private fun slotLabel(slot: String): String = when (slot) {
    "today" -> "今日总览"
    "breakfast" -> "早餐做法"
    "lunch" -> "午餐做法"
    "dinner" -> "晚餐做法"
    "current" -> "单张画面"
    else -> slot
}

/**
 * 后端没有登记这台设备的 `device_token` 时的警示。
 *
 * 值得占据页面最上方，因为这是**下发链路彻底不通**，而其他表现全是「一切正常」：发布成功、
 * 版本号自增、同步状态正常，设备也在联网重试，只是每次拉 manifest 都被 401 拒绝（那条 401
 * 只出现在容器日志和设备串口里）。
 *
 * 能一键修复是因为写进设备的那个令牌本来就存在 App 本地（`prov_device_token`），登记只是把
 * 它 POST 给后端。换后端、新数据库、卷被重建时都会走到这条路径。
 */
@Composable
private fun DeviceTokenAlert(
    lastContactMinutes: Int?,
    savedToken: String,
    busy: Boolean,
    onRegister: () -> Unit,
    onProvision: () -> Unit
) {
    Card(
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.errorContainer
        ),
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    Icons.Default.ErrorOutline,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onErrorContainer,
                    modifier = Modifier.size(20.dp)
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    "设备连接需要修复",
                    style = MaterialTheme.typography.titleSmall,
                    color = MaterialTheme.colorScheme.onErrorContainer
                )
            }
            Spacer(Modifier.height(6.dp))
            Text(
                "云端暂时无法验证这台设备的取图请求，所以屏幕不会更新。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onErrorContainer
            )
            // 设备来过的证据要说出来：它把「设备没联网」这个猜测直接排除掉，
            // 让人不必去折腾路由器和配网。
            lastContactMinutes?.let {
                Spacer(Modifier.height(4.dp))
                Text(
                    "设备${describeMinutesAgo(it)}来过云端，说明它已经联网。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onErrorContainer
                )
            }
            Spacer(Modifier.height(10.dp))
            if (savedToken.isNotBlank()) {
                Button(
                    onClick = onRegister,
                    enabled = !busy,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("修复设备连接")
                }
            } else {
                // 本地也没有凭据：那就只能重新配网了，因为设备侧的凭据只写不可读，
                // 后端无法从设备那边取回来。这时给一个诚实的出口而不是一个点了没用的按钮。
                Text(
                    "本机没有保存这台设备的连接凭据，需要重新配网。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onErrorContainer
                )
                Spacer(Modifier.height(8.dp))
                Button(
                    onClick = onProvision,
                    enabled = !busy,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("重新配置设备联网")
                }
            }
        }
    }
}

/**
 * 蓝牙 / Wi-Fi / 电量三态一行。
 *
 * 三者的**可知性不同**，图标必须如实反映，否则会把「不知道」画成「没有」：
 *   - 蓝牙：App 自己的连接状态，永远已知。
 *   - Wi-Fi：BLE `0x43` 是实时状态；未连蓝牙时只展示后端最近联系时间，不推断当前在线。
 *   - 电量：BLE 实时优先，未连蓝牙时用设备上次取图时上报的值兜底。
 */
@Composable
private fun DeviceStatusIcons(
    bleConnected: Boolean,
    wifiConnected: Boolean?,
    batteryPercent: Int?,
    lastContactMinutes: Int?
) {
    val link = wifiLinkStateOf(wifiConnected, lastContactMinutes)
    Row(verticalAlignment = Alignment.CenterVertically) {
        StatusIconLabel(
            icon = if (bleConnected) Icons.Default.Bluetooth else Icons.Default.BluetoothDisabled,
            label = if (bleConnected) "已连接" else "未连接",
            tint = if (bleConnected) OK_COLOR else INACTIVE_COLOR
        )
        Spacer(Modifier.width(18.dp))
        StatusIconLabel(
            icon = when (link) {
                is WifiLinkState.Connected -> Icons.Default.Wifi
                is WifiLinkState.SeenAgo -> Icons.Default.CloudQueue
                else -> Icons.Default.WifiOff
            },
            label = when (link) {
                is WifiLinkState.Connected -> "已联网"
                is WifiLinkState.Disconnected -> "未联网"
                is WifiLinkState.SeenAgo -> describeMinutesAgo(link.minutes)
                is WifiLinkState.Unknown -> "未知"
            },
            tint = when (link) {
                is WifiLinkState.Connected -> OK_COLOR
                is WifiLinkState.Disconnected -> WARN_COLOR
                is WifiLinkState.SeenAgo -> MaterialTheme.colorScheme.onSurfaceVariant
                is WifiLinkState.Unknown -> INACTIVE_COLOR
            }
        )
        Spacer(Modifier.width(18.dp))
        BatteryIcon(batteryPercent)
    }
}

/**
 * 设备上某张画面的缩略图。
 *
 * 图**不是**从设备读的：BLE 协议里没有「读取设备上 .film 内容」的命令。这里走后端
 * `display/preview.png` 把已发布的 .film 反解成 PNG —— 设备上的图正是从那儿下载的。
 *
 * 因此有一类图必然取不到：BLE 直传上去的手绘与相册照片，后端没有副本。那种情况显示占位图标，
 * 显示错的图比显示「没有预览」糟得多。
 */
@Composable
private fun FilmThumbnail(
    client: SfmBackendClient,
    /** 一套画面里的某一张；null = 取该版本的单张（旧的 slot='current' 发布）。 */
    fileName: String?,
    version: Int?
) {
    val context = LocalContext.current
    // 两个 remember 都以 client.config 为 key：用户在设置页改了地址或 Token 后如果不重算，
    // 这一页会继续用旧地址和旧鉴权头请求，而界面上只表现为"缩略图加载不出来"。
    val url = remember(client.config, fileName, version) {
        client.previewUrl(
            name = fileName,
            version = version,
            width = THUMB_REQUEST_WIDTH,
            orientation = "portrait"
        )
    }
    val auth = remember(client.config) { client.authorizationHeader() }
    // 点击缩略图放大：查看器里再请求一张不限宽的原图，缩略图本身只有 THUMB 宽。
    var zoom by remember { mutableStateOf(false) }

    Box(
        modifier = Modifier
            .size(width = THUMB_W, height = THUMB_H)
            .clip(MaterialTheme.shapes.extraSmall)
            .background(MaterialTheme.accents.track)
            .pressableCard(
                onClick = { zoom = true },
                enabled = url != null,
                scaleDown = 0.99f
            ),
        contentAlignment = Alignment.Center
    ) {
        if (url == null) {
            // 后端没配（预览要靠后端反解）。给图标而不是空白：空白看起来像加载卡住了。
            Icon(
                Icons.Default.ImageNotSupported,
                contentDescription = "未配置后端，无法预览",
                tint = INACTIVE_COLOR,
                modifier = Modifier.size(16.dp)
            )
            return@Box
        }
        val request = remember(url, auth) {
            ImageRequest.Builder(context)
                .data(url)
                // Coil 用的是它自己的 HTTP 客户端，鉴权头必须在这里挂上，
                // 否则每张预览都会 401 而界面上只表现为"加载不出来"。
                .apply { auth?.let { addHeader("Authorization", it) } }
                .crossfade(true)
                .build()
        }
        SubcomposeAsyncImage(
            model = request,
            contentDescription = "$fileName 的预览",
            modifier = Modifier.fillMaxSize(),
            loading = {
                FilmPreviewLoading(compact = true, modifier = Modifier.fillMaxSize())
            },
            error = {
                // 最常见的原因就是"这张是 BLE 直传的自定义图，后端没有副本"（404）。
                Text(
                    "无预览",
                    style = MaterialTheme.typography.labelSmall,
                    color = INACTIVE_COLOR
                )
            }
        )
    }

    if (zoom && url != null) {
        val fullUrl = remember(client.config, fileName, version) {
            client.previewUrl(name = fileName, version = version, orientation = "portrait")
        }
        val fullRequest = remember(fullUrl, auth) {
            ImageRequest.Builder(context)
                .data(fullUrl)
                .apply { auth?.let { addHeader("Authorization", it) } }
                .crossfade(true)
                .build()
        }
        AppSheet(
            title = "发布画面预览",
            onDismiss = { zoom = false },
        ) {
            SubcomposeAsyncImage(
                model = fullRequest,
                contentDescription = "$fileName 的大图预览",
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(2f / 3f),
                loading = {
                    FilmPreviewLoading(compact = false, modifier = Modifier.fillMaxSize())
                },
                error = {
                    Text(
                        "后端没有这张画面",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                },
            )
        }
    }
}

/** 与实体屏幕同构的菜单骨架，避免预览打开时出现脱离产品语境的大转圈。 */
@Composable
private fun FilmPreviewLoading(compact: Boolean, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier
            .clip(MaterialTheme.shapes.extraSmall)
            .background(MaterialTheme.colorScheme.surfaceContainer)
            .padding(if (compact) Spacing.xs else Spacing.md),
        verticalArrangement = Arrangement.spacedBy(if (compact) Spacing.xs else Spacing.sm)
    ) {
        Box(
            Modifier
                .fillMaxWidth()
                .height(if (compact) Spacing.xs else Spacing.md)
                .clip(MaterialTheme.shapes.extraSmall)
                .background(MaterialTheme.accents.brand.container)
        )
        repeat(3) { index ->
            Box(
                Modifier
                    .fillMaxWidth(if (index == 1) 0.72f else 0.9f)
                    .height(if (compact) Spacing.xs else Spacing.sm)
                    .clip(MaterialTheme.shapes.extraSmall)
                    .background(MaterialTheme.accents.track)
            )
        }
        Spacer(Modifier.weight(1f))
        LinearProgressIndicator(
            modifier = Modifier.fillMaxWidth(),
            color = MaterialTheme.accents.brand.solid,
            trackColor = MaterialTheme.accents.track
        )
    }
}

@Composable
private fun StatusIconLabel(icon: ImageVector, label: String, tint: Color) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Icon(icon, contentDescription = label, tint = tint, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(4.dp))
        Text(label, style = MaterialTheme.typography.labelMedium, color = tint)
    }
}

/**
 * 中空电池图标，百分比数字画在电池框内。
 *
 * 用数字而不是填充条：电量决定的是「要不要现在去充」，需要具体值。数字放在框内是为了让这一格
 * 与另外两格宽度接近，三个状态在视觉上并列。
 *
 * 正极那一小块必须画：只画圆角矩形加数字看起来像标签而不是电池。
 */
@Composable
private fun BatteryIcon(percent: Int?) {
    val tint = when {
        percent == null -> INACTIVE_COLOR
        percent <= LOW_BATTERY_PERCENT -> MaterialTheme.colorScheme.error
        else -> OK_COLOR
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(
            modifier = Modifier.size(width = 30.dp, height = 16.dp),
            contentAlignment = Alignment.Center
        ) {
            Canvas(Modifier.fillMaxSize()) {
                val stroke = 1.5.dp.toPx()
                val capW = 2.5.dp.toPx()
                val bodyW = size.width - capW
                drawRoundRect(
                    color = tint,
                    topLeft = Offset(stroke / 2, stroke / 2),
                    size = Size(bodyW - stroke, size.height - stroke),
                    cornerRadius = CornerRadius(2.dp.toPx()),
                    style = Stroke(width = stroke)
                )
                val capH = size.height * 0.45f
                drawRoundRect(
                    color = tint,
                    topLeft = Offset(bodyW, (size.height - capH) / 2f),
                    size = Size(capW, capH),
                    cornerRadius = CornerRadius(1.dp.toPx())
                )
            }
            Text(
                percent?.toString() ?: "--",
                style = MaterialTheme.typography.labelSmall.copy(fontSize = 9.sp),
                color = tint,
                // 往左偏一点，让数字在「电池主体」里居中而不是含正极一起居中。
                modifier = Modifier.padding(end = 3.dp)
            )
        }
        Spacer(Modifier.width(3.dp))
        Text("%", style = MaterialTheme.typography.labelMedium, color = tint)
    }
}

// ==================== 二级页 1：状态总览 ====================

/**
 * 云端记录与 BLE 实时状态。
 *
 * 两者分成两张卡：数据来源与可用前提不同 —— 云端那份电量是设备拉 manifest 时顺带上报的，
 * 不需要蓝牙；混进同一张卡会让用户以为看电量必须先连上蓝牙。
 */
@Composable
private fun StatusSection(
    state: DeviceConsoleState,
    connected: Boolean,
    onRetryCloud: () -> Unit
) {
    ConsoleCard("云端最近快照", subtitle = "设备联网时上报；不等同于 BLE 实时状态") {
        if (state.cloudLoading) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
                Spacer(Modifier.width(6.dp))
                Text("正在读取…", style = MaterialTheme.typography.bodySmall)
            }
            Spacer(Modifier.height(6.dp))
        }

        val cloud = state.syncStatus
        when {
            state.cloudError != null -> {
                Text(
                    state.cloudError!!,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error
                )
                TextButton(onClick = onRetryCloud, enabled = !state.busy) { Text("重试") }
            }

            cloud == null -> Text(
                "尚未读到云端记录。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            else -> {
                InfoRow("设备编号", cloud.deviceId.ifBlank { "—" })
                val reported = cloud.deviceStatus
                InfoRow("电量（上报）", reported?.batteryLevel?.let { "$it%" } ?: "从未上报")
                reported?.firmwareVersion?.takeIf { it.isNotBlank() }?.let {
                    InfoRow("固件版本", it)
                }
                // 时间一律按字符串原样显示。后端同一响应里两种格式并存（UTC 的
                // "Y-m-d H:M:S" 与带微秒的本地 ISO），单一 pattern 解析必然在其中一种上
                // 抛异常，而这些值只是给人看的。
                InfoRow(
                    "快照时间",
                    reported?.statusReportedAt?.takeIf { it.isNotBlank() } ?: "从未上报"
                )
                reported?.statusAgeMinutes?.let { age ->
                    InfoRow("快照年龄", describeMinutesAgo(age))
                    if (age > CLOUD_STALE_MINUTES) {
                        Text(
                            "该快照可能已过期，请等待设备下次联网或按 FN 键连接。",
                            style = MaterialTheme.typography.labelSmall,
                            color = WARN_COLOR
                        )
                    }
                }
                reported?.lastSeen?.takeIf { it.isNotBlank() }?.let { InfoRow("最近可见", it) }
                InfoRow(
                    "已应用版本",
                    reported?.appliedVersion?.let { "v$it" } ?: "未上报"
                )
                InfoRow(
                    "休眠模式",
                    reported?.sleepMode?.let { if (it) "开启" else "关闭" } ?: "未上报"
                )
                InfoRow(
                    "唤醒时间",
                    reported?.wakeSlots?.joinToString(" / ") {
                        BleProtocol.formatMinuteOfDay(it)
                    }?.ifBlank { "无" } ?: "未上报"
                )
                InfoRow(
                    "兜底周期",
                    reported?.wakeFallbackMinutes?.let { "$it 分钟" } ?: "未上报"
                )
                InfoRow(
                    "轮询周期",
                    reported?.pollIntervalSeconds?.let(::formatSeconds) ?: "未上报"
                )
                InfoRow(
                    "云端最新版本",
                    if (cloud.latestVersion > 0) "v${cloud.latestVersion}" else "从未发布"
                )
                cloud.latestUploadTime?.takeIf { it.isNotBlank() }?.let {
                    InfoRow("发布时间", it)
                }
                cloud.lastManifestQuery?.takeIf { it.isNotBlank() }?.let {
                    InfoRow("设备上次取图", it)
                }

                // slots 回答的是「为什么设备到点不换图」里最常见的那种情况：
                // 云端这一版只有一张总览，设备手里就没有三餐详情可切。
                Spacer(Modifier.height(6.dp))
                val slots = cloud.slots
                val complete = FULL_SLOTS.all { it in slots }
                Text(
                    "本版包含：" +
                        if (slots.isEmpty()) "（无）"
                        else slots.joinToString("、") { slotLabel(it) },
                    style = MaterialTheme.typography.bodySmall,
                    fontWeight = FontWeight.Medium
                )
                Text(
                    if (complete) "四张齐全，设备会按时段自动切图。"
                    else "缺 ${FULL_SLOTS.filterNot { it in slots }
                        .joinToString("、") { slotLabel(it) }}，" +
                        "设备不会按时段换图。到「联网与同步」发一整套。",
                    style = MaterialTheme.typography.labelSmall,
                    color = if (complete) OK_COLOR else WARN_COLOR
                )

                if (cloud.recentEvents.isNotEmpty()) {
                    Spacer(Modifier.height(10.dp))
                    Text("最近同步事件", style = MaterialTheme.typography.titleSmall)
                    // 只取最近几条：它回答"设备到底来拉过没有"，
                    // 再往前的历史在这个页面上没有决策价值。
                    cloud.recentEvents.take(RECENT_EVENT_LIMIT).forEach { ev ->
                        Text(
                            buildString {
                                append(ev.time?.takeIf { it.isNotBlank() } ?: "?")
                                append("  ")
                                append(syncEventLabel(ev.event))
                                ev.version?.let { append("  v$it") }
                                ev.detail?.takeIf { it.isNotBlank() }?.let { append("  —— $it") }
                            },
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(top = 2.dp)
                        )
                    }
                }
            }
        }
    }

    ConsoleCard("实时状态", needsBle = true, connected = connected) {
        val st = state.status.takeIf { connected }
        if (st == null) {
            Text(
                if (connected) "点右上角刷新读取。" else "连接后可读取 Wi-Fi、电量与忙碌状态。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        } else {
            InfoRow(
                "Wi-Fi", if (st.wifiConnected) "已连接" else "未连接",
                if (st.wifiConnected) OK_COLOR else null
            )
            InfoRow("电量", "${st.batteryPercent}%")
            InfoRow(
                "已应用版本",
                if (st.lastAppliedVersion > 0) "v${st.lastAppliedVersion}" else "未同步"
            )
            InfoRow("忙碌", if (st.busy) "是（传输/刷屏中）" else "否")
            if (st.lastSyncError.isNotBlank()) {
                InfoRow("最近错误", st.lastSyncError, MaterialTheme.colorScheme.error)
            }
            // 云端版本必须和设备已应用的版本一起看：固件的门控是
            // `manifest.version > last_applied || force`，云端版本号 ≤ 设备已应用版本时设备会
            // 正常拉 manifest、判定「已是最新」、然后什么都不做，两端日志都看不出异常。
            // 后端数据目录被重建（版本从 1 重新开始）时必然撞上这种情况。
            //
            // 版本号取自 sync-status，不单独拉 manifest：两个接口给的是同一个值。
            state.syncStatus?.latestVersion?.takeIf { it > 0 }?.let { cloudVer ->
                val stale = cloudVer <= st.lastAppliedVersion
                InfoRow(
                    "对比云端",
                    if (stale) "云端 v$cloudVer 不高于设备，普通同步不下载"
                    else "云端 v$cloudVer 更新，同步即可下载",
                    if (stale) MaterialTheme.colorScheme.error else OK_COLOR
                )
            }
        }
    }
}

private fun syncEventLabel(event: String): String = when (event) {
    "manifest_query" -> "查询清单"
    "download" -> "下载画面"
    "apply" -> "应用成功"
    "auth_failed" -> "鉴权失败"
    "upload", "upload_set" -> "云端发布"
    else -> event.ifBlank { "未知事件" }
}

// ==================== 二级页 2：画面管理 ====================

@Composable
private fun ScreenSection(
    state: DeviceConsoleState,
    connected: Boolean,
    transfer: BleTransfer,
    run: (String, suspend () -> String) -> Unit,
    onOpenLayoutPicker: () -> Unit,
    /** 用于取画面预览图（BLE 协议读不到设备上的 .film 内容，只能从后端反解）。 */
    client: SfmBackendClient
) {
    // 提到两张卡之外：「清空全部画面」那句警告也要知道当前 pin 的是哪个文件。
    val pin = state.pinned

    // ---- 置顶画面 ----
    // 只读 + 取消，**不在这里配置**：置顶要选一张具体的图，而选图与画图在「自定义画面」
    // 页里。把配置入口重复一份，就得在两处各维护一套四种模式的表单与校验。
    ConsoleCard("置顶画面", needsBle = true, connected = connected) {
        when {
            !connected -> Text(
                "连接后可查看设备当前的置顶设置。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            !state.pinnedRead -> Text(
                "点右上角刷新读取。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            pin == null -> Text(
                "读取失败（固件可能不支持置顶）。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error
            )

            else -> {
                InfoRow("状态", pin.describe())
                if (pin.mode != BleProtocol.PinMode.OFF) {
                    InfoRow("文件", pin.fileName.ifBlank { "—" })
                }
            }
        }
        Hint("在首页「自定义画面」里画图或选图，发送时即可设置置顶。")

        if (pin != null && pin.mode != BleProtocol.PinMode.OFF) {
            Spacer(Modifier.height(8.dp))
            OutlinedButton(
                onClick = {
                    run("取消置顶（0x4C）") {
                        if (transfer.clearPinnedFilm()) {
                            state.pinned = transfer.queryPinnedFilm()
                            state.pinnedRead = true
                            "✓ 已取消，下次自动选图恢复菜单逻辑"
                        } else "✗ 失败，设备仍在置顶"
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                enabled = connected && !state.busy
            ) { Text("取消置顶") }
        }
    }

    // ---- 后端已发布的画面（不需要蓝牙）----
    //
    // 单独一张卡：下面那张「设备上的画面」要连蓝牙才有内容（文件列表只能靠 BLE 查），而设备
    // 平时深睡。后端手里有每一版的 .film，反解成 PNG 就能看，不需要设备参与。
    val published = state.syncStatus
    if (published == null || published.latestVersion <= 0) {
        // 空态必须占位，不能整张卡消失：「后端还没发布过任何画面」与「后端发了但预览取不到」
        // 的下一步完全不同（去触发下发 vs 去查后端地址/令牌）。
        ConsoleCard("后端已发布的画面") {
            Hint(
                when {
                    published == null ->
                        "还没读到后端同步状态。先在「设置 → 冰箱贴后端」填好地址与 Token，" +
                            "再回到本页刷新一次。"
                    else ->
                        "后端还没有为这台设备发布过任何画面，所以没有可预览的内容 —— " +
                            "这不是预览坏了。可在下方「立即下发」触发一次，或等每日定时下发。"
                }
            )
        }
    } else {
        ConsoleCard(
            "后端已发布的画面",
            subtitle = "v${published.latestVersion} · ${published.slotFiles.size.takeIf { it > 0 } ?: 1} 张"
        ) {
            if (published.slotFiles.isEmpty()) {
                // 单张发布（旧 slot='current'）没有槽位文件名，用不带 name 的预览地址取该版本主图。
                FilmThumbnail(client = client, fileName = null, version = published.latestVersion)
                Hint("这一版是单张发布，设备不会按时段自动切图。")
            } else {
                published.slotFiles.forEach { sf ->
                    Row(
                        Modifier.fillMaxWidth().padding(vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        FilmThumbnail(
                            client = client,
                            fileName = sf.name,
                            version = published.latestVersion
                        )
                        Spacer(Modifier.width(10.dp))
                        Column(Modifier.weight(1f)) {
                            Text(slotLabel(sf.slot), style = MaterialTheme.typography.bodyMedium)
                            Text(
                                sf.name,
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }
            }
            // 说清这是"后端发的"而不是"设备屏幕上的"：设备可能还没下载这一版
            // （深睡未唤醒、或令牌没登记被 401 拒），把两者混为一谈会让人以为屏幕已经换了。
            Hint(
                buildString {
                    append("这是后端最新发布的那一版，不一定等于屏幕当前显示的。")
                    published.deviceLastContactMinutes?.let {
                        append("设备${describeMinutesAgo(it)}来取过图。")
                    }
                }
            )
        }
    }

    // ---- 设备上的画面 ----
    ConsoleCard("设备上的画面", needsBle = true, connected = connected) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(
                onClick = {
                    run("查询文件列表") {
                        try {
                            state.files = transfer.queryFileList()
                            state.filesRead = true
                            state.fileListError = null
                            "✓ 共 ${state.files.size} 个文件"
                        } catch (e: Exception) {
                            state.files = emptyList()
                            state.filesRead = false
                            state.fileListError = e.message ?: "查询失败"
                            throw e
                        }
                    }
                },
                modifier = Modifier.weight(1f),
                enabled = connected && !state.busy
            ) { Text("查询列表") }

            OutlinedButton(
                onClick = {
                    run("按时段显示（0x47）") {
                        if (transfer.displayByMealTime()) "✓ 已下发，刷屏约 20 秒" else "✗ 失败"
                    }
                },
                modifier = Modifier.weight(1f),
                enabled = connected && !state.busy
            ) {
                Icon(Icons.Default.Schedule, null, Modifier.size(16.dp))
                Spacer(Modifier.width(4.dp))
                Text("按时段显示")
            }
        }
        // 只说设备的行为，不列三个时间窗口的具体区间 —— 那三段时间在固件里是常量，
        // 用户改不了，写在这里只是让人多读一行。
        Hint("「按时段显示」让设备按当前钟点自己选图：做饭时段显示那一餐做法，其余显示今日总览。")

        state.fileListError?.let {
            Text(it, style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.error)
        }
        if (connected && state.files.isEmpty() && !state.filesRead && state.fileListError == null) {
            Hint("查询后可在这里看缩略图并点任意一张立即上屏。")
        } else if (state.filesRead && state.files.isEmpty()) {
            Hint("设备已返回列表，当前没有画面文件。")
        }
        // 预览按**设备当前应用的版本**取，而不是后端最新版本：设备上的 1_today.film 可能是
        // 几个版本前下载的，拿最新版本反解出来的是另一张图 —— 那种"预览与屏幕不一致"比
        // 没有预览更容易误导人。version 为 0/未知时不传，后端回落到最新版并由下面的
        // 提示说明这一点。
        val previewVersion = state.status?.lastAppliedVersion?.takeIf { it > 0 }
        if (state.files.isNotEmpty()) {
            Hint(
                if (previewVersion != null) {
                    "缩略图按设备当前版本 v$previewVersion 反解。手绘/相册直传的图后端没有副本，显示占位。"
                } else {
                    "设备版本未知（先在状态总览刷新一次），缩略图暂按后端最新版本显示，可能与屏幕不一致。"
                }
            )
        }
        state.files.forEach { f ->
            Spacer(Modifier.height(8.dp))
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                FilmThumbnail(client = client, fileName = f.name, version = previewVersion)
                Spacer(Modifier.width(10.dp))
                Text(
                    "${f.id}. ${f.name}",
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.weight(1f)
                )
                TextButton(
                    onClick = {
                        run("显示 ${f.name}") {
                            if (transfer.displayFilm(f.id)) "✓ 已切换显示" else "✗ 失败"
                        }
                    },
                    enabled = connected && !state.busy
                ) { Text("显示") }
                IconButton(
                    onClick = {
                        run("删除 ${f.name}") {
                            val ok = transfer.deleteFile(f.id)
                            // 删除后设备侧 id 会重排，必须重新查询，
                            // 否则按旧 id 继续操作会删错文件。
                            if (ok) {
                                state.files = transfer.queryFileList()
                                state.filesRead = true
                                state.fileListError = null
                                "✓ 已删除，列表已刷新"
                            } else "✗ 删除失败"
                        }
                    },
                    enabled = connected && !state.busy
                ) { Icon(Icons.Default.Delete, contentDescription = "删除") }
            }
        }

        Spacer(Modifier.height(8.dp))
        OutlinedButton(
            onClick = {
                run("清空全部画面（0x45）") {
                    if (transfer.clearAllFilms()) {
                        state.files = emptyList()
                        state.filesRead = true
                        state.fileListError = null
                        "✓ 已清空。不清屏，当前画面保留到下一张上屏"
                    } else "✗ 失败"
                }
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = connected && !state.busy,
            colors = ButtonDefaults.outlinedButtonColors(
                contentColor = MaterialTheme.colorScheme.error
            )
        ) {
            Icon(Icons.Default.DeleteSweep, null, Modifier.size(18.dp))
            Spacer(Modifier.width(6.dp))
            Text("清空全部画面")
        }
        // 清空会把置顶引用的那张一起删掉，而置顶记录还在 —— 固件此时回落到菜单逻辑，
        // 但设备页仍会显示"置顶中"。提前说一句，免得被当成置顶失效。
        if (pin != null && pin.mode != BleProtocol.PinMode.OFF) {
            Hint("清空会删掉置顶的 ${pin.fileName}，置顶将失去目标。")
        }
    }

    // ---- 版式 ----
    // 放这里而不是全局设置页：它回答"这台设备的画面长什么样"，属于设备设置。
    // 且它改的是云端偏好、不依赖 BLE，所以恒可点。
    ConsoleCard("画面版式") {
        Button(onClick = onOpenLayoutPicker, modifier = Modifier.fillMaxWidth()) {
            Icon(Icons.AutoMirrored.Filled.ViewQuilt, null, Modifier.size(18.dp))
            Spacer(Modifier.width(6.dp))
            Text("版式与预览")
        }
        Hint("选竖向 / 横向等排版并查看真实预览。改版式不需要连蓝牙。")
    }
}

// ==================== 二级页 3：联网与同步 ====================

@Composable
private fun NetworkSection(
    state: DeviceConsoleState,
    connected: Boolean,
    repository: LocalRepository,
    client: SfmBackendClient,
    transfer: BleTransfer,
    run: (String, suspend () -> String) -> Unit,
    refreshCloud: suspend () -> Unit,
    refreshDevice: suspend () -> Unit,
    onProvisionWifi: () -> Unit
) {
    ConsoleCard(
        "配网",
        needsBle = true,
        connected = connected,
        subtitle = "把 Wi-Fi 与取图地址写进设备"
    ) {
        Button(
            onClick = onProvisionWifi,
            modifier = Modifier.fillMaxWidth(),
            enabled = connected && !state.busy
        ) {
            Icon(Icons.Default.Wifi, null, Modifier.size(18.dp))
            Spacer(Modifier.width(6.dp))
            Text(if (state.status?.wifiConnected == true) "重新配置联网" else "配置设备联网")
        }

        // ---- 用保存的参数重新配网 ----
        // 已知 NVS schema 会由固件迁移；保留这个恢复入口用于恢复出厂、记录损坏或换设备，
        // 避免重新手输 manifest_url 与凭据。
        val savedSsid = repository.getProvisionSsid()
        val savedPassword = repository.getProvisionPassword()
        val savedToken = repository.getProvisionDeviceToken()
        val manifestUrl = repository.buildManifestUrl()
        val deviceId = repository.getSfmDeviceId()
        val missing = buildList {
            if (savedSsid.isBlank()) add("Wi-Fi 名称")
            if (savedToken.isBlank()) add("设备连接凭据")
            if (manifestUrl.isBlank()) add("后端地址")
        }

        Spacer(Modifier.height(8.dp))
        OutlinedButton(
            onClick = {
                run("用保存的参数重新配网") {
                    transfer.provisionWifi(
                        ssid = savedSsid,
                        password = savedPassword,
                        manifestUrl = manifestUrl,
                        deviceToken = savedToken,
                        deviceId = deviceId
                    ).fold(onSuccess = { "✓ $it" }, onFailure = { "✗ ${it.message}" })
                }
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = connected && !state.busy && missing.isEmpty()
        ) {
            Icon(Icons.Default.WifiProtectedSetup, null, Modifier.size(18.dp))
            Spacer(Modifier.width(6.dp))
            Text("用保存的参数重新配网")
        }
        Hint(
            if (missing.isEmpty()) "把 App 里保存的参数原样重发，不必手输长地址。\n将使用：$savedSsid → $manifestUrl"
            else "缺${missing.joinToString("、")}，请先用「配置设备联网」完整配一次。"
        )
    }

    ConsoleCard("同步", needsBle = true, connected = connected) {
        OutlinedButton(
            onClick = {
                run("立即同步（0x44）") {
                    // 说明"可能不下载"：版本门控下设备判定已是最新会跳过，
                    // 那是正常行为，不写清楚会被当成失败。
                    if (transfer.triggerSyncNow()) "✓ 已触发。设备若已是最新则不下载"
                    else "✗ 触发失败"
                }
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = connected && !state.busy
        ) {
            Icon(Icons.Default.Sync, null, Modifier.size(18.dp))
            Spacer(Modifier.width(6.dp))
            Text("立即同步（让设备去取图）")
        }

        Spacer(Modifier.height(8.dp))
        // 强制重拉：唯一能绕过版本门控的手段。固件门控是
        // `manifest.version > last_applied || force`，云端版本号不高于设备已应用版本时
        // 普通同步一定不下载。最常见的成因是后端数据目录被重建：版本从 1 重新开始，
        // 而设备还记着 v32。
        OutlinedButton(
            onClick = {
                run("强制重拉") {
                    val forced = client.forceNextSync()
                    if (forced.isFailure) {
                        "✗ 置位失败：${forced.exceptionOrNull()?.message}"
                    } else if (!transfer.triggerSyncNow()) {
                        "✓ 云端已置位 force；触发同步失败，等设备自己轮询也可以"
                    } else {
                        refreshDevice()
                        "✓ 已置位并触发，设备会无条件重新下载并刷屏（约 20 秒）"
                    }
                }
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = connected && !state.busy
        ) {
            Icon(Icons.Default.Refresh, null, Modifier.size(18.dp))
            Spacer(Modifier.width(6.dp))
            Text("强制重拉（版本号没变也下载）")
        }
        Hint("设备只在「云端版本 > 已应用版本」时下载。后端数据重建后版本从 1 开始，这时要强制重拉。")
    }

    // 「云端重新做图」与「设备去取图」是两件事，必须在 UI 上分清楚 ——
    // 混淆的后果是用户点了下发立刻点同步，设备拉到的还是旧版本。
    ConsoleCard("云端重新做图", subtitle = "不需要蓝牙") {
        Button(
            onClick = {
                run("立即重新生成并下发") {
                    // force = true：这是人按下的按钮，按钮上写的是"立即"。幂等窗口存在的
                    // 目的是不让每 5 分钟一跳的定时任务重复发，用它来回绝用户就是
                    // "点了没反应"——而界面只会显示"近期已下发过"，看不出是设计如此。
                    client.triggerPublish(null, force = true).fold(
                        // 原样展示后端那句话：发了几台、哪一餐只有后端知道，
                        // 自造的"已下发"会盖掉真实结果。
                        onSuccess = { detail -> refreshCloud(); "✓ $detail" },
                        onFailure = { "✗ ${it.message}" }
                    )
                }
            },
            modifier = Modifier.fillMaxWidth(),
            enabled = !state.busy
        ) {
            Icon(Icons.Default.Cloud, null, Modifier.size(18.dp))
            Spacer(Modifier.width(6.dp))
            Text("立即重新生成并下发")
        }
        Hint("顺序：先点这里让后端重新渲染一整套（约 30 秒），做完再点「立即同步」。反过来点设备拉到的还是旧版本。")
    }
}

// ==================== 二级页 4：硬件配置 ====================

/**
 * 休眠 / 唤醒时间表 / 兜底周期 / 轮询周期 —— 设备的电源与联网节奏，全部走 BLE 直写。
 *
 * 与设置页「用餐与推送」的分工：那边改的是**后端什么时候做图并发布**（写数据库），这边改的是
 * **设备什么时候醒来去取**（写 NVS）。两者由同一份输入、同一个算式推导
 * （[WakePlanner.computeWakeSlots]），但没有自动同步：改完用餐时间必须回到这里重下一次唤醒计划。
 *
 * 不自动下发是因为下发要求 BLE 正连着，而用户改设置时手机通常不在设备旁边。
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun HardwareSection(
    state: DeviceConsoleState,
    connected: Boolean,
    transfer: BleTransfer,
    run: (String, suspend () -> String) -> Unit
) {
    val reported = state.syncStatus?.deviceStatus
    ConsoleCard("休眠", needsBle = true, connected = connected) {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(Modifier.weight(1f)) {
                Text("休眠模式", style = MaterialTheme.typography.bodyMedium)
                val displayedSleep = if (connected) state.sleepMode else reported?.sleepMode
                val readFailed = connected && state.sleepMode == null && state.sleepRead
                Text(
                    displayedSleep?.let {
                        if (it) "开启（省电，推荐）" else "关闭（常亮，仅调试）"
                    }?.let { if (state.sleepMode == null) "$it · 云端快照" else it }
                        ?: if (readFailed) "读取失败，可重试" else "未上报",
                    style = MaterialTheme.typography.labelSmall,
                    color = if (readFailed) MaterialTheme.colorScheme.error
                    else MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
            // 读失败时给重试而不是只给开关：此时开关的位置是**默认值**而非设备实况，
            // 直接拨动等于拿一个猜出来的当前值去覆盖设备。
            if (connected && state.sleepMode == null && state.sleepRead) {
                OutlinedButton(
                    onClick = {
                        run("重新读取休眠状态") {
                            state.status = transfer.queryStatus()
                            state.sleepMode = state.status?.sleepMode ?: transfer.querySleepMode()
                            state.sleepMode?.let { "✓ 休眠${if (it) "开" else "关"}" }
                                ?: "✗ 仍读取失败"
                        }
                    },
                    enabled = connected && !state.busy
                ) { Text("重试") }
            } else {
                Switch(
                    checked = state.sleepMode ?: reported?.sleepMode ?: true,
                    onCheckedChange = { want ->
                        run(if (want) "开启休眠" else "关闭休眠") {
                            if (transfer.setSleepMode(want)) {
                                state.status = transfer.queryStatus()
                                state.sleepMode = state.status?.sleepMode ?: transfer.querySleepMode()
                                state.sleepRead = true
                                when (state.sleepMode) {
                                    want -> "✓ 已设置并回读确认"
                                    null -> "✗ 命令已发送，但休眠状态回读失败"
                                    else -> "✗ 设备回读状态与设置值不一致"
                                }
                            } else "✗ 失败"
                        }
                    },
                    enabled = connected && !state.busy
                )
            }
        }
        // 关掉休眠的后果是实测过的（一夜耗尽电池并反复重启），这句必须留在界面上 ——
        // 它是用户按下开关前唯一能得到的警告。
        Hint("休眠管「要不要深睡」，唤醒计划管「什么时候醒」。关掉休眠实测一夜耗尽电池，请保持开启。")
    }

    // ---- 唤醒时间表 ----
    ConsoleCard(
        "唤醒时间表",
        needsBle = true,
        connected = connected,
        subtitle = "每餐前提前一段时间醒来取图，兜底周期只用于错过计划时自愈"
    ) {
        val schedule = state.mealSchedule
        /* 唤醒点由「三餐时间 − 提前量」算出，与设置页显示的是同一个算式、同一份输入
         * （见 WakePlanner.computeWakeSlots）。只下发一个「每日更新时间」槽位会让设备一天只醒
         * 一次，中午和晚上那两餐的新画面要等到次日才上屏。 */
        val leadMinutes = WakePlanner.nearestLeadChoice(schedule?.leadMinutes ?: WakePlanner.LEAD_DEFAULT)
        val slots = remember(schedule, leadMinutes) {
            WakePlanner.computeWakeSlots(schedule?.schedule.orEmpty(), leadMinutes)
        }

        if (!connected) {
            InfoRow(
                "云端唤醒点",
                reported?.wakeSlots?.joinToString(" / ") {
                    BleProtocol.formatMinuteOfDay(it)
                }?.ifBlank { "无" } ?: "未上报"
            )
            InfoRow(
                "云端兜底周期",
                reported?.wakeFallbackMinutes?.let { "$it 分钟" } ?: "未上报"
            )
        }

        if (schedule == null) {
            Text(
                "尚未读到用餐时间（来自后端），无法推算唤醒点。",
                style = MaterialTheme.typography.bodySmall,
                color = WARN_COLOR
            )
        } else {
            InfoRow("提前取图", "$leadMinutes 分钟")
            schedule.schedule.forEach { item ->
                InfoRow(
                    item.label?.takeIf { it.isNotBlank() } ?: item.mealType,
                    "${item.mealTime} 切图"
                )
            }
            if (slots.isEmpty()) {
                Text(
                    "用餐时间都没填对。现在下发会清空设备上已有的计划唤醒点。",
                    style = MaterialTheme.typography.labelSmall,
                    color = WARN_COLOR
                )
            } else {
                Text(
                    "将下发 ${slots.size} 个唤醒点：" +
                        slots.joinToString(" / ") { BleProtocol.formatMinuteOfDay(it) },
                    style = MaterialTheme.typography.bodySmall,
                    fontWeight = FontWeight.Medium,
                    modifier = Modifier.padding(top = 4.dp)
                )
            }
        }

        Spacer(Modifier.height(12.dp))
        Text("兜底唤醒周期", style = MaterialTheme.typography.bodyMedium)
        Hint("计划唤醒失败时定期自愈；周期越短越耗电。")
        Spacer(Modifier.height(6.dp))
        val displayedFallback = if (connected) state.fallbackMinutes
            else reported?.wakeFallbackMinutes ?: state.fallbackMinutes
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp)
        ) {
            FALLBACK_CHOICES.forEach { (minutes, label) ->
                FilterChip(
                    selected = displayedFallback == minutes,
                    onClick = { state.fallbackMinutes = minutes },
                    label = { Text(label, style = MaterialTheme.typography.labelMedium) },
                    enabled = connected && !state.busy
                )
            }
        }
        if (displayedFallback == 0) {
            Text(
                "已选「关闭」：设备只在三餐前的计划时刻唤醒，错过后要等到下一餐。",
                style = MaterialTheme.typography.labelSmall,
                color = WARN_COLOR,
                modifier = Modifier.padding(top = 4.dp)
            )
        }

        Spacer(Modifier.height(12.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = {
                    run("设置唤醒计划（0x48）") {
                        val target = BleProtocol.WakeSchedule(slots, state.fallbackMinutes)
                        if (transfer.setWakeSchedule(target)) {
                            state.deviceWake = transfer.queryWakeSchedule()
                            state.wakeRead = true
                            val labels = target.slotLabels()
                            "✓ 已下发：唤醒点 " +
                                (if (labels.isEmpty()) "无" else labels.joinToString("/")) +
                                "，兜底 " +
                                (if (state.fallbackMinutes == 0) "关闭" else "${state.fallbackMinutes} 分钟")
                        } else "✗ 设置失败，设备仍是上一份时间表"
                    }
                },
                modifier = Modifier.weight(1f),
                // 用餐时间**读失败**时禁用（schedule == null）：那时 slots 是空的，但空的
                // 原因是"不知道"而不是"没有"。照发下去会把设备上原本好的每日唤醒点整份
                // 清掉（0x48 是全量替换），而用户只是想改兜底周期 —— 一次后端抽风就让
                // 设备从此不在饭点前醒，且页面上看不出发生了什么。
                enabled = connected && !state.busy && schedule != null
            ) { Text("设置唤醒计划") }

            OutlinedButton(
                onClick = {
                    run("查询唤醒计划（0x49）") {
                        state.deviceWake = transfer.queryWakeSchedule()
                        state.wakeRead = true
                        state.deviceWake?.let { ws ->
                            state.fallbackMinutes = ws.fallbackMinutes
                            "✓ 设备唤醒点 " +
                                (if (ws.slotMinutes.isEmpty()) "无"
                                else ws.slotLabels().joinToString("/")) +
                                "，兜底 ${ws.fallbackMinutes} 分钟"
                        } ?: "✗ 读取失败"
                    }
                },
                modifier = Modifier.weight(1f),
                enabled = connected && !state.busy
            ) { Text("读取设备当前") }
        }

        if (connected && state.wakeRead) {
            Spacer(Modifier.height(10.dp))
            val ws = state.deviceWake
            if (ws == null) {
                Text(
                    "读取设备唤醒计划失败。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error
                )
            } else {
                InfoRow(
                    "设备记着的唤醒点",
                    if (ws.slotMinutes.isEmpty()) "无" else ws.slotLabels().joinToString(" / ")
                )
                InfoRow(
                    "设备兜底周期",
                    if (ws.fallbackMinutes == 0) "关闭" else "${ws.fallbackMinutes} 分钟"
                )
            }
        }
    }

    // ---- 轮询周期 ----
    ConsoleCard(
        "轮询周期",
        needsBle = true,
        connected = connected,
        subtitle = "醒着时每隔多久查一次更新。越短越及时，也越耗电"
    ) {
        val displayedPoll = if (connected) state.pollSeconds else reported?.pollIntervalSeconds
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp)
        ) {
            POLL_CHOICES.forEach { (seconds, label) ->
                FilterChip(
                    selected = displayedPoll == seconds,
                    onClick = {
                        run("设置轮询周期 $label（0x4A）") {
                            // 显示固件**钳制后**的返回值而不是用户选的值：否则会出现
                            // "界面 5 秒、设备实际 30 秒"，现场调续航会据此得出完全
                            // 错误的结论。
                            val actual = transfer.setPollIntervalSeconds(seconds)
                            if (actual == null) {
                                "✗ 设置失败"
                            } else {
                                state.pollSeconds = actual
                                if (actual == seconds) "✓ 已设为 ${formatSeconds(actual)}"
                                else "✓ 已设为 ${formatSeconds(actual)}（固件把 ${formatSeconds(seconds)} 钳到了这个值）"
                            }
                        }
                    },
                    label = { Text(label, style = MaterialTheme.typography.labelMedium) },
                    enabled = connected && !state.busy
                )
            }
        }
        Spacer(Modifier.height(6.dp))
        InfoRow(
            if (state.pollSeconds == null) "云端快照" else "设备当前",
            displayedPoll?.let { formatSeconds(it) } ?: "未上报"
        )
    }
}

// ==================== 二级页 5：操作日志 ====================

/**
 * 设备操作大多没有立即可见的反馈（刷屏要 20 秒、同步可能因版本门控跳过），没有日志就只能
 * 靠猜命令有没有生效。
 *
 * 默认只显示最近一条：日常使用里唯一有用的是「我刚才这一下成没成」，整份历史是排障才需要的。
 */
@Composable
private fun LogSection(state: DeviceConsoleState) {
    var expanded by remember { mutableStateOf(false) }

    ConsoleCard("操作日志") {
        if (state.log.isEmpty()) {
            Text(
                "还没有操作记录。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            return@ConsoleCard
        }

        val shown = if (expanded) state.log else state.log.take(1)
        shown.forEach { LogLine(it) }

        Spacer(Modifier.height(4.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (state.log.size > 1) {
                TextButton(onClick = { expanded = !expanded }) {
                    Text(if (expanded) "收起" else "查看全部（${state.log.size} 条）")
                }
            }
            TextButton(onClick = { state.log = emptyList(); expanded = false }) {
                Text("清空")
            }
        }
    }
}

@Composable
private fun LogLine(line: String) {
    Text(
        line,
        style = MaterialTheme.typography.bodySmall,
        color = when {
            line.startsWith("✗") -> MaterialTheme.colorScheme.error
            line.startsWith("✓") -> OK_COLOR
            else -> MaterialTheme.colorScheme.onSurfaceVariant
        }
    )
}

// ==================== 内部构件 ====================

private val CARD_BG: Color
    @Composable @ReadOnlyComposable get() = MaterialTheme.colorScheme.surface
private val WARN_COLOR: Color
    @Composable @ReadOnlyComposable get() = MaterialTheme.accents.warning.onContainer
private val OK_COLOR: Color
    @Composable @ReadOnlyComposable get() = MaterialTheme.accents.success.solid

/** 状态未知（多为"没连蓝牙所以读不到"）。刻意用灰而不是红：那不是故障。 */
private val INACTIVE_COLOR: Color
    @Composable @ReadOnlyComposable get() = MaterialTheme.colorScheme.outline

/** 电量低于此值标红。20% 是"该充了但还能用一会儿"的位置。 */
private const val LOW_BATTERY_PERCENT = 20

/** 画面缩略图按设备实体方向显示。 */
private val THUMB_W = 44.dp
private val THUMB_H = 66.dp

/**
 * 向后端请求的缩略图像素宽度。
 *
 * 比显示尺寸大一截（66dp 在 3x 屏上约 198px）：按显示尺寸请求会在高密度屏上发虚。
 * 也不请求原图 792px —— 那一张 PNG 约 180KB，而 160px 只有 8KB，列表里有四张。
 */
private const val THUMB_REQUEST_WIDTH = 240

/** 一整套画面应有的槽位。缺任何一个都不会按时段自动切图。 */
private val FULL_SLOTS = listOf("today", "breakfast", "lunch", "dinner")

private const val RECENT_EVENT_LIMIT = 5
private const val LOG_LIMIT = 30
private const val CLOUD_STALE_MINUTES = 36 * 60

/** 兜底唤醒周期出厂默认值（分钟）。与固件默认一致。 */
private const val DEFAULT_FALLBACK_MINUTES = 1440

private val FALLBACK_CHOICES = listOf(
    720 to "12小时",
    DEFAULT_FALLBACK_MINUTES to "24小时",
    2880 to "48小时",
    0 to "关闭"
)

private val POLL_CHOICES = listOf(
    300 to "5 分钟",
    900 to "15 分钟",
    1800 to "30 分钟",
    3600 to "60 分钟",
    86400 to "24 小时"
)

/** 不带标题的容器卡。连接卡与入口列表用它。 */
@Composable
private fun SectionCard(content: @Composable ColumnScope.() -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = CARD_BG)
    ) {
        Column(Modifier.padding(16.dp), content = content)
    }
}

/**
 * 二级页里的功能卡。`needsBle` 的卡片在未连接时**显示但禁用**，并给一句提示。
 *
 * 刻意不隐藏：一个看不见的功能与不存在的功能没有区别，而这里想表达的是"连上就能用"。
 */
@Composable
private fun ConsoleCard(
    title: String,
    subtitle: String? = null,
    needsBle: Boolean = false,
    connected: Boolean = true,
    content: @Composable ColumnScope.() -> Unit
) {
    SectionCard {
        Text(title, style = MaterialTheme.typography.titleMedium)
        subtitle?.let {
            Text(
                it,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
        if (needsBle && !connected) {
            Text(
                "当前为云端只读快照；按设备 FN 键唤醒并连接后可修改",
                style = MaterialTheme.typography.labelSmall,
                color = WARN_COLOR,
                modifier = Modifier.padding(top = 4.dp)
            )
        }
        Spacer(Modifier.height(10.dp))
        content()
    }
}

/** 控件下面那行小字。存在的意义是把"说明最多两行"这条规矩变成一个统一的入口。 */
@Composable
private fun Hint(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 4.dp)
    )
}

// 唤醒点的三个纯函数（parseHhMm / normalizeMinute / computeWakeSlots）在
// device/WakePlanner.kt。**这里不留副本** —— 同一条时间算式存两份，早晚会在其中一份
// 修跨零点的 bug 而另一份没跟上，且两份都不会报错。

private fun formatSeconds(seconds: Int): String = when {
    seconds % 3600 == 0 -> "${seconds / 3600} 小时"
    seconds % 60 == 0 -> "${seconds / 60} 分钟"
    else -> "$seconds 秒"
}

@Composable
private fun InfoRow(label: String, value: String, valueColor: Color? = null) {
    Row(
        Modifier.fillMaxWidth().padding(vertical = 3.dp),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(
            label,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        Text(
            value,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Medium,
            color = valueColor ?: MaterialTheme.colorScheme.onSurface
        )
    }
}
