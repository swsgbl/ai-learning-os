package com.ailearningos.app.ui.study

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
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.ailearningos.app.data.model.PaperSummary
import com.ailearningos.app.ui.common.ContentStateView
import com.ailearningos.app.ui.common.difficultyLabel
import com.ailearningos.app.ui.components.SectionCard
import com.ailearningos.app.ui.theme.Subtle

/**
 * 学习 / 考场首屏（M12-02，M12-03 增语音陪练入口）：真实试卷列表
 * （加载/空态/错误/重试）、断线恢复提示（仅 exam_id，点击进入考场后以 GET
 * 结果为准）、点击开考或语音陪练。
 */
@Composable
fun StudyScreen(
    viewModel: StudyViewModel,
    onStartPaper: (String) -> Unit,
    onResumeExam: (String) -> Unit,
    onStartVoicePaper: (String) -> Unit = {},
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

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
            Text("学习 / 考场", style = MaterialTheme.typography.titleLarge)
            Text(
                "试卷库",
                style = MaterialTheme.typography.labelMedium,
                color = Subtle,
                modifier = Modifier.padding(top = 6.dp),
            )
        }

        state.resumeExamId?.let { resumeExamId ->
            ResumeBanner(onResume = { onResumeExam(resumeExamId) })
        }

        ContentStateView(
            state = state.papers,
            loadingText = "正在加载试卷…",
            emptyText = "暂无可考试卷",
            onRetry = viewModel::refresh,
            success = { papers ->
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    items(papers, key = { it.id }) { paper ->
                        PaperCard(
                            paper = paper,
                            onStart = { onStartPaper(paper.id) },
                            onStartVoice = { onStartVoicePaper(paper.id) },
                        )
                    }
                }
            },
        )
    }
}

/** 恢复提示：本地只有 exam_id；是否仍在进行以进入考场后的服务端状态为准 */
@Composable
private fun ResumeBanner(onResume: () -> Unit) {
    SectionCard(title = "未完成的考试") {
        Text(
            "检测到上次未收口的考试记录。进入后按服务端状态恢复作答；" +
                "若考试已结束会直接进入审阅或重新开考。",
            style = MaterialTheme.typography.bodyMedium,
        )
        Button(onClick = onResume, modifier = Modifier.fillMaxWidth()) {
            Text("继续考试")
        }
    }
}

@Composable
private fun PaperCard(
    paper: PaperSummary,
    onStart: () -> Unit,
    onStartVoice: () -> Unit,
) {
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
            if (paper.year != null) {
                Metadata("年份", paper.year.toString())
            }
        }
        if (paper.tags.isNotEmpty()) {
            Text(
                paper.tags.joinToString(" · "),
                style = MaterialTheme.typography.bodySmall,
                color = Subtle,
            )
        }
        Button(onClick = onStart, modifier = Modifier.fillMaxWidth()) { Text("开始考试") }
        OutlinedButton(onClick = onStartVoice, modifier = Modifier.fillMaxWidth()) { Text("语音陪练") }
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
        Text(
            value,
            style = MaterialTheme.typography.bodySmall,
            fontWeight = FontWeight.Medium,
        )
    }
}
