import type {
  AuditEntry,
  DailyPlan,
  ExamReport,
  ExamSession,
  GenerationDraft,
  ImportDraft,
  PaperDraft,
  PaperSummary,
  StudentStates,
  Submission,
  UserProfile,
  VariantDraft,
  VoiceSession,
  VoiceSessionAnswerRequest,
  VoiceSessionAnswerResult,
  VoiceSessionCommandRequest,
  VoiceSessionCommandResult,
  VoiceSessionIntentRequest,
  VoiceSessionIntentResult,
  VoiceSessionReport,
  VoiceSessionResume,
  VoiceSessions,
  VoiceTokenResponse,
} from "./types";
import { normalizeBasePath } from "./base-path";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

// M14-159: 公共 base path（构建期注入，经 normalizeBasePath 结构化校验；
// 空 = 根路径，行为与既有构建完全一致）。全仓导航审计结论：Link/router.*
// 由 Next 自动处理 basePath，唯一下面的 window.location 直接赋值会绕过
// 前缀 —— 登录跳转与 pathname 判定必须共用显式带前缀的 LOGIN_PATH，
// 否则 basePath 构建下会跳到根路径 404；前缀由校验器保证不会双重叠加。
const BASE_PATH = normalizeBasePath(process.env.NEXT_PUBLIC_BASE_PATH);
export const LOGIN_PATH = `${BASE_PATH}/login`;

// M10-02: 治理页需要区分 403（非管理员）/ 409（重复审核）做明确状态反馈
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
    cache: "no-store",
  });

  if (!response.ok) {
    // M9-02/M9-03: 认证开启后 401 = 未登录或凭据失效 —— 引导到登录页
    // M14-159: 登录页地址随 BASE_PATH（/aios 构建下为 /aios/login）
    if (response.status === 401 && typeof window !== "undefined" && !window.location.pathname.startsWith(LOGIN_PATH)) {
      // M14-188: 相对目的地直接赋 href 会被 Next 规则
      // no-location-assign-relative-destination 拦截（且 assign(相对)
      // 同样被拦）。显式绝对化到当前 origin：LOGIN_PATH 已含 BASE_PATH
      // 前缀，仍是 window.location.href 整页跳转 —— 清空客户端状态的
      // 安全语义不变，防登录页自身循环的 pathname 守卫亦不动。
      window.location.href = new URL(LOGIN_PATH, window.location.origin).href;
    }
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    const message = typeof detail.detail === "string" ? detail.detail : "请求失败";
    throw new ApiError(message, response.status);
  }
  return response.json() as Promise<T>;
}

// M14-248(R1): VoiceSession 动态路径统一构造——session_id 必须经
// encodeURIComponent 保持单 segment：含 "/"、"?"、"#"、空格等字符时
// 直接插值会把一个 path segment 拆成错误 route / 注入 query / fragment。
function voiceSessionPath(sessionId: string): string {
  return `/api/v1/voice/sessions/${encodeURIComponent(sessionId)}`;
}

// 四类草稿端点同构（list / get / approve / reject，均经统一 client）
function draftEndpoints<T>(base: string) {
  return {
    list: (status?: string) =>
      request<T[]>(status ? `${base}?status=${status}` : base),
    get: (id: string) => request<T>(`${base}/${id}`),
    approve: (id: string, note?: string) =>
      request<T>(`${base}/${id}/approve`, {
        method: "POST",
        body: JSON.stringify({ note: note?.trim() ? note.trim() : null }),
      }),
    reject: (id: string, note?: string) =>
      request<T>(`${base}/${id}/reject`, {
        method: "POST",
        body: JSON.stringify({ note: note?.trim() ? note.trim() : null }),
      }),
  };
}

