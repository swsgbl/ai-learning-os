package com.ailearningos.app.voice

import java.util.Collections
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.DelicateCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.launch
import kotlinx.coroutines.newSingleThreadContext
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * WAV 播放会话编排（M12-03 播报链核心语义）：
 * - **写完 ≠ 播完**：数据全部写入后，play 必须继续挂起，直到驱动侧
 *   播放完成回调（生产=AudioTrack marker：播放头到达终点帧）才返回——
 *   这是不让 question_read/options_read 过早推进服务端 FSM 的推进条件；
 * - 消费从 spec.dataOffset 开始（LIST/fact 等前置 chunk 使 data 不在 44）；
 * - 停止立即唤醒挂起的播放（取消语义，不当作已播完）并释放自己的轨道；
 * - 新播放顶掉在途会话；无效 WAV 未启动驱动即拒绝。
 */
class WavPlaybackSessionTest {

    private class FakeDriver : WavPlaybackDriver {
        val startedSpecs = mutableListOf<WavCodec.PcmSpec>()

        /** 每次写入的闭区间记录（offset..offset+length） */
        val writes = mutableListOf<IntRange>()

        /** 各阻塞操作实际执行的线程（调度边界断言用） */
        val startThreads = Collections.synchronizedList(mutableListOf<Thread>())

        val writeThreads = Collections.synchronizedList(mutableListOf<Thread>())

        val releaseThreads = Collections.synchronizedList(mutableListOf<Thread>())

        val pauseThreads = Collections.synchronizedList(mutableListOf<Thread>())

        var startError: Exception? = null

        var writeError: Exception? = null

        /** 首块写入时的重入钩子（测试停止时机用） */
        var onFirstWrite: (() -> Unit)? = null

        /**
         * 第一个 handle 的写入门（真实线程交错测试用）：首次 write 进入时
         * [FirstWriteGate.entered] 倒计数并阻塞，直到 [FirstWriteGate.open]。
         */
        var firstWriteGate: FirstWriteGate? = null

        private var finishCallback: (() -> Unit)? = null

        val handles = CopyOnWriteArrayList<FakeHandle>()

        override fun start(spec: WavCodec.PcmSpec, onPlaybackFinished: () -> Unit): WavPlaybackDriver.Handle {
            startError?.let { throw it }
            startThreads.add(Thread.currentThread())
            synchronized(startedSpecs) { startedSpecs.add(spec) }
            finishCallback = onPlaybackFinished
            val handle = FakeHandle(isFirst = handles.isEmpty())
            synchronized(this) {
                handles.add(handle)
                @Suppress("PLATFORM_CLASS_MAPPED_TO_KOTLIN")
                (this as Object).notifyAll()
            }
            return handle
        }

        /** 模拟驱动侧播放头到达终点（AudioTrack marker；只回调最后一次 start 注册的） */
        fun finishPlayback() {
            finishCallback?.invoke()
        }

        inner class FakeHandle(private val isFirst: Boolean) : WavPlaybackDriver.Handle {
            var pauseCalls = 0
                private set

            var releaseCalls = 0
                private set

            /** 本 handle 的写入次数（跨线程读，synchronized 写） */
            var writeCount = 0
                private set

            /** 首块写入完成信号（真实线程测试同步用） */
            val firstWriteDone = CountDownLatch(1)

            override fun write(pcm: ByteArray, offset: Int, length: Int) {
                writeThreads.add(Thread.currentThread())
                if (isFirst && writeCount == 0 && writes.isEmpty()) onFirstWrite?.invoke()
                writeError?.let { throw it }
                if (isFirst) {
                    firstWriteGate?.let { gate ->
                        gate.entered.countDown()
                        gate.awaitRelease()
                    }
                }
                synchronized(writes) { writes.add(offset until offset + length) }
                writeCount++
                firstWriteDone.countDown()
            }

            override fun pause() {
                pauseThreads.add(Thread.currentThread())
                pauseCalls++
            }

            override fun release() {
                releaseThreads.add(Thread.currentThread())
                releaseCalls++
            }
        }
    }

    /** 第一个 handle 首次 write 的真实线程门（禁止 sleep 竞运） */
    private class FirstWriteGate {
        val entered = CountDownLatch(1)

