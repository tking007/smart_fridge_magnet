package org.smartfridgemagnet.audio

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

/**
 * WAV 头逐字节校验。
 *
 * 这 44 个字节写错的表现是**上游拒收，或者识别出一串乱码**，而 App 侧一路没有异常（字节发出去了、
 * HTTP 也可能 200）。所以这里对着 RIFF 规范逐字段断言，而不是只看「能不能被某个播放器打开」。
 *
 * 后端 `ai_client.build_test_wav()` 手写的是同一份头：两边任一侧改错，这里与后端
 * tests/test_asr.py 的断言会分别失败，不会一起悄悄漂走。
 */
class WavWriterTest {

    private fun le32(b: ByteArray, at: Int): Int =
        (b[at].toInt() and 0xFF) or
            ((b[at + 1].toInt() and 0xFF) shl 8) or
            ((b[at + 2].toInt() and 0xFF) shl 16) or
            ((b[at + 3].toInt() and 0xFF) shl 24)

    private fun le16(b: ByteArray, at: Int): Int =
        (b[at].toInt() and 0xFF) or ((b[at + 1].toInt() and 0xFF) shl 8)

    private fun ascii(b: ByteArray, at: Int, len: Int) =
        String(b, at, len, Charsets.US_ASCII)

    @Test
    fun `头部恰 44 字节且总长等于头加数据`() {
        val pcm = ByteArray(3200) { (it % 256).toByte() }
        val wav = WavWriter.wrap(pcm, 16000)
        assertEquals(44, WavWriter.HEADER_SIZE)
        assertEquals(44 + pcm.size, wav.size)
    }

    @Test
    fun `块标识与规范一致`() {
        val wav = WavWriter.wrap(ByteArray(100), 16000)
        assertEquals("RIFF", ascii(wav, 0, 4))
        assertEquals("WAVE", ascii(wav, 8, 4))
        // 结尾那个空格是规范的一部分：块 ID 恒为 4 字节，"fmt" 只有 3 个字符
        assertEquals("fmt ", ascii(wav, 12, 4))
        assertEquals("data", ascii(wav, 36, 4))
    }

    @Test
    fun `RIFF 长度不含前八字节`() {
        val pcm = ByteArray(1000)
        val wav = WavWriter.wrap(pcm, 16000)
        // 写成 wav.size 是最常见的错法：多数播放器能容忍，严格的解析器会判文件截断
        assertEquals(36 + pcm.size, le32(wav, 4))
        assertEquals(wav.size - 8, le32(wav, 4))
    }

    @Test
    fun `fmt 块按 16-bit 线性 PCM 填写`() {
        val wav = WavWriter.wrap(ByteArray(64), sampleRate = 16000, channels = 1)
        assertEquals("fmt 块长度", 16, le32(wav, 16))
        assertEquals("audioFormat 1 = 线性 PCM", 1, le16(wav, 20))
        assertEquals("单声道", 1, le16(wav, 22))
        assertEquals("采样率", 16000, le32(wav, 24))
        // byteRate = sampleRate * channels * bits/8
        assertEquals("byteRate", 16000 * 1 * 2, le32(wav, 28))
        assertEquals("blockAlign", 2, le16(wav, 32))
        assertEquals("bitsPerSample", 16, le16(wav, 34))
    }

    @Test
    fun `立体声与不同采样率的派生字段跟着变`() {
        val wav = WavWriter.wrap(ByteArray(64), sampleRate = 44100, channels = 2)
        assertEquals(2, le16(wav, 22))
        assertEquals(44100, le32(wav, 24))
        assertEquals(44100 * 2 * 2, le32(wav, 28))
        assertEquals(4, le16(wav, 32))
    }

    @Test
    fun `data 长度是 PCM 字节数且样本原样跟在头后面`() {
        val pcm = byteArrayOf(1, 2, 3, 4, 5, 6, 7, 8)
        val wav = WavWriter.wrap(pcm, 16000)
        assertEquals(pcm.size, le32(wav, 40))
        assertArrayEquals(pcm, wav.copyOfRange(44, wav.size))
    }

    @Test
    fun `空 PCM 也产出合法头`() {
        // 极端但会真实发生：AudioRecord 一次都没读到就被 stop()。
        // 此时不该抛，产物是一段零长度音频，由上层按时长判定拦下来。
        val wav = WavWriter.wrap(ByteArray(0), 16000)
        assertEquals(44, wav.size)
        assertEquals(36, le32(wav, 4))
        assertEquals(0, le32(wav, 40))
    }

    @Test
    fun `非法采样率与声道数直接抛`() {
        assertThrows(IllegalArgumentException::class.java) {
            WavWriter.wrap(ByteArray(16), sampleRate = 0)
        }
        assertThrows(IllegalArgumentException::class.java) {
            WavWriter.wrap(ByteArray(16), sampleRate = -16000)
        }
        assertThrows(IllegalArgumentException::class.java) {
            WavWriter.wrap(ByteArray(16), sampleRate = 16000, channels = 0)
        }
    }

    @Test
    fun `时长按字节算`() {
        // 16kHz 单声道 16-bit = 32000 字节/秒
        assertEquals(1000, WavWriter.durationMs(32000, 16000))
        assertEquals(500, WavWriter.durationMs(16000, 16000))
        assertEquals(0, WavWriter.durationMs(0, 16000))
        // 立体声同样字节数只有一半时长
        assertEquals(500, WavWriter.durationMs(32000, 16000, channels = 2))
        // 非法采样率返回 0 而不是抛：这个函数用在错误处理路径上（判断"录太短"），
        // 在那里再抛一次异常只会盖掉真正的原因
        assertEquals(0, WavWriter.durationMs(32000, 0))
    }

    @Test
    fun `一分钟录音仍在 ASR 的体积上限内`() {
        // VoiceRecorder.MAX_DURATION_MS 是 60 秒，而 ASR 的限制是 base64 后 ≤10MB。
        // 这条断言把那个换算钉住：改采样率或改上限时，会先在这里失败而不是在真机上
        // 收到一条「音频过大」。
        val bytesPerSecond = VoiceRecorder.SAMPLE_RATE * 2
        val pcmBytes = bytesPerSecond * (VoiceRecorder.MAX_DURATION_MS / 1000)
        val wavBytes = pcmBytes + WavWriter.HEADER_SIZE
        val base64Bytes = (wavBytes + 2) / 3 * 4
        assert(base64Bytes < 10 * 1024 * 1024) {
            "60 秒录音 base64 后 $base64Bytes 字节，超过 ASR 的 10MB 上限"
        }
        // 也要在后端那条更严的原始字节上限（7.5MB）之内
        assert(wavBytes < 7_500_000) { "60 秒录音 $wavBytes 字节，超过后端 7.5MB 上限" }
    }
}
