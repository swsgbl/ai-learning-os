package com.ailearningos.app.ui.progress

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.ailearningos.app.data.remote.PlanTaskDto
import com.ailearningos.app.ui.components.SectionCard
import com.ailearningos.app.ui.theme.Subtle
import com.ailearningos.app.ui.theme.Warn

/**
 * M15-022 App 端工作台（与 Web /progress 同口径）：
 * 今天学什么——今日任务（新学/复习/错题重测）+ 三统计。
 */
@Composable
fun ProgressScreen(
    viewModel: ProgressViewModel,
    onOpenStudy: () -> Unit,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { viewModel.load() }

    Column(
        modifier = Modifier.verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("工作台", style = MaterialTheme.typography.headlineSmall)
        Text(
            "今日任务由服务端学习模型实时规划（新学 / 复习 / 错题重测），薄弱概念来自你的作答历史。",
            style = MaterialTheme.typography.bodySmall,
            color = Subtle,
        )

        // 三统计卡（与 Web 首页同口径）
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            StatCard("今日任务", state.plan?.task_count?.toString() ?: "—", Modifier.fillMaxWidth(0.33f))
            StatCard("薄弱概念", state.weakConceptCount.toString(), Modifier.fillMaxWidth(0.34f))
            StatCard("可练试卷", state.paperCount.toString(), Modifier.fillMaxWidth(0.33f))
        }

        state.error?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Warn) }

        val plan = state.plan
        if (plan == null && !state.loading) {
            SectionCard(title = "今天学什么") {
                Text(
                    "暂无学习计划——去「学习」页开一场练习，或用「定制课程」生成新课程，任务会自动出现在这里。",
                    style = MaterialTheme.typography.bodyMedium,
                )
                TextButton(onClick = onOpenStudy) { Text("去学习页") }
            }
        }

        val kinds = listOf(
            "new_learning" to "新学",
            "review" to "复习",
            "mistake_retry" to "错题重测",
        )
        kinds.forEach { (kind, label) ->
            val tasks = plan?.tasks?.filter { it.kind == kind }.orEmpty()
            if (tasks.isNotEmpty()) {
                SectionCard(title = "$label（${tasks.size}）") {
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        tasks.forEach { task -> TaskRow(task) }
                    }
                }
            }
        }
        if (plan != null && plan.tasks.isEmpty()) {
            SectionCard(title = "今天学什么") {
                Text("今天没有待办任务。去开一场新练习，或定制一门新课程。", style = MaterialTheme.typography.bodyMedium)
                TextButton(onClick = onOpenStudy) { Text("去学习页") }
            }
        }
    }
}

@Composable
private fun StatCard(label: String, value: String, modifier: Modifier = Modifier) {
    Card(modifier = modifier) {
        Column(Modifier.padding(12.dp)) {
            Text(label, style = MaterialTheme.typography.bodySmall, color = Subtle)
            Text(value, style = MaterialTheme.typography.headlineSmall)
        }
    }
}

@Composable
private fun TaskRow(task: PlanTaskDto) {
    Column {
        Text(task.title, style = MaterialTheme.typography.bodyMedium)
        task.reason?.let {
            Text(it, style = MaterialTheme.typography.bodySmall, color = Subtle)
        }
    }
}
