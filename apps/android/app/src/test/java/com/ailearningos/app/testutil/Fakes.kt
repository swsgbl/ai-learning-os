package com.ailearningos.app.testutil

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.AuthGateway
import com.ailearningos.app.data.ExamGateway
import com.ailearningos.app.data.GovernanceGateway
import com.ailearningos.app.data.SearchGateway
import com.ailearningos.app.data.SystemGateway
import com.ailearningos.app.data.VoiceGateway
import com.ailearningos.app.data.local.ExamResumeStore
import com.ailearningos.app.data.local.SettingsStore
import com.ailearningos.app.data.local.TokenStore
import com.ailearningos.app.data.local.VoiceResumeSlot
import com.ailearningos.app.data.local.VoiceResumeStore
import com.ailearningos.app.data.model.Angles
import com.ailearningos.app.data.model.ConceptScore
import com.ailearningos.app.data.model.ExamQuestion
import com.ailearningos.app.data.model.ExamReport
import com.ailearningos.app.data.model.ExamSession
import com.ailearningos.app.data.model.ExamSubmission
import com.ailearningos.app.data.model.GovernanceAuditEntry
import com.ailearningos.app.data.model.GradedItem
import com.ailearningos.app.data.model.MistakeEntry
import com.ailearningos.app.data.model.OpsSnapshot
import com.ailearningos.app.data.model.PaperSummary
import com.ailearningos.app.data.model.PendingReviewDrafts
import com.ailearningos.app.data.model.PrivacySnapshot
import com.ailearningos.app.data.model.QuestionOption
import com.ailearningos.app.data.model.RemediationTask
import com.ailearningos.app.data.model.ReleaseVersion
import com.ailearningos.app.data.model.ReportItem
import com.ailearningos.app.data.model.SearchOutcome
import com.ailearningos.app.data.model.SearchPlan
import com.ailearningos.app.data.model.SearchPlanItem
import com.ailearningos.app.data.model.SearchPlanSlots
import com.ailearningos.app.data.model.SearchProviderEntry
import com.ailearningos.app.data.model.SearchRecord
import com.ailearningos.app.data.model.SearchResultItem
import com.ailearningos.app.data.model.SearchSkipped
import com.ailearningos.app.data.model.ReviewQuestion
import com.ailearningos.app.data.model.UserProfile
import com.ailearningos.app.data.model.VoiceAnswerResult
import com.ailearningos.app.data.model.VoiceCommandResult
import com.ailearningos.app.data.model.VoiceIntentResult
import com.ailearningos.app.data.model.VoiceMistakeSummary
import com.ailearningos.app.data.model.VoiceProviderView
import com.ailearningos.app.data.model.VoiceProviders
import com.ailearningos.app.data.model.VoiceQuestion
import com.ailearningos.app.data.model.VoiceRemediationSummary
import com.ailearningos.app.data.model.VoiceReport
import com.ailearningos.app.data.model.VoiceResume
import com.ailearningos.app.data.model.VoiceSession
import com.ailearningos.app.data.model.VoiceTranscription
import com.ailearningos.app.voice.AudioCaptureEngine
import com.ailearningos.app.voice.AudioEngineException
import com.ailearningos.app.voice.AudioPlaybackEngine
import com.ailearningos.app.voice.WavCodec

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

// ====================================================================
// M12-03 语音域：fake 存储 / 迷你服务端 fake / 音频引擎 fake / fixtures
// ====================================================================

/** 测试专用内存语音恢复槽（真实 DataStore 实现不上 JVM 测试） */
class FakeVoiceResumeStore : VoiceResumeStore {

    var stored: VoiceResumeSlot? = null
        private set

    var saveCount = 0
        private set

    var clearCount = 0
        private set

    fun seed(slot: VoiceResumeSlot?) {
        stored = slot
    }

    override suspend fun load(): VoiceResumeSlot? = stored

    override suspend fun save(slot: VoiceResumeSlot) {
        stored = slot
        saveCount++
    }

    override suspend fun clear() {
        stored = null
        clearCount++
    }
}

/** 录音指令记录（fake 引擎观察面） */
class FakeAudioCaptureEngine : AudioCaptureEngine {

    var startError: AudioEngineException? = null

    var stopBytes: ByteArray? = null

