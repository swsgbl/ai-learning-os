/**
 * ExamApi.ets — 考试全流程 API 封装(hm-parity v2)
 *
 * 覆盖 Android ExamViewModel 的五步:开考/拉题/逐题作答/交卷/报告。
 * 全部经 AiosApi.requestJson(受保护请求:内存 bearer token 可用才附加头,
 * UrlPolicy 白名单先行,409/404/422 detail 原样透出,绝不 [object Object])。
 *
 * 答案隔离不变式(铁律,与 Android ExamDtos.kt / 服务端 exams.py 对齐):
 *   - GET /exams/{id} 与 POST /papers/{id}/exams 的 questions[] 只含
 *     id/type/stem/options 四字段,绝不下发 answer/explanation/angles;
 *   - 正确答案(expected)只在交卷(submit 响应 questions)或报告(report
 *     items[].expected)中下发;
 *   - 判分(score/correct)只来自服务端提交后响应。
 */

import { http } from '@kit.NetworkKit';
import { ApiResult, requestJson } from './AiosApi';

// ───────────────────────── 考试 DTO ─────────────────────────

/** 题目选项(maps QuestionOptionOut / Android OptionResponse) */
export interface ExamOption {
  key: string;
  text: string;
}

/** 公共题目投影(绝不含正确答案/解析) */
export interface ExamQuestion {
  id: string;
  /** single | multiple_select | short_answer | essay | numeric | true_false…(未知原样展示) */
  type: string;
  stem: string;
  options: Array<ExamOption> | null;
}

/** 考试会话(开考/拉题/逐题作答共用快照结构) */
export interface ExamSession {
  exam_id: string;
  paper_id: string;
  paper_title: string;
  mode: string;
  status: string;
  server_started_at: string;
  server_end_at: string;
  server_remaining_seconds: number;
  questions: Array<ExamQuestion>;
  /** question_id → 已保存作答(学员已保存,非正确答案) */
  answers: Record<string, string>;
  next_sequence: number;
}

/** 开考响应(与 ExamSession 同结构) */
export type StartExamResult = ExamSession;

/** 交卷判分条目 */
export interface GradedItem {
  question_id: string;
  given: string;
  /** 三态:null = 待复核(主观题) */
  correct: boolean | null;
  expected: string;
  explanation: string;
}

/** 交卷响应(SubmissionOut) */
export interface SubmissionResult {
  exam_id: string;
  paper_id: string;
  paper_title: string;
  mode: string;
  status: string;
  score: number;
  correct_count: number;
  total_count: number;
  duration_seconds: number;
  rule_version: string;
  items: Array<GradedItem>;
}

/** 报告逐题条目(ReportItemOut;expected 在此处下发) */
export interface ReportItem {
  question_id: string;
  sequence: number;
  question_type: string;
  stem: string;
  given: string;
  /** 正确答案:单选=选项 key(如 "A");多选={"option_indices":[0,2]} JSON 串 */
  expected: string;
  correct: boolean | null;
  score: number | null;
  max_score: number;
  explanation: string;
  knowledge: Array<string>;
  score_ratio: number | null;
}

/** 报告概念掌握行 */
export interface ReportConcept {
  concept: string;
  correct: number;
  total: number;
  reviewed: number;
  ratio: number | null;
}

/** 考试报告(ReportOut) */
export interface ExamReport {
  exam_id: string;
  paper_title: string;
  mode: string;
  score: number;
  score_earned: number;
  score_max: number;
  correct_count: number;
  total_count: number;
  reviewed_count: number;
  items: Array<ReportItem>;
  concepts: Array<ReportConcept>;
}

// ───────────────────────── 作答格式工具 ─────────────────────────

/**
 * 多选作答编码:0 基索引升序数组 → {"option_indices":[i,j]} JSON 字符串。
 * 与服务端 ObjectiveGrader / Android submitMultipleSelect 对齐。
 */
