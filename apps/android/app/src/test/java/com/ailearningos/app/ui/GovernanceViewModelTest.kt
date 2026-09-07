package com.ailearningos.app.ui

import androidx.lifecycle.viewModelScope
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.GovernanceGateway
import com.ailearningos.app.testutil.FakeGovernanceGateway
import com.ailearningos.app.testutil.MainDispatcherRule
import com.ailearningos.app.testutil.appError
import com.ailearningos.app.testutil.governanceVersionFixture
import com.ailearningos.app.testutil.opsSnapshotFixture
import com.ailearningos.app.ui.governance.GovernanceViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/**
 * 治理页状态机（M12-05）：init 三面并发加载、审计固定 limit、
 * 空审计如实呈现、单面失败互不阻塞（错误文案非空、成功面照常落地）。
 *
 * 调度隔离与 finally 取消约定与 SearchViewModelTest 同款
 * （MainDispatcherRule + created 列表统一 cancel viewModelScope）。
 */
class GovernanceViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private lateinit var gateway: FakeGovernanceGateway

    private val created = mutableListOf<GovernanceViewModel>()

    @Before
    fun setUp() {
        gateway = FakeGovernanceGateway()
    }

    private fun runGovernanceTest(block: suspend TestScope.() -> Unit) = runTest(
        mainDispatcherRule.testDispatcher.scheduler,
    ) {
        try {
            block()
        } finally {
            created.forEach { it.viewModelScope.cancel() }
        }
    }

    private fun newViewModel(): GovernanceViewModel {
        val model = GovernanceViewModel(governance = gateway)
        created.add(model)
        return model
    }

    // ---------- init 并发加载 ----------

    @Test
    fun `init loads version snapshot and audit concurrently`() = runGovernanceTest {
        val model = newViewModel()
        val state = model.state.value
        // 三个面各请求恰好一次
        assertEquals(1, gateway.versionCalls)
        assertEquals(1, gateway.opsSnapshotCalls)
        assertEquals(1, gateway.auditCalls.size)
        // 三个面都落地且无错误、无残留 loading
        assertEquals(governanceVersionFixture(), state.version)
        assertEquals(opsSnapshotFixture(), state.snapshot)
        assertEquals(2, state.auditEntries.size)
        assertNull(state.versionError)
        assertNull(state.snapshotError)
        assertNull(state.auditError)
        assertTrue(!state.versionLoading)
        assertTrue(!state.snapshotLoading)
        assertTrue(!state.auditLoading)
    }

    @Test
    fun `audit uses fixed limit 100`() = runGovernanceTest {
        val model = newViewModel()
        assertEquals(listOf(GovernanceGateway.DEFAULT_LIMIT), gateway.auditCalls)
        assertEquals(100, gateway.auditCalls.single())
        assertNull(model.state.value.auditError)
    }

    // ---------- 空审计 ----------

    @Test
    fun `empty audit stays empty without error`() = runGovernanceTest {
        gateway.auditResult = Result.success(emptyList())
        val model = newViewModel()
        val state = model.state.value
        assertTrue(state.auditEntries.isEmpty()) // 空列表是事实，不是错误
        assertNull(state.auditError)
        assertTrue(!state.auditLoading)
    }

    // ---------- 单面失败互不阻塞 ----------

    @Test
    fun `ops forbidden and audit server failure do not block version`() = runGovernanceTest {
        gateway.opsSnapshotResult = Result.failure(appError(AppErrorKind.FORBIDDEN)) // learner 403
        gateway.auditResult = Result.failure(appError(AppErrorKind.SERVER)) // 无 DB 503
        val model = newViewModel()
        val state = model.state.value
        // 失败面：loading 收口、错误文案非空（脱敏固定文案）
        assertTrue(!state.snapshotLoading)
        assertTrue(!state.auditLoading)
        assertTrue(!state.snapshotError.isNullOrEmpty())
        assertTrue(!state.auditError.isNullOrEmpty())
        assertNull(state.snapshot)
        assertTrue(state.auditEntries.isEmpty())
        // 成功面：version 照常落地，不被其他面拖累
        assertTrue(!state.versionLoading)
        assertNull(state.versionError)
        assertNotNull(state.version)
        assertEquals(governanceVersionFixture(), state.version)
    }

    // ---------- 刷新失败保留旧数据 ----------

    @Test
    fun `refresh version failure keeps previous version`() = runGovernanceTest {
        val model = newViewModel()
        // init 成功：version 已落地
        assertEquals(governanceVersionFixture(), model.state.value.version)
        assertNull(model.state.value.versionError)
        // 切换为 SERVER 错误后刷新
        gateway.versionResult = Result.failure(appError(AppErrorKind.SERVER))
        model.refresh()
        val state = model.state.value
        // 旧 version 保留，不因失败被清空
        assertEquals(governanceVersionFixture(), state.version)
        assertTrue(!state.versionError.isNullOrEmpty())
        assertTrue(!state.versionLoading)
    }

    @Test
    fun `refresh audit failure keeps previous audit entries`() = runGovernanceTest {
        val model = newViewModel()
        // init 成功：审计两条已落地
        assertEquals(2, model.state.value.auditEntries.size)
        assertNull(model.state.value.auditError)
        // 切换为 SERVER 错误后刷新
        gateway.auditResult = Result.failure(appError(AppErrorKind.SERVER))
        model.refresh()
        val state = model.state.value
        // 旧 auditEntries 保留，不因失败被清空
        assertEquals(2, state.auditEntries.size)
        assertTrue(!state.auditError.isNullOrEmpty())
        assertTrue(!state.auditLoading)
    }

    // ---------- 顺序竞争防护 ----------

    @Test
    fun `stale ops snapshot does not overwrite fresh refresh`() = runGovernanceTest {
        val gate = CompletableDeferred<Unit>()
        var firstCall = true
        gateway.opsSnapshotHandler = {
            if (firstCall) {
                firstCall = false
                try {
                    gate.await() // 首轮挂起（模拟慢网络）
                } catch (_: CancellationException) {
                    // 被 refresh 取消后吞掉取消信号，继续返回旧数据（迟到响应）
                }
                opsSnapshotFixture(papersTotal = 111)
            } else {
                null // 第二轮走默认 fixture（papersTotal=3）
            }
        }
        val model = newViewModel() // init 同步执行到 gate.await() 挂起
        model.refresh() // 取消首轮快照任务，第二轮立即用默认 fixture 完成
        gate.complete(Unit) // 旧响应逃过取消后返回，seq 守卫不得放行
        assertEquals(3, model.state.value.snapshot?.papersTotal) // 默认 3，而非迟到的 111
    }
}
