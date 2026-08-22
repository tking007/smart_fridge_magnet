package org.smartfridgemagnet.ui

import androidx.compose.material3.TopAppBarDefaults
import org.smartfridgemagnet.ui.theme.accents
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Key
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Router
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material.icons.filled.Wifi
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import org.smartfridgemagnet.FridgeMagnetApp
import org.smartfridgemagnet.data.LocalRepository
import org.smartfridgemagnet.transfer.BleTransfer
import org.smartfridgemagnet.transfer.ConnectionState
import kotlinx.coroutines.launch
import java.security.SecureRandom

/**
 * 设备 Wi-Fi 配网页面。
 *
 * 参数会存进 [LocalRepository] 并在进入页面时自动回填：固件把配网参数持久化在 NVS 并通过版本化
 * schema 迁移已知旧记录，App 保留一份加密副本便于设备恢复出厂或记录损坏后重新下发，不必重新手输
 * manifest_url。
 *
 * manifest_url 不让用户填，而是由后端基址与 device_id 推导后**只读展示**：它必须与后端实际地址
 * 一致，手抄一遍只会引入不一致，而这种错的表现是设备静默不更新。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WifiProvisionScreen(
    repository: LocalRepository,
    onBack: () -> Unit,
    onOpenSettings: () -> Unit
) {
    val context = LocalContext.current
    val app = context.applicationContext as FridgeMagnetApp
    val bleTransfer = remember { app.bleTransfer }
    val client = remember { app.sfmBackendClient }
    val coroutineScope = rememberCoroutineScope()
    val connectionState by bleTransfer.stateFlow.collectAsState()

    var ssid by remember { mutableStateOf(repository.getProvisionSsid()) }
    var password by remember { mutableStateOf(repository.getProvisionPassword()) }
    val deviceToken = remember {
        repository.getProvisionDeviceToken().ifBlank { generateDeviceToken() }
    }
    var showPassword by remember { mutableStateOf(false) }

    var busy by remember { mutableStateOf(false) }
    var resultMessage by remember { mutableStateOf<String?>(null) }
    var resultOk by remember { mutableStateOf(false) }
    var deviceConfig by remember { mutableStateOf<BleTransfer.ProvisionConfig?>(null) }

    val manifestUrl = repository.buildManifestUrl()
    val deviceId = repository.getSfmDeviceId()
    val bleConnected = connectionState is ConnectionState.Connected

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text("设备联网配置") },
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
                .verticalScroll(rememberScrollState())
                .padding(16.dp)
        ) {
            // ---- 前置条件：BLE 必须已连接 ----
            // 配网命令走 BLE（设备此时还没联网），所以未连接时直接说明去哪连，
            // 而不是让用户填完一堆字段再报「设备未连接」。
            if (!bleConnected) {
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.accents.warning.container)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(16.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Icon(Icons.Default.Error, contentDescription = null, tint = MaterialTheme.accents.warning.solid)
                        Spacer(Modifier.width(12.dp))
                        Column(modifier = Modifier.weight(1f)) {
                            Text("设备未通过蓝牙连接", style = MaterialTheme.typography.titleSmall)
                            // 指路必须指向真实入口：扫描/连接在设备控制台里，不在「发送到电子纸」页。
                            Text(
                                "配网命令通过蓝牙下发（设备此时尚未联网）。" +
                                    "请返回上一页「设备」，在顶部连接卡片里扫描并连接设备。",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                            Spacer(Modifier.height(8.dp))
                            // 与其让用户自己找返回键，直接给一个到位的按钮
                            OutlinedButton(onClick = onBack) { Text("去连接设备") }
                        }
                    }
                }
                Spacer(Modifier.height(16.dp))
            }

            // ---- Wi-Fi 凭据 ----
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("Wi-Fi 凭据", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "固件仅支持 2.4GHz 频段，5GHz 网络无法连接",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Spacer(Modifier.height(16.dp))

                    OutlinedTextField(
                        value = ssid,
                        onValueChange = { ssid = it },
                        label = { Text("Wi-Fi 名称（SSID）") },
                        leadingIcon = { Icon(Icons.Default.Wifi, contentDescription = null) },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true
                    )
                    Spacer(Modifier.height(12.dp))

                    OutlinedTextField(
                        value = password,
                        onValueChange = { password = it },
                        label = { Text("Wi-Fi 密码") },
                        leadingIcon = { Icon(Icons.Default.Key, contentDescription = null) },
                        trailingIcon = {
                            IconButton(onClick = { showPassword = !showPassword }) {
                                Icon(
                                    if (showPassword) Icons.Default.VisibilityOff
                                    else Icons.Default.Visibility,
                                    contentDescription = if (showPassword) "隐藏" else "显示"
                                )
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                        visualTransformation = if (showPassword) VisualTransformation.None
                        else PasswordVisualTransformation()
                    )
                }
            }

            Spacer(Modifier.height(16.dp))

            // ---- 取图地址（只读，由后端配置推导）----
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("取图地址", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "由「设置」里的后端地址与设备编号自动推导，无需手填",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Spacer(Modifier.height(12.dp))

                    if (manifestUrl.isBlank()) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(
                                Icons.Default.Error,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.error,
                                modifier = Modifier.size(18.dp)
                            )
                            Spacer(Modifier.width(8.dp))
                            Text(
                                "尚未配置后端地址",
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.error
                            )
                        }
                        Spacer(Modifier.height(8.dp))
                        OutlinedButton(onClick = onOpenSettings, modifier = Modifier.fillMaxWidth()) {
                            Icon(Icons.Default.Link, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(8.dp))
                            Text("前往设置填写后端地址")
                        }
                    } else {
                        Text("设备编号：$deviceId", style = MaterialTheme.typography.bodyMedium)
                        Spacer(Modifier.height(4.dp))
                        Text(
                            manifestUrl,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                        // 长度上限来自固件 SFM_MANIFEST_URL_MAX(128)，超了会被截断且不易察觉。
                        val urlBytes = manifestUrl.toByteArray(Charsets.US_ASCII).size
                        if (urlBytes > 127) {
                            Spacer(Modifier.height(8.dp))
                            Text(
                                "地址过长（$urlBytes 字节，上限 127），固件会截断。请缩短后端域名或设备编号。",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.error
                            )
                        }
                    }
                }
            }

            Spacer(Modifier.height(24.dp))

            // ---- 下发 ----
            Button(
                onClick = {
                    resultMessage = null
                    busy = true
                    // 先存后发：即使配网失败（密码错、信号弱），下次进来也不用重新输入。
                    // **只存 Wi-Fi 凭据，不存 token** —— token 那份记录的含义是"设备里
                    // 现在就是这个值"，要等 BLE 真的写进去才能落盘（见 saveProvisionDeviceToken）。
                    // Keystore 可能因用户刚改锁屏密码而失效。存不下来不该崩，但要说清 ——
                    // 用户下次进来会发现输入框空着，得知道原因。
                    val saved = repository.saveProvisionWifi(ssid, password)
                    if (saved.isFailure) {
                        resultOk = false
                        resultMessage = "Wi-Fi 密码没能安全保存在本机（${saved.exceptionOrNull()?.message}）。" +
                            "配网仍会继续，但下次需要重新输入。"
                    }
                    coroutineScope.launch {
                        // ── 配网是两半，缺任何一半设备都拿不到画面 ──
                        // 前半：把 Wi-Fi 凭据与令牌写进设备（BLE）。
                        // 后半：把同一个令牌登记到后端，否则后端会 401 拒绝设备的每次拉取。
                        // 之前只做前半，于是「配网成功」与「设备永远不更新」可以同时成立。
                        val bleResult = bleTransfer.provisionWifi(
                            ssid = ssid,
                            password = password,
                            manifestUrl = manifestUrl,
                            deviceToken = deviceToken,
                            deviceId = deviceId
                        )
                        if (bleResult.isFailure) {
                            // 设备侧没写成功就不登记后端：两边不一致比两边都空更难查
                            // （日志里是 auth failed 而不是 not registered，方向完全不同）。
                            resultOk = false
                            resultMessage = bleResult.exceptionOrNull()?.message ?: "配网失败"
                            busy = false
                            return@launch
                        }

                        // BLE 已经写进去了，此刻才是"设备里就是这个 token"成立的时刻。
                        // 设备页的「登记设备令牌」按这份记录去修后端，所以它必须准。
                        val tokenSaved = repository.saveProvisionDeviceToken(deviceToken)

                        val registered = if (client.config.configured) {
                            client.registerDeviceToken(deviceToken)
                        } else {
                            Result.failure(IllegalStateException("「设置」里还没填完后端配置"))
                        }
                        resultOk = registered.isSuccess && tokenSaved.isSuccess
                        resultMessage = buildString {
                            append(bleResult.getOrNull() ?: "已写入设备")
                            append('\n')
                            if (registered.isSuccess) {
                                append("✓ 设备连接已就绪，下次唤醒即可取图")
                            } else {
                                append("✗ 设备连接凭据同步失败：")
                                append(registered.exceptionOrNull()?.message ?: "未知错误")
                                append("\n设备联网信息已写入，但暂时无法从云端取图。")
                                append("请确认「设置」里的后端配置，然后重新下发一次。")
                            }
                        }
                        busy = false
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                enabled = !busy && bleConnected && ssid.isNotBlank() && manifestUrl.isNotBlank()
            ) {
                if (busy) {
                    CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(8.dp))
                    Text("配网中（最多等 15 秒）…")
                } else {
                    Icon(Icons.AutoMirrored.Filled.Send, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("下发配置并连接")
                }
            }

            if (resultMessage != null) {
                Spacer(Modifier.height(12.dp))
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = if (resultOk) MaterialTheme.accents.success.container else MaterialTheme.accents.danger.container
                    )
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(12.dp),
                        verticalAlignment = Alignment.Top
                    ) {
                        Icon(
                            if (resultOk) Icons.Default.CheckCircle else Icons.Default.Error,
                            contentDescription = null,
                            tint = if (resultOk) MaterialTheme.accents.success.solid else MaterialTheme.colorScheme.error
                        )
                        Spacer(Modifier.width(8.dp))
                        Text(resultMessage!!, style = MaterialTheme.typography.bodyMedium)
                    }
                }
            }

            Spacer(Modifier.height(24.dp))

            // ---- 回读设备当前配置 ----
            // 用来区分两种看起来一样的情况：配置从未下发成功，与配置曾成功但被固件重置
            // （NVS 记录因字段容量变化被判不兼容 → 整份恢复出厂默认）。没有这个回读，
            // 两者都表现为「设备不联网」，只能靠猜。
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text("设备当前已记住的配置", style = MaterialTheme.typography.titleMedium)
                        IconButton(
                            onClick = {
                                coroutineScope.launch {
                                    deviceConfig = bleTransfer.queryProvisionConfig()
                                }
                            },
                            enabled = bleConnected
                        ) {
                            Icon(Icons.Default.Refresh, contentDescription = "刷新")
                        }
                    }

                    val cfg = deviceConfig
                    if (cfg == null) {
                        Text(
                            if (bleConnected) "点击右上角刷新读取" else "需先连接设备",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    } else {
                        Spacer(Modifier.height(8.dp))
                        ProvisionInfoRow("Wi-Fi 开关", if (cfg.wifiEnabled) "已开启" else "未开启（配网会自动开启）")
                        ProvisionInfoRow("已记住的 SSID", cfg.ssid.ifBlank { "（空）" })
                        ProvisionInfoRow("已记住的取图地址", cfg.manifestUrl.ifBlank { "（空）" })
                        if (cfg.ssid.isBlank() && cfg.manifestUrl.isBlank()) {
                            Spacer(Modifier.height(8.dp))
                            Text(
                                "设备侧配置为空。若此前配过，说明固件的参数记录被重置" +
                                    "（多见于重新烧写固件后），重新下发一次即可。",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }
            }

            Spacer(Modifier.height(24.dp))
        }
    }
}

/**
 * 生成一个设备令牌。
 *
 * 32 位十六进制 = 128 bit 熵，够强，且远低于固件 `SFM_DEVICE_TOKEN_MAX`(128) 的容量，
 * 不会触发 `BleProtocol` 的长度拒绝。只用 `[0-9a-f]` 是为了一次避开三处麻烦：BLE 载荷
 * 要求可打印 ASCII、令牌要放进 HTTP 头、排障时还要能贴进 shell 命令 —— base64 的
 * `+/=` 在后两处都需要额外转义。
 */
private fun generateDeviceToken(): String {
    val bytes = ByteArray(16)
    SecureRandom().nextBytes(bytes)
    return bytes.joinToString("") { "%02x".format(it) }
}

@Composable
private fun ProvisionInfoRow(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(label, style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Spacer(Modifier.width(12.dp))
        Text(value, style = MaterialTheme.typography.bodySmall)
    }
}
