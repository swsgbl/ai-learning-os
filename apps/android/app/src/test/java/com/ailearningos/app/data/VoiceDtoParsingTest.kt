package com.ailearningos.app.data

import com.ailearningos.app.data.remote.AiosJson
import com.ailearningos.app.data.remote.VoiceAnswerResponse
import com.ailearningos.app.data.remote.VoiceCommandResponse
import com.ailearningos.app.data.remote.VoiceIntentResponse
import com.ailearningos.app.data.remote.VoiceProvidersResponse
import com.ailearningos.app.data.remote.VoiceReportResponse
import com.ailearningos.app.data.remote.VoiceResumeResponse
import com.ailearningos.app.data.remote.VoiceSessionResponse
import com.ailearningos.app.data.remote.VoiceTraceResponse
import com.ailearningos.app.data.remote.VoiceTranscriptionResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * M12-03 语音 DTO 解析：snake_case 与服务端 voice.py 契约一一对应；
 * 未知键容忍（服务端演进）、缺必填字段拒绝（不猜语义）、状态保留原词。
 */
class VoiceDtoParsingTest {

    private val json = AiosJson

    @Test
    fun `session parses all fields and keeps server status word`() {
        val dto = json.decodeFromString<VoiceSessionResponse>(
            """{"session_id":"vs_1","exam_id":"exam_1","status":"READING_QUESTION",
                "question_index":2,"revision":5,"created_at":"2026-09-06T12:00:00+00:00",
                "updated_at":"2026-09-06T12:01:00+00:00","future_field":123}""",
        )
        assertEquals("vs_1", dto.sessionId)
        assertEquals("exam_1", dto.examId)
        assertEquals("READING_QUESTION", dto.status)
        assertEquals(2, dto.questionIndex)
        assertEquals(5, dto.revision)
    }

    @Test
    fun `session missing required field is rejected`() {
        assertTrue(
            runCatching {
                json.decodeFromString<VoiceSessionResponse>("""{"session_id":"vs_1","exam_id":"e"}""")
            }.isFailure,
        )
    }

    @Test
    fun `command response parses optional clarify and total`() {
        val clarified = json.decodeFromString<VoiceCommandResponse>(
            """{"applied_event":"answer_clarify","from_status":"WAITING_ANSWER",
                "session":{"session_id":"vs_1","exam_id":"e","status":"CLARIFYING",
                "question_index":0,"revision":2,"created_at":"t","updated_at":"t"},
                "clarified_question":"没有听清，请再说一遍具体选项。"}""",
        )
        assertEquals("answer_clarify", clarified.appliedEvent)
        assertEquals("CLARIFYING", clarified.session.status)
        assertEquals("没有听清，请再说一遍具体选项。", clarified.clarifiedQuestion)
        assertNull(clarified.questionTotal)

        val finished = json.decodeFromString<VoiceCommandResponse>(
            """{"applied_event":"report_ready","from_status":"NEXT_QUESTION",
                "session":{"session_id":"vs_1","exam_id":"e","status":"REPORT_READY",
                "question_index":4,"revision":9,"created_at":"t","updated_at":"t"},
                "question_total":5}""",
        )
        assertEquals(5, finished.questionTotal)
        assertNull(finished.clarifiedQuestion)
    }

    @Test
    fun `resume parses question projection and committed answer`() {
        val dto = json.decodeFromString<VoiceResumeResponse>(
            """{"session":{"session_id":"vs_1","exam_id":"e","status":"ANSWER_COMMITTED",
                "question_index":0,"revision":3,"created_at":"t","updated_at":"t"},
                "question":{"id":"q1","type":"mcq","stem":"题干",
                "options":[{"key":"A","text":"甲"},{"key":"B","text":"乙"}]},
                "committed_answer":"B","question_total":5}""",
        )
        assertEquals("q1", dto.question?.id)
        assertEquals(listOf("A", "B"), dto.question?.options?.map { it.key })
        assertEquals("B", dto.committedAnswer)
        assertEquals(5, dto.questionTotal)
    }

    @Test
    fun `resume last question index out of range yields null question`() {
        val dto = json.decodeFromString<VoiceResumeResponse>(
            """{"session":{"session_id":"vs_1","exam_id":"e","status":"NEXT_QUESTION",
                "question_index":5,"revision":3,"created_at":"t","updated_at":"t"},
                "question":null,"committed_answer":null,"question_total":5}""",
        )
        assertNull(dto.question)
        assertNull(dto.committedAnswer)
    }

