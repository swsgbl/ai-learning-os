package com.ailearningos.app.ui.voice

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.ailearningos.app.data.model.VoiceQuestion
import com.ailearningos.app.ui.common.ErrorView
import com.ailearningos.app.ui.common.LoadingView
import com.ailearningos.app.ui.components.SectionCard
import com.ailearningos.app.ui.theme.Subtle

/**
 * 语音陪练页（M12-03）：选试卷进入（Start）/会话恢复（Resume）/tab 选择（Picker）。
 *
 * 交互边界：状态/题面/答案全部呈现服务端返回；录音须授权且状态可见；
 * 播报失败可重试；不描述实现细节，主操作单手可达。
 */
@Composable
fun VoiceScreen(
    viewModel: VoiceViewModel,
    onBack: () -> Unit,
    onOpenVoiceSession: (String) -> Unit,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    // RECORD_AUDIO：录音前请求；拒绝则保留文字作答（诚实降级，不冒充已录音）
    val context = LocalContext.current
    var micGranted by remember { mutableStateOf(false) }
    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        micGranted = granted
        viewModel.setMicGranted(granted)
    }
    LaunchedEffect(Unit) {
        val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        micGranted = granted
        viewModel.setMicGranted(granted)
    }

    when (val current = state) {
        is VoiceUiState.Loading -> Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
            LoadingView("正在进入语音陪练…")
        }

        is VoiceUiState.Unavailable -> Column(
            modifier = Modifier.fillMaxSize().padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Header(onBack = onBack)
            ErrorView(message = current.message, onRetry = viewModel::begin)
        }

        is VoiceUiState.Picker -> PickerBody(
            picker = current.value,
            onRetry = viewModel::refreshPicker,
            onOpenVoiceSession = onOpenVoiceSession,
        )

        is VoiceUiState.Live -> SessionBody(
            live = current.value,
            micGranted = micGranted,
            onRequestMic = { permissionLauncher.launch(Manifest.permission.RECORD_AUDIO) },
            viewModel = viewModel,
            onBack = onBack,
        )

        is VoiceUiState.Report -> ReportBody(
            report = current.value,
            onBack = onBack,
        )
    }
}

// ---------- Picker（tab 入口：链路概要 + 恢复提示 + 选卷引导） ----------

