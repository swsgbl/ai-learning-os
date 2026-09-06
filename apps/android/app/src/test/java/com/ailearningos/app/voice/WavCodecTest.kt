package com.ailearningos.app.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WAV 封装/读取纯逻辑（M12-03）：
 * - RIFF header 字段与字节序（服务端/本地采集同一形态契约）；
 * - 时长推算按 header 实际采样率（服务端本地 TTS 为 8kHz）；
 * - 非 RIFF/WAVE 拒绝——不猜时长。
 */
class WavCodecTest {

    @Test
    fun `wrapPcm16 writes riff wav header little endian`() {
        val pcm = ByteArray(320) // 16000Hz/mono/16bit 下 10ms
        val wav = WavCodec.wrapPcm16(pcm)
        assertEquals("RIFF", String(wav, 0, 4, Charsets.US_ASCII))
        assertEquals("WAVE", String(wav, 8, 4, Charsets.US_ASCII))
        assertEquals("fmt ", String(wav, 12, 4, Charsets.US_ASCII))
        assertEquals("data", String(wav, 36, 4, Charsets.US_ASCII))
        assertEquals(36 + 320, readIntLE(wav, 4))
        assertEquals(16, readIntLE(wav, 16)) // PCM chunk 大小
        assertEquals(1, readShortLE(wav, 20)) // PCM 格式
        assertEquals(1, readShortLE(wav, 22)) // 单声道
        assertEquals(16_000, readIntLE(wav, 24))
        assertEquals(32_000, readIntLE(wav, 28)) // byte rate
        assertEquals(2, readShortLE(wav, 32)) // block align
        assertEquals(16, readShortLE(wav, 34))
        assertEquals(320, readIntLE(wav, 40))
        assertTrue(wav.copyOfRange(44, 44 + 320).contentEquals(pcm))
    }

    @Test
    fun `durationMillis computes from actual sample rate`() {
        // 8kHz/mono/16bit：8000 字节 = 4000 帧 = 500ms（服务端本地 TTS 形态）
        val wav8k = WavCodec.wrapPcm16(ByteArray(8000), sampleRate = 8000)
        assertEquals(500L, WavCodec.durationMillis(wav8k))
        // 16kHz：32000 字节 = 16000 帧 = 1000ms（采集形态）
        val wav16k = WavCodec.wrapPcm16(ByteArray(32000), sampleRate = 16000)
        assertEquals(1000L, WavCodec.durationMillis(wav16k))
    }

    @Test
    fun `pcmSpec exposes header sample rate and channels`() {
        val stereo = WavCodec.wrapPcm16(ByteArray(4), sampleRate = 44_100, channels = 2)
        val spec = WavCodec.pcmSpec(stereo)
        assertEquals(44_100, spec?.sampleRate)
        assertEquals(2, spec?.channels)
        assertEquals(16, spec?.bitsPerSample)
    }

    @Test
    fun `non wav input rejected not guessed`() {
        assertNull(WavCodec.pcmSpec(ByteArray(10)))
        assertNull(WavCodec.pcmSpec("not a wav file.....".toByteArray()))
        assertNull(WavCodec.durationMillis(ByteArray(3)))
    }

    @Test
    fun `pcmSpec exposes data offset after leading list chunk`() {
        // RIFF(12) + LIST(8+10) + fmt(8+16) + data header(8) → dataOffset = 62（非 44）
        val wav = wavWithLeadingListChunk(pcmBytes = 60)
        val spec = WavCodec.pcmSpec(wav)
        assertNotNull(spec)
        assertEquals(62, spec?.dataOffset)
        assertEquals(60, spec?.dataBytes)
        assertEquals(122, spec?.dataEnd) // dataOffset + dataBytes
        assertEquals(30, spec?.totalFrames) // 16k/mono/16bit：60 字节 = 30 帧
        // 时长按 data 区实际字节数（不把 LIST 当音频）：30 帧 @ 16kHz = 30*1000/16000 = 1ms
        assertEquals(1L, WavCodec.durationMillis(wav))
    }

