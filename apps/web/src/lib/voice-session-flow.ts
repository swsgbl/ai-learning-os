// M14-249：Web voice-studio 的服务端权威 VoiceSession 编排层
// （M14-248 契约层的第一个消费者）。浏览器 SpeechRecognition /
// speechSynthesis 只是输入输出设备——状态（FSM status）、当前题公开
// 字段、已提交答案、规范化结果、澄清文案、语音报告全部以服务端
// resume/commands/answers/report 的返回为唯一权威；本模块不复制 FSM
// 转移表、不推演新状态、不猜答案。模块内对 status 字符串的分支只用于
// 决定「还欠哪些读题回执事件 / 选哪个推进命令」，目标状态一律取自
// 服务端响应（非法迁移由服务端 409 拒绝，调用方 resume 重新对齐）。
import { ApiError, api, newVoiceAnswerEventId } from "./api";
import { listenOnce, speakUtterance } from "./browser-speech";
import { optionsTailText, questionHeadText } from "./parse-answer";
import { withVoiceTrace, type VoiceTraceContext } from "./voice-trace";
import type {
  ExamSession,
  Submission,
  VoiceResumeQuestion,
  VoiceSession,
  VoiceSessionAnswerRequest,
  VoiceSessionAnswerResult,
  VoiceSessionReport,
  VoiceSessionResume,
} from "./types";

/** 服务端权威视图：resume 投影直存（snake_case 契约字段原样保留，
 *  视图字段为本模块的客户端投影名——值全部来自服务端返回）。 */
export type VoiceFlowView = {
  session: VoiceSession;
  question: VoiceResumeQuestion | null;
  committedAnswer: string | null;
  questionTotal: number;
};

export function viewFromResume(out: VoiceSessionResume): VoiceFlowView {
  return {
    session: out.session,
    question: out.question,
    committedAnswer: out.committed_answer,
    questionTotal: out.question_total,
  };
}

/** resume = 权威对齐面：服务端状态 + 当前题公开内容 + 已提交答案
 *  （exam answer_events 权威值）。终态会话 409（无恢复视图）。 */
export async function resumeVoiceSession(sessionId: string): Promise<VoiceFlowView> {
  return viewFromResume(await api.voiceSessions.resume(sessionId));
}

/** 启动顺序（验收 1）：startExam 成功 → 创建权威 VoiceSession →
 *  resume 对齐。startExam 失败绝不创建 VoiceSession；create/resume
 *  404/失败原样抛出（调用方如实提示不可用，不伪造可用状态）。 */
export async function startVoiceSession(paperId: string): Promise<{
  exam: ExamSession;
  view: VoiceFlowView;
}> {
  const exam = await api.startExam(paperId, "voice");
  const created = await api.voiceSessions.create(exam.exam_id);
  return { exam, view: await resumeVoiceSession(created.session_id) };
}

/** 读题回执事件：某段朗读「真实完成」的回执（question_read = 题面
 *  读完，options_read = 选项读完）。还欠哪个回执由服务端已记录的
 *  status 决定——不是客户端推演，而是「服务端尚未确认完成的事」。 */
export const QUESTION_READ_EVENT = "question_read";
export const OPTIONS_READ_EVENT = "options_read";

/** 朗读编排（验收 3）：题面、选项分两段朗读，各段真实完成（speakPhase
 *  resolve）后才发对应读题事件；朗读失败/中断（reject）→ 对应事件
 *  不发、错误原样抛出，绝不谎报完成。当前 status 已确认完成的部分
 *  只做本地重放（零事件）。返回最后一次服务端回传的 session（纯重放
 *  时无事件无响应，返回 null——调用方不强推状态）。 */
export async function speakQuestionWithEvents(options: {
  sessionId: string;
  status: string;
  question: VoiceResumeQuestion;
  index: number;
  total: number;
  speakPhase: (text: string) => Promise<void>;
}): Promise<VoiceSession | null> {
  const { sessionId, question, index, total, speakPhase } = options;
  let session: VoiceSession | null = null;
  let oweQuestionRead = false;
  let oweOptionsRead = false;

  if (options.status === "SESSION_READY" || options.status === "NEXT_QUESTION") {
    session = (await api.voiceSessions.command(sessionId, { type: "start_reading" })).session;
    oweQuestionRead = true;
    oweOptionsRead = true;
  } else if (options.status === "READING_QUESTION") {
    oweQuestionRead = true;
    oweOptionsRead = true;
  } else if (options.status === "READING_OPTIONS") {
    oweOptionsRead = true;
  }

  await speakPhase(questionHeadText(question, index, total));
  if (oweQuestionRead) {
    session = (await api.voiceSessions.command(sessionId, { type: QUESTION_READ_EVENT })).session;
  }
  await speakPhase(optionsTailText(question));
  if (oweOptionsRead) {
    session = (await api.voiceSessions.command(sessionId, { type: OPTIONS_READ_EVENT })).session;
  }
  return session;
}

// 网络层重试边界：仅 5xx/网络异常（TypeError）重试，同一 event_id 重发
// （服务端幂等去重，不重复落库）；4xx（含 409/404/422）不重试——业务
// 冲突交调用方 resume 对齐/如实提示。共尝试至多 3 次，间隔 150ms。
const ANSWER_ATTEMPT_LIMIT = 3;
const ANSWER_RETRY_DELAY_MS = 150;

