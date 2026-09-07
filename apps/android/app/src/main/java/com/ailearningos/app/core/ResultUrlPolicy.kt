package com.ailearningos.app.core

/**
 * M12-04 搜索结果来源 URL 安全校验与展示断行（纯函数，JVM 单测锁定）。
 *
 * 与 [BaseUrlPolicy] 的语义差异：结果 URL 是服务端返回的完整链接（path/
 * query/fragment 属于正常形态，予以保留），不是客户端配置的 API base URL；
 * 但在交给系统 View 能力（ACTION_VIEW）打开前必须收紧：
 * - 仅接受 https（大小写不敏感）——http/file/intent/javascript 等任意
 *   scheme 一律拒绝，不存在「debug 放行 http」的例外；
 * - 拒绝空值/纯空白、内部空白、控制字符、反斜杠（scheme 混淆面）；
 * - 拒绝 authority 内 userinfo（`user:pass@` / `good.com@evil.com` 的
 *   视觉混淆）；query 参数里的 `@` 不受影响；
 * - 必须能解析出 host；非 ASCII 原文（未 percent-encode）按 malformed
 *   拒绝——不做「猜意图」的修复，原样展示并禁用打开入口。
 */
enum class ResultUrlProblem {
    /** 空或纯空白 */
    EMPTY,

    /** 非 https scheme、无法解析、解析不出 host 或含未编码的非 ASCII 原文 */
    BAD_SCHEME,

    /** 含空白/控制字符/反斜杠/userinfo */
    BAD_CHARACTERS,
}

sealed interface ResultUrlValidation {
    /** 通过校验的 URL（原样返回，不规范化） */
    data class Valid(val url: String) : ResultUrlValidation

    data class Invalid(val problem: ResultUrlProblem) : ResultUrlValidation
}

object ResultUrlPolicy {

    private const val SCHEME_HTTPS = "https"

    /** 展示断行的默认行宽（bodySmall 12sp 下 360dp 屏不横向溢出） */
    const val DISPLAY_MAX_LINE = 40

    /** 断点搜索下限：保证每行至少这么长，防止连续分隔符造成死循环 */
    private const val MIN_CUT = 8

    fun validate(raw: String?): ResultUrlValidation {
        if (raw.isNullOrBlank()) return ResultUrlValidation.Invalid(ResultUrlProblem.EMPTY)
        if (raw.any { it.isWhitespace() || it.code < 0x20 || it.code == 0x7F }) {
            return ResultUrlValidation.Invalid(ResultUrlProblem.BAD_CHARACTERS)
        }
        if (raw.contains('\\')) {
            return ResultUrlValidation.Invalid(ResultUrlProblem.BAD_CHARACTERS)
        }
        // 非 ASCII 原文（未 percent-encode）按 malformed 拒绝：java.net.URI 对
        // path/query 里的原文非 ASCII 并不抛异常（会以 registry-based authority
        // 或原样 path 解析通过），必须显式拦截，不做「猜意图」的编码修复
        if (raw.any { it.code > 0x7E }) {
            return ResultUrlValidation.Invalid(ResultUrlProblem.BAD_SCHEME)
        }

        val uri = try {
            java.net.URI(raw)
        } catch (_: Exception) {
            return ResultUrlValidation.Invalid(ResultUrlProblem.BAD_SCHEME)
        }
        if (!uri.scheme.equals(SCHEME_HTTPS, ignoreCase = true)) {
            return ResultUrlValidation.Invalid(ResultUrlProblem.BAD_SCHEME)
        }
        if (uri.host.isNullOrEmpty()) {
            return ResultUrlValidation.Invalid(ResultUrlProblem.BAD_SCHEME)
        }
        // userinfo 只存在于 authority（`user:pass@host:port`）；query 里的 @ 不误伤
        if (uri.rawAuthority?.contains('@') == true) {
            return ResultUrlValidation.Invalid(ResultUrlProblem.BAD_CHARACTERS)
        }

        return ResultUrlValidation.Valid(raw)
    }

    /**
     * 长 URL 展示断行：在 [maxLine] 内优先于 `/` `?` `&` `=` 处断行
     * （断点字符留在行尾），窗口内没有可用断点才硬切——保证返回的每一行
     * 都不超过 [maxLine]，小屏不横向溢出。
     */
    fun wrapForDisplay(url: String, maxLine: Int = DISPLAY_MAX_LINE): List<String> {
        require(maxLine > MIN_CUT) { "maxLine 必须大于 $MIN_CUT" }
        val lines = mutableListOf<String>()
        var rest = url
        while (rest.length > maxLine) {
            val window = rest.substring(0, maxLine)
            // 窗口内无可用断点时硬切 maxLine 个字符（cut=maxLine-1，行尾不含
            // 分隔符）；有断点则断点字符留在行尾。两条路径行长都 ≤ maxLine
            val cut = window.indexOfLast { it == '/' || it == '?' || it == '&' || it == '=' }
                .takeIf { it >= MIN_CUT }
                ?: (maxLine - 1)
            lines.add(rest.substring(0, cut + 1))
            rest = rest.substring(cut + 1)
        }
        if (rest.isNotEmpty()) lines.add(rest)
        return lines
    }
}
