package com.ailearningos.app.ui

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.core.BaseUrlPolicy
import com.ailearningos.app.core.BaseUrlProblem
import com.ailearningos.app.core.BaseUrlValidation
import com.ailearningos.app.di.ConfigurationBus
import com.ailearningos.app.testutil.FakeSettingsStore
import com.ailearningos.app.testutil.FakeSystemGateway
import com.ailearningos.app.testutil.MainDispatcherRule
import com.ailearningos.app.testutil.privacyFixture
import com.ailearningos.app.ui.common.ContentState
import com.ailearningos.app.ui.settings.SettingsViewModel
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/** 设置页状态机：地址校验/保存、release 强制 https、隐私四态 */
class SettingsViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    // ---------- 初始态 ----------

    @Test
    fun `initial privacy state is empty not loading`() = runTest {
        val viewModel = createViewModel(allowInsecureHttp = true)
        advanceUntilIdle()
        assertEquals(ContentState.Empty, viewModel.state.value.privacy)
    }

    @Test
    fun `loads saved base url into input`() = runTest {
        val store = FakeSettingsStore("https://api.example.com/")
        val viewModel = createViewModel(allowInsecureHttp = false, store = store)
        advanceUntilIdle()
        assertEquals("https://api.example.com/", viewModel.state.value.effectiveBaseUrl)
        assertEquals("https://api.example.com/", viewModel.state.value.baseUrlInput)
    }

    // ---------- 即时校验 ----------

    @Test
    fun `blank input does not raise validation error`() = runTest {
        val viewModel = createViewModel(allowInsecureHttp = true)
        viewModel.onBaseUrlChange("")
        assertNull(viewModel.state.value.validation)
    }

    @Test
    fun `invalid input surfaces problem immediately`() = runTest {
        val viewModel = createViewModel(allowInsecureHttp = true)
        viewModel.onBaseUrlChange("ftp://api.example.com")
        assertEquals(
            BaseUrlValidation.Invalid(BaseUrlProblem.BAD_SCHEME),
            viewModel.state.value.validation,
        )
    }

    @Test
    fun `valid input surfaces normalized url`() = runTest {
        val viewModel = createViewModel(allowInsecureHttp = true)
        viewModel.onBaseUrlChange("http://10.0.2.2:8000")
        assertEquals(
            BaseUrlValidation.Valid("http://10.0.2.2:8000/"),
            viewModel.state.value.validation,
        )
    }

    // ---------- 保存 ----------

    @Test
    fun `save persists normalized url and notifies bus`() = runTest {
        val store = FakeSettingsStore()
        val bus = ConfigurationBus()
        val emissions = mutableListOf<Unit>()
        val collector = launch { bus.changes.collect { emissions.add(Unit) } }
        advanceUntilIdle()

        val viewModel = createViewModel(allowInsecureHttp = true, store = store, bus = bus)
        advanceUntilIdle()
        viewModel.onBaseUrlChange(" http://192.168.1.7:8000 ")
        viewModel.saveBaseUrl()
        advanceUntilIdle()

        assertEquals(listOf("http://192.168.1.7:8000/"), store.savedValues)
        assertEquals("http://192.168.1.7:8000/", viewModel.state.value.effectiveBaseUrl)
        assertEquals(SettingsViewModel.SAVED_NOTICE, viewModel.state.value.notice)
        assertEquals(1, emissions.size)
        collector.cancel()
    }

    @Test
    fun `save with invalid url is rejected and store untouched`() = runTest {
        val store = FakeSettingsStore()
        val viewModel = createViewModel(allowInsecureHttp = true, store = store)
        advanceUntilIdle()
        viewModel.onBaseUrlChange("https://api.example.com/?x=1")
        viewModel.saveBaseUrl()
        advanceUntilIdle()
        assertNull(store.stored)
        assertEquals("地址不能包含查询参数、锚点或凭据信息", viewModel.state.value.notice)
    }

    @Test
    fun `release build rejects http even loopback with clear message`() = runTest {
        val store = FakeSettingsStore()
        val viewModel = createViewModel(allowInsecureHttp = false, store = store)
        advanceUntilIdle()
        viewModel.onBaseUrlChange("http://10.0.2.2:8000")
        viewModel.saveBaseUrl()
        advanceUntilIdle()
        assertEquals("当前构建要求使用 https 地址", viewModel.state.value.notice)
        assertNull(store.stored)
    }

    @Test
    fun `debug build rejects public http with clear message`() = runTest {
        val store = FakeSettingsStore()
        val viewModel = createViewModel(allowInsecureHttp = true, store = store)
        advanceUntilIdle()
        viewModel.onBaseUrlChange("http://api.example.com")
        viewModel.saveBaseUrl()
        advanceUntilIdle()
        assertEquals("http 地址仅允许本机回环或局域网（调试构建）", viewModel.state.value.notice)
        assertNull(store.stored)
    }

    @Test
    fun `message mapping covers every problem`() {
        val viewModel = createViewModel(allowInsecureHttp = true)
        BaseUrlProblem.entries.forEach { problem ->
            val message = viewModel.messageFor(problem)
            assertTrue("每个问题都要有脱敏文案：$problem", message.isNotBlank())
        }
        assertEquals("请输入 API 地址", viewModel.messageFor(BaseUrlProblem.EMPTY))
    }

    @Test
    fun `default debug base url passes debug policy`() {
        val validation = BaseUrlPolicy.validate(BaseUrlPolicy.DEFAULT_DEBUG_BASE_URL, allowInsecureHttp = true)
        assertEquals(BaseUrlValidation.Valid(BaseUrlPolicy.DEFAULT_DEBUG_BASE_URL), validation)
    }

    // ---------- 隐私四态 ----------

    @Test
    fun `refreshPrivacy success`() = runTest {
        val gateway = FakeSystemGateway().apply { privacyResult = Result.success(privacyFixture()) }
        val viewModel = createViewModel(allowInsecureHttp = true, system = gateway)
        advanceUntilIdle()
        viewModel.refreshPrivacy()
        advanceUntilIdle()
        assertEquals(
            ContentState.Success(privacyFixture()),
            viewModel.state.value.privacy,
        )
    }

    @Test
    fun `refreshPrivacy failure shows sanitized error`() = runTest {
        val gateway = FakeSystemGateway().apply {
            privacyResult = Result.failure(AppError(AppErrorKind.SERVER))
        }
        val viewModel = createViewModel(allowInsecureHttp = true, system = gateway)
        advanceUntilIdle()
        viewModel.refreshPrivacy()
        advanceUntilIdle()
        assertEquals(
            ContentState.Error(AppError(AppErrorKind.SERVER).userMessage),
            viewModel.state.value.privacy,
        )
    }

    // ---------- 工厂 ----------

    private fun createViewModel(
        allowInsecureHttp: Boolean,
        store: FakeSettingsStore = FakeSettingsStore(),
        bus: ConfigurationBus = ConfigurationBus(),
        system: FakeSystemGateway = FakeSystemGateway(),
    ) = SettingsViewModel(
        settingsStore = store,
        configurationBus = bus,
        system = system,
        allowInsecureHttp = allowInsecureHttp,
    )
}
