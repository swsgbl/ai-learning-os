package com.ailearningos.app.ui.voice

import androidx.compose.runtime.Composable
import com.ailearningos.app.ui.common.PlaceholderPage

/** 语音陪练占位（M12-01）：只说明后续能力，不进行任何录音或联网语音请求 */
@Composable
fun VoicePlaceholderScreen(
    onBack: () -> Unit,
    onOpenSettings: () -> Unit,
) {
    PlaceholderPage(
        title = "语音陪练",
        tag = "占位",
        body = "后续版本将在这里接入：语音陪练会话、打断与恢复、转写记录与语音评测。" +
            "当前版本尚未接入语音能力，本页面不进行任何录音，也不发起任何语音联网请求。",
        onBack = onBack,
        onOpenSettings = onOpenSettings,
    )
}
