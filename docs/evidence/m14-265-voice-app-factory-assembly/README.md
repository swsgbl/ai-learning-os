# M14-265 证据：语音应用层工厂装配（纯域层，fake-only）

- 分支 `api/m14-265-voice-app-factory-assembly`（worktree
  `ai-learning-os-worktrees/m14-265-voice-app-factory-assembly`），
  基于 current main `34bc8e27190cc0a966e76333d15ba4d363cd7fc7`
  （PR #351 merge，即 M14-264，含其 Codex R1 修正）。执行前
  porcelain 干净（基点本地 git 可验证）。
- 动因：M14-263 装配真实 runtime 工厂、M14-264 补上
  room-aware token-provider factory，但尚缺从**显式受控凭据**
  到 room-aware 服务端订阅者 token provider、再到
  `ControlledVoiceSubscriberFactory` 的纯域装配链。本切片补齐
  该链——**这不是应用运行时接线**，不连 create_app。

## 非声明（explicit non-claims）

- **零接线**：不改 `services/api/app/main.py`/`create_app`
  /FastAPI lifespan/app.state、不改路由/readiness/compose。
- **零 settings/环境/凭据读取**：不调 get_settings、不读
  .env/环境变量；测试全用显眼假凭据字符串
  （`fake-api-key-000000` / `fake-api-secret-...` 形态），
  绝不引入真实 secret/token。
- **零真实 LiveKit 连接/网络**：runtime 工厂对全程
  monkeypatch 为最小 fake；签发路径为离线本地 JWT
  （`build_voice_token` 纯计算，零网络）。
- **无流式 ASR/VAD/barge-in/pause-resume/转写/帧消费者管线**。
- **无数据库/迁移/容器/compose/部署/Web/Android/Harmony 改动**。
- **不声明 M4-09 或 capability gap 3 关闭**：矩阵计数不变
  implemented 60 / partial 3。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变。

## 交付面

**`services/api/app/domain/voice_app_factory.py`（新增，纯域
装配模块）**：

- `VoiceAppFactoryConfig`——frozen+slots 显式不可变配置，恰
  承载 LiveKit URL/API key/API secret/TTL 四项（TTL 默认
  `voice_tokens.DEFAULT_TTL_SECONDS`）；构造后不可变
  （FrozenInstanceError 锚定）、无字典属性面（slots 锚定）；
  **凭据字段 `field(repr=False)` 抑制**（Codex R1 修正）——
  repr 只含类名/URL/TTL 非秘密字段，绝不外泄 key/secret
  （防意外日志泄漏；测试断言 repr 含 FAKE_URL 与 ttl 而
  FAKE_KEY/FAKE_SECRET/JWT 形态零命中）；
  本对象不在构造期校验（校验在装配函数先行完成）。
- `build_room_aware_token_provider_factory(api_key, api_secret,
  ttl_seconds=DEFAULT_TTL_SECONDS)`：
  - 先行校验：key/secret 非空普通字符串（非 str/bytes/空白/
    非字符串类型一律拒绝）；TTL 非布尔整数且 30–86400 闭
    区间；
  - 构造期零签发、零 LiveKit 调用、零日志、零持久化；
  - 返回的 provider factory 恰以调用方传入的 (room,
    identity) 调用 `build_voice_token(..., role=
    ROLE_SERVER_SUBSCRIBER)` 并只返回 `.token`——subscribe-only
    grants（join + subscribe；无发布/数据通道/管理面，
    `voice_tokens` 既有契约）；
  - **凭据只作 provider 闭包捕获局部变量**——绝不成为返回
    工厂的公开属性/持久值；
  - 运行期 `build_voice_token` 失败不在本层吞掉——沿既有
    M14-261/264 净化 failed-start 路径流转。
- `build_voice_app_subscriber_factory(config)`——纯组合：
  1. config 类型校验（非 `VoiceAppFactoryConfig` 拒绝）；
  2. 凭据/TTL 校验并构造 provider factory（不调用）；
  3. URL 校验**复用** M14-264 `validate_livekit_url`——错误
     类型（`VoiceRuntimeFactoryError`）与消息同源，绝不重复
     实现或放宽；
  4. 复用 `build_room_aware_controlled_voice_subscriber_factory`
     （其 URL/runtime 形状/异常链抑制行为原样保持）；
  5. 装配期零签发、零 runtime 协作者调用、绝不触发订阅者
     创建/启动。
- 配置失败通用净化消息 + 守卫后 `raise ... from None`
  （`__cause__`/`__context__` 均 None）——URL/key/secret/token/
  协作者异常文本绝不入错误或 traceback。

**`services/api/tests/test_voice_app_factory.py`（新增，39 项
fake-only 契约测试）**：签发器 fake（monkeypatch
`voice_app_factory.build_voice_token`，记录全参数、可注入
失败）+ runtime 工厂对 fake（monkeypatch
`voice_runtime_factory.livekit_runtime_factories`）+ 组合层
透传记录器。

1. provider 构造成功零签发、零 runtime 协作者调用。
2. config frozen+slots：赋值抛 FrozenInstanceError、无
   `__dict__`；**repr 凭据抑制**（Codex R1 修正）——repr 含
   类名/FAKE_URL/ttl_seconds 非秘密字段，FAKE_KEY/FAKE_SECRET/
   JWT 形态零命中。
3. 非法 key（7 例：None/123/bytes/""/空白/True/list）与
   secret（6 例）确定性失败；错误与 traceback 无 FAKE_KEY/
   FAKE_SECRET/FAKE_URL/JWT 形态、`__cause__`/`__context__`
   均 None（`_assert_clean` 统一断言）。
4. 非法 TTL（7 例：True/False/"3600"/29/86401/None/30.5；
   另组合路径 3 例）确定性失败且净化。
