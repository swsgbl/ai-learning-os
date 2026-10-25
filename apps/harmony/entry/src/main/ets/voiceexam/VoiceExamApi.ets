/**
 * VoiceExamApi.ets — 语音作答会话 API 层(M14-277 · hm-parity v5 命令集补全)
 *
 * 职责(任务书五端点契约):
 *   - POST /api/v1/voice/sessions            body {exam_id} → VoiceSessionOut(201)
 *   - GET  /api/v1/voice/sessions/{id}/resume → ResumeOut(当前题 + 已提交答案)
 *   - POST /api/v1/voice/sessions/{id}/commands body {type,...} → CommandOut
 *     v5:8 条 FSM 命令通用透传(answer_proposed/question_read/options_read/
 *     start_reading/commit_confirmed/report_ready/skip/end),服务端
 *     voice_session_fsm.py 是唯一权威,客户端不推演状态
 *   - POST /api/v1/voice/transcribe           multipart(field=audio,answer.wav,
 *     audio/wav)→ 转写文本(text/transcript 双读)
 *   - GET  /api/v1/voice/sessions/{id}/report → ReportOut(完卷报告)
 *   - 试卷列表复用 AiosApi.getPapers(既有只读端点,不重复封装)
 *
 * hm-parity v5 变更:
 *   - 全端点附 Authorization(buildProtectedHeader,内存 bearer fail-closed)——
 *     服务端 _require_owned_session 要求 owner,无 token = 404 如实展示
 *   - 4xx(409/404/503)尽力提取服务端 JSON detail 字符串如实展示
 *     (FSM 非法迁移/终态拒绝的语义依赖此口径);400 固定文案;5xx 固定兜底
 *   - VoiceCommandOut 补 clarified_question/question_total(服务端契约字段)
 */

import { http } from '@kit.NetworkKit';
import { safeErrorText } from '../ErrorText';
import { UrlPolicyCode, validateBaseUrl } from '../UrlPolicy';
import { buildProtectedHeader } from '../AiosApi';

/** 复用 AiosApi 的统一结果形状(结构等价,模块内重新声明避免循环依赖) */
export interface VoiceApiResult<T> {
  ok: boolean;
  status: number;
  data: T | null;
  error: string;
}

/** POST /voice/sessions 响应(VoiceSessionOut,snake_case 原样) */
export interface VoiceSessionOut {
  session_id: string;
  exam_id: string;
  status: string;
  question_index: number;
  revision: number;
  created_at: string;
  updated_at: string;
}

/** resume 当前题选项公共投影(key/text) */
export interface VoiceResumeOption {
  key: string;
  text: string;
}

/** resume 当前题公共投影(不含正确答案/解析) */
export interface VoiceResumeQuestion {
  id: string;
  type: string;
  stem: string;
  options: Array<VoiceResumeOption>;
}

/** POST /voice/sessions 请求体 */
interface CreateSessionRequest {
  exam_id: string;
}

/**
 * POST .../commands 请求体(服务端 VoiceCommandRequest 契约):
 * type 必填;answer_proposed 需 answer + question_id(缺一 422);
 * ambiguous=true 时服务端转 answer_clarify 进澄清;
 * expected_revision 可选乐观锁(本端不传,以服务端为准)。
 */
export interface VoiceCommandRequest {
  type: string;
  question_id?: string;
  answer?: string;
  ambiguous?: boolean;
  expected_revision?: number;
}

/** GET .../resume 响应 */
export interface VoiceResumeOut {
  session: VoiceSessionOut;
  question: VoiceResumeQuestion | null;
  committed_answer: string | null;
  question_total: number;
}

/**
 * POST .../commands 响应(v5 对齐服务端 VoiceCommandOut 全字段):
 * clarified_question = 进 CLARIFYING 时 TTS 追问文案;
 * question_total = report_ready 全卷完成时透出(播报收尾)。
 */
export interface VoiceCommandOut {
  applied_event: string;
  from_status: string;
  session: VoiceSessionOut;
  clarified_question?: string | null;
  question_total?: number | null;
}

/** GET .../report 响应(mistake/remediation 摘要数组由 JSON 原样承载) */
export interface VoiceReportOut {
  session_id: string;
  exam_id: string;
  spoken_text: string;
  mistake_summary: Array<Record<string, string>>;
  remediation_summary: Array<Record<string, string>>;
  written_report_url: string;
}

/** 转写结果(text 为服务端 FunASR 权威 transcript) */
export interface VoiceTranscribeOut {
  text: string;
}

