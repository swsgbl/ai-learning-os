/**
 * SearchApi.ets — hm-parity v4: 搜索 Tab 全流程网络契约
 *
 * 端点契约与以下两处逐字段核对一致后才写(契约勘察纪律, v3 血泪教训):
 *   - 服务端(唯一权威): services/api/app/api/routes/search.py
 *   - Android 对照: apps/android/.../data/remote/AiosApi.kt + SearchDtos.kt
 *
 * 端点清单(均挂在 /api/v1/search 下):
 *   - GET  /providers        → ProvidersOut {items: ProviderView[]}
 *   - POST /plan             → PlanOut {query, slots, plan[]}     两段式第一步
 *   - POST /queries          → {query_id, results, skipped, ...}  两段式第二步(执行)
 *   - GET  /queries/{id}     → SearchRecordOut                    记录查证
 *
 * 全部走 requestJson<T>(bearer fail-closed + 4xx/5xx detail 如实透出),
 * 密钥/token 零持久化零日志;未登录时按服务端 401/403 语义如实展示。
 * providers 列表仍用 AiosApi.getSearchProviders(既有只读链路,不重复声明)。
 */

import { http } from '@kit.NetworkKit';
import { ApiResult, requestJson } from '../AiosApi';

// ---------- 契约数据结构(字段名 snake_case 原样,与服务端 Pydantic 一一对应) ----------

/** 槽位识别结果(全部可空:未识别就是 null,不虚报) */
export interface SearchPlanSlotsData {
  subject: string | null;
  school: string | null;
  year: string | null;
  course: string | null;
  question_type: string | null;
  publicity: string | null;
}

/** 计划单项:provider + 该源将执行的查询词 + 可用性 */
export interface SearchPlanItemData {
  provider: string;
  query: string;
  enabled: boolean;
  unavailable_reason: string | null;
}

/** POST /api/v1/search/plan 响应 */
export interface SearchPlanData {
  query: string;
  slots: SearchPlanSlotsData;
  plan: Array<SearchPlanItemData>;
}

/** 弃用记录(执行时某源被跳过的原因;plan 无此结构,仅执行/回查有) */
export interface SearchSkippedData {
  provider: string;
  reason: string;
}

/** 结果单项 */
export interface SearchResultItemData {
  title: string;
  url: string;
  snippet: string;
  source: string;
  provider: string;
  authority: string | null;
  rank_reason: string;
}

/** POST /api/v1/search/queries 响应 */
export interface SearchQueryData {
  query_id: number;
  query: string;
  providers_requested: Array<string>;
  results: Array<SearchResultItemData>;
  skipped: Array<SearchSkippedData>;
  result_count: number;
  duration_ms: number;
}

/** GET /api/v1/search/queries/{id} 响应 */
export interface SearchRecordData {
  id: number;
  query: string;
  providers_requested: Array<string>;
  providers_skipped: Array<SearchSkippedData>;
  result_count: number;
  duration_ms: number;
  results: Array<SearchResultItemData>;
  created_at: string;
}

// ---------- 端点函数 ----------

/** POST /api/v1/search/plan — 槽位识别 + 计划预览(只出计划不执行) */
export async function postSearchPlan(
  baseUrl: string,
  query: string,
  providers: Array<string> | null
): Promise<ApiResult<SearchPlanData>> {
  return requestJson<SearchPlanData>(
    baseUrl,
    http.RequestMethod.POST,
    '/api/v1/search/plan',
    { 'query': query, 'providers': providers } as Record<string, Object>
  );
}

/** POST /api/v1/search/queries — 执行多源查询 */
export async function postSearchQueries(
  baseUrl: string,
  query: string,
  providers: Array<string> | null,
  limit: number
): Promise<ApiResult<SearchQueryData>> {
  return requestJson<SearchQueryData>(
    baseUrl,
    http.RequestMethod.POST,
    '/api/v1/search/queries',
    { 'query': query, 'providers': providers, 'limit': limit } as Record<string, Object>
  );
}

/** GET /api/v1/search/queries/{id} — 执行记录回查 */
export async function getSearchRecord(
  baseUrl: string,
  recordId: number
): Promise<ApiResult<SearchRecordData>> {
  const id: number = Math.trunc(recordId);
  if (!Number.isFinite(id) || id <= 0) {
    return { ok: false, status: -1, data: null, error: '记录 ID 必须为正整数' };
  }
  return requestJson<SearchRecordData>(
    baseUrl,
    http.RequestMethod.GET,
    `/api/v1/search/queries/${id}`,
    null
  );
}
