package com.ailearningos.app.testutil

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.AuthGateway
import com.ailearningos.app.data.ExamGateway
import com.ailearningos.app.data.SystemGateway
import com.ailearningos.app.data.local.ExamResumeStore
import com.ailearningos.app.data.local.SettingsStore
import com.ailearningos.app.data.local.TokenStore
import com.ailearningos.app.data.model.Angles
import com.ailearningos.app.data.model.ConceptScore
import com.ailearningos.app.data.model.ExamQuestion
import com.ailearningos.app.data.model.ExamReport
import com.ailearningos.app.data.model.ExamSession
import com.ailearningos.app.data.model.ExamSubmission
import com.ailearningos.app.data.model.GradedItem
import com.ailearningos.app.data.model.MistakeEntry
import com.ailearningos.app.data.model.PaperSummary
import com.ailearningos.app.data.model.PrivacySnapshot
import com.ailearningos.app.data.model.QuestionOption
import com.ailearningos.app.data.model.RemediationTask
import com.ailearningos.app.data.model.ReportItem
import com.ailearningos.app.data.model.ReviewQuestion
import com.ailearningos.app.data.model.UserProfile

/** 测试专用内存 token 存储（真实 Keystore 实现不上 JVM 测试） */
class FakeTokenStore : TokenStore {

    var stored: String? = null
        private set

    var saveCount = 0
        private set

    var clearCount = 0
        private set

    /** 供测试断言只读访问当前存储值 */
    fun peekStored(): String? = stored

    /** 测试播种已存在 token（模拟“上次登录遗留”） */
    fun seed(token: String?) {
        stored = token
    }

    var error: AppError? = null

    override suspend fun save(token: String) {
        error?.let { throw it }
        stored = token
        saveCount++
    }

    override suspend fun load(): String? {
        error?.let { throw it }
        return stored
    }

    override suspend fun clear() {
        error?.let { throw it }
        stored = null
        clearCount++
    }
}

/** 测试专用内存配置存储 */
class FakeSettingsStore(initial: String? = null) : SettingsStore {

    var stored: String? = initial

    val savedValues = mutableListOf<String>()

    override suspend fun loadBaseUrl(): String? = stored

    override suspend fun saveBaseUrl(value: String) {
        savedValues.add(value)
        stored = value
    }
}

/** 测试专用 AuthGateway：可控返回与异常，记录调用 */
class FakeAuthGateway : AuthGateway {

    var enabled = true

    var user: UserProfile? = null

    var loginResult: Result<UserProfile> = Result.success(UserProfile("u1", "alice", "learner"))

    var authEnabledError: AppError? = null

    var currentUserError: AppError? = null

    var logoutError: AppError? = null

    var loginCalls = 0
        private set

    var logoutCalls = 0
        private set

    var refreshCalls = 0
        private set

    var lastSubmittedPassword: String? = null
        private set

    var lastSubmittedUsername: String? = null
        private set

    override suspend fun authEnabled(): Boolean {
        refreshCalls++
        authEnabledError?.let { throw it }
        return enabled
    }

    override suspend fun login(username: String, password: String): UserProfile {
        loginCalls++
        lastSubmittedUsername = username
        lastSubmittedPassword = password
        return loginResult.getOrThrow()
    }

    override suspend fun currentUser(): UserProfile? {
        refreshCalls++
        currentUserError?.let { throw it }
        return user
    }

    override suspend fun logout() {
        logoutCalls++
        logoutError?.let { throw it }
    }
}

/** 测试专用 SystemGateway */
class FakeSystemGateway : SystemGateway {

    var healthOk = true

    var healthError: AppError? = null

    var privacyResult: Result<PrivacySnapshot> = Result.success(privacyFixture())

    var privacyCalls = 0
        private set

    override suspend fun healthOk(): Boolean {
        healthError?.let { throw it }
        return healthOk
    }

    override suspend fun privacy(): PrivacySnapshot {
        privacyCalls++
        return privacyResult.getOrThrow()
    }
}

