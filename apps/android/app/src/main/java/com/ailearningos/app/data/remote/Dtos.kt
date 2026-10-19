package com.ailearningos.app.data.remote

import com.ailearningos.app.data.model.PrivacySnapshot
import com.ailearningos.app.data.model.UserProfile
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * M12-01 第一切片 DTO：GET /health、auth status/login/me、system/privacy。
 * snake_case 与服务端 Pydantic 契约一一对应（docs/DEVELOPMENT.md）。
 */
@Serializable
data class HealthResponse(
    val status: String,
    val service: String? = null,
)

@Serializable
data class AuthStatusResponse(
    @SerialName("auth_enabled") val authEnabled: Boolean,
)

@Serializable
data class RegisterRequest(
    val username: String,
    val password: String,
)

@Serializable
data class LoginRequest(
    val username: String,
    val password: String,
)

@Serializable
data class TokenResponse(
    @SerialName("access_token") val accessToken: String,
    @SerialName("token_type") val tokenType: String = "bearer",
)

@Serializable
data class UserResponse(
    val id: String,
    val username: String,
    val role: String,
    @SerialName("created_at") val createdAt: String? = null,
)

@Serializable
data class PrivacyResponse(
    @SerialName("model_route") val modelRoute: String,
    @SerialName("voice_mode") val voiceMode: String,
    @SerialName("search_mode") val searchMode: String,
    @SerialName("store_audio") val storesAudio: Boolean,
    @SerialName("send_context_to_cloud") val sendsContextToCloud: Boolean,
)

fun UserResponse.toDomain() = UserProfile(id = id, username = username, role = role)

fun PrivacyResponse.toDomain() = PrivacySnapshot(
    modelRoute = modelRoute,
    voiceMode = voiceMode,
    searchMode = searchMode,
    storesAudio = storesAudio,
    sendsContextToCloud = sendsContextToCloud,
)


/**
 * M15-006 /api/v1/meta/latest-app 响应（服务端三项齐全才透出；404=未配置）。
 * M15-011 修复：服务端线上的字段名是 snake_case（version_code/download_url），
 * 本 DTO 曾误写 camelCase 无 @SerialName——「检查更新」全系客户端报
 * 「API 返回数据格式异常」（真机 v0.1.3 复现）。服务端已加 camelCase
 * 兼容通道救已发布包；本 DTO 对齐契约口径 snake_case。
 */
@Serializable
data class LatestAppResponse(
    val platform: String,
    val version: String,
    @SerialName("version_code") val versionCode: Int,
    @SerialName("download_url") val downloadUrl: String,
    /** M15-012 多通道回退（服务端可空）；旧字段名兼容由服务端双命名承担 */
    @SerialName("download_url_fallback") val downloadUrlFallback: String? = null,
)


/** M15-014 /api/v1/me/ai-keys（个人 BYOK 密钥状态；明文永不回显） */
@Serializable
data class UserAiKeyStatus(
    val slot: String,
    val configured: Boolean,
    val last4: String? = null,
    val updatedAt: String? = null,
    val effect: String,
    /** M15-015 个人 LLM 可选路由（空 = 沿用服务端网关配置） */
    val endpoint: String? = null,
    val model: String? = null,
)

@Serializable
data class UserAiKeysResponse(val keys: List<UserAiKeyStatus>)

@Serializable
data class UserAiKeyPutRequest(
    val api_key: String,
    val endpoint: String? = null,
    val model: String? = null,
)
