package com.ailearningos.app.ui.progress

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ailearningos.app.core.AppError
import com.ailearningos.app.data.ExamGateway
import com.ailearningos.app.data.SystemGateway
import com.ailearningos.app.data.remote.DailyPlanDto
import com.ailearningos.app.data.remote.StudentStatesDto
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * M15-022 App 端工作台（与 Web /progress 同口径）：
 * 今日任务（新学/复习/错题重测）+ 薄弱概念 + 可练试卷统计。
 */
data class ProgressUiState(
    val loading: Boolean = false,
    val plan: DailyPlanDto? = null,
    val weakConceptCount: Int = 0,
    val paperCount: Int = 0,
    val error: String? = null,
)

class ProgressViewModel(
    private val system: SystemGateway,
    private val exams: ExamGateway? = null,
) : ViewModel() {

    private val _state = MutableStateFlow(ProgressUiState())
    val state: StateFlow<ProgressUiState> = _state.asStateFlow()

    fun load() {
        if (_state.value.loading) return
        viewModelScope.launch {
            _state.update { it.copy(loading = true, error = null) }
            var plan: DailyPlanDto? = null
            var weak = 0
            var papers = 0
            var failure: Throwable? = null
            // 三源并行容错：单源失败不拖垮整页（与 Web allSettled 同口径）
            val jobs = listOf(
                launch {
                    try {
                        plan = system.dailyPlan()
                    } catch (cause: Throwable) {
                        failure = cause
                    }
                },
                launch {
                    try {
                        val s = system.studentStates()
                        weak = s?.states?.count { it.forgetting_risk > 0.5 || it.mastery < 0.6 } ?: 0
                    } catch (_: Throwable) {
                    }
                },
                launch {
                    try {
                        papers = exams?.papers()?.size ?: 0
                    } catch (_: Throwable) {
                    }
                },
            )
            jobs.forEach { it.join() }
            _state.update {
                it.copy(
                    loading = false,
                    plan = plan,
                    weakConceptCount = weak,
                    paperCount = papers,
                    error = failure?.let { f -> (f as? com.ailearningos.app.core.AppError)?.userMessage ?: f.message ?: "加载失败" },
                )
            }
        }
    }
}
