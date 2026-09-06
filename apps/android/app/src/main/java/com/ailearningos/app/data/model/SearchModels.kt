package com.ailearningos.app.data.model

/**
 * M12-04 搜索域领域视图。
 *
 * 服务端语义保留（不虚报、不重排、不隐藏）：
 * - [SearchProviderEntry.enabled]=false 时 [SearchProviderEntry.unavailableReason]
 *   是服务端给出的禁用原因，原样展示（如 SEARCH_MODE=local 不出站）；
 * - 结果顺序与 [SearchResultItem.rankReason] 由服务端 rank_and_dedup 决定，
 *   客户端不去重、不重排、不改写理由；
 * - [SearchOutcome.skipped] 是被弃用的搜索源及原因，全部可见；
 * - 槽位未识别即 null（服务端不猜语义，客户端同样不猜）；
 * - 搜索记录只存在服务端（queries 表），客户端不本地持久化。
 */

/** 搜索源注册表条目（GET /search/providers 投影） */
data class SearchProviderEntry(
    val name: String,
    val kind: String,
    val enabled: Boolean,
    val unavailableReason: String?,
)

/** 查询计划槽位（六槽位，未识别为 null） */
data class SearchPlanSlots(
    val subject: String?,
    val school: String?,
    val year: String?,
    val course: String?,
    val questionType: String?,
    val publicity: String?,
)

/** 计划内单源条目（不可用源同样出计划并带原因） */
data class SearchPlanItem(
    val provider: String,
    val query: String,
    val enabled: Boolean,
    val unavailableReason: String?,
)

data class SearchPlan(
    val query: String,
    val slots: SearchPlanSlots,
    val items: List<SearchPlanItem>,
)

/** 被弃用的搜索源及原因（未知源/不可用/执行失败 fail-closed） */
data class SearchSkipped(
    val provider: String,
    val reason: String,
)

/** 单条结果（authority 未标注为 null；rank_reason 为服务端排序理由） */
data class SearchResultItem(
    val title: String,
    val url: String,
    val snippet: String,
    val source: String,
    val provider: String,
    val authority: String?,
    val rankReason: String,
)

/** 执行结果（POST /search/queries 响应投影） */
data class SearchOutcome(
    val queryId: Long,
    val query: String,
    val providersRequested: List<String>,
    val results: List<SearchResultItem>,
    val skipped: List<SearchSkipped>,
    val resultCount: Int,
    val durationMs: Int,
)

/** 执行记录（GET /search/queries/{id} 回查投影；服务端是唯一可回查来源） */
data class SearchRecord(
    val id: Long,
    val query: String,
    val providersRequested: List<String>,
    val skipped: List<SearchSkipped>,
    val resultCount: Int,
    val durationMs: Int,
    val results: List<SearchResultItem>,
    val createdAt: String,
)
