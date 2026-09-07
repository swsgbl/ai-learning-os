package com.ailearningos.app.ui.governance

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ailearningos.app.data.GovernanceGateway
import com.ailearningos.app.data.asAppError
import com.ailearningos.app.data.model.GovernanceAuditEntry
import com.ailearningos.app.data.model.OpsSnapshot
import com.ailearningos.app.data.model.ReleaseVersion
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * 治理页状态（M12-05）。三个只读面并发加载，互不阻塞：
 * - 版本：alembic current/head 原样呈现，客户端不替服务端判断迁移漂移；
 * - 快照：各计数/状态键由服务端统计口径产生，客户端只投影、不重算；
 * - 审计：固定 [GovernanceGateway.DEFAULT_LIMIT]，服务端响应包含 before/after 载荷，
 *   客户端不建模、不保留、不渲染，不本地持久化、不改写服务端字段；
 * - 已有数据在单项刷新失败时保留（只置 error，不清空），refresh() 即重试入口；
 * - 审计 admin-only 由服务端强制（learner 403 → FORBIDDEN），
 *   ViewModel 不猜角色、不在客户端做任何权限预判。
 */
data class GovernanceUiState(
    // 发布版本
    val version: ReleaseVersion? = null,
    val versionLoading: Boolean = true,
    val versionError: String? = null,
    // 运维快照
    val snapshot: OpsSnapshot? = null,
    val snapshotLoading: Boolean = true,
    val snapshotError: String? = null,
    // 审计留痕
    val auditEntries: List<GovernanceAuditEntry> = emptyList(),
    val auditLoading: Boolean = true,
    val auditError: String? = null,
)

class GovernanceViewModel(
    private val governance: GovernanceGateway,
) : ViewModel() {

    private val _state = MutableStateFlow(GovernanceUiState())
    val state: StateFlow<GovernanceUiState> = _state.asStateFlow()

    /** 请求序号守卫：晚到的旧响应不得覆盖新请求的结果（顺序竞争防护） */
    private var versionSeq = 0
    private var snapshotSeq = 0
    private var auditSeq = 0

    private var versionJob: Job? = null
    private var snapshotJob: Job? = null
    private var auditJob: Job? = null

    init {
        refresh()
    }

    /** 并发刷新三个只读面；任一面失败不影响其他面，重试即整体重入 */
    fun refresh() {
        loadVersion()
        loadSnapshot()
        loadAudit()
    }

    // ---------- 发布版本 ----------

    private fun loadVersion() {
        versionJob?.cancel()
        val seq = ++versionSeq
        versionJob = viewModelScope.launch {
            update { it.copy(versionLoading = true, versionError = null) }
            try {
                val version = governance.version()
                if (seq != versionSeq) return@launch
                update { it.copy(versionLoading = false, version = version) }
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (cause: Throwable) {
                if (seq != versionSeq) return@launch
                update { it.copy(versionLoading = false, versionError = cause.asAppError().userMessage) }
            }
        }
    }

    // ---------- 运维快照 ----------

    private fun loadSnapshot() {
        snapshotJob?.cancel()
        val seq = ++snapshotSeq
        snapshotJob = viewModelScope.launch {
            update { it.copy(snapshotLoading = true, snapshotError = null) }
            try {
                val snapshot = governance.opsSnapshot()
                if (seq != snapshotSeq) return@launch
                update { it.copy(snapshotLoading = false, snapshot = snapshot) }
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (cause: Throwable) {
                if (seq != snapshotSeq) return@launch
                update { it.copy(snapshotLoading = false, snapshotError = cause.asAppError().userMessage) }
            }
        }
    }

    // ---------- 审计留痕（admin-only 由服务端强制，不本地持久化） ----------

    private fun loadAudit() {
        auditJob?.cancel()
        val seq = ++auditSeq
        auditJob = viewModelScope.launch {
            update { it.copy(auditLoading = true, auditError = null) }
            try {
                val entries = governance.auditEntries(GovernanceGateway.DEFAULT_LIMIT)
                if (seq != auditSeq) return@launch
                update { it.copy(auditLoading = false, auditEntries = entries) }
            } catch (cancellation: CancellationException) {
                throw cancellation
            } catch (cause: Throwable) {
                if (seq != auditSeq) return@launch
                update { it.copy(auditLoading = false, auditError = cause.asAppError().userMessage) }
            }
        }
    }

    // ---------- 内部 ----------

    private fun update(transform: (GovernanceUiState) -> GovernanceUiState) {
        _state.value = transform(_state.value)
    }
}
