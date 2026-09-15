"use client";

// M14-35 LiveKit 连接检测卡片：真实浏览器客户端直连当前部署的 LiveKit。
// 验收面 = 加入房间（connectionState）+ 数据通道 publish +（权限可用时）麦克风音轨发布；
// 不是完整语音会话（ASR/TTS 不在此链路）。
// token 只经局部变量喂给 SDK，绝不进入 React state / DOM / 日志。
import { useCallback, useState } from "react";
import {
  ConnectionState,
  Room,
  Track,
  createLocalTracks,
  type LocalTrack,
} from "livekit-client";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  CHECK_STEP_IDS,
  CONNECT_TIMEOUT_MS,
  DATA_TIMEOUT_MS,
  LIVEKIT_CHECK_DATA_PAYLOAD,
  MIC_TIMEOUT_MS,
  TOKEN_TIMEOUT_MS,
  type CheckOutcome,
  type CheckState,
  type CheckStepId,
  type MicSkipReason,
  type StepStatus,
  checkOutcome,
  classifyMicError,
  generateCheckRoom,
  initialCheckState,
  micSkipSummary,
  sanitizeError,
  stepFinished,
  stepStarted,
  withTimeout,
} from "@/lib/livekit-check";

const STEP_LABELS: Record<CheckStepId, string> = {
  token: "获取房间 token",
  connect: "连接 LiveKit 房间",
  data: "数据通道发布",
  mic: "麦克风音轨发布",
  cleanup: "退出清理",
};

const STATUS_LABELS: Record<StepStatus, string> = {
  pending: "待检测",
  running: "进行中…",
  passed: "通过",
  skipped: "跳过",
  failed: "失败",
};

const STATUS_CLASSES: Record<StepStatus, string> = {
  pending: "text-muted",
  running: "animate-pulse text-muted",
  passed: "text-ink",
  skipped: "text-muted",
  failed: "text-bad",
};

const OUTCOME_TITLES: Record<CheckOutcome, string> = {
  incomplete: "",
  passed: "检测通过：浏览器已连接 LiveKit 并发布麦克风音轨",
  "passed-with-skip": "连接与数据通道通过；麦克风不可用，已跳过",
  failed: "检测失败",
};

async function runLiveKitCheck(apply: (next: CheckState) => void): Promise<CheckState> {
  let state = initialCheckState();
  const commit = (next: CheckState) => {
    state = next;
    apply(next);
  };
  const mark = (
    step: CheckStepId,
    status: "passed" | "skipped" | "failed",
    opts: { micSkipReason?: MicSkipReason; error?: string; wsUrl?: string } = {},
  ) => commit(stepFinished(state, step, status, opts));

  let room: Room | null = null;
  let micTrack: LocalTrack | null = null;
  let failed = false;
  let wsUrl = "";
  let token = ""; // 局部变量：仅在本次检测内使用

  commit(stepStarted(state, "token"));
  const roomName = generateCheckRoom(); // 每次检测唯一房间：并发检测互不可见（R1）
  try {
    const res = await withTimeout(api.voiceToken(roomName), TOKEN_TIMEOUT_MS, "获取 token");
    token = res.token;
    wsUrl = res.ws_url;
    mark("token", "passed", { wsUrl });
  } catch (cause) {
    mark("token", "failed", { error: sanitizeError(cause) });
    failed = true;
  }

  if (!failed) {
    commit(stepStarted(state, "connect"));
    room = new Room();
    try {
      await withTimeout(room.connect(wsUrl, token), CONNECT_TIMEOUT_MS, "连接");
      if (room.state !== ConnectionState.Connected) {
        throw new Error(`连接未完成（state=${room.state}）`);
      }
      mark("connect", "passed");
    } catch (cause) {
      mark("connect", "failed", { error: sanitizeError(cause) });
      failed = true;
    }
  }

  if (!failed && room) {
    commit(stepStarted(state, "data"));
    try {
      const payload = new TextEncoder().encode(LIVEKIT_CHECK_DATA_PAYLOAD);
      await withTimeout(
        room.localParticipant.publishData(payload, { reliable: true }),
        DATA_TIMEOUT_MS,
        "数据通道发布",
      );
      mark("data", "passed");
    } catch (cause) {
      mark("data", "failed", { error: sanitizeError(cause) });
      failed = true;
    }
  }

  // 麦克风不可用不判失败：连接/数据通道才是本切片的验收面，降级跳过并给原因
  if (!failed && room) {
    commit(stepStarted(state, "mic"));
    try {
      const tracks = await withTimeout(
        createLocalTracks({ audio: true, video: false }),
        MIC_TIMEOUT_MS,
        "获取麦克风",
      );
      micTrack = tracks.find((t) => t.kind === Track.Kind.Audio) ?? null;
      if (!micTrack) {
        mark("mic", "skipped", { micSkipReason: "no-device" });
      } else {
        try {
          await withTimeout(
            room.localParticipant.publishTrack(micTrack),
            MIC_TIMEOUT_MS,
            "发布麦克风音轨",
          );
          mark("mic", "passed");
        } catch {
          mark("mic", "skipped", { micSkipReason: "publish-failed" });
        }
      }
    } catch (cause) {
      mark("mic", "skipped", { micSkipReason: classifyMicError(cause) });
    }
  }

  commit(stepStarted(state, "cleanup"));
  try {
    micTrack?.stop();
    if (room && room.state !== ConnectionState.Disconnected) {
      await room.disconnect();
    }
    mark("cleanup", "passed");
  } catch (cause) {
    mark("cleanup", "failed", { error: sanitizeError(cause) });
  }
  return state;
}

