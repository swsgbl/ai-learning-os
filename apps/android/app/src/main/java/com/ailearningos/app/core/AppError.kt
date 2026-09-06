package com.ailearningos.app.core

/**
 * 面向用户的应用错误。
 *
 * 安全边界（M12-01）：文案是固定脱敏字符串——不含 token、不含完整 URL/查询串、
 * 不含底层异常细节（异常链只保留在 [cause] 里供调试器查看，不进任何日志输出）。
 */
enum class AppErrorKind {
    /** 连接失败/超时/DNS 等网络层问题 */
    NETWORK,

    /** 401：未登录或凭据失效 */
    UNAUTHORIZED,

    /** 403：无权限 */
    FORBIDDEN,

    /** 404 */
    NOT_FOUND,

    /** 5xx 或其它服务端失败 */
    SERVER,

    /** 响应不是预期结构 / 协议层失败 */
    BAD_RESPONSE,

    /** API 地址未配置或不符合当前构建的安全策略（可在设置中修复） */
    BAD_CONFIG,

    /** 本机安全存储（Keystore）不可用 */
    STORAGE,

    /** 未归类失败 */
    UNEXPECTED,
}

class AppError(
    val kind: AppErrorKind,
    cause: Throwable? = null,
) : Exception(cause) {

    val userMessage: String
        get() = when (kind) {
            AppErrorKind.NETWORK -> "无法连接 API，请检查网络后重试"
            AppErrorKind.UNAUTHORIZED -> "登录状态已失效，请重新登录"
            AppErrorKind.FORBIDDEN -> "当前账号没有权限执行该操作"
            AppErrorKind.NOT_FOUND -> "请求的内容不存在"
            AppErrorKind.SERVER -> "服务暂时不可用，请稍后重试"
            AppErrorKind.BAD_RESPONSE -> "API 返回数据格式异常"
            AppErrorKind.BAD_CONFIG -> "API 地址未配置或无效，请在设置中修改"
            AppErrorKind.STORAGE -> "本机安全存储不可用，请重试；若持续失败请联系维护者"
            AppErrorKind.UNEXPECTED -> "发生意外错误，请稍后重试"
        }
}
