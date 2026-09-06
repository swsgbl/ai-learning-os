package com.ailearningos.app.voice

import android.annotation.SuppressLint
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 音频引擎失败（启动/采集/播放）：固定脱敏文案，不携带底层异常细节。
 */
class AudioEngineException(val userMessage: String) : Exception(userMessage)

/**
 * 录音采集引擎（M12-03）：可注入接口，生产侧采集 16kHz/单声道/PCM16 WAV。
 *
 * 诚实边界：
 * - 未获 RECORD_AUDIO 授权时调用方不得启动（UI 层先请求权限，拒绝则降级文本作答）；
 * - start/stop 失败如实抛 [AudioEngineException]，绝不冒充已录音。
 */
interface AudioCaptureEngine {
    /** 开始采集；设备忙/初始化失败抛 [AudioEngineException] */
    suspend fun start()

    /** 停止采集并返回完整 WAV 字节；无有效采样抛 [AudioEngineException] */
    suspend fun stop(): ByteArray

    /** 是否正在采集（用户可见的录音状态数据源） */
    val isActive: Boolean
}

/**
 * TTS 播放引擎（M12-03）：可注入接口，播放服务端合成的 WAV。
 *
 * play 挂起至**播放完成**（供「读题 → 读选项」链式推进——写完只代表
 * 数据入队，播完以播放头到达终点为准）；stop 立即停止当前播放
 * （暂停/打断路径）。播放失败如实抛 [AudioEngineException]，由上层显示
 * 可重试错误——绝不静默假装成功。
 */
interface AudioPlaybackEngine {
    suspend fun play(wav: ByteArray)

    fun stop()
}

// ---------- 播放编排（JVM 可测核心：写完 ≠ 播完） ----------

/**
 * WAV 播放驱动：生产侧桥接 AudioTrack（marker 回调 = 播放头到达终点），
 * 测试侧用 fake 驱动编程完成时机——「写完 ≠ 播完」的推进条件由
 * [WavPlaybackSession] 编排并以 [WavPlaybackSessionTest] 锁定。
 */
internal interface WavPlaybackDriver {
    /** 按规格启动轨道并注册播放完成回调（驱动侧在播放头到达终点时触发一次） */
    fun start(spec: WavCodec.PcmSpec, onPlaybackFinished: () -> Unit): Handle

    interface Handle {
        /** 阻塞写入一段 PCM（消费区间由调用方按 spec.dataOffset 指定） */
        fun write(pcm: ByteArray, offset: Int, length: Int)

        /** 立即暂停（外部停止路径） */
        fun pause()

        /** 释放底层资源（播放完成/失败/取消统一收尾；只释放自己的轨道） */
        fun release()
    }
}

/**
 * WAV 播放会话编排：
 * - 从 [WavCodec.PcmSpec.dataOffset]（非固定 44）分块消费 data 区；
 * - 数据全部写入后**挂起等待驱动侧播放完成回调**才返回——write 返回只
 *   代表入队，AudioTrack 的 marker（播放头到达终点）才是完成信号；
 * - **取消状态属于单次播放尝试**（[Attempt]）：新播放顶掉旧播放时取消
 *   旧尝试自己的标志与信号并 pause 旧尝试自己的轨道快照（旧音频在旧协程
 *   退出前立即静音，不与新播报重叠），绝不重置它——旧会话稳定以取消收尾，
 *   新会话继续等待自己的 marker；资源释放走各次播放自己的 finally，不忙等。
 *
 * 线程契约：[play] 的驱动调用（start/write/release）与 [Handle.pause]
 * 是阻塞操作，调用方负责在后台 dispatcher 执行（生产侧由
 * [AndroidWavPlaybackEngine] 统一经注入的 IO dispatcher 包裹）；
 * [requestStop] 只做内存写与 Deferred 取消，可在任意线程立即调用；
 * [current] 与各 [Attempt] 自己的轨道快照均 @Volatile，跨线程可见。
 */
internal class WavPlaybackSession(private val driver: WavPlaybackDriver) {

    /**
     * 单次播放尝试的私有状态：取消标志 + 完成信号 + 自己的轨道快照
     * （新旧尝试互不共享——取消与 pause 配对锁定在同一尝试上）。
     */
    private class Attempt {
        val signal = CompletableDeferred<Unit>()

        @Volatile
        var stopRequested = false
            private set

        /** 本尝试的轨道快照（play 在 start 成功后写入；requestStop 读取同一快照） */
        @Volatile
        var handle: WavPlaybackDriver.Handle? = null

        /** 取消：置标志并唤醒等待者（取消语义，绝不当作已播完） */
        fun requestStop() {
            stopRequested = true
            signal.cancel()
        }