@Composable
private fun PickerBody(
    picker: VoicePickerState,
    onRetry: () -> Unit,
    onOpenVoiceSession: (String) -> Unit,
) {
    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item { Text("语音陪练", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(top = 12.dp)) }
        item {
            SectionCard(title = "怎么开始") {
                Text(
                    "在学习页选择一套试卷，点击「语音陪练」即可开始。" +
                        "进入后按题听读、口述或点选作答，可随时暂停、打断或结束。",
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
        item {
            SectionCard(title = "语音链路") {
                if (picker.loading) {
                    LoadingView("正在获取语音链路…")
                } else if (picker.providers != null) {
                    val providers = picker.providers
                    Text(
                        "当前模式：${modeLabel(providers.voiceMode)}。" +
                            "听写：${providers.asr.provider}${if (providers.asr.fallback) "（已降级本地）" else ""}；" +
                            "播报：${providers.tts.provider}${if (providers.tts.fallback) "（已降级本地）" else ""}。",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                } else {
                    Text("语音链路状态暂不可用。", style = MaterialTheme.typography.bodyMedium)
                }
                if (picker.error != null) {
                    TextButton(onClick = onRetry) { Text("重试") }
                }
            }
        }
        if (picker.resumeSlot != null) {
            item {
                SectionCard(title = "未完成的语音陪练") {
                    Text(
                        "检测到上次未收口的语音陪练。进入后按服务端状态继续当前题；" +
                            "若会话已收口会提示重新开始。",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    Button(
                        onClick = { onOpenVoiceSession(picker.resumeSlot.sessionId) },
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text("继续语音陪练") }
                }
            }
        }
    }
}

private fun modeLabel(mode: String): String = when (mode) {
    "local" -> "本地"
    "cloud" -> "云端"
    "hybrid" -> "混合"
    else -> mode
}

// ---------- Session（陪练进行中） ----------

@Composable
private fun SessionBody(
    live: VoiceLive,
    micGranted: Boolean,
    onRequestMic: () -> Unit,
    viewModel: VoiceViewModel,
    onBack: () -> Unit,
) {
    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Header(
                onBack = onBack,
                trailing = {
                    Text(
                        "第 ${live.questionPosition}/${live.questionTotal} 题",
                        style = MaterialTheme.typography.labelMedium,
                        color = Subtle,
                    )
                },
            )
        }
        item { StatusCard(live) }
        if (live.restored) {
            item {
                NoticeCard(text = "已按服务端状态恢复，从当前题继续。", onDismiss = viewModel::dismissRestored)
            }
        }
        if (live.micNotice != null) {
            item { NoticeCard(text = live.micNotice, onDismiss = viewModel::dismissNotice) }
        }
        if (live.notice != null) {
            item { NoticeCard(text = live.notice, onDismiss = viewModel::dismissNotice) }
        }
        if (live.clarifiedQuestion != null) {
            item {
                SectionCard(title = "请再说一遍") {
                    Text(live.clarifiedQuestion, style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
        if (live.question != null) {
            item {
                QuestionCard(
                    position = live.questionPosition,
                    question = live.question,
                    committedAnswer = live.committedAnswer,
                    enabled = live.acceptsAnswer,
                    onChoose = viewModel::chooseOption,
                )
            }
        }
        if (live.ttsError != null) {
            item {
                SectionCard(title = "播报失败") {
                    Text(live.ttsError, style = MaterialTheme.typography.bodyMedium)
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Button(onClick = viewModel::retryAnnounce) { Text("重试播报") }
                        if (live.status == "READING_QUESTION") {
                            OutlinedButton(onClick = { viewModel.sendCommand("question_read") }) { Text("跳过播报，读选项") }
                        }
                        if (live.status == "READING_OPTIONS") {
                            OutlinedButton(onClick = { viewModel.sendCommand("options_read") }) { Text("跳过播报，开始作答") }
                        }
                    }
                }
            }
        }
        if (live.error != null) {
            item {
                ErrorView(message = live.error, onRetry = viewModel::dismissError, retryLabel = "知道了")
            }
        }
        item { PlaybackControls(live, viewModel) }
        if (!live.isReportReady) {
            item { AnswerArea(live, micGranted, onRequestMic, viewModel) }
        }
        item { AdvanceControls(live, viewModel) }
    }
}

@Composable
private fun Header(onBack: () -> Unit, trailing: @Composable () -> Unit = {}) {
    Row(
        modifier = Modifier.padding(top = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text("语音陪练", style = MaterialTheme.typography.titleLarge)
        trailing()
        TextButton(onClick = onBack) { Text("返回") }
    }
}

@Composable
private fun StatusCard(live: VoiceLive) {
    SectionCard(title = statusLabel(live.status)) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            if (live.busy || live.transcribing) {
                CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
            }
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                if (live.playing) Text("正在播报…", style = MaterialTheme.typography.bodyMedium)
                if (live.paused) Text("已暂停", style = MaterialTheme.typography.bodyMedium)
                if (live.recording) {
                    Text(
                        "正在录音，再次点击录音按钮结束并识别",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.error,
                        fontWeight = FontWeight.Medium,
                    )
                }
                if (live.transcribing) Text("正在识别…", style = MaterialTheme.typography.bodyMedium)
                if (live.committedAnswer != null) {
                    Text(
                        "已提交答案：${live.committedAnswer}",
                        style = MaterialTheme.typography.bodyMedium,
                        fontWeight = FontWeight.Medium,
                    )
                }
                if (!live.playing && !live.paused && !live.recording && !live.transcribing && live.committedAnswer == null) {
                    Text(hintFor(live.status), style = MaterialTheme.typography.bodySmall, color = Subtle)
                }
            }
        }
    }
}

private fun statusLabel(status: String): String = when (status) {
    "SESSION_READY" -> "准备就绪"
    "READING_QUESTION" -> "正在读题"
    "READING_OPTIONS" -> "正在读选项"
    "WAITING_ANSWER" -> "等待作答"
    "CLARIFYING" -> "请再确认答案"
    "ANSWER_COMMITTED" -> "已提交答案"
    "NEXT_QUESTION" -> "本题完成"
    "REPORT_READY" -> "全卷完成"
    else -> "进行中"
}

private fun hintFor(status: String): String = when (status) {
    "SESSION_READY" -> "点击下方「开始读题」，按题听读作答。"
    "WAITING_ANSWER" -> "口述、点选选项，或输入文字作答。"
    "CLARIFYING" -> "直接点选项，或更明确地说出选项字母。"
    "ANSWER_COMMITTED" -> "可改答案，或确认后进入下一题。"
    "NEXT_QUESTION" -> "读下一题，或完成全卷生成报告。"
    "REPORT_READY" -> "点击下方「生成报告」查看本次结果。"
    else -> ""
}

@Composable
private fun NoticeCard(text: String, onDismiss: () -> Unit) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = MaterialTheme.shapes.medium,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
            TextButton(onClick = onDismiss) { Text("知道了") }
        }
    }
}

