"use client";

// M11-01 数字滚动：仅对纯数字值做短促 count-up；非数字（如「登录后可见」）
// 直接渲染不动。最终值恒等于数据值，动效不改变可读性（tabular-nums 防抖动）。
import { useRef } from "react";
import { gsap, useMotion } from "@/lib/gsap";
import { MOTION } from "@/lib/motion";
import { cn } from "@/lib/utils";

export function CountUp({
  value,
  className,
  duration = MOTION.duration.slow,
}: {
  value: string;
  className?: string;
  duration?: number;
}) {
  const ref = useRef<HTMLSpanElement>(null);

  useMotion(
    (reduced) => {
      const el = ref.current;
      if (!el) return;
      const target = Number(value);
      if (!Number.isFinite(target) || !/^\d+$/.test(value.trim())) {
        el.textContent = value;
        return;
      }
      if (reduced) {
        el.textContent = value;
        return;
      }
      const state = { v: 0 };
      gsap.to(state, {
        v: target,
        duration,
        ease: MOTION.ease.out,
        onUpdate: () => {
          el.textContent = String(Math.round(state.v));
        },
        onComplete: () => {
          el.textContent = value;
        },
      });
    },
    { scope: ref, dependencies: [value] },
  );

  return (
    <span ref={ref} className={cn("tabular-nums", className)}>
      {value}
    </span>
  );
}
