package com.ailearningos.app.ui.progress

import com.ailearningos.app.data.remote.AiosJson
import com.ailearningos.app.data.remote.DailyPlanDto
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * M15-022 真实生产 payload 契约测试（从 ndtool.cn 实抓内嵌）：
 * 服务端 Python float 序列化 "priority": 2.0——Int 解析必失败（真机
 * 「API 返回数据格式异常」根因）。锁定 Double 解析不回归。
 */
class DailyPlanRealPayloadTest {

    private val realPayload = """{"generated_at": "2026-10-20T12:23:04.233306+00:00", "plan_date": "2026-10-20", "task_count": 2, "review_count": 0, "mistake_retry_count": 0, "new_learning_count": 10, "tasks": [{"kind": "new_learning", "question_id": "q_a54229ff913348af9c098f9524d3bcbf", "concept_ids": ["sql:select-basics"], "title": "请用自己的话解释什么是SELECT 基础查询，并举例说明。", "reason": "新概念起步：sql:select-basics 尚无学习记录，从易到难建立基础", "priority": 2.0}, {"kind": "new_learning", "question_id": "q_3511ccc12c46465c9a6c26bafb22cab4", "concept_ids": ["sql:select-basics"], "title": "请用自己的话解释什么是SELECT 基础查询，并举例说明。", "reason": "新概念起步：sql:select-basics 尚无学习记录，从易到难建立基础", "priority": 2.0}]}"""

    @Test
    fun `parses real production payload`() {
        val plan = AiosJson.decodeFromString<DailyPlanDto>(realPayload)
        assertEquals(2, plan.task_count)
        assertTrue(plan.tasks[0].priority > 0)
        assertTrue(plan.tasks[0].title.contains("SELECT"))
    }
}
