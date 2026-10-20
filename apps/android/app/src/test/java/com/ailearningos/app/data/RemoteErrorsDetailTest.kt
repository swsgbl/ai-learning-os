package com.ailearningos.app.data

import com.ailearningos.app.core.AppError
import com.ailearningos.app.core.AppErrorKind
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response

/** M15-019：服务端 detail 文案透出（422 的「支持主题」引导不再被吞成格式异常）。 */
class RemoteErrorsDetailTest {

    private fun httpError(code: Int, body: String): HttpException =
        HttpException(
            Response.error<Any>(code, body.toResponseBody("application/json".toMediaType()))
        )

    @Test
    fun `422 detail 字符串透出为 userMessage`() {
        val error = httpError(
            422,
            """{"detail":"目标未匹配到任何概念。当前已发布概念图支持的主题：SELECT 基础查询、表连接 JOIN"}""",
        ).asAppError()
        assertEquals(AppErrorKind.BAD_RESPONSE, error.kind)
        assertEquals(
            "目标未匹配到任何概念。当前已发布概念图支持的主题：SELECT 基础查询、表连接 JOIN",
            error.userMessage,
        )
    }

    @Test
    fun `detail 数组形态回落固定文案`() {
        // Fastapi 请求校验错误 detail 是数组——不透出，走固定脱敏文案
        val error = httpError(422, """{"detail":[{"loc":["body"],"msg":"bad"}]}""").asAppError()
        assertEquals("API 返回数据格式异常", error.userMessage)
    }

    @Test
    fun `502 detail 透出，非 JSON 响应回落`() {
        val e1 = httpError(502, """{"detail":"接口地址不可达"}""").asAppError()
        assertEquals("接口地址不可达", e1.userMessage)
        val e2 = httpError(500, "<html>oops</html>").asAppError()
        assertEquals("服务暂时不可用，请稍后重试", e2.userMessage)
    }

    @Test
    fun `AppError 带 override 优先`() {
        assertEquals("自定义", AppError(AppErrorKind.NETWORK, messageOverride = "自定义").userMessage)
        assertNull(null)
    }
}
