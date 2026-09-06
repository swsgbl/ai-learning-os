package com.ailearningos.app.ui.exam

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.SizeTransform
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
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
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.ailearningos.app.data.model.ExamQuestion
import com.ailearningos.app.ui.common.ErrorView
import com.ailearningos.app.ui.common.LoadingView
import com.ailearningos.app.ui.components.SectionCard
import com.ailearningos.app.ui.theme.Bad
import com.ailearningos.app.ui.theme.Warn
import android.provider.Settings
import java.util.Locale

/**
 * 考场（M12-02）：服务端权威倒计时 / append-only 答案同步 / 防抖交卷。
 * 全部状态来自 [ExamViewModel]；本文件只做呈现与回调转发。
 */
@Composable
fun ExamScreen(
    viewModel: ExamViewModel,
    onOpenReview: (String) -> Unit,
    onBack: () -> Unit,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val finished by viewModel.finished.collectAsStateWithLifecycle()

    LaunchedEffect(finished) {
        finished?.let { examId ->
            viewModel.consumeFinished()
            onOpenReview(examId)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        when (val current = state) {
            is ExamUiState.Loading -> LoadingView(text = "正在进入考场…", modifier = Modifier.fillMaxWidth())

            is ExamUiState.Unavailable -> {
                ErrorView(message = current.message, onRetry = viewModel::begin)
                OutlinedButton(onClick = onBack, modifier = Modifier.fillMaxWidth()) {
                    Text("返回")
                }
            }

            is ExamUiState.Active -> ExamContent(
                active = current.value,
                onChoose = viewModel::choose,
                onRetrySync = viewModel::retrySync,
                onSelectIndex = viewModel::selectIndex,
                onPrevious = viewModel::previous,
                onNext = viewModel::next,
                onSubmit = viewModel::submit,
            )
        }
    }
}

@Composable
private fun ExamContent(
    active: ExamActive,
    onChoose: (String, String) -> Unit,
    onRetrySync: () -> Unit,
    onSelectIndex: (Int) -> Unit,
    onPrevious: () -> Unit,
    onNext: () -> Unit,
    onSubmit: () -> Unit,
) {
    var confirming by remember { mutableStateOf(false) }
    val reducedMotion = rememberReducedMotion()

    SectionCard(title = active.paperTitle) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column {
                Text(
                    "第 ${active.currentIndex + 1} 题 / 共 ${active.questions.size} 题",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    "已作答 ${active.answeredCount} 题",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Text(
                formatRemaining(active.remainingSeconds),
                style = MaterialTheme.typography.headlineSmall,
                color = when {
                    active.remainingSeconds <= 60 -> Bad
                    active.remainingSeconds <= 300 -> Warn
                    else -> MaterialTheme.colorScheme.onSurface
                },
                modifier = Modifier.semantics { contentDescription = "剩余时间 ${formatRemaining(active.remainingSeconds)}" },
            )
        }
    }

    SyncBanner(sync = active.sync, submitError = active.submitError, onRetrySync = onRetrySync)

    QuestionNavRow(
        questions = active.questions,
        answers = active.answers,
        current = active.currentIndex,
        onSelect = onSelectIndex,
    )

    val question = active.questions.getOrNull(active.currentIndex)
    AnimatedContent(
        targetState = active.currentIndex,
        transitionSpec = {
            if (reducedMotion) {
                // 关闭动画：起止透明度均为 1 的空过渡
                fadeIn(initialAlpha = 1f) togetherWith fadeOut(targetAlpha = 1f)
            } else {
                val forward = targetState >= initialState
                (slideInHorizontally { if (forward) it / 4 else -it / 4 } + fadeIn())
                    .togetherWith(slideOutHorizontally { if (forward) -it / 4 else it / 4 } + fadeOut())
            }.using(SizeTransform(clip = false))
        },
        label = "question",
    ) { index ->
        val indexed = active.questions.getOrNull(index)
        if (indexed == null) {
            Text("题目缺失", style = MaterialTheme.typography.bodyMedium)
        } else {
            QuestionCard(
                question = indexed,
                index = index,
                total = active.questions.size,
                answer = active.answers[indexed.id],
                enabled = !active.submitting,
                onChoose = onChoose,
            )
        }
    }
    if (question == null) {
        Text("本卷没有题目", style = MaterialTheme.typography.bodyMedium)
    }

    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedButton(
            onClick = onPrevious,
            enabled = active.currentIndex > 0,
            modifier = Modifier.weight(1f),
        ) { Text("上一题") }
        if (active.currentIndex < active.questions.size - 1) {
            Button(onClick = onNext, modifier = Modifier.weight(1f)) { Text("下一题") }
        } else {
            Button(
                onClick = { confirming = true },
                enabled = !active.submitting,
                modifier = Modifier.weight(1f),
            ) { Text("交卷") }
        }
    }

    if (confirming) {
        SubmitConfirmDialog(
            answered = active.answeredCount,
            total = active.questions.size,
            syncing = active.sync is SyncState.Syncing,
            submitting = active.submitting,
            onConfirm = {
                confirming = false
                onSubmit()
            },
            onDismiss = { confirming = false },
        )
    }
}

