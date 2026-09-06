package com.ailearningos.app.data

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.testutil.FakeSettingsStore
import com.ailearningos.app.testutil.FakeTokenStore
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withContext
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.TimeUnit

/**
 * API 成功 / 401 / 网络错误 / 登录成功失败 / logout 清理 / Authorization 注入。
 * 全部走 MockWebServer fixture；fixture 值均为显式假值，不在输出中打印。
 */
class AuthRepositoryTest {

    private lateinit var server: MockWebServer
    private lateinit var tokenStore: FakeTokenStore
    private lateinit var repository: AuthRepository

    /** 测试 fixture（显式假值，不代表任何真实凭据） */
    private val fixtureToken = "fixture-access-token-do-not-use"

    @Before
    fun setUp() {
        server = MockWebServer()
        // 显式绑 loopback：server.url() 在部分机器（Docker Desktop）会把 127.0.0.1
        // 反解成 kubernetes.docker.internal 之类的别名，debug 策略会正确拒绝；
        // 直接用 127.0.0.1:port 构造 base URL，与 debug 允许的回环形态一致。
        server.start(java.net.InetAddress.getLoopbackAddress(), 0)
        tokenStore = FakeTokenStore()
        val tokenCache = SessionTokenCache(tokenStore)
        val apiProvider = ApiProvider(
            config = ApiConfigProvider(
                settingsStore = FakeSettingsStore("http://127.0.0.1:${server.port}/"),
                allowInsecureHttp = true,
            ),
            tokenCache = tokenCache,
        )
        repository = AuthRepository(apiProvider, tokenCache)
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun jsonResponse(body: String, code: Int = 200) = MockResponse()
        .setResponseCode(code)
        .setHeader("Content-Type", "application/json")
        .setBody(body)

    // ---------- /health ----------

    @Test
    fun `health reports service ok`() = runTest {
        server.enqueue(jsonResponse("""{"status":"ok","service":"ai-learning-os-api"}"""))
        assertTrue(repository.healthOk())
        assertEquals("/health", server.takeRequest().path)
    }

    @Test
    fun `health non-ok status maps to false`() = runTest {
        server.enqueue(jsonResponse("""{"status":"degraded"}"""))
        assertTrue(!repository.healthOk())
    }

    @Test
    fun `health network failure maps to NETWORK`() = runTest {
        server.shutdown()
        val error = runCatching { repository.healthOk() }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.NETWORK)
    }

    // ---------- /auth/status ----------

    @Test
    fun `authEnabled reads server switch`() = runTest {
        server.enqueue(jsonResponse("""{"auth_enabled":true}"""))
        assertTrue(repository.authEnabled())
        assertEquals("/api/v1/auth/status", server.takeRequest().path)
    }

    // ---------- /system/privacy ----------

    @Test
    fun `privacy snapshot parsed`() = runTest {
        server.enqueue(
            jsonResponse(
                """
                {"model_route":"local","voice_mode":"local","search_mode":"cloud",
                 "store_audio":false,"send_context_to_cloud":true}
                """.trimIndent(),
            ),
        )
        val snapshot = repository.privacy()
        assertEquals("local", snapshot.modelRoute)
        assertEquals("cloud", snapshot.searchMode)
        assertTrue(snapshot.sendsContextToCloud)
        assertEquals("/api/v1/system/privacy", server.takeRequest().path)
    }

    // ---------- login ----------

    @Test
    fun `login success saves token and uses Authorization on me`() = runTest {
        server.enqueue(jsonResponse("""{"access_token":"$fixtureToken","token_type":"bearer"}"""))
        server.enqueue(
            jsonResponse("""{"id":"u1","username":"alice","role":"learner","created_at":"2026-01-01T00:00:00+00:00"}"""),
        )

        val user = repository.login("alice", "pw12345678")

        assertEquals("alice", user.username)
        assertEquals(fixtureToken, tokenStore.load())
        val loginRequest = server.takeRequest()
        assertEquals("/api/v1/auth/login", loginRequest.path)
        assertNull("登录请求不应携带 Authorization", loginRequest.getHeader("Authorization"))
        assertEquals(
            """{"username":"alice","password":"pw12345678"}""",
            loginRequest.body.readUtf8(),
        )
        val meRequest = server.takeRequest()
        assertEquals("/api/v1/auth/me", meRequest.path)
        assertEquals("Bearer $fixtureToken", meRequest.getHeader("Authorization"))
    }