export const api = {
  papers: () => request<PaperSummary[]>("/api/v1/papers"),
  me: () => request<UserProfile>("/api/v1/auth/me"),
  audit: (limit = 100) => request<AuditEntry[]>(`/api/v1/audit?limit=${limit}`),
  dailyPlan: () => request<DailyPlan>("/api/v1/student/daily-plan"),
  studentStates: () => request<StudentStates>("/api/v1/student/states"),
  drafts: {
    courseImport: draftEndpoints<ImportDraft>("/api/v1/courses/import-drafts"),
    courseGeneration: draftEndpoints<GenerationDraft>("/api/v1/courses/generation-drafts"),
    paperExtraction: draftEndpoints<PaperDraft>("/api/v1/papers/import-drafts"),
    variantQuestion: draftEndpoints<VariantDraft>("/api/v1/questions/variant-drafts"),
  },
  startExam: (paperId: string, mode: "voice" | "exam") =>
    request<ExamSession>(`/api/v1/papers/${paperId}/exams`, {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
  saveAnswer: (examId: string, sequence: number, questionId: string, answer: string) =>
    request<ExamSession>(`/api/v1/exams/${examId}/answers`, {
      method: "PUT",
      body: JSON.stringify({ sequence, question_id: questionId, answer }),
    }),
  getExam: (examId: string) => request<ExamSession>(`/api/v1/exams/${examId}`),
  submitExam: (examId: string) =>
    request<Submission>(`/api/v1/exams/${examId}/submit`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  submission: (examId: string) => request<Submission>(`/api/v1/exams/${examId}/submission`),
  report: (examId: string) => request<ExamReport>(`/api/v1/exams/${examId}/report`),
  // M14-35: 房间 token（默认 student 角色；响应中的 token 字段由调用方只经局部变量使用）
  voiceToken: (room: string) =>
    request<VoiceTokenResponse>("/api/v1/voice/token", {
      method: "POST",
      body: JSON.stringify({ room }),
    }),
  // M14-248: 服务端权威 VoiceSession 契约面（services/api voice.py）。
  // 路径/method/payload 与服务端一一对应、不增字段；status 等响应字段
  // 原样透传——客户端不推演 FSM、不缓存答案、不虚报语音结果；响应即
  // 新状态。仅契约层，现有 voice-studio 流程不接入（不替换、不迁移）。
  voiceSessions: {
    create: (examId: string) =>
      request<VoiceSession>("/api/v1/voice/sessions", {
        method: "POST",
        body: JSON.stringify({ exam_id: examId }),
      }),
    list: (examId: string) =>
      request<VoiceSessions>(
        `/api/v1/voice/sessions?exam_id=${encodeURIComponent(examId)}`,
      ),
    get: (sessionId: string) => request<VoiceSession>(voiceSessionPath(sessionId)),
    resume: (sessionId: string) =>
      request<VoiceSessionResume>(`${voiceSessionPath(sessionId)}/resume`),
    command: (sessionId: string, payload: VoiceSessionCommandRequest) =>
      request<VoiceSessionCommandResult>(
        `${voiceSessionPath(sessionId)}/commands`,
        {
          method: "POST",
          body: JSON.stringify(payload),
        },
      ),
    intent: (sessionId: string, payload: VoiceSessionIntentRequest) =>
      request<VoiceSessionIntentResult>(
        `${voiceSessionPath(sessionId)}/intents`,
        {
          method: "POST",
          body: JSON.stringify(payload),
        },
      ),
    // event_id 为客户端幂等键：payload 必须由调用方显式传入（本层绝不
    // 隐式生成——隐式生成会让重试换键、破坏服务端幂等去重）；一次逻辑
    // 事件生成一次（newVoiceAnswerEventId），重试复用同一 payload。
    answer: (sessionId: string, payload: VoiceSessionAnswerRequest) =>
      request<VoiceSessionAnswerResult>(
        `${voiceSessionPath(sessionId)}/answers`,
        {
          method: "POST",
          body: JSON.stringify(payload),
        },
      ),
    report: (sessionId: string) =>
      request<VoiceSessionReport>(`${voiceSessionPath(sessionId)}/report`),
  },
};

// M14-248: answers 幂等键 helper——为「一次逻辑提交事件」生成 event_id
// （UUID v4，36 字符，落在服务端 Field(min_length=4, max_length=64) 界内）。
// 生成时机 = 发起事件前，一次事件恰一次；重试必须复用同一 event_id
// （服务端凭它幂等去重：同键重放返回既有结果不重复落库，换键 = 新事件）。
// 与 Android 端 UUID.randomUUID().toString() 同策略。
// R1: 运行时安全降级链——浏览器非安全上下文（LAN HTTP / 公网 HTTP）
// 无 crypto.randomUUID（secure-context-only），SSR/测试环境可能整个
// WebCrypto 缺席；逐级降级 randomUUID → getRandomValues → Math.random，
// 任何环境都能产出合法 36 字符 UUID v4，绝不因环境崩溃。
type WebCryptoLike = {
  randomUUID?: () => string;
  getRandomValues?: <T extends Uint8Array>(array: T) => T;
};

function currentCrypto(): WebCryptoLike | undefined {
  if (typeof globalThis === "undefined") return undefined;
  return (globalThis as { crypto?: WebCryptoLike }).crypto;
}

/** 16 随机字节 → 36 字符 UUID v4（版本/变体位强制置位；纯函数） */
function uuidV4FromBytes(bytes: Readonly<Uint8Array>): string {
  const b = bytes.slice();
  b[6] = (b[6] & 0x0f) | 0x40; // version 4
  b[8] = (b[8] & 0x3f) | 0x80; // variant 10xx
  const hex = Array.from(b, (v) => v.toString(16).padStart(2, "0")).join("");
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20, 32),
  ].join("-");
}

export function newVoiceAnswerEventId(): string {
  const cryptoApi = currentCrypto();
  if (typeof cryptoApi?.randomUUID === "function") return cryptoApi.randomUUID();
  if (typeof cryptoApi?.getRandomValues === "function") {
    return uuidV4FromBytes(cryptoApi.getRandomValues(new Uint8Array(16)));
  }
  const bytes = new Uint8Array(16);
  for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  return uuidV4FromBytes(bytes);
}

export { API_BASE, BASE_PATH };
