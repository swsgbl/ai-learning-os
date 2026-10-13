# M14-261 证据：受控语音订阅者工厂（纯域装配层，fake-only）

- 分支 `api/m14-261-controlled-voice-subscriber-factory`（worktree
  `ai-learning-os-worktrees/m14-261-controlled-voice-subscriber-factory`），
  基于 current main `6a9fca8b63f201c42ee37102e401fb80efcda179`
  （PR #347 merge，即 M14-260）。执行前 porcelain 干净（基点本地
  git 可验证）。
- 动因：M14-260 补上事件/帧的有界计量归宿后，域内仍缺一个把
  注入协作者（token provider / room factory / audio-stream
  factory）装配成订阅者 + 独立汇的受控工厂。本切片交付
  M14-260 之后的下一个窄后端基础件：纯域、构造零副作用、参数
  先行校验的 `ControlledVoiceSubscriberFactory`。**这不是运行时
  接线**：不导入/不调用真实 LiveKit SDK、不调用
  `livekit_runtime_factories`、不注册生产订阅者工厂——真实
  运行时接线是后续受控切片的职责。

## 非声明（explicit non-claims）

- **零 LiveKit**：不导入 livekit 包、不调用
  `voice_runtime.livekit_runtime_factories(url)`（AST 级导入
  白名单锚定——生产模块运行时导入严格限定
  `re`/`collections.abc`/`typing` + 同域纯模块
  `app.domain.voice_frame_sink`/`app.domain.voice_subscription`）。
- **无 FastAPI lifespan/app.state/HTTP/readiness 变更**：不进
  lifespan、不动 app.state、不新增/改动任何 HTTP 路由、
  `/readyz` 与 readiness 语义零变化。
- **无真实房间连接/网络**：零真实网络、零运行时服务启停、零
  真实 DB 凭据、零真实 secret 读写。
- **无流式 ASR/VAD/barge-in/pause-resume/转写/帧消费者管线**。
- **无数据库/迁移/容器/compose/部署/Web/Android/Harmony 改动**。
- **不声明 M4-09 或 capability gap 3 关闭**：矩阵计数不变
  implemented 60 / partial 3。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变。

## 交付面

**`services/api/app/domain/voice_subscriber_factory.py`（新增，
纯域装配模块）**：

- `ControlledVoiceSubscriberFactory`——构造注入三协作者
  callable（token_provider / room_factory /
  audio_stream_factory）+ 正数 `max_events`（默认与订阅域
  `DEFAULT_MAX_EVENTS` 同约定 64）；非 callable 协作者、非正
  上限确定性抛 `VoiceSubscriberFactoryError`（通用白名单文案）。
  **构造零副作用**：只保存引用，不调用任何注入 callable、无
  网络/磁盘/进程/定时器/线程/事件循环操作（测试锚定构造后
  token/room/stream 三计数恒 0）。
- `create(room, identity)` **参数先行校验**：
  - `validate_room`——与既有公开房间边界
    `^[A-Za-z0-9_-]{3,64}$`（routes/voice.py `_ROOM_RE`）同
    口径：非字符串（含 bytes/bool/list）、空白（""/空格/
    制表符）、长度 2 与 65、非法字符集（空格/斜杠/反斜杠/
    非 ASCII/控制字符 \x00 \x1b \x7f）一律确定性拒绝；通过
    即原样返回（不 trim——房间名是精确标识符）。
  - `validate_identity`——**保守既有风格边界：与 room 同一
    正则**。选择理由：①绝不放宽 room 语义（任务红线）；②与
    `voice_tokens.new_identity()` 生成的 `student-xxxxxxxx`
    形态天然兼容；③与既有服务端 `server-sub-*` 命名兼容。
  - 无效参数在任何协作者调用之前确定性失败——订阅者根本未
    构造，token provider/room factory/audio-stream factory
    零调用（测试两组 parametrize 锚定）。
