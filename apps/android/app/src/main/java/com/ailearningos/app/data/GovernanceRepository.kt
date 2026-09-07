package com.ailearningos.app.data

import com.ailearningos.app.data.model.GovernanceAuditEntry
import com.ailearningos.app.data.model.OpsSnapshot
import com.ailearningos.app.data.model.PendingReviewDrafts
import com.ailearningos.app.data.model.ReleaseVersion

/**
 * 治理业务面（M12-05）。UI/ViewModel 依赖此接口；测试用 fake 实现。
 *
 * 服务端语义（不得在客户端削弱）：
 * - 版本/快照/审计全部服务端权威，客户端只投影，不重算计数、不改写状态键；
 * - alembic current/head 是否一致由用户读原值判断，客户端不吞差异；
 * - 审计 admin-only（learner 403 → FORBIDDEN）；无 DB 时 503 → SERVER，不虚报；
 * - limit 值域 1..500 由服务端钳制（越界收拢不报错），客户端不重复截断；
 * - 审计条目：服务端响应包含 before/after 载荷，客户端不建模/不保留/不渲染，
 *   移动端只做留痕回查列表。
 */
interface GovernanceGateway {
    suspend fun version(): ReleaseVersion

    suspend fun opsSnapshot(): OpsSnapshot

    suspend fun auditEntries(limit: Int = DEFAULT_LIMIT): List<GovernanceAuditEntry>

    companion object {
        /** 与服务端 list_audit.limit 默认一致 */
        const val DEFAULT_LIMIT = 100
    }
}

class GovernanceRepository(
    private val apiProvider: ApiProvider,
) : GovernanceGateway {

    override suspend fun version(): ReleaseVersion = withRemoteError {
        apiProvider.get().version().toDomain()
    }

    override suspend fun opsSnapshot(): OpsSnapshot = withRemoteError {
        apiProvider.get().opsSnapshot().toDomain()
    }

    override suspend fun auditEntries(limit: Int): List<GovernanceAuditEntry> = withRemoteError {
        apiProvider.get().audit(limit).map { it.toDomain() }
    }
}

// ---------- DTO → 领域 ----------

internal fun com.ailearningos.app.data.remote.VersionResponse.toDomain() = ReleaseVersion(
    version = version,
    gitCommit = gitCommit,
    alembicCurrent = alembicCurrent,
    alembicHead = alembicHead,
)

internal fun com.ailearningos.app.data.remote.PendingReviewDraftsResponse.toDomain() = PendingReviewDrafts(
    courseImport = courseImport,
    courseGeneration = courseGeneration,
    paperQuestion = paperQuestion,
    variantQuestion = variantQuestion,
)

internal fun com.ailearningos.app.data.remote.OpsSnapshotResponse.toDomain() = OpsSnapshot(
    generatedAt = generatedAt,
    databaseBackend = databaseBackend,
    usersByRole = usersByRole,
    papersTotal = papersTotal,
    resourcesByParseStatus = resourcesByParseStatus,
    parseJobsByStatus = parseJobsByStatus,
    pendingReviewDrafts = pendingReviewDrafts.toDomain(),
    voiceSessionsByStatus = voiceSessionsByStatus,
    searchQueriesTotal = searchQueriesTotal,
    auditEntriesTotal = auditEntriesTotal,
    workerRunning = workerRunning,
)

internal fun com.ailearningos.app.data.remote.AuditEntryResponse.toDomain() = GovernanceAuditEntry(
    id = id,
    actorId = actorId.value,
    actorUsername = actorUsername.value,
    action = action,
    targetType = targetType,
    targetId = targetId,
    requestId = requestId,
    createdAt = createdAt,
)
