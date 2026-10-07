# M14-249 证据：Web voice-studio 接入权威 VoiceSession

- 分支 `web/m14-249-voice-session-ui`（worktree
  `ai-learning-os-worktrees/m14-249-web-voice-session-integration`），基于
  current main `25bebb6c0c0289929c13f11beaba55ac1f0e7872`（PR #335
  merge，即 M14-248）。执行前 porcelain 干净（基点本地 git 可验证）。
- 动因：M14-248 已备 Web 侧服务端 VoiceSession 契约层（types +
  `api.voiceSessions` 八方法 + 30 项契约测试），但 voice-studio 仍走
  本地自主流程（`/papers/{id}/exams` + `api.saveAnswer` + 本地序号/
  答案 record + 客户端 `parseSpokenAnswer` 猜答案）。本切片把
  voice-studio 改为消费服务端权威 VoiceSession——浏览器
  SpeechRecognition/speechSynthesis 降格为纯输入输出设备。

## 权威状态原则（本切片的核心边界）

- **服务端是唯一真相源**：会话状态（FSM status）、当前题公开字段
  （resume 投影，不含 answer/explanation）、已提交答案（exam
  answer_events 权威值）、规范化结果（normalized_answer）、澄清文案
  （clarified_question）、语音报告（report 投影）全部以服务端
  resume/commands/answers/report 的返回为准；本地 answers record、
  本地序号、客户端答案解析全部移除。
- **客户端不复制 FSM 转移表、不推演新状态**：编排层
  （`voice-session-flow.ts`）对 status 字符串的分支只用于两件事——
  决定「还欠哪些读题回执事件」（SESSION_READY/NEXT_QUESTION → 需
  start_reading；READING_QUESTION → 欠 question_read+options_read；
  READING_OPTIONS → 只欠 options_read；其余纯重放零事件）与「选哪个
  推进命令」（ANSWER_COMMITTED → commit_confirmed；
  WAITING_ANSWER/CLARIFYING → skip）。目标状态一律取自服务端响应，
  非法迁移由服务端 409 拒绝后 resume 重新对齐。还有下题/全卷完成的
  判定只用服务端权威 `question_index`/`question_total`（与服务端
  start_reading/report_ready 的边界判定同源）。
- **不猜答案**：语音 transcript 原文上送 `/answers` 由服务端
  intent parser（M4-04）+ answer normalizer（M4-05）解析与规范化；
  accepted=false 进入澄清路径（服务端 clarified_question 原样展示）。
  客户端 `parseSpokenAnswer`（含 KEY_WORDS 猜测表）从仓库删除。
- **不谎报朗读完成**：朗读拆为题面/选项两段，各段 speechSynthesis
  真实完成（resolve）后才发对应 question_read/options_read；朗读
  失败/中断 → 对应事件不发、错误如实提示（行为测试实证时序）。
- **404 如实失败**：create/resume 404 → 「服务端语音会话不可用
  （detail）」如实提示，不伪造可用状态。
- **不自行评分**：REPORT_READY 后先 `api.submitExam`（判分在服务端，
  幂等）再 `api.voiceSessions.report`（只投影不判定）；报告数据进入
  组件 state/渲染视图（`data-testid="voice-report"` 卡片），并保留
  既有跳转 `/review/{examId}` 体验。

## 交付面（apps/web only，零服务端/生产变更）

1. **`apps/web/src/lib/voice-session-flow.ts`（新增，编排层）**：
   `startVoiceSession`（startExam 成功 → create → resume 顺序，
   startExam 失败绝不创建 VoiceSession）、`resumeVoiceSession`/
   `viewFromResume`（权威视图投影）、`speakQuestionWithEvents`（分段
   朗读 + 读题回执事件时序）、`submitAnswerForSession`（/answers 权威
   提交；event_id 一次逻辑答案恰生成一次，5xx/网络错误同键重试至多
   3 次、4xx 不重试）、`reconcileSession`（409 后 resume 对齐；终态
   resume 409 → get 本体分流收尾）、`advanceFlow`（推进命令选择 +
   index/total 判定 + 收尾链）、`finishVoiceExam`（submitExam →
   report 顺序）、`voiceUnavailableText`/`describeVoiceStartError`
   （404 如实文案）、`tracedSpeak`/`tracedListen`（asr/tts 埋点包装，
   携带真实 session_id）。
