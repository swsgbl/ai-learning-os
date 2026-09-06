package com.ailearningos.app.data.local

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext

private val Context.voiceResumeStore by preferencesDataStore(name = "aios_voice_resume")

/**
 * 语音陪练断线/进程重启恢复槽（M12-03）。
 *
 * 本地只允许保存 session_id / exam_id 两个值，且只作为「恢复提示」：
 * 恢复时必须 GET /voice/sessions/{id}/resume 对齐状态、当前题、已提交答案、
 * 题数——状态与答案一律以服务端为唯一真相源，绝不在本地缓存或当真相使用。
 * 语音音频、transcript、判分结果绝不落本地。
 * （存储面守卫：VoiceResumeStoreGuardTest 源码级锁定只写这两个键。）
 */
data class VoiceResumeSlot(
    val sessionId: String,
    val examId: String,
)

interface VoiceResumeStore {
    /** 最近一次未收口语音陪练的槽位；无则 null */
    suspend fun load(): VoiceResumeSlot?

    /** 记录槽位（开新会话/恢复成功时覆盖旧槽，语义为「本设备当前活跃语音陪练」） */
    suspend fun save(slot: VoiceResumeSlot)

    /** 报告已获取/会话不存在（404）/会话已终态（409）后清除恢复槽 */
    suspend fun clear()
}

class DataStoreVoiceResumeStore(
    context: Context,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : VoiceResumeStore {

    private val dataStore = context.applicationContext.voiceResumeStore

    override suspend fun load(): VoiceResumeSlot? = withContext(ioDispatcher) {
        val prefs = dataStore.data.first()
        val sessionId = prefs[SESSION_ID_KEY] ?: return@withContext null
        val examId = prefs[EXAM_ID_KEY] ?: return@withContext null
        VoiceResumeSlot(sessionId = sessionId, examId = examId)
    }

    override suspend fun save(slot: VoiceResumeSlot): Unit = withContext(ioDispatcher) {
        dataStore.edit { prefs ->
            prefs[SESSION_ID_KEY] = slot.sessionId
            prefs[EXAM_ID_KEY] = slot.examId
        }
    }

    override suspend fun clear(): Unit = withContext(ioDispatcher) {
        dataStore.edit { prefs ->
            prefs.remove(SESSION_ID_KEY)
            prefs.remove(EXAM_ID_KEY)
        }
    }

    private companion object {
        val SESSION_ID_KEY = stringPreferencesKey("voice_resume_session_id_v1")
        val EXAM_ID_KEY = stringPreferencesKey("voice_resume_exam_id_v1")
    }
}
