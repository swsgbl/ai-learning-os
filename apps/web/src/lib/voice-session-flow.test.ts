// M14-249 TDD：Web voice-studio 服务端权威 VoiceSession 编排层
// （voice-session-flow.ts）的行为测试。只锚定真实行为——通过 stub 全局
// fetch 实证 HTTP 请求的 URL/method/body/顺序与时序（create→resume 启动
// 顺序、TTS 完成后才发读题事件、answers 幂等键重试稳定、409 后 resume
// 对齐、REPORT_READY 后 submitExam+report、M14-251 服务端 ASR 听写链
// （录音→WAV→/transcribe）零 /voice/trace、404 如实失败），不做源码
// 字符串断言。契约真值以 services/api app/api/routes/voice.py +
// voice_session_fsm.py 为准：客户端不推演 FSM，所有期望的目标状态都
// 来自 stub 的服务端响应。
import { afterEach, describe, expect, it, vi } from "vitest";

import { API_BASE, ApiError } from "./api";
import { optionClickTranscript } from "./parse-answer";
import {
  advanceFlow,
  describeVoiceStartError,
  finishVoiceExam,
  recordTranscriptViaServerAsr,
  reconcileSession,
  speakQuestionWithEvents,
  startVoiceSession,
  submitAnswerForSession,
} from "./voice-session-flow";
import type { ExamSession, VoiceResumeQuestion, VoiceSession, VoiceSessionResume } from "./types";

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const EXAM: ExamSession = {
  exam_id: "exam-1",
  paper_id: "paper-1",
  paper_title: "微积分检查",
  mode: "voice",
  status: "active",
  server_started_at: "2026-10-08T00:00:00Z",
  server_end_at: "2026-10-08T00:30:00Z",
  server_remaining_seconds: 1800,
  questions: [],
  answers: {},
  next_sequence: 1,
};

function sessionFixture(overrides: Partial<VoiceSession> = {}): VoiceSession {
  return {
    session_id: "sess-1",
    exam_id: "exam-1",
    status: "SESSION_READY",
    question_index: 0,
    revision: 1,
    created_at: "2026-10-08T00:00:00Z",
    updated_at: "2026-10-08T00:00:00Z",
    ...overrides,
  };
}

const QUESTION: VoiceResumeQuestion = {
  id: "q-1",
  type: "mcq",
  stem: "曲线 y=x^2 在点 (2,4) 处的切线斜率是多少？",
  options: [
    { key: "A", text: "2" },
    { key: "B", text: "4" },
  ],
};

const QUESTION_2: VoiceResumeQuestion = {
  id: "q-2",
  type: "tf",
  stem: "可导函数在某点取得极值时，该点导数一定为 0。",
  options: [
    { key: "T", text: "正确" },
    { key: "F", text: "错误" },
  ],
};

function resumeFixture(
  session: VoiceSession,
  question: VoiceResumeQuestion | null,
  committed: string | null,
  total: number,
): VoiceSessionResume {
  return { session, question, committed_answer: committed, question_total: total };
}

function commandOut(
  event: string,
  from: string,
  to: string,
  revision: number,
  index = 0,
): Record<string, unknown> {
  return {
    applied_event: event,
    from_status: from,
    session: sessionFixture({ status: to, revision, question_index: index }),
    clarified_question: null,
    question_total: null,
  };
}

function answerOut(accepted: boolean, status: string, normalized: string | null): Record<string, unknown> {
  return {
    event_id: "stub-event-id",
    idempotent: false,
    accepted,
    normalized_answer: normalized,
    intent: "choose_option",
    question_id: "q-1",
    session: sessionFixture({ status, revision: 2 }),
    clarified_question: accepted ? null : "没有听清具体选项。",
  };
}

const SUBMISSION_FIXTURE = { exam_id: "exam-1", status: "submitted", score: 2, correct_count: 2, total_count: 3 };
const REPORT_FIXTURE = {
  session_id: "sess-1",
  exam_id: "exam-1",
  spoken_text: "考试完成！本次答对 2 题（共 3 题），得分 2 分。",
  mistake_summary: [],
  remediation_summary: [],
  written_report_url: "/api/v1/exams/exam-1/report",
};

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}

type FetchMock = ReturnType<typeof vi.fn>;

