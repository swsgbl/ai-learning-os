"use client";

// M11-01 试卷选择列表（考场 / 语音索引入口共用）：
// stagger 入场 + 等待骨架 / 空态 / 错误态。只读投影，不含考试语义。
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { PaperSummary } from "@/lib/types";
import { gsap, useMotion } from "@/lib/gsap";
import { MOTION, staggerFor } from "@/lib/motion";
import { ErrorState, EmptyState } from "@/components/states";
import { PaperCard } from "@/components/paper-card";

export function PaperPicker() {
  const rootRef = useRef<HTMLDivElement>(null);
  const [papers, setPapers] = useState<PaperSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setError(null);
    api
      .papers()
      .then(setPapers)
      .catch((cause: Error) => setError(cause.message));
  };

  useEffect(load, []);

  useMotion(
    (reduced) => {
      const root = rootRef.current;
      if (!root) return;
      const cards = root.querySelectorAll<HTMLElement>("[data-paper-card]");
      if (cards.length === 0) return;
      if (reduced) {
        gsap.set(cards, { clearProps: "all" });
        return;
      }
      gsap.fromTo(
        cards,
        { autoAlpha: 0, y: MOTION.distance.rise },
        {
          autoAlpha: 1,
          y: 0,
          duration: MOTION.duration.base,
          ease: MOTION.ease.out,
          stagger: staggerFor(cards.length),
          clearProps: "opacity,visibility,transform",
        },
      );
    },
    { scope: rootRef, dependencies: [papers] },
  );

  if (error) return <ErrorState title="试卷列表加载失败" detail={error} onRetry={load} />;

  if (!papers) {
    return (
      <div className="grid gap-3 sm:grid-cols-2" aria-label="正在加载试卷">
        {[0, 1, 2, 3].map((index) => (
          <div key={index} className="h-44 animate-pulse rounded-xl bg-surface-2/70" />
        ))}
      </div>
    );
  }

  if (papers.length === 0) {
    return <EmptyState title="题库还没有试卷" detail="请先在服务端导入试卷。" />;
  }

  return (
    <div ref={rootRef} className="grid gap-3 sm:grid-cols-2">
      {papers.map((paper) => (
        <div key={paper.id} data-paper-card={paper.id}>
          <PaperCard paper={paper} compact />
        </div>
      ))}
    </div>
  );
}
