package com.ailearningos.app.data.remote

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * M12-03 语音域 DTO：providers / sessions / commands / intents / answers /
 * report / transcribe / synthesize / trace。
 * snake_case 与服务端 Pydantic 契约一一对应（services/api/app/api/routes/voice.py）；
 * 状态字符串保留服务端原词（8 状态 FSM），未知值不猜语义；
 * 服务端演进新增字段时靠 ignoreUnknownKeys 容忍。
 */

// ---------- providers（GET /api/v1/voice/providers） ----------

@Serializable
data class VoiceProviderViewResponse(
    val requested: String? = null,
    val provider: String,
    val fallback: Boolean = false,
)

@Serializable
data class VoiceProvidersResponse(
    @SerialName("voice_mode") val voiceMode: String,
    val asr: VoiceProviderViewResponse,
    val tts: VoiceProviderViewResponse,
    @SerialName("privacy_store_audio") val privacyStoreAudio: Boolean = false,
    @SerialName("privacy_send_context_to_cloud") val privacySendContextToCloud: Boolean = false,
)

// ---------- 会话（POST /sessions、GET /sessions?exam_id=、GET /sessions/{id}） ----------

@Serializable
data class VoiceSessionCreateRequest(
    @SerialName("exam_id") val examId: String,
)

@Serializable
data class VoiceSessionResponse(
    @SerialName("session_id") val sessionId: String,
    @SerialName("exam_id") val examId: String,
    val status: String,
    @SerialName("question_index") val questionIndex: Int,
    val revision: Int,
    @SerialName("created_at") val createdAt: String,
    @SerialName("updated_at") val updatedAt: String,
)

@Serializable
data class VoiceSessionsResponse(
    val items: List<VoiceSessionResponse> = emptyList(),
)

// ---------- 恢复（GET /sessions/{id}/resume） ----------

@Serializable
data class VoiceResumeQuestionResponse(
    val id: String,
    val type: String,
    val stem: String,
    val options: List<OptionResponse> = emptyList(),
)

@Serializable
data class VoiceResumeResponse(
    val session: VoiceSessionResponse,
    val question: VoiceResumeQuestionResponse? = null,
    @SerialName("committed_answer") val committedAnswer: String? = null,
    @SerialName("question_total") val questionTotal: Int,
)

// ---------- 命令（POST /sessions/{id}/commands） ----------

@Serializable
data class VoiceCommandRequest(
    val type: String,
    @SerialName("question_id") val questionId: String? = null,
    val answer: String? = null,
    val ambiguous: Boolean = false,
    @SerialName("expected_revision") val expectedRevision: Int? = null,
)

@Serializable
data class VoiceCommandResponse(
    @SerialName("applied_event") val appliedEvent: String,
    @SerialName("from_status") val fromStatus: String,
    val session: VoiceSessionResponse,
    @SerialName("clarified_question") val clarifiedQuestion: String? = null,
    @SerialName("question_total") val questionTotal: Int? = null,
)

// ---------- 意图（POST /sessions/{id}/intents） ----------

@Serializable
data class VoiceIntentRequest(
    val transcript: String,
    @SerialName("expected_revision") val expectedRevision: Int? = null,
)

@Serializable
data class VoiceIntentResponse(
    val transcript: String,
    val intent: String,
    val letter: String? = null,
    val ordinal: Int? = null,
    val ambiguous: Boolean = false,
    @SerialName("fsm_command") val fsmCommand: String? = null,
    @SerialName("fsm_applied") val fsmApplied: Boolean = false,
    @SerialName("applied_event") val appliedEvent: String? = null,
    val session: VoiceSessionResponse? = null,
    @SerialName("clarified_question") val clarifiedQuestion: String? = null,
)

// ---------- 规范化答案提交（POST /sessions/{id}/answers） ----------

@Serializable
data class VoiceAnswerRequest(
    val transcript: String,
    @SerialName("event_id") val eventId: String,
    @SerialName("question_id") val questionId: String? = null,
    @SerialName("normalized_answer") val normalizedAnswer: String? = null,
    val confidence: Double = 0.0,
)

@Serializable
data class VoiceAnswerResponse(
    @SerialName("event_id") val eventId: String,
    val idempotent: Boolean = false,
    val accepted: Boolean = false,
    @SerialName("normalized_answer") val normalizedAnswer: String? = null,
    val intent: String,
    @SerialName("question_id") val questionId: String,
    val session: VoiceSessionResponse? = null,
    @SerialName("clarified_question") val clarifiedQuestion: String? = null,
)

// ---------- 语音报告（GET /sessions/{id}/report） ----------

@Serializable
data class VoiceMistakeSummaryResponse(
    @SerialName("question_id") val questionId: String,
    @SerialName("stem_preview") val stemPreview: String,
    @SerialName("your_answer") val yourAnswer: String,
    @SerialName("correct_answer") val correctAnswer: String,
    val concepts: List<String> = emptyList(),
    val diagnosis: String,
)

@Serializable
data class VoiceRemediationSummaryResponse(
    val concept: String,
    val actions: List<String> = emptyList(),
    val detail: String,
    @SerialName("question_ids") val questionIds: List<String> = emptyList(),
)

@Serializable
data class VoiceReportResponse(
    @SerialName("session_id") val sessionId: String,
    @SerialName("exam_id") val examId: String,
    @SerialName("spoken_text") val spokenText: String,
    @SerialName("mistake_summary") val mistakeSummary: List<VoiceMistakeSummaryResponse> = emptyList(),
    @SerialName("remediation_summary") val remediationSummary: List<VoiceRemediationSummaryResponse> = emptyList(),
    @SerialName("written_report_url") val writtenReportUrl: String,
)

// ---------- 转写（POST /transcribe，multipart） ----------

@Serializable
data class VoiceTranscriptionResponse(
    val id: Int,
    val text: String,
    val confidence: Double,
    val provider: String,
    @SerialName("latency_ms") val latencyMs: Int,
    @SerialName("audio_bytes") val audioBytes: Int,
    @SerialName("audio_stored") val audioStored: Boolean = false,
    @SerialName("audio_object_key") val audioObjectKey: String? = null,
)

// ---------- 合成（POST /synthesize，请求 JSON、响应 WAV 字节） ----------

@Serializable
data class VoiceSynthesizeRequest(
    val text: String,
)

// ---------- 耗时观测（POST /trace） ----------

@Serializable
data class VoiceTraceRequest(
    val stage: String,
    @SerialName("duration_ms") val durationMs: Int,
    @SerialName("session_id") val sessionId: String? = null,
    @SerialName("exam_id") val examId: String? = null,
    @SerialName("question_id") val questionId: String? = null,
)

@Serializable
data class VoiceTraceResponse(
    val id: Int,
    val stage: String,
    @SerialName("duration_ms") val durationMs: Int,
    val source: String,
    @SerialName("session_id") val sessionId: String? = null,
    @SerialName("exam_id") val examId: String? = null,
    @SerialName("question_id") val questionId: String? = null,
    @SerialName("created_at") val createdAt: String,
)
