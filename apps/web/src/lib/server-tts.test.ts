// M14-250 TDD：Web voice-studio 服务端 TTS 通道（server-tts.ts）的
// 行为与契约测试。只锚定可实证的行为——stub 全局 fetch/Audio/URL 实证
// HTTP 契约（POST /api/v1/voice/synthesize 的 URL/method/body/
// credentials）、错误映射（4xx/5xx → ApiError 含服务端 detail）、WAV
// 播放的真实完成语义（ended 才 resolve、error/play 拒绝、object URL
// 恒回收）、通道停止语义、providers 视图契约与 R1 零客户端 tts 埋点
// （成功/失败均只发 /synthesize、零 /voice/trace），不做源码字符串
// 断言。契约真值以 services/api
// app/api/routes/voice.py synthesize/list_providers 为准：客户端不
// 解析文本、不判定语音质量、不虚报播报完成。
import { afterEach, describe, expect, it, vi } from "vitest";

import { API_BASE, ApiError, api } from "./api";
import {
  playWavAudio,
  speakWithServerTts,
  stopServerTtsPlayback,
  synthesizeSpeech,
} from "./server-tts";

const SYNTHESIZE_URL = `${API_BASE}/api/v1/voice/synthesize`;

const WAV_BYTES = new Uint8Array([0x52, 0x49, 0x46, 0x46, 1, 2, 3, 4]); // RIFF....

function wavResponse(headers: Record<string, string> = {}): Response {
  return new Response(WAV_BYTES, {
    status: 200,
    headers: { "Content-Type": "audio/wav", ...headers },
  });
}

function jsonResponse(data: unknown, status = 200): Response {
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

function bodiesOf(mock: FetchMock): Array<Record<string, unknown>> {
  return mock.mock.calls
    .map((call) => (call[1] as RequestInit | undefined)?.body)
    .filter((body): body is string => typeof body === "string")
    .map((body) => JSON.parse(body) as Record<string, unknown>);
}

// --- Audio / URL 环境桩：node 测试环境无 DOM，按需注入 ---
// 注意：vi.fn() 不可 new（非构造器），Audio 桩必须用普通构造函数；
// 构造即入 created 数组供断言（play/pause 仍为 vi.fn 可断言调用）。

type StubAudioElement = {
  src: string;
  play: ReturnType<typeof vi.fn>;
  pause: ReturnType<typeof vi.fn>;
  addEventListener: (type: "ended" | "error", listener: () => void) => void;
  emit: (type: "ended" | "error") => void;
};

function stubAudioEnvironment(
  options: { playImpl?: () => Promise<void> } = {},
): {
  created: StubAudioElement[];
  createObjectURL: ReturnType<typeof vi.fn>;
  revokeObjectURL: ReturnType<typeof vi.fn>;
} {
  const created: StubAudioElement[] = [];
  const createObjectURL = vi.fn(() => `blob:voice-${created.length + 1}`);
  const revokeObjectURL = vi.fn();
  const Audio = function (src: string) {
    const listeners = new Map<string, Array<() => void>>();
    const element: StubAudioElement = {
      src,
      play: vi.fn(options.playImpl ?? (() => Promise.resolve())),
      pause: vi.fn(),
      addEventListener: (type, listener) => {
        const bucket = listeners.get(type) ?? [];
        bucket.push(listener);
        listeners.set(type, bucket);
      },
      emit: (type) => {
        for (const listener of listeners.get(type) ?? []) listener();
      },
    };
    created.push(element);
    return element;
  };
  vi.stubGlobal("Audio", Audio);
  vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
  vi.stubGlobal("window", {});
  return { created, createObjectURL, revokeObjectURL };
}

afterEach(() => {
  vi.unstubAllGlobals();
  stopServerTtsPlayback(); // 清理模块级播放占用，避免跨用例泄漏
});

// --- synthesizeSpeech：HTTP 契约与错误映射 ---

describe("synthesizeSpeech：POST /api/v1/voice/synthesize", () => {
  it("POST、body 恰一键 text、credentials include、JSON 头；返回音频字节 + provider/fallback 头", async () => {
    const fetch = queueFetch([
      () => wavResponse({ "X-Voice-Provider": "local-cosyvoice", "X-Voice-Fallback": "0" }),
    ]);
    vi.stubGlobal("fetch", fetch);
    const result = await synthesizeSpeech("第 1 题，共 3 题。");
    const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(SYNTHESIZE_URL);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ text: "第 1 题，共 3 题。" });
    expect(init.credentials).toBe("include");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(new Uint8Array(result.audio)).toEqual(WAV_BYTES);
    expect(result.provider).toBe("local-cosyvoice");
    expect(result.fallback).toBe(false);
  });

  it("X-Voice-Fallback=1 → fallback=true（服务端降级 tone 替身如实透出，不谎报）", async () => {
    const fetch = queueFetch([
      () => wavResponse({ "X-Voice-Provider": "tone", "X-Voice-Fallback": "1" }),
    ]);
    vi.stubGlobal("fetch", fetch);
    const result = await synthesizeSpeech("题干");
    expect(result.provider).toBe("tone");
    expect(result.fallback).toBe(true);
  });

  it("fallback 头缺席 → false（不猜）", async () => {
    const fetch = queueFetch([() => wavResponse({ "X-Voice-Provider": "cloud-openai-tts" })]);
    vi.stubGlobal("fetch", fetch);
    const result = await synthesizeSpeech("题干");
    expect(result.fallback).toBe(false);
  });

  it("422/502 → ApiError 携带服务端 detail 原样抛出（不吞错不重试）", async () => {
    const fetch = queueFetch([
      () => jsonResponse({ detail: [] }, 422),
      () => jsonResponse({ detail: "语音服务暂不可用" }, 502),
    ]);
    vi.stubGlobal("fetch", fetch);
    const first = await synthesizeSpeech("题干").then(
      () => {
        throw new Error("422 应 reject");
      },
      (error: ApiError) => error,
    );
    expect(first).toBeInstanceOf(ApiError);
    expect(first.status).toBe(422);
    const second = await synthesizeSpeech("题干").then(
      () => {
        throw new Error("502 应 reject");
      },
      (error: ApiError) => error,
    );
    expect(second.status).toBe(502);
    expect(second.message).toBe("语音服务暂不可用");
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
    const cause = await synthesizeSpeech("题干").then(
      () => {
        throw new Error("401 应 reject");
      },
      (error: ApiError) => error,
    );
    expect(cause.status).toBe(401);
    expect(location.href).toBe("http://localhost:3000/login");
  });

  it("空音频字节 → 如实 reject（绝不把空合成当作可播放语音）", async () => {
    const empty = new Response(new Uint8Array(0), {
      status: 200,
      headers: { "Content-Type": "audio/wav", "X-Voice-Provider": "tone" },
    });
    const fetch = queueFetch([() => empty]);
    vi.stubGlobal("fetch", fetch);
    await expect(synthesizeSpeech("题干")).rejects.toThrow("空音频");
  });
});

