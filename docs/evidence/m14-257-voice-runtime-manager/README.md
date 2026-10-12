# M14-257 证据：FastAPI 独立的语音运行时管理器（fake-only 验证）

- 分支 `api/m14-257-voice-runtime-manager`（worktree
  `ai-learning-os-worktrees/m14-257-voice-runtime-manager`），基于
  current main `31d67a65`（PR #343 merge，即 M14-256）。执行前
  porcelain 干净（基点本地 git 可验证）。
- 动因：M14-256 交付了订阅控制器与 runtime 适配层，但缺少一个显式
  的所有者为未来 API lifespan 接线管理一个或多个订阅者的生命周期
  作用域。本切片交付 **FastAPI 独立**的管理器/注册表——**fake-only
  验证，不连接 LiveKit、不接 FastAPI startup/lifespan、不新增/改动
  任何 HTTP 路由、不做流式 ASR/VAD/barge-in/pause-resume**。

## 非声明（explicit non-claims）

- **fake-only 管理器验证**：零真实网络、零真实 LiveKit 连接、零
  运行时服务启停；真实栈接线（lifespan/容器）是后续切片。
- **无 FastAPI startup/lifespan/HTTP 路由/启动钩子/settings 加载/
  容器配置/调度器变更**。
- **无流式 ASR / VAD / barge-in / pause/resume**。
- **不声明 M4-09 或 capability gap 3 关闭**：矩阵计数不变
  implemented 60 / partial 3。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变。

## 交付面

1. **`services/api/app/domain/voice_runtime_manager.py`（新增域模块）**：
   - `ManagedSubscriberLike` 协议（async-native 最小生命周期面：
     `async start/stop` + 已净化有界 `state()`）——不要求继承
     `ServerVoiceSubscriber`（结构满足即可），管理器不暴露
     房间/token/流内部。
   - `validate_key`：不透明键严格校验——非空 strip 后普通字符串、
     无控制字符（<0x20/0x7F）、长度 ≤ 128；返回 strip 规范键
     （" a " 与 "a" 同键）；**绝不把键解释为路径/URL**。
   - `VoiceRuntimeManager`（显式构造、零全局态、单事件循环——
     无 `asyncio.run`/嵌套循环/线程/进程池/模块级单例，文本契约
     锁定）：构造注入正数容量 `max_subscribers`（默认 8）与每订阅者
     投影事件上限 `max_events_per_subscriber`（默认 16）；`start(key,
     factory)` 每键至多一实例——**并发/重复调用经 `_starting` 任务
     复用首次结果（factory 恰调一次）**，strip 规范键归一；**容量在
     factory 构造之前检查**（满载即拒、零构造零注册）；factory/
     start 失败零注册残留（start 失败先尽力 stop 再抛通用净化错误，
     订阅者异常文本绝不透传）；在册终态（stopped/failed）键拒绝重启
     （抛错，绝不静默替换）；`stop(key)` 成功即移除条目（释放容量）、
     幂等（未知键 no-op）、订阅者 stop 抛错时条目**仍被移除**
     （fail-closed：清理状态未知绝不继续显示 active）并抛通用错误；
     `stop_all()` 按键排序确定性遍历、单项失败不阻断其余（返回
     `{stopped, failed}` 计数）；`snapshot()`/`start()` 投影——
     **订阅者 state 白名单键**（status/room/identity/audio_track_
     count/audio_track_ids/event_count + events 截断到上限），任意
     其他键（异常文本/凭据/帧字段）在投影处即被丢弃，全部字符串值
     再过 JWT 形态正则（纵深防御——脏 fake 实证：订阅者 state 故意
     塞入 error/token/frames/exception 键与 JWT 形态文本，投影全部
     挡下）。
2. **`services/api/tests/test_voice_runtime_manager.py`（新增 36 项
   fake-only 契约测试；含 R1 四项修正随测）**：见下。
3. **零改动**：voice_subscription/voice_runtime/voice_tokens/
   voice.py 路由全部零触碰（M14-255/256 语义零回归）。

## 测试覆盖（36 项，零网络/零运行时/全 fake；含 R1 四项修正随测）

- **键校验（3 项）**：9 组非法形态参数化拒绝（None/int/空白/
  控制字符 NUL/ESC/DEL/超长）；strip 规范化同键；合理键接受
  （含中文、128 上限边界）。
- **容量（2 项）**：满载在 factory 构造之前拒绝（第二工厂调用
  计数 0），停止释放容量后可再注册（RED 抽查 A：移除前置检查即
  测试失败）；构造参数非正拒绝。
- **注册与投影（3 项）**：start 返回净化投影、snapshot 计数/上限/
  键列表/单订阅者投影且无内部对象引用；events 截断（52 条 → 4 条，
  event_count 全量保留）；键排序确定性。
- **并发/重复（2 项）**：`asyncio.gather` 三路并发（含 strip 变体）
  恰一实例、factory 恰一次、等价投影、计数 1（RED 抽查 B：移除
  `_starting` 复用即测试失败）；成功后重复 start 零新构造。
- **独立键（1 项）**：不同键独立 start/stop 互不影响。
- **失败零残留（3 项)**：factory 抛错零条目（异常消息携带 JWT 形态
  亦不透传）；订阅者 start 失败尽力清理（stop 被调）+ 零注册 +
  通用消息；错误消息净化断言。
