package com.ailearningos.app.data

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.model.PrivacySnapshot
import com.ailearningos.app.data.model.UserProfile
import com.ailearningos.app.data.remote.LoginRequest
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

    /** 有 token 且有效 → 用户；无 token / 401 → null（并清理本地 token）；403 抛 FORBIDDEN 保留凭据；其它失败抛 [AppError] */
    suspend fun currentUser(): UserProfile?

    /** 只清理本地 token 与用户状态（M12-01 语义：Bearer 客户端不依赖 cookie） */
    suspend fun logout()
}

/** 系统状态面（/health、/system/privacy） */
interface SystemGateway {
    suspend fun healthOk(): Boolean
    suspend fun privacy(): PrivacySnapshot
}

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