    @Test
    fun `pcmSpec rejects data chunk claiming more bytes than file holds`() {
        val wav = WavCodec.wrapPcm16(ByteArray(100))
        // 篡改 data chunk size（offset 40）为超出文件的实际长度
        wav[40] = 0xFF.toByte()
        wav[41] = 0xFF.toByte()
        wav[42] = 0x00
        wav[43] = 0x00
        assertNull("截断/谎报长度必须拒绝", WavCodec.pcmSpec(wav))
    }

    @Test
    fun `pcmSpec odd data bytes floor to whole frames`() {
        val wav = WavCodec.wrapPcm16(ByteArray(61)) // 61 字节 = 30 帧 + 1 残字节
        val spec = WavCodec.pcmSpec(wav)
        assertEquals(30, spec?.totalFrames)
    }

    @Test
    fun `pcmSpec rejects overflowing chunk size without crash or loop`() {
        // JUNK chunk 声称 0x7FFFFFFE 字节：body + chunkSize 会溢出 Int——
        // 必须以 Long 累加判界后 fail-closed 返回 null，不得负索引异常或死循环
        val out = java.io.ByteArrayOutputStream()
        out.write("RIFF".toByteArray())
        writeIntLEForTest(out, 100)
        out.write("WAVE".toByteArray())
        out.write("JUNK".toByteArray())
        writeIntLEForTest(out, 0x7FFFFFFE)
        out.write(ByteArray(8))
        assertNull("溢出 chunk 必须拒绝", WavCodec.pcmSpec(out.toByteArray()))
    }

    @Test
    fun `pcmSpec rejects data chunk size overflowing beyond file`() {
        // 合法 fmt 后 data 声称接近 Int.Max 的长度：data 边界校验拒绝（不猜）
        val wav = WavCodec.wrapPcm16(ByteArray(10))
        wav[40] = 0xFE.toByte()
        wav[41] = 0xFF.toByte()
        wav[42] = 0xFF.toByte()
        wav[43] = 0x7F // dataBytes ≈ 0x7FFFFFFE
        assertNull("data 区越界（含溢出量级）必须拒绝", WavCodec.pcmSpec(wav))
    }

    // ---------- 带前置 LIST chunk 的 WAV 构造（合法 RIFF 形态，data 不在 44） ----------

    private fun wavWithLeadingListChunk(pcmBytes: Int): ByteArray {
        val pcm = ByteArray(pcmBytes)
        val listBody = ByteArray(10) // 偶数大小，无 pad 字节
        val out = java.io.ByteArrayOutputStream()
        out.write("RIFF".toByteArray())
        writeIntLEForTest(out, 4 + (8 + listBody.size) + (8 + 16) + (8 + pcm.size))
        out.write("WAVE".toByteArray())
        out.write("LIST".toByteArray())
        writeIntLEForTest(out, listBody.size)
        out.write(listBody)
        val plain = WavCodec.wrapPcm16(ByteArray(0)) // 借用标准 fmt chunk 拼装
        out.write(plain.copyOfRange(12, 36)) // "fmt " + size + 16 字节体
        out.write("data".toByteArray())
        writeIntLEForTest(out, pcm.size)
        out.write(pcm)
        return out.toByteArray()
    }

    private fun writeIntLEForTest(out: java.io.ByteArrayOutputStream, value: Int) {
        out.write(value and 0xFF)
        out.write((value ushr 8) and 0xFF)
        out.write((value ushr 16) and 0xFF)
        out.write((value ushr 24) and 0xFF)
    }

    // ---------- 直接读字节（独立于被测读取路径，锁 header 字节序） ----------

    private fun readIntLE(bytes: ByteArray, offset: Int): Int =
        (bytes[offset].toInt() and 0xFF) or
            ((bytes[offset + 1].toInt() and 0xFF) shl 8) or
            ((bytes[offset + 2].toInt() and 0xFF) shl 16) or
            ((bytes[offset + 3].toInt() and 0xFF) shl 24)

    private fun readShortLE(bytes: ByteArray, offset: Int): Int =
        (bytes[offset].toInt() and 0xFF) or ((bytes[offset + 1].toInt() and 0xFF) shl 8)
}
