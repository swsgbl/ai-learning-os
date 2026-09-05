"use client";

// M11-01 学习工作台（保留 /progress 路由）：聚合 daily-plan / student states /
// papers 三个服务端投影（数据逻辑沿用 M10-02），视觉层为任务分组编排入场、
// 掌握度揭示条与薄弱概念层级。空状态、API 错误、未登录/本地模式均如实展示。
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AlertTriangle, BookMarked, RotateCcw, Sparkles } from "lucide-react";
import { API_BASE, api } from "@/lib/api";
import { probeAuth, type AuthState } from "@/lib/auth";
import type { DailyPlan, PaperSummary, PlanTask, StudentStates } from "@/lib/types";
import { cn } from "@/lib/utils";
import { gsap, useMotion } from "@/lib/gsap";
import { MOTION, staggerFor } from "@/lib/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { PaperCard } from "@/components/paper-card";
import { ErrorState, EmptyState, LoadingState } from "@/components/states";
import { RevealProgress } from "@/components/motion/reveal-progress";

const KIND_META: Record<string, { label: string; icon: typeof Sparkles; tone: "mute" | "good" | "bad" | "accent" }> = {
  new_learning: { label: "新学", icon: Sparkles, tone: "accent" },
  review: { label: "复习", icon: RotateCcw, tone: "good" },
  mistake_retry: { label: "错题重测", icon: AlertTriangle, tone: "bad" },
};

const KIND_ORDER = ["review", "mistake_retry", "new_learning"] as const;

function kindLabel(kind: string): string {
  return KIND_META[kind]?.label ?? kind; // 未知 kind 如实透出原始值
}

function formatDay(iso: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString("zh-CN", { month: "long", day: "numeric" });
  } catch {
    return iso;
  }
}

