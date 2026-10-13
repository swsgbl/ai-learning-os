# M14-260 证据：有界语音帧计量汇（纯域层，fake-only）

- 分支 `api/m14-260-voice-frame-sink`（worktree
  `ai-learning-os-worktrees/m14-260-voice-frame-sink`），基于
  current main `e81d1b7bbc72f014c96c8b1935f8f7251028b8e4`（PR #346
  merge，即 M14-259）。执行前 porcelain 干净（基点本地 git 可验证）。
- 动因：M14-259 把管理器接进了应用生命周期，但 `EventSinkLike`
  仍只有测试替身实现——订阅事件与音频帧还缺一个有界、净化、
  可观测的默认归宿。本切片交付 M14-259 之后的下一个窄后端基础件：
  纯域层、同步、确定性的事件/帧计量汇 `BoundedVoiceFrameSink`。
  **这不是帧消费者管线**：不做流式 ASR/VAD/barge-in/pause-resume，
  不注册生产订阅者工厂，不连真实房间服务。

## 非声明（explicit non-claims）

- **无生产订阅者工厂注册**：汇是纯域类，任何装配（lifespan/
  app.state/工厂）均为后续切片；本切片零接线。
- **无应用生命周期/HTTP 变化**：不进 FastAPI lifespan、不动
  app.state、不新增/改动任何 HTTP 路由、`/readyz` 与 readiness
  语义零变化。
- **无真实房间连接/网络**：零真实网络、零运行时服务启停、零
  真实 DB 凭据、零真实 secret 读写。
- **无流式 ASR/VAD/barge-in/pause-resume/转写行为**。
- **无数据库/迁移/容器/compose/部署/Web/Android/Harmony 改动**。
- **不声明 M4-09 或 capability gap 3 关闭**：矩阵计数不变
  implemented 60 / partial 3。
- `production_ready=false` / `release_ready=false` /
  `public_ready=false` 不变。

## 交付面

**`services/api/app/domain/voice_frame_sink.py`（新增，纯域模块）**：

- `BoundedVoiceFrameSink`——结构满足
  `app.domain.voice_subscription.EventSinkLike`（on_event/
  on_frame）+ `snapshot()` 有界净化快照；构造注入正数
  `max_events`（默认 `DEFAULT_MAX_EVENTS = 64`，与订阅域
  `voice_subscription.DEFAULT_MAX_EVENTS` 同约定同值）。
- **事件面**：白名单字段投影（恰好 type/room/identity/
  category/detail 五个已知安全字段，其余属性在投影处丢弃）+
  环形保留最新 N 条（deque maxlen）+ 全量 event_count（含被
  淘汰者）；已知字段仅接受 str/None——其他类型（bytes/数值/
  列表等）统一映射固定占位 `[INVALID-FIELD]`（绝不 str() 化
  bytes，杜绝 b'...' 音频字节经字段值渗入投影，R1 修正 2）；
  str 值先过 JWT 三段形态正则替换 `[REDACTED-JWT]`（与运行时
  管理器同口径纵深防御）、再确定性截断到 `MAX_FIELD_LENGTH`
  （512 字符显式常量；R1 修正 3）；原始事件对象绝不保留
  （投影为新建 dict，不改写原值）。
- **帧面**：仅 bytes/bytearray/memoryview 三类载荷视为有效
  （R1 修正 1）——只读 `len(frame.data)` 计量 frame_count 与
  frame_byte_count，不复制、不切片、不保留帧对象或帧字节；
  帧数据缺失/属性访问抛错/非有效载荷类型（str/list/dict/
  任意 Sized——即使 len 可用）/不可计长/长度非非负整数一律
  计 invalid_frame_count 且**绝不抛出**；零网络/磁盘/进程/
  线程/定时器/事件循环操作。
- **快照面**：恰六个有界字段（max_events/event_count/events/
  frame_count/frame_byte_count/invalid_frame_count）；无音频
  字节、无 token/凭据、无异常文本、无私有路径、无时间戳、无
  无界历史；每次返回全新拷贝（含 events 内层 dict），调用方
  改写不影响后续快照。
- **依赖极轻**：运行时导入严格限定 `re`/`collections`/`typing`
  （源码卫生测试锚定）；`SubscriptionEvent`/`AudioFrameLike`
  仅 TYPE_CHECKING 引用——结构满足协议即可，不把订阅控制器
  的运行时依赖拉进本模块。

**`services/api/tests/test_voice_frame_sink.py`（新增，14 项
fake-only 契约测试）**：全合成 fake，零网络/零运行时服务。

1. **默认上限与域约定一致**：默认 `max_events == 64` 且等于
   订阅域 `DEFAULT_MAX_EVENTS`；空快照全零计数。
2. **自定义正数上限**：`max_events=2` 时只留最新两条。
3. **非正上限确定性拒绝**：0/-1/-64 一律抛
   `VoiceFrameSinkError("max_events 必须为正")`（消息恒定）。
4. **最新-N 保留 + 全量计数**：10 条事件上限 3——event_count
   == 10、投影恰为第 8~10 条；快照全新拷贝（外层追加/内层改写
   均不影响后续快照）。
