package com.ailearningos.app.data.remote

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * M12-02 考试业务 DTO：GET /api/v1/papers、POST /papers/{id}/exams、
 * GET/PUT exams、submit、submission、report。
 * snake_case 与服务端 Pydantic 契约一一对应（services/api/app/api/schemas.py）；
 * 服务端演进新增字段时靠 ignoreUnknownKeys 容忍，不猜语义。
 */

// ---------- 试卷 ----------

@Serializable
data class PaperResponse(
    val id: String,
    val title: String,
    val subtitle: String,
    val source: String,
    val university: String? = null,
    val year: Int? = null,
    val subject: String,
    val difficulty: String,
    @SerialName("duration_minutes") val durationMinutes: Int,
    val tags: List<String> = emptyList(),
    @SerialName("origin_url") val originUrl: String? = null,
)

// ---------- 考试会话 ----------

@Serializable
data class OptionResponse(
    val key: String,
    val text: String,
)

@Serializable
data class PublicQuestionResponse(
    val id: String,
    val type: String,
    val stem: String,
    val options: List<OptionResponse> = emptyList(),
)

@Serializable
data class ExamSessionResponse(
    @SerialName("exam_id") val examId: String,
    @SerialName("paper_id") val paperId: String,
    @SerialName("paper_title") val paperTitle: String,
    val mode: String,
    val status: String,
    @SerialName("server_started_at") val serverStartedAt: String,
    @SerialName("server_end_at") val serverEndAt: String,
    @SerialName("server_remaining_seconds") val serverRemainingSeconds: Int,
    val questions: List<PublicQuestionResponse> = emptyList(),
    val answers: Map<String, String> = emptyMap(),
    @SerialName("next_sequence") val nextSequence: Int,
)

@Serializable
data class StartExamRequest(
    val mode: String = "exam",
)

@Serializable
data class SaveAnswerRequest(
    val sequence: Int,
    @SerialName("question_id") val questionId: String,
    val answer: String,
)

/** POST submit 的空 body；实例序列化为 `{}` */
@Serializable
class SubmitRequest

// ---------- 报告（M2-11 ReportOut） ----------

@Serializable
data class ReportItemResponse(
    @SerialName("question_id") val questionId: String,
    val sequence: Int,
    @SerialName("question_type") val questionType: String,
    val stem: String,
    val given: String,
    val expected: String,
    val correct: Boolean? = null,
    val score: Double? = null,
    @SerialName("max_score") val maxScore: Double,
    val explanation: String,
    val knowledge: List<String> = emptyList(),
    @SerialName("evidence_ids") val evidenceIds: List<String> = emptyList(),
    @SerialName("score_ratio") val scoreRatio: Double? = null,
)

@Serializable
data class ConceptScoreResponse(
    val concept: String,
    val correct: Int,
    val total: Int,
    val reviewed: Int,
    val ratio: Double? = null,
)

@Serializable
data class MistakeEntryResponse(
    @SerialName("question_id") val questionId: String,
    val stem: String,
    val given: String,
    val expected: String,
    val explanation: String,
    val diagnosis: String,
    val knowledge: List<String> = emptyList(),
    @SerialName("evidence_ids") val evidenceIds: List<String> = emptyList(),
    @SerialName("remediation_task_ids") val remediationTaskIds: List<Int> = emptyList(),
)

@Serializable
data class RemediationTaskResponse(
    val kind: String,
    val title: String,
    val detail: String,
    @SerialName("question_id") val questionId: String,
)

@Serializable
data class ReportResponse(
    @SerialName("exam_id") val examId: String,
    @SerialName("paper_title") val paperTitle: String,
    val mode: String,
    val score: Int,
    @SerialName("score_earned") val scoreEarned: Double,
    @SerialName("score_max") val scoreMax: Double,
    @SerialName("correct_count") val correctCount: Int,
    @SerialName("total_count") val totalCount: Int,
    @SerialName("reviewed_count") val reviewedCount: Int,
    val items: List<ReportItemResponse> = emptyList(),
    val concepts: List<ConceptScoreResponse> = emptyList(),
    val mistakes: List<MistakeEntryResponse> = emptyList(),
    @SerialName("remediation_tasks") val remediationTasks: List<RemediationTaskResponse> = emptyList(),
    @SerialName("evidence_ids") val evidenceIds: List<String> = emptyList(),
)

// ---------- 提交结果（SubmissionOut） ----------

@Serializable
data class AnglesResponse(
    val concept: String,
    val method: String,
    val mistake: String,
    val variant: String,
)

@Serializable
data class RubricCriterionResponse(
    val point: String,
    val achieved: Boolean? = null,
    @SerialName("evidence_id") val evidenceId: String? = null,
)

@Serializable
data class RubricResponse(
    @SerialName("rule_version") val ruleVersion: String,
    val criteria: List<RubricCriterionResponse> = emptyList(),
    @SerialName("score_ratio") val scoreRatio: Double? = null,
    val confidence: Double,
    @SerialName("judge_model") val judgeModel: String,
    @SerialName("prompt_hash") val promptHash: String = "",
)

@Serializable
data class GradedItemResponse(
    @SerialName("question_id") val questionId: String,
    val given: String,
    val correct: Boolean? = null,
    val expected: String,
    val explanation: String,
    val angles: AnglesResponse? = null,
    val rubric: RubricResponse? = null,
)

@Serializable
data class ReviewQuestionResponse(
    val id: String,
    val type: String,
    val stem: String,
    val options: List<OptionResponse> = emptyList(),
    val answer: String,
    val explanation: String,
    val angles: AnglesResponse? = null,
    val knowledge: List<String> = emptyList(),
)

@Serializable
data class SubmissionResponse(
    @SerialName("exam_id") val examId: String,
    @SerialName("paper_id") val paperId: String,
    @SerialName("paper_title") val paperTitle: String,
    val mode: String,
    val status: String,
    val score: Int,
    @SerialName("correct_count") val correctCount: Int,
    @SerialName("total_count") val totalCount: Int,
    @SerialName("duration_seconds") val durationSeconds: Int,
    @SerialName("rule_version") val ruleVersion: String,
    val items: List<GradedItemResponse> = emptyList(),
    val questions: List<ReviewQuestionResponse> = emptyList(),
)
