package com.ailearningos.app.testutil

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.TestDispatcher
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.rules.TestWatcher
import org.junit.runner.Description

/**
 * 把 Dispatchers.Main 替换为 Unconfined 测试 dispatcher：
 * viewModelScope 的 launch 会即刻执行到首个真实挂起点，fake 网关不真正挂起，
 * 状态迁移在断言前即已落定（无需跨 scheduler 推进）。
 */
class MainDispatcherRule(
    val testDispatcher: TestDispatcher = UnconfinedTestDispatcher(),
) : TestWatcher() {

    override fun starting(description: Description) {
        Dispatchers.setMain(testDispatcher)
    }

    override fun finished(description: Description) {
        Dispatchers.resetMain()
    }
}
