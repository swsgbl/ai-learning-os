package com.ailearningos.app.data

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.local.TokenStorageException
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.SerializationException
import retrofit2.HttpException
import java.io.IOException

/**
 * 远端/存储失败 → 面向用户的 [AppError]（脱敏文案 + 服务端 detail 透出）。
 *
 * M15-019：HTTP 错误响应体形如 {"detail": "友好中文文案"}（服务端有意
 * 给出，如「当前已发布概念图支持的主题：…」）——detail 为纯字符串时
 * 作为 userMessage 透出（与 Web 端 request() 的 detail 提取同口径）；
 * 其余形态（Fastapi 422 校验数组等）仍走固定脱敏文案，绝不携带
 * URL/header/底层异常文本。
 */
fun Throwable.asAppError(): AppError = when (this) {
    is CancellationException -> throw this
    is AppError -> this
    is HttpException -> when (code()) {
        401 -> AppError(AppErrorKind.UNAUTHORIZED, this, serverDetailOrNull())
        403 -> AppError(AppErrorKind.FORBIDDEN, this, serverDetailOrNull())
        404 -> AppError(AppErrorKind.NOT_FOUND, this, serverDetailOrNull())
        409 -> AppError(AppErrorKind.CONFLICT, this, serverDetailOrNull())
        in 500..599 -> AppError(AppErrorKind.SERVER, this, serverDetailOrNull())
        else -> AppError(AppErrorKind.BAD_RESPONSE, this, serverDetailOrNull())
    }
    is IOException -> AppError(AppErrorKind.NETWORK, this)
    is SerializationException -> AppError(AppErrorKind.BAD_RESPONSE, this)
    is TokenStorageException -> AppError(AppErrorKind.STORAGE, this)
    else -> AppError(AppErrorKind.UNEXPECTED, this)
}

/** detail 为纯字符串时返回（否则 null 走固定文案）；读取失败静默。 */
private fun Throwable.serverDetailOrNull(): String? = try {
    @Suppress("SwallowedException")
    runCatching {
        val body = (this as? HttpException)?.response()?.errorBody()?.string()
        if (body.isNullOrBlank()) return null
        val element = com.ailearningos.app.data.remote.AiosJson.parseToJsonElement(body)
        val detail = (element as? kotlinx.serialization.json.JsonObject)?.get("detail")
        (detail as? kotlinx.serialization.json.JsonPrimitive)?.takeIf { it.isString }?.content?.take(300)
    }.getOrNull()
} catch (_: Exception) {
    null
}

/** 统一包装：块内抛出的任何异常都被折叠为 [AppError]（取消除外） */
inline fun <T> withRemoteError(block: () -> T): T = try {
    block()
} catch (cancellation: CancellationException) {
    throw cancellation
} catch (cause: Throwable) {
    throw cause.asAppError()
}
