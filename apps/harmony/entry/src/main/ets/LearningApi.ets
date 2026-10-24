/**
 * LearningApi.ets — 学习冲刺 + 教材工作台 API 封装(hm-parity v1)
 *
 * 全部经 AiosApi.requestJson(受保护请求:内存 bearer token 可用才附加头,
 * UrlPolicy 白名单先行,错误如实收敛,绝不 [object Object])。
 *
 * 答案隔离不变式(铁律):
 *   - AssessmentItem 不含 answer/explanation 字段——UI 不假设有;
 *   - 判分结果(score/correct_count)只在提交后来自服务端。
 *
 * snake_case 与服务端 Pydantic 契约(services/api/app/api/routes/learning.py
 * / catalog.py / library.py)及 Android DTO(LearningDtos.kt / TextbookDtos.kt)
 * 精确对齐;服务端演进新增字段靠 as 断言容忍,不猜语义。
 */

import { http } from '@kit.NetworkKit';
import { ApiResult, requestJson } from './AiosApi';

// ───────────────────────── 学习冲刺 DTO ─────────────────────────

/** 学习合同(maps LearningContractDto) */
export interface LearningContract {
  id: string;
  title: string;
  target_outcomes: Array<TargetOutcome>;
  time_budget_hours: number;
  pass_criteria: string;
  /** null = 未确认;非 null = 已确认 */
  confirmed_at: string | null;
}

/** 能力目标 */
export interface TargetOutcome {
  statement: string;
  criteria: string;
}

/** 学习冲刺(maps LearningSprintDto);phase 为 11 阶段单向链 */
export interface LearningSprint {
  id: string;
  contract_id: string;
  phase: string;
  paused: boolean;
  pause_reason: string;
  expired: boolean;
}

/** 作答题选项 */
export interface AssessmentOption {
  key: string;
  text: string;
}

/** 作答题(公共投影,绝不含正确答案) */
export interface AssessmentItem {
  item_id: string;
  concept_id: string;
  question_type: string;
  stem: string;
  options: Array<AssessmentOption> | null;
}

/** 前测/后测拉题响应 */
export interface AssessmentResponse {
  sprint_id: string;
  /** baseline | posttest */
  phase: string;
  items: Array<AssessmentItem>;
}

/** 判分结果(只在提交后存在) */
export interface AssessmentResult {
  sprint_id: string;
  phase: string;
  score: number;
  correct_count: number;
  question_count: number;
  ungraded_count: number;
  /** 提交后服务端推进到的冲刺阶段 */
  sprint_phase: string;
}

// ───────────────────────── 教材工作台 DTO ─────────────────────────

/** 目录搜索结果(maps CatalogResourceDto) */
export interface CatalogResource {
  id: string;
  provider: string;
  source_path: string;
  source_url: string;
  title: string;
  education_stage: string;
  subject: string;
  grade: string;
  volume: string;
  mime_type: string;
  parts_count: number;
  permission_status: string;
}

/** 我的教材(书架行,maps ImportJobDto) */
export interface ImportJob {
  id: string;
  resource_id: string;
  status: string;
  parts_total: number;
  parts_fetched: number;
  sha256: string;
  size_bytes: number;
  error: string;
  title: string;
  parse_status: string;
  concept_counts: string;
}

/** 解析结果 */
export interface ParseResult {
  status: string;
  page_count: number;
  chunk_count: number;
  low_text_pages: number;
  ocr_pages_done: number;
  ocr_mean_confidence: number;
}

/** 概念提取结果 */
export interface ConceptExtraction {
  concept_count: number;
  edge_count: number;
  dropped: number;
}

/** 一键开冲刺结果 */
export interface TextbookSprint {
  contract_id: string;
  sprint_id: string;
  generated: number;
  published: number;
  blueprint_complete: boolean;
  sprint_phase: string;
}

// ───────────────────────── 11 阶段链(本地映射,与服务端 domain/sprint_state.py 对齐) ─────────────────────────

export const SPRINT_PHASES: Array<string> = [
  'created', 'contract_confirmed', 'baseline_assessment', 'concept_map_review',
  'plan_confirm', 'core_learning', 'targeted_practice', 'transfer_check',
  'posttest', 'report', 'follow_up'
];

