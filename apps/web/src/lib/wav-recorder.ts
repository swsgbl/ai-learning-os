// M14-251：浏览器录音 + 本地 WAV 编码模块（听写链的采集设备）。
// M14-252：拆出「外部提供流」入口 recordAndEncodeWavFromStream——
// LiveKit 已发布音轨的 utterance 采集走它，音轨所有权显式化
// （ownsTracks 缺省 false = 调用方拥有，一段 utterance 录完不停轨，
// LiveKit 控制器仍是音轨所有者）；recordAndEncodeWav 保持直接
// getUserMedia 路径并恒拥有/停止自己的音轨（M14-251 行为零改动）。
// 共用核心：MediaRecorder 采集 → Web Audio 解码容器 → 本地重编码
// RIFF/WAV（PCM、单声道、16-bit）。本模块是纯采集/编码设备：不解析
// 语音、不判定答案。资源诚实回收：超时定时器、recorder 监听、
// AudioContext 在成功/失败/手动停止/超时四条路径上一律清理；所拥有的
// 音轨恒停止，不拥有的（LiveKit）绝不越权停止。不支持 API、权限被拒、
// 设备缺失、零数据、解码失败、超限输出均如实 reject。
const DEFAULT_MAX_RECORDING_MS = 60_000;
// 与服务端 MAX_AUDIO_BYTES（20MB，/transcribe 413 上限）同口径——
// 超限的 WAV 不出浏览器，避免构造注定 413 的请求。
export const MAX_WAV_BYTES = 20 * 1024 * 1024;

type MediaRecorderConstructor = new (stream: MediaStream) => MediaRecorder;

export type WavRecordingOptions = {
  /** 最大录音时长（毫秒），到点自动停止并产出有效 WAV；默认 60s。 */
  maxDurationMs?: number;
};

/** 外部提供流的录音选项：音轨所有权显式化。
 *  ownsTracks 缺省 false = 音轨属调用方（如 LiveKit 已发布音轨），
 *  录音结束（成功/失败/手动停止/超时）不停轨——所有者负责生命周期；
 *  true = 本次调用拥有音轨，结束恒停止（与直接 getUserMedia 路径
 *  同语义）。 */
export type SuppliedStreamRecordingOptions = WavRecordingOptions & {
  ownsTracks?: boolean;
};

// 模块级当前录音占用：供 stopActiveRecording 手动停止（麦克风按钮
// 在录音期间复用为「停止并提交」）。同一时刻至多一段录音——直接路径
// 与提供流路径共用同一占用面。
let activeRecording: { requestStop: () => void } | null = null;

/** 请求停止当前活跃录音（幂等 no-op）：录音照常产出 WAV 并 resolve，
 *  调用方在 recordAndEncodeWav(FromStream) 的 promise 上继续转写
 *  提交链。无活跃录音时不做任何事。 */
export function stopActiveRecording(): void {
  activeRecording?.requestStop();
}

function requireRecorderSupport(): MediaRecorderConstructor {
  const RecorderCtor = typeof MediaRecorder === "undefined" ? undefined : MediaRecorder;
  if (typeof RecorderCtor !== "function") {
    throw new Error("当前浏览器不支持麦克风录音");
  }
  return RecorderCtor as MediaRecorderConstructor;
}

function requireRecordingSupport(): {
  getUserMedia: (constraints: MediaStreamConstraints) => Promise<MediaStream>;
  Recorder: MediaRecorderConstructor;
} {
  const mediaDevices = typeof navigator === "undefined" ? undefined : navigator.mediaDevices;
  if (typeof mediaDevices?.getUserMedia !== "function") {
    throw new Error("当前浏览器不支持麦克风录音");
  }
  return {
    getUserMedia: (constraints: MediaStreamConstraints) => mediaDevices.getUserMedia(constraints),
    Recorder: requireRecorderSupport(),
  };
}

function errorName(cause: unknown): string {
  return cause instanceof Error && typeof cause.name === "string" ? cause.name : "";
}

