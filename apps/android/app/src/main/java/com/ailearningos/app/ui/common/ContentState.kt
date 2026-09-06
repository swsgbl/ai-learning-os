package com.ailearningos.app.ui.common

/**
 * 内容四态（M12-01：状态要覆盖 loading/empty/error/success）。
 * Empty 语义固定为「已到达但尚无数据/尚未获取」，不用于错误。
 */
sealed interface ContentState<out T> {
    data object Loading : ContentState<Nothing>
    data object Empty : ContentState<Nothing>
    data class Error(val message: String) : ContentState<Nothing>
    data class Success<T>(val value: T) : ContentState<T>
}
