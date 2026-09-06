package com.ailearningos.app.ui

import androidx.lifecycle.viewModelScope
import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.testutil.FakeExamGateway
import com.ailearningos.app.testutil.FakeExamResumeStore
import com.ailearningos.app.testutil.MainDispatcherRule
import com.ailearningos.app.testutil.examSessionFixture
import com.ailearningos.app.testutil.mcqQuestionFixture
import com.ailearningos.app.testutil.shortQuestionFixture
import com.ailearningos.app.testutil.submissionFixture
import com.ailearningos.app.ui.exam.ExamActive
import com.ailearningos.app.ui.exam.ExamEntry
import com.ailearningos.app.ui.exam.ExamUiState
import com.ailearningos.app.ui.exam.ExamViewModel
import com.ailearningos.app.ui.exam.SyncState
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/**
 * 考场状态机（M12-02）：开考/恢复对齐、append-only 序号契约、
 * 同步失败不耗序号、409 对齐重放、倒计时归零、交卷防抖。
 *
 * 调度隔离（防挂死约定）：
 * - runTest 与 MainDispatcherRule 共享同一 testScheduler——否则
 *   advanceTimeBy 触不到 ticker 的 delay；
 * - 考试 VM 持有 while+delay 常驻 ticker，runTest 的 idle 检测会因其
 *   永不空闲而挂死，所以每个用例经 [runExamTest] 在测试体内 finally
 *   先 cancel 全部 viewModelScope，再让 runTest 收尾。
 */
class ExamViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private lateinit var gateway: FakeExamGateway
    private lateinit var resumeStore: FakeExamResumeStore

    /** fake 时钟（毫秒），测试直接改写推进时间 */
    private var nowMillis = 1_000_000L

    private val created = mutableListOf<ExamViewModel>()

    @Before
    fun setUp() {
        gateway = FakeExamGateway()
        resumeStore = FakeExamResumeStore()
    }

    private fun runExamTest(block: suspend TestScope.() -> Unit) = runTest(
        mainDispatcherRule.testDispatcher.scheduler,
    ) {
        try {
            block()
        } finally {
            created.forEach { it.viewModelScope.cancel() }
        }
    }

    private fun newViewModel(entry: ExamEntry): ExamViewModel {
        val model = ExamViewModel(
            exam = gateway,
            resumeStore = resumeStore,
            entry = entry,
            clock = { nowMillis },
        )
        created.add(model)
        return model
    }

    /** 以 fake 时钟为基准生成 endAt（now + 秒），保证倒计时可精确推演 */
    private fun endAtSecondsFromNow(seconds: Int): String =
        Instant.ofEpochMilli(nowMillis + seconds * 1000L)
            .atZone(ZoneOffset.UTC)
            .format(DateTimeFormatter.ISO_OFFSET_DATE_TIME)

    private fun activeSession(
        examId: String = "exam-fixture-0001",
        paperId: String = "functions-basics",
        remainingSeconds: Int = 360,
        nextSequence: Int = 1,
        answers: Map<String, String> = emptyMap(),
    ) = examSessionFixture(
        examId = examId,
        paperId = paperId,
        status = "active",
        serverEndAt = endAtSecondsFromNow(remainingSeconds),
        serverRemainingSeconds = remainingSeconds,
        nextSequence = nextSequence,
        answers = answers,
    )

    private fun activeValue(model: ExamViewModel): ExamActive {
        val state = model.state.value
        assertTrue("应处于 Active 态，实际 $state", state is ExamUiState.Active)
        return (state as ExamUiState.Active).value
    }

    // ---------- 开考（Start 入口） ----------

    @Test
    fun `start without resume record starts new exam and saves resume slot`() = runExamTest {
        gateway.startExamResult = Result.success(activeSession(examId = "exam-new"))
        val model = newViewModel(ExamEntry.Start("functions-basics"))

        val value = activeValue(model)
        assertEquals("exam-new", value.examId)
        assertEquals(2, value.questions.size)
        assertEquals(mapOf<String, String>(), value.answers)
        assertEquals("exam-new", resumeStore.load())
        assertEquals(listOf("functions-basics"), gateway.startExamCalls)
        assertTrue(gateway.examCalls.isEmpty())
    }

    @Test
    fun `start with active resume of same paper resumes instead of starting`() = runExamTest {
        resumeStore.seed("exam-live-1")
        gateway.session = activeSession(examId = "exam-live-1", answers = mapOf("q1" to "B"), nextSequence = 2)
        val model = newViewModel(ExamEntry.Start("functions-basics"))

        val value = activeValue(model)
        // 服务端权威：answers / next_sequence 全部来自 GET 响应
        assertEquals("exam-live-1", value.examId)
        assertEquals(mapOf("q1" to "B"), value.answers)
        assertTrue(gateway.startExamCalls.isEmpty())
        assertEquals(listOf("exam-live-1"), gateway.examCalls)
    }

    @Test
    fun `start with active resume of different paper starts fresh`() = runExamTest {
        resumeStore.seed("exam-other-paper")
        gateway.session = activeSession(examId = "exam-other-paper", paperId = "other-paper")
        gateway.startExamResult = Result.success(activeSession(examId = "exam-new"))
        val model = newViewModel(ExamEntry.Start("functions-basics"))

        assertEquals("exam-new", activeValue(model).examId)
        assertEquals("exam-new", resumeStore.load())
    }

    @Test
    fun `start with submitted resume navigates to review without new exam`() = runExamTest {
        resumeStore.seed("exam-done-1")
        gateway.session = activeSession(examId = "exam-done-1").copy(status = "submitted")
        val model = newViewModel(ExamEntry.Start("functions-basics"))

        assertEquals("exam-done-1", model.finished.value)
        assertNull("已收口的考试必须清除恢复槽", resumeStore.load())
        assertTrue(gateway.startExamCalls.isEmpty())
        assertEquals(0, gateway.submitCalls.size)
    }

    @Test
    fun `start with expired resume settles server side then reviews`() = runExamTest {
        resumeStore.seed("exam-expired-1")
        gateway.session = activeSession(examId = "exam-expired-1").copy(status = "expired")
        val model = newViewModel(ExamEntry.Start("functions-basics"))

        assertEquals("exam-expired-1", model.finished.value)
        assertEquals(listOf("exam-expired-1"), gateway.submitCalls)
        assertNull(resumeStore.load())
    }

    @Test
    fun `start with stale resume record clears slot and starts new`() = runExamTest {
        resumeStore.seed("exam-gone")
        gateway.failNextExam = AppError(AppErrorKind.NOT_FOUND)
        gateway.startExamResult = Result.success(activeSession(examId = "exam-new"))
        val model = newViewModel(ExamEntry.Start("functions-basics"))

        assertEquals("exam-new", activeValue(model).examId)
        // 404 清除失效的 exam-gone 后，开新考重新落槽指向新考试（与无记录开新考用例一致）
        assertEquals("exam-new", resumeStore.load())
    }

    @Test
    fun `start with unreachable server reports unavailable without starting duplicate exam`() = runExamTest {
        resumeStore.seed("exam-unknown-state")
        gateway.failNextExam = AppError(AppErrorKind.NETWORK)
        val model = newViewModel(ExamEntry.Start("functions-basics"))

        val state = model.state.value
        assertTrue("服务端状态未知时不得盲目开新考", state is ExamUiState.Unavailable)
        assertTrue(gateway.startExamCalls.isEmpty())
        assertEquals("exam-unknown-state", resumeStore.load())
    }

    // ---------- 恢复（Resume 入口） ----------

    @Test
    fun `resume entry adopts active session state`() = runExamTest {
        gateway.session = activeSession(
            answers = mapOf("q1" to "B", "q2" to "x=1"),
            nextSequence = 3,
            remainingSeconds = 120,
        )
        val model = newViewModel(ExamEntry.Resume("exam-live-1"))

        val value = activeValue(model)
        assertEquals(120, value.remainingSeconds)
        assertEquals(mapOf("q1" to "B", "q2" to "x=1"), value.answers)
        assertEquals(listOf("exam-live-1"), gateway.examCalls)
    }

    @Test
    fun `resume entry with missing exam clears slot and reports unavailable`() = runExamTest {
        resumeStore.seed("exam-gone")
        gateway.failNextExam = AppError(AppErrorKind.NOT_FOUND)
        val model = newViewModel(ExamEntry.Resume("exam-gone"))

        assertTrue(model.state.value is ExamUiState.Unavailable)
        assertNull(resumeStore.load())
    }

    @Test
    fun `resume failure can retry`() = runExamTest {
        gateway.failNextExam = AppError(AppErrorKind.NETWORK)
        val model = newViewModel(ExamEntry.Resume("exam-live-1"))
        assertTrue(model.state.value is ExamUiState.Unavailable)

        model.begin()
        activeValue(model)
        assertEquals(2, gateway.examCalls.size)
    }

    // ---------- 作答与 append-only 序号 ----------

    @Test
    fun `single choice saved with server sequence and optimistic display`() = runExamTest {
        gateway.session = activeSession()
        val model = newViewModel(ExamEntry.Resume("exam-live-1"))

        model.choose("q1", "B")

        val value = activeValue(model)
        assertEquals(mapOf("q1" to "B"), value.answers)
        assertEquals(1, gateway.savedAnswers.size)
        assertEquals(1, gateway.savedAnswers[0].sequence)
        assertTrue(value.sync is SyncState.Idle)
    }

    @Test
    fun `revised answer consumes next server sequence`() = runExamTest {
        gateway.session = activeSession()
        val model = newViewModel(ExamEntry.Resume("exam-live-1"))

        model.choose("q1", "B")
        model.choose("q1", "C")

        assertEquals(2, gateway.savedAnswers.size)
        assertEquals(1, gateway.savedAnswers[0].sequence)
        assertEquals(2, gateway.savedAnswers[1].sequence)
        assertEquals("C", gateway.savedAnswers[1].answer)
    }

    @Test
    fun `rapid choices across questions drain serially with increasing sequences`() = runExamTest {
        gateway.session = activeSession()
        val model = newViewModel(ExamEntry.Resume("exam-live-1"))

        model.choose("q1", "B")
        model.choose("q2", "x=1")
        model.choose("q1", "C")

        // 串行 drain：序号严格递增无重复消耗，最终状态收敛
        assertEquals(3, gateway.savedAnswers.size)
        assertEquals(listOf(1, 2, 3), gateway.savedAnswers.map { it.sequence })
        assertEquals(mapOf("q1" to "C", "q2" to "x=1"), gateway.session.answers)
        assertTrue(activeValue(model).sync is SyncState.Idle)
    }

    @Test
    fun `blank answer is ignored`() = runExamTest {
        gateway.session = activeSession()
        val model = newViewModel(ExamEntry.Resume("exam-live-1"))

        model.choose("q1", " ")

        assertTrue(gateway.savedAnswers.isEmpty())
        assertTrue(activeValue(model).answers.isEmpty())
    }

    // ---------- 同步失败：不耗序号 + 对齐重放 ----------

    @Test
    fun `transient put failure with reachable get realigns and replays`() = runExamTest {
        gateway.session = activeSession()
        val model = newViewModel(ExamEntry.Resume("exam-live-1"))

        gateway.failNextSaveAnswer = AppError(AppErrorKind.NETWORK)
        model.choose("q1", "B")

        // PUT 失败 → GET 对齐 → 自动重放：序号始终是服务端 next_sequence
        assertEquals(2, gateway.savedAnswers.size)
        assertEquals(1, gateway.savedAnswers[0].sequence)
        assertEquals(1, gateway.savedAnswers[1].sequence)
        assertTrue(activeValue(model).sync is SyncState.Idle)
        assertEquals(mapOf("q1" to "B"), gateway.session.answers)
    }

    @Test
    fun `offline put and get keeps pending without consuming sequence`() = runExamTest {
        gateway.session = activeSession()
        val model = newViewModel(ExamEntry.Resume("exam-live-1"))

        gateway.failNextSaveAnswer = AppError(AppErrorKind.NETWORK)
        gateway.failNextExam = AppError(AppErrorKind.NETWORK)
        model.choose("q1", "B")

        val failed = activeValue(model)
        assertTrue("断网时必须给出可重试提示", failed.sync is SyncState.Failed)
        // 乐观显示保留（非真相源，仅待同步覆盖）
        assertEquals(mapOf("q1" to "B"), failed.answers)

        // 网络恢复后重试：沿用同一序号（服务端幂等），不消耗新序号
        model.retrySync()
        assertEquals(2, gateway.savedAnswers.size)
        assertEquals(1, gateway.savedAnswers[0].sequence)
        assertEquals(1, gateway.savedAnswers[1].sequence)
        assertTrue(activeValue(model).sync is SyncState.Idle)
    }

    @Test
    fun `conflict 409 realigns to server state and stops replay when identical`() = runExamTest {
        // 服务端已有 q1=B（seq=1 已消耗），本地仍从 seq=1 发起
        gateway.session = activeSession(answers = mapOf("q1" to "B"), nextSequence = 2)
        val model = newViewModel(ExamEntry.Resume("exam-live-1"))

        gateway.failNextSaveAnswer = AppError(AppErrorKind.CONFLICT)
        model.choose("q1", "B")

        // PUT(1) 409 → GET 对齐（answers q1=B）→ pending 与服务端一致即清除，不重放
        assertEquals(1, gateway.savedAnswers.size)
        val value = activeValue(model)
        assertEquals(mapOf("q1" to "B"), value.answers)
        assertTrue(value.sync is SyncState.Idle)
    }

    @Test
    fun `conflict 409 with revised answer replays on next server sequence`() = runExamTest {
        gateway.session = activeSession()
        val model = newViewModel(ExamEntry.Resume("exam-live-1"))

        model.choose("q1", "B") // seq=1 成功，next=2
        gateway.failNextSaveAnswer = AppError(AppErrorKind.CONFLICT)
        model.choose("q1", "C") // PUT(2) 409 → GET 对齐(next=2) → 用 seq=2 重放

        assertEquals(3, gateway.savedAnswers.size)
        assertEquals(2, gateway.savedAnswers[2].sequence)
        assertEquals("C", gateway.savedAnswers[2].answer)
        assertEquals(mapOf("q1" to "C"), gateway.session.answers)
    }

    @Test
    fun `persistently rejected answer surfaces failure instead of looping`() = runExamTest {
        gateway.session = activeSession()
        val model = newViewModel(ExamEntry.Resume("exam-live-1"))

        // 服务端持续拒绝该答案（每次都 409），本地不应对齐-重放死循环
        gateway.saveAnswerBehavior = { _, _, _ -> throw AppError(AppErrorKind.CONFLICT) }
        model.choose("q1", "B")

        val value = activeValue(model)
        assertTrue("持续失败必须停在可重试态", value.sync is SyncState.Failed)
        assertTrue("不得无限重试消耗请求", gateway.savedAnswers.size <= 3)
        // 待同步答案与乐观显示保留，交卷时按服务端已有答案结算
        assertEquals(mapOf("q1" to "B"), value.answers)
    }

    // ---------- 倒计时（服务端权威 endAt + 本地时钟） ----------

    @Test
    fun `countdown derives from server end at via injected clock`() = runExamTest {
        gateway.session = activeSession(remainingSeconds = 30)
        val model = newViewModel(ExamEntry.Resume("exam-live-1"))

        assertEquals(30, activeValue(model).remainingSeconds)

        nowMillis += 10_000
        advanceTimeBy(600)
        assertEquals("剩余时间按 endAt-时钟 推算", 20, activeValue(model).remainingSeconds)
    }

    @Test
    fun `countdown reaching zero auto submits once and finishes`() = runExamTest {
        gateway.session = activeSession(remainingSeconds = 5)
        val model = newViewModel(ExamEntry.Resume("exam-live-1"))

        nowMillis += 6_000
        advanceTimeBy(600)

        assertTrue(model.state.value is ExamUiState.Active)
        assertEquals("倒计时归零必须自动交卷", listOf("exam-live-1"), gateway.submitCalls)
        assertEquals("exam-live-1", model.finished.value)
        assertNull(resumeStore.load())
    }

    // ---------- 交卷（幂等防抖 + 待同步补交） ----------

    @Test
    fun `submit is debounced against repeated triggers`() = runExamTest {
        gateway.session = activeSession()
        val model = newViewModel(ExamEntry.Resume("exam-live-1"))

        model.submit()
        model.submit()

        assertEquals(1, gateway.submitCalls.size)
        assertEquals("exam-live-1", model.finished.value)
        assertNull(resumeStore.load())
    }

    @Test
    fun `submit failure recovers and can submit again`() = runExamTest {
        gateway.session = activeSession()
        gateway.submitResult = Result.failure(AppError(AppErrorKind.NETWORK))
        val model = newViewModel(ExamEntry.Resume("exam-live-1"))

        model.submit()
        val failed = activeValue(model)
        assertTrue("交卷失败要如实提示", failed.submitError != null)
        assertTrue(model.finished.value == null)

        gateway.submitResult = Result.success(submissionFixture())
        model.submit()
        assertEquals(2, gateway.submitCalls.size)
        assertEquals("exam-live-1", model.finished.value)
    }

    @Test
    fun `submit flushes pending answers before settlement`() = runExamTest {
        gateway.session = activeSession()
        val model = newViewModel(ExamEntry.Resume("exam-live-1"))

        model.choose("q1", "B")
        gateway.failNextSaveAnswer = AppError(AppErrorKind.NETWORK)
        gateway.failNextExam = AppError(AppErrorKind.NETWORK)
        model.choose("q2", "x=1") // 卡在待同步
        assertTrue(activeValue(model).sync is SyncState.Failed)

        model.submit()

        // 补交链路在交卷前把待同步答案落库
        assertTrue(gateway.savedAnswers.any { it.questionId == "q2" && it.answer == "x=1" })
        assertEquals(listOf("exam-live-1"), gateway.submitCalls)
    }

    // ---------- 题目导航 ----------

    @Test
    fun `navigation moves within bounds`() = runExamTest {
        gateway.session = examSessionFixture(
            questions = listOf(mcqQuestionFixture("q1"), mcqQuestionFixture("q2"), shortQuestionFixture("q3")),
            serverEndAt = endAtSecondsFromNow(300),
        )
        val model = newViewModel(ExamEntry.Resume("exam-live-1"))

        assertEquals(0, activeValue(model).currentIndex)
        model.previous()
        assertEquals(0, activeValue(model).currentIndex)
        model.next()
        model.next()
        assertEquals(2, activeValue(model).currentIndex)
        model.next()
        assertEquals(2, activeValue(model).currentIndex)
        model.selectIndex(0)
        assertEquals(0, activeValue(model).currentIndex)
    }
}