/** FSM 8 命令字面量(与 services/api/app/domain/voice_session_fsm.py 事件一一对应) */
export const VOICE_CMD_START_READING: string = 'start_reading';
export const VOICE_CMD_QUESTION_READ: string = 'question_read';
export const VOICE_CMD_OPTIONS_READ: string = 'options_read';
export const VOICE_CMD_ANSWER_PROPOSED: string = 'answer_proposed';
export const VOICE_CMD_COMMIT_CONFIRMED: string = 'commit_confirmed';
export const VOICE_CMD_REPORT_READY: string = 'report_ready';
export const VOICE_CMD_SKIP: string = 'skip';
export const VOICE_CMD_END: string = 'end';

const TIMEOUT_MS: number = 10000;
const TRANSCRIBE_TIMEOUT_MS: number = 60000;
const SESSION_ID_ALLOWED_RE: RegExp = /^[A-Za-z0-9._:-]+$/;
const SESSION_ID_MAX: number = 128;
const BAD_REQUEST_TEXT: string = '请求被服务拒绝 (HTTP 400):请检查请求参数';
const SERVER_ERROR_TEXT: string = '服务内部错误 (HTTP 5xx):请稍后重试或联系管理员';

function is2xx(code: number): boolean {
  return code >= 200 && code < 300;
}

/** id 白名单校验(exam_id/session_id 同规则;返回 '' 表示合法) */
function validateId(raw: string): string {
  const id: string = raw.trim();
  if (id.length === 0 || id.length > SESSION_ID_MAX) {
    return 'id 为空或超长';
  }
  if (!SESSION_ID_ALLOWED_RE.test(id)) {
    return 'id 含非法字符(仅允许字母数字 . _ - :)';
  }
  return '';
}

/** ASCII 字符串 → Uint8Array(multipart 头部仅含 ASCII,逐字节 charCode) */
function asciiBytes(text: string): Uint8Array {
  const out: Uint8Array = new Uint8Array(text.length);
  for (let i: number = 0; i < text.length; i++) {
    out[i] = text.charCodeAt(i) & 0xFF;
  }
  return out;
}

/** baseUrl 校验 + 规范化(剥尾部 '/');失败返回 null,由调用方生成 ApiResult */
function normalizeBase(baseUrl: string): string | null {
  const check = validateBaseUrl(baseUrl);
  if (check.code !== UrlPolicyCode.OK) {
    return null;
  }
  return check.url.endsWith('/') ? check.url.substring(0, check.url.length - 1) : check.url;
}

/** 非 2xx → ApiResult 失败(固定文案兜底) */
function httpError<T>(status: number): VoiceApiResult<T> {
  let reason: string;
  if (status >= 500) {
    reason = SERVER_ERROR_TEXT;
  } else if (status === 400 || status === 422) {
    reason = BAD_REQUEST_TEXT;
  } else {
    reason = '';
  }
  return { ok: false, status, data: null, error: reason.length > 0 ? reason : `请求失败 (HTTP ${status})` };
}

/**
 * 非 2xx → ApiResult 失败(v5:409/404/503 尽力提取服务端 JSON detail
 * 字符串如实展示——FSM 拒绝迁移/终态/不存在语义依赖;400 固定文案不回显;
 * 5xx 提取不到 detail 用固定兜底)。语义与 AiosApi.requestJson 完全一致。
 */
function httpErrorDetail<T>(status: number, body: string): VoiceApiResult<T> {
  if (status !== 400 && status !== 422) {
    try {
      const parsedErr = JSON.parse(body) as Record<string, Object>;
      const d: Object | undefined = parsedErr['detail'];
      if (typeof d === 'string' && d.length > 0) {
        return { ok: false, status, data: null, error: `请求失败 (HTTP ${status}): ${d}` };
      }
    } catch (e) {
      // detail 提取失败走兜底
    }
  }
  if (status >= 500) {
    return { ok: false, status, data: null, error: SERVER_ERROR_TEXT };
  }
  return httpError<T>(status);
}

/** JSON 响应解析(体空/解析失败如实报错) */
function parseJson<T>(status: number, body: string): VoiceApiResult<T> {
  if (body.trim().length === 0) {
    return { ok: false, status, data: null, error: '响应体为空' };
  }
  try {
    return { ok: true, status, data: JSON.parse(body) as T, error: 'ok' };
  } catch (e) {
    return { ok: false, status, data: null, error: `JSON 解析失败: ${safeErrorText(e)}` };
  }
}

