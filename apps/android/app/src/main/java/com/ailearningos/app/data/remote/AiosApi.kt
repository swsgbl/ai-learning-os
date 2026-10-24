package com.ailearningos.app.data.remote

import okhttp3.MultipartBody
import okhttp3.ResponseBody
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.Multipart
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Part
import retrofit2.http.Path
import retrofit2.http.Query

/** M12-01 壳与认证 + M12-02 考试业务 + M12-03 语音业务 + M12-04 搜索业务 + M12-05 治理业务端点面 */
interface AiosApi {

    @GET("health")
    suspend fun health(): HealthResponse

    @GET("api/v1/auth/status")
    suspend fun authStatus(): AuthStatusResponse

    @POST("api/v1/auth/register")
    suspend fun register(@Body body: RegisterRequest): UserResponse

    @POST("api/v1/auth/login")
    suspend fun login(@Body body: LoginRequest): TokenResponse

    @GET("api/v1/auth/me")
    suspend fun me(): UserResponse

    @GET("api/v1/system/privacy")
    suspend fun privacy(): PrivacyResponse

    @GET("api/v1/meta/latest-app")
    suspend fun latestApp(@Query("platform") platform: String = "android"): LatestAppResponse
    @GET("api/v1/me/ai-keys")
    suspend fun myAiKeys(): UserAiKeysResponse

    @PUT("api/v1/me/ai-keys/{slot}")
    suspend fun putMyAiKey(@Path("slot") slot: String, @Body body: UserAiKeyPutRequest): UserAiKeyStatus

    @DELETE("api/v1/me/ai-keys/{slot}")
    suspend fun deleteMyAiKey(@Path("slot") slot: String): UserAiKeyStatus
    @POST("api/v1/me/ai-keys/llm/models")
    suspend fun probeLlmModels(@Body body: LlmModelsProbeRequest): LlmModelsProbeResponse
    @POST("api/v1/me/ai-keys/llm/profiles")
    suspend fun createLlmProfile(@Body body: ProfileCreateRequest): LlmProfile

    @POST("api/v1/me/ai-keys/llm/profiles/{id}/activate")
    suspend fun activateLlmProfile(@Path("id") id: String): LlmProfile

    @DELETE("api/v1/me/ai-keys/llm/profiles/{id}")
    suspend fun deleteLlmProfile(@Path("id") id: String): ProfileDeleteResponse

    @GET("api/v1/papers/{paperId}/study")
    suspend fun paperStudy(@Path("paperId") paperId: String): StudyViewDto

    @GET("api/v1/exams/history")
    suspend fun examHistory(): ExamHistoryOutDto

    @GET("api/v1/student/daily-plan")
    suspend fun dailyPlan(): DailyPlanDto

    @GET("api/v1/student/states")
    suspend fun studentStates(): StudentStatesDto

    @POST("api/v1/courses/generation-drafts")
    suspend fun createGenerationDraft(@Body body: GenerationDraftRequest): GenerationDraftResponse

    @POST("api/v1/courses/generation-drafts/{id}/materialize")
    suspend fun materializeDraft(@Path("id") id: String): MaterializedPaperResponse

    // ---------- M12-02 考试业务 ----------

    @GET("api/v1/papers")
    suspend fun papers(): List<PaperResponse>

    @POST("api/v1/papers/{paper_id}/exams")
    suspend fun startExam(
        @Path("paper_id") paperId: String,
        @Body body: StartExamRequest,
    ): ExamSessionResponse

    @GET("api/v1/exams/{exam_id}")
    suspend fun getExam(@Path("exam_id") examId: String): ExamSessionResponse

    @PUT("api/v1/exams/{exam_id}/answers")
    suspend fun saveAnswer(
        @Path("exam_id") examId: String,
        @Body body: SaveAnswerRequest,
    ): ExamSessionResponse

    @POST("api/v1/exams/{exam_id}/submit")
    suspend fun submitExam(
        @Path("exam_id") examId: String,
        @Body body: SubmitRequest,
    ): SubmissionResponse

    @GET("api/v1/exams/{exam_id}/submission")
    suspend fun submission(@Path("exam_id") examId: String): SubmissionResponse

    @GET("api/v1/exams/{exam_id}/report")
    suspend fun report(@Path("exam_id") examId: String): ReportResponse

    // ---------- M12-03 语音业务 ----------

    @GET("api/v1/voice/providers")
    suspend fun voiceProviders(): VoiceProvidersResponse

    @POST("api/v1/voice/sessions")
    suspend fun createVoiceSession(@Body body: VoiceSessionCreateRequest): VoiceSessionResponse

    @GET("api/v1/voice/sessions")
    suspend fun listVoiceSessions(@Query("exam_id") examId: String): VoiceSessionsResponse

    @GET("api/v1/voice/sessions/{session_id}")
    suspend fun voiceSession(@Path("session_id") sessionId: String): VoiceSessionResponse

    @GET("api/v1/voice/sessions/{session_id}/resume")
    suspend fun voiceResume(@Path("session_id") sessionId: String): VoiceResumeResponse

    @POST("api/v1/voice/sessions/{session_id}/commands")
    suspend fun voiceCommand(
        @Path("session_id") sessionId: String,
        @Body body: VoiceCommandRequest,
    ): VoiceCommandResponse

    @POST("api/v1/voice/sessions/{session_id}/intents")
    suspend fun voiceIntent(
        @Path("session_id") sessionId: String,
        @Body body: VoiceIntentRequest,
    ): VoiceIntentResponse

    @POST("api/v1/voice/sessions/{session_id}/answers")
    suspend fun voiceAnswer(
        @Path("session_id") sessionId: String,
        @Body body: VoiceAnswerRequest,
    ): VoiceAnswerResponse