    var stopError: AudioEngineException? = null

    var startCalls = 0
        private set

    var stopCalls = 0
        private set

    private var activeFlag = false

    override val isActive: Boolean get() = activeFlag

    override suspend fun start() {
        startError?.let { throw it }
        startCalls++
        activeFlag = true
    }

    override suspend fun stop(): ByteArray {
        stopCalls++
        activeFlag = false
        stopError?.let { throw it }
        return stopBytes ?: WavCodec.wrapPcm16(byteArrayOf(1, 2, 3, 4))
    }
}

class FakeAudioPlaybackEngine : AudioPlaybackEngine {

    val played = mutableListOf<ByteArray>()

    var playError: Exception? = null

    var stopCalls = 0
        private set

    override suspend fun play(wav: ByteArray) {
        playError?.let { throw it }
        played.add(wav)
    }

    override fun stop() {
        stopCalls++
    }
}

/**
 * 迷你语音服务端 fake：内置 M4-03 FSM 语义（迁移表与服务端 voice_session_fsm 一致）——
 * - 非法迁移 → CONFLICT(409)；未知会话 → NOT_FOUND；
 * - answer_proposed 需 question_id+answer（缺 → BAD_RESPONSE/422 同形）并落 answers；
 * - NEXT_QUESTION 后 start_reading 推进题号；越界 → CONFLICT；
 * - report_ready 透出题数；end/report_ready 进 REPORT_READY 终态；
 * - resume 只读投影当前题/权威答案；终态 → CONFLICT。
 * intent 默认按「选 X」绑定当前题提交；复杂场景用 [intentHandler] 覆盖。
 */
