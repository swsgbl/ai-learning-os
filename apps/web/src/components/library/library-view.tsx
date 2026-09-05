"use client";

// M11-01 学习库：搜索 + 难度过滤 + Flip 布局过渡（过滤重排时卡片平滑移动）。
// 等待（骨架屏）/ 空态 / 错误态三态清晰；Flip 已在 lib/gsap 注册，
// reduced-motion 下直接跳过动画，列表即时重排。
import { useEffect, useMemo, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { api } from "@/lib/api";
import type { PaperSummary } from "@/lib/types";
import { Flip, gsap, useMotion } from "@/lib/gsap";
import { MOTION } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { PaperCard } from "@/components/paper-card";
import { ErrorState, EmptyState } from "@/components/states";
import { Input } from "@/components/ui/input";

const DIFFICULTY_LABELS: Record<string, string> = {
  intro: "入门",
  core: "核心",
  advanced: "进阶",
};

type Filter = "all" | "intro" | "core" | "advanced";

export function LibraryView() {
  const rootRef = useRef<HTMLDivElement>(null);
  const flipStateRef = useRef<Flip.FlipState | null>(null);
  const prevIdsRef = useRef<Set<string>>(new Set());
  const [papers, setPapers] = useState<PaperSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");

  const load = () => {
    setError(null);
    api
      .papers()
      .then(setPapers)
      .catch((cause: Error) => setError(cause.message));
  };

  useEffect(load, []);

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return papers?.filter((paper) => {
      const matchDifficulty = filter === "all" || paper.difficulty === filter;
      if (!matchDifficulty) return false;
      if (!keyword) return true;
      return [
        paper.title,
        paper.subtitle,
        paper.source,
        paper.university ?? "",
        paper.subject,
        ...paper.tags,
      ].some((value) => value.toLowerCase().includes(keyword));
    });
  }, [papers, query, filter]);

  // Flip 布局过渡：依赖 query/filter/papers 变化（DOM 提交后）——
  // 先从上一帧状态补间到新布局，再捕获新状态；新进卡片单独 fade+rise 入场。
  useMotion(
    (reduced) => {
      const root = rootRef.current;
      if (!root || !filtered) return;
      const cards = root.querySelectorAll<HTMLElement>("[data-paper-card]");
      if (!reduced && flipStateRef.current) {
        Flip.from(flipStateRef.current, {
          duration: MOTION.duration.base,
          ease: MOTION.ease.inOut,
          scale: true,
          nested: true,
        });
      }
      if (!reduced) {
        const nextIds = new Set(filtered.map((paper) => paper.id));
        const entering = Array.from(cards).filter(
          (card) => !prevIdsRef.current.has(card.dataset.paperCard ?? ""),
        );
        if (entering.length > 0) {
          gsap.fromTo(
            entering,
            { autoAlpha: 0, y: MOTION.distance.rise, scale: 0.97 },
            {
              autoAlpha: 1,
              y: 0,
              scale: 1,
              duration: MOTION.duration.base,
              ease: MOTION.ease.out,
              stagger: 0.04,
              clearProps: "opacity,visibility,transform",
              overwrite: "auto",
            },
          );
        }
        prevIdsRef.current = nextIds;
      }
      flipStateRef.current = Flip.getState(cards);
    },
    { scope: rootRef, dependencies: [query, filter, papers] },
  );

  const hasActiveFilter = query.trim() !== "" || filter !== "all";

  return (
    <div ref={rootRef} className="space-y-8">
      <div data-animate="block">
        <p className="text-xs text-muted">模式三</p>
        <h1 className="mt-1 font-display text-3xl">学习库</h1>
        <p className="mt-2 max-w-xl text-sm text-muted">当前是服务端来源目录；真实多源检索、License Gate 和 Evidence 将在 M1 接入。</p>
      </div>

      <div data-animate="block" className="space-y-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-subtle" aria-hidden="true" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索课程、学科、学校或标签"
            className="pl-9"
            aria-label="搜索试卷"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="按难度过滤">
          {(["all", "intro", "core", "advanced"] as const).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setFilter(key)}
              aria-pressed={filter === key}
              className={cn(
                "min-h-11 rounded-full px-4 text-xs transition-colors duration-150 md:min-h-9 md:px-3",
                filter === key
                  ? "bg-accent text-accent-fg"
                  : "bg-surface-2 text-muted hover:text-ink",
              )}
            >
              {key === "all" ? "全部难度" : DIFFICULTY_LABELS[key]}
            </button>
          ))}
          {hasActiveFilter && (
            <button
              type="button"
              onClick={() => {
                setQuery("");
                setFilter("all");
              }}
              className="inline-flex min-h-11 items-center gap-1 rounded-full px-3 text-xs text-muted transition-colors duration-150 hover:text-ink md:min-h-9"
            >
              <X className="size-3.5" aria-hidden="true" /> 清除条件
            </button>
          )}
        </div>
      </div>

      {error && (
        <ErrorState title="试卷列表加载失败" detail={error} onRetry={load} />
      )}

      {!error && !papers && (
        <div className="grid gap-3 sm:grid-cols-2" aria-label="正在加载试卷">
          {[0, 1, 2, 3].map((index) => (
            <div key={index} className="h-44 animate-pulse rounded-xl bg-surface-2/70" />
          ))}
        </div>
      )}

      {papers && (
        <section>
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="font-display text-xl">可练试卷</h2>
            <p className="text-xs tabular-nums text-muted" aria-live="polite">
              {filtered?.length ?? 0} / {papers.length} 份
            </p>
          </div>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {filtered?.map((paper) => (
              <div key={paper.id} data-paper-card={paper.id}>
                <PaperCard paper={paper} compact />
              </div>
            ))}
          </div>
          {filtered?.length === 0 && (
            <div className="mt-3">
              <EmptyState
                title="没有匹配的试卷"
                detail="换个关键词，或清除难度条件再试。"
                action={
                  hasActiveFilter ? (
                    <button
                      type="button"
                      onClick={() => {
                        setQuery("");
                        setFilter("all");
                      }}
                      className="text-sm text-accent-strong underline underline-offset-4"
                    >
                      清除搜索与过滤
                    </button>
                  ) : undefined
                }
              />
            </div>
          )}
        </section>
      )}
    </div>
  );
}
