import { cn } from "@/lib/utils";

// M11-01 进度条：scaleX 揭示替代 width 动画（合成器友好，无布局重排）；
// 通过 style 变量 + CSS transition 实现，reduced-motion 全局规则会归零时长。
export function Progress({ value, className, ...props }: React.ComponentProps<"div"> & { value: number }) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div
      className={cn("h-2 w-full overflow-hidden rounded-full bg-surface-2", className)}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(clamped)}
      {...props}
    >
      <div
        className="h-full origin-left rounded-full bg-accent transition-transform duration-500 ease-[var(--ease-out)]"
        style={{ transform: `scaleX(${clamped / 100})` }}
      />
    </div>
  );
}
