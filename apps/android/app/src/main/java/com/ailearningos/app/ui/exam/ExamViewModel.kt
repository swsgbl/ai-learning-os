package com.ailearningos.app.ui.exam

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.ExamGateway
import com.ailearningos.app.data.asAppError
import com.ailearningos.app.data.local.ExamResumeStore
import com.ailearningos.app.data.model.ExamQuestion
import com.ailearningos.app.data.model.ExamSession
import java.time.OffsetDateTime
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** 考试页入口：从试卷开新考，或从本地恢复提示（exam_id）直接恢复 */
sealed interface ExamEntry {
    data class Start(val paperId: String) : ExamEntry
    data class Resume(val examId: String) : ExamEntry
}

/** 答案同步进度：Idle 已同步 / Syncing 同步中 / Failed 可重试 */
sealed interface SyncState {
    data object Idle : SyncState
    data object Syncing : SyncState
    data class Failed(val message: String) : SyncState
}

data class ExamActive(
    val examId: String,
    val paperTitle: String,
    val questions: List<ExamQuestion>,
    /** 显示值 = 服务端已落库答案 + 待同步（本地乐观）覆盖 */
    val answers: Map<String, String>,
    val currentIndex: Int,
    val remainingSeconds: Int,
    val sync: SyncState = SyncState.Idle,
    val submitting: Boolean = false,
    val submitError: String? = null,
) {
    val answeredCount: Int get() = answers.count { it.value.isNotBlank() }
}

sealed interface ExamUiState {
    data object Loading : ExamUiState
    data class Unavailable(val message: String) : ExamUiState
    data class Active(val value: ExamActive) : ExamUiState
}

/**
 * 考场状态机（M12-02）。服务端权威语义：
 * - next_sequence 只从服务端响应推进——PUT 失败绝不消耗序号，重试沿用同序号
 *   （服务端同 (sequence, question_id, answer) 幂等）；
 * - 同步失败（网络断/409）先 GET exam 对齐 answers/next_sequence/剩余时间，
 *   再以对齐后的序号重放未生效答案——本地答案只是乐观显示，不是真相源；
 * - 倒计时由 server_end_at + 时钟推算（server_remaining_seconds 为初值），
 *   归零自动交卷；交卷幂等 + 防抖（submitted 标志 + 在途 Job 检查）；
 * - 本地只保留 exam_id 恢复提示（[ExamResumeStore]），进程重启后重新 GET 对齐。
 */
