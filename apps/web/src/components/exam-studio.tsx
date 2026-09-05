"use client";

// M11-01 考场：状态机与考试语义（服务端权威计时 / append-only 答案 /
// 幂等提交 / 断线恢复）保持原样；视觉层拆到 QuestionPanel / QuestionNav /
// Countdown / SubmitDialog 四个 client leaf，动效只是状态反馈。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { ExamSession } from "@/lib/types";
import { ErrorState, LoadingState } from "@/components/states";
import { Countdown } from "@/components/exam/countdown";
import { QuestionNav } from "@/components/exam/question-nav";
import { QuestionPanel } from "@/components/exam/question-panel";
import { SubmitDialog } from "@/components/exam/submit-dialog";
import { Button } from "./ui/button";
import { Progress } from "./ui/progress";

function remainingSeconds(endAt: string) {
  return Math.max(0, Math.floor((new Date(endAt).getTime() - Date.now()) / 1000));
}

export function ExamStudio({ paperId }: { paperId: string }) {
  const router = useRouter();
  const [session, setSession] = useState<ExamSession | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [remaining, setRemaining] = useState(0);
  const [index, setIndex] = useState(0);
  const [direction, setDirection] = useState<1 | -1>(1);
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);
  // M2-06：下一个答案的服务端序号；以服务端 next_sequence 为权威，断线后由 getExam 恢复
  const nextSequence = useRef(1);
  const submitted = useRef(false);

  const submit = useCallback(
    async (examId: string) => {
      if (submitted.current) return;
      submitted.current = true;
      setSubmitting(true);
      try {
        await api.submitExam(examId);
        window.localStorage.removeItem(`aios.exam.${paperId}`);
        router.push(`/review/${examId}`);
      } catch (cause) {
        submitted.current = false;
        setSubmitting(false);
        setError(cause instanceof Error ? cause.message : "提交失败");
      }
    },
    [router, paperId],
  );

  const adopt = useCallback((result: ExamSession) => {
    setSession(result);
    setAnswers(result.answers);
    setRemaining(result.server_remaining_seconds);
    nextSequence.current = result.next_sequence;
    window.localStorage.setItem(`aios.exam.${paperId}`, result.exam_id);
  }, [paperId]);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const resumeExamId = window.localStorage.getItem(`aios.exam.${paperId}`);
    // M2-06 断线恢复：有进行中的考试先从服务端恢复（答案 + 剩余时间 + 序号），
    // 恢复失败或已结束才开新考试。
    const resume = resumeExamId
      ? api
          .getExam(resumeExamId)
          .then((result) =>
            result.status === "active" && result.paper_id === paperId
              ? result
              : api.startExam(paperId, "exam"),
          )
          .catch(() => api.startExam(paperId, "exam"))
      : api.startExam(paperId, "exam");
    resume
      .then((result) => {
        if (result.status === "submitted") {
          void router.push(`/review/${result.exam_id}`);
          return;
        }
        // 已过期但未出报告：服务端结算（EXPIRED→SUBMITTED）后进审阅
        if (result.status === "expired") {
          void submit(result.exam_id);
          return;
        }
        adopt(result);
      })
      .catch((cause: Error) => setError(cause.message));
  }, [paperId, submit, adopt, router]);

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

  const goTo = useCallback((next: number) => {
    setDirection(next >= index ? 1 : -1);
    setIndex(next);
  }, [index]);

  async function choose(key: string) {
    if (!session || !question || submitted.current) return;
    const nextAnswers = { ...answers, [question.id]: key };
    setAnswers(nextAnswers);
    try {
      // 序号由服务端权威管理：失败不消耗序号，重连后 getExam 重新对齐
      const saved = await api.saveAnswer(
        session.exam_id,
        nextSequence.current,
        question.id,
        key,
      );
      setSession(saved);
      setAnswers(saved.answers);
      nextSequence.current = saved.next_sequence;
      setError(null);
    } catch {
      // 断线：重新从服务端对齐状态；本地乐观答案保留，待网络恢复后重选或自动对齐
      try {
        const fresh = await api.getExam(session.exam_id);
        adopt(fresh);
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "答案同步失败，请检查网络后重试");
      }
    }
  }

  if (error && !session) return <ErrorState title="无法进入考场" detail={error} />;
  if (!session || !question)
    return <LoadingState title="正在准备考场" detail="正在向服务端申请考试会话。" />;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs text-muted">考场审阅</p>
          <h1 className="font-display text-2xl">{session.paper_title}</h1>
          <p className="mt-1 truncate text-xs text-subtle">server session · {session.exam_id}</p>
        </div>
        <Countdown remaining={remaining} />
      </div>

      <QuestionNav questions={questions} answers={answers} current={index} onSelect={goTo} />

      <QuestionPanel
        question={question}
        index={index}
        total={questions.length}
        answer={answers[question.id]}
        direction={direction}
        onChoose={(key) => void choose(key)}
      />

      <Progress value={progress} aria-label="作答进度" />

      <div className="flex gap-2">
        <Button
          variant="outline"
          className="flex-1"
          disabled={index === 0}
          onClick={() => goTo(index - 1)}
        >
          上一题
        </Button>
        {index < questions.length - 1 ? (
          <Button className="flex-1" onClick={() => goTo(index + 1)}>
            下一题
          </Button>
        ) : (
          <Button className="flex-1" onClick={() => setConfirming(true)}>
            交卷
          </Button>
        )}
      </div>
      {error && (
        <p role="alert" className="text-sm text-bad">
          {error}
        </p>
      )}

      {confirming && (
        <SubmitDialog
          answered={answered}
          total={questions.length}
          submitting={submitting}
          onCancel={() => setConfirming(false)}
          onConfirm={() => void submit(session.exam_id)}
        />
      )}
    </div>
  );
}
