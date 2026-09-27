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
      window.location.href = LOGIN_PATH;
    }
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    const message = typeof detail.detail === "string" ? detail.detail : "请求失败";
    throw new ApiError(message, response.status);
  }
  return response.json() as Promise<T>;
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
};

export { API_BASE, BASE_PATH };
