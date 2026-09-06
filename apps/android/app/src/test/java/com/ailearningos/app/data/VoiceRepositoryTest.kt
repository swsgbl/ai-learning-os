package com.ailearningos.app.data

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.testutil.FakeSettingsStore
import com.ailearningos.app.testutil.FakeTokenStore
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.Buffer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * M12-03 语音仓储：端点契约（方法/路径/查询参数/请求体/multipart/二进制响应）
 * 与错误映射（401/404/409/422/5xx/网络）。全部走 MockWebServer fixture
 * （显式绑 loopback）；fixture 值为显式假值，不触网。
 */
class VoiceRepositoryTest {

    private lateinit var server: MockWebServer
    private lateinit var repository: VoiceRepository

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start(java.net.InetAddress.getLoopbackAddress(), 0)
        val tokenCache = SessionTokenCache(FakeTokenStore())
        val apiProvider = ApiProvider(
            config = ApiConfigProvider(
                settingsStore = FakeSettingsStore("http://127.0.0.1:${server.port}/"),
                allowInsecureHttp = true,
            ),
            tokenCache = tokenCache,
        )
        repository = VoiceRepository(apiProvider)
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun jsonResponse(body: String, code: Int = 200) = MockResponse()
        .setResponseCode(code)
        .setHeader("Content-Type", "application/json")
        .setBody(body)

    private val sessionBody = """
        {"session_id":"vs_1","exam_id":"exam_abc","status":"SESSION_READY",
         "question_index":0,"revision":1,
         "created_at":"2026-09-06T12:00:00+00:00","updated_at":"2026-09-06T12:00:00+00:00"}
    """.trimIndent()

    // ---------- providers ----------

    @Test
    fun `providers hits endpoint and parses fallback flags`() = runTest {
        server.enqueue(
            jsonResponse(
                """{"voice_mode":"hybrid",
                    "asr":{"requested":null,"provider":"fake","fallback":false},
                    "tts":{"requested":"cloud-openai-tts","provider":"tone","fallback":true},
                    "privacy_store_audio":false,"privacy_send_context_to_cloud":true}""",
            ),
        )
        val providers = repository.providers()
        assertEquals("hybrid", providers.voiceMode)
        assertTrue(providers.tts.fallback)
        assertEquals("/api/v1/voice/providers", server.takeRequest().path)
    }

