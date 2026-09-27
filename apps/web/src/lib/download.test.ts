// M14-164 TDD：/download 渠道数据契约（诚实边界钉死在数据层）。
// 契约（对应 docs/MOBILE_DISTRIBUTION.md 第 0 节）：
//   - PWA 是唯一可用渠道；Android / Harmony 原生包在签名材料落地前
//     恒为 pending，永不提供下载链接（href === null）；
//   - 渠道数据里绝不出现 APK / HAP 文件引用——debug APK 与 unsigned HAP
//     不得作为生产分发的边界由测试钉住，防止未来误加；
//   - 每个渠道都有面向用户的中文状态说明与原因文案。
import { describe, expect, it } from "vitest";
import {
  DOWNLOAD_CHANNELS,
  PWA_INSTALL_HINTS,
  type DownloadChannel,
} from "./download";

const byId = (id: string) =>
  DOWNLOAD_CHANNELS.find((c) => c.id === id) as DownloadChannel;

describe("download 渠道数据契约", () => {
  it("恰好三个渠道：pwa / android / harmony", () => {
    expect(DOWNLOAD_CHANNELS.map((c) => c.id).sort()).toEqual([
      "android",
      "harmony",
      "pwa",
    ]);
  });

  it("PWA 是唯一 available 渠道，覆盖全平台", () => {
    const pwa = byId("pwa");
    expect(pwa.status).toBe("available");
    expect(pwa.title).toContain("PWA");
    expect(pwa.description).not.toBe("");
  });

  it("Android 恒为 pending：release 签名未落地", () => {
    const android = byId("android");
    expect(android.status).toBe("pending");
    expect(android.href).toBeNull();
    expect(android.reason).not.toBe("");
  });

  it("Harmony 恒为 pending：AGC 签名未落地", () => {
    const harmony = byId("harmony");
    expect(harmony.status).toBe("pending");
    expect(harmony.href).toBeNull();
    expect(harmony.reason).not.toBe("");
  });

  it("任何渠道都绝不提供原生包下载链接（href 恒 null，除 pwa 页内动作外）", () => {
    for (const channel of DOWNLOAD_CHANNELS) {
      // pwa 的动作在页面交互层（beforeinstallprompt / iOS 指引），
      // 数据层同样不携带外部下载 URL。
      expect(channel.href).toBeNull();
    }
  });

  it("全量数据不含 APK / HAP 文件引用（debug 与 unsigned 均不得出现）", () => {
    const serialized = JSON.stringify(DOWNLOAD_CHANNELS);
    expect(serialized).not.toMatch(/\.apk/i);
    expect(serialized).not.toMatch(/\.hap/i);
    expect(serialized).not.toMatch(/debug.*签名.*下载/s);
  });

  it("每个渠道均有非空中文文案（title / statusLabel / description）", () => {
    for (const channel of DOWNLOAD_CHANNELS) {
      expect(channel.title).not.toBe("");
      expect(channel.statusLabel).not.toBe("");
      expect(channel.description).not.toBe("");
    }
  });
});

describe("PWA 安装指引契约", () => {
  it("包含 iOS Safari 添加到主屏指引与桌面/Chrome 说明", () => {
    expect(PWA_INSTALL_HINTS.ios).not.toBe("");
    expect(PWA_INSTALL_HINTS.ios).toContain("主屏幕");
    expect(PWA_INSTALL_HINTS.generic).not.toBe("");
  });
});