export const SPRINT_PHASE_LABELS: Record<string, string> = {
  'created': '已创建',
  'contract_confirmed': '合同已确认',
  'baseline_assessment': '前测',
  'concept_map_review': '概念地图',
  'plan_confirm': '计划确认',
  'core_learning': '核心学习',
  'targeted_practice': '定向练习',
  'transfer_check': '迁移检验',
  'posttest': '后测',
  'report': '学习报告',
  'follow_up': '间隔复测'
};

/** 阶段中文标签(未知阶段原样展示) */
export function sprintPhaseLabel(phase: string): string {
  const v: string | undefined = SPRINT_PHASE_LABELS[phase];
  return v !== undefined ? v : phase;
}

/** 下一阶段 id;已在最后阶段返回 '' */
export function nextSprintPhase(phase: string): string {
  const idx: number = SPRINT_PHASES.indexOf(phase);
  if (idx < 0 || idx >= SPRINT_PHASES.length - 1) {
    return '';
  }
  return SPRINT_PHASES[idx + 1];
}

// ───────────────────────── 学习冲刺端点 ─────────────────────────

/** GET /api/v1/learning/contracts — 合同列表(owner 的) */
export async function getLearningContracts(baseUrl: string): Promise<ApiResult<Array<LearningContract>>> {
  return requestJson<Array<LearningContract>>(baseUrl, http.RequestMethod.GET, '/api/v1/learning/contracts', null);
}

/** POST /api/v1/learning/contracts/{id}/confirm — 确认合同 */
export async function confirmLearningContract(baseUrl: string, contractId: string): Promise<ApiResult<LearningContract>> {
  return requestJson<LearningContract>(baseUrl, http.RequestMethod.POST,
    `/api/v1/learning/contracts/${encodeURIComponent(contractId)}/confirm`, {});
}

/** GET /api/v1/learning/contracts/{id}/sprints — 该合同的冲刺列表 */
export async function getContractSprints(baseUrl: string, contractId: string): Promise<ApiResult<Array<LearningSprint>>> {
  return requestJson<Array<LearningSprint>>(baseUrl, http.RequestMethod.GET,
    `/api/v1/learning/contracts/${encodeURIComponent(contractId)}/sprints`, null);
}

/** POST /api/v1/learning/sprints/{id}/advance — 推进到下一阶段 */
export async function advanceSprint(baseUrl: string, sprintId: string, toPhase: string): Promise<ApiResult<LearningSprint>> {
  return requestJson<LearningSprint>(baseUrl, http.RequestMethod.POST,
    `/api/v1/learning/sprints/${encodeURIComponent(sprintId)}/advance`,
    { 'to_phase': toPhase } as Record<string, Object>);
}

/** GET /api/v1/learning/sprints/{id}/assessment?phase=baseline|posttest — 拉题 */
export async function getSprintAssessment(baseUrl: string, sprintId: string, phase: string): Promise<ApiResult<AssessmentResponse>> {
  return requestJson<AssessmentResponse>(baseUrl, http.RequestMethod.GET,
    `/api/v1/learning/sprints/${encodeURIComponent(sprintId)}/assessment?phase=${encodeURIComponent(phase)}`, null);
}

/** 提交作答行(item_id + response + confidence 1-5) */
export interface AssessmentSubmitItem {
  item_id: string;
  response: string;
  confidence: number;
}

/** POST /api/v1/learning/sprints/{id}/assessment/{phase}/submit — 提交判分 */
export async function submitSprintAssessment(baseUrl: string, sprintId: string, phase: string, responses: Array<AssessmentSubmitItem>): Promise<ApiResult<AssessmentResult>> {
  const rows: Array<Object> = [];
  for (let i: number = 0; i < responses.length; i++) {
    const r: AssessmentSubmitItem = responses[i];
    rows.push({ 'item_id': r.item_id, 'response': r.response, 'confidence': r.confidence } as Record<string, Object>);
  }
  return requestJson<AssessmentResult>(baseUrl, http.RequestMethod.POST,
    `/api/v1/learning/sprints/${encodeURIComponent(sprintId)}/assessment/${encodeURIComponent(phase)}/submit`,
    { 'responses': rows } as Record<string, Object>);
}

