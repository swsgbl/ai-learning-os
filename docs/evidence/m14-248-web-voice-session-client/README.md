# M14-248 证据：Web authoritative VoiceSession API contract / adapter

- 分支 `m14-248-web-voice-session-client`（worktree
  `ai-learning-os-worktrees/m14-248-web-voice-session-client`），基于
  current main `9cdffa590c9b34be99b496bd59b5e3255f392d47`（PR #334
  merge，即 M14-247）。执行前 porcelain 干净（基点本地 git 可验证）。
- **Supervisor Feedback Round 1 修正**（amend 回原提交，仍恰一个
  commit）：(1) 全部 session_id 动态路径经统一 `voiceSessionPath`
  helper（encodeURIComponent 单 segment——危险字符不拆 route/不注入
  query/fragment）；(2) `newVoiceAnswerEventId` 改为运行时安全降级链
  （randomUUID → getRandomValues → Math.random，非安全上下文/SSR 不
  崩溃）。+4 项 focused 契约测试（26→30）。
- 动因：M14-245 能力真相矩阵缺口 3（Web UI 与服务端语音链未合并）
  的前置件——服务端 VoiceSession 契约（M4-03/04/05/07/08：
  sessions/commands/intents/answers/resume/report，services/api
  `app/api/routes/voice.py`）真实存在且 Android 已接（M12-03
  `VoiceDtos.kt`/`VoiceRepository.kt`），但 `apps/web` 零客户端契约面。
  本切片为 Web 补齐**契约层**（types + API 方法 + 契约测试），
  不接入、不替换现有 voice-studio UI 流程。

## 交付面（apps/web only，零服务端/生产变更）

1. **`apps/web/src/lib/types.ts`（新增 VoiceSession 契约类型段）**
   - 与服务端 Pydantic snake_case 一一对应的 contract types：
     `VoiceSession`（VoiceSessionOut 七字段）、`VoiceSessions`、
     `VoiceSessionCreateRequest`、`VoiceSessionResume`（含
     `VoiceResumeQuestion`——当前题公开字段投影，复用既有
     `QuestionOption`）、`VoiceSessionCommandRequest/Result`、
     `VoiceSessionIntentRequest/Result`、
     `VoiceSessionAnswerRequest/Result`、`VoiceSessionReport`（含
     `VoiceMistakeSummary`/`VoiceRemediationSummary`，字段以
     `app/domain/voice_report.py` 实际投影为准）。
   - `status: string` 保留服务端原词（8 状态 FSM）——**客户端不定义
     状态集合、不推演转移**，未知状态按 string 原样接收（契约测试用
     非白名单原词 `SOME_FUTURE_STATE` 实证透传）；FSM 校验/答案/
     判分一律以服务端为唯一真相源。
   - 请求可空字段与服务端 `str | None = None` 一一对应（显式传
     null = 缺省语义）；响应可空字段统一 `?: T | null`。
2. **`apps/web/src/lib/api.ts`（新增 `api.voiceSessions` 八方法 +
   event_id helper）**
   - `create`→POST `/api/v1/voice/sessions`（body 恰 `{exam_id}`）；
     `list`→GET `/api/v1/voice/sessions?exam_id=`（必填 query，值经
     encodeURIComponent 编码）；`get`→GET `/sessions/{id}`；
     `resume`→GET `/sessions/{id}/resume`；`command`→POST
     `/sessions/{id}/commands`；`intent`→POST `/sessions/{id}/intents`；
     `answer`→POST `/sessions/{id}/answers`；`report`→GET
     `/sessions/{id}/report`。路径/method/payload 与 voice.py 逐端点
     一致，**不增服务端不存在的字段**（最小载荷键集有专项断言）。
   - **R1：六个动态路径（get/resume/command/intent/answer/report）
     统一经 `voiceSessionPath(sessionId)` 私有 helper**——
     encodeURIComponent 保证 session_id 恒为单 path segment：含
     `/`、`?`、`#`、空格等危险字符时不会拆错 route、不会注入
     query/fragment（契约测试用 `sess/1?x#y` → `sess%2F1%3Fx%23y`
     六路径逐一实证）。
   - 全部经既有 `request()` 统一 client——credentials=include、
     Content-Type=application/json、cache=no-store、ApiError（含
     401 登录跳转）语义继承，零新传输层。
   - `newVoiceAnswerEventId()`：answers 幂等键 helper——**R1 运行时
     安全降级链**：优先 `globalThis.crypto?.randomUUID`（安全上下文）
     → `getRandomValues` 逐字节 + 版本/变体位置位（非安全上下文
     LAN/公网 HTTP——`randomUUID` 为 secure-context-only）→
     `Math.random` fallback（整个 WebCrypto 缺席），恒产出合法 36
     字符 UUID v4（落在服务端 `Field(min_length=4, max_length=64)`
     界内），SSR/非安全上下文不崩溃；与 Android 端
     `UUID.randomUUID().toString()` 同策略。
     **event_id 由调用方传入**：`answer` 的 payload 类型必填
     `event_id`，API 层绝不隐式生成（隐式生成会让重试换键、破坏
     服务端幂等去重）；一次逻辑事件生成一次，重试复用同一值——
     契约测试实证「500 失败后同 payload 重试，两次请求 body 的
     event_id 逐字节不变」（降级链环境下同样实证）。
