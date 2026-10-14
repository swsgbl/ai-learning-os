# M14-266 证据：显式语音应用接线（适配+包装，fake-only）

- 分支 `api/m14-266-voice-app-explicit-wiring`（worktree
  `ai-learning-os-worktrees/m14-266-voice-app-explicit-wiring`），
  基于 current main `e0950f989c5139284c70bddfe34aeea9ba2eed8c`
  （PR #352 merge，即 M14-265 含其 Codex R1 repr 修正）。执行前
  porcelain 干净（基点本地 git 可验证）。
- 动因：M14-265 能从显式凭据构造
  `ControlledVoiceSubscriberFactory`，本切片补上最后的窄显式
  接线路径：已加载设置对象 → M14-265 配置 → 工厂 → 既有
  `create_app` 显式 keyword 注入。语音接线只能经由
  `create_app_with_voice_subscriber` **显式**发生。

## 非声明（explicit non-claims）

- **零隐式接线**：`app/main.py` 零改动——模块级
  `app = create_app()` 原样（仍无订阅者工厂）且不导入/不调用
  新包装器（文本锚定）；`create_app` 签名/lifespan 实现零改动；
  `create_app(None)` 与 `voice_subscriber_factory=None` 语义
  不变（M14-262 注入契约，既有测试零回归）。
- **零配置/环境/凭据读取**：设置对象必须显式传入；新模块绝不
  调用全局设置读取入口、绝不读 env 文件/环境变量/secret 文件/
  全局态（AST+文本级源码卫生锚定）；不新增环境设置项。
- **不声明已配置生产语音**：接线路径存在 ≠ 生产已配置——真实
  凭据接入与部署入口改动是后续切片；capability gap 3 / M14-09
  未关闭，矩阵计数不变 implemented 60 / partial 3。
- **零真实凭据/LiveKit 连接/网络**：测试全用显眼假凭据
  （`fake-api-key-000000` 等）；runtime 工厂对与签发器全程
  monkeypatch 记录；零 LiveKit SDK 调用、零服务启停。
- **无流式 ASR/VAD/barge-in/pause-resume/转写/帧消费者管线**。
- **无部署入口/compose/Docker/DB/迁移/Web/Android/Harmony
  改动**。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变。

## 交付面

**`services/api/app/voice_app.py`（新增，窄接线模块）**：

- `VoiceAppSettingsLike`（runtime_checkable 最小设置协议）：
  恰三必读属性——服务端 `livekit_url`/`livekit_api_key`/
  `livekit_api_secret`；**故意不读 `public_livekit_url`**
  （面向浏览器公开地址，非服务端订阅者可达装配面——测试以
  显式不同的 public URL 断言被忽略）。
- `voice_app_factory_config(settings)`——属性访问全部在守卫内
  完成：缺失/属性读取抛错统一转 `VoiceAppWiringError` 通用
  净化消息（守卫后 `raise ... from None`：`__cause__`/
  `__context__`/traceback 均无设置值/属性名/URL/凭据/协作者
  异常文本）；TTL 用 `voice_tokens.DEFAULT_TTL_SECONDS`（无
  既有类型化设置，本切片不新增）；值级校验（凭据/URL）不在
  本层重复——由 M14-265 装配先行完成（失败在任何签发/
  runtime/create_app 之前）。
- `create_app_with_voice_subscriber(database_url, *,
  settings)`——顺序契约：适配 → 复用 M14-265
  `build_voice_app_subscriber_factory`（凭据/URL/runtime 形状/
  token/净化逻辑全部复用，零重复零放宽）→ **恰一次**以
  keyword 参数把同一工厂实例显式传入既有 `create_app`；工厂
  失败（含设置值非法）先于任何 FastAPI app 返回（绝不半装配）；
  位置参数 `database_url` 原样透传；返回值直通。

**`services/api/tests/test_voice_app_wiring.py`（新增，14 项
fake-only 契约测试）**：签发器（monkeypatch
`voice_app_factory.build_voice_token`）与 runtime 工厂对
（monkeypatch `voice_runtime_factory.livekit_runtime_factories`）
均为记录型 fake。

1. **适配器成功**：服务端三字段恰映射进
   `VoiceAppFactoryConfig`；`public_livekit_url` 显式不同且被
   忽略；TTL == `DEFAULT_TTL_SECONDS`；零签发、零 runtime
   协作者调用。
2. **VoiceAppSettingsLike 结构兼容**（runtime_checkable
   isinstance）。
3. **缺属性**（3 例 parametrize：逐一删除三字段）/ **属性读取
   抛错**（property 抛消息带 JWT 形态的 RuntimeError）/
   **非对象设置**（None/str/int/dict 4 例）——统一净化失败：
   错误与 traceback 无设置值/URL/凭据/JWT/boom、`__cause__`/
   `__context__` 均 None（`_assert_clean` 统一断言）。
