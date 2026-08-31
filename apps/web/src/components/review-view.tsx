"use client";

import { useState } from "react";
import { Check, ChevronDown, X } from "lucide-react";
import { answerLabel, optionLabel } from "@/lib/parse-answer";
import type { ExamReport, Submission } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "./ui/badge";
import { Card } from "./ui/card";
import { Progress } from "./ui/progress";

const TABS = [
  { id: "concept", label: "知识点" },
  { id: "method", label: "正确思路" },
  { id: "mistake", label: "错因" },
  { id: "variant", label: "变式" },
] as const;

export function ReviewView({ submission, report }: { submission: Submission; report?: ExamReport | null }) {
  const wrong = submission.items.filter((item) => item.correct === false);
  const reviewing = submission.items.filter((item) => item.correct === null);
  const reportItems = report ? new Map(report.items.map((item) => [item.question_id, item])) : null;

  return (
    <div className="space-y-6">
      <Card className="p-6">
        <p className="text-xs tracking-wide text-muted uppercase">本卷评分</p>
        <div className="mt-2 flex items-end justify-between gap-4">
          <div>
            <p className="font-display text-5xl font-medium leading-none tabular-nums">
              {submission.score}
              <span className="ml-1 text-lg text-muted">分</span>
            </p>
            <p className="mt-2 text-sm text-muted">
              {submission.paper_title} · {submission.correct_count}/{submission.total_count} 题正确 ·{" "}
              {Math.floor(submission.duration_seconds / 60)} 分 {submission.duration_seconds % 60} 秒
            </p>
            {report && (
              <p className="mt-1 text-xs text-muted tabular-nums">
                原始分 {report.score_earned}/{report.score_max} 分
              </p>
            )}
          </div>
          <Badge tone={submission.score >= 80 ? "good" : submission.score >= 60 ? "accent" : "bad"}>
            {submission.score >= 80 ? "掌握良好" : submission.score >= 60 ? "尚可巩固" : "需要回炉"}
          </Badge>
        </div>
        <Progress className="mt-5" value={submission.score} />
      </Card>

      {report && report.concepts.length > 0 && (
        <section>
          <h2 className="font-display text-xl">概念掌握</h2>
          <p className="mt-1 text-xs text-muted">按题目关联概念聚合正确率；待复核的题不计入正确率分母。</p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {report.concepts.map((concept) => (
              <Card key={concept.concept} className="p-4">
                <div className="flex items-center justify-between gap-3">
                  <p className="truncate text-sm">{concept.concept}</p>
                  <span className="shrink-0 text-xs tabular-nums text-muted">
                    {concept.ratio === null ? "待复核" : `${Math.round(concept.ratio * 100)}%`}
                  </span>
                </div>
                <Progress className="mt-3" value={(concept.ratio ?? 0) * 100} />
                <p className="mt-2 text-xs text-muted tabular-nums">
                  {concept.correct}/{concept.total} 题正确
                  {concept.reviewed > 0 && <span className="ml-2">{concept.reviewed} 题待复核</span>}
                </p>
              </Card>
            ))}
          </div>
        </section>
      )}

      {wrong.length > 0 && (
        <section>
          <h2 className="font-display text-xl">错题 {wrong.length}</h2>
          <div className="mt-4 space-y-3">
            {wrong.map((item) => (
              <WrongCard key={item.question_id} submission={submission} questionId={item.question_id} />
            ))}
          </div>
        </section>
      )}

      {report && report.remediation_tasks.length > 0 && (
        <section>
          <h2 className="font-display text-xl">补救任务 {report.remediation_tasks.length}</h2>
          <div className="mt-4 space-y-2">
            {report.remediation_tasks.map((task, index) => (
              <Card key={`${task.question_id}-${task.kind}-${index}`} className="p-4">
                <div className="flex items-start gap-3">
                  <Badge tone={task.kind === "review_concept" ? "accent" : "good"}>
                    {task.kind === "review_concept" ? "复习概念" : "变式练习"}
                  </Badge>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm">{task.title}</p>
                    {task.kind === "variant_practice" && (
                      <p className="mt-1 text-xs leading-relaxed text-muted">{task.detail}</p>
                    )}
                  </div>
                </div>
              </Card>
            ))}
          </div>
        </section>
      )}

      {reviewing.length > 0 && (
        <section>
          <h2 className="font-display text-xl">待复核 {reviewing.length}</h2>
          <p className="mt-1 text-xs text-muted">判分不确定（如数学等价表达式、无法解析的数值），已进入人工复核，暂不计入得分。</p>
          <div className="mt-4 space-y-2">
            {reviewing.map((item) => {
              const question = submission.questions.find((candidate) => candidate.id === item.question_id);
              if (!question) return null;
              return (
                <Card key={item.question_id} className="p-4">
                  <p className="text-sm leading-relaxed">
                    <span className="mr-2 rounded bg-surface-2 px-1.5 py-0.5 text-xs text-muted">复核中</span>
                    {question.stem}
                  </p>
                  <p className="mt-2 text-xs text-muted">
                    作答 {optionLabel(question, item.given || "（未作答）")}
                    <span className="mx-2">·</span>
                    答案 {answerLabel(question, item.expected)}
                  </p>
                </Card>
              );
            })}
          </div>
        </section>
      )}

      <section>
        <h2 className="font-display text-xl">全部题目</h2>
        <div className="mt-4 space-y-2">
          {submission.items.map((item, index) => {
            const question = submission.questions.find((candidate) => candidate.id === item.question_id);
            if (!question) return null;
            return (
              <Card key={item.question_id} className="p-4">
                <div className="flex items-start gap-3">
                  <span
                    className={cn(
                      "mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full",
                      item.correct === null
                        ? "bg-surface-2 text-muted"
                        : item.correct
                          ? "bg-good-soft text-good"
                          : "bg-bad-soft text-bad",
                    )}
                  >
                    {item.correct === null ? "?" : item.correct ? <Check className="size-3.5" /> : <X className="size-3.5" />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm leading-relaxed">
                      <span className="mr-2 tabular-nums text-muted">{index + 1}.</span>
                      {question.stem}
                    </p>
                    <p className="mt-2 text-xs text-muted">
                      作答 {optionLabel(question, item.given || "（未作答）")}
                      <span className="mx-2">·</span>
                      答案 {answerLabel(question, item.expected)}
                    </p>
                    {item.rubric && <RubricDetailList rubric={item.rubric} />}
                  </div>
                  {reportItems && (
                    <ScoreBadge reportItem={reportItems.get(item.question_id)} />
                  )}
                </div>
              </Card>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function RubricDetailList({ rubric }: { rubric: NonNullable<Submission["items"][number]["rubric"]> }) {
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {rubric.criteria.map((criterion) => (
        <span
          key={criterion.point}
          className={cn(
            "rounded px-1.5 py-0.5 text-xs",
            criterion.achieved === null
              ? "bg-surface-2 text-muted"
              : criterion.achieved
                ? "bg-good-soft text-good"
                : "bg-bad-soft text-bad",
          )}
        >
          {criterion.achieved === null ? "?" : criterion.achieved ? "✓" : "✗"} {criterion.point}
        </span>
      ))}
      <span className="rounded bg-surface-2 px-1.5 py-0.5 text-xs text-muted">
        评分点 {rubric.criteria.filter((c) => c.achieved).length}/{rubric.criteria.length} ·{" "}
        {rubric.judge_model}
      </span>
    </div>
  );
}

function ScoreBadge({ reportItem }: { reportItem: ExamReport["items"][number] | undefined }) {
  if (!reportItem) return null;
  return (
    <span
      className={cn(
        "shrink-0 rounded-full px-2 py-0.5 text-xs tabular-nums",
        reportItem.score === null
          ? "bg-surface-2 text-muted"
          : reportItem.score >= reportItem.max_score
            ? "bg-good-soft text-good"
            : reportItem.score > 0
              ? "bg-surface-2 text-warn"
              : "bg-bad-soft text-bad",
      )}
    >
      {reportItem.score === null ? "待复核" : `${reportItem.score}/${reportItem.max_score} 分`}
    </span>
  );
}

function WrongCard({ submission, questionId }: { submission: Submission; questionId: string }) {
  const [open, setOpen] = useState(true);
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("concept");
  const question = submission.questions.find((candidate) => candidate.id === questionId);
  const item = submission.items.find((candidate) => candidate.question_id === questionId);
  if (!question || !item) return null;

  return (
    <Card className="overflow-hidden">
      <button type="button" onClick={() => setOpen((value) => !value)} className="flex w-full items-start justify-between gap-3 p-4 text-left">
        <div>
          <p className="text-sm leading-relaxed">{question.stem}</p>
          <p className="mt-2 text-xs text-muted">
            你选了 {optionLabel(question, item.given || "（未作答）")} · 正确是 {answerLabel(question, item.expected)}
          </p>
        </div>
        <ChevronDown className={cn("mt-1 size-4 shrink-0 text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="border-t border-border px-4 pb-4">
          <div className="flex gap-1 overflow-x-auto py-3">
            {TABS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setTab(item.id)}
                className={cn(
                  "rounded-full px-3 py-1.5 text-xs",
                  tab === item.id ? "bg-accent text-accent-fg" : "bg-surface-2 text-muted",
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
          <p className="text-sm leading-relaxed">{item.angles[tab] || item.explanation}</p>
          {question.knowledge.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {question.knowledge.map((knowledge) => (
                <Badge key={knowledge}>{knowledge}</Badge>
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
