package com.ailearningos.app.ui.session

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.AuthGateway
import com.ailearningos.app.data.asAppError
import com.ailearningos.app.data.model.UserProfile
import com.ailearningos.app.di.ConfigurationBus
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * 当前用户状态（M12-01）：
 * - Loading：探测中；
 * - Unavailable：探测失败（网络/配置），文案已脱敏，可在设置修正后恢复；
 * - AuthDisabled：后端未配置 AUTH_SECRET 的本地模式（如实透出，不虚报登录态）；
 * - Anonymous：未登录；
 * - Authenticated：已登录（持有 token + /me 用户）。
 */
sealed interface SessionState {
    data object Loading : SessionState
    data class Unavailable(val message: String) : SessionState
    data object AuthDisabled : SessionState
    data object Anonymous : SessionState
    data class Authenticated(val user: UserProfile) : SessionState
}

class SessionViewModel(
    private val auth: AuthGateway,
    private val configurationBus: ConfigurationBus,
) : ViewModel() {

    private val _state = MutableStateFlow<SessionState>(SessionState.Loading)
    val state: StateFlow<SessionState> = _state.asStateFlow()

    /** 短暂提示（如登出时安全存储失败）；UI 展示后调用 [clearNotice] */
    private val _notice = MutableStateFlow<String?>(null)
    val notice: StateFlow<String?> = _notice.asStateFlow()

    /** 在途探测任务；新探测/登出会取消它，保证不浪费也不乱写 */
    private var refreshJob: Job? = null

    /**
     * 探测代际号：每次新探测/登出递增，只有持最新代际的写入才被接受。
     * 取消与代际双保险——取消断掉挂起中的旧探测；即便旧探测已过挂起点、
     * 在无更多 suspension 的情况下走到写状态，代际检查也会拦下旧结果。
     */
    private var generation = 0

    init {
        viewModelScope.launch {
            configurationBus.changes.collect { refresh() }
        }
    }

    fun refresh() {
        refreshJob?.cancel()
        val myGeneration = ++generation
        refreshJob = viewModelScope.launch {
            setState(myGeneration, SessionState.Loading)
            val next = try {
                when {
                    !auth.authEnabled() -> SessionState.AuthDisabled
                    else -> {
                        val user = auth.currentUser()
                        if (user == null) SessionState.Anonymous else SessionState.Authenticated(user)
                    }
                }
            } catch (cause: AppError) {
                SessionState.Unavailable(cause.userMessage)
            } catch (cause: CancellationException) {
                // 过期探测被取消：按取消收场，绝不把「被取消」误写成 Unavailable
                throw cause
            } catch (_: Throwable) {
                SessionState.Unavailable(AppError(AppErrorKind.UNEXPECTED).userMessage)
            }
            setState(myGeneration, next)
        }
    }

    fun logout() {
        refreshJob?.cancel()
        val myGeneration = ++generation
        viewModelScope.launch {
            try {
                auth.logout()
            } catch (cause: CancellationException) {
                throw cause
            } catch (cause: Throwable) {
                _notice.value = cause.asAppError().userMessage
            }
            // 本地清理失败也不保留内存登录态：界面一律回到未登录
            setState(myGeneration, SessionState.Anonymous)
        }
    }

    /** 只接受最新代际的写入：旧探测晚完成不得覆盖新结果 */
    private fun setState(myGeneration: Int, state: SessionState) {
        if (myGeneration == generation) {
            _state.value = state
        }
    }

    fun clearNotice() {
        _notice.value = null
    }
}
