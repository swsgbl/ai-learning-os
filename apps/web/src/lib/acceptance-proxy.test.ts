// M14-35 R1 TDD：验收代理 env（AIOS_ACCEPTANCE_API_PROXY）的结构化校验与规范化。
// 契约：未设置 → null（无 rewrite，默认构建完全不变）；
// 合法 http/https → 规范化（去尾斜杠）；其他任何值 → 构建期抛错 fail-closed。
import { describe, expect, it } from "vitest";

import { normalizeAcceptanceApiProxy } from "./acceptance-proxy";

describe("normalizeAcceptanceApiProxy", () => {
  it("未设置 / 空串 → null（不生成 rewrite，默认构建行为不变）", () => {
    expect(normalizeAcceptanceApiProxy(undefined)).toBeNull();
    expect(normalizeAcceptanceApiProxy("")).toBeNull();
  });

  it("合法 http 源原样规范化（无尾斜杠）", () => {
    expect(normalizeAcceptanceApiProxy("http://127.0.0.1:8000")).toBe("http://127.0.0.1:8000");
  });

  it("尾斜杠被去掉（含多个）", () => {
    expect(normalizeAcceptanceApiProxy("http://127.0.0.1:8000/")).toBe("http://127.0.0.1:8000");
    expect(normalizeAcceptanceApiProxy("https://api.example.com/base///")).toBe("https://api.example.com/base");
    expect(normalizeAcceptanceApiProxy("https://api.example.com/")).toBe("https://api.example.com");
  });

  it("仅根路径时仅保留 origin", () => {
    expect(normalizeAcceptanceApiProxy("http://localhost:8000/")).toBe("http://localhost:8000");
  });

  it("非 http/https 协议 fail-closed 抛错（ws/file/ftp）", () => {
    expect(() => normalizeAcceptanceApiProxy("ws://127.0.0.1:7880")).toThrow();
    expect(() => normalizeAcceptanceApiProxy("file:///etc/passwd")).toThrow();
    expect(() => normalizeAcceptanceApiProxy("ftp://example.com")).toThrow();
  });

  it("无法解析的输入 fail-closed 抛错，绝不静默生成异常 rewrite", () => {
    expect(() => normalizeAcceptanceApiProxy("not a url")).toThrow();
    expect(() => normalizeAcceptanceApiProxy("127.0.0.1:8000")).toThrow(); // 无协议前缀
    expect(() => normalizeAcceptanceApiProxy("http://")).toThrow();
  });

  it("带查询串/片段的输入 fail-closed 抛错（会污染 rewrite 目标）", () => {
    expect(() => normalizeAcceptanceApiProxy("http://127.0.0.1:8000/?x=1")).toThrow();
    expect(() => normalizeAcceptanceApiProxy("http://127.0.0.1:8000/#frag")).toThrow();
  });
});
