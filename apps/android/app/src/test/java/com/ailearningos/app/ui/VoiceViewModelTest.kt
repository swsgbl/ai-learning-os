package com.ailearningos.app.ui

import androidx.lifecycle.viewModelScope
import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.local.VoiceResumeSlot
import com.ailearningos.app.data.model.VoiceIntentResult
import com.ailearningos.app.testutil.FakeAudioCaptureEngine
import com.ailearningos.app.testutil.FakeAudioPlaybackEngine
import com.ailearningos.app.testutil.FakeExamGateway
import com.ailearningos.app.testutil.FakeExamResumeStore
import com.ailearningos.app.testutil.FakeVoiceGateway
import com.ailearningos.app.testutil.FakeVoiceResumeStore
import com.ailearningos.app.testutil.MainDispatcherRule
import com.ailearningos.app.testutil.voiceSessionFixture
import com.ailearningos.app.ui.voice.VoiceEntry
import com.ailearningos.app.ui.voice.VoiceLive
import com.ailearningos.app.ui.voice.VoiceUiState
import com.ailearningos.app.ui.voice.VoiceViewModel
import com.ailearningos.app.voice.AudioEngineException
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/**
 * 语音陪练状态机（M12-03）：开新/恢复、读题播报链、命令/意图作答、
 * 澄清、下题循环、全卷报告、409 对齐、TTS 失败降级、权限降级、trace 上报。
 *
 * 调度隔离（防挂死约定，与 ExamViewModelTest 同款）：
 * - runTest 与 MainDispatcherRule 共享同一 testScheduler；
 * - VM 的播报链为嵌套 launch，fake 网关不真正挂起，每个用例经
 *   [runVoiceTest] 在测试体内 finally 先 cancel 全部 viewModelScope 再收尾。
 */
class VoiceViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private lateinit var gateway: FakeVoiceGateway
    private lateinit var examGateway: FakeExamGateway
    private lateinit var voiceResumeStore: FakeVoiceResumeStore
    private lateinit var capture: FakeAudioCaptureEngine
    private lateinit var playback: FakeAudioPlaybackEngine

    /** fake 时钟：每次读取自增，保证耗时 span 恒为正（trace 可断言） */
    private var nowMillis = 1_000_000L

    private val created = mutableListOf<VoiceViewModel>()

    @Before
    fun setUp() {
        gateway = FakeVoiceGateway()
        examGateway = FakeExamGateway()
        voiceResumeStore = FakeVoiceResumeStore()
        capture = FakeAudioCaptureEngine()
        playback = FakeAudioPlaybackEngine()
    }

    private fun runVoiceTest(block: suspend TestScope.() -> Unit) = runTest(
        mainDispatcherRule.testDispatcher.scheduler,
    ) {
        try {
            block()
        } finally {
            created.forEach { it.viewModelScope.cancel() }
        }
    }

    private fun newViewModel(entry: VoiceEntry): VoiceViewModel {
        val model = VoiceViewModel(
            voice = gateway,
            exam = examGateway,
            resumeStore = voiceResumeStore,
            capture = capture,
            playback = playback,
            entry = entry,
            clock = { ++nowMillis },
        )
        created.add(model)
        return model
    }

    private fun live(model: VoiceViewModel): VoiceLive {
        val state = model.state.value
        assertTrue("应处于 Live 态，实际 $state", state is VoiceUiState.Live)
        return (state as VoiceUiState.Live).value
    }

    /** 同步推进到 WAITING_ANSWER（start_reading → 播题干 → 读选项播完） */
    private fun advanceToWaiting(model: VoiceViewModel) {
        model.sendCommand("start_reading")
        assertEquals("WAITING_ANSWER", live(model).status)
    }

    // ---------- 开新（Start 入口） ----------

    @Test
    fun `start without slot opens exam creates session and saves resume slot`() = runVoiceTest {
        val model = newViewModel(VoiceEntry.Start("functions-basics"))

        val value = live(model)
        assertEquals("SESSION_READY", value.status)
        assertEquals("exam-fixture-0001", value.examId)
        assertEquals("q1", value.question?.id)
        assertEquals(2, value.questionTotal)
        assertEquals(1, examGateway.startExamCalls.size)
        assertEquals(listOf("exam-fixture-0001"), gateway.createdSessions)
        assertEquals(
            VoiceResumeSlot(sessionId = value.sessionId, examId = "exam-fixture-0001"),
            voiceResumeStore.load(),
        )
    }

    @Test
    fun `start with resumable slot continues instead of opening new exam`() = runVoiceTest {
        gateway.sessions["vs-old"] = voiceSessionFixture(
            sessionId = "vs-old",
            status = "WAITING_ANSWER",
        ) to linkedMapOf()
        voiceResumeStore.seed(VoiceResumeSlot("vs-old", "exam-fixture-0001"))

        val model = newViewModel(VoiceEntry.Start("functions-basics"))

        val value = live(model)
        assertEquals("vs-old", value.sessionId)
        assertTrue("恢复进入应显示恢复提示", value.restored)
        assertTrue("不应开新考", examGateway.startExamCalls.isEmpty())
        assertTrue("不应建新语音会话", gateway.createdSessions.isEmpty())
        assertEquals(VoiceResumeSlot("vs-old", "exam-fixture-0001"), voiceResumeStore.load())
    }

    @Test
    fun `start with missing slot session clears and opens new`() = runVoiceTest {
        voiceResumeStore.seed(VoiceResumeSlot("vs-gone", "exam-fixture-0001"))

        val model = newViewModel(VoiceEntry.Start("functions-basics"))

        assertEquals("SESSION_READY", live(model).status)
        assertEquals(1, voiceResumeStore.clearCount)
        assertEquals(1, examGateway.startExamCalls.size)
    }

    @Test
    fun `start with terminal slot session clears and opens new`() = runVoiceTest {
        gateway.sessions["vs-done"] = voiceSessionFixture(sessionId = "vs-done", status = "REPORT_READY") to linkedMapOf()
        voiceResumeStore.seed(VoiceResumeSlot("vs-done", "exam-fixture-0001"))

        val model = newViewModel(VoiceEntry.Start("functions-basics"))

        assertEquals("SESSION_READY", live(model).status)
        assertEquals(1, voiceResumeStore.clearCount)
        assertEquals(1, examGateway.startExamCalls.size)
    }

    @Test
    fun `start with resume network failure reports unavailable without new exam`() = runVoiceTest {
        voiceResumeStore.seed(VoiceResumeSlot("vs-x", "exam-fixture-0001"))
        gateway.failNextResume = AppError(AppErrorKind.NETWORK)

        val model = newViewModel(VoiceEntry.Start("functions-basics"))

        val state = model.state.value
        assertTrue("网络失败应如实报错，实际 $state", state is VoiceUiState.Unavailable)
        assertTrue("不得盲目开新考", examGateway.startExamCalls.isEmpty())
    }

    // ---------- 恢复（Resume 入口） ----------

    @Test
    fun `resume entry adopts server state with restored banner`() = runVoiceTest {
        gateway.sessions["vs-r"] = voiceSessionFixture(sessionId = "vs-r", status = "ANSWER_COMMITTED") to
            linkedMapOf("q1" to "B")

        val model = newViewModel(VoiceEntry.Resume("vs-r"))

        val value = live(model)
        assertEquals("ANSWER_COMMITTED", value.status)
        assertEquals("B", value.committedAnswer)
        assertTrue(value.restored)
        assertEquals("q1", value.question?.id)
    }

    @Test
    fun `resume entry terminal session reports unavailable and clears slot`() = runVoiceTest {
        gateway.sessions["vs-done"] = voiceSessionFixture(sessionId = "vs-done", status = "REPORT_READY") to linkedMapOf()
        voiceResumeStore.seed(VoiceResumeSlot("vs-done", "exam-fixture-0001"))

        val model = newViewModel(VoiceEntry.Resume("vs-done"))

        val state = model.state.value
        assertTrue(state is VoiceUiState.Unavailable)
        assertEquals(1, voiceResumeStore.clearCount)
    }

    // ---------- 读题播报链 ----------

    @Test
    fun `start reading announces stem then options then waits for answer`() = runVoiceTest {
        val model = newViewModel(VoiceEntry.Start("functions-basics"))

        model.sendCommand("start_reading")

        val value = live(model)
        assertEquals("WAITING_ANSWER", value.status)
        assertEquals(2, playback.played.size) // 题干 + 选项各一次
        assertTrue(gateway.synthesizeTexts.first().contains("单调递增区间"))
        assertTrue(gateway.synthesizeTexts[1].contains("选项A"))
        assertEquals(listOf("start_reading", "question_read", "options_read"), gateway.commands.map { it.second })
        assertTrue(gateway.traces.any { it.first == "tts" })
    }

    @Test
    fun `restored during announcement replays current question`() = runVoiceTest {
        gateway.sessions["vs-mid"] = voiceSessionFixture(sessionId = "vs-mid", status = "READING_OPTIONS") to linkedMapOf()
        voiceResumeStore.seed(VoiceResumeSlot("vs-mid", "exam-fixture-0001"))

        val model = newViewModel(VoiceEntry.Resume("vs-mid"))

        // 播报期断线重连：凭服务端返回的当前题重播（读选项段）→ 播完进倾听
        assertEquals("WAITING_ANSWER", live(model).status)
        assertTrue(gateway.synthesizeTexts.isNotEmpty())
        assertTrue(gateway.synthesizeTexts.first().contains("选项A"))
    }

    @Test
    fun `choose option during announcement prompts notice without submitting`() = runVoiceTest {
        gateway.synthesizeResult = Result.failure(AppError(AppErrorKind.SERVER)) // 链停在 READING_QUESTION
        val model = newViewModel(VoiceEntry.Start("functions-basics"))
        model.sendCommand("start_reading")
        assertEquals("READING_QUESTION", live(model).status)

        model.chooseOption("B")

        assertEquals("READING_QUESTION", live(model).status)
        assertNotNull(live(model).notice)
        assertTrue(gateway.commands.none { it.second == "answer_proposed" })
    }

    @Test
    fun `tts failure stops chain with retryable error and does not advance`() = runVoiceTest {
        gateway.synthesizeResult = Result.failure(AppError(AppErrorKind.SERVER))
        val model = newViewModel(VoiceEntry.Start("functions-basics"))

        model.sendCommand("start_reading")

        val value = live(model)
        assertEquals("READING_QUESTION", value.status)
        assertNotNull("播报失败必须可见", value.ttsError)
        assertTrue(gateway.commands.none { it.second == "question_read" })

        // 重试播报恢复链路（synthesize 恢复成功）
        gateway.synthesizeResult = Result.success(com.ailearningos.app.voice.WavCodec.wrapPcm16(ByteArray(16)))
        model.retryAnnounce()
        assertEquals("WAITING_ANSWER", live(model).status)
        assertNull(live(model).ttsError)
    }

    @Test
    fun `playback failure shows retryable error and skip path advances manually`() = runVoiceTest {
        playback.playError = IllegalStateException("track died")
        val model = newViewModel(VoiceEntry.Start("functions-basics"))

        model.sendCommand("start_reading")

        assertEquals("READING_QUESTION", live(model).status)
        assertNotNull(live(model).ttsError)

        model.sendCommand("question_read") // 用户手动推进（跳过播报）
        assertEquals("READING_OPTIONS", live(model).status)
    }

    // ---------- 作答 ----------

    @Test
    fun `choose option in waiting submits answer`() = runVoiceTest {
        val model = newViewModel(VoiceEntry.Start("functions-basics"))
        advanceToWaiting(model)

        model.chooseOption("B")

        val value = live(model)
        assertEquals("ANSWER_COMMITTED", value.status)
        val answers = gateway.sessions[value.sessionId]?.second
        assertEquals("B", answers?.get("q1"))
    }

    @Test
    fun `submit transcript applies intent from server`() = runVoiceTest {
        val model = newViewModel(VoiceEntry.Start("functions-basics"))
        advanceToWaiting(model)

        model.setInputText("选 B")
        model.submitTranscript()

        val value = live(model)
        assertEquals("ANSWER_COMMITTED", value.status)
        assertEquals("", value.inputText)
        assertEquals(listOf("选 B"), gateway.intents)
    }

    @Test
    fun `unknown transcript shows notice without state change`() = runVoiceTest {
        val model = newViewModel(VoiceEntry.Start("functions-basics"))
        advanceToWaiting(model)

        model.setInputText("今天天气不错")
        model.submitTranscript()

        val value = live(model)
        assertEquals("WAITING_ANSWER", value.status)
        assertNotNull(value.notice)
    }

    @Test
    fun `clarified intent enters clarifying with question`() = runVoiceTest {
        val model = newViewModel(VoiceEntry.Start("functions-basics"))
        advanceToWaiting(model)
        gateway.intentHandler = { transcript ->
            VoiceIntentResult(
                transcript = transcript,
                intent = "choose",
                letter = null,
                ordinal = 2,
                ambiguous = true,
                fsmCommand = "answer_clarify",
                fsmApplied = true,
                appliedEvent = "answer_clarify",
                session = gateway.command(model.let { live(it).sessionId }, "answer_clarify", "q1", transcript).session,
                clarifiedQuestion = "没有听清，请再说一遍具体选项。",
            )
        }

        model.setInputText("第二个")
        model.submitTranscript()

        val value = live(model)
        assertEquals("CLARIFYING", value.status)
        assertEquals("没有听清，请再说一遍具体选项。", value.clarifiedQuestion)
        assertTrue(gateway.synthesizeTexts.any { it.contains("没有听清") }) // 澄清语自动播报
    }

    // ---------- 下题循环与报告 ----------

    @Test
    fun `commit confirmed then start reading advances index with fresh answer view`() = runVoiceTest {
        val model = newViewModel(VoiceEntry.Start("functions-basics"))
        advanceToWaiting(model)
        model.chooseOption("B")
        assertEquals("ANSWER_COMMITTED", live(model).status)

        model.sendCommand("commit_confirmed")
        assertEquals("NEXT_QUESTION", live(model).status)

        model.sendCommand("start_reading")

        // 新题自动播报链（题干→选项）完成后停在倾听
        val value = live(model)
        assertEquals("WAITING_ANSWER", value.status)
        assertEquals(1, value.questionIndex)
        assertEquals("q2", value.question?.id)
        assertNull("换题后旧答案不展示", value.committedAnswer)
    }

    @Test
    fun `report ready then request report submits exam and shows projection`() = runVoiceTest {
        val model = newViewModel(VoiceEntry.Start("functions-basics"))
        advanceToWaiting(model)
        model.chooseOption("B")
        model.sendCommand("commit_confirmed")
        model.sendCommand("report_ready")

        assertEquals("REPORT_READY", live(model).status)
        model.requestReport()

        val state = model.state.value
        assertTrue("应进入报告态，实际 $state", state is VoiceUiState.Report)
        val report = (state as VoiceUiState.Report).value
        assertTrue(report.spokenText.contains("考试完成"))
        assertEquals(1, examGateway.submitCalls.size) // 判分来源：先提交考试
        assertNull("报告获取后恢复槽必须清空", voiceResumeStore.load())
    }

    @Test
    fun `end command reaches terminal and clears resume slot`() = runVoiceTest {
        val model = newViewModel(VoiceEntry.Start("functions-basics"))
        advanceToWaiting(model)

        model.sendCommand("end")

        assertEquals("REPORT_READY", live(model).status)
        assertEquals(1, voiceResumeStore.clearCount)
    }

    @Test
    fun `request report failure keeps live with retryable error`() = runVoiceTest {
        val model = newViewModel(VoiceEntry.Start("functions-basics"))
        advanceToWaiting(model)
        model.sendCommand("end")
        examGateway.submitResult = Result.failure(AppError(AppErrorKind.NETWORK))

        model.requestReport()

        val value = live(model)
        assertEquals("REPORT_READY", value.status)
        assertNotNull(value.error)
    }

    // ---------- 错误对齐与播报控制 ----------

    @Test
    fun `command conflict realigns from server and shows error`() = runVoiceTest {
        val model = newViewModel(VoiceEntry.Start("functions-basics"))
        // WAITING_ANSWER 不接受 commit_confirmed：服务端 409 → 客户端对齐 + 提示
        advanceToWaiting(model)
        model.sendCommand("commit_confirmed")

        val value = live(model)
        assertEquals("WAITING_ANSWER", value.status)
        assertNotNull(value.error)
    }

    @Test
    fun `pause stops playback and resume continues`() = runVoiceTest {
        val model = newViewModel(VoiceEntry.Start("functions-basics"))
        model.sendCommand("start_reading")
        assertEquals("WAITING_ANSWER", live(model).status)

        model.pauseAnnounce()

        val paused = live(model)
        assertTrue(paused.paused)
        assertEquals(1, playback.stopCalls)
        assertTrue(gateway.commands.any { it.second == "pause" })

        model.resumeAnnounce()

        val resumed = live(model)
        assertTrue(!resumed.paused)
        assertTrue(gateway.commands.any { it.second == "resume" })
        assertEquals("WAITING_ANSWER", resumed.status) // 播报控制自环不改状态
    }

    @Test
    fun `barge in during announcement moves to waiting`() = runVoiceTest {
        gateway.synthesizeResult = Result.failure(AppError(AppErrorKind.SERVER)) // 停在 READING_QUESTION
        val model = newViewModel(VoiceEntry.Start("functions-basics"))
        model.sendCommand("start_reading")
        assertEquals("READING_QUESTION", live(model).status)

        model.bargeIn()

        assertEquals("WAITING_ANSWER", live(model).status)
        assertEquals(1, playback.stopCalls)
    }

    // ---------- 录音（真实 ASR 代理链） ----------

    @Test
    fun `toggle recording without mic grant degrades to text with no capture start`() = runVoiceTest {
        val model = newViewModel(VoiceEntry.Start("functions-basics"))
        advanceToWaiting(model)

        model.toggleRecording()

        val value = live(model)
        assertNotNull("应显示降级提示", value.micNotice)
        assertEquals(0, capture.startCalls)
        assertTrue(!value.recording)
    }

    @Test
    fun `recording round trip transcribes and submits answer`() = runVoiceTest {
        val model = newViewModel(VoiceEntry.Start("functions-basics"))
        advanceToWaiting(model)
        model.setMicGranted(true)

        model.toggleRecording()
        assertTrue(live(model).recording)
        assertEquals(1, capture.startCalls)

        model.toggleRecording()

        val value = live(model)
        assertTrue(!value.recording)
        assertEquals("ANSWER_COMMITTED", value.status) // 转写「选 B」→ intents 提交
        assertEquals(1, capture.stopCalls)
        val answers = gateway.sessions[value.sessionId]?.second
        assertEquals("B", answers?.get("q1"))
        assertTrue(gateway.traces.any { it.first == "asr" })
    }

    @Test
    fun `capture start failure reports honestly without faking recording`() = runVoiceTest {
        capture.startError = AudioEngineException("录音设备不可用，请改用文字作答")
        val model = newViewModel(VoiceEntry.Start("functions-basics"))
        advanceToWaiting(model)
        model.setMicGranted(true)

        model.toggleRecording()

        val value = live(model)
        assertTrue(!value.recording)
        assertNotNull(value.error)
    }

    // ---------- Picker ----------

    @Test
    fun `picker entry shows providers and resume slot`() = runVoiceTest {
        voiceResumeStore.seed(VoiceResumeSlot("vs-pick", "exam-fixture-0001"))

        val model = newViewModel(VoiceEntry.Picker)

        val state = model.state.value
        assertTrue(state is VoiceUiState.Picker)
        val picker = (state as VoiceUiState.Picker).value
        assertEquals("local", picker.providers?.voiceMode)
        assertEquals("vs-pick", picker.resumeSlot?.sessionId)
        assertNull(picker.error)
    }

    @Test
    fun `picker provider failure still shows slot with error`() = runVoiceTest {
        gateway.providersResult = Result.failure(AppError(AppErrorKind.SERVER))
        voiceResumeStore.seed(VoiceResumeSlot("vs-pick", "exam-fixture-0001"))

        val model = newViewModel(VoiceEntry.Picker)

        val picker = (model.state.value as VoiceUiState.Picker).value
        assertNull(picker.providers)
        assertNotNull(picker.error)
        assertEquals("vs-pick", picker.resumeSlot?.sessionId) // 恢复提示不受链路概要失败影响
    }

    // ---------- trace 尽力而为 ----------

    @Test
    fun `trace failure never breaks the flow`() = runVoiceTest {
        gateway.traceError = AppError(AppErrorKind.SERVER)
        val model = newViewModel(VoiceEntry.Start("functions-basics"))

        model.sendCommand("start_reading")

        assertEquals("WAITING_ANSWER", live(model).status) // 播报链照常完成
    }
}
