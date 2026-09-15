// M14-35：浏览器 LiveKit 连接检测的纯逻辑（状态机 / 房间生成 / 错误脱敏 / 超时 / 麦克风降级）。
// 为什么单独抽出：这些规则必须可单测（vitest），且不能依赖浏览器 API，
// 组件层只负责把 livekit-client SDK 的调用编排进来。
export const LIVEKIT_CHECK_DATA_PAYLOAD = "m14-35-web-connect-check";

// ---------- 检测房间生成（R1：每次检测唯一房间，防并发互听） ----------
// student grants 可订阅：固定房间会让多用户同时检测时互相收到音频/数据。
// 每次点击生成 web-check-<16 位 URL 安全随机字符>，随机源必须是 Web Crypto。

export const CHECK_ROOM_PREFIX = "web-check-";
export const CHECK_ROOM_RANDOM_CHARS = 16;

// 62 字符 URL 安全字母表；4×62=248：>=248 的字节被拒绝重掷，避免取模偏置
const CHECK_ROOM_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
const CHECK_ROOM_REJECT_BYTE_AT = 4 * CHECK_ROOM_ALPHABET.length; // 248

export function generateCheckRoom(): string {
  let random = "";
  while (random.length < CHECK_ROOM_RANDOM_CHARS) {
    const bytes = new Uint8Array(CHECK_ROOM_RANDOM_CHARS);
    crypto.getRandomValues(bytes);
    for (const byte of bytes) {
      if (byte >= CHECK_ROOM_REJECT_BYTE_AT) continue; // 拒绝采样
      random += CHECK_ROOM_ALPHABET[byte % CHECK_ROOM_ALPHABET.length];
      if (random.length === CHECK_ROOM_RANDOM_CHARS) break;
    }
  }
  return CHECK_ROOM_PREFIX + random;
}

// 检测五步：token → connect → data → mic → cleanup（顺序固定，见组件编排）
export type CheckStepId = "token" | "connect" | "data" | "mic" | "cleanup";
export const CHECK_STEP_IDS: readonly CheckStepId[] = ["token", "connect", "data", "mic", "cleanup"] as const;

export type StepStatus = "pending" | "running" | "passed" | "skipped" | "failed";

// 麦克风不可用不判失败（连接/数据通道才是本切片的验收面），但要给用户明确原因
export type MicSkipReason = "permission-denied" | "no-device" | "timeout" | "publish-failed" | "unknown";

export type CheckState = {
  readonly steps: Readonly<Record<CheckStepId, StepStatus>>;
  readonly micSkipReason: MicSkipReason | null;
  readonly error: string | null;
  readonly wsUrl: string | null; // token 响应的 ws_url（非敏感，可展示）
};

export type CheckOutcome = "incomplete" | "passed" | "passed-with-skip" | "failed";

export const ERROR_MESSAGE_MAX_LENGTH = 200;

// 各步骤兜底超时（防 UI 永久挂起；正常路径远快于此）
export const TOKEN_TIMEOUT_MS = 10_000;
export const CONNECT_TIMEOUT_MS = 20_000;
export const DATA_TIMEOUT_MS = 10_000;
export const MIC_TIMEOUT_MS = 15_000;

export function initialCheckState(): CheckState {
  return {
    steps: { token: "pending", connect: "pending", data: "pending", mic: "pending", cleanup: "pending" },
    micSkipReason: null,
    error: null,
    wsUrl: null,
  };
}

export function stepStarted(state: CheckState, step: CheckStepId): CheckState {
  return { ...state, steps: { ...state.steps, [step]: "running" } };
}

export function stepFinished(
  state: CheckState,
  step: CheckStepId,
  status: "passed" | "skipped" | "failed",
  opts: { micSkipReason?: MicSkipReason; error?: string; wsUrl?: string } = {},
): CheckState {
  return {
    ...state,
    steps: { ...state.steps, [step]: status },
    micSkipReason: opts.micSkipReason ?? state.micSkipReason,
    error: opts.error ?? state.error,
    wsUrl: opts.wsUrl ?? state.wsUrl,
  };
}

export function checkOutcome(state: CheckState): CheckOutcome {
  if (Object.values(state.steps).some((s) => s === "failed")) {
    return "failed";
  }
  if (!Object.values(state.steps).every((s) => s === "passed" || s === "skipped")) {
    return "incomplete"; // 步骤未全部终态，不虚报完成
  }
  return state.steps.mic === "skipped" ? "passed-with-skip" : "passed";
}

// ---------- 麦克风降级 ----------

const MIC_SKIP_TEXT: Record<MicSkipReason, string> = {
  "permission-denied": "麦克风权限被拒绝",
  "no-device": "未找到可用的麦克风设备",
  timeout: "获取麦克风超时",
  "publish-failed": "麦克风已采集但发布失败",
  unknown: "麦克风不可用",
};

export function micSkipSummary(reason: MicSkipReason): string {
  return MIC_SKIP_TEXT[reason];
}

export function classifyMicError(error: unknown): MicSkipReason {
  const name = (error as { name?: unknown } | null | undefined)?.name;
  const message = error instanceof Error ? error.message : typeof error === "string" ? error : "";
  if (name === "NotAllowedError" || /permission|not allowed|denied/i.test(message)) {
    return "permission-denied";
  }
  if (name === "NotFoundError" || /no (audio|device)|not ?found|requested device/i.test(message)) {
    return "no-device";
  }
  if (name === "TimeoutError" || /timed? ?out|超时/i.test(message)) {
    return "timeout";
  }
  return "unknown";
}

// ---------- 错误脱敏：绝不把 token/key 带进 UI 或日志 ----------

const JWT_LIKE_RE = /eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}/g;

export function sanitizeError(error: unknown): string {
  let message: string;
  if (error instanceof Error && error.message) {
    message = error.message;
  } else if (typeof error === "string" && error) {
    message = error;
  } else {
    message = "未知错误";
  }
  message = message.replace(JWT_LIKE_RE, "[已脱敏]");
  message = message.replace(/\s+/g, " ").trim();
  if (message.length > ERROR_MESSAGE_MAX_LENGTH) {
    message = message.slice(0, ERROR_MESSAGE_MAX_LENGTH) + "…";
  }
  return message || "未知错误";
}

// ---------- 超时兜底 ----------

export async function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    // 提前挂一个空 catch 分支：竞速输掉的拒绝不再触发 unhandledrejection
    void promise.catch(() => undefined);
    return await Promise.race([
      promise,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error(`${label}超时（${ms}ms）`)), ms);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}