    @Test
    fun `login wrong credentials 401 maps to UNAUTHORIZED and saves nothing`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"用户名或密码错误"}""", code = 401))
        val error = runCatching { repository.login("alice", "wrong-password") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.UNAUTHORIZED)
        assertNull("失败登录不得留下 token", tokenStore.load())
    }

    @Test
    fun `login server failure maps to SERVER and saves nothing`() = runTest {
        server.enqueue(MockResponse().setResponseCode(500))
        val error = runCatching { repository.login("alice", "pw12345678") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.SERVER)
        assertNull(tokenStore.load())
    }

    @Test
    fun `login network failure maps to NETWORK`() = runTest {
        server.shutdown()
        val error = runCatching { repository.login("alice", "pw12345678") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.NETWORK)
    }

    @Test
    fun `login success with failing me rolls back token`() = runTest {
        server.enqueue(jsonResponse("""{"access_token":"$fixtureToken","token_type":"bearer"}"""))
        server.enqueue(MockResponse().setResponseCode(503))

        val error = runCatching { repository.login("alice", "pw12345678") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.SERVER)
        assertNull("半个会话必须回滚", tokenStore.load())
    }

    @Test
    fun `login cancelled during me rolls back token and propagates cancellation`() = runTest {
        server.enqueue(jsonResponse("""{"access_token":"$fixtureToken","token_type":"bearer"}"""))
        server.enqueue(
            jsonResponse("""{"id":"u1","username":"alice","role":"learner"}""")
                .setBodyDelay(3, TimeUnit.SECONDS),
        )

        var caught: Throwable? = null
        val job = launch {
            try {
                repository.login("alice", "pw12345678")
            } catch (t: Throwable) {
                caught = t
            }
        }
        // 等 /me 请求真实在途后再取消（真实延迟在 OkHttp 线程，不在测试调度器上）
        withContext(Dispatchers.IO) {
            val deadline = System.currentTimeMillis() + 5_000
            while (server.requestCount < 2 && System.currentTimeMillis() < deadline) {
                Thread.sleep(10)
            }
        }
        assertTrue("前置条件：/me 请求应已到达", server.requestCount >= 2)

        job.cancelAndJoin()

        assertTrue("取消必须按取消语义透传，不得包装成普通错误", caught is CancellationException)
        assertNull("取消后不得留下「token 已保存但 /me 未验证」的半个会话", tokenStore.load())
        assertEquals("取消也要清理持久层", 1, tokenStore.clearCount)
    }

    // ---------- currentUser ----------

    @Test
    fun `currentUser without token returns null and issues no request`() = runTest {
        assertNull(repository.currentUser())
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `currentUser with valid token returns user`() = runTest {
        tokenStore.seed(fixtureToken)
        server.enqueue(
            jsonResponse("""{"id":"u1","username":"alice","role":"admin","created_at":null}"""),
        )
        val user = repository.currentUser()
        assertEquals("alice", user?.username)
        assertEquals("Bearer $fixtureToken", server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun `currentUser 401 clears stored token`() = runTest {
        tokenStore.seed(fixtureToken)
        server.enqueue(jsonResponse("""{"detail":"Missing bearer token"}""", code = 401))
        assertNull(repository.currentUser())
        assertNull("401 后本地 token 应清理", tokenStore.load())
        assertEquals(1, tokenStore.clearCount)
    }

    @Test
    fun `currentUser 403 keeps token and throws FORBIDDEN`() = runTest {
        tokenStore.seed(fixtureToken)
        server.enqueue(jsonResponse("""{"detail":"admin only"}""", code = 403))

        val error = runCatching { repository.currentUser() }.exceptionOrNull()

        assertTrue(error is AppError && error.kind == AppErrorKind.FORBIDDEN)
        assertEquals("403 是权限不足，不得清理凭据", fixtureToken, tokenStore.load())
        assertEquals("403 不触发本地清理", 0, tokenStore.clearCount)
    }

    @Test
    fun `currentUser server failure keeps token for retry`() = runTest {
        tokenStore.seed(fixtureToken)
        server.enqueue(MockResponse().setResponseCode(503))
        val error = runCatching { repository.currentUser() }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.SERVER)
        assertEquals("非凭据失败不得清 token", fixtureToken, tokenStore.load())
    }

    // ---------- username 原样提交（不静默 trim） ----------

    @Test
    fun `login submits username verbatim without trimming`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"用户名或密码错误"}""", code = 401))

        runCatching { repository.login(" alice ", "pw12345678") }

        val body = server.takeRequest().body.readUtf8()
        assertEquals(
            "仓储层不得 trim：原样提交，修复与否由调用方决定",
            """{"username":" alice ","password":"pw12345678"}""",
            body,
        )
    }

    // ---------- token fail-closed（空白即 BAD_RESPONSE，不落存储） ----------

    @Test
    fun `login token with whitespace is bad response and never saved`() = runTest {
        val cases = mapOf(
            "前缀空白" to " $fixtureToken",
            "后缀空白" to "$fixtureToken ",
            "内部空白" to "ab c",
        )
        for ((label, token) in cases) {
            val requestsBefore = server.requestCount
            server.enqueue(jsonResponse("""{"access_token":"$token","token_type":"bearer"}"""))

            val error = runCatching { repository.login("alice", "pw12345678") }.exceptionOrNull()

            assertTrue(label, error is AppError && error.kind == AppErrorKind.BAD_RESPONSE)
            assertNull(label, tokenStore.load())
            assertEquals(label, 0, tokenStore.saveCount)
            assertEquals("$label：token 非法时不得再调 /me", requestsBefore + 1, server.requestCount)
        }
    }

    // ---------- interceptor 对空白 token 不拼 Authorization ----------

    @Test
    fun `whitespace token is not attached as authorization header`() = runTest {
        tokenStore.seed(" $fixtureToken")
        server.enqueue(jsonResponse("""{"detail":"Missing bearer token"}""", code = 401))

        assertNull(repository.currentUser())

        val request = server.takeRequest()
        assertNull("空白 token 不得拼出非法 Authorization 头", request.getHeader("Authorization"))
        assertNull("401 后本地清理照常", tokenStore.load())
    }

    // ---------- logout ----------

    @Test
    fun `logout clears local token only and issues no request`() = runTest {
        tokenStore.seed(fixtureToken)
        repository.logout()
        assertNull(tokenStore.load())
        assertEquals("logout 只做本地清理", 0, server.requestCount)
    }
}
