// M14-252 TDD：LiveKit 语音输入控制器（livekit-voice-input.ts）的
// 行为测试。stub 全局 fetch（token 契约）+ 注入 createRoom/
// createLocalAudioTrack（单元测试注入点——无需真实 LiveKit 服务）+
// stub MediaRecorder/AudioContext/MediaStream（recordWav 走真实
// recordAndEncodeWavFromStream）。实证：session 绑定的确定性房间名
// 与服务端 URL-safe 契约、token 只作 connect 局部参数（state/错误零
// 泄漏）、token→connect→local track→publish 顺序与单轨复用、record
// 用已发布轨的流不再 republish、token/connect/mic/publish/超时失败
// 不留活麦克风或连接房间、后续断连失效控制器（不静默复用）、
// disconnect 幂等收尾恰一次。
import { afterEach, describe, expect, it, vi } from "vitest";

import { API_BASE } from "./api";
import { stopActiveRecording } from "./wav-recorder";
import {
  connectVoiceInput,
  getOrCreateVoiceInput,
  voiceRoomName,
  VOICE_ROOM_PREFIX,
  type LiveKitInputDeps,
  type LiveKitRoomLike,
  type VoiceInputTrack,
} from "./livekit-voice-input";

// JWT 形态样本（结构与真实 LiveKit JWT 同形——测试专用假值）
const FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig";
const SESSION_ID = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b";

// --- 环境桩 ---

type StubRoom = {
  state: string;
  connect: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
  on: ReturnType<typeof vi.fn>;
  localParticipant: { publishTrack: ReturnType<typeof vi.fn> };
  emitDisconnected: () => void;
  handlers: Map<string, Array<() => void>>;
};

type StubTrack = { mediaStreamTrack: { stop: ReturnType<typeof vi.fn>; readyState: string } };

type LiveKitStub = {
  room: StubRoom;
  track: StubTrack;
  events: string[];
  deps: LiveKitInputDeps;
  fetch: ReturnType<typeof vi.fn>;
  recorders: Array<{
    stream: unknown;
    start: () => void;
    stop: () => void;
    ondataavailable: ((event: { data: Blob }) => void) | null;
    onstop: (() => void) | null;
    onerror: (() => void) | null;
  }>;
};

