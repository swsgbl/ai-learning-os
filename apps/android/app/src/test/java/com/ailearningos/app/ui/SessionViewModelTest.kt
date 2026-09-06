package com.ailearningos.app.ui

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.AuthGateway
import com.ailearningos.app.di.ConfigurationBus
import com.ailearningos.app.data.model.UserProfile
import com.ailearningos.app.testutil.FakeAuthGateway
import com.ailearningos.app.testutil.MainDispatcherRule
import com.ailearningos.app.testutil.userFixture
import com.ailearningos.app.ui.session.SessionState
import com.ailearningos.app.ui.session.SessionViewModel
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/** 会话状态机迁移（loading / unavailable / auth off / anonymous / authenticated） */
class SessionViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    @Test
    fun `starts at loading then resolves on refresh`() = runTest {
        val gateway = FakeAuthGateway().apply { enabled = false }
        val viewModel = SessionViewModel(gateway, ConfigurationBus())
        // 构造只订阅配置变更，不自动探测：初始为 Loading
        assertEquals(SessionState.Loading, viewModel.state.value)
        viewModel.refresh()
        advanceUntilIdle()
        assertEquals(SessionState.AuthDisabled, viewModel.state.value)
    }

    @Test
    fun `auth disabled maps to local mode state`() = runTest {
        val gateway = FakeAuthGateway().apply { enabled = false }
        val viewModel = SessionViewModel(gateway, ConfigurationBus())
        viewModel.refresh()
        advanceUntilIdle()
        assertEquals(SessionState.AuthDisabled, viewModel.state.value)
    }

    @Test
    fun `authenticated user state`() = runTest {
        val gateway = FakeAuthGateway().apply {
            enabled = true
            user = userFixture(username = "alice", role = "admin")
        }
        val viewModel = SessionViewModel(gateway, ConfigurationBus())
        viewModel.refresh()
        advanceUntilIdle()
        assertEquals(SessionState.Authenticated(userFixture(username = "alice", role = "admin")), viewModel.state.value)
    }

    @Test
    fun `no user maps to anonymous`() = runTest {
        val gateway = FakeAuthGateway().apply {
            enabled = true
            user = null
        }
        val viewModel = SessionViewModel(gateway, ConfigurationBus())
        viewModel.refresh()
        advanceUntilIdle()
        assertEquals(SessionState.Anonymous, viewModel.state.value)
    }

    @Test
    fun `probe failure maps to unavailable with sanitized message`() = runTest {
        val gateway = FakeAuthGateway().apply {
            authEnabledError = AppError(AppErrorKind.NETWORK)
        }
        val viewModel = SessionViewModel(gateway, ConfigurationBus())
        viewModel.refresh()
        advanceUntilIdle()
        val state = viewModel.state.value
        assertEquals(
            AppError(AppErrorKind.NETWORK).userMessage,
            (state as SessionState.Unavailable).message,
        )
    }

    @Test
    fun `logout resets to anonymous even when local clear fails`() = runTest {
        val gateway = FakeAuthGateway().apply {
            user = userFixture()
            logoutError = AppError(AppErrorKind.STORAGE)
        }
        val viewModel = SessionViewModel(gateway, ConfigurationBus())
        viewModel.refresh()
        advanceUntilIdle()
        viewModel.logout()
        advanceUntilIdle()
        assertEquals(SessionState.Anonymous, viewModel.state.value)
        assertEquals("清理失败要给出脱敏提示", AppError(AppErrorKind.STORAGE).userMessage, viewModel.notice.value)
        assertEquals(1, gateway.logoutCalls)
    }

    @Test
    fun `logout succeeds silently`() = runTest {
        val gateway = FakeAuthGateway().apply { user = userFixture() }
        val viewModel = SessionViewModel(gateway, ConfigurationBus())
        viewModel.refresh()
        advanceUntilIdle()
        viewModel.logout()
        advanceUntilIdle()
        assertEquals(SessionState.Anonymous, viewModel.state.value)
        assertNull(viewModel.notice.value)
    }

    @Test
    fun `configuration change triggers re-probe`() = runTest {
        val bus = ConfigurationBus()
        val gateway = FakeAuthGateway().apply { user = userFixture() }
        val viewModel = SessionViewModel(gateway, bus)
        advanceUntilIdle()

        gateway.user = null
        val probesBefore = gateway.refreshCalls
        bus.notifyChanged()
        advanceUntilIdle()

        assertTrue(gateway.refreshCalls > probesBefore)
        assertEquals(SessionState.Anonymous, viewModel.state.value)
    }

    @Test
    fun `change emitted before viewmodel subscribes still triggers re-probe`() = runTest {
        val bus = ConfigurationBus()
        // 事件先于订阅发生（界面尚未创建/订阅协程未启动）
        bus.notifyChanged()

        val gateway = FakeAuthGateway().apply { user = userFixture() }
        val viewModel = SessionViewModel(gateway, bus)
        advanceUntilIdle()

        assertTrue("后订阅者必须收到回放的变更并重探", gateway.refreshCalls > 0)
        assertEquals(SessionState.Authenticated(userFixture()), viewModel.state.value)
    }

    @Test
    fun `stale probe released late cannot overwrite newer refresh result`() = runTest {
        // Main 换成与 runTest 共用 scheduler 的 StandardTestDispatcher：
        // 闸门放行后的续体统一进测试调度器，闸门释放 → 旧探测恢复 → 覆盖与否
        // 全程确定性可断言（默认 Unconfined 规则的续体会落入其私有 scheduler）
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        try {
            val gateway = GatedAuthGateway()
            val staleGate = CompletableDeferred<Unit>()
            gateway.enqueueAuthEnabled(staleGate, enabled = true)
            val freshGate = CompletableDeferred<Unit>()
            gateway.enqueueAuthEnabled(freshGate, enabled = true)
            // currentUser 按调用序配对：第 1 次调用属于第二个 refresh（第一个探测
            // 挂在 authEnabled，到不了 currentUser），第 2 次属于被放行的旧探测
            val freshUserGate = CompletableDeferred<Unit>()
            gateway.enqueueUser(freshUserGate, user = null)
            val staleUserGate = CompletableDeferred<Unit>()
            gateway.enqueueUser(staleUserGate, user = userFixture(username = "stale"))

            val viewModel = SessionViewModel(gateway, ConfigurationBus())
            val seen = mutableListOf<SessionState>()
            val collector = launch { viewModel.state.collect { seen.add(it) } }
            advanceUntilIdle()

            // 第一个 refresh 挂起在 authEnabled 闸门上（旧 API 地址的探测迟迟不归）
            viewModel.refresh()
            advanceUntilIdle()
            assertEquals(1, gateway.authEnabledCalls)

            // 设置页保存新地址触发第二个 refresh：取消第一个并发起新探测
            viewModel.refresh()
            advanceUntilIdle()
            assertEquals(2, gateway.authEnabledCalls)

            freshGate.complete(Unit)
            freshUserGate.complete(Unit)
            advanceUntilIdle()
            assertEquals("新探测结果先生效", SessionState.Anonymous, viewModel.state.value)

            // 此刻才放行旧探测（连同它若被放行会取到的 stale 用户）：不得覆盖新结果
            staleGate.complete(Unit)
            staleUserGate.complete(Unit)
            advanceUntilIdle()

            assertEquals("最终状态以第二个 refresh 结果为准", SessionState.Anonymous, viewModel.state.value)
            assertTrue("取消的过期探测不得把状态误写成 Unavailable", seen.none { it is SessionState.Unavailable })
            assertTrue("旧探测结果不得进入状态机", seen.none { (it as? SessionState.Authenticated)?.user?.username == "stale" })
            collector.cancel()
        } finally {
            Dispatchers.resetMain()
        }
    }

    @Test
    fun `stale probe released after logout cannot resurrect authenticated`() = runTest {
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        try {
            val gateway = GatedAuthGateway()
            val gate = CompletableDeferred<Unit>()
            gateway.enqueueAuthEnabled(gate, enabled = true)

            val viewModel = SessionViewModel(gateway, ConfigurationBus())
            viewModel.refresh()
            advanceUntilIdle()
            assertEquals("第一个探测确实挂起在 authEnabled", 1, gateway.authEnabledCalls)

            viewModel.logout() // 取消已挂起的在途探测并清理本地
            advanceUntilIdle()
            assertEquals(SessionState.Anonymous, viewModel.state.value)

            // 此刻才放行旧探测：续体即使被调度恢复，也不得把已登出状态覆盖回 Authenticated
            gate.complete(Unit)
            advanceUntilIdle()

            assertEquals(SessionState.Anonymous, viewModel.state.value)
            assertEquals(1, gateway.logoutCalls)
        } finally {
            Dispatchers.resetMain()
        }
    }
}

