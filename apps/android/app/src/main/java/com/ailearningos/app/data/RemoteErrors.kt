package com.ailearningos.app.data

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.local.TokenStorageException
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.SerializationException
import retrofit2.HttpException
import java.io.IOException

/**
 * 远端/存储失败 → 面向用户的 [AppError]（固定脱敏文案）。
 *
 * 映射只看异常类型与 HTTP 状态码，绝不携带底层异常文本、URL、header 或响应体。
 */
fun Throwable.asAppError(): AppError = when (this) {
    is CancellationException -> throw this
    is AppError -> this
    is HttpException -> when (code()) {
        401 -> AppError(AppErrorKind.UNAUTHORIZED, this)
        403 -> AppError(AppErrorKind.FORBIDDEN, this)
        404 -> AppError(AppErrorKind.NOT_FOUND, this)
        409 -> AppError(AppErrorKind.CONFLICT, this)
        in 500..599 -> AppError(AppErrorKind.SERVER, this)
        else -> AppError(AppErrorKind.BAD_RESPONSE, this)
    }
    is IOException -> AppError(AppErrorKind.NETWORK, this)
    is SerializationException -> AppError(AppErrorKind.BAD_RESPONSE, this)
    is TokenStorageException -> AppError(AppErrorKind.STORAGE, this)
    else -> AppError(AppErrorKind.UNEXPECTED, this)
}

/** 统一包装：块内抛出的任何异常都被折叠为 [AppError]（取消除外） */
inline fun <T> withRemoteError(block: () -> T): T = try {
    block()
} catch (cancellation: CancellationException) {
    throw cancellation
} catch (cause: Throwable) {
    throw cause.asAppError()
}
