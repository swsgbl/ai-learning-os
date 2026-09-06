package com.ailearningos.app.core

/**
 * M12-01 API base URL 校验与规范化（纯函数，JVM 单测锁定）。
 *
 * 规则：
 * - 拒绝空值/纯空白/内部含空白；
 * - 仅接受 http/https（大小写不敏感）；
 * - 拒绝 query（?）、fragment（#）、反斜杠、控制字符与 userinfo（user:pass@）——
 *   尾部路径混乱一律拒绝，不做“猜意图”的修复；
 * - debug 构建（allowInsecureHttp=true）只对 loopback / 局域网放行 http，
 *   公网 http 同样拒绝；release（allowInsecureHttp=false）强制 https；
 * - 规范化：scheme/host 小写、去掉显式默认端口、恰好一个尾部斜杠。
 */
enum class BaseUrlProblem {
    /** 空或纯空白 */
    EMPTY,

    /** 内部含空白字符 */
    HAS_WHITESPACE,

    /** 缺 scheme、scheme 非 http(s) 或无法解析出主机 */
    BAD_SCHEME,

    /** 含 query/fragment/反斜杠/控制字符/userinfo */
    BAD_CHARACTERS,

    /** 非 https（release 构建强制 https） */
    INSECURE_FORBIDDEN,

    /** debug 下 http 仅限 loopback/局域网，公网 http 拒绝 */
    PUBLIC_HTTP_FORBIDDEN,
}

sealed interface BaseUrlValidation {
    /** 规范化后的 base URL（保证恰好一个尾部斜杠，可直接交给 Retrofit） */
    data class Valid(val normalized: String) : BaseUrlValidation

    data class Invalid(val problem: BaseUrlProblem) : BaseUrlValidation
}

object BaseUrlPolicy {

    /** debug 构建未配置时的默认地址（Android 模拟器访问宿主机 127.0.0.1） */
    const val DEFAULT_DEBUG_BASE_URL = "http://10.0.2.2:8000/"

    private const val SCHEME_HTTP = "http"
    private const val SCHEME_HTTPS = "https"

    fun validate(raw: String, allowInsecureHttp: Boolean): BaseUrlValidation {
        if (raw.isBlank()) return BaseUrlValidation.Invalid(BaseUrlProblem.EMPTY)
        if (raw.any { it.isWhitespace() }) {
            return BaseUrlValidation.Invalid(BaseUrlProblem.HAS_WHITESPACE)
        }
        if (raw.any { it.code < 0x20 || it.code == 0x7F }) {
            return BaseUrlValidation.Invalid(BaseUrlProblem.BAD_CHARACTERS)
        }
        // 尾部路径混乱面：query/fragment/反斜杠一律拒绝；userinfo 不接受（凭据不属于 base URL）
        if (raw.contains('?') || raw.contains('#') || raw.contains('\\') || raw.contains('@')) {
            return BaseUrlValidation.Invalid(BaseUrlProblem.BAD_CHARACTERS)
        }

        val uri = try {
            java.net.URI(raw)
        } catch (_: Exception) {
            return BaseUrlValidation.Invalid(BaseUrlProblem.BAD_SCHEME)
        }

        val scheme = uri.scheme?.lowercase()
        if (scheme != SCHEME_HTTP && scheme != SCHEME_HTTPS) {
            return BaseUrlValidation.Invalid(BaseUrlProblem.BAD_SCHEME)
        }
        val host = uri.host?.lowercase()
        if (host.isNullOrEmpty()) {
            return BaseUrlValidation.Invalid(BaseUrlProblem.BAD_SCHEME)
        }

        if (scheme == SCHEME_HTTP && !allowInsecureHttp) {
            return BaseUrlValidation.Invalid(BaseUrlProblem.INSECURE_FORBIDDEN)
        }
        if (scheme == SCHEME_HTTP && !isLoopbackHost(host) && !isPrivateLanHost(host)) {
            return BaseUrlValidation.Invalid(BaseUrlProblem.PUBLIC_HTTP_FORBIDDEN)
        }

        return BaseUrlValidation.Valid(normalize(scheme, uri))
    }

    /** Android 模拟器 loopback（10.0.2.2 / 10.0.3.2）、localhost、127.0.0.0/8、::1 */
    fun isLoopbackHost(host: String): Boolean {
        val h = host.removePrefix("[").removeSuffix("]").lowercase()
        return h == "localhost" ||
            h == "::1" ||
            h == "10.0.2.2" ||
            h == "10.0.3.2" ||
            h.startsWith("127.")
    }

    /** RFC1918 私网、mDNS .local、无点单标签主机名（局域网机器名） */
    fun isPrivateLanHost(host: String): Boolean {
        val h = host.removePrefix("[").removeSuffix("]").lowercase()
        if (h.endsWith(".local")) return true
        if (!h.contains('.')) return true
        val parts = h.split('.')
        val octets = parts.mapNotNull { it.toIntOrNull() }
        if (octets.size == 4 && parts.all { it.toIntOrNull() in 0..255 }) {
            val (a, b) = octets[0] to octets[1]
            return a == 10 || (a == 192 && b == 168) || (a == 172 && b in 16..31)
        }
        return false
    }

    private fun normalize(scheme: String, uri: java.net.URI): String {
        val host = uri.host.lowercase()
        val defaultPort = if (scheme == SCHEME_HTTPS) 443 else 80
        val port = if (uri.port != -1 && uri.port != defaultPort) ":${uri.port}" else ""
        val trimmed = uri.rawPath.orEmpty().trim('/')
        val path = if (trimmed.isEmpty()) "/" else "/$trimmed/"
        return "$scheme://$host$port$path"
    }
}
