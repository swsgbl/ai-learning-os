// M14-251 TDD：浏览器录音 + WAV 编码模块（wav-recorder.ts）的行为
// 测试。stub navigator.mediaDevices.getUserMedia / MediaRecorder /
// AudioContext 实证：录音生命周期（start/手动停止/超时自动停止）、
// 资源四路恒清理（定时器、监听、音轨/流、AudioContext close）、
// WAV 头/格式（RIFF/WAVE、PCM、mono、16-bit、byteRate/blockAlign/
// data 长度、样本映射）、空录音拒绝、不支持 API / 麦克风被拒 /
// 设备缺失 / 解码失败 / 超限输出的诚实拒绝。不做源码字符串断言。
// vi.fn 不可 new——MediaRecorder/AudioContext 桩用普通构造函数 +
// created 数组追踪（与 server-tts.test.ts 的 Audio 桩同思路）。
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  MAX_WAV_BYTES,
  encodeWavMono16,
  recordAndEncodeWav,
  stopActiveRecording,
} from "./wav-recorder";

// --- 环境桩 ---

type StubTrack = { stop: ReturnType<typeof vi.fn> };

type StubMediaStream = {
  getTracks: () => StubTrack[];
};

type StubRecorder = {
  stream: StubMediaStream;
  mimeType: string;
  start: ReturnType<typeof vi.fn>;
  stop: ReturnType<typeof vi.fn>;
  ondataavailable: ((event: { data: Blob }) => void) | null;
  onstop: (() => void) | null;
  onerror: (() => void) | null;
  emitData: (blob: Blob) => void;
  emitStop: () => void;
  emitError: () => void;
};

type RecordingStub = {
  recorders: StubRecorder[];
  tracks: StubTrack[];
  getUserMedia: ReturnType<typeof vi.fn>;
  decodeAudioData: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
};

/** 装配全套录音环境桩：getUserMedia 默认放行单音轨流；MediaRecorder
 *  stop() 同步触发 onstop（真实浏览器为异步，行为等价）；AudioContext
 *  decodeAudioData 默认解析出单声道 fixture。 */
function stubRecorderEnvironment(
  options: { decoded?: AudioBuffer | "reject" } = {},
): RecordingStub {
  const recorders: StubRecorder[] = [];
  const tracks: StubTrack[] = [{ stop: vi.fn() }];
  const stream: StubMediaStream = { getTracks: () => tracks };
  const getUserMedia = vi.fn(async () => stream);
  const close = vi.fn(() => Promise.resolve());
  const decodeAudioData = vi.fn(() => {
    if (options.decoded === "reject") return Promise.reject(new Error("EncodingError"));
    return Promise.resolve(options.decoded ?? audioBufferFixture());
  });
  const MediaRecorderCtor = function (mediaStream: StubMediaStream) {
    const recorder: StubRecorder = {
      stream: mediaStream,
      mimeType: "audio/webm;codecs=opus",
      start: vi.fn(),
      stop: vi.fn(() => {
        recorder.emitStop();
      }),
      ondataavailable: null,
      onstop: null,
      onerror: null,
      emitData: (blob: Blob) => {
        recorder.ondataavailable?.({ data: blob });
      },
      emitStop: () => {
        recorder.onstop?.();
      },
      emitError: () => {
        recorder.onerror?.();
      },
    };
    recorders.push(recorder);
    return recorder;
  };
  const AudioContextCtor = function () {
    return { decodeAudioData, close };
  };
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
  vi.stubGlobal("MediaRecorder", MediaRecorderCtor);
  vi.stubGlobal("AudioContext", AudioContextCtor);
  return { recorders, tracks, getUserMedia, decodeAudioData, close };
}