    @GET("api/v1/voice/sessions/{session_id}/report")
    suspend fun voiceReport(@Path("session_id") sessionId: String): VoiceReportResponse

    @Multipart
    @POST("api/v1/voice/transcribe")
    suspend fun voiceTranscribe(@Part audio: MultipartBody.Part): VoiceTranscriptionResponse

    @POST("api/v1/voice/synthesize")
    suspend fun voiceSynthesize(@Body body: VoiceSynthesizeRequest): ResponseBody

    @POST("api/v1/voice/trace")
    suspend fun voiceTrace(@Body body: VoiceTraceRequest): VoiceTraceResponse

    // ---------- M12-04 搜索业务 ----------

    @GET("api/v1/search/providers")
    suspend fun searchProviders(): SearchProvidersResponse

    @POST("api/v1/search/plan")
    suspend fun searchPlan(@Body body: SearchPlanRequest): SearchPlanResponse

    @POST("api/v1/search/queries")
    suspend fun searchQueries(@Body body: SearchQueryRequest): SearchQueryResponse

    @GET("api/v1/search/queries/{query_id}")
    suspend fun searchRecord(@Path("query_id") queryId: Long): SearchRecordResponse

    // ---------- M12-05 治理业务 ----------

    @GET("api/v1/version")
    suspend fun version(): VersionResponse

    @GET("api/v1/system/ops-snapshot")
    suspend fun opsSnapshot(): OpsSnapshotResponse

    @GET("api/v1/audit")
    suspend fun audit(@Query("limit") limit: Int): List<AuditEntryResponse>

    // ---------- M16-012 学习冲刺业务（Web 学习工作台的 App 面板） ----------

    @GET("api/v1/learning/contracts")
    suspend fun learningContracts(): List<LearningContractDto>

    @POST("api/v1/learning/contracts/{contract_id}/confirm")
    suspend fun confirmLearningContract(@Path("contract_id") contractId: String): LearningContractDto

    @GET("api/v1/learning/contracts/{contract_id}/sprints")
    suspend fun contractSprints(@Path("contract_id") contractId: String): List<LearningSprintDto>

    @POST("api/v1/learning/sprints/{sprint_id}/advance")
    suspend fun advanceSprint(
        @Path("sprint_id") sprintId: String,
        @Body body: AdvanceSprintRequestDto,
    ): LearningSprintDto

    @GET("api/v1/learning/sprints/{sprint_id}/assessment")
    suspend fun sprintAssessment(
        @Path("sprint_id") sprintId: String,
        @Query("phase") phase: String,
    ): AssessmentResponseDto

    // ---------- M20-002 迁移检验 / 定向练习 / 复习作答 ----------

    @GET("api/v1/learning/sprints/{sprint_id}/transfer/items")
    suspend fun transferItems(@Path("sprint_id") sprintId: String): TransferSessionDto

    @POST("api/v1/learning/sprints/{sprint_id}/transfer/submit")
    suspend fun submitTransfer(
        @Path("sprint_id") sprintId: String,
        @Body body: TransferSubmitRequestDto,
    ): TransferResultDto

    @GET("api/v1/learning/sprints/{sprint_id}/practice/session")
    suspend fun practiceSession(@Path("sprint_id") sprintId: String): PracticeStepDto

    @POST("api/v1/learning/sprints/{sprint_id}/practice/answer")
    suspend fun practiceAnswer(
        @Path("sprint_id") sprintId: String,
        @Body body: PracticeAnswerRequestDto,
    ): PracticeAnswerDto

    @GET("api/v1/learning/review-queue")
    suspend fun reviewQueue(): List<ReviewQueueItemDto>

    @GET("api/v1/learning/review-queue/{review_id}/item")
    suspend fun reviewItem(@Path("review_id") reviewId: String): AssessmentItemDto

    @POST("api/v1/learning/review-queue/{review_id}/attempt")
    suspend fun attemptReview(
        @Path("review_id") reviewId: String,
        @Body body: ReviewAttemptRequestDto,
    ): ReviewQueueItemDto

    // ---------- M20-001 教材工作流 ----------

    @GET("api/v1/catalog/search")
    suspend fun catalogSearch(
        @Query("q") query: String,
        @Query("limit") limit: Int = 30,
    ): List<CatalogResourceDto>

    @GET("api/v1/library/imports")
    suspend fun myImports(): List<ImportJobDto>

    @POST("api/v1/library/imports")
    suspend fun createImport(@Body body: ImportRequestDto): ImportJobDto

    @POST("api/v1/library/imports/{job_id}/parse")
    suspend fun parseImport(@Path("job_id") jobId: String): ParseResultDto

    @POST("api/v1/library/imports/{job_id}/parse")
    suspend fun parseImportOcr(
        @Path("job_id") jobId: String,
        @Query("ocr_max_pages") ocrMaxPages: Int,
    ): ParseResultDto

    @POST("api/v1/library/imports/{job_id}/concepts")
    suspend fun extractConcepts(@Path("job_id") jobId: String): ConceptExtractionDto

    @POST("api/v1/library/imports/{job_id}/create-sprint")
    suspend fun createSprintFromTextbook(@Path("job_id") jobId: String): TextbookSprintDto

    @POST("api/v1/learning/sprints/{sprint_id}/assessment/{phase}/submit")
    suspend fun submitSprintAssessment(
        @Path("sprint_id") sprintId: String,
        @Path("phase") phase: String,
        @Body body: AssessmentSubmitRequestDto,
    ): AssessmentResultDto
}