3. **`apps/web/src/lib/voice-session-api.test.ts`（30 项 vitest 契约
   测试，TDD RED 先行——首轮 26/26 失败实证 `api.voiceSessions`
   缺失；R1 追加 4 项先 4/4 失败实证缺陷后转绿）**：每方法 URL/
   method/body（全字段原样序列化 + 最小载荷键集恰等）、exam_id
   query 编码、八方法统一继承 credentials/headers/cache、响应 JSON
   原样透传（含未知 status 原词、resume/report 投影）、event_id
   helper 格式（UUID v4 正则）/长度（36 ∈ [4,64]）/唯一性（200 次
   全不同）/重试稳定性、载荷 deep equal（零添加键）、非 2xx 经
   ApiError 抛出（status 原样携带）；**R1 追加**：`sess/1?x#y` 六
   动态路径单 segment 编码、getRandomValues-only 环境（mulberry32
   确定性 PRNG 模拟）合法 UUID+200 唯一、完全无 WebCrypto
   （crypto=undefined）fallback 合法 UUID+200 唯一、降级环境下
   失败重试 event_id 逐字节不变。

## 不做的事（诚实边界）

- **不接入、不替换 voice-studio UI**：`voice-studio.tsx` 零改动，
  仍走浏览器原生 SpeechRecognition/speechSynthesis +
  `/papers/{id}/exams`（mode=voice）流程；本切片只是契约层就位，
  Web 端到端「服务端权威语音陪练」接入（矩阵缺口 3 的 UI 合并部分）
  为后续切片。
- **不声明 M4-09 或 capability gap 3 关闭**：M4-09 维持 🟡 partial
  （vad/llm/first_audio 仍无 Web 埋点）；缺口 3 仅收窄为「Web 契约层
  已备、UI 未接入」。M14-245 矩阵不改判，计数不变 implemented 60 /
  partial 3。
- **零服务端/生产变更**：不修改任何 API 行为（服务端语义以既有
  `test_voice_session_fsm.py`/`test_voice_resume.py`/
  `test_answer_normalizer.py`/`test_voice_report.py` 等契约为准，
  未重跑 pytest）；未改 `production_ready`/`release_ready`/
  `public_ready`（均 false 不变）；未启停 Docker/WSL/Ollama/ASR/
  TTS/生产容器或任何服务，未触碰 DB/Redis/LiveKit/凭据。
- **不虚构 UI 行为测试**：契约测试只锚定 HTTP 契约（URL/method/
  body/headers/透传），不测、不mock任何 UI 组件行为。

## 验证（2026-10-07，worktree 内真实执行）

| 命令 | 结果 |
| --- | --- |
| `npm run test --workspace apps/web -- src/lib/voice-session-api.test.ts` | **1 file / 30 tests passed**（首轮 RED 26/26 失败：`Cannot read properties of undefined (reading 'create')`；R1 追加 4 项先 4/4 失败实证路径未编码/旧 helper 依赖 randomUUID，修正后转绿） |
| `npm run test --workspace apps/web` | **13 files / 212 tests passed**（含既有 182 项全绿，零回归） |
| `npm run typecheck` | exit 0，0 error（首轮曾报 `normalized_answer: null` 不兼容——修正为与服务端 `str \| None` 一一对应后通过；R1 后复跑 0 error） |
| `npm run lint` | exit 0，0 error / 0 warning |
| `npm run build --workspace apps/web` | ✓ Compiled successfully，12 路由（静态/动态划分与基点一致） |
| `git diff --check` | 干净（无 whitespace 错误） |

新增/改动行卫生扫描（初版 201 diff insertions + 388 行测试；R1 增量
158 diff insertions，测试文件终量 494 行；secret 形态（token/secret/
password/api_key/credential/bearer）**0 命中**、绝对本地路径（盘符/
`/home/`）0 命中、U+FFFD 0 命中）。

依赖安装：worktree 首次 `npm install`（registry=npmmirror 本地镜像，
未走代理；411 packages，`package-lock.json` 锁定；unrs-resolver
postinstall 按仓库 allowScripts 策略被阻——与 m14-247 相同，不影响本
切片任何验证）。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-248（M14-247 转前一
  任务快照），里程碑段补注 Web 契约层已备、UI 未接入（计数不变）。
- `docs/ROADMAP.md`：M4 段缺口 3 注记收窄（Web VoiceSession 契约层
  已备[m14-248]，UI 仍未接入服务端语音链）；M14 段新增 M14-248
  状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-248 条目。
