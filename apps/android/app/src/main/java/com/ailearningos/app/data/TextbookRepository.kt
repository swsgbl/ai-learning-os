package com.ailearningos.app.data

import com.ailearningos.app.data.remote.CatalogResourceDto
import com.ailearningos.app.data.remote.ConceptExtractionDto
import com.ailearningos.app.data.remote.ImportJobDto
import com.ailearningos.app.data.remote.ImportRequestDto
import com.ailearningos.app.data.remote.ParseResultDto
import com.ailearningos.app.data.remote.TextbookSprintDto

/**
 * M20-001 教材工作流业务面（目录/书架/一键冲刺）。UI/ViewModel 依赖此接口。
 * 服务端语义保持：导入需用户声明使用权（permission_statement 恒 true——
 * UI 在导入前显式确认）；失败如实抛（AppError 经 RemoteErrors 转换）。
 */
interface TextbookGateway {
    suspend fun catalogSearch(query: String): List<CatalogResourceDto>
    suspend fun myImports(): List<ImportJobDto>
    suspend fun createImport(resourceId: String): ImportJobDto
    suspend fun parseImport(jobId: String, ocrMaxPages: Int? = null): ParseResultDto
    suspend fun extractConcepts(jobId: String): ConceptExtractionDto
    suspend fun createSprintFromTextbook(jobId: String): TextbookSprintDto
}

class TextbookRepository(
    private val apiProvider: ApiProvider,
) : TextbookGateway {
    override suspend fun catalogSearch(query: String): List<CatalogResourceDto> = withRemoteError {
        apiProvider.get().catalogSearch(query)
    }

    override suspend fun myImports(): List<ImportJobDto> = withRemoteError {
        apiProvider.get().myImports()
    }

    override suspend fun createImport(resourceId: String): ImportJobDto = withRemoteError {
        apiProvider.get().createImport(ImportRequestDto(resourceId, permissionStatement = true))
    }

    override suspend fun parseImport(jobId: String, ocrMaxPages: Int?): ParseResultDto =
        withRemoteError {
            if (ocrMaxPages != null) {
                apiProvider.get().parseImportOcr(jobId, ocrMaxPages)
            } else {
                apiProvider.get().parseImport(jobId)
            }
        }

    override suspend fun extractConcepts(jobId: String): ConceptExtractionDto = withRemoteError {
        apiProvider.get().extractConcepts(jobId)
    }

    override suspend fun createSprintFromTextbook(jobId: String): TextbookSprintDto =
        withRemoteError {
            apiProvider.get().createSprintFromTextbook(jobId)
        }
}