function tokenResponse(): Response {
  return new Response(
    JSON.stringify({
      token: FAKE_JWT,
      room: `${VOICE_ROOM_PREFIX}${SESSION_ID}`,
      identity: "student-1",
      role: "student",
      expires_at: "2026-10-09T12:00:00Z",
      ws_url: "wss://livekit.example.com/rtc",
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

/** 装配 LiveKit 输入桩：room.connect 成功置 connected；disconnect 同步
 *  触发 disconnected 处理器；MediaRecorder/AudioContext/MediaStream
 *  桩支撑 recordWav 走真实 WAV 模块。 */
function stubLiveKitEnvironment(options: { token?: () => Response } = {}): LiveKitStub {
  const events: string[] = [];
  const handlers = new Map<string, Array<() => void>>();
  const room: StubRoom = {
    state: "disconnected",
    connect: vi.fn(async () => {
      events.push("connect");
      room.state = "connected";
    }),
    disconnect: vi.fn(() => {
      events.push("disconnect");
      room.state = "disconnected";
      for (const handler of handlers.get("disconnected") ?? []) handler();
    }),
    on: vi.fn((event: string, handler: () => void) => {
      const bucket = handlers.get(event) ?? [];
      bucket.push(handler);
      handlers.set(event, bucket);
    }),
    localParticipant: {
      publishTrack: vi.fn(async () => {
        events.push("publish");
        return {};
      }),
    },
    emitDisconnected: () => {
      for (const handler of handlers.get("disconnected") ?? []) handler();
    },
    handlers,
  };
  const track: StubTrack = {
    mediaStreamTrack: { stop: vi.fn(), readyState: "live" },
  };
  const deps: LiveKitInputDeps = {
    createRoom: () => {
      events.push("createRoom");
      return room as unknown as LiveKitRoomLike;
    },
    createLocalAudioTrack: async () => {
      events.push("localTrack");
      return track as unknown as VoiceInputTrack;
    },
  };
  const fetch = vi.fn(async () => {
    events.push("token");
    return (options.token ?? tokenResponse)();
  });
  vi.stubGlobal("fetch", fetch);
  // recordWav → recordAndEncodeWavFromStream 的设备桩（MediaStream 装轨）
  const recordedStreams: MediaStream[] = [];
  const MediaStreamCtor = function (tracks: MediaStreamTrack[]) {
    const stream = { getTracks: () => tracks } as unknown as MediaStream;
    recordedStreams.push(stream);
    return stream;
  };
  const recorders: LiveKitStub["recorders"] = [];
  const MediaRecorderCtor = function (stream: MediaStream) {
    const recorder: LiveKitStub["recorders"][number] = {
      stream,
      start: () => undefined,
      stop: () => recorder.onstop?.(),
      ondataavailable: null,
      onstop: null,
      onerror: null,
    };
    recorders.push(recorder);
    return recorder;
  };
  vi.stubGlobal("MediaStream", MediaStreamCtor);
  vi.stubGlobal("MediaRecorder", MediaRecorderCtor);
  vi.stubGlobal("AudioContext", function () {
    return {
      decodeAudioData: vi.fn(async () => ({
        sampleRate: 16_000,
        numberOfChannels: 1,
        length: 2,
        getChannelData: () => new Float32Array([0.5, -0.5]),
      })),
      close: vi.fn(async () => undefined),
    };
  });
  return { room, track, events, deps, fetch, recorders };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

// --- 房间名：session 绑定 + 服务端 URL-safe 契约 ---

describe("voiceRoomName：session 绑定的确定性房间名", () => {
  it("确定性：同一 session_id 产出同一房间名（voice-<id>），落在服务端 ^[A-Za-z0-9_-]{3,64}$ 契约内", () => {
    const first = voiceRoomName(SESSION_ID);
    const second = voiceRoomName(SESSION_ID);
    expect(first).toBe(`${VOICE_ROOM_PREFIX}${SESSION_ID}`);
    expect(second).toBe(first);
    expect(first).toMatch(/^[A-Za-z0-9_-]{3,64}$/);
  });

  it("不同 session 产出不同房间名（权威会话互不串音）", () => {
    expect(voiceRoomName("aaa-111")).not.toBe(voiceRoomName("bbb-222"));
  });

  it("不安全 id（/ ? # 空格、空串、超长）→ null（拒绝请求 token，不构造注定 422 的请求）", () => {
    expect(voiceRoomName("sess/1")).toBeNull();
    expect(voiceRoomName("sess?x=1")).toBeNull();
    expect(voiceRoomName("sess#frag")).toBeNull();
    expect(voiceRoomName("sess 1")).toBeNull();
    expect(voiceRoomName("")).toBeNull();
    expect(voiceRoomName("x".repeat(80))).toBeNull();
  });
});

// --- connectVoiceInput：顺序 / token 安全 / 单轨 ---

describe("connectVoiceInput：token → connect → local track → publish", () => {
  it("顺序实证：token → connect → publish 前置 localTrack；单轨复用（第二次 recordWav 零 token/connect/publish）", async () => {
    const stub = stubLiveKitEnvironment();
    const controller = await connectVoiceInput(SESSION_ID, stub.deps);
    expect(stub.events).toEqual(["token", "createRoom", "connect", "localTrack", "publish"]);
    // token 只作 connect 参数：connect 收到 JWT 原文（局部变量），state 零 token
    expect(stub.room.connect).toHaveBeenCalledWith("wss://livekit.example.com/rtc", FAKE_JWT);
    expect(controller.state()).toMatchObject({
      status: "connected",
      room: `${VOICE_ROOM_PREFIX}${SESSION_ID}`,
      identity: "student-1",
    });
    expect(JSON.stringify(controller.state())).not.toContain("eyJ");
    // 第二次 utterance：复用同一轨，零新 token/connect/publish
    const pending = controller.recordWav();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(stub.fetch).toHaveBeenCalledTimes(1);
    expect(stub.room.connect).toHaveBeenCalledTimes(1);
    expect(stub.room.localParticipant.publishTrack).toHaveBeenCalledTimes(1);
    stub.recorders[0].ondataavailable?.({ data: new Blob([new Uint8Array([1])]) });
    stopActiveRecording();
    await expect(pending).resolves.toBeInstanceOf(Blob);
  });

  it("recordWav 用已发布轨的 mediaStreamTrack 构造流（音轨归 LiveKit，utterance 完成不停轨）", async () => {
    const stub = stubLiveKitEnvironment();
    const controller = await connectVoiceInput(SESSION_ID, stub.deps);
    const pending = controller.recordWav();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(stub.track.mediaStreamTrack.stop).not.toHaveBeenCalled();
    stub.recorders[0].ondataavailable?.({ data: new Blob([new Uint8Array([1])]) });
    stopActiveRecording();
    await pending;
    expect(stub.track.mediaStreamTrack.stop).not.toHaveBeenCalled(); // 轨仍归控制器
    expect(controller.state().status).toBe("connected"); // 房间仍在
  });

  it("token 请求体恰 { room }（POST /api/v1/voice/token，session 绑定房间）", async () => {
    const stub = stubLiveKitEnvironment();
    await connectVoiceInput(SESSION_ID, stub.deps);
    const [url, init] = stub.fetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE}/api/v1/voice/token`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ room: `${VOICE_ROOM_PREFIX}${SESSION_ID}` });
  });

  it("不安全 session_id → 零网络零房间直接拒绝", async () => {
    const stub = stubLiveKitEnvironment();
    await expect(connectVoiceInput("sess/1", stub.deps)).rejects.toThrow("无效的语音会话标识");
    expect(stub.fetch).not.toHaveBeenCalled();
    expect(stub.events).toEqual([]);
  });
});

// --- 失败路径：不留活麦克风、不留连接房间、错误脱敏 ---

describe("失败路径：token/connect/mic/publish/超时", () => {
  it("token 失败（503）：零房间零麦克风，ApiError 语义透出", async () => {
    const stub = stubLiveKitEnvironment({
      token: () => new Response(JSON.stringify({ detail: "Voice requires LiveKit configuration" }), { status: 503 }),
    });
    await expect(connectVoiceInput(SESSION_ID, stub.deps)).rejects.toThrow("Voice requires LiveKit configuration");
    expect(stub.events).toEqual(["token"]); // 未建房间未开麦克风
  });

  it("connect 失败：不留连接房间、零麦克风泄漏（localTrack 未发生）", async () => {
    const stub = stubLiveKitEnvironment();
    stub.room.connect.mockRejectedValue(new Error("connection error"));
    await expect(connectVoiceInput(SESSION_ID, stub.deps)).rejects.toThrow("connection error");
    expect(stub.room.connect).toHaveBeenCalledTimes(1); // 尝试过连接
    expect(stub.events).not.toContain("localTrack"); // 麦克风从未开启
    expect(stub.room.disconnect).not.toHaveBeenCalled(); // 房间从未连上，无需断开
    expect(stub.track.mediaStreamTrack.stop).not.toHaveBeenCalled(); // 轨从未创建
  });

  it("connect 失败的错误消息经脱敏（JWT 形态替换，绝不进错误面）", async () => {
    const stub = stubLiveKitEnvironment();
    stub.room.connect.mockRejectedValue(new Error(`handshake failed: ${FAKE_JWT}`));
    const cause = await connectVoiceInput(SESSION_ID, stub.deps).then(
      () => {
        throw new Error("应 reject");
      },
      (error: Error) => error,
    );
    expect(cause.message).not.toContain(FAKE_JWT);
    expect(cause.message).toContain("[已脱敏]");
  });

  it("local track 获取失败：断开已连接房间（不留连接）、错误如实", async () => {
    const stub = stubLiveKitEnvironment();
    stub.deps.createLocalAudioTrack = async () => {
      stub.events.push("localTrack");
      throw new Error("NotAllowedError");
    };
    await expect(connectVoiceInput(SESSION_ID, stub.deps)).rejects.toThrow();
    expect(stub.events).toEqual(["token", "createRoom", "connect", "localTrack", "disconnect"]);
  });

  it("publish 失败：停止麦克风 + 断开房间（无活轨无连接房间残留）", async () => {
    const stub = stubLiveKitEnvironment();
    stub.room.localParticipant.publishTrack.mockRejectedValue(new Error("publish rejected"));
    await expect(connectVoiceInput(SESSION_ID, stub.deps)).rejects.toThrow("publish");
    expect(stub.room.localParticipant.publishTrack).toHaveBeenCalledTimes(1); // 尝试过发布
    expect(stub.events).toContain("localTrack"); // 麦克风已开过
    expect(stub.events).toContain("disconnect"); // 房间已断开
    expect(stub.track.mediaStreamTrack.stop).toHaveBeenCalledTimes(1); // 麦克风已停止
  });

  it("token 永挂 → 超时拒绝（不无限等待），零房间零麦克风", async () => {
    vi.useFakeTimers();
    const stub = stubLiveKitEnvironment();
    stub.fetch.mockImplementation(
      () => new Promise(() => undefined), // 永不 resolve
    );
    const pending = connectVoiceInput(SESSION_ID, stub.deps);
    const assertion = expect(pending).rejects.toThrow("超时");
    await vi.advanceTimersByTimeAsync(10_001);
    await assertion;
    expect(stub.fetch).toHaveBeenCalledTimes(1); // 尝试过取 token
    expect(stub.events).toEqual([]); // 零房间零麦克风（token 未返回）
  });
});

// --- 断连失效与幂等收尾 ---

describe("断连失效与 disconnect 收尾", () => {
  it("后续断连（disconnected 事件）→ 控制器失效：recordWav 拒绝，不静默复用 stale 控制器", async () => {
    const stub = stubLiveKitEnvironment();
    const controller = await connectVoiceInput(SESSION_ID, stub.deps);
    stub.room.emitDisconnected();
    expect(controller.state().status).toBe("disconnected");
    await expect(controller.recordWav()).rejects.toThrow("语音连接已断开");
  });

  it("音轨 ended（设备拔出）→ 控制器失效：recordWav 拒绝重连提示", async () => {
    const stub = stubLiveKitEnvironment();
    const controller = await connectVoiceInput(SESSION_ID, stub.deps);
    stub.track.mediaStreamTrack.readyState = "ended";
    await expect(controller.recordWav()).rejects.toThrow("语音连接已断开");
  });

  it("disconnect 幂等：麦克风停止恰一次、房间断开恰一次；二次 no-op；state=disconnected", async () => {
    const stub = stubLiveKitEnvironment();
    const controller = await connectVoiceInput(SESSION_ID, stub.deps);
    await controller.disconnect();
    await controller.disconnect(); // 幂等
    await controller.disconnect();
    expect(stub.track.mediaStreamTrack.stop).toHaveBeenCalledTimes(1);
    expect(stub.room.disconnect).toHaveBeenCalledTimes(1);
    expect(controller.state().status).toBe("disconnected");
    await expect(controller.recordWav()).rejects.toThrow("语音连接已断开");
  });

  it("getOrCreateVoiceInput：existing connected → 原样复用零新请求", async () => {
    const stub = stubLiveKitEnvironment();
    const first = await connectVoiceInput(SESSION_ID, stub.deps);
    const reused = await getOrCreateVoiceInput(first, SESSION_ID, stub.deps);
    expect(reused).toBe(first);
    expect(stub.fetch).toHaveBeenCalledTimes(1);
  });

  it("getOrCreateVoiceInput：existing 断开 → 收尾旧控制器并显式新建（不静默复用）", async () => {
    const stub = stubLiveKitEnvironment();
    const first = await connectVoiceInput(SESSION_ID, stub.deps);
    stub.room.emitDisconnected();
    const second = await getOrCreateVoiceInput(first, SESSION_ID, stub.deps);
    expect(second).not.toBe(first);
    expect(stub.track.mediaStreamTrack.stop).toHaveBeenCalledTimes(1); // 旧轨收尾恰一次
    expect(stub.fetch).toHaveBeenCalledTimes(2); // 新连接显式重新取 token
    expect(second.state().status).toBe("connected");
  });

  it("getOrCreateVoiceInput：null → 直接新建", async () => {
    const stub = stubLiveKitEnvironment();
    const controller = await getOrCreateVoiceInput(null, SESSION_ID, stub.deps);
    expect(controller.state().status).toBe("connected");
    expect(stub.fetch).toHaveBeenCalledTimes(1);
  });
});