5. **投影白名单 + JWT 净化 + 原始事件不保留**：恰五字段集合
   相等；room/detail 中 JWT 三段形态替换为 `[REDACTED-JWT]`；
   快照序列化零 JWT 命中；投影为新建 dict（与原事件无对象
   同一性）；鸭子事件的白名单外字段（token/帧字节）绝进投影。
5b. **已知字段非 str/None 映射固定占位（R1 修正 2）**：事件
    字段注入 PCM 形态 bytes 与数值/列表——投影值恒为
    `[INVALID-FIELD]`；序列化文本无 b'、无原始/解码字节表示、
    无控制字符转义。
5c. **字段净化后 512 截断（R1 修正 3）**：>512 字符 detail 的
    投影恰为前 512 字符；截掉的尾部标记与尾块不出现在序列化
    文本。
6. **有效帧计量**：bytes 11 字节 + bytearray 6 字节 +
   memoryview 2 字节 + 空 bytes 0 字节——frame_count == 4、
   frame_byte_count == 19、invalid == 0（三类载荷类型同等
   有效，R1 修正 1）。
7. **无效帧不抛出且计数**：缺 data 属性/data 为 None/data 非
   Sized/str/list/dict（len 可用但非有效载荷类型，R1 修正 1）/
   data 属性访问抛错（异常文本含 JWT 形态）/`__len__` 返回
   非数字——八种一律计 invalid_frame_count == 8、不抛出、
   不计入有效帧；其后有效帧照常计量。
8. **快照安全面**：顶层字段恰六个白名单键；序列化文本无原始
   音频字节、无 JWT 形态、无帧数据异常文本（"data boom"）。
9. **EventSinkLike 结构兼容**：runtime_checkable 协议
   isinstance 成立；on_event/on_frame/snapshot 均 callable。
10. **ServerVoiceSubscriber 集成契约**（沿用既有 fake room/
    stream 模式）：start → track_subscribed → 两帧转发 → stop，
    订阅者行为零变化（status stopped、清理顺序 stream_close →
    room_disconnect 与既有订阅测试同口径）；汇接住全部 4 事件
    （connecting/connected/track_subscribed/stopped）与 2 帧
    （17 字节）；快照序列化零 JWT、零帧字节。
11. **源码卫生（AST 级）**：运行时导入严格限定
    `{__future__, re, collections, typing}`（TYPE_CHECKING 块
    除外）；无 open/system/popen/run/sleep/ensure_future/
    create_task/gather/start_new_thread/Thread/Popen/urlopen
    调用；无 AsyncFunctionDef/Await/AsyncFor/AsyncWith 节点
    （纯同步模块）。

（第 11 项单函数覆盖导入/调用/async 三面；合计 14 个测试函数。）

## 验证（2026-10-13，worktree 内真实执行；全部零网络/零运行时服务）

使用 canonical venv 解释器（`.venv/Scripts/python.exe`）：

| 命令 | 结果 |
| --- | --- |
| `-m pytest services/api/tests/test_voice_frame_sink.py -q` | **14 passed** |
| `-m pytest services/api/tests/test_voice_frame_sink.py services/api/tests/test_voice_subscription.py services/api/tests/test_voice_runtime.py services/api/tests/test_voice_runtime_manager.py services/api/tests/test_voice_runtime_lifespan.py -q` | **111 passed**（14+21+26+44+6；订阅/适配/管理器/lifespan 基线 97 零回归） |
| `-m ruff check services/api/app/domain/voice_frame_sink.py services/api/tests/test_voice_frame_sink.py` | All checks passed |
| `git diff --check` | 干净（无 whitespace 错误） |

TDD 顺序：先建测试文件确认 RED（`cannot import name
'voice_frame_sink'` 集合失败），后实现模块转 GREEN；期间修正过
一处测试自身缺陷（事件 detail 标记词与帧异常标记词撞车导致
断言误报，以不同标记区分两来源后全绿）。

**Supervisor R1 三项修正已实施并 amend 回本提交**（14/14 通过）：
①有效帧载荷类型收紧为仅 bytes/bytearray/memoryview（str/list/
dict 等 len 可用亦计无效帧）；②已知事件字段仅接受 str/None，
其他类型统一固定占位 `[INVALID-FIELD]`（绝不 str() 化 bytes）；
③事件字段净化后确定性截断到 `MAX_FIELD_LENGTH`（512 字符显式
常量），截掉的尾部不入快照。

新增行卫生：secret 形态 0 命中；JWT `eyJ` 命中均为合成假值；
私有绝对路径 0；U+FFFD 0；readiness 置 true 0。

## 后续空间（不在本切片）

- 生产订阅者工厂注册与汇的运行时装配（受控运行时切片）；
  帧消费者管线；VAD/barge-in/pause-resume；真实房间服务的
  受控验收。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-260（M14-259 转
  前一任务快照），里程碑段追加 M14-260 注记（计数不变）。
- `docs/ROADMAP.md`：M14 段新增 M14-260 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-260 条目（含非声明）。
