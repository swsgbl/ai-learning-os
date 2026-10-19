package com.ailearningos.app.ui.settings

import androidx.compose.runtime.LaunchedEffect
import com.ailearningos.app.ui.home.HomeViewModel
import com.ailearningos.app.ui.session.SessionState
import com.ailearningos.app.ui.components.statusColor
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
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.ailearningos.app.core.BaseUrlValidation
import com.ailearningos.app.ui.common.ContentState
import com.ailearningos.app.ui.common.ContentStateView
import androidx.compose.ui.text.input.PasswordVisualTransformation
import com.ailearningos.app.ui.components.SectionCard
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import com.ailearningos.app.data.MyAiKeyInfo
import com.ailearningos.app.ui.components.SessionCard
import com.ailearningos.app.BuildConfig
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
    homeViewModel: HomeViewModel? = null,
    onOpenLogin: () -> Unit = {},
    onOpenUpdateUrl: (String) -> Unit = {},
    onOpenGovernance: () -> Unit = {},
) {
    val state by settingsViewModel.state.collectAsStateWithLifecycle()
    val sessionState by sessionViewModel.state.collectAsStateWithLifecycle()
    val sessionNotice by sessionViewModel.notice.collectAsStateWithLifecycle()
    val homeState = homeViewModel?.state?.collectAsStateWithLifecycle()?.value

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

        if (sessionState is SessionState.Authenticated) {
            MyAiKeysCard(
                state = state,
                onLoad = settingsViewModel::loadMyAiKeys,
                onSave = settingsViewModel::saveMyAiKey,
                onDelete = settingsViewModel::deleteMyAiKey,
            )
        }

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

        // M15-007: API 状态从首页收纳至此（技术管理面归设置页）
        if (homeViewModel != null && homeState != null) {
            SectionCard(title = "API 状态") {
                LaunchedEffect(Unit) { homeViewModel.refresh() }
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
        }

        // 治理入口：本地免鉴权或已登录管理员（与后端治理准入一致）——从首页收纳至此
        val governanceVisible = when (val session = sessionState) {
            SessionState.AuthDisabled -> true
            is SessionState.Authenticated -> session.user.isAdmin
            else -> false
        }
        if (governanceVisible) {
            OutlinedButton(onClick = onOpenGovernance, modifier = Modifier.fillMaxWidth()) {
                Text("治理 / 发布")
            }
        }

        SectionCard(title = "应用更新") {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "当前版本 ${BuildConfig.VERSION_NAME}",
                    style = MaterialTheme.typography.bodyMedium,
                )
                OutlinedButton(onClick = settingsViewModel::checkUpdate) {
                    Text("检查更新")
                }
            }
            when (val check = state.updateCheck) {
                is ContentState.Empty -> Text(
                    "手动检查应用更新；需要已连接服务器。",
                    style = MaterialTheme.typography.bodySmall,
                    color = Subtle,
                )

                is ContentState.Loading -> Text("正在检查更新…", style = MaterialTheme.typography.bodyMedium)

                is ContentState.Error -> Text(
                    check.message,
                    style = MaterialTheme.typography.bodyMedium,
                    color = Warn,
                )

                is ContentState.Success -> when (val result = check.value) {
                    is UpdateCheckResult.NotConfigured -> Text(
                        "服务端未配置版本信息（自托管服务器可在部署时注入）。",
                        style = MaterialTheme.typography.bodyMedium,
                        color = Subtle,
                    )

                    is UpdateCheckResult.UpToDate -> Text(
                        "已是最新版本（${result.currentVersionName}）。",
                        style = MaterialTheme.typography.bodyMedium,
                    )

                    is UpdateCheckResult.Available -> Column(
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Text(
                            "发现新版本 ${result.version}，下载安装包后覆盖安装即可更新。",
                            style = MaterialTheme.typography.bodyMedium,
                        )
                        Button(onClick = { onOpenUpdateUrl(result.downloadUrl) }) {
                            Text("下载更新")
                        }
                        if (result.downloadUrlFallback != null) {
                            TextButton(onClick = { onOpenUpdateUrl(result.downloadUrlFallback) }) {
                                Text("备用下载地址")
                            }
                        }
                    }
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


@Composable
private fun MyAiKeysCard(
    state: SettingsUiState,
    onLoad: () -> Unit,
    onSave: (String, String) -> Unit,
    onDelete: (String) -> Unit,
) {
    LaunchedEffect(Unit) { onLoad() }
    SectionCard(title = "我的 AI 密钥") {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                "自带云端密钥（BYOK）：加密保存、仅自己的请求使用；不填则用服务端配置。" +
                    "云语音密钥立即生效；LLM 密钥保存后随后续版本生效。",
                style = MaterialTheme.typography.bodySmall,
                color = Subtle,
            )
            when (val keys = state.myAiKeys) {
                is ContentState.Success -> keys.value.forEach { key -> MyAiKeyRow(key, onSave, onDelete) }
                is ContentState.Error -> Text(keys.message, color = Warn, style = MaterialTheme.typography.bodySmall)
                else -> Text("加载中…", style = MaterialTheme.typography.bodySmall, color = Subtle)
            }
            state.aiKeyNotice?.let { notice ->
                Text(notice, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary)
            }
        }
    }
}

@Composable
private fun MyAiKeyRow(
    key: MyAiKeyInfo,
    onSave: (String, String) -> Unit,
    onDelete: (String) -> Unit,
) {
    val label = when (key.slot) {
        "llm" -> "LLM（判分/生成）"
        "asr_cloud" -> "云语音识别"
        "tts_cloud" -> "云语音合成"
        else -> key.slot
    }
    var input by remember(key.slot) { mutableStateOf("") }
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(label, style = MaterialTheme.typography.bodyMedium)
            Text(
                if (key.configured) "已配置 ····${key.last4}" else "未配置",
                style = MaterialTheme.typography.bodySmall,
                color = if (key.configured) MaterialTheme.colorScheme.primary else Subtle,
            )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(
                value = input,
                onValueChange = { input = it },
                placeholder = { Text("粘贴密钥（不回显）", style = MaterialTheme.typography.bodySmall) },
                visualTransformation = PasswordVisualTransformation(),
                singleLine = true,
                modifier = Modifier.fillMaxWidth(0.65f),
            )
            Button(
                onClick = { onSave(key.slot, input); input = "" },
                enabled = input.length >= 8,
            ) { Text("保存") }
            if (key.configured) {
                TextButton(onClick = { onDelete(key.slot) }) { Text("删除") }
            }
        }
    }
}
