"use client";

// M11-01 交卷确认：克制的 scale+fade 入场；可访问性优先——
// role=dialog + aria-modal、打开即聚焦标题、Esc/遮罩关闭、关闭后焦点回到触发钮，
// 提交中禁用按钮并显示进行中文案（提交语义仍由 ExamStudio.submit 掌握）。
// PR#20 交互补齐：Tab/Shift+Tab 焦点圈定在对话框内（首/尾环绕 + 焦点逃逸拉回）、
// 打开期间锁定 body 滚动（含滚动条宽度补偿）并在卸载时恢复、提交中焦点回落标题
// 防止禁用按钮把焦点丢回 body。
import { useEffect, useRef, useState } from "react";
import { Send } from "lucide-react";
import { gsap, useMotion } from "@/lib/gsap";
import { MOTION } from "@/lib/motion";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

// 与 Tab 语义一致的可聚焦元素（disabled 控件不进入 Tab 序，与浏览器行为对齐）
const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function focusableIn(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => el.getClientRects().length > 0,
  );
}

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
  const submittingRef = useRef(submitting);
  const onCancelRef = useRef(onCancel);
  const [unanswered, setUnanswered] = useState(total - answered);

  useEffect(() => {
    setUnanswered(Math.max(0, total - answered));
  }, [answered, total]);

  // 最新回调/状态入 ref：键盘监听只注册一次，不随父组件重渲染（onCancel 每帧新引用）重挂
  useEffect(() => {
    submittingRef.current = submitting;
    onCancelRef.current = onCancel;
  });

  // 打开（挂载）：焦点移入标题 + Esc/Tab 键盘圈定 + 锁定背景滚动；卸载时全部还原。
  // 仅挂载时执行——submitting/onCancel 变化不重跑（旧实现会在提交中途把焦点拉回触发钮）。
  useEffect(() => {
    returnFocusRef.current = document.activeElement as HTMLElement | null;
    cardRef.current?.querySelector<HTMLHeadingElement>("h2")?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !submittingRef.current) {
        onCancelRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const scope = cardRef.current;
      if (!scope) return;
      const focusables = focusableIn(scope);
      if (focusables.length === 0) {
        // 提交中两个按钮均禁用：Tab 不出对话框，焦点保持在标题
        event.preventDefault();
        scope.querySelector<HTMLHeadingElement>("h2")?.focus();
        return;
      }
      const active = document.activeElement as HTMLElement | null;
      const index = active ? focusables.indexOf(active) : -1;
      // 边界：index -1 = 焦点在可聚焦序之外（标题 tabIndex=-1 或已逃逸出对话框）
      const wrapsBackward = index <= 0;
      const wrapsForward = index === -1 || index === focusables.length - 1;
      if (event.shiftKey && wrapsBackward) {
        event.preventDefault();
        focusables[focusables.length - 1].focus();
      } else if (!event.shiftKey && wrapsForward) {
        event.preventDefault();
        focusables[0].focus();
      }
    };

    // capture 阶段拦截：在浏览器默认移动焦点之前完成环绕/拉回
    window.addEventListener("keydown", onKeyDown, true);

    // 背景滚动锁定（overflow 传播到视口）+ 滚动条宽度补偿，避免锁定瞬间布局跳动
    const { body } = document;
    const previousOverflow = body.style.overflow;
    const previousPaddingRight = body.style.paddingRight;
    const scrollbar = window.innerWidth - document.documentElement.clientWidth;
    if (scrollbar > 0) body.style.paddingRight = `${scrollbar}px`;
    body.style.overflow = "hidden";

    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      body.style.overflow = previousOverflow;
      body.style.paddingRight = previousPaddingRight;
      returnFocusRef.current?.focus?.();
    };
  }, []);

  // 提交中：确认钮被禁用会把焦点丢回 body —— 回落到对话框标题，焦点始终留在语义容器内
  useEffect(() => {
    if (!submitting) return;
    const scope = cardRef.current;
    if (!scope) return;
    const active = document.activeElement;
    if (!active || !scope.contains(active)) {
      scope.querySelector<HTMLHeadingElement>("h2")?.focus();
    }
  }, [submitting]);

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
        aria-busy={submitting}
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
