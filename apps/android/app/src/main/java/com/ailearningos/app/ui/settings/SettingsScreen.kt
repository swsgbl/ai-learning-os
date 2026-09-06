package com.ailearningos.app.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.ailearningos.app.core.BaseUrlValidation
import com.ailearningos.app.ui.common.ContentState
import com.ailearningos.app.ui.common.ContentStateView
import com.ailearningos.app.ui.components.SectionCard
import com.ailearningos.app.ui.components.SessionCard
import com.ailearningos.app.ui.components.StatusRow
import com.ailearningos.app.ui.components.privacyLabel
import com.ailearningos.app.ui.session.SessionViewModel
import com.ailearningos.app.ui.theme.Subtle
import com.ailearningos.app.ui.theme.Warn

/** 设置页：API 地址配置、当前账号、隐私路由、安全边界说明 */
@Composable
fun SettingsScreen(
    settingsViewModel: SettingsViewModel,
    sessionViewModel: SessionViewModel,
    onOpenLogin: () -> Unit,
) {
    val state by settingsViewModel.state.collectAsStateWithLifecycle()
    val sessionState by sessionViewModel.state.collectAsStateWithLifecycle()
    val sessionNotice by sessionViewModel.notice.collectAsStateWithLifecycle()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("设置", style = MaterialTheme.typography.titleLarge)

        SectionCard(title = "API 地址") {
            if (state.effectiveBaseUrl != null) {
                StatusRow("当前生效", state.effectiveBaseUrl.orEmpty())
            } else {
                Text(
                    "尚未配置，将使用默认调试地址。",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            OutlinedTextField(
                value = state.baseUrlInput,
                onValueChange = settingsViewModel::onBaseUrlChange,
                label = { Text("API base URL") },
                singleLine = true,
                enabled = !state.saving,
                isError = state.validation is BaseUrlValidation.Invalid,
                supportingText = {
                    val hint = when (val validation = state.validation) {
                        null ->
                            if (state.allowInsecureHttp) {
                                "调试构建允许本机回环/局域网 http 地址（例如模拟器访问宿主机的 10.0.2.2）"
                            } else {
                                "发布构建仅接受 https 地址"
                            }

                        is BaseUrlValidation.Valid -> "地址有效：${validation.normalized}"
                        is BaseUrlValidation.Invalid ->
                            settingsViewModel.messageFor(validation.problem)
                    }
                    Text(
                        hint,
                        color = if (state.validation is BaseUrlValidation.Invalid) {
                            MaterialTheme.colorScheme.error
                        } else {
                            Subtle
                        },
                    )
                },
                modifier = Modifier.fillMaxWidth(),
            )

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.End,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Button(onClick = settingsViewModel::saveBaseUrl, enabled = !state.saving) {
                    if (state.saving) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(16.dp),
                            strokeWidth = 2.dp,
                            color = MaterialTheme.colorScheme.onPrimary,
                        )
                    } else {
                        Text("保存")
                    }
                }
            }

            state.notice?.let { notice ->
                Text(notice, style = MaterialTheme.typography.bodySmall, color = Warn)
            }
        }

        SessionCard(
            state = sessionState,
            notice = sessionNotice,
            onNoticeDismissed = sessionViewModel::clearNotice,
            onLogin = onOpenLogin,
            onLogout = sessionViewModel::logout,
            onRetry = sessionViewModel::refresh,
        )

        SectionCard(title = "隐私路由") {
            ContentStateView(
                state = state.privacy,
                onRetry = settingsViewModel::refreshPrivacy,
                emptyText = "尚未获取隐私路由信息",
                retryText = "获取",
                loadingText = "正在获取…",
            ) { privacy ->
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    StatusRow("模型路由", privacyLabel(privacy.modelRoute))
                    StatusRow("语音", privacyLabel(privacy.voiceMode))
                    StatusRow("检索", privacyLabel(privacy.searchMode))
                    StatusRow("保存语音录音", if (privacy.storesAudio) "是" else "否")
                    StatusRow("上下文出站云端", if (privacy.sendsContextToCloud) "是" else "否")
                }
            }
        }

        SectionCard(title = "数据与安全") {
            Text(
                "登录令牌只保存在本机加密存储（Android Keystore AES-GCM），不写入明文配置。",
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(
                "应用不保存密码；退出登录只清理本机令牌与用户状态。",
                style = MaterialTheme.typography.bodyMedium,
            )
            Text(
                "调试与发布构建对 API 地址的协议要求不同：发布构建强制 https。",
                style = MaterialTheme.typography.bodySmall,
                color = Subtle,
            )
        }
    }
}
