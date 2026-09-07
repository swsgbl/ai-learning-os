package com.ailearningos.app.data

import com.ailearningos.app.data.model.SearchOutcome
import com.ailearningos.app.data.model.SearchPlan
import com.ailearningos.app.data.model.SearchPlanItem
import com.ailearningos.app.data.model.SearchPlanSlots
import com.ailearningos.app.data.model.SearchProviderEntry
import com.ailearningos.app.data.model.SearchRecord
import com.ailearningos.app.data.model.SearchResultItem
import com.ailearningos.app.data.model.SearchSkipped
import com.ailearningos.app.data.remote.SearchPlanRequest
import com.ailearningos.app.data.remote.SearchQueryRequest

/**
 * 搜索业务面（M12-04）。UI/ViewModel 依赖此接口；测试用 fake 实现。
 *
 * 服务端语义（不得在客户端削弱）：
 * - 计划、槽位识别、结果排序与去重全部由服务端完成——客户端只投影展示，
 *   不重排、不改写 rank_reason、不隐藏 skipped；
 * - 计划指定未知 provider 服务端直接 422（映射 BAD_RESPONSE）；
 *   执行指定未知 provider 进 skipped（响应体自带原因），两者语义不同；
 * - 无 DB 时服务端 503（映射 SERVER），不得虚报可用；
 * - limit 值域 1..50 由调用方保证，客户端不静默截断改写用户意图；
 * - 搜索记录只落服务端（queries 表），客户端不本地持久化任何搜索历史。
 */
interface SearchGateway {
    suspend fun providers(): List<SearchProviderEntry>

    suspend fun plan(query: String, providers: List<String>? = null): SearchPlan

    suspend fun search(query: String, providers: List<String>? = null, limit: Int = DEFAULT_LIMIT): SearchOutcome

    suspend fun record(queryId: Long): SearchRecord

    companion object {
        /** 与服务端 SearchRequest.limit 默认一致 */
        const val DEFAULT_LIMIT = 10
    }
}

class SearchRepository(
    private val apiProvider: ApiProvider,
) : SearchGateway {

    override suspend fun providers(): List<SearchProviderEntry> = withRemoteError {
        apiProvider.get().searchProviders().items.map { it.toDomain() }
    }

    override suspend fun plan(query: String, providers: List<String>?): SearchPlan = withRemoteError {
        apiProvider.get().searchPlan(SearchPlanRequest(query = query, providers = providers)).toDomain()
    }

    override suspend fun search(query: String, providers: List<String>?, limit: Int): SearchOutcome = withRemoteError {
        apiProvider.get().searchQueries(
            SearchQueryRequest(query = query, providers = providers, limit = limit),
        ).toDomain()
    }

    override suspend fun record(queryId: Long): SearchRecord = withRemoteError {
        apiProvider.get().searchRecord(queryId).toDomain()
    }
}

// ---------- DTO → 领域 ----------

internal fun com.ailearningos.app.data.remote.SearchProviderViewResponse.toDomain() = SearchProviderEntry(
    name = name,
    kind = kind,
    enabled = enabled,
    unavailableReason = unavailableReason,
)

internal fun com.ailearningos.app.data.remote.SearchPlanSlotsResponse.toDomain() = SearchPlanSlots(
    subject = subject,
    school = school,
    year = year,
    course = course,
    questionType = questionType,
    publicity = publicity,
)

internal fun com.ailearningos.app.data.remote.SearchPlanItemResponse.toDomain() = SearchPlanItem(
    provider = provider,
    query = query,
    enabled = enabled,
    unavailableReason = unavailableReason,
)

internal fun com.ailearningos.app.data.remote.SearchPlanResponse.toDomain() = SearchPlan(
    query = query,
    slots = slots.toDomain(),
    items = plan.map { it.toDomain() },
)

internal fun com.ailearningos.app.data.remote.SearchResultItemResponse.toDomain() = SearchResultItem(
    title = title,
    url = url,
    snippet = snippet,
    source = source,
    provider = provider,
    authority = authority,
    rankReason = rankReason,
)

internal fun com.ailearningos.app.data.remote.SearchSkippedResponse.toDomain() = SearchSkipped(
    provider = provider,
    reason = reason,
)

internal fun com.ailearningos.app.data.remote.SearchQueryResponse.toDomain() = SearchOutcome(
    queryId = queryId,
    query = query,
    providersRequested = providersRequested,
    results = results.map { it.toDomain() },
    skipped = skipped.map { it.toDomain() },
    resultCount = resultCount,
    durationMs = durationMs,
)

internal fun com.ailearningos.app.data.remote.SearchRecordResponse.toDomain() = SearchRecord(
    id = id,
    query = query,
    providersRequested = providersRequested,
    skipped = providersSkipped.map { it.toDomain() },
    resultCount = resultCount,
    durationMs = durationMs,
    results = results.map { it.toDomain() },
    createdAt = createdAt,
)
