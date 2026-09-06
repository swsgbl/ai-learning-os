package com.ailearningos.app.ui

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.testutil.FakeAuthGateway
import com.ailearningos.app.testutil.MainDispatcherRule
import com.ailearningos.app.testutil.userFixture
import com.ailearningos.app.ui.login.LoginViewModel
import com.ailearningos.app.ui.login.validateCredentials
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/** 登录状态机：校验、提交中、成功清密码、失败统一文案 */
class LoginViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    @Test
    fun `client side credential validation mirrors server rules`() {
        assertNull(validateCredentials("alice", "pw12345678"))
        assertEquals("用户名过短应拒绝", "用户名需为 3-32 位字母、数字或下划线", validateCredentials("ab", "pw12345678"))
        assertEquals(
            "用户名含非法字符应拒绝",
            "用户名需为 3-32 位字母、数字或下划线",
            validateCredentials("bad name", "pw12345678"),
        )
        assertEquals("密码过短应拒绝", "密码需为 8-128 位", validateCredentials("alice", "short"))
        assertNull("边界 8 位密码放行", validateCredentials("alice", "12345678"))
    }

    @Test
    fun `invalid username blocks submit without calling gateway`() = runTest {
        val gateway = FakeAuthGateway()
        val viewModel = LoginViewModel(gateway)
        viewModel.onUsernameChange("ab")
        viewModel.onPasswordChange("pw12345678")
        viewModel.submit()
        advanceUntilIdle()
        assertEquals(0, gateway.loginCalls)
        assertEquals("用户名需为 3-32 位字母、数字或下划线", viewModel.state.value.error)
    }

    @Test
    fun `username with surrounding whitespace is rejected not silently trimmed`() {
        assertEquals(
            "首尾空白直接非法，不得先 trim 再匹配",
            "用户名需为 3-32 位字母、数字或下划线",
            validateCredentials(" alice ", "pw12345678"),
        )
        assertEquals(
            "前缀空白同样非法",
            "用户名需为 3-32 位字母、数字或下划线",
            validateCredentials(" alice", "pw12345678"),
        )
        assertEquals(
            "后缀空白同样非法",
            "用户名需为 3-32 位字母、数字或下划线",
            validateCredentials("alice ", "pw12345678"),
        )
    }

    @Test
    fun `submit does not repair whitespace username`() = runTest {
        val gateway = FakeAuthGateway()
        val viewModel = LoginViewModel(gateway)
        viewModel.onUsernameChange(" alice ")
        viewModel.onPasswordChange("pw12345678")
        assertEquals("含空白用户名允许点击提交（由校验拦截）", true, viewModel.state.value.canSubmit)

        viewModel.submit()
        advanceUntilIdle()

        assertEquals("不得自动修复成 alice 提交", 0, gateway.loginCalls)
        assertNull(gateway.lastSubmittedUsername)
        assertEquals("用户名需为 3-32 位字母、数字或下划线", viewModel.state.value.error)
    }

    @Test
    fun `submit success clears password and reports success`() = runTest {
        val gateway = FakeAuthGateway().apply { loginResult = Result.success(userFixture()) }
        val viewModel = LoginViewModel(gateway)
        viewModel.onUsernameChange("alice")
        viewModel.onPasswordChange("pw12345678")
        viewModel.submit()
        advanceUntilIdle()
        val state = viewModel.state.value
        assertTrue(state.success)
        assertFalse(state.submitting)
        assertEquals("成功后内存中不得残留密码", "", state.password)
        assertNull(state.error)
        assertEquals("alice", gateway.lastSubmittedUsername)
        assertEquals(1, gateway.loginCalls)
    }

    @Test
    fun `submit with 401 shows unified failure message`() = runTest {
        val gateway = FakeAuthGateway().apply {
            loginResult = Result.failure(AppError(AppErrorKind.UNAUTHORIZED))
        }
        val viewModel = LoginViewModel(gateway)
        viewModel.onUsernameChange("alice")
        viewModel.onPasswordChange("pw12345678")
        viewModel.submit()
        advanceUntilIdle()
        val state = viewModel.state.value
        assertEquals(LoginViewModel.LOGIN_FAILED_MESSAGE, state.error)
        assertFalse(state.submitting)
        assertFalse(state.success)
    }

    @Test
    fun `submit with network failure shows sanitized message`() = runTest {
        val gateway = FakeAuthGateway().apply {
            loginResult = Result.failure(AppError(AppErrorKind.NETWORK))
        }
        val viewModel = LoginViewModel(gateway)
        viewModel.onUsernameChange("alice")
        viewModel.onPasswordChange("pw12345678")
        viewModel.submit()
        advanceUntilIdle()
        assertEquals(AppError(AppErrorKind.NETWORK).userMessage, viewModel.state.value.error)
    }

    @Test
    fun `typing clears previous error`() = runTest {
        val gateway = FakeAuthGateway().apply {
            loginResult = Result.failure(AppError(AppErrorKind.UNAUTHORIZED))
        }
        val viewModel = LoginViewModel(gateway)
        viewModel.onUsernameChange("alice")
        viewModel.onPasswordChange("pw12345678")
        viewModel.submit()
        advanceUntilIdle()
        assertEquals(LoginViewModel.LOGIN_FAILED_MESSAGE, viewModel.state.value.error)
        viewModel.onPasswordChange("another-password")
        assertNull(viewModel.state.value.error)
    }
}
