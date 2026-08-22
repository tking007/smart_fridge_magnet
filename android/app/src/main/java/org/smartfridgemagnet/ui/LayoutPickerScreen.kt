package org.smartfridgemagnet.ui

import org.smartfridgemagnet.ui.theme.accents
import androidx.compose.runtime.Composable
import androidx.compose.material3.MaterialTheme
import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import org.smartfridgemagnet.FridgeMagnetApp
import org.smartfridgemagnet.network.LayoutChoiceDto
import org.smartfridgemagnet.network.LayoutOptionChoicesDto
import org.smartfridgemagnet.network.LayoutOptionValueDto
import org.smartfridgemagnet.ui.components.ZoomableImageDialog
import org.smartfridgemagnet.ui.theme.Spacing
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 版式选择页：挑一套「设备画面长什么样」的排版，并看真实预览。
 *
 * 预览必须是**服务端渲染的真图**：这一页要回答的是「这个版式能放几道菜、菜名会不会被截断、竖版
 * 转过来还看得清吗」，示意图只能表达「分几栏」，而分几栏恰恰是唯一一眼就能从名字猜到的信息。
 * 后端 `/api/render/preview` 用的是与真正下发画面同一套排版代码。
 *
 * 预览是 PNG 而不是 `.film`：`.film` 是给固件的 4bit 索引格式，图片控件显示不了。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LayoutPickerScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    val app = context.applicationContext as FridgeMagnetApp
    val client = remember { app.sfmBackendClient }
    val scope = rememberCoroutineScope()

    val configured = client.config.configured

    var loadingPrefs by remember { mutableStateOf(false) }
    var prefsError by remember { mutableStateOf<String?>(null) }
    var layouts by remember { mutableStateOf<List<LayoutChoiceDto>>(emptyList()) }
    /** 设备当前生效的版式（云端偏好）。空串 = 还没读到。 */
    var appliedLayout by remember { mutableStateOf("") }
    /** 当前正在预览的版式。刻意与 [appliedLayout] 分开：看不等于用。 */
    var previewLayout by remember { mutableStateOf("") }
    var previewKind by remember { mutableStateOf(PREVIEW_KINDS.first().first) }
    var optionChoices by remember { mutableStateOf(LayoutOptionChoicesDto()) }
    var appliedOptions by remember { mutableStateOf<Map<String, String>>(emptyMap()) }
    var previewOptions by remember { mutableStateOf<Map<String, String>>(emptyMap()) }

    /**
     * 预览缓存，key = `"$layout|$kind"`。
     *
     * 必须缓存：每张预览都是一次网络往返 + 一次服务端 Pillow 渲染（实测数百毫秒起）。
     * 用户在四个 kind 标签之间来回切是这一页最常见的动作，不缓存就会把同一张图反复
     * 重新渲染，既慢也白耗后端。
     */
    val previews = remember { mutableStateMapOf<String, ImageBitmap>() }
    val previewErrors = remember { mutableStateMapOf<String, String>() }
    var loadingPreviewKey by remember { mutableStateOf<String?>(null) }
    /** 递增即触发当前 key 重新拉取（重试按钮用）。 */
    var previewRetryTick by remember { mutableIntStateOf(0) }
    /** 点击预览后要放大查看的图；非空即弹出全屏查看器。 */
    var zoomPreview by remember { mutableStateOf<ImageBitmap?>(null) }

    var applying by remember { mutableStateOf(false) }
    /** 操作结果原文。后端的幂等跳过说明也走这里原样展示。 */
    var message by remember { mutableStateOf<String?>(null) }
    var messageIsError by remember { mutableStateOf(false) }

    fun loadPrefs() {
        if (!configured || loadingPrefs) return
        loadingPrefs = true
        prefsError = null
        scope.launch {
            // 一次往返就能同时拿到「当前生效的版式」与「全部可选项」（后端 prefs 响应里
            // 带 layout_choices，与 /api/render/layouts 同源），所以不再单独拉一次清单。
            client.fetchDevicePrefs().fold(
                onSuccess = { dto ->
                    layouts = dto.layoutChoices
                    appliedLayout = dto.layout
                    optionChoices = dto.layoutOptionChoices
                    appliedOptions = dto.layoutOptions
                    if (previewOptions.isEmpty()) previewOptions = dto.layoutOptions
                    if (previewLayout.isBlank()) {
                        previewLayout = dto.layout.takeIf { it.isNotBlank() }
                            ?: dto.layoutChoices.firstOrNull()?.key.orEmpty()
                    }
                    loadingPrefs = false
                },
                onFailure = { e ->
                    // 退路：prefs 读不到时至少把可选清单列出来，让用户能看到有哪些版式、
                    // 也能看预览。此时"当前生效的是哪个"确实无从得知，不猜一个填上去 ——
                    // 猜错会让用户以为已经在用某个版式而不去应用。
                    client.fetchLayouts().fold(
                        onSuccess = { catalog ->
                            layouts = catalog.layouts
                            optionChoices = catalog.optionChoices
                            if (previewOptions.isEmpty()) {
                                previewOptions = catalog.optionDefaults
                            }
                            appliedLayout = ""
                            if (previewLayout.isBlank()) {
                                previewLayout = catalog.layouts.firstOrNull()?.key.orEmpty()
                            }
                            prefsError = "读取设备当前版式失败：${e.message}。" +
                                "下面是可选版式，但无法显示哪个正在生效。"
                        },
                        onFailure = { e2 ->
                            prefsError = "读取版式失败：${e.message ?: e2.message}"
                        }
                    )
                    loadingPrefs = false
                }
            )
        }
    }

    LaunchedEffect(Unit) { loadPrefs() }

    // 只拉「当前预览的版式 + 当前 kind」这一张，不在进页面时并发拉满全部组合：
    // 大多数用户只会查看其中几张，提前渲染其余图片没有收益。
    LaunchedEffect(previewLayout, previewKind, previewOptions, previewRetryTick) {
        val layout = previewLayout
        if (!configured || layout.isBlank()) return@LaunchedEffect
        val key = previewCacheKey(layout, previewKind, previewOptions)
        if (previews.containsKey(key)) return@LaunchedEffect
        loadingPreviewKey = key
        previewErrors.remove(key)
        client.fetchLayoutPreview(layout, previewKind, previewOptions).fold(
            onSuccess = { bytes ->
                // 解码放到后台：792×528 的 PNG 解码虽快，但它是纯 CPU 活，
                // 放在主线程上会在切标签时留下可感知的卡顿。
                val bitmap = withContext(Dispatchers.Default) {
                    runCatching { BitmapFactory.decodeByteArray(bytes, 0, bytes.size) }.getOrNull()
                }
                if (bitmap == null) {
                    previewErrors[key] = "预览图解码失败（返回了 ${bytes.size} 字节，但不是可解码的图片）"
                } else {
                    previews[key] = bitmap.asImageBitmap()
                }
            },
            onFailure = { e -> previewErrors[key] = e.message ?: "预览加载失败" }
        )
        loadingPreviewKey = null
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text("画面版式") },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background
                ),
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
                actions = {
                    IconButton(onClick = { loadPrefs() }, enabled = configured && !loadingPrefs) {
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
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            if (!configured) {
                // 后端未配置时整页只给一条引导：摆四张「加载失败」的卡片等于让用户自己去
                // 推断缺的是后端地址。
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.surfaceVariant
                    )
                ) {
                    Column(Modifier.padding(16.dp)) {
                        Text("尚未配置后端", style = MaterialTheme.typography.titleMedium)
                        Spacer(Modifier.height(6.dp))
                        Text(
                            "版式清单、预览图与设备偏好都存在后端。请先到「设置 → 冰箱贴后端」" +
                                "填写后端地址与 Token，再回到这一页。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
                Spacer(Modifier.height(16.dp))
                return@Column
            }

            message?.let { text ->
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = if (messageIsError) MaterialTheme.colorScheme.errorContainer
                        else MaterialTheme.accents.success.container
                    )
                ) {
                    Row(
                        Modifier.padding(14.dp).fillMaxWidth(),
                        verticalAlignment = Alignment.Top
                    ) {
                        Text(
                            text,
                            style = MaterialTheme.typography.bodySmall,
                            modifier = Modifier.weight(1f),
                            color = if (messageIsError) MaterialTheme.colorScheme.onErrorContainer
                            else MaterialTheme.accents.success.onContainer
                        )
                        TextButton(onClick = { message = null }) { Text("知道了") }
                    }
                }
            }

            prefsError?.let {
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.errorContainer
                    )
                ) {
                    Column(Modifier.padding(14.dp)) {
                        Text(
                            it,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onErrorContainer
                        )
                        Spacer(Modifier.height(6.dp))
                        TextButton(onClick = { loadPrefs() }) { Text("重试") }
                    }
                }
            }

            // ==================== 预览 ====================
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                Column(Modifier.padding(16.dp)) {
                    Text("预览", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "与真正下发到设备的画面同一套排版代码渲染，所见即所得。",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Spacer(Modifier.height(10.dp))

                    // kind 切换：同一个版式在总览与三餐详情上的排布差别很大，
                    // 只看总览决定不了"这个版式适不适合我家"。
                    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                        PREVIEW_KINDS.forEachIndexed { index, (key, label) ->
                            SegmentedButton(
                                selected = previewKind == key,
                                onClick = { previewKind = key },
                                shape = SegmentedButtonDefaults.itemShape(index, PREVIEW_KINDS.size),
                                label = { Text(label, style = MaterialTheme.typography.labelMedium) }
                            )
                        }
                    }

                    Spacer(Modifier.height(12.dp))

                    LayoutOptionControls(
                        choices = optionChoices,
                        values = previewOptions,
                        onChange = { key, value ->
                            previewOptions = previewOptions + (key to value)
                            message = null
                        }
                    )

                    Spacer(Modifier.height(Spacing.md))
                    val current = layouts.firstOrNull { it.key == previewLayout }
                    Text(
                        "正在预览：${current?.label?.ifBlank { previewLayout } ?: previewLayout.ifBlank { "—" }}",
                        style = MaterialTheme.typography.bodyMedium,
                        fontWeight = FontWeight.Medium
                    )
                    Spacer(Modifier.height(8.dp))

                    val key = previewCacheKey(previewLayout, previewKind, previewOptions)
                    val bitmap = previews[key]
                    val error = previewErrors[key]
                    Box(
                        Modifier
                            .fillMaxWidth()
                            .heightIn(min = 140.dp)
                            .clip(RoundedCornerShape(8.dp))
                            .background(MaterialTheme.accents.track),
                        contentAlignment = Alignment.Center
                    ) {
                        when {
                            bitmap != null -> Image(
                                bitmap = bitmap,
                                contentDescription = "版式预览（点击放大）",
                                // ContentScale.Fit 而不是 Crop：整张画布就是
                                // "这个版式能放几道菜"的全部信息，裁掉边缘正好裁掉了它。
                                contentScale = ContentScale.Fit,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .aspectRatio(bitmap.width.toFloat() / bitmap.height)
                                    .clickable { zoomPreview = bitmap }
                            )
                            loadingPreviewKey == key -> Column(
                                Modifier.padding(24.dp),
                                horizontalAlignment = Alignment.CenterHorizontally
                            ) {
                                CircularProgressIndicator(Modifier.size(28.dp))
                                Spacer(Modifier.height(8.dp))
                                Text("正在渲染预览…", style = MaterialTheme.typography.labelSmall)
                            }
                            error != null -> Column(
                                Modifier.padding(20.dp),
                                horizontalAlignment = Alignment.CenterHorizontally
                            ) {
                                Text(
                                    error,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.error
                                )
                                Spacer(Modifier.height(6.dp))
                                TextButton(onClick = { previewRetryTick++ }) { Text("重试") }
                            }
                            else -> Text(
                                "选择下方任一版式查看预览",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(24.dp)
                            )
                        }
                    }

                    if (current?.orientation == ORIENTATION_PORTRAIT) {
                        Spacer(Modifier.height(10.dp))
                        Card(
                            colors = CardDefaults.cardColors(containerColor = MaterialTheme.accents.warning.container)
                        ) {
                            Text(
                                "App 已按自然方向转正预览。设备屏幕物理上仍是横向像素，" +
                                    "使用此版式时请把冰箱贴顺时针旋转 90° 竖着看。",
                                style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.accents.warning.onContainer,
                                modifier = Modifier.padding(12.dp)
                            )
                        }
                    }
                }
            }

            // ==================== 版式列表 ====================
            if (loadingPrefs && layouts.isEmpty()) {
                Row(
                    Modifier.fillMaxWidth().padding(24.dp),
                    horizontalArrangement = Arrangement.Center
                ) { CircularProgressIndicator() }
            }

            layouts.forEach { layout ->
                val isAppliedLayout = layout.key == appliedLayout
                val isPreviewing = layout.key == previewLayout
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = if (isPreviewing) MaterialTheme.accents.info.container else MaterialTheme.colorScheme.surface
                    ),
                    border = if (isAppliedLayout) {
                        androidx.compose.foundation.BorderStroke(2.dp, MaterialTheme.accents.success.solid)
                    } else null,
                    onClick = { previewLayout = layout.key }
                ) {
                    Column(Modifier.padding(16.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text(
                                        layout.label.ifBlank { layout.key },
                                        style = MaterialTheme.typography.titleMedium
                                    )
                                    if (isAppliedLayout) {
                                        Spacer(Modifier.width(6.dp))
                                        Icon(
                                            Icons.Default.CheckCircle,
                                            contentDescription = "当前生效",
                                            tint = MaterialTheme.accents.success.solid,
                                            modifier = Modifier.size(18.dp)
                                        )
                                        Text(
                                            "当前生效",
                                            style = MaterialTheme.typography.labelSmall,
                                            color = MaterialTheme.accents.success.solid,
                                            modifier = Modifier.padding(start = 2.dp)
                                        )
                                    }
                                }
                                Spacer(Modifier.height(2.dp))
                                Text(
                                    orientationLabel(layout.orientation),
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant
                                )
                            }
                            if (isPreviewing) {
                                Text(
                                    "预览中",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.primary
                                )
                            }
                        }
                        layout.description?.takeIf { it.isNotBlank() }?.let {
                            Spacer(Modifier.height(6.dp))
                            Text(
                                it,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }

                        if (isPreviewing) {
                            // 版式没变时按钮仍在，但走 republish：屏幕没更新时用户需要一个
                            // 能再触发一遍的地方，否则只能干等下一次餐前自动发布。
                            val hasChanges =
                                !isAppliedLayout || previewOptions != appliedOptions
                            Spacer(Modifier.height(10.dp))
                            Button(
                                onClick = {
                                    if (applying) return@Button
                                    applying = true
                                    message = null
                                    scope.launch {
                                        client.updateDeviceLayout(
                                            layout.key, previewOptions, republish = !hasChanges
                                        ).fold(
                                            onSuccess = { dto ->
                                                appliedLayout = dto.layout.ifBlank { layout.key }
                                                appliedOptions = dto.layoutOptions
                                                messageIsError = false
                                                message = dto.displayUpdateDetail
                                                    ?: "版式已更新"
                                            },
                                            onFailure = { e ->
                                                messageIsError = true
                                                message = "切换失败：${e.message}"
                                            }
                                        )
                                        applying = false
                                    }
                                },
                                enabled = !applying,
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                Text(
                                    when {
                                        applying -> "正在应用并更新…"
                                        hasChanges -> "应用并更新四张菜单"
                                        else -> "重新下发四张菜单"
                                    }
                                )
                            }
                        }
                    }
                }
            }

            Spacer(Modifier.height(16.dp))
        }
    }

    zoomPreview?.let { bmp ->
        ZoomableImageDialog(onDismiss = { zoomPreview = null }) { modifier ->
            Image(
                bitmap = bmp,
                contentDescription = "版式预览大图",
                contentScale = ContentScale.Fit,
                modifier = modifier.fillMaxSize(),
            )
        }
    }
}

