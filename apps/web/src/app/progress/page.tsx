"use client";

// M10-02 学习工作台（保留 /progress 路由）：聚合 daily-plan / student states / papers
// 三个服务端投影。今日任务三分类（新学/复习/错题重测）带入选理由，
// 薄弱概念来自学生模型，试卷卡片一键进入 /exam/[paperId] 与 /voice/[paperId]。
// 空状态、API 错误、未登录/本地模式均如实展示——不虚构数据。
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, BookMarked, RotateCcw, Sparkles } from "lucide-react";
import { API_BASE, api } from "@/lib/api";
import { probeAuth, type AuthState } from "@/lib/auth";
import type { DailyPlan, PaperSummary, PlanTask, StudentStates } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { PaperCard } from "@/components/paper-card";

const KIND_META: Record<string, { label: string; icon: typeof Sparkles; tone: "mute" | "good" | "bad" | "accent" }> = {
  new_learning: { label: "新学", icon: Sparkles, tone: "accent" },
  review: { label: "复习", icon: RotateCcw, tone: "good" },
  mistake_retry: { label: "错题重测", icon: AlertTriangle, tone: "bad" },
};

const KIND_ORDER = ["review", "mistake_retry", "new_learning"] as const;

function kindLabel(kind: string): string {
  return KIND_META[kind]?.label ?? kind; // 未知 kind 如实透出原始值
}

function SectionError({ what, message, onRetry }: { what: string; message: string; onRetry: () => void }) {
  return (
    <Card className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm text-bad">
      <span>
        {what}加载失败：{message}
      </span>
      <Button variant="outline" size="sm" onClick={onRetry}>
        重试
      </Button>
    </Card>
  );
}

