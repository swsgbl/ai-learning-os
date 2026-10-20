package com.ailearningos.app.ui.progress

import com.ailearningos.app.data.remote.AiosJson
import com.ailearningos.app.data.remote.DailyPlanDto
import org.junit.Test
import java.io.File

/** M15-022：真实生产 payload 契约测试（从 ndtool.cn 实抓）。 */
class DailyPlanRealPayloadTest {
    @Test
    fun `parses real production payload`() {
        val raw = File(System.getenv("TEMP") + "/plan.json").readText()
        val plan = AiosJson.decodeFromString<DailyPlanDto>(raw)
        assert(plan.task_count > 0)
        println("tasks=${plan.task_count}, first=${plan.tasks[0].title}")
    }
}
