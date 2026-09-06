package com.ailearningos.app.ui.voice

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.ExamGateway
import com.ailearningos.app.data.VoiceGateway
import com.ailearningos.app.data.asAppError
import com.ailearningos.app.data.local.VoiceResumeSlot
import com.ailearningos.app.data.local.VoiceResumeStore
import com.ailearningos.app.data.model.VoiceIntentResult
import com.ailearningos.app.data.model.VoiceProviders
import com.ailearningos.app.data.model.VoiceQuestion
import com.ailearningos.app.data.model.VoiceReport
import com.ailearningos.app.data.model.VoiceResume
import com.ailearningos.app.data.model.VoiceSession
import com.ailearningos.app.voice.AudioCaptureEngine
import com.ailearningos.app.voice.AudioEngineException
import com.ailearningos.app.voice.AudioPlaybackEngine
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** 语音页入口：tab（选择/恢复）、按试卷开新、按会话恢复 */
sealed interface VoiceEntry {
    data object Picker : VoiceEntry
    data class Start(val paperId: String) : VoiceEntry
    data class Resume(val sessionId: String) : VoiceEntry
}

/** 语音陪练进行中视图（服务端权威状态的镜像 + 客户端交互位） */
data class VoiceLive(
    val sessionId: String,
    val examId: String,
    val status: String,
    val questionIndex: Int,
    val questionTotal: Int,
    val question: VoiceQuestion?,
    val committedAnswer: String?,
    val restored: Boolean = false,
    val clarifiedQuestion: String? = null,
    val notice: String? = null,
    val busy: Boolean = false,
    val playing: Boolean = false,
    val paused: Boolean = false,
    val recording: Boolean = false,
    val transcribing: Boolean = false,
    val inputText: String = "",
    val micGranted: Boolean = false,
    val micNotice: String? = null,
    val error: String? = null,
    val ttsError: String? = null,
) {
    val isReportReady: Boolean get() = status == "REPORT_READY"
    val questionPosition: Int get() = questionIndex + 1
    val isAnnouncing: Boolean get() = status == "READING_QUESTION" || status == "READING_OPTIONS"
    val acceptsAnswer: Boolean
        get() = status == "WAITING_ANSWER" || status == "CLARIFYING" || status == "ANSWER_COMMITTED"
}

data class VoicePickerState(
    val loading: Boolean = true,
    val providers: VoiceProviders? = null,
    val resumeSlot: VoiceResumeSlot? = null,
    val error: String? = null,
)

sealed interface VoiceUiState {
    data object Loading : VoiceUiState
    data class Picker(val value: VoicePickerState) : VoiceUiState
    data class Unavailable(val message: String) : VoiceUiState
    data class Live(val value: VoiceLive) : VoiceUiState
    data class Report(val value: VoiceReport) : VoiceUiState
}

/**
 * 语音陪练状态机（M12-03）。服务端权威语义：
 * - 状态/题面/已提交答案全部来自服务端响应（commands/intents/resume），
 *   客户端不推演 FSM、不缓存答案、不虚构语音结果；
 * - 读题→读选项→倾听由「播报完成」驱动链式推进；播报失败停在当前状态
 *   并显示可重试错误，绝不静默推进；
 * - 409（如播报期提交）先向服务端对齐再提示；404 清本地恢复槽；
 * - 本地只保留 session_id/exam_id 恢复提示（[VoiceResumeStore]），
 *   进程重启后凭 resume 端点对齐；语音音频、transcript、判分绝不落本地。
 */
