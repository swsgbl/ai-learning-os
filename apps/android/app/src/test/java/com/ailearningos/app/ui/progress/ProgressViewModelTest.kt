package com.ailearningos.app.ui.progress

import com.ailearningos.app.testutil.FakeSystemGateway
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class ProgressViewModelTest {

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
    fun `load 汇聚三源任务薄弱统计`() = runTest(dispatcher) {
        val vm = ProgressViewModel(system = FakeSystemGateway(), exams = null)
        vm.load()
        val state = vm.state.value
        assertNotNull(state.plan)
        assertEquals(2, state.plan?.task_count)
        assertEquals(1, state.weakConceptCount)
        assertEquals(0, state.paperCount)
    }

    @Test
    fun `单源失败不拖垮整页`() = runTest(dispatcher) {
        val system = FakeSystemGateway()
        system.dailyPlanResult = kotlin.Result.failure(IllegalStateException("plan down"))
        val vm = ProgressViewModel(system = system, exams = null)
        vm.load()
        val state = vm.state.value
        assertTrue(state.error != null)
        assertEquals(1, state.weakConceptCount)
    }
}
