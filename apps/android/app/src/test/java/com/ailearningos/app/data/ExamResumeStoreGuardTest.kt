package com.ailearningos.app.data

import com.ailearningos.app.testutil.FakeExamResumeStore
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import kotlin.io.path.readText

/**
 * 恢复槽安全边界守卫（M12-02）：
 * - 本地只允许保存 exam_id 作为恢复提示——接口面与存储键源码级锁定，
 *   答案（Map）/ 剩余时间（Int）/ 判分结果绝不落本地；
 * - 存储契约经 fake 实现验证（save/load/clear，槽位单值覆盖语义）。
 */
class ExamResumeStoreGuardTest {

    @Test
    fun `fake resume store round trip overwrite and clear`() = runTest {
        val store = FakeExamResumeStore()
        assertNull(store.load())
        store.save("exam-a")
        assertEquals("exam-a", store.load())
        // 单槽覆盖：开新考覆盖旧恢复提示
        store.save("exam-b")
        assertEquals("exam-b", store.load())
        store.clear()
        assertNull(store.load())
        assertEquals(1, store.clearCount)
    }

    @Test
    fun `resume store implementation only persists exam id key`() {
        val source = sourceFile("data/local/ExamResumeStore.kt").readText()
        val keys = KEY_REGEX.findAll(source).map { it.groupValues[1] }.toList()
        assertEquals("恢复槽只允许一个 exam_id 键", listOf("resume_exam_id_v1"), keys)
        assertEquals("只允许一个 DataStore 实例（不得旁路第二个存储面）", 1, countOccurrences(source, "preferencesDataStore("))
    }

    @Test
    fun `resume interface surface is exam id string only`() {
        val source = sourceFile("data/local/ExamResumeStore.kt").readText()
        // 接口声明块（到第一个实现 class 为止）：答案 Map / 时间 Int / 分数均不允许出现在
        // 方法签名里——出现即代表有人往恢复槽塞了真相源数据。
        val interfaceBlock = source.substringAfter("interface ExamResumeStore {").substringBefore("\nclass ")
        assertTrue("接口面不得出现集合容器（答案类形态）", !interfaceBlock.contains("Map<") && !interfaceBlock.contains("List<"))
        assertTrue("接口面不得出现数值类型（时间/分数类形态）", !interfaceBlock.contains("Int") && !interfaceBlock.contains("Double"))
    }

    // ---------- 源码定位（与 TokenStorageGuardTest 同款） ----------

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
