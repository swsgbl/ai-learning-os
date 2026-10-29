package com.ailearningos.app.ui.settings

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.ailearningos.app.BuildConfig
import com.ailearningos.app.core.BaseUrlValidation
import com.ailearningos.app.data.remote.LlmProfile
import com.ailearningos.app.ui.common.ContentState
import com.ailearningos.app.ui.common.ContentStateView
import com.ailearningos.app.ui.components.SectionCard
import com.ailearningos.app.ui.components.SessionCard
import com.ailearningos.app.ui.components.StatusRow
import com.ailearningos.app.ui.components.privacyLabel
import com.ailearningos.app.ui.components.statusColor
import com.ailearningos.app.ui.home.HomeViewModel
import com.ailearningos.app.ui.session.SessionState
import com.ailearningos.app.ui.session.SessionViewModel
import com.ailearningos.app.ui.theme.Subtle
import com.ailearningos.app.ui.theme.Warn

/** M15-025 设置子页（菜单式结构：根列表点击进入子页，返回键回根） */
private enum class SettingsPage {
    ROOT,
    AI_KEYS,
    API_ADDRESS,
    PRIVACY,
    API_STATUS,
    UPDATE,
    SECURITY,
}

/**
 * 设置页（M15-025 菜单式重构）：根页 = 账号卡片 + 设置项列表（点击进入
 * 子页展开），替代此前所有区块堆叠一页的形态——用户实测反馈「应做成
 * 列表，点开某一个设置页再展开」。子页内返回键/「‹ 返回」回根页。
 */
@Composable
fun SettingsScreen(
    settingsViewModel: SettingsViewModel,
    sessionViewModel: SessionViewModel,
    homeViewModel: HomeViewModel? = null,
    onOpenLogin: () -> Unit = {},
    onOpenUpdateUrl: (String) -> Unit = {},
    onOpenGovernance: () -> Unit = {},
) {
    val context = LocalContext.current
    val state by settingsViewModel.state.collectAsStateWithLifecycle()
    val sessionState by sessionViewModel.state.collectAsStateWithLifecycle()
    val sessionNotice by sessionViewModel.notice.collectAsStateWithLifecycle()
    val homeState = homeViewModel?.state?.collectAsStateWithLifecycle()?.value

    var page by remember { mutableStateOf(SettingsPage.ROOT) }
    BackHandler(enabled = page != SettingsPage.ROOT) { page = SettingsPage.ROOT }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        when (page) {
            SettingsPage.ROOT -> SettingsRootPage(
                state = state,
                sessionState = sessionState,
                sessionNotice = sessionNotice,
                homeViewModel = homeViewModel,
                onNoticeDismissed = sessionViewModel::clearNotice,
                onLogin = onOpenLogin,
                onLogout = sessionViewModel::logout,
                onSessionRetry = sessionViewModel::refresh,
                onOpenGovernance = onOpenGovernance,
                onOpenPage = { page = it },
            )

            SettingsPage.AI_KEYS -> SubPage(title = "我的 AI 密钥（多厂商）", onBack = { page = SettingsPage.ROOT }) {
                AiKeysPage(
                    state = state,
                    onLoad = settingsViewModel::loadMyAiKeys,
                    onAddProfile = settingsViewModel::addLlmProfile,
                    onSwitchProfile = settingsViewModel::switchLlmProfile,
                    onRemoveProfile = settingsViewModel::removeLlmProfile,
                    onProbeModels = settingsViewModel::probeLlmModels,
                    onModelPicked = settingsViewModel::consumeLlmModels,
                )
            }

            SettingsPage.API_ADDRESS -> SubPage(title = "API 地址", onBack = { page = SettingsPage.ROOT }) {
                ApiAddressSection(
                    state = state,
                    onBaseUrlChange = settingsViewModel::onBaseUrlChange,
                    onSave = settingsViewModel::saveBaseUrl,
                    messageFor = settingsViewModel::messageFor,
                )
            }

            SettingsPage.PRIVACY -> SubPage(title = "隐私路由", onBack = { page = SettingsPage.ROOT }) {
                PrivacySection(state = state, onRetry = settingsViewModel::refreshPrivacy)
            }

            SettingsPage.API_STATUS -> SubPage(title = "API 状态", onBack = { page = SettingsPage.ROOT }) {
                if (homeViewModel != null && homeState != null) {
                    ApiStatusSection(
                        homeViewModel = homeViewModel,
                        homeState = homeState,
                    )
                }
            }

            SettingsPage.UPDATE -> SubPage(title = "应用更新", onBack = { page = SettingsPage.ROOT }) {
                UpdateSection(
                    state = state,
                    onCheckUpdate = settingsViewModel::checkUpdate,
                    onStartApkDownload = { available ->
                        settingsViewModel.startApkDownload(available, context.cacheDir)
                    },
                    onInstallApk = { settingsViewModel.installDownloadedApk(context) },
                    onOpenUpdateUrl = onOpenUpdateUrl,
                )
            }

            SettingsPage.SECURITY -> SubPage(title = "数据与安全", onBack = { page = SettingsPage.ROOT }) {
                SecuritySection()
            }
        }
    }
}