/** 答案同步进度条：同步中 / 失败可重试 / 交卷失败 */
@Composable
private fun SyncBanner(
    sync: SyncState,
    submitError: String?,
    onRetrySync: () -> Unit,
) {
    if (submitError != null) {
        SectionCard(title = "交卷未完成") {
            Text(submitError, style = MaterialTheme.typography.bodyMedium, color = Bad)
        }
    }
    when (sync) {
        is SyncState.Syncing -> Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            CircularProgressIndicator(modifier = Modifier.size(14.dp), strokeWidth = 2.dp)
            Text(
                "答案同步中…",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        is SyncState.Failed -> Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                sync.message,
                style = MaterialTheme.typography.bodySmall,
                color = Warn,
                modifier = Modifier.weight(1f),
            )
            TextButton(onClick = onRetrySync) { Text("重试同步") }
        }

        is SyncState.Idle -> Unit
    }
}

/** 题号导航：已答高亮、当前描边；窄屏横向滚动 */
@Composable
private fun QuestionNavRow(
    questions: List<ExamQuestion>,
    answers: Map<String, String>,
    current: Int,
    onSelect: (Int) -> Unit,
) {
    LazyRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        itemsIndexed(questions) { index, question ->
            val answered = answers[question.id]?.isNotBlank() == true
            val isCurrent = index == current
            Surface(
                onClick = { onSelect(index) },
                shape = MaterialTheme.shapes.small,
                color = when {
                    isCurrent -> MaterialTheme.colorScheme.surfaceVariant
                    answered -> MaterialTheme.colorScheme.primary
                    else -> MaterialTheme.colorScheme.surface
                },
                contentColor = when {
                    isCurrent -> MaterialTheme.colorScheme.onSurface
                    answered -> MaterialTheme.colorScheme.onPrimary
                    else -> MaterialTheme.colorScheme.onSurfaceVariant
                },
                border = BorderStroke(
                    width = if (isCurrent) 1.dp else 0.dp,
                    color = MaterialTheme.colorScheme.primary,
                ),
            ) {
                Text(
                    "${index + 1}",
                    style = MaterialTheme.typography.labelLarge,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                )
            }
        }
    }
}

@Composable
private fun SubmitConfirmDialog(
    answered: Int,
    total: Int,
    syncing: Boolean,
    submitting: Boolean,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("确认交卷？") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("已作答 $answered / $total 题。交卷后立即判分，不能继续修改。")
                if (answered < total) {
                    Text(
                        "还有 ${total - answered} 题未作答，未作答按空答案计分。",
                        style = MaterialTheme.typography.bodySmall,
                        color = Warn,
                    )
                }
                if (syncing) {
                    Text(
                        "部分答案仍在同步，交卷将按服务端已收到的答案结算。",
                        style = MaterialTheme.typography.bodySmall,
                        color = Warn,
                    )
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onConfirm, enabled = !submitting) {
                Text(if (submitting) "交卷中…" else "确认交卷")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss, enabled = !submitting) { Text("继续作答") }
        },
    )
}

/** 剩余时间 mm:ss（超过一小时 h:mm:ss）；tabular 显示 */
internal fun formatRemaining(seconds: Int): String {
    val hours = seconds / 3600
    val minutes = (seconds % 3600) / 60
    val secs = seconds % 60
    return if (hours > 0) {
        String.format(Locale.ROOT, "%d:%02d:%02d", hours, minutes, secs)
    } else {
        String.format(Locale.ROOT, "%02d:%02d", minutes, secs)
    }
}

/** 系统动画时长为 0（关闭动画）时不播放题目切换动画 */
@Composable
private fun rememberReducedMotion(): Boolean {
    val context = LocalContext.current
    return remember {
        runCatching {
            Settings.Global.getFloat(
                context.contentResolver,
                Settings.Global.ANIMATOR_DURATION_SCALE,
                1f,
            ) == 0f
        }.getOrDefault(false)
    }
}
