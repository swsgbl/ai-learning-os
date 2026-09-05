"use client";

// M11-01 交卷确认：克制的 scale+fade 入场；可访问性优先——
// role=dialog + aria-modal、打开即聚焦标题、Esc/遮罩关闭、关闭后焦点回到触发钮，
// 提交中禁用按钮并显示进行中文案（提交语义仍由 ExamStudio.submit 掌握）。
import { useEffect, useRef, useState } from "react";
import { Send } from "lucide-react";
import { gsap, useMotion } from "@/lib/gsap";
import { MOTION } from "@/lib/motion";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

export function SubmitDialog({
  answered,
  total,
  submitting,
  onCancel,
  onConfirm,
}: {
  answered: number;
  total: number;
  submitting: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const [unanswered, setUnanswered] = useState(total - answered);

  useEffect(() => {
    setUnanswered(Math.max(0, total - answered));
  }, [answered, total]);

  useEffect(() => {
    returnFocusRef.current = document.activeElement as HTMLElement | null;
    cardRef.current?.querySelector<HTMLHeadingElement>("h2")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !submitting) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      returnFocusRef.current?.focus?.();
    };
  }, [onCancel, submitting]);

  // 入场：遮罩 fade + 卡片 rise（reduced-motion 直接呈现）
  useMotion(
    (reduced) => {
      const dialog = dialogRef.current;
      const card = cardRef.current;
      if (!dialog || !card) return;
      if (reduced) {
        gsap.set([dialog, card], { clearProps: "all" });
        return;
      }
      // 用 opacity 而非 autoAlpha：visibility:hidden 会把刚设置的焦点丢回 body
      gsap.fromTo(dialog, { opacity: 0 }, { opacity: 1, duration: MOTION.duration.fast, ease: MOTION.ease.out, clearProps: "opacity" });
      gsap.fromTo(
        card,
        { opacity: 0, y: MOTION.distance.rise, scale: 0.97 },
        { opacity: 1, y: 0, scale: 1, duration: MOTION.duration.base, ease: MOTION.ease.out, clearProps: "opacity,transform" },
      );
    },
    { scope: dialogRef },
  );

  return (
    <div
      ref={dialogRef}
      className="fixed inset-0 z-40 flex items-end justify-center bg-ink/45 p-4 backdrop-blur-[2px] sm:items-center"
      onClick={(event) => {
        if (event.target === event.currentTarget && !submitting) onCancel();
      }}
    >
      <Card
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="submit-dialog-title"
        className="w-full max-w-sm p-5 shadow-raised"
      >
        <h2 id="submit-dialog-title" tabIndex={-1} className="font-display text-xl outline-none">
          提交试卷？
        </h2>
        <p className="mt-2 text-sm text-muted">
          已作答 {answered}/{total} 题。
          {unanswered > 0 && ` 还有 ${unanswered} 题未作答，`}
          提交后进入审阅，不可再修改。
        </p>
        <div className="mt-4 flex gap-2">
          <Button variant="outline" className="flex-1" disabled={submitting} onClick={onCancel}>
            再看看
          </Button>
          <Button className="flex-1" disabled={submitting} onClick={onConfirm}>
            <Send aria-hidden="true" />
            {submitting ? "正在提交…" : "提交审阅"}
          </Button>
        </div>
      </Card>
    </div>
  );
}
