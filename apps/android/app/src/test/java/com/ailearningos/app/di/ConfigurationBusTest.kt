package com.ailearningos.app.di

import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

/** 配置变更广播：事件先发生、后订阅仍可收到（replay=1），且连续变更只保留最新 */
class ConfigurationBusTest {

    @Test
    fun `event emitted before subscription is replayed to late subscriber`() = runTest {
        val bus = ConfigurationBus()
        // 订阅者尚未出现时事件已发生
        bus.notifyChanged()

        val emissions = mutableListOf<Unit>()
        val collector = launch { bus.changes.collect { emissions.add(it) } }
        advanceUntilIdle()

        assertEquals("先发生的事件必须回放给后订阅者", 1, emissions.size)
        collector.cancel()
    }

    @Test
    fun `rapid changes collapse to latest for late subscriber`() = runTest {
        val bus = ConfigurationBus()
        repeat(5) { bus.notifyChanged() }

        val emissions = mutableListOf<Unit>()
        val collector = launch { bus.changes.collect { emissions.add(it) } }
        advanceUntilIdle()

        assertEquals("replay 槽位只有一个，连续变更折叠为最近一次", 1, emissions.size)
        collector.cancel()
    }

    @Test
    fun `no event means no emission for first subscriber`() = runTest {
        val bus = ConfigurationBus()

        val emissions = mutableListOf<Unit>()
        val collector = launch { bus.changes.collect { emissions.add(it) } }
        advanceUntilIdle()

        assertEquals(0, emissions.size)
        collector.cancel()
    }
}
