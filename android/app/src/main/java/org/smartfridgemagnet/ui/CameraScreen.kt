package org.smartfridgemagnet.ui

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import android.content.Context
import android.net.Uri
import androidx.core.content.FileProvider
import org.smartfridgemagnet.barcode.BarcodeReader
import org.smartfridgemagnet.data.Freshness
import org.smartfridgemagnet.data.Ingredient
import org.smartfridgemagnet.data.IngredientCategory
import org.smartfridgemagnet.network.BarcodeLookupDto
import org.smartfridgemagnet.network.toIngredientCategory
import android.util.Log
import org.smartfridgemagnet.ui.components.AppButton
import org.smartfridgemagnet.ui.components.AppButtonSize
import org.smartfridgemagnet.ui.components.AppButtonVariant
import org.smartfridgemagnet.ui.components.AppCard
import org.smartfridgemagnet.ui.components.ZoomableImageDialog
import org.smartfridgemagnet.ui.theme.Spacing
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.io.File

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CameraScreen(
    onBack: () -> Unit,
    onPhotoConfirmed: (Bitmap) -> Unit,
    /**
     * 条码识别成功后交出一样食材。null 表示不提供条码入口（例如后端未配置 —— 条码要靠
     * 后端查询商品库才能变成商品名）。
     */
    onBarcodeIngredient: ((Ingredient) -> Unit)? = null,
    /** 条码 → 商品。返回 null 表示没认出来，调用方据此提示改用拍照识别。 */
    onLookupBarcode: (suspend (String) -> BarcodeLookupDto?)? = null
) {
    val context = LocalContext.current
    val activity = context as? MainActivity
    val scope = rememberCoroutineScope()
    var photoBitmap by remember { mutableStateOf<Bitmap?>(null) }
    var hasPhoto by remember { mutableStateOf(false) }
    /** 条码流程的进度与结果文案。它是这条链路唯一的反馈，不能省。 */
    var barcodeStatus by remember { mutableStateOf<String?>(null) }
    var barcodeBusy by remember { mutableStateOf(false) }
    /** 预览图点击后全屏查看。确认前想看清细节（是否拍虚、有没有漏拍）只能靠放大。 */
    var zoomPhoto by remember { mutableStateOf(false) }
    var capturePath by rememberSaveable { mutableStateOf<String?>(null) }

    val cameraLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.TakePicture()
    ) { captured ->
        val path = capturePath
        if (!captured || path == null) {
            path?.let { File(it).delete() }
            barcodeStatus = if (captured) "照片文件不可用，请重新拍摄。" else null
            capturePath = null
            return@rememberLauncherForActivityResult
        }
        scope.launch {
            val file = File(path)
            try {
                val uri = FileProvider.getUriForFile(
                    context, "${context.packageName}.fileprovider", file
                )
                val bitmap = withContext(Dispatchers.IO) { uriToBitmap(context, uri) }
                if (bitmap != null) {
                    photoBitmap = bitmap
                    hasPhoto = true
                    barcodeStatus = null
                } else {
                    barcodeStatus = "照片打不开，请重新拍摄。"
                }
            } finally {
                file.delete()
                if (capturePath == path) capturePath = null
            }
        }
    }

    // 相册选择
    val galleryLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        if (uri == null) return@rememberLauncherForActivityResult
        // 解码放到 IO 线程：相册里的原图可能是 4000×3000，在这个回调（主线程）里全尺寸
        // 解码就是 48MB 的 ARGB_8888，既卡住 UI 也可能 OOM。
        scope.launch {
            barcodeStatus = null
            val bitmap = withContext(Dispatchers.IO) { uriToBitmap(context, uri) }
            if (bitmap != null) {
                photoBitmap = bitmap
                hasPhoto = true
                // 不在这里调 API，等用户确认
            } else {
                barcodeStatus = "这张图片打不开，请换一张或直接拍照。"
            }
        }
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text("拍照识别食材") },
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
                .padding(horizontal = Spacing.lg)
                .padding(bottom = Spacing.xxl),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            AppCard(
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(4f / 3f),
                contentPadding = PaddingValues()
            ) {
                if (hasPhoto && photoBitmap != null) {
                    Image(
                        bitmap = photoBitmap!!.asImageBitmap(),
                        contentDescription = "拍摄的照片，点击可放大",
                        modifier = Modifier
                            .fillMaxSize()
                            .clickable { zoomPhoto = true }
                    )
                } else {
                    Box(
                        modifier = Modifier.fillMaxSize(),
                        contentAlignment = Alignment.Center
                    ) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Icon(
                                Icons.Default.CameraAlt,
                                contentDescription = null,
                                modifier = Modifier.size(Spacing.xxxl),
                                tint = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                            Spacer(modifier = Modifier.height(Spacing.sm))
                            Text(
                                "点击下方按钮拍照或选择照片",
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }
            }

            Spacer(modifier = Modifier.height(Spacing.xxl))

            // 操作按钮
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly
            ) {
                AppButton(
                    text = "拍照",
                    onClick = {
                        if (activity == null) {
                            barcodeStatus = "当前页面无法启动相机。"
                            return@AppButton
                        }
                        activity.ensurePermissions(
                            onGranted = {
                                runCatching {
                                    val file = createCameraFile(context)
                                    capturePath = file.absolutePath
                                    val uri = FileProvider.getUriForFile(
                                        context, "${context.packageName}.fileprovider", file
                                    )
                                    cameraLauncher.launch(uri)
                                }.onFailure {
                                    capturePath?.let { File(it).delete() }
                                    capturePath = null
                                    barcodeStatus = "无法创建照片文件，请重试。"
                                }
                            },
                            onDenied = { barcodeStatus = it }
                        )
                    },
                    modifier = Modifier.weight(1f),
                    size = AppButtonSize.Medium,
                    leadingIcon = Icons.Default.CameraAlt
                )

                Spacer(modifier = Modifier.width(Spacing.sm))
                AppButton(
                    text = "相册",
                    onClick = {
                        if (activity == null) {
                            barcodeStatus = "当前页面无法打开相册。"
                            return@AppButton
                        }
                        activity.ensurePermissions(
                            onGranted = { galleryLauncher.launch("image/*") },
                            onDenied = { barcodeStatus = it }
                        )
                    },
                    modifier = Modifier.weight(1f),
                    variant = AppButtonVariant.Secondary,
                    size = AppButtonSize.Medium,
                    leadingIcon = Icons.Default.PhotoLibrary
                )
            }

            Spacer(modifier = Modifier.height(Spacing.lg))

            // 提示文字
            AppCard(
                tone = MaterialTheme.colorScheme.secondaryContainer,
                contentColor = MaterialTheme.colorScheme.onSecondaryContainer
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Icon(
                        Icons.Default.Info,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSecondaryContainer
                    )
                    Spacer(modifier = Modifier.width(Spacing.sm))
                    Text(
                        text = "请拍摄冰箱内的食材照片，AI 将自动识别",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSecondaryContainer
                    )
                }
            }

            // 已选照片时显示确认按钮
            if (hasPhoto) {
                Spacer(modifier = Modifier.height(Spacing.xxl))
                AppButton(
                    text = "确认照片，开始识别",
                    onClick = { photoBitmap?.let { onPhotoConfirmed(it) } },
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !barcodeBusy,
                    leadingIcon = Icons.Default.CheckCircle
                )

                // 条码按钮与「开始识别」并列，而不是另开一页：两者的输入是**同一张照片**，
                // 区别只在这张照片里有没有条码。分两页会让用户为了扫码再拍一次。
                if (onBarcodeIngredient != null && onLookupBarcode != null) {
                    Spacer(modifier = Modifier.height(Spacing.sm))
                    AppButton(
                        text = if (barcodeBusy) "处理中…" else "这张是条码，按条码添加",
                        onClick = {
                            val bitmap = photoBitmap ?: return@AppButton
                            barcodeBusy = true
                            barcodeStatus = "正在读条码…"
                            scope.launch {
                                // 一次解码拿到"有没有码"和"是不是商品码"两个答案。
                                // 分开提示两种失败：完全没扫到码 vs 扫到了但不是商品码。
                                // 前者要靠近重拍，后者是扫错了东西（二维码），处置不同。
                                val hit = BarcodeReader.read(bitmap)
                                if (hit == null || !hit.isProductCode) {
                                    barcodeStatus = if (hit != null) {
                                        "扫到的不是商品条码（可能是二维码）。请对准包装上的条形码，或直接「开始识别」。"
                                    } else {
                                        "没读到条码。让条形码占满画面中间再拍一次，或直接「开始识别」。"
                                    }
                                    barcodeBusy = false
                                    return@launch
                                }

                                barcodeStatus = "条码 ${hit.value}，正在查商品…"
                                val dto = onLookupBarcode(hit.value)
                                if (dto != null && dto.success && !dto.name.isNullOrBlank()) {
                                    onBarcodeIngredient(
                                        ingredientFromBarcode(dto.name, dto.category, dto.shelfLifeDays)
                                    )
                                    barcodeStatus = "已添加：${dto.name}" +
                                        if (dto.cached) "（来自缓存）" else ""
                                } else {
                                    // 直接用后端文案：它按 reason 分别说了「商品库里没有，
                                    // 请改用拍照识别」（重试无用）与「暂时连不上，请稍后
                                    // 重试」（重试有意义）。在这里另写一套会把这个区别抹平。
                                    barcodeStatus = dto?.message
                                        ?: "查商品失败，请改用「开始识别」。"
                                }
                                barcodeBusy = false
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                        enabled = !barcodeBusy,
                        loading = barcodeBusy,
                        variant = AppButtonVariant.Secondary,
                        leadingIcon = Icons.Default.QrCodeScanner
                    )
                }
            }

            barcodeStatus?.let { status ->
                Spacer(modifier = Modifier.height(Spacing.md))
                AppCard(
                    tone = if (status.startsWith("已添加"))
                        MaterialTheme.colorScheme.secondaryContainer
                    else MaterialTheme.colorScheme.surfaceVariant
                ) {
                    Text(
                        text = status,
                        style = MaterialTheme.typography.bodySmall
                    )
                }
            }
        }
    }

    // 点预览图看大图。确认食材之前想核对「是否拍虚、有没有漏拍」，只能靠放大。
    if (zoomPhoto) {
        val shown = photoBitmap
        if (shown == null) {
            zoomPhoto = false
        } else {
            ZoomableImageDialog(onDismiss = { zoomPhoto = false }) { modifier ->
                Image(
                    bitmap = shown.asImageBitmap(),
                    contentDescription = "照片原图",
                    modifier = modifier
                )
            }
        }
    }
}