fun userFixture(
    id: String = "u1",
    username: String = "alice",
    role: String = "learner",
) = UserProfile(id = id, username = username, role = role)

fun privacyFixture(
    modelRoute: String? = "local",
    voiceMode: String? = "local",
    searchMode: String? = "local",
    storesAudio: Boolean = false,
    sendsContextToCloud: Boolean = false,
) = PrivacySnapshot(
    modelRoute = modelRoute,
    voiceMode = voiceMode,
    searchMode = searchMode,
    storesAudio = storesAudio,
    sendsContextToCloud = sendsContextToCloud,
)

fun appError(kind: AppErrorKind) = AppError(kind)

// ====================================================================
// M12-02 考试域：fake 存储 / 迷你服务端 fake / fixtures
// ====================================================================

/** 测试专用内存恢复槽（真实 DataStore 实现不上 JVM 测试） */
class FakeExamResumeStore : ExamResumeStore {

    var stored: String? = null
        private set

    var saveCount = 0
        private set

    var clearCount = 0
        private set

    fun seed(examId: String?) {
        stored = examId
    }

    override suspend fun load(): String? = stored

    override suspend fun save(examId: String) {
        stored = examId
        saveCount++
    }

    override suspend fun clear() {
        stored = null
        clearCount++
    }
}

/** saveAnswer 调用记录（序号/题目/答案三元组，用于 append-only 断言） */
data class RecordedAnswer(val sequence: Int, val questionId: String, val answer: String)

/**
 * 迷你服务端 fake：内置 M2-06/M9 语义——
 * - append-only 事件：answers 投影取最新值，next_sequence 递增；
 * - 同 (sequence, questionId, answer) 幂等返回；
 * - 同序号不同内容 / 序号不连续 → CONFLICT(409)；
 * - 考试非 active → CONFLICT(409)；
 * - submit 幂等。
 * 需要注入故障时用 [failNextSaveAnswer] / [failNextExam]（一次性）。
 */
class FakeExamGateway : ExamGateway {

    var papersResult: Result<List<PaperSummary>> = Result.success(listOf(paperFixture()))

    var startExamResult: Result<ExamSession> = Result.success(examSessionFixture())

    /** 当前会话快照（getExam / saveAnswer 响应基线；测试可直接改写再观察恢复行为） */
    var session: ExamSession = examSessionFixture()

    var submitResult: Result<ExamSubmission> = Result.success(submissionFixture())

    var submissionResult: Result<ExamSubmission> = Result.success(submissionFixture())

    var reportResult: Result<ExamReport> = Result.success(reportFixture())

    /** 一次性错误注入：下一次 saveAnswer 抛出（之后恢复默认语义） */
    var failNextSaveAnswer: AppError? = null

    /** 持久行为覆盖：非 null 时 saveAnswer 完全按此执行（模拟服务端持续拒绝同一答案） */
    var saveAnswerBehavior: (suspend (sequence: Int, questionId: String, answer: String) -> ExamSession)? = null

    /** 一次性错误注入：下一次 exam() 抛出 */
    var failNextExam: AppError? = null

    val startExamCalls = mutableListOf<String>()

    val examCalls = mutableListOf<String>()

    val savedAnswers = mutableListOf<RecordedAnswer>()

    val submitCalls = mutableListOf<String>()

    val submissionCalls = mutableListOf<String>()

    val reportCalls = mutableListOf<String>()

    /** 内部事件表（sequence -> (questionId, answer)），模拟服务端 answer_events */
    private val events = linkedMapOf<Int, Pair<String, String>>()

    override suspend fun papers(): List<PaperSummary> = papersResult.getOrThrow()

    override suspend fun startExam(paperId: String): ExamSession {
        startExamCalls.add(paperId)
        return startExamResult.getOrThrow().also { session = it }
    }

    override suspend fun exam(examId: String): ExamSession {
        examCalls.add(examId)
        failNextExam?.let { error ->
            failNextExam = null
            throw error
        }
        return session.copy(examId = examId)
    }

