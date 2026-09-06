package com.ailearningos.app.data.local

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import java.security.KeyStore
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

private val Context.tokenSecureStore by preferencesDataStore(name = "aios_token_secure")

/**
 * M12-01 token 安全存储：Android Keystore AES-256/GCM 加密 + DataStore 存密文。
 *
 * - 密钥生成在 AndroidKeyStore 内，私钥材料不出安全硬件/系统密钥库；
 * - 落盘内容只有 Base64(iv ‖ ciphertext)（`aios_token_secure.preferences_pb`），
 *   无明文 token——源码级守卫测试锁定本文件不使用任何明文偏好/文件落盘形态；
 * - 解密失败（密钥轮换/密文损坏）fail-closed：当作未登录并清除残留密文，
 *   绝不把密文当明文返回；
 * - 保存空/空白 token 直接拒绝（不产生“看似已登录”的空会话）。
 */
class KeystoreTokenStore(
    context: Context,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
    private val keyAlias: String = DEFAULT_KEY_ALIAS,
) : TokenStore {

    private val dataStore = context.applicationContext.tokenSecureStore

    override suspend fun save(token: String): Unit = withContext(ioDispatcher) {
        if (token.isBlank()) throw TokenStorageException()
        try {
            val cipher = cipher(Cipher.ENCRYPT_MODE)
            val encrypted = cipher.doFinal(token.toByteArray(Charsets.UTF_8))
            val blob = cipher.iv + encrypted
            dataStore.edit { prefs ->
                prefs[TOKEN_BLOB_KEY] = Base64.getEncoder().encodeToString(blob)
            }
        } catch (cause: Exception) {
            throw TokenStorageException(cause)
        }
    }

    override suspend fun load(): String? = withContext(ioDispatcher) {
        val stored = try {
            dataStore.data.first()[TOKEN_BLOB_KEY]
        } catch (cause: Exception) {
            throw TokenStorageException(cause)
        } ?: return@withContext null
        try {
            val blob = Base64.getDecoder().decode(stored)
            if (blob.size <= GCM_IV_LENGTH_BYTES) throw TokenStorageException()
            val iv = blob.copyOfRange(0, GCM_IV_LENGTH_BYTES)
            val encrypted = blob.copyOfRange(GCM_IV_LENGTH_BYTES, blob.size)
            val plain = cipher(Cipher.DECRYPT_MODE, iv).doFinal(encrypted)
            String(plain, Charsets.UTF_8).ifEmpty { null }
        } catch (cause: Exception) {
            // 密文不可解 = 无有效登录态；顺手清掉残留密文（fail-closed，不降级）
            runCatching { dataStore.edit { prefs -> prefs.remove(TOKEN_BLOB_KEY) } }
            null
        }
    }

    override suspend fun clear(): Unit = withContext(ioDispatcher) {
        try {
            dataStore.edit { prefs -> prefs.remove(TOKEN_BLOB_KEY) }
        } catch (cause: Exception) {
            throw TokenStorageException(cause)
        }
    }

    private fun cipher(mode: Int, iv: ByteArray? = null): Cipher {
        val key = secretKey()
        return Cipher.getInstance(TRANSFORMATION).apply {
            if (iv == null) {
                init(mode, key)
            } else {
                init(mode, key, GCMParameterSpec(GCM_TAG_LENGTH_BITS, iv))
            }
        }
    }

    private fun secretKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getEntry(keyAlias, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }

        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(
                keyAlias,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(KEY_SIZE_BITS)
                .build(),
        )
        return generator.generateKey()
    }

    companion object {
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val DEFAULT_KEY_ALIAS = "aios_bearer_token_v1"
        private const val KEY_SIZE_BITS = 256
        private const val GCM_IV_LENGTH_BYTES = 12
        private const val GCM_TAG_LENGTH_BITS = 128
        private val TOKEN_BLOB_KEY = stringPreferencesKey("token_blob_v1")
    }
}
