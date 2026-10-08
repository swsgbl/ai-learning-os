# M14-251 证据：Web voice-studio 服务端 ASR 听写通道（supervisor review 修正后定稿）

- 分支 `web/m14-251-voice-server-asr`（worktree
  `ai-learning-os-worktrees/m14-251-web-voice-server-asr`），基于
  current main `3c941558`（PR #337 merge，即 M14-250）。执行前
  porcelain 干净（基点本地 git 可验证）。
- 动因：M14-250 后 voice-studio 的朗读已走服务端 TTS，但听写链仍是
  浏览器 SpeechRecognition 本地设备；服务端 ASR adapter
  （M4-02/M14-01：`POST /api/v1/voice/transcribe`，VOICE_MODE 三模式
  路由 + fallback 头透出）在 apps/web 零消费者。本切片把听写切换为
  服务端 ASR 通道——首个消费该端点的 Web 代码：用户在浏览器录音，
  客户端编码真实 WAV 上送既有认证端点，服务端返回的 transcript
  继续走既有权威 VoiceSession `/answers` 流。**这是浏览器录音上送
  桥接，刻意不是 LiveKit**——流式 VAD/barge-in/pause/resume 为
  后续切片空间。

## 通道语义（本切片的核心边界）

- **服务端 transcript 为唯一权威**：`transcribeAudioWav` 把录得的
  WAV 以 multipart FormData 上送（file part 名 `audio`、filename
  `answer.wav`、MIME `audio/wav`——与服务端 `UploadFile` 参数名
  一致；MIME 恒定不依赖入参 Blob 自带 type——以显式类型重新装包
  （修正 2）；不手写 Content-Type，multipart boundary 由浏览器
  生成），
  服务端 `TranscriptionOut`（id/text/confidence/provider/
  latency_ms/audio_bytes/audio_stored/audio_object_key）忠实映射
  返回，snake_case 原样不改写。返回的 `text` 直接作为
  `submitAnswerForSession` 的 transcript——解析/规范化/判定仍全部
  由服务端 intent parser / answer normalizer 执行（M14-249 语义
  零改动）。
- **通道事实不虚报**：X-Voice-Provider / X-Voice-Fallback 响应头
  原样透出；头缺席不猜（provider 落 "unknown"、fallback 落
  false）。服务端 local 模式未配置真实引擎时的降级事实由服务端
  头忠实携带，客户端不越权改写、不静默换回浏览器识别。
- **错误语义与 request() 一致**：credentials include（AUTH_SECRET
  开启时 HttpOnly cookie 携带）、cache no-store、401 引导登录页
  （整页跳转清空客户端状态；已在登录页不再跳转防循环）、非 2xx →
  ApiError 携服务端 detail（422「音频内容为空」/413「音频超过
  20MB 上限」/502 ProviderUnavailable 脱敏文案原样透出；detail
  非字符串时落安全通用文案「服务端语音识别失败」）；网络失败
  原样透传不吞不改。
- **录音与编码诚实边界**（wav-recorder.ts）：`getUserMedia`
  audio-only 约束；MediaRecorder 采集；Web Audio 解码浏览器容器
  （webm/ogg）后本地重编码 RIFF/WAV——PCM、多声道均值混缩 mono、
  16-bit 小端、44 字节标准头；默认 60s 上限到点自动停止并产出
  有效 WAV（超时不是错误）；客户端 20MB 上限与服务端 413 同口径
  （超限不出浏览器，避免构造注定 413 的请求）。不支持
  getUserMedia/MediaRecorder（SSR/非安全上下文/旧浏览器）、权限
  被拒（NotAllowedError）、设备缺失（NotFoundError）、零 chunk、
  解码失败（decodeAudioData reject）、零帧解码结果、超限输出
  ——七类失败全部如实 reject 且错误文案可辨，**绝不静默回退
  浏览器 SpeechRecognition**（browser-speech.ts 已整体删除，
  死回退代码不存在）。
- **资源诚实回收**：超时定时器、recorder 监听（ondataavailable/
  onstop/onerror 置 null）、音轨/流（track.stop）、AudioContext
  （close）在成功、失败、手动停止、超时四条路径上一律清理
  （try/finally 结构保证；行为测试逐路径实证）。
