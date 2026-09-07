package com.ailearningos.app.data.remote

import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.SerializationException
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonEncoder
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive

/**
 * M12-05 治理域 DTO：版本 / 运维快照 / 审计。
 * snake_case 与服务端 Pydantic 契约一一对应；审计响应流里包含 before/after 载荷，
 * 但客户端 DTO 不建模、不保留、不渲染它们；服务端演进新增字段时靠 ignoreUnknownKeys 容忍。
 */

// ---------- 版本（GET /api/v1/version） ----------

@Serializable
data class VersionResponse(
    val version: String,
    @SerialName("git_commit") val gitCommit: String,
    @SerialName("alembic_current") val alembicCurrent: String,
    @SerialName("alembic_head") val alembicHead: String,
)

// ---------- 运维快照（GET /api/v1/system/ops-snapshot） ----------

@Serializable
data class PendingReviewDraftsResponse(
    @SerialName("course_import") val courseImport: Int,
    @SerialName("course_generation") val courseGeneration: Int,
    @SerialName("paper_question") val paperQuestion: Int,
    @SerialName("variant_question") val variantQuestion: Int,
)

@Serializable
data class OpsSnapshotResponse(
    @SerialName("generated_at") val generatedAt: String,
    @SerialName("database_backend") val databaseBackend: String,
    @SerialName("users_by_role") val usersByRole: Map<String, Int>,
    @SerialName("papers_total") val papersTotal: Int,
    @SerialName("resources_by_parse_status") val resourcesByParseStatus: Map<String, Int>,
    @SerialName("parse_jobs_by_status") val parseJobsByStatus: Map<String, Int>,
    @SerialName("pending_review_drafts") val pendingReviewDrafts: PendingReviewDraftsResponse,
    @SerialName("voice_sessions_by_status") val voiceSessionsByStatus: Map<String, Int>,
    @SerialName("search_queries_total") val searchQueriesTotal: Int,
    @SerialName("audit_entries_total") val auditEntriesTotal: Int,
    @SerialName("worker_running") val workerRunning: Boolean,
)

// ---------- 审计（GET /api/v1/audit） ----------

/**
 * 治理审计专用：字段键必填、字段值可为显式 null 的 String 包装。
 * 全局 AiosJson 配置 explicitNulls=false，nullable 属性即使无默认值也会在键缺席时静默兜底为 null，
 * @Required 无法阻止；改用非 nullable 包装类型（无默认值）后，键缺席即 MissingFieldException。
 * 序列化为字符串或 JSON null；descriptor 使用非 nullable String descriptor，避免 nullable descriptor
 * 在 explicitNulls=false 下重新放宽语义。
 */
@Serializable(with = RequiredNullableStringSerializer::class)
data class RequiredNullableString(val value: String?)

object RequiredNullableStringSerializer : KSerializer<RequiredNullableString> {
    // 非 nullable String descriptor：值只允许字符串或 JSON null，由下面 deserialize 显式区分
    override val descriptor: SerialDescriptor = String.serializer().descriptor

    override fun deserialize(decoder: Decoder): RequiredNullableString {
        val input = decoder as? JsonDecoder
            ?: throw SerializationException("RequiredNullableString 仅支持 Json 解码")
        return when (val element = input.decodeJsonElement()) {
            is JsonNull -> RequiredNullableString(null)
            is JsonPrimitive ->
                if (element.isString) {
                    RequiredNullableString(element.content)
                } else {
                    throw SerializationException("RequiredNullableString 期望字符串，实际为 $element")
                }
            else -> throw SerializationException("RequiredNullableString 期望字符串或 null，实际为 $element")
        }
    }

    override fun serialize(encoder: Encoder, value: RequiredNullableString) {
        val output = encoder as? JsonEncoder
            ?: throw SerializationException("RequiredNullableString 仅支持 Json 编码")
        output.encodeJsonElement(value.value?.let { JsonPrimitive(it) } ?: JsonNull)
    }
}

@Serializable
data class AuditEntryResponse(
    val id: Long,
    // 服务端字段必填且可为显式 null（系统内操作无人类 actor）；
    // 非 nullable 包装类型（无默认值）确保键必须显式出现：显式 null 允许，键缺席拒绝
    @SerialName("actor_id") val actorId: RequiredNullableString,
    @SerialName("actor_username") val actorUsername: RequiredNullableString,
    val action: String,
    @SerialName("target_type") val targetType: String,
    @SerialName("target_id") val targetId: String,
    @SerialName("request_id") val requestId: String,
    @SerialName("created_at") val createdAt: String,
)
