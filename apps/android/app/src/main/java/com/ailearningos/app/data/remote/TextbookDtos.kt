package com.ailearningos.app.data.remote

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * M20-001 教材工作流 DTO：目录搜索 / 我的书架 / 一键开冲刺。
 *
 * snake_case 与服务端契约一一对应（catalog.py / library.py）；
 * ignoreUnknownKeys 容忍服务端演进。
 */

// ---------- 目录搜索（M18-001） ----------

@Serializable
data class CatalogResourceDto(
    val id: String,
    val provider: String = "",
    @SerialName("source_path") val sourcePath: String = "",
    @SerialName("source_url") val sourceUrl: String = "",
    val title: String,
    @SerialName("education_stage") val educationStage: String = "",
    val subject: String = "",
    val grade: String = "",
    val volume: String = "",
    @SerialName("mime_type") val mimeType: String = "",
    @SerialName("parts_count") val partsCount: Int = 0,
    @SerialName("permission_status") val permissionStatus: String = "unknown",
)

// ---------- 我的书架（M19-004） ----------

@Serializable
data class ImportJobDto(
    val id: String,
    @SerialName("resource_id") val resourceId: String,
    val status: String,
    @SerialName("parts_total") val partsTotal: Int = 0,
    @SerialName("parts_fetched") val partsFetched: Int = 0,
    val sha256: String = "",
    @SerialName("size_bytes") val sizeBytes: Long = 0,
    val error: String = "",
    val title: String = "",
    @SerialName("parse_status") val parseStatus: String = "",
    @SerialName("concept_counts") val conceptCounts: String = "",
)

@Serializable
data class ImportRequestDto(
    @SerialName("resource_id") val resourceId: String,
    @SerialName("permission_statement") val permissionStatement: Boolean,
)

@Serializable
data class ParseResultDto(
    val status: String,
    @SerialName("page_count") val pageCount: Int = 0,
    @SerialName("chunk_count") val chunkCount: Int = 0,
    @SerialName("low_text_pages") val lowTextPages: Int = 0,
    @SerialName("ocr_pages_done") val ocrPagesDone: Int = 0,
    @SerialName("ocr_mean_confidence") val ocrMeanConfidence: Double = 0.0,
)

@Serializable
data class ConceptExtractionDto(
    @SerialName("concept_count") val conceptCount: Int,
    @SerialName("edge_count") val edgeCount: Int = 0,
    val dropped: Int = 0,
)

// ---------- 一键开冲刺（M19-003） ----------

@Serializable
data class TextbookSprintDto(
    @SerialName("contract_id") val contractId: String,
    @SerialName("sprint_id") val sprintId: String,
    @SerialName("concept_ids") val conceptIds: List<String> = emptyList(),
    val generated: Int = 0,
    val published: Int = 0,
    @SerialName("blueprint_complete") val blueprintComplete: Boolean = false,
    @SerialName("sprint_phase") val sprintPhase: String = "",
)