4. **非法设置值先于 runtime 工厂与 create_app**（3 例：空白
   key/空 secret → M14-265 `VoiceAppFactoryError`；`http://`
   URL → M14-264 `VoiceRuntimeFactoryError` 同源消息）——
   runtime 工厂零调用、create_app（spy 哨兵）零调用。
5. **包装器恰一次传递**：恰构造一个 config → 一个工厂；恰一次
   `create_app`；位置参数原样透传；keyword 恰
   `voice_subscriber_factory` 一个且为该实例（isinstance
   `ControlledVoiceSubscriberFactory`）；返回值直通；装配全程
   零签发零房间/流构造（runtime 工厂对恰一次为 M14-265 装配
   内部调用）。
6. **fake-only lifespan 集成**：真实 `create_app` + TestClient
   ——`app.state.voice_subscriber_factory is factory`（同一
   实例）；`voice_runtime_manager` 存在且 open；订阅者计数 0
   （注册 ≠ 启动）；`/health` 200；签发器与房间/流构造全零；
   关停后管理器 closed、计数仍 0、仍零签发；退出后属性保留
   同实例（M14-262 诊断面契约）。
7. **源码卫生（AST+文本）**：运行时导入白名单
   {__future__, typing, fastapi, app.main,
   app.domain.voice_app_factory, app.domain.voice_tokens}；禁止
   调用清单（open/system/popen/run/sleep/get_settings/getenv/
   ensure_future/create_task/start_new_thread/build_voice_token
   + 属性面 connect）；无 asyncio 调度；文本级无全局设置
   读取/env 文件/环境变量/pydantic_settings/dotenv/token 生成/
   动态逃逸/`.create(`/`.start(` 调用形态（LiveKit SDK 导入由
   AST 白名单排除——适配器必然引用 livekit_* 属性名，不能按
   子串禁词）。
8. **main.py 默认不变锚定**：模块级裸 `app = create_app()`
   存在（正则锚定）；`voice_app`/`create_app_with_voice_
   subscriber` 零引用；M14-262 keyword-only 注入面文本保持。

## 验证（2026-10-14，worktree 内真实执行；全部零网络/零运行时服务/零真实凭据）

使用 canonical venv 解释器（`.venv/Scripts/python.exe`）：

| 命令 | 结果 |
| --- | --- |
| `-m pytest services/api/tests/test_voice_app_wiring.py services/api/tests/test_voice_app_factory.py services/api/tests/test_voice_subscriber_lifespan.py -q` | **65 passed**（14+39+12） |
| `-m pytest services/api/tests/test_voice_app_wiring.py services/api/tests/test_voice_app_factory.py services/api/tests/test_voice_subscriber_factory.py services/api/tests/test_voice_runtime_factory.py services/api/tests/test_voice_frame_sink.py services/api/tests/test_voice_subscription.py services/api/tests/test_voice_runtime.py services/api/tests/test_voice_runtime_manager.py services/api/tests/test_voice_runtime_lifespan.py services/api/tests/test_voice_subscriber_lifespan.py -q` | **297 passed**（M14-265 基线 283 零回归） |
| `-m ruff check services/api/app/voice_app.py services/api/tests/test_voice_app_wiring.py` | All checks passed |
| `-m compileall services/api/app/voice_app.py services/api/tests/test_voice_app_wiring.py` | OK（无输出即成功） |
| `git diff --check` | 干净（无 whitespace 错误） |

TDD 顺序：先建测试文件确认 RED（`No module named
'app.voice_app'` 集合失败），后实现模块转 GREEN；期间两轮自身
修正（①生产 docstring 含全局设置读取入口的英文名触发文本级
卫生自检——改中文措辞；②包装器测试把记录元组误当 create_app
返回值断言——改为记录返回哨兵；另按 ruff I001 合并重复导入行）。

新增行卫生：真实 secret 形态 0 命中（全部为显眼假凭据）；JWT
`eyJ` 命中均为合成假值；私有绝对路径 0；U+FFFD 0；readiness
置 true 0。

## 后续空间（不在本切片）

- 部署入口/启动脚本显式采用包装器（真实凭据从受控配置源注入
  ——需凭据边界评估）；HTTP 端点接线；帧消费者管线；
  VAD/barge-in/pause-resume；真实房间服务的受控验收。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-266（M14-265 转
  前一任务快照），里程碑段追加 M14-266 注记（计数不变）。
- `docs/ROADMAP.md`：M14 段新增 M14-266 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-266 条目（含非声明）。
