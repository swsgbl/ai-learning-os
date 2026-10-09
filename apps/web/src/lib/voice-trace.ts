// M14-247：M4-09 Web 客户端时延埋点（诚实切片）。
// M14-251 后客户端真实可上报的边界归零：tts/asr 时延均以服务端
// /synthesize、/transcribe 内自动埋点（source=server，仅各自耗时）
// 为唯一权威——客户端不上报 tts/asr span（避免 /trace/summary 同
// stage 双计数；浏览器 SpeechRecognition 已随 M14-251 移除，
// tracedListen 不复存在）；vad/llm/first_audio 发生在 LiveKit/
// 服务端链路，本模块不提供调用面，绝不虚报测不到的阶段（M14-245
// 矩阵缺口 3/4）。TRACE_STAGES 仍完整镜像服务端白名单（契约真值），
// 通用模块为未来 vad/llm/first_audio 的真实可观测边界保留。上报
// 尽力而为：构造失败零网络、网络失败/非 2xx 静默吞掉，绝不阻塞或
// 破坏主流程；失败的业务调用不计入时延样本（只统计成功 span）。
import { API_BASE } from "./api";

// 与 services/api app/domain/voice_trace.py TRACE_STAGES 同口径（七阶段白名单）
export const TRACE_STAGES = ["vad", "asr", "intent", "fsm", "llm", "tts", "first_audio"] as const;

export type TraceStage = (typeof TRACE_STAGES)[number];

export const MIN_TRACE_DURATION_MS = 1;
// 服务端 duration_ms 上限 10 分钟（超出会被 422 拒绝），客户端不截断不虚报
export const MAX_TRACE_DURATION_MS = 600_000;

// 可选上下文 ID：仅在真实上下文可用时由调用方携带。M14-249 起
// voice-studio 接入服务端权威 VoiceSession：session_id 来自
// api.voiceSessions.create 回传（真实会话 ID，不编造不缺省）；
// exam_id/question_id 来自当前考试与当前题。
export type VoiceTraceContext = {
  session_id?: string;
  exam_id?: string;
  question_id?: string;
};

export type VoiceTraceSpan = {
  stage: TraceStage;
  duration_ms: number;
} & VoiceTraceContext;

export type VoiceTracePayload = {
  stage: TraceStage;
  duration_ms: number;
} & VoiceTraceContext;

const TRACE_STAGE_SET: ReadonlySet<string> = new Set(TRACE_STAGES);

function optionalId(value: string | undefined): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

/** 构造合规上报载荷：stage 白名单 + duration 取整后必须落在 [1, 600000]
 *  （越界返回 null——不截断不虚报，宁可丢样本也不构造注定 422 的请求）；
 *  可选 ID 仅在为真值字符串时进载荷（空串/undefined 一律省略）。纯函数。 */
export function buildTraceSpanPayload(span: VoiceTraceSpan): VoiceTracePayload | null {
  if (!TRACE_STAGE_SET.has(span.stage)) return null;
  const duration = Math.round(span.duration_ms);
  if (!Number.isFinite(duration) || duration < MIN_TRACE_DURATION_MS || duration > MAX_TRACE_DURATION_MS) {
    return null;
  }
  const payload: VoiceTracePayload = { stage: span.stage, duration_ms: duration };
  const session_id = optionalId(span.session_id);
  const exam_id = optionalId(span.exam_id);
  const question_id = optionalId(span.question_id);
  if (session_id !== undefined) payload.session_id = session_id;
  if (exam_id !== undefined) payload.exam_id = exam_id;
  if (question_id !== undefined) payload.question_id = question_id;
  return payload;
}

/** 尽力而为上报单个 span 到 POST /api/v1/voice/trace：
 *  任何失败（构造失败/网络错误/非 2xx）都静默——观测绝不 reject、
 *  绝不触发 api.ts 的 401 登录跳转语义，主流程零感知。 */
export async function submitVoiceTrace(span: VoiceTraceSpan): Promise<void> {
  const payload = buildTraceSpanPayload(span);
  if (payload === null) return; // 非法载荷零网络请求
  try {
    await fetch(`${API_BASE}/api/v1/voice/trace`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    // 网络失败静默吞掉：观测尽力而为，不影响作答与朗读
  }
}

/** 在真实浏览器边界处计时包装：成功完成后 fire-and-forget 上报 span；
 *  业务异常原样透传且不上报（失败调用不构成时延样本），埋点永不改变
 *  主流程语义。M14-251 后无活跃调用方（asr/tts 均以服务端自动埋点
 *  为唯一权威）；通用工具为未来 vad/llm/first_audio 的真实可观测
 *  边界保留。 */
export async function withVoiceTrace<T>(
  stage: TraceStage,
  run: () => Promise<T>,
  context: VoiceTraceContext = {},
): Promise<T> {
  const startedAt = performance.now();
  const result = await run();
  void submitVoiceTrace({ stage, duration_ms: performance.now() - startedAt, ...context });
  return result;
}