function queueFetch(steps: Array<() => Response>): FetchMock {
  let index = 0;
  return vi.fn(() => {
    if (index >= steps.length) throw new Error(`意外的多余请求 #${index + 1}`);
    const respond = steps[index];
    index += 1;
    return Promise.resolve(respond());
  });
}

function urlsOf(mock: FetchMock): string[] {
  return mock.mock.calls.map((call) => String(call[0]));
}

function bodiesOf(mock: FetchMock): Array<Record<string, unknown>> {
  return mock.mock.calls
    .map((call) => (call[1] as RequestInit | undefined)?.body)
    .filter((body): body is string => typeof body === "string")
    .map((body) => JSON.parse(body) as Record<string, unknown>);
}

function commandTypesOf(mock: FetchMock): string[] {
  return bodiesOf(mock)
    .filter((body) => typeof body.type === "string")
    .map((body) => String(body.type));
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void; reject: (cause: unknown) => void } {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- 验收 1：create→resume 启动顺序 + 404 如实失败 ---

describe("startVoiceSession：startExam → create → resume 启动顺序", () => {
  it("三个请求按序发出，resume 投影直存视图（题面/已提交答案/总题数）", async () => {
    const fetch = queueFetch([
      () => jsonResponse(EXAM),
      () => jsonResponse(sessionFixture()),
      () =>
        jsonResponse(
          resumeFixture(
            sessionFixture({ status: "WAITING_ANSWER", revision: 3 }),
            QUESTION,
            "A",
            5,
          ),
        ),
    ]);
    vi.stubGlobal("fetch", fetch);
    const { exam, view } = await startVoiceSession("paper-1");
    expect(urlsOf(fetch)).toEqual([
      `${API_BASE}/api/v1/papers/paper-1/exams`,
      `${API_BASE}/api/v1/voice/sessions`,
      `${API_BASE}/api/v1/voice/sessions/sess-1/resume`,
    ]);
    expect((fetch.mock.calls[0][1] as RequestInit).method).toBe("POST");
    expect(JSON.parse(String((fetch.mock.calls[0][1] as RequestInit).body))).toEqual({ mode: "voice" });
    expect(JSON.parse(String((fetch.mock.calls[1][1] as RequestInit).body))).toEqual({ exam_id: "exam-1" });
    expect(exam.exam_id).toBe("exam-1");
    expect(view).toEqual({
      session: sessionFixture({ status: "WAITING_ANSWER", revision: 3 }),
      question: QUESTION,
      committedAnswer: "A",
      questionTotal: 5,
    });
  });

  it("startExam 失败绝不创建 VoiceSession（仅 1 个请求，零 voice/sessions POST）", async () => {
    const fetch = queueFetch([() => jsonResponse({ detail: "试卷不存在" }, 404)]);
    vi.stubGlobal("fetch", fetch);
    await expect(startVoiceSession("paper-1")).rejects.toMatchObject({ status: 404 });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(urlsOf(fetch)[0]).toContain("/api/v1/papers/");
  });

  it("create 404 如实失败：ApiError 原样抛出 + 启动文案明示服务端语音会话不可用", async () => {
    const fetch = queueFetch([
      () => jsonResponse(EXAM),
      () => jsonResponse({ detail: "语音会话不存在" }, 404),
    ]);
    vi.stubGlobal("fetch", fetch);
    const cause = await startVoiceSession("paper-1").then(
      () => {
        throw new Error("404 应 reject");
      },
      (error: ApiError) => error,
    );
    expect(cause).toBeInstanceOf(ApiError);
    expect(cause.status).toBe(404);
    expect(describeVoiceStartError(cause)).toBe("服务端语音会话不可用（语音会话不存在）");
  });

  it("resume 404 同样如实失败（不伪造可用状态）", async () => {
    const fetch = queueFetch([
      () => jsonResponse(EXAM),
      () => jsonResponse(sessionFixture()),
      () => jsonResponse({ detail: "语音会话不存在" }, 404),
    ]);
    vi.stubGlobal("fetch", fetch);
    const cause = await startVoiceSession("paper-1").then(
      () => {
        throw new Error("404 应 reject");
      },
      (error: ApiError) => error,
    );
    expect(cause.status).toBe(404);
    expect(describeVoiceStartError(cause)).toBe("服务端语音会话不可用（语音会话不存在）");
  });

  it("非 404 启动失败原样透出服务端 detail（不套不可用文案）", async () => {
    const fetch = queueFetch([
      () => jsonResponse(EXAM),
      () => jsonResponse({ detail: "考试状态 submitted 不允许语音作答" }, 409),
    ]);
    vi.stubGlobal("fetch", fetch);
    const cause = await startVoiceSession("paper-1").then(
      () => {
        throw new Error("409 应 reject");
      },
      (error: ApiError) => error,
    );
    expect(describeVoiceStartError(cause)).toBe("考试状态 submitted 不允许语音作答");
  });
});

// --- 验收 3：TTS 完成后才发 question_read/options_read ---

describe("speakQuestionWithEvents：朗读真实完成才发读题事件", () => {
  it("SESSION_READY 全序：start_reading 先行；question_read 在题面朗读 resolve 后才发；options_read 在选项朗读 resolve 后才发", async () => {
    const fetch = queueFetch([
      () => jsonResponse(commandOut("start_reading", "SESSION_READY", "READING_QUESTION", 1)),
      () => jsonResponse(commandOut("question_read", "READING_QUESTION", "READING_OPTIONS", 2)),
      () => jsonResponse(commandOut("options_read", "READING_OPTIONS", "WAITING_ANSWER", 3)),
    ]);
    vi.stubGlobal("fetch", fetch);
    const gateQuestion = deferred<void>();
    const gateOptions = deferred<void>();
    const spoken: string[] = [];
    let call = 0;
    const speakPhase = (text: string) => {
      call += 1;
      spoken.push(text);
      return call === 1 ? gateQuestion.promise : gateOptions.promise;
    };

    const pending = speakQuestionWithEvents({
      sessionId: "sess-1",
      status: "SESSION_READY",
      question: QUESTION,
      index: 0,
      total: 3,
      speakPhase,
    });

    // 题面朗读未完成：只允许 start_reading 已发出，question_read 不发
    await flush();
    expect(urlsOf(fetch)).toEqual([`${API_BASE}/api/v1/voice/sessions/sess-1/commands`]);
    expect(bodiesOf(fetch)[0]).toEqual({ type: "start_reading" });

    // 题面朗读完成 → question_read 发出；选项未读完 → options_read 不发
    gateQuestion.resolve();
    await flush();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(bodiesOf(fetch)[1]).toEqual({ type: "question_read" });

    // 选项朗读完成 → options_read 发出，返回服务端权威 session
    gateOptions.resolve();
    const session = await pending;
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(bodiesOf(fetch)[2]).toEqual({ type: "options_read" });
    expect(session?.status).toBe("WAITING_ANSWER");
    expect(spoken.length).toBe(2);
    expect(spoken[0]).toContain("切线斜率");
    expect(spoken[1]).toContain("选项 A");
  });

  it("题面朗读失败/中断：零读题事件（只保留已真实完成的 start_reading），错误原样抛出", async () => {
    const fetch = queueFetch([
      () => jsonResponse(commandOut("start_reading", "SESSION_READY", "READING_QUESTION", 1)),
    ]);
    vi.stubGlobal("fetch", fetch);
    await expect(
      speakQuestionWithEvents({
        sessionId: "sess-1",
        status: "SESSION_READY",
        question: QUESTION,
        index: 0,
        total: 3,
        speakPhase: () => Promise.reject(new Error("语音朗读失败")),
      }),
    ).rejects.toThrow("语音朗读失败");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(bodiesOf(fetch)[0]).toEqual({ type: "start_reading" });
  });

  it("选项朗读失败：question_read 已发（题面真实完成）、options_read 不发", async () => {
    const fetch = queueFetch([
      () => jsonResponse(commandOut("start_reading", "SESSION_READY", "READING_QUESTION", 1)),
      () => jsonResponse(commandOut("question_read", "READING_QUESTION", "READING_OPTIONS", 2)),
    ]);
    vi.stubGlobal("fetch", fetch);
    let call = 0;
    await expect(
      speakQuestionWithEvents({
        sessionId: "sess-1",
        status: "SESSION_READY",
        question: QUESTION,
        index: 0,
        total: 3,
        speakPhase: () => {
          call += 1;
          return call === 1 ? Promise.resolve() : Promise.reject(new Error("语音朗读失败"));
        },
      }),
    ).rejects.toThrow("语音朗读失败");
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(bodiesOf(fetch).map((body) => body.type)).toEqual(["start_reading", "question_read"]);
  });

  it("READING_OPTIONS 中断重试：只补发 options_read（不重发 start_reading/question_read）", async () => {
    const fetch = queueFetch([
      () => jsonResponse(commandOut("options_read", "READING_OPTIONS", "WAITING_ANSWER", 5)),
    ]);
    vi.stubGlobal("fetch", fetch);
    const spoken: string[] = [];
    const session = await speakQuestionWithEvents({
      sessionId: "sess-1",
      status: "READING_OPTIONS",
      question: QUESTION,
      index: 0,
      total: 3,
      speakPhase: (text) => {
        spoken.push(text);
        return Promise.resolve();
      },
    });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(bodiesOf(fetch)[0]).toEqual({ type: "options_read" });
    expect(spoken.length).toBe(2); // 题面本地重放 + 选项朗读
    expect(session?.status).toBe("WAITING_ANSWER");
  });

  it("等待态重复朗读为纯重放：零命令（不重复发读题事件），返回 null", async () => {
    const fetch = queueFetch([]);
    vi.stubGlobal("fetch", fetch);
    const session = await speakQuestionWithEvents({
      sessionId: "sess-1",
      status: "WAITING_ANSWER",
      question: QUESTION,
      index: 0,
      total: 3,
      speakPhase: () => Promise.resolve(),
    });
    expect(fetch).not.toHaveBeenCalled();
    expect(session).toBeNull();
  });
});

// --- 验收 4/5：语音与文字答案统一走 /answers + event_id 幂等 + 澄清路径 ---

describe("submitAnswerForSession：answers 权威提交与幂等键", () => {
  it("网络 5xx 重试复用同一 event_id（三次请求 URL 恒 /answers、body 恒等），成功后按服务端结果返回", async () => {
    const fetch = queueFetch([
      () => jsonResponse({ detail: "服务暂不可用" }, 500),
      () => jsonResponse({ detail: "服务暂不可用" }, 503),
      () => jsonResponse(answerOut(true, "ANSWER_COMMITTED", "A")),
    ]);
    vi.stubGlobal("fetch", fetch);
    const result = await submitAnswerForSession({
      sessionId: "sess-1",
      transcript: "选 A",
      questionId: "q-1",
    });
    expect(result.accepted).toBe(true);
    expect(result.normalized_answer).toBe("A");
    expect(result.session?.status).toBe("ANSWER_COMMITTED");
    expect(fetch).toHaveBeenCalledTimes(3);
    for (const url of urlsOf(fetch)) {
      expect(url).toBe(`${API_BASE}/api/v1/voice/sessions/sess-1/answers`);
    }
    const bodies = bodiesOf(fetch);
    for (const body of bodies) {
      expect(body).toEqual({ transcript: "选 A", event_id: bodies[0].event_id, question_id: "q-1" });
    }
    expect(bodies[0].event_id).toMatch(UUID_V4);
    expect(new Set(bodies.map((body) => body.event_id)).size).toBe(1); // 重试不改键
  });

  it("新的逻辑答案生成新 event_id（两次调用两把键，均为合法 UUID v4）", async () => {
    const fetch = queueFetch([
      () => jsonResponse(answerOut(true, "ANSWER_COMMITTED", "A")),
      () => jsonResponse(answerOut(true, "ANSWER_COMMITTED", "B")),
    ]);
    vi.stubGlobal("fetch", fetch);
    await submitAnswerForSession({ sessionId: "sess-1", transcript: "选 A" });
    await submitAnswerForSession({ sessionId: "sess-1", transcript: "选 B" });
    const [first, second] = bodiesOf(fetch);
    expect(first.event_id).toMatch(UUID_V4);
    expect(second.event_id).toMatch(UUID_V4);
    expect(first.event_id).not.toBe(second.event_id);
  });

  it("4xx 不重试：409 直接抛出交调用方 resume 对齐（仅一次请求）", async () => {
    const fetch = queueFetch([() => jsonResponse({ detail: "语音会话状态 READING_QUESTION 不接受事件 answer_proposed" }, 409)]);
    vi.stubGlobal("fetch", fetch);
    const cause = await submitAnswerForSession({ sessionId: "sess-1", transcript: "选 A" }).then(
      () => {
        throw new Error("409 应 reject");
      },
      (error: ApiError) => error,
    );
    expect(cause).toBeInstanceOf(ApiError);
    expect(cause.status).toBe(409);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("澄清路径：accepted=false 不含规范化答案，服务端 clarified_question 原样透传（客户端不猜答案）", async () => {
    const fetch = queueFetch([() => jsonResponse(answerOut(false, "CLARIFYING", null))]);
    vi.stubGlobal("fetch", fetch);
    const result = await submitAnswerForSession({ sessionId: "sess-1", transcript: "我选那个" });
    expect(result.accepted).toBe(false);
    expect(result.normalized_answer).toBeNull();
    expect(result.clarified_question).toBe("没有听清具体选项。");
    expect(result.session?.status).toBe("CLARIFYING");
  });
});

// --- 验收 6：409 后 resume 重新对齐 ---

describe("reconcileSession：409 后以 resume 重新对齐", () => {
  it("resume 200 → active 视图（服务端题面/已提交答案/总题数回读）", async () => {
    const fetch = queueFetch([
      () =>
        jsonResponse(
          resumeFixture(sessionFixture({ status: "READING_OPTIONS", revision: 7 }), QUESTION, null, 5),
        ),
    ]);
    vi.stubGlobal("fetch", fetch);
    const result = await reconcileSession("sess-1");
    expect(result).toEqual({
      kind: "active",
      view: {
        session: sessionFixture({ status: "READING_OPTIONS", revision: 7 }),
        question: QUESTION,
        committedAnswer: null,
        questionTotal: 5,
      },
    });
    expect(urlsOf(fetch)).toEqual([`${API_BASE}/api/v1/voice/sessions/sess-1/resume`]);
  });

  it("终态会话 resume 409 → 改取 session 本体 → terminal（供收尾分流）", async () => {
    const fetch = queueFetch([
      () => jsonResponse({ detail: "会话已结束，无可恢复状态" }, 409),
      () => jsonResponse(sessionFixture({ status: "REPORT_READY", revision: 9 })),
    ]);
    vi.stubGlobal("fetch", fetch);
    const result = await reconcileSession("sess-1");
    expect(result).toMatchObject({ kind: "terminal", session: { status: "REPORT_READY" } });
    expect(urlsOf(fetch)).toEqual([
      `${API_BASE}/api/v1/voice/sessions/sess-1/resume`,
      `${API_BASE}/api/v1/voice/sessions/sess-1`,
    ]);
  });

  it("reconcile 遇 404 原样抛出（如实不可用，不伪造状态）", async () => {
    const fetch = queueFetch([() => jsonResponse({ detail: "语音会话不存在" }, 404)]);
    vi.stubGlobal("fetch", fetch);
    await expect(reconcileSession("sess-1")).rejects.toMatchObject({ status: 404 });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

// --- 验收 7：REPORT_READY 后 submitExam + 权威报告 ---

describe("finishVoiceExam / advanceFlow：全卷完成收尾", () => {
  it("先 submitExam 再取权威语音报告（顺序实证；判分/报告全在服务端）", async () => {
    const fetch = queueFetch([
      () => jsonResponse(SUBMISSION_FIXTURE),
      () => jsonResponse(REPORT_FIXTURE),
    ]);
    vi.stubGlobal("fetch", fetch);
    const outcome = await finishVoiceExam("exam-1", "sess-1");
    expect(outcome.submission).toEqual(SUBMISSION_FIXTURE);
    expect(outcome.report).toEqual(REPORT_FIXTURE);
    expect(urlsOf(fetch)).toEqual([
      `${API_BASE}/api/v1/exams/exam-1/submit`,
      `${API_BASE}/api/v1/voice/sessions/sess-1/report`,
    ]);
    expect((fetch.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });

  it("已提交答案推进：commit_confirmed → 还有下题 → start_reading + resume 新题投影", async () => {
    const fetch = queueFetch([
      () => jsonResponse(commandOut("commit_confirmed", "ANSWER_COMMITTED", "NEXT_QUESTION", 2, 0)),
      () => jsonResponse(commandOut("start_reading", "NEXT_QUESTION", "READING_QUESTION", 3, 1)),
      () =>
        jsonResponse(
          resumeFixture(sessionFixture({ status: "READING_QUESTION", question_index: 1, revision: 3 }), QUESTION_2, null, 3),
        ),
    ]);
    vi.stubGlobal("fetch", fetch);
    const outcome = await advanceFlow({
      examId: "exam-1",
      sessionId: "sess-1",
      session: sessionFixture({ status: "ANSWER_COMMITTED" }),
      questionTotal: 3,
    });
    expect(outcome.kind).toBe("question");
    if (outcome.kind === "question") {
      expect(outcome.view.question?.id).toBe("q-2");
      expect(outcome.view.session.question_index).toBe(1);
      expect(outcome.view.questionTotal).toBe(3);
    }
    expect(commandTypesOf(fetch)).toEqual(["commit_confirmed", "start_reading"]);
  });

  it("未作答推进：WAITING_ANSWER → skip（而非 commit_confirmed）", async () => {
    const fetch = queueFetch([
      () => jsonResponse(commandOut("skip", "WAITING_ANSWER", "NEXT_QUESTION", 2, 0)),
      () => jsonResponse(commandOut("start_reading", "NEXT_QUESTION", "READING_QUESTION", 3, 1)),
      () =>
        jsonResponse(
          resumeFixture(sessionFixture({ status: "READING_QUESTION", question_index: 1, revision: 3 }), QUESTION_2, null, 3),
        ),
    ]);
    vi.stubGlobal("fetch", fetch);
    const outcome = await advanceFlow({
      examId: "exam-1",
      sessionId: "sess-1",
      session: sessionFixture({ status: "WAITING_ANSWER" }),
      questionTotal: 3,
    });
    expect(outcome.kind).toBe("question");
    expect(bodiesOf(fetch)[0]).toEqual({ type: "skip" });
  });

  it("最后一题完成：commit_confirmed → report_ready → submitExam + report（kind=finished）", async () => {
    const fetch = queueFetch([
      () => jsonResponse(commandOut("commit_confirmed", "ANSWER_COMMITTED", "NEXT_QUESTION", 2, 2)),
      () => jsonResponse(commandOut("report_ready", "NEXT_QUESTION", "REPORT_READY", 3, 2)),
      () => jsonResponse(SUBMISSION_FIXTURE),
      () => jsonResponse(REPORT_FIXTURE),
    ]);
    vi.stubGlobal("fetch", fetch);
    const outcome = await advanceFlow({
      examId: "exam-1",
      sessionId: "sess-1",
      session: sessionFixture({ status: "ANSWER_COMMITTED", question_index: 2 }),
      questionTotal: 3,
    });
    expect(outcome.kind).toBe("finished");
    if (outcome.kind === "finished") {
      expect(outcome.session.status).toBe("REPORT_READY");
      expect(outcome.outcome.report).toEqual(REPORT_FIXTURE);
      expect(outcome.outcome.submission).toEqual(SUBMISSION_FIXTURE);
    }
    expect(commandTypesOf(fetch)).toEqual(["commit_confirmed", "report_ready"]);
    expect(urlsOf(fetch).slice(2)).toEqual([
      `${API_BASE}/api/v1/exams/exam-1/submit`,
      `${API_BASE}/api/v1/voice/sessions/sess-1/report`,
    ]);
  });

  it("推进遇 409 原样抛出（交调用方 resume 对齐，不本地覆盖 FSM）", async () => {
    const fetch = queueFetch([() => jsonResponse({ detail: "语音会话状态 SESSION_READY 不接受事件 skip" }, 409)]);
    vi.stubGlobal("fetch", fetch);
    const cause = await advanceFlow({
      examId: "exam-1",
      sessionId: "sess-1",
      session: sessionFixture({ status: "SESSION_READY" }),
      questionTotal: 3,
    }).then(
      () => {
        throw new Error("409 应 reject");
      },
      (error: ApiError) => error,
    );
    expect(cause.status).toBe(409);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

// --- M14-251：听写通道 = 录音 → 本地 WAV → 服务端 /transcribe ---
// 浏览器 SpeechRecognition（listenOnce/tracedListen）已移除；asr 时延
// 以服务端 /transcribe 内自动埋点为唯一权威——本链零 /voice/trace
// 请求（M14-250 R1 的 tts 同口径；tracedListen 的客户端 asr span
// 行为由 M14-251 判定废除）。transcript 唯一权威 = 服务端返回的 text。

describe("recordTranscriptViaServerAsr：录音 → WAV → 服务端转写", () => {
  const WAV = new Blob([new Uint8Array([0x52, 0x49, 0x46, 0x46, 1, 2, 3, 4])], {
    type: "audio/wav",
  });

  it("完整链：录得的 WAV 以 multipart 上送 /transcribe，服务端 text 原样返回；零 /voice/trace", async () => {
    const fetch = queueFetch([
      () =>
        new Response(
          JSON.stringify({
            id: 7,
            text: "选 A",
            confidence: 0.9,
            provider: "local-funasr",
            latency_ms: 812,
            audio_bytes: WAV.size,
            audio_stored: false,
            audio_object_key: null,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    ]);
    vi.stubGlobal("fetch", fetch);
    const transcript = await recordTranscriptViaServerAsr({ recordImpl: async () => WAV });
    expect(transcript).toBe("选 A");
    const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE}/api/v1/voice/transcribe`);
    expect(init.method).toBe("POST");
    expect((init.body as FormData).get("audio")).toBeInstanceOf(File);
    await flush(); // 排空任何迟到上报
    expect(urlsOf(fetch)).toEqual([`${API_BASE}/api/v1/voice/transcribe`]); // 零 trace
  });

  it("onRecorded 在录音收尾与上送之间触发（UI 观察 recording → transcribing 转换）", async () => {
    const order: string[] = [];
    const fetch = queueFetch([
      () =>
        new Response(JSON.stringify({ text: "选 B" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    ]);
    vi.stubGlobal("fetch", fetch);
    const transcript = await recordTranscriptViaServerAsr({
      recordImpl: async () => {
        order.push("recorded");
        return WAV;
      },
      onRecorded: (wav) => {
        order.push(`onRecorded:${wav.type}:${wav.size}`);
      },
    });
    expect(transcript).toBe("选 B");
    expect(order).toEqual(["recorded", `onRecorded:audio/wav:${WAV.size}`]);
  });

  it("录音失败（权限被拒等）原样抛出、零网络请求、零 trace", async () => {
    const fetch = queueFetch([]);
    vi.stubGlobal("fetch", fetch);
    await expect(
      recordTranscriptViaServerAsr({
        recordImpl: async () => {
          throw new Error("未获得麦克风权限，请在浏览器中允许麦克风后重试");
        },
      }),
    ).rejects.toThrow("未获得麦克风权限");
    await flush();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("服务端转写失败（4xx/5xx）原样抛出 ApiError、零 trace（失败不构成样本也不双计数）", async () => {
    const fetch = queueFetch([
      () => new Response(JSON.stringify({ detail: "音频内容为空" }), { status: 422 }),
    ]);
    vi.stubGlobal("fetch", fetch);
    const cause = await recordTranscriptViaServerAsr({ recordImpl: async () => WAV }).then(
      () => {
        throw new Error("422 应 reject");
      },
      (error: ApiError) => error,
    );
    expect(cause).toBeInstanceOf(ApiError);
    expect(cause.status).toBe(422);
    await flush();
    expect(fetch).toHaveBeenCalledTimes(1); // 仅 /transcribe，零 trace
  });
});

// --- 选项点击 = 文字候选答案：transcript 形态必须服务端可靠可解析 ---

describe("optionClickTranscript：选项点击 → 服务端可解析 transcript", () => {
  it("mcq 选项 → 「选 {key}」（服务端 intent parser 字母槽位模式）", () => {
    expect(optionClickTranscript(QUESTION, QUESTION.options![0])).toBe("选 A");
    expect(optionClickTranscript(QUESTION, QUESTION.options![1])).toBe("选 B");
  });

  it("判断题选项 → 选项文本（服务端 true_false 规范化直接读对/错文本）", () => {
    expect(optionClickTranscript(QUESTION_2, QUESTION_2.options![0])).toBe("正确");
    expect(optionClickTranscript(QUESTION_2, QUESTION_2.options![1])).toBe("错误");
  });
});
