package com.ailearningos.app.data

import com.ailearningos.app.data.model.QuestionOption
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
import com.ailearningos.app.data.remote.VoiceAnswerRequest
import com.ailearningos.app.data.remote.VoiceCommandRequest
import com.ailearningos.app.data.remote.VoiceIntentRequest
import com.ailearningos.app.data.remote.VoiceSessionCreateRequest
import com.ailearningos.app.data.remote.VoiceSynthesizeRequest
import com.ailearningos.app.data.remote.VoiceTraceRequest
import java.util.UUID
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody

/**
 * 语音业务面（M12-03）。UI/ViewModel 依赖此接口；测试用 fake 实现。
 *
 * 服务端语义（不得在客户端削弱）：
 * - 状态/答案/判分以服务端为唯一真相源——commands/intents 响应即新状态，
 *   客户端不推演 FSM、不缓存答案、不虚构语音结果；
 * - answers 的 event_id 幂等键由客户端生成（UUID）；重放返回既有结果；
 * - synthesize 返回服务端合成的 WAV 字节（播放成败由音频引擎如实反馈）；
 * - trace 是观测通道：调用失败由调用方决定吞掉，绝不影响业务主流程。
 */
interface VoiceGateway {
    suspend fun providers(): VoiceProviders

    suspend fun createSession(examId: String): VoiceSession

    suspend fun sessions(examId: String): List<VoiceSession>

    suspend fun session(sessionId: String): VoiceSession

    suspend fun resume(sessionId: String): VoiceResume

    suspend fun command(sessionId: String, type: String, questionId: String? = null, answer: String? = null): VoiceCommandResult

    suspend fun intent(sessionId: String, transcript: String): VoiceIntentResult

    suspend fun submitAnswer(sessionId: String, transcript: String, eventId: String = UUID.randomUUID().toString()): VoiceAnswerResult

    suspend fun report(sessionId: String): VoiceReport

    suspend fun transcribe(wav: ByteArray): VoiceTranscription

    suspend fun synthesize(text: String): ByteArray

    suspend fun reportTrace(stage: String, durationMillis: Long, sessionId: String? = null, examId: String? = null, questionId: String? = null)
}

class VoiceRepository(
    private val apiProvider: ApiProvider,
) : VoiceGateway {

    override suspend fun providers(): VoiceProviders = withRemoteError {
        apiProvider.get().voiceProviders().toDomain()
    }

    override suspend fun createSession(examId: String): VoiceSession = withRemoteError {
        apiProvider.get().createVoiceSession(VoiceSessionCreateRequest(examId)).toDomain()
    }

    override suspend fun sessions(examId: String): List<VoiceSession> = withRemoteError {
        apiProvider.get().listVoiceSessions(examId).items.map { it.toDomain() }
    }

    override suspend fun session(sessionId: String): VoiceSession = withRemoteError {
        apiProvider.get().voiceSession(sessionId).toDomain()
    }

    override suspend fun resume(sessionId: String): VoiceResume = withRemoteError {
        apiProvider.get().voiceResume(sessionId).toDomain()
    }

    override suspend fun command(
        sessionId: String,
        type: String,
        questionId: String?,
        answer: String?,
    ): VoiceCommandResult = withRemoteError {
        apiProvider.get().voiceCommand(
            sessionId,
            VoiceCommandRequest(type = type, questionId = questionId, answer = answer),
        ).toDomain()
    }

    override suspend fun intent(sessionId: String, transcript: String): VoiceIntentResult = withRemoteError {
        apiProvider.get().voiceIntent(sessionId, VoiceIntentRequest(transcript = transcript)).toDomain()
    }

    override suspend fun submitAnswer(
        sessionId: String,
        transcript: String,
        eventId: String,
    ): VoiceAnswerResult = withRemoteError {
        apiProvider.get().voiceAnswer(sessionId, VoiceAnswerRequest(transcript = transcript, eventId = eventId)).toDomain()
    }

    override suspend fun report(sessionId: String): VoiceReport = withRemoteError {
        apiProvider.get().voiceReport(sessionId).toDomain()
    }

    override suspend fun transcribe(wav: ByteArray): VoiceTranscription = withRemoteError {
        val part = MultipartBody.Part.createFormData(
            name = "audio",
            filename = "clip.wav",
            body = wav.toRequestBody("audio/wav".toMediaType()),
        )
        apiProvider.get().voiceTranscribe(part).let {
            VoiceTranscription(text = it.text, confidence = it.confidence, provider = it.provider)
        }
    }

    override suspend fun synthesize(text: String): ByteArray = withRemoteError {
        apiProvider.get().voiceSynthesize(VoiceSynthesizeRequest(text = text)).bytes()
    }

    override suspend fun reportTrace(
        stage: String,
        durationMillis: Long,
        sessionId: String?,
        examId: String?,
        questionId: String?,
    ): Unit = withRemoteError {
        apiProvider.get().voiceTrace(
            VoiceTraceRequest(
                stage = stage,
                durationMs = durationMillis.coerceIn(1, TRACE_MAX_DURATION_MS).toInt(),
                sessionId = sessionId,
                examId = examId,
                questionId = questionId,
            ),
        )
        Unit
    }

    private companion object {
        /** 服务端 duration_ms 上限 10 分钟（超出会被 422 拒绝），客户端截断在界内 */
        const val TRACE_MAX_DURATION_MS = 600_000L
    }
}

