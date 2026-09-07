package com.ailearningos.app.ui.search

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ailearningos.app.data.SearchGateway
import com.ailearningos.app.data.asAppError
import com.ailearningos.app.data.model.SearchOutcome
import com.ailearningos.app.data.model.SearchPlan
import com.ailearningos.app.data.model.SearchProviderEntry
import com.ailearningos.app.data.model.SearchRecord
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * 搜索页状态（M12-04）。「复杂留给系统，简单留给用户」：
 * - 进入即加载搜索源注册表（可用性/禁用原因如实展示，不虚报）；
 * - 计划预览与执行都由服务端完成，客户端只投影槽位/计划/结果/skipped；
 * - 空白查询词本地拦截提示（省一次注定 422 的请求），其余校验交给服务端；
 * - 本地不持久化任何搜索历史/结果；query_id 回查以服务端记录为唯一来源。
 */
data class SearchUiState(
    // 搜索源注册表
    val providersLoading: Boolean = true,
    val providers: List<SearchProviderEntry> = emptyList(),
    val providersError: String? = null,
    // 查询输入
    val queryInput: String = "",
    // 计划预览
    val planning: Boolean = false,
    val plan: SearchPlan? = null,
    val planError: String? = null,
    // 执行搜索
    val searching: Boolean = false,
    val outcome: SearchOutcome? = null,
    val searchError: String? = null,
    // query_id 回查
    val recordInput: String = "",
    val recordLoading: Boolean = false,
    val record: SearchRecord? = null,
    val recordError: String? = null,
)

class SearchViewModel(
    private val search: SearchGateway,
) : ViewModel() {

    private val _state = MutableStateFlow(SearchUiState())
    val state: StateFlow<SearchUiState> = _state.asStateFlow()

    /** 请求序号守卫：晚到的旧响应不得覆盖新请求的结果（顺序竞争防护） */
    private var providersSeq = 0
    private var planSeq = 0
    private var searchSeq = 0
    private var recordSeq = 0

    private var providersJob: Job? = null
    private var planJob: Job? = null
    private var searchJob: Job? = null
    private var recordJob: Job? = null

    init {
        loadProviders()
    }

    // ---------- 搜索源注册表 ----------

    fun loadProviders() {
        providersJob?.cancel()
        val seq = ++providersSeq
        providersJob = viewModelScope.launch {
            update { it.copy(providersLoading = true, providersError = null) }
            try {
                val providers = search.providers()
                if (seq != providersSeq) return@launch
                update { it.copy(providersLoading = false, providers = providers) }
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (cause: Throwable) {
                if (seq != providersSeq) return@launch
                update { it.copy(providersLoading = false, providersError = cause.asAppError().userMessage) }
            }
        }
    }

    // ---------- 查询输入 ----------

    fun setQueryInput(value: String) = update { it.copy(queryInput = value) }

    // ---------- 计划预览（只出计划不执行） ----------

    fun previewPlan() {
        val query = _state.value.queryInput.trim()
        if (query.isEmpty()) {
            update { it.copy(planError = BLANK_QUERY_NOTICE) }
            return
        }
        planJob?.cancel()
        val seq = ++planSeq
        planJob = viewModelScope.launch {
            update { it.copy(planning = true, planError = null) }
            try {
                val plan = search.plan(query)
                if (seq != planSeq) return@launch
                update { it.copy(planning = false, plan = plan) }
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (cause: Throwable) {
                if (seq != planSeq) return@launch
                update { it.copy(planning = false, planError = cause.asAppError().userMessage) }
            }
        }
    }

    // ---------- 执行搜索 ----------

    fun search() {
        val query = _state.value.queryInput.trim()
        if (query.isEmpty()) {
            update { it.copy(searchError = BLANK_QUERY_NOTICE) }
            return
        }
        searchJob?.cancel()
        val seq = ++searchSeq
        searchJob = viewModelScope.launch {
            update { it.copy(searching = true, searchError = null) }
            try {
                val outcome = search.search(query)
                if (seq != searchSeq) return@launch
                update { it.copy(searching = false, outcome = outcome) }
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (cause: Throwable) {
                if (seq != searchSeq) return@launch
                update { it.copy(searching = false, searchError = cause.asAppError().userMessage) }
            }
        }
    }

    // ---------- query_id 回查（服务端记录为唯一来源） ----------

    fun setRecordInput(value: String) = update { it.copy(recordInput = value) }

    fun lookupRecord() {
        val input = _state.value.recordInput.trim()
        val queryId = input.toLongOrNull()
        if (queryId == null || queryId <= 0) {
            update { it.copy(recordError = BAD_QUERY_ID_NOTICE) }
            return
        }
        recordJob?.cancel()
        val seq = ++recordSeq
        recordJob = viewModelScope.launch {
            update { it.copy(recordLoading = true, recordError = null) }
            try {
                val record = search.record(queryId)
                if (seq != recordSeq) return@launch
                update { it.copy(recordLoading = false, record = record) }
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (cause: Throwable) {
                if (seq != recordSeq) return@launch
                update { it.copy(recordLoading = false, recordError = cause.asAppError().userMessage) }
            }
        }
    }

    // ---------- 内部 ----------

    private fun update(transform: (SearchUiState) -> SearchUiState) {
        _state.value = transform(_state.value)
    }

    private companion object {
        /** 空白查询本地拦截（与服务端「查询词不能为空白」422 同语义，省一次请求） */
        const val BLANK_QUERY_NOTICE = "请输入查询词"

        /** 回查编号非法本地拦截（非正整数在服务端同样是 422） */
        const val BAD_QUERY_ID_NOTICE = "请输入有效的查询编号（正整数）"
    }
}
