package com.ailearningos.app.data

import com.ailearningos.app.data.remote.AiosJson
import com.ailearningos.app.data.remote.SearchPlanResponse
import com.ailearningos.app.data.remote.SearchProvidersResponse
import com.ailearningos.app.data.remote.SearchQueryResponse
import com.ailearningos.app.data.remote.SearchRecordResponse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * M12-04 搜索 DTO 解析：snake_case 与服务端 search.py 契约一一对应；
 * 可空字段（unavailable_reason/slots/authority）保留 null 不猜语义；
 * 未知键容忍（服务端演进）、缺必填字段拒绝。
 */
class SearchDtoParsingTest {

    private val json = AiosJson

    // ---------- providers ----------

    @Test
    fun `providers parses enabled and disabled entries with reason`() {
        val dto = json.decodeFromString<SearchProvidersResponse>(
            """{"items":[
                {"name":"local-corpus","kind":"local-corpus","enabled":true,"unavailable_reason":null},
                {"name":"cloud-web","kind":"web","enabled":false,
                 "unavailable_reason":"SEARCH_MODE=local：检索路由本地语料，cloud-web 不出站"}],
               "future_field":1}""",
        )
        assertEquals(2, dto.items.size)
        assertTrue(dto.items[0].enabled)
        assertNull(dto.items[0].unavailableReason)
        assertEquals("web", dto.items[1].kind)
        assertEquals("SEARCH_MODE=local：检索路由本地语料，cloud-web 不出站", dto.items[1].unavailableReason)
    }

    @Test
    fun `providers parses empty registry`() {
        val dto = json.decodeFromString<SearchProvidersResponse>("""{"items":[]}""")
        assertTrue(dto.items.isEmpty())
    }

    @Test
    fun `providers entry missing required name is rejected`() {
        assertTrue(
            runCatching {
                json.decodeFromString<SearchProvidersResponse>("""{"items":[{"kind":"web","enabled":true}]}""")
            }.isFailure,
        )
    }

    // ---------- plan ----------

    @Test
    fun `plan parses slots and per provider entries`() {
        val dto = json.decodeFromString<SearchPlanResponse>(
            """{"query":"2024年清华大学高等数学公开课多项选择题",
                "slots":{"subject":null,"school":"清华大学","year":"2024","course":"高等数学",
                         "question_type":"多项选择题","publicity":"公开课"},
                "plan":[
                    {"provider":"local-corpus","query":"高等数学 多项选择题 公开课 清华大学 2024",
                     "enabled":true,"unavailable_reason":null},
                    {"provider":"cloud-web","query":"高等数学 多项选择题 公开课 清华大学 2024",
                     "enabled":false,"unavailable_reason":"SEARCH_MODE=local：检索路由本地语料，cloud-web 不出站"}]}""",
        )
        assertEquals("2024年清华大学高等数学公开课多项选择题", dto.query)
        assertNull(dto.slots.subject)
        assertEquals("清华大学", dto.slots.school)
        assertEquals("2024", dto.slots.year)
        assertEquals("高等数学", dto.slots.course)
        assertEquals("多项选择题", dto.slots.questionType)
        assertEquals("公开课", dto.slots.publicity)
        assertEquals(listOf("local-corpus", "cloud-web"), dto.plan.map { it.provider })
        assertTrue(dto.plan[0].enabled)
        assertNull(dto.plan[0].unavailableReason)
        assertTrue(!dto.plan[1].enabled)
        assertEquals("SEARCH_MODE=local：检索路由本地语料，cloud-web 不出站", dto.plan[1].unavailableReason)
    }

    @Test
    fun `plan keeps all null slots when nothing recognized`() {
        val dto = json.decodeFromString<SearchPlanResponse>(
            """{"query":"帮我找点复习资料",
                "slots":{"subject":null,"school":null,"year":null,"course":null,
                         "question_type":null,"publicity":null},
                "plan":[{"provider":"local-corpus","query":"帮我找点复习资料",
                         "enabled":true,"unavailable_reason":null}]}""",
        )
        assertNull(dto.slots.subject)
        assertNull(dto.slots.school)
        assertNull(dto.slots.year)
        assertNull(dto.slots.course)
        assertNull(dto.slots.questionType)
        assertNull(dto.slots.publicity)
        assertEquals("帮我找点复习资料", dto.plan[0].query)
    }

