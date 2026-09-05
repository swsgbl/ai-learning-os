"use client";

// M11-01 治理 tabs：role=tablist/tab + aria-selected + 方向键导航，
// active 下划线 scaleX 指示（与主导航一致），保持信息密度（紧凑尺寸）。
import { useRef } from "react";
import type { LucideIcon } from "lucide-react";
import { gsap, useMotion } from "@/lib/gsap";
import { MOTION } from "@/lib/motion";
import { cn } from "@/lib/utils";

export type GovTab = { id: string; label: string; icon: LucideIcon };

export function GovTabs({
  tabs,
  active,
  onChange,
}: {
  tabs: GovTab[];
  active: string;
  onChange: (id: string) => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);

  // active 指示条：每 tab 自带 underline，激活时 scaleX 弹入（无测量、resize 安全）
  useMotion(
    (reduced) => {
      const root = rootRef.current;
      if (!root) return;
      const indicator = root.querySelector<HTMLElement>(`[data-tab="${active}"] [data-indicator]`);
      if (!indicator) return;
      if (reduced) {
        gsap.set(indicator, { clearProps: "transform" });
        return;
      }
      gsap.fromTo(
        indicator,
        { scaleX: 0.4 },
        {
          scaleX: 1,
          duration: MOTION.duration.base,
          ease: MOTION.ease.out,
          clearProps: "transform",
        },
      );
    },
    { scope: rootRef, dependencies: [active] },
  );

  const onKeyDown = (event: React.KeyboardEvent) => {
    const currentIndex = tabs.findIndex((tab) => tab.id === active);
    if (currentIndex < 0) return;
    let nextIndex = currentIndex;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
    else if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = tabs.length - 1;
    else return;
    event.preventDefault();
    onChange(tabs[nextIndex].id);
    rootRef.current
      ?.querySelectorAll<HTMLButtonElement>("[role='tab']")
      [nextIndex]?.focus();
  };

  return (
    <div
      ref={rootRef}
      role="tablist"
      aria-label="治理队列"
      className="-mx-1 flex gap-1 overflow-x-auto px-1 pb-1"
      onKeyDown={onKeyDown}
    >
      {tabs.map((tab) => {
        const selected = tab.id === active;
        const Icon = tab.icon;
        return (
          <button
            key={tab.id}
            data-tab={tab.id}
            type="button"
            role="tab"
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.id)}
            className={cn(
              "relative inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-lg px-3 text-xs whitespace-nowrap transition-colors duration-150 outline-offset-2 md:min-h-9",
              selected ? "bg-accent text-accent-fg" : "bg-surface-2 text-muted hover:text-ink",
            )}
          >
            <Icon className="size-3.5" aria-hidden="true" />
            {tab.label}
            <span
              data-indicator
              aria-hidden="true"
              className={cn(
                "absolute inset-x-2 bottom-0 h-0.5 origin-left rounded-full",
                selected ? "bg-accent-fg/70" : "hidden",
              )}
            />
          </button>
        );
      })}
    </div>
  );
}