/** JSON POST 通用(默认超时 10s;v5:附 Authorization + 4xx detail 透出) */
async function postJson<T>(baseUrl: string, path: string, bodyObj: CreateSessionRequest | VoiceCommandRequest): Promise<VoiceApiResult<T>> {
  const base: string | null = normalizeBase(baseUrl);
  if (base === null) {
    return { ok: false, status: -1, data: null, error: '服务地址未配置或不合法' };
  }
  let bodyText: string;
  try {
    bodyText = JSON.stringify(bodyObj);
  } catch (e) {
    return { ok: false, status: -1, data: null, error: '请求构造失败' };
  }
  const client = http.createHttp();
  try {
    const resp = await client.request(`${base}${path}`, {
      method: http.RequestMethod.POST,
      header: buildProtectedHeader(),
      extraData: bodyText,
      expectDataType: http.HttpDataType.STRING,
      usingCache: false,
      connectTimeout: TIMEOUT_MS,
      readTimeout: TIMEOUT_MS
    });
    const bodyStr: string = typeof resp.result === 'string' ? resp.result : `${resp.result}`;
    if (!is2xx(resp.responseCode)) {
      return httpErrorDetail<T>(resp.responseCode, bodyStr);
    }
    return parseJson<T>(resp.responseCode, bodyStr);
  } catch (e) {
    return { ok: false, status: -1, data: null, error: `网络请求失败: ${safeErrorText(e)}` };
  } finally {
    client.destroy();
  }
}

/** JSON GET 通用(v5:附 Authorization + 4xx detail 透出) */
async function getJson<T>(baseUrl: string, path: string): Promise<VoiceApiResult<T>> {
  const base: string | null = normalizeBase(baseUrl);
  if (base === null) {
    return { ok: false, status: -1, data: null, error: '服务地址未配置或不合法' };
  }
  const client = http.createHttp();
  try {
    const resp = await client.request(`${base}${path}`, {
      method: http.RequestMethod.GET,
      header: buildProtectedHeader(),
      expectDataType: http.HttpDataType.STRING,
      usingCache: false,
      connectTimeout: TIMEOUT_MS,
      readTimeout: TIMEOUT_MS
    });
    const bodyStr: string = typeof resp.result === 'string' ? resp.result : `${resp.result}`;
    if (!is2xx(resp.responseCode)) {
      return httpErrorDetail<T>(resp.responseCode, bodyStr);
    }
    return parseJson<T>(resp.responseCode, bodyStr);
  } catch (e) {
    return { ok: false, status: -1, data: null, error: `网络请求失败: ${safeErrorText(e)}` };
  } finally {
    client.destroy();
  }
}

/** 创建语音会话:POST /api/v1/voice/sessions body {exam_id} */
export async function createVoiceSession(baseUrl: string, examId: string): Promise<VoiceApiResult<VoiceSessionOut>> {
  const invalid: string = validateId(examId);
  if (invalid !== '') {
    return { ok: false, status: -1, data: null, error: invalid };
  }
  const req: CreateSessionRequest = { exam_id: examId.trim() };
  return postJson<VoiceSessionOut>(baseUrl, '/api/v1/voice/sessions', req);
}

/** 会话恢复(当前题 + 已提交答案):GET /api/v1/voice/sessions/{id}/resume */
export async function resumeVoiceSession(baseUrl: string, sessionId: string): Promise<VoiceApiResult<VoiceResumeOut>> {
  const invalid: string = validateId(sessionId);
  if (invalid !== '') {
    return { ok: false, status: -1, data: null, error: invalid };
  }
  return getJson<VoiceResumeOut>(baseUrl, `/api/v1/voice/sessions/${encodeURIComponent(sessionId.trim())}/resume`);
}

/**
 * 会话命令(通用):POST /api/v1/voice/sessions/{id}/commands
 * v5:8 条 FSM 命令(start_reading/question_read/options_read/answer_proposed/
 * commit_confirmed/report_ready/skip/end)统一透传,服务端 FSM 校验迁移,
 * 409(非法迁移)/404(会话不存在)detail 如实返回,由调用方 resume 对齐。
 */
export async function sendVoiceCommand(baseUrl: string, sessionId: string,
  req: VoiceCommandRequest): Promise<VoiceApiResult<VoiceCommandOut>> {
  const invalid: string = validateId(sessionId);
  if (invalid !== '') {
    return { ok: false, status: -1, data: null, error: invalid };
  }
  return postJson<VoiceCommandOut>(
    baseUrl,
    `/api/v1/voice/sessions/${encodeURIComponent(sessionId.trim())}/commands`,
    req
  );
}

/**
 * 提交答案(answer_proposed 便捷封装):answer = 服务端转写文本,
 * question_id = 当前题 id(R1 对齐服务端契约;ambiguous=true 走 answer_clarify)。
 */
