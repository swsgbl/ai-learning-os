package com.ailearningos.app.ui.learning

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.LearningGateway
import com.ailearningos.app.data.remote.AssessmentItemDto
import com.ailearningos.app.data.remote.AssessmentSubmitItemDto
import com.ailearningos.app.data.remote.LearningContractDto
import com.ailearningos.app.data.remote.LearningSprintDto
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** Sprint 11 阶段单向链（与服务端 domain/sprint_state.py SPRINT_PHASES 对齐）。 */
val SPRINT_PHASES: List<String> = listOf(
    "created",
    "contract_confirmed",
    "baseline_assessment",
    "concept_map_review",
    "plan_confirm",
    "core_learning",
    "targeted_practice",
    "transfer_check",
    "posttest",
    "report",
    "follow_up",
)

/** 阶段展示标签（App 端本地映射；语义以服务端为准）。 */
val SPRINT_PHASE_LABELS: Map<String, String> = mapOf(
    "created" to "已创建",
    "contract_confirmed" to "合同已确认",
    "baseline_assessment" to "前测",
    "concept_map_review" to "概念地图",
    "plan_confirm" to "计划确认",
    "core_learning" to "核心学习",
    "targeted_practice" to "定向练习",
    "transfer_check" to "迁移检验",
    "posttest" to "后测",
    "report" to "学习报告",
    "follow_up" to "间隔复测",
)

/** 需要拉题作答的阶段 → assessment phase。 */
private val PHASE_TO_ASSESSMENT = mapOf(
    "baseline_assessment" to "baseline",
    "posttest" to "posttest",
)

data class ContractWithSprint(
    val contract: LearningContractDto,
    /** 该合同最新冲刺（服务端列表首个）；null = 未开冲刺 */
    val sprint: LearningSprintDto?,
)

/** 进行中的作答会话（前测/后测）——答案本地暂存，提交后服务端判分。 */
data class AssessmentSession(
    val sprintId: String,
    val phase: String,
    val items: List<AssessmentItemDto>,
    val answers: Map<String, String> = emptyMap(),
    val confidences: Map<String, Int> = emptyMap(),
) {
    val allAnswered: Boolean get() = items.isNotEmpty() && items.all { !answers[it.itemId].isNullOrBlank() }
}

data class LearningSprintState(
    val loading: Boolean = true,
    val contracts: List<ContractWithSprint> = emptyList(),
    // M20-002：迁移检验会话（App/Web 同步）
    val transferSession: com.ailearningos.app.data.remote.TransferSessionDto? = null,
    val transferAnswers: Map<String, String> = emptyMap(),
    val transferConfidence: Map<String, Int> = emptyMap(),
    val practiceConfidence: Int? = null,
    val reviewConfidence: Int? = null,
    val transferResult: com.ailearningos.app.data.remote.TransferResultDto? = null,
    // 定向练习（单题流）
    val practiceStep: com.ailearningos.app.data.remote.PracticeStepDto? = null,
    val practiceAnswer: String = "",
    val practiceFeedback: com.ailearningos.app.data.remote.PracticeAnswerDto? = null,
    // 复习
    val reviewQueue: List<com.ailearningos.app.data.remote.ReviewQueueItemDto> = emptyList(),
    val reviewSession: Pair<String, com.ailearningos.app.data.remote.AssessmentItemDto>? = null,
    val reviewAnswer: String = "",
    val reviewFeedback: Pair<Boolean?, String>? = null,
    /** 进行中的作答会话；null = 未在作答 */
    val session: AssessmentSession? = null,
    /** 最近一次提交结果（分数只在提交后存在——答案隔离不变式） */
    val lastResult: com.ailearningos.app.data.remote.AssessmentResultDto? = null,
    val busy: Boolean = false,
    val error: String? = null,
    val notice: String? = null,
)

/**
 * 学习冲刺简版面板（M16-012）：合同确认、阶段推进、前/后测作答与提交。
 * 全部网络流程经 [LearningGateway]；错误如实展示（409 跳段/重复提交等
 * 服务端语义不吞）。
 */