5. 畸形 config（None/dict/str/int 4 例）确定性失败且净化。
6. fake 签发器断言恰确参数：room/identity/api_key/
   api_secret/ttl_seconds/`role=ROLE_SERVER_SUBSCRIBER`；
   返回值恰为签发对象的 `.token` 字段。
7. 不同 (room, identity) 对：两次独立调用、调用序列恰确、
   token 互异。
8. **真实离线签发路径**（不 monkeypatch——本地 JWT 零网络）：
   `verify_voice_token` 断言 identity/room 恰确、room_join
   True、can_subscribe True、can_publish/can_publish_data
   False、room_admin/room_create/room_record/hidden/recorder/
   agent/ingress_admin 全 False（只断言、不持久化/不输出
   token）。
9. 签发失败（消息带 JWT 形态）：装配 + `create()` 全零触发；
   仅订阅者 `start()` 时恰一次尝试并进入既有净化
   failed-start——`VoiceSubscriberFactoryError("启动失败")`、
   终态重启同样净化、`__cause__`/`__context__` 均 None、
   str/traceback 无 boom/JWT、订阅者状态封存。
10. 组合函数：非法凭据先于 runtime 工厂（记录器零调用）；
    成功恰一次原样 URL、返回 `ControlledVoiceSubscriberFactory`；
    room-aware 行为保持（idle 创建 → start 才签发恰确
    (room, identity)）；装配零协作者（签发器/房间/流全零）。
11. 非法 URL 复用 M14-264 净化：`VoiceRuntimeFactoryError`
    同源消息（match "LiveKit URL 非法"）、URL 值不入错误、
    runtime 工厂零调用（M14-264 装配函数被替换为防误调用
    哨兵仍零触发）。
12. 秘密不进返回工厂公开面：公开属性 repr / 实例槽位字符串
    值 / 工厂 repr / provider 闭包代码常量（co_consts）四面
    无 FAKE_KEY/FAKE_SECRET（秘密只在闭包单元，符合设计）。
13. 源码卫生（AST+文本）：运行时导入白名单
    {__future__, collections.abc, dataclasses,
    app.domain.voice_runtime_factory,
    app.domain.voice_subscriber_factory, app.domain.voice_tokens}；
    禁止调用清单（open/system/popen/run/sleep/ensure_future/
    create_task/start_new_thread/getenv + 属性面 connect）；
    无 asyncio 调度；无 app.main/get_settings/os.environ/
    getenv/pydantic_settings/动态逃逸文本；无 `.create(`
    /`.start(` 调用形态。

## 验证（2026-10-14，worktree 内真实执行；全部零网络/零运行时服务/零真实凭据）

使用 canonical venv 解释器（`.venv/Scripts/python.exe`）：

| 命令 | 结果 |
| --- | --- |
| `-m pytest services/api/tests/test_voice_app_factory.py services/api/tests/test_voice_runtime_factory.py services/api/tests/test_voice_subscriber_factory.py -q` | **160 passed** |
| `-m pytest services/api/tests/test_voice_app_factory.py services/api/tests/test_voice_subscriber_factory.py services/api/tests/test_voice_runtime_factory.py services/api/tests/test_voice_frame_sink.py services/api/tests/test_voice_subscription.py services/api/tests/test_voice_runtime.py services/api/tests/test_voice_runtime_manager.py services/api/tests/test_voice_runtime_lifespan.py services/api/tests/test_voice_subscriber_lifespan.py -q` | **283 passed**（M14-264 基线 244 零回归） |
| `-m ruff check services/api/app/domain/voice_app_factory.py services/api/tests/test_voice_app_factory.py` | All checks passed |
| `-m compileall services/api/app/domain/voice_app_factory.py services/api/tests/test_voice_app_factory.py` | OK（无输出即成功） |
| `git diff --check` | 干净（无 whitespace 错误） |

TDD 顺序：先建测试文件确认 RED（`No module named
'app.domain.voice_app_factory'` 集合失败），后实现模块转
GREEN；期间修正过一处测试自身缺陷（签发失败用例误期
`VoiceAppFactoryError`——既有 failed-start 路径抛的是 M14-261
句柄的 `VoiceSubscriberFactoryError`（组合层按设计不吞不包），
按既有契约修正断言类型）。

**Codex R1 修正（已 amend 回本提交）**：安全审查发现
`VoiceAppFactoryConfig` 的 dataclass 默认 repr 会包含 api_key/
api_secret——意外日志（print/异常消息/调试输出打 repr）即泄漏
凭据。修法：两个字段改 `field(repr=False)`——repr 只含类名/
URL/TTL 非秘密字段；架构行为/API/URL/运行时组合零变化。
TDD：先加 `test_config_repr_suppresses_credentials`（断言 repr
含 FAKE_URL/ttl_seconds、无 FAKE_KEY/FAKE_SECRET/JWT 形态）
确认 RED，修正后全绿（39/160/283）。

新增行卫生：真实 secret 形态 0 命中（全部为显眼假凭据
`fake-api-key-000000`/`fake-api-secret-...`）；JWT `eyJ` 命中
均为合成假值；私有绝对路径 0；U+FFFD 0；readiness 置 true 0。

## 后续空间（不在本切片）

- 把装配链接进真实应用生命周期（create_app 启动模式装配、
  从受控配置源读取真实凭据——需凭据边界评估）；HTTP 端点
  接线；帧消费者管线；VAD/barge-in/pause-resume；真实房间
  服务的受控验收。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-265（M14-264 转
  前一任务快照），里程碑段追加 M14-265 注记（计数不变）。
- `docs/ROADMAP.md`：M14 段新增 M14-265 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-265 条目（含非声明）。
