"use client";

// M15-001 定制课程页 client leaf：学习目标输入 → 生成课程草稿（LLM）→
// 大纲审阅（章节/评估/备注）→ 采纳入库（approve）→ 去练习。
// 全程走既有治理课程生成流（/api/v1/courses/generation-drafts），
// 本组件只做用户面入口，不新增服务端能力。
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { BookOpen, Loader2, Sparkles, Wand2 } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { GenerationDraft } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

const EXAMPLE_GOALS = [
  "Python 基础入门",
  "高中函数与导数专题",
  "英语四级核心词汇",
  "SQL 数据分析入门",
] as const;

type Phase =
  | { kind: "idle" }
  | { kind: "generating" }
  | { kind: "review"; draft: GenerationDraft }
  | { kind: "approving"; draft: GenerationDraft }
  | { kind: "materializing"; draft: GenerationDraft }
  | { kind: "done"; goal: string }
  | { kind: "materialized"; paperId: string; questionCount: number; title: string }
  | { kind: "error"; message: string };

export function CourseCreator() {
  const [goal, setGoal] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [history, setHistory] = useState<GenerationDraft[] | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  const loadHistory = useCallback(() => {
    api.drafts.courseGeneration
      .list()
      .then(setHistory)
      .catch(() => setHistoryError("历史草稿加载失败，可重试"));
  }, []);

  useEffect(() => {
    loadHistory();
    return () => {
      setHistory(null);
    };
  }, [loadHistory]);

  async function generate() {
    const trimmed = goal.trim();
    if (!trimmed) return;
    setPhase({ kind: "generating" });
    try {
      const draft = await api.drafts.courseGeneration.create(trimmed);
      setPhase({ kind: "review", draft });
      loadHistory();
    } catch (cause) {
      setPhase({
        kind: "error",
        message: cause instanceof ApiError ? cause.message : "生成失败，请重试",
      });
    }
  }

  async function approve(draft: GenerationDraft) {
    setPhase({ kind: "approving", draft });
    try {
      await api.drafts.courseGeneration.approve(draft.id);
      setPhase({ kind: "done", goal: draft.goal });
      loadHistory();
    } catch (cause) {
      setPhase({
        kind: "error",
        message: cause instanceof ApiError ? cause.message : "采纳失败，请重试",
      });
    }
  }

  // M15-001: 学习者主路径——草稿直接生成我的私有练习卷（无需治理审核）。
  async function materialize(draft: GenerationDraft) {
    setPhase({ kind: "materializing", draft });
    try {
      const out = await api.drafts.courseGeneration.materialize(draft.id);
      setPhase({
        kind: "materialized",
        paperId: out.paper_id,
        questionCount: out.question_count,
        title: out.title,
      });
    } catch (cause) {
      setPhase({
        kind: "error",
        message: cause instanceof ApiError ? cause.message : "生成练习卷失败，请重试",
      });
    }
  }

  const busy =
    phase.kind === "generating" ||
    phase.kind === "approving" ||
    phase.kind === "materializing";

  return (
    <div ref={rootRef} className="mx-auto max-w-3xl space-y-6 px-4 py-8">
      <header className="space-y-2">
        <p className="flex items-center gap-2 text-xs text-muted">
          <Sparkles className="size-3.5" aria-hidden />
          三步定制你的专属课程
        </p>
        <h1 className="font-display text-3xl">定制我的课程</h1>
        <p className="max-w-xl text-sm text-muted">
          告诉我们你的学习目标，服务端会生成一份含章节大纲与配套评估的课程草稿，
          你审阅后采纳即入库，随即可以开考或语音陪练。
        </p>
      </header>

      {/* 第 1 步：目标输入 */}
      <Card className="space-y-3 p-5">
        <label htmlFor="goal" className="text-sm font-medium">
          1 · 你想学什么？
        </label>
        <textarea
          id="goal"
          value={goal}
          onChange={(event) => setGoal(event.target.value)}
          placeholder="例如：我想在两周内掌握 Python 的基础语法与常用数据结构"
          rows={3}
          maxLength={512}
          disabled={busy}
          className="w-full resize-none rounded-lg border bg-background p-3 text-sm outline-none focus-visible:ring-2"
        />
        <div className="flex flex-wrap gap-2">
          {EXAMPLE_GOALS.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => setGoal(example)}
              disabled={busy}
              className="rounded-full border px-3 py-1 text-xs text-muted transition-colors hover:bg-surface-2"
            >
              {example}
            </button>
          ))}
        </div>
        <Button onClick={() => void generate()} disabled={busy || !goal.trim()} className="w-full sm:w-auto">
          {phase.kind === "generating" ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : (
            <Wand2 className="size-4" aria-hidden />
          )}
          {phase.kind === "generating" ? "正在生成课程大纲…" : "生成课程草稿"}
        </Button>
        {phase.kind === "error" && (
          <p role="alert" className="text-sm text-destructive">
            {phase.message}
          </p>
        )}
      </Card>

      {/* 第 2 步：大纲审阅 */}
      {phase.kind === "review" && (
        <DraftReview
          draft={phase.draft}
          onApprove={() => void approve(phase.draft)}
          onMaterialize={() => void materialize(phase.draft)}
        />
      )}
      {phase.kind === "approving" && (
        <DraftReview draft={phase.draft} onApprove={undefined} approving />
      )}
      {phase.kind === "materializing" && (
        <DraftReview draft={phase.draft} onApprove={undefined} approving materializing />
      )}

      {/* 第 3 步：完成 */}
      {phase.kind === "done" && (
        <Card className="space-y-3 p-5">
          <p className="text-sm font-medium">课程已入库 🎉</p>
          <p className="text-sm text-muted">「{phase.goal}」已加入学习库，现在就去练第一卷吧。</p>
          <div className="flex flex-wrap gap-2">
            <Link href="/exam" className="text-sm underline underline-offset-4">
              去考场开考
            </Link>
            <Link href="/voice" className="text-sm underline underline-offset-4">
              去语音陪练
            </Link>
            <Link href="/library" className="text-sm underline underline-offset-4">
              去学习库看看
            </Link>
          </div>
        </Card>
      )}

      {/* 练习卷生成成功（学习者主路径） */}
      {phase.kind === "materialized" && (
        <Card className="space-y-3 p-5" data-testid="materialized">
          <p className="text-sm font-medium">练习卷已生成 🎉</p>
          <p className="text-sm text-muted">
            「{phase.title}」共 {phase.questionCount} 题（开放作答，练后可看 AI 报告）。
          </p>
          <div className="flex flex-wrap gap-2">
            <Link
              href={`/exam/${phase.paperId}`}
              className="text-sm underline underline-offset-4"
            >
              立即开考
            </Link>
            <Link href={`/voice/${phase.paperId}`} className="text-sm underline underline-offset-4">
              语音陪练这卷
            </Link>
          </div>
        </Card>
      )}

      {/* 历史草稿 */}
      <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted">我的生成记录</h2>
        {historyError && <p className="text-xs text-muted">{historyError}</p>}
        {history === null && !historyError && <p className="text-xs text-muted">加载中…</p>}
        {history !== null && history.length === 0 && (
          <p className="text-xs text-muted">还没有生成过课程——从上方输入目标开始。</p>
        )}
        {history !== null && history.length > 0 && (
          <ul className="space-y-2">
            {history.map((draft) => (
              <li key={draft.id}>
                <button
                  type="button"
                  onClick={() => setPhase({ kind: "review", draft })}
                  className="w-full rounded-lg border p-3 text-left transition-colors hover:bg-surface-2"
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm">{draft.goal}</span>
                    <span className="shrink-0 text-xs text-muted">
                      {draft.status === "approved" ? "已采纳" : draft.status === "rejected" ? "已放弃" : "待审阅"}
                    </span>
                  </span>
                  <span className="mt-1 block text-xs text-muted">
                    {draft.chapter_count} 章 · {new Date(draft.created_at).toLocaleDateString()}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function DraftReview({
  draft,
  onApprove,
  onMaterialize,
  approving = false,
  materializing = false,
}: {
  draft: GenerationDraft;
  onApprove?: () => void;
  onMaterialize?: () => void;
  approving?: boolean;
  materializing?: boolean;
}) {
  const chapters = draft.plan.outline ?? [];
  const assessments = draft.plan.assessments ?? [];
  return (
    <Card className="space-y-4 p-5" data-testid="draft-review">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium">2 · 审阅课程大纲</p>
        <span className="text-xs text-muted">{draft.chapter_count} 章</span>
      </div>
      <p className="text-sm text-muted">目标：{draft.goal}</p>
      {chapters.length > 0 && (
        <ol className="space-y-2">
          {chapters.map((chapter) => (
            <li key={chapter.chapter_no} className="rounded-lg border p-3">
              <p className="text-sm">
                第 {chapter.chapter_no} 章 · {chapter.title}
              </p>
              <p className="mt-1 text-xs text-muted">
                难度 {chapter.difficulty} · 概念 {chapter.concept_id}
              </p>
            </li>
          ))}
        </ol>
      )}
      {assessments.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted">配套评估（{assessments.length} 题）</summary>
          <ul className="mt-2 space-y-1">
            {assessments.map((item, index) => (
              <li key={index} className="text-xs text-muted">
                第 {item.chapter_no} 章 · {item.stem}
              </li>
            ))}
          </ul>
        </details>
      )}
      {draft.generation_note && (
        <p className="text-xs text-muted">生成备注：{draft.generation_note}</p>
      )}
      <div className="flex flex-col gap-2 sm:flex-row">
        {onMaterialize && (
          <Button onClick={onMaterialize} data-testid="materialize-course" className="w-full sm:w-auto">
            {materializing ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <BookOpen className="size-4" aria-hidden />}
            {materializing ? "正在生成练习卷…" : "生成我的练习卷（立即开练）"}
          </Button>
        )}
        {onApprove && (
          <Button
            onClick={onApprove}
            data-testid="approve-course"
            variant="outline"
            className="w-full sm:w-auto"
          >
            {approving ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
            {approving ? "正在提交…" : "提交课程库审核（治理）"}
          </Button>
        )}
      </div>
      {(approving || materializing) && (
        <p className="text-xs text-muted">
          {materializing
            ? "从大纲生成你的私有练习卷，无需等待审核。"
            : "治理审核通过后进入全局课程库（需要治理员权限）。"}
        </p>
      )}
    </Card>
  );
}
