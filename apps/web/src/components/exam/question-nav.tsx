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

  // M14-189: pulse 接收点击目标元素（事件回调里传 e.currentTarget），不再在
  // render 期闭包内读 rootRef.current（react-hooks/refs：render 期间不得访问
  // ref）。目标与原 querySelector(`[data-qnav="${index}"]`) 是同一按钮元素，
  // contextSafe 包装保留（动画仍登记进 useGSAP context，随卸载清理）。
  const pulse = contextSafe((el: HTMLElement, answered: boolean) => {
    if (!answered) return;
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
            onClick={(event) => {
              pulse(event.currentTarget, answered);
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
