/**
 * UrlPolicy.ets — 可复用的 URL 安全校验(M13-01 第 1 小步)
 *
 * 规则(base URL 白名单校验,只允许 http/https):
 *   1. 非空字符串且无前后空白,不含任何控制字符(U+0000..U+001F、U+007F);
 *   2. 不含反斜杠 '\'(防 '\'/':' 混淆绕过);
 *   3. 协议只能是 http 或 https(小写精确匹配,scheme 后必须紧跟 '://');
 *   4. host 必填,不允许 userinfo(整条 URL 禁止出现 '@' 在 authority 段);
 *   5. port 若存在必须是纯数字(0..65535);
 *   6. path 只允许安全子集 [A-Za-z0-9._~/:-],无 query、无 fragment
 *      (query/hash 由各 API 方法自己拼接,base URL 保持纯净)。
 *
 * 本模块为纯函数:不发起网络请求、不读盘、不写任何持久化状态。
 */

/** 校验结果码 */
export enum UrlPolicyCode {
  OK = 0,
  EMPTY = 1,
  CONTROL_CHAR = 2,
  BACKSLASH = 3,
  BAD_SCHEME = 4,
  NO_HOST = 5,
  USERINFO = 6,
  BAD_PORT = 7,
  BAD_PATH_CHAR = 8,
  BAD_STRUCTURE = 9
}

/** 校验结果:code 为 OK 时 url 为规范化后的 URL,否则 url 为空串 */
export interface UrlPolicyResult {
  code: UrlPolicyCode;
  url: string;
  reason: string;
}

/** 允许的协议 */
const ALLOWED_SCHEMES: string[] = ['http', 'https'];

/** path 允许字符(白名单) */
const PATH_ALLOWED: string[] = [
  'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm',
  'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z',
  'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M',
  'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z',
  '0', '1', '2', '3', '4', '5', '6', '7', '8', '9',
  '.', '_', '~', '/', ':', '-'
];

function isControlChar(c: string): boolean {
  const cp: number = c.charCodeAt(0);
  return (cp >= 0x00 && cp <= 0x1F) || cp === 0x7F;
}

function isDigit(c: string): boolean {
  return c >= '0' && c <= '9';
}

function fail(code: UrlPolicyCode, reason: string): UrlPolicyResult {
  return { code: code, url: '', reason: reason };
}

/**
 * 校验 base URL。通过则返回 { code: OK, url: 规范化URL },失败返回具体原因。
 * 非法输入一律原样拒绝,绝不做"猜测式"修复。
 */
export function validateBaseUrl(input: string | null | undefined): UrlPolicyResult {
  // 1. 非空 + 类型防御
  if (input === null || input === undefined) {
    return fail(UrlPolicyCode.EMPTY, 'URL 为 null/undefined');
  }
  const raw: string = `${input}`;
  if (raw.length === 0) {
    return fail(UrlPolicyCode.EMPTY, 'URL 为空');
  }
  // 前后空白即拒绝(trim 后与原串不等,视为污染)
  const trimmed: string = raw.trim();
  if (trimmed.length === 0) {
    return fail(UrlPolicyCode.EMPTY, 'URL 仅含空白字符');
  }
  if (trimmed !== raw) {
    return fail(UrlPolicyCode.EMPTY, 'URL 含前后空白');
  }

  // 2. 控制字符
  for (let i = 0; i < raw.length; i++) {
    if (isControlChar(raw[i])) {
      return fail(UrlPolicyCode.CONTROL_CHAR,
        `URL 含控制字符 (U+${raw[i].charCodeAt(0).toString(16).padStart(4, '0').toUpperCase()} @${i})`);
    }
  }

  // 3. 反斜杠
  if (raw.indexOf('\\') >= 0) {
    return fail(UrlPolicyCode.BACKSLASH, 'URL 含反斜杠');
  }

  // 4. 协议:scheme:// 必须存在且为 http/https
  const sep: number = raw.indexOf('://');
  if (sep <= 0) {
    return fail(UrlPolicyCode.BAD_SCHEME, '缺少 scheme:// (仅允许 http/https)');
  }
  const scheme: string = raw.substring(0, sep);
  if (ALLOWED_SCHEMES.indexOf(scheme) < 0) {
    return fail(UrlPolicyCode.BAD_SCHEME, `协议 "${scheme}" 不允许 (仅 http/https)`);
  }

  // 5. authority + path
  const afterScheme: string = raw.substring(sep + 3); // '://' 之后
  const slashPos: number = afterScheme.indexOf('/');
  let authority: string = '';
  let path: string = '';
  if (slashPos < 0) {
    authority = afterScheme;
  } else {
    authority = afterScheme.substring(0, slashPos);
    path = afterScheme.substring(slashPos); // 含开头 '/'
  }
  if (authority.length === 0) {
    return fail(UrlPolicyCode.NO_HOST, '缺少 host');
  }

  // 6. userinfo:host 段禁止 '@'(http://user:pass@host/ 形态整体拒绝)
  if (authority.indexOf('@') >= 0) {
    return fail(UrlPolicyCode.USERINFO, 'URL 含 userinfo (禁止 user:pass@host)');
  }

  // 7. port
  const lastColon: number = authority.lastIndexOf(':');
  if (lastColon >= 0) {
    const portStr: string = authority.substring(lastColon + 1);
    if (portStr.length === 0) {
      return fail(UrlPolicyCode.BAD_PORT, 'URL 以 ":" 结尾,port 为空');
    }
    for (let i = 0; i < portStr.length; i++) {
      if (!isDigit(portStr[i])) {
        return fail(UrlPolicyCode.BAD_PORT, `port 含非数字字符 "${portStr[i]}"`);
      }
    }
    const portNum: number = parseInt(portStr, 10);
    if (portNum > 65535) {
      return fail(UrlPolicyCode.BAD_PORT, `port 超出范围: ${portNum}`);
    }
  }

  // 8. path 字符白名单 + 禁 query/fragment
  for (let i = 0; i < path.length; i++) {
    if (PATH_ALLOWED.indexOf(path[i]) < 0) {
      return fail(UrlPolicyCode.BAD_PATH_CHAR, `path 含非法字符 "${path[i]}" @${i}`);
    }
  }
  if (path.indexOf('?') >= 0 || path.indexOf('#') >= 0) {
    // 理论上已被白名单拦截,此处双保险
    return fail(UrlPolicyCode.BAD_STRUCTURE, 'base URL 不允许携带 query 或 fragment');
  }

  // 规范化:无 path 时补 '/'
  const normalized: string = path.length > 0 ? raw : `${raw}/`;
  return { code: UrlPolicyCode.OK, url: normalized, reason: 'ok' };
}

/** 便捷断言:合法返回 true */
export function isAllowedUrl(input: string | null | undefined): boolean {
  return validateBaseUrl(input).code === UrlPolicyCode.OK;
}

/** 校验失败原因转可读文案(供 UI 展示) */
export function urlPolicyMessage(r: UrlPolicyResult): string {
  if (r.code === UrlPolicyCode.OK) {
    return 'ok';
  }
  return `URL 校验失败 [${r.code}]: ${r.reason}`;
}
