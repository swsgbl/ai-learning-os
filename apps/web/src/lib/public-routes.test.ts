// M14-179 TDD：公开路由（免登录态探测）契约。
// 背景（M14-178 真浏览器验收）：公开 /download 页上 AppShell 的匿名
// auth 探测只会收到必然的 401（auth/me），构成浏览器网络噪音。
// 契约（见 public-routes.ts）：
//   - pathname 是 Next App Router 规范路径（usePathname 不含 basePath，
//     root 与 /aios 构建同值——源码零 /aios 硬编码）；
//   - 恰为 "/download" → 免探测；其余一切路由照常探测（AppShell
//     认证行为不回归）。
import { describe, expect, it } from "vitest";

import { isPublicRoute } from "./public-routes";

describe("isPublicRoute（M14-179 公开路由免探测契约）", () => {
  it("/download 是唯一公开路由：免登录态探测（零 auth/status + auth/me 调用）", () => {
    expect(isPublicRoute("/download")).toBe(true);
  });

  it("代表性认证路由照常探测：首页/语音/考场/学习库/工作台/登录/治理", () => {
    for (const pathname of [
      "/",
      "/voice",
      "/exam",
      "/library",
      "/progress",
      "/login",
      "/governance",
    ]) {
      expect(isPublicRoute(pathname)).toBe(false);
    }
  });

  it("近形路径不误伤：前缀近似/尾斜杠/子路径/相对形态一律照常探测", () => {
    for (const pathname of [
      "/download2",
      "/downloads",
      "/download/",
      "/download/x",
      "//download",
      "download",
      "",
    ]) {
      expect(isPublicRoute(pathname)).toBe(false);
    }
  });

  it("沉浸式与动态段路由照常探测（voice/exam 会话页）", () => {
    expect(isPublicRoute("/voice/session-1")).toBe(false);
    expect(isPublicRoute("/exam/paper-9")).toBe(false);
  });
});
