package com.ailearningos.app.data.local

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext

private val Context.settingsStore by preferencesDataStore(name = "aios_settings")

/**
 * 普通配置存储（M12-01）：只放 API base URL 这类非敏感配置。
 * token 永不进入这里（安全边界见 [TokenStore]；测试守卫锁定）。
 */
interface SettingsStore {
    suspend fun loadBaseUrl(): String?
    suspend fun saveBaseUrl(value: String)
}

class DataStoreSettingsStore(
    context: Context,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : SettingsStore {

    private val dataStore = context.applicationContext.settingsStore

    override suspend fun loadBaseUrl(): String? = withContext(ioDispatcher) {
        dataStore.data.first()[BASE_URL_KEY]
    }

    override suspend fun saveBaseUrl(value: String): Unit = withContext(ioDispatcher) {
        dataStore.edit { prefs -> prefs[BASE_URL_KEY] = value }
    }

    private companion object {
        val BASE_URL_KEY = stringPreferencesKey("api_base_url_v1")
    }
}
