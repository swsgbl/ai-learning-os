// M14-164 通用手机下载/安装入口：渠道状态数据契约（单一事实来源）。
// /download 页面与测试都读这里；状态文案遵守 docs/MOBILE_DISTRIBUTION.md
// 第 0 节诚实边界：签名材料落地前原生包只显示 pending，绝不给出链接。
// href 字段恒为 null 是刻意设计：pwa 安装是页内交互（beforeinstallprompt /
// iOS 添加到主屏指引），原生包不存在任何可下载 URL——数据层从结构上
// 排除"误配一个下载链接"的可能（download.test.ts 钉住）。

export type ChannelId = "pwa" | "android" | "harmony";

/** pwa=可安装；pending=签名材料未落地，如实展示、不给链接。 */
export type ChannelStatus = "available" | "pending";

export interface DownloadChannel {
  id: ChannelId;
  title: string;
  /** 面向用户的状态短语（可用 / 待签名 …）。 */
  statusLabel: string;
  status: ChannelStatus;
  description: string;
  /** 原因说明（pending 渠道必填，available 可为 null）。 */
  reason: string | null;
  /** 恒为 null：见文件头注释，链接不存在于数据层。 */
  href: null;
}

export const DOWNLOAD_CHANNELS: readonly DownloadChannel[] = [
  {
    id: "pwa",
    title: "安装网页应用（PWA）",
    statusLabel: "可用",
    status: "available",
    description:
      "Android、iPhone 与桌面浏览器均可安装：获得独立窗口、主屏图标与更快的进入速度。考试、语音与学习数据仍以服务端为准，离线仅保留极简外壳。",
    reason: null,
    href: null,
  },
  {
    id: "android",
    title: "Android 原生应用",
    statusLabel: "待发布（release 签名未就绪）",
    status: "pending",
    description:
      "Android 原生包需要 release 签名材料（keystore）落地并完成真机验收后才会提供。当前不会发布任何安装包，请先使用上面的 PWA 安装方式。",
    reason:
      "release keystore 尚未配置；签名材料落地并通过验收前不提供原生安装包。",
    href: null,
  },
  {
    id: "harmony",
    title: "HarmonyOS 原生应用",
    statusLabel: "待发布（AGC 签名未就绪）",
    status: "pending",
    description:
      "HarmonyOS 版本将通过 AppGallery 分发，需要 AGC 发布证书与签名 Profile 落地后提交审核。当前不会发布任何安装包，请先使用上面的 PWA 安装方式。",
    reason:
      "AGC 发布证书与 Profile 尚未落地；未签名产物不作分发，落地前仅展示状态。",
    href: null,
  },
] as const;

/** PWA 安装指引文案（页面按平台展示对应条目）。 */
export const PWA_INSTALL_HINTS = {
  ios: "在 iPhone 上的 Safari 打开本页，点底部「分享」按钮，选择「添加到主屏幕」，即可像原生应用一样进入。",
  generic:
    "Android / 桌面 Chrome、Edge 等浏览器安装：点击地址栏右侧的安装图标，或使用下方「安装到本机」按钮。",
} as const;
