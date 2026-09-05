"use client";

// M11-01 语音波形：只在「聆听中」出现的状态指示（不是装饰循环动画）——
// 停止即静止消失。reduced-motion 下渲染静态高度条 + 文字状态，信息不丢。
import { useRef } from "react";
import { gsap, useMotion } from "@/lib/gsap";
import { cn } from "@/lib/utils";

const BARS = [0.45, 0.75, 1, 0.7, 0.4];

export function Waveform({ active, className }: { active: boolean; className?: string }) {
  const rootRef = useRef<HTMLDivElement>(null);

  useMotion(
    (reduced) => {
      const root = rootRef.current;
      if (!root) return;
      const bars = root.querySelectorAll<HTMLElement>("[data-bar]");
      if (!active || reduced) return;
      // 仅聆听期间运行的轻量状态动画：每根条围绕自身基线呼吸
      const tweens = Array.from(bars).map((bar, index) =>
        gsap.to(bar, {
          scaleY: 0.35 + ((index * 37) % 60) / 100,
          duration: 0.4 + index * 0.07,
          repeat: -1,
          yoyo: true,
          ease: "sine.inOut",
        }),
      );
      return () => {
        tweens.forEach((tween) => tween.kill());
        gsap.set(bars, { clearProps: "transform" });
      };
    },
    { scope: rootRef, dependencies: [active] },
  );

  if (!active) return null;

  return (
    <div
      ref={rootRef}
      className={cn("flex h-5 items-center gap-1", className)}
      role="status"
      aria-label="正在聆听"
    >
      {BARS.map((height, index) => (
        <span
          key={index}
          data-bar
          aria-hidden="true"
          className="w-1 origin-center rounded-full bg-accent"
          style={{ height: `${height * 100}%` }}
        />
      ))}
      <span className="ml-2 text-xs text-accent-strong">聆听中…</span>
    </div>
  );
}
