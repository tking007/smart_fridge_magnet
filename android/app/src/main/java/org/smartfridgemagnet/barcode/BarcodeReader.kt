package org.smartfridgemagnet.barcode

import android.graphics.Bitmap
import android.util.Log
import com.google.zxing.BarcodeFormat
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.MultiFormatReader
import com.google.zxing.NotFoundException
import com.google.zxing.RGBLuminanceSource
import com.google.zxing.common.HybridBinarizer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * 从系统相机保存的静态照片中读取一维商品码。
 *
 * 使用 ZXing core，避免为辅助入口引入另一套相机栈或依赖 GMS。输入最长边限制为 2048；
 * [read] 找不到条码时返回 null，由调用方提示用户靠近重拍。
 */
object BarcodeReader {

    private const val TAG = "BarcodeReader"

    /**
     * 只认一维商品条码。
     *
     * 刻意不接受 QR_CODE 与其它二维码：冰箱里的东西不会贴二维码，而收款码、网址码解出来
     * 是一串 URL，拿去问模型「这是什么商品」只会得到一个编造的答案。
     */
    private val PRODUCT_FORMATS = listOf(
        BarcodeFormat.EAN_13,
        BarcodeFormat.EAN_8,
        BarcodeFormat.UPC_A,
        BarcodeFormat.UPC_E,
    )

    /**
     * 识别结果。
     *
     * @param value 条码内容（EAN-13 是 13 位数字；二维码则是它承载的文本）
     * @param formatName 供日志与界面显示，例如 "EAN_13"
     * @param isProductCode 是否是一维商品条码（见 [PRODUCT_FORMATS]）。false 表示扫到了码但不是
     *        商品码（多半是二维码）—— 调用方要把它与「完全没扫到」分开提示：前者是扫错了东西，
     *        后者要靠近重拍。
     */
    data class Result(val value: String, val formatName: String, val isProductCode: Boolean)

    /**
     * 从位图里读**任意一个**码，并标出它是不是商品条码。
     *
     * 一次解码而不是「先按商品码试、读不到再不限格式试一遍」：两次都是整幅图 + TRY_HARDER，而在
     * 最常见的失败路径上（画面里根本没有码）第二次注定也读不到。
     *
     * @return 找到的码；没找到（或解码失败）返回 null。
     */
    suspend fun read(bitmap: Bitmap): Result? = decode(bitmap)?.let { (text, format) ->
        Result(value = text, formatName = format.name, isProductCode = format in PRODUCT_FORMATS)
    }

    private suspend fun decode(
        bitmap: Bitmap
    ): Pair<String, BarcodeFormat>? = withContext(Dispatchers.Default) {
        // 解码是纯 CPU 计算且对整幅图做二值化，放到 Default 调度器上，别占住主线程。
        val width = bitmap.width
        val height = bitmap.height
        if (width <= 0 || height <= 0) return@withContext null

        val pixels = IntArray(width * height)
        bitmap.getPixels(pixels, 0, width, 0, 0, width, height)

        val source = RGBLuminanceSource(width, height, pixels)
        val reader = MultiFormatReader()
        // 不限定 POSSIBLE_FORMATS：解出来的格式由调用方判断是不是商品码。限定格式会让
        // "扫到二维码"这种情况变成"没扫到"，而这两种要给出的引导完全不同。
        // TRY_HARDER：这是单张静态图、只解一次，多花几十毫秒换更高成功率是划算的
        // （实时预览里才需要吝惜每一帧的耗时）。
        reader.setHints(mapOf<DecodeHintType, Any>(DecodeHintType.TRY_HARDER to true))

        try {
            val result = reader.decodeWithState(BinaryBitmap(HybridBinarizer(source)))
            val text = result.text?.trim()
            if (text.isNullOrEmpty()) null else text to result.barcodeFormat
        } catch (_: NotFoundException) {
            // 没找到码是正常结果，不是异常情况 —— 调用方据此提示重拍或改用拍照识别。
            null
        } catch (e: Exception) {
            // 解码器对畸形输入可能抛别的异常。同样按"没读到"处理：把一个
            // ArrayIndexOutOfBounds 透给用户没有任何用处。
            Log.w(TAG, "barcode decode failed", e)
            null
        }
    }
}