- **录音期交互语义**：`stopActiveRecording` 提供模块级活跃录音的
  手动停止面（幂等 no-op）——voice-studio 麦克风按钮在录音期间
  保持可用并复用为「停止并提交」（micPhase 三态：idle/recording/
  transcribing；仅识别阶段禁用，录音阶段绝不整段禁用）。
- **时延观测权威分界**：asr 时延以服务端 /transcribe 内自动埋点
  （`_record_trace stage="asr"`，voice.py:262）为唯一权威——
  客户端听写链（录音 → WAV → /transcribe → /answers）零
  /voice/trace 请求（M14-250 R1 的 tts 同口径，杜绝
  /trace/summary 同 stage 双计数）；`tracedListen` 删除，客户端
  可上报边界归零（voice-trace.ts 通用模块保留给未来 vad/llm/
  first_audio，行为零改动）。

## 交付面（apps/web only，零服务端/生产变更）

1. **`apps/web/src/lib/server-asr.ts`（新增，通道模块）**：
   `transcribeAudioWav(wav: Blob)`（POST
   `${API_BASE}/api/v1/voice/transcribe`；返回
   `{ transcript: ServerAsrTranscript, provider, fallback }`——
   transcript 为服务端 JSON 忠实映射，provider/fallback 为响应头
   事实）；`ServerAsrTranscript` 类型与服务端 TranscriptionOut
   snake_case 一一对应。纯 IO 通道：不解析文本、不判定答案、
   不发任何 /voice/trace 请求。
2. **`apps/web/src/lib/wav-recorder.ts`（新增，录音/编码模块）**：
   `recordAndEncodeWav(options?)`（完整录音链，maxDurationMs 默认
   60_000）、`stopActiveRecording()`（手动停止面）、
   `encodeWavMono16(buffer)`（纯函数 WAV 编码器，导出供格式测试
   直接锚定）、`MAX_WAV_BYTES`（20MB，与服务端上限同口径）。
   纯采集/编码设备：不解析语音、不判定答案。
3. **`apps/web/src/lib/voice-session-flow.ts`（听写编排切换）**：
   新增 `recordTranscriptViaServerAsr(options)`（recordImpl →
   onRecorded 阶段回调 → transcribeImpl → 服务端 text；默认实现
   直连 recordAndEncodeWav + transcribeAudioWav；两实现仅供测试
   注入）；`tracedListen` 删除（withVoiceTrace import 随之移除）；
   其余编排逻辑（启动链/读题回执时序/answers 幂等/409 realign/
   推进/收尾）零改动。
4. **`apps/web/src/lib/browser-speech.ts`（删除）**：listenOnce
   无消费者，整个文件移除——不留死回退。
5. **`apps/web/src/lib/voice-trace.ts`（注释同步）**：头注释更新
   ——客户端可上报边界归零（asr/tts 均以服务端自动埋点为唯一
   权威），通用模块为未来 vad/llm/first_audio 保留；
   TRACE_STAGES 白名单镜像不动（契约真值）。行为零改动
   （voice-trace.test.ts 零改动全绿）。
6. **`apps/web/src/components/voice-studio.tsx`（听写链接入）**：
   busy 单态改 micPhase 三态（idle/recording/transcribing）；
   listen() = recordTranscriptViaServerAsr（onRecorded 切
   transcribing）→ setHeard(text) → 既有 submitTranscript；
   麦克风按钮 onClick 分派（recording → stopActiveRecording、
   transcribing → no-op（按钮已禁用）、idle → listen）；录音期
   按钮文案/图标/aria-label「停止并提交」，识别期「识别中」；
   Waveform active 于录音+识别两阶段。既有权威 /answers、event_id
   重试、409 realign、澄清、文字作答、选项点击流零改动；
   M14-189 约束保持（唯一 effect 启动链、零 set-state-in-effect、
   朗读全程事件化——本切片未触碰 effect 结构）。
7. **测试**：新增 `server-asr.test.ts` 12 项 + `wav-recorder
   .test.ts` 22 项（见下，含 supervisor review 修正各一项）；
   `voice-session-flow.test.ts` 更新
   （tracedListen 两项废除——M14-247 钉住的客户端 asr span 行为
   由 M14-251 判定废除，服务端埋点为唯一权威；
   recordTranscriptViaServerAsr 四项新增）；
   `react-hooks-hygiene.test.ts` voice-studio 段新增三项等价
   约束（零 SpeechRecognition/tracedListen/listenOnce 残留、
   录音期按钮可用仅识别期禁用、micPhase 三态可辨）；其余钉住项
   零改动；`voice-session-api.test.ts` 零改动（全绿——M14-248
   契约层未被触碰）。