- **stop 语义（3 项）**：幂等（二次 no-op、未知键 no-op）+ 成功即
  移除；stop_all 单项抛错继续其余（{stopped:2, failed:1}，全部条目
  移除——fail-closed）；stop 失败错误净化 + 条目仍移除。
- **终态重启（1 项）**：在册 failed 键再 start 拒绝（第二工厂
  零调用——绝不静默替换）。
- **投影净化（2 项）**：脏 state（error/token/frames/exception/
  internal_room_handle 五个非法键 + JWT 形态文本 + 帧字节 + 100 条
  超长 events）全部被白名单/正则/截断挡下；脏 event detail 中 JWT
  形态被替换为 [REDACTED-JWT]。
- **纪律（1 项）**：新域模块零 `asyncio.run(`/`run_until_complete(`/
  `threading.`/`Thread(`/`ProcessPool`/`new_event_loop(`；类定义前
  无模块级实例化（无隐藏单例）。
- **集成式（1 项）**：真实 `ServerVoiceSubscriber`（M14-255/256
  域类）+ 结构等价的迷你 fake room/stream/sink 注入（与
  test_voice_subscription Harness 同构——订阅者自身行为已由其 21
  项测试锁定，此处只验管理面与真实 state 键域契合，不重复整套
  harness 断言）：start 注册/同键去重/stop 移除断房/移除后同键新
  实例（非重启旧终态）/stop_all 收尾。
- 其余 8 项为上述边界的参数化与组合。

## 修正记录（supervisor review R1，amend 回原提交）

- **修正 1（容量预留）**：进行中的启动现计入容量预留
  （`len(entries) + len(starting) >= max`）——旧实现并发不同键在
  max=1 下会注册 2 条；现恰一个 factory/start 幸存，另一个在
  factory 构造之前被拒（两工厂总调用恰 1）。RED 抽查：移除预留
  即并发容量测试失败。
- **修正 2（pending 期间停止）**：`stop(key)` 先等待该键进行中的
  启动落定——成功则停掉产物订阅者并移除条目（旧缺陷：立即返回
  count 0，启动随后完成留下 count 1 且 stop_calls=0 的活资源）；
  启动自清失败后 stop 为 no-op（吞启动错误不传播）。
  `stop_all()` 覆盖面扩为注册条目 ∪ 进行中启动键。
- **修正 3（取消不破坏去重）**：`_starting` 归属清理由启动任务
  自身的完成回调负责（`add_done_callback`——回调同时消费任务
  异常，杜绝 unobserved-task 报警）；调用方一律
  `await asyncio.shield(task)`——取消不传播到底层任务，同键
  第二次 start 在首次仍进行中时复用同一任务（第二个工厂零
  调用）。RED 抽查：回退 caller-owned finally 清理即取消去重
  测试失败。
- **修正 4（异常链抑制）**：包装订阅者/factory 异常的管理器错误
  一律 `from None`（旧 `from cause` 保留 __cause__ 链——独立复验
  证实异常文本经链外泄）；测试断言 __cause__ 为 None 且
  str/repr/traceback 序列化均不含合成 JWT 形态（factory/start/
  stop 三路径）。RED 抽查：回退 from cause 即链净化测试失败。
- 新增 **6 项 R1 聚焦测试**（30→36），四项均 RED→GREEN 实证。

## 验证（2026-10-12，worktree 内真实执行；全部零网络/零运行时服务）

| 命令 | 结果 |
| --- | --- |
| `... -m pytest services/api/tests/test_voice_runtime_manager.py -q` | **36 passed**（R1 后：30 基线 + 6 项 R1 聚焦；四项 RED→GREEN 抽查复跑） |
| `... -m pytest services/api/tests/test_voice_subscription.py services/api/tests/test_voice_runtime.py services/api/tests/test_voice_runtime_manager.py services/api/tests/test_voice_tokens.py services/api/tests/test_voice_providers.py -q` | **128 passed**（21+26+36+16+29；邻居零回归） |
| `... -m ruff check services/api/app/domain/voice_subscription.py services/api/app/domain/voice_runtime.py services/api/app/domain/voice_runtime_manager.py services/api/tests/test_voice_subscription.py services/api/tests/test_voice_runtime.py services/api/tests/test_voice_runtime_manager.py` | All checks passed（清理 5 处 noqa 冗余/未用解包后） |
| `git diff --check` | 干净（无 whitespace 错误） |

有效性抽查（RED）：A——临时移除容量前置检查 → 容量测试立即失败；
B——临时移除 `_starting` 并发复用 → 并发去重测试立即失败；两项
恢复后 30 全绿——测试钉住真实行为而非恒真。

新增/改动行卫生扫描：secret 形态 0 命中；JWT `eyJ` 命中均为合成
假值（FAKE_JWT 断言样本）；私有绝对路径 0；U+FFFD 0；readiness
置 true 0。

## 后续空间（不在本切片）

- FastAPI lifespan 持有 `VoiceRuntimeManager` 实例并接线
  `livekit_runtime_factories`（受控运行时切片，supervisor 决定）；
- 帧消费者（流式 ASR 管线入口）；VAD / barge-in / pause/resume；
- 真实 LiveKit 服务的受控验收。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-257（M14-256 转前一
  任务快照），里程碑段追加 M14-257 注记（计数不变）。
- `docs/ROADMAP.md`：M14 段新增 M14-257 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-257 条目（含非声明）。