class FakeVoiceGateway(
    private val questions: List<VoiceQuestion> = listOf(
        voiceQuestionFixture(),
        voiceQuestionFixture(id = "q2", stem = "函数 f(x)=x^2 在 (0,+∞) 上是？"),
    ),
) : VoiceGateway {

    var providersResult: Result<VoiceProviders> = Result.success(voiceProvidersFixture())

    var transcribeResult: Result<VoiceTranscription> =
        Result.success(VoiceTranscription(text = "选 B", confidence = 0.9, provider = "fake"))

    var synthesizeResult: Result<ByteArray> = Result.success(WavCodec.wrapPcm16(ByteArray(160)))

    var reportResult: Result<VoiceReport> = Result.success(voiceReportFixture())

    var traceError: AppError? = null

    /** 一次性错误注入：下一次 resume 抛出（之后恢复默认语义） */
    var failNextResume: AppError? = null

    /** 意图覆盖钩子：返回 null 走默认「选 X」逻辑 */
    var intentHandler: (suspend (transcript: String) -> VoiceIntentResult?)? = null

    val createdSessions = mutableListOf<String>()

    val commands = mutableListOf<Pair<String, String>>()

    val intents = mutableListOf<String>()

    val submittedAnswers = mutableListOf<String>()

    val traces = mutableListOf<Triple<String, Long, String?>>()

    val synthesizeTexts = mutableListOf<String>()

    /** sessionId -> 可变服务端状态（会话 + exam answers 投影） */
    val sessions = linkedMapOf<String, Pair<VoiceSession, MutableMap<String, String>>>()

    private var nextSessionNumber = 0

    override suspend fun providers(): VoiceProviders = providersResult.getOrThrow()

    override suspend fun createSession(examId: String): VoiceSession {
        createdSessions.add(examId)
        val sessionId = "vs-fixture-%04d".format(++nextSessionNumber)
        val session = voiceSessionFixture(sessionId = sessionId, examId = examId)
        sessions[sessionId] = session to linkedMapOf()
        return session
    }

    override suspend fun sessions(examId: String): List<VoiceSession> =
        sessions.values.map { it.first }.filter { it.examId == examId }

    override suspend fun session(sessionId: String): VoiceSession {
        val record = sessions[sessionId] ?: throw AppError(AppErrorKind.NOT_FOUND)
        return record.first
    }

    override suspend fun resume(sessionId: String): VoiceResume {
        failNextResume?.let { error ->
            failNextResume = null
            throw error
        }
        val record = sessions[sessionId] ?: throw AppError(AppErrorKind.NOT_FOUND)
        val (session, answers) = record
        if (session.isReportReady) throw AppError(AppErrorKind.CONFLICT)
        val index = session.questionIndex
        val question = questions.getOrNull(index)
        return VoiceResume(
            session = session,
            question = question,
            committedAnswer = question?.let { answers[it.id] },
            questionTotal = questions.size,
        )
    }

    override suspend fun command(
        sessionId: String,
        type: String,
        questionId: String?,
        answer: String?,
    ): VoiceCommandResult {
        commands.add(sessionId to type)
        val record = sessions.entries.find { it.key == sessionId } ?: throw AppError(AppErrorKind.NOT_FOUND)
        val (session, answers) = record.value
        if (type !in events) throw AppError(AppErrorKind.BAD_RESPONSE)
        val target = try {
            transition(session.status, type)
        } catch (cause: IllegalArgumentException) {
            throw AppError(AppErrorKind.CONFLICT)
        }

        var clarifiedQuestion: String? = null
        var questionIndex = session.questionIndex
        var questionTotal: Int? = null

        when (type) {
            "answer_proposed", "answer_clarify" -> {
                if (questionId == null || answer == null) throw AppError(AppErrorKind.BAD_RESPONSE)
                if (questions.none { it.id == questionId }) throw AppError(AppErrorKind.BAD_RESPONSE)
                if (type == "answer_proposed") {
                    answers[questionId] = answer
                } else {
                    clarifiedQuestion = "没有听清，请再说一遍具体选项。"
                }
            }
            "start_reading" -> if (session.status == "NEXT_QUESTION") {
                questionIndex = session.questionIndex + 1
                if (questionIndex >= questions.size) throw AppError(AppErrorKind.CONFLICT)
            }
            "report_ready" -> questionTotal = questions.size
        }

        val updated = session.copy(
            status = target,
            questionIndex = questionIndex,
            revision = session.revision + 1,
        )
        record.setValue(updated to answers)
        return VoiceCommandResult(
            appliedEvent = type,
            fromStatus = session.status,
            session = updated,
            clarifiedQuestion = clarifiedQuestion,
            questionTotal = questionTotal,
        )
    }

    override suspend fun intent(sessionId: String, transcript: String): VoiceIntentResult {
        intents.add(transcript)
        val record = sessions[sessionId] ?: throw AppError(AppErrorKind.NOT_FOUND)
        intentHandler?.invoke(transcript)?.let { return it }
        val letter = Regex("选\\s*([A-Z])").find(transcript)?.groupValues?.get(1)
        return if (letter == null) {
            VoiceIntentResult(
                transcript = transcript,
                intent = "unknown",
                letter = null,
                ordinal = null,
                ambiguous = false,
                fsmCommand = null,
                fsmApplied = false,
                appliedEvent = null,
                session = null,
                clarifiedQuestion = null,
            )
        } else {
            val question = questions[record.first.questionIndex]
            val out = command(sessionId, "answer_proposed", question.id, letter)
            VoiceIntentResult(
                transcript = transcript,
                intent = "choose",
                letter = letter,
                ordinal = null,
                ambiguous = false,
                fsmCommand = "answer_proposed",
                fsmApplied = true,
                appliedEvent = out.appliedEvent,
                session = out.session,
                clarifiedQuestion = null,
            )
        }
    }

    override suspend fun submitAnswer(
        sessionId: String,
        transcript: String,
        eventId: String,
    ): VoiceAnswerResult {
        submittedAnswers.add(eventId)
        val record = sessions[sessionId] ?: throw AppError(AppErrorKind.NOT_FOUND)
        val (session, _) = record
        val question = questions[session.questionIndex]
        val letter = Regex("选\\s*([A-Z])").find(transcript)?.groupValues?.get(1)
        return if (letter == null) {
            VoiceAnswerResult(
                eventId = eventId,
                idempotent = false,
                accepted = false,
                normalizedAnswer = null,
                intent = "unknown",
                questionId = question.id,
                session = session,
                clarifiedQuestion = "答案无法识别。",
            )
        } else {
            val out = command(sessionId, "answer_proposed", question.id, letter)
            VoiceAnswerResult(
                eventId = eventId,
                idempotent = false,
                accepted = true,
                normalizedAnswer = letter,
                intent = "choose",
                questionId = question.id,
                session = out.session,
                clarifiedQuestion = null,
            )
        }
    }

    override suspend fun report(sessionId: String): VoiceReport {
        val record = sessions[sessionId] ?: throw AppError(AppErrorKind.NOT_FOUND)
        if (!record.first.isReportReady) throw AppError(AppErrorKind.CONFLICT)
        return reportResult.getOrThrow()
    }

    override suspend fun transcribe(wav: ByteArray): VoiceTranscription = transcribeResult.getOrThrow()

    override suspend fun synthesize(text: String): ByteArray {
        synthesizeTexts.add(text)
        return synthesizeResult.getOrThrow()
    }

    override suspend fun reportTrace(
        stage: String,
        durationMillis: Long,
        sessionId: String?,
        examId: String?,
        questionId: String?,
    ) {
        traceError?.let { throw it }
        traces.add(Triple(stage, durationMillis, questionId))
    }

    // ---------- FSM 迁移表（与服务端 voice_session_fsm._TRANSITIONS 一致） ----------

    private val transitions: Map<String, Map<String, String>> = mapOf(
        "SESSION_READY" to mapOf(
            "start_reading" to "READING_QUESTION",
            "end" to "REPORT_READY",
            "pause" to "SESSION_READY",
            "resume" to "SESSION_READY",
        ),
        "READING_QUESTION" to mapOf(
            "question_read" to "READING_OPTIONS",
            "repeat_question" to "READING_QUESTION",
            "slow_down" to "READING_QUESTION",
            "barge_in" to "WAITING_ANSWER",
            "pause" to "READING_QUESTION",
            "resume" to "READING_QUESTION",
            "end" to "REPORT_READY",
        ),
        "READING_OPTIONS" to mapOf(
            "options_read" to "WAITING_ANSWER",
            "repeat_options" to "READING_OPTIONS",
            "slow_down" to "READING_OPTIONS",
            "barge_in" to "WAITING_ANSWER",
            "pause" to "READING_OPTIONS",
            "resume" to "READING_OPTIONS",
            "end" to "REPORT_READY",
        ),
        "WAITING_ANSWER" to mapOf(
            "answer_proposed" to "ANSWER_COMMITTED",
            "answer_clarify" to "CLARIFYING",
            "skip" to "NEXT_QUESTION",
            "repeat_question" to "WAITING_ANSWER",
            "repeat_options" to "WAITING_ANSWER",
            "slow_down" to "WAITING_ANSWER",
            "barge_in" to "WAITING_ANSWER",
            "pause" to "WAITING_ANSWER",
            "resume" to "WAITING_ANSWER",
            "end" to "REPORT_READY",
        ),
        "CLARIFYING" to mapOf(
            "answer_proposed" to "ANSWER_COMMITTED",
            "answer_clarify" to "CLARIFYING",
            "skip" to "NEXT_QUESTION",
            "barge_in" to "CLARIFYING",
            "pause" to "CLARIFYING",
            "resume" to "CLARIFYING",
            "end" to "REPORT_READY",
        ),
        "ANSWER_COMMITTED" to mapOf(
            "commit_confirmed" to "NEXT_QUESTION",
            "answer_proposed" to "ANSWER_COMMITTED",
            "answer_clarify" to "CLARIFYING",
            "skip" to "NEXT_QUESTION",
            "repeat_question" to "ANSWER_COMMITTED",
            "repeat_options" to "ANSWER_COMMITTED",
            "slow_down" to "ANSWER_COMMITTED",
            "pause" to "ANSWER_COMMITTED",
            "resume" to "ANSWER_COMMITTED",
            "end" to "REPORT_READY",
        ),
        "NEXT_QUESTION" to mapOf(
            "start_reading" to "READING_QUESTION",
            "report_ready" to "REPORT_READY",
            "end" to "REPORT_READY",
            "pause" to "NEXT_QUESTION",
            "resume" to "NEXT_QUESTION",
        ),
        "REPORT_READY" to emptyMap(),
    )

    private val events = setOf(
        "start_reading", "question_read", "options_read", "answer_proposed", "answer_clarify",
        "commit_confirmed", "skip", "repeat_question", "repeat_options", "slow_down",
        "end", "pause", "resume", "barge_in", "report_ready",
    )

    private fun transition(state: String, event: String): String {
        val target = transitions[state]?.get(event)
            ?: throw IllegalArgumentException("语音会话状态 $state 不接受事件 $event")
        return target
    }
}

