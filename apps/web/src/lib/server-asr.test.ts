// M14-251 TDD：Web voice-studio 服务端 ASR 通道（server-asr.ts）的
// 行为与契约测试。只锚定可实证的行为——stub 全局 fetch/window 实证
// HTTP 契约（POST /api/v1/voice/transcribe 的 URL/multipart 字段/
// 文件名/MIME/credentials/cache）、TranscriptionOut JSON 响应忠实映射、
// X-Voice-Provider/X-Voice-Fallback 头三态、401 跳登录、非 2xx →
// ApiError 携服务端 detail（含安全通用回退）、网络失败原样透传，以及
// 零 /voice/trace 请求（asr 时延以服务端 /transcribe 内自动埋点为
// 唯一权威，客户端不得重复上报——M14-250 R1 的 tts 同口径）。
// 不做源码字符串断言。契约真值以 services/api
// app/api/routes/voice.py transcribe 为准：客户端不解析文本、不判定
// 答案、不虚报通道事实。
import { afterEach, describe, expect, it, vi } from "vitest";

import { API_BASE, ApiError } from "./api";
import { transcribeAudioWav } from "./server-asr";

const TRANSCRIBE_URL = `${API_BASE}/api/v1/voice/transcribe`;
const TRACE_URL = `${API_BASE}/api/v1/voice/trace`;

// 非空 WAV 形态 Blob（模块只透传字节，不解析内容——形状真实即可）
const WAV_BLOB = new Blob(
  [new Uint8Array([0x52, 0x49, 0x46, 0x46, 1, 2, 3, 4])],
  { type: "audio/wav" },
);

function transcriptionFixture() {
  return {
    id: 7,
    text: "选 A",
    confidence: 0.92,
    provider: "local-funasr",
    latency_ms: 812,
    audio_bytes: 96_044,
    audio_stored: true,
    audio_object_key: "voice/ab/abcd0123deadbeef",
  };
}

function transcriptionResponse(
  data: unknown = transcriptionFixture(),
  headers: Record<string, string> = {},
  status = 200,
): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function jsonResponse(data: unknown, status: number): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
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

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- HTTP 契约：multipart 字段/文件名/MIME/init 语义 ---

