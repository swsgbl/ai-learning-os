# M14-264 证据：room-aware 受控订阅者工厂（纯域层，fake-only）

- 分支 `api/m14-264-room-aware-voice-subscriber-factory`（worktree
  `ai-learning-os-worktrees/m14-264-room-aware-voice-subscriber-factory`），
  基于 current main `9248c42ea11064da77f12bf2c42b9e0652d219c6`
  （PR #350 merge，即 M14-263）。执行前 porcelain 干净（基点
  本地 git 可验证）。
- 动因：M14-263 能装配真实 runtime 工厂，但 M14-261 的 token
  协作者是零参 callable——生产服务端订阅者 JWT 必须由传给
  `create(room, identity)` 的**恰确** room/identity 派生，把
  零参工厂直接注入应用启动会有为错误房间签发 token 的风险。
  本切片补上 room-aware provider 路径——不读 settings、不接线
  应用。

## 非声明（explicit non-claims）

- **零接线**：不改 `app.main.create_app`/FastAPI lifespan/
  app.state（M14-262 注入面原样）、不加 HTTP/readiness、无
  部署/公开暴露变化。
- **零 settings/环境/凭据读取**：不读任何配置、环境变量、
  LiveKit 凭据、API key、secret。
- **不实现/不调用真实 `build_voice_token`**：测试全程使用
  fake token-provider factory（记录 (room, identity)、返回
  `tok-{room}-{identity}` 形态假值）。
- **零真实 LiveKit 连接/网络**：测试 monkeypatch
  `voice_runtime_factory.livekit_runtime_factories`；工厂层
  fake room/stream 注入。
- **无流式 ASR/VAD/barge-in/pause-resume/转写/帧消费者管线**。
- **无数据库/迁移/容器/compose/部署/Web/Android/Harmony 改动**。
- **不声明 M4-09 或 capability gap 3 关闭**：矩阵计数不变
  implemented 60 / partial 3。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变。

## 交付面

**`services/api/app/domain/voice_subscriber_factory.py`（保守扩展）**：

- 构造器 token 协作者**二选一**：
  - 既有零参 `token_provider: Callable[[], str]`——参数降为
    可选默认 None，源兼容（keyword 名不变）；语义完全兼容
    （零参路径行为有显式回归守卫测试）；
  - 新 room-aware `token_provider_factory: Callable[[str, str],
    str]`；
  - 两者都缺/都给确定性抛 `VoiceSubscriberFactoryError`
    （`token_provider 配置非法：...恰提供一个`）；**None 为
    「未提供」哨兵**——`token_provider=None` 落配置错误，
    非 None 非 callable 仍落协作者错误（`注入协作者非法...`）；
    构造仍零协作者调用。
- room-aware 路径（`create()`）：仍先校验 room/identity 再装配
  （无效参数在任何协作者调用之前失败——原契约保持）；以捕获
  **已校验** canonical_room/canonical_identity 的**零参闭包**
  适配 `ServerVoiceSubscriber`（订阅者面零改动）：
  - provider factory 仅在订阅者启动时被调用（创建句柄零调用）；
  - 收到恰确的该句柄 room/identity（杜绝为错误房间签发
    token）；
  - 返回 token 只进订阅者启动局部（闭包直转
    `room.connect(token)`），绝不存于工厂/句柄/投影。
- 既有净化/异常链抑制/每 create 独立 sink/句柄投影/管理器
  兼容/关停语义全部保持（M14-261 全部既有测试零改动通过）。

**`services/api/app/domain/voice_runtime_factory.py`（增补）**：

- `build_room_aware_controlled_voice_subscriber_factory(url,
  token_provider_factory)`——与零参版（M14-263，原样保留）
  同一顺序契约：URL 校验（M14-263 语义原样复用——同一
  `validate_livekit_url`）→ provider factory callable 校验
  （先于 runtime 工厂调用；绝不调用它）→
  `livekit_runtime_factories(validated_url)` **恰一次** →
  room/audio-stream 工厂与 provider factory 以引用透传构造
  room-aware 受控工厂；装配零调用（provider/room/stream/
  create 全零）；runtime 工厂失败转通用净化错误且异常链彻底
  抑制（runtime 工厂对调用抽取为共享私有助手
  `_runtime_factory_pair`——零参与 room-aware 两版本共享同
  一净化语义；**Codex R1 修正（已 amend 回本提交）**：解包与
  callable/None 形状验证在助手守卫路径内完成——返回值恰为
  二元可迭代且两元素均 callable 才通过，抛错/None/长度错误
  （3 元组、1 元组）/非可迭代/元素非 callable 任何畸形形状
  统一转通用净化错误且 `__cause__`/`__context__` 均 None，
  绝不以 ValueError/TypeError 从调用方解包处逃逸）。

## 测试（TDD：先 RED 后 GREEN）

`test_voice_subscriber_factory.py` **+9 项**（既有 58 项零改动
语义保持；harness 扩展：FakeRoom 记录连接收到的 token、
Collaborators 增加 room-aware provider 替身）：

1. 构造二选一：都缺/都给确定性拒绝且零协作者调用。
2. 非 callable room-aware provider（123/str/object）拒绝。
3. room-aware `create()` 返回 idle 句柄且 provider factory/
   room/stream 全零调用。
4. `await handle.start()`：provider factory 恰在启动时收到
   **恰确的** `("voice-exact", "server-sub-exact")`；fake 房间
   连接收到返回的 token（`received_tokens` 断言）；token
   不入工厂/句柄/汇投影序列化。
