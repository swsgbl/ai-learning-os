# M14-263 证据：真实 LiveKit 工厂受控装配（纯域函数，fake-only）

- 分支 `api/m14-263-voice-livekit-factory-assembly`（worktree
  `ai-learning-os-worktrees/m14-263-voice-livekit-factory-assembly`），
  基于 current main `d7ee18064c4a39186719d42c78a9c9a78e445fe8`
  （PR #349 merge，即 M14-262）。执行前 porcelain 干净（基点
  本地 git 可验证）。
- 动因：M14-262 打开了工厂的应用层注入面，但尚无一个受控的、
  经过校验的装配函数把真实 runtime 工厂
  （`voice_runtime.livekit_runtime_factories`）装进 M14-261
  受控工厂。本切片交付 M14-262 之后的下一个窄后端基础件：纯域
  装配函数 `build_controlled_voice_subscriber_factory`——装配
  即返回，绝不启动任何订阅者/连接。

## 非声明（explicit non-claims）

- **零接线**：不改 `app.main.create_app`/FastAPI lifespan/
  app.state（M14-262 注入面保持原样）、不加 HTTP 路由/
  readiness、无部署/公开暴露变化。
- **零 settings/环境/凭据读取**：不读任何配置、环境变量、
  凭据或 token 材料（源码卫生测试文本级锚定 get_settings/
  os.environ/getenv/pydantic_settings/SECRET/API_KEY 缺席）。
- **零真实 LiveKit 连接/网络**：测试全程 monkeypatch
  `voice_runtime_factory.livekit_runtime_factories`；生产函数
  本身只调用该工厂函数（其返回闭包对——调用本身零 SDK 构造、
  零网络），绝不连接任何服务。
- **零启动**：装配即返回 `ControlledVoiceSubscriberFactory`，
  绝不触发订阅者创建/启动路径（源码卫生测试锚定无 create 调用）。
- **无流式 ASR/VAD/barge-in/pause-resume/转写/帧消费者管线**。
- **无数据库/迁移/容器/compose/部署/Web/Android/Harmony 改动**。
- **不声明 M4-09 或 capability gap 3 关闭**：矩阵计数不变
  implemented 60 / partial 3。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变。

## 交付面

**`services/api/app/domain/voice_runtime_factory.py`（新增，
纯域装配模块）**：

- `validate_livekit_url(url)`——确定性 URL 校验，通过即原样
  返回（不规范化、不改写）：
  - 非空普通字符串（非字符串/bytes/空白拒绝）；
  - 仅 `ws`/`wss` scheme（http/https/ftp/无 scheme 拒绝）；
  - 主机名存在且非空（`ws://`、`ws:///path` 拒绝）；
  - userinfo 缺席（`ws://user:pass@h`、`wss://u@h` 拒绝）；
  - query 与 fragment 缺席（`ws://h?q=1`、`wss://h#f` 拒绝）；
  - 端口可解析为 1–65535 整数（`ws://h:0` 保留端口、`:99999`
    越界、`:abc` 非数字、`:-1` 负数均拒绝——urlsplit 抛
    ValueError 被 `from None` 转确定性域错误）；
  - 失败抛 `VoiceRuntimeFactoryError` 通用净化消息——**URL
    原文绝不进错误文本**（URL 可携带凭据形态）。
- `build_controlled_voice_subscriber_factory(url,
  token_provider)`——顺序契约：
  1. URL 校验（在任何工厂或 token-provider 调用之前）；
  2. token-provider callable 校验（绝不调用、绝不存储或检视
     其返回值——token 只经 provider callable 与订阅者启动局部
     流动，M14-255/261 契约保持）；
  3. `livekit_runtime_factories(validated_url)` **恰一次**；
  4. 返回的 room 工厂与 audio-stream 工厂连同 token-provider
     callable 以引用透传 `ControlledVoiceSubscriberFactory`
     （其构造零副作用——M14-261 契约）；
  5. 装配即返回，绝不触发订阅者创建/启动；
  6. runtime 工厂失败（含返回形状错误——解包失败同路径）统一
     转通用净化错误 `语音运行时工厂装配失败`，且在 **except
     块之外** `raise ... from None`——`__cause__`/
     `__context__` 均为 None、序列化 traceback 不含协作者
     异常文本（token/凭据形态绝不泄漏）。
- **零副作用**：除恰一次调用注入/被替换的
  `livekit_runtime_factories` 外，无网络/磁盘/进程/定时器/
  线程/事件循环/settings/环境/凭据/LiveKit SDK 副作用
  （AST 级源码卫生测试锚定）。

**`services/api/tests/test_voice_runtime_factory.py`（新增，
37 项 fake-only 契约测试）**：monkeypatch 为记录型 fake
（记录 URL、调用计数、可注入异常），零真实连接。

