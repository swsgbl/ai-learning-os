# M14-272a 证据：服务端语音会话管线组装器（工厂 sink 注入 + 纯域编排，fake-only）

- 分支 `api/m14-272-voice-pipeline`（worktree
  `ai-learning-os-worktrees/m14-272-voice-pipeline`），基于
  current main `516fbfe9`（PR #358 merge，即 M14-271）。执行前
  porcelain 干净（基点本地 git 可验证）。
- 动因：M14-267~271 五件纯域积木各自就绪但缺组装面——且受控
  工厂的订阅者 sink 是内部固定装配（帧路径无法接入会话级通道）。
  本切片是 M14-272（真实本机端到端验收）的第一子切片。

## 非声明（explicit non-claims)

- **组装器 ≠ 真实语音能力**：不接 HTTP 路由/lifespan、不实现
  FunASR adapter（adapter 由构造注入——真实适配属 M14-272b）、
  不连真实 LiveKit、不做 VAD 检测器；本机端到端验收属
  M14-272c。
- 工厂扩展**缺省零回归**：不传 `event_sink_factory` 时既有装配
  与 `sink_snapshot` 语义逐字不变（既有两套工厂测试零回归
  实证）。
- 零真实凭据（测试全为显眼假值）；未启停任何生产/运行时服务；
  不触碰 compose/env/Web/Android/Harmony/DB/迁移/recovery。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变；计数不变 implemented 60 / partial 3。

## 交付面

**`services/api/app/domain/voice_subscriber_factory.py`（扩展，
向后兼容）**：

- `ControlledVoiceSubscriberFactory` 新增可选
  `event_sink_factory: Callable[[], object] | None = None`：
  - 缺省：既有内部 `BoundedVoiceFrameSink(max_events)` 装配
    （零回归）；
  - 给定：`create()` 调用其产物作订阅者 event_sink——产物必须
    满足 on_event/on_frame 结构面（装配期确定性净化拒绝）；
  - 句柄 `sink_snapshot()`：注入 sink 无 `snapshot` 面时确定性
    净化拒绝（通用白名单文案）。

**`services/api/app/domain/voice_pipeline.py`（新增，纯域组装器）**：

- `ServerVoicePipeline(manager=, factory_builder=,
  asr_adapter_factory=)`：
  - `open_session(session_id, room=, identity=)`：每会话组装
    `SplitVoiceSink(计量汇, 通道)` → 经注入 `factory_builder(split)`
    构造绑定该 split 的受控订阅者工厂 → 经
    `VoiceSessionSubscriptionService.acquire` 注册（订阅只能由
    runtime manager 管理）→ `VoiceAsrStreamSession` +
    `VoiceSessionController`；同 session 幂等返回既有句柄
    （manager 条目恰一、工厂 create 恰一次）；
  - `close_session(session_id)`：确定性四段关停——①停帧源
    （订阅者 release：房间断开）→ ②消费残余帧（通道 drain →
    adapter.feed → transcript）→ ③ASR finish 冲刷尾段 → ④句柄
    转终态审计面（`final_transcript`/`state` 仍可查、pump/submit
    拒绝、close 幂等 no-op）；
  - `pump_session`/`submit_session`/`session_state`/
    `final_transcript`/`snapshot` 管线级操作面（未知会话确定性
    拒绝、close_session 幂等）；
- `ServerVoiceSessionHandle`：会话级操作面（pump/submit/
  submit_vad/transcript/control_snapshot/channel_snapshot/state
  组合投影）；会话隔离（每会话独立通道/transcript/控制器）。

## 测试（TDD：先 RED 后 GREEN）

`services/api/tests/test_voice_pipeline.py`（新增，15 项
fake-only 契约测试）：全真实积木（manager/受控工厂/订阅服务/
ASR 会话/控制器/管线）+ 最内层 fake 协作者（token/房间/流/
ASR adapter 记录器）。

1. 构造校验（manager/factory_builder/asr_adapter_factory 缺失
   三例）。
2. 工厂缺省 sink 行为零回归（计量语义原样）。
3. 工厂注入 sink 收帧（split 双路径：通道入界 + 计量同步）。
4. 注入 sink 无快照面 → handle.sink_snapshot 净化拒绝。
5. event_sink_factory 非 callable → 构造拒绝。
6. 注入产物无结构面 → create 装配期净化拒绝。
7. open_session：订阅者经 manager 在册（键 = voice-session-*）、
   emit 三帧 → pump 转写顺序逐字一致（adapter.fed == payloads）。
8. handle 控制：start_reading → READING_QUESTION；submit_vad
   (speech_start) → WAITING_ANSWER；重复 pause 幂等。
9. 幂等 open：同 session 同句柄、条目恰一、create 恰一次。
10. close 四段序：manager 零条目、房间断开、ASR 冲刷恰一次、
    close 后 pump/submit 拒绝、二次 close 幂等。
11. close 冲刷残余：未 pump 的帧在 close 中进 transcript
    （w0/w1 + tail）。
12. 会话隔离：A emit 只进 A 通道、控制器独立、双 close 后零
    条目。
13. ASR 全失败隔离：failed_chunk_count=3、管线存活（控制可用）。
14. 未知会话操作拒绝 + close 幂等 no-op。
15. 源码卫生（AST+文本）：域内导入白名单五模块；禁网络/文件/
    线程/asyncio import/LiveKit/FastAPI/HTTP client。

## 验证（2026-10-15，worktree 内真实执行；全部零网络/零真实凭据）

使用 canonical venv 解释器（`.venv/Scripts/python.exe`）：

| 命令 | 结果 |
| --- | --- |
| `-m pytest services/api/tests/test_voice_pipeline.py -q` | **15 passed** |
| `-m pytest`（十六文件语音回归：pipeline + session_control + asr_stream + frame_channel + session_subscriptions + entrypoint + app_wiring + app_factory + subscriber_factory + runtime_factory + frame_sink + subscription + runtime + runtime_manager + runtime_lifespan + subscriber_lifespan）`-q` | **448 passed**（M14-271 基线 433 零回归——含被扩展工厂的 subscriber_factory/app_factory 两套既有测试） |
| `-m pytest services/api/tests/test_m905_ownership_audit.py services/api/tests/test_compose_profiles.py services/api/tests/test_rc_smoke_rehearsal.py -q` | **93 passed, 1 skipped** |
| `-m ruff check`（触及三文件） | All checks passed |
| `-m compileall`（同三文件） | OK |
| `git diff --check` | 干净 |

TDD 顺序：先建测试确认 RED（模块不存在），实现后转 GREEN；
三轮修正（①emit 帧前需先触发 track_subscribed（订阅者只在
音轨到达后建流）；②close 语义改为句柄保留终态审计面（幂等
close 对已关句柄短路）；③多会话 emit 的房间索引参数化）。

## 后续空间（M14-272b/c，不在本切片）

- M14-272b：HTTP 接线（voice session 路由复用管线句柄 +
  lifespan 安装 pipeline）+ FunASR→VoiceAsrAdapterLike 适配器
  （batch transcribe 适配流式协议）；
- M14-272c：本机端到端验收（compose 栈 + 真实 LiveKit +
  FunASR + 浏览器录音；Web/Android/Harmony 分批）。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-272a（M14-271 转
  前一任务快照，含 PR #358 合并结果），里程碑段追加注记。
- `docs/ROADMAP.md`：M14 段新增 M14-272a 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-272a 条目（含非声明）。
