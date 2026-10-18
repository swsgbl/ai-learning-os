package com.ailearningos.app.ui.settings

/**
 * M15-006 应用更新检查（纯判定逻辑，可单测）。
 *
 * 服务端 [com.ailearningos.app.data.LatestAppInfo] 三项齐全才存在；
 * null = 服务端未配置更新信息（如实「暂无版本信息」，绝不猜默认地址）。
 * 版本比较只用 versionCode（单调递增整数，无字符串解析歧义）；
 * versionName 仅用于展示。
 */
sealed interface UpdateCheckResult {
    /** 服务端未配置（404）或数据不完整 */
    data object NotConfigured : UpdateCheckResult

    /** 本机已是服务端已知最新版 */
    data class UpToDate(val currentVersionName: String) : UpdateCheckResult

    /** 服务端有更高 versionCode：展示版本号并提供下载地址 */
    data class Available(
        val version: String,
        val downloadUrl: String,
    ) : UpdateCheckResult
}

object UpdateCheckEvaluator {
    fun evaluate(
        currentVersionCode: Int,
        currentVersionName: String,
        latest: com.ailearningos.app.data.LatestAppInfo?,
    ): UpdateCheckResult {
        if (latest == null) return UpdateCheckResult.NotConfigured
        return if (latest.versionCode > currentVersionCode) {
            UpdateCheckResult.Available(latest.version, latest.downloadUrl)
        } else {
            UpdateCheckResult.UpToDate(currentVersionName)
        }
    }
}