        private val open = CountDownLatch(1)

        fun awaitRelease() {
            open.await()
        }

        fun open() {
            open.countDown()
        }
    }

    @Test
    fun `writing all data does not complete playback until driver signals`() = runTest {
        val driver = FakeDriver()
        val session = WavPlaybackSession(driver)
        val job = launch { session.play(WavCodec.wrapPcm16(ByteArray(100))) }
        advanceUntilIdle() // 数据已全部写入（fake 无挂起）

        assertTrue("数据写完后仍须挂起等待播放完成（写完 ≠ 播完）", job.isActive)
        assertTrue("数据应已写入驱动", driver.writes.isNotEmpty())

        driver.finishPlayback() // 播放头到达终点
        advanceUntilIdle()
        assertTrue("播放完成后 play 才返回", job.isCompleted)
        assertTrue(job.isCompleted && !job.isCancelled)
        assertEquals(1, driver.handles.single().releaseCalls)
    }

    @Test
    fun `playback consumes from data offset not fixed forty four`() = runTest {
        val wav = wavWithLeadingListChunk(pcmBytes = 60, chunkPad = 0) // dataOffset != 44
        val spec = WavCodec.pcmSpec(wav)!!
        val driver = FakeDriver()
        val session = WavPlaybackSession(driver)

        val job = launch { session.play(wav) }
        advanceUntilIdle()
        driver.finishPlayback()
        advanceUntilIdle()
        assertTrue(job.isCompleted && !job.isCancelled)

        assertEquals(spec.dataOffset, driver.startedSpecs.single().dataOffset)
        val written = driver.writes.reduce { acc, range -> acc.first..acc.last + (range.last - range.first + 1) }
        assertEquals("写入区间必须从 dataOffset 开始", spec.dataOffset, written.first)
        assertEquals("写入总量必须等于 data 区字节数", spec.dataBytes, written.last - written.first + 1)
    }

    @Test
    fun `stop while awaiting completion cancels playback without faking completion`() = runTest {
        val driver = FakeDriver()
        val session = WavPlaybackSession(driver)
        val job = launch { session.play(WavCodec.wrapPcm16(ByteArray(100))) }
        advanceUntilIdle()
        assertTrue(job.isActive) // 挂起等待播放完成

        session.stop()

        job.join()
        assertTrue("停止必须结束播放（取消语义）", job.isCancelled)
        assertEquals("停止必须立即暂停驱动", 1, driver.handles.single().pauseCalls)
        assertEquals("取消路径也必须释放轨道", 1, driver.handles.single().releaseCalls)
    }

    @Test
    fun `stop during write loop aborts remaining writes`() = runTest {
        val driver = FakeDriver()
        val session = WavPlaybackSession(driver)
        // 大数据分多块写入；首块写入时重入停止
        driver.onFirstWrite = { session.stop() }

        val job = launch { session.play(WavCodec.wrapPcm16(ByteArray(WRITE_CHUNK_BYTES * 3))) }
        advanceUntilIdle()
        job.join()

        assertTrue(job.isCancelled)
        assertTrue("停止后不得继续写剩余块", driver.writes.size < 3)
        assertEquals(1, driver.handles.single().pauseCalls)
        assertEquals(1, driver.handles.single().releaseCalls)
    }

    @Test
    fun `new playback supersedes in-flight session and old handle released`() = runTest {
        val driver = FakeDriver()
        val session = WavPlaybackSession(driver)
        val first = launch { session.play(WavCodec.wrapPcm16(ByteArray(100))) }
        advanceUntilIdle()
        assertTrue(first.isActive)

        val second = launch { session.play(WavCodec.wrapPcm16(ByteArray(80))) }
        advanceUntilIdle()

        assertTrue("旧会话必须被顶掉（取消收尾）", first.isCancelled)
        assertTrue("新会话挂起等待自己的播放完成", second.isActive)
        assertEquals("顶替路径旧轨道必须被 pause 恰好一次（立即静音）", 1, driver.handles[0].pauseCalls)
        assertEquals("新轨道不得被顶替路径误 pause", 0, driver.handles[1].pauseCalls)
        assertEquals("旧轨道必须释放", 1, driver.handles[0].releaseCalls)
        assertEquals("新轨道未完成不得释放", 0, driver.handles[1].releaseCalls)

        driver.finishPlayback() // 只回调最后一次 start 注册的完成信号
        advanceUntilIdle()
        assertTrue(second.isCompleted && !second.isCancelled)
        assertEquals(1, driver.handles[1].releaseCalls)
    }

