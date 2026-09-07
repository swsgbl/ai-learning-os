package com.ailearningos.app.data

import com.ailearningos.app.data.remote.AiosJson
import com.ailearningos.app.data.remote.AuditEntryResponse
import com.ailearningos.app.data.remote.OpsSnapshotResponse
import com.ailearningos.app.data.remote.VersionResponse
import kotlinx.serialization.descriptors.elementNames
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * M12-05 治理 DTO 解析（governance：version / ops snapshot / audit）：
 * snake_case 与服务端 Pydantic 契约一一对应；
 * 未知键容忍（服务端演进）、缺必填字段拒绝；
 * git/alembic 缺席时服务端如实下发 not_available，客户端原词保留不猜语义。
 */
class GovernanceDtoParsingTest {

    private val json = AiosJson

    // ---------- version ----------

    @Test
    fun `version parses fields and tolerates unknown keys`() {
        val dto = json.decodeFromString<VersionResponse>(
            """{"version":"0.12.5","git_commit":"b6475c0f1e2d3a4b5c6",
                "alembic_current":"a1b2c3d4e5f6","alembic_head":"a1b2c3d4e5f6",
                "deploy_env":"prod","future_field":1}""",
        )
        assertEquals("0.12.5", dto.version)
        assertEquals("b6475c0f1e2d3a4b5c6", dto.gitCommit)
        assertEquals("a1b2c3d4e5f6", dto.alembicCurrent)
        assertEquals("a1b2c3d4e5f6", dto.alembicHead)
    }

    @Test
    fun `version keeps not_available verbatim when git or alembic absent`() {
        val dto = json.decodeFromString<VersionResponse>(
            """{"version":"0.12.5","git_commit":"not_available",
                "alembic_current":"not_available","alembic_head":"not_available"}""",
        )
        assertEquals("not_available", dto.gitCommit)
        assertEquals("not_available", dto.alembicCurrent)
        assertEquals("not_available", dto.alembicHead)
    }

    @Test
    fun `version missing git commit is rejected`() {
        assertTrue(
            runCatching {
                json.decodeFromString<VersionResponse>(
                    """{"version":"0.12.5","alembic_current":"a1b2c3d4e5f6","alembic_head":"a1b2c3d4e5f6"}""",
                )
            }.isFailure,
        )
    }

    // ---------- ops snapshot ----------

    @Test
    fun `ops snapshot parses maps pending and bool and tolerates unknown keys`() {
        val dto = json.decodeFromString<OpsSnapshotResponse>(
            """{"generated_at":"2026-09-07T10:00:00Z","database_backend":"postgresql",
                "users_by_role":{"admin":2,"teacher":7,"learner":128},
                "papers_total":34,
                "resources_by_parse_status":{"pending":3,"parsed":40,"failed":1},
                "parse_jobs_by_status":{"queued":1,"running":2,"succeeded":33},
                "pending_review_drafts":{"course_import":1,"course_generation":0,
                    "paper_question":4,"variant_question":2},
                "voice_sessions_by_status":{"completed":90,"failed":3},
                "search_queries_total":456,"audit_entries_total":1024,
                "worker_running":true,"future_field":{"nested":true}}""",
        )
        assertEquals("2026-09-07T10:00:00Z", dto.generatedAt)
        assertEquals("postgresql", dto.databaseBackend)
        assertEquals(mapOf("admin" to 2, "teacher" to 7, "learner" to 128), dto.usersByRole)
        assertEquals(34, dto.papersTotal)
        assertEquals(mapOf("pending" to 3, "parsed" to 40, "failed" to 1), dto.resourcesByParseStatus)
        assertEquals(mapOf("queued" to 1, "running" to 2, "succeeded" to 33), dto.parseJobsByStatus)
        assertEquals(1, dto.pendingReviewDrafts.courseImport)
        assertEquals(0, dto.pendingReviewDrafts.courseGeneration)
        assertEquals(4, dto.pendingReviewDrafts.paperQuestion)
        assertEquals(2, dto.pendingReviewDrafts.variantQuestion)
        assertEquals(mapOf("completed" to 90, "failed" to 3), dto.voiceSessionsByStatus)
        assertEquals(456, dto.searchQueriesTotal)
        assertEquals(1024, dto.auditEntriesTotal)
        assertEquals(true, dto.workerRunning)
    }