// ───────────────────────── 教材工作台端点 ─────────────────────────

/** GET /api/v1/catalog/search?q=...&limit=30 — 目录搜索 */
export async function catalogSearch(baseUrl: string, query: string): Promise<ApiResult<Array<CatalogResource>>> {
  return requestJson<Array<CatalogResource>>(baseUrl, http.RequestMethod.GET,
    `/api/v1/catalog/search?q=${encodeURIComponent(query)}&limit=30`, null);
}

/** GET /api/v1/library/imports — 我的书架 */
export async function getMyImports(baseUrl: string): Promise<ApiResult<Array<ImportJob>>> {
  return requestJson<Array<ImportJob>>(baseUrl, http.RequestMethod.GET, '/api/v1/library/imports', null);
}

/** POST /api/v1/library/imports — 导入(声明使用权恒 true,UI 导入前显式确认) */
export async function createImport(baseUrl: string, resourceId: string): Promise<ApiResult<ImportJob>> {
  return requestJson<ImportJob>(baseUrl, http.RequestMethod.POST, '/api/v1/library/imports',
    { 'resource_id': resourceId, 'permission_statement': true } as Record<string, Object>);
}

/** POST /api/v1/library/imports/{job_id}/parse — 解析(ocr=true 时 ?ocr_max_pages=15) */
export async function parseImport(baseUrl: string, jobId: string, ocr: boolean): Promise<ApiResult<ParseResult>> {
  const q: string = ocr ? '?ocr_max_pages=15' : '';
  return requestJson<ParseResult>(baseUrl, http.RequestMethod.POST,
    `/api/v1/library/imports/${encodeURIComponent(jobId)}/parse${q}`, {});
}

/** POST /api/v1/library/imports/{job_id}/concepts — 提取概念 */
export async function extractConcepts(baseUrl: string, jobId: string): Promise<ApiResult<ConceptExtraction>> {
  return requestJson<ConceptExtraction>(baseUrl, http.RequestMethod.POST,
    `/api/v1/library/imports/${encodeURIComponent(jobId)}/concepts`, {});
}

/** POST /api/v1/library/imports/{job_id}/create-sprint — 一键开冲刺 */
export async function createSprintFromTextbook(baseUrl: string, jobId: string): Promise<ApiResult<TextbookSprint>> {
  return requestJson<TextbookSprint>(baseUrl, http.RequestMethod.POST,
    `/api/v1/library/imports/${encodeURIComponent(jobId)}/create-sprint`, {});
}
// ───────────────────────── hm-parity v2:迁移检验/定向练习/间隔复习 ─────────────────────────

/** 迁移检验拉题响应(每概念一题,排除前后测已用题) */
export interface TransferItemsResponse {
  sprint_id: string;
  phase: string;
  items: Array<AssessmentItem>;
}

/** 定向练习单题会话(最弱概念优先) */
export interface PracticeSession {
  concept_id: string;
  /** first_attempt|recall_practice|faded_practice|independent_practice|exit_ticket */
  step: string;
  strategy: string;
  rationale: string;
  item: AssessmentItem;
  passed_steps: number;
  target_steps: number;
  remaining_concepts: Array<string>;
}

/** 定向练习单题作答响应(判分后含 explanation) */
export interface PracticeAnswerResult {
  concept_id: string;
  /** 三态:null = 待判分 */
  correct: boolean | null;
  explanation: string;
  /** = correct===true;答错不推进(同步骤重试) */
  step_passed: boolean;
  passed_steps: number;
  target_steps: number;
  next_step: string;
  strategy: string;
  rationale: string;
  /** 全部蓝图概念 passed_steps≥target 时 true,服务端推进到 transfer_check */
  practice_complete: boolean;
  sprint_phase: string;
}

/** 间隔复习队列行 */
export interface ReviewQueueEntry {
  id: string;
  concept_id: string;
  sprint_id: string | null;
  /** scheduled|presented|attempted|completed */
  status: string;
  due_at: string;
  interval_days: number | null;
  algorithm_version: string;
  overdue: boolean;
}

/** 间隔复习作答响应 = 队列行 + last_correct(本次判分) */
export interface ReviewAttemptResult extends ReviewQueueEntry {
  last_correct: boolean | null;
}

