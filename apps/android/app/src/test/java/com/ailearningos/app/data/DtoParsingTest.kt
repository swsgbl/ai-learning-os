package com.ailearningos.app.data

import com.ailearningos.app.data.remote.AiosJson
import com.ailearningos.app.data.remote.AuthStatusResponse
import com.ailearningos.app.data.remote.HealthResponse
import com.ailearningos.app.data.remote.LoginRequest
import com.ailearningos.app.data.remote.PrivacyResponse
import com.ailearningos.app.data.remote.TokenResponse
import com.ailearningos.app.data.remote.UserResponse
import com.ailearningos.app.data.remote.toDomain
import kotlinx.serialization.SerializationException
import kotlinx.serialization.encodeToString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** DTO 与服务端 Pydantic 契约的解析矩阵（snake_case / 未知键 / 缺字段） */
class DtoParsingTest {

    // ---------- /health ----------

    @Test
    fun `parses health response`() {
        val dto = AiosJson.decodeFromString<HealthResponse>(
            """{"status":"ok","service":"ai-learning-os-api"}""",
        )
        assertEquals("ok", dto.status)
        assertEquals("ai-learning-os-api", dto.service)
    }

    @Test
    fun `ignores unknown fields in health response`() {
        val dto = AiosJson.decodeFromString<HealthResponse>(
            """{"status":"ok","unexpected_key":123}""",
        )
        assertEquals("ok", dto.status)
        assertNull(dto.service)
    }

    // ---------- /auth/status ----------

    @Test
    fun `parses auth status both states`() {
        assertTrue(AiosJson.decodeFromString<AuthStatusResponse>("""{"auth_enabled":true}""").authEnabled)
        assertFalse(AiosJson.decodeFromString<AuthStatusResponse>("""{"auth_enabled":false}""").authEnabled)
    }

    // ---------- /auth/login ----------

    @Test
    fun `parses token response`() {
        val dto = AiosJson.decodeFromString<TokenResponse>(
            """{"access_token":"fixture-access-token","token_type":"bearer"}""",
        )
        assertEquals("fixture-access-token", dto.accessToken)
        assertEquals("bearer", dto.tokenType)
    }

    @Test
    fun `missing access_token rejected`() {
        val error = runCatching {
            AiosJson.decodeFromString<TokenResponse>("""{"token_type":"bearer"}""")
        }.exceptionOrNull()
        assertTrue("缺少 access_token 应解析失败", error is SerializationException)
    }

    @Test
    fun `login request serializes exact contract`() {
        val encoded = AiosJson.encodeToString(LoginRequest(username = "alice", password = "pw12345678"))
        assertEquals("""{"username":"alice","password":"pw12345678"}""", encoded)
    }

    // ---------- /auth/me ----------

    @Test
    fun `parses user response and maps to domain`() {
        val dto = AiosJson.decodeFromString<UserResponse>(
            """{"id":"u1","username":"alice","role":"admin","created_at":"2026-09-06T00:00:00+00:00"}""",
        )
        val user = dto.toDomain()
        assertEquals("u1", user.id)
        assertEquals("alice", user.username)
        assertEquals("admin", user.role)
        assertTrue(user.isAdmin)
    }

    @Test
    fun `user role learner is not admin`() {
        val dto = AiosJson.decodeFromString<UserResponse>(
            """{"id":"u2","username":"bob","role":"learner"}""",
        )
        assertTrue(!dto.toDomain().isAdmin)
    }

    @Test
    fun `user missing role rejected`() {
        val error = runCatching {
            AiosJson.decodeFromString<UserResponse>("""{"id":"u3","username":"carol"}""")
        }.exceptionOrNull()
        assertTrue("缺少 role 应解析失败", error is SerializationException)
    }

    // ---------- /system/privacy ----------

    @Test
    fun `parses privacy snapshot`() {
        val dto = AiosJson.decodeFromString<PrivacyResponse>(
            """
            {
              "model_route": "local",
              "voice_mode": "hybrid",
              "search_mode": "cloud",
              "store_audio": true,
              "send_context_to_cloud": false
            }
            """.trimIndent(),
        )
        val snapshot = dto.toDomain()
        assertEquals("local", snapshot.modelRoute)
        assertEquals("hybrid", snapshot.voiceMode)
        assertEquals("cloud", snapshot.searchMode)
        assertTrue(snapshot.storesAudio)
        assertFalse(snapshot.sendsContextToCloud)
    }
}
