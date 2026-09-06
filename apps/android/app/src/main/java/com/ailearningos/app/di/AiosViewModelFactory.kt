package com.ailearningos.app.di

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import com.ailearningos.app.ui.home.HomeViewModel
import com.ailearningos.app.ui.login.LoginViewModel
import com.ailearningos.app.ui.session.SessionViewModel
import com.ailearningos.app.ui.settings.SettingsViewModel

/** 手工装配的 ViewModel 工厂（无 DI 框架） */
class AiosViewModelFactory(
    private val container: AppContainer,
) : ViewModelProvider.Factory {

    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T = when {
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

        else -> throw IllegalArgumentException("Unknown ViewModel: ${modelClass.name}")
    }
}
