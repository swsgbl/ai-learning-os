package com.ailearningos.app.ui.login

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.AuthGateway
import com.ailearningos.app.data.asAppError
import com.ailearningos.app.data.local.CredentialVault
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

enum class LoginMode { LOGIN, REGISTER }

data class LoginUiState(
    val username: String = "",
    val password: String = "",
    /** M15-007 记住密码（默认开）：勾选经 CredentialVault（Keystore 加密）记住 */
    val rememberPassword: Boolean = true,
    val mode: LoginMode = LoginMode.LOGIN,
    val submitting: Boolean = false,
    val error: String? = null,
    val success: Boolean = false,
) {
    val canSubmit: Boolean
        get() = !submitting && username.isNotBlank() && password.isNotBlank()
}

/**
 * 登录表单状态机（M12-01）。
 * 密码只存在于表单内存态：成功后立即清空，且任何路径都不持久化。
 */
class LoginViewModel(
    private val auth: AuthGateway,
    private val credentialVault: CredentialVault? = null,
) : ViewModel() {

    private val _state = MutableStateFlow(LoginUiState())
    val state: StateFlow<LoginUiState> = _state.asStateFlow()

    init {
        // M15-007: 上次「记住密码」的凭据预填（Keystore 加密保险库；无库/未记住 = 空表单）
        val vault = credentialVault
        if (vault != null) {
            viewModelScope.launch {
                val remembered = try {
                    vault.load()
                } catch (cause: CancellationException) {
                    throw cause
                } catch (_: Throwable) {
                    null
                }
                if (remembered != null) {
                    _state.update {
                        it.copy(
                            username = remembered.first,
                            password = remembered.second,
                            rememberPassword = true,
                        )
                    }
                }
            }
        }
    }

    /** M15-007: 切换记住密码（取消勾选即抹除已存密文） */
    fun onRememberPasswordChange(value: Boolean) {
        _state.update { it.copy(rememberPassword = value) }
        if (!value) {
            viewModelScope.launch {
                try {
                    credentialVault?.clear()
                } catch (cause: CancellationException) {
                    throw cause
                } catch (_: Throwable) {
                    // 清除失败不阻断 UI（下次保存会覆盖）
                }
            }
        }
    }

    fun onUsernameChange(value: String) {
        _state.update { it.copy(username = value, error = null) }
    }

    fun onPasswordChange(value: String) {
        _state.update { it.copy(password = value, error = null) }
    }

    /** M15-002: 登录/注册双模式切换（切换清错误，表单内容保留便于补填）。 */
    fun onModeChange(mode: LoginMode) {
        _state.update { it.copy(mode = mode, error = null) }
    }

    fun submit() {
        val current = _state.value
        if (!current.canSubmit) return
        val invalidMessage = validateCredentials(current.username, current.password)
        if (invalidMessage != null) {
            _state.update { it.copy(error = invalidMessage) }
            return
        }
        viewModelScope.launch {
            _state.update { it.copy(submitting = true, error = null) }
            try {
                if (current.mode == LoginMode.REGISTER) {
                    auth.register(current.username, current.password)
                } else {
                    auth.login(current.username, current.password)
                }
                // M15-007: 记住密码 = Keystore 加密保险库保存；未勾选 = 确保抹除。
                // 表单（含密码）照旧清空——界面态不残留明文。
                val vault = credentialVault
                if (vault != null) {
                    try {
                        if (current.rememberPassword) {
                            vault.save(current.username, current.password)
                        } else {
                            vault.clear()
                        }
                    } catch (cause: CancellationException) {
                        throw cause
                    } catch (_: Throwable) {
                        // 保存失败不阻断登录成功路径（下次可再勾选）
                    }
                }
                _state.value = LoginUiState(success = true)
            } catch (cause: CancellationException) {
                throw cause
            } catch (cause: Throwable) {
                val appError = cause.asAppError()
                val message = when {
                    // 注册冲突：如实告知用户名已存在（可换名或去登录）
                    current.mode == LoginMode.REGISTER && appError.kind == AppErrorKind.CONFLICT ->
                        USERNAME_TAKEN_MESSAGE
                    // 与服务端同口径：登录失败统一「用户名或密码错误」，不区分用户不存在
                    appError.kind == AppErrorKind.UNAUTHORIZED -> LOGIN_FAILED_MESSAGE
                    else -> appError.userMessage
                }
                _state.update { it.copy(submitting = false, error = message) }
            }
        }
    }

    fun clearError() {
        _state.update { it.copy(error = null) }
    }

    companion object {
        const val LOGIN_FAILED_MESSAGE = "用户名或密码错误"
        const val USERNAME_TAKEN_MESSAGE = "用户名已被注册，换一个或切换到登录"
    }
}

/** 与服务端 M9-01 规则一致的用户名/密码预校验；返回错误文案或 null */
fun validateCredentials(username: String, password: String): String? {
    // 不做任何 trim：首尾空白的用户名直接非法。
    // 静默 trim 会造成「界面显示与服务端收到的身份不一致」的双重修复面。
    if (!USERNAME_PATTERN.matches(username)) {
        return "用户名需为 3-32 位字母、数字或下划线"
    }
    if (password.length < PASSWORD_MIN_LENGTH || password.length > PASSWORD_MAX_LENGTH) {
        return "密码需为 8-128 位"
    }
    return null
}

private val USERNAME_PATTERN = Regex("^[a-zA-Z0-9_]{3,32}$")
private const val PASSWORD_MIN_LENGTH = 8
private const val PASSWORD_MAX_LENGTH = 128
