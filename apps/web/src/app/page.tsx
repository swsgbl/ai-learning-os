"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { BookOpen, Headphones, Library } from "lucide-react";
import { api } from "@/lib/api";
import type { PaperSummary } from "@/lib/types";
import { PaperCard } from "@/components/paper-card";
import { Card } from "@/components/ui/card";

const MODES = [
  {
    href: "/voice",
    icon: Headphones,
    title: "语音陪练",
    copy: "朗读题目和选项，口头作答，整卷结束后评分并拆错题。",
  },
  {
    href: "/exam",
    icon: BookOpen,
    title: "考场审阅",
    copy: "服务器控制倒计时，交卷后逐题给出对错、知识点与错因。",
  },
  {
    href: "/library",
    icon: Library,
    title: "学习库",
    copy: "公开课与题库来源统一登记，后续接入授权导入与证据追踪。",
  },
] as const;

export default function HomePage() {
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.papers().then(setPapers).catch((cause: Error) => setError(cause.message));
  }, []);

  return (
    <div className="space-y-10">
      <section className="pt-2">
        <p className="text-sm text-muted">随身学伴</p>
        <h1 className="mt-2 font-display text-4xl leading-[1.15] sm:text-5xl">
          把复杂留给系统
          <br />
          把简单留给你
        </h1>
        <p className="mt-4 max-w-xl text-sm leading-relaxed text-muted">
          砚席承接 Grok 原型的学习界面风格，底层改为服务端权威考试、可替换语音 adapter 和来源治理。
        </p>
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

      <section>
        <div className="mb-3 flex items-end justify-between">
          <h2 className="font-display text-2xl">今日练习</h2>
          <Link href="/library" className="text-sm text-muted hover:text-ink">
            学习库
          </Link>
        </div>
        {error ? (
          <Card className="p-5 text-sm text-muted">API 未启动：{error}。请先启动 services/api。</Card>
        ) : papers.length === 0 ? (
          <Card className="p-5 text-sm text-muted">正在读取服务端题库。</Card>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {papers.slice(0, 4).map((paper) => (
              <PaperCard key={paper.id} paper={paper} compact />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
