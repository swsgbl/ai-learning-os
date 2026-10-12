# M14-256 证据：LiveKit Python runtime 适配层（async-native，fake-only 验证）

- 分支 `api/m14-256-livekit-runtime-adapter`（worktree
  `ai-learning-os-worktrees/m14-256-livekit-runtime-adapter`），基于
  current main `b3e66222`（PR #342 merge，即 M14-255）。执行前
  porcelain 干净（基点本地 git 可验证）。
- 动因：M14-255 的订阅控制器是同步协议，与 livekit realtime SDK
  的真实形态（`rtc.Room.connect/disconnect` 与
  `rtc.AudioStream.aclose` 均 async、AudioStream 为 async 迭代器）
  之间缺一座显式声明依赖的 async-native 适配层。本切片把控制器
  生命周期操作演进为 awaitable，并交付真实 SDK 对象到控制器协议
  的适配 facades——**全部行为用 fake 验证（零网络/零真实 LiveKit
  服务），不接 FastAPI startup/lifespan，不做 ASR/VAD/barge-in/
  pause-resume**。

## 非声明（explicit non-claims）

- **fake-only 适配层/运行时验证**：未连接任何真实 LiveKit 服务、
  未开任何网络 socket；真实栈运行留给 supervisor 后续受控切片。
- **无 FastAPI startup/lifespan 接线**；零 HTTP 端点新增/改动，
  公开 voice token 端点行为零变化。
- **无流式 ASR / VAD / barge-in / pause/resume**：帧只转发注入
  sink（本切片仍无消费者），不识别、不产生 transcript。
- **不声明 capability gap 3 / M4-09 关闭**：矩阵计数不变
  implemented 60 / partial 3。
- `production_ready` / `release_ready` / `public_ready` 均 false
  不变；本切片未启停任何生产/运行时服务。

## 交付面

1. **`services/api/requirements.txt`（依赖声明）**：新增
   `livekit>=1.1,<1.2`（realtime 发行版——PyPI 包名就是 `livekit`，
   与既有 REST 签发包 `livekit-api>=1.0,<2` 并列；本地已装
   1.1.16、最新 1.1.20，pin 落在同 minor 内）。零安装动作（仅
   声明；测试用本地已装包 import）。
2. **`services/api/app/domain/voice_subscription.py`（async-native
   演进，M14-255 语义零回归）**：`RoomLike.connect/disconnect` 与
   `AudioStreamLike.close` 协议改为 Awaitable；`start/stop` 与
   全部清理/失败路径改为 `async def`；事件回调经 `_dispatch`
   包装（`ensure_future` + 任务集合追踪）调度为循环任务——
   SDK 同步事件分发由适配层桥接；`start` 幂等升级为「并发重复
   调用等待同一首次任务」；`stop` 清理顺序扩为**残留事件任务 →
   全部音频流 → 房间断开**（`_cancel_pending_tasks` 排除当前
   任务——失败路径可能正运行在事件任务里，取消自己会死锁）；
   token 只在内存局部变量、帧不缓存、fail-closed、有界净化
   state、终态不可重启全部保持（迁移后 20 项测试全绿）。
   全程零 `asyncio.run`、零嵌套循环、零线程桥（文本契约锁定）。
3. **`services/api/app/domain/voice_runtime.py`（新增，适配层）**：
   - `normalize_track_kind`：SDK 枚举（supervisor 实证
     KIND_UNKNOWN=0/KIND_AUDIO=1/KIND_VIDEO=2）/裸 int/str 输入
     统一映射 `"audio"`/`"video"`/`"unknown"`——未知值安全落
     unknown（bool 显式拒绝），绝不猜绝不抛。
   - `LiveKitRoomAdapter`：**LiveKit URL 保存在适配器**（构造
     注入，配置面），控制器 `await connect(token)` 把 (url,
     token) 恰按序传 `rtc.Room.connect`；`on()` 注册的事件
     handler 经参数桥接——track-like 参数替换为
     `_NormalizedTrackView`（**kind 归一化 + 其余属性
     `__getattr__` 代理直通原始 track**：sid/frames/SDK 媒体
     句柄对下游工厂仍可达——初版只暴露 sid/kind 会吞掉真实
     track 能力面，集成调试发现后修正）；`disconnect` 可等待。
   - `LiveKitAudioStreamAdapter`：消费 `rtc.AudioStream`（async
     迭代器）——`set_frame_handler` 启动消费任务，`async for`
     每帧**原样**转发 handler（帧对象直转，不复制不缓存字节）；
     `await close()` 确定顺序：先 cancel 消费任务并等待其真正
     退出、再 `await aclose()`（幂等）。
   - `livekit_runtime_factories(url)`：构造真实 SDK 的工厂对供
     `ServerVoiceSubscriber` 注入（URL 只进 room 工厂闭包；
     测试 monkeypatch `voice_runtime.rtc` 命名空间后同一装配面
     零网络覆盖）。
4. **`services/api/tests/test_voice_runtime.py`（新增 26 项，含 R1
   流消费失败集成）**：见下。
5. **`services/api/tests/test_voice_subscription.py`（async 迁移）**：
   20 项 M14-255 行为断言全部迁移到 async 生命周期
   （`asyncio.run` 驱动 + drain 排空 dispatch 链——仓库既有
   惯例），新增并发重复 start 断言；语义零回归。

## 测试覆盖（26 项 runtime + 21 项迁移订阅，零网络/零真实服务；含 R1 两项修正随测）

