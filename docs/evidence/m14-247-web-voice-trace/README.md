# M14-247 证据：Web 客户端语音时延埋点（M4-09 诚实可测切片）

- 分支 `web/m14-247-voice-trace`（worktree
  `ai-learning-os-worktrees/m14-247-web-voice-trace`），基于 current
  main `84072e173dd884227622df1aa0ef350f104a226f`（M14-246 API 可观测性）。
  执行前 porcelain 干净（基点本地 git 可验证）。
- 动因：M14-245 能力真相矩阵判定 M4-09（Latency tracing）为 🟡
  partial——服务端 asr/tts/intent/fsm 自动埋点与聚合视图已在，但
  `apps/web` 无任何调用 `POST /api/v1/voice/trace` 的代码（矩阵 §5
  缺口 4），客户端时延闭环未打通。本切片只补 Web 浏览器**真实可观测**
  的部分，不虚报测不到的环节。

## 交付面（apps/web only，零服务端/生产变更）

1. **`apps/web/src/lib/voice-trace.ts`（新模块，类型 + API wrapper）**
   - `TRACE_STAGES` 七阶段白名单 `vad/asr/intent/fsm/llm/tts/first_audio`
     与 `services/api/app/domain/voice_trace.py` 同口径；运行时防御式
     校验（非法 stage 构造 null，不出浏览器）。
   - `buildTraceSpanPayload` 纯函数：`duration_ms` 四舍五入为整数后必须
     落在 `[1, 600000]`（服务端 `TraceSpanRequest` ge=1/le=600000）；
     **越界返回 null——不截断不虚报**（宁可丢样本，不把 12 分钟改写成
     600000；与 Android 端 `coerceIn` 截断策略不同，为有意选择）。
   - 可选 `session_id/exam_id/question_id` 仅在为真值字符串时进载荷
     （空串/undefined 一律省略，请求体不带 undefined 键）。
   - `submitVoiceTrace`：专用 fetch POST `${API_BASE}/api/v1/voice/trace`
     （credentials include + JSON）；**不用 `api.ts` 的 `request()`**——
     401 登录跳转语义对观测上报是错误行为（会打断作答流）；网络失败/
     非 2xx（503 无 DB / 422 越界）一律静默 resolve，绝不 reject。
   - `withVoiceTrace(stage, run, context)` 计时包装：成功完成后
     fire-and-forget 上报；**业务异常原样透传且零上报**（失败调用不构成
     时延样本）；埋点永不改变主流程语义。
2. **`apps/web/src/components/voice-studio.tsx`（仅两处真实边界插桩）**
   - `listenOnce()` 外围 `asr`（浏览器 SpeechRecognition 听写耗时），
     `speakLocal()` 外围 `tts`（浏览器 speechSynthesis 朗读耗时）——
     这是 Web 语音陪练流程中仅有的两个真实浏览器语音边界。
   - 上下文：`exam_id`（经 `examIdRef`/`session.exam_id`，真实在位）与
     `question_id`（当前题）；**`session_id` 不携带**——该流程走
     `POST /papers/{id}/exams`（mode=voice），不创建 VoiceSession
     （FSM），无真实来源，不虚报。
   - 被钉住的 M14-189 契约字符串（三处 `speak(...)` 调用形态）全部
     原样保留，`react-hooks-hygiene.test.ts` 全绿零改动。
3. **`apps/web/src/lib/voice-trace.test.ts`（15 项 vitest，TDD RED 先行
   后 GREEN）**：载荷构造、七阶段白名单与非法 stage、duration 取整/
   界内边界（1/600000）/越界（0/600001/取整后 0/NaN/Infinity）、可选
   ID 携带/省略/空串、POST 端点与方法与凭据、无上下文请求体两键、
   非法载荷零网络、网络失败与非 2xx 静默 resolve、withVoiceTrace 成功
   透传+上报、失败原错误引用透传+零上报、上报通道故障不影响业务结果。

## 不做的事（诚实边界）

- **不虚报 vad/llm/first_audio**：这三阶段发生在 LiveKit/服务端语音链
  与上游模型（M14-245 矩阵缺口 3——Web UI 与服务端语音链未合并），
  浏览器 voice-studio 流程测不到，本切片不为其提供任何调用面。
- **M4-09 维持 🟡 partial**：本切片不声称完整 LiveKit/ASR/TTS 会话
  覆盖；M14-245 矩阵 M4-09 行不改判、缺口 3/4 注记保留（缺口 4 的
  「apps/web 零调用」表述由本切片部分收窄：asr/tts 浏览器边界已接，
  vad/llm/first_audio 仍无 Web 埋点）。
- **未做生产/部署/真实浏览器级验证**：未启停任何容器/服务，未触碰
  DB/Redis/voice/LiveKit/凭据，未改任何 readiness 标志——
  `production_ready=false / release_ready=false / public_ready=false`
  不变。上报链路的服务端行为以既有
  `services/api/tests/test_voice_trace.py` 契约为准（本切片零服务端
  改动，未重跑 pytest）。

## 验证（2026-10-07，worktree 内真实执行）

| 命令 | 结果 |
| --- | --- |
| `npm run test --workspace apps/web` | **12 files / 182 tests passed**（含新增 `voice-trace.test.ts` 15 项；单跑该文件 15 passed，RED 阶段先实证 `Cannot find module './voice-trace'` 失败） |
| `npm run typecheck` | exit 0，0 error |
| `npm run lint` | exit 0，0 error / 0 warning |
| `npm run build --workspace apps/web` | ✓ Compiled successfully，12 路由（`/voice/[paperId]` 等静态/动态划分与基点一致） |
| `git diff --check` | 干净（无 whitespace 错误） |

新增/改动行扫描（`git diff` + 新文件全量，共 361 行）：U+FFFD 0 命中、
绝对本地路径（盘符/`/home/`/`C:/Users`）0 命中、secret 形态
（api_key/secret/password/token/bearer/AKIA 前缀）0 命中。

依赖安装：worktree 首次 `npm install`（registry=npmmirror，本地镜像，
未走代理；`package-lock.json` 锁定，411 packages，unrs-resolver
postinstall 按仓库 allowScripts 策略被阻——不影响本切片任何验证）。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-247（M14-246 转前一任务
  快照），里程碑段补注 M4-09 仍 partial（计数不变 implemented 60 /
  partial 3）。
- `docs/ROADMAP.md`：M4 段 M4-09 缺口注记改为「浏览器 asr/tts 两边界
  已埋（m14-247），vad/llm/first_audio 仍缺」。
- `docs/CHANGELOG.md`：新增 M14-247 条目。