    @Test
    fun `providers unavailable maps to SERVER`() = runTest {
        server.enqueue(MockResponse().setResponseCode(503))
        val error = runCatching { repository.providers() }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.SERVER)
    }

    // ---------- 创建/列出/读取会话 ----------

    @Test
    fun `createSession posts exam id and adopts session`() = runTest {
        server.enqueue(jsonResponse(sessionBody, code = 201))
        val session = repository.createSession("exam_abc")
        assertEquals("vs_1", session.sessionId)
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/v1/voice/sessions", request.path)
        assertEquals("""{"exam_id":"exam_abc"}""", request.body.readUtf8())
    }

    @Test
    fun `createSession submitted exam maps to CONFLICT`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"考试状态 submitted 不允许语音作答"}""", code = 409))
        val error = runCatching { repository.createSession("exam_x") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.CONFLICT)
    }

    @Test
    fun `createSession unknown exam maps to NOT_FOUND`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"考试不存在"}""", code = 404))
        val error = runCatching { repository.createSession("ghost") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.NOT_FOUND)
    }

    @Test
    fun `sessions lists by exam query param`() = runTest {
        server.enqueue(jsonResponse("""{"items":[$sessionBody]}"""))
        val sessions = repository.sessions("exam_abc")
        assertEquals(1, sessions.size)
        assertEquals("vs_1", sessions[0].sessionId)
        assertEquals("/api/v1/voice/sessions?exam_id=exam_abc", server.takeRequest().path)
    }

    @Test
    fun `session gets by id`() = runTest {
        server.enqueue(jsonResponse(sessionBody))
        assertEquals("SESSION_READY", repository.session("vs_1").status)
        assertEquals("/api/v1/voice/sessions/vs_1", server.takeRequest().path)
    }

    // ---------- resume ----------

    @Test
    fun `resume returns question projection and committed answer`() = runTest {
        server.enqueue(
            jsonResponse(
                """{"session":{"session_id":"vs_1","exam_id":"e","status":"ANSWER_COMMITTED",
                    "question_index":0,"revision":3,"created_at":"t","updated_at":"t"},
                    "question":{"id":"q1","type":"mcq","stem":"题干",
                    "options":[{"key":"A","text":"甲"},{"key":"B","text":"乙"}]},
                    "committed_answer":"B","question_total":5}""",
            ),
        )
        val resumed = repository.resume("vs_1")
        assertEquals("q1", resumed.question?.id)
        assertEquals("B", resumed.committedAnswer)
        assertEquals(5, resumed.questionTotal)
        assertEquals("/api/v1/voice/sessions/vs_1/resume", server.takeRequest().path)
    }

    @Test
    fun `resume terminal session maps to CONFLICT`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"会话已结束，无可恢复状态"}""", code = 409))
        val error = runCatching { repository.resume("vs_done") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.CONFLICT)
    }

    // ---------- commands ----------

    @Test
    fun `command posts type only for control commands`() = runTest {
        server.enqueue(
            jsonResponse(
                """{"applied_event":"start_reading","from_status":"SESSION_READY",
                    "session":{"session_id":"vs_1","exam_id":"e","status":"READING_QUESTION",
                    "question_index":0,"revision":2,"created_at":"t","updated_at":"t"}}""",
            ),
        )
        val out = repository.command("vs_1", "start_reading")
        assertEquals("READING_QUESTION", out.session.status)
        val request = server.takeRequest()
        assertEquals("/api/v1/voice/sessions/vs_1/commands", request.path)
        assertEquals("""{"type":"start_reading","ambiguous":false}""", request.body.readUtf8())
    }

    @Test
    fun `command carries question id and answer for propose`() = runTest {
        server.enqueue(
            jsonResponse(
                """{"applied_event":"answer_proposed","from_status":"WAITING_ANSWER",
                    "session":{"session_id":"vs_1","exam_id":"e","status":"ANSWER_COMMITTED",
                    "question_index":0,"revision":3,"created_at":"t","updated_at":"t"}}""",
            ),
        )
        val out = repository.command("vs_1", "answer_proposed", questionId = "q1", answer = "B")
        assertEquals("ANSWER_COMMITTED", out.session.status)
        val request = server.takeRequest()
        assertEquals("/api/v1/voice/sessions/vs_1/commands", request.path)
        assertEquals(
            """{"type":"answer_proposed","question_id":"q1","answer":"B","ambiguous":false}""",
            request.body.readUtf8(),
        )
    }

    @Test
    fun `command illegal transition maps to CONFLICT`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"语音会话状态 REPORT_READY 不接受事件 skip"}""", code = 409))
        val error = runCatching { repository.command("vs_1", "skip") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.CONFLICT)
    }

    @Test
    fun `command unknown type maps to BAD_RESPONSE`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"未知语音命令: dance"}""", code = 422))
        val error = runCatching { repository.command("vs_1", "dance") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.BAD_RESPONSE)
    }

    // ---------- intents / answers ----------

    @Test
    fun `intent posts transcript and parses applied session`() = runTest {
        server.enqueue(
            jsonResponse(
                """{"transcript":"选 B","intent":"choose","letter":"B","ordinal":null,
                    "ambiguous":false,"fsm_command":"answer_proposed","fsm_applied":true,
                    "applied_event":"answer_proposed",
                    "session":{"session_id":"vs_1","exam_id":"e","status":"ANSWER_COMMITTED",
                    "question_index":0,"revision":2,"created_at":"t","updated_at":"t"}}""",
            ),
        )
        val out = repository.intent("vs_1", "选 B")
        assertTrue(out.fsmApplied)
        val request = server.takeRequest()
        assertEquals("/api/v1/voice/sessions/vs_1/intents", request.path)
        assertEquals("""{"transcript":"选 B"}""", request.body.readUtf8())
    }

    @Test
    fun `submitAnswer posts event id and parses idempotent replay`() = runTest {
        server.enqueue(
            jsonResponse(
                """{"event_id":"evt-1","idempotent":true,"accepted":true,
                    "normalized_answer":"B","intent":"choose","question_id":"q1",
                    "session":{"session_id":"vs_1","exam_id":"e","status":"ANSWER_COMMITTED",
                    "question_index":0,"revision":3,"created_at":"t","updated_at":"t"}}""",
            ),
        )
        val out = repository.submitAnswer("vs_1", "选 B", eventId = "evt-1")
        assertTrue(out.idempotent)
        val request = server.takeRequest()
        assertEquals("/api/v1/voice/sessions/vs_1/answers", request.path)
        assertEquals("""{"transcript":"选 B","event_id":"evt-1","confidence":0.0}""", request.body.readUtf8())
    }

    // ---------- report ----------

    @Test
    fun `report parses projection`() = runTest {
        server.enqueue(
            jsonResponse(
                """{"session_id":"vs_1","exam_id":"e","spoken_text":"考试完成！",
                    "mistake_summary":[],"remediation_summary":[],
                    "written_report_url":"/api/v1/exams/e/report"}""",
            ),
        )
        val report = repository.report("vs_1")
        assertEquals("考试完成！", report.spokenText)
        assertEquals("/api/v1/voice/sessions/vs_1/report", server.takeRequest().path)
    }

    @Test
    fun `report without grading maps to CONFLICT`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"考试尚未提交判分，先提交后获取报告"}""", code = 409))
        val error = runCatching { repository.report("vs_1") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.CONFLICT)
    }

    // ---------- transcribe（multipart） ----------

    @Test
    fun `transcribe uploads wav as multipart and parses text`() = runTest {
        server.enqueue(
            jsonResponse(
                """{"id":7,"text":"[fake] 选 B","confidence":0.9,"provider":"fake",
                    "latency_ms":5,"audio_bytes":44,"audio_stored":false,
                    "audio_object_key":null}""",
            ),
        )
        val transcription = repository.transcribe(byteArrayOf(1, 2, 3))
        assertEquals("[fake] 选 B", transcription.text)
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/v1/voice/transcribe", request.path)
        val contentType = request.getHeader("Content-Type").orEmpty()
        assertTrue("应为 multipart：$contentType", contentType.startsWith("multipart/"))
        val body = request.body.readUtf8()
        assertTrue("应含音频文件名", body.contains("clip.wav"))
        assertTrue("应含 wav 媒体类型", body.contains("audio/wav"))
    }

    @Test
    fun `transcribe oversized audio maps to BAD_RESPONSE`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"音频超过 20MB 上限"}""", code = 413))
        val error = runCatching { repository.transcribe(ByteArray(10)) }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.BAD_RESPONSE)
    }

    // ---------- synthesize（WAV 二进制） ----------

    @Test
    fun `synthesize posts text and returns raw wav bytes`() = runTest {
        val wav = byteArrayOf(0x52, 0x49, 0x46, 0x46, 1, 2, 3, 4) // RIFF...
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "audio/wav")
                .setHeader("X-Voice-Provider", "tone")
                .setBody(Buffer().write(wav)),
        )
        val bytes = repository.synthesize("读题")
        assertTrue(bytes.contentEquals(wav))
        val request = server.takeRequest()
        assertEquals("/api/v1/voice/synthesize", request.path)
        assertEquals("""{"text":"读题"}""", request.body.readUtf8())
    }

    @Test
    fun `synthesize provider failure maps to SERVER`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"cloud TTS 端点返回 HTTP 503"}""", code = 502))
        val error = runCatching { repository.synthesize("读题") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.SERVER)
    }

    // ---------- trace ----------

    @Test
    fun `trace posts stage duration and ids`() = runTest {
        server.enqueue(
            jsonResponse(
                """{"id":9,"stage":"vad","duration_ms":80,"source":"client",
                    "session_id":"vs_1","exam_id":"e","question_id":"q1",
                    "created_at":"2026-09-06T12:00:00+00:00"}""",
            ),
        )
        repository.reportTrace(stage = "vad", durationMillis = 80L, sessionId = "vs_1", examId = "e", questionId = "q1")
        val request = server.takeRequest()
        assertEquals("/api/v1/voice/trace", request.path)
        assertEquals(
            """{"stage":"vad","duration_ms":80,"session_id":"vs_1","exam_id":"e","question_id":"q1"}""",
            request.body.readUtf8(),
        )
    }

    @Test
    fun `trace network failure maps to NETWORK`() = runTest {
        server.shutdown()
        val error = runCatching {
            repository.reportTrace(stage = "vad", durationMillis = 80L)
        }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.NETWORK)
    }
}
