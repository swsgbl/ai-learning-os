/**
 * VoiceExamApi.ets — 语音作答会话 API 层(M14-277)
 *
 * 职责(任务书五端点契约):
 *   - POST /api/v1/voice/sessions            body {exam_id} → VoiceSessionOut(201)
 *   - GET  /api/v1/voice/sessions/{id}/resume → ResumeOut(当前题 + 已提交答案)
 *   - POST /api/v1/voice/sessions/{id}/commands body {type:"answer_proposed",
 *     answer, question_id} → CommandOut(状态流转结果;R1 契约修正)
 *   - POST /api/v1/voice/transcribe           multipart(field=audio,answer.wav,
 *     audio/wav)→ 转写文本(TranscriptionOut.text 为权威;任务书摘要写
 *     transcript 字段,解析时 text/transcript 双读,取非空,不猜其他)
 *   - GET  /api/v1/voice/sessions/{id}/report → ReportOut(完卷报告)
 *   - 试卷列表复用 AiosApi.getPapers(既有只读端点,不重复封装)。
 *
 * 模式(与 AiosApi.ets 精确对齐,拆独立文件避免单文件超 400 行):
 *   - ApiResult<T> + safeErrorText;baseUrl 先过 UrlPolicy.validateBaseUrl;
 *   - 5xx/400 固定中文文案兜底(不透出响应体/内部细节);
 *   - 单次请求,不重试,超时 10s(转写上传放宽到 60s——ASR 推理慢);
 *   - sessionId/examId 白名单校验(同 AiosApi.validateExamId 的字符集),
 *     校验失败不发起网络请求;
 *   - multipart 手工构造(boundary + 文本 part 头 + WAV 字节 + 尾界),
 *     整体 ArrayBuffer 作为 extraData 上送。
 */

import { http } from '@kit.NetworkKit';
import { safeErrorText } from '../ErrorText';
import { UrlPolicyCode, validateBaseUrl, urlPolicyMessage } from '../UrlPolicy';

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

/** POST .../commands 请求体(MVP 固定 answer_proposed)。
 * R1(supervisor): 服务端 VoiceCommandRequest 契约字段为 answer +
 * question_id(缺一 422),非 answer_text。 */
interface AnswerProposedRequest {
  type: string;
  answer: string;
  question_id: string;
}

/** GET .../resume 响应 */
export interface VoiceResumeOut {
  session: VoiceSessionOut;
  question: VoiceResumeQuestion | null;
  committed_answer: string | null;
  question_total: number;
}

/** POST .../commands 响应(投影字段) */
export interface VoiceCommandOut {
  applied_event: string;
  from_status: string;
  session: VoiceSessionOut;
}

/** GET .../report 响应(mistake/remediation 摘要数组以 JSON 原样承载) */
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

const TIMEOUT_MS: number = 10000;
const TRANSCRIBE_TIMEOUT_MS: number = 60000;
const SESSION_ID_ALLOWED_RE: RegExp = /^[A-Za-z0-9._:-]+$/;
const SESSION_ID_MAX: number = 128;
const BAD_REQUEST_TEXT: string = '请求被服务拒绝 (HTTP 400):请检查请求参数';
const SERVER_ERROR_TEXT: string = '服务内部错误 (HTTP 5xx):请稍后重试或联系管理员';

function is2xx(code: number): boolean {
  return code >= 200 && code < 300;
}

/** id 白名单校验(exam_id/session_id 同规则);返回 '' 表示合法 */
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

/** ASCII 字符串 → Uint8Array(multipart 头部仅含 ASCII,逐字符 charCode) */
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

/** 非 2xx → ApiResult 失败(文案收敛) */
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

