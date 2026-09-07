package com.ailearningos.app.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * M12-04 结果来源 URL 安全校验与展示断行规则矩阵：
 * 仅 https 可通过（交给 ACTION_VIEW 的唯一入口），非 https / malformed
 * fail-closed 拒绝且原文如实展示；断行保证每行不超过 maxLine、字符零丢失。
 */
class ResultUrlPolicyTest {

    private fun problemOf(raw: String?): ResultUrlProblem =
        (ResultUrlPolicy.validate(raw) as ResultUrlValidation.Invalid).problem

    private fun validOf(raw: String): String =
        (ResultUrlPolicy.validate(raw) as ResultUrlValidation.Valid).url

    private fun isInvalid(raw: String?): Boolean =
        ResultUrlPolicy.validate(raw) is ResultUrlValidation.Invalid

    // ---------- 空值 / 空白 ----------

    @Test
    fun `empty and blank input rejected`() {
        assertEquals(ResultUrlProblem.EMPTY, problemOf(null))
        assertEquals(ResultUrlProblem.EMPTY, problemOf(""))
        assertEquals(ResultUrlProblem.EMPTY, problemOf("   "))
        // NBSP 也算空白（Kotlin isWhitespace 含 isSpaceChar），不留给 URI 解析
        assertEquals(ResultUrlProblem.EMPTY, problemOf("\u00A0"))
    }

    @Test
    fun `internal whitespace rejected`() {
        assertEquals(ResultUrlProblem.BAD_CHARACTERS, problemOf("https://example.com/a b"))
        assertEquals(ResultUrlProblem.BAD_CHARACTERS, problemOf("https://exa\u00A0mple.com/"))
    }

    // ---------- 非法字符 ----------

    @Test
    fun `control characters and backslash rejected`() {
        assertEquals(ResultUrlProblem.BAD_CHARACTERS, problemOf("https://example.com/\u0007"))
        assertEquals(ResultUrlProblem.BAD_CHARACTERS, problemOf("https://example.com/\u007F"))
        assertEquals(ResultUrlProblem.BAD_CHARACTERS, problemOf("https://example.com\\path"))
    }

    // ---------- scheme / 结构 ----------

    @Test
    fun `non https scheme rejected`() {
        assertEquals(ResultUrlProblem.BAD_SCHEME, problemOf("http://example.com/"))
        assertEquals(ResultUrlProblem.BAD_SCHEME, problemOf("HTTP://example.com/"))
        assertEquals(ResultUrlProblem.BAD_SCHEME, problemOf("ftp://example.com/"))
        assertEquals(ResultUrlProblem.BAD_SCHEME, problemOf("file:///tmp/x"))
        assertEquals(ResultUrlProblem.BAD_SCHEME, problemOf("javascript:alert(1)"))
        assertEquals(ResultUrlProblem.BAD_SCHEME, problemOf("intent://example.com/#Intent;end"))
    }

    @Test
    fun `missing scheme or host rejected`() {
        // 无 scheme：不是 URL
        assertEquals(ResultUrlProblem.BAD_SCHEME, problemOf("example.com/path"))
        // 单斜杠 / 空 authority：解析不出 host
        assertEquals(ResultUrlProblem.BAD_SCHEME, problemOf("https:/example.com"))
        assertEquals(ResultUrlProblem.BAD_SCHEME, problemOf("https://"))
        // opaque 形态（scheme 后非 //）：无 host
        assertEquals(ResultUrlProblem.BAD_SCHEME, problemOf("https:example.com"))
    }

    @Test
    fun `unparsable raw non-ascii rejected`() {
        // 未 percent-encode 的非 ASCII 原文无法解析：按 malformed 拒绝，不猜意图修复
        assertEquals(ResultUrlProblem.BAD_SCHEME, problemOf("https://example.com/路径"))
        assertEquals(ResultUrlProblem.BAD_SCHEME, problemOf("https://例え.jp/"))
    }

    // ---------- 合法 https：原样放行 ----------

