package com.ailearningos.app.data.local

/**
 * Bearer token 的存储边界（M12-01）。
 *
 * 契约：
 * - 实现必须经 Android Keystore 加密后落盘（[KeystoreTokenStore]）；
 *   明文 SharedPreferences / 文件 / DataStore 保存 token 一律不允许（测试守卫锁定）；
 * - 测试一律使用内存 fake 实现，不触碰真实 Keystore；
 * - 失败语义：无法安全读写时抛 [TokenStorageException]，绝不降级为明文存储。
 */
interface TokenStore {
    suspend fun save(token: String)
    suspend fun load(): String?
    suspend fun clear()
}

/** 安全存储不可用（Keystore 初始化/加解密失败）；不携带底层异常文本给 UI */
class TokenStorageException(cause: Throwable? = null) : Exception(cause)
