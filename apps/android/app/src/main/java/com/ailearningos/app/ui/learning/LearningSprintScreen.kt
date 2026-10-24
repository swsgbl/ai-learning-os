package com.ailearningos.app.ui.learning

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.ailearningos.app.data.remote.AssessmentItemDto
import com.ailearningos.app.ui.components.SectionCard
import com.ailearningos.app.ui.theme.Subtle

/**
 * 学习冲刺简版面板（M16-012，App/Web 同步）：合同确认 → 阶段推进 →
 * 前/后测作答 → 服务端判分。答案本地只作暂存——判分结论只在提交后
 * 来自服务端（答案隔离不变式，客户端不持有正确答案）。
 */
@Composable
fun LearningSprintScreen(viewModel: LearningSprintViewModel) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { viewModel.refresh() }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(
            modifier = Modifier.padding(top = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text("学习冲刺", style = MaterialTheme.typography.titleLarge)
            Text(
                "48 小时可验证闭环",
                style = MaterialTheme.typography.labelMedium,
                color = Subtle,
                modifier = Modifier.padding(top = 6.dp),
            )
        }

        state.error?.let { text ->
            Text(
                text,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        }
        state.notice?.let { text ->
            Text(text, style = MaterialTheme.typography.bodySmall, color = Subtle)
        }

        when {
            state.loading -> Text("正在加载学习合同…", color = Subtle)
            state.contracts.isEmpty() -> SectionCard(title = "学习冲刺") {
                Text(
                    "还没有学习合同。在 Web 端「学习冲刺」页创建并确认合同后，" +
                        "可以在这里推进阶段与作答前后测。",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            else -> LazyColumn(
                modifier = Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                items(state.contracts, key = { it.contract.id }) { entry ->
                    ContractSprintCard(
                        onOpenTransfer = { viewModel.startTransfer(it) },
                        onOpenPractice = { viewModel.startPracticeStep(it) },
                        entry = entry,
                        busy = state.busy,
                        onConfirm = { viewModel.confirmContract(entry.contract.id) },
                        onAdvance = { entry.sprint?.let { viewModel.advance(it.id) } },
                        onStartAssessment = { entry.sprint?.let { viewModel.startAssessment(it.id) } },
                    )
                }
                state.session?.let { session ->
                    item(key = "assessment-${session.sprintId}") {
                        AssessmentSessionCard(
                            session = session,
                            busy = state.busy,
                            viewModel = viewModel,
                        )
                    }
                }
                state.transferSession?.let { session ->
                    item(key = "transfer-${session.sprintId}") {
                        TransferCard(session, state, viewModel)
                    }
                }
                state.transferResult?.let { result ->
                    item(key = "transfer-result") {
                        SectionCard(title = "迁移检验得分") {
                            Text("${result.score} 分 · ${result.correctCount}/${result.questionCount} 对")
                        }
                    }
                }
                state.practiceStep?.let { step ->
                    item(key = "practice-${step.item.itemId}") {
                        PracticeCard(step, state, viewModel)
                    }
                }
                state.reviewSession?.let { (reviewId, item) ->
                    item(key = "review-$reviewId") {
                        ReviewCard(reviewId, item, state, viewModel)
                    }
                }
                state.lastResult?.let { result ->
                    item(key = "result-${result.sprintId}-${result.phase}") {
                        SectionCard(
                            title = if (result.phase == "baseline") "前测得分" else "后测得分",
                        ) {
                            Text(
                                "${result.score}分 · ${result.correctCount}/${result.questionCount} 对" +
                                    if (result.ungradedCount > 0) "（${result.ungradedCount} 题未判分）" else "",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Medium,
                            )
                            Text(
                                "阶段已推进到「${SPRINT_PHASE_LABELS[result.sprintPhase] ?: result.sprintPhase}」",
                                style = MaterialTheme.typography.bodySmall,
                                color = Subtle,
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ContractSprintCard(
    entry: ContractWithSprint,
    busy: Boolean,
    onConfirm: () -> Unit,
    onAdvance: () -> Unit,
    onStartAssessment: () -> Unit,
    onOpenTransfer: (String) -> Unit = {},
    onOpenPractice: (String) -> Unit = {},
) {
    val contract = entry.contract
    val sprint = entry.sprint
    SectionCard(title = contract.title) {
        Text(
            "${contract.targetOutcomes.size} 个能力目标 · ${contract.timeBudgetHours} 小时 · ${contract.passCriteria}",
            style = MaterialTheme.typography.bodySmall,
            color = Subtle,
        )
        if (contract.confirmedAt == null) {
            Button(onClick = onConfirm, enabled = !busy) { Text("确认合同") }
            return@SectionCard
        }
        if (sprint == null) {
            Text(
                "合同已确认——在 Web 端构建蓝图并开始冲刺后，这里会展示进度。",
                style = MaterialTheme.typography.bodySmall,
                color = Subtle,
            )
            return@SectionCard
        }
        // 阶段链：当前及已过阶段高亮（领域层单向链，客户端只投影）
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            val currentIndex = SPRINT_PHASES.indexOf(sprint.phase)
            SPRINT_PHASES.drop(1).forEachIndexed { index, phase ->
                FilterChip(
                    selected = index <= currentIndex - 1,
                    onClick = {},
                    enabled = false,
                    label = { Text(SPRINT_PHASE_LABELS[phase] ?: phase, style = MaterialTheme.typography.labelSmall) },
                )
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            val inAssessmentPhase = sprint.phase == "baseline_assessment" || sprint.phase == "posttest"
            if (sprint.phase == "transfer_check") {
                Button(onClick = { onOpenTransfer(sprint.id) }, enabled = !busy) { Text("开始迁移检验") }
            }
            if (sprint.phase == "targeted_practice") {
                Button(onClick = { onOpenPractice(sprint.id) }, enabled = !busy) { Text("定向练习（逐题）") }
            }
            if (inAssessmentPhase) {
                Button(onClick = onStartAssessment, enabled = !busy) {
                    Text(if (sprint.phase == "baseline_assessment") "开始前测" else "开始后测")
                }
            } else {
                val next = SPRINT_PHASES.getOrNull(SPRINT_PHASES.indexOf(sprint.phase) + 1)
                if (next != null) {
                    OutlinedButton(onClick = onAdvance, enabled = !busy) {
                        Text("推进到 ${SPRINT_PHASE_LABELS[next] ?: next}")
                    }
                }
            }
        }
        if (sprint.paused) {
            Text("已暂停：${sprint.pauseReason}（学习证据保留）", style = MaterialTheme.typography.bodySmall, color = Subtle)
        }
    }
}

@Composable
private fun AssessmentSessionCard(
    session: AssessmentSession,
    busy: Boolean,
    viewModel: LearningSprintViewModel,
) {
    SectionCard(
        title = (if (session.phase == "baseline") "前测" else "后测") + "（独立作答，先提交后见判分）",
    ) {
        session.items.forEach { item -> AssessmentItemView(item, session, viewModel) }
        Button(
            onClick = viewModel::submitAssessment,
            enabled = !busy && session.allAnswered,
        ) { Text(if (busy) "判分中…" else "提交（服务端判分）") }
    }
}

@Composable
private fun AssessmentItemView(
    item: AssessmentItemDto,
    session: AssessmentSession,
    viewModel: LearningSprintViewModel,
) {
    Column(
        modifier = Modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(item.stem, style = MaterialTheme.typography.bodyMedium)
        Text("概念 ${item.conceptId} · ${item.questionType}", style = MaterialTheme.typography.labelSmall, color = Subtle)
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            item.options?.forEach { option ->
                val selected = session.answers[item.itemId] == option.key
                OutlinedButton(
                    onClick = { viewModel.selectAnswer(item.itemId, option.key) },
                    border = BorderStroke(
                        width = if (selected) 2.dp else 1.dp,
                        color = if (selected) MaterialTheme.colorScheme.primary
                        else MaterialTheme.colorScheme.outline,
                    ),
                ) { Text("${option.key}. ${option.text}", style = MaterialTheme.typography.bodySmall) }
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("信心：", style = MaterialTheme.typography.labelSmall, color = Subtle, modifier = Modifier.padding(top = 10.dp))
            (1..5).forEach { level ->
                val selected = session.confidences[item.itemId] == level
                FilterChip(
                    selected = selected,
                    onClick = { viewModel.setConfidence(item.itemId, level) },
                    label = { Text("$level") },
                )
            }
        }
    }
}


// ---------- M20-002 作答面板 ----------

@Composable
private fun TransferCard(
    session: com.ailearningos.app.data.remote.TransferSessionDto,
    state: LearningSprintState,
    viewModel: LearningSprintViewModel,
) {
    SectionCard(title = "迁移检验（未见情境应用题）") {
        val allAnswered = session.items.all { !state.transferAnswers[it.itemId].isNullOrBlank() }
        session.items.forEach { item ->
            Column(Modifier.padding(vertical = 6.dp)) {
                Text(item.stem, style = MaterialTheme.typography.bodyMedium)
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    (item.options ?: emptyList()).forEach { option ->
                        val selected = state.transferAnswers[item.itemId] == option.key
                        OutlinedButton(
                            onClick = { viewModel.selectTransferAnswer(item.itemId, option.key) },
                            border = androidx.compose.foundation.BorderStroke(
                                width = if (selected) 2.dp else 1.dp,
                                color = if (selected) MaterialTheme.colorScheme.primary
                                else MaterialTheme.colorScheme.outline,
                            ),
                        ) { Text("${option.key}", style = MaterialTheme.typography.labelMedium) }
                    }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("信心", style = MaterialTheme.typography.labelSmall)
                    (1..5).forEach { level ->
                        FilterChip(
                            selected = state.transferConfidence[item.itemId] == level,
                            onClick = { viewModel.setTransferConfidence(item.itemId, level) },
                            label = { Text("$level") },
                        )
                    }
                }
            }
        }
        Button(
            onClick = { viewModel.submitTransfer(session.sprintId) },
            enabled = allAnswered && state.busy.not(),
        ) { Text("提交（服务端判分）") }
    }
}

@Composable
private fun PracticeCard(
    step: com.ailearningos.app.data.remote.PracticeStepDto,
    state: LearningSprintState,
    viewModel: LearningSprintViewModel,
) {
    SectionCard(title = "定向练习 · ${step.conceptId}（${step.passedSteps + 1}/${step.targetSteps} 步）") {
        Text("策略：${step.strategy}", style = MaterialTheme.typography.labelSmall)
        Text(step.item.stem, style = MaterialTheme.typography.bodyMedium)
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            (step.item.options ?: emptyList()).forEach { option ->
                val selected = state.practiceAnswer == option.key
                OutlinedButton(onClick = { viewModel.setPracticeAnswer(option.key) }) {
                    Text(option.key, style = MaterialTheme.typography.labelMedium)
                }
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("信心", style = MaterialTheme.typography.labelSmall)
            (1..5).forEach { level ->
                FilterChip(
                    selected = state.practiceConfidence == level,
                    onClick = { viewModel.setPracticeConfidence(level) },
                    label = { Text("$level") },
                )
            }
        }
        val feedback = state.practiceFeedback
        if (feedback != null) {
            Text(
                if (feedback.stepPassed) "答对（步骤推进）" else "答错（同步骤重试）",
                color = if (feedback.stepPassed) MaterialTheme.colorScheme.primary
                else MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall,
            )
            if (feedback.explanation.isNotBlank()) {
                Text("解析：${feedback.explanation.take(160)}", style = MaterialTheme.typography.labelSmall)
            }
            if (!feedback.practiceComplete) {
                OutlinedButton(onClick = { viewModel.startPracticeStep(currentSprintIdOf(state)) }) {
                    Text("下一题")
                }
            }
        } else {
            Button(
                onClick = { viewModel.submitPracticeAnswer(currentSprintIdOf(state)) },
                enabled = state.practiceAnswer.isNotBlank() && !state.busy,
            ) { Text("提交") }
        }
    }
}

private fun stepStepId(state: LearningSprintState): String =
    state.contracts.firstNotNullOfOrNull { c -> c.sprint?.id } ?: ""

private fun currentSprintIdOf(state: LearningSprintState): String =
    state.contracts.firstNotNullOfOrNull { c -> c.sprint?.id } ?: ""

@Composable
private fun ReviewCard(
    reviewId: String,
    item: com.ailearningos.app.data.remote.AssessmentItemDto,
    state: LearningSprintState,
    viewModel: LearningSprintViewModel,
) {
    SectionCard(title = "间隔复习 · ${item.conceptId}") {
        Text(item.stem, style = MaterialTheme.typography.bodyMedium)
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            (item.options ?: emptyList()).forEach { option ->
                OutlinedButton(onClick = { viewModel.setReviewAnswer(option.key) }) {
                    Text(option.key, style = MaterialTheme.typography.labelMedium)
                }
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("信心", style = MaterialTheme.typography.labelSmall)
            (1..5).forEach { level ->
                FilterChip(
                    selected = state.reviewConfidence == level,
                    onClick = { viewModel.setReviewConfidence(level) },
                    label = { Text("$level") },
                )
            }
        }
        Button(
            onClick = viewModel::submitReviewAnswer,
            enabled = state.reviewAnswer.isNotBlank() && !state.busy,
        ) { Text("提交复习作答") }
    }
}