/** 根页：账号卡片 + 设置菜单列表 */
@Composable
private fun SettingsRootPage(
    state: SettingsUiState,
    sessionState: SessionState,
    sessionNotice: String?,
    homeViewModel: HomeViewModel?,
    onNoticeDismissed: () -> Unit,
    onLogin: () -> Unit,
    onLogout: () -> Unit,
    onSessionRetry: () -> Unit,
    onOpenGovernance: () -> Unit,
    onOpenPage: (SettingsPage) -> Unit,
) {
    Text("设置", style = MaterialTheme.typography.titleLarge)

    SessionCard(
        state = sessionState,
        notice = sessionNotice,
        onNoticeDismissed = onNoticeDismissed,
        onLogin = onLogin,
        onLogout = onLogout,
        onRetry = onSessionRetry,
    )

    val authenticated = sessionState is SessionState.Authenticated
    val governanceVisible = when (sessionState) {
        SessionState.AuthDisabled -> true
        is SessionState.Authenticated -> sessionState.user.isAdmin
        else -> false
    }

    SettingsMenuCard {
        MenuRow(
            title = "我的 AI 密钥（多厂商）",
            subtitle = aiKeysSubtitle(state, authenticated),
            onClick = { if (authenticated) onOpenPage(SettingsPage.AI_KEYS) },
            enabled = authenticated,
        )
        HorizontalDivider()
        MenuRow(
            title = "API 地址",
            subtitle = state.effectiveBaseUrl ?: "未配置（使用默认调试地址）",
            onClick = { onOpenPage(SettingsPage.API_ADDRESS) },
        )
        HorizontalDivider()
        MenuRow(
            title = "隐私路由",
            subtitle = "模型 / 语音 / 检索的路由与出站策略",
            onClick = { onOpenPage(SettingsPage.PRIVACY) },
        )
        if (homeViewModel != null) {
            HorizontalDivider()
            MenuRow(
                title = "API 状态",
                subtitle = "服务健康探测",
                onClick = { onOpenPage(SettingsPage.API_STATUS) },
            )
        }
        if (governanceVisible) {
            HorizontalDivider()
            MenuRow(
                title = "治理 / 发布",
                subtitle = "管理员：版本与内容治理",
                onClick = onOpenGovernance,
            )
        }
        HorizontalDivider()
        MenuRow(
            title = "应用更新",
            subtitle = "当前版本 ${BuildConfig.VERSION_NAME}",
            onClick = { onOpenPage(SettingsPage.UPDATE) },
        )
        HorizontalDivider()
        MenuRow(
            title = "数据与安全",
            subtitle = "令牌存储与边界说明",
            onClick = { onOpenPage(SettingsPage.SECURITY) },
        )
    }

    if (!authenticated && sessionState != SessionState.AuthDisabled) {
        Text(
            "登录后可配置个人 AI 密钥（多厂商切换）。",
            style = MaterialTheme.typography.bodySmall,
            color = Subtle,
        )
    }
}

/** 菜单容器（一张卡内多行） */
@Composable
private fun SettingsMenuCard(content: @Composable () -> Unit) {
    Surface(
        shape = MaterialTheme.shapes.medium,
        color = MaterialTheme.colorScheme.surface,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(vertical = 4.dp)) { content() }
    }
}