5. 双房间两句柄：provider 调用序列恰为各自 (room, identity)、
   各自房间收到各自 token（互不串房）。
6. provider factory 失败（消息带 JWT 形态）：走既有净化
   failed-start 路径——句柄抛通用「启动失败」错误、终态重启
   同样净化、`__cause__`/`__context__` 均 None、traceback/
   str 无 boom/JWT/token 形态、订阅者侧封存 failed（净化事件）。
7. 零参路径显式回归守卫：零参 provider 恰一次调用、room-aware
   面零调用、房间收到零参 token。

`test_voice_runtime_factory.py` **+17 项**（既有 37 项保持）：

1. room-aware 装配 URL 先行拒绝（6 例循环：空/http/无主机名/
   userinfo/query/None）且 runtime 工厂与 provider 零调用。
2. 非 callable provider factory（123/str/object）先于 runtime
   工厂拒绝。
3. 恰一次原样 URL + provider factory 引用透传（白盒身份）+
   零参路径未配置（`_token_provider is None`）+ 装配零调用。
4. 装配出的工厂创建 idle 句柄且 provider factory 零调用
   （创建 ≠ 启动）。
5. runtime 工厂失败净化（链抑制、恰一次尝试）。
6. 零参装配显式回归守卫（`_token_provider` 引用透传、
   `_token_provider_factory is None`）。
7. **Codex R1 修正**——两个 builder（零参与 room-aware）各
   4 例畸形 runtime 返回形状（3 元组/1 元组/非可迭代 42/
   元素非 callable，畸形值携带假标记 `boom-`/`extra-` 与
   JWT 形态）：全部转 `VoiceRuntimeFactoryError` 通用净化
   错误——str 无假标记/无 JWT、`__cause__`/`__context__` 均
   None、traceback 序列化零泄漏、runtime 恰一次尝试
   （Recorder 增设 `runtime_malformed` 形状错误注入面）。

既有非 callable 构造测试按 None 哨兵新语义调整：`None` →
配置错误（恰提供一个），123/str/object → 协作者错误——两类
均确定性失败且零协作者调用。

## 验证（2026-10-14，worktree 内真实执行；全部零网络/零运行时服务）

使用 canonical venv 解释器（`.venv/Scripts/python.exe`）：

| 命令 | 结果 |
| --- | --- |
| `-m pytest services/api/tests/test_voice_subscriber_factory.py services/api/tests/test_voice_runtime_factory.py -q` | **121 passed** |
| `-m pytest services/api/tests/test_voice_subscriber_factory.py services/api/tests/test_voice_runtime_factory.py services/api/tests/test_voice_frame_sink.py services/api/tests/test_voice_subscription.py services/api/tests/test_voice_runtime.py services/api/tests/test_voice_runtime_manager.py services/api/tests/test_voice_runtime_lifespan.py services/api/tests/test_voice_subscriber_lifespan.py -q` | **244 passed**（M14-263 基线 218 零回归） |
| `-m ruff check services/api/app/domain/voice_subscriber_factory.py services/api/app/domain/voice_runtime_factory.py services/api/tests/test_voice_subscriber_factory.py services/api/tests/test_voice_runtime_factory.py` | All checks passed |
| `-m compileall services/api/app/domain/voice_subscriber_factory.py services/api/app/domain/voice_runtime_factory.py services/api/tests/test_voice_subscriber_factory.py services/api/tests/test_voice_runtime_factory.py` | OK（无输出即成功） |
| `git diff --check` | 干净（无 whitespace 错误） |

TDD 顺序：先加新断言确认 RED（`build_room_aware_...` 导入
失败致收集失败），实现后转 GREEN；期间两轮测试自身修正
（①`token_provider=None` 在二选一语义下落配置错误而非协作者
错误——既有与新增非 callable 测试的 None 用例按哨兵语义拆分；
②一处无占位 f-string 触发 ruff F541——去掉 f 前缀）。

**Codex R1 修正（已 amend 回本提交）**：审查发现
`_runtime_factory_pair` 把协作者返回值原样交给调用方、解包
发生在 try 守卫之外——3 元组/非可迭代返回会以 ValueError/
TypeError 逃逸而非净化的 `VoiceRuntimeFactoryError`（回归
M14-263「任何失败统一净化」契约）。修法：解包与 callable/
None 形状验证全部移入助手守卫路径内，畸形形状统一转通用
净化错误且异常链彻底抑制。TDD：先加两个 builder × 4 例畸形
形状测试确认 RED（8 failed——ValueError/TypeError 逃逸），
修复后全绿（121/244）。

新增行卫生：secret 形态 0 命中；JWT `eyJ` 命中均为合成假值
（FAKE_JWT 测试常量与泄漏防线断言）；`tok-` 命中均为 fake
provider 返回的合成假 token；私有绝对路径 0；U+FFFD 0；
readiness 置 true 0。

## 后续空间（不在本切片）

- 把 room-aware 装配接进真实应用生命周期（create_app/启动模式
  装配、真实 `build_voice_token` 的受控接入——需凭据边界评估）；
  HTTP 端点接线；帧消费者管线；VAD/barge-in/pause-resume；
  真实房间服务的受控验收。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-264（M14-263 转
  前一任务快照），里程碑段追加 M14-264 注记（计数不变）。
- `docs/ROADMAP.md`：M14 段新增 M14-264 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-264 条目（含非声明）。
