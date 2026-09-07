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
 * M12-05 治理仓储：成功路径契约（方法/路径/limit 查询参数/字段映射）。
 * 全部走 MockWebServer fixture（显式绑 loopback）；fixture 值为显式假值，
 * 不触网、不访问任何真实治理端点。计数/状态键由服务端产生，这里只验证
 * 客户端原样投影（不重算、不过滤、不虚报）。
 */
class GovernanceRepositoryTest {

    private lateinit var server: MockWebServer
    private lateinit var tokenStore: FakeTokenStore
    private lateinit var tokenCache: SessionTokenCache
    private lateinit var repository: GovernanceRepository

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
        repository = GovernanceRepository(apiProvider)
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun jsonResponse(body: String, code: Int = 200) = MockResponse()
        .setResponseCode(code)
        .setHeader("Content-Type", "application/json")
        .setBody(body)

    private val versionBody = """
        {"version":"1.2.3","git_commit":"9f8e7d6c",
         "alembic_current":"a1b2c3d4e5f6","alembic_head":"a1b2c3d4e5f6"}
    """.trimIndent()

    private val opsSnapshotBody = """
        {"generated_at":"2026-09-07T02:00:00+00:00",
         "database_backend":"postgresql",
         "users_by_role":{"learner":120,"staff":8,"admin":3},
         "papers_total":456,
         "resources_by_parse_status":{"pending":12,"parsed":340,"failed":4},
         "parse_jobs_by_status":{"queued":2,"running":1,"succeeded":900,"failed":3},
         "pending_review_drafts":{"course_import":1,"course_generation":2,
                                  "paper_question":3,"variant_question":4},
         "voice_sessions_by_status":{"running":5,"completed":210},
         "search_queries_total":789,
         "audit_entries_total":3210,
         "worker_running":true}
    """.trimIndent()

    private val auditBody = """
        [{"id":7,"actor_id":"user-1","actor_username":"admin-wu",
          "action":"paper.publish","target_type":"paper","target_id":"paper-9",
          "request_id":"req-abc","created_at":"2026-09-07T01:23:45+00:00"},
         {"id":6,"actor_id":null,"actor_username":null,
          "action":"worker.tick","target_type":"job","target_id":"job-1",
          "request_id":"req-sys","created_at":"2026-09-07T01:00:00+00:00"}]
    """.trimIndent()

    // ---------- version ----------

    @Test
    fun `version hits endpoint and parses release fingerprint fields`() = runTest {
        server.enqueue(jsonResponse(versionBody))
        val version = repository.version()
        assertEquals("1.2.3", version.version)
        assertEquals("9f8e7d6c", version.gitCommit)
        assertEquals("a1b2c3d4e5f6", version.alembicCurrent)
        assertEquals("a1b2c3d4e5f6", version.alembicHead)
        val request = server.takeRequest()
        assertEquals("GET", request.method)
        assertEquals("/api/v1/version", request.path)
    }

    // ---------- ops snapshot ----------

    @Test
    fun `opsSnapshot hits endpoint and maps maps, pending drafts and worker flag`() = runTest {
        server.enqueue(jsonResponse(opsSnapshotBody))
        val snapshot = repository.opsSnapshot()
        assertEquals("2026-09-07T02:00:00+00:00", snapshot.generatedAt)
        assertEquals("postgresql", snapshot.databaseBackend)
        assertEquals(mapOf("learner" to 120, "staff" to 8, "admin" to 3), snapshot.usersByRole)
        assertEquals(456, snapshot.papersTotal)
        assertEquals(mapOf("pending" to 12, "parsed" to 340, "failed" to 4), snapshot.resourcesByParseStatus)
        assertEquals(mapOf("queued" to 2, "running" to 1, "succeeded" to 900, "failed" to 3), snapshot.parseJobsByStatus)
        assertEquals(1, snapshot.pendingReviewDrafts.courseImport)
        assertEquals(2, snapshot.pendingReviewDrafts.courseGeneration)
        assertEquals(3, snapshot.pendingReviewDrafts.paperQuestion)
        assertEquals(4, snapshot.pendingReviewDrafts.variantQuestion)
        assertEquals(mapOf("running" to 5, "completed" to 210), snapshot.voiceSessionsByStatus)
        assertEquals(789, snapshot.searchQueriesTotal)
        assertEquals(3210, snapshot.auditEntriesTotal)
        assertTrue(snapshot.workerRunning)
        val request = server.takeRequest()
        assertEquals("GET", request.method)
        assertEquals("/api/v1/system/ops-snapshot", request.path)
    }