class VoiceViewModel(
    private val voice: VoiceGateway,
    private val exam: ExamGateway,
    private val resumeStore: VoiceResumeStore,
    private val capture: AudioCaptureEngine,
    private val playback: AudioPlaybackEngine,
    private val entry: VoiceEntry,
    private val clock: () -> Long = System::currentTimeMillis,
) : ViewModel() {

    private val _state = MutableStateFlow<VoiceUiState>(VoiceUiState.Loading)
    val state: StateFlow<VoiceUiState> = _state.asStateFlow()

    // ---------- 服务端权威状态镜像（只在成功响应后更新） ----------

    private var sessionId: String? = null
    private var examId: String? = null
    private var lastSession: VoiceSession? = null

    /** 已自动播报过的 (状态:题号)，防止 resume 对齐/命令自环重复开播 */
    private var announcedKey: String? = null

    private var beginJob: Job? = null
    private var commandJob: Job? = null
    private var playbackJob: Job? = null
    private var recordingJob: Job? = null

    init {
        begin()
    }

    /** 进入/重试进入语音页（重入会取消在途 begin） */
    fun begin() {
        beginJob?.cancel()
        beginJob = viewModelScope.launch {
            _state.value = VoiceUiState.Loading
            try {
                when (val source = entry) {
                    is VoiceEntry.Picker -> openPicker()
                    is VoiceEntry.Start -> startOrResume(source.paperId)
                    is VoiceEntry.Resume -> resumeFrom(source.sessionId)
                }
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (cause: AppError) {
                _state.value = VoiceUiState.Unavailable(cause.userMessage)
            } catch (_: Throwable) {
                _state.value = VoiceUiState.Unavailable(AppError(AppErrorKind.UNEXPECTED).userMessage)
            }
        }
    }

    // ---------- 进入流程 ----------

    private suspend fun openPicker() {
        val slot = resumeStore.load()
        val picker = try {
            VoicePickerState(loading = false, providers = voice.providers(), resumeSlot = slot)
        } catch (cause: AppError) {
            VoicePickerState(loading = false, resumeSlot = slot, error = cause.userMessage)
        }
        _state.value = VoiceUiState.Picker(picker)
    }

    /** Picker 态重试 provider 概要 */
    fun refreshPicker() {
        begin()
    }

    /** 点击试卷开语音陪练：本地若有恢复槽先尝试续接（能恢复才续），否则开新 */
    private suspend fun startOrResume(paperId: String) {
        val slot = resumeStore.load()
        if (slot != null) {
            when (tryResume(slot.sessionId)) {
                ResumeOutcome.Adopted -> return
                ResumeOutcome.Stale -> Unit // 槽已清理，按点击意图开新
                ResumeOutcome.Failed -> return // 网络等服务端失败：不盲目开新考（避免重复考试），如实报错
            }
        }
        openNew(paperId)
    }

    /** 从恢复卡进入：一切以服务端 resume 结果为准 */
    private suspend fun resumeFrom(targetSessionId: String) {
        when (tryResume(targetSessionId)) {
            ResumeOutcome.Adopted -> Unit
            ResumeOutcome.Stale -> _state.value = VoiceUiState.Unavailable("语音会话已收口或不存在，请从学习页重新开始")
            ResumeOutcome.Failed -> Unit
        }
    }

    private enum class ResumeOutcome { Adopted, Stale, Failed }

    private suspend fun tryResume(targetSessionId: String): ResumeOutcome = try {
        adoptResume(voice.resume(targetSessionId), restored = true)
        ResumeOutcome.Adopted
    } catch (cause: AppError) {
        when (cause.kind) {
            // 恢复提示失效（服务端已无此会话）或会话已终态（无可恢复状态）：清槽
            AppErrorKind.NOT_FOUND, AppErrorKind.CONFLICT -> {
                resumeStore.clear()
                ResumeOutcome.Stale
            }
            else -> {
                _state.value = VoiceUiState.Unavailable(cause.userMessage)
                ResumeOutcome.Failed
            }
        }
    }

    private suspend fun openNew(paperId: String) {
        // 语音会话必须挂在进行中的考试上：先开考（服务端权威），再建语音会话
        val examSession = exam.startExam(paperId)
        val session = voice.createSession(examSession.examId)
        resumeStore.save(VoiceResumeSlot(sessionId = session.sessionId, examId = session.examId))
        adoptResume(voice.resume(session.sessionId), restored = false)
    }

    // ---------- 会话采纳（服务端快照 → UI 状态） ----------

    private suspend fun adoptResume(resumed: VoiceResume, restored: Boolean) {
        sessionId = resumed.session.sessionId
        examId = resumed.session.examId
        lastSession = resumed.session
        announcedKey = null
        _state.value = VoiceUiState.Live(
            VoiceLive(
                sessionId = resumed.session.sessionId,
                examId = resumed.session.examId,
                status = resumed.session.status,
                questionIndex = resumed.session.questionIndex,
                questionTotal = resumed.questionTotal,
                question = resumed.question,
                committedAnswer = resumed.committedAnswer,
                restored = restored,
                micGranted = currentLive()?.micGranted ?: false,
            ),
        )
        maybeAutoAnnounce(clarifiedQuestion = null)
    }

    /**
     * 命令/意图成功后采纳新会话状态；新题或题面缺失时拉 resume 对齐题面。
     */
    private suspend fun adoptSessionAfterCommand(session: VoiceSession, clarifiedQuestion: String?) {
        val previous = lastSession
        lastSession = session
        val indexChanged = previous == null || previous.questionIndex != session.questionIndex
        updateLive {
            it.copy(
                status = session.status,
                questionIndex = session.questionIndex,
                clarifiedQuestion = clarifiedQuestion,
                // 换题后旧答案/澄清不再相关；同题覆盖提交保留旧值等 resume 刷新
                committedAnswer = if (indexChanged) null else it.committedAnswer,
                notice = null,
            )
        }
        if (session.isReportReady) {
            resumeStore.clear() // 终态收口：报告仍可按 sessionId 获取
        }
        if (!session.isReportReady && (indexChanged || currentLive()?.question == null)) {
            refreshQuestion()
        } else {
            maybeAutoAnnounce(clarifiedQuestion)
        }
    }

    /** 拉服务端恢复视图对齐题面/已提交答案/题数（只读幂等；失败保留旧题面不虚构） */
    private suspend fun refreshQuestion() {
        val currentSessionId = sessionId ?: return
        val resumed = try {
            voice.resume(currentSessionId)
        } catch (_: Throwable) {
            return
        }
        lastSession = resumed.session
        updateLive {
            it.copy(
                status = resumed.session.status,
                questionIndex = resumed.session.questionIndex,
                questionTotal = resumed.questionTotal,
                question = resumed.question,
                committedAnswer = resumed.committedAnswer,
            )
        }
        maybeAutoAnnounce(clarifiedQuestion = null)
    }

    // ---------- 命令（UI 单命令在途；播报链推进绕过防重入） ----------

    fun sendCommand(type: String) {
        if (currentLive()?.busy == true) return
        commandJob?.cancel()
        commandJob = viewModelScope.launch {
            updateLive { it.copy(busy = true, error = null) }
            try {
                applyCommandDirect(type, questionId = null, answer = null, after = null)
            } finally {
                updateLive { it.copy(busy = false) }
            }
        }
    }

    /** 直接应用命令并采纳（无 UI 防重入门）：播报完成链与录音转写链复用 */
    private suspend fun applyCommandDirect(
        type: String,
        questionId: String?,
        answer: String?,
        after: (suspend () -> Unit)?,
    ) {
        val currentSessionId = sessionId ?: return
        try {
            val out = voice.command(currentSessionId, type, questionId, answer)
            adoptSessionAfterCommand(out.session, out.clarifiedQuestion)
            after?.invoke()
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (cause: AppError) {
            handleCommandFailure(currentSessionId, cause)
        } catch (_: Throwable) {
            updateLive { it.copy(error = AppError(AppErrorKind.UNEXPECTED).userMessage) }
        }
    }

    /** 命令失败：409 以服务端为准对齐后提示；404 清槽退出；其余如实提示可重试 */
    private suspend fun handleCommandFailure(currentSessionId: String, cause: AppError) {
        when (cause.kind) {
            AppErrorKind.CONFLICT -> {
                try {
                    val resumed = voice.resume(currentSessionId)
                    lastSession = resumed.session
                    updateLive {
                        it.copy(
                            status = resumed.session.status,
                            questionIndex = resumed.session.questionIndex,
                            questionTotal = resumed.questionTotal,
                            question = resumed.question,
                            committedAnswer = resumed.committedAnswer,
                            error = cause.userMessage,
                        )
                    }
                } catch (_: Throwable) {
                    updateLive { it.copy(error = cause.userMessage) }
                }
            }
            AppErrorKind.NOT_FOUND -> {
                resumeStore.clear()
                _state.value = VoiceUiState.Unavailable(cause.userMessage)
            }
            else -> updateLive { it.copy(error = cause.userMessage) }
        }
    }

    // ---------- 播报（读题→读选项链由播放完成驱动；失败可重试不推进） ----------

    private fun maybeAutoAnnounce(clarifiedQuestion: String?) {
        val live = currentLive() ?: return
        if (live.paused) return
        if (clarifiedQuestion != null) {
            announce(clarifiedQuestion, expectedStatus = null, completionEvent = null)
            return
        }
        val key = "${live.status}:${live.questionIndex}"
        if (announcedKey == key) return
        val question = live.question ?: return
        when (live.status) {
            "READING_QUESTION" -> {
                announcedKey = key
                announce(question.stem, expectedStatus = "READING_QUESTION", completionEvent = "question_read")
            }
            "READING_OPTIONS" -> {
                announcedKey = key
                announce(optionsSpeech(question), expectedStatus = "READING_OPTIONS", completionEvent = "options_read")
            }
        }
    }

    private fun announce(text: String, expectedStatus: String?, completionEvent: String?) {
        playbackJob = viewModelScope.launch {
            updateLive { it.copy(playing = true, ttsError = null) }
            try {
                val started = clock()
                val wav = voice.synthesize(text)
                reportTraceBestEffort(stage = "tts", durationMillis = clock() - started)
                playback.play(wav)
                updateLive { it.copy(playing = false) }
                if (completionEvent != null) {
                    val live = currentLive()
                    if (live != null && (expectedStatus == null || live.status == expectedStatus)) {
                        commandJob?.join() // 等在途 UI 命令收口，保证命令顺序
                        applyCommandDirect(completionEvent, questionId = null, answer = null, after = null)
                    }
                }
            } catch (cancellation: CancellationException) {
                updateLive { it.copy(playing = false) }
                throw cancellation
            } catch (_: Throwable) {
                // TTS 合成或播放失败：如实显示可重试，不推进状态、不静默假装成功
                updateLive { it.copy(playing = false, ttsError = "语音播报失败，可点击重试，或直接点选项作答") }
            }
        }
    }

    /** 重试播报 / 恢复后重播当前段 */
    fun retryAnnounce() {
        stopPlayback()
        val live = currentLive() ?: return
        val question = live.question
        when {
            live.clarifiedQuestion != null -> announce(live.clarifiedQuestion, expectedStatus = null, completionEvent = null)
            live.status == "READING_QUESTION" && question != null ->
                announce(question.stem, expectedStatus = "READING_QUESTION", completionEvent = "question_read")
            live.status == "READING_OPTIONS" && question != null ->
                announce(optionsSpeech(question), expectedStatus = "READING_OPTIONS", completionEvent = "options_read")
        }
    }

    fun pauseAnnounce() {
        stopPlayback()
        updateLive { it.copy(paused = true) }
        sendCommand("pause") // 服务端自环留审计；TTS 停播是客户端行为
    }

    fun resumeAnnounce() {
        updateLive { it.copy(paused = false) }
        if (currentLive()?.busy == true) return
        commandJob?.cancel()
        commandJob = viewModelScope.launch {
            updateLive { it.copy(busy = true) }
            try {
                applyCommandDirect("resume", questionId = null, answer = null, after = { announceCurrentSegment() })
            } finally {
                updateLive { it.copy(busy = false) }
            }
        }
    }

    fun bargeIn() {
        stopPlayback()
        sendCommand("barge_in")
    }

    private suspend fun announceCurrentSegment() {
        val live = currentLive() ?: return
        val question = live.question ?: return
        when (live.status) {
            "READING_QUESTION" -> announce(question.stem, expectedStatus = "READING_QUESTION", completionEvent = "question_read")
            "READING_OPTIONS" -> announce(optionsSpeech(question), expectedStatus = "READING_OPTIONS", completionEvent = "options_read")
        }
    }

    private fun stopPlayback() {
        playbackJob?.cancel()
        playback.stop()
        updateLive { it.copy(playing = false) }
    }

    private fun optionsSpeech(question: VoiceQuestion): String =
        question.options.joinToString(separator = "。") { "选项${it.key}，${it.text}" }

    // ---------- 作答（点选直提；文本/语音 transcript 统一走 intents） ----------

    fun chooseOption(key: String) {
        val live = currentLive() ?: return
        val question = live.question ?: return
        if (live.busy) return
        when {
            live.acceptsAnswer -> sendAnswerCommand(question.id, key)
            live.isAnnouncing -> updateLive { it.copy(notice = "正在播报本题，播报完成或打断后再作答") }
        }
    }

    private fun sendAnswerCommand(questionId: String, answer: String) {
        commandJob?.cancel()
        commandJob = viewModelScope.launch {
            updateLive { it.copy(busy = true, error = null) }
            try {
                applyCommandDirect("answer_proposed", questionId = questionId, answer = answer, after = null)
            } finally {
                updateLive { it.copy(busy = false) }
            }
        }
    }

    fun setInputText(value: String) = updateLive { it.copy(inputText = value) }

    /** 文本转写入口（可测试的 ASR 代理）与服务端转写结果共用路径 */
    fun submitTranscript() {
        val live = currentLive() ?: return
        val transcript = live.inputText.trim()
        if (transcript.isEmpty() || live.busy) return
        commandJob?.cancel()
        commandJob = viewModelScope.launch {
            updateLive { it.copy(busy = true, error = null) }
            try {
                applyTranscript(transcript)
            } finally {
                updateLive { it.copy(busy = false) }
            }
        }
    }

    private suspend fun applyTranscript(transcript: String) {
        val currentSessionId = sessionId ?: return
        try {
            val out = voice.intent(currentSessionId, transcript)
            if (out.fsmApplied && out.session != null) {
                updateLive { it.copy(inputText = "") }
                adoptSessionAfterCommand(out.session, out.clarifiedQuestion)
            } else {
                // 服务端未应用（unknown/pause/resume）：如实反馈，不改状态
                updateLive { it.copy(notice = intentNotice(out)) }
            }
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (cause: AppError) {
            handleCommandFailure(currentSessionId, cause)
        } catch (_: Throwable) {
            updateLive { it.copy(error = AppError(AppErrorKind.UNEXPECTED).userMessage) }
        }
    }

    private fun intentNotice(out: VoiceIntentResult): String = when {
        out.intent == "unknown" -> "没有理解这句话，请换个说法或直接点选项作答"
        else -> "该语音指令请使用界面上的播放控制按钮"
    }

    // ---------- 录音（真实 ASR 代理：采集 WAV → 服务端转写 → intents） ----------

    fun setMicGranted(granted: Boolean) {
        updateLive { it.copy(micGranted = granted) }
    }

    fun toggleRecording() {
        val live = currentLive() ?: return
        if (live.transcribing) return
        if (live.recording) {
            stopRecording()
            return
        }
        if (!live.micGranted) {
            updateLive { it.copy(micNotice = "未开启麦克风权限，请在系统弹窗中允许，或使用文字作答") }
            return
        }
        recordingJob?.cancel()
        recordingJob = viewModelScope.launch {
            try {
                capture.start()
                updateLive { it.copy(recording = true, micNotice = null, error = null) }
            } catch (cause: AudioEngineException) {
                updateLive { it.copy(recording = false, error = cause.userMessage) }
            } catch (_: Throwable) {
                updateLive { it.copy(recording = false, error = AppError(AppErrorKind.UNEXPECTED).userMessage) }
            }
        }
    }

    private fun stopRecording() {
        recordingJob?.cancel()
        recordingJob = viewModelScope.launch {
            updateLive { it.copy(recording = false, transcribing = true) }
            try {
                val wav = capture.stop()
                val started = clock()
                val transcription = voice.transcribe(wav)
                reportTraceBestEffort(stage = "asr", durationMillis = clock() - started)
                applyTranscript(transcription.text)
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (cause: AudioEngineException) {
                updateLive { it.copy(error = cause.userMessage) }
            } catch (cause: AppError) {
                updateLive { it.copy(error = cause.userMessage) }
            } catch (_: Throwable) {
                updateLive { it.copy(error = AppError(AppErrorKind.UNEXPECTED).userMessage) }
            } finally {
                updateLive { it.copy(transcribing = false) }
            }
        }
    }

    // ---------- 全卷报告（REPORT_READY 后先提交判分再投影语音报告） ----------

    fun requestReport() {
        val live = currentLive() ?: return
        if (live.busy) return
        commandJob?.cancel()
        commandJob = viewModelScope.launch {
            updateLive { it.copy(busy = true, error = null) }
            try {
                // 报告的分数/错题来自服务端判分：先提交考试（幂等），再取语音播报投影
                exam.submit(live.examId)
                val report = voice.report(live.sessionId)
                resumeStore.clear()
                _state.value = VoiceUiState.Report(report)
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (cause: Throwable) {
                updateLive { it.copy(error = cause.asAppError().userMessage) }
            } finally {
                updateLive { it.copy(busy = false) }
            }
        }
    }

    // ---------- 观测（客户端实测耗时；尽力而为不扰动业务） ----------

    private suspend fun reportTraceBestEffort(stage: String, durationMillis: Long) {
        if (durationMillis <= 0) return
        try {
            voice.reportTrace(
                stage = stage,
                durationMillis = durationMillis,
                sessionId = sessionId,
                examId = examId,
                questionId = currentLive()?.question?.id,
            )
        } catch (_: Throwable) {
            // 观测尽力而为：trace 失败绝不影响业务主流程（与服务端同款语义）
        }
    }

    // ---------- 一次性提示 ----------

    fun dismissRestored() = updateLive { it.copy(restored = false) }

    fun dismissNotice() = updateLive { it.copy(notice = null, micNotice = null) }

    fun dismissError() = updateLive { it.copy(error = null, ttsError = null) }

    // ---------- 内部 ----------

    private fun currentLive(): VoiceLive? = (_state.value as? VoiceUiState.Live)?.value

    private fun updateLive(transform: (VoiceLive) -> VoiceLive) {
        val current = _state.value
        if (current is VoiceUiState.Live) {
            _state.value = VoiceUiState.Live(transform(current.value))
        }
    }
}