// ---------- 语音 fixtures（显式假值；对应服务端 voice 契约形态，非真实数据） ----------

fun voiceQuestionFixture(
    id: String = "q1",
    stem: String = "函数 f(x)=x^3-3x 在 (0, +∞) 上的单调递增区间是？",
) = VoiceQuestion(
    id = id,
    type = "mcq",
    stem = stem,
    options = listOf(
        QuestionOption("A", "(0,1)"),
        QuestionOption("B", "(1,+∞)"),
        QuestionOption("C", "(0,+∞)"),
        QuestionOption("D", "不存在"),
    ),
)

fun voiceSessionFixture(
    sessionId: String = "vs-fixture-0001",
    examId: String = "exam-fixture-0001",
    status: String = "SESSION_READY",
    questionIndex: Int = 0,
    revision: Int = 1,
) = VoiceSession(
    sessionId = sessionId,
    examId = examId,
    status = status,
    questionIndex = questionIndex,
    revision = revision,
    createdAt = "2026-09-06T22:00:00+00:00",
    updatedAt = "2026-09-06T22:00:00+00:00",
)

fun voiceProvidersFixture(
    voiceMode: String = "local",
) = VoiceProviders(
    voiceMode = voiceMode,
    asr = VoiceProviderView(requested = null, provider = "fake", fallback = false),
    tts = VoiceProviderView(requested = null, provider = "tone", fallback = false),
    privacyStoreAudio = false,
    privacySendContextToCloud = false,
)

