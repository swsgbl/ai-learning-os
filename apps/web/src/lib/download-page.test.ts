// M14-164 TDD：/download 页面与 AppShell 入口的源码级契约。
// 页面是 client leaf（安装交互依赖浏览器事件），无 DOM 测试依赖——
// 以文本契约钉住诚实边界与关键交互（与 sw-contract.test.ts 同思路）：
//   1. 渠道状态必须来自 src/lib/download.ts 单一事实来源（不硬编码文案，
//      防止页面与数据层状态漂移）；
//   2. 必须处理 beforeinstallprompt（Android/桌面 Chrome 安装按钮）；
//   3. 必须包含 iOS Safari「添加到主屏幕」指引；
//   4. 绝不出现 APK / HAP 链接或文件引用（debug/unsigned 均不得出现）；
//   5. AppShell 提供不占移动底栏的 /download 入口。
// M14-179 追加：公开 /download 页浏览器噪音消除的接线契约——
// root metadata 提供 basePath 感知的通用 favicon（消除宿主根
// /favicon.ico 404 回退），AppShell 仅在公开路由跳过匿名 auth 探测
// （消除必然 401 的 auth/me 噪音），认证路由探测行为不回归。
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const PAGE_SOURCE = readFileSync(
  join(__dirname, "../app/download/page.tsx"),
  "utf8",
);
const PANEL_SOURCE = readFileSync(
  join(__dirname, "../components/download/download-panel.tsx"),
  "utf8",
);
const SHELL_SOURCE = readFileSync(
  join(__dirname, "../components/app-shell.tsx"),
  "utf8",
);
const LAYOUT_SOURCE = readFileSync(
  join(__dirname, "../app/layout.tsx"),
  "utf8",
);

describe("/download 页面契约", () => {
  it("页面渲染 DownloadPanel（路由薄层，符合现有页面职责约定）", () => {
    expect(PAGE_SOURCE).toContain("DownloadPanel");
  });

  it("渠道状态来自 DOWNLOAD_CHANNELS 单一事实来源", () => {
    expect(PANEL_SOURCE).toContain("DOWNLOAD_CHANNELS");
    expect(PANEL_SOURCE).toContain("PWA_INSTALL_HINTS");
  });

  it("处理 beforeinstallprompt（Android / 桌面安装按钮）", () => {
    expect(PANEL_SOURCE).toContain("beforeinstallprompt");
  });

  it("识别已安装状态（standalone 显示模式或 appinstalled）", () => {
    expect(PANEL_SOURCE).toMatch(/standalone|appinstalled/);
  });

  it("包含 iOS Safari 添加到主屏幕指引", () => {
    expect(PANEL_SOURCE).toContain("主屏幕");
  });

  it("页面与面板绝不包含原生包链接或文件引用", () => {
    for (const source of [PAGE_SOURCE, PANEL_SOURCE]) {
      expect(source).not.toMatch(/\.apk/i);
      expect(source).not.toMatch(/\.hap/i);
      // 不为 pending 渠道渲染下载按钮：<a href> 下载形态不得出现
      expect(source).not.toMatch(/href=\{?["'][^"']*\.(apk|hap)/i);
    }
  });

  it("渠道卡按状态渲染：available 可操作，pending 仅展示原因", () => {
    expect(PANEL_SOURCE).toMatch(/status\s*===?\s*["']available["']/);
    expect(PANEL_SOURCE).toMatch(/reason/);
  });

  it("Android 渠道状态经同源 download manifest 运行时判定（不写死进组件）", () => {
    // 读取与判定全部来自 lib/download-manifest，组件只消费状态
    expect(PANEL_SOURCE).toContain("fetchAndroidChannelState");
    expect(PANEL_SOURCE).toContain("download-manifest");
    // 无 manifest / 校验失败时的诚实默认：初始 pending
    expect(PANEL_SOURCE).toMatch(/useState<AndroidChannelState>\(\{\s*status: "pending"/);
  });

  it("Android 下载链接仅在运行时状态 available 时渲染", () => {
    // 下载 <a> 的渲染以 androidOverride（available 分支）为门
    expect(PANEL_SOURCE).toMatch(/androidOverride\s*\?\s*/);
    expect(PANEL_SOURCE).toMatch(/href=\{androidOverride\.href\}/);
  });
});

describe("AppShell 入口契约", () => {
  it("header 提供 /download 链接（图标按钮，不挤占移动底栏）", () => {
    expect(SHELL_SOURCE).toContain('"/download"');
    // 底栏导航项 BASE_NAV 不含 /download（移动底栏保持 5+1 项不变）
    const baseNavMatch = SHELL_SOURCE.match(/BASE_NAV[\s\S]*?\];/);
    expect(baseNavMatch).not.toBeNull();
    expect(baseNavMatch![0]).not.toContain("/download");
  });

  it("入口是图标按钮且有可访问名称（aria-label）", () => {
    expect(SHELL_SOURCE).toMatch(/aria-label="[^"]*下载[^"]*"/);
  });
});

describe("M14-179 公开 /download 页浏览器噪音消除契约", () => {
  it("root metadata 提供 basePath 感知的通用 favicon（与 apple-touch-icon 并列）", () => {
    // Next 不为 metadata icons 自动加 basePath——URL 必须来自 pwa.ts
    // 契约常量（root=/icons/...、/aios=/aios/icons/...，见 pwa.test.ts），
    // 提供 rel=icon 后浏览器不再回退请求宿主根 /favicon.ico（404 噪音）
    expect(LAYOUT_SOURCE).toMatch(/icon:\s*\[\{\s*url:\s*FAVICON_ICON/);
    expect(LAYOUT_SOURCE).toMatch(/apple:\s*\[\{\s*url:\s*APPLE_TOUCH_ICON/);
  });

  it("AppShell 仅在公开路由跳过 auth 探测：isPublicRoute 门在 probeAuth 之前", () => {
    expect(SHELL_SOURCE).toContain("isPublicRoute(pathname)");
    const gateIndex = SHELL_SOURCE.indexOf("isPublicRoute(pathname)");
    const probeIndex = SHELL_SOURCE.indexOf("probeAuth(API_BASE)");
    expect(probeIndex).toBeGreaterThan(-1);
    expect(gateIndex).toBeLessThan(probeIndex);
  });

  it("认证探测未被全局禁用：probeAuth 调用仍由 AppShell 持有", () => {
    expect(SHELL_SOURCE).toContain("probeAuth(API_BASE)");
    // 门只作用于公开路由判定，不触碰 logout / 徽章 / 治理入口逻辑
    expect(SHELL_SOURCE).toContain("logout(API_BASE)");
    expect(SHELL_SOURCE).toContain("AuthBadge");
  });
});
