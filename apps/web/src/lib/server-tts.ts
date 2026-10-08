// M14-250：服务端 TTS 通道（voice-studio 朗读链的服务端合成播报设备）。
// POST /api/v1/voice/synthesize → WAV 字节 → HTMLAudioElement 播放，
// 真实播放完成（ended 事件）才 resolve——与被替换的浏览器
// speechSynthesis speakUtterance 保持同一「真实完成才发读题事件」契约
// （M14-249）。本模块是纯 IO 设备：不解析文本、不判定语音质量、不
// 推演任何会话状态；provider/fallback 经 X-Voice-Provider/
// X-Voice-Fallback 响应头透出（服务端 local 未配置引擎时降级 tone
// 替身——照播不谎报，降级事实由 UI 经 providers 视图另行透出）。
// 失败（网络/4xx/5xx/空音频/播放错误/自动播放策略）如实 reject，
// 绝不把未完成的播报假装成功。与 Android M12-03 announce 链同构。
import { API_BASE, ApiError, LOGIN_PATH } from "./api";

const SYNTHESIZE_PATH = "/api/v1/voice/synthesize";

/** 服务端合成结果：音频字节 + 通道身份（headers 原样透出，不猜）。 */
export type ServerTtsAudio = {
  audio: ArrayBuffer;
  provider: string;
  fallback: boolean;
};

/** 文本 → 服务端 WAV。错误映射与 api.ts request() 同语义（401 跳登录、
 *  非 2xx → ApiError 携服务端 detail）；仅响应体是音频字节而非 JSON。 */
export async function synthesizeSpeech(text: string): Promise<ServerTtsAudio> {
  const response = await fetch(`${API_BASE}${SYNTHESIZE_PATH}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
    cache: "no-store",
  });
  if (!response.ok) {
    // M9-02/M9-03: 与 request() 同语义——401 引导登录（整页跳转清空状态）
    if (
      response.status === 401 &&
      typeof window !== "undefined" &&
      !window.location.pathname.startsWith(LOGIN_PATH)
    ) {
      window.location.href = new URL(LOGIN_PATH, window.location.origin).href;
    }
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    const message = typeof detail.detail === "string" ? detail.detail : "服务端语音合成失败";
    throw new ApiError(message, response.status);
  }
  const audio = await response.arrayBuffer();
  if (audio.byteLength === 0) {
    // 服务端契约保证 RIFF/WAV 非空（M10-13）；空字节 = 异常响应，不冒充可播放
    throw new Error("服务端语音合成返回空音频");
  }
  return {
    audio,
    provider: response.headers.get("X-Voice-Provider") ?? "unknown",
    fallback: response.headers.get("X-Voice-Fallback") === "1",
  };
}

type AudioLike = {
  src: string;
  play: () => Promise<void> | void;
  pause: () => void;
  addEventListener: (type: "ended" | "error", listener: () => void) => void;
};

// 模块级当前播放占用：供 stopServerTtsPlayback 收尾停止（对应浏览器
// 时代的 speechSynthesis.cancel）。同一时刻至多一段服务端朗读。
let currentPlayback: { audio: AudioLike; revoke: () => void } | null = null;

function releasePlayback(element: AudioLike, revoke: () => void): void {
  if (currentPlayback?.audio === element) currentPlayback = null;
  revoke();
}

/** 播放一段 WAV：ended 才 resolve；error 事件 / play() 拒绝 → reject；
 *  object URL 无论成败恒回收。新播放开始时先停止上一段未完成播放
 *  （与 speechSynthesis.cancel 同语义——被切断的旧 promise 不再结算，
 *  对应的读题回执事件链被放弃，不谎报完成）。 */
export function playWavAudio(audio: ArrayBuffer): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    if (typeof window === "undefined" || typeof Audio === "undefined") {
      reject(new Error("当前环境不支持音频播放"));
      return;
    }
    stopServerTtsPlayback(); // 切断上一段未完成播放（若有）
    const blob = new Blob([audio], { type: "audio/wav" });
    const url = URL.createObjectURL(blob);
    const element = new Audio(url);
    const revoke = () => URL.revokeObjectURL(url);
    currentPlayback = { audio: element, revoke };
    let settled = false;
    element.addEventListener("ended", () => {
      if (settled) return;
      settled = true;
      releasePlayback(element, revoke);
      resolve();
    });
    element.addEventListener("error", () => {
      if (settled) return;
      settled = true;
      releasePlayback(element, revoke);
      reject(new Error("音频播放失败"));
    });
    void Promise.resolve(element.play()).catch(() => {
      // 自动播放策略/解码失败等 play() 拒绝：如实失败，不假装播完
      if (settled) return;
      settled = true;
      releasePlayback(element, revoke);
      reject(new Error("音频播放失败"));
    });
  });
}

/** 停止当前服务端 TTS 播放（收尾/离场清理）：暂停元素并回收 URL。
 *  未决的播放 promise 不再结算——调用方（completeExam）正离场，不
 *  触发任何后续状态更新或读题回执事件。无播放时幂等 no-op。 */
export function stopServerTtsPlayback(): void {
  if (currentPlayback === null) return;
  const { audio, revoke } = currentPlayback;
  currentPlayback = null;
  try {
    audio.pause();
  } finally {
    revoke();
  }
}

/** 单段朗读（服务端通道）：合成 → 播放，播放真实完成才 resolve。
 *  voice-studio speakPhase 直连本函数（R1：零客户端 tts 埋点——不发
 *  任何 /voice/trace 请求；tts 时延由服务端 /synthesize 自动埋点
 *  权威记录）。 */
export async function speakWithServerTts(text: string): Promise<void> {
  const { audio } = await synthesizeSpeech(text);
  await playWavAudio(audio);
}
