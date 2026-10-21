package com.ailearningos.app.ui.exam

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.ailearningos.app.data.model.PaperSummary
import com.ailearningos.app.data.remote.ExamHistoryItemDto
import com.ailearningos.app.ui.common.ContentState
import com.ailearningos.app.ui.common.ContentStateView
import com.ailearningos.app.ui.common.difficultyLabel
import com.ailearningos.app.ui.components.SectionCard
import com.ailearningos.app.ui.study.StudyViewModel
import com.ailearningos.app.ui.theme.Subtle

/**
 * M15-025 考场审阅首屏：与 Web 端「考场审阅」导航对齐的独立入口。
 * - 全部考试记录（跨卷）：历次成绩 + 逐题审阅报告回看；
 * - 选择试卷开考：直接进入倒计时考场；
 * - 未完成考试恢复提示。
 * 数据复用 StudyViewModel（papers + /exams/history + resume 槽）。
 */
@Composable
fun ExamCenterScreen(
    viewModel: StudyViewModel,
    onStartPaper: (String) -> Unit,
    onResumeExam: (String) -> Unit,
    onOpenReview: (String) -> Unit,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    // 每次进入该标签都刷新：backstack 复用的 VM 不会重跑 init（切换 API
    // 地址后回本页曾显示缓存的旧错误态——重试按钮不应是唯一出路）
    LaunchedEffect(Unit) { viewModel.refresh() }
    // 试卷 id → 标题（历史条目只有 paper_id，展示时补卷名）
    val paperTitles: Map<String, String> = remember(state.papers) {
        val papers = (state.papers as? ContentState.Success)?.value.orEmpty()
        papers.associate { it.id to it.title }
    }
    val allHistory: List<ExamHistoryItemDto> = remember(state.examHistory) {
        state.examHistory?.sortedByDescending { it.started_at } ?: emptyList()
    }

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
            Text("考场审阅", style = MaterialTheme.typography.titleLarge)
            Text(
                "考试与报告",
                style = MaterialTheme.typography.labelMedium,
                color = Subtle,
                modifier = Modifier.padding(top = 6.dp),
            )
        }

        state.resumeExamId?.let { resumeExamId ->
            SectionCard(title = "未完成的考试") {
                Text(
                    "检测到上次未收口的考试记录，进入后按服务端状态恢复作答。",
                    style = MaterialTheme.typography.bodyMedium,
                )
                Button(onClick = { onResumeExam(resumeExamId) }, modifier = Modifier.fillMaxWidth()) {
                    Text("继续考试")
                }
            }
        }

        ContentStateView(
            state = state.papers,
            loadingText = "正在加载考试记录…",
            emptyText = "暂无可考试卷",
            onRetry = viewModel::refresh,
            success = { papers ->
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    item(key = "history-header") {
                        Text(
                            "全部考试记录（${allHistory.size}）",
                            style = MaterialTheme.typography.titleMedium,
                        )
                    }
                    if (allHistory.isEmpty()) {
                        item(key = "history-empty") {
                            SectionCard(title = "考试记录") {
                                Text(
                                    "还没有考试记录。从下方选择试卷开考，交卷后这里会保留历次成绩与审阅报告。",
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    } else {
                        items(allHistory, key = { "h-${it.exam_id}" }) { item ->
                            HistoryRow(
                                item = item,
                                paperTitle = paperTitles[item.paper_id] ?: "试卷",
                                onOpenReport = { onOpenReview(item.exam_id) },
                            )
                        }
                    }
                    item(key = "papers-header") {
                        Text(
                            "选择试卷开考",
                            style = MaterialTheme.typography.titleMedium,
                            modifier = Modifier.padding(top = 6.dp),
                        )
                    }
                    val minePapers = papers.filter { it.mine }
                    val publicPapers = papers.filter { !it.mine }
                    items(minePapers, key = { it.id }) { paper ->
                        ExamPaperCard(paper = paper, onStart = { onStartPaper(paper.id) })
                    }
                    if (publicPapers.isNotEmpty()) {
                        item(key = "papers-public-header") {
                            Text(
                                "公共试卷（${publicPapers.size}）",
                                style = MaterialTheme.typography.bodyMedium,
                                color = Subtle,
                            )
                        }
                        items(publicPapers, key = { it.id }) { paper ->
                            ExamPaperCard(paper = paper, onStart = { onStartPaper(paper.id) })
                        }
                    }
                }
            },
        )
    }
}

/** 单条历史：卷名标题 + 成绩/对题数/日期 + 审阅报告入口 */
@Composable
private fun HistoryRow(
    item: ExamHistoryItemDto,
    paperTitle: String,
    onOpenReport: () -> Unit,
) {
    SectionCard(title = paperTitle) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                buildString {
                    append(item.score?.let { "$it 分" } ?: "未交卷")
                    item.correct_count?.let { append(" · $it/${item.question_count ?: "?"} 对") }
                    append(" · ${item.started_at.take(10)}")
                },
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.fillMaxWidth(0.6f),
            )
            if (item.has_report) {
                OutlinedButton(onClick = onOpenReport) { Text("审阅报告") }
            } else {
                Text("暂无报告", style = MaterialTheme.typography.bodySmall, color = Subtle)
            }
        }
    }
}

/** 考场版试卷卡：元数据 + 直接开考（学习入口留在「学习」标签页） */
@Composable
private fun ExamPaperCard(paper: PaperSummary, onStart: () -> Unit) {
    SectionCard(title = paper.title) {
        Text(
            paper.subtitle,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Metadata("科目", paper.subject)
            Metadata("难度", difficultyLabel(paper.difficulty))
            Metadata("时长", "${paper.durationMinutes} 分钟")
        }
        Button(onClick = onStart, modifier = Modifier.fillMaxWidth()) { Text("进入考场") }
    }
}

@Composable
private fun Metadata(label: String, value: String) {
    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(
            label,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(value, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium)
    }
}
