// M14-252：VoiceStudio 的 LiveKit 管理语音输入控制器。
// 首次语音作答时懒连接（不在页面加载时、不在 render/effect 驱动的
// 自动麦克风请求中）：session 绑定的确定性房间名 → api.voiceToken
// → Room.connect → 单条本地音频轨 publish → 返回小控制器
// （recordWav / state / disconnect）。房间与音轨跨题目复用；每段
// utterance 用已发布轨的 mediaStreamTrack 走
// recordAndEncodeWavFromStream（音轨归本控制器所有，utterance 结束
// 不停轨），WAV 继续上送既有 POST /api/v1/voice/transcribe——本模块
// 是纯输入传输层：不做服务端订阅、流式 ASR、VAD、barge-in、
// pause/resume。token 是 connect 的局部参数：绝不进入控制器 state、
// DOM、日志或错误消息（sanitizeError 脱敏 + 200 字符截断）。失败
// （token/connect/mic/publish/超时）不留活麦克风或连接房间；一旦
// 断连控制器失效，不静默复用——显式新连接（getOrCreateVoiceInput）。
// disconnect 幂等且安全用于 finish/unmount 收尾。createRoom/
// createLocalAudioTrack 为单元测试注入点（无需真实 LiveKit 服务）。
import { ConnectionState, Room, RoomEvent, createLocalAudioTrack } from "livekit-client";

import { api } from "./api";
import { CONNECT_TIMEOUT_MS, MIC_TIMEOUT_MS, TOKEN_TIMEOUT_MS, sanitizeError, withTimeout } from "./livekit-check";
import { recordAndEncodeWavFromStream } from "./wav-recorder";

export const VOICE_ROOM_PREFIX = "voice-";
// 服务端 voice.py _ROOM_RE 契约：^[A-Za-z0-9_-]{3,64}$
const ROOM_CONTRACT_RE = /^[A-Za-z0-9_-]{3,64}$/;
// session_id 允许的字符（URL 安全子集；超长拒绝——room 前缀后仍在契约内）
const SESSION_ID_RE = /^[A-Za-z0-9_-]+$/;
const MAX_SESSION_ID_LENGTH = 64 - VOICE_ROOM_PREFIX.length;

/** session_id → 确定性房间名（voice-<session_id>），请求 token 前先
 *  对照服务端 URL-safe 契约校验；不安全 id 返回 null（调用方如实
 *  拒绝，不构造注定 422 的请求）。纯函数。 */
export function voiceRoomName(sessionId: string): string | null {
  if (!SESSION_ID_RE.test(sessionId) || sessionId.length > MAX_SESSION_ID_LENGTH) return null;
  const room = `${VOICE_ROOM_PREFIX}${sessionId}`;
  return ROOM_CONTRACT_RE.test(room) ? room : null;
}

/** 本控制器依赖的最小 Room 面（livekit-client Room 的结构子集——
 *  单元测试注入 stub 时只需实现这些成员）。 */
export type LiveKitRoomLike = {
  connect: (wsUrl: string, token: string) => Promise<void>;
  disconnect: () => void | Promise<void>;
  state: string;
  on: (event: string, handler: () => void) => void;
  localParticipant: {
    publishTrack: (track: unknown) => Promise<unknown>;
  };
};

/** 本控制器依赖的最小本地音轨面（LocalAudioTrack 的结构子集）。 */
export type VoiceInputTrack = {
  mediaStreamTrack: MediaStreamTrack;
};

/** 单元测试注入点：默认实现直连 livekit-client SDK。 */
export type LiveKitInputDeps = {
  createRoom: () => LiveKitRoomLike;
  createLocalAudioTrack: () => Promise<VoiceInputTrack>;
};

const defaultDeps: LiveKitInputDeps = {
  createRoom: () => new Room() as unknown as LiveKitRoomLike,
  createLocalAudioTrack: async () => (await createLocalAudioTrack()) as unknown as VoiceInputTrack,
};

export type LiveKitVoiceInputStatus = "connected" | "disconnected";

export type LiveKitVoiceInputState = {
  status: LiveKitVoiceInputStatus;
  room: string | null;
  identity: string | null;
  error: string | null;
};

