// M14-251：服务端 ASR 通道（voice-studio 听写链的服务端转写设备）。
// multipart FormData → POST /api/v1/voice/transcribe → 服务端
// TranscriptionOut JSON（text 为唯一 transcript 权威）。本模块是纯 IO
// 通道：不解析文本、不判定答案、不推演会话状态——解析与规范化仍由
// 服务端 VoiceSession intent parser / answer normalizer 执行。
// provider/fallback 经 X-Voice-Provider/X-Voice-Fallback 响应头透出
// （与 server-tts /synthesize 同口径，头缺席不猜：provider 缺省
// "unknown"、fallback 缺省 false）。失败（网络/4xx/5xx）如实 reject，
// 绝不静默回退浏览器 SpeechRecognition（该代码已随本切片移除）。
// asr 时延以服务端 /transcribe 内自动埋点为唯一权威——本模块零
// /voice/trace 请求（避免 /trace/summary 同 stage 双计数，与 M14-250
// R1 的 tts 口径一致）。
import { API_BASE, ApiError, LOGIN_PATH } from "./api";

const TRANSCRIBE_PATH = "/api/v1/voice/transcribe";

/** 服务端 TranscriptionOut 视图（services/api voice.py TranscriptionOut
 *  snake_case 一一对应，字段不增不减、原样透出不虚报）。 */
export type ServerAsrTranscript = {
  id: number;
  text: string;
  confidence: number;
  provider: string;
  latency_ms: number;
  audio_bytes: number;
  audio_stored: boolean;
  audio_object_key: string | null;
};

/** 转写结果：服务端 JSON 正文 + 通道身份（响应头原样透出，不猜）。 */
export type ServerAsrOutcome = {
  transcript: ServerAsrTranscript;
  /** X-Voice-Provider 头（缺席 = "unknown"，不虚构通道事实）。 */
  provider: string;
  /** X-Voice-Fallback === "1" 才为 true（缺席/"0" 均为 false）。 */
  fallback: boolean;
};

/** WAV 音频 → 服务端转写。文件 part 名 audio、filename answer.wav、
 *  MIME audio/wav（服务端 UploadFile 契约）；错误映射与 api.ts
 *  request() 同语义——401 跳登录（整页跳转清空状态）、非 2xx →
 *  ApiError 携服务端 detail（detail 非字符串时落安全通用文案）。 */
export async function transcribeAudioWav(wav: Blob): Promise<ServerAsrOutcome> {
  const form = new FormData();
  // 不依赖调用方 Blob 自带的 MIME（FormData.append 会原样继承 blob
  // 的 type）——通道契约恒定：field=audio、filename=answer.wav、
  // MIME=audio/wav（与服务端 UploadFile 的 content_type 对齐），故以
  // 显式类型重新装包；字节原样透传。
  const wavFile = new Blob([wav], { type: "audio/wav" });
  form.append("audio", wavFile, "answer.wav");
  const response = await fetch(`${API_BASE}${TRANSCRIBE_PATH}`, {
    method: "POST",
    credentials: "include",
    body: form,
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
    const message = typeof detail.detail === "string" ? detail.detail : "服务端语音识别失败";
    throw new ApiError(message, response.status);
  }
  const transcript = (await response.json()) as ServerAsrTranscript;
  return {
    transcript,
    provider: response.headers.get("X-Voice-Provider") ?? "unknown",
    fallback: response.headers.get("X-Voice-Fallback") === "1",
  };
}
