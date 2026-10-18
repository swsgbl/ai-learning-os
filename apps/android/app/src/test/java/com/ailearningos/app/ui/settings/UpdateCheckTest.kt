package com.ailearningos.app.ui.settings

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.LatestAppInfo
import com.ailearningos.app.data.local.SettingsStore
import com.ailearningos.app.di.ConfigurationBus
import com.ailearningos.app.testutil.FakeSystemGateway
import com.ailearningos.app.ui.common.ContentState
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class UpdateCheckTest {

    private val dispatcher = UnconfinedTestDispatcher()

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    // ---- 纯判定逻辑 ----

    @Test
    fun `evaluate null latest = NotConfigured`() {
        val result = UpdateCheckEvaluator.evaluate(1, "0.1.0", null)
        assertTrue(result is UpdateCheckResult.NotConfigured)
    }

    @Test
    fun `evaluate higher server code = Available`() {
        val latest = LatestAppInfo("android", "0.2.0", 2, "https://example.com/a.apk")
        val result = UpdateCheckEvaluator.evaluate(1, "0.1.0", latest)
        assertEquals(UpdateCheckResult.Available("0.2.0", "https://example.com/a.apk"), result)
    }

    @Test
    fun `evaluate equal or lower server code = UpToDate`() {
        val same = LatestAppInfo("android", "0.1.0", 1, "https://example.com/a.apk")
        val lower = LatestAppInfo("android", "0.0.9", 0, "https://example.com/a.apk")
        assertTrue(UpdateCheckEvaluator.evaluate(1, "0.1.0", same) is UpdateCheckResult.UpToDate)
        assertTrue(UpdateCheckEvaluator.evaluate(1, "0.1.0", lower) is UpdateCheckResult.UpToDate)
    }

    // ---- ViewModel 集成 ----

    private fun viewModelWith(
        system: FakeSystemGateway,
        code: Int = 1,
        name: String = "0.1.0",
    ): SettingsViewModel = SettingsViewModel(
        settingsStore = object : SettingsStore {
            override suspend fun loadBaseUrl(): String? = null
            override suspend fun saveBaseUrl(value: String) = Unit
        },
        configurationBus = ConfigurationBus(),
        system = system,
        allowInsecureHttp = true,
        currentVersionCode = code,
        currentVersionName = name,
    )

    @Test
    fun `checkUpdate 未配置 404 映射为 NotConfigured`() = runTest(dispatcher) {
        val system = FakeSystemGateway()
        val vm = viewModelWith(system)
        vm.checkUpdate()
        val state = vm.state.value.updateCheck
        assertTrue(state is ContentState.Success && state.value is UpdateCheckResult.NotConfigured)
        assertEquals(1, system.latestAppCalls)
    }

    @Test
    fun `checkUpdate 服务端有新版 映射为 Available`() = runTest(dispatcher) {
        val system = FakeSystemGateway()
        system.latestAppResult = Result.success(LatestAppInfo("android", "0.2.0", 2, "https://example.com/a.apk"))
        val vm = viewModelWith(system)
        vm.checkUpdate()
        val state = vm.state.value.updateCheck
        assertTrue(state is ContentState.Success && state.value is UpdateCheckResult.Available)
    }

    @Test
    fun `checkUpdate 网络故障 映射为脱敏 Error`() = runTest(dispatcher) {
        val system = FakeSystemGateway()
        system.latestAppResult = Result.failure(AppError(AppErrorKind.NETWORK))
        val vm = viewModelWith(system)
        vm.checkUpdate()
        val state = vm.state.value.updateCheck
        assertTrue(state is ContentState.Error)
        assertEquals("无法连接 API，请检查网络后重试", (state as ContentState.Error).message)
    }
}