- **每次装配彼此独立**：每次 create 新建一个
  `ServerVoiceSubscriber` + 一个**独立** `BoundedVoiceFrameSink`
  （同一 `max_events` 注入两者）；token provider 以 callable
  引用透传订阅者——create 绝不调用它；**token 只经
  token-provider callable 与订阅者启动局部变量流动**（订阅域
  M14-255 契约），绝不进工厂状态/句柄状态/快照/异常/证据；
  无共享可变订阅者/汇状态（帧与事件只进本订阅者自己的汇，
  测试双订阅者隔离锚定）。
- `ControlledVoiceSubscriberHandle`——结构满足
  `ManagedSubscriberLike`（async `start()`/`stop()` + `state()`
  委托订阅者）；另暴露 `sink_snapshot()` 委托汇的
  `snapshot()`（有界净化、每次全新拷贝）。句柄绝不暴露原始
  sink 对象、订阅者内部、token 或音频字节（公开面只有净化
  投影；测试断言公开属性无 sink/subscriber/token）；
  `state()` 对订阅者投影再做 **JWT 形态递归净化**（纵深防御
  ——即使外部音轨 sid 携带 token 形态也进不了公开投影，测试
  脏注入锚定）。
- **异常链彻底抑制**：需要包装协作者失败的错误一律通用净化
  消息且**在 except 块之外** `raise ... from None`——
  `__cause__`/`__context__` 均为 None、序列化 traceback 不含
  原始异常文本（区别于仅在 except 块内 `from None` 的常规写法
  ——后者 `__context__` 仍引用原异常对象）；token provider
  失败由订阅者转 failed 终态（通用净化事件），句柄在 start
  返回后检测 failed 状态抛通用启动失败错误（fail-closed——
  调用方绝不误判成功）；装配失败（sink/订阅者构造抛错）同样
  转通用错误零残留。
- `VoiceRuntimeManager` 集成面：工厂自身无注册/容量/排空
  状态——`create` 直接作为管理器 `start(key, factory)` 的零参
  工厂闭包体（`lambda: factory.create(room, ident)`），fake-only
  集成测试锚定成功启动（active 投影、真实连接 fake room）与
  stop 条目移除（subscriber_count 0、房间断开）。

**`services/api/tests/test_voice_subscriber_factory.py`（新增，
58 项 fake-only 契约测试）**：全合成 fake（记录调用的协作者
三件套 + fake room/stream/track/frame，与既有订阅/汇测试同构），
零网络/零运行时服务。

1. **构造零副作用**：构造后三计数恒 0、无 room/stream 产物。
2. **非 callable 协作者拒绝**：None/123/str/object 分别对三个
   注入位 parametrize 拒绝（通用消息）。
3. **非正 max_events 拒绝**：0/-1/-64。
4. **room 校验成功**：长度 3/64 边界、字母数字_- 混合——并
   断言与 API 房间正则 `^[A-Za-z0-9_-]{3,64}$` fullmatch 同口径。
5. **room 校验失败全集**（16 例 parametrize）：None/123/bytes/
   bool/list/""/空白/长度 2/长度 65/空格+感叹号/正斜杠/反斜杠/
   非 ASCII/控制字符 \x00 \x7f。
6. **identity 校验成功**：server-sub-1、student-ab12cd34
   （new_identity 形态）、长度边界——同一保守正则。
7. **identity 校验失败全集**（12 例 parametrize）。
8. **无效 room（8 例）/无效 identity（8 例）在任何协作者调用
   之前失败**：两组 parametrize 各自锚定三计数恒 0。
9. **每次 create 独立实例**：句柄不同、订阅者不同、汇不同；
   初始投影 idle + room/identity 正确 + 零帧；create 后仍零
   协作者调用（start 才触发）。
10. **双订阅者帧隔离**：各自房间收各自音轨后，帧只进本订阅者
    流→本订阅者汇（frame_count 2 vs 1、字节累计独立、事件数
    独立）。
11. **ManagedSubscriberLike 结构兼容**：runtime_checkable
    isinstance 成立；start/stop/state/sink_snapshot callable；
    公开面无 sink/subscriber/token。
