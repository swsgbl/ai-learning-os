// M14-159 R1 TDD：NEXT_PUBLIC_BASE_PATH 结构化校验 + api.ts 登录跳转契约。
// 契约：未设置/空 → ""（根路径构建不变）；恰为 "/aios" → "/aios"；
// 其他任何值（尾斜杠/查询串/片段/畸形/其他路径）fail-closed 抛错。
// LOGIN_PATH：默认构建恒 "/login"（现状不变）；"/aios" 构建恒 "/aios/login"
// （无双重前缀），window.location 的 401 跳转与 pathname 判定共用该常量。
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
