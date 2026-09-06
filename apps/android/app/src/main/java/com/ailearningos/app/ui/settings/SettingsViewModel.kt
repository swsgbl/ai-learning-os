package com.ailearningos.app.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.core.BaseUrlPolicy
import com.ailearningos.app.core.BaseUrlProblem
import com.ailearningos.app.core.BaseUrlValidation
import com.ailearningos.app.data.SystemGateway
import com.ailearningos.app.data.local.SettingsStore
import com.ailearningos.app.data.asAppError
import com.ailearningos.app.data.model.PrivacySnapshot
import com.ailearningos.app.di.ConfigurationBus
import com.ailearningos.app.ui.common.ContentState
import kotlinx.coroutines.CancellationException
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
)

class SettingsViewModel(
    private val settingsStore: SettingsStore,
    private val configurationBus: ConfigurationBus,
    private val system: SystemGateway,
    allowInsecureHttp: Boolean,
) : ViewModel() {

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