describe("transcribeAudioWav：POST /api/v1/voice/transcribe", () => {
  it("POST、multipart body 为 FormData：part 名 audio、filename answer.wav、MIME audio/wav", async () => {
    const fetch = queueFetch([
      () => transcriptionResponse(undefined, { "X-Voice-Provider": "local-funasr", "X-Voice-Fallback": "0" }),
    ]);
    vi.stubGlobal("fetch", fetch);
    await transcribeAudioWav(WAV_BLOB);
    const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(TRANSCRIBE_URL);
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(init.cache).toBe("no-store");
    // multipart 由浏览器构造（不手写 Content-Type——boundary 自动生成）
    expect(init.headers).toBeUndefined();
    const body = init.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    const file = body.get("audio");
    expect(file).toBeInstanceOf(File);
    expect((file as File).name).toBe("answer.wav");
    expect((file as File).type).toBe("audio/wav");
    expect((file as File).size).toBe(WAV_BLOB.size);
  });

  it("无类型 Blob（调用方 type 缺失）→ 上送 File 恒为 answer.wav / audio/wav、字节原样（不依赖入参 MIME）", async () => {
    const bytes = new Uint8Array([0x52, 0x49, 0x46, 0x46, 9, 8, 7, 6]);
    const untyped = new Blob([bytes]); // type === ""
    expect(untyped.type).toBe("");
    const fetch = queueFetch([() => transcriptionResponse()]);
    vi.stubGlobal("fetch", fetch);
    await transcribeAudioWav(untyped);
    const body = (fetch.mock.calls[0][1] as RequestInit).body as FormData;
    const file = body.get("audio") as File;
    expect(file.name).toBe("answer.wav");
    expect(file.type).toBe("audio/wav");
    expect(file.size).toBe(bytes.length);
    expect(new Uint8Array(await file.arrayBuffer())).toEqual(bytes);
  });

  it("服务端 TranscriptionOut JSON 忠实映射（snake_case 原样，含 audio_object_key=null 形态）", async () => {
    const fixture = { ...transcriptionFixture(), audio_stored: false, audio_object_key: null };
    const fetch = queueFetch([() => transcriptionResponse(fixture)]);
    vi.stubGlobal("fetch", fetch);
    const outcome = await transcribeAudioWav(WAV_BLOB);
    expect(outcome.transcript).toEqual(fixture);
  });

  it("X-Voice-Provider/X-Voice-Fallback 头透出：\"1\" → fallback=true", async () => {
    const fetch = queueFetch([
      () => transcriptionResponse(undefined, { "X-Voice-Provider": "tone", "X-Voice-Fallback": "1" }),
    ]);
    vi.stubGlobal("fetch", fetch);
    const outcome = await transcribeAudioWav(WAV_BLOB);
    expect(outcome.provider).toBe("tone");
    expect(outcome.fallback).toBe(true);
  });

  it("X-Voice-Fallback=\"0\" → false；两头缺席 → provider=unknown / fallback=false（不猜）", async () => {
    const fetch = queueFetch([
      () => transcriptionResponse(undefined, { "X-Voice-Fallback": "0" }),
      () => transcriptionResponse(),
    ]);
    vi.stubGlobal("fetch", fetch);
    const explicit = await transcribeAudioWav(WAV_BLOB);
    expect(explicit.fallback).toBe(false);
    const absent = await transcribeAudioWav(WAV_BLOB);
    expect(absent.provider).toBe("unknown");
    expect(absent.fallback).toBe(false);
  });

  it("422/502/413 → ApiError 携服务端 detail 原样抛出（不吞错不重试）", async () => {
    const fetch = queueFetch([
      () => jsonResponse({ detail: "音频内容为空" }, 422),
      () => jsonResponse({ detail: "语音服务暂不可用" }, 502),
      () => jsonResponse({ detail: "音频超过 20MB 上限" }, 413),
    ]);
    vi.stubGlobal("fetch", fetch);
    for (const [status, message] of [
      [422, "音频内容为空"],
      [502, "语音服务暂不可用"],
      [413, "音频超过 20MB 上限"],
    ] as const) {
      const cause = await transcribeAudioWav(WAV_BLOB).then(
        () => {
          throw new Error(`${status} 应 reject`);
        },
        (error: ApiError) => error,
      );
      expect(cause).toBeInstanceOf(ApiError);
      expect(cause.status).toBe(status);
      expect(cause.message).toBe(message);
    }
  });

  it("detail 非字符串（如 422 校验数组）→ 安全通用文案，不把结构体拼进错误", async () => {
    const fetch = queueFetch([() => jsonResponse({ detail: [] }, 422)]);
    vi.stubGlobal("fetch", fetch);
    const cause = await transcribeAudioWav(WAV_BLOB).then(
      () => {
        throw new Error("应 reject");
      },
      (error: ApiError) => error,
    );
    expect(cause.message).toBe("服务端语音识别失败");
  });

  it("401 → 与 request() 同语义跳转登录页（window 存在且不在登录页）", async () => {
    const fetch = queueFetch([() => jsonResponse({ detail: "未认证" }, 401)]);
    vi.stubGlobal("fetch", fetch);
    const location = {
      pathname: "/voice/studio",
      origin: "http://localhost:3000",
      href: "http://localhost:3000/voice/studio",
    };
    vi.stubGlobal("window", { location });
    const cause = await transcribeAudioWav(WAV_BLOB).then(
      () => {
        throw new Error("401 应 reject");
      },
      (error: ApiError) => error,
    );
    expect(cause.status).toBe(401);
    expect(location.href).toBe("http://localhost:3000/login");
  });

  it("401 已在登录页 → 不再跳转（防循环），仍 ApiError", async () => {
    const fetch = queueFetch([() => jsonResponse({ detail: "未认证" }, 401)]);
    vi.stubGlobal("fetch", fetch);
    const location = {
      pathname: "/login",
      origin: "http://localhost:3000",
      href: "http://localhost:3000/login",
    };
    vi.stubGlobal("window", { location });
    const cause = await transcribeAudioWav(WAV_BLOB).then(
      () => {
        throw new Error("401 应 reject");
      },
      (error: ApiError) => error,
    );
    expect(cause.status).toBe(401);
    expect(location.href).toBe("http://localhost:3000/login");
  });

  it("网络失败原样透传（TypeError 不吞不改，调用方如实提示）", async () => {
    const fetch = vi.fn(() => Promise.reject(new TypeError("Failed to fetch")));
    vi.stubGlobal("fetch", fetch);
    await expect(transcribeAudioWav(WAV_BLOB)).rejects.toThrow("Failed to fetch");
  });
});

// --- M14-251：服务端 ASR 通道零客户端 asr 埋点 ---
// asr 时延以服务端 /transcribe 内自动埋点（_record_trace stage="asr"）
// 为唯一权威；客户端成功/失败均不得发出任何 /voice/trace 请求
// （避免 /trace/summary 同 stage 双计数）。

describe("零 /voice/trace 请求（asr 权威埋点在服务端）", () => {
  it("成功链恰一个请求（transcribe），排空迟到微任务后仍零 trace", async () => {
    const fetch = queueFetch([() => transcriptionResponse()]);
    vi.stubGlobal("fetch", fetch);
    const outcome = await transcribeAudioWav(WAV_BLOB);
    expect(outcome.transcript.text).toBe("选 A");
    await new Promise((resolve) => setTimeout(resolve, 5)); // 排空任何迟到上报
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(urlsOf(fetch)).toEqual([TRANSCRIBE_URL]);
  });

  it("失败链同样零 trace（422 错误原样抛出，失败不构成样本也不双计数）", async () => {
    const fetch = queueFetch([() => jsonResponse({ detail: "音频内容为空" }, 422)]);
    vi.stubGlobal("fetch", fetch);
    await expect(transcribeAudioWav(WAV_BLOB)).rejects.toMatchObject({ status: 422 });
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(urlsOf(fetch)).toEqual([TRANSCRIBE_URL]);
    expect(urlsOf(fetch)).not.toContain(TRACE_URL);
  });
});
