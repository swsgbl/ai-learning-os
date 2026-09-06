package com.ailearningos.app.data.remote

import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST

/** M12-01 第一切片端点面：只做壳与认证/API 基础 */
interface AiosApi {

    @GET("health")
    suspend fun health(): HealthResponse

    @GET("api/v1/auth/status")
    suspend fun authStatus(): AuthStatusResponse

    @POST("api/v1/auth/login")
    suspend fun login(@Body body: LoginRequest): TokenResponse

    @GET("api/v1/auth/me")
    suspend fun me(): UserResponse

    @GET("api/v1/system/privacy")
    suspend fun privacy(): PrivacyResponse
}
