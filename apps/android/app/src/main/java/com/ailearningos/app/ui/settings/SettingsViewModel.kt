package com.ailearningos.app.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import android.content.Context
import com.ailearningos.app.BuildConfig
import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.core.BaseUrlPolicy
import com.ailearningos.app.core.BaseUrlProblem
import com.ailearningos.app.core.BaseUrlValidation
import com.ailearningos.app.data.MyAiKeyInfo
import com.ailearningos.app.data.SystemGateway
import com.ailearningos.app.data.local.SettingsStore
import com.ailearningos.app.data.asAppError
import com.ailearningos.app.data.model.PrivacySnapshot
import com.ailearningos.app.di.ConfigurationBus
import com.ailearningos.app.ui.common.ContentState
import kotlinx.coroutines.CancellationException
import okhttp3.OkHttpClient
import java.io.File
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class SettingsUiState(
    val baseUrlInput: String = "",
    /** 已保存（生效）的 base URL；null = 尚未读取到 */
    val effectiveBaseUrl: String? = null,
    /** 输入框即时校验反馈；null = 尚未输入 */
    val validation: BaseUrlValidation? = null,
    val saving: Boolean = false,
    /** 保存/校验结果提示（脱敏文案） */
    val notice: String? = null,
    /** 隐私路由：初始 Empty（未获取），主动获取后 Loading/Success/Error */
    val privacy: ContentState<PrivacySnapshot> = ContentState.Empty,
    /** debug 构建 = true（允许 loopback/局域网 http）；release = false（强制 https） */
    val allowInsecureHttp: Boolean = true,
    /** M15-006 更新检查：Empty（未查）/ Loading / Success(判定结果) / Error(脱敏文案) */
    val updateCheck: ContentState<UpdateCheckResult> = ContentState.Empty,
    /** M21 应用内自更新：后台下载/校验/待安装状态机 */
    val apkUpdate: ApkUpdateState = ApkUpdateState.Idle,
    /** M15-014 个人 AI 密钥（BYOK）：null=未加载（未登录）；Empty 也未加载 */
    val myAiKeys: ContentState<List<MyAiKeyInfo>> = ContentState.Empty,
    /** BYOK 保存/删除的脱敏反馈 */
    val aiKeyNotice: String? = null,
    /** M15-016 模型列表探测：null=未探测；Loading/Success(模型名列表)/Error */
    val llmModels: ContentState<List<String>> = ContentState.Empty,
    /** M15-018 多厂商配置列表（含激活标志） */
    val llmProfiles: List<com.ailearningos.app.data.remote.LlmProfile> = emptyList(),
    /** 添加/切换/删除中的 profile id（防抖） */
    val profileBusy: String? = null,
)