@Composable
private fun QuestionCard(
    position: Int,
    question: VoiceQuestion,
    committedAnswer: String?,
    enabled: Boolean,
    onChoose: (String) -> Unit,
) {
    SectionCard(title = "第 $position 题") {
        Text(question.stem, style = MaterialTheme.typography.bodyLarge)
        Column(verticalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 8.dp)) {
            question.options.forEach { option ->
                val selected = committedAnswer == option.key
                OutlinedButton(
                    onClick = { onChoose(option.key) },
                    enabled = enabled,
                    modifier = Modifier.fillMaxWidth(),
                    colors = if (selected) {
                        androidx.compose.material3.ButtonDefaults.outlinedButtonColors(
                            containerColor = MaterialTheme.colorScheme.primaryContainer,
                        )
                    } else {
                        androidx.compose.material3.ButtonDefaults.outlinedButtonColors()
                    },
                ) {
                    Text(
                        "${option.key}．${option.text}",
                        fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
        }
    }
}

@Composable
private fun PlaybackControls(live: VoiceLive, viewModel: VoiceViewModel) {
    SectionCard(title = "播放") {
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            if (live.paused) {
                FilledTonalButton(onClick = viewModel::resumeAnnounce) { Text("继续播报") }
            } else {
                FilledTonalButton(onClick = viewModel::pauseAnnounce, enabled = live.playing) { Text("暂停") }
            }
            FilledTonalButton(onClick = viewModel::bargeIn, enabled = live.isAnnouncing) { Text("打断") }
            FilledTonalButton(onClick = viewModel::retryAnnounce) { Text("重听") }
        }
    }
}

@Composable
private fun AnswerArea(
    live: VoiceLive,
    micGranted: Boolean,
    onRequestMic: () -> Unit,
    viewModel: VoiceViewModel,
) {
    SectionCard(title = "作答") {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Button(
                onClick = {
                    if (micGranted) viewModel.toggleRecording() else onRequestMic()
                },
                enabled = live.acceptsAnswer && !live.busy,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    when {
                        live.recording -> "结束录音并识别"
                        live.transcribing -> "识别中…"
                        micGranted -> "录音作答"
                        else -> "开启麦克风录音作答"
                    },
                )
            }
            if (live.recording) {
                Text(
                    "● 正在录音",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.error,
                )
            }
            OutlinedTextField(
                value = live.inputText,
                onValueChange = viewModel::setInputText,
                modifier = Modifier.fillMaxWidth(),
                placeholder = { Text("或输入文字，如「选 B」") },
                enabled = live.acceptsAnswer && !live.busy,
                singleLine = true,
            )
            Button(
                onClick = viewModel::submitTranscript,
                enabled = live.acceptsAnswer && live.inputText.isNotBlank() && !live.busy,
                modifier = Modifier.fillMaxWidth(),
            ) { Text("提交作答") }
        }
    }
}