## 修正记录（supervisor review，amend 回原提交）

- **缺陷 1（资源清理）**：`recordAndEncodeWav` 中 `new
  MediaRecorder(stream)` 若构造抛错（如流的编码不受支持），
  `getUserMedia` 已授予的音轨不会被停止——麦克风占用泄漏，且该
  路径在原 finally 保护之前。修正：构造调用纳入独立 try/catch，
  失败时先 `track.stop()` 释放全部已获得音轨再抛
  「无法启动录音：…」；模块级活跃录音引用在赋值之前，构造失败
  不残留（`stopActiveRecording` 保持幂等 no-op）。行为测试新增
  一项实证（构造抛错 → track.stop 恰一次、no-op 不再触碰）。
- **缺陷 2（multipart 契约）**：`form.append("audio", wav,
  "answer.wav")` 会原样继承入参 Blob 自带的 MIME——调用方传入
  无类型 Blob（type=""）时上送 File 的 MIME 为空串，违反「恒
  audio/wav」的通道契约。修正：先以 `{ type: "audio/wav" }`
  重新装包再 append，MIME 恒定不依赖入参，字节原样透传。行为
  测试新增一项实证（无类型 Blob → File 恰 name=answer.wav /
  type=audio/wav / 字节逐一致）。
- 两项修正均先 RED 验证（临时还原实现，新测试分别失败实证其
  确实钉住缺口）后 GREEN；计数 11+21 → 12+22、全套 295 → 297。

## focused 测试覆盖（12+22 项，全部行为级——stub 全局
fetch/window/getUserMedia/MediaRecorder/AudioContext 实证行为，
无源码字符串断言；vi.fn 不可 new——MediaRecorder/AudioContext 桩
用普通构造函数 + created 数组追踪，与 server-tts.test.ts 的
Audio 桩同思路）

- server-asr（12 项）：POST URL/method/multipart（FormData 实例、
  part 名 audio、File 实例、filename answer.wav、MIME audio/wav、
  字节数与源一致）/credentials include/cache no-store/零手写
  Content-Type；**无类型 Blob 入参 → 上送 File 恒 answer.wav /
  audio/wav、字节逐一致（MIME 不依赖入参——修正 2）**；
  TranscriptionOut 忠实映射（含 audio_object_key=
  null 形态）；provider/fallback 头三态（"1"→true、"0"→false、
  缺席→unknown/false）；422/502/413 → ApiError 携 detail；
  detail 非字符串 → 安全通用文案；401 跳登录（含登录页防循环）；
  网络失败原样透传；**零 /voice/trace**（成功/失败链均恰一个
  /transcribe 请求，迟到微任务排空后仍零）。
- wav-recorder（22 项）：手动停止链（getUserMedia audio-only →
  监听挂好后 start → stopActiveRecording → stop 恰一次 → 非空
  WAV）；无活跃录音 stopActiveRecording 幂等 no-op；超时自动停止
  （25ms 上限实证）仍产出有效 WAV；默认 60s 不缩水（100ms 内绝不
  自动停止）；onerror 如实 reject；资源清理（成功/解码失败/
  getUserMedia 失败/**MediaRecorder 构造失败——已获得音轨仍恒
  停止、活跃引用不残留（修正 1）**/模块引用复位——音轨 stop、
  监听置 null、AudioContext close、二次 stopActiveRecording 不
  触碰旧 recorder）；WAV 头完整性（RIFF/WAVE/fmt=16/PCM=1/
  mono=1/sampleRate/byteRate=rate×2/blockAlign=2/bits=16/
  data 长度/总长）；样本映射精确值（0→0、0.5→16383、
  -0.5→-16384、1→32767、-1→-32768）；双声道均值混缩 mono；
  MediaRecorder 容器字节透传解码（不重编码容器）；诚实拒绝七类
  （零 chunk 不进解码、零帧、超 20MB（AudioContext 仍回收）、
  无 MediaRecorder、无 mediaDevices、NotAllowedError、
  NotFoundError、无 AudioContext）。

## 验证（2026-10-09，worktree 内真实执行）