fun voiceReportFixture(
    sessionId: String = "vs-fixture-0001",
    examId: String = "exam-fixture-0001",
) = VoiceReport(
    sessionId = sessionId,
    examId = examId,
    spokenText = "考试完成！本次答对 1 题（共 2 题），得分 50 分，共有 1 道错题。",
    mistakes = listOf(
        VoiceMistakeSummary(
            questionId = "q2",
            stemPreview = "函数 f(x)=x^2 在 (0,+∞) 上是？…",
            yourAnswer = "A",
            correctAnswer = "B",
            concepts = listOf("函数"),
            diagnosis = "概念不清",
        ),
    ),
    remediations = listOf(
        VoiceRemediationSummary(
            concept = "函数",
            actions = listOf("review_concept"),
            detail = "复习概念「函数」后完成变式练习",
            questionIds = listOf("q2"),
        ),
    ),
    writtenReportUrl = "/api/v1/exams/$examId/report",
)

// ====================================================================
// M12-04 搜索域：迷你服务端 fake / fixtures
// ====================================================================

/** search 调用记录（查询词/源过滤/limit 三元组） */
data class RecordedSearch(val query: String, val providers: List<String>?, val limit: Int)

/**
 * 迷你搜索服务端 fake：默认返回成功 fixture；结果/异常经 Result 字段可控，
 * 时序类用例（顺序竞争/门控响应）用 *Handler 覆盖钩子（返回 null 走默认）。
 */
class FakeSearchGateway : SearchGateway {

    var providersResult: Result<List<SearchProviderEntry>> = Result.success(searchProvidersFixture())

    var planResult: Result<SearchPlan> = Result.success(searchPlanFixture())

    var searchResult: Result<SearchOutcome> = Result.success(searchOutcomeFixture())

    var recordResult: Result<SearchRecord> = Result.success(searchRecordFixture())

    var providersHandler: (suspend () -> List<SearchProviderEntry>)? = null

    var planHandler: (suspend (query: String) -> SearchPlan?)? = null

    var searchHandler: (suspend (query: String) -> SearchOutcome?)? = null

    var recordHandler: (suspend (queryId: Long) -> SearchRecord?)? = null

    val planCalls = mutableListOf<String>()

    val searchCalls = mutableListOf<RecordedSearch>()

    val recordCalls = mutableListOf<Long>()

    override suspend fun providers(): List<SearchProviderEntry> =
        providersHandler?.invoke() ?: providersResult.getOrThrow()

    override suspend fun plan(query: String, providers: List<String>?): SearchPlan {
        planCalls.add(query)
        return planHandler?.invoke(query) ?: planResult.getOrThrow()
    }

