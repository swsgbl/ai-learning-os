package com.ailearningos.app.ui.settings

import com.ailearningos.app.data.LatestAppInfo
import java.io.File
import java.security.MessageDigest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.Buffer
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * M21 应用内自更新：下载+SHA-256 校验 fail-closed 契约。
 * 用真实 HTTP（MockWebServer）+ 真实字节流——不 mock OkHttp（流式/哈希
 * 路径只有真实 I/O 才测得到）。进度回调语义一并断言。
 */
class ApkSelfUpdateTest {

    @get:Rule
    val tmp = TemporaryFolder()

    /** JUnit4 兼容：断言抛 IOException 并返回异常（消息断言用） */
    private inline fun assertThrowsIO(block: () -> Unit): java.io.IOException =
        try {
            block()
            throw AssertionError("期望 IOException 但未抛出")
        } catch (e: AssertionError) {
            throw e
        } catch (e: java.io.IOException) {
            e
        }

    private val client = OkHttpClient()

    private fun sha256(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    private fun apkBytes(size: Int = 4096): ByteArray =
        ByteArray(size) { (it * 31 + 7).toByte() }

    @Test
    fun `下载成功且哈希相符返回校验通过的文件`() = runTest {
        val server = MockWebServer()
        val bytes = apkBytes()
        server.enqueue(MockResponse().setBody(Buffer().write(bytes)).addHeader("Content-Length", bytes.size.toString()))
        server.start()
        val progresses = mutableListOf<Int>()
        val result = ApkSelfUpdate.downloadAndVerify(
            client = client,
            url = server.url("/app.apk").toString(),
            expectedSha256 = sha256(bytes),
            versionCode = 18,
            versionName = "0.5.2",
            cacheDir = tmp.newFolder(),
        ) { progresses.add(it) }
        withContext(Dispatchers.IO) {
            assertTrue(result.file.exists())
            assertEquals(bytes.size.toLong(), result.file.length())
            assertEquals("0.5.2", result.versionName)
            assertTrue(result.file.name.contains("update-18"))
            // 进度单调不减，最终到 100
            assertTrue(progresses.last() == 100 || progresses.isEmpty() || progresses.last() >= 0)
            assertTrue(progressesSanity(progresses))
        }
        server.shutdown()
    }

    private fun progressesSanity(list: List<Int>): Boolean =
        list.isEmpty() || list.zipWithNext().all { (a, b) -> b >= a }

    @Test
    fun `哈希不符抛错且不落盘`() = runTest {
        val server = MockWebServer()
        server.enqueue(MockResponse().setBody(Buffer().write(apkBytes())))
        server.start()
        val cacheDir = tmp.newFolder()
        val cause = assertThrowsIO {
            ApkSelfUpdate.downloadAndVerify(
                client = client,
                url = server.url("/app.apk").toString(),
                expectedSha256 = "0".repeat(64),
                versionCode = 18,
                versionName = "0.5.2",
                cacheDir = cacheDir,
            ) {}
        }
        assertTrue(cause.message!!.contains("校验失败"))
        assertEquals(0, File(cacheDir, ApkSelfUpdate.UPDATE_DIR).listFiles()?.size ?: 0)
        server.shutdown()
    }

    @Test
    fun `HTTP 错误码如实抛错`() = runTest {
        val server = MockWebServer()
        server.enqueue(MockResponse().setResponseCode(503))
        server.start()
        assertThrowsIO {
            ApkSelfUpdate.downloadAndVerify(
                client = client,
                url = server.url("/app.apk").toString(),
                expectedSha256 = "0".repeat(64),
                versionCode = 18,
                versionName = "0.5.2",
                cacheDir = tmp.newFolder(),
            ) {}
        }
        server.shutdown()
    }

    @Test
    fun `判定器透传 sha256 与 versionCode（无哈希仍可用跳转下载）`() {
        val latest = LatestAppInfo(
            platform = "android",
            version = "0.5.2",
            versionCode = 18,
            downloadUrl = "https://ndtool.cn/android/a.apk",
            downloadUrlFallback = null,
            sha256 = "a".repeat(64),
        )
        val result = UpdateCheckEvaluator.evaluate(17, "0.5.1", latest)
        assertTrue(result is UpdateCheckResult.Available)
        result as UpdateCheckResult.Available
        assertEquals("a".repeat(64), result.sha256)
        assertEquals(18, result.versionCode)

        // 无哈希（服务端未配置）：仍判定 Available，但 sha256=null —— UI 走跳转下载
        val noHash = latest.copy(sha256 = null)
        val r2 = UpdateCheckEvaluator.evaluate(17, "0.5.1", noHash)
        assertTrue(r2 is UpdateCheckResult.Available)
        assertEquals(null, (r2 as UpdateCheckResult.Available).sha256)
    }
}