    @Test
    fun `ops snapshot keeps empty maps and status keys verbatim`() {
        val dto = json.decodeFromString<OpsSnapshotResponse>(
            """{"generated_at":"2026-09-07T10:00:00Z","database_backend":"postgresql",
                "users_by_role":{},
                "papers_total":0,
                "resources_by_parse_status":{"unknown_status":1},
                "parse_jobs_by_status":{"queued":1,"cancelled":2},
                "pending_review_drafts":{"course_import":0,"course_generation":0,
                    "paper_question":0,"variant_question":0},
                "voice_sessions_by_status":{},
                "search_queries_total":0,"audit_entries_total":0,
                "worker_running":false}""",
        )
        // 服务端契约必发的 map 字段：空 map 也是显式值，原样保留
        assertEquals(emptyMap<String, Int>(), dto.usersByRole)
        assertEquals(emptyMap<String, Int>(), dto.voiceSessionsByStatus)
        // 状态键（含 unknown_status）与计数原样呈现，不猜语义
        assertEquals(mapOf("unknown_status" to 1), dto.resourcesByParseStatus)
        // 服务端演进新增的状态键 cancelled：客户端不过滤键、不重算，原词保留
        assertEquals(mapOf("queued" to 1, "cancelled" to 2), dto.parseJobsByStatus)
        assertEquals(0, dto.papersTotal)
        assertEquals(false, dto.workerRunning)
    }

    @Test
    fun `ops snapshot missing papers total or pending review drafts is rejected`() {
        // 四个 map 字段契约必填（在场、可为空 map），确保被拒绝的原因只剩待测缺失字段
        val base = """"generated_at":"2026-09-07T10:00:00Z","database_backend":"postgresql",
            "users_by_role":{},"resources_by_parse_status":{},
            "parse_jobs_by_status":{},"voice_sessions_by_status":{},
            "search_queries_total":0,"audit_entries_total":0,"worker_running":true"""
        // 缺 papers_total
        assertTrue(
            runCatching {
                json.decodeFromString<OpsSnapshotResponse>(
                    """{$base,"pending_review_drafts":{"course_import":0,"course_generation":0,
                        "paper_question":0,"variant_question":0}}""",
                )
            }.isFailure,
        )
        // papers_total 在场但缺 pending_review_drafts
        assertTrue(
            runCatching {
                json.decodeFromString<OpsSnapshotResponse>("""{$base,"papers_total":1}""")
            }.isFailure,
        )
    }

    @Test
    fun `ops snapshot missing users_by_role is rejected`() {
        // map 字段契约必填：键缺席（而非空 map）必须拒绝，不做静默 emptyMap 兜底
        assertTrue(
            runCatching {
                json.decodeFromString<OpsSnapshotResponse>(
                    """{"generated_at":"2026-09-07T10:00:00Z","database_backend":"postgresql",
                        "papers_total":1,
                        "resources_by_parse_status":{"parsed":1},
                        "parse_jobs_by_status":{},
                        "pending_review_drafts":{"course_import":0,"course_generation":0,
                            "paper_question":0,"variant_question":0},
                        "voice_sessions_by_status":{},
                        "search_queries_total":0,"audit_entries_total":0,
                        "worker_running":true}""",
                )
            }.isFailure,
        )
    }

    // ---------- audit ----------

