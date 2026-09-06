package com.ailearningos.app.data.model

/**
 * M12-02 考试域领域视图。
 *
 * 服务端语义保留（不改写、不本地判定）：
 * - [ExamSession.nextSequence] 由服务端返回，是唯一权威序号来源；
 * - [ExamSession.answers] 是服务端已落库的最新答案（append-only 事件的投影）；
 * - 状态字符串保留服务端原词（created/active/submitted/expired/report_ready），
 *   未知值不猜测，按非进行中处理。
 */

data class PaperSummary(
    val id: String,
    val title: String,
    val subtitle: String,
    val source: String,
    val university: String?,
    val year: Int?,
    val subject: String,
    val difficulty: String,
    val durationMinutes: Int,
    val tags: List<String>,
    val originUrl: String?,
)

data class QuestionOption(
    val key: String,
    val text: String,
)

data class ExamQuestion(
    val id: String,
    val type: String,
    val stem: String,
    val options: List<QuestionOption>,
) {
    /** 多选：选项以 checkbox 录入，提交 {"option_indices":[...]} JSON */
    val isMultipleSelect: Boolean get() = type == "multiple_select"

    /** 有选项且非多选：单选点选即提交（mcq / true_false / tf 等） */
    val isSingleChoice: Boolean get() = options.isNotEmpty() && !isMultipleSelect

    /** 无选项：文本录入（short_answer / essay / numeric / math / …，未知题型同路） */
    val isTextInput: Boolean get() = options.isEmpty()
}

data class ExamSession(
    val examId: String,
    val paperId: String,
    val paperTitle: String,
    val mode: String,
    val status: String,
    val serverEndAt: String,
    val serverRemainingSeconds: Int,
    val questions: List<ExamQuestion>,
    val answers: Map<String, String>,
    val nextSequence: Int,
) {
    val isActive: Boolean get() = status == "active"
    val isSubmitted: Boolean get() = status == "submitted" || status == "report_ready"
    val isExpired: Boolean get() = status == "expired"
}

data class ReportItem(
    val questionId: String,
    val sequence: Int,
    val questionType: String,
    val stem: String,
    val given: String,
    val expected: String,
    val correct: Boolean?,
    val score: Double?,
    val maxScore: Double,
    val explanation: String,
    val knowledge: List<String>,
    val evidenceIds: List<String>,
    val scoreRatio: Double?,
)

data class ConceptScore(
    val concept: String,
    val correct: Int,
    val total: Int,
    val reviewed: Int,
    val ratio: Double?,
)

data class MistakeEntry(
    val questionId: String,
    val stem: String,
    val given: String,
    val expected: String,
    val explanation: String,
    val diagnosis: String,
    val knowledge: List<String>,
    val evidenceIds: List<String>,
    val remediationTaskIds: List<Int>,
)

data class RemediationTask(
    val kind: String,
    val title: String,
    val detail: String,
    val questionId: String,
)

data class ExamReport(
    val examId: String,
    val paperTitle: String,
    val mode: String,
    val score: Int,
    val scoreEarned: Double,
    val scoreMax: Double,
    val correctCount: Int,
    val totalCount: Int,
    val reviewedCount: Int,
    val items: List<ReportItem>,
    val concepts: List<ConceptScore>,
    val mistakes: List<MistakeEntry>,
    val remediationTasks: List<RemediationTask>,
    val evidenceIds: List<String>,
)

data class Angles(
    val concept: String,
    val method: String,
    val mistake: String,
    val variant: String,
)

data class GradedItem(
    val questionId: String,
    val given: String,
    val correct: Boolean?,
    val expected: String,
    val explanation: String,
    val angles: Angles?,
)

data class ReviewQuestion(
    val id: String,
    val type: String,
    val stem: String,
    val options: List<QuestionOption>,
    val answer: String,
    val explanation: String,
    val angles: Angles?,
    val knowledge: List<String>,
)

data class ExamSubmission(
    val examId: String,
    val paperId: String,
    val paperTitle: String,
    val mode: String,
    val status: String,
    val score: Int,
    val correctCount: Int,
    val totalCount: Int,
    val durationSeconds: Int,
    val ruleVersion: String,
    val items: List<GradedItem>,
    val questions: List<ReviewQuestion>,
)
