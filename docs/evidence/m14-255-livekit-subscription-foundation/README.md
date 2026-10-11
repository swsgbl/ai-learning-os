# M14-255 证据：服务端 LiveKit 订阅基础（域层 foundation，fake-only 行为覆盖）

- 分支 `api/m14-255-livekit-subscription`（worktree
  `ai-learning-os-worktrees/m14-255-livekit-subscription`），基于
  current main `0f89567b`（PR #341 merge，即 M14-254）。执行前
  porcelain 干净（基点本地 git 可验证）。
- 动因：M14-252/M14-254 打通了 Web 侧 LiveKit 管理输入，但服务端
  对学习者已发布音轨零订阅能力。本切片交付**第一个窄后端基础件**：
  subscribe-only 内部 token 角色 + 单房间订阅控制器（纯域层）。
  全部行为用注入 fake 验证（fake Room / fake 音轨 / fake
  AudioStream / 记录型 sink），零网络、零浏览器、零运行时服务。

## 非声明（explicit non-claims）

- **未交付运行时 LiveKit 连接**：控制器不进 FastAPI
  startup/lifespan，零 HTTP 端点新增/改动，未连接任何真实 LiveKit
  服务——真实 rtc Room/AudioStream 的 async 适配与接线是后续切片。
- **不是流式 ASR**：帧只转发给注入 sink（本切片无消费者），不落
  库、不识别、不产生 transcript。
- **未交付 VAD / barge-in / pause/resume**。
- **不声明 capability gap 3 / M4-09 关闭**：矩阵计数不变
  implemented 60 / partial 3。
- `production_ready` / `release_ready` / `public_ready` 均 false
  不变；本切片未启停任何生产/运行时服务。

## 交付面

1. **`services/api/app/domain/voice_tokens.py`（角色扩展）**：
   新增 `ROLE_SERVER_SUBSCRIBER = "server-subscriber"` 与
   `_server_subscriber_grants`——**恰好 join + subscribe**：
   `can_publish=False`、`can_publish_data=False` 显式固定，
   `can_subscribe=True`、`room_join=True`；管理/录制/agent/hidden
   （room_admin/room_create/room_record/recorder/agent/hidden/
   ingress_admin）全部保持默认 False。签发域 `VOICE_ROLES` 扩为
   两角色；**新增 `PUBLIC_VOICE_ROLES = (ROLE_STUDENT,)`**——公开
   `POST /api/v1/voice/token` 端点改用它校验，外部请求
   server-subscriber 依旧 422「未知角色」（公开行为逐字节零变化，
   契约测试实证 student 200 / server-subscriber 422）。
   student grants 与既有端点行为零改动（邻居 test_voice_tokens
   全绿）。
2. **`services/api/app/domain/voice_subscription.py`（新增，域层
   控制器，零 FastAPI 接线）**：`ServerVoiceSubscriber`——构造注入
   room 名、identity、token provider、Room 工厂、AudioStream 工厂、
   事件汇（`EventSinkLike`：on_event + on_frame 两面）；`start()`
   幂等（active 期 no-op；终态重启抛 VoiceSubscriptionError——单
   实例单连接）；`stop()` 幂等且按**确定顺序**清理（先全部音频流
   按 sid 排序关闭、后房间断开）；`state()` 有界净化投影（环形
   事件 64 条 + 全量计数；无 token/无帧字节/无凭据字段）。
   安全契约：token 只存在内存局部变量并直接传 room.connect——
   绝不进入 state/事件/日志/错误（失败消息为白名单通用文案，不
   拼接 cause 文本——注入的异常消息故意携带 JWT 形态与帧字节，
   断言无法穿透）；音频帧原样转发 sink，控制器不缓存不保留。
   失败契约：token/connect/audio_stream 失败先清全部已获资源
   （流→房间）再进入 failed 终态，暴露安全类别
   （token/connect/audio_stream/cleanup）+ 通用净化消息；cleanup
   失败与**轨退订 close 失败同样 fail-closed**（清剩余流 + 断房间 +
   failed 终态 + cleanup 类别通用净化消息——supervisor 修正 2）不
   阻塞 stopped 终态（stop 路径的 cleanup 失败仍尽力而为）。仅消费
   `kind == "audio"` 轨，
   非 audio 轨安全忽略（track_ignored 事件只记 kind 标签）。
3. **`services/api/tests/test_voice_subscription.py`（新增 20 项
   契约测试，全 fake 零网络；含 supervisor 三项修正随测）**：见下。
4. **`services/api/app/api/routes/voice.py`（一行集合切换）**：
   端点角色校验 `VOICE_ROLES` → `PUBLIC_VOICE_ROLES`（公开行为
   零变化——server-subscriber 原本就不在旧集合中，422 语义逐字
   保持）。

## 测试覆盖（20 项，零网络/零运行时/全 fake 注入）

