package com.ailearningos.app.data

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.testutil.FakeSettingsStore
import com.ailearningos.app.testutil.FakeTokenStore
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * M12-04 搜索仓储：端点契约（方法/路径/请求体/鉴权头）与错误映射
 * （401/404/422/503/网络）。全部走 MockWebServer fixture（显式绑 loopback）；
 * fixture 值为显式假值，不触网、不访问任何真实 search provider。
 */
class SearchRepositoryTest {

    private lateinit var server: MockWebServer
    private lateinit var tokenStore: FakeTokenStore
    private lateinit var tokenCache: SessionTokenCache
    private lateinit var repository: SearchRepository

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start(java.net.InetAddress.getLoopbackAddress(), 0)
        tokenStore = FakeTokenStore()
        tokenCache = SessionTokenCache(tokenStore)
        val apiProvider = ApiProvider(
            config = ApiConfigProvider(
                settingsStore = FakeSettingsStore("http://127.0.0.1:${server.port}/"),
                allowInsecureHttp = true,
            ),
            tokenCache = tokenCache,
        )
        repository = SearchRepository(apiProvider)
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun jsonResponse(body: String, code: Int = 200) = MockResponse()
        .setResponseCode(code)
        .setHeader("Content-Type", "application/json")
        .setBody(body)

    private val providersBody = """
        {"items":[
            {"name":"local-corpus","kind":"local-corpus","enabled":true,"unavailable_reason":null},
            {"name":"cloud-web","kind":"web","enabled":false,
             "unavailable_reason":"SEARCH_MODE=local：检索路由本地语料，cloud-web 不出站"}]}
    """.trimIndent()

    private val planBody = """
        {"query":"高等数学 选择题",
         "slots":{"subject":null,"school":null,"year":"2024","course":"高等数学",
                  "question_type":"选择题","publicity":null},
         "plan":[
            {"provider":"local-corpus","query":"高等数学 选择题 2024","enabled":true,"unavailable_reason":null},
            {"provider":"cloud-web","query":"高等数学 选择题 2024","enabled":false,
             "unavailable_reason":"SEARCH_MODE=local：检索路由本地语料，cloud-web 不出站"}]}
    """.trimIndent()

    private val queryBody = """
        {"query_id":42,"query":"正弦定理",
         "providers_requested":["local-corpus","cloud-web"],
         "results":[{"title":"正弦定理的内容","url":"https://x.invalid/a",
                     "snippet":"a/sinA=b/sinB","source":"local-corpus","provider":"local-corpus",
                     "authority":null,"rank_reason":"本地语料命中：关键词重叠"}],
         "skipped":[{"provider":"cloud-web","reason":"SEARCH_MODE=local：检索路由本地语料，cloud-web 不出站"}],
         "result_count":1,"duration_ms":12}
    """.trimIndent()

    private val recordBody = """
        {"id":42,"query":"正弦定理",
         "providers_requested":["local-corpus","cloud-web"],
         "providers_skipped":[{"provider":"cloud-web","reason":"SEARCH_MODE=local：检索路由本地语料，cloud-web 不出站"}],
         "result_count":1,"duration_ms":12,
         "results":[{"title":"正弦定理的内容","url":"https://x.invalid/a",
                     "snippet":"a/sinA=b/sinB","source":"local-corpus","provider":"local-corpus",
                     "authority":null,"rank_reason":"本地语料命中：关键词重叠"}],
         "created_at":"2026-09-07T02:00:00+00:00"}
    """.trimIndent()

    // ---------- providers ----------

    @Test
    fun `providers hits endpoint and parses availability matrix`() = runTest {
        server.enqueue(jsonResponse(providersBody))
        val providers = repository.providers()
        assertEquals(listOf("local-corpus", "cloud-web"), providers.map { it.name })
        assertTrue(providers[0].enabled)
        assertTrue(!providers[1].enabled)
        assertEquals("SEARCH_MODE=local：检索路由本地语料，cloud-web 不出站", providers[1].unavailableReason)
        assertEquals("/api/v1/search/providers", server.takeRequest().path)
    }

    @Test
    fun `providers carries bearer token when signed in`() = runTest {
        // 已登录形态：token 落 store 后经 ensureLoaded 进内存缓存（拦截器只读内存）
        tokenStore.seed("token-fixture-123")
        tokenCache.ensureLoaded()
        server.enqueue(jsonResponse(providersBody))
        repository.providers()
        val request = server.takeRequest()
        assertEquals("Bearer token-fixture-123", request.getHeader("Authorization"))
    }

