// M14-248 TDD：Web VoiceSession API 契约（先 RED 后 GREEN）。
// 只锚定可实证的 HTTP 契约：每个方法的 URL/method/body、credentials/
// headers/cache 继承、响应 JSON 原样透传、event_id 幂等 helper 的格式
// 与重试稳定性。契约真值以 services/api/app/api/routes/voice.py 为准
// ——客户端不加服务端不存在的字段、不改写、不推演 FSM 状态转移。
import { afterEach, describe, expect, it, vi } from "vitest";

import { API_BASE, ApiError, api, newVoiceAnswerEventId } from "./api";

// 与服务端 VoiceSessionOut 同形的 fixture（status 用未知原词——证明
// 客户端按 string 原样接收，不校验 8 状态白名单、不猜语义）
const SESSION = {
  session_id: "sess-1",
  exam_id: "exam-1",
  status: "SOME_FUTURE_STATE",
  question_index: 2,
  revision: 4,
  created_at: "2026-10-07T00:00:00Z",
  updated_at: "2026-10-07T00:01:00Z",
};

function okFetch(data: unknown): ReturnType<typeof vi.fn> {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify(data), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

function lastCall(mock: ReturnType<typeof vi.fn>): [string, RequestInit] {
  expect(mock).toHaveBeenCalledTimes(1);
  return mock.mock.calls[0] as [string, RequestInit];
}

function bodyOf(init: RequestInit): Record<string, unknown> {
  return JSON.parse(String(init.body)) as Record<string, unknown>;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("voiceSessions.create：POST /api/v1/voice/sessions", () => {
  it("POST、body 恰一键 exam_id、响应 JSON 原样透传", async () => {
    const fetch = okFetch(SESSION);
    vi.stubGlobal("fetch", fetch);
    const result = await api.voiceSessions.create("exam-1");
    const [url, init] = lastCall(fetch);
    expect(url).toBe(`${API_BASE}/api/v1/voice/sessions`);
    expect(init.method).toBe("POST");
    expect(bodyOf(init)).toEqual({ exam_id: "exam-1" });
    expect(result).toEqual(SESSION);
  });
});

describe("voiceSessions.list：GET /api/v1/voice/sessions?exam_id=", () => {
  it("GET（无 method 覆写）、exam_id 作为必填 query 参数", async () => {
    const fetch = okFetch({ items: [SESSION] });
    vi.stubGlobal("fetch", fetch);
    const result = await api.voiceSessions.list("exam-1");
    const [url, init] = lastCall(fetch);
    expect(url).toBe(`${API_BASE}/api/v1/voice/sessions?exam_id=exam-1`);
    expect(init.method).toBeUndefined();
    expect(result).toEqual({ items: [SESSION] });
  });

  it("exam_id 含需编码字符时按 URL 参数编码（不改路径结构）", async () => {
    const fetch = okFetch({ items: [] });
    vi.stubGlobal("fetch", fetch);
    await api.voiceSessions.list("exam 1&x");
    const [url] = lastCall(fetch);
    expect(url).toBe(`${API_BASE}/api/v1/voice/sessions?exam_id=exam%201%26x`);
  });
});

describe("voiceSessions.get：GET /api/v1/voice/sessions/{session_id}", () => {
  it("GET 动态路径、未知 status 原词透传（客户端不校验状态白名单）", async () => {
    const fetch = okFetch(SESSION);
    vi.stubGlobal("fetch", fetch);
    const result = await api.voiceSessions.get("sess-1");
    const [url, init] = lastCall(fetch);
    expect(url).toBe(`${API_BASE}/api/v1/voice/sessions/sess-1`);
    expect(init.method).toBeUndefined();
    expect(result.status).toBe("SOME_FUTURE_STATE");
  });
});

describe("voiceSessions.resume：GET /api/v1/voice/sessions/{id}/resume", () => {
  it("GET 动态路径、响应投影（session/question/committed_answer/question_total）透传", async () => {
    const resume = {
      session: SESSION,
      question: {
        id: "q-1",
        type: "mcq",
        stem: "题干",
        options: [{ key: "A", text: "选项 A" }],
      },
      committed_answer: "A",
      question_total: 5,
    };
    const fetch = okFetch(resume);
    vi.stubGlobal("fetch", fetch);
    const result = await api.voiceSessions.resume("sess-1");
    const [url, init] = lastCall(fetch);
    expect(url).toBe(`${API_BASE}/api/v1/voice/sessions/sess-1/resume`);
    expect(init.method).toBeUndefined();
    expect(result).toEqual(resume);
  });
});

describe("voiceSessions.command：POST /api/v1/voice/sessions/{id}/commands", () => {
  it("POST、全字段载荷原样序列化（服务端 VoiceCommandRequest 五键）", async () => {
    const out = {
      applied_event: "answer_proposed",
      from_status: "WAITING_ANSWER",
      session: SESSION,
      clarified_question: null,
      question_total: null,
    };
    const fetch = okFetch(out);
    vi.stubGlobal("fetch", fetch);
    const result = await api.voiceSessions.command("sess-1", {
      type: "answer_proposed",
      question_id: "q-1",
      answer: "A",
      ambiguous: false,
      expected_revision: 4,
    });
    const [url, init] = lastCall(fetch);
    expect(url).toBe(`${API_BASE}/api/v1/voice/sessions/sess-1/commands`);
    expect(init.method).toBe("POST");
    expect(bodyOf(init)).toEqual({
      type: "answer_proposed",
      question_id: "q-1",
      answer: "A",
      ambiguous: false,
      expected_revision: 4,
    });
    expect(result).toEqual(out);
  });

  it("最小载荷 body 恰一键 type（可选键缺省不进载荷、零多余字段）", async () => {
    const fetch = okFetch({});
    vi.stubGlobal("fetch", fetch);
    await api.voiceSessions.command("sess-1", { type: "start_reading" });
    const [, init] = lastCall(fetch);
    expect(Object.keys(bodyOf(init))).toEqual(["type"]);
  });
});

describe("voiceSessions.intent：POST /api/v1/voice/sessions/{id}/intents", () => {
  it("POST、载荷恰 transcript（+可选 expected_revision）、响应透传", async () => {
    const out = {
      transcript: "选 A",
      intent: "choose",
      letter: "A",
      ordinal: null,
      ambiguous: false,
      fsm_command: "answer_proposed",
      fsm_applied: true,
      applied_event: "answer_proposed",
      session: SESSION,
      clarified_question: null,
    };
    const fetch = okFetch(out);
    vi.stubGlobal("fetch", fetch);
    const result = await api.voiceSessions.intent("sess-1", {
      transcript: "选 A",
      expected_revision: 4,
    });
    const [url, init] = lastCall(fetch);
    expect(url).toBe(`${API_BASE}/api/v1/voice/sessions/sess-1/intents`);
    expect(init.method).toBe("POST");
    expect(bodyOf(init)).toEqual({ transcript: "选 A", expected_revision: 4 });
    expect(result).toEqual(out);
  });

  it("最小载荷 body 恰一键 transcript", async () => {
    const fetch = okFetch({});
    vi.stubGlobal("fetch", fetch);
    await api.voiceSessions.intent("sess-1", { transcript: "重复读题" });
    const [, init] = lastCall(fetch);
    expect(Object.keys(bodyOf(init))).toEqual(["transcript"]);
  });
});

describe("voiceSessions.answer：POST /api/v1/voice/sessions/{id}/answers", () => {
  it("POST、全字段载荷原样序列化（服务端 VoiceAnswerRequest 五键）", async () => {
    const out = {
      event_id: "evt-1",
      idempotent: false,
      accepted: true,
      normalized_answer: "A",
      intent: "choose",
      question_id: "q-1",
      session: SESSION,
      clarified_question: null,
    };
    const fetch = okFetch(out);
    vi.stubGlobal("fetch", fetch);
    const result = await api.voiceSessions.answer("sess-1", {
      transcript: "选 A",
      event_id: "evt-1",
      question_id: "q-1",
      normalized_answer: "A",
      confidence: 0.9,
    });
    const [url, init] = lastCall(fetch);
    expect(url).toBe(`${API_BASE}/api/v1/voice/sessions/sess-1/answers`);
    expect(init.method).toBe("POST");
    expect(bodyOf(init)).toEqual({
      transcript: "选 A",
      event_id: "evt-1",
      question_id: "q-1",
      normalized_answer: "A",
      confidence: 0.9,
    });
    expect(result).toEqual(out);
  });

  it("最小载荷 body 恰两键 transcript+event_id（event_id 必填、由调用方传入）", async () => {
    const fetch = okFetch({});
    vi.stubGlobal("fetch", fetch);
    await api.voiceSessions.answer("sess-1", {
      transcript: "选 A",
      event_id: "evt-1",
    });
    const [, init] = lastCall(fetch);
    expect(Object.keys(bodyOf(init)).sort()).toEqual(["event_id", "transcript"]);
  });
});

describe("voiceSessions.report：GET /api/v1/voice/sessions/{id}/report", () => {
  it("GET 动态路径、播报投影（mistake/remediation summary）原样透传", async () => {
    const report = {
      session_id: "sess-1",
      exam_id: "exam-1",
      spoken_text: "考试完成！",
      mistake_summary: [
        {
          question_id: "q-2",
          stem_preview: "截断题干…",
          your_answer: "A",
          correct_answer: "B",
          concepts: ["概念一"],
          diagnosis: "概念混淆",
        },
      ],
      remediation_summary: [
        {
          concept: "概念一",
          actions: ["review_concept"],
          detail: "复习该概念",
          question_ids: ["q-2"],
        },
      ],
      written_report_url: "/api/v1/exams/exam-1/report",
    };
    const fetch = okFetch(report);
    vi.stubGlobal("fetch", fetch);
    const result = await api.voiceSessions.report("sess-1");
    const [url, init] = lastCall(fetch);
    expect(url).toBe(`${API_BASE}/api/v1/voice/sessions/sess-1/report`);
    expect(init.method).toBeUndefined();
    expect(result).toEqual(report);
  });
});

describe("统一请求语义：credentials/headers/cache 经 request() 继承", () => {
  const cases: Array<{ name: string; run: () => Promise<unknown> }> = [
    { name: "create", run: () => api.voiceSessions.create("exam-1") },
    { name: "list", run: () => api.voiceSessions.list("exam-1") },
    { name: "get", run: () => api.voiceSessions.get("sess-1") },
    { name: "resume", run: () => api.voiceSessions.resume("sess-1") },
    {
      name: "command",
      run: () => api.voiceSessions.command("sess-1", { type: "start_reading" }),
    },
    {
      name: "intent",
      run: () => api.voiceSessions.intent("sess-1", { transcript: "继续" }),
    },
    {
      name: "answer",
      run: () =>
        api.voiceSessions.answer("sess-1", { transcript: "选 A", event_id: "evt-1" }),
    },
    { name: "report", run: () => api.voiceSessions.report("sess-1") },
  ];

  for (const item of cases) {
    it(`${item.name}：credentials=include + Content-Type=application/json + cache=no-store`, async () => {
      const fetch = okFetch({});
      vi.stubGlobal("fetch", fetch);
      await item.run();
      const [, init] = lastCall(fetch);
      expect(init.credentials).toBe("include");
      expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
      expect(init.cache).toBe("no-store");
    });
  }
});

describe("newVoiceAnswerEventId：answers 幂等键 helper", () => {
  it("生成 UUID v4 形态（8-4-4-4-12 十六进制、版本 4、变体位）", () => {
    const id = newVoiceAnswerEventId();
    expect(id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  });

  it("长度 36 落在服务端 event_id 界内（min_length=4 / max_length=64）", () => {
    const id = newVoiceAnswerEventId();
    expect(id.length).toBeGreaterThanOrEqual(4);
    expect(id.length).toBeLessThanOrEqual(64);
    expect(id.length).toBe(36);
  });

  it("每次调用生成新键（一次逻辑事件一次生成；200 次全不相同）", () => {
    const seen = new Set<string>();
    for (let i = 0; i < 200; i += 1) seen.add(newVoiceAnswerEventId());
    expect(seen.size).toBe(200);
  });

  it("幂等重试：同一次逻辑提交失败后重试，两次请求 body 的 event_id 不变", async () => {
    const eventId = newVoiceAnswerEventId(); // 调用方在事件发起前生成一次
    const payload = { transcript: "选 A", event_id: eventId } as const;

    // 第一次提交：服务端 500（网络/服务抖动）——payload 原样发出
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "服务暂不可用" }), { status: 500 }),
      ),
    );
    await expect(api.voiceSessions.answer("sess-1", payload)).rejects.toMatchObject({
      name: "ApiError",
      status: 500,
    });

    // 同一 payload 重试：event_id 不重新生成、body 逐字节一致
    const retry = okFetch({ event_id: eventId, idempotent: false });
    vi.stubGlobal("fetch", retry);
    await api.voiceSessions.answer("sess-1", payload);
    const [, retryInit] = lastCall(retry);
    expect(bodyOf(retryInit).event_id).toBe(eventId);
    expect(bodyOf(retryInit)).toEqual({ transcript: "选 A", event_id: eventId });
  });
});