    override suspend fun search(query: String, providers: List<String>?, limit: Int): SearchOutcome {
        searchCalls.add(RecordedSearch(query, providers, limit))
        return searchHandler?.invoke(query) ?: searchResult.getOrThrow()
    }

    override suspend fun record(queryId: Long): SearchRecord {
        recordCalls.add(queryId)
        return recordHandler?.invoke(queryId) ?: recordResult.getOrThrow()
    }
}

// ---------- 搜索 fixtures（显式假值；对应服务端 search 契约形态，非真实数据） ----------

fun searchProviderFixture(
    name: String = "local-corpus",
    kind: String = "local-corpus",
    enabled: Boolean = true,
    unavailableReason: String? = null,
) = SearchProviderEntry(name = name, kind = kind, enabled = enabled, unavailableReason = unavailableReason)

fun searchProvidersFixture() = listOf(
    searchProviderFixture(),
    searchProviderFixture(
        name = "cloud-web",
        kind = "web",
        enabled = false,
        unavailableReason = "SEARCH_MODE=local：检索路由本地语料，cloud-web 不出站",
    ),
)

fun searchPlanFixture(
    query: String = "高等数学 选择题",
) = SearchPlan(
    query = query,
    slots = SearchPlanSlots(
        subject = null,
        school = null,
        year = "2024",
        course = "高等数学",
        questionType = "选择题",
        publicity = null,
    ),
    items = listOf(
        SearchPlanItem(provider = "local-corpus", query = "高等数学 选择题 2024", enabled = true, unavailableReason = null),
        SearchPlanItem(
            provider = "cloud-web",
            query = "高等数学 选择题 2024",
            enabled = false,
            unavailableReason = "SEARCH_MODE=local：检索路由本地语料，cloud-web 不出站",
        ),
    ),
)

fun searchOutcomeFixture(
    queryId: Long = 7,
    query: String = "正弦定理",
    resultCount: Int = 1,
) = SearchOutcome(
    queryId = queryId,
    query = query,
    providersRequested = listOf("local-corpus", "cloud-web"),
    results = listOf(
        SearchResultItem(
            title = "正弦定理的内容",
            url = "https://example.invalid/doc/1",
            snippet = "a/sinA=b/sinB，正弦定理的内容",
            source = "local-corpus",
            provider = "local-corpus",
            authority = null,
            rankReason = "本地语料命中：关键词重叠",
        ),
    ),
    skipped = listOf(
        SearchSkipped(provider = "cloud-web", reason = "SEARCH_MODE=local：检索路由本地语料，cloud-web 不出站"),
    ),
    resultCount = resultCount,
    durationMs = 12,
)

fun searchRecordFixture(
    id: Long = 7,
    query: String = "正弦定理",
) = SearchRecord(
    id = id,
    query = query,
    providersRequested = listOf("local-corpus", "cloud-web"),
    skipped = listOf(
        SearchSkipped(provider = "cloud-web", reason = "SEARCH_MODE=local：检索路由本地语料，cloud-web 不出站"),
    ),
    resultCount = 1,
    durationMs = 12,
    results = listOf(
        SearchResultItem(
            title = "正弦定理的内容",
            url = "https://example.invalid/doc/1",
            snippet = "a/sinA=b/sinB，正弦定理的内容",
            source = "local-corpus",
            provider = "local-corpus",
            authority = null,
            rankReason = "本地语料命中：关键词重叠",
        ),
    ),
    createdAt = "2026-09-07T02:00:00+00:00",
)

// ====================================================================
// M12-05 治理域：迷你服务端 fake / fixtures
// ====================================================================

/**
 * 迷你治理服务端 fake：默认返回成功 fixture；结果/异常经 Result 字段可控，
 * 时序类用例（顺序刷新/错误恢复）用 *Handler 覆盖钩子（返回 null 走默认）。
 * auditCalls 记录每次传入的 limit（size 即调用次数）。
 */
class FakeGovernanceGateway : GovernanceGateway {

    var versionResult: Result<ReleaseVersion> = Result.success(governanceVersionFixture())

    var opsSnapshotResult: Result<OpsSnapshot> = Result.success(opsSnapshotFixture())

