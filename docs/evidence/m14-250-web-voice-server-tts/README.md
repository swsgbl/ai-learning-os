# M14-250 证据：Web voice-studio 服务端 TTS 通道（R1 修正后定稿）

- 分支 `web/m14-250-voice-server-tts`（worktree
  `ai-learning-os-worktrees/m14-250-web-voice-server-tts`），基于
  current main `7c7912e3`（PR #336 merge，即 M14-249）。执行前
  porcelain 干净（基点本地 git 可验证）。
- 动因：M14-249 后 voice-studio 已消费服务端权威 VoiceSession
  （状态/题面/答案/报告全以服务端返回为准），但朗读链仍是浏览器
  speechSynthesis 本地输出设备；服务端 TTS adapter（M4-02/M14-01：
  `POST /api/v1/voice/synthesize`，VOICE_MODE 三模式路由 + fallback
  头透出）与 `GET /api/v1/voice/providers` 视图在 apps/web 零消费者。
  本切片把朗读切换为服务端 TTS 通道——首个消费两者的 Web 代码，与
  Android M12-03 announce 链（synthesize → 播放 → 真实完成才发读题
  回执，失败如实可重试）同构。

## R1 修正记录（supervisor Round 1，amend 回原提交）

- **缺陷**：初版把客户端 tts 埋点保留为「服务端合成+播放全程」的
  span（tracedSpeak 默认包装 withVoiceTrace("tts")）——与服务端
  /synthesize 内自动埋点（source=server，仅合成耗时）同名并存，
  `/trace/summary` 按 stage 聚合不分 source，构成同 stage 双计数与
  口径混淆。
- **修正（只动这一处缺陷，零范围扩展）**：`tracedSpeak` 整个删除
  （voice-session-flow.ts），voice-studio speakPhase 直连
  `speakWithServerTts`；**成功/失败的服务端 TTS 朗读只发
  `/synthesize`、零 `/voice/trace` 请求**（行为测试实证，含迟到
  fire-and-forget 排空后仍为零）；`tracedListen`（asr span）原样
  保留；`voice-trace.ts` 头注释同步（客户端可观测边界收窄为 asr
  一处；`TRACE_STAGES` 仍完整镜像服务端白名单，不因客户端只用
  asr 而收窄）。测试随 R1 更新：server-tts.test.ts 的 tracedSpeak
  接线两项替换为「零客户端 tts 埋点」两项；voice-session-flow
  .test.ts 删 tts span 两项、补听写失败零上报一项（27→26）。

## 通道语义（本切片的核心边界，R1 后口径）

- **播放真实完成才 resolve**：`playWavAudio` 只在 HTMLAudioElement
  `ended` 事件后 resolve；`error` 事件、`play()` 拒绝（如自动播放
  策略）、合成失败（网络/4xx/5xx）一律如实 reject——上层
  （M14-249 speakQuestionWithEvents）沿用同一契约：失败/中断不发
  question_read/options_read、错误如实提示、重复读题按钮可重试。
  服务端返回空音频字节时通道直接拒绝，绝不把空合成当作可播放语音。
- **通道事实不虚报**：X-Voice-Provider / X-Voice-Fallback 响应头
  原样透出；服务端 local 模式未配置真实引擎时降级 tone 替身
  （零依赖 WAV，非真实语音）——Web 照播不静默换回浏览器 TTS（通道
  是服务端部署决策，客户端不越权改写），同时进度卡徽标经
  providers 视图如实标注「降级替身，非真实语音」。
- **错误语义与 request() 一致**：credentials include（AUTH_SECRET
  开启时 HttpOnly cookie 携带）、401 引导登录页（整页跳转清空
  客户端状态）、非 2xx → ApiError 携服务端 detail（含 502 的
  ProviderUnavailable 固定脱敏文案）；4xx 不重试（合成无幂等键，
  逐段朗读即逐段新请求）。
- **资源与占用回收**：object URL 无论 ended/error/play 拒绝恒
  revoke；新播放开始时切断上一段未完成播放（与浏览器时代
  speechSynthesis.cancel 同语义）——被放弃链的 promise 不再结算，
  对应读题回执事件不发；completeExam/离场经 stopServerTtsPlayback
  停止（pause + revoke，幂等 no-op）。
- **时延观测权威分界（R1）**：客户端只上报 **asr** span
  （tracedListen，携带真实 session_id/question_id）；**tts 时延以
  服务端 /synthesize 内自动埋点（source=server，仅合成耗时）为
  唯一权威**——客户端朗读链零 /voice/trace 请求，杜绝
  /trace/summary 同 stage 双计数；vad/llm/first_audio 仍无 Web
  埋点（M4-09 维持 partial 的依据之一）。
- **朗读链服务端化、听写仍浏览器原生**：browser-speech.ts 移除已
  无消费者的 speakUtterance，保留 listenOnce——缺口 3 剩余边界为
  服务端 ASR/LiveKit 未进 Web UI。

