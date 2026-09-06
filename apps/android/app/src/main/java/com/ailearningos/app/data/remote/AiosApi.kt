package com.ailearningos.app.data.remote

import okhttp3.MultipartBody
import okhttp3.ResponseBody
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.Multipart
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Part
import retrofit2.http.Path
import retrofit2.http.Query

/** M12-01 壳与认证 + M12-02 考试业务 + M12-03 语音业务 + M12-04 搜索业务端点面 */
interface AiosApi {

    @GET("health")
    suspend fun health(): HealthResponse

    @GET("api/v1/auth/status")
    suspend fun authStatus(): AuthStatusResponse

    @POST("api/v1/auth/login")
    suspend fun login(@Body body: LoginRequest): TokenResponse

    @GET("api/v1/auth/me")
    suspend fun me(): UserResponse

    @GET("api/v1/system/privacy")
    suspend fun privacy(): PrivacyResponse

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
}