function isRetryableTransportError(cause: unknown): boolean {
  if (cause instanceof TypeError) return true; // fetch 网络层失败
  return cause instanceof ApiError && cause.status >= 500;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

/** 答案提交（验收 4/5）：语音与文字候选答案统一走
 *  api.voiceSessions.answer——transcript 原文上送，解析/规范化/判定
 *  全部由服务端执行（accepted/normalized_answer/clarified_question 按
 *  返回渲染）。event_id 在此生成：一次逻辑答案恰一个（网络重试复用
 *  同一键，服务端凭它幂等去重）；调用方发起新逻辑答案 = 新调用 =
 *  新键。API 层不隐式生成（M14-248 契约语义保持）。 */
export async function submitAnswerForSession(options: {
  sessionId: string;
  transcript: string;
  questionId?: string | null;
}): Promise<VoiceSessionAnswerResult> {
  const payload: VoiceSessionAnswerRequest = {
    transcript: options.transcript,
    event_id: newVoiceAnswerEventId(),
  };
  if (options.questionId != null) payload.question_id = options.questionId;
  let lastError: unknown;
  for (let attempt = 1; attempt <= ANSWER_ATTEMPT_LIMIT; attempt += 1) {
    try {
      return await api.voiceSessions.answer(options.sessionId, payload);
    } catch (cause) {
      lastError = cause;
      if (attempt === ANSWER_ATTEMPT_LIMIT || !isRetryableTransportError(cause)) throw cause;
      await delay(ANSWER_RETRY_DELAY_MS);
    }
  }
  throw lastError;
}

export type VoiceReconcileResult =
  | { kind: "active"; view: VoiceFlowView }
  | { kind: "terminal"; session: VoiceSession };

/** 409 后重新对齐（验收 6）：resume 为权威对齐面；终态会话 resume 409
 *  （「会话已结束，无可恢复状态」）→ 改取 session 本体按终态处理。
 *  404 原样抛出（调用方如实提示不可用，不伪造状态）。 */
export async function reconcileSession(sessionId: string): Promise<VoiceReconcileResult> {
  try {
    return { kind: "active", view: await resumeVoiceSession(sessionId) };
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 409) {
      return { kind: "terminal", session: await api.voiceSessions.get(sessionId) };
    }
    throw cause;
  }
}

export type VoiceFinishOutcome = {
  submission: Submission;
  report: VoiceSessionReport;
};

/** 全卷完成（REPORT_READY）后的收尾（验收 7）：先提交书面考试（判分
 *  由服务端执行，幂等重复提交同结果），再取权威语音报告（只投影不
 *  判定）。任一步 409/失败如实抛出——绝不以客户端自行评分替代。 */
export async function finishVoiceExam(examId: string, sessionId: string): Promise<VoiceFinishOutcome> {
  const submission = await api.submitExam(examId);
  const report = await api.voiceSessions.report(sessionId);
  return { submission, report };
}

export type VoiceAdvanceResult =
  | { kind: "question"; view: VoiceFlowView }
  | { kind: "finished"; session: VoiceSession; outcome: VoiceFinishOutcome };

/** 推进（下一题/全卷完成）：推进命令以服务端 status 为准——
 *  ANSWER_COMMITTED → commit_confirmed（确认所答）；WAITING_ANSWER /
 *  CLARIFYING → skip（未作答跳过）；其余状态不先发命令。随后还有下题
 *  （判定只用服务端权威 question_index/question_total，与服务端
 *  start_reading/report_ready 的边界同源）→ start_reading + resume
 *  取新题投影；无下题 → report_ready + submitExam + 权威语音报告。
 *  非法迁移由服务端 409 拒绝（调用方 resume 对齐）。 */
export async function advanceFlow(options: {
  examId: string;
  sessionId: string;
  session: VoiceSession;
  questionTotal: number;
}): Promise<VoiceAdvanceResult> {
  const { examId, sessionId, questionTotal } = options;
  let session = options.session;
  if (session.status === "ANSWER_COMMITTED") {
    session = (await api.voiceSessions.command(sessionId, { type: "commit_confirmed" })).session;
  } else if (session.status === "WAITING_ANSWER" || session.status === "CLARIFYING") {
    session = (await api.voiceSessions.command(sessionId, { type: "skip" })).session;
  }
  if (session.question_index + 1 < questionTotal) {
    await api.voiceSessions.command(sessionId, { type: "start_reading" });
    return { kind: "question", view: await resumeVoiceSession(sessionId) };
  }
  const finished = await api.voiceSessions.command(sessionId, { type: "report_ready" });
  const outcome = await finishVoiceExam(examId, sessionId);
  return { kind: "finished", session: finished.session, outcome };
}

/** 404 = 服务端语音会话当前不可用——如实文案，不伪造可用状态。 */
export function voiceUnavailableText(cause: ApiError): string {
  return `服务端语音会话不可用（${cause.message}）`;
}

/** 启动失败文案（如实）：create/resume 404 走不可用文案；其他错误
 *  原样透出服务端 detail（含 startExam 失败）。 */
export function describeVoiceStartError(cause: unknown): string {
  if (cause instanceof ApiError && cause.status === 404) return voiceUnavailableText(cause);
  return cause instanceof Error ? cause.message : "无法开始语音练习";
}

// M14-247→M14-249（验收 8）：asr/tts 时延 span 携带真实 voice
// session_id（由 api.voiceSessions.create 回传；无会话上下文时调用方
// 不伪造——session_id 缺省即省略）。trace 尽力而为，不影响主流程。
export function tracedSpeak(
  text: string,
  context: VoiceTraceContext,
  speakImpl: (t: string) => Promise<void> = speakUtterance,
): Promise<void> {
  return withVoiceTrace("tts", () => speakImpl(text), context);
}

export function tracedListen(
  context: VoiceTraceContext,
  listenImpl: () => Promise<string> = listenOnce,
): Promise<string> {
  return withVoiceTrace("asr", () => listenImpl(), context);
}