    @Test
    fun `audit list parses actor null and non-null`() {
        val dtos = json.decodeFromString<List<AuditEntryResponse>>(
            """[
                {"id":1,"actor_id":null,"actor_username":null,
                    "action":"auth.login","target_type":"user","target_id":"u-1",
                    "request_id":"req-001","created_at":"2026-09-07T10:00:00Z"},
                {"id":2,"actor_id":"u-2","actor_username":"admin",
                    "action":"paper.publish","target_type":"paper","target_id":"p-9",
                    "request_id":"req-002","created_at":"2026-09-07T10:05:00Z"},
                {"id":3,"actor_id":null,"actor_username":null,
                    "action":"course.import","target_type":"course","target_id":"c-4",
                    "request_id":"req-003","created_at":"2026-09-07T10:10:00Z"}
            ]""",
        )
        assertEquals(3, dtos.size)
        // 显式 null：系统内操作（无人类 actor）原样保留；包装类型取 .value 还原 String?
        assertEquals(null, dtos[0].actorId.value)
        assertEquals(null, dtos[0].actorUsername.value)
        // 非 null：正常用户操作
        assertEquals("u-2", dtos[1].actorId.value)
        assertEquals("admin", dtos[1].actorUsername.value)
        // 服务端字段必填：无 actor 的条目也必须显式下发 null，而非键缺席
        assertEquals(null, dtos[2].actorId.value)
        assertEquals(null, dtos[2].actorUsername.value)
        assertEquals("paper.publish", dtos[1].action)
        assertEquals("req-003", dtos[2].requestId)
    }

    @Test
    fun `audit entry missing actor id is rejected`() {
        // actor_id/actor_username 契约必填且可为显式 null；键缺席即解析失败，不做默认 null 兜底
        assertTrue(
            runCatching {
                json.decodeFromString<AuditEntryResponse>(
                    """{"id":10,"actor_username":"admin",
                        "action":"paper.delete","target_type":"paper","target_id":"p-1",
                        "request_id":"req-010","created_at":"2026-09-07T11:40:00Z"}""",
                )
            }.isFailure,
        )
        assertTrue(
            runCatching {
                json.decodeFromString<AuditEntryResponse>(
                    """{"id":11,"actor_id":"u-2",
                        "action":"paper.delete","target_type":"paper","target_id":"p-2",
                        "request_id":"req-011","created_at":"2026-09-07T11:45:00Z"}""",
                )
            }.isFailure,
        )
    }

    @OptIn(kotlinx.serialization.ExperimentalSerializationApi::class)
    @Test
    fun `audit entry ignores before and after keys and dto has no such members`() {
        // 服务端审计条目含 before/after 载荷；客户端会收到含载荷的 JSON 响应流，
        // 但 DTO 不建模、不保留、不渲染它们；未知键容忍 + 反射双保险确保契约不回退
        val dto = json.decodeFromString<AuditEntryResponse>(
            """{"id":9,"actor_id":"u-2","actor_username":"admin",
                "action":"user.role_change","target_type":"user","target_id":"u-7",
                "before":{},"after":{},
                "request_id":"req-009","created_at":"2026-09-07T11:00:00Z"}""",
        )
        assertEquals(9, dto.id)
        assertEquals("user.role_change", dto.action)
        assertEquals("u-7", dto.targetId)
        // 反射断言：AuditEntryResponse 没有承载 before/after 的成员
        val fieldNames = AuditEntryResponse::class.java.declaredFields.map { it.name }
        assertFalse("before" in fieldNames)
        assertFalse("after" in fieldNames)
        // 序列化契约层再核一遍：descriptor 元素名里也没有 before/after
        val serialNames = AuditEntryResponse.serializer().descriptor.elementNames.toList()
        assertFalse("before" in serialNames)
        assertFalse("after" in serialNames)
    }

    @Test
    fun `audit entry missing request id is rejected`() {
        assertTrue(
            runCatching {
                json.decodeFromString<AuditEntryResponse>(
                    """{"id":9,"actor_id":"u-2","actor_username":"admin",
                        "action":"paper.delete","target_type":"paper","target_id":"p-1",
                        "created_at":"2026-09-07T11:30:00Z"}""",
                )
            }.isFailure,
        )
    }

    @Test
    fun `audit empty list parses to empty`() {
        val dtos = json.decodeFromString<List<AuditEntryResponse>>("[]")
        assertTrue(dtos.isEmpty())
    }
}
