// M14-35 TDD：LiveKit 连接检测的纯逻辑契约（状态机/房间生成/脱敏/超时/麦克风降级）。
// 这些测试先于实现编写（RED），实现使其变绿（GREEN）。
import { describe, expect, it, vi } from "vitest";

import {
  CHECK_ROOM_PREFIX,
  CHECK_ROOM_RANDOM_CHARS,
  CHECK_STEP_IDS,
  ERROR_MESSAGE_MAX_LENGTH,
  classifyMicError,
  checkOutcome,
  generateCheckRoom,
  initialCheckState,
  micSkipSummary,
  sanitizeError,
  stepFinished,
  stepStarted,
  withTimeout,
} from "./livekit-check";

const ROOM_RE = /^[A-Za-z0-9_-]{3,64}$/;

function allPassedExcept(mic: "passed" | "skipped" = "passed") {
  let state = initialCheckState();
  for (const step of CHECK_STEP_IDS) {
    state = stepStarted(state, step);
    state = stepFinished(state, step, step === "mic" ? mic : "passed");
  }
  return state;
}

// lib.dom 的 getRandomValues 形参类型是 ArrayBufferView | null（含 DataView）：
// mock 统一经 Uint8Array 视图写同一内存区域，与真实实现的写入面等价
function fillBytes(view: ArrayBufferView | null, value: number): void {
  if (view === null) throw new Error("mock: getRandomValues 收到 null");
  new Uint8Array(view.buffer, view.byteOffset, view.byteLength).fill(value);
}

describe("检测房间生成（R1：每次检测唯一房间，防并发互听）", () => {
  it("生成的房间名满足后端 room 校验（3-64 位字母数字/-/_）", () => {
    for (let i = 0; i < 50; i++) {
      expect(ROOM_RE.test(generateCheckRoom())).toBe(true);
    }
  });

  it("形如 web-check-<16 位 URL 安全随机字符>", () => {
    const room = generateCheckRoom();
    expect(room.startsWith(CHECK_ROOM_PREFIX)).toBe(true);
    expect(room.length).toBe(CHECK_ROOM_PREFIX.length + CHECK_ROOM_RANDOM_CHARS);
    expect(room.slice(CHECK_ROOM_PREFIX.length)).toMatch(/^[A-Za-z0-9]+$/);
  });

  it("多次生成不重复（500 次全唯一）", () => {
    const rooms = new Set(Array.from({ length: 500 }, () => generateCheckRoom()));
    expect(rooms.size).toBe(500);
  });

  it("随机源是 Web Crypto getRandomValues，输出由随机字节决定", () => {
    const spy = vi.spyOn(crypto, "getRandomValues").mockImplementation((arr) => {
      fillBytes(arr, 7); // 全部字节=7（<248，无需重掷）→ alphabet[7]
      return arr;
    });
    try {
      const room = generateCheckRoom();
      expect(spy).toHaveBeenCalled();
      expect(room.endsWith("H".repeat(CHECK_ROOM_RANDOM_CHARS))).toBe(true);
    } finally {
      spy.mockRestore();
    }
  });

  it("拒绝采样：偏置字节（>=248）被丢弃重掷，绝不用取模硬吃", () => {
    let call = 0;
    const spy = vi.spyOn(crypto, "getRandomValues").mockImplementation((arr) => {
      fillBytes(arr, call === 0 ? 255 : 7);
      call += 1;
      return arr;
    });
    try {
      const room = generateCheckRoom();
      expect(spy).toHaveBeenCalledTimes(2); // 首轮全偏置被拒，二轮重掷成功
      expect(room.endsWith("H".repeat(CHECK_ROOM_RANDOM_CHARS))).toBe(true);
    } finally {
      spy.mockRestore();
    }
  });
});

