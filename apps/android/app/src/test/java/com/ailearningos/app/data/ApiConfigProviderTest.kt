package com.ailearningos.app.data

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import com.ailearningos.app.core.BaseUrlPolicy
import com.ailearningos.app.testutil.FakeSettingsStore
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** API 地址解析：默认值、fail-closed（存储层不静默修复）、release 强制 https、错误态可恢复 */
class ApiConfigProviderTest {

    @Test
    fun `empty store falls back to emulator debug default`() = runTest {
        val provider = ApiConfigProvider(FakeSettingsStore(), allowInsecureHttp = true)
        assertEquals(BaseUrlPolicy.DEFAULT_DEBUG_BASE_URL, provider.resolve())
    }

    @Test
    fun `stored value is normalized`() = runTest {
        val provider = ApiConfigProvider(
            FakeSettingsStore("https://api.example.com"),
            allowInsecureHttp = false,
        )
        assertEquals("https://api.example.com/", provider.resolve())
    }

    @Test
    fun `debug allows stored lan http`() = runTest {
        val provider = ApiConfigProvider(
            FakeSettingsStore("http://192.168.1.5:8000"),
            allowInsecureHttp = true,
        )
        assertEquals("http://192.168.1.5:8000/", provider.resolve())
    }

    @Test
    fun `release without config rejects insecure default`() = runTest {
        val provider = ApiConfigProvider(FakeSettingsStore(), allowInsecureHttp = false)
        val error = catchesAppError { provider.resolve() }
        assertEquals(AppErrorKind.BAD_CONFIG, error.kind)
    }

    @Test
    fun `release rejects stored http`() = runTest {
        val provider = ApiConfigProvider(
            FakeSettingsStore("http://10.0.2.2:8000"),
            allowInsecureHttp = false,
        )
        val error = catchesAppError { provider.resolve() }
        assertEquals(AppErrorKind.BAD_CONFIG, error.kind)
    }

    @Test
    fun `error state is recoverable once store holds a valid value`() = runTest {
        val store = FakeSettingsStore("http://api.example.com")
        val provider = ApiConfigProvider(store, allowInsecureHttp = false)
        catchesAppError { provider.resolve() }
        // 用户在设置中改成合法 https 后，同一 provider 直接恢复
        store.stored = "https://api.example.com"
        assertEquals("https://api.example.com/", provider.resolve())
    }

    // ---------- 存储层 fail-closed：读到脏值一律拒绝，绝不静默修复 ----------

    @Test
    fun `stored value with leading or trailing whitespace is rejected not trimmed`() = runTest {
        val cases = listOf(
            "  https://api.example.com  ",
            "\thttps://api.example.com\n",
            " https://api.example.com",
            "https://api.example.com ",
        )
        for (case in cases) {
            val provider = ApiConfigProvider(FakeSettingsStore(case), allowInsecureHttp = true)
            val error = catchesAppError("case=<首尾空白 $case>") { provider.resolve() }
            assertEquals(AppErrorKind.BAD_CONFIG, error.kind)
        }
    }

    @Test
    fun `stored dirty values fail closed`() = runTest {
        // 本机绝对形态不入库的占位 host；这些值只在内存 fake 中断言拒绝行为
        val cases: Map<String, String> = mapOf(
            "内部空白" to "https://api.example.com /",
            "query" to "https://api.example.com/?x=1",
            "fragment" to "https://api.example.com/#section",
            "userinfo" to "https://user:pass@api.example.com/",
            "反斜杠" to "https://api.example.com\\path",
            "控制字符" to "https://api.example.com/\u0007",
            "纯空白" to "   ",
        )
        for ((label, stored) in cases) {
            val provider = ApiConfigProvider(FakeSettingsStore(stored), allowInsecureHttp = true)
            val error = catchesAppError("case=<$label>") { provider.resolve() }
            assertEquals(label, AppErrorKind.BAD_CONFIG, error.kind)
        }
    }

    @Test
    fun `dirty public http still rejected in debug`() = runTest {
        val provider = ApiConfigProvider(FakeSettingsStore("http://api.example.com "), allowInsecureHttp = true)
        val error = catchesAppError { provider.resolve() }
        assertEquals(AppErrorKind.BAD_CONFIG, error.kind)
    }

    /** inline 才能 carrier 挂起调用；断言必须抛 AppError */
    private inline fun catchesAppError(label: String = "", block: () -> Unit): AppError {
        try {
            block()
        } catch (expected: AppError) {
            return expected
        }
        throw AssertionError("期望抛出 AppError $label")
    }
}