    /** 默认两条：一条带 actor 的用户动作，一条 actor 为 null 的系统动作 */
    var auditResult: Result<List<GovernanceAuditEntry>> = Result.success(
        listOf(
            governanceAuditEntryFixture(),
            governanceAuditEntryFixture(id = 2, actorId = null, actorUsername = null, action = "worker.tick", targetType = "job", targetId = "job-1"),
        ),
    )

    /** 版本覆盖钩子：返回 null 走 [versionResult] 默认 */
    var versionHandler: (suspend () -> ReleaseVersion?)? = null

    /** 快照覆盖钩子：返回 null 走 [opsSnapshotResult] 默认 */
    var opsSnapshotHandler: (suspend () -> OpsSnapshot?)? = null

    /** 审计覆盖钩子：返回 null 走 [auditResult] 默认 */
    var auditHandler: (suspend (limit: Int) -> List<GovernanceAuditEntry>?)? = null

    var versionCalls = 0
        private set

    var opsSnapshotCalls = 0
        private set

    /** auditEntries 每次调用传入的 limit（size 即调用次数） */
    val auditCalls = mutableListOf<Int>()

    override suspend fun version(): ReleaseVersion {
        versionCalls++
        return versionHandler?.invoke() ?: versionResult.getOrThrow()
    }

    override suspend fun opsSnapshot(): OpsSnapshot {
        opsSnapshotCalls++
        return opsSnapshotHandler?.invoke() ?: opsSnapshotResult.getOrThrow()
    }

    override suspend fun auditEntries(limit: Int): List<GovernanceAuditEntry> {
        auditCalls.add(limit)
        return auditHandler?.invoke(limit) ?: auditResult.getOrThrow()
    }
}

// ---------- 治理 fixtures（显式假值；对应服务端 version/ops-snapshot/audit 契约形态，非真实数据） ----------

fun governanceVersionFixture(
    version: String = "0.14.0",
    gitCommit: String = "1a2b3c4",
    alembicCurrent: String = "a1b2c3d4e5f6",
    alembicHead: String = "a1b2c3d4e5f6",
) = ReleaseVersion(
    version = version,
    gitCommit = gitCommit,
    alembicCurrent = alembicCurrent,
    alembicHead = alembicHead,
)

fun opsSnapshotFixture(
    generatedAt: String = "2026-09-07T02:00:00+00:00",
    databaseBackend: String = "postgresql",
    usersByRole: Map<String, Int> = mapOf("admin" to 1, "learner" to 2),
    papersTotal: Int = 3,
    resourcesByParseStatus: Map<String, Int> = mapOf("parsed" to 2, "pending" to 1),
    parseJobsByStatus: Map<String, Int> = mapOf("succeeded" to 1),
    pendingReviewDrafts: PendingReviewDrafts = PendingReviewDrafts(
        courseImport = 0,
        courseGeneration = 0,
        paperQuestion = 0,
        variantQuestion = 0,
    ),
    voiceSessionsByStatus: Map<String, Int> = mapOf("REPORT_READY" to 1),
    searchQueriesTotal: Int = 7,
    auditEntriesTotal: Int = 12,
    workerRunning: Boolean = true,
) = OpsSnapshot(
    generatedAt = generatedAt,
    databaseBackend = databaseBackend,
    usersByRole = usersByRole,
    papersTotal = papersTotal,
    resourcesByParseStatus = resourcesByParseStatus,
    parseJobsByStatus = parseJobsByStatus,
    pendingReviewDrafts = pendingReviewDrafts,
    voiceSessionsByStatus = voiceSessionsByStatus,
    searchQueriesTotal = searchQueriesTotal,
    auditEntriesTotal = auditEntriesTotal,
    workerRunning = workerRunning,
)

fun governanceAuditEntryFixture(
    id: Long = 1,
    actorId: String? = "u1",
    actorUsername: String? = "alice",
    action: String = "resource.create",
    targetType: String = "resource",
    targetId: String = "res-1",
    requestId: String = "req-fixture-0001",
    createdAt: String = "2026-09-07T01:00:00+00:00",
) = GovernanceAuditEntry(
    id = id,
    actorId = actorId,
    actorUsername = actorUsername,
    action = action,
    targetType = targetType,
    targetId = targetId,
    requestId = requestId,
    createdAt = createdAt,
)