/** 菜单行：标题 + 副标题 + 「›」 */
@Composable
private fun MenuRow(
    title: String,
    subtitle: String,
    onClick: () -> Unit,
    enabled: Boolean = true,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = enabled) { onClick() }
            .padding(horizontal = 16.dp, vertical = 12.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                title,
                style = MaterialTheme.typography.bodyLarge,
                color = if (enabled) {
                    MaterialTheme.colorScheme.onSurface
                } else {
                    MaterialTheme.colorScheme.onSurfaceVariant
                },
            )
            Text(subtitle, style = MaterialTheme.typography.bodySmall, color = Subtle)
        }
        Text("›", style = MaterialTheme.typography.titleLarge, color = Subtle)
    }
}

/** 子页骨架：返回行 + 标题 + 内容 */
@Composable
private fun SubPage(title: String, onBack: () -> Unit, content: @Composable () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            TextButton(onClick = onBack, contentPadding = PaddingValues(0.dp)) {
                Text("‹ 返回")
            }
            Text(title, style = MaterialTheme.typography.titleLarge)
        }
        content()
    }
}

private fun aiKeysSubtitle(state: SettingsUiState, authenticated: Boolean): String = when {
    !authenticated -> "登录后可配置"
    state.llmProfiles.isEmpty() -> "未配置厂商（将用服务端默认模型）"
    else -> {
        val active = state.llmProfiles.firstOrNull { it.active }
        "已配置 ${state.llmProfiles.size} 家" + (active?.let { " · 使用中：${it.label}" } ?: "")
    }
}

// ---------------------------------------------------------------- AI 密钥子页

/** 预设厂商（M15-025：固定列表 + 状态徽章——支持哪些、配了哪些一目了然） */
private data class LlmPreset(val label: String, val endpoint: String, val matchKeys: List<String>)

private val LLM_PRESETS = listOf(
    LlmPreset("DeepSeek", "https://api.deepseek.com/v1", listOf("deepseek")),
    LlmPreset("OpenAI", "https://api.openai.com/v1", listOf("openai", "gpt")),
    LlmPreset("智谱 GLM", "https://open.bigmodel.cn/api/paas/v4", listOf("智谱", "glm", "bigmodel")),
    LlmPreset("Kimi（月之暗面）", "https://api.moonshot.cn/v1", listOf("kimi", "moonshot", "月之暗面")),
    LlmPreset("Agnes Hub", "https://apihub.agnes-ai.com/v1", listOf("agnes")),
    LlmPreset("硅基流动", "https://api.siliconflow.cn/v1", listOf("siliconflow", "硅基")),
    LlmPreset("自定义厂商", "", emptyList()),
)

/** profile 归属预设（按名称关键词匹配；无命中 = 自定义） */
private fun presetFor(profile: LlmProfile): LlmPreset? =
    LLM_PRESETS.firstOrNull { preset ->
        preset.matchKeys.any { key -> profile.label.lowercase().contains(key) }
    }

/**
 * AI 密钥子页（M15-025 重构）：支持的全部厂商列成固定列表，各家状态
 * （使用中 / 已配置 / 未配置）清晰展示；点开某厂商展开配置表单，
 * 模型名可在线获取列表后选择。已配置可随时切换/删除。
 */
