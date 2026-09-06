package com.ailearningos.app.di

import android.content.Context
import com.ailearningos.app.BuildConfig
import com.ailearningos.app.data.ApiConfigProvider
import com.ailearningos.app.data.ApiProvider
import com.ailearningos.app.data.AuthGateway
import com.ailearningos.app.data.AuthRepository
import com.ailearningos.app.data.ExamGateway
import com.ailearningos.app.data.ExamRepository
import com.ailearningos.app.data.SessionTokenCache
import com.ailearningos.app.data.SystemGateway
import com.ailearningos.app.data.VoiceGateway
import com.ailearningos.app.data.VoiceRepository
import com.ailearningos.app.data.local.DataStoreExamResumeStore
import com.ailearningos.app.data.local.DataStoreSettingsStore
import com.ailearningos.app.data.local.DataStoreVoiceResumeStore
import com.ailearningos.app.data.local.ExamResumeStore
import com.ailearningos.app.data.local.KeystoreTokenStore
import com.ailearningos.app.data.local.SettingsStore
import com.ailearningos.app.data.local.TokenStore
import com.ailearningos.app.data.local.VoiceResumeStore
import com.ailearningos.app.voice.AndroidWavCaptureEngine
import com.ailearningos.app.voice.AndroidWavPlaybackEngine
import com.ailearningos.app.voice.AudioCaptureEngine
import com.ailearningos.app.voice.AudioPlaybackEngine

/**
 * 手工依赖装配（M12-01 不引入 DI 框架）。
 * 不打任何日志：token、base URL、header 不进日志/崩溃信息。
 */
class AppContainer(context: Context) {

    /** debug 构建放行 loopback/局域网 http；release 强制 https */
    val allowInsecureHttp: Boolean = BuildConfig.ALLOW_INSECURE_HTTP

    val configurationBus = ConfigurationBus()

    private val tokenStore: TokenStore = KeystoreTokenStore(context.applicationContext)

    val settingsStore: SettingsStore = DataStoreSettingsStore(context.applicationContext)

    /** 断线恢复槽：本地只保存 exam_id（恢复提示），不保存答案/时间/判分 */
    val examResumeStore: ExamResumeStore = DataStoreExamResumeStore(context.applicationContext)

    /** 语音陪练恢复槽：本地只保存 session_id/exam_id，不保存音频/transcript/判分 */
    val voiceResumeStore: VoiceResumeStore = DataStoreVoiceResumeStore(context.applicationContext)

    private val tokenCache = SessionTokenCache(tokenStore)

    private val apiProvider = ApiProvider(
        config = ApiConfigProvider(settingsStore, allowInsecureHttp),
        tokenCache = tokenCache,
    )

    private val repository = AuthRepository(apiProvider, tokenCache)

    val authGateway: AuthGateway = repository

    val systemGateway: SystemGateway = repository

    val examGateway: ExamGateway = ExamRepository(apiProvider)

    val voiceGateway: VoiceGateway = VoiceRepository(apiProvider)

    /** 录音采集：16kHz/单声道 PCM16 WAV（授权由 UI 层先请求，拒绝则文本作答降级） */
    val audioCaptureEngine: AudioCaptureEngine = AndroidWavCaptureEngine()

    /** 播报播放：以服务端 WAV header 的采样率/声道为准 */
    val audioPlaybackEngine: AudioPlaybackEngine = AndroidWavPlaybackEngine()
}