describe("契约边界：API 层不改写载荷、不虚增字段", () => {
  it("answer 载荷序列化后与传入对象 deep equal（键序无关、无添加键）", async () => {
    const fetch = okFetch({});
    vi.stubGlobal("fetch", fetch);
    const payload = {
      transcript: "第二个",
      event_id: "evt-boundary",
      question_id: "q-3",
      normalized_answer: null,
      confidence: 0,
    };
    await api.voiceSessions.answer("sess-1", payload);
    const [, init] = lastCall(fetch);
    expect(bodyOf(init)).toEqual(payload);
  });

  it("非 2xx 错误经 ApiError 抛出（status 原样携带，不吞错）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "考试状态 active 不允许语音作答" }), {
          status: 409,
        }),
      ),
    );
    const failure = api.voiceSessions.create("exam-1");
    const cause = await failure.then(
      () => {
        throw new Error("非 2xx 应 reject");
      },
      (error: ApiError) => error,
    );
    expect(cause).toBeInstanceOf(ApiError);
    expect(cause.status).toBe(409);
    expect(cause.message).toBe("考试状态 active 不允许语音作答");
  });
});

// --- Supervisor R1 修正：动态路径 segment 编码 + 幂等键降级链 ---

describe("R1：动态路径 segment 编码——危险字符不拆 route", () => {
  it("sess/1?x#y 在全部六个动态路径中保持单 segment（sess%2F1%3Fx%23y）", async () => {
    const sessionId = "sess/1?x#y";
    const encoded = "sess%2F1%3Fx%23y";
    const cases: Array<{ run: () => Promise<unknown>; suffix: string }> = [
      { run: () => api.voiceSessions.get(sessionId), suffix: "" },
      { run: () => api.voiceSessions.resume(sessionId), suffix: "/resume" },
      {
        run: () => api.voiceSessions.command(sessionId, { type: "start_reading" }),
        suffix: "/commands",
      },
      {
        run: () => api.voiceSessions.intent(sessionId, { transcript: "继续" }),
        suffix: "/intents",
      },
      {
        run: () => api.voiceSessions.answer(sessionId, { transcript: "选 A", event_id: "evt-1" }),
        suffix: "/answers",
      },
      { run: () => api.voiceSessions.report(sessionId), suffix: "/report" },
    ];
    for (const item of cases) {
      const fetch = okFetch({});
      vi.stubGlobal("fetch", fetch);
      await item.run();
      const [url] = lastCall(fetch);
      expect(url).toBe(`${API_BASE}/api/v1/voice/sessions/${encoded}${item.suffix}`);
    }
  });
});

