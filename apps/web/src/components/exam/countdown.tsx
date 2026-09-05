"use client";

// M11-01 考场倒计时：纯展示组件。时间真相源是服务端 server_end_at（父组件
// 每秒重算 remaining），这里只做视觉——低于 60s 切换警示色并做一次脉冲，
// reduced-motion 只变色不脉冲。动画绝不参与计时/交卷逻辑。
import { useEffect, useRef } from "react";
import { AlarmClock } from "lucide-react";
import { gsap, useMotion } from "@/lib/gsap";
import { MOTION } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { formatClock } from "@/lib/utils";

export function Countdown({ remaining }: { remaining: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const wasLow = useRef(remaining < 60);

  useEffect(() => {
    wasLow.current = remaining < 60;
  }, [remaining]);

  // 从充裕切到紧张（<60s）的一次性脉冲提醒；持续循环脉冲会干扰作答，不做
  useMotion(
    (reduced) => {
      const el = ref.current;
      if (!el) return;
      const low = remaining < 60;
      if (low && !wasLow.current && !reduced) {
        gsap.fromTo(
          el,
          { scale: 1 },
          {
            scale: 1.06,
            duration: MOTION.duration.fast,
            yoyo: true,
            repeat: 1,
            ease: MOTION.ease.inOut,
            clearProps: "transform",
          },
        );
      }
    },
    { scope: ref, dependencies: [remaining < 60] },
  );

  const low = remaining < 60;
  return (
    <div
      ref={ref}
      role="timer"
      aria-live="off"
      aria-label={`剩余 ${formatClock(remaining)}`}
      className={cn(
        "flex items-center gap-2 rounded-lg px-3 py-2 shadow-border transition-colors duration-300",
        low ? "bg-bad-soft text-bad" : "bg-surface text-ink",
      )}
    >
      <AlarmClock className="size-4" aria-hidden="true" />
      <div>
        <p className="text-[10px] text-muted">剩余</p>
        <p className={cn("font-mono text-lg leading-none tabular-nums", low && "text-bad")}>
          {formatClock(remaining)}
        </p>
      </div>
    </div>
  );
}
