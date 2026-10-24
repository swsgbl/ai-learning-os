package com.ailearningos.app.ui.learning

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.TextbookGateway
import com.ailearningos.app.data.remote.CatalogResourceDto
import com.ailearningos.app.data.remote.ImportJobDto
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** 教材工作台状态（M20-001）：目录搜索 + 我的书架 + 分态操作。 */
data class TextbookState(
    val query: String = "",
    val searching: Boolean = false,
    val searchResults: List<CatalogResourceDto>? = null,
    val imports: List<ImportJobDto>? = null,
    val loadingShelf: Boolean = true,
    val busyJobId: String? = null,
    val importingId: String? = null,
    val notice: String? = null,
    val error: String? = null,
)

/**
 * 教材→学习链路（M20-001）：搜索目录 → 导入（声明权利）→ 解析/OCR →
 * 概念提取 → 一键开冲刺（向导端点一次完成后六步）。
 */
class TextbookViewModel(
    private val textbook: TextbookGateway,
) : ViewModel() {

    private val _state = MutableStateFlow(TextbookState())
    val state: StateFlow<TextbookState> = _state.asStateFlow()

    private var searchJob: Job? = null

    init {
        refreshShelf()
    }

    fun setQuery(value: String) {
        _state.value = _state.value.copy(query = value)
    }

    fun dismissMessages() {
        _state.value = _state.value.copy(error = null, notice = null)
    }

    fun search() {
        val query = _state.value.query.trim()
        if (query.isEmpty()) return
        searchJob?.cancel()
        searchJob = viewModelScope.launch {
            _state.value = _state.value.copy(searching = true, error = null)
            try {
                val results = textbook.catalogSearch(query)
                _state.value = _state.value.copy(searching = false, searchResults = results)
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (cause: AppError) {
                _state.value = _state.value.copy(searching = false, error = cause.userMessage)
            } catch (_: Throwable) {
                _state.value = _state.value.copy(
                    searching = false,
                    error = AppError(AppErrorKind.UNEXPECTED).userMessage,
                )
            }
        }
    }

    fun refreshShelf() {
        viewModelScope.launch {
            _state.value = _state.value.copy(loadingShelf = true)
            val imports = try {
                textbook.myImports()
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (_: Throwable) {
                null
            }
            _state.value = _state.value.copy(loadingShelf = false, imports = imports)
        }
    }

    fun importResource(resourceId: String) = launchAction(importsBusy = resourceId) {
        textbook.createImport(resourceId)
        _state.value = _state.value.copy(notice = "已导入到我的教材（大书组装可能需 1-2 分钟）")
        refreshShelf()
    }

    fun parseJob(jobId: String, ocr: Boolean) = launchAction(jobId) {
        val result = textbook.parseImport(jobId, if (ocr) 15 else null)
        _state.value = _state.value.copy(
            notice = when {
                result.status == "parsed" -> "解析完成：${result.chunkCount} 个文本块 / ${result.pageCount} 页"
                result.status == "needs_ocr" && result.ocrPagesDone > 0 ->
                    "OCR ${result.ocrPagesDone} 页（置信 ${result.ocrMeanConfidence}）——扫描书分批解析中"
                else -> "扫描书（${result.lowTextPages} 低文本页）——点 OCR 解析分批处理"
            },
        )
        refreshShelf()
    }

    fun extractConcepts(jobId: String) = launchAction(jobId) {
        val result = textbook.extractConcepts(jobId)
        _state.value = _state.value.copy(notice = "提取 ${result.conceptCount} 个概念候选——请在 Web 端确认后一键开冲刺（或直接尝试一键开冲刺）")
        refreshShelf()
    }

    fun createSprint(jobId: String) = launchAction(jobId) {
        val result = textbook.createSprintFromTextbook(jobId)
        _state.value = _state.value.copy(
            notice = "冲刺已创建：${result.generated} 题已生成发布，蓝图" +
                (if (result.blueprintComplete) "完整" else "部分") +
                "——到「学习冲刺」开始前测",
        )
        refreshShelf()
    }

    private inline fun launchAction(
        importsBusy: String? = null,
        busyJobId: String? = null,
        crossinline block: suspend () -> Unit,
    ): Job = viewModelScope.launch {
        _state.value = _state.value.copy(
            busyJobId = busyJobId ?: importsBusy,
            importingId = importsBusy,
            error = null,
            notice = null,
        )
        try {
            block()
        } catch (cancellation: CancellationException) {
            throw cancellation
        } catch (cause: AppError) {
            _state.value = _state.value.copy(error = cause.userMessage)
        } catch (_: Throwable) {
            _state.value = _state.value.copy(
                error = AppError(AppErrorKind.UNEXPECTED).userMessage,
            )
        } finally {
            _state.value = _state.value.copy(busyJobId = null, importingId = null)
        }
    }
}