| 命令 | 结果 |
| --- | --- |
| `npx vitest run --root apps/web src/lib/server-asr.test.ts src/lib/wav-recorder.test.ts` | **2 files / 34 tests passed**（server-asr 12 + wav-recorder 22；修正 1/2 各 +1，RED→GREEN 实证） |
| `npx vitest run --root apps/web src/lib/voice-session-flow.test.ts src/lib/voice-session-api.test.ts src/lib/react-hooks-hygiene.test.ts src/lib/voice-trace.test.ts` | **4 files / 116 tests passed**（flow 更新 + api 零改动 + hygiene 等价更新 + trace 零改动） |
| `npm run test --workspace apps/web` | **17 files / 297 tests passed**（M14-250 基点 15 files/258 → +2 文件 +34 新测试 +2 flow 净增 +3 hygiene 净增，零回归） |
| `npm run typecheck --workspace apps/web` | exit 0，0 error |
| `npm run lint --workspace apps/web` | exit 0，0 error / 0 warning |
| `npm run build --workspace apps/web` | ✓ Compiled successfully，13 路由条目（本切片零 app/ 路由改动，静态/动态划分与基点一致） |
| `git diff --check` | 干净（无 whitespace 错误） |

新增/改动行卫生扫描（全部 staged `+` 行——代码 diff + 两个新模块
+ 两个新测试文件 + 三份 ledger 与本证据文档）：secret 形态
（api_key/secret/password/token/bearer/credential 赋值形态）
**0 命中**（唯一词形命中为 fetch `credentials` API 术语与描述
文字）；Windows 盘符/绝对本地路径 0 命中（唯一路径形命中为测试内
`http://localhost:3000` URL，非本地文件路径）；U+FFFD 0 命中；
`production_ready`/`release_ready`/`public_ready` 置 true 的过度
声明 0 命中。

依赖安装：worktree 首次 `npm install`（本地 registry 镜像，未走
代理；与 m14-247/248/249/250 相同的 unrs-resolver postinstall 按
allowScripts 策略被阻，不影响本切片任何验证）。

## 不做的事（诚实边界）

- **不声明 M4-09 或 capability gap 3 关闭**：缺口 3 仅进一步收窄为
  「读与写均已走服务端 ASR/TTS（浏览器录音→WAV 上送桥接）、
  LiveKit 流式链（VAD/barge-in/pause/resume）未进 Web UI」；
  M4-09 维持 partial（客户端已无可上报边界——asr/tts 均以服务端
  自动埋点为唯一权威；vad/llm/first_audio 无 Web 埋点），矩阵
  计数不变 implemented 60 / partial 3。
- **不做真实服务端/真实 provider/真实浏览器端到端验证**：真实
  麦克风采集、MediaRecorder 容器真实解码、录音听感、识别准确率
  均未冒烟（仓库测试口径：vitest node 环境 stub
  getUserMedia/MediaRecorder/AudioContext/fetch 的行为级断言，
  无 DOM 测试依赖）。服务端行为零改动（未重跑 pytest，语义以
  既有 test_voice 系列 ASR 契约为准）。
- **不做静默浏览器回退**：录音/转写失败如实提示（错误文案区分
  权限/设备/不支持/网络/服务端），重试 = 再点麦克风按钮，不静默
  换回浏览器识别假装通道正常——浏览器听写代码（browser-speech
  .ts）已从仓库移除。
- **不改服务端**：/transcribe 端点、VOICE_MODE 路由、fallback
  语义、服务端 asr 自动埋点、20MB 上限零改动；是否配置真实引擎
  （local-funasr/cloud-openai ASR）是服务端部署决策，Web 只透出
  不越权。
- 未启停 Docker/WSL/Ollama/ASR/TTS/LiveKit/生产容器或任何服务，
  未触碰 DB/Redis/LiveKit/MinIO/凭据，未改 readiness/生产配置；
  `production_ready`/`release_ready`/`public_ready` 均 false
  不变。
- LiveKit 流式听写、VAD、barge-in/pause/resume UI 入口、真实
  provider/浏览器麦克风冒烟仍为后续切片空间。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-251（M14-250 转前一
  任务快照），里程碑段追加 M14-251 收窄注记（计数不变）。
- `docs/ROADMAP.md`：M4 段缺口 3 注记更新（读与写均已走服务端
  ASR/TTS、LiveKit 流式未进、客户端埋点边界归零）；M14 段新增
  M14-251 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-251 条目。