class SettingsViewModel(
    private val settingsStore: SettingsStore,
    private val configurationBus: ConfigurationBus,
    private val system: SystemGateway,
    allowInsecureHttp: Boolean,
    private val currentVersionCode: Int = BuildConfig.VERSION_CODE,
    private val currentVersionName: String = BuildConfig.VERSION_NAME,
) : ViewModel() {

    /** M21 自更新：独立下载 client（无 Auth 拦截器——公开 APK 不带凭据）；cacheDir 由 UI 层传入（VM 不持 Context） */
    private val downloadClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)
            .build()
    }

    /** 已下载校验通过的 APK（Ready 态点击安装用） */
    private var downloadedApk: DownloadedApk? = null

    private val _state = MutableStateFlow(SettingsUiState(allowInsecureHttp = allowInsecureHttp))
    val state: StateFlow<SettingsUiState> = _state.asStateFlow()

    init {
        loadSavedBaseUrl()
    }

    private fun loadSavedBaseUrl() {
        viewModelScope.launch {
            val saved = try {
                settingsStore.loadBaseUrl()
            } catch (cause: CancellationException) {
                throw cause
            } catch (_: Throwable) {
                null
            }
            _state.update { current ->
                // 只在尚未读取到时填入，避免覆盖用户正在编辑的输入
                if (current.effectiveBaseUrl == null) {
                    current.copy(effectiveBaseUrl = saved, baseUrlInput = saved.orEmpty())
                } else {
                    current
                }
            }
        }
    }

    fun onBaseUrlChange(value: String) {
        _state.update { current ->
            current.copy(
                baseUrlInput = value,
                // 空输入不即时报错（视为未填写），非空才给实时反馈；先去掉首尾空白
                validation = if (value.isBlank()) {
                    null
                } else {
                    BaseUrlPolicy.validate(value.trim(), current.allowInsecureHttp)
                },
                notice = null,
            )
        }
    }

    fun saveBaseUrl() {
        val current = _state.value
        if (current.saving) return
        when (val validation = BaseUrlPolicy.validate(current.baseUrlInput.trim(), current.allowInsecureHttp)) {
            is BaseUrlValidation.Invalid -> {
                _state.update { it.copy(notice = messageFor(validation.problem)) }
            }

            is BaseUrlValidation.Valid -> viewModelScope.launch {
                _state.update { it.copy(saving = true, notice = null) }
                try {
                    settingsStore.saveBaseUrl(validation.normalized)
                    _state.update {
                        it.copy(
                            saving = false,
                            effectiveBaseUrl = validation.normalized,
                            baseUrlInput = validation.normalized,
                            validation = null,
                            notice = SAVED_NOTICE,
                        )
                    }
                    // 通知会话/首页立即用新地址重新探测
                    configurationBus.notifyChanged()
                } catch (cause: CancellationException) {
                    throw cause
                } catch (cause: Throwable) {
                    _state.update { it.copy(saving = false, notice = cause.asAppError().userMessage) }
                }
            }
        }
    }

    fun clearNotice() {
        _state.update { it.copy(notice = null) }
    }

    fun refreshPrivacy() {
        viewModelScope.launch {
            _state.update { it.copy(privacy = ContentState.Loading) }
            _state.update { current ->
                current.copy(
                    privacy = try {
                        ContentState.Success(system.privacy())
                    } catch (cause: CancellationException) {
                        throw cause
                    } catch (cause: AppError) {
                        ContentState.Error(cause.userMessage)
                    } catch (_: Throwable) {
                        ContentState.Error(AppError(AppErrorKind.UNEXPECTED).userMessage)
                    },
                )
            }
        }
    }

    /** M15-006 检查更新：手动触发（不做后台静默轮询——更新意图由用户主导） */
    fun checkUpdate() {
        if (_state.value.updateCheck == ContentState.Loading) return
        viewModelScope.launch {
            _state.update { it.copy(updateCheck = ContentState.Loading) }
            val result = try {
                val latest = system.latestApp()
                ContentState.Success(
                    UpdateCheckEvaluator.evaluate(currentVersionCode, currentVersionName, latest),
                )
            } catch (cause: CancellationException) {
                throw cause
            } catch (cause: AppError) {
                ContentState.Error(cause.userMessage)
            } catch (_: Throwable) {
                ContentState.Error(AppError(AppErrorKind.UNEXPECTED).userMessage)
            }
            _state.update { it.copy(updateCheck = result) }
        }
    }

    /** M21 应用内自更新：后台下载 → SHA-256 校验 → Ready（等待用户点安装） */
    fun startApkDownload(available: UpdateCheckResult.Available, cacheDir: File) {
        if (_state.value.apkUpdate is ApkUpdateState.Downloading ||
            _state.value.apkUpdate is ApkUpdateState.Verifying
        ) return
        val sha256 = available.sha256
        if (sha256.isNullOrBlank()) {
            // fail-closed：服务端未透出哈希 → 拒绝自装（只保留跳转下载）
            _state.update { it.copy(apkUpdate = ApkUpdateState.Failed("服务端未提供安装包校验值，请用下方下载地址手动更新")) }
            return
        }
        viewModelScope.launch {
            _state.update { it.copy(apkUpdate = ApkUpdateState.Downloading(0)) }
            try {
                val apk = ApkSelfUpdate.downloadAndVerify(
                    client = downloadClient,
                    url = available.downloadUrl,
                    expectedSha256 = sha256,
                    versionCode = available.versionCode,
                    versionName = available.version,
                    cacheDir = cacheDir,
                ) { pct ->
                    _state.update { it.copy(apkUpdate = ApkUpdateState.Downloading(pct)) }
                }
                _state.update { it.copy(apkUpdate = ApkUpdateState.Verifying) }
                downloadedApk = apk
                _state.update { it.copy(apkUpdate = ApkUpdateState.Ready(apk.versionName)) }
            } catch (cause: CancellationException) {
                throw cause
            } catch (cause: Throwable) {
                _state.update {
                    it.copy(apkUpdate = ApkUpdateState.Failed(cause.message ?: "下载失败"))
                }
            }
        }
    }

    /** Ready 态点击：引导安装授权（首次）→ 触发系统安装器（覆盖安装即"重启更新"） */
    fun installDownloadedApk(context: Context): Boolean {
        val apk = downloadedApk ?: return false
        if (!ApkSelfUpdate.canRequestInstall(context)) {
            return ApkSelfUpdate.ensureInstallPermission(context)
        }
        return ApkSelfUpdate.launchInstall(context, apk.file)
    }

    /** 放弃本次已下载更新（清缓存态，回到跳转下载语义） */
    fun resetApkUpdate() {
        _state.update { it.copy(apkUpdate = ApkUpdateState.Idle) }
    }

    /** M15-014 个人 AI 密钥：加载当前用户三槽位状态 */
    fun loadMyAiKeys() {
        if (_state.value.myAiKeys == ContentState.Loading) return
        viewModelScope.launch {
            _state.update { it.copy(myAiKeys = ContentState.Loading) }
            val result = try {
                ContentState.Success(system.myAiKeys())
            } catch (cause: Throwable) {
                val message = cause.asAppError().userMessage
                ContentState.Error(message)
            }
            _state.update { it.copy(myAiKeys = result) }
        }
    }

    /** M15-015 保存个人 LLM 配置（密钥必填；接口地址/模型名可选） */
    fun saveMyAiKey(slot: String, apiKey: String, endpoint: String? = null, model: String? = null) {
        if (apiKey.length < 8) {
            _state.update { it.copy(aiKeyNotice = "密钥过短（至少 8 位）") }
            return
        }
        viewModelScope.launch {
            try {
                system.saveMyAiKey(slot, apiKey, endpoint, model)
                _state.update { it.copy(aiKeyNotice = "已保存（加密存储，判分即时生效）") }
                loadMyAiKeys()
            } catch (cause: Throwable) {
                _state.update { it.copy(aiKeyNotice = cause.asAppError().userMessage) }
            }
        }
    }

    /** M15-014 删除个人密钥（回退全局/服务端配置） */
    fun deleteMyAiKey(slot: String) {
        viewModelScope.launch {
            try {
                system.deleteMyAiKey(slot)
                _state.update { it.copy(aiKeyNotice = "已删除（回退服务端配置）") }
                loadMyAiKeys()
            } catch (cause: Throwable) {
                _state.update { it.copy(aiKeyNotice = cause.asAppError().userMessage) }
            }
        }
    }

    fun consumeAiKeyNotice() {
        _state.update { it.copy(aiKeyNotice = null) }
    }

    /** M15-016 获取模型列表（服务端代理探测；密钥/地址取当前输入或已保存值） */
    fun probeLlmModels(apiKey: String, endpoint: String) {
        if (_state.value.llmModels == ContentState.Loading) return
        viewModelScope.launch {
            _state.update { it.copy(llmModels = ContentState.Loading) }
            val result = try {
                ContentState.Success(system.probeLlmModels(apiKey.ifBlank { null }, endpoint.ifBlank { null }))
            } catch (cause: Throwable) {
                ContentState.Error(cause.asAppError().userMessage)
            }
            _state.update { it.copy(llmModels = result) }
        }
    }

    fun consumeLlmModels() {
        _state.update { it.copy(llmModels = ContentState.Empty) }
    }

    /** M15-018 多厂商：加载列表（随 myAiKeys 一并刷新） */
    private suspend fun refreshProfiles() {
        try {
            val profiles = system.llmProfiles()
            _state.update { it.copy(llmProfiles = profiles) }
        } catch (_: Throwable) {
            // 列表加载失败不阻断（keys 已有独立状态）
        }
    }

    fun addLlmProfile(label: String, apiKey: String, endpoint: String?, model: String?, activate: Boolean) {
        if (apiKey.length < 8 || label.isBlank()) {
            _state.update { it.copy(aiKeyNotice = "请填写名称与至少 8 位密钥") }
            return
        }
        viewModelScope.launch {
            try {
                system.createLlmProfile(label.trim(), apiKey, endpoint, model, activate)
                _state.update { it.copy(aiKeyNotice = "已添加厂商配置${if (activate) "并切换" else ""}") }
                loadMyAiKeys()
                refreshProfiles()
            } catch (cause: Throwable) {
                _state.update { it.copy(aiKeyNotice = cause.asAppError().userMessage) }
            }
        }
    }

    fun switchLlmProfile(profileId: String) {
        if (_state.value.profileBusy != null) return
        viewModelScope.launch {
            _state.update { it.copy(profileBusy = profileId) }
            try {
                system.activateLlmProfile(profileId)
                _state.update { it.copy(aiKeyNotice = "已切换，判分即时走该厂商") }
                loadMyAiKeys()
                refreshProfiles()
            } catch (cause: Throwable) {
                _state.update { it.copy(aiKeyNotice = cause.asAppError().userMessage) }
            } finally {
                _state.update { it.copy(profileBusy = null) }
            }
        }
    }

    fun removeLlmProfile(profileId: String) {
        if (_state.value.profileBusy != null) return
        viewModelScope.launch {
            _state.update { it.copy(profileBusy = profileId) }
            try {
                system.deleteLlmProfile(profileId)
                _state.update { it.copy(aiKeyNotice = "已删除") }
                loadMyAiKeys()
                refreshProfiles()
            } catch (cause: Throwable) {
                _state.update { it.copy(aiKeyNotice = cause.asAppError().userMessage) }
            } finally {
                _state.update { it.copy(profileBusy = null) }
            }
        }
    }

    fun messageFor(problem: BaseUrlProblem): String = when (problem) {
        BaseUrlProblem.EMPTY -> "请输入 API 地址"
        BaseUrlProblem.HAS_WHITESPACE -> "API 地址不能包含空格"
        BaseUrlProblem.BAD_SCHEME -> "地址需以 http:// 或 https:// 开头"
        BaseUrlProblem.BAD_CHARACTERS -> "地址不能包含查询参数、锚点或凭据信息"
        BaseUrlProblem.INSECURE_FORBIDDEN -> "当前构建要求使用 https 地址"
        BaseUrlProblem.PUBLIC_HTTP_FORBIDDEN -> "http 地址仅允许本机回环或局域网（调试构建）"
    }

    companion object {
        const val SAVED_NOTICE = "已保存，正在用新地址重新探测"
    }
}
