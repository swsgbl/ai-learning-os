package com.ailearningos.app.data

import com.ailearningos.app.testutil.FakeTokenStore
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import kotlin.io.path.isRegularFile
import kotlin.io.path.readText
import kotlin.streams.toList

/**
 * token 安全边界守卫（M12-01）：
 * - 存储契约经 fake 实现验证（save/load/clear）；
 * - 源码级守卫锁定：Keystore 实现不落明文（无 SharedPreferences）、
 *   配置存储无 token 键、data/ui 层零日志输出（token/base URL/header 不进日志）。
 */
class TokenStorageGuardTest {

    @Test
    fun `fake store round trip and clear`() = runTest {
        val store = FakeTokenStore()
        assertNull(store.load())
        store.save("fixture-token")
        assertEquals("fixture-token", store.load())
        store.clear()
        assertNull(store.load())
        assertEquals(1, store.clearCount)
    }

    @Test
    fun `keystore implementation uses keystore crypto not plaintext prefs`() {
        val source = sourceFile("data/local/KeystoreTokenStore.kt").readText()
        assertTrue("必须使用 AndroidKeyStore", source.contains("AndroidKeyStore"))
        assertTrue("必须使用 AES/GCM 加密", source.contains("AES/GCM/NoPadding"))
        assertTrue(
            "token 不允许走 SharedPreferences（明文形态）",
            !source.contains("SharedPreferences"),
        )
        assertTrue(
            "token 不允许写普通文件（openFileOutput 形态）",
            !source.contains("openFileOutput"),
        )
    }

    @Test
    fun `settings store holds no token key`() {
        val source = sourceFile("data/local/SettingsStore.kt").readText()
        val keys = KEY_REGEX.findAll(source).map { it.groupValues[1] }.toList()
        assertTrue(
            "普通配置存储不得保存 token 类键：$keys",
            keys.none { it.contains("token", ignoreCase = true) },
        )
    }

    @Test
    fun `data and ui layers never print or log`() {
        val offenders = mainSourceFiles()
            .filter { isGuardedLayer(it) }
            .filter { path ->
                val text = path.readText()
                text.contains("println(") || text.contains("Log.") || text.contains("printStackTrace")
            }
        assertTrue(
            "data/ui 层不允许出现日志或标准输出：${offenders.map { it.fileName }}",
            offenders.isEmpty(),
        )
    }

    @Test
    fun `no source contains credential-shaped constants`() {
        val offenders = mainSourceFiles().filter { path ->
            val text = path.readText()
            text.contains("sk-") || text.contains("AKIA") || text.contains("Bearer eyJ")
        }
        assertTrue("发现可疑凭据形态常量：${offenders.map { it.fileName }}", offenders.isEmpty())
    }

    // ---------- 源码定位（与运行目录无关） ----------

    private fun sourceFile(name: String): Path = sourceRoot().resolve(name)

    private fun sourceRoot(): Path {
        var dir: Path? = Paths.get(System.getProperty("user.dir")).toAbsolutePath()
        while (dir != null) {
            val candidate = dir.resolve("src").resolve("main").resolve("java")
                .resolve("com").resolve("ailearningos").resolve("app")
            if (Files.isDirectory(candidate)) return candidate
            dir = dir.parent
        }
        throw IllegalStateException("未找到 app 模块源码目录（user.dir=${System.getProperty("user.dir")}）")
    }

    private fun mainSourceFiles(): List<Path> = Files.walk(sourceRoot()).use { stream ->
        stream.filter { path -> path.isRegularFile() && path.toString().endsWith(".kt") }.toList()
    }

    /** 守卫面：data 与 ui 两个包层（相对 sourceRoot 的首段） */
    private fun isGuardedLayer(path: Path): Boolean {
        val first = sourceRoot().relativize(path).first().toString()
        return first == "data" || first == "ui"
    }

    private companion object {
        val KEY_REGEX = Regex("""stringPreferencesKey\("([^"]+)"\)""")
    }
}
