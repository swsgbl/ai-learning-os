package com.ailearningos.app.data

import com.ailearningos.app.data.local.TokenStorageException
import com.ailearningos.app.data.local.TokenStore
import com.ailearningos.app.testutil.FakeTokenStore
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * 内存 token 缓存并发契约：
 * 启动恢复、登录写穿、logout 清理、并发操作互斥、clear 不被在途 load 复活。
 */
class SessionTokenCacheTest {

    // ---------- 启动恢复 ----------

    @Test
    fun `startup recovery loads once and exposes via peek`() = runTest {
        val store = FakeTokenStore().apply { seed("stored-token") }
        val cache = SessionTokenCache(store)

        assertNull("未加载前 peek 不得返回值", cache.peek())
        assertEquals("stored-token", cache.ensureLoaded())
        assertEquals("stored-token", cache.peek())
    }

    @Test
    fun `ensureLoaded is idempotent no repeated store reads`() = runTest {
        val store = CountingStore(initial = "tok")
        val cache = SessionTokenCache(store)
        cache.ensureLoaded()
        cache.ensureLoaded()
        assertEquals(1, store.loadCalls)
    }

    // ---------- 登录（save 写穿） ----------

    @Test
    fun `save writes through to store and memory`() = runTest {
        val store = FakeTokenStore()
        val cache = SessionTokenCache(store)

        cache.save("login-token")

        assertEquals("login-token", cache.peek())
        assertEquals("login-token", store.load())
        assertEquals(1, store.saveCount)
        assertEquals("login-token", cache.ensureLoaded())
    }

    // ---------- logout（clear） ----------

    @Test
    fun `logout clears store and memory without resurrecting from store`() = runTest {
        val store = CountingStore(initial = null)
        val cache = SessionTokenCache(store)
        cache.save("session-token")
        cache.clear()

        assertNull(cache.peek())
        assertNull(cache.ensureLoaded())
        assertEquals("clear 后不得再回读 store（避免读到残留密文）", 0, store.loadCalls)
        assertEquals(1, store.clearCalls)
    }

    @Test
    fun `clear with failing store still wipes memory and rethrows`() = runTest {
        val cache = SessionTokenCache(FailingClearStore())
        cache.save("session-token")

        try {
            cache.clear()
            fail("store 清理失败必须抛出，不得静默")
        } catch (expected: TokenStorageException) {
            // 预期路径
        }
        assertNull("store 清理失败也不得残留可用凭据", cache.peek())
    }

    // ---------- 并发：clear 不复活在途 load ----------

    @Test
    fun `concurrent clear cannot resurrect token from in-flight load`() = runTest {
        val events = mutableListOf<String>()
        val loadStarted = CompletableDeferred<Unit>()
        val releaseLoad = CompletableDeferred<Unit>()
        var clearCalls = 0
        val store = object : TokenStore {
            override suspend fun save(token: String) {
                events.add("save")
            }

            override suspend fun load(): String? {
                events.add("load-start")
                loadStarted.complete(Unit)
                releaseLoad.await()
                events.add("load-end")
                return "stale-token"
            }

            override suspend fun clear() {
                events.add("clear")
                clearCalls++
            }
        }
        val cache = SessionTokenCache(store)

        val loadJob = launch { cache.ensureLoaded() }
        loadStarted.await()
        advanceUntilIdle()

        // load 仍挂在 store.load 内部（互斥锁未释放），clear 只能排队
        val clearJob = launch { cache.clear() }
        advanceUntilIdle()
        assertTrue("clear 必须被互斥挡住在途 load 之后", !events.contains("clear"))
        assertNull("load 未返回前 peek 仍为空", cache.peek())

        releaseLoad.complete(Unit)
        advanceUntilIdle()
        loadJob.join()
        clearJob.join()

        assertNull("clear 之后不得被旧 load 复活", cache.peek())
        assertEquals(1, clearCalls)
        assertTrue(
            "事件必须串行：load 完成先于 clear（$events）",
            events.indexOf("load-end") < events.indexOf("clear"),
        )
    }

    @Test
    fun `mixed concurrent operations stay serialized and consistent`() = runTest {
        val store = CountingStore(initial = null)
        val cache = SessionTokenCache(store)

        val jobs = List(60) { index ->
            launch {
                when (index % 4) {
                    0 -> cache.save("token-$index")
                    1 -> cache.clear()
                    2 -> cache.ensureLoaded()
                    else -> cache.peek()
                }
            }
        }
        jobs.forEach { it.join() }
        // 最后一个 store 触达操作是 index=58（58 % 4 == 2 → ensureLoaded），
        // 其前一个写操作 index=57（57 % 4 == 1 → clear）
        assertNull(cache.peek())
        assertNull(cache.ensureLoaded())
        assertEquals("store 读写次数与并发操作一一对应", 30, store.mutations)
        assertEquals(0, store.loadCalls)
    }

    // ---------- fake ----------

    /** 记录 store 调用次数的最小 fake */
    private class CountingStore(initial: String?) : TokenStore {
        var loadCalls = 0
        var clearCalls = 0
        var mutations = 0
        private var value: String? = initial

        override suspend fun save(token: String) {
            mutations++
            value = token
        }

        override suspend fun load(): String? {
            loadCalls++
            return value
        }

        override suspend fun clear() {
            mutations++
            clearCalls++
            value = null
        }
    }

    private class FailingClearStore : TokenStore {
        override suspend fun save(token: String) = Unit

        override suspend fun load(): String? = null

        override suspend fun clear() {
            throw TokenStorageException()
        }
    }
}
