package com.ailearningos.app.data.model

/** 当前登录用户（服务端 /auth/me 的领域视图；token 不进入该对象） */
data class UserProfile(
    val id: String,
    val username: String,
    val role: String,
) {
    val isAdmin: Boolean get() = role == "admin"
}

/**
 * 隐私路由快照（GET /api/v1/system/privacy）。
 * 模式值保留服务端原词（local/cloud/hybrid）；未知值由 UI 显示「未知」，
 * 不猜测、不虚构已接入能力。
 */
data class PrivacySnapshot(
    val modelRoute: String?,
    val voiceMode: String?,
    val searchMode: String?,
    val storesAudio: Boolean,
    val sendsContextToCloud: Boolean,
)
