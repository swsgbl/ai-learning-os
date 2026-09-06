package com.ailearningos.app.data.remote

import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.PUT
import retrofit2.http.Path

/** M12-01 壳与认证 + M12-02 考试业务端点面 */
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
}
