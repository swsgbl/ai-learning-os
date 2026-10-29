"use client";

// M14-164 通用下载/安装面板：任何手机访问 /download 都得到诚实入口。
// - PWA 卡：beforeinstallprompt 触发时给「安装到本机」按钮；iOS Safari
//   （无该事件）显示「添加到主屏幕」分步指引；已安装（standalone 模式）
//   如实显示已安装；其余环境显示通用指引。安装能力探测失败静默降级。
// - Android 卡：静态默认来自 src/lib/download.ts（无 manifest 时恒
//   pending、无链接——诚实默认）；运行时读取同源 download manifest
//   （src/lib/download-manifest.ts），仅当 android 频道恰好一个
//   signed=true 且结构可信的条目时升级为可下载。获取/校验失败静默
//   降级 pending，绝不显示错误链接。manifest 内容不写死在本组件。
// - Harmony 卡：状态来自 src/lib/download.ts，AGC 签名未落地前恒 pending。
import { useEffect, useState, useSyncExternalStore } from "react";
import { CheckCircle2, Clock, Download, MonitorSmartphone, Share, Smartphone } from "lucide-react";
import {
  DOWNLOAD_CHANNELS,
  PWA_INSTALL_HINTS,
  androidRequirementLabel,
  detectPlatformFromUserAgent,
  type DetectedPlatform,
} from "@/lib/download";
import {
  fetchAndroidChannelState,
  type AndroidChannelState,
} from "@/lib/download-manifest";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

// beforeinstallprompt 是非标准事件（Chromium 系）：局部类型声明即可，
// 不放宽全局 DOM 类型。
interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

/** PWA 安装交互状态：能力探测完成前为 null（避免闪烁）。 */
type PwaInstallState =
  | { kind: "probing" }
  | { kind: "promptable"; event: BeforeInstallPromptEvent }
  | { kind: "installed" }
  | { kind: "unsupported-ios" }
  | { kind: "unsupported" };

// M14-189: standalone（已安装显示模式）改经 useSyncExternalStore 派生——
// display-mode 媒体查询是外部 store：订阅 change、快照读 matches，消除
// effect 内同步 setState（set-state-in-effect 级联渲染）；SSR/水合恒
// false（探测期），水合后同步真实值，行为与原「effect 首跑分流」等价。
const STANDALONE_QUERY = "(display-mode: standalone)";