- **token 角色边界（3 项）**：server-subscriber 恰好 join+subscribe
  （can_publish/can_publish_data 显式空 + 管理七项全空，经真实
  build→verify 往返断言 claims.video）；student grants 不受侵蚀；
  公开端点拒绝 server-subscriber（422 未知角色）且 student 照常
  200（行为零变化）。
- **happy path（5 项）**：start 连接（token 只作 connect 参数，
  state/事件序列化零 JWT）；audio 轨订阅 + 帧直转 sink（state 序列化
  无帧字节）；非 audio 轨忽略（零 AudioStream、track_ignored 只记
  kind）；退订关闭流；stop 确定顺序（流按 sid 排序先关、房间最后
  断开，ops 序列断言）+ 幂等（零重复事件/清理）；远端断开自动
  清理且后续 stop no-op。
- **失败路径（5 项）**：token 失败（零连接、category=token、通用
  消息）；connect 失败（房间尽力断开恰一次、零流、零泄漏——异常
  消息携带 token 原文亦无法穿透）；audio_stream 失败（已建流全清
  + 房间断开 + 零泄漏——异常消息携带 JWT 形态与帧字节亦无法
  穿透）；cleanup 失败（stream close 与 room disconnect 双双抛错
  仍达 stopped、恰一次断开尝试、两条 cleanup 失败事件）；终态
  （stopped/failed）重启一律 VoiceSubscriptionError（无双重连接）。
- **fail-closed 与幂等有界（5 项）**：轨退订 close 失败 fail-closed
  （剩余流关闭 + 房间断开恰一次 + failed 终态 + 仅安全 cleanup 类别/
  通用净化消息 + 零泄漏——supervisor 修正 2，RED 抽查钉住）；
  active 期重复 start（工厂/token/connect
  各恰一次）；stop-before-start 封存生命周期；构造校验（空 room/
  identity/非正 max_events）；事件环形有界（80+2 条事件 → 投影
  恰 64 条 + 全量计数 82）。
- **有效性抽查（RED）**：临时移除 `_fail` 的资源清理两行，
  connect/audio_stream 失败两项测试立即失败、恢复后 19 全过——
  测试钉住真实行为而非恒真。

## 修正记录（supervisor review，amend 回原提交）

- **修正 1（恒真断言）**：test_event_log_is_bounded 的
  `... == "connected" or "track_ignored"`（第二操作数为非空字符串，
  恒真）替换为严格滚动断言——保留窗口 64 条恰为第 17..80 个
  track_ignored（首/尾均为 track_ignored、窗口内类型集合唯一、
  每条 detail 为 kind=video）。
- **修正 2（轨退订 close 失败 fail-closed）**：原实现单轨 close
  失败仅发 cleanup 事件并 return——房间保持连接、状态保持
  active，违反「失败零活资源」契约。改为调用既有
  `_fail(CATEGORY_CLEANUP, "订阅资源清理失败")`：剩余流全关、房间
  断开、failed 终态、仅安全类别 + 通用净化消息。新增聚焦测试
  （close 异常携带 JWT 形态亦无法穿透；RED 抽查：还原旧宽松路径
  即测试失败）。
- **修正 3（死房间引用）**：_on_room_disconnected 在远端断开后
  清空 `_room_conn`——控制器不再保留死房间对象（对外行为与
  stop 幂等不变，现有测试覆盖足额，未新增面向内部的测试）。

## 验证（2026-10-11，worktree 内真实执行；全部零网络/零运行时服务）

| 命令 | 结果 |
| --- | --- |
| `... -m pytest services/api/tests/test_voice_subscription.py -q` | **20 passed**（含两项 RED→GREEN 抽查复跑） |
| `... -m pytest services/api/tests/test_voice_tokens.py services/api/tests/test_voice_subscription.py services/api/tests/test_voice_providers.py -q` | **65 passed**（直接受影响邻居：token 契约与 voice 路由窄回归，零回归） |
| `... -m ruff check services/api/app/domain/voice_tokens.py services/api/app/domain/voice_subscription.py services/api/app/api/routes/voice.py services/api/tests/test_voice_subscription.py` | All checks passed（ruff --fix 清理 5 处 import 规范后） |
| `git diff --check` | 干净（无 whitespace 错误） |

新增/改动行卫生扫描：secret 形态 0 命中（测试内 API_KEY/API_SECRET
为邻居同款公开测试常量，签发为纯本地 JWT 不触网）；JWT `eyJ` 命中
均为合成假值（FAKE_JWT 断言样本，签名段占位）；私有绝对路径 0；
U+FFFD 0；readiness 置 true 0。

## 后续空间（不在本切片）

- 真实 livekit-rtc Room/AudioStream 的 async 适配器 + FastAPI
  lifespan 接线（受控运行时切片）；
- 帧消费者（流式 ASR 管线入口——届时才有 transcript）；
- VAD / barge-in / pause/resume；
- 真实 LiveKit 服务的受控验收（supervisor 运维）。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-255（M14-254 转前一
  任务快照），里程碑段追加 M14-255 注记（计数不变）。
- `docs/ROADMAP.md`：M14 段新增 M14-255 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-255 条目（含非声明）。
