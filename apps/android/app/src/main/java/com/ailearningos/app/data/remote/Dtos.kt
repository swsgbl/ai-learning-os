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