function subscribeStandalone(onChange: () => void) {
  const query = window.matchMedia(STANDALONE_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

function getStandaloneSnapshot(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia(STANDALONE_QUERY).matches;
}

function getStandaloneServerSnapshot(): boolean {
  return false;
}

function detectIOS() {
  if (typeof navigator === "undefined") return false;
  // iPadOS 桌面 UA 也以 Macintosh 出现且多点触控；Safari 主屏入口一致
  const ua = navigator.userAgent;
  return /iPhone|iPad|iPod/i.test(ua) || (/Macintosh/i.test(ua) && navigator.maxTouchPoints > 1);
}

function usePwaInstallState(): PwaInstallState {
  const standalone = useSyncExternalStore(
    subscribeStandalone,
    getStandaloneSnapshot,
    getStandaloneServerSnapshot,
  );
  const [state, setState] = useState<PwaInstallState>({ kind: "probing" });

  // 仅在未安装（非 standalone）时订阅安装事件；所有 setState 都发生在
  // 事件/定时器回调内（合规路径），effect 体内零同步 setState。
  useEffect(() => {
    if (standalone) return;
    const onPrompt = (e: Event) => {
      e.preventDefault();
      setState({ kind: "promptable", event: e as BeforeInstallPromptEvent });
    };
    const onInstalled = () => setState({ kind: "installed" });
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    // 探测收尾：未触发 prompt 事件的平台按能力分流（iOS 指引 / 通用说明）
    const timer = window.setTimeout(() => {
      setState((prev) => {
        if (prev.kind !== "probing") return prev;
        return detectIOS() ? { kind: "unsupported-ios" } : { kind: "unsupported" };
      });
    }, 300);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
      window.clearTimeout(timer);
    };
  }, [standalone]);

  // standalone 为真时已安装，state 事件机结果不再有意义（原实现同理直接短路）
  if (standalone) return { kind: "installed" };
  return state;
}

function InstallButton({ state }: { state: PwaInstallState }) {
  const [installing, setInstalling] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  if (state.kind === "installed") {
    return (
      <p className="flex items-center gap-2 text-sm text-good">
        <CheckCircle2 className="size-4" aria-hidden="true" />
        已安装——你可以从主屏或桌面直接打开砚席。
      </p>
    );
  }

  if (state.kind === "promptable") {
    return (
      <button
        type="button"
        disabled={installing || dismissed}
        onClick={async () => {
          setInstalling(true);
          try {
            await state.event.prompt();
            const choice = await state.event.userChoice;
            if (choice.outcome === "dismissed") setDismissed(true);
          } finally {
            setInstalling(false);
          }
        }}
        className="min-h-11 rounded-lg bg-accent px-4 text-sm font-medium text-accent-fg transition-opacity duration-150 hover:opacity-90 disabled:opacity-60"
      >
        {installing ? "正在唤起安装…" : dismissed ? "已取消，可稍后再安装" : "安装到本机"}
      </button>
    );
  }

  // iOS Safari：无安装事件，给分步指引（PWA_INSTALL_HINTS.ios）
  if (state.kind === "unsupported-ios") {
    return (
      <div className="rounded-lg bg-surface-2 p-4">
        <p className="flex items-center gap-2 text-sm text-ink">
          <Share className="size-4 text-accent" aria-hidden="true" />
          iPhone / iPad 安装步骤
        </p>
        <p className="mt-2 text-sm leading-relaxed text-muted">{PWA_INSTALL_HINTS.ios}</p>
      </div>
    );
  }

  // probing / unsupported：通用指引（桌面菜单安装入口等）
  return (
    <p className="text-sm leading-relaxed text-muted">{PWA_INSTALL_HINTS.generic}</p>
  );
}

function ChannelStatusIcon({ status }: { status: "available" | "pending" }) {
  return status === "available" ? (
    <CheckCircle2 className="size-4 shrink-0 text-good" aria-hidden="true" />
  ) : (
    <Clock className="size-4 shrink-0 text-warn" aria-hidden="true" />
  );
}

/**
 * M21 设备识别推荐：UA 判定访问设备 → 顶部「为你推荐」一句话。
 * SSR 首帧 unknown（不渲染），水合后探测——避免水合不匹配。
 */
function useDetectedPlatform(): DetectedPlatform {
  const getSnapshot = () =>
    typeof navigator === "undefined"
      ? ("unknown" as const)
      : detectPlatformFromUserAgent(navigator.userAgent);
  return useSyncExternalStore(
    () => () => {},
    getSnapshot,
    () => "unknown" as const,
  );
}

const PLATFORM_RECOMMENDATIONS: Record<
  Exclude<DetectedPlatform, "unknown">,
  { icon: typeof Smartphone; text: string }
> = {
  android: {
    icon: Smartphone,
    text: "检测到你正在使用 Android 设备——推荐下方「Android 原生应用（APK 安装包）」；不方便装 APK 时也可用网页应用（PWA）。",
  },
  ios: {
    icon: Smartphone,
    text: "检测到你正在使用 iPhone / iPad——请安装「网页应用（PWA）」（见下方 iPhone 安装步骤）。此网站的 APK / hap 包不适用于苹果设备。",
  },
  harmony: {
    icon: Smartphone,
    text: "检测到你正在使用 HarmonyOS / 深开鸿设备——当前推荐安装「网页应用（PWA）」；HarmonyOS 原生应用（hap）待发布，Android 的 APK 包不适用于你的设备。",
  },
};

function PlatformRecommendationBar({ platform }: { platform: DetectedPlatform }) {
  if (platform === "unknown") return null;
  const rec = PLATFORM_RECOMMENDATIONS[platform];
  const Icon = rec.icon;
  return (
    <div
      data-testid="platform-recommendation"
      className="flex items-start gap-3 rounded-lg border border-accent-soft bg-accent-soft p-4"
    >
      <Icon className="mt-0.5 size-5 shrink-0 text-accent" aria-hidden="true" />
      <p className="text-sm leading-relaxed text-ink">{rec.text}</p>
    </div>
  );
}

/** 字节数 → MB 文案（下载卡信息行用）。 */
function formatSizeMB(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Android 渠道运行时状态：初始 pending（诚实默认），manifest 可信才 available。 */
function useAndroidChannelState(): AndroidChannelState {
  const [state, setState] = useState<AndroidChannelState>({
    status: "pending",
  });

  useEffect(() => {
    let cancelled = false;
    // 任何失败都在 lib 内静默降级 pending——这里只负责呈现。
    fetchAndroidChannelState().then((resolved) => {
      if (!cancelled) setState(resolved);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}

export function DownloadPanel() {
  const pwaState = usePwaInstallState();
  const androidState = useAndroidChannelState();
  const platform = useDetectedPlatform();
  const channels = DOWNLOAD_CHANNELS;
  const pwa = channels.find((c) => c.id === "pwa")!;
  const native = channels.filter((c) => c.id !== "pwa");

  return (
    <div className="space-y-6">
      <section>
        <p className="text-sm text-muted">下载与安装</p>
        <h1 className="mt-2 font-display text-3xl leading-snug">把砚席装进手机</h1>
        <p className="mt-3 max-w-xl text-sm leading-relaxed text-muted">
          每个安装包只适用于一种操作系统——请先看下面这一句，再按你的设备选择：
          Android 手机下 <strong className="font-medium text-ink">APK</strong>，
          HarmonyOS / 深开鸿设备装 <strong className="font-medium text-ink">网页应用</strong>（原生 hap 待发布），
          iPhone / iPad 与电脑装 <strong className="font-medium text-ink">网页应用（PWA）</strong>。
        </p>
      </section>

      <PlatformRecommendationBar platform={platform} />

      {/* PWA：可用渠道 + 安装交互 */}
      <Card className="p-5 sm:p-6" data-testid="channel-pwa">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Smartphone className="size-5 text-accent" aria-hidden="true" />
            <h2 className="font-display text-xl">{pwa.title}</h2>
          </div>
          <span className="inline-flex items-center gap-1.5 rounded-md bg-good-soft px-2 py-1 text-xs text-good">
            <ChannelStatusIcon status={pwa.status} />
            {pwa.statusLabel}
          </span>
        </div>
        <p className="mt-3 text-sm leading-relaxed text-muted">{pwa.description}</p>
        <div className="mt-3 flex items-start gap-2 text-xs leading-relaxed text-subtle">
          <MonitorSmartphone className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>
            适用系统：{pwa.platforms} · {pwa.packageKind}
          </span>
        </div>
        <div className="mt-4">
          <InstallButton state={pwaState} />
        </div>
      </Card>

      {/* 原生渠道：默认 pending 如实展示；Android 经可信 manifest 升级可下载 */}
      <section
        aria-label="原生应用状态"
        className={cn("grid gap-3", native.length > 1 && "sm:grid-cols-2")}
      >
        {native.map((channel) => {
          const androidOverride =
            channel.id === "android" && androidState.status === "available"
              ? androidState
              : null;
          const status = androidOverride ? "available" : channel.status;
          const statusLabel = androidOverride
            ? "可下载"
            : channel.statusLabel;
          const badgeTone = androidOverride
            ? "bg-good-soft text-good"
            : "bg-warn-soft text-warn";
          return (
            <Card
              key={channel.id}
              className="p-5"
              data-testid={`channel-${channel.id}`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="font-display text-lg">{channel.title}</h2>
                <span
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs",
                    badgeTone,
                  )}
                >
                  <ChannelStatusIcon status={status} />
                  {statusLabel}
                </span>
              </div>
              {androidOverride ? (
                <>
                  <p className="mt-3 text-sm leading-relaxed text-muted">
                    Android 原生应用签名安装包已发布，可从下方下载安装。
                  </p>
                  <div className="mt-3 flex items-start gap-2 text-xs leading-relaxed text-subtle">
                    <MonitorSmartphone className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                    <span>
                      适用系统：Android 手机 / 平板
                      {androidRequirementLabel(androidOverride.minApiLevel)
                        ? `（${androidRequirementLabel(androidOverride.minApiLevel)}）`
                        : ""}
                       · 包类型：APK（{formatSizeMB(androidOverride.sizeBytes)}）
                       · 不适用于 iPhone / iPad / HarmonyOS
                    </span>
                  </div>
                  <div className="mt-4">
                    <a
                      href={androidOverride.href}
                      download
                      className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-accent px-4 text-sm font-medium text-accent-fg transition-opacity duration-150 hover:opacity-90"
                    >
                      <Download className="size-4" aria-hidden="true" />
                      下载 APK 安装包
                      {androidOverride.versionName
                        ? `（v${androidOverride.versionName}）`
                        : ""}
                    </a>
                  </div>
                  <p className="mt-3 text-xs leading-relaxed text-subtle">
                    安装方法：下载后点开 APK 文件；若系统提示「未知来源应用」，允许本次安装即可（包已签名，SHA-256 校验值见下载属性）。
                  </p>
                </>
              ) : (
                <>
                  <p className="mt-3 text-sm leading-relaxed text-muted">
                    {channel.description}
                  </p>
                  <div className="mt-3 flex items-start gap-2 text-xs leading-relaxed text-subtle">
                    <MonitorSmartphone className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                    <span>
                      适用系统：{channel.platforms} · 包类型：{channel.packageKind}
                    </span>
                  </div>
                  {channel.reason && (
                    <p className="mt-3 border-t border-border pt-3 text-xs leading-relaxed text-subtle">
                      原因：{channel.reason}
                    </p>
                  )}
                </>
              )}
            </Card>
          );
        })}
      </section>
    </div>
  );
}