class LearningSprintViewModel(
    private val learning: LearningGateway,
) : ViewModel() {

    private val _state = MutableStateFlow(LearningSprintState())
    val state: StateFlow<LearningSprintState> = _state.asStateFlow()

    private var loadJob: Job? = null

    init {
        refresh()
    }

    fun refresh() {
        loadJob?.cancel()
        loadJob = viewModelScope.launch {
            _state.value = _state.value.copy(loading = true, error = null)
            try {
                val contracts = learning.contracts()
                val withSprints = contracts.map { contract ->
                    val sprint = try {
                        learning.contractSprints(contract.id).firstOrNull()
                    } catch (cancellation: CancellationException) {
                        throw cancellation
                    } catch (_: Throwable) {
                        // 单合同冲刺列表失败不阻塞整体（其他合同照常展示）
                        null
                    }
                    ContractWithSprint(contract, sprint)
                }
                _state.value = _state.value.copy(loading = false, contracts = withSprints)
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (cause: AppError) {
                _state.value = _state.value.copy(loading = false, error = cause.userMessage)
            } catch (_: Throwable) {
                _state.value = _state.value.copy(
                    loading = false,
                    error = AppError(AppErrorKind.UNEXPECTED).userMessage,
                )
            }
        }
    }

    fun confirmContract(contractId: String) = launchBusy {
        learning.confirmContract(contractId)
        _state.value = _state.value.copy(notice = "合同已确认——可以开始冲刺")
        refreshSilently()
    }

    /** 推进到下一阶段（服务端 11 阶段白名单校验，409 如实展示）。 */
    fun advance(sprintId: String) = launchBusy {
        val current = currentSprint(sprintId)
        val next = nextPhaseOf(current?.phase ?: return@launchBusy)
        if (next == null) {
            _state.value = _state.value.copy(error = "已在最后阶段")
            return@launchBusy
        }
        val updated = learning.advanceSprint(sprintId, next)
        _state.value = _state.value.copy(notice = "已推进到「${SPRINT_PHASE_LABELS[updated.phase] ?: updated.phase}」")
        refreshSilently()
    }

    /** 进入作答（前测/后测阶段可用；其他阶段按钮不出现）。 */
    fun startAssessment(sprintId: String) = launchBusy {
        val phase = PHASE_TO_ASSESSMENT[currentSprint(sprintId)?.phase] ?: return@launchBusy
        val response = learning.assessment(sprintId, phase)
        _state.value = _state.value.copy(
            session = AssessmentSession(sprintId = sprintId, phase = phase, items = response.items),
            lastResult = null,
        )
    }

    fun selectAnswer(itemId: String, key: String) {
        val session = _state.value.session ?: return
        _state.value = _state.value.copy(
            session = session.copy(answers = session.answers + (itemId to key)),
        )
    }

    fun setConfidence(itemId: String, level: Int) {
        val session = _state.value.session ?: return
        _state.value = _state.value.copy(
            session = session.copy(confidences = session.confidences + (itemId to level)),
        )
    }

    fun submitAssessment() = launchBusy {
        val session = _state.value.session ?: return@launchBusy
        if (!session.allAnswered) {
            _state.value = _state.value.copy(error = "请作答全部题目")
            return@launchBusy
        }
        val responses = session.items.map { item ->
            AssessmentSubmitItemDto(
                itemId = item.itemId,
                response = session.answers[item.itemId].orEmpty(),
                confidence = session.confidences[item.itemId],
            )
        }
        val result = learning.submitAssessment(session.sprintId, session.phase, responses)
        _state.value = _state.value.copy(
            session = null,
            lastResult = result,
            notice = "已提交：服务端判分 ${result.score}分（${result.correctCount}/${result.questionCount}）",
        )
        refreshSilently()
    }

    // ---------- M20-002 迁移检验 ----------
    fun startTransfer(sprintId: String) = launchBusy {
        _state.value = _state.value.copy(transferResult = null)
        val session = learning.transferItems(sprintId)
        _state.value = _state.value.copy(transferSession = session, transferAnswers = emptyMap())
    }

    fun selectTransferAnswer(itemId: String, key: String) {
        _state.value = _state.value.copy(
            transferAnswers = _state.value.transferAnswers + (itemId to key),
        )
    }

    fun setTransferConfidence(itemId: String, level: Int) {
        _state.value = _state.value.copy(
            transferConfidence = _state.value.transferConfidence + (itemId to level),
        )
    }

    fun submitTransfer(sprintId: String) = launchBusy {
        val session = _state.value.transferSession ?: return@launchBusy
        val responses = session.items.map {
            com.ailearningos.app.data.remote.TransferSubmitDto(
                itemId = it.itemId,
                response = _state.value.transferAnswers[it.itemId] ?: "",
                confidence = _state.value.transferConfidence[it.itemId],
            )
        }
        val result = learning.submitTransfer(sprintId, responses)
        _state.value = _state.value.copy(transferSession = null, transferResult = result)
        refreshSilently()
    }

    // ---------- 定向练习 ----------
    fun startPracticeStep(sprintId: String) = launchBusy {
        _state.value = _state.value.copy(practiceFeedback = null, practiceAnswer = "")
        val step = learning.practiceSession(sprintId)
        _state.value = _state.value.copy(practiceStep = step)
    }

    fun setPracticeAnswer(key: String) {
        _state.value = _state.value.copy(practiceAnswer = key)
    }

    fun setPracticeConfidence(level: Int) {
        _state.value = _state.value.copy(practiceConfidence = level)
    }

    fun submitPracticeAnswer(sprintId: String) = launchBusy {
        val step = _state.value.practiceStep ?: return@launchBusy
        if (_state.value.practiceAnswer.isBlank()) return@launchBusy
        val result = learning.practiceAnswer(sprintId, step.item.itemId, _state.value.practiceAnswer, _state.value.practiceConfidence)
        _state.value = _state.value.copy(practiceFeedback = result)
        if (!result.practiceComplete) {
            // 下一步在用户点「下一题」时拉取
        }
        refreshSilently()
    }

    // ---------- 复习 ----------
    fun startReview(reviewId: String) = launchBusy {
        val item = learning.reviewItem(reviewId)
        _state.value = _state.value.copy(reviewSession = reviewId to item, reviewFeedback = null, reviewAnswer = "")
    }

    fun setReviewAnswer(key: String) {
        _state.value = _state.value.copy(reviewAnswer = key)
    }

    fun setReviewConfidence(level: Int) {
        _state.value = _state.value.copy(reviewConfidence = level)
    }

    fun submitReviewAnswer() = launchBusy {
        val (reviewId, item) = _state.value.reviewSession ?: return@launchBusy
        if (_state.value.reviewAnswer.isBlank()) return@launchBusy
        learning.attemptReview(reviewId, item.itemId, _state.value.reviewAnswer, _state.value.reviewConfidence)
        // 判分结果从队列刷新推断（attempt 返回行不含对错展示——简版以队列状态呈现）
        _state.value = _state.value.copy(reviewSession = null)
        refreshSilently()
    }

    fun dismissMessages() {
        _state.value = _state.value.copy(error = null, notice = null)
    }

    private fun currentSprint(sprintId: String): LearningSprintDto? =
        _state.value.contracts.firstNotNullOfOrNull { c -> c.sprint?.takeIf { it.id == sprintId } }

    private fun nextPhaseOf(phase: String): String? {
        val index = SPRINT_PHASES.indexOf(phase)
        return if (index in 0 until SPRINT_PHASES.size - 1) SPRINT_PHASES[index + 1] else null
    }

    private fun refreshSilently() {
        loadJob?.cancel()
        loadJob = viewModelScope.launch {
            try {
                val contracts = learning.contracts()
                val withSprints = contracts.map { contract ->
                    ContractWithSprint(contract, learning.contractSprints(contract.id).firstOrNull())
                }
                _state.value = _state.value.copy(contracts = withSprints, loading = false)
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (_: Throwable) {
                // 静默刷新失败不打断当前 UI（下次 refresh 会有显式错误）
            }
        }
    }

    private inline fun launchBusy(crossinline block: suspend () -> Unit): Job =
        viewModelScope.launch {
            _state.value = _state.value.copy(busy = true, error = null)
            try {
                block()
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (cause: AppError) {
                _state.value = _state.value.copy(error = cause.userMessage)
            } catch (_: Throwable) {
                _state.value = _state.value.copy(
                    error = AppError(AppErrorKind.UNEXPECTED).userMessage,
                )
            } finally {
                _state.value = _state.value.copy(busy = false)
            }
        }
}
