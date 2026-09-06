package com.ailearningos.app.ui.review

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.ailearningos.app.data.model.ConceptScore
import com.ailearningos.app.data.model.ExamReport
import com.ailearningos.app.data.model.ExamSubmission
import com.ailearningos.app.data.model.GradedItem
import com.ailearningos.app.data.model.MistakeEntry
import com.ailearningos.app.data.model.RemediationTask
import com.ailearningos.app.data.model.ReviewQuestion
import com.ailearningos.app.data.remote.AiosJson
import com.ailearningos.app.ui.common.ErrorView
import com.ailearningos.app.ui.common.LoadingView
import com.ailearningos.app.ui.components.SectionCard
import com.ailearningos.app.ui.components.StatusRow
import com.ailearningos.app.ui.theme.Bad
import com.ailearningos.app.ui.theme.Good
import com.ailearningos.app.ui.theme.Subtle
import com.ailearningos.app.ui.theme.Warn
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * 审阅页（M12-02）：只读呈现服务端判分结果。
 * report 优先；404 时诚实降级到 submission 概要（ViewModel 决定），本页零判分。
 */
@Composable
fun ReviewScreen(
    viewModel: ReviewViewModel,
    onBack: () -> Unit,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        when (val current = state) {
            is ReviewUiState.Loading -> LoadingView(text = "正在生成审阅…", modifier = Modifier.fillMaxWidth())

            is ReviewUiState.Error -> {
                ErrorView(message = current.message, onRetry = viewModel::refresh)
                OutlinedButton(onClick = onBack, modifier = Modifier.fillMaxWidth()) { Text("返回") }
            }

            is ReviewUiState.Report -> ReportContent(report = current.report, onBack = onBack)

            is ReviewUiState.Fallback -> FallbackContent(
                submission = current.submission,
                onRetry = viewModel::refresh,
                onBack = onBack,
            )
        }
    }
}

// ---------- 完整报告 ----------

@Composable
private fun ReportContent(report: ExamReport, onBack: () -> Unit) {
    Text("考试审阅", style = MaterialTheme.typography.titleLarge)

    SectionCard(title = report.paperTitle) {
        Text(
            "${formatScore(report.scoreEarned)} / ${formatScore(report.scoreMax)} 分",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.SemiBold,
        )
        StatusRow("答对题数", "${report.correctCount} / ${report.totalCount}")
        if (report.reviewedCount > 0) {
            StatusRow("待复核", "${report.reviewedCount} 题（判分不确定，暂不计入得分）", valueColor = Warn)
        }
    }

    if (report.concepts.isNotEmpty()) {
        SectionCard(title = "概念掌握") {
            Text(
                "按题目关联概念聚合正确率；待复核的题不计入正确率分母。",
                style = MaterialTheme.typography.bodySmall,
                color = Subtle,
            )
            report.concepts.forEach { concept -> ConceptRow(concept) }
        }
    }

    if (report.mistakes.isNotEmpty()) {
        SectionCard(title = "错题（${report.mistakes.size}）") {
            report.mistakes.forEach { mistake -> MistakeCard(mistake) }
        }
    }

    if (report.remediationTasks.isNotEmpty()) {
        SectionCard(title = "补救任务（${report.remediationTasks.size}）") {
            report.remediationTasks.forEach { task -> RemediationRow(task) }
        }
    }

    OutlinedButton(onClick = onBack, modifier = Modifier.fillMaxWidth()) { Text("返回学习页") }
}

@Composable
private fun ConceptRow(concept: ConceptScore) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(concept.concept, style = MaterialTheme.typography.bodyMedium)
            Text(
                concept.ratio?.let { "${Math.round(it * 100)}%" } ?: "待复核",
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Medium,
                color = when {
                    concept.ratio == null -> Warn
                    concept.ratio >= 0.8 -> Good
                    else -> MaterialTheme.colorScheme.onSurface
                },
            )
        }
        Text(
            "${concept.correct} / ${concept.total} 题正确" +
                if (concept.reviewed > 0) " · ${concept.reviewed} 题待复核" else "",
            style = MaterialTheme.typography.bodySmall,
            color = Subtle,
        )
    }
}

