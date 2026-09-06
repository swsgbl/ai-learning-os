package com.ailearningos.app.testutil

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.AuthGateway
import com.ailearningos.app.data.SystemGateway
import com.ailearningos.app.data.local.SettingsStore
import com.ailearningos.app.data.local.TokenStore
import com.ailearningos.app.data.model.PrivacySnapshot
import com.ailearningos.app.data.model.UserProfile

/** 测试专用内存 token 存储（真实 Keystore 实现不上 JVM 测试） */
class FakeTokenStore : TokenStore {

    var stored: String? = null
        private set

    var saveCount = 0
        private set

    var clearCount = 0
        private set

    /** 供测试断言只读访问当前存储值 */
    fun peekStored(): String? = stored

    /** 测试播种已存在 token（模拟“上次登录遗留”） */
    fun seed(token: String?) {
        stored = token
    }

    var error: AppError? = null

    override suspend fun save(token: String) {
        error?.let { throw it }
        stored = token
        saveCount++
    }

    override suspend fun load(): String? {
        error?.let { throw it }
        return stored
    }

    override suspend fun clear() {
        error?.let { throw it }
        stored = null
        clearCount++
    }
}

/** 测试专用内存配置存储 */
class FakeSettingsStore(initial: String? = null) : SettingsStore {

    var stored: String? = initial

    val savedValues = mutableListOf<String>()

    override suspend fun loadBaseUrl(): String? = stored

    override suspend fun saveBaseUrl(value: String) {
        savedValues.add(value)
        stored = value
    }
}

/** 测试专用 AuthGateway：可控返回与异常，记录调用 */
class FakeAuthGateway : AuthGateway {

    var enabled = true

    var user: UserProfile? = null

    var loginResult: Result<UserProfile> = Result.success(UserProfile("u1", "alice", "learner"))

    var authEnabledError: AppError? = null

    var currentUserError: AppError? = null

    var logoutError: AppError? = null

    var loginCalls = 0
        private set

    var logoutCalls = 0
        private set

    var refreshCalls = 0
        private set

    var lastSubmittedPassword: String? = null
        private set

    var lastSubmittedUsername: String? = null
        private set

    override suspend fun authEnabled(): Boolean {
        refreshCalls++
        authEnabledError?.let { throw it }
        return enabled
    }

    override suspend fun login(username: String, password: String): UserProfile {
        loginCalls++
        lastSubmittedUsername = username
        lastSubmittedPassword = password
        return loginResult.getOrThrow()
    }

    override suspend fun currentUser(): UserProfile? {
        refreshCalls++
        currentUserError?.let { throw it }
        return user
    }

    override suspend fun logout() {
        logoutCalls++
        logoutError?.let { throw it }
    }
}

/** 测试专用 SystemGateway */
class FakeSystemGateway : SystemGateway {

    var healthOk = true

    var healthError: AppError? = null

    var privacyResult: Result<PrivacySnapshot> = Result.success(privacyFixture())

    var privacyCalls = 0
        private set

    override suspend fun healthOk(): Boolean {
        healthError?.let { throw it }
        return healthOk
    }

    override suspend fun privacy(): PrivacySnapshot {
        privacyCalls++
        return privacyResult.getOrThrow()
    }
}

fun userFixture(
    id: String = "u1",
    username: String = "alice",
    role: String = "learner",
) = UserProfile(id = id, username = username, role = role)

fun privacyFixture(
    modelRoute: String? = "local",
    voiceMode: String? = "local",
    searchMode: String? = "local",
    storesAudio: Boolean = false,
    sendsContextToCloud: Boolean = false,
) = PrivacySnapshot(
    modelRoute = modelRoute,
    voiceMode = voiceMode,
    searchMode = searchMode,
    storesAudio = storesAudio,
    sendsContextToCloud = sendsContextToCloud,
)

fun appError(kind: AppErrorKind) = AppError(kind)
