"use client";

// M11-01 分数揭示：数字 count-up + 进度条从 0 揭示到真实值；
// reduced-motion 下数字直接显示（信息等价，动效不承载语义）。
import type { ExamReport, Submission } from "@/lib/types";
import { CountUp } from "@/components/motion/count-up";
import { RevealProgress } from "@/components/motion/reveal-progress";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

export function ScoreHero({ submission, report }: { submission: Submission; report?: ExamReport | null }) {
  return (
    <div data-animate="block">
      <Card className="p-6">
        <p className="text-xs uppercase tracking-wide text-muted">本卷评分</p>
        <div className="mt-2 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="font-display text-5xl font-medium leading-none tabular-nums">
              <CountUp value={String(submission.score)} duration={0.8} />
              <span className="ml-1 text-lg text-muted">分</span>
            </p>
            <p className="mt-2 text-sm text-muted">
              {submission.paper_title} · {submission.correct_count}/{submission.total_count} 题正确 ·{" "}
              {Math.floor(submission.duration_seconds / 60)} 分 {submission.duration_seconds % 60} 秒
            </p>
            {report && (
              <p className="mt-1 text-xs tabular-nums text-muted">
                原始分 {report.score_earned}/{report.score_max} 分
              </p>
            )}
          </div>
          <Badge tone={submission.score >= 80 ? "good" : submission.score >= 60 ? "accent" : "bad"}>
            {submission.score >= 80 ? "掌握良好" : submission.score >= 60 ? "尚可巩固" : "需要回炉"}
          </Badge>
        </div>
        <RevealProgress className="mt-5" value={submission.score} aria-label="得分" />
      </Card>
    </div>
  );
}
