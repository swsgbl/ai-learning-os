package com.ailearningos.app.ui.study

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.ExamGateway
import com.ailearningos.app.data.remote.StudyViewDto
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * M15-024 学习模式（先学习再考试）：章节逐个学习——读目标与评估要点 →
 * 练该章的题（在考试流程里做）→ 下一章。全部章节完成后 → 整卷模拟考。
 */
data class StudyModeUiState(
    val loading: Boolean = false,
    val view: StudyViewDto? = null,
    val error: String? = null,
    val currentChapter: Int = 0,
)

class StudyModeViewModel(
    private val exams: ExamGateway?,
) : ViewModel() {
    private val _state = MutableStateFlow(StudyModeUiState())
    val state: StateFlow<StudyModeUiState> = _state.asStateFlow()

    fun load(paperId: String) {
        if (_state.value.loading || _state.value.view != null) return
        viewModelScope.launch {
            _state.value = _state.value.copy(loading = true, error = null)
            val result = try {
                exams?.paperStudy(paperId)
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (cause: Throwable) {
                null
            }
            _state.value = _state.value.copy(
                loading = false,
                view = result,
                error = if (result == null) "该试卷暂无学习视图（非定制课程）" else null,
            )
        }
    }

    fun nextChapter() {
        val v = _state.value.view ?: return
        if (_state.value.currentChapter < v.chapters.size - 1) {
            _state.value = _state.value.copy(currentChapter = _state.value.currentChapter + 1)
        }
    }

    fun prevChapter() {
        if (_state.value.currentChapter > 0) {
            _state.value = _state.value.copy(currentChapter = _state.value.currentChapter - 1)
        }
    }
}

@Composable
fun StudyModeScreen(
    viewModel: StudyModeViewModel,
    paperId: String,
    onStartPaperExam: () -> Unit,
    onBack: () -> Unit,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    LaunchedEffect(paperId) { viewModel.load(paperId) }

    Column(
        modifier = Modifier.verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("学习模式", style = MaterialTheme.typography.headlineSmall)
        when {
            state.loading -> Text("正在加载课程内容…", style = MaterialTheme.typography.bodyMedium)
            state.error != null -> {
                Text(state.error ?: "", style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.error)
                OutlinedButton(onClick = onBack) { Text("返回") }
            }
            state.view != null -> {
                val view = state.view!!
                Text(view.paper_title, style = MaterialTheme.typography.titleLarge)
                Text("学习目标：${view.goal}", style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                val chapter = view.chapters[state.currentChapter]
                ChapterCard(chapter, state.currentChapter, view.chapters.size)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (state.currentChapter > 0) {
                        OutlinedButton(onClick = viewModel::prevChapter) { Text("上一章") }
                    }
                    if (state.currentChapter < view.chapters.size - 1) {
                        Button(onClick = viewModel::nextChapter) { Text("下一章") }
                    } else {
                        Button(onClick = onStartPaperExam) { Text("学完了，去考试") }
                    }
                }
            }
        }
    }
}

@Composable
private fun ChapterCard(
    chapter: com.ailearningos.app.data.remote.StudyChapterDto,
    index: Int,
    total: Int,
) {
    Card {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("第${chapter.chapter_no}章 · ${chapter.title}", style = MaterialTheme.typography.titleMedium)
            Text("难度 ${chapter.difficulty}/5 · 进度 ${index + 1}/$total",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (chapter.objectives.isNotBlank()) {
                Text("学习目标", style = MaterialTheme.typography.titleSmall)
                Text(chapter.objectives, style = MaterialTheme.typography.bodyMedium)
            }
            if (chapter.assessment_stem.isNotBlank()) {
                Text("练一练（读完试着口头回答，再正式考试检验）", style = MaterialTheme.typography.titleSmall)
                Text(chapter.assessment_stem, style = MaterialTheme.typography.bodyMedium)
            }
        }
    }
}