        /** 驱动侧 marker 到达终点：未被取消才完成（迟到的停止不复活播放） */
        fun complete() {
            if (!stopRequested) signal.complete(Unit)
        }
    }

    @Volatile
    private var current: Attempt? = null

    suspend fun play(wav: ByteArray) {
        current?.let { superseded ->
            superseded.requestStop() // 顶掉在途尝试（若有）：取消属于旧尝试，不波及新尝试
            superseded.handle?.pause() // 旧轨道立即静音：不等旧协程退出才停（与新播报重叠的窗口）
        }
        val spec = WavCodec.pcmSpec(wav) ?: throw AudioEngineException("语音数据无效，无法播放")
        val attempt = Attempt()
        current = attempt
        var handle: WavPlaybackDriver.Handle? = null
        try {
            handle = driver.start(spec) { attempt.complete() }
            attempt.handle = handle
            var offset = spec.dataOffset
            val end = spec.dataEnd
            while (offset < end) {
                if (attempt.stopRequested) break // 已被顶掉/停止：不再写已暂停的轨道
                val length = minOf(WRITE_CHUNK_BYTES, end - offset)
                handle.write(wav, offset, length)
                offset += length
            }
            // 写完 ≠ 播完：挂起至驱动侧播放头到达终点（或被取消唤醒）
            attempt.signal.await()
        } finally {
            if (current === attempt) current = null
            handle?.release()
        }
    }

    /**
     * 立即停止当前播放尝试（内存写与 Deferred 取消，任意线程可调）；
     * **只读一次 [current]**：取消的尝试与返回的轨道快照属于同一个
     * [Attempt]（各尝试私有自己的 handle，不存在「取消新尝试却返回旧
     * 轨道」的错配）；可能返回 null——无在途播放、或尝试已创建但驱动
     * 尚未启动（此时取消已生效，play 侧首轮写前即退出，轨道由自己的
     * finally 释放，fail-closed）。
     */
    fun requestStop(): WavPlaybackDriver.Handle? {
        val attempt = current ?: return null
        attempt.requestStop()
        return attempt.handle
    }

    /** 外部停止：立即唤醒 + 同步暂停驱动（等价 [requestStop] 返回值 pause；测试与会话级使用） */
    fun stop() {
        requestStop()?.pause()
    }

    private companion object {
        const val WRITE_CHUNK_BYTES = 8192
    }
}

/** 生产实现：AudioRecord 采集 → [WavCodec] 封 WAV */
class AndroidWavCaptureEngine(
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : AudioCaptureEngine {

    @Volatile
    private var recorder: AudioRecord? = null

    @Volatile
    private var collecting = false

    private val buffer = ArrayList<ByteArray>()

    override val isActive: Boolean get() = collecting

    @SuppressLint("MissingPermission") // 调用方（VoiceScreen）先请求 RECORD_AUDIO；未授权不进入本路径
    override suspend fun start() = withContext(ioDispatcher) {
        if (collecting) return@withContext
        val minBuffer = AudioRecord.getMinBufferSize(
            WavCodec.SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuffer <= 0) throw AudioEngineException("录音设备不可用，请改用文字作答")
        val record = try {
            AudioRecord(
                MediaRecorder.AudioSource.MIC,
                WavCodec.SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                minBuffer * 2,
            )
        } catch (cause: IllegalArgumentException) {
            throw AudioEngineException("录音设备不可用，请改用文字作答")
        } catch (cause: UnsupportedOperationException) {
            throw AudioEngineException("录音设备不可用，请改用文字作答")
        }
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            throw AudioEngineException("录音设备不可用，请改用文字作答")
        }
        synchronized(buffer) { buffer.clear() }
        record.startRecording()
        recorder = record
        collecting = true
        val reader = Thread {
            val chunk = ByteArray(minBuffer)
            while (collecting) {
                val read = record.read(chunk, 0, chunk.size)
                if (read > 0) synchronized(buffer) { buffer.add(chunk.copyOf(read)) }
            }
        }
        reader.isDaemon = true
        reader.start()
    }

    override suspend fun stop(): ByteArray = withContext(ioDispatcher) {
        val record = recorder ?: throw AudioEngineException("尚未开始录音")
        collecting = false
        try {
            val pcm = synchronized(buffer) {
                val total = buffer.sumOf { it.size }
                val merged = ByteArray(total)
                var offset = 0
                buffer.forEach { chunk ->
                    System.arraycopy(chunk, 0, merged, offset, chunk.size)
                    offset += chunk.size
                }
                merged
            }
            if (pcm.isEmpty()) throw AudioEngineException("没有录到声音，请重试")
            WavCodec.wrapPcm16(pcm)
        } finally {
            record.stop()
            record.release()
            recorder = null
        }
    }
}

