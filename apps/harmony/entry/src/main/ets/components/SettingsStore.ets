/**
 * SettingsStore.ets — 设置持久化(M13-01 第 3b-1 小步, M13-03 使用官方 emitter API,
 * M13-09 生产加固:消除 may-throw 静态告警)
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
 *   - M13-03:saveBaseUrl 成功后通过 emitter.emit 通知已挂载订阅者。
 *
 * M13-09 生产加固:
 *   1) 消除 ArkTS 编译器 may-throw 静态告警 —— 去掉「返回 Promise 的 openPrefs」
 *      间接层,preferences.getPreferences / put / flush 等可抛调用全部词法地
 *      位于调用方的 try/catch 块内,异常路径静态可见地被捕获;
 *   2) 保存/读取失败文案经 ErrorText.safeErrorText 收敛 —— 绝不
 *      [object Object],绝不序列化整个异常对象;
 *   3) emitter.emit 自身包 try/catch —— 通知失败绝不影响已成功的持久化结果,
 *      不把订阅方异常传播回保存流程(failure containment)。
 *
 * 行为保持不变:
 *   - 持久化语义:preferences 名称/键名、默认值、URL 校验、规范化逻辑均不变;
 *   - 事件语义:仅当 put+flush 全部成功后才 emit(EVENT_ID_URL_CHANGED),
 *      payload 仍为调试/观测用途,订阅方始终以 loadBaseUrl 重读持久化值为准。
 */

import { common } from '@kit.AbilityKit';
import { preferences } from '@kit.ArkData';
import { emitter } from '@kit.BasicServicesKit';
import { safeErrorText } from '../ErrorText';
import { UrlPolicyCode, validateBaseUrl, urlPolicyMessage } from '../UrlPolicy';

/** 默认服务地址(本地开发) */
export const DEFAULT_API_BASE_URL: string = 'http://127.0.0.1:8000';

/** preferences 存储名与键名(只此一组,无任何凭据键) */
const PREF_NAME: string = 'aios_settings';
const KEY_API_BASE_URL: string = 'api_base_url';

/** 事件 ID(用于通知 URL 变更) — 由 SettingsStore 统一管理,HomePane 等订阅方统一从此导入 */
export const EVENT_ID_URL_CHANGED: string = 'aios://settings/url_changed';

/**
 * saveBaseUrl 结果:ok=true 时 url 为规范化后地址;ok=false 时 error 为可读错误
 */
export interface SaveResult {
  ok: boolean;
  url: string;
  error: string;
}

/**
 * 读取已保存的 base URL。
 * 任何异常(打开/读取失败、类型异常、白名单校验失败)都返回 DEFAULT_API_BASE_URL
 * —— 回退是安全的:默认值本身可通过白名单校验。
 */
export async function loadBaseUrl(context: common.Context): Promise<string> {
  let raw: preferences.ValueType;
  try {
    // 可抛调用(preferences.getPreferences/getSync)词法地位于本 try 块内
    const prefs: preferences.Preferences = await preferences.getPreferences(context, PREF_NAME);
    raw = prefs.getSync(KEY_API_BASE_URL, '');
  } catch (e) {
    // 读取失败安全回退默认值,绝不把异常抛给调用方(面板不因此崩溃)
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
 * 写入或 flush 失败:如实返回 {ok:false, error: 底层错误经 safeErrorText 收敛}。
 * 成功保存后通过官方 emitter 通知所有订阅者刷新数据。
 *
 * 注意:emit 附带 payload 仅作调试/观测用途;订阅方(HomePane)始终通过
 * loadBaseUrl(context) 重新读取持久化值作为真实来源,不因 payload 而信赖。
 */
export async function saveBaseUrl(context: common.Context, input: string): Promise<SaveResult> {
  const check = validateBaseUrl(input);
  if (check.code !== UrlPolicyCode.OK) {
    return { ok: false, url: '', error: urlPolicyMessage(check) };
  }
  try {
    // 可抛调用(preferences.getPreferences/put/flush)词法地位于本 try 块内
    const prefs: preferences.Preferences = await preferences.getPreferences(context, PREF_NAME);
    await prefs.put(KEY_API_BASE_URL, check.url);
    await prefs.flush();
    // 仅当持久化成功时才通过官方 emitter 通知监听者;
    // M13-09:emit 自身包 try/catch —— 通知失败不影响已成功的持久化结果
    try {
      emitter.emit(EVENT_ID_URL_CHANGED, { data: check.url });
    } catch (emitErr) {
      // 通知失败被包含:URL 已成功写盘,返回仍为成功;不做任何补偿性重试
    }
    return { ok: true, url: check.url, error: 'ok' };
  } catch (e) {
    return { ok: false, url: '', error: `写入失败: ${safeErrorText(e)}` };
  }
}
