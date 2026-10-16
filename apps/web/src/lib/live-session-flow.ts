// M14-274：Web ↔ 服务端 live 会话编排（服务端订阅转写链，M14-272d 契约）。
//
// 语义定位：live 会话是**增强面**——学生端主作答流（M14-252 LiveKit
// 发布 + 录音上传 /transcribe）不依赖它：open/close 全部 fire-and-forget
// 容错（失败静默降噪为 console.debug，绝不阻塞作答/抛到 UI 错误面）；
// 成功时服务端订阅者加入**同一房间**（voice-<session_id>）接收已发布
// 音轨，close 冲刷 FunASR 转写得到服务端权威 transcript。
//
// 编排点（voice-studio 接线）：
// - LiveKit 连接成功后 openLiveSession（幂等：服务端 200/201 均成功）；
// - 收尾（完卷/离场/unmount 共用 releaseVoiceInput）时 closeLiveSession
//   ——返回 final transcript 文本（供 UI 对照展示/验收断言）。
//
// 边界：token/凭据由 api 基座 cookie 携带（401 由基座引导登录——
// live 增强面在未登录态本就不可用）；本模块不做 VAD/打断/实时
// transcript 轮询（后续需要时另切片）；错误消息不进 UI。
import { api, ApiError } from "./api";

/** open 失败的降噪日志前缀（增强面——失败不影响作答）。 */
const DEBUG_TAG = "[voice-live]";

/**
 * LiveKit 连接成功后打开服务端 live 会话（fire-and-forget 容错）。
 *
 * 幂等：服务端对同 session 二次 open 返回 200（既有句柄）；网络/
 * 5xx/401 静默降噪（console.debug）——绝不抛出、绝不让增强面失败
 * 阻塞学生作答主流程。
 */
export async function openLiveSession(sessionId: string, room: string): Promise<void> {
  try {
    await api.voiceLiveOpen(sessionId, room);
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 404) return; // 服务端旧版无 live 路由——静默
    console.debug(DEBUG_TAG, "open 降级（增强面，不影响作答）");
  }
}

/**
 * 收尾时关闭服务端 live 会话：四段关停（停帧源→消费残余→FunASR
 * 冲刷→终态）→ 返回 final transcript 文本数组。
 *
 * 失败/无会话返回 null（增强面）；成功返回文本列表（可能为空——
 * 批式语义下无音频时零段）。
 */
export async function closeLiveSession(
  sessionId: string,
): Promise<string[] | null> {
  try {
    const closed = await api.voiceLiveClose(sessionId);
    return (closed.transcript?.segments ?? []).map((segment) => segment.text);
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 404) return null;
    console.debug(DEBUG_TAG, "close 降级（增强面）");
    return null;
  }
}