## 交付面（apps/web only，零服务端/生产变更）

1. **`apps/web/src/lib/server-tts.ts`（新增，通道模块）**：
   `synthesizeSpeech`（POST `${API_BASE}/api/v1/voice/synthesize`，
   body 恰 `{text}`；WAV 字节响应；provider/fallback 头透出；空字节
   拒绝）、`playWavAudio`（audio/wav Blob → object URL → Audio；
   ended 才 resolve；error/play 拒绝 reject；URL 恒回收；新播放切
   旧播放）、`stopServerTtsPlayback`（模块级当前播放占用的收尾
   停止）、`speakWithServerTts`（合成→播放组合，零埋点纯 IO）。
   纯 IO 设备：不解析文本、不判定语音质量、不推演会话状态、
   不发任何 /voice/trace 请求。
2. **`apps/web/src/lib/voice-session-flow.ts`（通道切换点 + R1）**：
   删除 tracedSpeak（R1：客户端 tts 埋点移除）；tracedListen
   （asr span）原样保留；其余编排逻辑（启动链/读题回执时序/
   answers 幂等/409 realign/推进/收尾）零改动。
3. **`apps/web/src/lib/browser-speech.ts`（收窄）**：移除
   speakUtterance（唯一消费者已切服务端通道），保留 listenOnce；
   头注释更新（ASR 仍浏览器原生 = 缺口 3 剩余边界）。
4. **`apps/web/src/components/voice-studio.tsx`（通道接入）**：
   speakPhase 直连 `speakWithServerTts`（零客户端 tts 埋点）；
   启动链 promise 回调内尽力而为 `api.voiceProviders()`（失败缺省
   null——徽标缺席不阻塞作答、不伪造通道事实），进度卡新增 TTS
   通道徽标（`data-testid="tts-channel"`，tone 降级如实标注）；
   `completeExam` 由 `window.speechSynthesis?.cancel()` 改为
   `stopServerTtsPlayback()`。M14-189 约束保持：唯一 effect 为启动
   链（providers/徽标 setState 全在 promise 回调，effect 体内零同步
   状态更新），朗读只在启动回调/切题回调/重复读题按钮发起。
5. **`apps/web/src/lib/types.ts` / `api.ts`（契约面）**：新增
   `VoiceProviderView`/`VoiceProvidersView`（与服务端 ProvidersOut
   snake_case 一一对应）与 `api.voiceProviders()`（GET
   /api/v1/voice/providers，经既有 request() 继承 credentials/
   headers/ApiError 语义）。
6. **`apps/web/src/lib/voice-trace.ts`（注释同步，R1）**：头注释
   更新——客户端可观测边界收窄为 asr 一处，tts 以服务端自动埋点为
   唯一权威；TRACE_STAGES 白名单镜像不动（契约真值）。行为零改动
   （voice-trace.test.ts 零改动全绿）。
7. **测试**：新增 `server-tts.test.ts` 17 项 vitest 行为测试（见
   下）；`react-hooks-hygiene.test.ts` voice-studio 段更新为等价
   约束（38→40：收尾停止断言改 stopServerTtsPlayback；新增「朗读
   链零 speechSynthesis/speakUtterance 残留」「通道徽标透出
   providers 视图」两项；其余钉住项零改动）；
   `voice-session-api.test.ts` **零改动**（30 项全绿——M14-248
   契约层未被触碰）；`voice-session-flow.test.ts` 随 R1 更新
   （27→26：删 tts span 两项、补听写失败零上报一项——M14-249 钉住
   的客户端 tts span 行为由 R1 判定废除，服务端埋点为唯一权威）。

## focused 测试覆盖（17 项，全部行为级——stub 全局 fetch/Audio/URL
实证 HTTP 契约/时序/资源回收，无源码字符串断言；vi.fn 不可 new，
Audio 桩用普通构造函数 + created 数组追踪）

- synthesizeSpeech 契约：POST URL/method/body 恰 `{text}`/
  credentials include/JSON 头；X-Voice-Provider 透出与
  X-Voice-Fallback 三态（"1"→true、"0"/缺席→false）；422/502 →
  ApiError 携服务端 detail；401 → 与 request() 同语义跳登录页；
  空字节 200 → 如实拒绝。
- playWavAudio：audio/wav Blob 建 object URL；ended 才 resolve 且
  播放中不回收、结束后回收；error 事件 reject + URL 回收；play()
  拒绝（自动播放策略形态）reject + URL 回收；无 window（SSR）
  reject；新播放切断上一段（pause + 回收，旧 promise 不再结算——
  实证 settled=false）。
- stopServerTtsPlayback：暂停 + 回收 + 幂等 no-op。
- speakWithServerTts：先合成后播放（Audio 在 fetch resolve 后才
  构造）；合成失败零播放（Audio 从未构造、零 object URL）。
