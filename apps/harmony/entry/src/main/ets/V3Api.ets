/**
 * V3Api.ets — hm-parity v3 端点封装(AI 密钥管理 + 定制课程)
 *
 * 全部经 AiosApi.requestJson(内存 bearer fail-closed + detail 透出)。
 * 契约对齐 services/api/app/api/routes/me_ai_keys.py:
 *   - 槽位白名单收敛为单槽 "llm";
 *   - 密钥明文/密文永不回显,槽位只给 configured/last4;
 *   - profile 字段: id/label/last4/endpoint/model/active。
 * 密钥安全铁律:api_key 只进请求体,零持久化、零日志。
 */

import { http } from '@kit.NetworkKit';
import { ApiResult, requestJson } from './AiosApi';

// ───────────────────────── AI 密钥管理 DTO ─────────────────────────

/** 密钥槽位行(GET /me/ai-keys 的 keys[] 行) */
export interface AiKeySlot {
  slot: string;
  configured: boolean;
  last4: string | null;
  updated_at: string | null;
  /** hot=判分请求即时生效 */
  effect: string;
  endpoint: string | null;
  model: string | null;
}

/** LLM 厂商配置行 */
export interface LlmProfile {
  id: string;
  label: string;
  last4: string;
  endpoint: string | null;
  model: string | null;
  active: boolean;
}

/** GET /me/ai-keys 响应(keys + profiles 一次返回) */
export interface AiKeysData {
  keys: Array<AiKeySlot>;
  profiles: Array<LlmProfile>;
}

/** PUT/DELETE /me/ai-keys/{slot} 响应(单槽位脱敏视图) */
export interface AiKeyWriteResult {
  slot: string;
  configured: boolean;
  last4: string | null;
  effect: string;
}

/** POST llm/models 探测响应 */
export interface LlmModelsData {
  models: Array<string>;
}

// ───────────────────────── 定制课程 DTO ─────────────────────────

/**
 * POST /courses/generation-drafts 同步响应(= 服务端 GenerationDraftOut)。
 * POST 201 直接返回完整草稿(八阶段管线同步跑完,LLM 兜底可达数十秒),
 * 无 pending→ready 异步流转,无需轮询;status 落库即 "pending_review"。
 */
export interface GenerationDraft {
  id: string;
  goal: string;
  status: string;
  chapter_count: number;
  generation_note: string;
  /** 完整课程方案 JSON;大纲章节数组在 plan['outline'] */
  plan: Record<string, Object> | null;
}

/** POST materialize 响应(服务端 MaterializeOut) */
export interface MaterializeResult {
  paper_id: string;
  question_count: number;
  title: string;
}

// ───────────────────────── AI 密钥端点 ─────────────────────────

/** GET /api/v1/me/ai-keys — 槽位 + 厂商配置列表 */
export async function getAiKeys(baseUrl: string): Promise<ApiResult<AiKeysData>> {
  return requestJson<AiKeysData>(baseUrl, http.RequestMethod.GET, '/api/v1/me/ai-keys', null);
}

/** PUT /api/v1/me/ai-keys/{slot} — 保存/更新密钥(可选 endpoint/model) */
export async function putAiKey(baseUrl: string, slot: string, apiKey: string, endpoint: string, model: string): Promise<ApiResult<AiKeyWriteResult>> {
  const body: Record<string, Object> = { 'api_key': apiKey };
  if (endpoint.length > 0) {
    body['endpoint'] = endpoint;
  }
  if (model.length > 0) {
    body['model'] = model;
  }
  return requestJson<AiKeyWriteResult>(baseUrl, http.RequestMethod.PUT,
    `/api/v1/me/ai-keys/${encodeURIComponent(slot)}`, body);
}

/** DELETE /api/v1/me/ai-keys/{slot} — 删除密钥 */
export async function deleteAiKey(baseUrl: string, slot: string): Promise<ApiResult<AiKeyWriteResult>> {
  return requestJson<AiKeyWriteResult>(baseUrl, http.RequestMethod.DELETE,
    `/api/v1/me/ai-keys/${encodeURIComponent(slot)}`, null);
}

/** POST /api/v1/me/ai-keys/llm/models — 探测可用模型(密钥+endpoint 均可空=用已存配置) */
export async function probeLlmModels(baseUrl: string, apiKey: string, endpoint: string): Promise<ApiResult<LlmModelsData>> {
  const body: Record<string, Object> = {};
  if (apiKey.length > 0) {
    body['api_key'] = apiKey;
  }
  if (endpoint.length > 0) {
    body['endpoint'] = endpoint;
  }
  return requestJson<LlmModelsData>(baseUrl, http.RequestMethod.POST,
    '/api/v1/me/ai-keys/llm/models', body);
}

/** POST /api/v1/me/ai-keys/llm/profiles — 建厂商配置(响应裸 LlmProfileOut,201) */
export async function createLlmProfile(baseUrl: string, label: string, apiKey: string, endpoint: string, model: string, activate: boolean): Promise<ApiResult<LlmProfile>> {
  const body: Record<string, Object> = { 'label': label, 'api_key': apiKey, 'activate': activate };
  if (endpoint.length > 0) {
    body['endpoint'] = endpoint;
  }
  if (model.length > 0) {
    body['model'] = model;
  }
  return requestJson<LlmProfile>(baseUrl, http.RequestMethod.POST,
    '/api/v1/me/ai-keys/llm/profiles', body);
}

/** POST /api/v1/me/ai-keys/llm/profiles/{id}/activate — 激活(响应裸 LlmProfileOut) */
export async function activateLlmProfile(baseUrl: string, profileId: string): Promise<ApiResult<LlmProfile>> {
  return requestJson<LlmProfile>(baseUrl, http.RequestMethod.POST,
    `/api/v1/me/ai-keys/llm/profiles/${encodeURIComponent(profileId)}/activate`, {});
}

/** DELETE /api/v1/me/ai-keys/llm/profiles/{id} — 删除(响应 {deleted:id}) */
export async function deleteLlmProfile(baseUrl: string, profileId: string): Promise<ApiResult<Object>> {
  return requestJson<Object>(baseUrl, http.RequestMethod.DELETE,
    `/api/v1/me/ai-keys/llm/profiles/${encodeURIComponent(profileId)}`, null);
}

// ───────────────────────── 定制课程端点 ─────────────────────────

/**
 * POST /api/v1/courses/generation-drafts — 同步生成课程草稿。
 * 请求体 { goal }(服务端 GenerateRequest 只收 goal,1..512 字);
 * POST 201 直接带完整草稿(服务端同步跑完八阶段管线),无需轮询。
 */
export async function createGenerationDraft(baseUrl: string, goal: string): Promise<ApiResult<GenerationDraft>> {
  return requestJson<GenerationDraft>(baseUrl, http.RequestMethod.POST,
    '/api/v1/courses/generation-drafts', { 'goal': goal } as Record<string, Object>);
}

/** POST /api/v1/courses/generation-drafts/{id}/materialize — 固化入库 */
export async function materializeDraft(baseUrl: string, draftId: string): Promise<ApiResult<MaterializeResult>> {
  return requestJson<MaterializeResult>(baseUrl, http.RequestMethod.POST,
    `/api/v1/courses/generation-drafts/${encodeURIComponent(draftId)}/materialize`, {});
}