    override suspend fun saveAnswer(
        examId: String,
        sequence: Int,
        questionId: String,
        answer: String,
    ): ExamSession {
        savedAnswers.add(RecordedAnswer(sequence, questionId, answer))
        saveAnswerBehavior?.let { behavior -> return behavior(sequence, questionId, answer) }
        failNextSaveAnswer?.let { error ->
            failNextSaveAnswer = null
            throw error
        }
        if (!session.isActive) {
            throw AppError(AppErrorKind.CONFLICT)
        }
        val existing = events[sequence]
        if (existing != null) {
            // 幂等：同序号同题同答案直接返回当前状态
            if (existing.first == questionId && existing.second == answer) {
                return session.snapshot()
            }
            throw AppError(AppErrorKind.CONFLICT)
        }
        if (sequence != session.nextSequence) {
            throw AppError(AppErrorKind.CONFLICT)
        }
        if (session.questions.none { it.id == questionId }) {
            throw AppError(AppErrorKind.NOT_FOUND)
        }
        events[sequence] = questionId to answer
        session = session.copy(
            examId = examId,
            answers = session.answers + (questionId to answer),
            nextSequence = sequence + 1,
        )
        return session.snapshot()
    }

    override suspend fun submit(examId: String): ExamSubmission {
        submitCalls.add(examId)
        val submission = submitResult.getOrThrow()
        // 提交后考试非 active（幂等重复提交仍返回同一结果）
        session = session.copy(status = "submitted")
        return submission
    }

    override suspend fun submission(examId: String): ExamSubmission {
        submissionCalls.add(examId)
        return submissionResult.getOrThrow()
    }

    override suspend fun report(examId: String): ExamReport {
        reportCalls.add(examId)
        return reportResult.getOrThrow()
    }

    private fun ExamSession.snapshot(): ExamSession = copy()
}

// ---------- fixtures（显式假值；对应服务端 seed/契约形态，非真实数据） ----------

fun paperFixture(
    id: String = "functions-basics",
    title: String = "函数与导数基础",
    subject: String = "math",
    difficulty: String = "core",
    durationMinutes: Int = 6,
) = PaperSummary(
    id = id,
    title = title,
    subtitle = "围绕单调性、切线和极值的自编检查卷",
    source = "AI Learning OS seed",
    university = null,
    year = 2026,
    subject = subject,
    difficulty = difficulty,
    durationMinutes = durationMinutes,
    tags = listOf("函数", "导数"),
    originUrl = null,
)

fun mcqQuestionFixture(
    id: String = "q1",
    type: String = "mcq",
    stem: String = "函数 f(x)=x^3-3x 在 (0, +∞) 上的单调递增区间是？",
) = ExamQuestion(
    id = id,
    type = type,
    stem = stem,
    options = listOf(
        QuestionOption("A", "(0,1)"),
        QuestionOption("B", "(1,+∞)"),
        QuestionOption("C", "(0,+∞)"),
        QuestionOption("D", "不存在"),
    ),
)

fun shortQuestionFixture(
    id: String = "q2",
    type: String = "short_answer",
    stem: String = "写出 f(x)=x^3-3x 的极小值点。",
) = ExamQuestion(id = id, type = type, stem = stem, options = emptyList())

fun examSessionFixture(
    examId: String = "exam-fixture-0001",
    paperId: String = "functions-basics",
    paperTitle: String = "函数与导数基础",
    status: String = "active",
    questions: List<ExamQuestion> = listOf(mcqQuestionFixture(), shortQuestionFixture()),
    answers: Map<String, String> = emptyMap(),
    nextSequence: Int = 1,
    serverEndAt: String = "2026-09-06T22:00:00+00:00",
    serverRemainingSeconds: Int = 360,
) = ExamSession(
    examId = examId,
    paperId = paperId,
    paperTitle = paperTitle,
    mode = "exam",
    status = status,
    serverEndAt = serverEndAt,
    serverRemainingSeconds = serverRemainingSeconds,
    questions = questions,
    answers = answers,
    nextSequence = nextSequence,
)

