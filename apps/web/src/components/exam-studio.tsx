"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { ExamSession } from "@/lib/types";
import { formatClock } from "@/lib/utils";
import { cn } from "@/lib/utils";
import { Button } from "./ui/button";
import { Card } from "./ui/card";

function remainingSeconds(endAt: string) {
  return Math.max(0, Math.floor((new Date(endAt).getTime() - Date.now()) / 1000));
}

export function ExamStudio({ paperId }: { paperId: string }) {
  const router = useRouter();
  const [session, setSession] = useState<ExamSession | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [remaining, setRemaining] = useState(0);
  const [index, setIndex] = useState(0);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);
  const sequence = useRef(0);
  const submitted = useRef(false);

  const submit = useCallback(
    async (examId: string) => {
      if (submitted.current) return;
      submitted.current = true;
      try {
        await api.submitExam(examId);
        router.push(`/review/${examId}`);
      } catch (cause) {
        submitted.current = false;
        setError(cause instanceof Error ? cause.message : "提交失败");
      }
    },
    [router],
  );

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    api
      .startExam(paperId, "exam")
      .then((result) => {
        setSession(result);
        setAnswers(result.answers);
        setRemaining(result.server_remaining_seconds);
      })
      .catch((cause: Error) => setError(cause.message));
  }, [paperId, submit]);

  useEffect(() => {
    if (!session || session.status !== "active") return;
    const timer = window.setInterval(() => {
      const next = remainingSeconds(session.server_end_at);
      setRemaining(next);
      if (next === 0) void submit(session.exam_id);
    }, 500);
    return () => window.clearInterval(timer);
  }, [session, submit]);

  const questions = session?.questions ?? [];
  const question = questions[index];
  const answered = Object.values(answers).filter(Boolean).length;
  const progress = useMemo(() => (questions.length ? (answered / questions.length) * 100 : 0), [answered, questions.length]);

  async function choose(key: string) {
    if (!session || !question || submitted.current) return;
    const nextAnswers = { ...answers, [question.id]: key };
    setAnswers(nextAnswers);
    sequence.current += 1;
    try {
      const saved = await api.saveAnswer(session.exam_id, sequence.current, question.id, key);
      setSession(saved);
      setAnswers(saved.answers);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "答案同步失败");
    }
  }

  if (error && !session) return <Card className="p-6 text-sm text-bad">{error}</Card>;
  if (!session || !question) return <Card className="p-6 text-sm text-muted">正在准备考场。</Card>;

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs text-muted">考场审阅</p>
          <h1 className="font-display text-2xl">{session.paper_title}</h1>
          <p className="mt-1 text-xs text-subtle">server session · {session.exam_id}</p>
        </div>
        <div className={cn("rounded-lg bg-surface px-3 py-2 text-right shadow-border", remaining < 60 && "text-bad")}>
          <p className="text-[10px] text-muted">剩余</p>
          <p className="font-mono text-lg leading-none tabular-nums">{formatClock(remaining)}</p>
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {questions.map((item, itemIndex) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setIndex(itemIndex)}
            className={cn(
              "size-9 rounded-md text-xs tabular-nums shadow-border",
              itemIndex === index && "bg-accent text-accent-fg",
              itemIndex !== index && answers[item.id] && "bg-surface-2",
              itemIndex !== index && !answers[item.id] && "bg-surface",
            )}
          >
            {itemIndex + 1}
          </button>
        ))}
      </div>

      <Card className="p-5 sm:p-6">
        <p className="text-xs text-muted">
          第 {index + 1} 题 · {question.type === "tf" ? "判断" : "选择"}
        </p>
        <p className="mt-3 font-display text-xl leading-snug">{question.stem}</p>
        <ul className="mt-5 space-y-2">
          {question.options?.map((option) => {
            const active = answers[question.id] === option.key;
            return (
              <li key={option.key}>
                <button
                  type="button"
                  onClick={() => void choose(option.key)}
                  className={cn(
                    "flex w-full items-start gap-3 rounded-lg px-4 py-3 text-left text-sm shadow-border transition-colors",
                    active ? "bg-accent text-accent-fg" : "bg-surface hover:bg-surface-2",
                  )}
                >
                  <span className="font-medium">{option.key}</span>
                  <span>{option.text}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </Card>

      <div className="h-2 overflow-hidden rounded-full bg-surface-2">
        <div className="h-full rounded-full bg-accent transition-all duration-300" style={{ width: `${progress}%` }} />
      </div>

      <div className="flex gap-2">
        <Button variant="outline" className="flex-1" disabled={index === 0} onClick={() => setIndex((value) => value - 1)}>
          上一题
        </Button>
        {index < questions.length - 1 ? (
          <Button className="flex-1" onClick={() => setIndex((value) => value + 1)}>
            下一题
          </Button>
        ) : (
          <Button className="flex-1" onClick={() => setConfirming(true)}>
            交卷
          </Button>
        )}
      </div>
      {error && <p className="text-sm text-bad">{error}</p>}

      {confirming && (
        <div className="fixed inset-0 z-40 flex items-end justify-center bg-ink/40 p-4 sm:items-center">
          <Card className="w-full max-w-sm p-5">
            <h2 className="font-display text-xl">提交试卷？</h2>
            <p className="mt-2 text-sm text-muted">
              已作答 {answered}/{questions.length} 题。提交后进入审阅。
            </p>
            <div className="mt-4 flex gap-2">
              <Button variant="outline" className="flex-1" onClick={() => setConfirming(false)}>
                再看看
              </Button>
              <Button className="flex-1" onClick={() => void submit(session.exam_id)}>
                提交审阅
              </Button>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
