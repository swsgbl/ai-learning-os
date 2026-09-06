package com.ailearningos.app.data

import com.ailearningos.app.data.local.TokenStore
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * 内存 token 缓存（M12-01）。
 *
 * - OkHttp 拦截器是同步路径，经 [peek] 读内存值，不做 IO；
 * - 持久化只经 [TokenStore]（Keystore 加密实现），内存之外的副本只以密文存在；
 * - 首次使用时惰性加载一次，之后以内存为准（写穿透到 store）；
 * - ensureLoaded/save/clear 全部经 [Mutex] 串行化：并发的 clear 与在途 load
 *   不可能交错，杜绝「clear 之后旧 load 返回、把已清除 token 写回内存」的复活；
 * - cached/loaded 均为 @Volatile：写只发生在 mutex 临界区内，[peek] 无锁读取
 *   也能看到最新值（内存可见性）。
 */
class SessionTokenCache(private val store: TokenStore) {

    private val mutex = Mutex()

    @Volatile
    private var cached: String? = null

    @Volatile
    private var loaded = false

    suspend fun ensureLoaded(): String? = mutex.withLock {
        if (!loaded) {
            cached = store.load()
            loaded = true
        }
        cached
    }

    /** 同步读取（OkHttp 拦截器专用）；未加载时返回 null，不触发 IO */
    fun peek(): String? = cached

    suspend fun save(token: String) {
        mutex.withLock {
            store.save(token)
            cached = token
            loaded = true
        }
    }

    suspend fun clear() {
        mutex.withLock {
            // 先清内存再让 store 清理结果生效：store 清理失败也不得残留可用凭据，
            // 且失败原样抛出（调用方决定如何提示），不静默吞掉。
            val storeError = runCatching { store.clear() }.exceptionOrNull()
            cached = null
            loaded = true
            storeError?.let { throw it }
        }
    }
}