/** 麦克风访问失败 → 诚实的用户可读错误（不吞权限事实、不编造原因）。 */
function micAccessError(cause: unknown): Error {
  const name = errorName(cause);
  if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") {
    return new Error("未获得麦克风权限，请在浏览器中允许麦克风后重试");
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError") {
    return new Error("未找到可用的麦克风设备");
  }
  return new Error(cause instanceof Error ? `无法访问麦克风：${cause.message}` : "无法访问麦克风");
}

/** AudioBuffer → RIFF/WAV 字节（PCM、混缩单声道、16-bit 小端）。
 *  多声道按均值混缩为 mono；样本 clamp 到 [-1, 1] 后线性映射
 *  int16。纯函数，不触碰任何设备资源。 */
export function encodeWavMono16(buffer: AudioBuffer): Uint8Array<ArrayBuffer> {
  const channels = buffer.numberOfChannels;
  const frames = buffer.length;
  const mono = new Float32Array(frames);
  for (let channel = 0; channel < channels; channel += 1) {
    const data = buffer.getChannelData(channel);
    for (let index = 0; index < frames; index += 1) mono[index] += data[index];
  }
  if (channels > 1) {
    for (let index = 0; index < frames; index += 1) mono[index] /= channels;
  }
  const dataBytes = frames * 2;
  const bytes = new Uint8Array(44 + dataBytes);
  const view = new DataView(bytes.buffer);
  const writeAscii = (offset: number, text: string) => {
    for (let index = 0; index < text.length; index += 1) {
      view.setUint8(offset + index, text.charCodeAt(index));
    }
  };
  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + dataBytes, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true); // fmt chunk 恒 16 字节（PCM）
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, buffer.sampleRate, true);
  view.setUint32(28, buffer.sampleRate * 2, true); // byteRate = rate × 1ch × 2B
  view.setUint16(32, 2, true); // blockAlign
  view.setUint16(34, 16, true); // bitsPerSample
  writeAscii(36, "data");
  view.setUint32(40, dataBytes, true);
  let offset = 44;
  for (let index = 0; index < frames; index += 1) {
    const sample = Math.max(-1, Math.min(1, mono[index]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += 2;
  }
  return bytes;
}

/** 浏览器容器 Blob → WAV Blob：Web Audio 解码 + 本地重编码。空数据、
 *  解码能力缺失、解码失败、超限输出如实 reject。AudioContext 无论
 *  成败恒 close。 */
async function decodeToWavBlob(container: Blob): Promise<Blob> {
  const AudioContextCtor = typeof AudioContext === "undefined" ? undefined : AudioContext;
  if (typeof AudioContextCtor !== "function") {
    throw new Error("当前环境不支持录音音频解码");
  }
  const context = new AudioContextCtor();
  try {
    let decoded: AudioBuffer;
    try {
      decoded = await context.decodeAudioData(await container.arrayBuffer());
    } catch {
      // MediaRecorder 容器解码失败（损坏/截断/未知编码）——如实拒绝
      throw new Error("录音解码失败");
    }
    const wav = encodeWavMono16(decoded);
    if (wav.byteLength <= 44) {
      // 零帧数据（44 = 纯 header）＝没录到任何声音，不构造空 WAV 冒充
      throw new Error("没有录到声音");
    }
    if (wav.byteLength > MAX_WAV_BYTES) {
      throw new Error("录音过长，超过可上传上限");
    }
    return new Blob([wav], { type: "audio/wav" });
  } finally {
    void context.close().catch(() => {});
  }
}

/** 录音核心（直接路径与提供流路径共用）：对既有流启动 MediaRecorder，
 *  到时或手动停止后解码编码为 WAV。ownsTracks 决定结束时是否停止
 *  流上全部音轨（true = 本次调用拥有）。定时器/监听/AudioContext
 *  四路恒清理；构造失败释放已拥有的资源（supervisor 修正 1 保持）。 */
async function recordStreamToWav(
  stream: MediaStream,
  Recorder: MediaRecorderConstructor,
  maxDurationMs: number,
  ownsTracks: boolean,
): Promise<Blob> {
  const chunks: Blob[] = [];
  let recorder: MediaRecorder;
  try {
    recorder = new Recorder(stream);
  } catch (cause) {
    // MediaRecorder 构造失败（如流的编码不受支持）——本次调用拥有的
    // 音轨必须立即释放，不泄漏麦克风占用；调用方拥有的音轨不越权停止
    if (ownsTracks) {
      for (const track of stream.getTracks()) track.stop();
    }
    throw new Error(`无法启动录音：${cause instanceof Error ? cause.message : "录音设备初始化失败"}`);
  }
  const stopped = new Promise<void>((resolve, reject) => {
    recorder.ondataavailable = (event: BlobEvent) => {
      if (event.data.size > 0) chunks.push(event.data);
    };
    recorder.onstop = () => {
      resolve();
    };
    recorder.onerror = () => {
      reject(new Error("录音过程中发生错误"));
    };
  });
  let timeoutId: ReturnType<typeof setTimeout> | null = null;
  const stopOnce = () => {
    try {
      recorder.stop();
    } catch {
      // 已停止的 recorder 再 stop 会抛 InvalidStateError——幂等吞掉
    }
  };
  activeRecording = { requestStop: stopOnce };
  try {
    recorder.start();
    timeoutId = setTimeout(stopOnce, maxDurationMs);
    await stopped;
  } finally {
    if (timeoutId !== null) clearTimeout(timeoutId);
    recorder.ondataavailable = null;
    recorder.onstop = null;
    recorder.onerror = null;
    activeRecording = null;
    if (ownsTracks) {
      for (const track of stream.getTracks()) track.stop();
    }
  }
  const container = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
  if (container.size === 0) {
    // MediaRecorder 无任何 chunk（如瞬间停止）：如实拒绝，不构造空 WAV
    throw new Error("没有录到声音");
  }
  return decodeToWavBlob(container);
}

/** 录一段麦克风音频并本地编码为非空 WAV（PCM/mono/16-bit）：getUserMedia
 *  → MediaRecorder → 到时或手动停止 → 容器 Blob → Web Audio 解码 →
 *  WAV。本路径拥有音轨：成功、失败、手动停止、超时四条路径都停止
 *  全部音轨并清理定时器/监听/AudioContext（M14-251 行为零改动）。 */
export async function recordAndEncodeWav(options: WavRecordingOptions = {}): Promise<Blob> {
  const maxDurationMs = options.maxDurationMs ?? DEFAULT_MAX_RECORDING_MS;
  const { getUserMedia, Recorder } = requireRecordingSupport();
  let stream: MediaStream;
  try {
    stream = await getUserMedia({ audio: true, video: false });
  } catch (cause) {
    throw micAccessError(cause);
  }
  return recordStreamToWav(stream, Recorder, maxDurationMs, true);
}

/** M14-252：对外部提供的流录音并编码为 WAV（不触碰 getUserMedia）。
 *  LiveKit 已发布音轨的 utterance 采集入口：音轨所有权缺省归调用方
 *  （ownsTracks=false，录音结束不停轨——LiveKit 控制器仍是所有者），
 *  ownsTracks=true 时本调用拥有并恒停止。60s 默认上限、20MB 上限、
 *  PCM/mono/16-bit 编码与全部诚实错误行为与直接路径一致；
 *  stopActiveRecording 对本入口同样生效（同一模块级占用面）。 */
export async function recordAndEncodeWavFromStream(
  stream: MediaStream,
  options: SuppliedStreamRecordingOptions = {},
): Promise<Blob> {
  const maxDurationMs = options.maxDurationMs ?? DEFAULT_MAX_RECORDING_MS;
  const Recorder = requireRecorderSupport();
  return recordStreamToWav(stream, Recorder, maxDurationMs, options.ownsTracks ?? false);
}