describe("检测状态机", () => {
  it("初始态：全部步骤 pending，无错误，结论 incomplete", () => {
    const state = initialCheckState();
    expect(Object.values(state.steps).every((s) => s === "pending")).toBe(true);
    expect(state.error).toBeNull();
    expect(state.wsUrl).toBeNull();
    expect(state.micSkipReason).toBeNull();
    expect(checkOutcome(state)).toBe("incomplete");
  });

  it("stepStarted 标记单步 running，结论仍 incomplete", () => {
    const state = stepStarted(initialCheckState(), "token");
    expect(state.steps.token).toBe("running");
    expect(state.steps.connect).toBe("pending");
    expect(checkOutcome(state)).toBe("incomplete");
  });

  it("全绿路径：五步 passed → 结论 passed，并保留 token 步写入的 ws_url", () => {
    let state = initialCheckState();
    state = stepFinished(state, "token", "passed", { wsUrl: "ws://127.0.0.1:7880" });
    state = stepFinished(state, "connect", "passed");
    state = stepFinished(state, "data", "passed");
    state = stepFinished(state, "mic", "passed");
    state = stepFinished(state, "cleanup", "passed");
    expect(state.wsUrl).toBe("ws://127.0.0.1:7880");
    expect(checkOutcome(state)).toBe("passed");
  });

  it("麦克风跳过：连接/数据通道仍成立 → 结论 passed-with-skip 而非 failed", () => {
    const state = allPassedExcept("skipped");
    expect(state.steps.mic).toBe("skipped");
    expect(checkOutcome(state)).toBe("passed-with-skip");
  });

  it("任一步失败 → 结论 failed，即使后续步骤未到（cleanup 允许补跑）", () => {
    let state = stepFinished(initialCheckState(), "token", "passed", { wsUrl: "ws://x" });
    state = stepFinished(state, "connect", "failed", { error: "连接失败" });
    expect(state.error).toBe("连接失败");
    expect(checkOutcome(state)).toBe("failed");
  });

  it("cleanup 补跑后失败结论保持 failed（清理不翻转判定）", () => {
    let state = stepFinished(initialCheckState(), "token", "failed", { error: "x" });
    state = stepFinished(state, "cleanup", "passed");
    expect(checkOutcome(state)).toBe("failed");
  });

  it("步骤未全部终态 → 结论 incomplete（不虚报完成）", () => {
    let state = stepFinished(initialCheckState(), "token", "passed", { wsUrl: "ws://x" });
    state = stepFinished(state, "connect", "passed");
    expect(checkOutcome(state)).toBe("incomplete");
  });
});

describe("麦克风降级分类", () => {
  it("NotAllowedError → permission-denied，给用户明确原因", () => {
    const err = Object.assign(new Error("Permission denied"), { name: "NotAllowedError" });
    expect(classifyMicError(err)).toBe("permission-denied");
    expect(micSkipSummary("permission-denied")).toContain("权限");
  });

  it("NotFoundError → no-device", () => {
    const err = Object.assign(new Error("Requested device not found"), { name: "NotFoundError" });
    expect(classifyMicError(err)).toBe("no-device");
    expect(micSkipSummary("no-device")).toContain("麦克风");
  });

  it("超时消息 → timeout", () => {
    expect(classifyMicError(new Error("getUserMedia timed out"))).toBe("timeout");
    expect(micSkipSummary("timeout")).toContain("超时");
  });

  it("无法识别 → unknown，文案仍可读", () => {
    expect(classifyMicError(new Error("boom"))).toBe("unknown");
    expect(micSkipSummary("unknown").length).toBeGreaterThan(0);
  });
});

describe("错误脱敏", () => {
  it("保留普通错误消息", () => {
    expect(sanitizeError(new Error("连接被拒绝"))).toBe("连接被拒绝");
  });

  it("绝不透出 JWT 形态的 token（替换为占位符）", () => {
    const fakeJwt = `eyJhbGciOiJIUzI1NiJ9.eyJ2aWRlbyI6eyJyb29tIjoiaiJ9fQ.abc123def456ghi789`;
    const out = sanitizeError(new Error(`connect failed with ${fakeJwt}`));
    expect(out).not.toContain("eyJhbGciOiJIUzI1NiJ9");
    expect(out).toContain("已脱敏");
  });

  it("超长消息截断到上限", () => {
    const out = sanitizeError(new Error("x".repeat(1000)));
    expect(out.length).toBeLessThanOrEqual(ERROR_MESSAGE_MAX_LENGTH + 1);
    expect(out.endsWith("…")).toBe(true);
  });

  it("非 Error 输入兜底为可读文案，空消息同理", () => {
    expect(sanitizeError(undefined)).toContain("未知");
    expect(sanitizeError(new Error(""))).toContain("未知");
  });
});

describe("withTimeout", () => {
  it("按时完成 → 返回原值", async () => {
    await expect(withTimeout(Promise.resolve(42), 1000, "检测")).resolves.toBe(42);
  });

  it("超时 → 拒绝并带步骤名与时长", async () => {
    const slow = new Promise<number>(() => {});
    await expect(withTimeout(slow, 20, "连接")).rejects.toThrow(/连接超时（20ms）/);
  });
});
