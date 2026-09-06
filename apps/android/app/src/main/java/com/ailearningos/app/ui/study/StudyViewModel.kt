package com.ailearningos.app.ui.study

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.ExamGateway
import com.ailearningos.app.data.local.ExamResumeStore
import com.ailearningos.app.data.model.PaperSummary
import com.ailearningos.app.ui.common.ContentState
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class StudyState(
    /** 试卷列表四态（loading/empty/error/success） */
    val papers: ContentState<List<PaperSummary>> = ContentState.Loading,
    /** 本地恢复提示：仅 exam_id；是否有进行中考试以进入考场后的 GET 为准 */
    val resumeExamId: String? = null,
)

/**
 * 学习/考场首屏（M12-02）：真实试卷列表 + 恢复提示。
 * 开考/恢复的网络流程由考试页（ExamViewModel）负责，这里只做呈现与导航入口。
 */
class StudyViewModel(
    private val exam: ExamGateway,
    private val resumeStore: ExamResumeStore,
) : ViewModel() {

    private val _state = MutableStateFlow(StudyState())
    val state: StateFlow<StudyState> = _state.asStateFlow()

    private var loadJob: Job? = null

    init {
        refresh()
    }

    fun refresh() {
        loadJob?.cancel()
        loadJob = viewModelScope.launch {
            _state.value = _state.value.copy(papers = ContentState.Loading)
            val resumeExamId = try {
                resumeStore.load()
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (_: Throwable) {
                // 恢复槽读取失败不阻塞列表（它只是提示，不是真相源）
                null
            }
            val papers = try {
                exam.papers().let { list ->
                    // 空列表是正常到达态（EmptyView 提示），不是 Success 空白
                    if (list.isEmpty()) ContentState.Empty else ContentState.Success(list)
                }
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (cause: AppError) {
                ContentState.Error(cause.userMessage)
            } catch (_: Throwable) {
                ContentState.Error(AppError(AppErrorKind.UNEXPECTED).userMessage)
            }
            _state.value = StudyState(papers = papers, resumeExamId = resumeExamId)
        }
    }
}
