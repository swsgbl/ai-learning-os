package com.ailearningos.app.data.local

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 真机回归（M15-007 修复）：AndroidKeyStore 不允许「加密时」caller-provided IV
 * （InvalidAlgorithmParameterException 被上层吞掉 → remembered_credentials.bin
 * 静默不落盘，v0.1.2 真机实证）。JVM 单测走 FakeVault 覆盖不到 Keystore 路径，
 * 本测试在真机直接跑 save→load→clear 往返锁定该回归。
 */
@RunWith(AndroidJUnit4::class)
class CredentialVaultDiagTest {

    @Test
    fun saveLoadRoundtripOnDevice() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val vault = KeystoreCredentialVault(context)
        runBlocking {
            vault.clear()
            vault.save("diag_user", "diag-Pass-2026")
            assertEquals("diag_user" to "diag-Pass-2026", vault.load())
            vault.clear()
            assertNull(vault.load())
        }
    }
}
