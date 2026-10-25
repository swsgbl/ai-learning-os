/**
 * VoiceResumeStore.ets — 语音陪练会话恢复槽(hm-parity v5 · 对齐 Android
 * data/local/VoiceResumeStore.kt M12-03 语义逐条照抄)
 *
 * 职责边界(刻意收窄,存储面守卫):
 *   - 本地只允许保存 session_id / exam_id 两个键(preferences 持久化;
 *     非凭据,可持久化——但 token/音频/transcript/判分结果绝不落本地);
 *   - 槽位只作为「恢复提示」:恢复时必须 GET /voice/sessions/{id}/resume
 *     对齐状态/当前题/已提交答案/题数——服务端是唯一真相源,绝不在本地
 *     缓存状态或当真相使用;
 *   - save 时机:开新会话成功/恢复成功(覆盖旧槽,语义为「本设备当前活跃
 *     语音陪练」);
 *   - clear 时机:报告已获取/会话不存在(404)/会话已终态(409);
 *   - 任何异常 fail-open 返回 null 槽位(恢复提示宁可漏显示,不可错显示
 *     已被清除的会话;写入失败如实返回 false,调用方如实展示)。
 *
 * 契约(harmony @kit.ArkData preferences,参照 SettingsStore.ets 模式):
 *   getPreferences(context, name) → Preferences;put/flush 可抛,全部
 *   词法位于 try/catch 内(may-throw 静态告警消除,M13-09 同款加固)。
 */

import { common } from '@kit.AbilityKit';
import { preferences } from '@kit.ArkData';

/** preferences 存储名(独立于 aios_settings,只承载恢复槽两键) */
const PREF_NAME: string = 'aios_voice_resume';
/** 键名 v1(与 Android voice_resume_session_id_v1 语义对齐) */
const KEY_SESSION_ID: string = 'voice_resume_session_id_v1';
const KEY_EXAM_ID: string = 'voice_resume_exam_id_v1';

/** 恢复槽位(只承载两键;无则 null) */
export interface VoiceResumeSlot {
  sessionId: string;
  examId: string;
}

/** 读取恢复槽;无槽/任何异常返回 null(fail-open,不阻塞选卷流程) */
export async function loadVoiceResumeSlot(context: common.Context): Promise<VoiceResumeSlot | null> {
  try {
    const prefs: preferences.Preferences = await preferences.getPreferences(context, PREF_NAME);
    const sid: preferences.ValueType = prefs.getSync(KEY_SESSION_ID, '');
    const eid: preferences.ValueType = prefs.getSync(KEY_EXAM_ID, '');
    if (typeof sid !== 'string' || typeof eid !== 'string') {
      return null;
    }
    if (sid.length === 0 || eid.length === 0) {
      return null;
    }
    const slot: VoiceResumeSlot = { sessionId: sid, examId: eid };
    return slot;
  } catch (e) {
    return null;
  }
}

/**
 * 记录恢复槽(开新会话/恢复成功时覆盖旧槽)。
 * 写入失败如实返回 false(调用方可选择展示提示,但绝不虚构成功)。
 */
export async function saveVoiceResumeSlot(context: common.Context, slot: VoiceResumeSlot): Promise<boolean> {
  try {
    const prefs: preferences.Preferences = await preferences.getPreferences(context, PREF_NAME);
    await prefs.put(KEY_SESSION_ID, slot.sessionId);
    await prefs.put(KEY_EXAM_ID, slot.examId);
    await prefs.flush();
    return true;
  } catch (e) {
    return false;
  }
}

/** 清除恢复槽(报告已取/404 会话不存在/409 已终态);失败如实返回 false */
export async function clearVoiceResumeSlot(context: common.Context): Promise<boolean> {
  try {
    const prefs: preferences.Preferences = await preferences.getPreferences(context, PREF_NAME);
    await prefs.delete(KEY_SESSION_ID);
    await prefs.delete(KEY_EXAM_ID);
    await prefs.flush();
    return true;
  } catch (e) {
    return false;
  }
}