// --- playWavAudio：真实完成语义（ended 才 resolve）与资源回收 ---

describe("playWavAudio：WAV 播放", () => {
  it("object URL 以 audio/wav Blob 创建；ended 才 resolve，随后回收 URL", async () => {
    const env = stubAudioEnvironment();
    const pending = playWavAudio(new ArrayBuffer(8));
    expect(env.created).toHaveLength(1);
    expect(env.createObjectURL).toHaveBeenCalledTimes(1);
    const blob = env.createObjectURL.mock.calls[0][0] as Blob;
    expect(blob).toBeInstanceOf(Blob);
    expect(blob.type).toBe("audio/wav");
    expect(env.created[0].play).toHaveBeenCalledTimes(1);
    await Promise.resolve(); // play() promise 排空
    expect(env.revokeObjectURL).not.toHaveBeenCalled(); // 播放中不回收
    env.created[0].emit("ended");
    await expect(pending).resolves.toBeUndefined();
    expect(env.revokeObjectURL).toHaveBeenCalledWith("blob:voice-1");
  });

  it("播放 error 事件 → reject（不谎报完成），URL 恒回收", async () => {
    const env = stubAudioEnvironment();
    const pending = playWavAudio(new ArrayBuffer(8));
    env.created[0].emit("error");
    await expect(pending).rejects.toThrow("音频播放失败");
    expect(env.revokeObjectURL).toHaveBeenCalledWith("blob:voice-1");
  });

  it("play() 拒绝（如自动播放策略）→ reject，URL 恒回收", async () => {
    const env = stubAudioEnvironment({
      playImpl: () => Promise.reject(new DOMException("NotAllowed", "NotAllowedError")),
    });
    await expect(playWavAudio(new ArrayBuffer(8))).rejects.toThrow("音频播放失败");
    expect(env.revokeObjectURL).toHaveBeenCalledWith("blob:voice-1");
  });

  it("无 window（SSR）→ reject 不触碰全局", async () => {
    await expect(playWavAudio(new ArrayBuffer(8))).rejects.toThrow("不支持音频播放");
  });

  it("新播放开始时停止并回收上一段未完成播放（与 speechSynthesis.cancel 同语义）", async () => {
    const env = stubAudioEnvironment();
    const first = playWavAudio(new ArrayBuffer(8));
    expect(env.created).toHaveLength(1);
    const second = playWavAudio(new ArrayBuffer(8));
    expect(env.created).toHaveLength(2);
    expect(env.created[0].pause).toHaveBeenCalledTimes(1); // 上一段被切断
    expect(env.revokeObjectURL).toHaveBeenCalledWith("blob:voice-1");
    env.created[1].emit("ended");
    await expect(second).resolves.toBeUndefined();
    // 第一段未决 promise 不再结算（被放弃的链不再发读题事件——不谎报完成）
    let settled = false;
    void first.then(
      () => {
        settled = true;
      },
      () => {
        settled = true;
      },
    );
    await Promise.resolve();
    expect(settled).toBe(false);
  });
});

