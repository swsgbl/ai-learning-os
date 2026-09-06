package com.ailearningos.app.data

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.testutil.FakeSettingsStore
import com.ailearningos.app.testutil.FakeTokenStore
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * M12-02 考试仓储：端点契约（方法/路径/请求体）与错误映射（401/404/409/5xx/网络）。
 * 全部走 MockWebServer fixture（显式绑 loopback）；fixture 值为显式假值。
 */
class ExamRepositoryTest {

    private lateinit var server: MockWebServer
    private lateinit var repository: ExamRepository

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
        repository = ExamRepository(apiProvider)
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun jsonResponse(body: String, code: Int = 200) = MockResponse()
        .setResponseCode(code)
        .setHeader("Content-Type", "application/json")
        .setBody(body)

    private val examBody = """
        {"exam_id":"exam_abc","paper_id":"functions-basics","paper_title":"卷","mode":"exam",
         "status":"active","server_started_at":"2026-09-06T12:00:00+00:00",
         "server_end_at":"2026-09-06T12:06:00+00:00","server_remaining_seconds":300,
         "questions":[{"id":"q1","type":"mcq","stem":"题干","options":[{"key":"A","text":"甲"},{"key":"B","text":"乙"}]}],
         "answers":{},"next_sequence":1}
    """.trimIndent()

    // ---------- 试卷列表 ----------

    @Test
    fun `papers hits list endpoint and parses`() = runTest {
        server.enqueue(jsonResponse("""[{"id":"p1","title":"卷","subtitle":"","source":"seed","university":null,"year":2026,"subject":"math","difficulty":"core","duration_minutes":6,"tags":[],"origin_url":null}]"""))
        val papers = repository.papers()
        assertEquals(1, papers.size)
        assertEquals("p1", papers[0].id)
        assertEquals("/api/v1/papers", server.takeRequest().path)
    }

    @Test
    fun `papers empty list maps to empty state`() = runTest {
        server.enqueue(jsonResponse("[]"))
        assertTrue(repository.papers().isEmpty())
    }

