package com.ailearningos.app.ui

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.testutil.FakeExamGateway
import com.ailearningos.app.testutil.FakeExamResumeStore
import com.ailearningos.app.testutil.MainDispatcherRule
import com.ailearningos.app.testutil.paperFixture
import com.ailearningos.app.ui.common.ContentState
import com.ailearningos.app.ui.study.StudyViewModel
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/** 学习首屏：试卷列表四态 + 恢复提示（只透出 exam_id） */
class StudyViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private lateinit var gateway: FakeExamGateway
    private lateinit var resumeStore: FakeExamResumeStore
    private lateinit var viewModel: StudyViewModel

    @Before
    fun setUp() {
        gateway = FakeExamGateway()
        resumeStore = FakeExamResumeStore()
        viewModel = StudyViewModel(exam = gateway, resumeStore = resumeStore)
    }

    @Test
    fun `loads papers into success state`() = runTest {
        val state = viewModel.state.value
        assertTrue(state.papers is ContentState.Success)
        assertEquals(1, (state.papers as ContentState.Success).value.size)
        assertEquals("functions-basics", (state.papers as ContentState.Success).value[0].id)
    }

    @Test
    fun `empty paper list maps to empty state not error`() = runTest {
        gateway.papersResult = Result.success(emptyList())
        viewModel.refresh()
        assertTrue(viewModel.state.value.papers is ContentState.Empty)
    }

    @Test
    fun `papers failure maps to error state with retry`() = runTest {
        gateway.papersResult = Result.failure(AppError(AppErrorKind.NETWORK))
        viewModel.refresh()
        val errorState = viewModel.state.value.papers
        assertTrue(errorState is ContentState.Error)
        assertEquals("无法连接 API，请检查网络后重试", (errorState as ContentState.Error).message)

        gateway.papersResult = Result.success(listOf(paperFixture()))
        viewModel.refresh()
        assertTrue(viewModel.state.value.papers is ContentState.Success)
    }

    @Test
    fun `resume exam id surfaced as hint only`() = runTest {
        resumeStore.seed("exam-pending-1")
        viewModel.refresh()
        assertEquals("exam-pending-1", viewModel.state.value.resumeExamId)
    }

    @Test
    fun `no resume record shows no hint`() = runTest {
        assertNull(viewModel.state.value.resumeExamId)
    }

    @Test
    fun `resume store failure does not break paper list`() = runTest {
        val brokenStore = object : com.ailearningos.app.data.local.ExamResumeStore {
            override suspend fun load(): String = throw AppError(AppErrorKind.STORAGE)
            override suspend fun save(examId: String) = Unit
            override suspend fun clear() = Unit
        }
        val model = StudyViewModel(exam = gateway, resumeStore = brokenStore)
        assertTrue(model.state.value.papers is ContentState.Success)
        assertNull(model.state.value.resumeExamId)
    }
}
