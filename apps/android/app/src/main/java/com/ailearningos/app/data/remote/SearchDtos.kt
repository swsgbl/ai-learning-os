package com.ailearningos.app.data.remote

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * M12-04 搜索域 DTO：providers / plan / queries 执行 / queries 回查。
 * snake_case 与服务端 Pydantic 契约一一对应（services/api/app/api/routes/search.py）；
 * 槽位/authority/unavailable_reason 等可空字段保留 null（服务端未识别/未标注
 * 就是不虚报）；服务端演进新增字段时靠 ignoreUnknownKeys 容忍。
 */

// ---------- providers（GET /api/v1/search/providers） ----------

@Serializable
data class SearchProviderViewResponse(
    val name: String,
    val kind: String,
    val enabled: Boolean,
    @SerialName("unavailable_reason") val unavailableReason: String? = null,
)

@Serializable
data class SearchProvidersResponse(
    val items: List<SearchProviderViewResponse> = emptyList(),
)

// ---------- 计划预览（POST /api/v1/search/plan） ----------

@Serializable
data class SearchPlanRequest(
    val query: String,
    val providers: List<String>? = null,
)

@Serializable
data class SearchPlanSlotsResponse(
    val subject: String? = null,
    val school: String? = null,
    val year: String? = null,
    val course: String? = null,
    @SerialName("question_type") val questionType: String? = null,
    val publicity: String? = null,
)

@Serializable
data class SearchPlanItemResponse(
    val provider: String,
    val query: String,
    val enabled: Boolean,
    @SerialName("unavailable_reason") val unavailableReason: String? = null,
)

@Serializable
data class SearchPlanResponse(
    val query: String,
    val slots: SearchPlanSlotsResponse,
    val plan: List<SearchPlanItemResponse> = emptyList(),
)

// ---------- 执行搜索（POST /api/v1/search/queries） ----------

@Serializable
data class SearchQueryRequest(
    val query: String,
    val providers: List<String>? = null,
    /** 服务端默认 10、界 1..50；encodeDefaults 会随请求体显式带上（值域由调用方保证） */
    val limit: Int = 10,
)

@Serializable
data class SearchSkippedResponse(
    val provider: String,
    val reason: String,
)

@Serializable
data class SearchResultItemResponse(
    val title: String,
    val url: String,
    val snippet: String,
    val source: String,
    val provider: String,
    val authority: String? = null,
    @SerialName("rank_reason") val rankReason: String,
)

@Serializable
data class SearchQueryResponse(
    @SerialName("query_id") val queryId: Long,
    val query: String,
    @SerialName("providers_requested") val providersRequested: List<String> = emptyList(),
    val results: List<SearchResultItemResponse> = emptyList(),
    val skipped: List<SearchSkippedResponse> = emptyList(),
    @SerialName("result_count") val resultCount: Int,
    @SerialName("duration_ms") val durationMs: Int,
)

// ---------- 执行记录回查（GET /api/v1/search/queries/{query_id}） ----------

@Serializable
data class SearchRecordResponse(
    val id: Long,
    val query: String,
    @SerialName("providers_requested") val providersRequested: List<String> = emptyList(),
    @SerialName("providers_skipped") val providersSkipped: List<SearchSkippedResponse> = emptyList(),
    @SerialName("result_count") val resultCount: Int,
    @SerialName("duration_ms") val durationMs: Int,
    val results: List<SearchResultItemResponse> = emptyList(),
    @SerialName("created_at") val createdAt: String,
)