    @Test
    fun `valid https urls pass through unchanged`() {
        // path/query/fragment 是结果链接的正常形态，且不规范化（原样返回）
        assertEquals("https://example.com/", validOf("https://example.com/"))
        assertEquals(
            "https://example.com/courses/calculus/exam-2024.pdf",
            validOf("https://example.com/courses/calculus/exam-2024.pdf"),
        )
        assertEquals(
            "https://example.com/search?q=%E9%AB%98%E7%AD%89%E6%95%B0%E5%AD%A6&page=2",
            validOf("https://example.com/search?q=%E9%AB%98%E7%AD%89%E6%95%B0%E5%AD%A6&page=2"),
        )
        assertEquals("https://example.com/page#section", validOf("https://example.com/page#section"))
        assertEquals("https://example.com:8443/x", validOf("https://example.com:8443/x"))
        assertEquals("https://[2001:db8::1]/docs", validOf("https://[2001:db8::1]/docs"))
        // scheme/host 大小写不敏感，但返回值保留原文
        assertEquals("HTTPS://API.Example.COM/X", validOf("HTTPS://API.Example.COM/X"))
    }

    @Test
    fun `userinfo in authority rejected`() {
        assertEquals(ResultUrlProblem.BAD_CHARACTERS, problemOf("https://user:pass@example.com/"))
        // 视觉混淆形态：@ 前的 good.com 不是真实 host
        assertEquals(ResultUrlProblem.BAD_CHARACTERS, problemOf("https://good.com@evil.com/"))
    }

    @Test
    fun `at sign in query is not userinfo`() {
        assertEquals(
            "https://example.com/search?q=user@example.com",
            validOf("https://example.com/search?q=user@example.com"),
        )
    }

    // ---------- 展示断行 ----------

    @Test
    fun `short url stays on one line unchanged`() {
        assertEquals(listOf("https://example.com/"), ResultUrlPolicy.wrapForDisplay("https://example.com/"))
        // 恰好 maxLine 长度不触发断行
        val exact = "a".repeat(ResultUrlPolicy.DISPLAY_MAX_LINE)
        assertEquals(listOf(exact), ResultUrlPolicy.wrapForDisplay(exact))
        assertEquals(emptyList<String>(), ResultUrlPolicy.wrapForDisplay(""))
    }

    @Test
    fun `long url wraps at separators keeping separator at line end`() {
        val url = "https://example.com/" + "a".repeat(39) + "/bbbb"
        assertEquals(
            listOf("https://example.com/", "a".repeat(39) + "/", "bbbb"),
            ResultUrlPolicy.wrapForDisplay(url),
        )
    }

    @Test
    fun `unbroken run is hard cut at max line`() {
        val lines = ResultUrlPolicy.wrapForDisplay("A".repeat(100))
        assertEquals(listOf("A".repeat(40), "A".repeat(40), "A".repeat(20)), lines)
    }

    @Test
    fun `separator closer than min cut is ignored`() {
        // '/' 在索引 2（< MIN_CUT=8）：不用它断行，按窗口硬切
        val url = "ab/" + "c".repeat(50)
        val lines = ResultUrlPolicy.wrapForDisplay(url)
        assertEquals(listOf(40, 13), lines.map { it.length })
    }

    @Test
    fun `lines never exceed max line and reassemble exactly`() {
        val samples = listOf(
            "https://example.com/",
            "https://example.com/courses/calculus/exam-2024.pdf",
            "https://example.com/search?q=%E9%AB%98%E7%AD%89%E6%95%B0%E5%AD%A6&section=1&lang=zh",
            "https://" + "x".repeat(200),
            "https://a.io/" + "bbbbbbbb/".repeat(30),
            "https://example.com/?q=" + "&".repeat(99),
        )
        samples.forEach { url ->
            val lines = ResultUrlPolicy.wrapForDisplay(url)
            assertTrue("行数不能为空：$url", lines.isNotEmpty())
            lines.forEach { line ->
                assertTrue("行超长（${line.length}）：「$line」", line.length <= ResultUrlPolicy.DISPLAY_MAX_LINE)
            }
            // 断行只影响显示，字符零丢失（可溯源展示的前提）
            assertEquals(url, lines.joinToString(""))
        }
    }

    @Test
    fun `max line below min cut rejected`() {
        try {
            ResultUrlPolicy.wrapForDisplay("https://example.com/", maxLine = 8)
            fail("maxLine <= MIN_CUT 应该 require 失败")
        } catch (_: IllegalArgumentException) {
            // 预期：参数契约由 require 锁定
        }
    }
}
