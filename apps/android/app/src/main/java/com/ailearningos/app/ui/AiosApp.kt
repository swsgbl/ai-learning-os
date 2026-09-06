package com.ailearningos.app.ui

import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.ailearningos.app.ui.home.HomeScreen
import com.ailearningos.app.ui.icons.AiosIcons
import com.ailearningos.app.ui.login.LoginScreen
import com.ailearningos.app.ui.session.SessionViewModel
import com.ailearningos.app.ui.settings.SettingsScreen
import com.ailearningos.app.ui.study.StudyPlaceholderScreen
import com.ailearningos.app.ui.voice.VoicePlaceholderScreen

/**
 * M12-01 壳：单 Activity + Navigation + 底部导航（4 个主页面）。
 * 登录页不占底栏位，从用户状态卡片进入。
 */
enum class AiosDestination(val route: String, val label: String, val icon: ImageVector) {
    Home("home", "首页", Icons.Filled.Home),
    Study("study", "学习", AiosIcons.School),
    Voice("voice", "语音", AiosIcons.Mic),
    Settings("settings", "设置", Icons.Filled.Settings),
}

const val LOGIN_ROUTE = "login"

@Composable
fun AiosApp(viewModelFactory: ViewModelProvider.Factory) {
    val navController = rememberNavController()
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route

    val sessionViewModel: SessionViewModel = viewModel(factory = viewModelFactory)
    val homeViewModel: com.ailearningos.app.ui.home.HomeViewModel = viewModel(factory = viewModelFactory)
    val settingsViewModel: com.ailearningos.app.ui.settings.SettingsViewModel = viewModel(factory = viewModelFactory)
    val loginViewModel: com.ailearningos.app.ui.login.LoginViewModel = viewModel(factory = viewModelFactory)

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        bottomBar = {
            val showBottomBar = AiosDestination.entries.any { it.route == currentRoute }
            if (showBottomBar) {
                NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                    AiosDestination.entries.forEach { destination ->
                        NavigationBarItem(
                            selected = currentRoute == destination.route,
                            onClick = {
                                navController.navigate(destination.route) {
                                    popUpTo(navController.graph.findStartDestination().id) {
                                        saveState = true
                                    }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            },
                            icon = { Icon(destination.icon, contentDescription = destination.label) },
                            label = { Text(destination.label) },
                        )
                    }
                }
            }
        },
    ) { padding ->
        NavHost(
            navController = navController,
            startDestination = AiosDestination.Home.route,
            modifier = Modifier.fillMaxSize().padding(padding),
        ) {
            composable(AiosDestination.Home.route) {
                HomeScreen(
                    sessionViewModel = sessionViewModel,
                    homeViewModel = homeViewModel,
                    onOpenLogin = { navController.navigate(LOGIN_ROUTE) },
                    onOpenStudy = { navController.navigate(AiosDestination.Study.route) },
                    onOpenVoice = { navController.navigate(AiosDestination.Voice.route) },
                    onOpenSettings = { navController.navigate(AiosDestination.Settings.route) },
                )
            }
            composable(AiosDestination.Study.route) {
                StudyPlaceholderScreen(
                    onBack = { navController.popBackStack() },
                    onOpenSettings = { navController.navigate(AiosDestination.Settings.route) },
                )
            }
            composable(AiosDestination.Voice.route) {
                VoicePlaceholderScreen(
                    onBack = { navController.popBackStack() },
                    onOpenSettings = { navController.navigate(AiosDestination.Settings.route) },
                )
            }
            composable(AiosDestination.Settings.route) {
                SettingsScreen(
                    settingsViewModel = settingsViewModel,
                    sessionViewModel = sessionViewModel,
                    onOpenLogin = { navController.navigate(LOGIN_ROUTE) },
                )
            }
            composable(LOGIN_ROUTE) {
                LoginScreen(
                    loginViewModel = loginViewModel,
                    onSuccess = {
                        sessionViewModel.refresh()
                        navController.popBackStack()
                    },
                )
            }
        }
    }
}
