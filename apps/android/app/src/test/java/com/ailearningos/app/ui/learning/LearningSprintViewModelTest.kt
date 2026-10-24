package com.ailearningos.app.ui.learning

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.LearningGateway
import com.ailearningos.app.data.remote.AssessmentItemDto
import com.ailearningos.app.data.remote.AssessmentOptionDto
import com.ailearningos.app.data.remote.AssessmentResponseDto
import com.ailearningos.app.data.remote.AssessmentResultDto
import com.ailearningos.app.data.remote.AssessmentSubmitItemDto
import com.ailearningos.app.data.remote.LearningContractDto
import com.ailearningos.app.data.remote.LearningSprintDto
import com.ailearningos.app.data.remote.TargetOutcomeDto
import com.ailearningos.app.testutil.MainDispatcherRule
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * M16-012 学习冲刺面板守卫测试：
 * - 合同确认推进本地状态；
 * - 阶段链 next 推进（服务端返回为真相，客户端只投影）；
 * - 前测作答（选项+信心）→ 未答全不给提交 → 提交后判分结果与阶段推进
 *   通知来自服务端响应（客户端不持有正确答案）。
 */
class LearningSprintViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private fun contract(confirmed: Boolean = true) = LearningContractDto(
        id = "lc_1",
        title = "SQL 冲刺",
        targetOutcomes = listOf(TargetOutcomeDto(statement = "能写聚合查询")),
        timeBudgetHours = 48.0,
        passCriteria = "等值后测 ≥ 80 分",
        confirmedAt = if (confirmed) "2026-10-22T00:00:00Z" else null,
    )

    private fun sprint(phase: String) = LearningSprintDto(
        id = "ls_1",
        contractId = "lc_1",
        phase = phase,
    )

    private fun assessment() = AssessmentResponseDto(
        sprintId = "ls_1",
        phase = "baseline",
        items = listOf(
            AssessmentItemDto(
                itemId = "q1",
                conceptId = "sql:group-by",
                questionType = "mcq",
                stem = "题干",
                options = listOf(
                    AssessmentOptionDto("A", "甲"),
                    AssessmentOptionDto("B", "乙"),
                ),
            ),
        ),
    )

    private class FakeLearningGateway(
        var contractEntity: LearningContractDto = LearningContractDto(id = "lc_1", title = "SQL 冲刺"),
        var sprintEntity: LearningSprintDto? = null,
        var assessmentEntity: AssessmentResponseDto? = null,
        var resultEntity: AssessmentResultDto? = null,
        var advanceError: AppError? = null,
    ) : LearningGateway {
        var advancedTo: String? = null
        var submitted: List<AssessmentSubmitItemDto>? = null

        override suspend fun contracts(): List<LearningContractDto> = listOf(contractEntity)
        override suspend fun confirmContract(contractId: String): LearningContractDto {
            // Fake 状态回写（真实服务端 confirm 后 contracts 即含确认态）
            contractEntity = contractEntity.copy(confirmedAt = "2026-10-22T01:00:00Z")
            return contractEntity
        }

        override suspend fun contractSprints(contractId: String): List<LearningSprintDto> =
            sprintEntity?.let { listOf(it) } ?: emptyList()

        override suspend fun advanceSprint(sprintId: String, toPhase: String): LearningSprintDto {
            advanceError?.let { throw it }
            advancedTo = toPhase
            return sprintEntity!!.copy(phase = toPhase)
        }

        override suspend fun assessment(sprintId: String, phase: String): AssessmentResponseDto =
            assessmentEntity ?: throw AppError(AppErrorKind.NOT_FOUND)

        override suspend fun transferItems(sprintId: String): com.ailearningos.app.data.remote.TransferSessionDto =
            com.ailearningos.app.data.remote.TransferSessionDto(sprintId, "transfer", emptyList())

        override suspend fun submitTransfer(
            sprintId: String,
            responses: List<com.ailearningos.app.data.remote.TransferSubmitDto>,
        ): com.ailearningos.app.data.remote.TransferResultDto =
            com.ailearningos.app.data.remote.TransferResultDto(sprintId, "transfer", 100.0, 1, 1)

        override suspend fun practiceSession(sprintId: String): com.ailearningos.app.data.remote.PracticeStepDto =
            throw com.ailearningos.app.core.AppError(com.ailearningos.app.core.AppErrorKind.NOT_FOUND)

        override suspend fun practiceAnswer(
            sprintId: String,
            itemId: String,
            response: String,
            confidence: Int?,
        ): com.ailearningos.app.data.remote.PracticeAnswerDto =
            com.ailearningos.app.data.remote.PracticeAnswerDto(conceptId = "c")

        override suspend fun reviewQueue(): List<com.ailearningos.app.data.remote.ReviewQueueItemDto> = emptyList()

        override suspend fun reviewItem(reviewId: String): com.ailearningos.app.data.remote.AssessmentItemDto =
            throw com.ailearningos.app.core.AppError(com.ailearningos.app.core.AppErrorKind.NOT_FOUND)

        override suspend fun attemptReview(
            reviewId: String,
            itemId: String,
            response: String,
            confidence: Int?,
        ): com.ailearningos.app.data.remote.ReviewQueueItemDto =
            throw com.ailearningos.app.core.AppError(com.ailearningos.app.core.AppErrorKind.NOT_FOUND)

        override suspend fun submitAssessment(
            sprintId: String,
            phase: String,
            responses: List<AssessmentSubmitItemDto>,
        ): AssessmentResultDto {
            submitted = responses
            return resultEntity ?: throw AppError(AppErrorKind.UNEXPECTED)
        }
    }

    @Test
    fun `refresh loads contracts with latest sprint`() = runTest {
        val gateway = FakeLearningGateway(
            contractEntity = contract(confirmed = true),
            sprintEntity = sprint("concept_map_review"),
        )
        val viewModel = LearningSprintViewModel(gateway)
        advanceUntilIdle()
        assertFalse(viewModel.state.value.loading)
        assertEquals(1, viewModel.state.value.contracts.size)
        assertEquals("concept_map_review", viewModel.state.value.contracts.first().sprint?.phase)
    }

    @Test
    fun `unconfirmed contract offers confirm and refreshes`() = runTest {
        val gateway = FakeLearningGateway(contractEntity = contract(confirmed = false))
        val viewModel = LearningSprintViewModel(gateway)
        advanceUntilIdle()
        assertNull(viewModel.state.value.contracts.first().contract.confirmedAt)
        viewModel.confirmContract("lc_1")
        advanceUntilIdle()
        assertNotNull(viewModel.state.value.contracts.first().contract.confirmedAt)
    }

    @Test
    fun `advance sends next phase from chain`() = runTest {
        val gateway = FakeLearningGateway(
            contractEntity = contract(),
            sprintEntity = sprint("baseline_assessment"),
        )
        val viewModel = LearningSprintViewModel(gateway)
        advanceUntilIdle()
        viewModel.advance("ls_1")
        advanceUntilIdle()
        assertEquals("concept_map_review", gateway.advancedTo)
    }

    @Test
    fun `advance at last phase reports error without request`() = runTest {
        val gateway = FakeLearningGateway(
            contractEntity = contract(),
            sprintEntity = sprint("follow_up"),
        )
        val viewModel = LearningSprintViewModel(gateway)
        advanceUntilIdle()
        viewModel.advance("ls_1")
        advanceUntilIdle()
        assertNull(gateway.advancedTo)
        assertEquals("已在最后阶段", viewModel.state.value.error)
    }

    @Test
    fun `assessment session collects answers and blocks until complete`() = runTest {
        val gateway = FakeLearningGateway(
            contractEntity = contract(),
            sprintEntity = sprint("baseline_assessment"),
            assessmentEntity = assessment(),
        )
        val viewModel = LearningSprintViewModel(gateway)
        advanceUntilIdle()
        viewModel.startAssessment("ls_1")
        advanceUntilIdle()
        val session = viewModel.state.value.session
        assertNotNull(session)
        assertFalse(session!!.allAnswered)
        viewModel.selectAnswer("q1", "B")
        viewModel.setConfidence("q1", 4)
        assertTrue(viewModel.state.value.session!!.allAnswered)
    }

    @Test
    fun `submit posts responses and shows server graded score`() = runTest {
        val gateway = FakeLearningGateway(
            contractEntity = contract(),
            sprintEntity = sprint("baseline_assessment"),
            assessmentEntity = assessment(),
            resultEntity = AssessmentResultDto(
                sprintId = "ls_1",
                phase = "baseline",
                score = 100.0,
                correctCount = 1,
                questionCount = 1,
                ungradedCount = 0,
                sprintPhase = "concept_map_review",
            ),
        )
        val viewModel = LearningSprintViewModel(gateway)
        advanceUntilIdle()
        viewModel.startAssessment("ls_1")
        advanceUntilIdle()
        viewModel.selectAnswer("q1", "B")
        viewModel.setConfidence("q1", 4)
        viewModel.submitAssessment()
        advanceUntilIdle()
        val submitted = gateway.submitted!!.single()
        assertEquals("q1", submitted.itemId)
        assertEquals("B", submitted.response)
        assertEquals(4, submitted.confidence)
        assertNull(viewModel.state.value.session)
        assertEquals(100.0, viewModel.state.value.lastResult!!.score, 0.01)
    }

    @Test
    fun `submit before all answered keeps session and shows error`() = runTest {
        val gateway = FakeLearningGateway(
            contractEntity = contract(),
            sprintEntity = sprint("baseline_assessment"),
            assessmentEntity = assessment(),
        )
        val viewModel = LearningSprintViewModel(gateway)
        advanceUntilIdle()
        viewModel.startAssessment("ls_1")
        advanceUntilIdle()
        viewModel.submitAssessment()
        advanceUntilIdle()
        assertNull(gateway.submitted)
        assertNotNull(viewModel.state.value.session)
        assertEquals("请作答全部题目", viewModel.state.value.error)
    }

    @Test
    fun `server 409 on advance surfaces sanitized message`() = runTest {
        val gateway = FakeLearningGateway(
            contractEntity = contract(),
            sprintEntity = sprint("baseline_assessment"),
            advanceError = AppError(AppErrorKind.CONFLICT),
        )
        val viewModel = LearningSprintViewModel(gateway)
        advanceUntilIdle()
        viewModel.advance("ls_1")
        advanceUntilIdle()
        assertNotNull(viewModel.state.value.error)
    }
}
