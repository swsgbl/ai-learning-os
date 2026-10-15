# M14-269 证据：有界音频帧管道（纯域层，fake-only）

- 分支 `api/m14-269-voice-frame-channel`（worktree
  `ai-learning-os-worktrees/m14-269-voice-frame-channel`），基于
  current main `1f8b80b1`（PR #355 merge，即 M14-268）。执行前
  porcelain 干净（基点本地 git 可验证）。
- 动因：订阅者帧路径（M14-255/256 的 `event_sink.on_frame`
  直转）只到计量（M14-260 纯计数不保留字节）。本切片补上
  **有界可消费管道**：帧字节经分流汇同时进入计量与有界缓冲，
  供 M14-270 流式 ASR `drain` 拉取。

## 非声明（explicit non-claims）

- **帧管道 ≠ 语音能力**：不做转写/VAD/barge-in/pause-resume
  （M14-270/271 的职责）；不加 HTTP 路由/readiness、不进
  lifespan、不连真实 LiveKit、不注册生产订阅者。
- **零落盘（不可落盘）**：模块无任何文件 I/O 代码面（AST 断言
  禁 open/write/Path/pickle/tempfile）——内存明文音频只在
  缓冲与 drain 交付物中短暂驻留；本切片比「隐私配置允许才
  落盘」更保守：根本不存在落盘路径。
- **内存上界可证明**：任意时刻缓冲帧数 ≤ max_frames 且缓冲
  字节 ≤ max_bytes（构造校验均为正；单帧超过总预算拒绝入界
  并审计——绝不放宽上界）；压力测试 50 帧两策略参数化断言
  上界恒成立。
- **审计有界净化**：丢弃事件只含 type/reason 常量/frame_bytes
  整数（环形保留最新 N、全量计数含被淘汰者）——绝不含帧
  内容/凭据/token。
- 零真实凭据（测试全为合成 PCM 假帧与显眼假 JWT）；未启停
  任何生产/运行时服务；不触碰 compose/env/Web/Android/
  Harmony/DB/迁移/recovery/edge/TURN。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变；计数不变 implemented 60 / partial 3。

## 交付面

**`services/api/app/domain/voice_frame_channel.py`（新增，纯域层）**：

- `BoundedVoiceFrameChannel`：
  - 双上限缓冲（默认 `DEFAULT_MAX_FRAMES=256` /
    `DEFAULT_MAX_BYTES=512 KiB`，构造可注入，任一到顶即触发
    丢弃策略）；
  - 丢弃策略 `DROP_OLDEST`（默认——满时淘汰最旧、新帧入界，
    缓冲恒为最新 N）/ `DROP_NEWEST`（满时拒绝新帧，缓冲恒为
    最先 N）；单帧超总预算直接拒绝（reason=
    `frame_over_budget`）；
  - 丢弃审计：`dropped_frame_count`/`dropped_byte_count` 单调
    计数 + 环形审计事件（`frame_dropped` + 原因常量 +
    字节数）；
  - `drain(limit)`：批量 FIFO 取走帧字节（正整数 limit，API
    误用确定性拒绝），帧数/字节预算同步归还，所有权移交，
    二次 drain 不重复交付；
  - `on_frame` 无效帧口径与 M14-260 计量汇一致（data 仅
    bytes/bytearray/memoryview 有效；缺失/属性抛错/其他类型
    计 invalid_frame_count，绝不抛出）；
  - `snapshot()`/`audit_snapshot()`：全新拷贝、无帧字节。
- `SplitVoiceSink`：结构满足 `EventSinkLike` 的分流接线件——
  `on_event` 只路由计量汇（`BoundedVoiceFrameSink`）；`on_frame`
  **fan-out**（计量计数 + 通道入界同时发生）；构造校验两协作
  者结构面。

## 测试（TDD：先 RED 后 GREEN）