function TaskCard({ task }: { task: PlanTask }) {
  const meta = KIND_META[task.kind];
  const Icon = meta?.icon ?? BookMarked;
  return (
    <Card data-task className="flex items-start gap-3 p-4 transition-shadow duration-150 hover:shadow-border-hover">
      <span
        className={cn(
          "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg",
          task.kind === "new_learning" && "bg-accent text-accent-fg",
          task.kind === "review" && "bg-good-soft text-good",
          task.kind === "mistake_retry" && "bg-bad-soft text-bad",
          !meta && "bg-surface-2 text-muted",
        )}
        aria-hidden="true"
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

export function ProgressWorkbench() {
  const rootRef = useRef<HTMLDivElement>(null);
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
    // 认证状态未定（null）时绝不发受保护请求——未登录访问会被 401 全局跳转
    // 打断浏览；只有明确 disabled（本地模式）/ authenticated 才加载。
    if (auth?.mode === "disabled" || auth?.mode === "authenticated") void loadAll();
  }, [auth, loadAll]);

  // 任务卡编排：按分组 stagger；数据变化（重试/重载）时重放
  useMotion(
    (reduced) => {
      const root = rootRef.current;
      if (!root) return;
      const tasks = root.querySelectorAll<HTMLElement>("[data-task]");
      if (tasks.length === 0) return;
      if (reduced) {
        gsap.set(tasks, { clearProps: "all" });
        return;
      }
      gsap.fromTo(
        tasks,
        { autoAlpha: 0, y: MOTION.distance.rise },
        {
          autoAlpha: 1,
          y: 0,
          duration: MOTION.duration.base,
          ease: MOTION.ease.out,
          stagger: staggerFor(tasks.length),
          clearProps: "opacity,visibility,transform",
        },
      );
    },
    { scope: rootRef, dependencies: [plan] },
  );

  const grouped = plan
    ? KIND_ORDER.map((kind) => ({
        kind,
        tasks: plan.tasks.filter((task) => task.kind === kind),
      }))
    : [];

  return (
    <div ref={rootRef} className="space-y-8">
      <div data-animate="block">
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
        <Card data-animate="block" className="p-6">
          <h2 className="font-display text-xl">请先登录</h2>
          <p className="mt-2 max-w-md text-sm leading-relaxed text-muted">
            登录后这里会聚合你的今日任务、薄弱概念与可练试卷。
          </p>
          <Button asChild className="mt-4">
            <Link href="/login">前往登录</Link>
          </Button>
        </Card>
      )}
      {!auth && <LoadingState title="正在确认访问权限" />}

      {(auth?.mode === "disabled" || auth?.mode === "authenticated") && (
        <>
          <section>
            <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
              <h2 className="font-display text-2xl">
                今日任务
                {plan && (
                  <span className="ml-2 align-middle text-sm font-normal tabular-nums text-muted">
                    {formatDay(plan.plan_date)} · 共 {plan.task_count} 项
                  </span>
                )}
              </h2>
            </div>
            {planError ? (
              <ErrorState title="今日任务加载失败" detail={planError} onRetry={() => void loadAll()} />
            ) : !plan ? (
              <LoadingState title="正在读取今日计划" />
            ) : plan.task_count === 0 ? (
              <EmptyState
                title="今日暂无任务"
                detail="提交一场考试后，系统会从你的学习事件生成新学 / 复习 / 错题重测计划。"
              />
            ) : (
              <div className="space-y-4">
                {grouped.map(
                  ({ kind, tasks }) =>
                    tasks.length > 0 && (
                      <div key={kind} className="space-y-2">
                        <p className="flex items-center gap-2 text-sm font-medium">
                          <Badge tone={KIND_META[kind]?.tone ?? "mute"}>{kindLabel(kind)}</Badge>
                          <span className="tabular-nums text-muted">{tasks.length} 项</span>
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
              <ErrorState title="学生状态加载失败" detail={statesError} onRetry={() => void loadAll()} />
            ) : !states ? (
              <LoadingState title="正在读取学生模型" />
            ) : states.concept_count === 0 ? (
              <EmptyState
                title="暂无掌握度数据"
                detail="完成一场考试并交卷后，这里会给出每个概念的掌握度与遗忘风险。"
              />
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
                    <div key={state.concept_id} className="p-4">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="min-w-0">
                          <p className="text-sm font-medium">{state.concept_id}</p>
                          <p className="mt-0.5 text-xs tabular-nums text-muted">
                            证据 {state.evidence_count} 条 · 对 {state.correct_count} / 错 {state.wrong_count} · 遗忘风险{" "}
                            {(state.forgetting_risk * 100).toFixed(0)}%
                          </p>
                        </div>
                        <span
                          className={cn(
                            "shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium tabular-nums",
                            state.mastery >= 0.7
                              ? "bg-good-soft text-good"
                              : state.mastery >= 0.4
                                ? "bg-warn-soft text-warn"
                                : "bg-bad-soft text-bad",
                          )}
                          title={`掌握度 ${(state.mastery * 100).toFixed(0)}% · 遗忘风险 ${(state.forgetting_risk * 100).toFixed(0)}%`}
                        >
                          掌握 {(state.mastery * 100).toFixed(0)}%
                        </span>
                      </div>
                      <RevealProgress
                        className="mt-2 h-1.5"
                        value={state.mastery * 100}
                        aria-label={`${state.concept_id} 掌握度`}
                      />
                    </div>
                  ))}
                </Card>
              </div>
            )}
          </section>

          <section>
            <div className="mb-3 flex items-end justify-between gap-2">
              <h2 className="font-display text-2xl">可练试卷</h2>
              <Link href="/exam" className="rounded text-sm text-muted transition-colors duration-150 hover:text-ink">
                全部试卷
              </Link>
            </div>
            {papersError ? (
              <ErrorState title="试卷列表加载失败" detail={papersError} onRetry={() => void loadAll()} />
            ) : !papers ? (
              <LoadingState title="正在读取试卷" />
            ) : papers.length === 0 ? (
              <EmptyState title="题库还没有试卷" detail="请先在服务端导入。" />
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