@Composable
private fun AiKeysPage(
    state: SettingsUiState,
    onLoad: () -> Unit,
    onAddProfile: (String, String, String?, String?, Boolean) -> Unit,
    onSwitchProfile: (String) -> Unit,
    onRemoveProfile: (String) -> Unit,
    onProbeModels: (String, String) -> Unit,
    onModelPicked: () -> Unit,
) {
    LaunchedEffect(Unit) { onLoad() }

    Text(
        "判分即时走「使用中」的厂商；不配置则用服务端默认模型。点击厂商展开配置。",
        style = MaterialTheme.typography.bodySmall,
        color = Subtle,
    )

    when (val keys = state.myAiKeys) {
        is ContentState.Error -> Text(keys.message, color = Warn, style = MaterialTheme.typography.bodySmall)
        is ContentState.Loading -> Text("加载中…", style = MaterialTheme.typography.bodySmall, color = Subtle)
        else -> {}
    }

    // 与预设匹配不上的存量 profile（自定义名称）单独列出
    val customProfiles = state.llmProfiles.filter { presetFor(it) == null }

    SettingsMenuCard {
        LLM_PRESETS.forEachIndexed { index, preset ->
            if (index > 0) HorizontalDivider()
            PresetRow(
                preset = preset,
                profiles = state.llmProfiles,
                busyId = state.profileBusy,
                onAddProfile = onAddProfile,
                onSwitchProfile = onSwitchProfile,
                onRemoveProfile = onRemoveProfile,
                onProbeModels = onProbeModels,
                modelsState = state.llmModels,
                onModelPicked = onModelPicked,
                isFirst = state.llmProfiles.isEmpty(),
            )
        }
    }

    if (customProfiles.isNotEmpty()) {
        Text("其他已配置厂商", style = MaterialTheme.typography.titleMedium)
        SettingsMenuCard {
            customProfiles.forEachIndexed { index, profile ->
                if (index > 0) HorizontalDivider()
                ConfiguredProfileBody(
                    profile = profile,
                    busy = state.profileBusy == profile.id,
                    onSwitch = { onSwitchProfile(profile.id) },
                    onRemove = { onRemoveProfile(profile.id) },
                )
            }
        }
    }

    state.aiKeyNotice?.let { notice ->
        Text(notice, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary)
    }
}

/** 预设厂商行：状态徽章 + 点开配置表单 */
@Composable
private fun PresetRow(
    preset: LlmPreset,
    profiles: List<LlmProfile>,
    busyId: String?,
    onAddProfile: (String, String, String?, String?, Boolean) -> Unit,
    onSwitchProfile: (String) -> Unit,
    onRemoveProfile: (String) -> Unit,
    onProbeModels: (String, String) -> Unit,
    modelsState: ContentState<List<String>>,
    onModelPicked: () -> Unit,
    isFirst: Boolean,
) {
    val matched = profiles.filter { presetOf(preset, it) }
    val activeProfile = matched.firstOrNull { it.active }
    var expanded by remember(preset.label) { mutableStateOf(false) }
    val isCustom = preset.matchKeys.isEmpty()

    Column(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clickable { expanded = !expanded }
                .padding(horizontal = 16.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(preset.label, style = MaterialTheme.typography.bodyLarge)
                Text(
                    when {
                        activeProfile != null -> listOfNotNull(
                            "使用中",
                            activeProfile.model,
                            "····" + activeProfile.last4,
                        ).joinToString(" · ")

                        matched.isNotEmpty() -> "已配置 ····${matched.first().last4}"

                        else -> preset.endpoint.ifBlank { "填写密钥后可随时切换到该厂商" }
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = if (activeProfile != null) MaterialTheme.colorScheme.primary else Subtle,
                )
            }
            StatusBadge(
                when {
                    activeProfile != null -> "使用中"
                    matched.isNotEmpty() -> "已配置"
                    else -> "未配置"
                },
            )
            Text(if (expanded) "收起 ▲" else "配置 ▼", style = MaterialTheme.typography.bodySmall, color = Subtle)
        }

        if (activeProfile != null || matched.isNotEmpty()) {
            matched.forEach { profile ->
                ConfiguredProfileBody(
                    profile = profile,
                    busy = busyId == profile.id,
                    onSwitch = { onSwitchProfile(profile.id) },
                    onRemove = { onRemoveProfile(profile.id) },
                )
            }
        }

        if (expanded) {
            PresetConfigForm(
                preset = preset,
                isCustom = isCustom,
                onAddProfile = onAddProfile,
                onProbeModels = onProbeModels,
                modelsState = modelsState,
                onModelPicked = onModelPicked,
                isFirst = isFirst,
            )
        }
    }
}

