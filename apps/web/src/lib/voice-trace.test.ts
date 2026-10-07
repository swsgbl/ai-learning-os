// M14-247 TDD：M4-09 Web 客户端时延埋点契约（先 RED 后 GREEN）。
// 只测仓库内可实证的行为：载荷构造、stage 白名单与 duration 界内处理、
// 可选 ID 仅真实上下文可用时携带、上报失败静默吞掉、埋点不阻塞也不改变
// 主流程语义。不虚报浏览器测不到的阶段（vad/llm/first_audio 不在本模块
// 调用面内——那些属于 LiveKit/服务端链路，见 M14-245 矩阵缺口 3/4）。
import { afterEach, describe, expect, it, vi } from "vitest";

import { API_BASE } from "./api";
import {
  MAX_TRACE_DURATION_MS,
  TRACE_STAGES,
  buildTraceSpanPayload,
  submitVoiceTrace,
  withVoiceTrace,
  type VoiceTraceSpan,
} from "./voice-trace";

// 运行时防御测试用：绕过编译期 stage 类型（与真实调用方传入不可信数据的
// 防御场景等价——白名单最终由服务端 422 兜底，客户端先行静默跳过）。
function asSpan(input: unknown): VoiceTraceSpan {
  return input as VoiceTraceSpan;
}

