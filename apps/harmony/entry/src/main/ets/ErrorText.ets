/**
 * ErrorText.ets — 异常 → 用户可读文本的统一收敛(M13-09 生产加固)
 *
 * 职责边界(刻意收窄):
 *   - 只做一件事:把任意底层异常安全转换为「有界的用户可读文本」;
 *   - 三条硬保证:
 *     1) 绝不输出 [object Object] —— 白名单提取 code/message 字段,
 *        裸对象/空串一律落入固定中文兜底文案;
 *     2) 绝不序列化整个异常对象(不 JSON.stringify)—— 避免把
 *        URL、令牌、响应体等随对象字段带出到 UI;
 *     3) 自身绝不抛出 —— 任何提取步骤失败都有静态兜底,不引入新崩溃面;
 *   - 文本有上界(200 字符),防止异常 message 本身超长撑爆 UI;
 *   - 纯函数、无状态、无 IO,可被任何模块复用(AiosApi / SettingsStore 等)。
 *
 * 使用方约定:仅用于「面向用户的错误文案生成」;
 * 日志/排障需要完整原始错误时由调用方自行决定,本模块不做日志。
 */

/** 中文兜底文案(提取彻底失败时使用) */
const FALLBACK_TEXT: string = '未知错误';

/** 提取文本的最大长度(有界,超出截断) */
const MAX_TEXT_LENGTH: number = 200;

/**
 * 从异常对象白名单提取 code(数字)。
 * 只认 number 类型的 code 字段(BusinessError 形态);
 * 其余一律返回 -1(不猜测、不做隐式转换)。
 */
export function extractErrorCode(e: Object | undefined): number {
  if (e === null || e === undefined) {
    return -1;
  }
  try {
    const rec: Record<string, Object> = e as Record<string, Object>;
    const raw: Object | undefined = rec['code'];
    if (typeof raw === 'number') {
      return raw;
    }
  } catch (err) {
    return -1;
  }
  return -1;
}

/**
 * 从异常对象白名单提取 message(字符串)。
 * 只认 string 类型的 message 字段;其余返回 ''(不猜测)。
 */
function extractMessage(e: Object | undefined): string {
  if (e === null || e === undefined) {
    return '';
  }
  try {
    const rec: Record<string, Object> = e as Record<string, Object>;
    const raw: Object | undefined = rec['message'];
    if (typeof raw === 'string') {
      return raw;
    }
  } catch (err) {
    return '';
  }
  return '';
}

/** 有界截断(按字符数,不抛出) */
function bounded(text: string): string {
  if (text.length <= MAX_TEXT_LENGTH) {
    return text;
  }
  return text.substring(0, MAX_TEXT_LENGTH);
}

/**
 * 异常 → 用户可读文本(永不抛出、永不 [object Object])。
 * 提取优先级:message 字段 > toString 结果;
 * 存在数字 code 时前置展示(便于排障对照,数字本身不含敏感信息)。
 */
export function safeErrorText(e: Object | undefined): string {
  const code: number = extractErrorCode(e);
  const message: string = extractMessage(e);
  const codeHead: string = code > 0 ? `${code} ` : '';
  if (message.length > 0) {
    return bounded(`${codeHead}${message}`);
  }
  if (e === null || e === undefined) {
    return FALLBACK_TEXT;
  }
  let viaToString: string = '';
  try {
    viaToString = `${e}`;
  } catch (err) {
    viaToString = '';
  }
  if (viaToString.length === 0 || viaToString === '[object Object]') {
    return FALLBACK_TEXT;
  }
  return bounded(`${codeHead}${viaToString}`);
}
