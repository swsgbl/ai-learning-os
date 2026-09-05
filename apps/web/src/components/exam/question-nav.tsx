"use client";

// M11-01 题号导航：当前/已答/未答三态（形状+颜色双通道，已答带勾），
// 移动端触控目标 44px；刚作答的题号做一次 scale 脉冲反馈。
import { useRef } from "react";
import { Check } from "lucide-react";
import { gsap, useGSAP } from "@/lib/gsap";
import { MOTION } from "@/lib/motion";
import { cn } from "@/lib/utils";

export function QuestionNav({
  questions,
  answers,
  current,
  onSelect,
}: {
  questions: Array<{ id: string }>;
  answers: Record<string, string>;
  current: number;
  onSelect: (index: number) => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const { contextSafe } = useGSAP({ scope: rootRef });

  const pulse = contextSafe((index: number, answered: boolean) => {
    const el = rootRef.current?.querySelector<HTMLElement>(`[data-qnav="${index}"]`);
    if (!el || !answered) return;
    gsap.fromTo(
      el,
      { scale: 0.85 },
      { scale: 1, duration: MOTION.duration.fast, ease: MOTION.ease.out, clearProps: "transform" },
    );
  });

  return (
    <div
      ref={rootRef}
      className="flex flex-wrap gap-1.5"
      role="group"
      aria-label="题号导航"
    >
      {questions.map((item, itemIndex) => {
        const answered = Boolean(answers[item.id]);
        const active = itemIndex === current;
        return (
          <button
            key={item.id}
            type="button"
            data-qnav={itemIndex}
            aria-current={active ? "step" : undefined}
            aria-label={`第 ${itemIndex + 1} 题${answered ? "（已作答）" : ""}`}
            onClick={() => {
              pulse(itemIndex, answered);
              onSelect(itemIndex);
            }}
            className={cn(
              "flex size-11 items-center justify-center rounded-md text-xs tabular-nums shadow-border transition-colors duration-150 outline-offset-2 md:size-9",
              active && "bg-accent text-accent-fg",
              !active && answered && "bg-accent-soft text-accent-strong",
              !active && !answered && "bg-surface text-muted hover:bg-surface-2",
            )}
          >
            {answered && !active ? <Check className="size-3.5" aria-hidden="true" /> : itemIndex + 1}
          </button>
        );
      })}
    </div>
  );
}
