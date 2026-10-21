package com.ailearningos.app.ui

import android.content.Intent
import android.net.Uri
import androidx.compose.ui.platform.LocalContext
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.List
import androidx.compose.material.icons.filled.Search
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
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.ailearningos.app.ui.exam.ExamScreen
import com.ailearningos.app.ui.governance.GovernanceScreen
import com.ailearningos.app.ui.governance.GovernanceViewModel
import com.ailearningos.app.ui.home.HomeScreen
import com.ailearningos.app.ui.icons.AiosIcons
import com.ailearningos.app.ui.login.LoginScreen
import com.ailearningos.app.ui.review.ReviewScreen
import com.ailearningos.app.ui.search.SearchScreen
import com.ailearningos.app.ui.search.SearchViewModel
import com.ailearningos.app.ui.session.SessionViewModel
import com.ailearningos.app.ui.settings.SettingsScreen
import com.ailearningos.app.ui.create.CreateCourseScreen
import com.ailearningos.app.ui.progress.ProgressScreen
import com.ailearningos.app.ui.progress.ProgressViewModel
import com.ailearningos.app.ui.create.CreateCourseViewModel
import com.ailearningos.app.ui.study.StudyScreen
import com.ailearningos.app.ui.study.StudyViewModel
import com.ailearningos.app.ui.voice.VoiceScreen

/**
 * 壳 + 考试/语音/搜索业务导航（M12-02/M12-03/M12-04）：单 Activity + Navigation + 底部导航。
 * 登录页/考场/审阅不占底栏位；考场、审阅与语音陪练流程页为全屏。
 */
enum class AiosDestination(val route: String, val label: String, val icon: ImageVector) {
    Home("home", "首页", Icons.Filled.Home),
    Study("study", "学习", AiosIcons.School),
    Progress("progress", "工作台", Icons.Filled.List),
    Search("search", "搜索", Icons.Filled.Search),
    Voice("voice", "语音", AiosIcons.Mic),
    Settings("settings", "设置", Icons.Filled.Settings),
}

/** M15-022：底栏展示集（搜索移至首页搜索框进入，工作台入底栏——与 Web 对齐） */
val bottomBarDestinations: List<AiosDestination> = listOf(
    AiosDestination.Home,
    AiosDestination.Study,
    AiosDestination.Progress,
    AiosDestination.Voice,
    AiosDestination.Settings,
)

