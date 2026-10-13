# M14-262 证据：受控订阅者工厂 lifespan 注册（显式可选注入，fake-only）

- 分支 `api/m14-262-voice-subscriber-lifespan`（worktree
  `ai-learning-os-worktrees/m14-262-voice-subscriber-lifespan`），
  基于 current main `c1956f81ad9109de7121cc15e485fdf4cd19d53e`
  （PR #348 merge，即 M14-261）。执行前 porcelain 干净（基点
  本地 git 可验证）。
- 动因：M14-261 交付了纯域受控工厂，但应用层尚无显式所有权
  把它接进生命周期。本切片把 `ControlledVoiceSubscriberFactory`
  以**显式可选注入**接进 `create_app`——不构造默认生产工厂、
  不连真实 LiveKit。注册 ≠ 启动：仅当调用方经管理器显式 start
  才创建订阅者。

## 非声明（explicit non-claims）

- **零 LiveKit**：不导入 livekit 包、不调用
  `livekit_runtime_factories`（源码卫生测试锚定）；不在
  `main.py` 读任何 LiveKit 凭据、不构造 token provider。
- **无隐式生产工厂**：`create_app(None)` 继续只暴露
  `voice_runtime_manager`——默认不注册任何工厂；注入是唯一
  注册途径。
- **无 HTTP/readiness 变化**：不新增/改动任何端点、不加
  readiness 检查、settings 与环境解析零变化。
- **无真实网络/运行时服务**：零真实连接、零服务启停、零真实
  DB 凭据、零真实 secret 读写。
- **无流式 ASR/VAD/barge-in/pause-resume/转写/帧消费者管线**。
- **无数据库/迁移/容器/compose/部署/Web/Android/Harmony 改动**。
- **不声明 M4-09 或 capability gap 3 关闭**：矩阵计数不变
  implemented 60 / partial 3。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变。

## 交付面

**`services/api/app/main.py`（保守扩展，三处）**：

- **签名**：`create_app` 新增 keyword-only 可选参数
  `voice_subscriber_factory: ControlledVoiceSubscriberFactory |
  None = None`（既有 `create_app(None)` 位置调用完全兼容）。
- **前置校验**（函数首段、任何 setup 之前）：非 None 必须为
  `ControlledVoiceSubscriberFactory` 实例，否则抛
  `ValueError("voice_subscriber_factory 必须为
  ControlledVoiceSubscriberFactory 实例或 None")`——在返回
  app 之前确定性失败，lifespan 绝不半启动。
- **lifespan 接线**（管理器接线后、`yield` 前）：注入存在时把
  **同一实例**挂到 `app.state.voice_subscriber_factory`；未注入
  则**不安装该属性**。实例同一性：期间
  `app.state.voice_subscriber_factory is` 注入工厂；lifespan
  退出后属性保留同实例（诊断面）、管理器照常 closed。
- **管理器零变化**：`VoiceRuntimeManager` 构造容量、`close`
  即排空、关停顺序（close 先于 `worker.stop()` 先于
  `engine.dispose()`）、幂等语义全部保持——既有
  `test_voice_runtime_lifespan.py` 六项锚定测试零回归。

**`services/api/tests/test_voice_subscriber_lifespan.py`（新增，
12 项 fake-only 契约测试）**：全合成 fake room/stream/track/
frame（与 M14-261 工厂测试同构），零网络/零运行时服务。

1. **非法注入类型前置失败**（5 例 parametrize：object/str/
   int/lambda/类引用）：`create_app(None,
   voice_subscriber_factory=bad)` 同步抛 `ValueError`（无
   TestClient、无 lifespan）。
2. **省略注入不安装属性**：`create_app(None)`——lifespan 前、
   期间、退出后均无 `voice_subscriber_factory` 属性；仅管理器
   被接线；`/health` 200。
3. **None 注入同上**：显式 `voice_subscriber_factory=None`
   与省略语义一致。
4. **注入工厂身份同一**：lifespan 前：未安装；期间：`is`
   注入工厂、管理器 open、`/health` 200；退出后：属性仍
   `is` 注入工厂、管理器 closed。
5. **独立 app/工厂互不共享**：两个 app 各注入各自工厂——
   `app.state` 各持其工厂、管理器实例互不相同；退出后各自
   保持注入实例、各自 closed。
