package com.ailearningos.app.data

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.core.BaseUrlPolicy
import com.ailearningos.app.core.BaseUrlValidation
import com.ailearningos.app.data.local.SettingsStore
import com.ailearningos.app.data.remote.AiosApi
import com.ailearningos.app.data.remote.ApiClientFactory

/**
 * API 地址解析（M12-01）：
 *
 * - 未配置时 debug 构建回落默认 `http://10.0.2.2:8000/`；
 * - 已配置但不符合当前构建安全策略（如 release 下的 http）→ [AppError]（BAD_CONFIG），
 *   错误态可在设置页修正后恢复，不做静默改写；
 * - 解析出的地址已经过规范化（恰好一个尾部斜杠）。
 */
class ApiConfigProvider(
    private val settingsStore: SettingsStore,
    private val allowInsecureHttp: Boolean,
) {
    suspend fun resolve(): String {
        // 存储层不做静默修复：读到的原始值（含首尾/内部空白）直接交给策略 fail-closed。
        // 首尾空白只允许发生在 UI 输入层（设置页保存前 trim），不在这里补救。
        val stored = settingsStore.loadBaseUrl().orEmpty()
        val raw = stored.ifEmpty { BaseUrlPolicy.DEFAULT_DEBUG_BASE_URL }
        return when (val validation = BaseUrlPolicy.validate(raw, allowInsecureHttp)) {
            is BaseUrlValidation.Valid -> validation.normalized
            is BaseUrlValidation.Invalid -> throw AppError(AppErrorKind.BAD_CONFIG)
        }
    }
}

/** 按 base URL 缓存 Retrofit 实例（地址变更时重建；token 经内存缓存注入） */
class ApiProvider(
    private val config: ApiConfigProvider,
    private val tokenCache: SessionTokenCache,
) {
    @Volatile
    private var cached: Pair<String, AiosApi>? = null

    suspend fun get(): AiosApi {
        val baseUrl = config.resolve()
        cached?.let { (url, api) -> if (url == baseUrl) return api }
        val api = ApiClientFactory.create(baseUrl) { tokenCache.peek() }
        cached = baseUrl to api
        return api
    }
}
