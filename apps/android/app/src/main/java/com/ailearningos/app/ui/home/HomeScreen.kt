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
import com.ailearningos.app.ui.session.SessionViewModel
import com.ailearningos.app.ui.theme.BrandStyle
import com.ailearningos.app.ui.theme.Subtle

@Composable
fun HomeScreen(
    sessionViewModel: SessionViewModel,
    homeViewModel: HomeViewModel,
    onOpenLogin: () -> Unit,
    onOpenStudy: () -> Unit,
    onOpenVoice: () -> Unit,
    onOpenSettings: () -> Unit,
) {
    val sessionState by sessionViewModel.state.collectAsStateWithLifecycle()
    val sessionNotice by sessionViewModel.notice.collectAsStateWithLifecycle()
    val homeState by homeViewModel.state.collectAsStateWithLifecycle()

    LaunchedEffect(Unit) {
        homeViewModel.refresh()
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

        SessionCard(
            state = sessionState,
            notice = sessionNotice,
            onNoticeDismissed = sessionViewModel::clearNotice,
            onLogin = onOpenLogin,
            onLogout = sessionViewModel::logout,
            onRetry = sessionViewModel::refresh,
        )

        SectionCard(title = "API 状态") {
            ContentStateView(
                state = homeState,
                onRetry = homeViewModel::refresh,
                loadingText = "正在探测 API…",
            ) { snapshot ->
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    StatusRow(
                        label = "服务（/health）",
                        value = if (snapshot.apiServiceOk) "正常" else "响应异常",
                        valueColor = statusColor(snapshot.apiServiceOk),
                    )
                    when (val privacy = snapshot.privacy) {
                        is ContentState.Success -> {
                            StatusRow("模型路由", privacyLabel(privacy.value.modelRoute))
                            StatusRow("语音", privacyLabel(privacy.value.voiceMode))
                            StatusRow("检索", privacyLabel(privacy.value.searchMode))
                            StatusRow("保存语音录音", if (privacy.value.storesAudio) "是" else "否")
                            StatusRow("上下文出站云端", if (privacy.value.sendsContextToCloud) "是" else "否")
                        }

                        is ContentState.Error -> Text(
                            privacy.message,
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.error,
                        )

                        is ContentState.Loading -> Text(
                            "隐私路由读取中…",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )

                        is ContentState.Empty -> Text(
                            "暂无隐私路由信息",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }

        SectionCard(title = "功能入口") {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = onOpenStudy, modifier = Modifier.fillMaxWidth()) {
                    Text("学习 / 考场（占位）")
                }
                OutlinedButton(onClick = onOpenVoice, modifier = Modifier.fillMaxWidth()) {
                    Text("语音陪练（占位）")
                }
                OutlinedButton(onClick = onOpenSettings, modifier = Modifier.fillMaxWidth()) {
                    Text("设置 / API 配置")
                }
            }
        }

        Text(
            "第一切片只包含应用壳与认证/API 基础：考试、语音、检索、治理业务尚未接入。",
            style = MaterialTheme.typography.bodySmall,
            color = Subtle,
        )
    }
}
