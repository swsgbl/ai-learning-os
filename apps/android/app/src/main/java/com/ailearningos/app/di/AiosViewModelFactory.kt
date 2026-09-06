package com.ailearningos.app.di

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.createSavedStateHandle
import androidx.lifecycle.viewmodel.CreationExtras
import com.ailearningos.app.ui.exam.ExamEntry
import com.ailearningos.app.ui.exam.ExamViewModel
import com.ailearningos.app.ui.home.HomeViewModel
import com.ailearningos.app.ui.login.LoginViewModel
import com.ailearningos.app.ui.review.ReviewViewModel
import com.ailearningos.app.ui.session.SessionViewModel
import com.ailearningos.app.ui.settings.SettingsViewModel
import com.ailearningos.app.ui.study.StudyViewModel

/**
 * 手工装配的 ViewModel 工厂（无 DI 框架）。
 * 路由参数（paperId/examId）经 SavedStateHandle 注入（M12-02）。
 */
class AiosViewModelFactory(
    private val container: AppContainer,
) : ViewModelProvider.Factory {

    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>, extras: CreationExtras): T {
        val handle = extras.createSavedStateHandle()
        return when {
            modelClass.isAssignableFrom(SessionViewModel::class.java) -> SessionViewModel(
                auth = container.authGateway,
                configurationBus = container.configurationBus,
            ) as T

            modelClass.isAssignableFrom(LoginViewModel::class.java) -> LoginViewModel(
                auth = container.authGateway,
            ) as T

            modelClass.isAssignableFrom(HomeViewModel::class.java) -> HomeViewModel(
                system = container.systemGateway,
            ) as T

            modelClass.isAssignableFrom(SettingsViewModel::class.java) -> SettingsViewModel(
                settingsStore = container.settingsStore,
                configurationBus = container.configurationBus,
                system = container.systemGateway,
                allowInsecureHttp = container.allowInsecureHttp,
            ) as T

            modelClass.isAssignableFrom(StudyViewModel::class.java) -> StudyViewModel(
                exam = container.examGateway,
                resumeStore = container.examResumeStore,
            ) as T

            modelClass.isAssignableFrom(ExamViewModel::class.java) -> ExamViewModel(
                exam = container.examGateway,
                resumeStore = container.examResumeStore,
                entry = examEntry(handle),
            ) as T

            modelClass.isAssignableFrom(ReviewViewModel::class.java) -> ReviewViewModel(
                exam = container.examGateway,
                examId = handle.get<String>(KEY_EXAM_ID).orEmpty(),
            ) as T

            else -> throw IllegalArgumentException("Unknown ViewModel: ${modelClass.name}")
        }
    }

    /** 考试页入口：恢复路由带 examId，开考路由带 paperId；二者缺一不可 */
    private fun examEntry(handle: SavedStateHandle): ExamEntry {
        val examId = handle.get<String>(KEY_EXAM_ID)
        val paperId = handle.get<String>(KEY_PAPER_ID)
        return when {
            !examId.isNullOrBlank() -> ExamEntry.Resume(examId)
            !paperId.isNullOrBlank() -> ExamEntry.Start(paperId)
            else -> throw IllegalArgumentException("ExamViewModel 需要 examId 或 paperId 路由参数")
        }
    }

    private companion object {
        const val KEY_EXAM_ID = "examId"
        const val KEY_PAPER_ID = "paperId"
    }
}
