package org.smartfridgemagnet.ui

import androidx.compose.runtime.Composable
import androidx.compose.material3.MaterialTheme
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.CloudUpload
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.*
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import org.smartfridgemagnet.FridgeMagnetApp
import org.smartfridgemagnet.transfer.BleTransfer
import org.smartfridgemagnet.transfer.ConnectionState
import org.smartfridgemagnet.transfer.DeviceInfo
import org.smartfridgemagnet.transfer.DeviceType
import org.smartfridgemagnet.transfer.TransferProgress
import org.smartfridgemagnet.transfer.TransferState
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RecipeTransferScreen(
    recipe: RecipeResult?,
    onBack: () -> Unit,
    onDone: () -> Unit,
    /** 后端未配置时的跳转出口：只报"尚未配置"而不给去处，用户无从下手。 */
    onOpenSettings: () -> Unit = {}
) {
    val context = LocalContext.current
    val app = context.applicationContext as FridgeMagnetApp
    val coroutineScope = rememberCoroutineScope()
    val bleTransfer = remember { app.bleTransfer }
    val connectionState by bleTransfer.stateFlow.collectAsState()
    val transferProgress by bleTransfer.progressFlow.collectAsState()
    val discoveredDevices by bleTransfer.discoveredDevices.collectAsState()

    var isScanning by remember { mutableStateOf(false) }

    // 与设备页同一处理：BleTransfer 是单例，不停扫会让扫描活过页面并撞上系统的
    // 「30 秒内 5 次」节流，之后扫描静默失败。
    DisposableEffect(Unit) {
        onDispose {
            bleTransfer.stopScan()
        }
    }
    var selectedDevice by remember { mutableStateOf<DeviceInfo?>(null) }
    var statusMessage by remember { mutableStateOf("选择传输方式") }
    /** 后端相关失败后显示「去设置」快捷入口 —— 多数这类失败要改的就是那三个字段。 */
    var needSettings by remember { mutableStateOf(false) }
    var filmData by remember { mutableStateOf<ByteArray?>(null) }

    // 画面字节向后端取，不在本地画：渲染只有后端一份（跨端契约），两条通道因此显示同一张图。
    // 代价是 BLE 直传也需要后端可达 —— 下面的提示直接告诉用户去配后端，而不是画一张跟设备上
    // 不一样的图糊过去。
    LaunchedEffect(recipe) {
        if (recipe == null || filmData != null) return@LaunchedEffect
        val dishes = buildDishLines(recipe)
        if (dishes.isEmpty()) {
            statusMessage = "菜谱内容为空，无法渲染画面"
            return@LaunchedEffect
        }
        if (!app.sfmBackendClient.config.configured) {
            statusMessage = "画面由后端渲染，请先到「设置 → 冰箱贴后端」填写地址"
            needSettings = true
            return@LaunchedEffect
        }
        statusMessage = "正在向后端请求渲染画面…"
        app.sfmBackendClient.renderFilmBytes(dishes = dishes).fold(
            onSuccess = { bytes ->
                filmData = bytes
                needSettings = false
                statusMessage = "画面已就绪（${bytes.size} 字节），选择设备发送"
            },
            onFailure = { e ->
                statusMessage = "❌ 渲染画面失败：${e.message}"
                needSettings = true
            }
        )
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text("发送到电子纸") },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background
                ),
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                }
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            // 状态卡片
            Card(modifier = Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("📡 传输状态", style = MaterialTheme.typography.titleSmall)
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(statusMessage, style = MaterialTheme.typography.bodyMedium)
                    if (transferProgress.state == TransferState.SENDING_DATA) {
                        Spacer(modifier = Modifier.height(8.dp))
                        LinearProgressIndicator(
                            progress = { transferProgress.percentage / 100f },
                            modifier = Modifier.fillMaxWidth()
                        )
                        Text("${transferProgress.percentage}%", style = MaterialTheme.typography.labelSmall)
                    }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            // BLE 扫描
            Card(modifier = Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("蓝牙设备", style = MaterialTheme.typography.titleSmall)
                    Spacer(modifier = Modifier.height(8.dp))

                    Button(
                        onClick = {
                            if (isScanning) {
                                bleTransfer.stopScan()
                                isScanning = false
                            } else {
                                bleTransfer.startScan()
                                isScanning = true
                            }
                        },
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Text(if (isScanning) "停止扫描" else "扫描设备")
                    }

                    if (discoveredDevices.isNotEmpty()) {
                        Spacer(modifier = Modifier.height(8.dp))
                        discoveredDevices.forEach { device ->
                            Row(
                                modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Column {
                                    Text(device.name, style = MaterialTheme.typography.bodyMedium)
                                    Text("${device.address} | RSSI: ${device.rssi}", style = MaterialTheme.typography.labelSmall)
                                }
                                Button(
                                    onClick = {
                                        selectedDevice = device
                                        bleTransfer.stopScan()
                                        isScanning = false
                                        statusMessage = "已选择: ${device.name}，点击发送"
                                    },
                                    enabled = selectedDevice?.address != device.address
                                ) {
                                    Text(if (selectedDevice?.address == device.address) "已选" else "选择")
                                }
                            }
                        }
                    }
                }
            }

            Spacer(modifier = Modifier.height(16.dp))

            // 发送按钮
            Button(
                onClick = {
                    if (selectedDevice == null) {
                        statusMessage = "请先选择设备"
                        return@Button
                    }
                    if (filmData == null) {
                        statusMessage = ".film 数据未生成"
                        return@Button
                    }
                    coroutineScope.launch {
                        statusMessage = "正在连接 ${selectedDevice!!.name}..."
                        // connect() 会等到写特征就绪、MTU 有结论才返回，所以既不需要 delay 猜时间，
                        // 也必须检查返回值 —— 链路慢时 writeCharacteristic 仍可能是 null。
                        if (!bleTransfer.connect(selectedDevice!!)) {
                            statusMessage = "❌ 连接失败：" +
                                ((connectionState as? ConnectionState.Error)?.message ?: "请重试")
                            return@launch
                        }
                        statusMessage = "正在发送..."
                        val success = bleTransfer.sendFilm(filmData!!, "recipe.film")
                        if (success) {
                            statusMessage = "✅ 发送成功！"
                        } else {
                            statusMessage = "❌ 发送失败"
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                enabled = selectedDevice != null && filmData != null
            ) {
                Icon(Icons.AutoMirrored.Filled.Send, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(modifier = Modifier.width(8.dp))
                Text("发送到电子纸")
            }

            Spacer(modifier = Modifier.height(8.dp))

            // ---- 经后端下发（Wi-Fi 通道）----
            // 与上面的蓝牙直传是两条独立通道，各有取舍，所以并列而非替换：
            //   蓝牙：不依赖设备联网，但要手机在旁边、且 209KB 要传约 27 秒；
            //   后端：设备自己按 manifest 拉取，人不用在场，但设备必须已配网。
            // 渲染放在后端还有一层好处：编码实现只有一份（后端的 film_encoder 已与网页
            // 调试工具做过逐字节 SHA-256 交叉验证，也被固件校验器正反验证过），
            // App 本地再实现一遍就多一处会漂移的实现。
            OutlinedButton(
                onClick = {
                    if (recipe == null) {
                        statusMessage = "没有可下发的菜谱"
                        return@OutlinedButton
                    }
                    // 前置检查放在点击时而非禁用按钮：按钮灰着不告诉原因，用户只会以为坏了。
                    if (!app.sfmBackendClient.config.configured) {
                        statusMessage = "后端地址为空，请到「设置 → 冰箱贴后端」填写后重试"
                        needSettings = true
                        return@OutlinedButton
                    }
                    needSettings = false
                    coroutineScope.launch {
                        statusMessage = "正在按当前版式发布四张菜单…"
                        val dishes = buildDishLines(recipe)
                        if (dishes.isEmpty()) {
                            statusMessage = "菜谱内容为空，无法渲染"
                            return@launch
                        }
                        app.sfmBackendClient.triggerPublish(force = true).fold(
                            onSuccess = { detail ->
                                statusMessage = "✅ $detail。设备将在下次同步时更新" +
                                    "（连上蓝牙可点“立即同步”催一次）"
                            },
                            onFailure = { e ->
                                statusMessage = "❌ 后端发布失败：${e.message}"
                                needSettings = true
                            }
                        )
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                enabled = recipe != null
            ) {
                Icon(Icons.Default.CloudUpload, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(modifier = Modifier.width(8.dp))
                Text("经后端下发（设备自己联网取图）")
            }

            if (needSettings) {
                Spacer(modifier = Modifier.height(8.dp))
                TextButton(onClick = onOpenSettings, modifier = Modifier.fillMaxWidth()) {
                    Text("前往设置检查后端配置")
                }
            }

            Spacer(modifier = Modifier.height(8.dp))

            // 立即同步：绕过等待设备的轮询周期。需要蓝牙在连，因为这条命令走 BLE。
            OutlinedButton(
                onClick = {
                    coroutineScope.launch {
                        statusMessage = "正在触发设备立即同步…"
                        val ok = bleTransfer.triggerSyncNow()
                        statusMessage = if (ok) {
                            "已触发同步，设备开始拉取（电子纸刷新约 20 秒）"
                        } else {
                            "触发失败，请确认设备蓝牙已连接"
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                enabled = connectionState is ConnectionState.Connected
            ) {
                Icon(Icons.Default.Sync, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(modifier = Modifier.width(8.dp))
                Text("立即同步")
            }

            Spacer(modifier = Modifier.height(8.dp))

            // 完成按钮
            OutlinedButton(
                onClick = onDone,
                modifier = Modifier.fillMaxWidth()
            ) {
                Icon(Icons.Default.Check, contentDescription = null, modifier = Modifier.size(18.dp))
                Spacer(modifier = Modifier.width(8.dp))
                Text("完成")
            }
        }
    }
}

/**
 * 把菜谱转成后端渲染所需的 dishes 行。
 *
 * 格式是 "分类|内容"，竖线前的标签由后端排版时单独着色（见 menu_render.py）。
 * 版式容量由后端统一处理，App 不得提前截断推荐结果。
 */
internal fun buildDishLines(recipe: RecipeResult): List<String> {
    val lines = mutableListOf<String>()
    recipe.breakfast.forEach { lines.add("早餐|$it") }
    recipe.lunch.forEach { lines.add("午餐|$it") }
    recipe.dinner.forEach { lines.add("晚餐|$it") }
    return lines
}