/**
 * 把条码查到的商品变成一样待确认的食材。
 *
 * 数量固定 1 个：条码只能告诉我们「是什么」，说不出「有几个」，1 是最小的假设，用户在确认页可以改。
 *
 * 保质期留空（不猜）：包装商品的保质期印在包装上，从分类默认值编一个日期会驱动临期提醒。
 */
private fun ingredientFromBarcode(
    name: String,
    backendCategory: String?,
    shelfLifeDays: Int?
): Ingredient {
    val category = backendCategory?.toIngredientCategory() ?: IngredientCategory.OTHER
    return Ingredient(
        name = name.trim(),
        quantity = "1个",
        quantityAmount = 1.0,
        quantityUnit = "个",
        freshness = Freshness.UNKNOWN,
        category = category,
        shelfLifeDays = shelfLifeDays,
        recognitionDerived = shelfLifeDays != null
    )
}

/**
 * 从图片 Uri 解码位图，**限制最长边**避免整张原图进内存。
 *
 * 相册原图常见 4000×3000，全尺寸 ARGB_8888 是 48MB，而这张图最终只是压成 JPEG 发给后端识别。
 * `setTargetSampleSize` 让解码器直接按 1/2、1/4… 出图，峰值内存降一到两个数量级。
 *
 * @return null 表示这个 Uri 解不出图（格式不支持、文件损坏、权限撤销）。调用方必须提示。
 */
