package com.ailearningos.app.data

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.model.PrivacySnapshot
import com.ailearningos.app.data.model.UserProfile
import com.ailearningos.app.data.remote.LoginRequest
import com.ailearningos.app.data.remote.UserAiKeyPutRequest
import com.ailearningos.app.data.remote.RegisterRequest
import com.ailearningos.app.data.remote.toDomain
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.withContext
import retrofit2.HttpException

/** UI/ViewModel 依赖的认证面（测试用 fake 实现） */
interface AuthGateway {
    /** auth_enabled（服务端认证开关）；失败抛 [AppError] */
    suspend fun authEnabled(): Boolean

    /** 登录成功返回用户并保存 token；凭据错误 401 → UNAUTHORIZED；失败时不留半个会话 */
    suspend fun login(username: String, password: String): UserProfile

    /** 注册并自动登录（M15-002）：注册 201 → 立即 login 建会话；用户名冲突 409 → CONFLICT 文案 */
    suspend fun register(username: String, password: String): UserProfile

    /** 有 token 且有效 → 用户；无 token / 401 → null（并清理本地 token）；403 抛 FORBIDDEN 保留凭据；其它失败抛 [AppError] */
    suspend fun currentUser(): UserProfile?

    /** 只清理本地 token 与用户状态（M12-01 语义：Bearer 客户端不依赖 cookie） */
    suspend fun logout()
}

/** 系统状态面（/health、/system/privacy、/meta/latest-app） */
/** M15-014 个人 AI 密钥状态（BYOK）：明文只进不出 */
data class MyAiKeyInfo(
    val slot: String,
    val configured: Boolean,
    val last4: String?,
    val effect: String,
    val endpoint: String? = null,
    val model: String? = null,
)

interface SystemGateway {
    suspend fun healthOk(): Boolean
    suspend fun privacy(): PrivacySnapshot

    /** M15-006 更新检查：服务端未配置（404）→ null（如实「无版本信息」）；网络/服务器故障抛 [AppError] */
    suspend fun latestApp(): LatestAppInfo?
    suspend fun myAiKeys(): List<MyAiKeyInfo>
    suspend fun saveMyAiKey(slot: String, apiKey: String, endpoint: String?, model: String?)
    suspend fun deleteMyAiKey(slot: String)
}

/** M15-006 服务端透出的最新应用版本（领域模型，UI 不接触 DTO） */
data class LatestAppInfo(
    val platform: String,
    val version: String,
    val versionCode: Int,
    val downloadUrl: String,
    /** M15-012 多通道：主地址打不开时的备用下载入口（可空） */
    val downloadUrlFallback: String? = null,
)

class AuthRepository(
    private val apiProvider: ApiProvider,
    private val tokenCache: SessionTokenCache,
) : AuthGateway, SystemGateway {

    override suspend fun authEnabled(): Boolean = withRemoteError {
        apiProvider.get().authStatus().authEnabled
    }

    override suspend fun healthOk(): Boolean = withRemoteError {
        val health = apiProvider.get().health()
        health.status.equals("ok", ignoreCase = true)
    }

    override suspend fun privacy(): PrivacySnapshot = withRemoteError {
        apiProvider.get().privacy().toDomain()
    }

    override suspend fun latestApp(): LatestAppInfo? = try {
        withRemoteError {
            apiProvider.get().latestApp().let {
                LatestAppInfo(it.platform, it.version, it.versionCode, it.downloadUrl, it.downloadUrlFallback)
            }
        }
    } catch (cause: AppError) {
        if (cause.kind == AppErrorKind.NOT_FOUND) null else throw cause
    }

    override suspend fun myAiKeys(): List<MyAiKeyInfo> = withRemoteError {
        apiProvider.get().myAiKeys().keys.map {
            MyAiKeyInfo(it.slot, it.configured, it.last4, it.effect, it.endpoint, it.model)
        }
    }

    override suspend fun saveMyAiKey(slot: String, apiKey: String, endpoint: String?, model: String?) =
        withRemoteError {
            apiProvider.get().putMyAiKey(slot, UserAiKeyPutRequest(apiKey, endpoint?.ifBlank { null }, model?.ifBlank { null }))
            Unit
        }

    override suspend fun deleteMyAiKey(slot: String) = withRemoteError {
        apiProvider.get().deleteMyAiKey(slot)
        Unit
    }

    override suspend fun login(username: String, password: String): UserProfile = withRemoteError {
        // username 原样提交：不 trim、不改写——输入合法性只由 UI 层预校验负责，
        // 仓储层对「看似可修复」的输入做静默变形只会掩盖上游 bug。
        val token = apiProvider.get().login(LoginRequest(username, password)).accessToken
        // token fail-closed：空值或含任何空白（首尾/内部）都是响应形态非法，
        // 不 trim 补救，一律 BAD_RESPONSE 且不落任何存储。
        if (token.isBlank() || token.any { it.isWhitespace() }) {
            throw AppError(AppErrorKind.BAD_RESPONSE)
        }
        tokenCache.save(token)
        try {
            apiProvider.get().me().toDomain()
        } catch (cause: Throwable) {
            // /me 失败（含被取消）不保留半个会话：NonCancellable 下清理 token，
            // 取消语义原样透传（withRemoteError 对 CancellationException 不做包装）。
            withContext(NonCancellable) {
                runCatching { tokenCache.clear() }
            }
            throw cause
        }
    }

    override suspend fun register(username: String, password: String): UserProfile = withRemoteError {
        // 注册即登录：服务端 /register 只创建账号（201 UserOut），会话令牌仍走 /login。
        apiProvider.get().register(RegisterRequest(username, password))
        login(username, password)
    }

    override suspend fun currentUser(): UserProfile? = withRemoteError {
        tokenCache.ensureLoaded() ?: return@withRemoteError null
        try {
            apiProvider.get().me().toDomain()
        } catch (cause: HttpException) {
            // 401 = 凭据失效：清本地 token 回落未登录。
            // 403 = 权限不足：凭据本身仍有效，保留 token 抛 FORBIDDEN，
            // 绝不能把「无权限」静默降级成「已登出」。
            if (cause.code() == 401) {
                tokenCache.clear()
                null
            } else {
                throw cause
            }
        }
    }

    override suspend fun logout() {
        tokenCache.clear()
    }
}