// mulberry32 确定性 PRNG：模拟「非安全上下文 WebCrypto 子集」的字节源
// （每次调用推进状态，保证 200 次调用产出互不相同的字节序列）
function cryptoWithGetRandomValuesOnly(): {
  getRandomValues: <T extends Uint8Array>(array: T) => T;
} {
  let seed = 0x9e3779b9;
  const next = (): number => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = seed;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return {
    getRandomValues: <T extends Uint8Array>(array: T): T => {
      for (let i = 0; i < array.length; i += 1) array[i] = Math.floor(next() * 256);
      return array;
    },
  };
}

describe("R1：newVoiceAnswerEventId 非安全上下文降级链（SSR/LAN HTTP 不崩溃）", () => {
  const UUID_V4 =
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

  it("无 randomUUID、仅 getRandomValues：仍生成合法 UUID v4 且 200 次唯一", () => {
    vi.stubGlobal("crypto", cryptoWithGetRandomValuesOnly());
    const seen = new Set<string>();
    for (let i = 0; i < 200; i += 1) {
      const id = newVoiceAnswerEventId();
      expect(id).toMatch(UUID_V4);
      expect(id.length).toBe(36);
      seen.add(id);
    }
    expect(seen.size).toBe(200);
  });

  it("完全无 WebCrypto（crypto=undefined）：Math.random fallback 仍生成合法 UUID v4 且 200 次唯一", () => {
    vi.stubGlobal("crypto", undefined);
    const seen = new Set<string>();
    for (let i = 0; i < 200; i += 1) {
      const id = newVoiceAnswerEventId();
      expect(id).toMatch(UUID_V4);
      expect(id.length).toBe(36);
      seen.add(id);
    }
    expect(seen.size).toBe(200);
  });

  it("降级链不改变幂等重试语义：getRandomValues-only 环境失败后重试，event_id 逐字节不变", async () => {
    vi.stubGlobal("crypto", cryptoWithGetRandomValuesOnly());
    const eventId = newVoiceAnswerEventId(); // 一次逻辑事件生成一次
    const payload = { transcript: "选 A", event_id: eventId } as const;

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "服务暂不可用" }), { status: 500 }),
      ),
    );
    await expect(api.voiceSessions.answer("sess-1", payload)).rejects.toMatchObject({
      name: "ApiError",
      status: 500,
    });

    const retry = okFetch({ event_id: eventId, idempotent: false });
    vi.stubGlobal("fetch", retry);
    await api.voiceSessions.answer("sess-1", payload);
    const [, init] = lastCall(retry);
    expect(bodyOf(init).event_id).toBe(eventId);
  });
});