    @Test
    fun `plan missing slots object is rejected`() {
        assertTrue(
            runCatching {
                json.decodeFromString<SearchPlanResponse>("""{"query":"词","plan":[]}""")
            }.isFailure,
        )
    }

    // ---------- queries 执行 ----------

    @Test
    fun `query response parses results skipped and counters`() {
        val dto = json.decodeFromString<SearchQueryResponse>(
            """{"query_id":42,"query":"正弦定理",
                "providers_requested":["local-corpus","cloud-web"],
                "results":[{"title":"正弦定理的内容","url":"https://x.invalid/a",
                            "snippet":"a/sinA=b/sinB","source":"local-corpus","provider":"local-corpus",
                            "authority":null,"rank_reason":"本地语料命中：关键词重叠"},
                           {"title":"Result B","url":"https://x.invalid/b","snippet":"内容 B",
                            "source":"cloud-web","provider":"cloud-web",
                            "authority":"official","rank_reason":"云端来源：官方域名加权"}],
                "skipped":[{"provider":"cloud-web","reason":"SEARCH_CLOUD_ENDPOINT 未配置"}],
                "result_count":2,"duration_ms":35}""",
        )
        assertEquals(42L, dto.queryId)
        assertEquals(listOf("local-corpus", "cloud-web"), dto.providersRequested)
        assertEquals(2, dto.results.size)
        assertNull(dto.results[0].authority)
        assertEquals("official", dto.results[1].authority)
        assertEquals("云端来源：官方域名加权", dto.results[1].rankReason)
        assertEquals(listOf("cloud-web"), dto.skipped.map { it.provider })
        assertEquals("SEARCH_CLOUD_ENDPOINT 未配置", dto.skipped[0].reason)
        assertEquals(2, dto.resultCount)
        assertEquals(35, dto.durationMs)
    }

    @Test
    fun `query response parses zero result outcome`() {
        val dto = json.decodeFromString<SearchQueryResponse>(
            """{"query_id":43,"query":"不存在的词","providers_requested":["local-corpus"],
                "results":[],"skipped":[],"result_count":0,"duration_ms":3}""",
        )
        assertTrue(dto.results.isEmpty())
        assertTrue(dto.skipped.isEmpty())
        assertEquals(0, dto.resultCount)
    }

    @Test
    fun `query response unknown provider skipped keeps server reason`() {
        val dto = json.decodeFromString<SearchQueryResponse>(
            """{"query_id":44,"query":"任意","providers_requested":["no-such"],
                "results":[],"skipped":[{"provider":"no-such","reason":"未知搜索源"}],
                "result_count":0,"duration_ms":1}""",
        )
        assertEquals("未知搜索源", dto.skipped[0].reason)
    }

    @Test
    fun `query response missing result count is rejected`() {
        assertTrue(
            runCatching {
                json.decodeFromString<SearchQueryResponse>("""{"query_id":1,"query":"词"}""")
            }.isFailure,
        )
    }

    // ---------- queries 回查 ----------

    @Test
    fun `record response parses stored record shape`() {
        val dto = json.decodeFromString<SearchRecordResponse>(
            """{"id":42,"query":"正弦定理",
                "providers_requested":["local-corpus","cloud-web"],
                "providers_skipped":[{"provider":"cloud-web","reason":"SEARCH_MODE=local：检索路由本地语料，cloud-web 不出站"}],
                "result_count":1,"duration_ms":12,
                "results":[{"title":"正弦定理的内容","url":"https://x.invalid/a",
                            "snippet":"a/sinA=b/sinB","source":"local-corpus","provider":"local-corpus",
                            "authority":"community","rank_reason":"本地语料命中：关键词重叠"}],
                "created_at":"2026-09-07T02:00:00+00:00"}""",
        )
        assertEquals(42L, dto.id)
        assertEquals("community", dto.results[0].authority)
        assertEquals(listOf("cloud-web"), dto.providersSkipped.map { it.provider })
        assertEquals("2026-09-07T02:00:00+00:00", dto.createdAt)
    }

    @Test
    fun `record response without skipped parses empty list`() {
        val dto = json.decodeFromString<SearchRecordResponse>(
            """{"id":45,"query":"词","providers_requested":["local-corpus"],
                "result_count":0,"duration_ms":2,"results":[],
                "created_at":"2026-09-07T02:00:00+00:00"}""",
        )
        assertTrue(dto.providersSkipped.isEmpty())
        assertEquals(0, dto.resultCount)
    }
}