export type LiveKitVoiceInput = {
  /** 从已发布轨录一段 utterance 并本地编码 WAV（音轨不停——归控制器）。 */
  recordWav: () => Promise<Blob>;
  /** 只读状态投影（不含 token——凭据只存在于 connect 局部变量）。 */
  state: () => LiveKitVoiceInputState;
  /** 幂等收尾：停止本地麦克风 + 断开房间；安全用于 finish/unmount。 */
  disconnect: () => Promise<void>;
};

/** 懒连接一次 LiveKit 语音输入（token → connect → local track →
 *  publish）。任何失败都先释放已获得的资源（不留活麦克风、不留
 *  连接房间）再抛脱敏错误；token 永不进入错误面。 */
export async function connectVoiceInput(
  sessionId: string,
  deps: LiveKitInputDeps = defaultDeps,
): Promise<LiveKitVoiceInput> {
  const room = voiceRoomName(sessionId);
  if (room === null) {
    throw new Error("无效的语音会话标识，无法建立语音房间");
  }
  // token 只进局部变量：作为 connect 参数后即不可达
  const granted = await withTimeout(api.voiceToken(room), TOKEN_TIMEOUT_MS, "获取语音房间凭据");
  const live = deps.createRoom();
  let track: VoiceInputTrack | null = null;
  try {
    await withTimeout(live.connect(granted.ws_url, granted.token), CONNECT_TIMEOUT_MS, "连接语音房间");
    track = await withTimeout(deps.createLocalAudioTrack(), MIC_TIMEOUT_MS, "获取麦克风");
    await withTimeout(live.localParticipant.publishTrack(track), MIC_TIMEOUT_MS, "发布麦克风音轨");
  } catch (cause) {
    // 失败清理：已获得的麦克风停止、已连接的房间断开——不留活资源
    if (track !== null) track.mediaStreamTrack.stop();
    if (live.state !== ConnectionState.Disconnected) {
      try {
        await live.disconnect();
      } catch {
        // 清理尽力而为：清理失败不掩盖原始错误
      }
    }
    throw new Error(sanitizeError(cause));
  }
  const state: LiveKitVoiceInputState = {
    status: "connected",
    room,
    identity: granted.identity,
    error: null,
  };
  const invalidate = () => {
    state.status = "disconnected";
  };
  live.on(RoomEvent.Disconnected, invalidate);
  let closed = false;
  const ensureReleased = () => {
    if (closed) return;
    closed = true;
    state.status = "disconnected";
    try {
      track?.mediaStreamTrack.stop();
    } catch {
      // 收尾尽力而为
    }
  };
  return {
    recordWav: async () => {
      const usable = state.status === "connected" && track?.mediaStreamTrack.readyState === "live";
      if (!track || !usable) {
        // 已断连/轨已结束：如实拒绝并要求重连，绝不静默复用 stale 控制器
        throw new Error("语音连接已断开，请重试");
      }
      const stream = new MediaStream([track.mediaStreamTrack]);
      // 音轨归控制器（utterance 录完不停轨）；stopActiveRecording 面向
      // 本段录音照常生效（wav-recorder 模块级占用）
      return recordAndEncodeWavFromStream(stream);
    },
    state: () => {
      if (!closed && state.status === "connected" && track?.mediaStreamTrack.readyState !== "live") {
        state.status = "disconnected"; // 防御性同步：轨已死即失效（含 ended）
      }
      return { ...state };
    },
    disconnect: async () => {
      ensureReleased(); // 幂等：closed 守卫保证恰一次
      if (live.state !== ConnectionState.Disconnected) {
        try {
          await live.disconnect();
        } catch {
          // 收尾尽力而为：unmount 路径绝不抛
        }
      }
    },
  };
}

/** 懒获取或复用：existing 已连接 → 原样复用（零新请求零新轨）；
 *  existing 为 null 或已断连 → 收尾旧控制器后显式新连接（不静默
 *  复用 stale 控制器）。voice-studio 语音按钮的第一次与后续作答都
 *  走这里——页面加载/渲染不触发任何连接或麦克风请求。 */
export async function getOrCreateVoiceInput(
  existing: LiveKitVoiceInput | null,
  sessionId: string,
  deps: LiveKitInputDeps = defaultDeps,
): Promise<LiveKitVoiceInput> {
  if (existing !== null) {
    if (existing.state().status === "connected") return existing;
    await existing.disconnect(); // stale 收尾（停旧轨），显式重建
  }
  return connectVoiceInput(sessionId, deps);
}