@Composable
private fun AdvanceControls(live: VoiceLive, viewModel: VoiceViewModel) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        when {
            live.status == "SESSION_READY" -> Button(
                onClick = { viewModel.sendCommand("start_reading") },
                enabled = !live.busy,
                modifier = Modifier.fillMaxWidth(),
            ) { Text("开始读题") }

            live.status == "ANSWER_COMMITTED" -> Button(
                onClick = { viewModel.sendCommand("commit_confirmed") },
                enabled = !live.busy,
                modifier = Modifier.fillMaxWidth(),
            ) { Text("确认，进入下一题") }

            live.status == "NEXT_QUESTION" -> Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Button(
                    onClick = { viewModel.sendCommand("start_reading") },
                    enabled = !live.busy,
                    modifier = Modifier.weight(1f),
                ) { Text("读下一题") }
                OutlinedButton(
                    onClick = { viewModel.sendCommand("report_ready") },
                    enabled = !live.busy,
                    modifier = Modifier.weight(1f),
                ) { Text("完成全卷") }
            }

            live.status == "REPORT_READY" -> Button(
                onClick = viewModel::requestReport,
                enabled = !live.busy,
                modifier = Modifier.fillMaxWidth(),
            ) { Text(if (live.busy) "正在生成…" else "生成报告") }

            live.status == "WAITING_ANSWER" || live.status == "CLARIFYING" -> OutlinedButton(
                onClick = { viewModel.sendCommand("skip") },
                enabled = !live.busy,
                modifier = Modifier.fillMaxWidth(),
            ) { Text("跳过本题") }
        }
        if (!live.isReportReady) {
            TextButton(
                onClick = { viewModel.sendCommand("end") },
                enabled = !live.busy,
                modifier = Modifier.fillMaxWidth(),
            ) { Text("结束语音陪练", color = MaterialTheme.colorScheme.error) }
        }
    }
}

// ---------- Report（全卷播报投影） ----------

@Composable
private fun ReportBody(report: com.ailearningos.app.data.model.VoiceReport, onBack: () -> Unit) {
    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Header(onBack = onBack, trailing = { Text("全卷完成", style = MaterialTheme.typography.labelMedium, color = Subtle) })
        }
        item {
            SectionCard(title = "本次结果") {
                Text(report.spokenText, style = MaterialTheme.typography.bodyLarge)
            }
        }
        if (report.mistakes.isNotEmpty()) {
            item { Text("错题摘要", style = MaterialTheme.typography.titleMedium) }
            items(report.mistakes.size) { index ->
                val mistake = report.mistakes[index]
                SectionCard(title = "题 ${mistake.questionId}") {
                    Text(mistake.stemPreview, style = MaterialTheme.typography.bodyMedium)
                    Text(
                        "你的答案：${mistake.yourAnswer}｜正确答案：${mistake.correctAnswer}",
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(top = 6.dp),
                    )
                    if (mistake.diagnosis.isNotBlank()) {
                        Text(
                            "错因：${mistake.diagnosis}",
                            style = MaterialTheme.typography.bodySmall,
                            color = Subtle,
                        )
                    }
                }
            }
        }
        if (report.remediations.isNotEmpty()) {
            item { Text("补救建议", style = MaterialTheme.typography.titleMedium) }
            items(report.remediations.size) { index ->
                val remediation = report.remediations[index]
                SectionCard(title = remediation.concept) {
                    Text(remediation.detail, style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
        item {
            Text(
                "更详细的讲解可在学习与审阅中查看。",
                style = MaterialTheme.typography.bodySmall,
                color = Subtle,
                modifier = Modifier.padding(bottom = 16.dp),
            )
        }
    }
}
