package com.ailearningos.app.data

import com.ailearningos.app.data.model.Angles
import com.ailearningos.app.data.model.ConceptScore
import com.ailearningos.app.data.model.ExamQuestion
import com.ailearningos.app.data.model.ExamReport
import com.ailearningos.app.data.model.ExamSession
import com.ailearningos.app.data.model.ExamSubmission
import com.ailearningos.app.data.model.GradedItem
import com.ailearningos.app.data.model.MistakeEntry
import com.ailearningos.app.data.model.PaperSummary
import com.ailearningos.app.data.model.RemediationTask
import com.ailearningos.app.data.model.ReportItem
import com.ailearningos.app.data.model.ReviewQuestion
import com.ailearningos.app.data.model.QuestionOption
import com.ailearningos.app.data.remote.AnglesResponse
import com.ailearningos.app.data.remote.ConceptScoreResponse
import com.ailearningos.app.data.remote.ExamSessionResponse
import com.ailearningos.app.data.remote.GradedItemResponse
import com.ailearningos.app.data.remote.MistakeEntryResponse
import com.ailearningos.app.data.remote.OptionResponse
import com.ailearningos.app.data.remote.PaperResponse
import com.ailearningos.app.data.remote.PublicQuestionResponse
import com.ailearningos.app.data.remote.RemediationTaskResponse
import com.ailearningos.app.data.remote.ReportItemResponse
import com.ailearningos.app.data.remote.ReportResponse
import com.ailearningos.app.data.remote.ReviewQuestionResponse
import com.ailearningos.app.data.remote.SaveAnswerRequest
import com.ailearningos.app.data.remote.StartExamRequest
import com.ailearningos.app.data.remote.SubmissionResponse
import com.ailearningos.app.data.remote.SubmitRequest

/**
 * 考试业务面（M12-02）。UI/ViewModel 依赖此接口；测试用 fake 实现。
 *
 * 服务端语义（不得在客户端削弱）：
 * - 答案 append-only：每次作答是一个新事件，序号由服务端返回的
 *   next_sequence 指定，客户端只在成功响应后推进本地序号；
 * - 404/409/401 经 [asAppError] 区分（NOT_FOUND / 语义冲突 / UNAUTHORIZED）；
 * - submit 幂等：重复提交返回既有结果。
 */
interface ExamGateway {
    suspend fun papers(): List<PaperSummary>

    suspend fun startExam(paperId: String): ExamSession

    suspend fun exam(examId: String): ExamSession

    suspend fun saveAnswer(examId: String, sequence: Int, questionId: String, answer: String): ExamSession

    suspend fun submit(examId: String): ExamSubmission

    suspend fun submission(examId: String): ExamSubmission

    suspend fun report(examId: String): ExamReport
}

class ExamRepository(
    private val apiProvider: ApiProvider,
) : ExamGateway {

    override suspend fun papers(): List<PaperSummary> = withRemoteError {
        apiProvider.get().papers().map { it.toDomain() }
    }

    override suspend fun startExam(paperId: String): ExamSession = withRemoteError {
        apiProvider.get().startExam(paperId, StartExamRequest(mode = "exam")).toDomain()
    }

    override suspend fun exam(examId: String): ExamSession = withRemoteError {
        apiProvider.get().getExam(examId).toDomain()
    }

    override suspend fun saveAnswer(
        examId: String,
        sequence: Int,
        questionId: String,
        answer: String,
    ): ExamSession = withRemoteError {
        apiProvider.get().saveAnswer(
            examId,
            SaveAnswerRequest(sequence = sequence, questionId = questionId, answer = answer),
        ).toDomain()
    }

    override suspend fun submit(examId: String): ExamSubmission = withRemoteError {
        apiProvider.get().submitExam(examId, SubmitRequest()).toDomain()
    }

    override suspend fun submission(examId: String): ExamSubmission = withRemoteError {
        apiProvider.get().submission(examId).toDomain()
    }

    override suspend fun report(examId: String): ExamReport = withRemoteError {
        apiProvider.get().report(examId).toDomain()
    }
}

