package com.ailearningos.app.data.model

/**
 * M12-05 治理域领域视图。
 *
 * 服务端语义保留（不虚报、不加工）：
 * - [ReleaseVersion.alembicCurrent] != [ReleaseVersion.alembicHead] 即迁移漂移，
 *   客户端原样呈现两个值，不替服务端下结论；
 * - 快照各计数/状态键由服务端统计口径产生，客户端不重算、不过滤键；
 * - [GovernanceAuditEntry] 不含 before/after 载荷（服务端响应包含、客户端不建模/不保留/不渲染）；
 *   actor 为空（系统动作）时 actorId/actorUsername 为 null，原样展示；
 * - 审计为 admin-only：learner 403 由错误映射层呈现，客户端不绕行。
 */

/** 发布版本指纹（GET /api/v1/version 投影） */
data class ReleaseVersion(
    val version: String,
    val gitCommit: String,
    val alembicCurrent: String,
    val alembicHead: String,
)

/** 待复核草稿计数（按草稿类型分桶） */
data class PendingReviewDrafts(
    val courseImport: Int,
    val courseGeneration: Int,
    val paperQuestion: Int,
    val variantQuestion: Int,
)

/** 运维快照（GET /api/v1/system/ops-snapshot 投影，状态/字符串键原词保留） */
data class OpsSnapshot(
    val generatedAt: String,
    val databaseBackend: String,
    val usersByRole: Map<String, Int>,
    val papersTotal: Int,
    val resourcesByParseStatus: Map<String, Int>,
    val parseJobsByStatus: Map<String, Int>,
    val pendingReviewDrafts: PendingReviewDrafts,
    val voiceSessionsByStatus: Map<String, Int>,
    val searchQueriesTotal: Int,
    val auditEntriesTotal: Int,
    val workerRunning: Boolean,
)

/** 审计条目（GET /api/v1/audit 投影；无 before/after 载荷） */
data class GovernanceAuditEntry(
    val id: Long,
    val actorId: String?,
    val actorUsername: String?,
    val action: String,
    val targetType: String,
    val targetId: String,
    val requestId: String,
    val createdAt: String,
)