export function encodeMultiSelectAnswer(indices: Array<number>): string {
  const sorted: Array<number> = indices.slice().sort((a: number, b: number) => a - b);
  return JSON.stringify({ 'option_indices': sorted } as Record<string, Object>);
}

/**
 * 多选 expected/given 解码为可读字母串:{"option_indices":[0,2]} → "AC"。
 * 解析失败返回原文(单选 key 或文本作答原样)。
 */
export function readableAnswer(raw: string, options: Array<ExamOption> | null): string {
  if (raw === null || raw === undefined) {
    return '(空)';
  }
  const s: string = `${raw}`;
  if (s.startsWith('{"option_indices"')) {
    try {
      const parsed = JSON.parse(s) as Record<string, Object>;
      const idx: Object | undefined = parsed['option_indices'];
      if (Array.isArray(idx)) {
        const arr: Array<Object> = idx as Array<Object>;
        let out: string = '';
        for (let i: number = 0; i < arr.length; i++) {
          const n: number = arr[i] as number;
          if (options !== null && n >= 0 && n < options.length) {
            out += options[n].key;
          } else {
            out += `${n}`;
          }
        }
        return out.length > 0 ? out : '(空)';
      }
    } catch (e) {
      return s;
    }
  }
  return s.length > 0 ? s : '(空)';
}

/** 是否多选题(type 判定与 Android QuestionCard 对齐) */
export function isMultipleSelect(type: string): boolean {
  return type === 'multiple_select';
}

/** 是否客观选择题(有 options 且非多选 → 单选点选) */
export function isSingleChoice(type: string, options: Array<ExamOption> | null): boolean {
  if (options === null || options.length === 0) {
    return false;
  }
  return !isMultipleSelect(type);
}

// ───────────────────────── 端点 ─────────────────────────

/** POST /api/v1/papers/{paper_id}/exams — 开考(请求体 {mode:'exam'}) */
export async function startExam(baseUrl: string, paperId: string): Promise<ApiResult<StartExamResult>> {
  return requestJson<StartExamResult>(baseUrl, http.RequestMethod.POST,
    `/api/v1/papers/${encodeURIComponent(paperId)}/exams`,
    { 'mode': 'exam' } as Record<string, Object>);
}

/** GET /api/v1/exams/{exam_id} — 拉题(断线恢复用,也是考试页主数据源) */
export async function getExam(baseUrl: string, examId: string): Promise<ApiResult<ExamSession>> {
  return requestJson<ExamSession>(baseUrl, http.RequestMethod.GET,
    `/api/v1/exams/${encodeURIComponent(examId)}`, null);
}

/** PUT /api/v1/exams/{exam_id}/answers — 逐题作答(append-only;响应为最新会话快照) */
export async function putAnswer(baseUrl: string, examId: string, sequence: number, questionId: string, answer: string): Promise<ApiResult<ExamSession>> {
  return requestJson<ExamSession>(baseUrl, http.RequestMethod.PUT,
    `/api/v1/exams/${encodeURIComponent(examId)}/answers`,
    { 'sequence': sequence, 'question_id': questionId, 'answer': answer } as Record<string, Object>);
}

/** POST /api/v1/exams/{exam_id}/submit — 交卷(服务端判分;幂等,重复交卷返回已有结果) */
export async function submitExam(baseUrl: string, examId: string): Promise<ApiResult<SubmissionResult>> {
  return requestJson<SubmissionResult>(baseUrl, http.RequestMethod.POST,
    `/api/v1/exams/${encodeURIComponent(examId)}/submit`,
    {} as Record<string, Object>);
}

/** GET /api/v1/exams/{exam_id}/report — 报告(无 submission 时 404「审阅报告尚未生成」) */
export async function getExamReport(baseUrl: string, examId: string): Promise<ApiResult<ExamReport>> {
  return requestJson<ExamReport>(baseUrl, http.RequestMethod.GET,
    `/api/v1/exams/${encodeURIComponent(examId)}/report`, null);
}
