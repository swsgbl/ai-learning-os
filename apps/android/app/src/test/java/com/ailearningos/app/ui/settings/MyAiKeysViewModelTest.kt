package com.ailearningos.app.ui.settings

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.ui.common.ContentState
import com.ailearningos.app.data.MyAiKeyInfo
import com.ailearningos.app.testutil.FakeSystemGateway
import com.ailearningos.app.di.ConfigurationBus
import com.ailearningos.app.data.local.SettingsStore
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
class MyAiKeysViewModelTest {

    private val dispatcher = UnconfinedTestDispatcher()

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    private fun vm(system: FakeSystemGateway) = SettingsViewModel(
        settingsStore = object : SettingsStore {
            override suspend fun loadBaseUrl(): String? = null
            override suspend fun saveBaseUrl(value: String) = Unit
        },
        configurationBus = ConfigurationBus(),
        system = system,
        allowInsecureHttp = true,
    )

    @Test
    fun `loadMyAiKeys 透出三槽位状态`() = runTest(dispatcher) {
        val vm = vm(FakeSystemGateway())
        vm.loadMyAiKeys()
        val state = vm.state.value.myAiKeys
        assertTrue(state is ContentState.Success)
        assertEquals(listOf("llm", "asr_cloud", "tts_cloud"), (state as ContentState.Success).value.map { it.slot })
        assertEquals(false, state.value.first().configured)
    }

    @Test
    fun `saveMyAiKey 短密钥被拒且不调网关`() = runTest(dispatcher) {
        val system = FakeSystemGateway()
        val vm = vm(system)
        vm.saveMyAiKey("llm", "short")
        assertEquals("密钥过短（至少 8 位）", vm.state.value.aiKeyNotice)
        assertTrue(vm.state.value.myAiKeys is ContentState.Empty)
    }

    @Test
    fun `save 后状态刷新为已配置`() = runTest(dispatcher) {
        val vm = vm(FakeSystemGateway())
        vm.saveMyAiKey("asr_cloud", "sk-mine-987654")
        val state = vm.state.value.myAiKeys as ContentState.Success
        val asr = state.value.first { it.slot == "asr_cloud" }
        assertEquals(true, asr.configured)
        assertEquals("7654", asr.last4)
        assertEquals("已保存（加密存储，仅自己可见）", vm.state.value.aiKeyNotice)
    }

    @Test
    fun `delete 后回退未配置`() = runTest(dispatcher) {
        val vm = vm(FakeSystemGateway())
        vm.saveMyAiKey("tts_cloud", "sk-tts-123456")
        vm.deleteMyAiKey("tts_cloud")
        val state = vm.state.value.myAiKeys as ContentState.Success
        assertEquals(false, state.value.first { it.slot == "tts_cloud" }.configured)
    }

    @Test
    fun `load 失败进 Error 不崩溃`() = runTest(dispatcher) {
        val system = FakeSystemGateway()
        system.myAiKeysError = AppError(AppErrorKind.NETWORK)
        val vm = vm(system)
        vm.loadMyAiKeys()
        assertTrue(vm.state.value.myAiKeys is ContentState.Error)
    }
}