- **依赖与导入（2 项）**：requirements 逐行含 `livekit>=1.1,<1.2`
  且 livekit-api 并列保留；本地包 import 零网络成功且
  rtc.Room/AudioStream/TrackKind 存在、枚举值 1/2/0 与 supervisor
  实证一致。
- **RoomAdapter（2 项）**：`await connect` 后 (url, token) 恰按
  序传入（url 在前——RED 抽查：反转参数序立即失败）；空白 URL
  构造拒绝。
- **kind 归一化（2 项）**：15 组参数化输入（枚举/int/str/None/
  bool/越界）全部安全映射；`on()` 事件桥接把 SDK track 归一化
  为 sid/kind 视图、非 track 参数直通。
- **AudioStreamAdapter（2 项）**：三帧逐一直转（帧对象同一性——
  不复制）；close 先 cancel 任务等待退出再 aclose 恰一次、幂等、
  挂起期零帧转发。
- **控制器 × 真实工厂装配（3 项，monkeypatch rtc 命名空间）**：
  happy path（token 只作 connect 参数、帧到 sink、事件序列
  connecting→connected→track_subscribed→stopped、stop 幂等、
  清理顺序 stream aclose 先于 room disconnect）；connect 失败
  （failed 终态 + 房间尽力断开 + 类别/通用消息 + JWT 零泄漏）；
  AudioStream 构造失败（failed + 零活资源 + 零泄漏）。
- **async-native 纪律（1 项，文本契约）**：域层与适配层源码零
  `asyncio.run(`/`run_until_complete(`/`threading.`/`Thread(`
  （docstring 字样不算——纪律针对可执行代码路径）。
- **M14-255 行为保持（20 项迁移）**：grants 边界/公开端点 422/
  async happy path/帧不保留/非 audio 忽略/退订关流/stop 确定
  顺序+幂等/远端断开/token·connect·stream·cleanup 失败零活
  资源零泄漏/轨退订 close 失败 fail-closed/终态不可重启（含
  failed）/顺序+并发幂等 start/构造校验/事件有界严格滚动断言。

## 修正记录（supervisor review R1，amend 回原提交）

- **修正 1（事件处理异常 fail-closed）**：`_run_handler` 捕获任意
  事件处理异常后原先只 emit cleanup failed、状态停留 active（资源
  状态未知却继续会话）——改为 `await self._fail(CATEGORY_CLEANUP,
  "订阅资源清理失败")`：清任务/流/房间并进 failed 终态。新增聚焦
  测试（第一路正常建流后第二路 handler 抛异常——异常消息携带
  JWT 形态；断言已建流关闭、房间断开、failed 终态、零泄漏；
  RED 抽查：还原只发事件实现即测试失败）。
- **修正 2（流消费异常观察面）**：`LiveKitAudioStreamAdapter`
  `_consume` 的 async 迭代异常原先无人观察——消费任务死亡后
  控制器仍 active、流仍挂 `_streams`。契约新增
  `set_error_handler`（`AudioStreamLike` 协议 + 适配器实现 +
  控制器在 `set_frame_handler` 后注册）：`_consume` 捕获非
  CancelledError 异常触发回调（**不带异常文本**——杜绝 cause
  泄漏），控制器 `_on_stream_error` 调度 `_fail(CATEGORY_
  CLEANUP, 通用消息)`。新增集成测试（fake stream 产 1 帧后抛
  异常：已到帧保留、剩余流关闭、房间断开、failed 终态、token/
  帧字节零泄漏；RED 抽查：移除注册行即停留在 active 而测试
  失败）。

## 验证（2026-10-11，worktree 内真实执行；全部零网络/零运行时服务）

| 命令 | 结果 |
| --- | --- |
| `... -m pytest services/api/tests/test_voice_subscription.py -q` | **21 passed**（async 迁移 + R1 事件异常 fail-closed） |
| `... -m pytest services/api/tests/test_voice_runtime.py -q` | **26 passed**（含三项 RED→GREEN 抽查复跑） |
| `... -m pytest services/api/tests/test_voice_subscription.py services/api/tests/test_voice_runtime.py services/api/tests/test_voice_tokens.py services/api/tests/test_voice_providers.py -q` | **92 passed**（直接受影响邻居零回归） |
| `... -m ruff check services/api/app/domain/voice_subscription.py services/api/app/domain/voice_runtime.py services/api/app/domain/voice_tokens.py services/api/app/api/routes/voice.py services/api/tests/test_voice_subscription.py services/api/tests/test_voice_runtime.py` | All checks passed（`--fix` 清理 16 处 import/注解规范后） |
| `git diff --check` | 干净（无 whitespace 错误） |

有效性抽查（RED）：临时反转 `LiveKitRoomAdapter.connect` 参数序
→ 顺序断言与集成 happy path 两项测试立即失败、恢复后 25 全绿。

新增/改动行卫生扫描：secret 形态命中均为邻居同款公开测试常量
（`devkey`/`test-secret-...`，签发纯本地不触网）；JWT `eyJ` 命中
均为合成假值（FAKE_JWT 断言样本）；私有绝对路径 0；U+FFFD 0；
readiness 置 true 0。

## 后续空间（不在本切片）

- FastAPI lifespan/startup 接线（受控运行时切片，supervisor 决定）；
- 帧消费者（流式 ASR 管线入口）；
- VAD / barge-in / pause/resume；
- 真实 LiveKit 服务的受控验收。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-256（M14-255 转前一
  任务快照），里程碑段追加 M14-256 注记（计数不变）。
- `docs/ROADMAP.md`：M14 段新增 M14-256 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-256 条目（含非声明）。