/** 生产实现：AudioTrack 桥接驱动——marker（播放头到达终点）即完成回调 */
internal class AndroidAudioTrackDriver : WavPlaybackDriver {

    override fun start(spec: WavCodec.PcmSpec, onPlaybackFinished: () -> Unit): WavPlaybackDriver.Handle {
        val track = buildTrack(spec)
        if (spec.totalFrames <= 0) {
            // 无数据帧：没有播放过程，立即完成（仍按完成语义而非失败）
            runCatching {
                track.release()
            }
            onPlaybackFinished()
            return ReleasedHandle
        }
        // marker 在播放头到达终点帧时触发一次——write 返回只代表入队，
        // 完成以播放头为准（M12-03 播报链核心语义）
        track.setPlaybackPositionUpdateListener(
            object : AudioTrack.OnPlaybackPositionUpdateListener {
                override fun onMarkerReached(track: AudioTrack?) {
                    onPlaybackFinished()
                }

                override fun onPeriodicNotification(track: AudioTrack?) = Unit
            },
        )
        track.setNotificationMarkerPosition(spec.totalFrames)
        track.play()
        return TrackHandle(track)
    }

    private object ReleasedHandle : WavPlaybackDriver.Handle {
        override fun write(pcm: ByteArray, offset: Int, length: Int) = Unit

        override fun pause() = Unit

        override fun release() = Unit
    }

    private class TrackHandle(private val track: AudioTrack) : WavPlaybackDriver.Handle {
        override fun write(pcm: ByteArray, offset: Int, length: Int) {
            var written = 0
            while (written < length) {
                val result = track.write(pcm, offset + written, length - written)
                if (result < 0) throw AudioEngineException("播放失败，可点击重试")
                written += result
            }
        }

        override fun pause() {
            runCatching {
                track.pause()
                track.flush()
            }
        }

        override fun release() {
            runCatching {
                track.stop()
                track.release()
            }
        }
    }

    private fun buildTrack(spec: WavCodec.PcmSpec): AudioTrack {
        // 播放参数以 WAV header 为准（服务端本地 TTS 为 8kHz、云端可能 16kHz——硬编码会变速变调）
        val channelMask = if (spec.channels == 1) AudioFormat.CHANNEL_OUT_MONO else AudioFormat.CHANNEL_OUT_STEREO
        val minBuffer = AudioTrack.getMinBufferSize(
            spec.sampleRate,
            channelMask,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuffer <= 0) throw AudioEngineException("播放失败，可点击重试")
        return try {
            AudioTrack(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
                AudioFormat.Builder()
                    .setSampleRate(spec.sampleRate)
                    .setChannelMask(channelMask)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .build(),
                minBuffer * 2,
                AudioTrack.MODE_STREAM,
                AudioManager.AUDIO_SESSION_ID_GENERATE,
            )
        } catch (cause: IllegalArgumentException) {
            throw AudioEngineException("播放失败，可点击重试")
        } catch (cause: UnsupportedOperationException) {
            throw AudioEngineException("播放失败，可点击重试")
        }
    }
}

/**
 * 生产播放引擎：WavPlaybackSession 编排 + AudioTrack 驱动。
 *
 * 调度边界：AudioTrack 创建、PCM 写入、等待完成与 release 全部在注入的
 * [ioDispatcher]（默认 Dispatchers.IO）上执行——ViewModel 的 Main 调用
 * [play] 不会阻塞主线程；[stop] 的立即部分（取消 + 唤醒）同步执行，
 * 停止时刻的轨道快照交 IO pause/flush。停止绝不当作播放完成。
 */
class AndroidWavPlaybackEngine internal constructor(
    private val ioDispatcher: CoroutineDispatcher,
    driver: WavPlaybackDriver,
) : AudioPlaybackEngine {

    constructor() : this(Dispatchers.IO, AndroidAudioTrackDriver())

    private val session = WavPlaybackSession(driver)

    private val ioScope = CoroutineScope(SupervisorJob() + ioDispatcher)

    override suspend fun play(wav: ByteArray) = withContext(ioDispatcher) {
        session.play(wav)
    }

    override fun stop() {
        // 立即取消并唤醒挂起的播放（内存写，任意线程安全）；
        // 对停止时刻的轨道快照做 pause/flush（阻塞调用移出 Main）
        val handle = session.requestStop()
        if (handle != null) ioScope.launch { handle.pause() }
    }
}
