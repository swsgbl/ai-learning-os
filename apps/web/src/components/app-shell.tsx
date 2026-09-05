"use client";

// M11-01 AppShell 体验重设计：
// - 桌面导航：active 下划线指示（scaleX 过渡）+ hover/focus 反馈 + aria-current
// - 移动底栏：active 指示点 + 图标/字重反馈，触控目标 >=44px
// - 路由进入过渡：PageTransition（reduced-motion 归零）
// - 导航不遮挡内容：main 预留底栏高度的 padding
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
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
import { gsap, useMotion } from "@/lib/gsap";
import { MOTION } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { API_BASE } from "@/lib/api";
import { logout, probeAuth, type AuthState } from "@/lib/auth";
import { PageTransition } from "./motion/page-transition";

const BASE_NAV: Array<{ href: string; label: string; icon: LucideIcon }> = [
  { href: "/", label: "首页", icon: Home },
  { href: "/voice", label: "语音陪练", icon: Headphones },
  { href: "/exam", label: "考场审阅", icon: BookOpen },
  { href: "/library", label: "学习库", icon: Library },
  { href: "/progress", label: "工作台", icon: LineChart },
];

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

// M10-02: 治理入口只对 admin / 本地模式渲染（role 来自 auth/me）。
// 这只是入口可见性——安全边界在后端 require_admin，learner 直接访问 /governance
// 会被 API 403 拦下并得到无泄露提示。
function useAuthState(): [AuthState | null, (next: AuthState | null) => void] {
  const pathname = usePathname();
  const [state, setState] = useState<AuthState | null>(null);
  useEffect(() => {
    // 路由变化时重新探测：登录页 saveSession 后 router.push 不会重挂 AppShell，
    // 用户徽章与治理入口需要在这里跟上新凭据（M9-03 遗留，M10-02 治理入口同样依赖）
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
  }, [pathname]);
  return [state, setState];
}

function AuthBadge({ state, onLogout }: { state: AuthState | null; onLogout: () => void }) {
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
        className="inline-flex min-h-9 items-center gap-1.5 rounded-lg bg-accent px-3 text-xs font-medium text-accent-fg transition-opacity duration-150 hover:opacity-90"
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
        className="min-h-11 rounded-md px-3 text-muted transition-colors duration-150 hover:text-ink md:min-h-9 md:px-1"
        onClick={onLogout}
      >
        退出
      </button>
    </span>
  );
}

/** 桌面导航链接：active 下划线（scaleX，transform-only）+ hover/focus 提示 */
function NavLink({
  href,
  label,
  active,
}: {
  href: string;
  label: string;
  active: boolean;
}) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "group relative rounded-lg px-3 py-2 text-sm outline-offset-4 transition-colors duration-150",
        active ? "text-accent-strong" : "text-muted hover:text-ink",
      )}
    >
      {label}
      <span
        aria-hidden="true"
        className={cn(
          "absolute inset-x-3 -bottom-px h-0.5 origin-left rounded-full transition-transform duration-200 ease-[var(--ease-out)]",
          active
            ? "scale-x-100 bg-accent"
            : "scale-x-0 bg-border-strong group-hover:scale-x-100 group-focus-visible:scale-x-100",
        )}
      />
    </Link>
  );
}

/** 移动底栏项：active 时顶部指示点 + 图标着色 + 字重反馈 */
function TabLink({
  href,
  label,
  icon: Icon,
  active,
}: {
  href: string;
  label: string;
  icon: LucideIcon;
  active: boolean;
}) {
  const ref = useRef<HTMLAnchorElement>(null);
  const dotRef = useRef<HTMLSpanElement>(null);

  // active 切换时指示点 scale 弹入（transform-only）；reduced-motion 静态显示
  useMotion(
    (reduced) => {
      if (!dotRef.current) return;
      if (reduced) {
        gsap.set(dotRef.current, { clearProps: "transform" });
        return;
      }
      gsap.fromTo(
        dotRef.current,
        { scale: 0 },
        {
          scale: 1,
          duration: MOTION.duration.base,
          ease: MOTION.ease.out,
          clearProps: "transform",
        },
      );
    },
    { scope: ref, dependencies: [active] },
  );

  return (
    <Link
      ref={ref}
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "relative flex min-h-14 flex-col items-center justify-center gap-0.5 rounded-lg px-1 py-1.5 text-[11px] outline-offset-2 transition-colors duration-150",
        active ? "text-accent-strong" : "text-muted",
      )}
    >
      <span
        ref={dotRef}
        aria-hidden="true"
        className={cn(
          "absolute top-0.5 h-1 w-6 rounded-full bg-accent",
          !active && "hidden",
        )}
      />
      <Icon className="size-5" strokeWidth={active ? 2.2 : 1.7} aria-hidden="true" />
      <span className={cn(active && "font-medium")}>{label}</span>
    </Link>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [auth, setAuth] = useAuthState();
  const immersive = /^\/(voice|exam)\/[^/]+/.test(pathname);
  // 本地模式 = 单用户即治理者（后端 auth off 放行治理端点），同样显示入口
  const governanceVisible =
    auth?.mode === "disabled" || (auth?.mode === "authenticated" && auth.role === "admin");
  const nav = governanceVisible
    ? [...BASE_NAV, { href: "/governance", label: "治理", icon: ShieldCheck }]
    : BASE_NAV;

  // 退出必须立即生效：同步置 anonymous 让徽章/治理入口当场消失（不等下一次
  // 路由探测），并跳登录页离开可能含私有数据的工作台/治理页
  const handleLogout = async () => {
    await logout(API_BASE);
    setAuth({ mode: "anonymous" });
    router.push("/login");
  };

  return (
    <div className="min-h-dvh bg-bg text-ink">
      <header className="sticky top-0 z-30 border-b border-border/80 bg-bg/85 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-3 px-4">
          <Link href="/" className="flex items-baseline gap-2 rounded-md outline-offset-4">
            <span className="font-display text-xl font-medium tracking-tight">砚席</span>
            <span className="hidden text-xs text-subtle sm:inline">AI Learning OS</span>
          </Link>
          <nav aria-label="主导航" className="hidden items-center gap-0.5 md:flex">
            {nav.map((item) => (
              <NavLink
                key={item.href}
                href={item.href}
                label={item.label}
                active={isActive(pathname, item.href)}
              />
            ))}
          </nav>
          <div className="flex items-center gap-2">
            <AuthBadge state={auth} onLogout={handleLogout} />
          </div>
        </div>
      </header>
      <main className={cn("mx-auto w-full max-w-5xl px-4 py-6", immersive ? "pb-8" : "pb-24 md:pb-10")}>
        <PageTransition>{children}</PageTransition>
      </main>
      {!immersive && (
        <nav
          aria-label="底部导航"
          className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-paper/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-md md:hidden"
        >
          <ul className={cn("grid", governanceVisible ? "grid-cols-6" : "grid-cols-5")}>
            {nav.map((item) => (
              <li key={item.href}>
                <TabLink
                  href={item.href}
                  label={item.label}
                  icon={item.icon}
                  active={isActive(pathname, item.href)}
                />
              </li>
            ))}
          </ul>
        </nav>
      )}
    </div>
  );
}
