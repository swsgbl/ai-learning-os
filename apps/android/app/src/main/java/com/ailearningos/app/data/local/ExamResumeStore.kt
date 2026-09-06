package com.ailearningos.app.data.local

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext

private val Context.examResumeStore by preferencesDataStore(name = "aios_exam_resume")

/**
 * 断线/进程重启恢复槽（M12-02）。
 *
 * 本地只允许保存 exam_id 一个值，且只作为「恢复提示」：
 * 恢复时必须 GET /exams/{exam_id} 对齐 answers / next_sequence /
 * server_remaining_seconds / server_end_at——答案、剩余时间、判分结果
 * 一律以服务端为唯一真相源，绝不在本地缓存或当真相使用。
 * （存储面守卫：ExamResumeStoreGuardTest 源码级锁定只写 exam_id 键。）
 */
interface ExamResumeStore {
    /** 最近一场未收口考试的 exam_id；无则 null */
    suspend fun load(): String?

    /** 记录 exam_id（开考/恢复成功时覆盖旧槽，语义为「本设备当前活跃考试」） */
    suspend fun save(examId: String)

    /** 交卷完成/考试不存在后清除恢复槽 */
    suspend fun clear()
}

class DataStoreExamResumeStore(
    context: Context,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : ExamResumeStore {

    private val dataStore = context.applicationContext.examResumeStore

    override suspend fun load(): String? = withContext(ioDispatcher) {
        dataStore.data.first()[EXAM_ID_KEY]
    }

    override suspend fun save(examId: String): Unit = withContext(ioDispatcher) {
        dataStore.edit { prefs -> prefs[EXAM_ID_KEY] = examId }
    }

    override suspend fun clear(): Unit = withContext(ioDispatcher) {
        dataStore.edit { prefs -> prefs.remove(EXAM_ID_KEY) }
    }

    private companion object {
        val EXAM_ID_KEY = stringPreferencesKey("resume_exam_id_v1")
    }
}
