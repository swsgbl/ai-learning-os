"use client";

// M11-01 揭示进度条：挂载后从 0 过渡到目标值（CSS transform 过渡，
// prefers-reduced-motion 全局规则自动归零），数据可读性不受影响——
// aria-valuenow 从一开始就是真实值。
import { useEffect, useState } from "react";
import { Progress } from "@/components/ui/progress";

export function RevealProgress({ value, ...props }: React.ComponentProps<typeof Progress> & { value: number }) {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => setShown(value));
    return () => window.cancelAnimationFrame(frame);
  }, [value]);
  return <Progress value={shown} aria-valuenow={Math.round(value)} {...props} />;
}