    /**
     * 真实线程交错（虚拟时间顺序测不到的窗口）：旧 play 阻塞在 write 时
     * 新 play 启动——取消状态属于单次尝试，新播放不得重置旧播放的取消
     * 标志；释放旧 write 后旧会话必须取消收尾，新会话继续等待自己的
     * marker 完成。全程 wait/notify 条件等待与 latch/join 同步，禁止 sleep 竞运。
     */
    @OptIn(DelicateCoroutinesApi::class)
    @Test
    fun `superseding play cancels old attempt blocked in write and new completes by own marker`() {
        val driver = FakeDriver()
        val session = WavPlaybackSession(driver)
        val gate = FirstWriteGate()
        driver.firstWriteGate = gate
        val wav = WavCodec.wrapPcm16(ByteArray(100))

        val oldContext = newSingleThreadContext("voice-test-old")
        val newContext = newSingleThreadContext("voice-test-new")
        try {
            val oldJob = CoroutineScope(SupervisorJob() + oldContext).launch { session.play(wav) }
            assertTrue("旧 play 应已阻塞在 write", gate.entered.await(2, TimeUnit.SECONDS))

            // 新播放（真实线程交错）：顶掉旧尝试后自行写入并挂起等待自己的 marker
            val newJob = CoroutineScope(SupervisorJob() + newContext).launch { session.play(wav) }
            assertTrue("新会话应已启动轨道", awaitHandleCount(driver, 2))
            assertTrue(
                "新会话应已完成写入并挂起等待完成",
                driver.handles[1].firstWriteDone.await(2, TimeUnit.SECONDS),
            )

            gate.open() // 释放旧 write：旧尝试已被取消，必须停止写入并取消收尾

            runBlocking { withTimeout(2_000) { oldJob.join() } }
            assertTrue("旧会话必须以取消结束（新播放不得重置其取消标志）", oldJob.isCancelled)
            assertEquals("顶替路径旧轨道必须被 pause 恰好一次（旧协程退出前立即静音）", 1, driver.handles[0].pauseCalls)
            assertEquals("旧轨道必须释放", 1, driver.handles[0].releaseCalls)
            assertEquals("旧会话停止后不得继续写剩余数据", 1, driver.handles[0].writeCount)

            assertTrue("新会话仍在等待自己的播放完成", newJob.isActive)
            assertEquals("新轨道不得被顶替路径误 pause", 0, driver.handles[1].pauseCalls)
            assertEquals("新轨道未完成不得释放", 0, driver.handles[1].releaseCalls)

            driver.finishPlayback() // 只回调最后一次 start 注册的完成信号（新尝试）
            runBlocking { withTimeout(2_000) { newJob.join() } }
            assertTrue("新会话由自己的 marker 完成（不被旧取消波及）", newJob.isCompleted && !newJob.isCancelled)
            assertEquals(1, driver.handles[1].releaseCalls)
        } finally {
            oldContext.close()
            newContext.close()
        }
    }

