# M14-252 证据：Web VoiceStudio LiveKit 管理语音输入（输入传输切片，非流式）

- 分支 `web/m14-252-voice-livekit-input`（worktree
  `ai-learning-os-worktrees/m14-252-voice-livekit-input`），基于
  current main `f8259b07`（PR #338 merge，即 M14-251）。执行前
  porcelain 干净（基点本地 git 可验证）。
- 动因：M14-251 的浏览器录音桥接（getUserMedia → 本地 WAV →
  /transcribe）每次作答独立开关麦克风，LiveKit 房间/轨道在
  VoiceStudio 零接入。本切片把语音输入传输升级为 LiveKit 管理：
  首次语音作答懒获取一条本地麦克风音轨、连接房间、发布该轨，
  同一已发布轨贯穿整场练习的每段 utterance 录音与既有
  /transcribe 上送；房间/音轨跨题存活，完卷/unmount 清理。
- **角色边界**：本切片零服务端/生产变更——未启停 Docker/WSL/
  LiveKit/API/Web/ASR/TTS/代理/模拟器或任何生产服务，未触碰
  DB/LiveKit 服务端/凭据。LiveKit JWT 在生产代码中只作
  `room.connect(wsUrl, token)` 的局部参数，绝不进入 React
  state、DOM、日志、测试快照或本证据（测试使用的 token 为结构
  相同的假值 `eyJhbGciOiJIUzI1NiJ9.…`，非真凭据）。

## 非声明（explicit non-claims）

- **不声明服务端订阅**：Web 只 publish 自己的音轨，不订阅其他
  参与者/服务端轨道。
- **不声明流式 ASR**：utterance 仍整段录制 → 本地 WAV → multipart
  POST /transcribe（M14-251 通道原样），非边说边识别。
- **不声明 VAD / barge-in / pause/resume**：朗读打断、暂停恢复、
  语音活动检测均未实现（M14-249 既注空间）。
- **不声明 M4-09 或 capability gap 3 关闭**：缺口 3 收窄为
  「Web 输入传输已 LiveKit 管理、服务端订阅/流式链未接入」；
  M4-09 维持 partial（vad/llm/first_audio 无 Web 埋点），矩阵
  计数不变 implemented 60 / partial 3。
- `production_ready` / `release_ready` / `public_ready` 均 false
  不变；真实 LiveKit 服务/真实浏览器麦克风端到端冒烟缺位
  （仓库测试口径：vitest node 环境 stub + 注入点行为断言，无
  DOM/真实网络依赖）。

## 控制器语义（本切片的核心边界）

- **懒连接**：`getOrCreateVoiceInput(existing, sessionId)` 只在
  用户点击语音按钮时被调用——页面加载、渲染、effect 均零麦克风
  请求/零连接。existing 已连接 → 原样复用（零新 token/连接/
  发布）；stale（断连/失效）→ 先收尾旧控制器（停旧轨）再显式
  新建；null → 直接新建。
- **确定性房间名**：`voiceRoomName(sessionId)` = `voice-<session_id>`
  （session_id 为服务端 create 回传的权威 UUID）；请求 token 前
  对照服务端 `_ROOM_RE`（`^[A-Za-z0-9_-]{3,64}$`）契约校验，
  不安全 id（含 `/ ? #` 空格、超长）零网络直接拒绝——不构造
  注定 422 的请求。同一 session 恒同名（不同 session 不同名，
  权威会话互不串音）。
- **token 安全**：token 只进 `room.connect(wsUrl, token)` 的局部
  参数；控制器 state 只有 `{status, room, identity, error}`——
  结构上无 token 字段；一切错误经 `sanitizeError`（复用
  livekit-check.ts：JWT 形态正则替换 `[已脱敏]` + 空白折叠 +
  200 字符截断）后才可能到达 UI。
- **顺序与单轨**：token → Room.connect → createLocalAudioTrack →
  publishTrack（行为测试按 events 序列实证）。一条本地音轨一次
  发布贯穿整场练习；后续 utterance 的 `recordWav()` 从已发布轨的
  `mediaStreamTrack` 构造 `MediaStream` 走
  `recordAndEncodeWavFromStream`（零 getUserMedia、零 republish、
  零新 token）。
- **失败与资源**：token/connect/mic/publish/超时（token 10s、
  connect 20s、mic/publish 15s，复用 livekit-check 常量 +
  withTimeout）任何一步失败先释放已获资源——已开的麦克风
  `track.stop()`、已连的房间 `room.disconnect()`——再抛脱敏错误
  （不留活麦克风、不留连接房间）。后续断连（RoomEvent.
  Disconnected）或音轨 ended 即失效：`recordWav` 如实拒绝
  「语音连接已断开，请重试」，不静默复用 stale 控制器。
- **幂等收尾**：`disconnect()` 恰一次停麦克风 + 断房间（closed
  守卫），二次调用 no-op；安全用于 completeExam 与组件 unmount。