const val LOGIN_ROUTE = "login"
const val EXAM_PAPER_ROUTE = "exam-paper/{paperId}"
const val EXAM_RESUME_ROUTE = "exam/{examId}"
const val REVIEW_ROUTE = "review/{examId}"
const val STUDY_MODE_ROUTE = "study-mode/{paperId}"
const val GOVERNANCE_ROUTE = "governance"
const val CREATE_COURSE_ROUTE = "create-course"
const val VOICE_PAPER_ROUTE = "voice-paper/{paperId}"
const val VOICE_SESSION_ROUTE = "voice-session/{sessionId}"

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
            val showBottomBar = bottomBarDestinations.any { it.route == currentRoute }
            if (showBottomBar) {
                NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                    bottomBarDestinations.forEach { destination ->
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
                val settingsState by settingsViewModel.state.collectAsStateWithLifecycle()
                HomeScreen(
                    sessionViewModel = sessionViewModel,
                    // M15-004: 从未保存过服务器地址 = 首次使用 → 首页引导横幅
                    firstLaunch = settingsState.effectiveBaseUrl == null,
                    onOpenLogin = { navController.navigate(LOGIN_ROUTE) },
                    onOpenStudy = { navController.navigate(AiosDestination.Study.route) },
                    onOpenCreateCourse = { navController.navigate(CREATE_COURSE_ROUTE) },
                    onOpenSearch = { navController.navigate(AiosDestination.Search.route) },
                    onOpenVoice = { navController.navigate(AiosDestination.Voice.route) },
                    onOpenSettings = { navController.navigate(AiosDestination.Settings.route) },
                    onOpenGovernance = {
                        navController.navigate(GOVERNANCE_ROUTE) { launchSingleTop = true }
                    },
                )
            }
            composable(CREATE_COURSE_ROUTE) {
                val vm: CreateCourseViewModel = viewModel(factory = viewModelFactory)
                CreateCourseScreen(
                    viewModel = vm,
                    onOpenStudy = { navController.navigate(AiosDestination.Study.route) },
                )
            }
            composable(AiosDestination.Study.route) {
                val studyViewModel: StudyViewModel = viewModel(factory = viewModelFactory)
                StudyScreen(
                    viewModel = studyViewModel,
                    onStartPaper = { paperId -> navController.navigateExamPaper(paperId) },
                    onResumeExam = { examId -> navController.navigateExamResume(examId) },
                    onOpenReview = { examId -> navController.navigate("review/$examId") },
                    onStartVoicePaper = { paperId -> navController.navigateVoicePaper(paperId) },
                    onOpenStudyMode = { paperId -> navController.navigate("study-mode/$paperId") },
                )
            }
            composable(AiosDestination.Progress.route) {
                val progressVm: ProgressViewModel = viewModel(factory = viewModelFactory)
                ProgressScreen(
                    viewModel = progressVm,
                    onOpenStudy = { navController.navigate(AiosDestination.Study.route) },
                )
            }
            composable(AiosDestination.Search.route) {
                val searchViewModel: SearchViewModel = viewModel(factory = viewModelFactory)
                SearchScreen(viewModel = searchViewModel)
            }
            composable(AiosDestination.Voice.route) {
                val voiceViewModel: com.ailearningos.app.ui.voice.VoiceViewModel =
                    viewModel(factory = viewModelFactory)
                VoiceScreen(
                    viewModel = voiceViewModel,
                    onBack = { navController.popBackStack() },
                    onOpenVoiceSession = { sessionId -> navController.navigateVoiceSession(sessionId) },
                )
            }
            composable(VOICE_PAPER_ROUTE) {
                val voiceViewModel: com.ailearningos.app.ui.voice.VoiceViewModel =
                    viewModel(factory = viewModelFactory)
                VoiceScreen(
                    viewModel = voiceViewModel,
                    onBack = { navController.popBackStack() },
                    onOpenVoiceSession = { sessionId -> navController.navigateVoiceSession(sessionId) },
                )
            }
            composable(VOICE_SESSION_ROUTE) {
                val voiceViewModel: com.ailearningos.app.ui.voice.VoiceViewModel =
                    viewModel(factory = viewModelFactory)
                VoiceScreen(
                    viewModel = voiceViewModel,
                    onBack = { navController.popBackStack() },
                    onOpenVoiceSession = { sessionId -> navController.navigateVoiceSession(sessionId) },
                )
            }
            composable(AiosDestination.Settings.route) {
                val appContext = LocalContext.current
                SettingsScreen(
                    settingsViewModel = settingsViewModel,
                    sessionViewModel = sessionViewModel,
                    homeViewModel = homeViewModel,
                    onOpenGovernance = {
                        navController.navigate(GOVERNANCE_ROUTE) { launchSingleTop = true }
                    },
                    onOpenLogin = { navController.navigate(LOGIN_ROUTE) },
                    // M15-006 更新下载：系统浏览器打开分发地址（APK 由用户确认安装，
                    // 应用不做静默下载/安装——无 INSTALL_PACKAGES 权限诉求）
                    onOpenUpdateUrl = { url ->
                        appContext.startActivity(
                            Intent(Intent.ACTION_VIEW, Uri.parse(url)).apply {
                                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                            },
                        )
                    },
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
            composable(EXAM_PAPER_ROUTE) {
                val examViewModel: com.ailearningos.app.ui.exam.ExamViewModel =
                    viewModel(factory = viewModelFactory)
                ExamScreen(
                    viewModel = examViewModel,
                    onOpenReview = { examId -> navController.navigateReviewFromExam(examId) },
                    onBack = { navController.popBackStack() },
                )
            }
            composable(EXAM_RESUME_ROUTE) {
                val examViewModel: com.ailearningos.app.ui.exam.ExamViewModel =
                    viewModel(factory = viewModelFactory)
                ExamScreen(
                    viewModel = examViewModel,
                    onOpenReview = { examId -> navController.navigateReviewFromExam(examId) },
                    onBack = { navController.popBackStack() },
                )
            }
            composable(STUDY_MODE_ROUTE) { backStackEntry ->
                val paperId = backStackEntry.arguments?.getString("paperId") ?: return@composable
                val vm: com.ailearningos.app.ui.study.StudyModeViewModel = viewModel(factory = viewModelFactory)
                com.ailearningos.app.ui.study.StudyModeScreen(
                    viewModel = vm,
                    paperId = paperId,
                    onStartPaperExam = { navController.navigateExamPaper(paperId) },
                    onBack = { navController.popBackStack() },
                )
            }
            composable(REVIEW_ROUTE) {
                val reviewViewModel: com.ailearningos.app.ui.review.ReviewViewModel =
                    viewModel(factory = viewModelFactory)
                ReviewScreen(
                    viewModel = reviewViewModel,
                    onBack = { navController.popBackStack() },
                )
            }
            composable(GOVERNANCE_ROUTE) {
                val governanceViewModel: GovernanceViewModel =
                    viewModel(factory = viewModelFactory)
                GovernanceScreen(
                    viewModel = governanceViewModel,
                    sessionViewModel = sessionViewModel,
                    onBack = { navController.popBackStack() },
                )
            }
        }
    }
}

private fun NavHostController.navigateExamPaper(paperId: String) {
    navigate("exam-paper/$paperId") { launchSingleTop = true }
}

private fun NavHostController.navigateExamResume(examId: String) {
    navigate("exam/$examId") { launchSingleTop = true }
}

private fun NavHostController.navigateVoicePaper(paperId: String) {
    navigate("voice-paper/$paperId") { launchSingleTop = true }
}

private fun NavHostController.navigateVoiceSession(sessionId: String) {
    navigate("voice-session/$sessionId") { launchSingleTop = true }
}

/** 交卷后进审阅：清掉考场页（不能回退到已交卷的考场） */
private fun NavHostController.navigateReviewFromExam(examId: String) {
    navigate("review/$examId") {
        launchSingleTop = true
        popUpTo(AiosDestination.Study.route)
    }
}