    /**
     * 真实线程交错：取消与 pause 必须配对在**同一个**播放尝试上。
     * 场景——旧 play 阻塞在 write、新 play 已接管（current 已确定是新尝试）时
     * 外部 stop 介入：必须取消新尝试并 pause 新轨道，绝不「取消新尝试却 pause
     * 已被顶替的旧轨道」（旧 handle 的 pause 只来自顶替路径，恰好一次）。
     * current 在新 play 进入 driver.start 前即同步写入，此处读取无交错窗口。
     */
    @OptIn(DelicateCoroutinesApi::class)
    @Test
    fun `external stop after supersede cancels and pauses the same new attempt as one pair`() {
        val driver = FakeDriver()
        val session = WavPlaybackSession(driver)
        val gate = FirstWriteGate()
        driver.firstWriteGate = gate
        val wav = WavCodec.wrapPcm16(ByteArray(100))

        val oldContext = newSingleThreadContext("voice-test-pair-old")
        val newContext = newSingleThreadContext("voice-test-pair-new")
        try {
            val oldJob = CoroutineScope(SupervisorJob() + oldContext).launch { session.play(wav) }
            assertTrue("旧 play 应已阻塞在 write", gate.entered.await(2, TimeUnit.SECONDS))

            // 新播放接管：current 已确定是新尝试，新轨道已写入并挂起等待 marker
            val newJob = CoroutineScope(SupervisorJob() + newContext).launch { session.play(wav) }
            assertTrue("新会话应已启动轨道", awaitHandleCount(driver, 2))
            assertTrue(
                "新会话应已完成写入并挂起等待完成",
                driver.handles[1].firstWriteDone.await(2, TimeUnit.SECONDS),
            )

            session.stop() // 外部停止：取消的尝试与 pause 的轨道必须是同一个新尝试

            runBlocking { withTimeout(2_000) { newJob.join() } }
            assertTrue("外部停止必须取消当前（新）尝试", newJob.isCancelled)
            assertEquals("新轨道必须被外部停止 pause 恰好一次", 1, driver.handles[1].pauseCalls)
            assertEquals("新会话取消收尾必须释放自己的轨道", 1, driver.handles[1].releaseCalls)

            gate.open() // 释放旧 write：旧尝试早已在顶替时取消
            runBlocking { withTimeout(2_000) { oldJob.join() } }
            assertTrue("旧会话必须以取消结束", oldJob.isCancelled)
            assertEquals("旧轨道的 pause 只来自顶替路径（外部停止不得错配 pause 已被顶替的旧轨道）", 1, driver.handles[0].pauseCalls)
            assertEquals("旧轨道必须释放", 1, driver.handles[0].releaseCalls)
        } finally {
            oldContext.close()
            newContext.close()
        }
    }

