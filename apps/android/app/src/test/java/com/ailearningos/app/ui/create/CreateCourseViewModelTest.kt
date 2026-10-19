package com.ailearningos.app.ui.create

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
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
class CreateCourseViewModelTest {

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
    fun `generate 成功透出章节方案`() = runTest(dispatcher) {
        val vm = CreateCourseViewModel(FakeSystemGateway())
        vm.onGoalChange("我想学 SQL 数据分析")
        vm.generate()
        val draft = vm.state.value.draft
        assertTrue(draft is ContentState.Success)
        draft as ContentState.Success
        assertEquals(1, draft.value.chapters.size)
        assertEquals("SELECT 基础查询", draft.value.chapters[0].title)
    }

    @Test
    fun `generate 422 无匹配透出服务端支持主题文案`() = runTest(dispatcher) {
        val system = FakeSystemGateway()
        system.generateCourseResult = kotlin.Result.failure(
            IllegalStateException("学习目标未命中概念。当前已发布概念图支持的主题：SELECT 基础查询、表连接 JOIN")
        )
        val vm = CreateCourseViewModel(system)
        vm.onGoalChange("我想学天体物理")
        vm.generate()
        val draft = vm.state.value.draft
        assertTrue(draft is ContentState.Error)
        assertTrue((draft as ContentState.Error).message.contains("支持的主题"))
    }

    @Test
    fun `materialize 成功产出私有卷引导跳学习页`() = runTest(dispatcher) {
        val vm = CreateCourseViewModel(FakeSystemGateway())
        vm.onGoalChange("学 SQL")
        vm.generate()
        vm.materialize()
        assertEquals("paper_1", vm.state.value.createdPaper?.id)
    }
}
