/**
 * AiosApi.ets — 只读 GET API 封装(M13-01 第 1/3a 小步 + M13-05 论文端点)
 *
 * 职责边界(刻意收窄):
 *   - 仅封装 7 个只读端点:/health、/api/v1/auth/status、/api/v1/system/privacy、
 *     /api/v1/version、/api/v1/system/ops-snapshot、/api/v1/audit?limit=100、
 *     /api/v1/papers(M13-05);
 *   - 每次请求前先经 UrlPolicy.validateBaseUrl 校验 base URL,不合法直接返回失败;
 *   - 失败/错误如实返回(ApiResult.ok=false + 原始错误信息),不吞错、不美化;
 *   - 不重试、不写入、不保存任何 token/key/password(零持久化、零凭据);
 *   - 纯 ArkTS + @kit.NetworkKit,无页面依赖,可被任何页面/组件复用。
 *
 * DTO 契约(M13-01 第 3a 小步 + M13-05):字段与 Android 冒烟共享只读 mock 契约
 * (tools/android_smoke/mock_contract.py, M12-04/M12-05/M13-05)精确对齐;
 * 审计条目只保留 UI 投影字段,不含 before/after 载荷。
 */

import { http } from '@kit.NetworkKit';
import { UrlPolicyCode, validateBaseUrl, urlPolicyMessage } from './UrlPolicy';

/** 统一 API 结果:如实承载成功与失败 */
export interface ApiResult<T> {
  ok: boolean;
  /** HTTP 状态码;网络层直接失败(未拿到响应)时为 -1 */
  status: number;
  /** 响应体(成功时);失败时为错误描述 */
  data: T | null;
  /** 人类可读错误信息,ok=true 时为 'ok' */
  error: string;
}

/** 只读端点定义 */
export enum AiosEndpoint {
  HEALTH = '/health',
  AUTH_STATUS = '/api/v1/auth/status',
  PRIVACY = '/api/v1/system/privacy',
  VERSION = '/api/v1/version',
  OPS_SNAPSHOT = '/api/v1/system/ops-snapshot',
  AUDIT = '/api/v1/audit?limit=100',
  PAPERS = '/api/v1/papers',
}

/**
 * GET /health — 服务健康
 * mock: {"status": "ok", "service": "aios-mock-android-smoke"}
 */
export interface HealthData {
  status: string;
  service: string;
}

/**
 * GET /api/v1/auth/status — 认证开关状态(只读查询,不含任何凭据)
 * mock: {"auth_enabled": false}
 */
export interface AuthStatusData {
  auth_enabled: boolean;
}

/**
 * GET /api/v1/system/privacy — 隐私/数据合规开关
 * mock: {"model_route": "local", "voice_mode": "local", "search_mode": "local",
 *        "store_audio": false, "send_context_to_cloud": false}
 */
export interface PrivacyData {
  model_route: string;
  voice_mode: string;
  search_mode: string;
  store_audio: boolean;
  send_context_to_cloud: boolean;
}

/**
 * GET /api/v1/version — 服务版本信息
 * mock: {"version": "0.12.5-mock", "git_commit": "m1205mockgit",
 *        "alembic_current": "m1205mockrev", "alembic_head": "m1205mockrev"}
 */
export interface VersionData {
  version: string;
  git_commit: string;
  alembic_current: string;
  alembic_head: string;
}

/**
 * GET /api/v1/system/ops-snapshot — 运维快照
 * 字段与 mock_contract.OPS_SNAPSHOT 精确对齐;各 *_by_* 为动态键计数表。
 */
export interface OpsSnapshotData {
  generated_at: string;
  database_backend: string;
  users_by_role: Record<string, number>;
  papers_total: number;
  resources_by_parse_status: Record<string, number>;
  parse_jobs_by_status: Record<string, number>;
  pending_review_drafts: Record<string, number>;
  voice_sessions_by_status: Record<string, number>;
  search_queries_total: number;
  audit_entries_total: number;
  worker_running: boolean;
}

/**
 * GET /api/v1/audit?limit=100 — 审计日志条目(UI 投影)
 * 刻意不含 before/after 载荷字段:服务端会返回,但 UI 契约不投影、不展示。
 * mock 中 actor_id/actor_username 对系统动作(如 worker.tick)为 null。
 */
export interface AuditEntry {
  id: number;
  actor_id: string | null;
  actor_username: string | null;
  action: string;
  target_type: string;
  target_id: string;
  request_id: string;
  created_at: string;
}

/**
 * GET /api/v1/papers — 论文列表(M13-05)
 * mock: [{"id": "paper-001", "title": "...", ...}] (顶层数组)
 */
