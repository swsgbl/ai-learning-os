package com.ailearningos.app.voice

import java.io.ByteArrayOutputStream

/**
 * WAV（RIFF/PCM16）封装与读取：纯逻辑，无 Android 依赖（JVM 可测）。
 *
 * 服务端合成/转写契约固定 audio/wav；采集侧把 AudioRecord 的裸 PCM
 * 封成同形态上传，播放侧不解析、整体交给 AudioTrack。
 */
object WavCodec {

    const val SAMPLE_RATE = 16_000
    const val CHANNELS = 1
    const val BITS_PER_SAMPLE = 16

    /** 裸 PCM16 → 完整 WAV 字节（RIFF header + data） */
    fun wrapPcm16(pcm: ByteArray, sampleRate: Int = SAMPLE_RATE, channels: Int = CHANNELS): ByteArray {
        val byteRate = sampleRate * channels * BITS_PER_SAMPLE / 8
        val blockAlign = channels * BITS_PER_SAMPLE / 8
        val out = ByteArrayOutputStream(44 + pcm.size)
        out.write("RIFF".toByteArray())
        writeIntLE(out, 36 + pcm.size)
        out.write("WAVE".toByteArray())
        out.write("fmt ".toByteArray())
        writeIntLE(out, 16) // PCM chunk 大小
        writeShortLE(out, 1) // PCM 格式
        writeShortLE(out, channels)
        writeIntLE(out, sampleRate)
        writeIntLE(out, byteRate)
        writeShortLE(out, blockAlign)
        writeShortLE(out, BITS_PER_SAMPLE)
        out.write("data".toByteArray())
        writeIntLE(out, pcm.size)
        out.write(pcm)
        return out.toByteArray()
    }

    /** 解析出的 PCM 规格（fmt/data chunk 投影；dataOffset=data 区在文件中的起点） */
    data class PcmSpec(
        val sampleRate: Int,
        val channels: Int,
        val bitsPerSample: Int,
        val dataOffset: Int,
        val dataBytes: Int,
    ) {
        val bytesPerFrame: Int get() = channels * bitsPerSample / 8

        /** 可播放的完整帧数（非整帧的残字节向下取整——不把残字节当音频） */
        val totalFrames: Int get() = if (bytesPerFrame > 0) dataBytes / bytesPerFrame else 0

        val dataEnd: Int get() = dataOffset + dataBytes
    }

    /**
     * 解析 fmt/data chunk 得到 PCM 规格；非 RIFF/WAVE、字段非法或 data 区
     * 越界（声称长度超出文件实际）返回 null——不猜。
     * dataOffset 保留实际起点：LIST/fact 等合法前置 chunk 使 data 不在 44，
     * 播放必须从 [PcmSpec.dataOffset] 消费而非固定 44。
     * （服务端本地 TTS 合成 8kHz WAV、云端可能 16kHz+——播放参数以 header 为准。）
     */
    fun pcmSpec(wav: ByteArray): PcmSpec? {
        if (wav.size < 44) return null
        if (!wav.copyOfRange(0, 4).contentEquals("RIFF".toByteArray())) return null
        if (!wav.copyOfRange(8, 12).contentEquals("WAVE".toByteArray())) return null
        var offset = 12
        var sampleRate = -1
        var channels = -1
        var bitsPerSample = -1
        var dataOffset = -1
        var dataBytes = -1
        while (offset + 8 <= wav.size) {
            val chunkId = wav.copyOfRange(offset, offset + 4)
            val chunkSize = readIntLE(wav, offset + 4)
            val body = offset + 8
            when {
                chunkId.contentEquals("fmt ".toByteArray()) && body + 16 <= wav.size -> {
                    channels = readShortLE(wav, body + 2)
                    sampleRate = readIntLE(wav, body + 4)
                    bitsPerSample = readShortLE(wav, body + 14)
                }
                chunkId.contentEquals("data".toByteArray()) -> {
                    dataOffset = body
                    dataBytes = chunkSize
                }
            }
            if (chunkSize <= 0) break
            // Long 累加防 Int 溢出（恶意/损坏 WAV 的巨大 chunkSize 不得绕回负 offset）；
            // 声称的 chunk 超出文件实际 → 停止遍历，靠 data 边界校验 fail-closed
            val next = body.toLong() + chunkSize.toLong() + (chunkSize % 2)
            if (next > wav.size) break
            offset = next.toInt()
        }
        if (sampleRate <= 0 || channels <= 0 || bitsPerSample != BITS_PER_SAMPLE) return null
        if (dataOffset < 0 || dataBytes < 0) return null
        if (dataBytes.toLong() + dataOffset > wav.size) return null // 截断/谎报长度：拒绝
        return PcmSpec(
            sampleRate = sampleRate,
            channels = channels,
            bitsPerSample = bitsPerSample,
            dataOffset = dataOffset,
            dataBytes = dataBytes,
        )
    }

    /** 从 WAV 头推算播放时长（毫秒）；非 RIFF/WAVE 形态返回 null——不猜时长 */
    fun durationMillis(wav: ByteArray): Long? {
        val spec = pcmSpec(wav) ?: return null
        val bytesPerFrame = spec.channels * spec.bitsPerSample / 8
        return spec.dataBytes.toLong() * 1000L / (spec.sampleRate.toLong() * bytesPerFrame)
    }

    // ---------- 小端读写 ----------

    private fun writeIntLE(out: ByteArrayOutputStream, value: Int) {
        out.write(value and 0xFF)
        out.write((value ushr 8) and 0xFF)
        out.write((value ushr 16) and 0xFF)
        out.write((value ushr 24) and 0xFF)
    }

    private fun writeShortLE(out: ByteArrayOutputStream, value: Int) {
        out.write(value and 0xFF)
        out.write((value ushr 8) and 0xFF)
    }

    private fun readIntLE(bytes: ByteArray, offset: Int): Int =
        (bytes[offset].toInt() and 0xFF) or
            ((bytes[offset + 1].toInt() and 0xFF) shl 8) or
            ((bytes[offset + 2].toInt() and 0xFF) shl 16) or
            ((bytes[offset + 3].toInt() and 0xFF) shl 24)

    private fun readShortLE(bytes: ByteArray, offset: Int): Int =
        (bytes[offset].toInt() and 0xFF) or ((bytes[offset + 1].toInt() and 0xFF) shl 8)
}
