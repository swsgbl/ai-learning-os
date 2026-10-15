# M14-268 证据：服务端订阅生命周期编排（纯域服务，fake-only）

- 分支 `api/m14-268-voice-session-subscriptions`（worktree
  `ai-learning-os-worktrees/m14-268-voice-session-subscriptions`），
  基于 current main `a131494f`（PR #354 merge，即 M14-267）。
  执行前 porcelain 干净（基点本地 git 可验证）。
- 动因：M14-267 让部署入口注册受控订阅者工厂，但 voice session
  仍无法**显式申请/释放**服务端订阅者。本切片补上纯域编排层：
  `(session_id → 管理键)` 确定性映射 + 参数先行校验，叠在
  `VoiceRuntimeManager`（M14-257/258）与
  `ControlledVoiceSubscriberFactory`（M14-261/264/265）之上。

## 非声明（explicit non-claims）

- **订阅者只能由 runtime manager 管理**：编排服务绝不直接
  构造/启动/停止订阅者——工厂 `create` 只作为
  `manager.start(key, factory)` 的零参闭包进入（构造恰发生在
  管理器启动路径内、容量检查之后；AST 调用面白名单锚定）。
- **编排 ≠ 运行时能力**：不连真实 LiveKit、不签发真实 token、
  不加 HTTP 路由/readiness、不进 FastAPI lifespan、不做流式
  ASR/VAD/barge-in/pause-resume、不建帧管道（后续切片）。
- **零真实凭据**：测试全用显眼假值（`fake-api-key-000000`、
  合成 JWT 形态假 token）；fake 协作者零网络。
- **单一事实源**：服务无自身 session→订阅者映射/计数/历史——
  申请去重、容量、排空、终态拒绝、净化投影全部复用 manager
  既有语义；`snapshot()` 纯透传。
- **错误零包装**：manager/工厂错误原样透传（本层不包装、不改
  写、不拼接任何值/文本）；本层自身错误仅参数/构造校验白名单
  文案。
- 未启停任何 API/Docker/LiveKit/ASR/TTS/emulator/代理/生产
  服务；不触碰 Web/Android/Harmony/DB/迁移/recovery/edge/
  TURN/compose。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变；计数不变 implemented 60 / partial 3。

## 交付面

**`services/api/app/domain/voice_session_subscriptions.py`（新增，
纯域服务）**：

- `validate_session_id` / `session_key`：session_id 保守边界与
  room/identity 同口径 `^[A-Za-z0-9_-]{3,64}$`（复用工厂公开
  正则 `ROOM_IDENTITY_PATTERN`，不另立宽松边界；精确标识符不
  trim）；`session_key = "voice-session-" + canonical session_id`
  （13+64=77 < 管理器 `KEY_MAX_LENGTH` 128）。
- `VoiceSessionSubscriptionService`（slots 锁死属性面）：
  - `acquire(session_id, *, room, identity)`：参数先行校验
    （session/room/identity 任一非法在任何 manager 调用之前
    失败——不占容量预留、不进启动去重面、工厂零调用）→
    `manager.start(key, 零参闭包)`；闭包捕获**已校验**
    canonical room/identity，恰确参数到达工厂；返回纯透传
    manager 净化有界投影。重复 acquire 同 session 复用首次
    结果（manager 每键去重）。
  - `release(session_id)`：`manager.stop(key)`——未知/已释放
    幂等 no-op；条目移除后同键可重新 acquire（非"重启"：
    manager 终态语义是对**在册终态键**拒绝，移除后即新 start）。
  - `snapshot()`：纯透传 manager 总投影。
  - 构造校验：manager/factory 结构面（`runtime_checkable`
    协议 `_ManagerLike`/`_SubscriberFactoryLike`——不要求具体
    类，结构满足即可）。

## 测试（TDD：先 RED 后 GREEN）

`services/api/tests/test_voice_session_subscriptions.py`（新增，
32 项 fake-only 契约测试）：真实 `VoiceRuntimeManager` + 真实
`ControlledVoiceSubscriberFactory` + fake 最内层协作者三件套
（token-provider factory 返回合成 JWT 假值、fake room/stream
记录器——与既有订阅/工厂测试同构，零网络）。

1. **键规则**（3+1 组）：同 session 恒同键、不同 session 不同
   键、带空白即拒绝（精确标识符不 trim）、最长 64 位键 ≤128。
