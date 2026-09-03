"use client";

// M10-02 首页轻量聚合：三个数字（今日任务 / 薄弱概念 / 可练试卷）+ 主要入口。
// 完整工作台在 /progress——这里不复制它；未登录时不发请求，数字如实显示待登录。
import { useEffect, useState } from "react";
import Link from "next/link";
import { BookOpen, Headphones, Library } from "lucide-react";
import { API_BASE, api } from "@/lib/api";
import { probeAuth, type AuthState } from "@/lib/auth";
import type { DailyPlan, PaperSummary, StudentStates } from "@/lib/types";
import { Card } from "@/components/ui/card";

const MODES = [
  {
    href: "/progress",
    icon: Library,
    title: "学习工作台",
    copy: "今日任务、薄弱概念与可练试卷聚合在一处，一键开考或语音陪练。",
  },
  {
    href: "/exam",
    icon: BookOpen,
    title: "考场审阅",
    copy: "服务器控制倒计时，交卷后逐题给出对错、知识点与错因。",
  },
  {
    href: "/voice",
    icon: Headphones,
    title: "语音陪练",
    copy: "朗读题目和选项，口头作答，整卷结束后评分并拆错题。",
  },
] as const;

type Summary = {
  plan: DailyPlan | null;
  states: StudentStates | null;
  papers: PaperSummary[] | null;
  error: string | null;
};

function StatCard({
  href,
  label,
  value,
  hint,
}: {
  href: string;
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <Link href={href} className="block">
      <Card className="h-full p-5 transition-shadow duration-150 hover:shadow-border-hover">
        <p className="text-xs text-muted">{label}</p>
        <p className="mt-2 font-display text-3xl tabular-nums">{value}</p>
        <p className="mt-1 text-xs text-muted">{hint}</p>
      </Card>
    </Link>
  );
}

export default function HomePage() {
  const [auth, setAuth] = useState<AuthState | null>(null);
  const [summary, setSummary] = useState<Summary>({ plan: null, states: null, papers: null, error: null });

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

  useEffect(() => {
    if (auth?.mode === "anonymous") return; // 未登录不发请求（401 会强跳登录，浏览不该被打断）
    if (!auth) return;
    let active = true;
    Promise.allSettled([api.dailyPlan(), api.studentStates(), api.papers()]).then(
      ([planResult, statesResult, papersResult]) => {
        if (!active) return;
        const firstError = [planResult, statesResult, papersResult].find(
          (result) => result.status === "rejected",
        );
        setSummary({
          plan: planResult.status === "fulfilled" ? planResult.value : null,
          states: statesResult.status === "fulfilled" ? statesResult.value : null,
          papers: papersResult.status === "fulfilled" ? papersResult.value : null,
          error:
            firstError && firstError.status === "rejected" && firstError.reason instanceof Error
              ? firstError.reason.message
              : null,
        });
      },
    );
    return () => {
      active = false;
    };
  }, [auth]);

  const loggedIn = auth?.mode === "disabled" || auth?.mode === "authenticated";
  const plan = summary.plan;
  const taskValue = loggedIn ? (plan ? `${plan.task_count}` : "—") : "登录后可见";
  const weakValue = loggedIn
    ? (summary.states ? `${summary.states.weak_concepts.length}` : "—")
    : "登录后可见";
  const paperValue = loggedIn ? (summary.papers ? `${summary.papers.length}` : "—") : "登录后可见";

  return (
    <div className="space-y-8">
      <section className="pt-2">
        <p className="text-sm text-muted">随身学伴</p>
        <h1 className="mt-2 font-display text-3xl leading-snug">把复杂留给系统，把简单留给你</h1>
        <p className="mt-3 max-w-xl text-sm leading-relaxed text-muted">
          服务端权威考试、错因驱动的学习计划、来源治理与审计留痕。
        </p>
      </section>

      <section className="grid gap-3 sm:grid-cols-3">
        <StatCard
          href="/progress"
          label="今日任务"
          value={taskValue}
          hint={
            plan
              ? `新学 ${plan.new_learning_count} · 复习 ${plan.review_count} · 错题重测 ${plan.mistake_retry_count}`
              : loggedIn
                ? summary.error
                  ? "读取失败，可稍后重试"
                  : "正在读取"
                : "登录后展示新学 / 复习 / 错题重测"
          }
        />
        <StatCard
          href="/progress"
          label="薄弱概念"
          value={weakValue}
          hint={
            summary.states
              ? `学生模型已覆盖 ${summary.states.concept_count} 个概念`
              : loggedIn
                ? "完成考试后生成"
                : "登录后来自你的作答历史"
          }
        />
        <StatCard
          href="/exam"
          label="可练试卷"
          value={paperValue}
          hint={
            summary.papers
              ? "一键进入考场或语音陪练"
              : loggedIn
                ? "题库为空或读取失败"
                : "登录后查看题库"
          }
        />
      </section>

      <section className="grid gap-3 sm:grid-cols-3">
        {MODES.map((mode) => (
          <Link key={mode.href} href={mode.href} className="block">
            <Card className="h-full p-5 transition-shadow duration-150 hover:shadow-border-hover">
              <mode.icon className="size-5 text-accent" />
              <h2 className="mt-4 font-display text-xl">{mode.title}</h2>
              <p className="mt-2 text-sm leading-relaxed text-muted">{mode.copy}</p>
              <p className="mt-3 text-sm text-accent">进入</p>
            </Card>
          </Link>
        ))}
      </section>
    </div>
  );
}