function TaskCard({ task }: { task: PlanTask }) {
  const meta = KIND_META[task.kind];
  const Icon = meta?.icon ?? BookMarked;
  return (
    <Card className="flex items-start gap-3 p-4">
      <span
        className={cn(
          "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg",
          task.kind === "new_learning" && "bg-accent text-accent-fg",
          task.kind === "review" && "bg-good-soft text-good",
          task.kind === "mistake_retry" && "bg-bad-soft text-bad",
          !meta && "bg-surface-2 text-muted",
        )}
      >
        <Icon className="size-4" />
      </span>
      <div className="min-w-0">
        <p className="text-sm font-medium leading-snug">{task.title}</p>
        <p className="mt-1 text-xs leading-relaxed text-muted">{task.reason}</p>
        {task.concept_ids.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {task.concept_ids.map((concept) => (
              <Badge key={concept} className="font-normal">
                {concept}
              </Badge>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}

function formatDay(iso: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString("zh-CN", { month: "long", day: "numeric" });
  } catch {
    return iso;
  }
}

export default function ProgressPage() {
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [plan, setPlan] = useState<DailyPlan | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [states, setStates] = useState<StudentStates | null>(null);
  const [statesError, setStatesError] = useState<string | null>(null);
  const [papers, setPapers] = useState<PaperSummary[] | null>(null);
  const [papersError, setPapersError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    probeAuth(API_BASE)
      .then((state) => {
        if (active) setAuth(state);
      })
      .catch(() => {
        if (active) setAuth({ mode: "anonymous" });
      });
    return () => {
      active = false;
    };
  }, []);

  const loadAll = useCallback(async () => {
    setPlanError(null);
    setStatesError(null);
    setPapersError(null);
    const [planResult, statesResult, papersResult] = await Promise.allSettled([
      api.dailyPlan(),
      api.studentStates(),
      api.papers(),
    ]);
    if (planResult.status === "fulfilled") setPlan(planResult.value);
    else setPlanError(planResult.reason instanceof Error ? planResult.reason.message : "请求失败");
    if (statesResult.status === "fulfilled") setStates(statesResult.value);
    else setStatesError(statesResult.reason instanceof Error ? statesResult.reason.message : "请求失败");
    if (papersResult.status === "fulfilled") setPapers(papersResult.value);
    else setPapersError(papersResult.reason instanceof Error ? papersResult.reason.message : "请求失败");
  }, []);

  useEffect(() => {
    if (auth?.mode !== "anonymous") void loadAll();
  }, [auth, loadAll]);

  const grouped = plan
    ? KIND_ORDER.map((kind) => ({
        kind,
        tasks: plan.tasks.filter((task) => task.kind === kind),
      }))
    : [];

  return (
    <div className="space-y-8">
      <div>
        <p className="text-xs text-muted">工作台</p>
        <h1 className="mt-1 font-display text-3xl">今天学什么</h1>
        <p className="mt-2 max-w-xl text-sm text-muted">
          今日任务由服务端学习模型实时规划（新学 / 复习 / 错题重测），薄弱概念来自你的作答历史，试卷可一键开考或语音陪练。
        </p>
      </div>

      {auth?.mode === "disabled" && (
        <Card className="p-4 text-xs text-muted">本地模式：未配置 AUTH_SECRET，学习数据仅保存在本机。</Card>
      )}
      {auth?.mode === "anonymous" && (
        <Card className="p-6">
          <h2 className="font-display text-xl">请先登录</h2>
          <p className="mt-2 max-w-md text-sm leading-relaxed text-muted">
            登录后这里会聚合你的今日任务、薄弱概念与可练试卷。
          </p>
          <Button asChild className="mt-4">
            <Link href="/login">前往登录</Link>
          </Button>
        </Card>
      )}

      {auth?.mode !== "anonymous" && (
        <>
          <section>
            <div className="mb-3 flex items-end justify-between">
              <h2 className="font-display text-2xl">
                今日任务
                {plan && (
                  <span className="ml-2 align-middle text-sm font-normal text-muted tabular-nums">
                    {formatDay(plan.plan_date)} · 共 {plan.task_count} 项
                  </span>
                )}
              </h2>
            </div>
            {planError ? (
              <SectionError what="今日任务" message={planError} onRetry={() => void loadAll()} />
            ) : !plan ? (
              <Card className="p-5 text-sm text-muted">正在读取今日计划……</Card>
            ) : plan.task_count === 0 ? (
              <Card className="p-5 text-sm text-muted">
                今日暂无任务。提交一场考试后，系统会从你的学习事件生成新学 / 复习 / 错题重测计划。
              </Card>
            ) : (
              <div className="space-y-4">
                {grouped.map(
                  ({ kind, tasks }) =>
                    tasks.length > 0 && (
                      <div key={kind} className="space-y-2">
                        <p className="flex items-center gap-2 text-sm font-medium">
                          <Badge tone={KIND_META[kind]?.tone ?? "mute"}>{kindLabel(kind)}</Badge>
                          <span className="text-muted tabular-nums">{tasks.length} 项</span>
                        </p>
                        <div className="grid gap-2 sm:grid-cols-2">
                          {tasks.map((task) => (
                            <TaskCard key={`${task.kind}-${task.question_id}`} task={task} />
                          ))}
                        </div>
                      </div>
                    ),
                )}
              </div>
            )}
          </section>

          <section>
            <h2 className="mb-3 font-display text-2xl">薄弱概念</h2>
            {statesError ? (
              <SectionError what="学生状态" message={statesError} onRetry={() => void loadAll()} />
            ) : !states ? (
              <Card className="p-5 text-sm text-muted">正在读取学生模型……</Card>
            ) : states.concept_count === 0 ? (
              <Card className="p-5 text-sm text-muted">
                暂无掌握度数据。完成一场考试并交卷后，这里会给出每个概念的掌握度与遗忘风险。
              </Card>
            ) : (
              <div className="space-y-2">
                {states.weak_concepts.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {states.weak_concepts.map((concept) => (
                      <Badge key={concept} tone="bad">
                        {concept}
                      </Badge>
                    ))}
                  </div>
                )}
                <Card className="divide-y divide-border">
                  {states.states.map((state) => (
                    <div key={state.concept_id} className="flex items-center justify-between gap-3 p-4">
                      <div className="min-w-0">
                        <p className="text-sm font-medium">{state.concept_id}</p>
                        <p className="mt-0.5 text-xs text-muted tabular-nums">
                          证据 {state.evidence_count} 条 · 对 {state.correct_count} / 错 {state.wrong_count}
                        </p>
                      </div>
                      <span
                        className={cn(
                          "shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium tabular-nums",
                          state.mastery >= 0.7
                            ? "bg-good-soft text-good"
                            : state.mastery >= 0.4
                              ? "bg-surface-2 text-warn"
                              : "bg-bad-soft text-bad",
                        )}
                        title={`掌握度 ${(state.mastery * 100).toFixed(0)}% · 遗忘风险 ${(state.forgetting_risk * 100).toFixed(0)}%`}
                      >
                        掌握 {(state.mastery * 100).toFixed(0)}%
                      </span>
                    </div>
                  ))}
                </Card>
              </div>
            )}
          </section>

          <section>
            <div className="mb-3 flex items-end justify-between">
              <h2 className="font-display text-2xl">可练试卷</h2>
              <Link href="/exam" className="text-sm text-muted hover:text-ink">
                全部试卷
              </Link>
            </div>
            {papersError ? (
              <SectionError what="试卷列表" message={papersError} onRetry={() => void loadAll()} />
            ) : !papers ? (
              <Card className="p-5 text-sm text-muted">正在读取试卷……</Card>
            ) : papers.length === 0 ? (
              <Card className="p-5 text-sm text-muted">题库还没有试卷，请先在服务端导入。</Card>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2">
                {papers.slice(0, 4).map((paper) => (
                  <PaperCard key={paper.id} paper={paper} compact />
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
