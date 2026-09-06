package com.ailearningos.app.data

import com.ailearningos.app.data.remote.AiosJson
import com.ailearningos.app.data.remote.ExamSessionResponse
import com.ailearningos.app.data.remote.PaperResponse
import com.ailearningos.app.data.remote.ReportResponse
import com.ailearningos.app.data.remote.SaveAnswerRequest
import com.ailearningos.app.data.remote.StartExamRequest
import com.ailearningos.app.data.remote.SubmissionResponse
import com.ailearningos.app.data.remote.SubmitRequest
import com.ailearningos.app.data.remote.toDomain
import kotlinx.serialization.SerializationException
import kotlinx.serialization.encodeToString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** M12-02 考试域 DTO 解析矩阵（snake_case / 可空字段 / 未知键 / 请求体契约） */
class ExamDtoParsingTest {

    // ---------- 试卷 ----------

    @Test
    fun `parses paper list with nullable fields`() {
        val dto = AiosJson.decodeFromString<List<PaperResponse>>(
            """
            [
              {"id":"functions-basics","title":"函数与导数基础","subtitle":"seed 卷",
               "source":"AI Learning OS seed","university":null,"year":2026,"subject":"math",
               "difficulty":"core","duration_minutes":6,"tags":["函数","导数"],"origin_url":null},
              {"id":"imported-1","title":"导入卷","subtitle":"","source":"upload",
               "university":"某大学","year":null,"subject":"math","difficulty":"basic",
               "duration_minutes":90,"tags":[],"origin_url":"https://example.org/paper"}
            ]
            """.trimIndent(),
        )
        assertEquals(2, dto.size)
        assertNull(dto[0].university)
        assertEquals(2026, dto[0].year)
        assertEquals("某大学", dto[1].university)
        assertNull(dto[1].year)
        val domain = dto[0].toDomain()
        assertEquals("functions-basics", domain.id)
        assertEquals(6, domain.durationMinutes)
        assertEquals(listOf("函数", "导数"), domain.tags)
    }

    @Test
    fun `paper missing required duration rejected`() {
        val error = runCatching {
            AiosJson.decodeFromString<PaperResponse>(
                """{"id":"p1","title":"t","subtitle":"s","source":"x","subject":"math","difficulty":"core"}""",
            )
        }.exceptionOrNull()
        assertTrue("缺少 duration_minutes 应解析失败", error is SerializationException)
    }

    // ---------- 考试会话 ----------

    @Test
    fun `parses exam session with server authority fields`() {
        val dto = AiosJson.decodeFromString<ExamSessionResponse>(
            """
            {
              "exam_id":"exam_abc","paper_id":"functions-basics","paper_title":"函数与导数基础",
              "mode":"exam","status":"active",
              "server_started_at":"2026-09-06T12:00:00.123456+00:00",
              "server_end_at":"2026-09-06T12:06:00+00:00",
              "server_remaining_seconds":300,
              "questions":[
                {"id":"q1","type":"mcq","stem":"题干","options":[{"key":"A","text":"甲"},{"key":"B","text":"乙"}]},
                {"id":"q2","type":"short_answer","stem":"简答","options":[]}
              ],
              "answers":{"q1":"B"},
              "next_sequence":2
            }
            """.trimIndent(),
        )
        val domain = dto.toDomain()
        assertEquals("exam_abc", domain.examId)
        assertTrue(domain.isActive)
        assertEquals(2, domain.questions.size)
        assertEquals(2, domain.questions[0].options.size)
        assertTrue(domain.questions[0].isSingleChoice)
        assertTrue(domain.questions[1].isTextInput)
        assertEquals(mapOf("q1" to "B"), domain.answers)
        assertEquals(2, domain.nextSequence)
        assertEquals(300, domain.serverRemainingSeconds)
    }

    @Test
    fun `exam session unknown status is neither active nor submitted`() {
        val domain = AiosJson.decodeFromString<ExamSessionResponse>(
            """{"exam_id":"e","paper_id":"p","paper_title":"t","mode":"exam","status":"weird",
               "server_started_at":"","server_end_at":"","server_remaining_seconds":0,
               "questions":[],"answers":{},"next_sequence":1}""",
        ).toDomain()
        assertTrue(!domain.isActive)
        assertTrue(!domain.isSubmitted)
        assertTrue(!domain.isExpired)
    }

    @Test
    fun `save answer request serializes exact contract`() {
        val encoded = AiosJson.encodeToString(
            SaveAnswerRequest(sequence = 3, questionId = "q1", answer = "B"),
        )
        assertEquals("""{"sequence":3,"question_id":"q1","answer":"B"}""", encoded)
    }

