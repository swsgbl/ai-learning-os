import type { ExamReport, ExamSession, PaperSummary, Submission } from "./types";
import { getToken } from "./auth";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
    cache: "no-store",
  });

  if (!response.ok) {
    // M9-02/M9-03: 认证开启后 401 = 未登录或凭据失效 —— 引导到登录页
    if (response.status === 401 && typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(typeof detail.detail === "string" ? detail.detail : "请求失败");
  }
  return response.json() as Promise<T>;
}

export const api = {
  papers: () => request<PaperSummary[]>("/api/v1/papers"),
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
};

export { API_BASE };