2. **session_id 边界**（10 例参数化）：非字符串/bytes/空/空白/
   长度 2 与 65/非法字符（`/`、空格、`:`）确定性拒绝。
3. **参数先行**（5 例参数化）：session/room/identity 任一非法
   ——manager.start 与工厂 create **双零计数**。
4. **申请编排**：恰一次 `manager.start`（key 与闭包）；闭包
   调用时才构造（调用前工厂零调用）且产物即 `create` 产物；
   canonical room/identity 原样到达；返回值纯透传。
5. **所有权（真实集成）**：acquire 后条目在 manager、订阅者
   active、fake room `connect` 恰一次且只收到假 token（token
   只进连接局部）；AST 断言服务类对外属性调用白名单恰
   {start, stop, snapshot, create}且 create 仅闭包内。
6. **会话级去重**：同 session 两次 acquire——工厂 create 恰
   一次、条目恰一、两次投影 active。
7. **释放**：条目移除 + 房间断开；重复释放与未知 session 幂等
   no-op；释放后同 session 重新 acquire 成功（键已移除）。
8. **失败透传净化**：connect 抛错（含诱饵异常文本）→ manager
   净化错误透传（无凭据/token/异常文本泄漏、零注册残留）；
   容量满（max=1 第二 session）与 close 后新 acquire 均透传
   manager 错误。
9. **构造校验**（3 例参数化 + 1 正例）：manager/factory 缺失
   确定性失败。
10. **snapshot 透传**：与 manager.snapshot() 同构、状态 open。
11. **源码卫生**（AST+文本）：导入白名单 {__future__, typing,
    collections.abc, re, app.domain.voice_subscriber_factory}；
    禁止调用 open/system/popen/run/sleep/getenv/ensure_future/
    create_task/start_new_thread/build_voice_token + 属性面
    connect；无 asyncio（编排层零事件循环操作）；文本级无
    socket/subprocess/LiveKit/FastAPI/httpx/aiohttp/env 文件/
    动态逃逸。

## 验证（2026-10-15，worktree 内真实执行；全部零网络/零运行时服务/零真实凭据）

使用 canonical venv 解释器（`.venv/Scripts/python.exe`）：

| 命令 | 结果 |
| --- | --- |
| `-m pytest services/api/tests/test_voice_session_subscriptions.py -q` | **32 passed** |
| `-m pytest`（十二文件语音回归：session_subscriptions + entrypoint + app_wiring + app_factory + subscriber_factory + runtime_factory + frame_sink + subscription + runtime + runtime_manager + runtime_lifespan + subscriber_lifespan）`-q` | **347 passed**（M14-267 基线 315 零回归） |
| `-m pytest services/api/tests/test_m905_ownership_audit.py services/api/tests/test_compose_profiles.py services/api/tests/test_rc_smoke_rehearsal.py -q` | **93 passed, 1 skipped** |
| `-m ruff check services/api/app/domain/voice_session_subscriptions.py services/api/tests/test_voice_session_subscriptions.py` | All checks passed |
| `-m compileall`（同两文件） | OK |
| `git diff --check` | 干净（无 whitespace 错误） |

TDD 顺序：先建测试文件确认 RED（模块不存在，collection error），
实现后转 GREEN；期间两轮测试自身修正（①fake 包装类用
`__call__` 而非 `create` 方法——结构协议 isinstance 检查失败，
改为 `create` 方法；②源码卫生导入白名单遗漏 `re`——实现用
正则复用工厂边界）。

新增行卫生：真实 secret 形态 0 命中（测试全为显眼假值；JWT
`eyJ` 命中均为合成假值）；无绝对私有路径；无 U+FFFD；无
readiness 置 true。

## 后续空间（不在本切片）

- HTTP 端点接线（voice session 申请/释放路由与鉴权边界）；
- lifespan 安装 `VoiceSessionSubscriptionService` 到 app.state；
- 帧消费者管道（M14-269 有界音频帧管道）；流式 ASR 消费链
  （M14-270）；VAD/barge-in/pause-resume（M14-271）；真实
  LiveKit 端到端验收（M14-272）。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-268（M14-267 转
  前一任务快照，含 PR #354 合并结果），里程碑段追加 M14-268
  注记（计数不变）。
- `docs/ROADMAP.md`：M14 段新增 M14-268 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-268 条目（含非声明）。