// --- stopServerTtsPlayback：收尾停止 ---

describe("stopServerTtsPlayback", () => {
  it("停止当前播放（pause + 回收 URL）；无播放时 no-op", async () => {
    const env = stubAudioEnvironment();
    void playWavAudio(new ArrayBuffer(8));
    stopServerTtsPlayback();
    expect(env.created[0].pause).toHaveBeenCalledTimes(1);
    expect(env.revokeObjectURL).toHaveBeenCalledWith("blob:voice-1");
    expect(() => stopServerTtsPlayback()).not.toThrow(); // 幂等 no-op
  });
});

// --- speakWithServerTts：合成 + 播放组合链 ---

describe("speakWithServerTts：服务端 TTS 通道组合", () => {
  it("先合成后播放，播放真实完成才 resolve", async () => {
    const env = stubAudioEnvironment();
    const fetch = queueFetch([
      () => wavResponse({ "X-Voice-Provider": "local-cosyvoice", "X-Voice-Fallback": "0" }),
    ]);
    vi.stubGlobal("fetch", fetch);
    const pending = speakWithServerTts("第 1 题");
    await new Promise((resolve) => setTimeout(resolve, 0)); // 排空合成链微任务
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(env.created).toHaveLength(1);
    env.created[0].emit("ended");
    await expect(pending).resolves.toBeUndefined();
  });

  it("合成失败 → 零播放（Audio 从未构造），错误原样抛出", async () => {
    const env = stubAudioEnvironment();
    const fetch = queueFetch([() => jsonResponse({ detail: "语音服务暂不可用" }, 502)]);
    vi.stubGlobal("fetch", fetch);
    await expect(speakWithServerTts("第 1 题")).rejects.toMatchObject({ status: 502 });
    expect(env.created).toHaveLength(0);
    expect(env.createObjectURL).not.toHaveBeenCalled();
  });
});

// --- api.voiceProviders：providers 视图契约（GET /api/v1/voice/providers） ---

describe("api.voiceProviders：GET /api/v1/voice/providers", () => {
  it("GET（无 method 覆写、无 body）、响应 JSON 原样透传（snake_case 不改写）", async () => {
    const view = {
      voice_mode: "hybrid",
      asr: { requested: "local-funasr", provider: "local-funasr", fallback: false },
      tts: { requested: "cloud-openai-tts", provider: "cloud-openai-tts", fallback: false },
      privacy_store_audio: true,
      privacy_send_context_to_cloud: false,
    };
    const fetch = vi.fn().mockResolvedValue(jsonResponse(view));
    vi.stubGlobal("fetch", fetch);
    const result = await api.voiceProviders();
    const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE}/api/v1/voice/providers`);
    expect(init.method).toBeUndefined();
    expect(init.body).toBeUndefined();
    expect(result).toEqual(view);
  });
});

// --- M14-250 R1：服务端 TTS 通道零客户端 tts 埋点 ---
// tts 时延以服务端 /synthesize 内自动埋点（source=server）为唯一权威；
// 客户端朗读链（speakWithServerTts，voice-studio speakPhase 直连）
// 成功/失败均不得发出任何 /voice/trace 请求（避免同 stage 双计数）。

describe("R1：服务端 TTS 朗读只发 /synthesize，零 /voice/trace", () => {
  it("成功链恰一个请求（synthesize，body 恰 text），播放完成后仍零 trace", async () => {
    const env = stubAudioEnvironment();
    const fetch = queueFetch([
      () => wavResponse({ "X-Voice-Provider": "local-cosyvoice", "X-Voice-Fallback": "0" }),
    ]);
    vi.stubGlobal("fetch", fetch);
    const pending = speakWithServerTts("第 1 题，共 3 题。");
    await new Promise((resolve) => setTimeout(resolve, 0)); // 排空合成链微任务
    expect(urlsOf(fetch)).toEqual([SYNTHESIZE_URL]);
    expect(bodiesOf(fetch)[0]).toEqual({ text: "第 1 题，共 3 题。" });
    expect(env.created).toHaveLength(1);
    env.created[0].emit("ended");
    await pending;
    await new Promise((resolve) => setTimeout(resolve, 5)); // 排空任何迟到的 fire-and-forget 上报
    expect(fetch).toHaveBeenCalledTimes(1); // 仅 synthesize，零 trace
    expect(urlsOf(fetch)).toEqual([SYNTHESIZE_URL]);
  });

  it("合成失败同样零 trace（错误原样抛出，失败不构成样本也不双计数）", async () => {
    stubAudioEnvironment();
    const fetch = queueFetch([() => jsonResponse({ detail: "语音服务暂不可用" }, 502)]);
    vi.stubGlobal("fetch", fetch);
    await expect(speakWithServerTts("第 1 题")).rejects.toMatchObject({ status: 502 });
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(fetch).toHaveBeenCalledTimes(1); // 仅 synthesize，零 trace
    expect(urlsOf(fetch)).toEqual([SYNTHESIZE_URL]);
  });
});