/** JSON POST 通用(默认头;超时 10s) */
async function postJson<T>(baseUrl: string, path: string, bodyObj: CreateSessionRequest | AnswerProposedRequest): Promise<VoiceApiResult<T>> {
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
      header: { 'content-type': 'application/json', 'accept': 'application/json' },
      extraData: bodyText,
      expectDataType: http.HttpDataType.STRING,
      usingCache: false,
      connectTimeout: TIMEOUT_MS,
      readTimeout: TIMEOUT_MS
    });
    if (!is2xx(resp.responseCode)) {
      return httpError<T>(resp.responseCode);
    }
    return parseJson<T>(resp.responseCode, typeof resp.result === 'string' ? resp.result : `${resp.result}`);
  } catch (e) {
    return { ok: false, status: -1, data: null, error: `网络请求失败: ${safeErrorText(e)}` };
  } finally {
    client.destroy();
  }
}

/** JSON GET 通用 */
async function getJson<T>(baseUrl: string, path: string): Promise<VoiceApiResult<T>> {
  const base: string | null = normalizeBase(baseUrl);
  if (base === null) {
    return { ok: false, status: -1, data: null, error: '服务地址未配置或不合法' };
  }
  const client = http.createHttp();
  try {
    const resp = await client.request(`${base}${path}`, {
      method: http.RequestMethod.GET,
      header: { 'accept': 'application/json' },
      expectDataType: http.HttpDataType.STRING,
      usingCache: false,
      connectTimeout: TIMEOUT_MS,
      readTimeout: TIMEOUT_MS
    });
    if (!is2xx(resp.responseCode)) {
      return httpError<T>(resp.responseCode);
    }
    return parseJson<T>(resp.responseCode, typeof resp.result === 'string' ? resp.result : `${resp.result}`);
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
 * 会话命令:POST /api/v1/voice/sessions/{id}/commands
 * MVP 只使用 answer_proposed(answer = 服务端转写文本,question_id =
 * 当前题 id——R1 对齐服务端契约);
 * 服务端 FSM 拒绝非法迁移(409)如实返回,由调用方 resume 对齐。
 */
export async function postAnswerProposed(baseUrl: string, sessionId: string,
  questionId: string, answerText: string): Promise<VoiceApiResult<VoiceCommandOut>> {
  const invalid: string = validateId(sessionId);
  if (invalid !== '') {
    return { ok: false, status: -1, data: null, error: invalid };
  }
  const text: string = answerText.trim();
  if (text.length === 0) {
    return { ok: false, status: -1, data: null, error: '答案文本为空' };
  }
  const req: AnswerProposedRequest = { type: 'answer_proposed', answer: text, question_id: questionId };
  return postJson<VoiceCommandOut>(
    baseUrl,
    `/api/v1/voice/sessions/${encodeURIComponent(sessionId.trim())}/commands`,
    req
  );
}

/** 完卷报告:GET /api/v1/voice/sessions/{id}/report */
export async function getVoiceReport(baseUrl: string, sessionId: string): Promise<VoiceApiResult<VoiceReportOut>> {
  const invalid: string = validateId(sessionId);
  if (invalid !== '') {
    return { ok: false, status: -1, data: null, error: invalid };
  }
  return getJson<VoiceReportOut>(baseUrl, `/api/v1/voice/sessions/${encodeURIComponent(sessionId.trim())}/report`);
}

/** multipart boundary(固定随机段,不与 WAV 内容冲突即可) */
const MULTIPART_BOUNDARY: string = '----M14277VoiceBoundary7f3a9c';

/**
 * WAV 上传转写:POST /api/v1/voice/transcribe(multipart,field=audio,
 * filename=answer.wav,MIME=audio/wav)。整体 body 为 ArrayBuffer;
 * 超时放宽 60s(ASR 推理时延)。转写文本读取:text 优先、transcript 兜底。
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

  const client = http.createHttp();
  try {
    const resp = await client.request(`${base}/api/v1/voice/transcribe`, {
      method: http.RequestMethod.POST,
      header: {
        'content-type': `multipart/form-data; boundary=${MULTIPART_BOUNDARY}`,
        'accept': 'application/json'
      },
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
