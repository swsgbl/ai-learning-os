package com.ailearningos.app.ui

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.testutil.FakeExamGateway
import com.ailearningos.app.testutil.MainDispatcherRule
import com.ailearningos.app.testutil.reportFixture
import com.ailearningos.app.testutil.submissionFixture
import com.ailearningos.app.ui.review.ReviewUiState
import com.ailearningos.app.ui.review.ReviewViewModel
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/** 审阅呈现：report 优先、404 诚实降级 submission、双 404 明确文案 */
class ReviewViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private lateinit var gateway: FakeExamGateway
    private lateinit var viewModel: ReviewViewModel

    @Before
    fun setUp() {
        gateway = FakeExamGateway()
        viewModel = ReviewViewModel(exam = gateway, examId = "exam-fixture-0001")
    }

    @Test
    fun `report available renders full report`() = runTest {
        val state = viewModel.state.value
        assertTrue(state is ReviewUiState.Report)
        assertEquals(60.0, (state as ReviewUiState.Report).report.scoreEarned, 0.0)
        assertEquals(listOf("exam-fixture-0001"), gateway.reportCalls)
        assertTrue(gateway.submissionCalls.isEmpty())
    }

    @Test
    fun `report 404 falls back to submission`() = runTest {
        gateway.reportResult = Result.failure(AppError(AppErrorKind.NOT_FOUND))
        gateway.submissionResult = Result.success(submissionFixture())
        viewModel.refresh()
        val state = viewModel.state.value
        assertTrue("404 必须降级到 submission 概要", state is ReviewUiState.Fallback)
        assertEquals("submitted", (state as ReviewUiState.Fallback).submission.status)
        assertEquals(listOf("exam-fixture-0001"), gateway.submissionCalls)
    }

    @Test
    fun `both report and submission 404 yields honest message`() = runTest {
        gateway.reportResult = Result.failure(AppError(AppErrorKind.NOT_FOUND))
        gateway.submissionResult = Result.failure(AppError(AppErrorKind.NOT_FOUND))
        viewModel.refresh()
        val state = viewModel.state.value
        assertTrue(state is ReviewUiState.Error)
        assertEquals("审阅报告尚未生成，请稍后回来查看", (state as ReviewUiState.Error).message)
    }

    @Test
    fun `report network failure is error with retry`() = runTest {
        gateway.reportResult = Result.failure(AppError(AppErrorKind.NETWORK))
        viewModel.refresh()
        assertTrue(viewModel.state.value is ReviewUiState.Error)
        // 不降级：网络失败 ≠ 报告不存在（诚实区分）
        assertTrue(gateway.submissionCalls.isEmpty())

        gateway.reportResult = Result.success(reportFixture())
        viewModel.refresh()
        assertTrue(viewModel.state.value is ReviewUiState.Report)
    }

    @Test
    fun `fallback submission failure after report 404 surfaces its message`() = runTest {
        gateway.reportResult = Result.failure(AppError(AppErrorKind.NOT_FOUND))
        gateway.submissionResult = Result.failure(AppError(AppErrorKind.SERVER))
        viewModel.refresh()
        val state = viewModel.state.value
        assertTrue(state is ReviewUiState.Error)
        assertEquals("服务暂时不可用，请稍后重试", (state as ReviewUiState.Error).message)
    }
}