- **不回退**：LiveKit 失败如实提示可重试（下次点击走
  getOrCreateVoiceInput 显式重连）；组件内零
  `recordAndEncodeWav` 直接调用（hygiene 源码契约钉住）——
  绝不静默回退直接浏览器录音假装通道正常。
- **音轨所有权**（wav-recorder 重构核心）：提供流入口
  `recordAndEncodeWavFromStream(stream, { ownsTracks })`——
  缺省 false=调用方（LiveKit 控制器）拥有，utterance 录完不停轨
  （所有者管理生命周期）；true=本次调用拥有恒停止。直接入口
  `recordAndEncodeWav()` 拥有并恒停自己的轨——M14-251 行为（含
  supervisor 修正：构造失败释放、MIME 不依赖入参）零回归。
  `stopActiveRecording()` 对两入口共用同一模块级占用面照常生效
  （幂等 utterance 停止）。

## 交付面（apps/web only）

1. **`apps/web/src/lib/livekit-voice-input.ts`（新增，控制器）**：
   `voiceRoomName` / `connectVoiceInput` / `getOrCreateVoiceInput` /
   控制器（`recordWav`/`state`/`disconnect`）+ 类型
   （`LiveKitRoomLike`/`VoiceInputTrack`/`LiveKitInputDeps` 等）。
   `LiveKitInputDeps.createRoom`/`createLocalAudioTrack` 为单元
   测试注入点（默认实现直连 livekit-client SDK：`new Room()`、
   `createLocalAudioTrack()`）。
2. **`apps/web/src/lib/wav-recorder.ts`（重构，行为零回归）**：
   拆出共享核心 `recordStreamToWav(stream, Recorder,
   maxDurationMs, ownsTracks)` 与提供流入口
   `recordAndEncodeWavFromStream`；`requireRecorderSupport`（仅
   MediaRecorder，供提供流路径）与 `requireRecordingSupport`
   （getUserMedia + MediaRecorder，供直接路径）分离；直接路径
   与全部 M14-251 契约保持。
3. **`apps/web/src/components/voice-studio.tsx`（集成）**：
   `voiceInputRef`（控制器跨题复用）+ `releaseVoiceInput`（幂等
   收尾）+ `listen()` 懒连接/复用（`recordImpl: () =>
   active.recordWav()` 注入 `recordTranscriptViaServerAsr`，
   `onRecorded` 切 transcribing）+ micPhase 四态 + 失败清 stale
   引用如实重试 + completeExam/unmount 双路径收尾（unmount 为
   纯清理 effect：体内零 setState/零麦克风请求/零答案流）+
   按钮四态（连接中/停止并提交/识别中/语音作答；recording 期
   保持可用，connecting/transcribing 禁用但各有超时兜底）。
   权威 /answers、event_id 重试、409 realign、澄清、文字作答、
   选项点击、TTS 朗读链零改动。
4. **`apps/web/src/components/voice/voice-index-view.tsx（文案）**：
   过时表述「练习仍使用浏览器本地语音；完整 ASR/TTS 会话尚未
   接入」更正为「语音作答走 LiveKit 管理的麦克风输入，识别与
   朗读由服务端 ASR/TTS 权威处理；流式语音（边说边识别、VAD、
   打断/暂停）尚未接入」。
5. **测试**：新增 `livekit-voice-input.test.ts` 19 项；
   `wav-recorder.test.ts` 增 6 项（22→28）；
   `react-hooks-hygiene.test.ts` voice-studio 段等价更新
   （40→46：effect 断言改为「集恰两个：启动链 + unmount 纯清理」；
   M14-252 新增三项；既有权威链断言全部保持）；
   其余测试文件零改动（server-asr/voice-session-flow/
   voice-session-api/server-tts/voice-trace 全绿——M14-251 契约
   零回归）。

## focused 测试覆盖（19+28+46，全部行为级——stub fetch + 注入
createRoom/createLocalAudioTrack + MediaRecorder/AudioContext/
MediaStream 桩；RED 先行：livekit-voice-input 19 项在模块缺失时
全部失败实证、wav-recorder 6 项在入口缺失时全部失败实证后编码）

- 房间名（3 项）：确定性（同 id 同名、`voice-<id>` 落在服务端
  契约内）、不同 session 不同名、不安全 id（`/ ? #` 空格/空串/
  超长）→ null。
- 连接编排（4 项）：events 序列恰 `token → createRoom → connect
  → localTrack → publish`；connect 恰收到 (ws_url, token 原文)
  而 state JSON 零 JWT；token 请求体恰 `{room}`（POST
  /api/v1/voice/token）；不安全 id 零网络零房间直接拒绝。
- 单轨复用与录制（2 项）：第二次 utterance 零新 token/connect/
  publish 且产出 WAV；recordWav 用已发布轨（utterance 完成后
  音轨未停止、房间仍连接）。
