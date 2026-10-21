"use client";

// M15-024 学习模式（Web）：章节学习 → 逐章读完 → 考场模拟（与 App 同口径）。
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import type { StudyView } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ErrorState, LoadingState } from "@/components/states";

export default function StudyPage() {
  const params = useParams<{ paperId: string }>();
  const [view, setView] = useState<StudyView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (!params.paperId) return;
    api.paperStudy(params.paperId).then(setView).catch((cause: ApiError) => setError(cause.message));
  }, [params.paperId]);

  if (error) return <ErrorState title="学习内容加载失败" detail={error} />;
  if (!view) return <LoadingState />;

  const chapter = view.chapters[index];
  const last = index >= view.chapters.length - 1;

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
      <div>
        <p className="text-xs text-muted">学习模式</p>
        <h1 className="mt-1 font-display text-3xl">{view.paper_title}</h1>
        <p className="mt-2 text-sm text-muted">学习目标：{view.goal} · {view.chapter_count} 章</p>
      </div>
      <Card className="space-y-3 p-6">
        <div className="flex items-baseline justify-between">
          <h2 className="font-display text-xl">第{chapter.chapter_no}章 · {chapter.title}</h2>
          <span className="text-xs text-muted">难度 {chapter.difficulty}/5 · 进度 {index + 1}/{view.chapters.length}</span>
        </div>
        {chapter.objectives && (
          <div>
            <p className="text-sm font-medium">学习目标</p>
            <p className="mt-1 text-sm leading-relaxed">{chapter.objectives}</p>
          </div>
        )}
        {chapter.assessment_stem && (
          <div>
            <p className="text-sm font-medium">练一练</p>
            <p className="mt-1 text-sm leading-relaxed">{chapter.assessment_stem}</p>
          </div>
        )}
      </Card>
      <div className="flex gap-2">
        {index > 0 && <Button variant="outline" onClick={() => setIndex(index - 1)}>上一章</Button>}
        {last ? (
          <Button asChild><Link href={`/exam/${view.paper_id}`}>学完了，去考试</Link></Button>
        ) : (
          <Button onClick={() => setIndex(index + 1)}>下一章</Button>
        )}
      </div>
    </div>
  );
}