export interface PaperOut {
  id: string;
  title: string;
  subtitle: string;
  source: string;
  university: string | null;
  year: number | null;
  subject: string;
  difficulty: string;
  duration_minutes: number;
  tags: Array<string>;
  origin_url: string | null;
}

/** 只允许 GET,硬编码拒绝其它 method */
const METHOD_GET: http.RequestMethod = http.RequestMethod.GET;
/** 读超时 10s,连接超时 10s,单次请求,不重试 */
const TIMEOUT_MS: number = 10000;
/** 2xx 视为成功 */
function is2xx(code: number): boolean {
  return code >= 200 && code < 300;
}

/** 把网络层错误安全转字符串(不抛出) */
function errToString(e: Object | undefined): string {
  if (e === null || e === undefined) {
    return 'unknown error';
  }
  try {
    return `${e}`;
  } catch (err) {
    return 'error toString failed';
  }
}

/** 单次 GET,任何失败如实返回,不重试 */
async function getJson<T>(baseUrl: string, endpoint: AiosEndpoint): Promise<ApiResult<T>> {
  // 1. 先过 URL 白名单校验
  const check = validateBaseUrl(baseUrl);
  if (check.code !== UrlPolicyCode.OK) {
    return { ok: false, status: -1, data: null, error: urlPolicyMessage(check) };
  }

  // 2. 拼接完整 URL:base 已规范化为以 '/' 结尾(或带 path)
  const base: string = check.url.endsWith('/') ? check.url : `${check.url}/`;
  const fullUrl: string = `${base}${endpoint}`;

  const httpClient = http.createHttp();
  try {
    const resp = await httpClient.request(fullUrl, {
      method: METHOD_GET,
      header: { 'content-type': 'application/json', 'accept': 'application/json' },
      expectDataType: http.HttpDataType.STRING,
      usingCache: false,
      connectTimeout: TIMEOUT_MS,
      readTimeout: TIMEOUT_MS
    });

    const status: number = resp.responseCode;
    const body: string = typeof resp.result === 'string' ? resp.result : `${resp.result}`;

    if (!is2xx(status)) {
      return { ok: false, status: status, data: null,
        error: `HTTP ${status}: ${body.substring(0, 200)}` };
    }

    if (body.trim().length === 0) {
      return { ok: false, status: status, data: null, error: '响应体为空' };
    }

    try {
      const parsed = JSON.parse(body) as T;
      return { ok: true, status: status, data: parsed, error: 'ok' };
    } catch (e) {
      return { ok: false, status: status, data: null,
        error: `JSON 解析失败: ${errToString(e)}` };
    }
  } catch (e) {
    // 网络层异常(DNS/超时/拒绝等):如实返回,不重试
    return { ok: false, status: -1, data: null, error: `网络请求失败: ${errToString(e)}` };
  } finally {
    httpClient.destroy();
  }
}

/** 服务健康检查 */
export async function getHealth(baseUrl: string): Promise<ApiResult<HealthData>> {
  return getJson<HealthData>(baseUrl, AiosEndpoint.HEALTH);
}

/** 认证开关状态(只读查询,不保存任何凭据) */
export async function getAuthStatus(baseUrl: string): Promise<ApiResult<AuthStatusData>> {
  return getJson<AuthStatusData>(baseUrl, AiosEndpoint.AUTH_STATUS);
}

/** 隐私/数据合规状态 */
export async function getPrivacy(baseUrl: string): Promise<ApiResult<PrivacyData>> {
  return getJson<PrivacyData>(baseUrl, AiosEndpoint.PRIVACY);
}

/** 服务版本信息 */
export async function getVersion(baseUrl: string): Promise<ApiResult<VersionData>> {
  return getJson<VersionData>(baseUrl, AiosEndpoint.VERSION);
}

/** 运维快照 */
export async function getOpsSnapshot(baseUrl: string): Promise<ApiResult<OpsSnapshotData>> {
  return getJson<OpsSnapshotData>(baseUrl, AiosEndpoint.OPS_SNAPSHOT);
}

/** 审计日志(最近 100 条,固定参数);返回 UI 投影条目,不含 before/after 载荷 */
export async function getAudit(baseUrl: string): Promise<ApiResult<Array<AuditEntry>>> {
  return getJson<Array<AuditEntry>>(baseUrl, AiosEndpoint.AUDIT);
}

/** 论文列表(M13-05);返回 PaperOut 数组 */
export async function getPapers(baseUrl: string): Promise<ApiResult<Array<PaperOut>>> {
  return getJson<Array<PaperOut>>(baseUrl, AiosEndpoint.PAPERS);
}