export function LiveKitConnectCard() {
  const [state, setState] = useState<CheckState>(initialCheckState);
  const [running, setRunning] = useState(false);
  const outcome = checkOutcome(state);

  const run = useCallback(async () => {
    if (running) return;
    setRunning(true);
    try {
      await runLiveKitCheck(setState);
    } finally {
      setRunning(false);
    }
  }, [running]);

  return (
    <section
      data-livekit-check
      data-check-outcome={outcome}
      className="rounded-lg bg-surface-2/50 p-4 sm:p-5"
      aria-label="LiveKit 连接检测"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium">LiveKit 连接检测</h2>
          <p className="mt-1 max-w-xl text-xs text-muted">
            从当前浏览器直连已部署的 LiveKit：验证加入房间、数据通道与麦克风音轨发布。
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={run} disabled={running}>
          {running ? "检测中…" : "开始检测"}
        </Button>
      </div>

      <ol className="mt-4 space-y-1.5">
        {CHECK_STEP_IDS.map((step) => (
          <li
            key={step}
            data-check-step={step}
            data-check-status={state.steps[step]}
            className="flex items-center justify-between rounded-lg px-2 py-1 text-xs"
          >
            <span className="text-muted">{STEP_LABELS[step]}</span>
            <span className={`font-medium ${STATUS_CLASSES[state.steps[step]]}`}>
              {STATUS_LABELS[state.steps[step]]}
            </span>
          </li>
        ))}
      </ol>

      {state.wsUrl ? (
        <p className="mt-3 text-xs text-muted">
          服务器地址：<span data-check-ws-url={state.wsUrl}>{state.wsUrl}</span>
        </p>
      ) : null}

      {outcome === "passed" || outcome === "passed-with-skip" ? (
        <p data-check-result className="mt-3 rounded-lg bg-surface px-3 py-2 text-xs">
          {OUTCOME_TITLES[outcome]}
        </p>
      ) : null}

      {outcome === "failed" ? (
        <p data-check-result className="mt-3 rounded-lg bg-surface px-3 py-2 text-xs text-bad">
          {OUTCOME_TITLES[outcome]}
          {state.error ? `：${state.error}` : ""}
        </p>
      ) : null}

      {state.steps.mic === "skipped" && state.micSkipReason ? (
        <p data-check-mic-skip={state.micSkipReason} className="mt-2 text-xs text-muted">
          麦克风跳过原因：{micSkipSummary(state.micSkipReason)}
        </p>
      ) : null}

      <p className="mt-4 border-t border-surface-3/40 pt-3 text-[11px] leading-relaxed text-muted">
        这是连接诊断切片：只验证浏览器到 LiveKit 的连接与发布链路，不代表语音会话（ASR/TTS）已接入。
      </p>
    </section>
  );
}
