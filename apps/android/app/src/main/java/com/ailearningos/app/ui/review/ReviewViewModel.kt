package com.ailearningos.app.ui.review

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.ExamGateway
import com.ailearningos.app.data.model.ExamReport
import com.ailearningos.app.data.model.ExamSubmission
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * 审阅呈现（M12-02）：
 * - 优先 GET report（总分/题分/概念掌握/错题/解析/补救任务）；
 * - report 404 → 诚实降级 GET submission（只呈现提交结果概要 + 明确标注
 *   「详细报告暂不可用」）；submission 也 404 → 「审阅报告尚未生成」；
 * - 绝不本地判分：correct/expected/score 全部来自服务端响应。
 */
sealed interface ReviewUiState {
    data object Loading : ReviewUiState
    data class Report(val report: ExamReport) : ReviewUiState
    data class Fallback(val submission: ExamSubmission) : ReviewUiState
    data class Error(val message: String) : ReviewUiState
}

class ReviewViewModel(
    private val exam: ExamGateway,
    private val examId: String,
) : ViewModel() {

    private val _state = MutableStateFlow<ReviewUiState>(ReviewUiState.Loading)
    val state: StateFlow<ReviewUiState> = _state.asStateFlow()

    private var loadJob: Job? = null

    init {
        refresh()
    }

    fun refresh() {
        loadJob?.cancel()
        loadJob = viewModelScope.launch {
            _state.value = ReviewUiState.Loading
            _state.value = try {
                ReviewUiState.Report(exam.report(examId))
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (cause: AppError) {
                if (cause.kind == AppErrorKind.NOT_FOUND) loadFallback() else ReviewUiState.Error(cause.userMessage)
            } catch (_: Throwable) {
                ReviewUiState.Error(AppError(AppErrorKind.UNEXPECTED).userMessage)
            }
        }
    }

    /** report 未生成（404）：诚实降级到 submission 概要 */
    private suspend fun loadFallback(): ReviewUiState = try {
        ReviewUiState.Fallback(exam.submission(examId))
    } catch (cancellation: CancellationException) {
        throw cancellation
    } catch (cause: AppError) {
        if (cause.kind == AppErrorKind.NOT_FOUND) {
            ReviewUiState.Error("审阅报告尚未生成，请稍后回来查看")
        } else {
            ReviewUiState.Error(cause.userMessage)
        }
    } catch (_: Throwable) {
        ReviewUiState.Error(AppError(AppErrorKind.UNEXPECTED).userMessage)
    }
}
