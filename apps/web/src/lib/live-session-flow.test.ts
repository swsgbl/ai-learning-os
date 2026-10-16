// M14-274 TDD：Web ↔ 服务端 live 会话编排（live-session-flow.ts）。
// stub 全局 fetch（live 路由契约）——增强面语义：open/close 全部
// fire-and-forget 容错（失败静默降噪，绝不阻塞学生作答主流程抛到
// UI 错误面）；成功路径返回 final transcript 文本数组。
import { afterEach, describe, expect, it, vi } from "vitest";

import { closeLiveSession, openLiveSession } from "./live-session-flow";

function stubFetch(handler: (url: string, init?: RequestInit) => { status: number; body: unknown }) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const { status, body } = handler(url, init);
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("openLiveSession（增强面容错）", () => {
  it("成功（201/200 均可）不抛出", async () => {
    const fetchMock = stubFetch(() => ({
      status: 201,
      body: { session_id: "s1", identity: "server-sub-x", closed: false },
    }));
    await expect(openLiveSession("s1", "voice-s1")).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url.endsWith("/api/v1/voice/live-sessions/s1/open")).toBe(true);
    expect(init?.method).toBe("POST");
    expect(String(init?.body)).toBe(JSON.stringify({ room: "voice-s1" }));
  });

  it("404（服务端旧版无 live 路由）静默", async () => {
    stubFetch(() => ({ status: 404, body: { detail: "Not Found" } }));
    await expect(openLiveSession("s1", "voice-s1")).resolves.toBeUndefined();
  });

  it("5xx 静默降噪（增强面失败不进 UI）", async () => {
    stubFetch(() => ({ status: 503, body: { detail: "pipeline" } }));
    await expect(openLiveSession("s1", "voice-s1")).resolves.toBeUndefined();
  });

  it("网络失败静默", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(openLiveSession("s1", "voice-s1")).resolves.toBeUndefined();
  });
});

describe("closeLiveSession（final transcript）", () => {
  it("成功返回文本数组", async () => {
    const fetchMock = stubFetch(() => ({
      status: 200,
      body: {
        session_id: "s1",
        closed: true,
        transcript: {
          segment_count: 2,
          segments: [
            { seq: 0, text: "选 A", is_final: true },
            { seq: 1, text: "第二个", is_final: true },
          ],
        },
      },
    }));
    await expect(closeLiveSession("s1")).resolves.toEqual(["选 A", "第二个"]);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url.endsWith("/api/v1/voice/live-sessions/s1")).toBe(true);
    expect(init?.method).toBe("DELETE");
  });

  it("404（从未打开/已关）返回 null", async () => {
    stubFetch(() => ({ status: 404, body: { detail: "live 会话不存在" } }));
    await expect(closeLiveSession("gone")).resolves.toBeNull();
  });

  it("空段 transcript 返回空数组（长度 0——UI 门控由调用方）", async () => {
    stubFetch(() => ({
      status: 200,
      body: { session_id: "s1", closed: true, transcript: { segment_count: 0, segments: [] } },
    }));
    await expect(closeLiveSession("s1")).resolves.toEqual([]);
  });

  it("网络失败返回 null（不抛）", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("offline"));
    await expect(closeLiveSession("s1")).resolves.toBeNull();
  });
});
