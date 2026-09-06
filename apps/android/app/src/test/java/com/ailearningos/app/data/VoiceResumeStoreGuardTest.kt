package com.ailearningos.app.data

import com.ailearningos.app.data.local.VoiceResumeSlot
import com.ailearningos.app.testutil.FakeVoiceResumeStore
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import kotlin.io.path.readText
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 语音恢复槽安全边界守卫（M12-03）：
 * - 本地只允许保存 session_id / exam_id 两个最小元数据键——接口面与存储键
 *   源码级锁定；语音音频、transcript、答案、判分结果绝不落本地；
 * - 存储契约经 fake 实现验证（save/load/clear，槽位单值覆盖语义、残缺槽不复活）。
 */
class VoiceResumeStoreGuardTest {

    @Test
    fun `fake resume store round trip overwrite and clear`() = runTest {
        val store = FakeVoiceResumeStore()
        assertNull(store.load())
        store.save(VoiceResumeSlot(sessionId = "vs-a", examId = "exam-a"))
        assertEquals(VoiceResumeSlot("vs-a", "exam-a"), store.load())
        // 单槽覆盖：新会话覆盖旧恢复提示
        store.save(VoiceResumeSlot(sessionId = "vs-b", examId = "exam-b"))
        assertEquals(VoiceResumeSlot("vs-b", "exam-b"), store.load())
        store.clear()
        assertNull(store.load())
        assertEquals(1, store.clearCount)
    }

    @Test
    fun `resume store implementation only persists session and exam id keys`() {
        val source = sourceFile("data/local/VoiceResumeStore.kt").readText()
        val keys = KEY_REGEX.findAll(source).map { it.groupValues[1] }.toList()
        assertEquals(
            "语音恢复槽只允许 session_id/exam_id 两个键",
            listOf("voice_resume_session_id_v1", "voice_resume_exam_id_v1"),
            keys,
        )
        assertEquals("只允许一个 DataStore 实例（不得旁路第二个存储面）", 1, countOccurrences(source, "preferencesDataStore("))
    }

    @Test
    fun `resume interface surface is string pair only`() {
        val source = sourceFile("data/local/VoiceResumeStore.kt").readText()
        // 接口声明块（到第一个实现 class 为止）：音频（ByteArray）、答案（Map）、
        // 判分（Double）形态均不允许出现在方法签名里——出现即代表有人往恢复槽
        // 塞了真相源数据。
        val interfaceBlock = source.substringAfter("interface VoiceResumeStore {").substringBefore("\nclass ")
        assertTrue("接口面不得出现二进制形态（音频）", !interfaceBlock.contains("ByteArray"))
        assertTrue("接口面不得出现集合容器（答案类形态）", !interfaceBlock.contains("Map<") && !interfaceBlock.contains("List<"))
        assertTrue("接口面不得出现浮点（判分类形态）", !interfaceBlock.contains("Double"))
    }

    // ---------- 源码定位（与 ExamResumeStoreGuardTest 同款） ----------

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

    private fun countOccurrences(text: String, token: String): Int = text.split(token).size - 1

    private companion object {
        val KEY_REGEX = Regex("""stringPreferencesKey\("([^"]+)"\)""")
    }
}
