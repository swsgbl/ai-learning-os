package com.ailearningos.app.ui

import androidx.lifecycle.viewModelScope
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.model.SearchOutcome
import com.ailearningos.app.testutil.FakeSearchGateway
import com.ailearningos.app.testutil.MainDispatcherRule
import com.ailearningos.app.testutil.RecordedSearch
import com.ailearningos.app.testutil.appError
import com.ailearningos.app.testutil.searchOutcomeFixture
import com.ailearningos.app.testutil.searchProvidersFixture
import com.ailearningos.app.ui.search.SearchViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/**
 * 搜索页状态机（M12-04）：providers 加载与重试、计划预览、执行搜索
 * （成功/零结果/skipped/503/422）、空白输入本地拦截、query_id 回查、
 * 迟到响应与顺序竞争防护。
 *
 * 时序用例经 handler 钩子 + CompletableDeferred 门控（不依赖真实时间睡眠）；
 * 调度隔离约定与 ExamViewModelTest/VoiceViewModelTest 同款（finally 取消全部 viewModelScope）。
 */
class SearchViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private lateinit var gateway: FakeSearchGateway

    private val created = mutableListOf<SearchViewModel>()

    @Before
    fun setUp() {
        gateway = FakeSearchGateway()
    }

    private fun runSearchTest(block: suspend TestScope.() -> Unit) = runTest(
        mainDispatcherRule.testDispatcher.scheduler,
    ) {
        try {
            block()
        } finally {
            created.forEach { it.viewModelScope.cancel() }
        }
    }

    private fun newViewModel(): SearchViewModel {
        val model = SearchViewModel(search = gateway)
        created.add(model)
        return model
    }

    // ---------- providers 加载 ----------

    @Test
    fun `init loads providers and keeps availability honest`() = runSearchTest {
        val model = newViewModel()
        val state = model.state.value
        assertTrue(!state.providersLoading)
        assertNull(state.providersError)
        assertEquals(searchProvidersFixture(), state.providers)
    }

    @Test
    fun `providers failure shows error and retry recovers`() = runSearchTest {
        gateway.providersResult = Result.failure(appError(AppErrorKind.SERVER))
        val model = newViewModel()
        assertTrue(model.state.value.providersError != null)
        assertTrue(model.state.value.providers.isEmpty())

        gateway.providersResult = Result.success(searchProvidersFixture())
        model.loadProviders()
        assertNull(model.state.value.providersError)
        assertEquals(searchProvidersFixture(), model.state.value.providers)
    }

    @Test
    fun `stale providers response does not overwrite fresh reload`() = runSearchTest {
        val gate = CompletableDeferred<Unit>()
        var firstCall = true
        gateway.providersHandler = {
            if (firstCall) {
                firstCall = false
                gate.await() // 首次加载挂起（模拟慢网络）
                searchProvidersFixture().take(1)
            } else {
                searchProvidersFixture()
            }
        }
        val model = newViewModel() // init 同步执行到 gate.await() 挂起
        model.loadProviders() // 取消首次加载并立即完成全量刷新
        gate.complete(Unit) // 首次加载此刻恢复，已是过期响应
        val state = model.state.value
        assertNull(state.providersError)
        assertEquals(listOf("local-corpus", "cloud-web"), state.providers.map { it.name })
    }

    // ---------- 计划预览 ----------

    @Test
    fun `preview plan stores slots and per provider items`() = runSearchTest {
        val model = newViewModel()
        model.setQueryInput("2024 高等数学 选择题")
        model.previewPlan()
        val plan = model.state.value.plan
        assertNotNull(plan)
        assertEquals("高等数学", plan!!.slots.course)
        assertEquals(listOf("local-corpus", "cloud-web"), plan.items.map { it.provider })
        assertTrue(!plan.items[1].enabled)
        assertEquals(listOf("2024 高等数学 选择题"), gateway.planCalls)
    }

    @Test
    fun `blank query is rejected locally without request`() = runSearchTest {
        val model = newViewModel()
        model.setQueryInput("   ")
        model.previewPlan()
        model.search()
        assertTrue(gateway.planCalls.isEmpty())
        assertTrue(gateway.searchCalls.isEmpty())
        assertNotNull(model.state.value.planError)
        assertNotNull(model.state.value.searchError)
    }

    @Test
    fun `plan unknown provider shows BAD_RESPONSE message`() = runSearchTest {
        gateway.planResult = Result.failure(appError(AppErrorKind.BAD_RESPONSE))
        val model = newViewModel()
        model.setQueryInput("词")
        model.previewPlan()
        val state = model.state.value
        assertTrue(!state.planning)
        assertNotNull(state.planError)
        assertNull(state.plan)
    }

    @Test
    fun `plan without database shows SERVER message without fake plan`() = runSearchTest {
        gateway.planResult = Result.failure(appError(AppErrorKind.SERVER))
        val model = newViewModel()
        model.setQueryInput("词")
        model.previewPlan()
        val state = model.state.value
        assertNotNull(state.planError)
        assertNull(state.plan)
        assertTrue(!state.planning)
    }

    // ---------- 执行搜索 ----------

    @Test
    fun `search stores outcome with skipped visible`() = runSearchTest {
        val model = newViewModel()
        model.setQueryInput("正弦定理")
        model.search()
        val outcome = model.state.value.outcome
        assertNotNull(outcome)
        assertEquals(7L, outcome!!.queryId)
        assertEquals(1, outcome.resultCount)
        assertEquals(listOf("cloud-web"), outcome.skipped.map { it.provider })
        assertNull(model.state.value.searchError)
        assertEquals(listOf(RecordedSearch("正弦定理", null, 10)), gateway.searchCalls)
    }

    @Test
    fun `zero result outcome is kept as is without inventing results`() = runSearchTest {
        gateway.searchResult = Result.success(
            SearchOutcome(
                queryId = 9,
                query = "不存在的词",
                providersRequested = listOf("local-corpus"),
                results = emptyList(),
                skipped = emptyList(),
                resultCount = 0,
                durationMs = 2,
            ),
        )
        val model = newViewModel()
        model.setQueryInput("不存在的词")
        model.search()
        val outcome = model.state.value.outcome
        assertNotNull(outcome)
        assertEquals(0, outcome!!.resultCount)
        assertTrue(outcome.results.isEmpty())
        assertNull(model.state.value.searchError) // 零结果是事实，不是错误
    }

    @Test
    fun `search server failure keeps previous outcome and shows error`() = runSearchTest {
        val model = newViewModel()
        model.setQueryInput("正弦定理")
        model.search()
        val firstOutcome = model.state.value.outcome

        gateway.searchResult = Result.failure(appError(AppErrorKind.SERVER))
        model.setQueryInput("另一个词")
        model.search()
        val state = model.state.value
        assertNotNull(state.searchError)
        assertEquals(firstOutcome, state.outcome) // 失败不销毁旧结果，也不虚报新结果
    }

    @Test
    fun `search validation failure shows error`() = runSearchTest {
        gateway.searchResult = Result.failure(appError(AppErrorKind.BAD_RESPONSE))
        val model = newViewModel()
        model.setQueryInput("词")
        model.search()
        assertNotNull(model.state.value.searchError)
        assertNull(model.state.value.outcome)
    }

    @Test
    fun `rapid second search discards superseded first response`() = runSearchTest {
        val gate = CompletableDeferred<Unit>()
        gateway.searchHandler = { query ->
            if (query == "慢词") {
                gate.await() // 第一次搜索慢（模拟网络延迟）
                searchOutcomeFixture(queryId = 1, query = query)
            } else {
                null // 走默认立即结果
            }
        }
        val model = newViewModel()
        model.setQueryInput("慢词")
        model.search() // 挂起在 gate
        model.setQueryInput("快词")
        model.search() // 取消第一次并立即完成
        gate.complete(Unit) // 第一次搜索此刻恢复：已被取代，不得落地
        assertEquals(7L, model.state.value.outcome?.queryId)
        assertEquals(listOf("慢词", "快词"), gateway.searchCalls.map { it.query })
    }

    @Test
    fun `sequence guard drops stale response that escaped cancellation`() = runSearchTest {
        val gate = CompletableDeferred<Unit>()
        gateway.searchHandler = { query ->
            if (query == "慢词") {
                try {
                    gate.await()
                } catch (_: CancellationException) {
                    // 模拟真实网络层：响应已在飞行中完成，取消通知晚于结果到达
                }
                searchOutcomeFixture(queryId = 1, query = query)
            } else {
                null
            }
        }
        val model = newViewModel()
        model.setQueryInput("慢词")
        model.search() // 挂起在 gate
        model.setQueryInput("快词")
        model.search() // 取消标记 + 立即完成
        gate.complete(Unit) // 第一次的响应此刻送达：序号守卫必须拦截
        assertEquals(7L, model.state.value.outcome?.queryId)
    }

    // ---------- query_id 回查 ----------

    @Test
    fun `lookup record shows stored record`() = runSearchTest {
        val model = newViewModel()
        model.setRecordInput("42")
        model.lookupRecord()
        val record = model.state.value.record
        assertNotNull(record)
        assertEquals(7L, record!!.id)
        assertNull(model.state.value.recordError)
        assertEquals(listOf(42L), gateway.recordCalls)
    }

    @Test
    fun `lookup record missing id shows error`() = runSearchTest {
        gateway.recordResult = Result.failure(appError(AppErrorKind.NOT_FOUND))
        val model = newViewModel()
        model.setRecordInput("99999")
        model.lookupRecord()
        assertNotNull(model.state.value.recordError)
        assertNull(model.state.value.record)
    }

    @Test
    fun `invalid record input is rejected locally without request`() = runSearchTest {
        val model = newViewModel()
        model.setRecordInput("abc")
        model.lookupRecord()
        model.setRecordInput("-3")
        model.lookupRecord()
        assertTrue(gateway.recordCalls.isEmpty())
        assertNotNull(model.state.value.recordError)
    }

    @Test
    fun `lookup record failure keeps previous record and shows error`() = runSearchTest {
        val model = newViewModel()
        model.setRecordInput("42")
        model.lookupRecord()
        val first = model.state.value.record

        gateway.recordResult = Result.failure(appError(AppErrorKind.SERVER))
        model.setRecordInput("43")
        model.lookupRecord()
        val state = model.state.value
        assertNotNull(state.recordError)
        assertEquals(first, state.record)
    }
}
