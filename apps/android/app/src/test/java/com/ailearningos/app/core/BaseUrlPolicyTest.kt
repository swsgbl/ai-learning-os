package com.ailearningos.app.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** M12-01 base URL 校验与规范化规则矩阵 */
class BaseUrlPolicyTest {

    private fun problemOf(raw: String, allowInsecureHttp: Boolean): BaseUrlProblem =
        (BaseUrlPolicy.validate(raw, allowInsecureHttp) as BaseUrlValidation.Invalid).problem

    private fun validOf(raw: String, allowInsecureHttp: Boolean): String =
        (BaseUrlPolicy.validate(raw, allowInsecureHttp) as BaseUrlValidation.Valid).normalized

    // ---------- 空值 / 空白 ----------

    @Test
    fun `empty and blank input rejected`() {
        assertEquals(BaseUrlProblem.EMPTY, problemOf("", allowInsecureHttp = true))
        assertEquals(BaseUrlProblem.EMPTY, problemOf("   ", allowInsecureHttp = true))
    }

    @Test
    fun `internal whitespace rejected`() {
        assertEquals(
            BaseUrlProblem.HAS_WHITESPACE,
            problemOf("http://10.0.2.2 :8000", allowInsecureHttp = true),
        )
        assertEquals(
            BaseUrlProblem.HAS_WHITESPACE,
            problemOf("https://api.example.com/api v1", allowInsecureHttp = false),
        )
    }

    // ---------- scheme / 结构 ----------

    @Test
    fun `missing or unsupported scheme rejected`() {
        assertEquals(BaseUrlProblem.BAD_SCHEME, problemOf("api.example.com", allowInsecureHttp = true))
        assertEquals(BaseUrlProblem.BAD_SCHEME, problemOf("ftp://api.example.com", allowInsecureHttp = true))
        assertEquals(BaseUrlProblem.BAD_SCHEME, problemOf("file:///tmp/x", allowInsecureHttp = true))
        assertEquals(BaseUrlProblem.BAD_SCHEME, problemOf("http://", allowInsecureHttp = true))
    }

    @Test
    fun `query fragment backslash userinfo and control chars rejected`() {
        assertEquals(
            BaseUrlProblem.BAD_CHARACTERS,
            problemOf("https://api.example.com/?debug=1", allowInsecureHttp = true),
        )
        assertEquals(
            BaseUrlProblem.BAD_CHARACTERS,
            problemOf("https://api.example.com/#/page", allowInsecureHttp = true),
        )
        assertEquals(
            BaseUrlProblem.BAD_CHARACTERS,
            problemOf("http://10.0.2.2:8000\\api", allowInsecureHttp = true),
        )
        assertEquals(
            BaseUrlProblem.BAD_CHARACTERS,
            problemOf("http://user:pass@10.0.2.2:8000", allowInsecureHttp = true),
        )
        assertEquals(
            BaseUrlProblem.BAD_CHARACTERS,
            problemOf("http://10.0.2.2:8000/\u0007", allowInsecureHttp = true),
        )
    }

    // ---------- debug：http 仅限 loopback / 局域网 ----------

    @Test
    fun `debug allows http loopback and lan`() {
        val allowed = listOf(
            "http://10.0.2.2:8000",
            "http://10.0.3.2:8000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://192.168.1.20:8000",
            "http://10.1.2.3:8000",
            "http://172.16.0.9:8000",
            "http://172.31.255.255:8000",
            "http://my-laptop.local:8000",
            "http://mypc:8000",
            "http://[::1]:8000",
        )
        allowed.forEach { raw ->
            assertTrue(
                "应放行 $raw",
                BaseUrlPolicy.validate(raw, allowInsecureHttp = true) is BaseUrlValidation.Valid,
            )
        }
    }

    @Test
    fun `debug rejects public http`() {
        assertEquals(
            BaseUrlProblem.PUBLIC_HTTP_FORBIDDEN,
            problemOf("http://api.example.com", allowInsecureHttp = true),
        )
        assertEquals(
            BaseUrlProblem.PUBLIC_HTTP_FORBIDDEN,
            problemOf("http://8.8.8.8:8000", allowInsecureHttp = true),
        )
        // 172.32.0.1 不属于 RFC1918
        assertEquals(
            BaseUrlProblem.PUBLIC_HTTP_FORBIDDEN,
            problemOf("http://172.32.0.1:8000", allowInsecureHttp = true),
        )
    }

    @Test
    fun `debug allows https anywhere`() {
        assertEquals(
            "https://api.example.com/",
            validOf("https://api.example.com", allowInsecureHttp = true),
        )
    }

    // ---------- release：强制 https ----------

    @Test
    fun `release rejects any http even loopback`() {
        assertEquals(
            BaseUrlProblem.INSECURE_FORBIDDEN,
            problemOf("http://10.0.2.2:8000", allowInsecureHttp = false),
        )
        assertEquals(
            BaseUrlProblem.INSECURE_FORBIDDEN,
            problemOf("http://localhost:8000", allowInsecureHttp = false),
        )
        assertTrue(
            BaseUrlPolicy.validate("https://api.example.com", allowInsecureHttp = false)
                is BaseUrlValidation.Valid,
        )
    }

    // ---------- 规范化 ----------

    @Test
    fun `normalizes trailing slash exactly one`() {
        assertEquals("https://api.example.com/", validOf("https://api.example.com", true))
        assertEquals("https://api.example.com/", validOf("https://api.example.com/", true))
        assertEquals("https://api.example.com/", validOf("https://api.example.com///", true))
        assertEquals("https://api.example.com/api/", validOf("https://api.example.com/api", true))
        assertEquals("https://api.example.com/api/", validOf("https://api.example.com/api//", true))
    }

    @Test
    fun `lowercases scheme and host and drops default port`() {
        assertEquals("http://localhost:8000/", validOf("HTTP://LOCALHOST:8000", true))
        assertEquals("https://api.example.com/", validOf("HTTPS://API.Example.COM:443", false))
        assertEquals("https://api.example.com:8443/", validOf("https://API.example.com:8443", false))
    }

    // ---------- host 分类 ----------

    @Test
    fun `loopback classification`() {
        assertTrue(BaseUrlPolicy.isLoopbackHost("localhost"))
        assertTrue(BaseUrlPolicy.isLoopbackHost("127.0.0.1"))
        assertTrue(BaseUrlPolicy.isLoopbackHost("127.9.9.9"))
        assertTrue(BaseUrlPolicy.isLoopbackHost("10.0.2.2"))
        assertTrue(BaseUrlPolicy.isLoopbackHost("[::1]"))
        assertTrue(!BaseUrlPolicy.isLoopbackHost("192.168.0.1"))
    }

    @Test
    fun `lan classification`() {
        assertTrue(BaseUrlPolicy.isPrivateLanHost("192.168.0.1"))
        assertTrue(BaseUrlPolicy.isPrivateLanHost("10.0.0.1"))
        assertTrue(BaseUrlPolicy.isPrivateLanHost("172.20.1.1"))
        assertTrue(BaseUrlPolicy.isPrivateLanHost("nas.local"))
        assertTrue(BaseUrlPolicy.isPrivateLanHost("mypc"))
        assertTrue(!BaseUrlPolicy.isPrivateLanHost("172.32.0.1"))
        assertTrue(!BaseUrlPolicy.isPrivateLanHost("api.example.com"))
    }
}