/** 已配置 profile 的操作行（切换使用 / 删除） */
@Composable
private fun ConfiguredProfileBody(
    profile: LlmProfile,
    busy: Boolean,
    onSwitch: () -> Unit,
    onRemove: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 4.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                listOfNotNull(profile.model, "····" + profile.last4).joinToString(" · "),
                style = MaterialTheme.typography.bodySmall,
                color = if (profile.active) MaterialTheme.colorScheme.primary else Subtle,
            )
        }
        if (!profile.active) {
            TextButton(onClick = onSwitch, enabled = !busy) { Text("切换") }
        }
        TextButton(onClick = onRemove, enabled = !busy) { Text("删除") }
    }
}

/** 状态徽章：使用中（主色）/ 已配置（中性）/ 未配置（弱化） */
@Composable
private fun StatusBadge(text: String) {
    val (bg, fg) = when (text) {
        "使用中" -> MaterialTheme.colorScheme.primaryContainer to MaterialTheme.colorScheme.primary
        "已配置" -> MaterialTheme.colorScheme.surfaceVariant to MaterialTheme.colorScheme.onSurfaceVariant
        else -> MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f) to Subtle
    }
    Text(
        text,
        style = MaterialTheme.typography.labelSmall,
        color = fg,
        modifier = Modifier
            .background(color = bg, shape = CircleShape)
            .padding(horizontal = 8.dp, vertical = 2.dp),
    )
}

/** 厂商配置表单：密钥 + 接口地址（预填）+ 模型（可在线获取列表选择） */
@Composable
private fun PresetConfigForm(
    preset: LlmPreset,
    isCustom: Boolean,
    onAddProfile: (String, String, String?, String?, Boolean) -> Unit,
    onProbeModels: (String, String) -> Unit,
    modelsState: ContentState<List<String>>,
    onModelPicked: () -> Unit,
    isFirst: Boolean,
) {
    var labelInput by remember(preset.label) { mutableStateOf(if (isCustom) "" else preset.label) }
    var keyInput by remember(preset.label) { mutableStateOf("") }
    var endpointInput by remember(preset.label) { mutableStateOf(preset.endpoint) }
    var modelInput by remember(preset.label) { mutableStateOf("") }
    var showModelPicker by remember(preset.label) { mutableStateOf(false) }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        if (isCustom) {
            OutlinedTextField(
                value = labelInput,
                onValueChange = { labelInput = it },
                placeholder = { Text("厂商名称（如 通义千问）", style = MaterialTheme.typography.bodySmall) },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        }
        OutlinedTextField(
            value = keyInput,
            onValueChange = { keyInput = it },
            placeholder = { Text("API 密钥（必填，不回显）", style = MaterialTheme.typography.bodySmall) },
            visualTransformation = PasswordVisualTransformation(),
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = endpointInput,
            onValueChange = { endpointInput = it },
            placeholder = { Text("接口地址（选填）", style = MaterialTheme.typography.bodySmall) },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            OutlinedTextField(
                value = modelInput,
                onValueChange = { modelInput = it },
                placeholder = { Text("模型名（选填）", style = MaterialTheme.typography.bodySmall) },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(0.62f),
            )
            OutlinedButton(onClick = { onProbeModels(keyInput, endpointInput); showModelPicker = true }) {
                Text("获取列表", style = MaterialTheme.typography.bodySmall)
            }
        }
        when (val models = modelsState) {
            is ContentState.Loading -> if (showModelPicker) {
                Text("正在获取模型列表…", style = MaterialTheme.typography.bodySmall, color = Subtle)
            }

            is ContentState.Error -> if (showModelPicker) {
                Text(models.message, style = MaterialTheme.typography.bodySmall, color = Warn)
            }

            is ContentState.Success -> if (showModelPicker && models.value.isNotEmpty()) {
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text("选择模型：", style = MaterialTheme.typography.bodySmall, color = Subtle)
                    models.value.take(8).forEach { name ->
                        TextButton(
                            onClick = { modelInput = name; showModelPicker = false; onModelPicked() },
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text(name, style = MaterialTheme.typography.bodySmall) }
                    }
                    if (models.value.size > 8) {
                        Text(
                            "共 ${models.value.size} 个，其余可手动输入",
                            style = MaterialTheme.typography.bodySmall,
                            color = Subtle,
                        )
                    }
                }
            }

            else -> {}
        }
        Button(
            onClick = {
                onAddProfile(labelInput, keyInput, endpointInput, modelInput, isFirst)
                keyInput = ""; modelInput = ""
            },
            enabled = keyInput.length >= 8 && labelInput.isNotBlank(),
            modifier = Modifier.fillMaxWidth(),
        ) { Text(if (isFirst) "保存并启用" else "保存配置") }
    }
}