fun submissionFixture(
    examId: String = "exam-fixture-0001",
    status: String = "submitted",
) = ExamSubmission(
    examId = examId,
    paperId = "functions-basics",
    paperTitle = "函数与导数基础",
    mode = "exam",
    status = status,
    score = 100,
    correctCount = 1,
    totalCount = 2,
    durationSeconds = 240,
    ruleVersion = "v1",
    items = listOf(
        GradedItem(
            questionId = "q1",
            given = "B",
            correct = true,
            expected = "B",
            explanation = "f'(x)=3x^2-3，在 x>1 时为正。",
            angles = Angles("导数符号决定单调性。", "求 f'(x) 再解不等式。", "漏掉区间限制。", "改为 (-∞,0)。"),
        ),
        GradedItem(
            questionId = "q2",
            given = "x=1",
            correct = false,
            expected = "x=1 处取得极小值 -2",
            explanation = "极小值点需给出坐标或函数值。",
            angles = Angles("极值判定。", "解 f'(x)=0。", "只给 x 忘给值。", "求极大值点。"),
        ),
    ),
    questions = listOf(
        ReviewQuestion(
            id = "q1",
            type = "mcq",
            stem = "题干一",
            options = emptyList(),
            answer = "B",
            explanation = "解析一",
            angles = Angles("概念", "方法", "错因", "变式"),
            knowledge = listOf("导数"),
        ),
        ReviewQuestion(
            id = "q2",
            type = "short_answer",
            stem = "题干二",
            options = emptyList(),
            answer = "x=1 处取得极小值 -2",
            explanation = "解析二",
            angles = Angles("概念", "方法", "错因", "变式"),
            knowledge = listOf("极值"),
        ),
    ),
)

fun reportFixture(
    examId: String = "exam-fixture-0001",
) = ExamReport(
    examId = examId,
    paperTitle = "函数与导数基础",
    mode = "exam",
    score = 100,
    scoreEarned = 60.0,
    scoreMax = 100.0,
    correctCount = 1,
    totalCount = 2,
    reviewedCount = 0,
    items = listOf(
        ReportItem(
            questionId = "q1",
            sequence = 1,
            questionType = "mcq",
            stem = "题干一",
            given = "B",
            expected = "B",
            correct = true,
            score = 50.0,
            maxScore = 50.0,
            explanation = "解析一",
            knowledge = listOf("导数", "单调性"),
            evidenceIds = listOf("ev-1"),
            scoreRatio = 1.0,
        ),
        ReportItem(
            questionId = "q2",
            sequence = 2,
            questionType = "short_answer",
            stem = "题干二",
            given = "x=1",
            expected = "x=1 处取得极小值 -2",
            correct = false,
            score = 10.0,
            maxScore = 50.0,
            explanation = "解析二",
            knowledge = listOf("极值"),
            evidenceIds = emptyList(),
            scoreRatio = 0.2,
        ),
    ),
    concepts = listOf(
        ConceptScore(concept = "导数", correct = 1, total = 1, reviewed = 0, ratio = 1.0),
        ConceptScore(concept = "极值", correct = 0, total = 1, reviewed = 0, ratio = 0.0),
    ),
    mistakes = listOf(
        MistakeEntry(
            questionId = "q2",
            stem = "题干二",
            given = "x=1",
            expected = "x=1 处取得极小值 -2",
            explanation = "解析二",
            diagnosis = "只给 x 忘给函数值",
            knowledge = listOf("极值"),
            evidenceIds = emptyList(),
            remediationTaskIds = listOf(1),
        ),
    ),
    remediationTasks = listOf(
        RemediationTask(kind = "review_concept", title = "复习「极值」", detail = "结合变式练习巩固。", questionId = "q2"),
    ),
    evidenceIds = listOf("ev-1"),
)
