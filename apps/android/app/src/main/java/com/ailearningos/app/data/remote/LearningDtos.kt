package com.ailearningos.app.data.remote

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * M16-012 学习冲刺业务 DTO：GET /api/v1/learning/contracts、
 * POST /contracts/{id}/confirm、GET /contracts/{id}/sprints、
 * POST /sprints/{id}/advance、GET/POST sprints/{id}/assessment。
 *
 * snake_case 与服务端 Pydantic 契约一一对应
 * （services/api/app/api/routes/learning.py）；服务端演进新增字段靠
 * ignoreUnknownKeys 容忍，不猜语义。答案隔离不变式：AssessmentItemDto
 * 不含 answer/explanation——正确答案绝不下发（M16-004）。
 */

@Serializable
data class LearningContractDto(
    val id: String,
    val title: String,
    @SerialName("target_outcomes") val targetOutcomes: List<TargetOutcomeDto> = emptyList(),
    /** 服务端 Pydantic 声明 float（序列化为 48.0 形态）——DTO 必须同型 */
    @SerialName("time_budget_hours") val timeBudgetHours: Double = 48.0,
    @SerialName("pass_criteria") val passCriteria: String = "",
    /** 服务端语义：null = 未确认；非 null = 已确认（确认时间戳不展示） */
    @SerialName("confirmed_at") val confirmedAt: String? = null,
)

@Serializable
data class TargetOutcomeDto(
    val statement: String,
    val criteria: String = "",
)

@Serializable
data class LearningSprintDto(
    val id: String,
    @SerialName("contract_id") val contractId: String,
    /** 11 阶段单向链（domain/sprint_state.py SPRINT_PHASES）——展示用标签本地映射 */
    val phase: String,
    val paused: Boolean = false,
    @SerialName("pause_reason") val pauseReason: String = "",
    val expired: Boolean = false,
)

@Serializable
data class AssessmentItemDto(
    @SerialName("item_id") val itemId: String,
    @SerialName("concept_id") val conceptId: String,
    @SerialName("question_type") val questionType: String,
    val stem: String,
    val options: List<AssessmentOptionDto>? = null,
)

@Serializable
data class AssessmentOptionDto(
    val key: String,
    val text: String,
)

@Serializable
data class AssessmentResponseDto(
    @SerialName("sprint_id") val sprintId: String,
    /** baseline | posttest */
    val phase: String,
    val items: List<AssessmentItemDto>,
)

@Serializable
data class AssessmentSubmitItemDto(
    @SerialName("item_id") val itemId: String,
    val response: String,
    /** 1-5；null = 不填（服务端容缺省） */
    val confidence: Int? = null,
)

@Serializable
data class AssessmentSubmitRequestDto(
    val responses: List<AssessmentSubmitItemDto>,
)

@Serializable
data class AssessmentResultDto(
    @SerialName("sprint_id") val sprintId: String,
    val phase: String,
    /** 百分比 0-100 */
    val score: Double,
    @SerialName("correct_count") val correctCount: Int,
    @SerialName("question_count") val questionCount: Int,
    @SerialName("ungraded_count") val ungradedCount: Int,
    /** 提交后服务端推进到的冲刺阶段（baseline→concept_map_review 等） */
    @SerialName("sprint_phase") val sprintPhase: String,
)

@Serializable
data class AdvanceSprintRequestDto(
    val to_phase: String,
)