private fun presetOf(preset: LlmPreset, profile: LlmProfile): Boolean =
    preset.matchKeys.isNotEmpty() && preset.matchKeys.any { profile.label.lowercase().contains(it) }

// ---------------------------------------------------------------- 其余子页内容

@Composable
private fun ApiAddressSection(
    state: SettingsUiState,
    onBaseUrlChange: (String) -> Unit,
    onSave: () -> Unit,
    messageFor: (com.ailearningos.app.core.BaseUrlProblem) -> String,
) {
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
            onValueChange = onBaseUrlChange,
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
                    is BaseUrlValidation.Invalid -> messageFor(validation.problem)
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
            Button(onClick = onSave, enabled = !state.saving) {
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
}

@Composable
private fun PrivacySection(state: SettingsUiState, onRetry: () -> Unit) {
    SectionCard(title = "隐私路由") {
        ContentStateView(
            state = state.privacy,
            onRetry = onRetry,
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
}

@Composable
private fun ApiStatusSection(
    homeViewModel: HomeViewModel,
    homeState: ContentState<com.ailearningos.app.ui.home.HomeSnapshot>,
) {
    LaunchedEffect(Unit) { homeViewModel.refresh() }
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
}

@Composable
private fun UpdateSection(
    state: SettingsUiState,
    onCheckUpdate: () -> Unit,
    onOpenUpdateUrl: (String) -> Unit,
    onStartApkDownload: (UpdateCheckResult.Available) -> Unit = {},
    onInstallApk: () -> Boolean = { false },
) {
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
            OutlinedButton(onClick = onCheckUpdate) {
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
                        "发现新版本 ${result.version}。",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    when (val apk = state.apkUpdate) {
                        ApkUpdateState.Idle -> {
                            if (result.sha256 != null) {
                                Button(onClick = { onStartApkDownload(result) }) {
                                    Text("后台下载并安装")
                                }
                                Text(
                                    "下载在后台进行，完成后可直接应用内安装（无需跳浏览器）。",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = Subtle,
                                )
                            } else {
                                Button(onClick = { onOpenUpdateUrl(result.downloadUrl) }) {
                                    Text("下载更新")
                                }
                            }
                        }

                        is ApkUpdateState.Downloading -> {
                            val hasProgress = apk.progressPercent > 0
                            LinearProgressIndicator(
                                progress = { if (hasProgress) apk.progressPercent / 100f else 0f },
                                modifier = Modifier.fillMaxWidth(),
                            )
                            Text(
                                if (hasProgress) "正在后台下载 ${apk.progressPercent}%…" else "正在后台下载…",
                                style = MaterialTheme.typography.bodySmall,
                                color = Subtle,
                            )
                        }

                        ApkUpdateState.Verifying -> Text(
                            "下载完成，正在校验安装包…",
                            style = MaterialTheme.typography.bodyMedium,
                        )

                        is ApkUpdateState.Ready -> {
                            Button(onClick = { onInstallApk() }) {
                                Text("重启更新（已就绪 → v${apk.versionName}）")
                            }
                            Text(
                                "新版本已下载校验完成，点击后系统将确认安装；" +
                                    "首次需允许本应用「安装未知应用」（仅需一次）。",
                                style = MaterialTheme.typography.bodySmall,
                                color = Subtle,
                            )
                        }

                        is ApkUpdateState.Failed -> {
                            Text(
                                "自动更新失败：${apk.message}",
                                style = MaterialTheme.typography.bodyMedium,
                                color = Warn,
                            )
                        }
                    }
                    // 跳转下载始终保留：自更新失败/无哈希时的兜底通道
                    if (result.downloadUrlFallback != null ||
                        state.apkUpdate is ApkUpdateState.Failed
                    ) {
                        TextButton(onClick = { onOpenUpdateUrl(result.downloadUrl) }) {
                            Text("改为手动下载")
                        }
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
}

@Composable
private fun SecuritySection() {
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