    // ---------- audit ----------

    @Test
    fun `audit hits endpoint with default limit 100 and maps actor, action and target`() = runTest {
        server.enqueue(jsonResponse(auditBody))
        val entries = repository.auditEntries()
        assertEquals(2, entries.size)
        assertEquals(7L, entries[0].id)
        assertEquals("user-1", entries[0].actorId)
        assertEquals("admin-wu", entries[0].actorUsername)
        assertEquals("paper.publish", entries[0].action)
        assertEquals("paper", entries[0].targetType)
        assertEquals("paper-9", entries[0].targetId)
        // 系统动作 actor 为空：原样投影为 null，不虚报、不兜底命名
        assertEquals(6L, entries[1].id)
        assertNull(entries[1].actorId)
        assertNull(entries[1].actorUsername)
        assertEquals("worker.tick", entries[1].action)
        assertEquals("job", entries[1].targetType)
        assertEquals("job-1", entries[1].targetId)
        val request = server.takeRequest()
        assertEquals("GET", request.method)
        assertEquals("/api/v1/audit?limit=100", request.path)
    }

    @Test
    fun `audit empty list stays empty without inventing entries`() = runTest {
        server.enqueue(jsonResponse("[]"))
        val entries = repository.auditEntries()
        assertTrue(entries.isEmpty())
        assertEquals("/api/v1/audit?limit=100", server.takeRequest().path)
    }

    // ---------- 鉴权契约 ----------

    @Test
    fun `opsSnapshot request carries Bearer token seeded into token store`() = runTest {
        // 模拟「上次登录遗留」token：seed 进 FakeTokenStore 后惰性加载进内存缓存，
        // OkHttp 拦截器经 peek() 读到值 → 请求必须带 Authorization: Bearer ...
        tokenStore.seed("token-fixture-123")
        tokenCache.ensureLoaded()
        server.enqueue(jsonResponse(opsSnapshotBody))
        repository.opsSnapshot()
        val request = server.takeRequest()
        assertEquals("Bearer token-fixture-123", request.getHeader("Authorization"))
    }

    @Test
    fun `version request sends no Authorization header when no token seeded`() = runTest {
        // 未 seed（未登录/已登出）：peek() 为 null → 原样放行，不得拼出任何
        // Authorization 头（含 "Bearer null" 之类的非法兜底）
        server.enqueue(jsonResponse(versionBody))
        repository.version()
        assertNull(server.takeRequest().getHeader("Authorization"))
    }

    // ---------- 错误映射 ----------

    @Test
    fun `opsSnapshot 403 maps to FORBIDDEN`() = runTest {
        // learner 访问 admin-only 快照：映射只看状态码；
        // 服务端 detail 文案不进入任何用户可见输出（脱敏边界）
        server.enqueue(jsonResponse("""{"detail":"admin only"}""", code = 403))
        val error = runCatching { repository.opsSnapshot() }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.FORBIDDEN)
    }

    @Test
    fun `opsSnapshot 401 maps to UNAUTHORIZED`() = runTest {
        // 凭据缺失/失效：401 → UNAUTHORIZED，不降级、不吞错
        server.enqueue(jsonResponse("""{"detail":"Not authenticated"}""", code = 401))
        val error = runCatching { repository.opsSnapshot() }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.UNAUTHORIZED)
    }

    @Test
    fun `opsSnapshot 503 maps to SERVER`() = runTest {
        // 服务端 DB 不可用等 5xx：503 → SERVER，不虚报快照数据、不吞错
        server.enqueue(jsonResponse("""{"detail":"database unavailable"}""", code = 503))
        val error = runCatching { repository.opsSnapshot() }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.SERVER)
    }

    @Test
    fun `version network failure after server shutdown maps to NETWORK`() = runTest {
        // 服务端关闭 → 连接层失败（ConnectException 等传输层 IO）：映射 NETWORK，
        // 不得误报为 SERVER 或吞成 null。tearDown 的 shutdown 幂等，可安全重复调用
        server.shutdown()
        val error = runCatching { repository.version() }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.NETWORK)
    }
}