// ---------- DTO → 领域 ----------

internal fun com.ailearningos.app.data.remote.VoiceProviderViewResponse.toDomain() = VoiceProviderView(
    requested = requested,
    provider = provider,
    fallback = fallback,
)

internal fun com.ailearningos.app.data.remote.VoiceProvidersResponse.toDomain() = VoiceProviders(
    voiceMode = voiceMode,
    asr = asr.toDomain(),
    tts = tts.toDomain(),
    privacyStoreAudio = privacyStoreAudio,
    privacySendContextToCloud = privacySendContextToCloud,
)

internal fun com.ailearningos.app.data.remote.VoiceSessionResponse.toDomain() = VoiceSession(
    sessionId = sessionId,
    examId = examId,
    status = status,
    questionIndex = questionIndex,
    revision = revision,
    createdAt = createdAt,
    updatedAt = updatedAt,
)

internal fun com.ailearningos.app.data.remote.VoiceResumeQuestionResponse.toDomain() = VoiceQuestion(
    id = id,
    type = type,
    stem = stem,
    options = options.map { QuestionOption(key = it.key, text = it.text) },
)

internal fun com.ailearningos.app.data.remote.VoiceResumeResponse.toDomain() = VoiceResume(
    session = session.toDomain(),
    question = question?.toDomain(),
    committedAnswer = committedAnswer,
    questionTotal = questionTotal,
)

internal fun com.ailearningos.app.data.remote.VoiceCommandResponse.toDomain() = VoiceCommandResult(
    appliedEvent = appliedEvent,
    fromStatus = fromStatus,
    session = session.toDomain(),
    clarifiedQuestion = clarifiedQuestion,
    questionTotal = questionTotal,
)

internal fun com.ailearningos.app.data.remote.VoiceIntentResponse.toDomain() = VoiceIntentResult(
    transcript = transcript,
    intent = intent,
    letter = letter,
    ordinal = ordinal,
    ambiguous = ambiguous,
    fsmCommand = fsmCommand,
    fsmApplied = fsmApplied,
    appliedEvent = appliedEvent,
    session = session?.toDomain(),
    clarifiedQuestion = clarifiedQuestion,
)

internal fun com.ailearningos.app.data.remote.VoiceAnswerResponse.toDomain() = VoiceAnswerResult(
    eventId = eventId,
    idempotent = idempotent,
    accepted = accepted,
    normalizedAnswer = normalizedAnswer,
    intent = intent,
    questionId = questionId,
    session = session?.toDomain(),
    clarifiedQuestion = clarifiedQuestion,
)

internal fun com.ailearningos.app.data.remote.VoiceMistakeSummaryResponse.toDomain() = VoiceMistakeSummary(
    questionId = questionId,
    stemPreview = stemPreview,
    yourAnswer = yourAnswer,
    correctAnswer = correctAnswer,
    concepts = concepts,
    diagnosis = diagnosis,
)

internal fun com.ailearningos.app.data.remote.VoiceRemediationSummaryResponse.toDomain() = VoiceRemediationSummary(
    concept = concept,
    actions = actions,
    detail = detail,
    questionIds = questionIds,
)

internal fun com.ailearningos.app.data.remote.VoiceReportResponse.toDomain() = VoiceReport(
    sessionId = sessionId,
    examId = examId,
    spokenText = spokenText,
    mistakes = mistakeSummary.map { it.toDomain() },
    remediations = remediationSummary.map { it.toDomain() },
    writtenReportUrl = writtenReportUrl,
)