12. **VoiceRuntimeManager 集成**：零参工厂闭包经
    `manager.start("voice-int", closure)` 成功启动（projection
    status active、room/identity 正确、真实 connect 一次）；
    `manager.stop` 移除条目（subscriber_count 0、房间断开）。
13. **停止清理顺序订阅者拥有**：双音轨场景 handle.stop 后操作
    序恰为 stream_close → stream_close → room_disconnect（无
    交错）、状态 stopped。
14. **快照有界净化**：脏注入（音轨 sid 携带 JWT 形态）后
    state+sink 序列化——零 JWT 命中（handle.state 深度净化 +
    sink 字段净化双防线）、含 [REDACTED-JWT] 占位、无帧字节
    （含可解码文本）、无 b' 形态、无协作者异常文本、无绝对
    私有路径形态（D:\ / C:\ / /home/ / /Users/）；events 截断
    到注入上限（max_events=4）；sink 顶层恰六白名单字段。
15. **token provider 失败**：handle.start 抛通用「启动失败」
    错误；二次 start（终态重启路径）同样转通用错误且
    `__cause__`/`__context__` 均 None、traceback 序列化零
    JWT/零 boom；订阅者侧状态封存、事件无 token 文本。
16. **room factory 失败**：消息携带 JWT 的原始异常被句柄转
    通用错误（str 无 room boom/JWT）+ 异常链三面抑制。
17. **源码卫生（AST 级）**：运行时导入严格限定白名单
    {__future__, re, collections.abc, typing,
    app.domain.voice_frame_sink, app.domain.voice_subscription}
    （TYPE_CHECKING 块外）；禁止调用 open/system/popen/run/
    sleep/ensure_future/create_task/start_new_thread；无
    asyncio 属性调用（本模块无 asyncio 导入——async 方法只是
    委托，不做调度）；无 __import__/eval/exec 动态逃逸口。

## 验证（2026-10-13，worktree 内真实执行；全部零网络/零运行时服务）

使用 canonical venv 解释器（`.venv/Scripts/python.exe`）：

| 命令 | 结果 |
| --- | --- |
| `-m pytest services/api/tests/test_voice_subscriber_factory.py -q` | **58 passed** |
| `-m pytest services/api/tests/test_voice_subscriber_factory.py services/api/tests/test_voice_frame_sink.py services/api/tests/test_voice_subscription.py services/api/tests/test_voice_runtime.py services/api/tests/test_voice_runtime_manager.py services/api/tests/test_voice_runtime_lifespan.py -q` | **169 passed**（58+14+21+26+44+6；M14-260 基线 111 零回归） |
| `-m ruff check services/api/app/domain/voice_subscriber_factory.py services/api/tests/test_voice_subscriber_factory.py` | All checks passed |
| `git diff --check` | 干净（无 whitespace 错误） |

TDD 顺序：先建测试文件确认 RED（`No module named
'app.domain.voice_subscriber_factory'` 集合失败），后实现模块转
GREEN；期间修正过一处测试自身缺陷（「无效参数零协作者调用」
用例的参数组合把两个合法值凑成一对、误期望抛错——拆成 room/
identity 两组独立 parametrize 后全绿），并按 ruff 反馈移除一个
未用导入、排序 `__slots__`。

新增行卫生：secret 形态 0 命中；JWT `eyJ` 命中均为合成假值
（FAKE_JWT 测试常量与泄漏防线断言）；私有绝对路径 0；U+FFFD 0；
readiness 置 true 0。

## 后续空间（不在本切片）

- 生产订阅者工厂注册与运行时装配（受控运行时切片——真实
  LiveKit 工厂的受控注入、HTTP 端点接线）；帧消费者管线；
  VAD/barge-in/pause-resume；真实房间服务的受控验收。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-261（M14-260 转
  前一任务快照），里程碑段追加 M14-261 注记（计数不变）。
- `docs/ROADMAP.md`：M14 段新增 M14-261 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-261 条目（含非声明）。
