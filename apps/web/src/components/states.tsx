"use client";

// M11-01 全局等待 / 空 / 错误态：图标 + 文案双通道（不只靠颜色），
// 入场只有一次克制的 fade+rise；reduced-motion 下完全静态。
import { useRef } from "react";
import { AlertCircle, Inbox, LoaderCircle } from "lucide-react";
import { gsap, useMotion } from "@/lib/gsap";
import { MOTION } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { Button } from "./ui/button";
import { Card } from "./ui/card";

function StateCard({
  icon,
  tone,
  title,
  detail,
  action,
}: {
  icon: React.ReactNode;
  tone: "loading" | "empty" | "error";
  title: string;
  detail?: string;
  action?: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useMotion(
    (reduced) => {
      if (reduced || !ref.current) return;
      gsap.fromTo(
        ref.current,
        { autoAlpha: 0, y: MOTION.distance.rise },
        {
          autoAlpha: 1,
          y: 0,
          duration: MOTION.duration.base,
          ease: MOTION.ease.out,
          clearProps: "opacity,visibility,transform",
        },
      );
    },
    { scope: ref, dependencies: [title, detail] },
  );

  return (
    <div ref={ref}>
      <Card
        className={cn(
          "flex flex-col items-start gap-3 p-5",
          tone === "error" && "shadow-[0_0_0_1px_rgba(162,61,50,0.35)]",
        )}
      >
        <span
          className={cn(
            "flex size-9 items-center justify-center rounded-lg",
            tone === "loading" && "bg-surface-2 text-muted",
            tone === "empty" && "bg-surface-2 text-muted",
            tone === "error" && "bg-bad-soft text-bad",
          )}
          aria-hidden="true"
        >
          {icon}
        </span>
        <div>
          <p className="text-sm font-medium">{title}</p>
          {detail && <p className="mt-1 text-xs leading-relaxed text-muted">{detail}</p>}
        </div>
        {action}
      </Card>
    </div>
  );
}

export function LoadingState({ title = "正在读取", detail }: { title?: string; detail?: string }) {
  return (
    <StateCard
      tone="loading"
      icon={<LoaderCircle className="size-4 animate-spin" aria-hidden="true" />}
      title={title}
      detail={detail}
    />
  );
}

export function EmptyState({
  title,
  detail,
  action,
}: {
  title: string;
  detail?: string;
  action?: React.ReactNode;
}) {
  return <StateCard tone="empty" icon={<Inbox className="size-4" aria-hidden="true" />} title={title} detail={detail} action={action} />;
}

export function ErrorState({
  title,
  detail,
  onRetry,
}: {
  title: string;
  detail?: string;
  onRetry?: () => void;
}) {
  return (
    <StateCard
      tone="error"
      icon={<AlertCircle className="size-4" aria-hidden="true" />}
      title={title}
      detail={detail}
      action={
        onRetry ? (
          <Button variant="outline" size="sm" onClick={onRetry}>
            重试
          </Button>
        ) : undefined
      }
    />
  );
}
