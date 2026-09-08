/**
 * AiosApi.ets — 只读 GET API 封装(M13-01 第 1/3a 小步 + M13-05 论文端点
 * + M13-07 语音端点 + M13-08 考试会话只读端点)
 *
 * 职责边界(刻意收窄):
 *   - 仅封装 10 个只读端点:/health、/api/v1/auth/status、/api/v1/system/privacy、
 *     /api/v1/version、/api/v1/system/ops-snapshot、/api/v1/audit?limit=100、
 *     /api/v1/papers(M13-05)、/api/v1/search/providers(M13-06)、
 *     /api/v1/voice/providers(M13-07)、
 *     /api/v1/exams/{exam_id}(M13-08,动态路径,examId 白名单校验 + encodeURIComponent);
 *   - 每次请求前先经 UrlPolicy.validateBaseUrl 校验 base URL,不合法直接返回失败;
 *   - 失败/错误如实返回(ApiResult.ok=false + 原始错误信息),不吞错、不美化;
 *   - 不重试、不写入、不保存任何 token/key/password(零持久化、零凭据);
 *   - 纯 ArkTS + @kit.NetworkKit,无页面依赖,可被任何页面/组件复用。
 *
 * DTO 契约(M13-01 第 3a 小步 + M13-05 + M13-06 + M13-07 + M13-08):字段与
 * Android 冒烟共享只读 mock 契约(tools/android_smoke/mock_contract.py,
 * M12-04/M12-05/M13-05/M13-08)及 Android DTO(apps/android ExamDtos.kt)精确对齐;
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

/** 只读端点定义(固定路径枚举;M13-08 的动态路径不进枚举,见 EXAM_PATH_PREFIX) */
export enum AiosEndpoint {
  HEALTH = '/health',
  AUTH_STATUS = '/api/v1/auth/status',
  PRIVACY = '/api/v1/system/privacy',
  VERSION = '/api/v1/version',
  OPS_SNAPSHOT = '/api/v1/system/ops-snapshot',
  AUDIT = '/api/v1/audit?limit=100',
  PAPERS = '/api/v1/papers',
  SEARCH_PROVIDERS = '/api/v1/search/providers',
  VOICE_PROVIDERS = '/api/v1/voice/providers',
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

/**
 * GET /api/v1/search/providers — 搜索 provider 列表(M13-06)
 * mock: {"items": [{"name": "...", "kind": "...", "enabled": true,
 *        "unavailable_reason": null}, ...]}
 */
export interface SearchProviderOut {
  name: string;
  kind: string;
  enabled: boolean;
  unavailable_reason: string | null;
}

/** GET /api/v1/search/providers 响应结构(M13-06) */
export interface SearchProvidersData {
  items: Array<SearchProviderOut>;
}

/**
 * GET /api/v1/voice/providers — 语音 provider 单项视图(M13-07)
 * 字段与 Android VoiceDtos.kt VoiceProviderViewResponse 精确对齐:
 * requested 为请求指定的 provider(未指定时为 null),provider 为实际生效者,
 * fallback 表示是否因请求项不可用而回退。
 * mock: {"requested": null, "provider": "...", "fallback": false}
 */
export interface VoiceProviderViewOut {
  requested: string | null;
  provider: string;
  fallback: boolean;
}

/**
 * GET /api/v1/voice/providers 响应结构(M13-07)
 * 字段与 Android VoiceDtos.kt VoiceProvidersResponse 精确对齐(snake_case 原样保留)。
 * mock: {"voice_mode": "local", "asr": {...}, "tts": {...},
 *        "privacy_store_audio": false, "privacy_send_context_to_cloud": false}
 */
export interface VoiceProvidersData {
  voice_mode: string;
  asr: VoiceProviderViewOut;
  tts: VoiceProviderViewOut;
  privacy_store_audio: boolean;
  privacy_send_context_to_cloud: boolean;
}

/**
 * GET /api/v1/exams/{exam_id} — 选项(M13-08)
 * 字段与 mock_contract.EXAM_SESSION 的 questions[].options[] 及
 * Android ExamDtos.kt OptionResponse 精确对齐(snake_case 原样保留)。
 * mock: {"key": "A", "text": "Recurrent layers"}
 */
export interface QuestionOptionOut {
  key: string;
  text: string;
}

/**
 * GET /api/v1/exams/{exam_id} — 公共题目投影(M13-08)
 * 刻意不含正确答案/解析:与 mock 契约一致只下发 id/type/stem/options。
 * 字段与 Android ExamDtos.kt PublicQuestionResponse 精确对齐。
 */
export interface PublicQuestionOut {
  id: string;
  type: string;
  stem: string;
  options: Array<QuestionOptionOut>;
}

/**
 * GET /api/v1/exams/{exam_id} — 考试会话只读快照(M13-08)
 * 字段与 mock_contract.EXAM_SESSION 及 Android ExamDtos.kt ExamSessionResponse
 * 逐字段一一对应(snake_case 原样保留):
 *   - server_started_at/server_end_at/server_remaining_seconds 为服务端权威时间窗;
 *   - questions 为公共投影,不含正确答案/解析;
 *   - answers 只含 learner 已保存作答(键=题目 id,值=所选选项 key);
 *   - next_sequence 为服务端权威序号(最后已作答事件 sequence+1)。
 * 本端点只读:不提供任何作答/提交路径。
 */
export interface ExamSessionData {
  exam_id: string;
  paper_id: string;
  paper_title: string;
  mode: string;
  status: string;
  server_started_at: string;
  server_end_at: string;
  server_remaining_seconds: number;
  questions: Array<PublicQuestionOut>;
  answers: Record<string, string>;
  next_sequence: number;
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

/**
 * M13-08 动态端点:/api/v1/exams/{exam_id} 的固定前缀。
 * 刻意不放进 AiosEndpoint 枚举:枚举只承载完整固定路径,避免把"半个路径"
 * 当作端点直接请求;动态路径由 getExamSession 在校验 + encodeURIComponent 后拼出。
 */
const EXAM_PATH_PREFIX: string = '/api/v1/exams/';

/** examId 允许字符白名单:字母、数字、'.'、'_'、'-'、':' */
const EXAM_ID_ALLOWED_RE: RegExp = /^[A-Za-z0-9._:-]+$/;
/** examId 长度上限 */
const EXAM_ID_MAX_LENGTH: number = 128;

/**
 * 校验 examId(在发起任何网络请求之前执行)。
 * 规则(依次):
 *   1. 先 trim(首尾空白去除后,合法 id 不含任何空白);
 *   2. 非空;
 *   3. 长度 <= 128;
 *   4. 不含任何控制字符(U+0000-U+001F、U+007F-U+009F);
 *   5. 不含 '/'、'?'、'#'(路径分隔与查询/片段起始符,防注入路径结构);
 *   6. 仅允许 [A-Za-z0-9._:-]。
 * 返回 '' 表示合法;否则返回中文错误信息(校验失败不发起网络请求)。
 */
function validateExamId(rawExamId: string): string {
  const examId: string = rawExamId.trim();
  if (examId.length === 0) {
    return 'examId 不能为空';
  }
  if (examId.length > EXAM_ID_MAX_LENGTH) {
    return `examId 长度超限(>${EXAM_ID_MAX_LENGTH} 字符)`;
  }
  // 控制字符检查覆盖 U+0000-U+001F(C0)、U+007F(DEL)、U+0080-U+009F(C1);
  // JS 字符串按 UTF-16 code unit 遍历,charCodeAt 覆盖全部 BMP 控制字符,
  // 代理对(code unit >= 0xD800)不属于控制字符,由第 6 步白名单兜底拒绝。
  for (let i: number = 0; i < examId.length; i++) {
    const cc: number = examId.charCodeAt(i);
    if (cc < 0x20 || (cc >= 0x7F && cc <= 0x9F)) {
      return 'examId 含控制字符';
    }
  }
  for (let i: number = 0; i < examId.length; i++) {
    const ch: string = examId.charAt(i);
    if (ch === '/' || ch === '?' || ch === '#') {
      return 'examId 含非法字符(/、?、#)';
    }
  }
  if (!EXAM_ID_ALLOWED_RE.test(examId)) {
    return 'examId 仅允许字母、数字与 . _ - :';
  }
  return '';
}

/**
 * 单次 GET,任何失败如实返回,不重试。
 * endpoint:固定枚举端点,或经校验+编码的动态完整路径(M13-08 起支持 string;
 * 动态路径仅由 getExamSession 内部构造,不对外提供通用 path API)。
 */
async function getJson<T>(baseUrl: string, endpoint: AiosEndpoint | string): Promise<ApiResult<T>> {
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

/** 搜索 providers 列表(M13-06);返回 {items: [...]} */
export async function getSearchProviders(baseUrl: string): Promise<ApiResult<SearchProvidersData>> {
  return getJson<SearchProvidersData>(baseUrl, AiosEndpoint.SEARCH_PROVIDERS);
}

/** 语音 providers 只读视图(M13-07);返回 {voice_mode, asr, tts, privacy_*} */
export async function getVoiceProviders(baseUrl: string): Promise<ApiResult<VoiceProvidersData>> {
  return getJson<VoiceProvidersData>(baseUrl, AiosEndpoint.VOICE_PROVIDERS);
}

/**
 * 考试会话只读快照(M13-08);GET /api/v1/exams/{exam_id}。
 * examId 先过白名单校验(不合法直接返回,不发起任何网络请求),
 * 再 encodeURIComponent 拼入路径;单次 GET,无重试、无写入、无任何凭据处理。
 */
export async function getExamSession(baseUrl: string, examId: string): Promise<ApiResult<ExamSessionData>> {
  const invalidReason: string = validateExamId(examId);
  if (invalidReason !== '') {
    return { ok: false, status: -1, data: null, error: invalidReason };
  }
  const endpoint: string = `${EXAM_PATH_PREFIX}${encodeURIComponent(examId.trim())}`;
  return getJson<ExamSessionData>(baseUrl, endpoint);
}