/** 预览的四个 kind，取值与后端 `/api/render/preview?kind=` 一致。默认第一个（今日总览）。 */
private val PREVIEW_KINDS = listOf(
    "overview" to "今日总览",
    "breakfast" to "早餐",
    "lunch" to "午餐",
    "dinner" to "晚餐"
)

private const val ORIENTATION_PORTRAIT = "portrait"

private fun previewCacheKey(
    layout: String,
    kind: String,
    options: Map<String, String>
): String = buildString {
    append(layout).append('|').append(kind)
    options.toSortedMap().forEach { (key, value) -> append('|').append(key).append('=').append(value) }
}

@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
@Composable
private fun LayoutOptionControls(
    choices: LayoutOptionChoicesDto,
    values: Map<String, String>,
    onChange: (String, String) -> Unit
) {
    if (choices.accent.isEmpty()) return
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
        Text("强调色", style = MaterialTheme.typography.labelLarge)
        FlowRow(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(Spacing.sm),
            verticalArrangement = Arrangement.spacedBy(Spacing.xs)
        ) {
            choices.accent.forEach { choice ->
                FilterChip(
                    selected = values["accent"] == choice.key,
                    onClick = { onChange("accent", choice.key) },
                    label = { Text(choice.label) },
                    leadingIcon = {
                        Box(
                            Modifier
                                .size(Spacing.lg)
                                .clip(CircleShape)
                                .background(accentSwatch(choice.key))
                        )
                    }
                )
            }
        }
        SegmentedLayoutOption("字号", "font_size", choices.fontSize, values, onChange)
        SegmentedLayoutOption("密度", "density", choices.density, values, onChange)
        SegmentedLayoutOption("分隔", "separator", choices.separator, values, onChange)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SegmentedLayoutOption(
    title: String,
    key: String,
    choices: List<LayoutOptionValueDto>,
    values: Map<String, String>,
    onChange: (String, String) -> Unit
) {
    if (choices.isEmpty()) return
    Column(verticalArrangement = Arrangement.spacedBy(Spacing.xs)) {
        Text(title, style = MaterialTheme.typography.labelLarge)
        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
            choices.forEachIndexed { index, choice ->
                SegmentedButton(
                    selected = values[key] == choice.key,
                    onClick = { onChange(key, choice.key) },
                    shape = SegmentedButtonDefaults.itemShape(index, choices.size),
                    label = { Text(choice.label, style = MaterialTheme.typography.labelMedium) }
                )
            }
        }
    }
}

@Composable
private fun accentSwatch(key: String): Color = when (key) {
    "red" -> MaterialTheme.accents.danger.solid
    "green" -> MaterialTheme.accents.success.solid
    "blue" -> MaterialTheme.accents.info.solid
    else -> MaterialTheme.colorScheme.outline
}

private fun orientationLabel(orientation: String): String = when (orientation) {
    ORIENTATION_PORTRAIT -> "竖向排版（需把设备转 90° 看）"
    "landscape" -> "横向排版（与设备朝向一致）"
    else -> "排版方向：$orientation"
}
