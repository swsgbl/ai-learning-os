package com.ailearningos.app.data.remote

import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Response
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import java.util.concurrent.TimeUnit

/**
 * Retrofit/OkHttp 装配。
 *
 * 安全边界（M12-01）：
 * - [AuthInterceptor] 只从内存 token 缓存读值并附加 Authorization，绝不打印
 *   header、token 或 base URL，也不写任何日志；
 * - baseUrl 必须是已经过 [com.ailearningos.app.core.BaseUrlPolicy] 校验规范化的值。
 */
object ApiClientFactory {

    fun create(baseUrl: String, tokenProvider: () -> String?): AiosApi {
        val client = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(20, TimeUnit.SECONDS)
            .writeTimeout(20, TimeUnit.SECONDS)
            .addInterceptor(AuthInterceptor(tokenProvider))
            .build()

        return Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(client)
            .addConverterFactory(AiosJson.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(AiosApi::class.java)
    }
}

/** 登录后所有请求经此附加 `Authorization: Bearer ...`；无 token 时原样放行 */
private class AuthInterceptor(private val tokenProvider: () -> String?) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        // 不 trim：空白/异常 token 一律不拼 Authorization（拼出的头必然非法，
        // 静默修整只会掩盖上游存了脏值的事实），让请求以未授权形态到达服务端。
        val token = tokenProvider().orEmpty()
        val authorized = if (token.isEmpty() || token.any { it.isWhitespace() }) {
            request
        } else {
            request.newBuilder().header("Authorization", "Bearer $token").build()
        }
        return chain.proceed(authorized)
    }
}
