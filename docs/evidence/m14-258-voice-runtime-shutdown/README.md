# M14-258 证据：语音运行时管理器关闭/排空边界（fake-only 验证）

- 分支 `api/m14-258-voice-runtime-shutdown`（worktree
  `ai-learning-os-worktrees/m14-258-voice-runtime-shutdown`），基于
  current main `32c8715a`（PR #344 merge，即 M14-257）。执行前
  porcelain 干净（基点本地 git 可验证）。
- 动因：M14-257 的显式管理器缺少安全的关闭/排空边界——未来 API
  lifespan 关停时需要「排空中拒绝新启动、pending 一并排空、永久
  关闭终态幂等」的确定性语义。本切片在
  `voice_runtime_manager.py` 上保守扩展（订阅者控制器/LiveKit
  适配层零改动），**fake-only、FastAPI 独立**：不连接 LiveKit、
  不接 lifespan/HTTP 路由、不做流式 ASR/VAD/barge-in/pause-resume。

## 非声明（explicit non-claims）

- **fake-only 关闭/排空验证**：零真实网络、零真实 LiveKit 连接、
  零运行时服务启停；lifespan 接线是后续切片。
- **无 FastAPI startup/lifespan/HTTP 路由/settings/容器/调度器
  变更**（voice_subscription/voice_runtime/voice_tokens/voice.py
  全部零触碰）。
- **无流式 ASR / VAD / barge-in / pause/resume**。
- **不声明 M4-09 或 capability gap 3 关闭**：矩阵计数不变
  implemented 60 / partial 3。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变。

## 交付面

**`services/api/app/domain/voice_runtime_manager.py`（保守扩展，
M14-257 语义全保留）**：

- **显式生命周期状态**：`STATE_OPEN/STATE_DRAINING/STATE_CLOSED`
  常量 + `snapshot()["state"]`（closed > draining > open 的显示
  优先级；close 一经开始即报 closed——诚实终态）。
- **排空/关闭屏障**：`start()` 在键校验后、任何 entries/starting
  查询与 factory 构造之前检查——`_closed` 永拒（「管理器已关闭
  （永久终态），拒绝新启动」）、`_draining` 临时拒（「管理器正在
  排空（stop_all/close 进行中），拒绝新启动（在构造之前拒绝）」）。
  屏障先于任何 await 生效（单线程事件循环无竞态窗口）。
- **`stop_all()` 排空屏障**：置 `_draining` → 共享排空任务
  （`_drain_task` + `asyncio.shield`——并发 stop_all/close 去重，
  绝不双停；调用方取消只解除自身等待，任务继续）→ 键排序遍历
  `entries ∪ starting`（pending 键经 `stop(key)` 等待落定后清理）
  → **finally 恢复屏障**（清理抛错也不卡在 draining）。正常完成
  后回 open——管理器仍可接受新启动。
- **`close()` 永久终态**：入口立即 `_closed=True`（屏障即刻生效）
  → 与进行中的 stop_all 共享同一排空任务或新建 → 排空全部条目
  与 pending → `_on_drain_done` 归属清理（清映射 + 已关闭时记录
  `_close_outcome` + 消费任务异常防 unobserved）。**幂等**：重复
  close 返回记录的结果（no-op，绝不重排空/双停）；**并发去重**：
  gather 三路 close 共享一任务、结果一致、订阅者 stop 恰一次。
  **无重开操作**。
- **确定性清理**：排空循环单项 `stop()` 失败计数继续（fail-closed
  条目仍被移除）；结果恒为纯计数 `{stopped, failed}`。
- **M14-257 全语义保持**：不透明键校验、容量含进行中预留、同键
  并发/取消调用方去重（_starting 归属清理）、pending-start 停止
  语义、白名单+截断+JWT 正则的有界净化投影、异常链抑制
  （`from None`）。

## 测试覆盖（44 项 = 36 项 M14-257 回归 + 8 项 M14-258 聚焦）

M14-258 新增 8 项（全部零网络/零运行时/全 fake）：

1. **stop_all 屏障**：慢启动排空进行中，并发新 start 在 factory
   构造之前被拒（新工厂调用计数 0），pending 产物仍被排空
   （stop_calls 1、条目清零）。
2. **屏障恢复**：正常 stop_all 完成后 snapshot 回 open 且可重新
   start（factory 第二次调用成功）。
3. **close 含 pending**：close 等待 pending 启动落定、停掉产物、
   移除条目、终态 closed（outcome {stopped:1, failed:0}）。
4. **close 永久终态 + 幂等**：close 后 start 永拒（在构造之前，
   later 工厂零调用零启动）；重复 close 返回同结果且订阅者
   stop_calls 不变（绝不双停）。
5. **并发 close**：gather 三路 close 共享排空——结果一致、订阅者
   stop 恰一次、终态 closed。
6. **close 单项失败容错**：三条目其一 stop 抛错（异常消息携带
   JWT 形态）——其余两条目照常停止移除、outcome 纯计数
   {stopped:2, failed:1}、全部条目清零、closed；排空结果序列化
   无 JWT 形态。
7. **状态暴露**：open → draining（stop_all 进行中，投影无订阅者
   内部对象）→ open → closed 全状态序列可见。
8. **关闭面净化**：stop 抛错订阅者经 close 排空后，outcome 与
   snapshot 序列化均无 FAKE_JWT/JWT 形态；closed 后 start 拒绝
   错误 `__cause__ is None` 且消息无泄漏。

**有效性 RED 抽查（三项，均 CRLF 锚定后实证）**：A——移除
start() 屏障块 → 屏障/终态/净化三项测试立即失败；B——移除
close 幂等守卫（重复 close 重排空）→ 终态幂等测试失败；C——
移除 _drain 的 finally 屏障恢复 → open 恢复测试失败。三项恢复
后 44 全绿。

## 验证（2026-10-13，worktree 内真实执行；全部零网络/零运行时服务）

| 命令 | 结果 |
| --- | --- |
| `... -m pytest services/api/tests/test_voice_runtime_manager.py -q` | **44 passed**（36 基线零回归 + 8 新增；含三项 RED→GREEN 抽查复跑） |
| `... -m pytest services/api/tests/test_voice_subscription.py services/api/tests/test_voice_runtime.py services/api/tests/test_voice_runtime_manager.py services/api/tests/test_voice_tokens.py services/api/tests/test_voice_providers.py -q` | **136 passed**（21+26+44+16+29；邻居零回归） |
| `... -m ruff check services/api/app/domain/voice_subscription.py services/api/app/domain/voice_runtime.py services/api/app/domain/voice_runtime_manager.py services/api/tests/test_voice_subscription.py services/api/tests/test_voice_runtime.py services/api/tests/test_voice_runtime_manager.py` | All checks passed（`--fix` 清理 1 处 `__all__` 排序后） |
| `git diff --check` | 干净（无 whitespace 错误） |

新增/改动行卫生扫描：secret 形态 0 命中；JWT `eyJ` 命中均为合成
假值（FAKE_JWT 断言样本 + 泄漏防线正则）；私有绝对路径 0；U+FFFD
0；readiness 置 true 0。

## 后续空间（不在本切片）

- FastAPI lifespan 持有 `VoiceRuntimeManager` 并在关停时 `close()`
  （受控运行时切片）；帧消费者；VAD/barge-in/pause-resume；
  真实 LiveKit 服务的受控验收。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-258（M14-257 转前一
  任务快照），里程碑段追加 M14-258 注记（计数不变）。
- `docs/ROADMAP.md`：M14 段新增 M14-258 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-258 条目（含非声明）。