class ExamViewModel(
    private val exam: ExamGateway,
    private val resumeStore: ExamResumeStore,
    private val entry: ExamEntry,
    private val clock: () -> Long = System::currentTimeMillis,
) : ViewModel() {

    private val _state = MutableStateFlow<ExamUiState>(ExamUiState.Loading)
    val state: StateFlow<ExamUiState> = _state.asStateFlow()

    /** 交卷完成待导航（exam_id）；Screen 消费后调 [consumeFinished] */
    private val _finished = MutableStateFlow<String?>(null)
    val finished: StateFlow<String?> = _finished.asStateFlow()

    // ---------- 服务端权威状态镜像（只在成功响应后更新） ----------

    private var examId: String? = null
    private var nextSequence: Int = 1
    private var serverAnswers: Map<String, String> = emptyMap()
    private var deadlineMillis: Long = 0L

    /** 待同步答案（questionId -> 最新值）；PUT 成功且服务端值一致时清除 */
    private val pending = LinkedHashMap<String, String>()

    private var beginJob: Job? = null
    private var tickerJob: Job? = null
    private var drainJob: Job? = null
    private var submitJob: Job? = null

    /** 交卷防抖：一旦进入交卷流程（含在途失败重试窗口）不再重复触发 */
    @Volatile
    private var submitted = false

    init {
        begin()
    }

    /** 进入/重试进入考场（重入会取消在途 begin） */
    fun begin() {
        beginJob?.cancel()
        beginJob = viewModelScope.launch {
            _state.value = ExamUiState.Loading
            try {
                when (val source = entry) {
                    is ExamEntry.Resume -> resumeFrom(source.examId)
                    is ExamEntry.Start -> startOrResume(source.paperId)
                }
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (cause: AppError) {
                _state.value = ExamUiState.Unavailable(cause.userMessage)
            } catch (_: Throwable) {
                _state.value = ExamUiState.Unavailable(AppError(AppErrorKind.UNEXPECTED).userMessage)
            }
        }
    }

    // ---------- 进入流程 ----------

    /** 点击试卷开考：本地若有恢复提示先尝试续考（同卷 active 才续），否则开新考 */
    private suspend fun startOrResume(paperId: String) {
        val resumeExamId = resumeStore.load()
        if (resumeExamId == null) {
            startNew(paperId)
            return
        }
        val session = try {
            exam.exam(resumeExamId)
        } catch (cause: AppError) {
            if (cause.kind == AppErrorKind.NOT_FOUND) {
                // 恢复提示失效（服务端已无此考试）：清槽后按点击意图开新考
                resumeStore.clear()
                startNew(paperId)
                return
            }
            // 网络等服务端失败：不能盲目开新考（可能产生重复考试），如实报错
            throw cause
        }
        when {
            session.isActive && session.paperId == paperId -> adopt(session)
            session.isSubmitted -> finishToReview(session.examId)
            session.isExpired -> settleAndReview(session.examId)
            else -> startNew(paperId) // 非进行中（含恢复的是别的卷）：按点击意图开新考
        }
    }

    /** 从恢复卡进入：一切以 GET 结果为准 */
    private suspend fun resumeFrom(examId: String) {
        val session = try {
            exam.exam(examId)
        } catch (cause: AppError) {
            if (cause.kind == AppErrorKind.NOT_FOUND) resumeStore.clear()
            throw cause
        }
        when {
            session.isActive -> adopt(session)
            session.isSubmitted -> finishToReview(session.examId)
            session.isExpired -> settleAndReview(session.examId)
            else -> _state.value = ExamUiState.Unavailable(AppError(AppErrorKind.BAD_RESPONSE).userMessage)
        }
    }

    private suspend fun startNew(paperId: String) {
        val session = exam.startExam(paperId)
        resumeStore.save(session.examId)
        adopt(session)
    }

    /** 已过期未出报告：交由服务端结算（submit 幂等）后进审阅 */
    private suspend fun settleAndReview(targetExamId: String) {
        exam.submit(targetExamId)
        finishToReview(targetExamId)
    }

    // ---------- 会话采纳（服务端快照 → UI 状态） ----------

    private fun adopt(session: ExamSession) {
        examId = session.examId
        nextSequence = session.nextSequence
        serverAnswers = session.answers
        // 对齐后服务端已生效的待同步项清除（值一致才清；用户改过则保留重放）
        val iter = pending.entries.iterator()
        while (iter.hasNext()) {
            val item = iter.next()
            if (serverAnswers[item.key] == item.value) iter.remove()
        }
        deadlineMillis = parseDeadline(session)
        _state.value = ExamUiState.Active(
            ExamActive(
                examId = session.examId,
                paperTitle = session.paperTitle,
                questions = session.questions,
                answers = serverAnswers + pending,
                currentIndex = currentIndexOf(),
                remainingSeconds = session.serverRemainingSeconds,
            ),
        )
        startTicker()
    }

    /** server_end_at 是权威截止；解析失败（防御）退回「采纳时刻 + 服务端剩余秒数」 */
    private fun parseDeadline(session: ExamSession): Long =
        runCatching { OffsetDateTime.parse(session.serverEndAt).toInstant().toEpochMilli() }
            .getOrElse { clock() + session.serverRemainingSeconds * 1000L }

    private fun currentIndexOf(): Int {
        val active = (_state.value as? ExamUiState.Active)?.value ?: return 0
        return active.currentIndex.coerceIn(0, (active.questions.size - 1).coerceAtLeast(0))
    }

    // ---------- 服务端权威倒计时 ----------

    private fun startTicker() {
        tickerJob?.cancel()
        tickerJob = viewModelScope.launch {
            while (true) {
                delay(TICK_MILLIS)
                val remaining = ((deadlineMillis - clock()) / 1000L).coerceAtLeast(0L).toInt()
                updateActive { it.copy(remainingSeconds = remaining) }
                if (remaining <= 0) {
                    submit()
                    break
                }
            }
        }
    }

    // ---------- 作答与同步 ----------

    /**
     * 录入答案（单选/多选/文本统一入口）：
     * 本地乐观显示 + 串行同步；序号只在 PUT 成功后推进。
     */
    fun choose(questionId: String, answer: String) {
        if (answer.isBlank()) return
        if (_state.value !is ExamUiState.Active) return
        if (submitted) return
        pending[questionId] = answer
        updateActive { it.copy(answers = it.answers + (questionId to answer), sync = SyncState.Syncing) }
        scheduleDrain()
    }

    /** 同步失败后的手动重试入口 */
    fun retrySync() {
        if (_state.value !is ExamUiState.Active) return
        if (pending.isEmpty()) return
        updateActive { it.copy(sync = SyncState.Syncing) }
        scheduleDrain()
    }

    private fun scheduleDrain() {
        if (drainJob?.isActive == true) return
        drainJob = viewModelScope.launch { drain() }
    }

    private suspend fun drain() {
        var consecutiveFailures = 0
        while (pending.isNotEmpty() && _state.value is ExamUiState.Active) {
            val currentExamId = examId ?: return
            val first = pending.entries.first()
            val questionId = first.key
            val answer = first.value
            try {
                val saved = exam.saveAnswer(currentExamId, nextSequence, questionId, answer)
                if (pending[questionId] == answer) pending.remove(questionId)
                adopt(saved)
                updateActive { it.copy(sync = SyncState.Idle) }
                consecutiveFailures = 0
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (cause: Throwable) {
                // 失败：先向服务端对齐（answers/next_sequence/剩余时间），对齐成功以新序号重放；
                // 对齐失败（断网）保留待同步答案并提示重试——绝不消耗序号。
                val fresh = try {
                    exam.exam(currentExamId)
                } catch (alignmentCancellation: CancellationException) {
                    throw alignmentCancellation
                } catch (_: Throwable) {
                    null
                }
                if (fresh == null) {
                    updateActive { it.copy(sync = SyncState.Failed(cause.asAppError().userMessage)) }
                    return
                }
                when {
                    fresh.isActive -> {
                        // 服务端持续拒绝同一答案（对齐后重放仍失败）时不无限对齐-重放：
                        // 停在可重试态，由用户重试或交卷按服务端已有答案结算。
                        consecutiveFailures++
                        if (consecutiveFailures >= MAX_SYNC_RETRIES) {
                            updateActive { it.copy(sync = SyncState.Failed(cause.asAppError().userMessage)) }
                            return
                        }
                        adopt(fresh)
                        // 循环继续：以对齐后的 nextSequence 重放 pending
                    }
                    fresh.isSubmitted -> {
                        pending.clear()
                        finishToReview(fresh.examId)
                        return
                    }
                    else -> {
                        // expired：按服务端结算收口
                        pending.clear()
                        submit()
                        return
                    }
                }
            }
        }
        if (pending.isEmpty()) updateActive { it.copy(sync = SyncState.Idle) }
    }

    // ---------- 交卷（幂等 + 防抖） ----------

    /** 用户确认或倒计时归零统一走这里；重复触发被防抖挡下 */
    fun submit() {
        val currentExamId = examId ?: return
        if (submitted || submitJob?.isActive == true) return
        submitted = true
        updateActive { it.copy(submitting = true, submitError = null) }
        submitJob = viewModelScope.launch {
            try {
                // 等在途同步收口后再补交一轮待同步答案（失败不阻塞：按服务端已有答案结算）
                drainJob?.join()
                if (pending.isNotEmpty() && _state.value is ExamUiState.Active) {
                    drain()
                }
                exam.submit(currentExamId)
                resumeStore.clear()
                tickerJob?.cancel()
                _finished.value = currentExamId
            } catch (cancellation: CancellationException) {
                submitted = false
                updateActive { it.copy(submitting = false) }
                throw cancellation
            } catch (cause: Throwable) {
                submitted = false
                updateActive { it.copy(submitting = false, submitError = cause.asAppError().userMessage) }
            }
        }
    }

    // ---------- 题目导航 ----------

    fun selectIndex(index: Int) {
        updateActive { it.copy(currentIndex = index.coerceIn(0, (it.questions.size - 1).coerceAtLeast(0))) }
    }

    fun previous() = updateActive {
        it.copy(currentIndex = (it.currentIndex - 1).coerceAtLeast(0))
    }

    fun next() = updateActive {
        it.copy(currentIndex = (it.currentIndex + 1).coerceAtMost(it.questions.size - 1))
    }

    // ---------- 导航事件 ----------

    fun consumeFinished() {
        _finished.value = null
    }

    /** 考试收口进审阅：停 ticker、清恢复槽（ExamResumeStore 契约：交卷完成后不再保留提示） */
    private suspend fun finishToReview(finishedExamId: String) {
        tickerJob?.cancel()
        resumeStore.clear()
        _finished.value = finishedExamId
    }

    private fun updateActive(transform: (ExamActive) -> ExamActive) {
        val current = _state.value
        if (current is ExamUiState.Active) {
            _state.value = ExamUiState.Active(transform(current.value))
        }
    }

    private companion object {
        const val TICK_MILLIS = 500L

        /** 对齐-重放连续失败上限：达到即停在可重试态，不无限消耗请求 */
        const val MAX_SYNC_RETRIES = 2
    }
}