function fetchMock(): ReturnType<typeof vi.fn> {
  return vi.fn().mockResolvedValue(new Response(null, { status: 201 }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("buildTraceSpanPayload：载荷构造", () => {
  it("七个白名单阶段全部可构造（与 services/api TRACE_STAGES 同口径）", () => {
    expect([...TRACE_STAGES]).toEqual([
      "vad",
      "asr",
      "intent",
      "fsm",
      "llm",
      "tts",
      "first_audio",
    ]);
    for (const stage of TRACE_STAGES) {
      const payload = buildTraceSpanPayload({ stage, duration_ms: 100 });
      expect(payload?.stage).toBe(stage);
      expect(payload?.duration_ms).toBe(100);
    }
  });

  it("非法 stage 返回 null（静默跳过，不构造注定 422 的载荷）", () => {
    expect(buildTraceSpanPayload(asSpan({ stage: "total", duration_ms: 100 }))).toBeNull();
    expect(buildTraceSpanPayload(asSpan({ stage: "", duration_ms: 100 }))).toBeNull();
  });

  it("小数耗时四舍五入为整数（服务端 duration_ms 为整数）", () => {
    expect(buildTraceSpanPayload({ stage: "asr", duration_ms: 1.6 })?.duration_ms).toBe(2);
    expect(buildTraceSpanPayload({ stage: "tts", duration_ms: 0.6 })?.duration_ms).toBe(1);
  });

  it("duration 界内边界值可构造：1 与 600000", () => {
    expect(buildTraceSpanPayload({ stage: "asr", duration_ms: 1 })).not.toBeNull();
    expect(buildTraceSpanPayload({ stage: "tts", duration_ms: MAX_TRACE_DURATION_MS })).not.toBeNull();
  });

  it("duration 越界返回 null（不截断不虚报：0/600001/取整后为 0/NaN/Infinity）", () => {
    expect(buildTraceSpanPayload({ stage: "asr", duration_ms: 0 })).toBeNull();
    expect(buildTraceSpanPayload({ stage: "asr", duration_ms: MAX_TRACE_DURATION_MS + 1 })).toBeNull();
    expect(buildTraceSpanPayload({ stage: "asr", duration_ms: 0.4 })).toBeNull();
    expect(buildTraceSpanPayload({ stage: "asr", duration_ms: Number.NaN })).toBeNull();
    expect(buildTraceSpanPayload({ stage: "asr", duration_ms: Number.POSITIVE_INFINITY })).toBeNull();
  });

  it("可选 ID 仅在为真值字符串时携带（真实上下文可用才上报）", () => {
    const full = buildTraceSpanPayload({
      stage: "asr",
      duration_ms: 120,
      session_id: "sess-1",
      exam_id: "exam-1",
      question_id: "q-1",
    });
    expect(full).toMatchObject({ session_id: "sess-1", exam_id: "exam-1", question_id: "q-1" });

    const bare = buildTraceSpanPayload({ stage: "asr", duration_ms: 120 });
    expect(bare).not.toBeNull();
    expect("session_id" in (bare ?? {})).toBe(false);
    expect("exam_id" in (bare ?? {})).toBe(false);
    expect("question_id" in (bare ?? {})).toBe(false);
  });

  it("空串 ID 视为缺失（undefined/空串一律不进载荷）", () => {
    const payload = buildTraceSpanPayload({
      stage: "tts",
      duration_ms: 90,
      session_id: "",
      exam_id: undefined,
    });
    expect(payload).not.toBeNull();
    expect("session_id" in (payload ?? {})).toBe(false);
    expect("exam_id" in (payload ?? {})).toBe(false);
  });
});

describe("submitVoiceTrace：尽力而为上报", () => {
  it("合法载荷以 POST + JSON 提交到 /api/v1/voice/trace（带会话凭据）", async () => {
    const fetch = fetchMock();
    vi.stubGlobal("fetch", fetch);
    await submitVoiceTrace({
      stage: "asr",
      duration_ms: 250,
      session_id: "sess-1",
      exam_id: "exam-1",
      question_id: "q-1",
    });
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE}/api/v1/voice/trace`);
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
    expect(JSON.parse(String(init.body))).toEqual({
      stage: "asr",
      duration_ms: 250,
      session_id: "sess-1",
      exam_id: "exam-1",
      question_id: "q-1",
    });
  });

  it("无上下文载荷的请求体只含 stage 与 duration_ms 两键", async () => {
    const fetch = fetchMock();
    vi.stubGlobal("fetch", fetch);
    await submitVoiceTrace({ stage: "tts", duration_ms: 800 });
    const [, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(Object.keys(JSON.parse(String(init.body))).sort()).toEqual(["duration_ms", "stage"]);
  });

  it("非法载荷零网络请求（构造失败的 span 不出浏览器）", async () => {
    const fetch = fetchMock();
    vi.stubGlobal("fetch", fetch);
    await submitVoiceTrace({ stage: "asr", duration_ms: 0 });
    await submitVoiceTrace(asSpan({ stage: "nope", duration_ms: 100 }));
    expect(fetch).not.toHaveBeenCalled();
  });

  it("网络失败静默吞掉：promise 恒 resolve，绝不 reject", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(submitVoiceTrace({ stage: "asr", duration_ms: 100 })).resolves.toBeUndefined();
  });

  it("非 2xx 响应同样静默（503 无 DB / 422 越界均不惊动业务）", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 503 })));
    await expect(submitVoiceTrace({ stage: "asr", duration_ms: 100 })).resolves.toBeUndefined();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 422 })));
    await expect(submitVoiceTrace({ stage: "tts", duration_ms: 100 })).resolves.toBeUndefined();
  });
});

describe("withVoiceTrace：埋点不阻塞主流程", () => {
  it("成功路径：透传结果并上报 span（stage 正确、duration 为界内整数）", async () => {
    const fetch = fetchMock();
    vi.stubGlobal("fetch", fetch);
    const result = await withVoiceTrace(
      "asr",
      async () => {
        await new Promise((resolve) => setTimeout(resolve, 10)); // 真实耗时 ≥1ms，保证取整后界内
        return "transcript";
      },
      { exam_id: "exam-1", question_id: "q-1" },
    );
    expect(result).toBe("transcript");
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE}/api/v1/voice/trace`);
    const body = JSON.parse(String(init.body)) as {
      stage: string;
      duration_ms: number;
      exam_id?: string;
      question_id?: string;
    };
    expect(body.stage).toBe("asr");
    expect(Number.isInteger(body.duration_ms)).toBe(true);
    expect(body.duration_ms).toBeGreaterThanOrEqual(1);
    expect(body.duration_ms).toBeLessThanOrEqual(MAX_TRACE_DURATION_MS);
    expect(body.exam_id).toBe("exam-1");
    expect(body.question_id).toBe("q-1");
  });

  it("失败路径：原错误对象原样抛出且零上报（失败不构成时延样本）", async () => {
    const fetch = fetchMock();
    vi.stubGlobal("fetch", fetch);
    const boom = new Error("当前浏览器不支持本地听写");
    const run = async (): Promise<string> => {
      throw boom;
    };
    await expect(withVoiceTrace("asr", run, { exam_id: "exam-1" })).rejects.toBe(boom);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(fetch).not.toHaveBeenCalled();
  });

  it("上报通道故障不影响主流程结果（fetch 拒绝，业务值照常返回）", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const result = await withVoiceTrace(
      "tts",
      async () => {
        await new Promise((resolve) => setTimeout(resolve, 10));
        return "spoken";
      },
      { exam_id: "exam-1", question_id: "q-9" },
    );
    expect(result).toBe("spoken");
  });
});
