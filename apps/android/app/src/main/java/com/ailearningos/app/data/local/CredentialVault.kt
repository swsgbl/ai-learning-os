package com.ailearningos.app.data.local

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.io.File
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * M15-007「记住密码」凭据保险库：用户名+密码经 Android Keystore AES-GCM 加密落盘。
 *
 * 边界（与 [KeystoreTokenStore] 同款）：
 * - 密钥由 Keystore 生成与保管（专用 KEY_ALIAS，不出安全硬件）；
 * - 明文密码只存在于内存与 Keystore 加密产物中，绝不写明文文件/日志；
 * - 用户取消「记住密码」→ [clear] 抹除密文；
 * - 密文损坏/密钥不可用等异常 → 调用方按「未记住」处理（fail-open 到手动
 *   输入，不阻断登录）。
 */
interface CredentialVault {
    suspend fun save(username: String, password: String)
    suspend fun load(): Pair<String, String>?
    suspend fun clear()
}

private const val KEY_ALIAS = "aios_remembered_credentials_key"
private const val FILE_NAME = "remembered_credentials.bin"
private const val IV_BYTES = 12
private const val GCM_TAG_BITS = 128

class KeystoreCredentialVault(
    context: Context,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : CredentialVault {

    private val file = File(context.filesDir, FILE_NAME)

    override suspend fun save(username: String, password: String): Unit = withContext(ioDispatcher) {
        val cipher = cipher(Cipher.ENCRYPT_MODE)
        val plaintext = "$username\n$password".toByteArray(Charsets.UTF_8)
        file.writeBytes(cipher.iv + cipher.doFinal(plaintext))
    }

    override suspend fun load(): Pair<String, String>? = withContext(ioDispatcher) {
        if (!file.isFile) return@withContext null
        try {
            val blob = file.readBytes()
            if (blob.size <= IV_BYTES + GCM_TAG_BITS / 8) return@withContext null
            val iv = blob.copyOfRange(0, IV_BYTES)
            val ciphertext = blob.copyOfRange(IV_BYTES, blob.size)
            val plaintext = cipher(Cipher.DECRYPT_MODE, iv).doFinal(ciphertext).toString(Charsets.UTF_8)
            val separator = plaintext.indexOf('\n')
            if (separator <= 0) null else plaintext.substring(0, separator) to plaintext.substring(separator + 1)
        } catch (_: Throwable) {
            // 密文损坏/密钥轮换等：按未记住处理（静默丢弃旧密文，不阻断登录）
            file.delete()
            null
        }
    }

    override suspend fun clear(): Unit = withContext(ioDispatcher) {
        file.delete()
    }

    private fun cipher(mode: Int, iv: ByteArray? = null): Cipher {
        val effectiveIv = iv ?: ByteArray(IV_BYTES).also { SecureRandom().nextBytes(it) }
        return Cipher.getInstance("AES/GCM/NoPadding").apply {
            init(mode, secretKey(), GCMParameterSpec(GCM_TAG_BITS, effectiveIv))
        }
    }

    private fun secretKey(): SecretKey {
        val keyStore = java.security.KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return generator.generateKey()
    }
}