fun uriToBitmap(context: Context, uri: Uri): Bitmap? {
    return try {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            val source = ImageDecoder.createSource(context.contentResolver, uri)
            ImageDecoder.decodeBitmap(source) { decoder, info, _ ->
                decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
                decoder.setTargetSampleSize(
                    calculateSampleSize(info.size.width, info.size.height, GALLERY_MAX_EDGE)
                )
            }
        } else {
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            context.contentResolver.openInputStream(uri)?.use {
                BitmapFactory.decodeStream(it, null, bounds)
            }
            if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
            val options = BitmapFactory.Options().apply {
                inSampleSize = calculateSampleSize(
                    bounds.outWidth, bounds.outHeight, GALLERY_MAX_EDGE
                )
            }
            context.contentResolver.openInputStream(uri)?.use {
                BitmapFactory.decodeStream(it, null, options)
            }
        }
    } catch (e: Exception) {
        Log.w("CameraScreen", "相册图片解码失败: ${e.message}")
        null
    }
}

/** 相册图解码后的最长边上限。识别不需要更高分辨率，而原图会吃掉几十 MB。 */
private const val GALLERY_MAX_EDGE = 2048

internal fun calculateSampleSize(width: Int, height: Int, maxEdge: Int): Int {
    if (width <= 0 || height <= 0 || maxEdge <= 0) return 1
    val longest = maxOf(width, height)
    var sample = 1
    while (longest / sample > maxEdge && sample <= Int.MAX_VALUE / 2) sample *= 2
    return sample
}

private fun createCameraFile(context: Context): File {
    val directory = File(context.cacheDir, "camera").apply { mkdirs() }
    return File.createTempFile("ingredient_", ".jpg", directory)
}

/**
 * Bitmap 转 JPEG 字节。
 *
 * 后端 `/api/recognize` 接受 multipart 原始字节；压缩参数集中在这里，避免调用方漂移。
 */
fun bitmapToJpegBytes(bitmap: Bitmap): ByteArray {
    val byteArrayOutputStream = ByteArrayOutputStream()
    bitmap.compress(Bitmap.CompressFormat.JPEG, 80, byteArrayOutputStream)
    return byteArrayOutputStream.toByteArray()
}
