package com.ailearningos.app.ui.login

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.testutil.FakeAuthGateway
import com.ailearningos.app.testutil.FakeCredentialVault
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class LoginRememberPasswordTest {

    private val dispatcher = UnconfinedTestDispatcher()

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun `init 预填记住的凭据`() = runTest(dispatcher) {
        val vault = FakeCredentialVault()
        vault.loadResult = Result.success("alice" to "secret-pass-123")
        val vm = LoginViewModel(auth = FakeAuthGateway(), credentialVault = vault)
        assertEquals("alice", vm.state.value.username)
        assertEquals("secret-pass-123", vm.state.value.password)
        assertTrue(vm.state.value.rememberPassword)
    }

    @Test
    fun `登录成功且勾选 → 保存凭据`() = runTest(dispatcher) {
        val auth = FakeAuthGateway()
        val vault = FakeCredentialVault()
        val vm = LoginViewModel(auth = auth, credentialVault = vault)
        vm.onUsernameChange("bob")
        vm.onPasswordChange("pass-word-1234")
        vm.submit()
        assertEquals("bob" to "pass-word-1234", vault.saved)
        assertEquals(1, vault.saveCalls)
        assertEquals("", vm.state.value.password)
    }

    @Test
    fun `取消勾选 → 立即抹除已存密文且登录不保存`() = runTest(dispatcher) {
        val auth = FakeAuthGateway()
        val vault = FakeCredentialVault()
        val vm = LoginViewModel(auth = auth, credentialVault = vault)
        vm.onUsernameChange("carol")
        vm.onPasswordChange("pass-word-1234")
        vm.onRememberPasswordChange(false)
        assertEquals(1, vault.clearCalls)
        vm.submit()
        assertEquals(0, vault.saveCalls)
        assertNull(vault.saved)
    }

    @Test
    fun `vault 读失败 → 空表单不阻断登录`() = runTest(dispatcher) {
        val vault = FakeCredentialVault()
        vault.loadResult = Result.failure(AppError(AppErrorKind.STORAGE))
        val vm = LoginViewModel(auth = FakeAuthGateway(), credentialVault = vault)
        assertEquals("", vm.state.value.username)
    }
}
