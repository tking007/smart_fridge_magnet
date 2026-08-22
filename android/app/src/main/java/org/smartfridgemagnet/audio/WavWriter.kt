package org.smartfridgemagnet.audio

/**
 * 把 16-bit 单声道 PCM 裹上 44 字节 RIFF/WAVE 头。
 *
 * 自己封 WAV 是因为 AI 的 ASR **只接受 wav 与 mp3**，而 Android 的 `MediaRecorder` 一个都不产
 * （容器 MP4/3GP、编码 AAC 或 AMR）。用 `AudioRecord` 读原始 PCM 再加 44 字节头即可，另一条路
 * （引编码库转 mp3）要为一段几十 KB 的语音多背一个 native 库。
 *
 * 每个字段都对着 RIFF 规范写、并在测试里逐字节断言：头写错的表现是上游拒收或识别出乱码，而
 * App 侧一路没有异常。
 *
 * 全部字段小端序（RIFF 规范如此，与 BLE 那边的大端相反）。
 */
object WavWriter {

    /** RIFF/WAVE 的固定头长度：12（RIFF 块）+ 24（fmt 块）+ 8（data 块头）。 */
    const val HEADER_SIZE = 44

    /**
     * @param pcm 16-bit 小端、交错排列的 PCM 采样
     * @param sampleRate 采样率（本项目用 16000，见 [VoiceRecorder]）
     * @param channels 声道数（1 = 单声道）
     */
    fun wrap(pcm: ByteArray, sampleRate: Int, channels: Int = 1): ByteArray {
        require(sampleRate > 0) { "sampleRate 必须为正：$sampleRate" }
        require(channels > 0) { "channels 必须为正：$channels" }

        val bitsPerSample = 16
        val bytesPerFrame = channels * bitsPerSample / 8
        val byteRate = sampleRate * bytesPerFrame
        val out = ByteArray(HEADER_SIZE + pcm.size)
        var p = 0

        fun ascii(text: String) {
            for (c in text) out[p++] = c.code.toByte()
        }
        fun le32(value: Int) {
            out[p++] = (value and 0xFF).toByte()
            out[p++] = (value ushr 8 and 0xFF).toByte()
            out[p++] = (value ushr 16 and 0xFF).toByte()
            out[p++] = (value ushr 24 and 0xFF).toByte()
        }
        fun le16(value: Int) {
            out[p++] = (value and 0xFF).toByte()
            out[p++] = (value ushr 8 and 0xFF).toByte()
        }

        // ---- RIFF 块 ----
        ascii("RIFF")
        // 这个长度**不含**前 8 字节（"RIFF" + 本字段自己）。写成整个文件长度是最常见的
        // 错法，多数播放器能容忍，严格的解析器会判文件截断。
        le32(36 + pcm.size)
        ascii("WAVE")

        // ---- fmt 子块 ----
        ascii("fmt ")          // 注意结尾有个空格，块 ID 恒为 4 字节
        le32(16)               // PCM 的 fmt 块长度固定 16
        le16(1)                // audioFormat: 1 = 线性 PCM（非压缩）
        le16(channels)
        le32(sampleRate)
        le32(byteRate)         // = sampleRate * channels * bitsPerSample / 8
        le16(bytesPerFrame)    // blockAlign：一帧（所有声道各一个采样）的字节数
        le16(bitsPerSample)

        // ---- data 子块 ----
        ascii("data")
        le32(pcm.size)
        pcm.copyInto(out, p)
        return out
    }

    /**
     * 这段 PCM 有多长（毫秒）。用于「录太短就别发了」这类判断。
     *
     * 按字节算而不是记时间戳：真正发出去的时长由采到的字节数决定，而 `AudioRecord` 在
     * 系统繁忙时会丢缓冲 —— 那时按墙上时钟算出的时长会比实际音频长。
     */
    fun durationMs(pcmBytes: Int, sampleRate: Int, channels: Int = 1): Int {
        if (sampleRate <= 0 || channels <= 0) return 0
        val frames = pcmBytes / (channels * 2)
        return (frames * 1000L / sampleRate).toInt()
    }
}
