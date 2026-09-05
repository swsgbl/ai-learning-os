"use client";

// M11-01 考场题目面板：题目切换的方向性 slide+fade（keyed 重放），
// 选项选择即时反馈（scale 脉冲 + 勾选图标），选中态以颜色/图标持续呈现
// ——动效只是反馈，作答语义仍在 ExamStudio 的 choose/saveAnswer 链路。
import { useEffect, useRef, useState } from "react";
import { Check } from "lucide-react";
import type { PublicQuestion } from "@/lib/types";
import { gsap, useGSAP, useMotion } from "@/lib/gsap";
import { MOTION } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

const TYPE_LABELS: Record<string, string> = {
  mcq: "选择",
  multiple_select: "多选",
  tf: "判断",
  true_false: "判断",
  short: "简答",
  short_answer: "简答",
  numeric: "数值",
  math: "数学",
  coding: "编程",
  essay: "写作",
};

// 非选项题（数值/数学/简答）：文本输入，走同一 append-only 保存链路
function AnswerInput({ value, onSave }: { value: string; onSave: (text: string) => void }) {
  const [draft, setDraft] = useState(value);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  return (
    <div className="mt-5 flex flex-col gap-2 sm:flex-row">
      <input
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && draft.trim()) onSave(draft.trim());
        }}
        placeholder="输入答案，如 3.14 或 2x"
        aria-label="文字作答"
        className="min-h-11 w-full rounded-lg bg-surface px-4 py-3 text-sm shadow-border outline-none placeholder:text-subtle focus:bg-surface-2"
      />
      <Button disabled={!draft.trim() || draft === value} onClick={() => onSave(draft.trim())}>
        保存
      </Button>
    </div>
  );
}

export function QuestionPanel({
  question,
  index,
  total,
  answer,
  direction,
  onChoose,
}: {
  question: PublicQuestion;
  index: number;
  total: number;
  answer?: string;
  direction: 1 | -1;
  onChoose: (key: string) => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);

  // 题目切换：方向性 slide+fade（prev 从左、next 从右）；reduced-motion 直接切换
  useMotion(
    (reduced) => {
      const root = rootRef.current;
      if (!root) return;
      if (reduced) {
        gsap.set(root, { clearProps: "all" });
        return;
      }
      const fromX = direction >= 0 ? 24 : -24;
      gsap.fromTo(
        root,
        { autoAlpha: 0, x: fromX },
        {
          autoAlpha: 1,
          x: 0,
          duration: MOTION.duration.base,
          ease: MOTION.ease.out,
          clearProps: "opacity,visibility,transform",
        },
      );
    },
    { scope: rootRef, dependencies: [question.id] },
  );

  // 事件驱动的一次性反馈动画必须包 contextSafe 才能随组件卸载清理
  const { contextSafe } = useGSAP({ scope: rootRef });
  const pulseOption = contextSafe((target: HTMLElement) => {
    gsap.fromTo(
      target,
      { scale: 0.98 },
      { scale: 1, duration: MOTION.duration.fast, ease: MOTION.ease.out, clearProps: "transform" },
    );
  });

  return (
    <div ref={rootRef} aria-live="polite">
      <Card className="p-5 sm:p-6">
        <p className="text-xs text-muted">
          第 {index + 1} 题 / 共 {total} 题 · {TYPE_LABELS[question.type] ?? "作答"}
        </p>
        <p className="mt-3 font-display text-xl leading-snug">{question.stem}</p>
        {question.options?.length ? (
          <ul className="mt-5 space-y-2">
            {question.options.map((option) => {
              const active = answer === option.key;
              return (
                <li key={option.key}>
                  <button
                    type="button"
                    aria-pressed={active}
                    onClick={(event) => {
                      pulseOption(event.currentTarget);
                      onChoose(option.key);
                    }}
                    className={cn(
                      "flex w-full items-start gap-3 rounded-lg px-4 py-3 text-left text-sm shadow-border transition-colors duration-150 outline-offset-2",
                      active
                        ? "bg-accent text-accent-fg"
                        : "bg-surface hover:bg-surface-2",
                    )}
                  >
                    <span className="flex size-5 shrink-0 items-center justify-center">
                      {active ? (
                        <Check className="size-4" aria-hidden="true" />
                      ) : (
                        <span className="text-sm font-medium">{option.key}</span>
                      )}
                    </span>
                    <span>{option.text}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <AnswerInput value={answer ?? ""} onSave={onChoose} />
        )}
      </Card>
    </div>
  );
}