    @Test
    fun `start exam request defaults to exam mode`() {
        assertEquals("""{"mode":"exam"}""", AiosJson.encodeToString(StartExamRequest()))
    }

    @Test
    fun `submit request serializes empty object`() {
        assertEquals("{}", AiosJson.encodeToString(SubmitRequest()))
    }

    // ---------- 报告 ----------

    @Test
    fun `parses report with nullable grading fields`() {
        val dto = AiosJson.decodeFromString<ReportResponse>(
            """
            {
              "exam_id":"exam_abc","paper_title":"函数与导数基础","mode":"exam",
              "score":100,"score_earned":60.5,"score_max":100.0,
              "correct_count":1,"total_count":2,"reviewed_count":1,
              "items":[
                {"question_id":"q1","sequence":1,"question_type":"mcq","stem":"题干一",
                 "given":"B","expected":"B","correct":true,"score":50.0,"max_score":50.0,
                 "explanation":"解析","knowledge":["导数"],"evidence_ids":["ev-1"],"score_ratio":1.0},
                {"question_id":"q2","sequence":2,"question_type":"essay","stem":"题干二",
                 "given":"作答","expected":"","correct":null,"score":null,"max_score":50.0,
                 "explanation":"","knowledge":[],"evidence_ids":[],"score_ratio":null}
              ],
              "concepts":[
                {"concept":"导数","correct":1,"total":1,"reviewed":0,"ratio":1.0},
                {"concept":"写作","correct":0,"total":0,"reviewed":1,"ratio":null}
              ],
              "mistakes":[
                {"question_id":"q9","stem":"错题","given":"A","expected":"B","explanation":"解析",
                 "diagnosis":"诊断","knowledge":["k"],"evidence_ids":["ev-2"],"remediation_task_ids":[1,2]}
              ],
              "remediation_tasks":[
                {"kind":"review_concept","title":"复习概念","detail":"细节","question_id":"q9"}
              ],
              "evidence_ids":["ev-1","ev-2"]
            }
            """.trimIndent(),
        )
        val domain = dto.toDomain()
        assertEquals(60.5, domain.scoreEarned, 0.0)
        assertEquals(1, domain.reviewedCount)
        assertNull(domain.items[1].correct)
        assertNull(domain.items[1].score)
        assertNull(domain.concepts[1].ratio)
        assertEquals(listOf(1, 2), domain.mistakes[0].remediationTaskIds)
        assertEquals("review_concept", domain.remediationTasks[0].kind)
    }

    // ---------- 提交结果 ----------

    @Test
    fun `parses submission with graded items and review questions`() {
        val dto = AiosJson.decodeFromString<SubmissionResponse>(
            """
            {
              "exam_id":"exam_abc","paper_id":"functions-basics","paper_title":"卷",
              "mode":"exam","status":"submitted","score":100,"correct_count":1,"total_count":2,
              "duration_seconds":240,"rule_version":"v1",
              "items":[
                {"question_id":"q1","given":"B","correct":true,"expected":"B","explanation":"解析",
                 "angles":{"concept":"c","method":"m","mistake":"x","variant":"v"},"rubric":null},
                {"question_id":"q2","given":"","correct":null,"expected":"标准答案","explanation":"",
                 "angles":{"concept":"c","method":"m","mistake":"x","variant":"v"},
                 "rubric":{"rule_version":"v2","criteria":[{"point":"要点","achieved":true,"evidence_id":"ev-1"}],
                           "score_ratio":0.8,"confidence":0.9,"judge_model":"fixture-judge","prompt_hash":""}}
              ],
              "questions":[
                {"id":"q1","type":"mcq","stem":"题干","options":[{"key":"A","text":"甲"}],
                 "answer":"B","explanation":"解析",
                 "angles":{"concept":"c","method":"m","mistake":"x","variant":"v"},"knowledge":["导数"]}
              ]
            }
            """.trimIndent(),
        )
        val domain = dto.toDomain()
        assertEquals("submitted", domain.status)
        assertEquals(240, domain.durationSeconds)
        assertTrue(domain.items[0].correct == true)
        assertNull(domain.items[1].correct)
        assertEquals(1, domain.questions.size)
        assertEquals("B", domain.questions[0].answer)
    }

    // ---------- 服务端演进容忍 ----------

    @Test
    fun `ignores unknown fields in exam session`() {
        val dto = AiosJson.decodeFromString<ExamSessionResponse>(
            """{"exam_id":"e","paper_id":"p","paper_title":"t","mode":"exam","status":"active",
               "server_started_at":"","server_end_at":"","server_remaining_seconds":1,
               "future_field":{"nested":1},"questions":[],"answers":{},"next_sequence":1}""",
        )
        assertEquals("e", dto.examId)
    }
}