`services/api/tests/test_voice_frame_channel.py`（新增，31 项
fake-only 契约测试）：合成 PCM 假帧 + 显眼假 JWT；真实
`ServerVoiceSubscriber` + fake 房间/流集成（scenario 包进单一
async 上下文——订阅者事件回调经 ensure_future 需要 running
loop）。

1. 构造校验（6 例参数化）与默认值。
2. FIFO 入界与字节计量（bytes/bytearray/memoryview 混合）。
3. 单帧超总预算拒绝（不入界、审计 over_budget）。
4. 帧数限/字节限任一触发（各自独立用例）。
5. 压力下上界恒成立（50 帧 × drop_oldest/drop_newest 参数化）。
6. drop_oldest 保留最新 / drop_newest 保留最先。
7. 丢弃审计单调 + 环形有界（max_audit_events=3 下 10 次丢弃
   只留最新 3 条、全量计数 9）+ 事件字段白名单。
8. drain：FIFO 全量/部分取、预算归还后可重新入界、空缓冲
   空列表、limit 参数校验（7 例：0/-1/1.5/"3"/None/True）。
9. 无效帧四类（str/list/None/属性抛错）只计数不抛。
10. SplitVoiceSink：事件只路由计量、帧 fan-out 双路径、构造
    校验两例。
11. fake-only 集成：emit 5 帧经流 handler → 通道收到逐字一致
    字节序列、计量 frame_count=5 同步、drain 全量取回后缓冲空。
12. 快照序列化卫生：无帧字节形态/无 JWT/无 secret 字样。
13. 源码卫生（AST+文本）：导入白名单 {__future__, typing,
    collections} + TYPE_CHECKING 域内 voice 协议引用；禁调用
    open/system/popen/run/sleep/getenv/dump/write/flush/
    ensure_future/create_task/start_new_thread；文本级无
    socket/subprocess/threading/asyncio/pathlib/pickle/
    tempfile/shutil/LiveKit/FastAPI。

## 验证（2026-10-15，worktree 内真实执行；全部零网络/零运行时服务/零真实凭据/零落盘）

使用 canonical venv 解释器（`.venv/Scripts/python.exe`）：

| 命令 | 结果 |
| --- | --- |
| `-m pytest services/api/tests/test_voice_frame_channel.py -q` | **31 passed** |
| `-m pytest`（十三文件语音回归：frame_channel + session_subscriptions + entrypoint + app_wiring + app_factory + subscriber_factory + runtime_factory + frame_sink + subscription + runtime + runtime_manager + runtime_lifespan + subscriber_lifespan）`-q` | **378 passed**（M14-268 基线 347 零回归） |
| `-m pytest services/api/tests/test_m905_ownership_audit.py services/api/tests/test_compose_profiles.py services/api/tests/test_rc_smoke_rehearsal.py -q` | **93 passed, 1 skipped** |
| `-m ruff check`（新两文件） | All checks passed |
| `-m compileall`（同两文件） | OK |
| `git diff --check` | 干净 |

TDD 顺序：先建测试确认 RED（模块不存在，collection error），
实现后转 GREEN；两轮测试自身修正（①集成测试最初在事件循环外
同步触发 track_subscribed handler——订阅者事件回调经
ensure_future 需要 running loop，改 scenario 包进单一 async
上下文；②源码卫生白名单遗漏 TYPE_CHECKING 域内协议引用）。

## 后续空间（不在本切片）

- M14-270 流式 ASR 消费链（drain 拉取 → 本地 FunASR 或可替换
  adapter；transcript 权威来源/会话绑定/失败恢复/超时边界）；
- VAD/barge-in/pause-resume（M14-271）；真实 LiveKit 端到端
  验收（M14-272）；HTTP 路由/lifespan 接线。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-269（M14-268 转
  前一任务快照，含 PR #355 合并结果），里程碑段追加注记。
- `docs/ROADMAP.md`：M14 段新增 M14-269 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-269 条目（含非声明）。
