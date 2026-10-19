package com.ailearningos.app.ui.home

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.ailearningos.app.ui.common.ContentState
import com.ailearningos.app.ui.common.ContentStateView
import com.ailearningos.app.ui.components.SectionCard
import com.ailearningos.app.ui.components.SessionCard
import com.ailearningos.app.ui.components.StatusRow
import com.ailearningos.app.ui.components.privacyLabel
import com.ailearningos.app.ui.components.statusColor
import com.ailearningos.app.ui.session.SessionState
import com.ailearningos.app.ui.session.SessionViewModel
import com.ailearningos.app.ui.theme.BrandStyle
import com.ailearningos.app.ui.theme.Subtle

@Composable
fun HomeScreen(
    sessionViewModel: SessionViewModel,
    onOpenLogin: () -> Unit,
    onOpenStudy: () -> Unit,
    onOpenCreateCourse: () -> Unit = {},
    onOpenVoice: () -> Unit,
    onOpenSettings: () -> Unit,
    firstLaunch: Boolean = false,
    onOpenGovernance: () -> Unit,
) {
    val sessionState by sessionViewModel.state.collectAsStateWithLifecycle()
    val sessionNotice by sessionViewModel.notice.collectAsStateWithLifecycle()

    LaunchedEffect(Unit) {
        sessionViewModel.refresh()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.Bottom,
        ) {
            Text("砚席", style = BrandStyle)
            Text("AI Learning OS", style = MaterialTheme.typography.labelMedium, color = Subtle)
        }

        if (firstLaunch) {
            WelcomeBanner(onOpenSettings = onOpenSettings)
        }

        // M15-007: 技术卡（账号/API 状态/隐私/更新/安全）全部收纳进「设置」页；
        // 首页只保留业务面：登录引导、上手引导、学习主入口。
        when (val state = sessionState) {
            is SessionState.Anonymous -> SectionCard(title = "开始使用") {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        "登录或注册后，开始你的定制学习。",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    Button(onClick = onOpenLogin, modifier = Modifier.fillMaxWidth()) {
                        Text("登录 / 注册")
                    }
                }
            }

            is SessionState.Authenticated -> OnboardingCard()

            else -> Unit
        }

        SectionCard(title = "快速入口") {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = onOpenCreateCourse, modifier = Modifier.fillMaxWidth()) {
                    Text("定制课程（说出想学的）")
                }
                OutlinedButton(onClick = onOpenStudy, modifier = Modifier.fillMaxWidth()) {
                    Text("我的课程 / 考场")
                }
                OutlinedButton(onClick = onOpenVoice, modifier = Modifier.fillMaxWidth()) {
                    Text("语音陪练")
                }
            }
        }
    }
}
