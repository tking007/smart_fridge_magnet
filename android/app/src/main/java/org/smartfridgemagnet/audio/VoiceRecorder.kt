package org.smartfridgemagnet.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream

/**
 * 按住说话的录音器：`AudioRecord` 采 16-bit 单声道 PCM，松手后裹成 WAV。
 *
 * 用 AudioRecord 而不是 MediaRecorder：ASR 只收 wav / mp3，而 `MediaRecorder` 产的是 MP4/AAC 或
 * 3GP/AMR。`AudioRecord` 给的是裸 PCM，加 44 字节头就是 wav（[WavWriter]）。
 *
 * 16 kHz 单声道：人声主要能量在 4 kHz 以下，16 kHz 已是语音领域的惯例上限；它直接决定上传体积
 * （16 kHz/16-bit/单声道 32 KB/s，而 44.1 kHz 立体声 176 KB/s 一分钟就撞上 ASR 的
 * 「base64 后 ≤10 MB」那条线）。
 *
 * 不做 VAD、不做自动停止：交互是「按住说、松手发」，自动检测静音结束在嘈杂厨房里会把人说到
 * 一半的句子截断，而用户只会觉得识别不准。唯一的自动停止是 [MAX_DURATION_MS]（60 秒），
 * 它是协议上限的换算：60 秒 ≈ 1.92 MB，base64 后约 2.6 MB。
 */
class VoiceRecorder {

    /** 录音状态。UI 只关心 [isRecording]，其余细节不外露。 */
    @Volatile
    var isRecording: Boolean = false
        private set

    /**
     * 开始录音并**阻塞**读取，直到 [stop] 被调用、达到 [MAX_DURATION_MS]、或出错。
     *
     * 返回 WAV 字节；失败返回 `Result.failure`。
     *
     * 设计成一个挂起函数读到底而不是 start()/stop() 两个回调：录音有且只有一个产物（那段音频），
     * 用返回值表达比让调用方去某个字段里取更难写错。
     */
    @SuppressLint("MissingPermission") // 调用方负责在启动前确认 RECORD_AUDIO 已授权
    suspend fun record(): Result<ByteArray> = withContext(Dispatchers.IO) {
        if (isRecording) {
            return@withContext Result.failure(IllegalStateException("已在录音中"))
        }

        val minBuffer = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT)
        if (minBuffer == AudioRecord.ERROR || minBuffer == AudioRecord.ERROR_BAD_VALUE) {
            return@withContext Result.failure(
                IllegalStateException("设备不支持 ${SAMPLE_RATE}Hz 单声道 16-bit 录音")
            )
        }
        // 缓冲取 minBuffer 的两倍：正好一个的话，读循环稍有延迟就会丢采样，而丢采样的表现
        // 是识别结果少几个字 —— 看起来像模型不准，不像缓冲不够。
        val bufferSize = minBuffer * 2

        val audio = try {
            AudioRecord(
                // VOICE_RECOGNITION 而不是 MIC：系统会针对识别场景调整前处理（降噪、
                // 不加自动增益的音乐优化）。MIC 在部分 ROM 上会带上为录像调的音效。
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT, bufferSize
            )
        } catch (e: Exception) {
            // 未授权 RECORD_AUDIO 时构造就会抛。
            Log.e(TAG, "AudioRecord init failed", e)
            return@withContext Result.failure(IllegalStateException("无法打开麦克风：${e.message}"))
        }

        if (audio.state != AudioRecord.STATE_INITIALIZED) {
            audio.release()
            return@withContext Result.failure(
                IllegalStateException("麦克风被占用或权限未授予")
            )
        }

        val pcm = ByteArrayOutputStream()
        val chunk = ByteArray(bufferSize)
        val maxBytes = MAX_DURATION_MS / 1000 * SAMPLE_RATE * 2   // 16-bit 单声道

        try {
            audio.startRecording()
            isRecording = true
            while (isRecording) {
                val read = audio.read(chunk, 0, chunk.size)
                if (read <= 0) {
                    // 负值是错误码（ERROR_INVALID_OPERATION 等）。已经采到的部分仍然可用，
                    // 所以跳出而不是整段丢弃 —— 用户说过的话不该因为最后一次读失败就没了。
                    if (read < 0) Log.w(TAG, "AudioRecord.read returned $read")
                    break
                }
                pcm.write(chunk, 0, read)
                if (pcm.size() >= maxBytes) {
                    Log.i(TAG, "reached max duration ${MAX_DURATION_MS}ms, stopping")
                    break
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "record failed", e)
            return@withContext Result.failure(e)
        } finally {
            isRecording = false
            runCatching { if (audio.recordingState == AudioRecord.RECORDSTATE_RECORDING) audio.stop() }
            runCatching { audio.release() }
        }

        val bytes = pcm.toByteArray()
        val ms = WavWriter.durationMs(bytes.size, SAMPLE_RATE)
        if (ms < MIN_DURATION_MS) {
            // 点一下就松手（或权限刚给完那一瞬）会得到几十毫秒的噪声。发出去只会换回一个
            // 空串，然后用户看到"没听到内容"却不知道是自己按得太快。这里说清楚。
            return@withContext Result.failure(
                IllegalStateException("录音太短（${ms}ms），请按住说话")
            )
        }
        Result.success(WavWriter.wrap(bytes, SAMPLE_RATE))
    }

    /** 松手时调用。让 [record] 的读循环退出并返回已采到的音频。 */
    fun stop() {
        isRecording = false
    }

    companion object {
        private const val TAG = "VoiceRecorder"

        const val SAMPLE_RATE = 16000
        private const val CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO
        private const val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT

        /** 单次录音上限。见类注释：这是 ASR 体积上限的换算，不是产品偏好。 */
        const val MAX_DURATION_MS = 60_000

        /** 短于此视为误触。 */
        const val MIN_DURATION_MS = 300
    }
}