- api.voiceProviders：GET（无 method/body）、响应 JSON 原样透传。
- **R1 零客户端 tts 埋点**：成功链恰一个请求（synthesize，body 恰
  text），播放完成 + 迟到 fire-and-forget 排空后仍零 /voice/trace；
  合成失败同样零 trace（错误原样抛出，失败不构成样本也不双计数）。

## 验证（2026-10-08，worktree 内真实执行；R1 修正后全量复跑）

| 命令 | 结果 |
| --- | --- |
| `npm run test --workspace apps/web -- src/lib/server-tts.test.ts` | **1 file / 17 tests passed**（RED 先行：实现前因模块缺失全部失败实证；R1 后接线两项替换为零埋点两项复跑全绿） |
| `npm run test --workspace apps/web -- src/lib/voice-session-flow.test.ts src/lib/voice-session-api.test.ts src/lib/react-hooks-hygiene.test.ts` | **3 files / 96 tests passed**（26+30+40；api 零改动、flow 随 R1 27→26、hygiene 等价更新 38→40） |
| `npm run test --workspace apps/web` | **15 files / 258 tests passed**（M14-249 基点 14 files/240 → +1 文件 +17 通道测试 +2 hygiene −1 R1 移除的 tts span 测试净额，零回归） |
| `npm run typecheck` | exit 0，0 error |
| `npm run lint` | exit 0，0 error / 0 warning（react-hooks/set-state-in-effect、react-hooks/refs 均无告警） |
| `npm run build --workspace apps/web` | ✓ Compiled successfully，13 路由条目（本切片零 app/ 路由改动，静态/动态划分与基点一致） |
| `git diff --check` | 干净（无 whitespace 错误） |

新增/改动行卫生扫描（amend 前全部 staged 行——代码 diff `+` 行 +
两个新文件全量 + 三份 ledger 与本证据文档）：secret 形态（api_key/
secret/password/token/bearer/credential 赋值形态）**0 命中**（唯一
词形命中为 fetch `credentials` API 术语与描述文字）、Windows 盘符/
绝对本地路径 0 命中（唯一路径形命中为测试内 `http://localhost:3000`
URL，非本地文件路径）、U+FFFD 0 命中、
`production_ready`/`release_ready`/`public_ready` 置 true 的过度
声明 0 命中。

依赖安装：worktree 首次 `npm install`（本地 registry 镜像，未走
代理；与 m14-247/248/249 相同的 unrs-resolver postinstall 按
allowScripts 策略被阻，不影响本切片任何验证）。

## 不做的事（诚实边界）

- **不声明 M4-09 或 capability gap 3 关闭**：缺口 3 仅进一步收窄为
  「朗读已走服务端 TTS（synthesize + WAV 播放）、听写仍浏览器原生
  SpeechRecognition——服务端 ASR/LiveKit 链未进 Web UI」；M4-09
  维持 partial（客户端埋点边界仅 asr 一处；vad/llm/first_audio 无
  Web 埋点），矩阵计数不变 implemented 60 / partial 3。
- **不做真实服务端/真实 provider/真实浏览器端到端验证**：音频可听
  质量、自动播放策略实际表现、tone 降级的真实听感均未冒烟（仓库
  测试口径：vitest node 环境 stub fetch/Audio/URL 的行为级断言，
  无 DOM 测试依赖）。服务端行为零改动（未重跑 pytest，语义以既有
  test_voice 系列契约为准）。
- **不做静默浏览器回退**：服务端合成/播放失败时如实提示 + 重复
  读题按钮重试（与 Android 同口径），不静默换回浏览器 TTS 假装
  通道正常——浏览器 speechSynthesis 朗读代码（speakUtterance）
  已从仓库移除。
- **不改服务端**：synthesize/providers 端点、VOICE_MODE 路由、
  fallback 语义、服务端 tts 自动埋点零改动；是否配置真实引擎
  （local-cosyvoice/cloud-openai-tts）是服务端部署决策，Web 只
  透出不越权。
- 未启停 Docker/WSL/Ollama/ASR/TTS/生产容器或任何服务，未触碰
  DB/Redis/LiveKit/MinIO/凭据，未改 readiness/生产配置；
  `production_ready`/`release_ready`/`public_ready` 均 false 不变。
- 语音报告完整分层播报、播报期 barge_in/pause/resume UI 入口仍为
  后续切片空间（M14-249 既注）。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-250（R1 修正后口径，
  M14-249 转前一任务快照），里程碑段追加 M14-250 收窄注记（计数
  不变）。
- `docs/ROADMAP.md`：M4 段缺口 3 注记更新（朗读已走服务端 TTS、
  听写仍浏览器原生、客户端埋点边界收窄为 asr）；M14 段新增
  M14-250 状态更新小节（含 R1 修正记录）。
- `docs/CHANGELOG.md`：新增 M14-250 条目（含 R1 修正）。