// ---------- DTO → 领域 ----------

internal fun PaperResponse.toDomain() = PaperSummary(
    id = id,
    title = title,
    subtitle = subtitle,
    source = source,
    university = university,
    year = year,
    subject = subject,
    difficulty = difficulty,
    durationMinutes = durationMinutes,
    tags = tags,
    originUrl = originUrl,
)

internal fun OptionResponse.toDomain() = QuestionOption(key = key, text = text)

internal fun PublicQuestionResponse.toDomain() = ExamQuestion(
    id = id,
    type = type,
    stem = stem,
    options = options.map { it.toDomain() },
)

internal fun ExamSessionResponse.toDomain() = ExamSession(
    examId = examId,
    paperId = paperId,
    paperTitle = paperTitle,
    mode = mode,
    status = status,
    serverEndAt = serverEndAt,
    serverRemainingSeconds = serverRemainingSeconds,
    questions = questions.map { it.toDomain() },
    answers = answers,
    nextSequence = nextSequence,
)

internal fun ReportItemResponse.toDomain() = ReportItem(
    questionId = questionId,
    sequence = sequence,
    questionType = questionType,
    stem = stem,
    given = given,
    expected = expected,
    correct = correct,
    score = score,
    maxScore = maxScore,
    explanation = explanation,
    knowledge = knowledge,
    evidenceIds = evidenceIds,
    scoreRatio = scoreRatio,
)

internal fun ConceptScoreResponse.toDomain() = ConceptScore(
    concept = concept,
    correct = correct,
    total = total,
    reviewed = reviewed,
    ratio = ratio,
)

internal fun MistakeEntryResponse.toDomain() = MistakeEntry(
    questionId = questionId,
    stem = stem,
    given = given,
    expected = expected,
    explanation = explanation,
    diagnosis = diagnosis,
    knowledge = knowledge,
    evidenceIds = evidenceIds,
    remediationTaskIds = remediationTaskIds,
)

internal fun RemediationTaskResponse.toDomain() = RemediationTask(
    kind = kind,
    title = title,
    detail = detail,
    questionId = questionId,
)

internal fun ReportResponse.toDomain() = ExamReport(
    examId = examId,
    paperTitle = paperTitle,
    mode = mode,
    score = score,
    scoreEarned = scoreEarned,
    scoreMax = scoreMax,
    correctCount = correctCount,
    totalCount = totalCount,
    reviewedCount = reviewedCount,
    items = items.map { it.toDomain() },
    concepts = concepts.map { it.toDomain() },
    mistakes = mistakes.map { it.toDomain() },
    remediationTasks = remediationTasks.map { it.toDomain() },
    evidenceIds = evidenceIds,
)

internal fun AnglesResponse?.toDomainOrNull(): Angles? = this?.let {
    Angles(concept = it.concept, method = it.method, mistake = it.mistake, variant = it.variant)
}

internal fun GradedItemResponse.toDomain() = GradedItem(
    questionId = questionId,
    given = given,
    correct = correct,
    expected = expected,
    explanation = explanation,
    angles = angles.toDomainOrNull(),
)

internal fun ReviewQuestionResponse.toDomain() = ReviewQuestion(
    id = id,
    type = type,
    stem = stem,
    options = options.map { it.toDomain() },
    answer = answer,
    explanation = explanation,
    angles = angles.toDomainOrNull(),
    knowledge = knowledge,
)

internal fun SubmissionResponse.toDomain() = ExamSubmission(
    examId = examId,
    paperId = paperId,
    paperTitle = paperTitle,
    mode = mode,
    status = status,
    score = score,
    correctCount = correctCount,
    totalCount = totalCount,
    durationSeconds = durationSeconds,
    ruleVersion = ruleVersion,
    items = items.map { it.toDomain() },
    questions = questions.map { it.toDomain() },
)
