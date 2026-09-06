package com.ailearningos.app.ui.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.data.SystemGateway
import com.ailearningos.app.data.model.PrivacySnapshot
import com.ailearningos.app.ui.common.ContentState
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class HomeSnapshot(
    /** /health 探测结果 */
    val apiServiceOk: Boolean,
    /** 隐私路由快照；读取失败时是 Error 态（不影响其它状态展示） */
    val privacy: ContentState<PrivacySnapshot>,
)

class HomeViewModel(
    private val system: SystemGateway,
) : ViewModel() {

    private val _state = MutableStateFlow<ContentState<HomeSnapshot>>(ContentState.Loading)
    val state: StateFlow<ContentState<HomeSnapshot>> = _state.asStateFlow()

    fun refresh() {
        viewModelScope.launch {
            _state.value = ContentState.Loading
            _state.value = try {
                val ok = system.healthOk()
                val privacy = try {
                    ContentState.Success(system.privacy())
                } catch (cause: AppError) {
                    ContentState.Error(cause.userMessage)
                }
                ContentState.Success(HomeSnapshot(apiServiceOk = ok, privacy = privacy))
            } catch (cause: CancellationException) {
                throw cause
            } catch (cause: AppError) {
                ContentState.Error(cause.userMessage)
            } catch (_: Throwable) {
                ContentState.Error(AppError(AppErrorKind.UNEXPECTED).userMessage)
            }
        }
    }
}