/** AudioBuffer 桩：默认 16kHz 单声道 [0, 0.5, -0.5, 1, -1]。 */
function audioBufferFixture(
  options: { sampleRate?: number; channels?: Float32Array[] } = {},
): AudioBuffer {
  const channels = options.channels ?? [new Float32Array([0, 0.5, -0.5, 1, -1])];
  return {
    sampleRate: options.sampleRate ?? 16_000,
    numberOfChannels: channels.length,
    length: channels[0].length,
    getChannelData: (index: number) => channels[index],
  } as unknown as AudioBuffer;
}

/** 读取 Blob 字节（node Blob.arrayBuffer）。 */
async function bytesOf(blob: Blob): Promise<Uint8Array> {
  return new Uint8Array(await blob.arrayBuffer());
}

function ascii(bytes: Uint8Array, offset: number, length: number): string {
  return String.fromCharCode(...bytes.slice(offset, offset + length));
}

function readUint16(bytes: Uint8Array, offset: number): number {
  return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint16(offset, true);
}

function readUint32(bytes: Uint8Array, offset: number): number {
  return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint32(offset, true);
}

function readInt16(bytes: Uint8Array, offset: number): number {
  return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getInt16(offset, true);
}

async function flush(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- 录音生命周期：手动停止 / 超时 / 顺序 ---

describe("recordAndEncodeWav：录音生命周期", () => {
  it("手动停止：getUserMedia(audio-only) → 监听挂好后 start → stopActiveRecording 停止 → 非空 WAV", async () => {
    const stub = stubRecorderEnvironment();
    const pending = recordAndEncodeWav();
    await flush(); // getUserMedia 异步放行后构造 recorder
    expect(stub.getUserMedia).toHaveBeenCalledWith({ audio: true, video: false });
    const recorder = stub.recorders[0];
    expect(recorder.start).toHaveBeenCalledTimes(1);
    recorder.emitData(new Blob([new Uint8Array([1, 2, 3, 4])], { type: "audio/webm" }));
    stopActiveRecording();
    expect(recorder.stop).toHaveBeenCalledTimes(1);
    const wav = await pending;
    expect(wav.type).toBe("audio/wav");
    expect(wav.size).toBe(44 + 5 * 2); // 5 帧 fixture
  });

  it("无活跃录音时 stopActiveRecording 幂等 no-op（不抛错）", () => {
    expect(() => stopActiveRecording()).not.toThrow();
  });

  it("超时安全：maxDurationMs 到点自动停止（无需手动 stop），仍产出有效 WAV", async () => {
    const stub = stubRecorderEnvironment();
    const pending = recordAndEncodeWav({ maxDurationMs: 25 });
    await flush();
    const recorder = stub.recorders[0];
    recorder.emitData(new Blob([new Uint8Array([9])], { type: "audio/webm" }));
    await new Promise((resolve) => setTimeout(resolve, 60)); // 越过 25ms 上限
    expect(recorder.stop).toHaveBeenCalledTimes(1); // 定时器自动停止
    const wav = await pending;
    expect(wav.type).toBe("audio/wav");
    expect(wav.size).toBeGreaterThan(44); // 非空（含数据）
  });

  it("超长时长默认 60s 上限：maxDurationMs 缺省时 100ms 内绝不自动停止（默认值不缩水）", async () => {
    const stub = stubRecorderEnvironment();
    const pending = recordAndEncodeWav();
    await flush();
    const recorder = stub.recorders[0];
    recorder.emitData(new Blob([new Uint8Array([9])], { type: "audio/webm" }));
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(recorder.stop).not.toHaveBeenCalled(); // 60s 上限未到
    stopActiveRecording();
    await pending;
  });

  it("录音中设备错误（onerror）→ 如实 reject，资源照常清理", async () => {
    const stub = stubRecorderEnvironment();
    const pending = recordAndEncodeWav();
    await flush();
    const recorder = stub.recorders[0];
    recorder.emitError();
    await expect(pending).rejects.toThrow("录音过程中发生错误");
    expect(stub.tracks[0].stop).toHaveBeenCalledTimes(1);
  });
});

// --- 资源清理：四条路径恒清理（音轨/监听/定时器/AudioContext） ---

describe("资源清理（成功/失败/手动停止/超时一律回收）", () => {
  it("成功路径：音轨 stop、监听摘除、AudioContext close", async () => {
    const stub = stubRecorderEnvironment();
    const pending = recordAndEncodeWav();
    await flush();
    const recorder = stub.recorders[0];
    recorder.emitData(new Blob([new Uint8Array([1])], { type: "audio/webm" }));
    stopActiveRecording();
    await pending;
    expect(stub.tracks[0].stop).toHaveBeenCalledTimes(1);
    expect(recorder.ondataavailable).toBeNull();
    expect(recorder.onstop).toBeNull();
    expect(recorder.onerror).toBeNull();
    expect(stub.close).toHaveBeenCalledTimes(1);
  });

  it("解码失败路径：音轨与 AudioContext 仍恒清理", async () => {
    const stub = stubRecorderEnvironment({ decoded: "reject" });
    const pending = recordAndEncodeWav();
    await flush();
    const recorder = stub.recorders[0];
    recorder.emitData(new Blob([new Uint8Array([1])], { type: "audio/webm" }));
    stopActiveRecording();
    await expect(pending).rejects.toThrow("录音解码失败");
    expect(stub.tracks[0].stop).toHaveBeenCalledTimes(1);
    expect(stub.close).toHaveBeenCalledTimes(1);
  });

  it("getUserMedia 失败路径：无 recorder 构造、零音轨泄漏（流未建立）", async () => {
    const stub = stubRecorderEnvironment();
    stub.getUserMedia.mockRejectedValue(
      Object.assign(new Error("Permission denied"), { name: "NotAllowedError" }),
    );
    await expect(recordAndEncodeWav()).rejects.toThrow("未获得麦克风权限");
    expect(stub.recorders).toHaveLength(0);
    expect(stub.decodeAudioData).not.toHaveBeenCalled();
  });

  it("MediaRecorder 构造失败 → 已获得的音轨仍恒停止、无活跃录音残留、stopActiveRecording 幂等 no-op", async () => {
    const tracks: StubTrack[] = [{ stop: vi.fn() }];
    const getUserMedia = vi.fn(async () => ({ getTracks: () => tracks }));
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
    const MediaRecorderCtor = function () {
      throw new Error("NotSupportedError");
    };
    vi.stubGlobal("MediaRecorder", MediaRecorderCtor);
    await expect(recordAndEncodeWav()).rejects.toThrow("无法启动录音");
    expect(tracks[0].stop).toHaveBeenCalledTimes(1); // 构造失败也释放麦克风
    stopActiveRecording(); // 模块级活跃引用未残留（构造失败在赋值之前）
    expect(tracks[0].stop).toHaveBeenCalledTimes(1); // no-op 不再触碰
  });

  it("清理后模块级活跃引用复位：二次 stopActiveRecording 不触碰旧 recorder", async () => {
    const stub = stubRecorderEnvironment();
    const first = recordAndEncodeWav();
    await flush();
    const recorder = stub.recorders[0];
    recorder.emitData(new Blob([new Uint8Array([1])], { type: "audio/webm" }));
    stopActiveRecording();
    await first;
    expect(recorder.stop).toHaveBeenCalledTimes(1);
    stopActiveRecording(); // 复位后的 no-op
    expect(recorder.stop).toHaveBeenCalledTimes(1);
  });
});

// --- WAV 编码：头/格式/样本映射（RIFF/WAVE、PCM、mono、16-bit） ---

describe("WAV 编码格式", () => {
  it("RIFF/WAVE 头完整：fmt PCM(1)、mono(1)、16bit(16)、byteRate/blockAlign/data 长度一致", async () => {
    const sampleRate = 48_000;
    const stub = stubRecorderEnvironment({ decoded: audioBufferFixture({ sampleRate }) });
    const pending = recordAndEncodeWav();
    await flush();
    const recorder = stub.recorders[0];
    recorder.emitData(new Blob([new Uint8Array([1])], { type: "audio/webm" }));
    stopActiveRecording();
    const wav = await pending;
    const bytes = await bytesOf(wav);
    expect(ascii(bytes, 0, 4)).toBe("RIFF");
    expect(readUint32(bytes, 4)).toBe(36 + 5 * 2);
    expect(ascii(bytes, 8, 4)).toBe("WAVE");
    expect(ascii(bytes, 12, 4)).toBe("fmt ");
    expect(readUint32(bytes, 16)).toBe(16);
    expect(readUint16(bytes, 20)).toBe(1); // PCM
    expect(readUint16(bytes, 22)).toBe(1); // mono
    expect(readUint32(bytes, 24)).toBe(sampleRate);
    expect(readUint32(bytes, 28)).toBe(sampleRate * 2); // byteRate = rate × 1ch × 2B
    expect(readUint16(bytes, 32)).toBe(2); // blockAlign
    expect(readUint16(bytes, 34)).toBe(16); // bitsPerSample
    expect(ascii(bytes, 36, 4)).toBe("data");
    expect(readUint32(bytes, 40)).toBe(5 * 2);
    expect(bytes.length).toBe(44 + 5 * 2);
  });

  it("样本映射：0→0、0.5→+16383、-0.5→-16384、1→32767、-1→-32768（16-bit 小端）", async () => {
    const stub = stubRecorderEnvironment();
    const pending = recordAndEncodeWav();
    await flush();
    const recorder = stub.recorders[0];
    recorder.emitData(new Blob([new Uint8Array([1])], { type: "audio/webm" }));
    stopActiveRecording();
    const bytes = await bytesOf(await pending);
    expect(readInt16(bytes, 44)).toBe(0);
    expect(readInt16(bytes, 46)).toBe(Math.floor(0.5 * 0x7fff)); // 16383
    expect(readInt16(bytes, 48)).toBe(Math.ceil(-0.5 * 0x8000)); // -16384
    expect(readInt16(bytes, 50)).toBe(0x7fff);
    expect(readInt16(bytes, 52)).toBe(-0x8000);
  });

  it("多声道按均值混缩为 mono（双声道 [1,-1]/[-1,1] → 每帧 0）", async () => {
    const mixed = encodeWavMono16(
      audioBufferFixture({
        channels: [new Float32Array([1, -1]), new Float32Array([-1, 1])],
      }),
    );
    expect(readUint16(mixed, 22)).toBe(1); // mono
    expect(readInt16(mixed, 44)).toBe(0);
    expect(readInt16(mixed, 46)).toBe(0);
  });

  it("MediaRecorder 容器字节如实上送解码（arrayBuffer 透传，不重编码容器）", async () => {
    const containerBytes = new Uint8Array([1, 2, 3, 4]);
    const stub = stubRecorderEnvironment();
    const pending = recordAndEncodeWav();
    await flush();
    const recorder = stub.recorders[0];
    recorder.emitData(new Blob([containerBytes], { type: "audio/webm" }));
    stopActiveRecording();
    await pending;
    const decodedBytes = new Uint8Array(
      (stub.decodeAudioData.mock.calls[0][0] as ArrayBuffer),
    );
    expect(decodedBytes).toEqual(containerBytes);
  });
});

// --- 诚实拒绝：空录音 / 不支持 / 权限 / 设备 / 超限 ---

describe("诚实拒绝（不静默降级、不构造空 WAV）", () => {
  it("MediaRecorder 零 chunk（容器为空）→ 拒绝「没有录到声音」，不进入解码", async () => {
    const stub = stubRecorderEnvironment();
    const pending = recordAndEncodeWav();
    await flush();
    stopActiveRecording(); // 未 emitData 即停
    await expect(pending).rejects.toThrow("没有录到声音");
    expect(stub.decodeAudioData).not.toHaveBeenCalled();
  });

  it("解码结果零帧（44 字节纯头）→ 拒绝「没有录到声音」", async () => {
    const stub = stubRecorderEnvironment({ decoded: audioBufferFixture({ channels: [new Float32Array(0)] }) });
    const pending = recordAndEncodeWav();
    await flush();
    stub.recorders[0].emitData(new Blob([new Uint8Array([1])], { type: "audio/webm" }));
    stopActiveRecording();
    await expect(pending).rejects.toThrow("没有录到声音");
  });

  it("超限输出（> 20MB，与服务端 /transcribe 413 上限同口径）→ 出浏览器前拒绝", async () => {
    const overLimitFrames = Math.floor(MAX_WAV_BYTES / 2) + 8; // WAV = 44 + 2×frames > 20MB
    const stub = stubRecorderEnvironment({
      decoded: audioBufferFixture({ channels: [new Float32Array(overLimitFrames)] }),
    });
    const pending = recordAndEncodeWav();
    await flush();
    stub.recorders[0].emitData(new Blob([new Uint8Array([1])], { type: "audio/webm" }));
    stopActiveRecording();
    await expect(pending).rejects.toThrow("录音过长");
    expect(stub.close).toHaveBeenCalledTimes(1); // AudioContext 仍回收
  });

  it("浏览器不支持（无 MediaRecorder）→ 拒绝，不触碰 getUserMedia", async () => {
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: vi.fn() } });
    vi.stubGlobal("AudioContext", function () {
      return { decodeAudioData: vi.fn(), close: vi.fn(() => Promise.resolve()) };
    });
    await expect(recordAndEncodeWav()).rejects.toThrow("当前浏览器不支持麦克风录音");
  });

  it("无 mediaDevices（如非安全上下文/SSR）→ 拒绝", async () => {
    vi.stubGlobal("navigator", {});
    vi.stubGlobal("MediaRecorder", function () {
      return {};
    });
    await expect(recordAndEncodeWav()).rejects.toThrow("当前浏览器不支持麦克风录音");
  });

  it("麦克风权限被拒（NotAllowedError）→ 诚实文案", async () => {
    const stub = stubRecorderEnvironment();
    stub.getUserMedia.mockRejectedValue(new DOMException("Permission denied", "NotAllowedError"));
    await expect(recordAndEncodeWav()).rejects.toThrow("未获得麦克风权限");
  });

  it("无可用麦克风设备（NotFoundError）→ 诚实文案", async () => {
    const stub = stubRecorderEnvironment();
    stub.getUserMedia.mockRejectedValue(new DOMException("NotFound", "NotFoundError"));
    await expect(recordAndEncodeWav()).rejects.toThrow("未找到可用的麦克风设备");
  });

  it("解码引擎缺席（无 AudioContext）→ 拒绝且不泄漏音轨之外资源", async () => {
    const recorders: StubRecorder[] = [];
    const tracks: StubTrack[] = [{ stop: vi.fn() }];
    const MediaRecorderCtor = function (stream: StubMediaStream) {
      const recorder: StubRecorder = {
        stream,
        mimeType: "audio/webm",
        start: vi.fn(),
        stop: vi.fn(() => recorder.emitStop()),
        ondataavailable: null,
        onstop: null,
        onerror: null,
        emitData: (blob: Blob) => recorder.ondataavailable?.({ data: blob }),
        emitStop: () => recorder.onstop?.(),
        emitError: () => recorder.onerror?.(),
      };
      recorders.push(recorder);
      return recorder;
    };
    vi.stubGlobal("navigator", {
      mediaDevices: { getUserMedia: vi.fn(async () => ({ getTracks: () => tracks })) },
    });
    vi.stubGlobal("MediaRecorder", MediaRecorderCtor);
    const pending = recordAndEncodeWav();
    await flush();
    recorders[0].emitData(new Blob([new Uint8Array([1])], { type: "audio/webm" }));
    stopActiveRecording();
    await expect(pending).rejects.toThrow("当前环境不支持录音音频解码");
    expect(tracks[0].stop).toHaveBeenCalledTimes(1);
  });
});