2. **`apps/web/src/lib/browser-speech.ts`（新增）**：自 voice-studio
   抽出的浏览器 IO 薄封装（`speakUtterance`/`listenOnce`），可注入
   替换（测试注入 fake），零解析零判定。
3. **`apps/web/src/components/voice-studio.tsx`（重写接入）**：
   权威视图 `VoiceFlowView`（session/question/committedAnswer/
   questionTotal 全来自服务端）驱动渲染——进度与状态徽标用服务端
   `question_index`/`question_total`/`status`，选项选中态用
   `committedAnswer`（服务端已提交答案），澄清文案 `role="status"`
   展示服务端 clarified_question；语音/文字/选项点击统一走
   `submitTranscript` → `/answers`（选项点击转成服务端可靠解析的
   transcript：mcq「选 {key}」、判断题选项文本）；409 → resume 重新
   对齐（提示后按服务端状态继续）、404 → 如实不可用；REPORT_READY
   收尾后 `completeExam`（报告入 state + TTS 取消 + 跳转 review）。
   M14-189 约束保持：唯一 effect 为启动链（setState 全在 promise
   回调），朗读只在启动回调/切题回调/重复读题按钮发起，`started`
   ref 防重复开考。
4. **`apps/web/src/lib/parse-answer.ts`（收窄）**：删除
   `parseSpokenAnswer` + KEY_WORDS（客户端猜答案）与整段合并的
   `speakableQuestion`；新增 `questionHeadText`/`optionsTailText`
   （两段朗读文案，语义与原 speakableQuestion 拆分等价）与
   `optionClickTranscript`（选项点击 transcript 形态）；`optionLabel`/
   `answerLabel` 原样保留（review-view 在用）。
5. **`apps/web/src/lib/voice-trace.ts`（注释更新）**：M14-247 的
   「voice-studio 流程无 VoiceSession，session_id 恒缺省」注释已
   过时——M14-249 起携带 create 回传的真实 session_id（行为不变，
   仅注释与调用方更新）。
6. **测试**：新增 `voice-session-flow.test.ts` 27 项 vitest 行为
   测试（见下）；`react-hooks-hygiene.test.ts` voice-studio 段按新
   流程更新为等价约束（唯一 effect 启动链/effect 体内零同步状态
   更新/首题朗读在启动回调/切题朗读在 advanceFrom 且完成分支不朗读/
   重复读题按钮直调/speaking 生命周期与 TTS 取消/started 守卫），
   其余 10 文件钉住项零改动；`voice-session-api.test.ts` 零改动
   （30 项全绿——契约层未被本切片触碰）。

## focused 测试覆盖（27 项，全部行为级——stub 全局 fetch 实证
URL/method/body/顺序/时序，无源码字符串断言）

- create→resume 启动顺序（三请求按序 + startExam 失败零 sessions
  POST）；create/resume 404 如实失败（ApiError + 不可用文案）；非
  404 启动失败原样透出 detail。
- TTS 完成后才发读题事件：SESSION_READY 全序（start_reading 先行 →
  题面 resolve 后 question_read → 选项 resolve 后 options_read，
  deferred 门控逐步实证）；题面失败零读题事件；选项失败只欠
  options_read 不发；READING_OPTIONS 重试只补 options_read；
  等待态纯重放零命令返回 null。
- /answers 权威提交：5xx×2 后同 event_id 重试成功（三次 URL 恒
  /answers、body 恒等、键合法 UUID v4）；新逻辑答案新键；4xx 不重试
  （409 一次即抛）；澄清路径 accepted=false 无规范化答案、
  clarified_question 原样透传。
- 409 后 resume 对齐：active 视图回读；终态 resume 409 → get 本体 →
  terminal；404 原样抛出。
- REPORT_READY 收尾：finishVoiceExam 先 submitExam 再 report（顺序
  实证）；advanceFlow 三形态（commit_confirmed→下一题、skip→下一题、
  最后一题 commit_confirmed→report_ready→submitExam+report）；推进
  409 原样抛出。
- trace 携带真实 session_id：asr/tts span POST /api/v1/voice/trace
  body 含 session_id/exam_id/question_id；失败调用零上报。