/** GET /api/v1/learning/sprints/{id}/transfer/items — 迁移检验拉题 */
export async function getTransferItems(baseUrl: string, sprintId: string): Promise<ApiResult<TransferItemsResponse>> {
  return requestJson<TransferItemsResponse>(baseUrl, http.RequestMethod.GET,
    `/api/v1/learning/sprints/${encodeURIComponent(sprintId)}/transfer/items`, null);
}

/** POST /api/v1/learning/sprints/{id}/transfer/submit — 迁移检验提交(响应含 sprint_phase=posttest) */
export async function submitTransfer(baseUrl: string, sprintId: string, responses: Array<AssessmentSubmitItem>): Promise<ApiResult<AssessmentResult>> {
  const rows: Array<Object> = [];
  for (let i: number = 0; i < responses.length; i++) {
    const r: AssessmentSubmitItem = responses[i];
    rows.push({ 'item_id': r.item_id, 'response': r.response, 'confidence': r.confidence } as Record<string, Object>);
  }
  return requestJson<AssessmentResult>(baseUrl, http.RequestMethod.POST,
    `/api/v1/learning/sprints/${encodeURIComponent(sprintId)}/transfer/submit`,
    { 'responses': rows } as Record<string, Object>);
}

/** GET /api/v1/learning/sprints/{id}/practice/session — 定向练习单题会话 */
export async function getPracticeSession(baseUrl: string, sprintId: string): Promise<ApiResult<PracticeSession>> {
  return requestJson<PracticeSession>(baseUrl, http.RequestMethod.GET,
    `/api/v1/learning/sprints/${encodeURIComponent(sprintId)}/practice/session`, null);
}

/** POST /api/v1/learning/sprints/{id}/practice/answer — 定向练习作答 */
export async function submitPracticeAnswer(baseUrl: string, sprintId: string, itemId: string, response: string, confidence: number): Promise<ApiResult<PracticeAnswerResult>> {
  return requestJson<PracticeAnswerResult>(baseUrl, http.RequestMethod.POST,
    `/api/v1/learning/sprints/${encodeURIComponent(sprintId)}/practice/answer`,
    { 'item_id': itemId, 'response': response, 'confidence': confidence } as Record<string, Object>);
}

/** GET /api/v1/learning/review-queue — 间隔复习队列(到期+非 completed,按 due_at 升序) */
export async function getReviewQueue(baseUrl: string): Promise<ApiResult<Array<ReviewQueueEntry>>> {
  return requestJson<Array<ReviewQueueEntry>>(baseUrl, http.RequestMethod.GET,
    '/api/v1/learning/review-queue', null);
}

/** POST /api/v1/learning/review-queue/{id}/present — 展示转换(scheduled→presented;作答前必调) */
export async function presentReview(baseUrl: string, reviewId: string): Promise<ApiResult<ReviewQueueEntry>> {
  return requestJson<ReviewQueueEntry>(baseUrl, http.RequestMethod.POST,
    `/api/v1/learning/review-queue/${encodeURIComponent(reviewId)}/present`,
    {} as Record<string, Object>);
}

/** GET /api/v1/learning/review-queue/{id}/item — 复习单题(裸 AssessmentItem) */
export async function getReviewItem(baseUrl: string, reviewId: string): Promise<ApiResult<AssessmentItem>> {
  return requestJson<AssessmentItem>(baseUrl, http.RequestMethod.GET,
    `/api/v1/learning/review-queue/${encodeURIComponent(reviewId)}/item`, null);
}

/** POST /api/v1/learning/review-queue/{id}/attempt — 复习作答(响应含 last_correct 与下轮 interval_days) */
export async function submitReviewAttempt(baseUrl: string, reviewId: string, itemId: string, response: string, confidence: number): Promise<ApiResult<ReviewAttemptResult>> {
  return requestJson<ReviewAttemptResult>(baseUrl, http.RequestMethod.POST,
    `/api/v1/learning/review-queue/${encodeURIComponent(reviewId)}/attempt`,
    { 'item_id': itemId, 'response': response, 'confidence': confidence } as Record<string, Object>);
}