1. **合法 URL 装配**（4 例 parametrize：`ws://localhost`、
   `ws://127.0.0.1:7880`、`wss://livekit.example.com`、
   `wss://media.internal:9090`）：runtime 工厂恰一次收到
   原样 URL；结果为 `ControlledVoiceSubscriberFactory`。
2. **validate 原样返回**：通过校验的 URL 不被改写。
3. **callable 透传 + 装配零调用**：token/room/stream 三计数
   恒 0；白盒身份同一（token-provider 与 fake 返回的两个
   工厂**同一对象**进入受控工厂——bound method 每次属性访问
   新建，故以捕获引用比较）。
4. **装配出的工厂可创建句柄**：`factory.create(...)` 返回
   `ControlledVoiceSubscriberHandle`（协议兼容证明——受控工厂
   接受鸭子型 fake 工厂）、状态 idle（未启动）、汇零帧、
   token/room/stream 仍零调用（创建 ≠ 启动）。
5. **非法 URL 24 例 parametrize**：非字符串（None/123/bytes/
   True）、空白（""/空格/制表）、无 scheme、错误 scheme
   （http/https/ftp/wsx）、缺主机名（`ws://`、`wss://`、
   `ws:///path-only`）、userinfo（user:pass@/user@）、query、
   fragment、非法端口（0/99999/abc/-1）——全部确定性抛
   `VoiceRuntimeFactoryError("LiveKit URL 非法...")` 且
   runtime 工厂与 token provider 零调用。
6. **validate 直拒抽查**：7 例直接调用亦确定性拒绝。
7. **非 callable token provider**（4 例：None/123/str/object）
   在 runtime 工厂调用之前失败。
8. **runtime 工厂异常净化**：fake 抛消息带 JWT 形态的
   RuntimeError → `VoiceRuntimeFactoryError`：str 无原始文本
   （boom/JWT）、`__cause__`/`__context__` 均 None、traceback
   序列化干净、恰一次尝试、错误消息可安全 JSON 序列化。
9. **源码卫生（AST + 文本）**：运行时导入严格白名单
   {__future__, urllib.parse, collections.abc, typing,
   app.domain.voice_runtime, app.domain.voice_subscriber_factory}；
   禁止调用清单（open/system/popen/run/sleep/ensure_future/
   create_task/start_new_thread + 属性面 getenv/connect）；
   无 asyncio 调度；无 app.main/get_settings/os.environ/
   getenv/pydantic_settings/SECRET/API_KEY 文本；无
   __import__/eval/exec/create(/start( 调用形态。

## 验证（2026-10-13，worktree 内真实执行；全部零网络/零运行时服务）

使用 canonical venv 解释器（`.venv/Scripts/python.exe`）：

| 命令 | 结果 |
| --- | --- |
| `-m pytest services/api/tests/test_voice_runtime_factory.py -q` | **37 passed** |
| `-m pytest services/api/tests/test_voice_runtime_factory.py services/api/tests/test_voice_subscriber_factory.py services/api/tests/test_voice_frame_sink.py services/api/tests/test_voice_subscription.py services/api/tests/test_voice_runtime.py services/api/tests/test_voice_runtime_manager.py services/api/tests/test_voice_runtime_lifespan.py services/api/tests/test_voice_subscriber_lifespan.py -q` | **218 passed**（37+58+14+21+26+44+6+12；M14-262 基线 181 零回归） |
| `-m ruff check services/api/app/domain/voice_runtime_factory.py services/api/tests/test_voice_runtime_factory.py` | All checks passed |
| `-m compileall services/api/app/domain/voice_runtime_factory.py services/api/tests/test_voice_runtime_factory.py` | OK（无输出即成功） |
| `git diff --check` | 干净（无 whitespace 错误） |

TDD 顺序：先建测试文件确认 RED（`No module named
'app.domain.voice_runtime_factory'` 集合失败），后实现模块转
GREEN；期间修正过一处测试自身缺陷（bound method 身份断言——
`recorder.token_provider` 每次属性访问新建对象导致 `is` 必然
失败，改为捕获同一引用/fake 返回值后比较；并预置
returned_factories 初值防未调用访问）。

新增行卫生：secret 形态 0 命中；JWT `eyJ` 命中均为合成假值
（FAKE_JWT 测试常量与泄漏防线断言）；私有绝对路径 0；U+FFFD 0；
readiness 置 true 0。

## 后续空间（不在本切片）

- 把装配函数接进真实应用生命周期（create_app/启动模式装配、
  token provider 受控构造——需凭据边界评估）；HTTP 端点接线；
  帧消费者管线；VAD/barge-in/pause-resume；真实房间服务的
  受控验收。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-263（M14-262 转
  前一任务快照），里程碑段追加 M14-263 注记（计数不变）。
- `docs/ROADMAP.md`：M14 段新增 M14-263 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-263 条目（含非声明）。