    /** wait/notify 条件等待：驱动的 handle 数达到 [count]（start 的 notifyAll 唤醒） */
    private fun awaitHandleCount(driver: FakeDriver, count: Int, timeoutMillis: Long = 2_000): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMillis
        @Suppress("PLATFORM_CLASS_MAPPED_TO_KOTLIN")
        return synchronized(driver) {
            while (driver.handles.size < count) {
                val remaining = deadline - System.currentTimeMillis()
                if (remaining <= 0) return false
                @Suppress("PLATFORM_CLASS_MAPPED_TO_KOTLIN")
                (driver as Object).wait(remaining)
            }
            true
        }
    }

    @Test
    fun `invalid wav rejected without starting driver`() = runTest {
        val driver = FakeDriver()
        val session = WavPlaybackSession(driver)
        val error = runCatching { session.play("not a wav".toByteArray()) }.exceptionOrNull()

        assertTrue("非 WAV 必须以 AudioEngineException 拒绝", error is AudioEngineException)
        assertTrue("驱动不得被启动", driver.startedSpecs.isEmpty())
        assertTrue(driver.handles.isEmpty())
    }

    // ---------- 调度边界：阻塞的驱动操作绝不在调用方（Main）dispatcher 执行 ----------

    @Test
    fun `engine runs blocking driver operations on injected io dispatcher`() = runTest {
        val ioExecutor = Executors.newSingleThreadExecutor { runnable ->
            Thread(runnable, "voice-io-test").also { it.isDaemon = true }
        }
        val callerThread = Thread.currentThread()
        try {
            val driver = FakeDriver()
            val engine = AndroidWavPlaybackEngine(
                ioDispatcher = ioExecutor.asCoroutineDispatcher(),
                driver = driver,
            )

            // 在测试的 Main（runTest 调度器）上调用 play——引擎必须切到注入的 IO
            val job = launch { engine.play(WavCodec.wrapPcm16(ByteArray(100))) }
            advanceUntilIdle() // 推进 Main 部分：withContext 切到 IO 线程后异步执行
            assertTrue("数据应已写入（在 IO 线程）", awaitCondition { driver.writes.isNotEmpty() })
            assertTrue("写完 ≠ 播完：仍在等待完成", job.isActive)

            driver.finishPlayback()
            job.join()
            assertTrue(job.isCompleted && !job.isCancelled)

            val ioThread = driver.startThreads.single()
            assertNotSame("start 必须在后台线程（不在 Main）", callerThread, ioThread)
            assertTrue("write 必须全部在 IO 线程", driver.writeThreads.all { it === ioThread })
            assertTrue("release 必须在 IO 线程", driver.releaseThreads.all { it === ioThread })
            assertEquals(1, driver.handles.single().releaseCalls)
        } finally {
            ioExecutor.shutdown()
        }
    }

    @Test
    fun `engine stop during playback cancels without completing and releases off main`() = runTest {
        val ioExecutor = Executors.newSingleThreadExecutor { runnable ->
            Thread(runnable, "voice-io-stop-test").also { it.isDaemon = true }
        }
        val callerThread = Thread.currentThread()
        try {
            val driver = FakeDriver()
            val engine = AndroidWavPlaybackEngine(
                ioDispatcher = ioExecutor.asCoroutineDispatcher(),
                driver = driver,
            )

            val job = launch { engine.play(WavCodec.wrapPcm16(ByteArray(100))) }
            advanceUntilIdle() // 推进 Main 部分：withContext 切到 IO 线程后异步执行
            assertTrue(awaitCondition { driver.writes.isNotEmpty() })

            engine.stop() // 从 Main 调用：立即唤醒（取消语义），pause/flush 调度到 IO

            job.join()
            assertTrue("停止必须立即唤醒并以取消结束（不当作已播完）", job.isCancelled)
            val ioThread = driver.startThreads.single()
            assertNotSame(callerThread, ioThread)
            // stop 时刻的轨道快照在 IO 上 pause——绝不在调用方 Main/test 线程
            assertTrue("驱动 pause 必须到达", awaitCondition { driver.pauseThreads.isNotEmpty() })
            assertTrue(
                "pause 不得在调用方线程执行：${driver.pauseThreads}",
                driver.pauseThreads.none { it === callerThread },
            )
            assertTrue("pause 必须在注入的 IO 线程执行", driver.pauseThreads.all { it === ioThread })
            assertTrue("取消路径的 release 也必须在 IO 线程", driver.releaseThreads.all { it === ioThread })
            assertEquals("停止时刻轨道必须被 pause 恰好一次", 1, driver.handles.single().pauseCalls)
            assertEquals("取消路径释放恰好一次", 1, driver.handles.single().releaseCalls)
        } finally {
            ioExecutor.shutdown()
        }
    }

    /** 真实线程同步的短轮询（调度边界测试用；不进生产代码） */
    private fun awaitCondition(timeoutMillis: Long = 2000, condition: () -> Boolean): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMillis
        while (System.currentTimeMillis() < deadline) {
            if (condition()) return true
            Thread.sleep(10)
        }
        return condition()
    }

    // ---------- 测试用 WAV 构造（data 不在 44 的合法 RIFF 形态） ----------

    private fun wavWithLeadingListChunk(pcmBytes: Int, chunkPad: Int): ByteArray {
        val pcm = ByteArray(pcmBytes)
        val listBody = ByteArray(10)
        val out = java.io.ByteArrayOutputStream()
        fun intLE(value: Int) {
            out.write(value and 0xFF)
            out.write((value ushr 8) and 0xFF)
            out.write((value ushr 16) and 0xFF)
            out.write((value ushr 24) and 0xFF)
        }
        out.write("RIFF".toByteArray())
        intLE(4 + (8 + listBody.size) + (8 + 16) + (8 + pcm.size))
        out.write("WAVE".toByteArray())
        out.write("LIST".toByteArray())
        intLE(listBody.size)
        out.write(listBody)
        val plain = WavCodec.wrapPcm16(ByteArray(0))
        out.write(plain.copyOfRange(12, 36)) // 标准 fmt chunk
        out.write("data".toByteArray())
        intLE(pcm.size)
        out.write(pcm)
        return out.toByteArray()
    }

    private companion object {
        /** 与 WavPlaybackSession 的写分块一致（8192），用于构造多块数据 */
        const val WRITE_CHUNK_BYTES = 8192
    }
}
