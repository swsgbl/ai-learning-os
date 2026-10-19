package com.ailearningos.app.ui.create

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.ailearningos.app.ui.common.ContentState
import com.ailearningos.app.ui.components.SectionCard
import com.ailearningos.app.ui.theme.Subtle
import com.ailearningos.app.ui.theme.Warn

/**
 * M15-017 定制课程（App 端闭环）：说出学习目标 → 章节方案 → 一键生成试卷开练。
 * 目标无概念匹配时如实展示服务端返回的「当前支持主题」引导。
 */
@Composable
fun CreateCourseScreen(
    viewModel: CreateCourseViewModel,
    onOpenStudy: () -> Unit,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    LaunchedEffect(state.createdPaper) {
        if (state.createdPaper != null) {
            viewModel.consumeCreatedPaper()
            onOpenStudy()
        }
    }

    Column(
        modifier = Modifier.verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("定制课程", style = MaterialTheme.typography.headlineSmall)
        Text(
            "说出你想学的主题、学科或行业（例如「我想学 SQL 数据分析」），系统生成章节方案，确认后一键变成可练的试卷。",
            style = MaterialTheme.typography.bodySmall,
            color = Subtle,
        )

        SectionCard(title = "学习目标") {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = state.goal,
                    onValueChange = viewModel::onGoalChange,
                    placeholder = { Text("我想学 ……", style = MaterialTheme.typography.bodyMedium) },
                    minLines = 2,
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = viewModel::generate, enabled = !state.generating && state.goal.isNotBlank()) {
                        Text(if (state.generating) "生成中…" else "生成课程方案")
                    }
                    if (state.generating) {
                        CircularProgressIndicator(modifier = Modifier.padding(top = 12.dp))
                    }
                }
            }
        }

        when (val draft = state.draft) {
            is ContentState.Loading -> Text("正在按学习目标组织课程……", style = MaterialTheme.typography.bodySmall, color = Subtle)
            is ContentState.Error -> Text(
                draft.message,
                style = MaterialTheme.typography.bodySmall,
                color = Warn,
            )
            is ContentState.Success -> CoursePlanCard(draft.value, state.materializing, viewModel::materialize)
            else -> {}
        }

        state.notice?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Warn, textAlign = TextAlign.Start) }
    }
}

@Composable
private fun CoursePlanCard(plan: CoursePlanView, materializing: Boolean, onMaterialize: () -> Unit) {
    SectionCard(title = "课程方案（${plan.chapters.size} 章）") {
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(plan.goal, style = MaterialTheme.typography.bodyMedium)
            Text(plan.note, style = MaterialTheme.typography.bodySmall, color = Subtle)
            plan.chapters.forEach { chapter ->
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(
                        "第${chapter.chapterNo}章 · ${chapter.title}（难度 ${chapter.difficulty}）",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    if (chapter.assessmentStem.isNotBlank()) {
                        Text("评估：${chapter.assessmentStem}", style = MaterialTheme.typography.bodySmall, color = Subtle)
                    }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = onMaterialize, enabled = !materializing) {
                    Text(if (materializing) "生成试卷中…" else "生成试卷并开练")
                }
            }
            Text(
                "试卷为你的私有练习卷（仅自己可见），生成后进入「学习」页开练。",
                style = MaterialTheme.typography.bodySmall,
                color = Subtle,
            )
        }
    }
}
