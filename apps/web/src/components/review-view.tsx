"use client";

// M11-01 审阅：分数揭示（ScoreHero count-up + 进度揭示）、概念掌握卡、
// 错题展开（aria-expanded + 内容 fade/rise，容器高度自然流动不遮挡）、
// 补救任务。全部为服务端已定结果的只读呈现。
import { useRef, useState } from "react";
import { Check, ChevronDown, X } from "lucide-react";
import { answerLabel, optionLabel } from "@/lib/parse-answer";
import type { ExamReport, Submission } from "@/lib/types";
import { cn } from "@/lib/utils";
import { gsap, useMotion } from "@/lib/gsap";
import { MOTION } from "@/lib/motion";
import { Badge } from "./ui/badge";
import { Card } from "./ui/card";
import { RevealProgress } from "@/components/motion/reveal-progress";
import { ScoreHero } from "@/components/review/score-hero";

const TABS = [
  { id: "concept", label: "知识点" },
  { id: "method", label: "正确思路" },
  { id: "mistake", label: "错因" },
  { id: "variant", label: "变式" },
] as const;

export function ReviewView({ submission, report }: { submission: Submission; report?: ExamReport | null }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const wrong = submission.items.filter((item) => item.correct === false);
  const reviewing = submission.items.filter((item) => item.correct === null);
  const reportItems = report ? new Map(report.items.map((item) => [item.question_id, item])) : null;

  // 区块编排：概念卡 / 错题卡 / 补救任务分批 stagger 入场
  useMotion(
    (reduced) => {
      const root = rootRef.current;
      if (!root) return;
      const groups = root.querySelectorAll<HTMLElement>("[data-review-group]");
      if (groups.length === 0) return;
      if (reduced) {
        gsap.set(groups, { clearProps: "all" });
        return;
      }
      gsap.fromTo(
        groups,
        { autoAlpha: 0, y: MOTION.distance.rise },
        {
          autoAlpha: 1,
          y: 0,
          duration: MOTION.duration.base,
          ease: MOTION.ease.out,
          stagger: 0.08,
          clearProps: "opacity,visibility,transform",
        },
      );
    },
    { scope: rootRef, dependencies: [submission.exam_id] },
  );

  return (
    <div ref={rootRef} className="space-y-6">
      <ScoreHero submission={submission} report={report} />

      {report && report.concepts.length > 0 && (
        <section>
          <h2 className="font-display text-xl">概念掌握</h2>
          <p className="mt-1 text-xs text-muted">按题目关联概念聚合正确率；待复核的题不计入正确率分母。</p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {report.concepts.map((concept) => (
              <Card key={concept.concept} data-review-group className="p-4">
                <div className="flex items-center justify-between gap-3">
                  <p className="truncate text-sm">{concept.concept}</p>
                  <span className="shrink-0 text-xs tabular-nums text-muted">
                    {concept.ratio === null ? "待复核" : `${Math.round(concept.ratio * 100)}%`}
                  </span>
                </div>
                <RevealProgress className="mt-3" value={(concept.ratio ?? 0) * 100} aria-label={`${concept.concept} 正确率`} />
                <p className="mt-2 text-xs tabular-nums text-muted">
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
              <WrongCard
                key={item.question_id}
                submission={submission}
                questionId={item.question_id}
              />
            ))}
          </div>
        </section>
      )}

      {report && report.remediation_tasks.length > 0 && (
        <section>
          <h2 className="font-display text-xl">补救任务 {report.remediation_tasks.length}</h2>
          <div className="mt-4 space-y-2">
            {report.remediation_tasks.map((task, index) => (
              <Card key={`${task.question_id}-${task.kind}-${index}`} data-review-group className="p-4">
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
                    aria-hidden="true"
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
                  {reportItems && <ScoreBadge reportItem={reportItems.get(item.question_id)} />}
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
              ? "bg-warn-soft text-warn"
              : "bg-bad-soft text-bad",
      )}
    >
      {reportItem.score === null ? "待复核" : `${reportItem.score}/${reportItem.max_score} 分`}
    </span>
  );
}

// 错题卡：展开/收起用 aria-expanded + 内容 fade/rise；展开内容常驻 DOM 树
// 的自然流（不裁剪、不遮挡），tab 切换即时反馈。
function WrongCard({ submission, questionId }: { submission: Submission; questionId: string }) {
  const [open, setOpen] = useState(true);
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("concept");
  const contentRef = useRef<HTMLDivElement>(null);
  const question = submission.questions.find((candidate) => candidate.id === questionId);
  const item = submission.items.find((candidate) => candidate.question_id === questionId);
  if (!question || !item) return null;

  return (
    <Card data-review-group className="overflow-hidden">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-start justify-between gap-3 p-4 text-left outline-offset-[-4px]"
      >
        <div className="min-w-0">
          <p className="text-sm leading-relaxed">{question.stem}</p>
          <p className="mt-2 text-xs text-muted">
            你选了 {optionLabel(question, item.given || "（未作答）")} · 正确是 {answerLabel(question, item.expected)}
          </p>
        </div>
        <ChevronDown
          className={cn("mt-1 size-4 shrink-0 text-muted transition-transform duration-200 ease-[var(--ease-out)]", open && "rotate-180")}
          aria-hidden="true"
        />
      </button>
      {open && (
        <div ref={contentRef} className="border-t border-border px-4 pb-4">
          <div className="flex gap-1 overflow-x-auto py-3" role="tablist" aria-label="错题解析视角">
            {TABS.map((entry) => (
              <button
                key={entry.id}
                type="button"
                role="tab"
                aria-selected={tab === entry.id}
                onClick={() => setTab(entry.id)}
                className={cn(
                  "min-h-11 rounded-full px-3 py-1.5 text-xs whitespace-nowrap transition-colors duration-150 md:min-h-9",
                  tab === entry.id ? "bg-accent text-accent-fg" : "bg-surface-2 text-muted",
                )}
              >
                {entry.label}
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
