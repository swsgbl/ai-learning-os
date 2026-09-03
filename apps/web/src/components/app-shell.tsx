"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  BookOpen,
  EyeOff,
  Headphones,
  Home,
  Library,
  LineChart,
  LogIn,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { API_BASE } from "@/lib/api";
import { clearSession, probeAuth, type AuthState } from "@/lib/auth";

const BASE_NAV: Array<{ href: string; label: string; icon: LucideIcon }> = [
  { href: "/", label: "首页", icon: Home },
  { href: "/voice", label: "语音陪练", icon: Headphones },
  { href: "/exam", label: "考场审阅", icon: BookOpen },
  { href: "/library", label: "学习库", icon: Library },
  { href: "/progress", label: "掌握", icon: LineChart },
];

// M10-02: 治理入口只对 admin / 本地模式渲染（role 来自 auth/me）。
// 这只是入口可见性——安全边界在后端 require_admin，learner 直接访问 /governance
// 会被 API 403 拦下并得到无泄露提示。
function useAuthState(): AuthState | null {
  const [state, setState] = useState<AuthState | null>(null);
  useEffect(() => {
    let active = true;
    probeAuth(API_BASE)
      .then((s) => {
        if (active) setState(s);
      })
      .catch(() => {
        if (active) setState({ mode: "anonymous" });
      });
    return () => {
      active = false;
    };
  }, []);
  return state;
}

function AuthBadge({ state }: { state: AuthState | null }) {
  const router = useRouter();

  if (!state) return null; // 探测中不闪烁

  if (state.mode === "disabled") {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-md bg-surface-2 px-2 py-1 text-[11px] text-muted"
        title="API 未配置 AUTH_SECRET，数据仅保存在本机"
      >
        <EyeOff className="size-3.5" /> 本地模式
      </span>
    );
  }

  if (state.mode === "anonymous") {
    return (
      <Link
        href="/login"
        className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90"
      >
        <LogIn className="size-3.5" /> 登录
      </Link>
    );
  }

  return (
    <span className="inline-flex items-center gap-2 text-xs">
      <span className="max-w-24 truncate rounded-md bg-surface-2 px-2 py-1 text-ink">
        {state.username}
      </span>
      <button
        type="button"
        className="text-muted hover:text-ink"
        onClick={() => {
          clearSession();
          router.refresh();
        }}
      >
        退出
      </button>
    </span>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const auth = useAuthState();
  const immersive = /^\/(voice|exam)\/[^/]+/.test(pathname);
  // 本地模式 = 单用户即治理者（后端 auth off 放行治理端点），同样显示入口
  const governanceVisible =
    auth?.mode === "disabled" || (auth?.mode === "authenticated" && auth.role === "admin");
  const nav = governanceVisible
    ? [...BASE_NAV, { href: "/governance", label: "治理", icon: ShieldCheck }]
    : BASE_NAV;

  return (
    <div className="min-h-dvh bg-bg text-ink">
      <header className="sticky top-0 z-30 border-b border-border/80 bg-bg/90 backdrop-blur-sm">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between px-4">
          <Link href="/" className="flex items-baseline gap-2">
            <span className="font-display text-xl font-medium">砚席</span>
            <span className="hidden text-xs text-muted sm:inline">AI Learning OS</span>
          </Link>
          <nav className="hidden items-center gap-1 md:flex">
            {nav.map((item) => {
              const active = item.href === "/" ? pathname === "/" : pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    "rounded-lg px-3 py-2 text-sm transition-colors",
                    active ? "bg-surface-2 text-ink" : "text-muted hover:text-ink",
                  )}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <div className="flex items-center gap-2">
            <AuthBadge state={auth} />
          </div>
        </div>
      </header>
      <main className={cn("mx-auto w-full max-w-5xl px-4 py-6", immersive ? "pb-8" : "pb-24 md:pb-10")}>{children}</main>
      {!immersive && (
        <nav className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-sm md:hidden">
          <ul className={cn("grid", governanceVisible ? "grid-cols-6" : "grid-cols-5")}>
            {nav.map((item) => {
              const active = item.href === "/" ? pathname === "/" : pathname === item.href || pathname.startsWith(`${item.href}/`);
              const Icon = item.icon;
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    className={cn(
                      "flex min-h-14 flex-col items-center justify-center gap-0.5 text-[11px]",
                      active ? "text-accent" : "text-muted",
                    )}
                  >
                    <Icon className="size-5" strokeWidth={active ? 2.2 : 1.7} />
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      )}
    </div>
  );
}