- 失败路径（6 项）：token 503（零房间零麦克风）；connect 失败
  （未连上无需断开、麦克风从未开启）；connect 失败错误 JWT 脱敏
  （`[已脱敏]` 替换）；mic 失败（断开已连房间）；publish 失败
  （停麦克风 + 断房间）；token 永挂 → 10s 超时拒绝（fake timers
  实证零房间零麦克风）。
- 失效与收尾（4 项）：断连事件 → state disconnected + recordWav
  拒绝；音轨 ended → recordWav 拒绝；disconnect 幂等（track.stop
  恰一次、room.disconnect 恰一次、state=disconnected、recordWav
  拒绝）；getOrCreate 复用（原样返回零新请求）/stale 重建（旧轨
  收尾恰一次 + 新 token）/null 新建。
- 提供流录音（6 项，wav-recorder.test.ts）：零 getUserMedia 直接
  录制提供流（容器字节透传解码）；默认所有权（LiveKit）utterance
  完不停轨；ownsTracks=true 成功/失败路径恒停轨；
  stopActiveRecording 生效且模块引用复位（两段录音互不干扰）；
  超时自动停止产出有效 WAV 且不停调用方轨。
- voice-studio 等价约束（react-hooks-hygiene，源码契约钉组件
  接线；行为级验证在上述 lib 测试）：effect 集恰两个且 unmount
  清理体内零 setState/零 getUserMedia；语音按钮零
  `recordAndEncodeWav` 直接调用（`recordImpl: () =>
  active.recordWav()`）；完卷（completeExam 内）与 unmount 双
  路径 `releaseVoiceInput`；失败清 stale 引用（「失败不留 stale
  控制器」）+「语音连接失败」如实文案；micPhase 四态与
  connecting/transcribing 禁用形态（recording 绝不整段禁用）。

## 验证（2026-10-09，worktree 内真实执行）

| 命令 | 结果 |
| --- | --- |
| `npx vitest run --root apps/web src/lib/livekit-voice-input.test.ts` | **1 file / 19 tests passed**（RED 先行实证） |
| `npx vitest run --root apps/web src/lib/wav-recorder.test.ts` | **1 file / 28 tests passed**（+6 提供流，原 22 零回归） |
| `npx vitest run --root apps/web src/lib/react-hooks-hygiene.test.ts` | **1 file / 46 tests passed**（等价更新后） |
| `npm run test --workspace apps/web` | **18 files / 325 tests passed**（M14-251 基点 17 files/297 → +1 文件 +19 控制器测试 +6 提供流 +3 hygiene 净增，零回归） |
| `npm run typecheck --workspace apps/web` | exit 0，0 error |
| `npm run lint --workspace apps/web` | exit 0，0 error / 0 warning |
| `npm run build --workspace apps/web` | ✓ Compiled successfully，13 路由条目（零 app/ 路由改动，静态/动态划分与基点一致） |
| `git diff --check` | 干净（无 whitespace 错误） |

新增/改动行卫生扫描（全部 staged `+` 行）：secret/JWT 形态
（api_key/secret/password/apikey/bearer 赋值形态 + `eyJ…` 三段
JWT 正则）——测试中 JWT 形态命中仅为**结构相同的假 token 断言
样本**（`livekit-voice-input.test.ts` 的 FAKE_JWT 与其脱敏断言，
非真凭据；生产代码零命中）；Windows 盘符/私有绝对路径 0 命中；
U+FFFD 0 命中；`production_ready`/`release_ready`/`public_ready`
置 true 的过度声明 0 命中。

依赖安装：worktree 首次 `npm install`（本地 registry 镜像，未走
代理；与 m14-247…251 相同的 unrs-resolver postinstall 按
allowScripts 策略被阻，不影响本切片任何验证）。

## 不做的事（诚实边界）

- 见顶部「非声明」。此外：不做服务端订阅、不做流式分片上送、
  不做真实 LiveKit 服务连通性验证（连接行为以 livekit-connect-
  card 的既有检测为准）、不在 effect/页面加载时请求麦克风、
  不静默回退直接浏览器录音、不修改服务端 token 端点或房间契约
  （`_ROOM_RE` 只被镜像校验，未被改动）。
- 未启停任何生产/运行时服务；未读取或打印任何 secret 值；
  LiveKit JWT 在生产代码中仅存在于 `connectVoiceInput` 的局部
  变量并直接传入 `room.connect`。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-252（M14-251 转前
  一任务快照），里程碑段追加 M14-252 收窄注记（计数不变）。
- `docs/ROADMAP.md`：M4 段缺口 3 注记更新（Web 输入传输已
  LiveKit 管理、服务端订阅/流式链未接入）；M14 段新增 M14-252
  状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-252 条目（含非声明）。