    @Test
    fun `providers sends no auth header without token`() = runTest {
        server.enqueue(jsonResponse(providersBody))
        repository.providers()
        assertNull(server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun `providers no database maps to SERVER`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"Search requires a database"}""", code = 503))
        val error = runCatching { repository.providers() }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.SERVER)
    }

    // ---------- plan ----------

    @Test
    fun `plan posts query and parses slots and per provider items`() = runTest {
        server.enqueue(jsonResponse(planBody))
        val plan = repository.plan("高等数学 选择题")
        assertEquals("高等数学", plan.slots.course)
        assertNull(plan.slots.subject)
        assertEquals(listOf("local-corpus", "cloud-web"), plan.items.map { it.provider })
        assertTrue(!plan.items[1].enabled)
        assertEquals("SEARCH_MODE=local：检索路由本地语料，cloud-web 不出站", plan.items[1].unavailableReason)
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/v1/search/plan", request.path)
        // providers 缺省（null 不编码）：缺省即全部已注册源，与服务端语义一致
        assertEquals("""{"query":"高等数学 选择题"}""", request.body.readUtf8())
    }

    @Test
    fun `plan with providers filter encodes list in body`() = runTest {
        server.enqueue(jsonResponse(planBody))
        repository.plan("词", providers = listOf("local-corpus"))
        assertEquals(
            """{"query":"词","providers":["local-corpus"]}""",
            server.takeRequest().body.readUtf8(),
        )
    }

    @Test
    fun `plan unknown provider maps to BAD_RESPONSE`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"未知搜索源: no-such"}""", code = 422))
        val error = runCatching { repository.plan("词", providers = listOf("no-such")) }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.BAD_RESPONSE)
    }

    @Test
    fun `plan no database maps to SERVER`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"Search requires a database"}""", code = 503))
        val error = runCatching { repository.plan("词") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.SERVER)
    }

    // ---------- queries 执行 ----------

    @Test
    fun `search posts default limit and parses outcome`() = runTest {
        server.enqueue(jsonResponse(queryBody))
        val outcome = repository.search("正弦定理")
        assertEquals(42L, outcome.queryId)
        assertEquals(1, outcome.resultCount)
        assertEquals("本地语料命中：关键词重叠", outcome.results[0].rankReason)
        assertEquals(listOf("cloud-web"), outcome.skipped.map { it.provider })
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/v1/search/queries", request.path)
        // limit 默认 10 随体显式编码（encodeDefaults）；providers null 省略
        assertEquals("""{"query":"正弦定理","limit":10}""", request.body.readUtf8())
    }

    @Test
    fun `search with providers and limit encodes both`() = runTest {
        server.enqueue(jsonResponse(queryBody))
        repository.search("词", providers = listOf("local-corpus"), limit = 5)
        assertEquals(
            """{"query":"词","providers":["local-corpus"],"limit":5}""",
            server.takeRequest().body.readUtf8(),
        )
    }

    @Test
    fun `search keeps zero results without inventing items`() = runTest {
        server.enqueue(
            jsonResponse(
                """{"query_id":43,"query":"不存在的词","providers_requested":["local-corpus"],
                    "results":[],"skipped":[],"result_count":0,"duration_ms":3}""",
            ),
        )
        val outcome = repository.search("不存在的词")
        assertEquals(0, outcome.resultCount)
        assertTrue(outcome.results.isEmpty())
        assertTrue(outcome.skipped.isEmpty())
    }

    @Test
    fun `search no database maps to SERVER`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"Search requires a database"}""", code = 503))
        val error = runCatching { repository.search("词") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.SERVER)
    }

    @Test
    fun `search blank query maps to BAD_RESPONSE`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"查询词不能为空白"}""", code = 422))
        val error = runCatching { repository.search("   ") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.BAD_RESPONSE)
    }

    @Test
    fun `search unauthorized maps to UNAUTHORIZED`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"Not authenticated"}""", code = 401))
        val error = runCatching { repository.search("词") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.UNAUTHORIZED)
    }

    // ---------- queries 回查 ----------

    @Test
    fun `record gets by numeric id and parses stored record`() = runTest {
        server.enqueue(jsonResponse(recordBody))
        val record = repository.record(42L)
        assertEquals(42L, record.id)
        assertEquals("2026-09-07T02:00:00+00:00", record.createdAt)
        assertEquals(listOf("cloud-web"), record.skipped.map { it.provider })
        assertEquals("/api/v1/search/queries/42", server.takeRequest().path)
    }

    @Test
    fun `record missing id maps to NOT_FOUND`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"搜索记录不存在"}""", code = 404))
        val error = runCatching { repository.record(99999L) }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.NOT_FOUND)
    }

    @Test
    fun `record no database maps to SERVER`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"Search requires a database"}""", code = 503))
        val error = runCatching { repository.record(1L) }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.SERVER)
    }

    @Test
    fun `providers network failure maps to NETWORK`() = runTest {
        server.shutdown()
        val error = runCatching { repository.providers() }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.NETWORK)
    }
}
