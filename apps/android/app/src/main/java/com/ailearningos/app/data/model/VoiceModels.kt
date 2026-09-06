package com.ailearningos.app.data.model

/**
 * M12-03 语音域领域视图。
 *
 * 服务端语义保留（不改写、不本地判定）：
 * - [VoiceSession.status] 保留服务端 8 状态原词
 *   （SESSION_READY / READING_QUESTION / READING_OPTIONS / WAITING_ANSWER /
 *   CLARIFYING / ANSWER_COMMITTED / NEXT_QUESTION / REPORT_READY），
 *   未知值不猜测，按非播报、非作答处理；
 * - [VoiceSession.committedAnswer] 是服务端已落库的权威答案（exam answer_events 投影）；
 * - 语音报告只投影不判定——分数/错题/补救全部来自服务端判分结果。
 */

/** provider 解析结果（不虚报：fallback=true 表示云端未配置已降级本地） */
data class VoiceProviderView(
    val requested: String?,
    val provider: String,
    val fallback: Boolean,
)

data class VoiceProviders(
    val voiceMode: String,
    val asr: VoiceProviderView,
    val tts: VoiceProviderView,
    val privacyStoreAudio: Boolean,
    val privacySendContextToCloud: Boolean,
)

data class VoiceSession(
    val sessionId: String,
    val examId: String,
    val status: String,
    val questionIndex: Int,
    val revision: Int,
    val createdAt: String,
    val updatedAt: String,
) {
    val isReportReady: Boolean get() = status == "REPORT_READY"
}

/** 当前题公开字段（resume 端点投影；不含 answer/explanation——考试模式不泄露） */
data class VoiceQuestion(
    val id: String,
    val type: String,
    val stem: String,
    val options: List<QuestionOption>,
)

data class VoiceResume(
    val session: VoiceSession,
    val question: VoiceQuestion?,
    val committedAnswer: String?,
    val questionTotal: Int,
)

data class VoiceCommandResult(
    val appliedEvent: String,
    val fromStatus: String,
    val session: VoiceSession,
    val clarifiedQuestion: String?,
    val questionTotal: Int?,
)

data class VoiceIntentResult(
    val transcript: String,
    val intent: String,
    val letter: String?,
    val ordinal: Int?,
    val ambiguous: Boolean,
    val fsmCommand: String?,
    val fsmApplied: Boolean,
    val appliedEvent: String?,
    val session: VoiceSession?,
    val clarifiedQuestion: String?,
)

data class VoiceAnswerResult(
    val eventId: String,
    val idempotent: Boolean,
    val accepted: Boolean,
    val normalizedAnswer: String?,
    val intent: String,
    val questionId: String,
    val session: VoiceSession?,
    val clarifiedQuestion: String?,
)

data class VoiceTranscription(
    val text: String,
    val confidence: Double,
    val provider: String,
)

data class VoiceMistakeSummary(
    val questionId: String,
    val stemPreview: String,
    val yourAnswer: String,
    val correctAnswer: String,
    val concepts: List<String>,
    val diagnosis: String,
)

data class VoiceRemediationSummary(
    val concept: String,
    val actions: List<String>,
    val detail: String,
    val questionIds: List<String>,
)

/** 语音报告播报投影（REPORT_READY + 考试已提交判分后可获取） */
data class VoiceReport(
    val sessionId: String,
    val examId: String,
    val spokenText: String,
    val mistakes: List<VoiceMistakeSummary>,
    val remediations: List<VoiceRemediationSummary>,
    val writtenReportUrl: String,
)
