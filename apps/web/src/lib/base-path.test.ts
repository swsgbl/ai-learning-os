// M14-159 R1 TDD：NEXT_PUBLIC_BASE_PATH 结构化校验 + api.ts 登录跳转契约。
// 契约：未设置/空 → ""（根路径构建不变）；恰为 "/aios" → "/aios"；
// 其他任何值（尾斜杠/查询串/片段/畸形/其他路径）fail-closed 抛错。
// LOGIN_PATH：默认构建恒 "/login"（现状不变）；"/aios" 构建恒 "/aios/login"
// （无双重前缀），window.location 的 401 跳转与 pathname 判定共用该常量。
// M14-188 扩展：401 整页跳转实现契约定为「href 写入显式携带 BASE_PATH
// 的绝对 URL」——见文末 describe。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { normalizeBasePath } from "./base-path";

describe("normalizeBasePath", () => {
  it("未设置 / null / 空串 → 空串（根路径构建，默认行为完全不变）", () => {
    expect(normalizeBasePath(undefined)).toBe("");
    expect(normalizeBasePath(null)).toBe("");
    expect(normalizeBasePath("")).toBe("");
  });

  it("恰为 /aios → 原样返回", () => {
    expect(normalizeBasePath("/aios")).toBe("/aios");
  });

  it("尾斜杠 fail-closed 抛错（部署最常见笔误）", () => {
    expect(() => normalizeBasePath("/aios/")).toThrow();
    expect(() => normalizeBasePath("/aios//")).toThrow();
  });

  it("查询串 / 片段 fail-closed 抛错", () => {
    expect(() => normalizeBasePath("/aios?x=1")).toThrow();
    expect(() => normalizeBasePath("/aios#frag")).toThrow();
  });

  it("畸形 URL / 绝对 URL fail-closed 抛错", () => {
    expect(() => normalizeBasePath("http://example.com/aios")).toThrow();
    expect(() => normalizeBasePath("//aios")).toThrow();
    expect(() => normalizeBasePath("not a path")).toThrow();
  });

  it("任何其他路径 fail-closed 抛错（大小写/空白/前缀缺失/多段）", () => {
    expect(() => normalizeBasePath("/AIOS")).toThrow();
    expect(() => normalizeBasePath("/aios ")).toThrow();
    expect(() => normalizeBasePath(" /aios")).toThrow();
    expect(() => normalizeBasePath("aios")).toThrow();
    expect(() => normalizeBasePath("/")).toThrow();
    expect(() => normalizeBasePath("/app")).toThrow();
    expect(() => normalizeBasePath("/aios/extra")).toThrow();
    expect(() => normalizeBasePath("/aios/../x")).toThrow();
  });
});

describe("api LOGIN_PATH 契约（401 跳转绕过 Next basePath，必须显式带前缀）", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("默认（未设置）构建：LOGIN_PATH 恒为 /login，pathname 判定沿用现状", async () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", undefined);
    const { LOGIN_PATH } = await import("./api");
    expect(LOGIN_PATH).toBe("/login");
    expect("/login".startsWith(LOGIN_PATH)).toBe(true);
    expect("/exam".startsWith(LOGIN_PATH)).toBe(false);
  });

  it("basePath=/aios 构建：LOGIN_PATH 恒为 /aios/login（无双重前缀）", async () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "/aios");
    const { LOGIN_PATH } = await import("./api");
    expect(LOGIN_PATH).toBe("/aios/login");
    expect("/aios/login".startsWith(LOGIN_PATH)).toBe(true);
    expect("/aios/exam".startsWith(LOGIN_PATH)).toBe(false);
    expect(LOGIN_PATH).not.toContain("/aios/aios");
  });

  it("非法值在模块加载期即抛错（fail-closed 传播到构建/运行边界）", async () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "/aios/");
    await expect(import("./api")).rejects.toThrow();
  });
});

// M14-188：401 整页跳转实现的调用契约（warning hygiene 回归防护）。
// 背景：window.location.href = LOGIN_PATH（相对目的地）被 Next 规则
// no-location-assign-relative-destination 拦截；实现改为显式绝对化
// new URL(LOGIN_PATH, window.location.origin).href。以下用例锁住：
//   - 仍以 window.location.href 整页跳转落地（清空客户端状态的安全
//     语义不变，不得退化为 router.push / location.assign）；
//   - 目标 URL 显式携带 BASE_PATH 前缀（默认 /login、/aios 构建
//     /aios/login，无双重前缀），以绝对 URL 形式写入；
//   - 登录页自身 401 不重定向（防循环守卫保留）；非 401 不跳转。
// vitest 为 node 环境：用 stubGlobal 构造 window/fetch 驱动 401 分支。
describe("api 401 整页跳转实现（目标 URL 显式携带 BASE_PATH）", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  function stubWindow(pathname: string) {
    const location = {
      pathname,
      origin: "http://testhost",
      href: `http://testhost${pathname}`,
      assign: vi.fn(),
    };
    vi.stubGlobal("window", { location });
    return location;
  }

  function stubFetchStatus(status: number, statusText: string) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status,
        statusText,
        json: async () => ({ detail: `HTTP ${status}` }),
      })),
    );
  }

  it("默认构建：401 → href 被写为绝对 URL {origin}/login（不走向 assign）", async () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", undefined);
    const location = stubWindow("/exam");
    stubFetchStatus(401, "Unauthorized");
    const { api } = await import("./api");
    await expect(api.me()).rejects.toThrow();
    expect(location.href).toBe("http://testhost/login");
    expect(location.assign).not.toHaveBeenCalled();
  });

  it("basePath=/aios 构建：401 → href 被写为 {origin}/aios/login（前缀显式、无双重）", async () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "/aios");
    const location = stubWindow("/aios/exam");
    stubFetchStatus(401, "Unauthorized");
    const { api } = await import("./api");
    await expect(api.me()).rejects.toThrow();
    expect(location.href).toBe("http://testhost/aios/login");
    expect(location.href).not.toContain("/aios/aios");
    expect(location.assign).not.toHaveBeenCalled();
  });

  it("登录页自身 401 不重定向（防循环守卫保留）", async () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "/aios");
    const location = stubWindow("/aios/login");
    stubFetchStatus(401, "Unauthorized");
    const { api } = await import("./api");
    await expect(api.me()).rejects.toThrow();
    expect(location.href).toBe("http://testhost/aios/login");
  });

  it("非 401 失败不触发跳转（仅认证失效引导登录）", async () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", undefined);
    const location = stubWindow("/exam");
    stubFetchStatus(500, "Internal Server Error");
    const { api } = await import("./api");
    await expect(api.me()).rejects.toThrow();
    expect(location.href).toBe("http://testhost/exam");
  });
});