/**
 * 闸门式 AuthGateway：authEnabled/currentUser 的第 N 次调用挂起在第 N 个
 * 排队闸门（[CompletableDeferred]）上，测试放行后才返回排队结果——用于把
 * 「旧探测晚于新探测完成」的并发窗口做成确定性 JVM 测试。
 * 注意按“调用序”配对而非按“探测”配对：被取消的旧探测可能永远走不到 currentUser。
 */
private class GatedAuthGateway : AuthGateway {

    private val authEnabledGates = ArrayDeque<CompletableDeferred<Unit>>()
    private val authEnabledResults = ArrayDeque<Boolean>()
    private val userGates = ArrayDeque<CompletableDeferred<Unit>>()
    private val userResults = ArrayDeque<UserProfile?>()

    /** 真实进入 authEnabled 的次数（断言第一个探测确实挂住） */
    var authEnabledCalls = 0
        private set

    var logoutCalls = 0
        private set

    /** 排队一次 authEnabled 调用：放行 [gate] 后返回 [enabled] */
    fun enqueueAuthEnabled(gate: CompletableDeferred<Unit>, enabled: Boolean) {
        authEnabledGates.addLast(gate)
        authEnabledResults.addLast(enabled)
    }

    /** 排队一次 currentUser 调用：放行 [gate] 后返回 [user] */
    fun enqueueUser(gate: CompletableDeferred<Unit>, user: UserProfile?) {
        userGates.addLast(gate)
        userResults.addLast(user)
    }

    override suspend fun authEnabled(): Boolean {
        authEnabledCalls++
        authEnabledGates.removeFirst().await()
        return authEnabledResults.removeFirst()
    }

    override suspend fun login(username: String, password: String): UserProfile =
        error("刷新并发测试不触达登录")

    override suspend fun currentUser(): UserProfile? {
        userGates.removeFirst().await()
        return userResults.removeFirst()
    }

    override suspend fun logout() {
        logoutCalls++
    }
}