@Composable
private fun MistakeCard(mistake: MistakeEntry) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(mistake.stem, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
        StatusRow("你的作答", readableAnswer(mistake.given).ifBlank { "（未作答）" }, valueColor = Bad)
        StatusRow("正确答案", readableAnswer(mistake.expected))
        if (mistake.diagnosis.isNotBlank()) {
            StatusRow("错因", mistake.diagnosis)
        }
        if (mistake.explanation.isNotBlank()) {
            Text(
                mistake.explanation,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (mistake.knowledge.isNotEmpty()) {
            Text(
                mistake.knowledge.joinToString(" · "),
                style = MaterialTheme.typography.bodySmall,
                color = Subtle,
            )
        }
    }
}

@Composable
private fun RemediationRow(task: RemediationTask) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                when (task.kind) {
                    "review_concept" -> "复习概念"
                    "variant_practice" -> "变式练习"
                    else -> task.kind
                },
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.primary,
            )
            Text(task.title, style = MaterialTheme.typography.bodyMedium)
        }
        if (task.detail.isNotBlank()) {
            Text(
                task.detail,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

// ---------- 降级：submission 概要 ----------

@Composable
private fun FallbackContent(
    submission: ExamSubmission,
    onRetry: () -> Unit,
    onBack: () -> Unit,
) {
    Text("考试审阅", style = MaterialTheme.typography.titleLarge)

    SectionCard(title = submission.paperTitle) {
        Text(
            "提交结果概要",
            style = MaterialTheme.typography.labelMedium,
            color = Warn,
        )
        Text(
            "详细报告（概念掌握、错题诊断、补救任务）暂不可用，以下为服务端提交结果；" +
                "可稍后重试获取完整报告。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        StatusRow("得分", "${submission.score} 分")
        StatusRow("答对题数", "${submission.correctCount} / ${submission.totalCount}")
        StatusRow("用时", "${submission.durationSeconds / 60} 分 ${submission.durationSeconds % 60} 秒")
    }

    val questionsById = submission.questions.associateBy { it.id }
    SectionCard(title = "逐题结果") {
        submission.items.forEach { item ->
            GradedRow(item = item, question = questionsById[item.questionId])
        }
    }

    OutlinedButton(onClick = onRetry, modifier = Modifier.fillMaxWidth()) { Text("重试获取完整报告") }
    OutlinedButton(onClick = onBack, modifier = Modifier.fillMaxWidth()) { Text("返回学习页") }
}

@Composable
private fun GradedRow(item: GradedItem, question: ReviewQuestion?) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(
            question?.stem ?: item.questionId,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Medium,
        )
        StatusRow(
            label = "你的作答",
            value = readableAnswer(item.given).ifBlank { "（未作答）" },
            valueColor = when (item.correct) {
                false -> Bad
                true -> Good
                null -> Warn
            },
        )
        StatusRow(
            label = when (item.correct) {
                true -> "结果"
                false -> "正确答案"
                null -> "状态"
            },
            value = when (item.correct) {
                true -> "答对"
                false -> readableAnswer(item.expected)
                null -> "待复核"
            },
            valueColor = when (item.correct) {
                false -> Good
                else -> Warn
            },
        )
        if (item.explanation.isNotBlank()) {
            Text(
                item.explanation,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

// ---------- 数值/答案可读化 ----------

/** 60.0 → "60"；59.5 → "59.5" */
internal fun formatScore(value: Double): String =
    if (value == value.toLong().toDouble()) value.toLong().toString() else value.toString()

/**
 * 服务端答案原样可读化：JSON 形态（多选 option_indices / 数值 / 简答 accepted）
 * 转人话；普通字符串原样返回。与 Web 端 answerLabel 同源语义。
 */
internal fun readableAnswer(raw: String): String {
    val text = raw.trim()
    if (!text.startsWith("{")) return text
    return runCatching {
        val obj = AiosJson.parseToJsonElement(text).jsonObject
        obj["option_indices"]
            ?.jsonArray
            ?.mapIndexedNotNull { _, element -> OPTION_LETTERS.getOrNull(element.jsonPrimitive.int) }
            ?.joinToString("")
            ?.ifEmpty { "（未选择）" }
            ?: obj["latex"]?.jsonPrimitive?.content
            ?: obj["accepted"]?.jsonArray?.joinToString(" / ") { it.jsonPrimitive.content }
            ?: text
    }.getOrDefault(text)
}

private const val OPTION_LETTERS = "ABCDEFGH"