    @Test
    fun `papers server failure maps to SERVER`() = runTest {
        server.enqueue(MockResponse().setResponseCode(503))
        val error = runCatching { repository.papers() }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.SERVER)
    }

    @Test
    fun `papers network failure maps to NETWORK`() = runTest {
        server.shutdown()
        val error = runCatching { repository.papers() }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.NETWORK)
    }

    // ---------- 开考 ----------

    @Test
    fun `startExam posts mode exam and adopts session`() = runTest {
        server.enqueue(jsonResponse(examBody, code = 201))
        val session = repository.startExam("functions-basics")
        assertEquals("exam_abc", session.examId)
        assertTrue(session.isActive)
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/v1/papers/functions-basics/exams", request.path)
        assertEquals("""{"mode":"exam"}""", request.body.readUtf8())
    }

    @Test
    fun `startExam unknown paper maps to NOT_FOUND`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"试卷不存在"}""", code = 404))
        val error = runCatching { repository.startExam("ghost") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.NOT_FOUND)
    }

    @Test
    fun `startExam without credentials maps to UNAUTHORIZED`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"Missing bearer token"}""", code = 401))
        val error = runCatching { repository.startExam("p1") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.UNAUTHORIZED)
    }

    // ---------- 读考试（恢复） ----------

    @Test
    fun `exam gets session by id`() = runTest {
        server.enqueue(jsonResponse(examBody))
        val session = repository.exam("exam_abc")
        assertEquals(1, session.nextSequence)
        assertEquals("/api/v1/exams/exam_abc", server.takeRequest().path)
    }

    @Test
    fun `exam foreign exam maps to NOT_FOUND`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"考试不存在"}""", code = 404))
        val error = runCatching { repository.exam("exam_other") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.NOT_FOUND)
    }

    // ---------- 保存答案 ----------

    @Test
    fun `saveAnswer puts append-only contract and adopts response`() = runTest {
        server.enqueue(jsonResponse(examBody))
        val saved = repository.saveAnswer("exam_abc", sequence = 1, questionId = "q1", answer = "B")
        assertEquals("exam_abc", saved.examId)
        val request = server.takeRequest()
        assertEquals("PUT", request.method)
        assertEquals("/api/v1/exams/exam_abc/answers", request.path)
        assertEquals(
            """{"sequence":1,"question_id":"q1","answer":"B"}""",
            request.body.readUtf8(),
        )
    }

    @Test
    fun `saveAnswer sequence conflict maps to CONFLICT`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"同一事件序号不能承载不同答案"}""", code = 409))
        val error = runCatching {
            repository.saveAnswer("exam_abc", sequence = 1, questionId = "q1", answer = "A")
        }.exceptionOrNull()
        assertTrue("409 必须与 404/401 区分", error is AppError && error.kind == AppErrorKind.CONFLICT)
    }

    @Test
    fun `saveAnswer exam finished maps to CONFLICT`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"考试已结束，不能再修改答案"}""", code = 409))
        val error = runCatching {
            repository.saveAnswer("exam_abc", sequence = 2, questionId = "q1", answer = "B")
        }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.CONFLICT)
    }

    @Test
    fun `saveAnswer unknown question maps to NOT_FOUND`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"题目不存在"}""", code = 404))
        val error = runCatching {
            repository.saveAnswer("exam_abc", sequence = 1, questionId = "ghost", answer = "B")
        }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.NOT_FOUND)
    }

    // ---------- 交卷 ----------

    @Test
    fun `submit posts empty body and parses submission`() = runTest {
        server.enqueue(jsonResponse("""{"exam_id":"exam_abc","paper_id":"p","paper_title":"卷","mode":"exam","status":"submitted","score":100,"correct_count":1,"total_count":2,"duration_seconds":240,"rule_version":"v1","items":[],"questions":[]}"""))
        val submission = repository.submit("exam_abc")
        assertEquals("submitted", submission.status)
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/v1/exams/exam_abc/submit", request.path)
        assertEquals("{}", request.body.readUtf8())
    }

    @Test
    fun `submit unknown exam maps to NOT_FOUND`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"考试不存在"}""", code = 404))
        val error = runCatching { repository.submit("ghost") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.NOT_FOUND)
    }

    // ---------- submission / report ----------

    @Test
    fun `submission not generated maps to NOT_FOUND`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"审阅报告尚未生成"}""", code = 404))
        val error = runCatching { repository.submission("exam_abc") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.NOT_FOUND)
    }

    @Test
    fun `report not generated maps to NOT_FOUND`() = runTest {
        server.enqueue(jsonResponse("""{"detail":"审阅报告尚未生成"}""", code = 404))
        val error = runCatching { repository.report("exam_abc") }.exceptionOrNull()
        assertTrue(error is AppError && error.kind == AppErrorKind.NOT_FOUND)
    }

    @Test
    fun `report parses full structure`() = runTest {
        server.enqueue(
            jsonResponse(
                """
                {"exam_id":"exam_abc","paper_title":"卷","mode":"exam","score":100,
                 "score_earned":60.0,"score_max":100.0,"correct_count":1,"total_count":2,"reviewed_count":0,
                 "items":[],"concepts":[{"concept":"导数","correct":1,"total":1,"reviewed":0,"ratio":1.0}],
                 "mistakes":[],"remediation_tasks":[{"kind":"variant_practice","title":"变式","detail":"d","question_id":"q1"}],
                 "evidence_ids":[]}
                """.trimIndent(),
            ),
        )
        val report = repository.report("exam_abc")
        assertEquals(60.0, report.scoreEarned, 0.0)
        assertEquals(1, report.concepts.size)
        assertEquals("variant_practice", report.remediationTasks[0].kind)
    }
}