6. **集成一（工厂→管理器→帧→净化→显式停止→幂等关停）**：
   注册工厂可直接 `create` 句柄（注册 ≠ 启动——idle、零连接、
   token provider 零调用）；管理器以零参工厂闭包 `start`
   拥有并启动被管实例（投影 active/room/identity 正确、无
   `token` 键、序列化零 JWT 形态）；fake 音轨/帧到达被管实例
   的有界汇（`frame_count==1`、`frame_byte_count` 精确、句柄
   state+sink 序列化无 JWT/无帧字节文本）；显式
   `manager.stop` 移除条目（`subscriber_count==0`、房间断开
   恰 1 次）；TestClient 退出（close 排空空注册表）后手动再
   `close()` 返回 `{"stopped": 0, "failed": 0}` 且再次幂等、
   无双重停止。
7. **集成二（关停时活跃订阅者恰排空一次）**：订阅者保持
   active 不显式停——lifespan 退出经既有 `manager.close()`
   路径排空：`disconnect` 恰 1 次、`subscriber_count==0`、
   closed；再 `close()` 幂等（返回首次排空记录结果
   `{"stopped": 1, "failed": 0}`——M14-258 契约语义）、仍恰
   1 次断开。
8. **源码卫生（main.py）**：无 `ControlledVoiceSubscriberFactory(`
   构造调用（isinstance 引用与类型注解均无调用括号）；无
   `from livekit`/`import livekit`/`livekit_runtime_factories`；
   无 `os.environ`/`getenv(` 新增环境读取；恰一个守卫式
   `app.state.voice_subscriber_factory = ` 赋值；
   `inspect.signature` 锚定注入参数 keyword-only 且默认
   None；管理器关停顺序不变（`await voice_runtime_manager.
   close()` < `await worker.stop()` < `await engine.dispose()`、
   无 `stop_all(` 调用、`VoiceRuntimeManager(` 构造仍恰一处且
   缩进锚定不变）。

## 验证（2026-10-13，worktree 内真实执行；全部零网络/零运行时服务）

使用 canonical venv 解释器（`.venv/Scripts/python.exe`）：

| 命令 | 结果 |
| --- | --- |
| `-m pytest services/api/tests/test_voice_subscriber_lifespan.py -q` | **12 passed** |
| `-m pytest services/api/tests/test_voice_subscriber_lifespan.py services/api/tests/test_voice_subscriber_factory.py services/api/tests/test_voice_frame_sink.py services/api/tests/test_voice_subscription.py services/api/tests/test_voice_runtime.py services/api/tests/test_voice_runtime_manager.py services/api/tests/test_voice_runtime_lifespan.py -q` | **181 passed**（12+58+14+21+26+44+6；M14-261 基线 169 零回归） |
| `-m ruff check services/api/app/main.py services/api/tests/test_voice_subscriber_lifespan.py` | All checks passed |
| `git diff --check` | 干净（无 whitespace 错误） |

TDD 顺序：先建测试文件确认 RED（11 failed——注入参数不存在、
属性不存在、签名断言失败；1 个「省略注入不安装属性」用例在
实现前平凡通过，属防回归守卫），实现后转 GREEN；期间修正过两处
测试自身缺陷（①集成一先以探针句柄断言帧计数，但管理器闭包按
M14-261 契约创建的是另一独立实例——改为捕获闭包创建的被管实例
再断言；②集成二对二次 close 期望 `{"stopped": 0}` 与 M14-258
既有语义不符——close 返回首次排空的记录结果，按既有契约改为
`{"stopped": 1, "failed": 0}`）。

新增行卫生：secret 形态 0 命中；JWT `eyJ` 命中均为合成假值
（FAKE_JWT 测试常量与泄漏防线断言）；私有绝对路径 0；U+FFFD 0；
readiness 置 true 0。

## 后续空间（不在本切片）

- 生产工厂的真实受控装配（真实 LiveKit 工厂、token provider
  的受控构造与注入）；HTTP 端点接线；帧消费者管线；
  VAD/barge-in/pause-resume；真实房间服务的受控验收。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-262（M14-261 转
  前一任务快照），里程碑段追加 M14-262 注记（计数不变）。
- `docs/ROADMAP.md`：M14 段新增 M14-262 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-262 条目（含非声明）。
