// M14-164 TDD：PWA 路径契约 + manifest route 字段契约。
// 契约：start_url / scope / id / SW 注册地址 / apple-touch-icon / manifest
// 图标全部由 NEXT_PUBLIC_BASE_PATH 推导——空 = 根路径构建行为完全不变；
// "/aios" = 所有路径带且仅带一层前缀（无双重前缀、无遗漏前缀）。
// M14-179：favicon（FAVICON_ICON）纳入同一契约——metadata 不提供
// rel=icon 时浏览器回退请求域根 /favicon.ico（宿主 404 噪音，M14-178）。
// scope 必须以斜杠结尾（W3C 规范：scope 是 URL 前缀），start_url 必须落在
// scope 内；非 GET-bypass 等运行时行为见 sw-contract.test.ts。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("pwa 路径契约（随 basePath 正确变化）", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("默认（未设置）构建：根路径，行为与既有构建完全一致", async () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", undefined);
    const pwa = await import("./pwa");
    expect(pwa.PWA_START_URL).toBe("/");
    expect(pwa.PWA_SCOPE).toBe("/");
    expect(pwa.PWA_ID).toBe("/");
    expect(pwa.SW_REGISTER_SRC).toBe("/sw.js");
    expect(pwa.APPLE_TOUCH_ICON).toBe("/apple-touch-icon.png");
    // M14-179：favicon 复用既有 any-192 图标资产，root 构建为根相对路径
    expect(pwa.FAVICON_ICON).toBe("/icons/icon-192.png");
    expect(pwa.PWA_MANIFEST_ICONS).toHaveLength(4);
    for (const icon of pwa.PWA_MANIFEST_ICONS) {
      expect(icon.src.startsWith("/icons/")).toBe(true);
      expect(icon.src).not.toContain("/aios");
    }
  });

  it("basePath=/aios 构建：所有路径带且仅带一层前缀", async () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "/aios");
    const pwa = await import("./pwa");
    expect(pwa.PWA_START_URL).toBe("/aios/");
    expect(pwa.PWA_SCOPE).toBe("/aios/");
    expect(pwa.PWA_ID).toBe("/aios/");
    expect(pwa.SW_REGISTER_SRC).toBe("/aios/sw.js");
    expect(pwa.APPLE_TOUCH_ICON).toBe("/aios/apple-touch-icon.png");
    // M14-179：favicon 与其他公共路径同受 basePath 契约约束
    expect(pwa.FAVICON_ICON).toBe("/aios/icons/icon-192.png");
    expect(pwa.FAVICON_ICON.startsWith("/aios/icons/")).toBe(true);
    for (const icon of pwa.PWA_MANIFEST_ICONS) {
      expect(icon.src.startsWith("/aios/icons/")).toBe(true);
      expect(icon.src).not.toContain("/aios/aios");
    }
  });

  it("scope 恒为尾斜杠形式，start_url 恒落在 scope 内（两种构建同验）", async () => {
    for (const base of [undefined, "/aios"] as const) {
      vi.resetModules();
      vi.stubEnv("NEXT_PUBLIC_BASE_PATH", base);
      const pwa = await import("./pwa");
      expect(pwa.PWA_SCOPE.endsWith("/")).toBe(true);
      expect(pwa.PWA_START_URL.startsWith(pwa.PWA_SCOPE)).toBe(true);
      expect(pwa.PWA_ID.startsWith(pwa.PWA_SCOPE)).toBe(true);
    }
  });

  it("图标条目覆盖 192/512 × any/maskable 四象限，均为 PNG", async () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", undefined);
    const pwa = await import("./pwa");
    const combos = pwa.PWA_MANIFEST_ICONS.map((i) => `${i.sizes}:${i.purpose}`).sort();
    expect(combos).toEqual([
      "192x192:any",
      "192x192:maskable",
      "512x512:any",
      "512x512:maskable",
    ]);
    for (const icon of pwa.PWA_MANIFEST_ICONS) {
      expect(icon.type).toBe("image/png");
    }
  });

  it("非法 basePath 在模块加载期即抛错（fail-closed 传播）", async () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "/aios/");
    await expect(import("./pwa")).rejects.toThrow();
  });
});

describe("manifest route 契约（app/manifest.ts default export）", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  const requiredStringFields = ["name", "short_name", "description", "id", "start_url", "scope", "display", "background_color", "theme_color"] as const;

  async function loadManifest(base: string | undefined) {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", base);
    const mod = await import("../app/manifest");
    const manifest = (mod.default as () => Record<string, unknown>)();
    return manifest;
  }

  it("默认构建：字段齐全，start_url/scope/id 为根路径", async () => {
    const manifest = await loadManifest(undefined);
    for (const field of requiredStringFields) {
      expect(typeof manifest[field]).toBe("string");
      expect(manifest[field]).not.toBe("");
    }
    expect(manifest.start_url).toBe("/");
    expect(manifest.scope).toBe("/");
    expect(manifest.id).toBe("/");
    expect(manifest.display).toBe("standalone");
    // 主题色与现有 viewport themeColor（砚席 porcelain 底）一致
    expect(manifest.theme_color).toBe("#f6f5f1");
    expect(manifest.background_color).toBe("#f6f5f1");
    expect(Array.isArray(manifest.icons)).toBe(true);
  });

  it("basePath=/aios 构建：start_url/scope/id 带前缀，无双重前缀", async () => {
    const manifest = await loadManifest("/aios");
    expect(manifest.start_url).toBe("/aios/");
    expect(manifest.scope).toBe("/aios/");
    expect(manifest.id).toBe("/aios/");
    expect(JSON.stringify(manifest)).not.toContain("/aios/aios");
    const icons = manifest.icons as Array<{ src: string }>;
    for (const icon of icons) {
      expect(icon.src.startsWith("/aios/")).toBe(true);
    }
  });

  it("icons 覆盖 any 与 maskable 两种 purpose（192 与 512 尺寸齐全）", async () => {
    const manifest = await loadManifest(undefined);
    const icons = manifest.icons as Array<{ sizes: string; purpose: string }>;
    expect(icons.some((i) => i.purpose.includes("any") && i.sizes === "192x192")).toBe(true);
    expect(icons.some((i) => i.purpose.includes("any") && i.sizes === "512x512")).toBe(true);
    expect(icons.some((i) => i.purpose.includes("maskable") && i.sizes === "192x192")).toBe(true);
    expect(icons.some((i) => i.purpose.includes("maskable") && i.sizes === "512x512")).toBe(true);
  });
});
