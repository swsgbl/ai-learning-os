package com.ailearningos.app.di

import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow

/**
 * 配置变更广播（M12-01）：设置页保存 API 地址后通知会话探测立即重跑，
 * 让「当前用户状态 / API 状态」跟随新地址，而不必重建界面。
 *
 * replay=1：事件先于订阅发生（如界面尚未重建、订阅协程未启动）也不丢——
 * 后订阅者会立即收到最近一次变更并重探；`tryEmit` + DROP_OLDEST 保证不挂起。
 */
class ConfigurationBus {

    private val _changes = MutableSharedFlow<Unit>(
        replay = 1,
        extraBufferCapacity = 1,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )

    val changes: SharedFlow<Unit> = _changes

    fun notifyChanged() {
        _changes.tryEmit(Unit)
    }
}