export async function postAnswerProposed(baseUrl: string, sessionId: string,
  questionId: string, answerText: string, ambiguous: boolean = false): Promise<VoiceApiResult<VoiceCommandOut>> {
  const text: string = answerText.trim();
  if (text.length === 0) {
    return { ok: false, status: -1, data: null, error: '答案文本为空' };
  }
  const req: VoiceCommandRequest = {
    type: VOICE_CMD_ANSWER_PROPOSED,
    answer: text,
    question_id: questionId,
    ambiguous: ambiguous
  };
  return sendVoiceCommand(baseUrl, sessionId, req);
}

/** 完卷报告:GET /api/v1/voice/sessions/{id}/report */
export async function getVoiceReport(baseUrl: string, sessionId: string): Promise<VoiceApiResult<VoiceReportOut>> {
  const invalid: string = validateId(sessionId);
  if (invalid !== '') {
    return { ok: false, status: -1, data: null, error: invalid };
  }
  return getJson<VoiceReportOut>(baseUrl, `/api/v1/voice/sessions/${encodeURIComponent(sessionId.trim())}/report`);
}

/** multipart boundary(固定随机串,不与 WAV 内容冲突即可) */
const MULTIPART_BOUNDARY: string = '----M14277VoiceBoundary7f3a9c';

/**
 * WAV 上传转写:POST /api/v1/voice/transcribe(multipart,field=audio,
 * filename=answer.wav,MIME=audio/wav)。整体 body 为 ArrayBuffer;
 * 超时放宽 60s(ASR 推理时延)。转写文本读取 text 优先、transcript 兜底。
 * v5:请求头附 Authorization(transcribe 按 owner 解析 provider key)。
 */
export async function transcribeWav(baseUrl: string, wav: Uint8Array): Promise<VoiceApiResult<VoiceTranscribeOut>> {
  const base: string | null = normalizeBase(baseUrl);
  if (base === null) {
    return { ok: false, status: -1, data: null, error: '服务地址未配置或不合法' };
  }
  // 手工构造 multipart:text 部分(头 + 文件)+ 尾界,全部拼入一个 Uint8Array
  const headText: string =
    `--${MULTIPART_BOUNDARY}\r\n` +
    `Content-Disposition: form-data; name="audio"; filename="answer.wav"\r\n` +
    `Content-Type: audio/wav\r\n\r\n`;
  const tailText: string = `\r\n--${MULTIPART_BOUNDARY}--\r\n`;
  const head: Uint8Array = asciiBytes(headText);
  const tail: Uint8Array = asciiBytes(tailText);
  const body: Uint8Array = new Uint8Array(head.length + wav.length + tail.length);
  body.set(head, 0);
  body.set(wav, head.length);
  body.set(tail, head.length + wav.length);
  const bodyBuffer: ArrayBuffer = body.buffer.slice(
    body.byteOffset, body.byteOffset + body.byteLength
  ) as ArrayBuffer;

  // multipart 头:保留 content-type 边界声明,叠加 Authorization(若有)
  const header: Record<string, string> = buildProtectedHeader();
  header['content-type'] = `multipart/form-data; boundary=${MULTIPART_BOUNDARY}`;

  const client = http.createHttp();
  try {
    const resp = await client.request(`${base}/api/v1/voice/transcribe`, {
      method: http.RequestMethod.POST,
      header: header,
      extraData: bodyBuffer,
      expectDataType: http.HttpDataType.STRING,
      usingCache: false,
      connectTimeout: TIMEOUT_MS,
      readTimeout: TRANSCRIBE_TIMEOUT_MS
    });
    if (!is2xx(resp.responseCode)) {
      return httpError<VoiceTranscribeOut>(resp.responseCode);
    }
    const bodyStr: string = typeof resp.result === 'string' ? resp.result : `${resp.result}`;
    const parsed: VoiceApiResult<Object> = parseJson<Object>(resp.responseCode, bodyStr);
    if (!parsed.ok || parsed.data === null) {
      return { ok: false, status: parsed.status, data: null, error: parsed.error };
    }
    const obj: Record<string, Object> = parsed.data as Record<string, Object>;
    const rawText: Object | undefined = obj['text'];
    const rawTranscript: Object | undefined = obj['transcript'];
    const fromText: string = typeof rawText === 'string' ? rawText : '';
    const fromTranscript: string = typeof rawTranscript === 'string' ? rawTranscript : '';
    const text: string = fromText.length > 0 ? fromText : fromTranscript;
    if (text.length === 0) {
      return { ok: false, status: parsed.status, data: null, error: '转写响应缺少文本字段' };
    }
    return { ok: true, status: parsed.status, data: { text }, error: 'ok' };
  } catch (e) {
    return { ok: false, status: -1, data: null, error: `转写上传失败: ${safeErrorText(e)}` };
  } finally {
    client.destroy();
  }
}