    @Test
    fun `intent response parses applied and unapplied shapes`() {
        val applied = json.decodeFromString<VoiceIntentResponse>(
            """{"transcript":"选 B","intent":"choose","letter":"B","ordinal":null,
                "ambiguous":false,"fsm_command":"answer_proposed","fsm_applied":true,
                "applied_event":"answer_proposed",
                "session":{"session_id":"vs_1","exam_id":"e","status":"ANSWER_COMMITTED",
                "question_index":0,"revision":2,"created_at":"t","updated_at":"t"}}""",
        )
        assertTrue(applied.fsmApplied)
        assertEquals("B", applied.letter)
        assertEquals("ANSWER_COMMITTED", applied.session?.status)

        val unknown = json.decodeFromString<VoiceIntentResponse>(
            """{"transcript":"今天天气不错","intent":"unknown","letter":null,"ordinal":null,
                "ambiguous":false,"fsm_command":null,"fsm_applied":false}""",
        )
        assertFalse(unknown.fsmApplied)
        assertNull(unknown.session)
    }

    @Test
    fun `answer response parses idempotent and accepted flags`() {
        val dto = json.decodeFromString<VoiceAnswerResponse>(
            """{"event_id":"evt-uuid-1","idempotent":true,"accepted":true,
                "normalized_answer":"B","intent":"choose","question_id":"q1",
                "session":{"session_id":"vs_1","exam_id":"e","status":"ANSWER_COMMITTED",
                "question_index":0,"revision":2,"created_at":"t","updated_at":"t"}}""",
        )
        assertTrue(dto.idempotent)
        assertTrue(dto.accepted)
        assertEquals("B", dto.normalizedAnswer)
        assertEquals("evt-uuid-1", dto.eventId)
    }

    @Test
    fun `report parses spoken text mistake and remediation summaries`() {
        val dto = json.decodeFromString<VoiceReportResponse>(
            """{"session_id":"vs_1","exam_id":"e",
                "spoken_text":"考试完成！本次答对 3 题（共 5 题），得分 60 分，共有 2 道错题。",
                "mistake_summary":[{"question_id":"q1","stem_preview":"题干…","your_answer":"A",
                "correct_answer":"B","concepts":["函数"],"diagnosis":"概念不清"}],
                "remediation_summary":[{"concept":"函数","actions":["review_concept"],
                "detail":"复习概念后完成变式练习","question_ids":["q1","q2"]}],
                "written_report_url":"/api/v1/exams/e/report"}""",
        )
        assertEquals(1, dto.mistakeSummary.size)
        assertEquals("题干…", dto.mistakeSummary[0].stemPreview)
        assertEquals(listOf("函数"), dto.mistakeSummary[0].concepts)
        assertEquals(listOf("q1", "q2"), dto.remediationSummary[0].questionIds)
        assertEquals("/api/v1/exams/e/report", dto.writtenReportUrl)
    }

    @Test
    fun `transcription parses provider and privacy fields`() {
        val dto = json.decodeFromString<VoiceTranscriptionResponse>(
            """{"id":7,"text":"[fake] 选 B","confidence":0.9,"provider":"fake",
                "latency_ms":12,"audio_bytes":64000,"audio_stored":false,
                "audio_object_key":null}""",
        )
        assertEquals("[fake] 选 B", dto.text)
        assertEquals("fake", dto.provider)
        assertFalse(dto.audioStored)
        assertNull(dto.audioObjectKey)
    }

    @Test
    fun `providers parses mode asr tts and privacy flags`() {
        val dto = json.decodeFromString<VoiceProvidersResponse>(
            """{"voice_mode":"hybrid",
                "asr":{"requested":null,"provider":"fake","fallback":false},
                "tts":{"requested":"cloud-openai-tts","provider":"tone","fallback":true},
                "privacy_store_audio":false,"privacy_send_context_to_cloud":true}""",
        )
        assertEquals("hybrid", dto.voiceMode)
        assertFalse(dto.asr.fallback)
        assertTrue(dto.tts.fallback)
        assertTrue(dto.privacySendContextToCloud)
    }

    @Test
    fun `trace response parses stage and source`() {
        val dto = json.decodeFromString<VoiceTraceResponse>(
            """{"id":9,"stage":"vad","duration_ms":80,"source":"client",
                "session_id":"vs_1","exam_id":"e","question_id":null,
                "created_at":"2026-09-06T12:00:00+00:00"}""",
        )
        assertEquals("vad", dto.stage)
        assertEquals("client", dto.source)
        assertNull(dto.questionId)
    }

    @Test
    fun `sessions list parses empty and multiple items`() {
        val empty = json.decodeFromString<com.ailearningos.app.data.remote.VoiceSessionsResponse>("""{"items":[]}""")
        assertTrue(empty.items.isEmpty())
        val two = json.decodeFromString<List<VoiceSessionResponse>>(
            """[{"session_id":"a","exam_id":"e","status":"SESSION_READY","question_index":0,
                "revision":1,"created_at":"t","updated_at":"t"},
               {"session_id":"b","exam_id":"e","status":"READING_QUESTION","question_index":0,
                "revision":2,"created_at":"t","updated_at":"t"}]""",
        )
        assertEquals(listOf("a", "b"), two.map { it.sessionId })
    }
}