- optionClickTranscript 形态（mcq「选 {key}」/判断题选项文本——
  与服务端 parser/normalizer 契约对齐）。

## 验证（2026-10-08，worktree 内真实执行）

| 命令 | 结果 |
| --- | --- |
| `npm run test --workspace apps/web -- src/lib/voice-session-flow.test.ts` | **1 file / 27 tests passed**（新行为测试；与实现同步编写，行为级断言非源码串） |
| `npm run test --workspace apps/web -- src/lib/voice-session-api.test.ts` | **1 file / 30 tests passed**（M14-248 契约层零改动，全绿） |
| `npm run test --workspace apps/web -- src/lib/react-hooks-hygiene.test.ts` | **1 file / 38 tests passed**（voice-studio 段更新为等价约束，37→38） |
| `npm run test --workspace apps/web` | **14 files / 240 tests passed**（M14-248 基点 13 files/212 → +1 文件 +27 流程测试 +1 hygiene 项，零回归） |
| `npm run typecheck` | exit 0，0 error |
| `npm run lint` | exit 0，0 error / 0 warning（react-hooks/set-state-in-effect、react-hooks/refs 均无告警） |
| `npm run build --workspace apps/web` | ✓ Compiled successfully，13 路由条目（本切片零 app/ 路由改动，静态/动态划分与基点一致） |
| `git diff --check` | 干净（无 whitespace 错误） |

新增/改动行卫生扫描（提交前全部 staged 新增行——代码 diff `+` 行 +
新文件全量 + 三份 ledger 与本证据文档，共 1658 行）：secret 形态
（api_key/secret/password/token/bearer/credential 赋值形态）
**0 命中**、Windows 盘符/绝对本地路径 0 命中、U+FFFD 0 命中、
`production_ready`/`release_ready`/`public_ready` 置 true 的过度
声明 0 命中（宽松扫描的唯一命中为本行自指的说明文字）。

依赖安装：worktree 首次 `npm install`（registry=npmmirror 本地镜像，
未走代理；与 m14-247/248 相同的 unrs-resolver postinstall 按
allowScripts 策略被阻，不影响本切片任何验证）。

## 不做的事（诚实边界）

- **不声明 M4-09 或 capability gap 3 关闭**：M14-245 矩阵缺口 3
  （Web 语音 UI 与服务端语音链未合并）仅进一步收窄——权威状态/
  答案/报告/读题时序已走服务端 VoiceSession，但 **IO 设备仍是浏览器
  原生 SpeechRecognition/speechSynthesis**，服务端 ASR/TTS
  （/transcribe /synthesize、LiveKit 链路）仍未进入 Web UI；M4-09
  维持 🟡 partial（vad/llm/first_audio 仍无 Web 可观测边界），矩阵
  计数不变 implemented 60 / partial 3。
- 播报期交互按服务端边界收窄：READING_* 期间提交答案/跳过会被服务端
  409 拒绝（打断不提交半成品，M4-06 语义）——客户端如实提示并 resume
  对齐；barge_in/pause/resume 命令端点存在但本切片 UI 未提供入口
  （后续切片空间）。
- 不做真实浏览器/真实服务端端到端验证（无 DOM 测试依赖，与仓库测试
  口径一致）；服务端行为零改动（未重跑 pytest，语义以既有
  `test_voice_session_fsm.py`/`test_voice_resume.py`/
  `test_answer_normalizer.py`/`test_voice_report.py` 契约为准）。
- 未启停 Docker/WSL/Ollama/ASR/TTS/生产容器或任何服务，未触碰
  DB/Redis/LiveKit/MinIO/凭据，未改 readiness/生产配置；
  `production_ready`/`release_ready`/`public_ready` 均 false 不变。
- report 数据进入 state/视图模型后随即跳转 review（既有体验保留），
  语音报告的完整分层播报（错题逐条朗读等）为后续切片空间。

## 文档同步

- `docs/PROJECT_STATUS.md`：当前任务换为 M14-249（M14-248 转前一
  任务快照），里程碑段追加 M14-249 收窄注记（计数不变）。
- `docs/ROADMAP.md`：M4 段缺口 3 注记更新（UI 已接入权威会话、IO 仍
  浏览器原生）；M14 段新增 M14-249 状态更新小节。
- `docs/CHANGELOG.md`：新增 M14-249 条目。
