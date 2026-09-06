package com.ailearningos.app.ui

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.testutil.FakeSystemGateway
import com.ailearningos.app.testutil.MainDispatcherRule
import com.ailearningos.app.testutil.privacyFixture
import com.ailearningos.app.ui.common.ContentState
import com.ailearningos.app.ui.home.HomeViewModel
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/** 首页快照状态机：loading / error / success（含隐私子态） */
class HomeViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    @Test
    fun `initial state is loading`() = runTest {
        val viewModel = HomeViewModel(FakeSystemGateway())
        assertEquals(ContentState.Loading, viewModel.state.value)
    }

    @Test
    fun `refresh success exposes health and privacy`() = runTest {
        val gateway = FakeSystemGateway().apply {
            healthOk = true
            privacyResult = Result.success(privacyFixture(searchMode = "cloud"))
        }
        val viewModel = HomeViewModel(gateway)
        viewModel.refresh()
        advanceUntilIdle()
        val state = viewModel.state.value
        assertTrue(state is ContentState.Success)
        val snapshot = (state as ContentState.Success).value
        assertTrue(snapshot.apiServiceOk)
        assertEquals(privacyFixture(searchMode = "cloud"), (snapshot.privacy as ContentState.Success).value)
    }

    @Test
    fun `privacy failure keeps health success with privacy error`() = runTest {
        val gateway = FakeSystemGateway().apply {
            healthOk = true
            privacyResult = Result.failure(AppError(AppErrorKind.NETWORK))
        }
        val viewModel = HomeViewModel(gateway)
        viewModel.refresh()
        advanceUntilIdle()
        val snapshot = (viewModel.state.value as ContentState.Success).value
        assertTrue(snapshot.apiServiceOk)
        assertEquals(
            AppError(AppErrorKind.NETWORK).userMessage,
            (snapshot.privacy as ContentState.Error).message,
        )
    }

    @Test
    fun `health failure maps whole state to error`() = runTest {
        val gateway = FakeSystemGateway().apply {
            healthError = AppError(AppErrorKind.SERVER)
        }
        val viewModel = HomeViewModel(gateway)
        viewModel.refresh()
        advanceUntilIdle()
        assertEquals(
            AppError(AppErrorKind.SERVER).userMessage,
            (viewModel.state.value as ContentState.Error).message,
        )
    }

    @Test
    fun `health responding but degraded is still success with flag false`() = runTest {
        val gateway = FakeSystemGateway().apply { healthOk = false }
        val viewModel = HomeViewModel(gateway)
        viewModel.refresh()
        advanceUntilIdle()
        val snapshot = (viewModel.state.value as ContentState.Success).value
        assertEquals(false, snapshot.apiServiceOk)
    }
}
