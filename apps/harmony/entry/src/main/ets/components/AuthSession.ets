/**
 * AuthSession.ets — 认证会话编排(M14-89 第 1 切片)
 *
 * 职责边界:
 *   - 登录:POST /auth/login => token;token 先过可用性校验(非空、无空白),
 *     再以 GET /auth/me 验证 —— /me 失败即登录失败,绝不产生半会话;
 *   - 会话状态:内存 token(经 AiosApi.setBearerToken 唯一持有)+
 *     当前用户 MeData(仅 username/role/created_at 投影到 UI);
 *   - 持久化:token 落 Asset Store Kit(TokenVault);启动恢复时从 vault
 *     读出后必须再过一次 /me 验证 —— 失效 token 立即清除(fail-closed);
 *   - 登出:清内存 token + 清 vault + 清用户状态;不做服务端 logout 调用
 *     (后端 logout 仅作用于 HttpOnly cookie,对 bearer 客户端无意义);
 *   - 事件:登录/登出/恢复后经 emitter 广播 EVENT_ID_SESSION_CHANGED,
 *     各只读面板订阅后用新凭据刷新;
 *   - 硬保证:token/密码绝不进日志、绝不进事件 payload、绝不进 UI 文案。
 */

import { emitter } from '@kit.BasicServicesKit';
import {
  ApiResult,
  MeData,
  TokenData,
  clearBearerToken,
  getMe,
  hasBearerToken,
  isUsableBearer,
  postLogin,
  setBearerToken
} from '../AiosApi';
import { VaultLoadResult, VaultResult, clearTokenVault, loadTokenFromVault, saveTokenToVault } from './TokenVault';

/** 会话事件 ID(登录/登出/恢复各发一次;payload 永远为空,不携带任何凭据) */
export const EVENT_ID_SESSION_CHANGED: string = 'aios://auth/session_changed';

export type SessionPhase = 'idle' | 'loading' | 'authenticated' | 'error';

export interface SessionState {
  phase: SessionPhase;
  user: MeData | null;
  error: string;
}

export interface LoginOutcome {
  ok: boolean;
  error: string;
  user: MeData | null;
}

/** 登出/恢复结果 */
export interface SimpleOutcome {
  ok: boolean;
  restored: boolean;
  error: string;
}

/** 模块级会话状态(单例;UI 通过 getState 读取) */
let currentPhase: SessionPhase = 'idle';
let currentUser: MeData | null = null;
let currentError: string = '';

export function getSessionState(): SessionState {
  const st: SessionState = {
    phase: currentPhase,
    user: currentUser,
    error: currentError
  };
  return st;
}

/** 广播会话变更(payload 为空对象 —— 契约:凭据永不进事件) */
function broadcast(): void {
  try {
    emitter.emit(EVENT_ID_SESSION_CHANGED, { data: {} });
  } catch (e) {
    // 通知失败不改变会话状态本身
  }
}

function failOutcome(error: string): SimpleOutcome {
  const o: SimpleOutcome = { ok: false, restored: false, error: error };
  return o;
}

/**
 * 登录全流程:login => token 校验 => /me 验证 => 内存 + vault 持久化。
 * 任何一步失败:清内存 token(不留半会话),如实返回错误。
 * vault 写失败:会话仍在(内存有效),但 error 如实标注「未持久化」。
 */
export async function login(baseUrl: string, username: string, password: string): Promise<LoginOutcome> {
  currentPhase = 'loading';
  currentError = '';
  broadcast();
  const r: ApiResult<TokenData> = await postLogin(baseUrl, username, password);
  if (!r.ok || r.data === null) {
    clearBearerToken();
    currentUser = null;
    currentPhase = 'error';
    currentError = r.error;
    broadcast();
    const out: LoginOutcome = { ok: false, error: r.error, user: null };
    return out;
  }
  const token: string | null = r.data.access_token;
  if (!isUsableBearer(token)) {
    // 空白/畸形 token:拒绝建立会话(fail-closed)
    clearBearerToken();
    currentUser = null;
    currentPhase = 'error';
    currentError = '登录响应 token 非法(空或含空白),已拒绝';
    broadcast();
    const out: LoginOutcome = { ok: false, error: currentError, user: null };
    return out;
  }
  // R16 契约:成功载荷必须显式声明 token_type==='bearer' (case-insensitive),
  // 否则拒绝建立任何内存/vault 会话(与 token 可用性校验同为 fail-closed 前置条件)。
  if ((r.data.token_type ?? '').toLowerCase() !== 'bearer') {
    clearBearerToken();
    currentUser = null;
    currentPhase = 'error';
    currentError = '登录响应 token_type 非法(必须为 bearer),已拒绝';
    broadcast();
    const ttOut: LoginOutcome = { ok: false, error: currentError, user: null };
    return ttOut;
  }
  // /me 验证:失败即登录失败(半会话不落地)
  const me: ApiResult<MeData> = await getMe(baseUrl, token);
  if (!me.ok || me.data === null) {
    clearBearerToken();
    currentUser = null;
    currentPhase = 'error';
    currentError = `登录后身份验证失败: ${me.error}`;
    broadcast();
    const out: LoginOutcome = { ok: false, error: currentError, user: null };
    return out;
  }
  // 成功:内存 token + 用户状态
  setBearerToken(token);
  currentUser = me.data;
  currentPhase = 'authenticated';
  // vault 持久化(失败不影响本进程会话,如实标注)
  const vault: VaultResult = await saveTokenToVault(token);
  if (!vault.ok) {
    currentError = `登录成功,但 token 持久化失败(重启后需重新登录): ${vault.error}`;
  }
  broadcast();
  const okOut: LoginOutcome = { ok: true, error: vault.ok ? '' : currentError, user: me.data };
  return okOut;
}

/**
 * 登出:清内存 token、清 vault、清用户状态。
 * vault 清理失败也如实返回(但内存会话已清除 —— fail-closed 方向正确)。
 */
export async function logout(): Promise<SimpleOutcome> {
  clearBearerToken();
  currentUser = null;
  currentPhase = 'idle';
  currentError = '';
  const vault: VaultResult = await clearTokenVault();
  broadcast();
  const out: SimpleOutcome = vault.ok
    ? { ok: true, restored: false, error: '' }
    : { ok: false, restored: false, error: `已登出,但安全存储清理失败: ${vault.error}` };
  return out;
}

/**
 * 冷启动恢复:从 vault 读 token -> /me 验证 -> 有效则恢复会话。
 * 任何失败(无 token / /me 401 / vault 异常)都保持未登录,并清掉失效 token。
 */
export async function restoreSession(baseUrl: string): Promise<SimpleOutcome> {
  const loaded: VaultLoadResult = await loadTokenFromVault();
  if (!loaded.ok || loaded.token === null) {
    clearBearerToken();
    currentUser = null;
    return failOutcome(loaded.error === 'no_token' ? '' : loaded.error);
  }
  const me: ApiResult<MeData> = await getMe(baseUrl, loaded.token);
  if (!me.ok || me.data === null) {
    // 失效 token:清除,不留半会话
    clearBearerToken();
    currentUser = null;
    await clearTokenVault();
    return failOutcome('');
  }
  setBearerToken(loaded.token);
  currentUser = me.data;
  currentPhase = 'authenticated';
  broadcast();
  const out: SimpleOutcome = { ok: true, restored: true, error: '' };
  return out;
}

/** 当前是否有可用内存会话(供只读面板决定是否提示 401 语义) */
export function isSessionActive(): boolean {
  return hasBearerToken() && currentUser !== null;
}
