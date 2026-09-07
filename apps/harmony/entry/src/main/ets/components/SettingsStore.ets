/**
 * SettingsStore.ets — 设置持久化(M13-01 第 3b-1 小步)
 *
 * 职责边界(刻意收窄):
 *   - 只保存一个值:AIOS 服务基地址 URL
 *     (preferences 名称 aios_settings,key api_base_url);
 *   - 不保存任何凭据:零 token、零 password、零 key(本文件没有任何相关字段/方法);
 *   - 读 loadBaseUrl:缺省 / 类型异常 / 校验失败,一律回退 DEFAULT_API_BASE_URL,
 *     绝不把非法值放行到网络层;
 *   - 写 saveBaseUrl:先 UrlPolicy.validateBaseUrl 白名单校验,失败不写盘并如实
 *     返回 {ok:false,error:校验文案};成功 put + flush;flush 失败也如实报错,
 *     绝不谎报成功。
 */

import { common } from '@kit.AbilityKit';
import { preferences } from '@kit.ArkData';
import { UrlPolicyCode, validateBaseUrl, urlPolicyMessage } from '../UrlPolicy';

/** 默认服务地址(本地开发) */
export const DEFAULT_API_BASE_URL: string = 'http://127.0.0.1:8000';

/** preferences 存储名与键名(只此一组,无任何凭据键) */
const PREF_NAME: string = 'aios_settings';
const KEY_API_BASE_URL: string = 'api_base_url';

/** saveBaseUrl 结果:ok=true 时 url 为规范化后地址;ok=false 时 error 为可读错误 */
export interface SaveResult {
  ok: boolean;
  url: string;
  error: string;
}

/** 把异常安全转字符串(不抛出) */
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

/** 打开(或创建)preferences 实例;异常向上抛,由调用方决定回退策略 */
function openPrefs(context: common.Context): Promise<preferences.Preferences> {
  return preferences.getPreferences(context, PREF_NAME);
}

/**
 * 读取已保存的 base URL。
 * 任何异常(打开/读取失败、类型异常、白名单校验失败)都返回 DEFAULT_API_BASE_URL
 * —— 回退是安全的:默认值本身可通过白名单校验。
 */
export async function loadBaseUrl(context: common.Context): Promise<string> {
  let raw: preferences.ValueType;
  try {
    const prefs = await openPrefs(context);
    raw = prefs.getSync(KEY_API_BASE_URL, '');
  } catch (e) {
    return DEFAULT_API_BASE_URL;
  }
  if (typeof raw !== 'string') {
    return DEFAULT_API_BASE_URL;
  }
  const check = validateBaseUrl(raw);
  if (check.code !== UrlPolicyCode.OK) {
    return DEFAULT_API_BASE_URL;
  }
  return check.url;
}

/**
 * 保存 base URL(先校验后写入,只写 URL,不写任何凭据)。
 * 校验失败:不写盘,返回 {ok:false, error: urlPolicyMessage}。
 * 写入或 flush 失败:如实返回 {ok:false, error: 底层错误}。
 */
export async function saveBaseUrl(context: common.Context, input: string): Promise<SaveResult> {
  const check = validateBaseUrl(input);
  if (check.code !== UrlPolicyCode.OK) {
    return { ok: false, url: '', error: urlPolicyMessage(check) };
  }
  try {
    const prefs = await openPrefs(context);
    await prefs.put(KEY_API_BASE_URL, check.url);
    await prefs.flush();
    return { ok: true, url: check.url, error: 'ok' };
  } catch (e) {
    return { ok: false, url: '', error: `写入失败: ${errToString(e)}` };
  }
}
