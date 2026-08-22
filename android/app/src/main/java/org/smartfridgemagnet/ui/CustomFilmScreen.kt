package org.smartfridgemagnet.ui

import org.smartfridgemagnet.ui.theme.accents
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.Composable
import androidx.compose.material3.MaterialTheme
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.Brush
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.PhotoLibrary
import androidx.compose.material.icons.automirrored.outlined.Undo
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke as DrawStroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import org.smartfridgemagnet.FridgeMagnetApp
import org.smartfridgemagnet.ui.theme.Spacing
import org.smartfridgemagnet.film.FilmDecoder
import org.smartfridgemagnet.ui.components.ZoomableImageDialog
import org.smartfridgemagnet.transfer.BleProtocol
import org.smartfridgemagnet.transfer.ConnectionState
import org.smartfridgemagnet.transfer.TransferState
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream

/**
 * 自定义画面：把手绘涂鸦或相册照片发到电子纸上。
 *
 * 手绘和相册同一个页面：两者只有前半段不同（一个画、一个选），后半段完全相同 —— 生成设备预览 →
 * BLE 直传 → 设置置顶，而恰恰这三步最容易出错。拆成两个页面要实现两遍，漂移的那一份不会报错，
 * 只是照片在某一种入口下会莫名消失。
 *
 * 三步各自的关键点：
 *
 * 1. **预览必须是后端渲染的 `.film` 解码结果**，不是原图缩略。电子纸只有 6 色，原图缩略图与设备
 *    上的量化结果差距大到会被当成「传坏了」。
 * 2. **文件名由 [BleProtocol.suggestPinFileName] 生成，不给用户输入**。固件选菜单图用 `strstr`
 *    包含匹配，名字里带 `today`/`breakfast`/`lunch`/`dinner` 会与菜单图撞名。
 * 3. **传完必须 pin**，否则设备深睡唤醒后「上次切到哪个时段」归零，时间一同步就把自定义图换回菜单。
 *
 * @param onOpenDevice 跳到设备页去连接蓝牙。未连接时给出口而不是只报错 —— 设备平时深睡不广播，
 *        用户需要被告知「按一下设备侧键唤醒」才连得上
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CustomFilmScreen(onOpenDevice: () -> Unit) {
    val context = LocalContext.current
    val app = context.applicationContext as FridgeMagnetApp
    val bleTransfer = remember { app.bleTransfer }
    val client = remember { app.sfmBackendClient }
    val scope = rememberCoroutineScope()

    val connectionState by bleTransfer.stateFlow.collectAsState()
    val connected = connectionState is ConnectionState.Connected
    val progress by bleTransfer.progressFlow.collectAsState()
    val backendConfigured = client.config.configured

    // ---- 来源切换 ----
    var source by remember { mutableStateOf(SOURCE_DRAW) }

    // ---- 手绘状态 ----
    // 笔画列表用 mutableStateListOf：手势回调里要直接 add，而它拿不到最新的
    // MutableState（闭包捕获的是创建 pointerInput 时那一份）。
    val strokes = remember { mutableStateListOf<DoodleStroke>() }
    /** 正在画的那一笔（还没抬手），单独一份好让画布实时跟手。 */
    var livePoints by remember { mutableStateOf<List<Offset>>(emptyList()) }
    var colorIndex by remember { mutableIntStateOf(0) }
    var eraser by remember { mutableStateOf(false) }
    var widthNorm by remember { mutableFloatStateOf(DEFAULT_STROKE_WIDTH_NORM) }
    var showClearConfirm by remember { mutableStateOf(false) }

    // ---- 相册状态 ----
    var photoBytes by remember { mutableStateOf<ByteArray?>(null) }
    var photoMime by remember { mutableStateOf("image/jpeg") }
    var photoThumb by remember { mutableStateOf<ImageBitmap?>(null) }
    var photoError by remember { mutableStateOf<String?>(null) }
    var fit by remember { mutableStateOf(FIT_COVER) }

    // ---- 共用后半段 ----
    var filmBytes by remember { mutableStateOf<ByteArray?>(null) }
    var previewBitmap by remember { mutableStateOf<ImageBitmap?>(null) }
    /** 点击设备预览后放大查看的图；非空即弹出全屏查看器。 */
    var zoomPreview by remember { mutableStateOf<ImageBitmap?>(null) }
    /**
     * 与 [filmBytes] 配套的文件名，**生成一次就存住**：pin 必须指向刚传上去的那个名字，
     * 每次用到时重新 suggest 会得到一个设备上并不存在的随机名，固件按名字找不到文件就
     * 静默回落到菜单 —— 传输成功、pin 也成功，画面却不出现。
     */
    var fileName by remember { mutableStateOf<String?>(null) }
    var rendering by remember { mutableStateOf(false) }
    var renderError by remember { mutableStateOf<String?>(null) }
    var sending by remember { mutableStateOf(false) }
    /** 这一份 [filmBytes] 是否已经成功传进设备。pin 之前必须先有文件。 */
    var sent by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    var messageIsError by remember { mutableStateOf(false) }

    // ---- 置顶 ----
    var pinModeRaw by remember { mutableIntStateOf(BleProtocol.PinMode.OUTSIDE_MEAL.raw) }
    var windowStart by remember { mutableIntStateOf(DEFAULT_WINDOW_START) }
    var windowEnd by remember { mutableIntStateOf(DEFAULT_WINDOW_END) }
    var editingWindowStart by remember { mutableStateOf(false) }
    var editingWindowEnd by remember { mutableStateOf(false) }
    var pinning by remember { mutableStateOf(false) }
    /** 设备回读到的置顶配置。 */
    var devicePin by remember { mutableStateOf<BleProtocol.PinnedFilm?>(null) }
    /**
     * 回读是否失败过。
     *
     * 必须与 `devicePin == null` 分开：[BleProtocol.parsePinnedFilm] 用 `PinnedFilm(mode = OFF)`
     * 表达「设备明确说未置顶」，null 才是「读取失败」。都画成「未置顶」的话，一次通讯失败会让
     * 用户以为设备没 pin，去按设置又被已存在的配置盖回去。
     */
    var pinReadFailed by remember { mutableStateOf(false) }
    var pinLoading by remember { mutableStateOf(false) }

    fun notify(text: String, isError: Boolean) {
        message = text
        messageIsError = isError
    }

    /** 回读设备当前的置顶配置。未连接时什么都不做（保留上一次读到的值与提示）。 */
    suspend fun refreshPin() {
        if (!bleTransfer.isConnected()) return
        pinLoading = true
        val result = bleTransfer.queryPinnedFilm()
        devicePin = result
        pinReadFailed = result == null
        pinLoading = false
    }

    // 进页面 / 连上蓝牙后读一次。设备平时深睡，进页面那一刻往往还没连上，
    // 所以键在 connected 上而不是 Unit —— 否则连上之后这一屏永远显示「未连接」。
    LaunchedEffect(connected) {
        if (connected) refreshPin()
    }

    /** 丢弃已生成的 film 与文件名。任何会改变画面内容的操作都要调，否则预览会与内容不符。 */
    fun invalidateFilm() {
        filmBytes = null
        previewBitmap = null
        fileName = null
        sent = false
        renderError = null
    }

    // 相册选择器**放在顶层**而不是相册那张卡片里：卡片是条件渲染的，把
    // rememberLauncherForActivityResult 放进去会让它随来源切换反复注册/注销，而系统
    // PhotoPicker 返回结果时这个 launcher 可能已经被注销掉了。
    val photoPicker = rememberLauncherForActivityResult(
        // 系统 PhotoPicker：**不需要任何存储权限**。用它而不是 GetContent + READ_MEDIA_IMAGES ——
        // 后者要弹一次权限框，用户拒绝之后整个功能就没法用了。
        ActivityResultContracts.PickVisualMedia()
    ) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        photoError = null
        invalidateFilm()
        scope.launch {
            val loaded = withContext(Dispatchers.IO) {
                runCatching {
                    val mime = context.contentResolver.getType(uri) ?: "image/jpeg"
                    val bytes = context.contentResolver.openInputStream(uri)
                        ?.use { it.readBytes() }
                        ?: throw IllegalStateException("无法读取所选图片")
                    mime to bytes
                }
            }
            loaded.fold(
                onSuccess = { (mime, bytes) ->
                    if (bytes.size > MAX_PHOTO_BYTES) {
                        // 客户端先拦一道：后端另有 5000 万像素的上限，但那是解码之后才判的 ——
                        // 先在这里挡住明显过大的输入，省一次几十兆的上传和一次注定失败的渲染。
                        photoBytes = null
                        photoThumb = null
                        photoError = "这张图约 ${bytes.size / 1024 / 1024}MB，" +
                            "超过 ${MAX_PHOTO_BYTES / 1024 / 1024}MB 上限，请换一张。"
                    } else {
                        photoThumb = withContext(Dispatchers.Default) {
                            decodeThumbnail(bytes)?.asImageBitmap()
                        }
                        if (photoThumb == null) {
                            // 解不开就别往后端送：那一趟必然失败，而失败原因会变成一句
                            // 后端的报错，比这里直接说「这张图解不开」难懂。
                            photoBytes = null
                            photoError = "这张图解不开（${bytes.size} 字节），请换一张。"
                        } else {
                            photoBytes = bytes
                            photoMime = mime
                        }
                    }
                },
                onFailure = { e ->
                    photoBytes = null
                    photoThumb = null
                    photoError = e.message ?: "读取图片失败"
                }
            )
        }
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            // 不给返回箭头：这一页是底部 dock 的一个 tab，箭头会与 dock 重复。
            TopAppBar(
                title = { Text("自定义画面") },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background
                )
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
            if (!backendConfigured) {
                // 后端没配就整页只给引导：渲染 `.film` 只有后端一条路（App 侧刻意不实现
                // 量化，见 FilmDecoder 的注释），画得再好也发不出去。
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.surfaceVariant
                    )
                ) {
                    Column(Modifier.padding(16.dp)) {
                        Text("尚未配置后端", style = MaterialTheme.typography.titleMedium)
                        Spacer(Modifier.height(6.dp))
                        Text(
                            "涂鸦和照片都要经后端渲染成设备能显示的画面格式（六色量化只有后端" +
                                "一份实现，App 侧刻意不重复做）。请先到「设置 → 冰箱贴后端」" +
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
                    Row(Modifier.padding(14.dp).fillMaxWidth(), verticalAlignment = Alignment.Top) {
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

            // ==================== 来源切换 ====================
            SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                SOURCES.forEachIndexed { index, (key, label) ->
                    SegmentedButton(
                        selected = source == key,
                        onClick = {
                            if (source != key) {
                                source = key
                                // 换来源就作废已生成的 film：留着会让「生成预览」显示上一个
                                // 来源的结果，用户按「发送」传的是他刚刚没在看的那张图。
                                invalidateFilm()
                            }
                        },
                        shape = SegmentedButtonDefaults.itemShape(index, SOURCES.size),
                        label = { Text(label, style = MaterialTheme.typography.labelLarge) },
                        icon = {
                            Icon(
                                if (key == SOURCE_DRAW) Icons.Outlined.Brush
                                else Icons.Outlined.PhotoLibrary,
                                contentDescription = null,
                                modifier = Modifier.size(18.dp)
                            )
                        }
                    )
                }
            }

            // ==================== 一、手绘 ====================
            if (source == SOURCE_DRAW) {
                Card(colors = CardDefaults.cardColors(containerColor = CUSTOM_CARD_BG)) {
                    Column(Modifier.padding(16.dp)) {
                        Text("手绘涂鸦", style = MaterialTheme.typography.titleMedium)
                        Text(
                            "画布比例与设备屏幕一致（792×528），所见的构图就是设备上的构图。" +
                                "笔刷只有 6 个颜色 —— 它们正是设备能显示的那 6 色，所以画出来" +
                                "是什么色，设备上就是什么色，不会被换成相近的另一种。",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                        Spacer(Modifier.height(10.dp))

                        // 画布：**比例锁死 792:528**。比例不一致的话，用户精心安排的构图到
                        // 设备上会被 cover 裁掉边缘（或 contain 留一圈死白），而那一刀切在
                        // 哪儿在手机上完全看不出来。
                        Box(
                            Modifier
                                .fillMaxWidth()
                                .aspectRatio(
                                    DoodleGeometry.exportWidth.toFloat() /
                                        DoodleGeometry.exportHeight
                                )
                                .clip(RoundedCornerShape(8.dp))
                                .background(Color.White)
                                .border(
                                    1.dp,
                                    MaterialTheme.colorScheme.outlineVariant,
                                    RoundedCornerShape(8.dp)
                                )
                                // 手势键在笔刷参数上：闭包捕获的是创建这一刻的值，
                                // 换色/换粗细后必须重建才能生效。
                                .pointerInput(colorIndex, eraser, widthNorm) {
                                    val effectiveColor =
                                        if (eraser) FilmDecoder.PALETTE_INDEX_WHITE else colorIndex
                                    awaitEachGesture {
                                        val down = awaitFirstDown()
                                        // 手动收点而不是 detectDragGestures：后者要过触摸
                                        // 滑动阈值才回调，于是"按下即抬起"的单点点击一个点
                                        // 都收不到 —— 而点一个圆点是画画时很常用的动作。
                                        var points = listOf(
                                            normalizedOffset(down.position, size.width, size.height)
                                        )
                                        livePoints = points
                                        down.consume()
                                        while (true) {
                                            val event = awaitPointerEvent()
                                            val change =
                                                event.changes.firstOrNull { it.id == down.id }
                                                    ?: break
                                            change.consume()
                                            if (!change.pressed) break
                                            points = points + normalizedOffset(
                                                change.position, size.width, size.height
                                            )
                                            livePoints = points
                                        }
                                        strokes.add(
                                            DoodleStroke(points, effectiveColor, widthNorm)
                                        )
                                        livePoints = emptyList()
                                        invalidateFilm()
                                    }
                                }
                        ) {
                            Canvas(Modifier.matchParentSize()) {
                                // 显示与导出跑的是同一份归一化数据、各按自己的尺寸重绘，
                                // 中间没有"先按屏幕画好再缩放"这一步，所以不存在缩放误差。
                                // 这里保留抗锯齿（Compose 默认）而导出关掉：屏幕这块只是
                                // 绘图台面，权威预览是下面那张后端渲染结果。
                                strokes.forEach { drawDoodleStroke(it) }
                                if (livePoints.isNotEmpty()) {
                                    val live = if (eraser) FilmDecoder.PALETTE_INDEX_WHITE
                                    else colorIndex
                                    drawDoodleStroke(DoodleStroke(livePoints, live, widthNorm))
                                }
                            }
                        }

                        Spacer(Modifier.height(12.dp))

                        // ---- 颜色 ----
                        Text("颜色", style = MaterialTheme.typography.labelMedium)
                        Spacer(Modifier.height(6.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            BRUSH_COLORS.forEach { (index, label) ->
                                val selected = !eraser && colorIndex == index
                                Box(
                                    Modifier
                                        .size(36.dp)
                                        .clip(CircleShape)
                                        .background(Color(FilmDecoder.PALETTE[index]))
                                        .border(
                                            if (selected) 3.dp else 1.dp,
                                            if (selected) MaterialTheme.colorScheme.primary
                                            else MaterialTheme.colorScheme.outline,
                                            CircleShape
                                        )
                                        .clickable {
                                            colorIndex = index
                                            eraser = false
                                        },
                                    contentAlignment = Alignment.Center
                                ) {
                                    // 白色色块在白底上看不见，标一个字。
                                    if (index == FilmDecoder.PALETTE_INDEX_WHITE) {
                                        Text(
                                            label,
                                            style = MaterialTheme.typography.labelSmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant
                                        )
                                    }
                                }
                            }
                        }

                        Spacer(Modifier.height(12.dp))

                        // ---- 工具 ----
                        Row(
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            FilterChip(
                                selected = !eraser,
                                onClick = { eraser = false },
                                label = { Text("画笔") }
                            )
                            FilterChip(
                                selected = eraser,
                                onClick = { eraser = true },
                                // 橡皮就是白色笔，不是"擦掉笔画"：真正的擦除要按笔画做
                                // 命中测试，而白笔在纯白底上的效果与擦除一致，代价是零。
                                label = { Text("橡皮") }
                            )
                            Spacer(Modifier.weight(1f))
                            IconButton(
                                onClick = {
                                    if (strokes.isNotEmpty()) {
                                        strokes.removeAt(strokes.lastIndex)
                                        invalidateFilm()
                                    }
                                },
                                enabled = strokes.isNotEmpty()
                            ) { Icon(Icons.AutoMirrored.Outlined.Undo, contentDescription = "撤销上一笔") }
                            IconButton(
                                onClick = { showClearConfirm = true },
                                enabled = strokes.isNotEmpty()
                            ) { Icon(Icons.Outlined.Delete, contentDescription = "清空画布") }
                        }
                        Text(
                            "橡皮是白色画笔（底色也是白），效果与擦除一致。" +
                                "撤销可逐笔回退，不必因为画错一笔重画整张。",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )

                        Spacer(Modifier.height(8.dp))

                        // ---- 粗细 ----
                        Text(
                            "粗细：${
                                DoodleGeometry.strokeWidthPx(
                                    widthNorm, DoodleGeometry.exportWidth
                                ).toInt()
                            } 像素（设备上）",
                            style = MaterialTheme.typography.labelMedium
                        )
                        Slider(
                            value = widthNorm,
                            onValueChange = { widthNorm = it },
                            valueRange = MIN_STROKE_WIDTH_NORM..MAX_STROKE_WIDTH_NORM
                        )
                    }
                }
            }

            // ==================== 二、相册 ====================
            if (source == SOURCE_PHOTO) {
                Card(colors = CardDefaults.cardColors(containerColor = CUSTOM_CARD_BG)) {
                    Column(Modifier.padding(16.dp)) {
                        Text("相册照片", style = MaterialTheme.typography.titleMedium)
                        Text(
                            "从系统相册里挑一张。这一步不需要任何存储权限。",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                        Spacer(Modifier.height(8.dp))
                        Button(
                            onClick = {
                                photoPicker.launch(
                                    PickVisualMediaRequest(
                                        ActivityResultContracts.PickVisualMedia.ImageOnly
                                    )
                                )
                            },
                            modifier = Modifier.fillMaxWidth(),
                            enabled = !rendering && !sending
                        ) {
                            Icon(Icons.Outlined.PhotoLibrary, null, Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text(if (photoBytes == null) "选择照片" else "换一张")
                        }

                        photoError?.let {
                            Spacer(Modifier.height(8.dp))
                            Text(
                                it,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.error
                            )
                        }

                        photoThumb?.let { thumb ->
                            Spacer(Modifier.height(12.dp))
                            Text("原图", style = MaterialTheme.typography.labelMedium)
                            Spacer(Modifier.height(4.dp))
                            Image(
                                bitmap = thumb,
                                contentDescription = "已选照片",
                                contentScale = ContentScale.Fit,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .heightIn(max = 200.dp)
                                    .clip(RoundedCornerShape(8.dp))
                            )
                            Text(
                                "这是原图。设备上的实际效果要看下面「设备预览」那张 —— " +
                                    "电子纸只有 6 色，颜色会被换成最接近的那一种。",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )

                            Spacer(Modifier.height(12.dp))
                            Text("怎么放进 792×528 的屏幕", style = MaterialTheme.typography.labelMedium)
                            Spacer(Modifier.height(4.dp))
                            FIT_CHOICES.forEach { choice ->
                                Row(
                                    Modifier
                                        .fillMaxWidth()
                                        .clickable {
                                            if (fit != choice.key) {
                                                fit = choice.key
                                                invalidateFilm()
                                            }
                                        }
                                        .padding(vertical = 4.dp),
                                    verticalAlignment = Alignment.Top
                                ) {
                                    RadioButton(
                                        selected = fit == choice.key,
                                        onClick = {
                                            if (fit != choice.key) {
                                                fit = choice.key
                                                invalidateFilm()
                                            }
                                        }
                                    )
                                    Column(Modifier.padding(top = 12.dp)) {
                                        Text(
                                            choice.title,
                                            style = MaterialTheme.typography.bodyMedium,
                                            fontWeight = FontWeight.Medium
                                        )
                                        Text(
                                            choice.detail,
                                            style = MaterialTheme.typography.labelSmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // ==================== 三、生成设备预览 ====================
            val hasContent = if (source == SOURCE_DRAW) strokes.isNotEmpty() else photoBytes != null

            Card(colors = CardDefaults.cardColors(containerColor = CUSTOM_CARD_BG)) {
                Column(Modifier.padding(16.dp)) {
                    Text("设备预览", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "下面这张就是设备上的实际效果（六色量化之后），不是原图。看到色块和" +
                            "边缘的颜色跳变是正常的 —— 电子纸只有黑白黄红蓝绿六种墨。",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Spacer(Modifier.height(10.dp))

                    Button(
                        onClick = {
                            if (rendering || sending) return@Button
                            rendering = true
                            renderError = null
                            filmBytes = null
                            previewBitmap = null
                            fileName = null
                            sent = false
                            scope.launch {
                                val prepared = withContext(Dispatchers.Default) {
                                    runCatching {
                                        if (source == SOURCE_DRAW) {
                                            // PNG 而不是 JPEG：JPEG 的有损压缩会在纯色块
                                            // 边缘造出中间色，量化后落到别的调色板索引 ——
                                            // 凭空多出一圈杂色，而原图里根本没有。
                                            Triple(
                                                exportDoodlePng(strokes.toList()),
                                                "image/png",
                                                // 导出比例已经精确是 792:528，
                                                // cover/contain/stretch 三者等价。
                                                FIT_COVER
                                            )
                                        } else {
                                            Triple(photoBytes!!, photoMime, fit)
                                        }
                                    }
                                }
                                prepared.fold(
                                    onSuccess = { (bytes, mime, fitValue) ->
                                        client.renderImageFilm(bytes, mime, fitValue).fold(
                                            onSuccess = { film ->
                                                filmBytes = film
                                                // 文件名与这一份 film 绑定，pin 要用同一个。
                                                fileName = BleProtocol.suggestPinFileName(
                                                    if (source == SOURCE_DRAW) PREFIX_DRAW
                                                    else PREFIX_PHOTO
                                                )
                                                // 41.8 万像素的解码是纯 CPU 活，
                                                // 放主线程会明显卡一下。
                                                previewBitmap = withContext(Dispatchers.Default) {
                                                    FilmDecoder.toBitmap(film)?.asImageBitmap()
                                                }
                                                if (previewBitmap == null) {
                                                    renderError =
                                                        "后端返回了 ${film.size} 字节，但解码不出画面"
                                                }
                                            },
                                            onFailure = { e ->
                                                renderError = e.message ?: "渲染失败"
                                            }
                                        )
                                    },
                                    onFailure = { e ->
                                        renderError = e.message ?: "导出图片失败"
                                    }
                                )
                                rendering = false
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                        enabled = hasContent && !rendering && !sending
                    ) {
                        Text(if (rendering) "正在渲染…" else "生成设备预览")
                    }
                    if (!hasContent) {
                        Spacer(Modifier.height(4.dp))
                        Text(
                            if (source == SOURCE_DRAW) "先在上面画点什么" else "先选一张照片",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }

                    Spacer(Modifier.height(10.dp))
                    Box(
                        Modifier
                            .fillMaxWidth()
                            .heightIn(min = 120.dp)
                            .clip(RoundedCornerShape(8.dp))
                            .background(MaterialTheme.accents.track),
                        contentAlignment = Alignment.Center
                    ) {
                        val preview = previewBitmap
                        when {
                            preview != null -> Image(
                                bitmap = preview,
                                contentDescription = "设备预览（点击放大）",
                                // Fit 而不是 Crop：整张 792×528 就是构图本身，
                                // 裁掉边缘正好裁掉了"会不会被切"这个要看的信息。
                                contentScale = ContentScale.Fit,
                                modifier = Modifier.fillMaxWidth().clickable { zoomPreview = preview }
                            )
                            rendering -> Column(
                                Modifier.padding(24.dp),
                                horizontalAlignment = Alignment.CenterHorizontally
                            ) {
                                CircularProgressIndicator(Modifier.size(28.dp))
                                Spacer(Modifier.height(8.dp))
                                Text(
                                    "正在渲染（约 1 秒）…",
                                    style = MaterialTheme.typography.labelSmall
                                )
                            }
                            renderError != null -> Text(
                                renderError!!,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.error,
                                modifier = Modifier.padding(20.dp)
                            )
                            else -> Text(
                                "点上面的按钮生成预览",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(24.dp)
                            )
                        }
                    }
                }
            }

            // ==================== 四、发送到设备 ====================
            Card(colors = CardDefaults.cardColors(containerColor = CUSTOM_CARD_BG)) {
                Column(Modifier.padding(16.dp)) {
                    Text("发送到设备", style = MaterialTheme.typography.titleMedium)
                    Text(
                        "经蓝牙直传 209120 字节，约 20–30 秒；传完设备会自己刷新显示，" +
                            "电子纸刷屏还要再等约 20 秒。期间请不要离开这一页。",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Spacer(Modifier.height(10.dp))

                    if (!connected) {
                        // 只报"未连接"没用 —— 用户不知道该去哪儿连，也不知道设备平时不广播。
                        Text(
                            "蓝牙未连接。设备平时深度休眠、不持续广播，扫不到时按一下设备侧键" +
                                "唤醒再连。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.error
                        )
                        Spacer(Modifier.height(8.dp))
                        OutlinedButton(
                            onClick = onOpenDevice,
                            modifier = Modifier.fillMaxWidth()
                        ) { Text("去设备页连接") }
                        Spacer(Modifier.height(10.dp))
                    }

                    Button(
                        onClick = {
                            val film = filmBytes ?: return@Button
                            val name = fileName ?: return@Button
                            if (sending) return@Button
                            sending = true
                            message = null
                            scope.launch {
                                // 刻意用 sendFilm 而不是 sendFilmSet：后者默认先发 0x45
                                // 清空全部、末尾再发 0x47 按时段选图，两个动作都会把刚传
                                // 的自定义图弄掉（一个删文件、一个切回菜单）。
                                val ok = bleTransfer.sendFilm(film, name)
                                sent = ok
                                if (ok) {
                                    notify(
                                        "已传到设备（$name），电子纸正在刷新，约 20 秒后可见。" +
                                            "接下来在下面选一个置顶方式 —— 不置顶的话这张图会在" +
                                            "设备下一次唤醒后被菜单换掉。",
                                        false
                                    )
                                    refreshPin()
                                } else {
                                    // 直接读 flow 的当前值而不是 composition 里那份
                                    // `progress`：后者是本次 onClick 创建时的快照，传输失败
                                    // 发生在之后，错误原因不会体现在快照里。
                                    notify(
                                        "发送失败：" + (
                                            bleTransfer.progressFlow.value.error
                                                ?: "传输中断，请确认设备仍在连接后重试"
                                            ),
                                        true
                                    )
                                }
                                sending = false
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                        enabled = filmBytes != null && connected && !sending && !rendering
                    ) {
                        Text(
                            when {
                                sending -> "正在发送…"
                                sent -> "重新发送"
                                else -> "发送到设备"
                            }
                        )
                    }

                    if (sending) {
                        Spacer(Modifier.height(10.dp))
                        LinearProgressIndicator(
                            progress = { progress.percentage / 100f },
                            modifier = Modifier.fillMaxWidth()
                        )
                        Spacer(Modifier.height(4.dp))
                        Text(
                            "${progress.percentage}%  " +
                                transferStateLabel(progress.state),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                    if (filmBytes == null && !sending) {
                        Spacer(Modifier.height(4.dp))
                        Text(
                            "先生成设备预览，确认效果后再发送。",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
            }

            // ==================== 五、置顶 ====================
            Card(colors = CardDefaults.cardColors(containerColor = CUSTOM_CARD_BG)) {
                Column(Modifier.padding(16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("让它留在屏幕上", style = MaterialTheme.typography.titleMedium)
                        Spacer(Modifier.weight(1f))
                        if (pinLoading) CircularProgressIndicator(Modifier.size(16.dp))
                    }
                    Text(
                        "设备约十分钟深睡一次，醒来后「上次显示的是哪一餐」的记录会归零，" +
                            "时间一同步就把画面换回菜单。所以要让自定义画面留住，必须在这里" +
                            "设置置顶。",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )

                    Spacer(Modifier.height(10.dp))

                    // ---- 设备当前状态 ----
                    HorizontalDivider()
                    Spacer(Modifier.height(8.dp))
                    Text("设备当前", style = MaterialTheme.typography.labelMedium)
                    Spacer(Modifier.height(2.dp))
                    val current = devicePin
                    when {
                        !connected -> Text(
                            "未连接蓝牙，读不到设备当前的置顶状态。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                        // 读取失败与"设备说没置顶"是两回事，分开显示。
                        pinReadFailed -> Column {
                            Text(
                                "读取失败（超时或响应不完整），无法确认设备当前的置顶状态。",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.error
                            )
                            TextButton(
                                onClick = { scope.launch { refreshPin() } },
                                enabled = !pinLoading
                            ) { Text("重试") }
                        }
                        current == null -> Text(
                            "正在读取…",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                        current.mode == BleProtocol.PinMode.OFF -> Text(
                            "未置顶：设备按菜单逻辑自动选图。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                        else -> Column {
                            Text(
                                "${current.describe()}　文件：${current.fileName.ifBlank { "（无名）" }}",
                                style = MaterialTheme.typography.bodySmall,
                                fontWeight = FontWeight.Medium,
                                color = MaterialTheme.accents.success.onContainer
                            )
                            OutlinedButton(
                                onClick = {
                                    if (pinning) return@OutlinedButton
                                    pinning = true
                                    message = null
                                    scope.launch {
                                        val ok = bleTransfer.clearPinnedFilm()
                                        if (ok) {
                                            // 措辞要点：取消置顶**不清屏也不切图**。
                                            // 说成"已恢复菜单"会让用户盯着没变化的屏幕
                                            // 以为命令没生效。
                                            notify(
                                                "已取消置顶。屏幕上当前这张画面会保留到下一次" +
                                                    "自动选图（设备唤醒同步、或按设备 FN 键）才变，" +
                                                    "这是正常的。",
                                                false
                                            )
                                        } else {
                                            notify(pinFailureText(bleTransfer.isConnected()), true)
                                        }
                                        refreshPin()
                                        pinning = false
                                    }
                                },
                                enabled = connected && !pinning,
                                colors = ButtonDefaults.outlinedButtonColors(
                                    contentColor = MaterialTheme.colorScheme.error
                                )
                            ) { Text("取消置顶") }
                        }
                    }

                    Spacer(Modifier.height(12.dp))
                    HorizontalDivider()
                    Spacer(Modifier.height(8.dp))

                    // ---- 选择置顶方式 ----
                    Text("这张画面怎么显示", style = MaterialTheme.typography.labelMedium)
                    PIN_CHOICES.forEach { choice ->
                        Row(
                            Modifier
                                .fillMaxWidth()
                                .clickable { pinModeRaw = choice.mode.raw }
                                .padding(vertical = 4.dp),
                            verticalAlignment = Alignment.Top
                        ) {
                            RadioButton(
                                selected = pinModeRaw == choice.mode.raw,
                                onClick = { pinModeRaw = choice.mode.raw }
                            )
                            Column(Modifier.padding(top = 12.dp)) {
                                Text(
                                    choice.title,
                                    style = MaterialTheme.typography.bodyMedium,
                                    fontWeight = FontWeight.Medium
                                )
                                Text(
                                    choice.detail,
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant
                                )
                            }
                        }
                    }

                    // ---- 时间窗口 ----
                    val windowSelected = pinModeRaw == BleProtocol.PinMode.WINDOW.raw
                    val emptyWindow = windowStart == windowEnd
                    if (windowSelected) {
                        Spacer(Modifier.height(8.dp))
                        Row(
                            Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            OutlinedButton(
                                onClick = { editingWindowStart = true },
                                modifier = Modifier.weight(1f)
                            ) { Text("开始 ${BleProtocol.formatMinuteOfDay(windowStart)}") }
                            OutlinedButton(
                                onClick = { editingWindowEnd = true },
                                modifier = Modifier.weight(1f)
                            ) { Text("结束 ${BleProtocol.formatMinuteOfDay(windowEnd)}") }
                        }
                        if (emptyWindow) {
                            Spacer(Modifier.height(4.dp))
                            Text(
                                "开始与结束相同表示空窗口（一秒都不显示），不是「显示一整天」。" +
                                    "想全天显示请选「一直显示」。",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.error
                            )
                        } else if (windowStart > windowEnd) {
                            Spacer(Modifier.height(4.dp))
                            Text(
                                "跨零点窗口：今天 ${BleProtocol.formatMinuteOfDay(windowStart)} 到" +
                                    "次日 ${BleProtocol.formatMinuteOfDay(windowEnd)} 显示。",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }

                    Spacer(Modifier.height(10.dp))
                    val choosingOff = pinModeRaw == BleProtocol.PinMode.OFF.raw
                    Button(
                        onClick = {
                            if (pinning) return@Button
                            val mode = BleProtocol.PinMode.fromRaw(pinModeRaw) ?: return@Button
                            pinning = true
                            message = null
                            scope.launch {
                                val ok = if (mode == BleProtocol.PinMode.OFF) {
                                    // 选"不置顶"也要发一次清除：设备上可能还 pin 着**上一张**
                                    // 自定义图，那条记录会一直把旧图顶在屏幕上，新传的这张
                                    // 反而看不见。
                                    bleTransfer.clearPinnedFilm()
                                } else {
                                    bleTransfer.setPinnedFilm(
                                        BleProtocol.PinnedFilm(
                                            mode = mode,
                                            windowStartMinutes = windowStart,
                                            windowEndMinutes = windowEnd,
                                            fileName = fileName.orEmpty()
                                        )
                                    )
                                }
                                if (ok) {
                                    notify(
                                        if (mode == BleProtocol.PinMode.OFF) {
                                            "已设为不置顶。这张画面会在设备下一次唤醒后被菜单" +
                                                "换掉；屏幕现在还不会变。"
                                        } else {
                                            "置顶已生效：${
                                                BleProtocol.PinnedFilm(
                                                    mode, windowStart, windowEnd,
                                                    fileName.orEmpty()
                                                ).describe()
                                            }。"
                                        },
                                        false
                                    )
                                } else {
                                    notify(pinFailureText(bleTransfer.isConnected()), true)
                                }
                                refreshPin()
                                pinning = false
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                        // 除"不置顶"外都要求文件已经传上去：pin 指向一个设备上不存在的
                        // 文件时，固件找不到文件就静默回落菜单 —— 命令成功、画面不出现。
                        enabled = connected && !pinning && !sending &&
                            (choosingOff || (sent && fileName != null)) &&
                            !(windowSelected && emptyWindow)
                    ) {
                        Text(
                            when {
                                pinning -> "正在保存…"
                                choosingOff -> "取消设备上的置顶"
                                else -> "保存置顶设置"
                            }
                        )
                    }
                    if (!choosingOff && !sent) {
                        Spacer(Modifier.height(4.dp))
                        Text(
                            "先把画面发送到设备，再设置置顶 —— 置顶记录要指向设备上真实存在的" +
                                "那个文件。",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
            }

            Spacer(Modifier.height(16.dp))
        }
    }

    // ==================== 弹窗 ====================
    if (showClearConfirm) {
        AlertDialog(
            onDismissRequest = { showClearConfirm = false },
            title = { Text("清空画布") },
            text = { Text("会删掉全部 ${strokes.size} 笔，且不能撤销。只想退一笔请用撤销。") },
            confirmButton = {
                Button(onClick = {
                    strokes.clear()
                    livePoints = emptyList()
                    invalidateFilm()
                    showClearConfirm = false
                }) { Text("清空") }
            },
            dismissButton = {
                TextButton(onClick = { showClearConfirm = false }) { Text("取消") }
            }
        )
    }

    if (editingWindowStart) {
        MinuteOfDayPickerDialog(
            title = "开始时间",
            initialMinuteOfDay = windowStart,
            onDismiss = { editingWindowStart = false },
            onConfirm = {
                windowStart = it
                editingWindowStart = false
            }
        )
    }
    if (editingWindowEnd) {
        MinuteOfDayPickerDialog(
            title = "结束时间",
            initialMinuteOfDay = windowEnd,
            onDismiss = { editingWindowEnd = false },
            onConfirm = {
                windowEnd = it
                editingWindowEnd = false
            }
        )
    }

    zoomPreview?.let { bmp ->
        ZoomableImageDialog(onDismiss = { zoomPreview = null }) { modifier ->
            Image(
                bitmap = bmp,
                contentDescription = "设备预览大图",
                contentScale = ContentScale.Fit,
                modifier = modifier.fillMaxSize(),
            )
        }
    }
}

// ==================== 手绘数据与绘制 ====================

/**
 * 一笔涂鸦。坐标是**归一化的**（`0f..1f`，相对画布宽/高），线宽是相对画布宽度的比例。
 *
 * 名字带 Doodle 前缀是为了不与 `androidx.compose.ui.graphics.drawscope.Stroke` 撞名 —— 那个类型
 * 就在同一个绘制作用域里用。
 *
 * 不直接画进 Bitmap 的理由见 [DoodleGeometry] 的类注释。
 */
private data class DoodleStroke(
    val points: List<Offset>,
    val colorIndex: Int,
    val widthNorm: Float
)

/** 触摸点像素 → 归一化 [Offset]。x/y 各按自己那一轴的长度算。 */
private fun normalizedOffset(position: Offset, width: Int, height: Int): Offset = Offset(
    DoodleGeometry.normalize(position.x, width.toFloat()),
    DoodleGeometry.normalize(position.y, height.toFloat())
)

/**
 * 在 Compose 画布上渲染一笔。
 *
 * 单点（按下即抬起）画成圆点：不画的话点一下什么都不出现，用户会以为控件坏了，
 * 而"点一个点"是画画时很常用的动作。
 */
private fun androidx.compose.ui.graphics.drawscope.DrawScope.drawDoodleStroke(
    stroke: DoodleStroke
) {
    val color = Color(FilmDecoder.PALETTE[stroke.colorIndex])
    val widthPx = DoodleGeometry.strokeWidthPx(stroke.widthNorm, size.width.toInt())
    val pixels = stroke.points.map {
        Offset(
            DoodleGeometry.toPixel(it.x, size.width.toInt()),
            DoodleGeometry.toPixel(it.y, size.height.toInt())
        )
    }
    if (pixels.isEmpty()) return
    if (pixels.size == 1) {
        drawCircle(color, radius = widthPx / 2f, center = pixels.first())
        return
    }
    val path = Path().apply {
        moveTo(pixels.first().x, pixels.first().y)
        pixels.drop(1).forEach { lineTo(it.x, it.y) }
    }
    drawPath(
        path = path,
        color = color,
        style = DrawStroke(width = widthPx, cap = StrokeCap.Round, join = StrokeJoin.Round)
    )
}

/**
 * 把笔画列表导出成 792×528 的 PNG 字节。
 *
 * 用 PNG 而不是 JPEG：有损压缩会在纯色块边缘造出中间色，量化后落到别的调色板索引 —— 一条黑线两侧
 * 会凭空多出一圈红或蓝。
 *
 * 关掉抗锯齿：抗锯齿会在线条边缘产生调色板之外的过渡色，而这个功能的前提是「笔刷只取调色板里的
 * 6 个精确值 → 后端量化是恒等映射 → 预览与设备逐像素一致」。代价是轻微锯齿，在 792 像素宽上可接受。
 *
 * 必须在后台线程调用：创建 41.8 万像素的 Bitmap 并逐笔描边不是主线程该做的事。
 */
private fun exportDoodlePng(strokes: List<DoodleStroke>): ByteArray {
    val width = DoodleGeometry.exportWidth
    val height = DoodleGeometry.exportHeight
    val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
    try {
        val canvas = android.graphics.Canvas(bitmap)
        // 白底：电子纸的"空白"就是白，且橡皮擦画的也是这个白，两者必须是同一个值。
        canvas.drawColor(FilmDecoder.PALETTE[FilmDecoder.PALETTE_INDEX_WHITE])

        val paint = android.graphics.Paint().apply {
            isAntiAlias = false
            strokeCap = android.graphics.Paint.Cap.ROUND
            strokeJoin = android.graphics.Paint.Join.ROUND
        }
        strokes.forEach { stroke ->
            paint.color = FilmDecoder.PALETTE[stroke.colorIndex]
            val widthPx = DoodleGeometry.strokeWidthPx(stroke.widthNorm, width)
            paint.strokeWidth = widthPx
            val pixels = stroke.points.map {
                android.graphics.PointF(
                    DoodleGeometry.toPixel(it.x, width),
                    DoodleGeometry.toPixel(it.y, height)
                )
            }
            when {
                pixels.isEmpty() -> Unit
                pixels.size == 1 -> {
                    paint.style = android.graphics.Paint.Style.FILL
                    canvas.drawCircle(pixels[0].x, pixels[0].y, widthPx / 2f, paint)
                }
                else -> {
                    paint.style = android.graphics.Paint.Style.STROKE
                    val path = android.graphics.Path().apply {
                        moveTo(pixels[0].x, pixels[0].y)
                        pixels.drop(1).forEach { lineTo(it.x, it.y) }
                    }
                    canvas.drawPath(path, paint)
                }
            }
        }

        val out = ByteArrayOutputStream()
        // quality 参数对 PNG 无意义（无损），传 100 只是 API 要求。
        bitmap.compress(Bitmap.CompressFormat.PNG, 100, out)
        return out.toByteArray()
    } finally {
        bitmap.recycle()
    }
}

/**
 * 解出一张降采样后的缩略图用于显示原图。
 *
 * 必须降采样：相册里一张 4000×3000 的照片按 ARGB_8888 展开是 48MB，直接解会 OOM，
 * 而这里只需要一张几百像素宽的预览。
 */
private fun decodeThumbnail(bytes: ByteArray): Bitmap? {
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
    if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null

    var sample = 1
    while (bounds.outWidth / sample > THUMBNAIL_MAX_EDGE ||
        bounds.outHeight / sample > THUMBNAIL_MAX_EDGE
    ) {
        sample *= 2
    }
    val options = BitmapFactory.Options().apply { inSampleSize = sample }
    return runCatching { BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options) }.getOrNull()
}

// ==================== 时间选择 ====================

/**
 * 选一个「当地时间的分钟数」（`hour*60+min`），与固件置顶窗口的表达一致。
 *
 * 24 小时制固定死：窗口两端要参与跨午夜比较，AM/PM 界面会让 12:00 与 00:00 这一对最容易
 * 填错的值更难分辨。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MinuteOfDayPickerDialog(
    title: String,
    initialMinuteOfDay: Int,
    onDismiss: () -> Unit,
    onConfirm: (Int) -> Unit
) {
    val state = rememberTimePickerState(
        initialHour = initialMinuteOfDay / 60,
        initialMinute = initialMinuteOfDay % 60,
        is24Hour = true
    )
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                TimePicker(state = state)
            }
        },
        confirmButton = {
            Button(onClick = { onConfirm(state.hour * 60 + state.minute) }) { Text("确定") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } }
    )
}

// ==================== 常量与文案 ====================

private const val SOURCE_DRAW = "draw"
private const val SOURCE_PHOTO = "photo"

private val SOURCES = listOf(
    SOURCE_DRAW to "手绘涂鸦",
    SOURCE_PHOTO to "相册照片"
)

/** pinned 文件名前缀，取值见 docs/contracts/README.md。 */
private const val PREFIX_DRAW = "draw"
private const val PREFIX_PHOTO = "photo"

private const val FIT_COVER = "cover"

private val CUSTOM_CARD_BG: Color
    @Composable @ReadOnlyComposable get() = MaterialTheme.colorScheme.surface

/**
 * 笔刷可选色：**只有这 6 个**，索引与 `.film` 调色板一一对应。
 *
 * 限定在调色板内 → 后端的六色最近色量化对它是**恒等映射** → 预览与设备逐像素一致，不会出现
 * 「我画的粉色到设备上变成红色」。自由取色则必须靠抖动（dithering）补救量化损失，并带来噪点。
 *
 * 绿色是 `(41, 204, 20)` 而不是纯绿 —— 三端一致的既有取值，见 [FilmDecoder.PALETTE]。
 */
private val BRUSH_COLORS = listOf(
    0 to "黑",
    FilmDecoder.PALETTE_INDEX_WHITE to "白",
    2 to "黄",
    3 to "红",
    4 to "蓝",
    5 to "绿"
)

/** 线宽相对画布宽度的取值范围。下限约 3px、上限约 40px（在 792 宽上）。 */
private const val MIN_STROKE_WIDTH_NORM = 0.004f
private const val MAX_STROKE_WIDTH_NORM = 0.05f
private const val DEFAULT_STROKE_WIDTH_NORM = 0.012f

/**
 * 客户端的图片体积上限。
 *
 * 后端另有 5000 万像素的上限，但那是**解码之后**才判的 —— 先在这里挡住明显过大的输入，
 * 省一次几十兆的上传和一次注定失败的渲染。
 */
private const val MAX_PHOTO_BYTES = 20 * 1024 * 1024

/** 原图缩略的最长边。够看清构图，又远离 OOM。 */
private const val THUMBNAIL_MAX_EDGE = 1024

/** 时间窗口的默认值：晚上到清早，是"照片模式"最常见的用法。 */
private const val DEFAULT_WINDOW_START = 22 * 60
private const val DEFAULT_WINDOW_END = 6 * 60

/** 缩放策略。三项文案都说清**后果**，只写 cover/contain 这种英文词对用户没有信息量。 */
private data class FitChoice(val key: String, val title: String, val detail: String)

private val FIT_CHOICES = listOf(
    FitChoice(
        FIT_COVER,
        "填满屏幕，裁掉多余部分（推荐）",
        "照片等比放大到铺满整块屏幕，超出的边缘被切掉。推荐它是因为另一种方案会留下大片" +
            "空白 —— 而在六色电子纸上，空白就是一大片死白，比裁掉一点边缘难看得多。"
    ),
    FitChoice(
        "contain",
        "完整显示，四周留白",
        "照片整张都在，比例不符的方向留出白边。适合一点都不能裁的图（比如带文字的截图）。"
    ),
    FitChoice(
        "stretch",
        "拉伸到刚好铺满（会变形）",
        "强行拉成 792×528，不裁也不留白，代价是画面被拉扁或拉长。只在图本身就接近这个比例" +
            "时才适合。"
    )
)

/** 置顶方式的可选项。每一项都写清**后果**，尤其是"不置顶"那条。 */
private data class PinChoice(
    val mode: BleProtocol.PinMode,
    val title: String,
    val detail: String
)

private val PIN_CHOICES = listOf(
    PinChoice(
        BleProtocol.PinMode.OUTSIDE_MEAL,
        "做饭时段外显示（推荐）",
        "做饭窗口内（早 06:00–09:00 / 午 11:00–13:30 / 晚 17:00–19:30）照常显示菜单，" +
            "其余时间显示这张画面。两件事都不耽误，所以推荐它。"
    ),
    PinChoice(
        BleProtocol.PinMode.ALWAYS,
        "一直显示",
        "任何时候都显示这张画面，菜单不再自动上屏 —— 相当于把冰箱贴变成一个相框。" +
            "不依赖设备时间，没配网也生效。"
    ),
    PinChoice(
        BleProtocol.PinMode.WINDOW,
        "指定时间段显示",
        "只在下面选的时间段内显示这张画面，其余时间按菜单逻辑。支持跨零点 —— " +
            "想表达「晚 10 点到早 6 点」就直接填 22:00 和 06:00。"
    ),
    PinChoice(
        BleProtocol.PinMode.OFF,
        "不置顶",
        "这张画面会在设备下一次唤醒后被菜单换掉（设备约十分钟深睡一次，" +
            "醒来时「上次显示的是哪一餐」的记录已归零）。另外，若设备上还 pin 着以前那张" +
            "自定义画面，选这项会一并取消它 —— 否则旧图会一直顶在屏幕上，新传的这张反而看不见。"
    )
)

/** 传输阶段的人话标签。 */
private fun transferStateLabel(state: TransferState): String = when (state) {
    TransferState.IDLE -> "等待"
    TransferState.PREPARING -> "准备"
    TransferState.SENDING_NAME -> "发送文件名"
    TransferState.SENDING_LEN -> "发送长度"
    TransferState.SENDING_DATA -> "传输数据"
    TransferState.COMPLETED -> "已完成，设备正在刷屏"
    TransferState.FAILED -> "失败"
}

/**
 * 置顶失败的原因文案。
 *
 * 「未连接」与「固件拒收」要分开：前者去连一下就好，后者说明这条配置设备不接受
 * （最常见的是文件名撞了菜单关键字，但本页的文件名由 suggestPinFileName 生成，
 * 不会踩这条），用户重试一百次也一样 —— 得看日志。
 */
private fun pinFailureText(stillConnected: Boolean): String = if (!stillConnected) {
    "保存失败：蓝牙已断开。请回设备页重新连接后再试（设备可能已进入深睡）。"
} else {
    "保存失败：设备拒绝了这条置顶配置，设备上的原有设置没有改变。" +
        "详细原因在 App 日志里（固件只回一个拒收标志，看不出是哪一条不满足）。"
}
