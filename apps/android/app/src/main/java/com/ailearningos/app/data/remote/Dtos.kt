package com.ailearningos.app.data.remote

import com.ailearningos.app.data.model.PrivacySnapshot
import com.ailearningos.app.data.model.UserProfile
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * M12-01 第一切片 DTO：GET /health、auth status/login/me、system/privacy。
 * snake_case 与服务端 Pydantic 契约一一对应（docs/DEVELOPMENT.md）。
 */
@Serializable
data class HealthResponse(
    val status: String,
    val service: String? = null,
)

@Serializable
data class AuthStatusResponse(
    @SerialName("auth_enabled") val authEnabled: Boolean,
)

@Serializable
data class RegisterRequest(
    val username: String,
    val password: String,
)

@Serializable
data class LoginRequest(
    val username: String,
    val password: String,
)

@Serializable
data class TokenResponse(
    @SerialName("access_token") val accessToken: String,
    @SerialName("token_type") val tokenType: String = "bearer",
)

@Serializable
data class UserResponse(
    val id: String,
    val username: String,
    val role: String,
    @SerialName("created_at") val createdAt: String? = null,
)

@Serializable
data class PrivacyResponse(
    @SerialName("model_route") val modelRoute: String,
    @SerialName("voice_mode") val voiceMode: String,
    @SerialName("search_mode") val searchMode: String,
    @SerialName("store_audio") val storesAudio: Boolean,
    @SerialName("send_context_to_cloud") val sendsContextToCloud: Boolean,
)

fun UserResponse.toDomain() = UserProfile(id = id, username = username, role = role)

fun PrivacyResponse.toDomain() = PrivacySnapshot(
    modelRoute = modelRoute,
    voiceMode = voiceMode,
    searchMode = searchMode,
    storesAudio = storesAudio,
    sendsContextToCloud = sendsContextToCloud,
)


/**
 * M15-006 /api/v1/meta/latest-app 响应（服务端三项齐全才透出；404=未配置）。
 * M15-011 修复：服务端线上的字段名是 snake_case（version_code/download_url），
 * 本 DTO 曾误写 camelCase 无 @SerialName——「检查更新」全系客户端报
 * 「API 返回数据格式异常」（真机 v0.1.3 复现）。服务端已加 camelCase
 * 兼容通道救已发布包；本 DTO 对齐契约口径 snake_case。
 */
@Serializable
data class LatestAppResponse(
    val platform: String,
    val version: String,
    @SerialName("version_code") val versionCode: Int,
    @SerialName("download_url") val downloadUrl: String,
    /** M15-012 多通道回退（服务端可空）；旧字段名兼容由服务端双命名承担 */
    @SerialName("download_url_fallback") val downloadUrlFallback: String? = null,
)


/** M15-014 /api/v1/me/ai-keys（个人 BYOK 密钥状态；明文永不回显） */
@Serializable
data class UserAiKeyStatus(
    val slot: String,
    val configured: Boolean,
    val last4: String? = null,
    val updatedAt: String? = null,
    val effect: String,
    /** M15-015 个人 LLM 可选路由（空 = 沿用服务端网关配置） */
    val endpoint: String? = null,
    val model: String? = null,
)

@Serializable
data class UserAiKeysResponse(
    val keys: List<UserAiKeyStatus>,
    val profiles: List<LlmProfile> = emptyList(),
)

@Serializable
data class UserAiKeyPutRequest(
    val api_key: String,
    val endpoint: String? = null,
    val model: String? = null,
)


/** M15-016 模型列表探测（服务端代理 /me/ai-keys/llm/models） */
@Serializable
data class LlmModelsProbeRequest(
    val api_key: String? = null,
    val endpoint: String? = null,
)

@Serializable
data class LlmModelsProbeResponse(val models: List<String>)


/** M15-017 课程生成（/courses/generation-drafts）——plan 保留原始 JSON，渲染时取 outline */
@Serializable
data class GenerationDraftRequest(val goal: String)

@Serializable
data class GenerationDraftResponse(
    val id: String,
    val goal: String,
    val status: String,
    val chapter_count: Int,
    val generation_note: String,
    val plan: JsonObject,
)

/** materialize 产物：私有练习卷——字段名对齐服务端真实 payload（paper_id） */
@Serializable
data class MaterializedPaperResponse(
    @SerialName("paper_id") val paperId: String,
    val title: String,
    val question_count: Int,
)


/** M15-018 多厂商 LLM 密钥库 */
@Serializable
data class LlmProfile(
    val id: String,
    val label: String,
    val last4: String,
    val endpoint: String? = null,
    val model: String? = null,
    val active: Boolean = false,
)

@Serializable
data class ProfileCreateRequest(
    val label: String,
    val api_key: String,
    val endpoint: String? = null,
    val model: String? = null,
    val activate: Boolean = false,
)

@Serializable
data class ProfileDeleteResponse(val deleted: String)


/** M15-022 学生工作台（/student/daily-plan + /student/states） */
@Serializable
data class PlanTaskDto(
    val kind: String,
    val question_id: String? = null,
    val concept_ids: List<String> = emptyList(),
    val title: String,
    val reason: String? = null,
    // M15-022 服务端 Python float 序列化为 2.0——Int 会解析失败（真实 payload 实证）
    val priority: Double = 0.0,
)

@Serializable
data class DailyPlanDto(
    val generated_at: String,
    val plan_date: String,
    val task_count: Int,
    val review_count: Int,
    val mistake_retry_count: Int,
    val new_learning_count: Int,
    val tasks: List<PlanTaskDto>,
)

@Serializable
data class ConceptStateDto(
    val concept_id: String,
    val mastery: Double,
    val confidence: Double,
    val forgetting_risk: Double,
    val evidence_count: Int,
    val correct_count: Int,
    val wrong_count: Int,
)

@Serializable
data class StudentStatesDto(
    val concept_count: Int,
    val states: List<ConceptStateDto> = emptyList(),
)


/** M15-024 学习视图（/papers/{id}/study）——先学习再考试 */
@Serializable
data class StudyQuestionDto(
    val question_id: String,
    val stem: String,
    val question_type: String,
    val options: Map<String, String>? = null,
)

@Serializable
data class StudyChapterDto(
    val chapter_no: Int,
    val title: String,
    val difficulty: Int = 3,
    val objectives: String = "",
    val assessment_stem: String = "",
    val questions: List<StudyQuestionDto> = emptyList(),
)

@Serializable
data class StudyViewDto(
    val paper_id: String,
    val paper_title: String,
    val goal: String = "",
    val chapter_count: Int,
    val chapters: List<StudyChapterDto> = emptyList(),
)
