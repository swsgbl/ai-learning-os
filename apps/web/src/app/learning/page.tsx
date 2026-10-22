"use client";

// M16-006 Web 学习冲刺工作台（升级方案 Phase 1-6 的用户入口）。
// 三区：合同与 Sprint 流程（创建→确认→蓝图→前测→推进→后测→报告→复测队列）、
// 掌握度视图（attempts 实时重算）、间隔复习队列。Web 只提交意图——
// 判分/状态推进全部服务端权威（AGENTS.md 架构规则）。
import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type {
  AssessmentItem,
  BlueprintInfo,
  LearningContract,
  LearningSprint,
  MasteryOverviewItem,
  ReviewQueueItem,
  SprintReport,
  TargetOutcome,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

const PHASE_CHAIN = [
  "created",
  "contract_confirmed",
  "baseline_assessment",
  "concept_map_review",
  "plan_confirmed",
  "learning",
  "targeted_practice",
  "transfer_check",
  "posttest",
  "report",
  "follow_up",
] as const;

const PHASE_LABELS: Record<string, string> = {
  created: "已创建",
  contract_confirmed: "合同已确认",
  baseline_assessment: "前测",
  concept_map_review: "概念地图",
  plan_confirmed: "计划确认",
  learning: "核心学习",
  targeted_practice: "定向练习",
  transfer_check: "迁移检验",
  posttest: "后测",
  report: "学习报告",
  follow_up: "间隔复测",
};

const MASTERY_LABELS: Record<string, string> = {
  unknown: "未知",
  emerging: "萌芽",
  practicing: "练习中",
  provisional: "初步掌握",
  mastered: "已掌握",
  at_risk: "有遗忘风险",
};

const MASTERY_TONE: Record<string, "mute" | "good" | "bad" | "accent"> = {
  unknown: "mute",
  emerging: "mute",
  practicing: "accent",
  provisional: "accent",
  mastered: "good",
  at_risk: "bad",
};

export default function LearningPage() {
  const [contracts, setContracts] = useState<LearningContract[] | null>(null);
  const [sprints, setSprints] = useState<Record<string, LearningSprint | null>>({});
  const [blueprints, setBlueprints] = useState<Record<string, BlueprintInfo | null>>({});
  const [mastery, setMastery] = useState<MasteryOverviewItem[] | null>(null);
  const [reviews, setReviews] = useState<ReviewQueueItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const list = await api.learning.contracts();
      setContracts(list);
      const sprintMap: Record<string, LearningSprint | null> = {};
      await Promise.all(
        list.map(async (contract) => {
          try {
            const sprintsForContract = await api.learning.contractSprints(contract.id);
            sprintMap[contract.id] = sprintsForContract[0] ?? null; // 最新冲刺
          } catch {
            sprintMap[contract.id] = null;
          }
        }),
      );
      setSprints(sprintMap);
      setMastery(await api.learning.mastery());
      setReviews(await api.learning.reviewQueue());
      setError(null);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-6">
      <div data-animate="block">
        <p className="text-xs text-muted">升级方案 M16</p>
        <h1 className="mt-1 font-display text-3xl">学习冲刺</h1>
        <p className="mt-2 max-w-xl text-sm text-muted">
          48 小时可验证学习闭环：合同确认 → 前测 → 学习与练习 → 等值后测 → 报告 → 间隔复测。
          掌握结论只来自独立作答证据。
        </p>
      </div>
      {error && <Card className="p-4 text-sm text-destructive">{error}</Card>}
      {notice && <Card className="p-4 text-sm">{notice}</Card>}
      {contracts === null ? (
        <Card className="p-6 text-sm text-muted">加载中…</Card>
      ) : contracts.length === 0 ? (
        <NewContractCard
          onCreated={() => {
            setNotice("合同已创建——确认后即可构建蓝图并开始冲刺");
            void load();
          }}
        />
      ) : (
        <div className="space-y-4">
          {contracts.map((contract) => (
            <ContractCard
              key={contract.id}
              contract={contract}
              sprint={sprints[contract.id] ?? null}
              onNotice={setNotice}
              onChanged={() => void load()}
            />
          ))}
          <NewContractCard
            compact
            onCreated={() => {
              setNotice("合同已创建");
              void load();
            }}
          />
        </div>
      )}
      <MasteryPanel mastery={mastery} />
      <ReviewPanel reviews={reviews} onChanged={() => void load()} onNotice={setNotice} />
    </div>
  );
}

function NewContractCard({ onCreated, compact = false }: { onCreated: () => void; compact?: boolean }) {
  const [title, setTitle] = useState("");
  const [outcomesText, setOutcomesText] = useState("");
  const [hours, setHours] = useState(48);
  const [passCriteria, setPassCriteria] = useState("等值后测 ≥ 80 分");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const create = async () => {
    const outcomes: TargetOutcome[] = outcomesText
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .slice(0, 8)
      .map((statement) => ({ statement, criteria: "见通过标准" }));
    if (!title.trim() || outcomes.length === 0) {
      setError("请填写标题与至少 1 条可观察能力目标（每行一条）");
      return;
    }
    setBusy(true);
    try {
      await api.learning.createContract({
        title: title.trim(),
        mode: "exam_sprint",
        target_outcomes: outcomes,
        time_budget_hours: hours,
        pass_criteria: passCriteria,
        scope_included: [],
        scope_excluded: [],
        prerequisites: [],
      });
      setTitle("");
      setOutcomesText("");
      setError(null);
      onCreated();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "创建失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className={compact ? "p-4" : "p-6"}>
      <h2 className="text-sm font-semibold">新建学习合同</h2>
      <p className="mt-1 text-xs text-muted">
        目标是可观察结果（给定 X，能在标准 Z 下完成 Y）——不是主题名。每行一条，1-8 条。
      </p>
      <div className="mt-3 space-y-2">
        <input
          className="w-full rounded border bg-transparent px-3 py-2 text-sm"
          placeholder="冲刺标题（如：SQL 数据分析 48 小时）"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <textarea
          className="min-h-24 w-full rounded border bg-transparent px-3 py-2 text-sm"
          placeholder={"能按地区写聚合 SELECT\n能解释 JOIN 行数变化\n能区分 WHERE 与 HAVING"}
          value={outcomesText}
          onChange={(e) => setOutcomesText(e.target.value)}
        />
        <div className="flex gap-2">
          <input
            type="number"
            min={1}
            max={200}
            className="w-24 rounded border bg-transparent px-3 py-2 text-sm"
            value={hours}
            onChange={(e) => setHours(Number(e.target.value) || 48)}
          />
          <input
            className="flex-1 rounded border bg-transparent px-3 py-2 text-sm"
            value={passCriteria}
            onChange={(e) => setPassCriteria(e.target.value)}
          />
          <Button onClick={create} disabled={busy}>
            {busy ? "创建中…" : "创建合同"}
          </Button>
        </div>
        {error && <p className="text-xs text-destructive">{error}</p>}
      </div>
    </Card>
  );
}

function ContractCard({
  contract,
  sprint,
  onNotice,
  onChanged,
}: {
  contract: LearningContract;
  sprint: LearningSprint | null;
  onNotice: (message: string) => void;
  onChanged: () => void;
}) {
  const [blueprint, setBlueprint] = useState<BlueprintInfo | null>(null);
  const [report, setReport] = useState<SprintReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [assessing, setAssessing] = useState<"baseline" | "transfer" | "posttest" | null>(null);
  const [practicing, setPracticing] = useState(false);

  const act = async (fn: () => Promise<unknown>, message: string) => {
    setBusy(true);
    try {
      await fn();
      onNotice(message);
      onChanged();
    } catch (cause) {
      onNotice(cause instanceof ApiError ? cause.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };

  const loadBlueprint = useCallback(async () => {
    try {
      setBlueprint(await api.learning.getBlueprint(contract.id));
    } catch {
      setBlueprint(null);
    }
  }, [contract.id]);

  const loadReport = useCallback(async (sprintId: string) => {
    try {
      setReport(await api.learning.report(sprintId));
    } catch {
      setReport(null);
    }
  }, []);

  useEffect(() => {
    void loadBlueprint();
    if (sprint && (sprint.phase === "report" || sprint.phase === "follow_up")) {
      void loadReport(sprint.id);
    }
  }, [loadBlueprint, loadReport, sprint]);

  const nextPhase = sprint
    ? PHASE_CHAIN[PHASE_CHAIN.indexOf(sprint.phase as (typeof PHASE_CHAIN)[number]) + 1]
    : null;
  const currentLabel = sprint ? (PHASE_LABELS[sprint.phase] ?? sprint.phase) : "未开始";

  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-medium">{contract.title}</h2>
          <p className="mt-1 text-xs text-muted">
            {contract.target_outcomes.length} 个能力目标 · {contract.time_budget_hours} 小时 ·{" "}
            {contract.pass_criteria}
          </p>
        </div>
        <Badge tone={contract.confirmed_at ? "accent" : "mute"}>
          {contract.confirmed_at ? "已确认" : "待确认"}
        </Badge>
      </div>

      <ol className="mt-3 flex flex-wrap gap-1 text-[11px]">
        {PHASE_CHAIN.map((phase, index) => {
          const active = sprint ? PHASE_CHAIN.indexOf(sprint.phase as (typeof PHASE_CHAIN)[number]) >= index : false;
          return (
            <li
              key={phase}
              className={`rounded px-2 py-1 ${active ? "bg-primary/15 text-primary" : "text-muted"}`}
            >
              {PHASE_LABELS[phase]}
            </li>
          );
        })}
      </ol>

      <div className="mt-3 flex flex-wrap gap-2">
        {!contract.confirmed_at && (
          <Button size="sm" disabled={busy} onClick={() => act(() => api.learning.confirmContract(contract.id), "合同已确认")}>
            确认合同
          </Button>
        )}
        {contract.confirmed_at && !sprint && (
          <Button
            size="sm"
            disabled={busy}
            onClick={() => act(() => api.learning.createSprint(contract.id), "冲刺已开始")}
          >
            开始冲刺
          </Button>
        )}
        {sprint && nextPhase && (
          <Button
            size="sm"
            variant="outline"
            disabled={busy || sprint.paused}
            onClick={() => act(() => api.learning.advanceSprint(sprint.id, nextPhase), `已推进到「${PHASE_LABELS[nextPhase]}」`)}
          >
            推进到 {PHASE_LABELS[nextPhase]}
          </Button>
        )}
        {sprint && !sprint.paused && sprint.phase !== "created" && sprint.phase !== "follow_up" && (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => act(() => api.learning.pauseSprint(sprint.id, "休息"), "已暂停（证据保留）")}
          >
            暂停
          </Button>
        )}
        {sprint?.paused && (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => act(() => api.learning.resumeSprint(sprint.id), "已恢复")}>
            恢复
          </Button>
        )}
        {contract.confirmed_at && (
          <>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() =>
                act(
                  () => api.learning.generateItems(contract.id).then((r) => {
                    onNotice(
                      `已生成 ${r.generated} 道客观题（丢弃 ${r.dropped}）——现在可以构建蓝图`,
                    );
                  }),
                  "",
                )
              }
            >
              生成客观题
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => act(() => api.learning.buildBlueprint(contract.id), "蓝图已构建（等值前后测题集）")}
            >
              构建/重建蓝图
            </Button>
          </>
        )}
        {sprint?.phase === "baseline_assessment" && (
          <Button size="sm" onClick={() => setAssessing("baseline")}>
            开始前测
          </Button>
        )}
        {sprint?.phase === "targeted_practice" && (
          <Button size="sm" onClick={() => setPracticing(true)}>
            开始定向练习
          </Button>
        )}
        {sprint?.phase === "transfer_check" && (
          <Button size="sm" onClick={() => setAssessing("transfer")}>
            开始迁移检验
          </Button>
        )}
        {sprint?.phase === "posttest" && (
          <Button size="sm" onClick={() => setAssessing("posttest")}>
            开始后测
          </Button>
        )}
        {sprint && (sprint.phase === "report" || sprint.phase === "follow_up") && (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => act(() => api.learning.scheduleFollowUp(sprint.id), "间隔复测队列已创建")}
          >
            安排间隔复测
          </Button>
        )}
      </div>

      {sprint?.paused && <p className="mt-2 text-xs text-amber-600">已暂停：{sprint.pause_reason || "—"}（学习证据保留）</p>}
      {sprint?.expired && <p className="mt-2 text-xs text-amber-600">已超出时间窗口——报告将如实汇总未完成项</p>}

      {blueprint && (
        <div className="mt-3 rounded border p-3 text-xs">
          <p className="font-medium">
            评估蓝图：{blueprint.complete ? "完整（前后测等值不重叠）" : "部分覆盖（题量不足，如实标注）"}
          </p>
          {blueprint.gaps.length > 0 && <p className="mt-1 text-muted">缺口：{blueprint.gaps.join("；")}</p>}
          <p className="mt-1 text-muted">
            {blueprint.plans.map((p) => `${p.concept_id}（前测 ${p.pretest_item_ids.length}/后测 ${p.posttest_item_ids.length}）`).join(" · ")}
          </p>
        </div>
      )}

      {practicing && sprint && sprint.phase === "targeted_practice" && (
        <PracticeRunner sprintId={sprint.id} onDone={() => { setPracticing(false); onChanged(); }} onNotice={onNotice} />
      )}

      {assessing && sprint && (
        <AssessmentRunner
          sprintId={sprint.id}
          phase={assessing}
          onDone={(score) => {
            setAssessing(null);
            onNotice(`${assessing === "baseline" ? "前测" : assessing === "transfer" ? "迁移检验" : "后测"}已提交：${score} 分`);
            onChanged();
          }}
        />
      )}

      {report && (
        <div className="mt-3 rounded border p-3 text-xs">
          <p className="font-medium">学习报告</p>
          <p className="mt-1">
            前测 {report.baseline?.score ?? "—"} 分 → 后测 {report.posttest?.score ?? "—"} 分
            {report.gain !== null && `（增益 ${report.gain > 0 ? "+" : ""}${report.gain}）`}
          </p>
          <p className="mt-1 text-muted">
            概念掌握：
            {report.mastery_snapshot
              .map((m) => `${m.concept_id}=${MASTERY_LABELS[m.state] ?? m.state}`)
              .join(" · ") || "暂无证据"}
          </p>
          {report.incomplete.length > 0 && (
            <p className="mt-1 text-amber-600">未完成：{report.incomplete.map((p) => PHASE_LABELS[p] ?? p).join("、")}</p>
          )}
        </div>
      )}
    </Card>
  );
}

function PracticeRunner({
  sprintId,
  onDone,
  onNotice,
}: {
  sprintId: string;
  onDone: () => void;
  onNotice: (message: string) => void;
}) {
  // M17-006 定向练习：单题流（域层决策逐步）→ 判分反馈带解析 → 下一题
  const [session, setSession] = useState<{
    concept_id: string; step: string; strategy: string; rationale: string;
    item: { item_id: string; question_type: string; stem: string; options: Array<{ key: string; text: string }> | null };
    passed_steps: number; target_steps: number;
  } | null>(null);
  const [answer, setAnswer] = useState("");
  const [confidence, setConfidence] = useState<number | null>(null);
  const [feedback, setFeedback] = useState<{ correct: boolean | null; explanation: string; rationale: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const loadNext = () => {
    setAnswer("");
    setConfidence(null);
    setFeedback(null);
    api.learning
      .practiceSession(sprintId)
      .then((s) => setSession(s))
      .catch((cause) => {
        onNotice(cause instanceof ApiError ? cause.message : "练习会话加载失败");
        onDone();
      });
  };

  useEffect(() => {
    loadNext();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sprintId]);

  const submit = async () => {
    if (!session || !answer) return;
    setBusy(true);
    try {
      const result = await api.learning.practiceAnswer(sprintId, {
        item_id: session.item.item_id,
        response: answer,
        confidence: confidence ?? undefined,
      });
      setFeedback({ correct: result.correct, explanation: result.explanation, rationale: result.rationale });
      if (result.practice_complete) {
        onNotice(`定向练习完成（已推进「迁移检验」）`);
        onDone();
      }
    } catch (cause) {
      onNotice(cause instanceof ApiError ? cause.message : "提交失败");
    } finally {
      setBusy(false);
    }
  };

  if (!session) return <p className="mt-3 text-xs text-muted">练习会话加载中…（答案服务端保管）</p>;

  return (
    <div className="mt-3 space-y-3 rounded border p-3">
      <p className="text-xs font-medium">
        定向练习 · {session.concept_id}（第 {session.passed_steps + 1}/{session.target_steps} 步 · {session.step}）
      </p>
      <p className="text-[11px] text-muted">策略：{session.strategy} —— {session.rationale}</p>
      <div className="rounded bg-surface-2/50 p-3">
        <p className="text-sm">{session.item.stem}</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {(session.item.options ?? []).map((option) => (
            <button
              key={option.key}
              type="button"
              onClick={() => setAnswer(option.key)}
              className={`rounded border px-3 py-1.5 text-sm ${answer === option.key ? "border-primary bg-primary/10" : ""}`}
            >
              {option.key}. {option.text}
            </button>
          ))}
        </div>
        <div className="mt-2 flex items-center gap-2 text-xs text-muted">
          <span>信心：</span>
          {[1, 2, 3, 4, 5].map((level) => (
            <button
              key={level}
              type="button"
              onClick={() => setConfidence(level)}
              className={`rounded px-2 py-0.5 ${confidence === level ? "bg-primary/20" : ""}`}
            >
              {level}
            </button>
          ))}
        </div>
      </div>
      {feedback ? (
        <div className="rounded border p-2 text-xs">
          <p className={feedback.correct ? "text-emerald-600" : "text-red-600"}>
            {feedback.correct === true ? "答对（步骤推进）" : feedback.correct === null ? "未判分" : "答错（同步骤重试——看解析再试）"}
          </p>
          {feedback.explanation && <p className="mt-1 text-muted">解析：{feedback.explanation}</p>}
          <Button size="sm" variant="outline" className="mt-2" onClick={loadNext}>
            下一题
          </Button>
        </div>
      ) : (
        <Button size="sm" disabled={busy || !answer} onClick={submit}>
          {busy ? "判分中…" : "提交（服务端判分）"}
        </Button>
      )}
    </div>
  );
}

function AssessmentRunner({
  sprintId,
  phase,
  onDone,
}: {
  sprintId: string;
  phase: "baseline" | "transfer" | "posttest";
  onDone: (score: number) => void;
}) {
  const [items, setItems] = useState<Awaited<ReturnType<typeof api.learning.getAssessment>>["items"] | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [confidences, setConfidences] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // M17-005：transfer 阶段走迁移检验端点（练习性质选题，非蓝图等值分配）
    (phase === "transfer"
      ? api.learning.transferItems(sprintId).then((r) => r.items)
      : api.learning.getAssessment(sprintId, phase).then((r) => r.items)
    )
      .then((items) => setItems(items))
      .catch((cause) => setError(cause instanceof ApiError ? cause.message : "题集加载失败"));
  }, [sprintId, phase]);

  if (error) return <p className="mt-3 text-xs text-destructive">{error}</p>;
  if (items === null) return <p className="mt-3 text-xs text-muted">题集加载中…（答案服务端保管）</p>;

  const submit = async () => {
    const responses = items.map((item) => ({
      item_id: item.item_id,
      response: answers[item.item_id] ?? "",
      confidence: confidences[item.item_id],
    }));
    if (responses.some((r) => !r.response.trim())) {
      setError("请作答全部题目");
      return;
    }
    setBusy(true);
    try {
      const result =
        phase === "transfer"
          ? await api.learning.submitTransfer(sprintId, responses)
          : await api.learning.submitAssessment(sprintId, phase, responses);
      onDone(result.score);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "提交失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 space-y-3 rounded border p-3">
      <p className="text-xs font-medium">
        {phase === "baseline" ? "前测" : phase === "transfer" ? "迁移检验（未见情境应用题）" : "后测"}（独立作答，先提交后见判分）
      </p>
      {items.map((item) => (
        <div key={item.item_id} className="rounded bg-surface-2/50 p-3">
          <p className="text-sm">{item.stem}</p>
          <p className="mt-1 text-[11px] text-muted">概念 {item.concept_id} · {item.question_type}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {(item.options ?? []).map((option) => (
              <button
                key={option.key}
                type="button"
                onClick={() => setAnswers((prev) => ({ ...prev, [item.item_id]: option.key }))}
                className={`rounded border px-3 py-1.5 text-sm ${
                  answers[item.item_id] === option.key ? "border-primary bg-primary/10" : ""
                }`}
              >
                {option.key}. {option.text}
              </button>
            ))}
          </div>
          <div className="mt-2 flex items-center gap-2 text-xs text-muted">
            <span>作答信心（提交前）：</span>
            {[1, 2, 3, 4, 5].map((level) => (
              <button
                key={level}
                type="button"
                onClick={() => setConfidences((prev) => ({ ...prev, [item.item_id]: level }))}
                className={`rounded px-2 py-0.5 ${confidences[item.item_id] === level ? "bg-primary/20" : ""}`}
              >
                {level}
              </button>
            ))}
          </div>
        </div>
      ))}
      <Button size="sm" onClick={submit} disabled={busy}>
        {busy ? "判分中…" : "提交（服务端判分）"}
      </Button>
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

function MasteryPanel({ mastery }: { mastery: MasteryOverviewItem[] | null }) {
  if (mastery === null) return null;
  if (mastery.length === 0) {
    return (
      <Card className="p-4 text-xs text-muted">
        概念掌握度：暂无作答证据——完成前测/练习后由证据实时重算（unknown→…→mastered，复测失败会回到 at_risk）。
      </Card>
    );
  }
  return (
    <Card className="p-4">
      <p className="text-sm font-medium">概念掌握度（由作答证据实时重算）</p>
      <ul className="mt-2 space-y-1 text-xs">
        {mastery.map((m) => (
          <li key={m.concept_id} className="flex items-center gap-2">
            <Badge tone={MASTERY_TONE[m.state] ?? "mute"}>{MASTERY_LABELS[m.state] ?? m.state}</Badge>
            <span>{m.concept_id}</span>
            <span className="text-muted">
              证据 {m.evidence_count} · 独立对 {m.independent_correct} · 高信心错 {m.high_confidence_errors}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function ReviewPanel({
  reviews,
  onChanged,
  onNotice,
}: {
  reviews: ReviewQueueItem[] | null;
  onChanged: () => void;
  onNotice: (message: string) => void;
}) {
  // M17-002：复习作答会话（present → 取题 → 作答 → 服务端判分 → 滚动排期）
  const [session, setSession] = useState<{
    reviewId: string;
    conceptId: string;
    item: AssessmentItem;
    answer: string;
    confidence: number | null;
  } | null>(null);
  const [lastResult, setLastResult] = useState<{ conceptId: string; correct: boolean | null; intervalDays: number | null } | null>(null);
  const [busy, setBusy] = useState(false);

  if (reviews === null || (reviews.length === 0 && !session)) return null;

  const startReview = async (reviewId: string, conceptId: string) => {
    setBusy(true);
    try {
      await api.learning.presentReview(reviewId);
      const item = await api.learning.reviewItem(reviewId);
      setSession({ reviewId, conceptId, item, answer: "", confidence: null });
      setLastResult(null);
    } catch (cause) {
      onNotice(cause instanceof ApiError ? cause.message : "取复习题失败");
    } finally {
      setBusy(false);
    }
  };

  const submitReview = async () => {
    if (!session || !session.answer) return;
    setBusy(true);
    try {
      const result = await api.learning.attemptReview(session.reviewId, {
        item_id: session.item.item_id,
        response: session.answer,
        confidence: session.confidence ?? undefined,
      });
      setLastResult({
        conceptId: session.conceptId,
        correct: result.last_correct ?? null,
        intervalDays: result.interval_days,
      });
      setSession(null);
      onChanged();
    } catch (cause) {
      onNotice(cause instanceof ApiError ? cause.message : "提交失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="p-4">
      <p className="text-sm font-medium">间隔复习队列（复习完成 = 真实作答判分）</p>
      {lastResult && (
        <p className="mt-2 text-xs">
          <Badge tone={lastResult.correct === true ? "good" : lastResult.correct === null ? "mute" : "bad"}>
            {lastResult.correct === true ? "复习答对" : "复习答错"}
          </Badge>{" "}
          <span className="text-muted">
            {lastResult.conceptId} · 下一轮间隔 {lastResult.intervalDays ?? "—"} 天（失败回最短档）
          </span>
        </p>
      )}
      {session ? (
        <div className="mt-2 rounded border p-3">
          <p className="text-sm">{session.item.stem}</p>
          <p className="mt-1 text-[11px] text-muted">
            复习 · 概念 {session.conceptId} · {session.item.question_type}
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {(session.item.options ?? []).map((option) => (
              <button
                key={option.key}
                type="button"
                onClick={() => setSession((prev) => (prev ? { ...prev, answer: option.key } : prev))}
                className={`rounded border px-3 py-1.5 text-sm ${
                  session.answer === option.key ? "border-primary bg-primary/10" : ""
                }`}
              >
                {option.key}. {option.text}
              </button>
            ))}
          </div>
          <div className="mt-2 flex items-center gap-2 text-xs text-muted">
            <span>作答信心：</span>
            {[1, 2, 3, 4, 5].map((level) => (
              <button
                key={level}
                type="button"
                onClick={() => setSession((prev) => (prev ? { ...prev, confidence: level } : prev))}
                className={`rounded px-2 py-0.5 ${session.confidence === level ? "bg-primary/20" : ""}`}
              >
                {level}
              </button>
            ))}
          </div>
          <Button size="sm" className="mt-2" disabled={busy || !session.answer} onClick={submitReview}>
            {busy ? "判分中…" : "提交复习作答（服务端判分）"}
          </Button>
        </div>
      ) : (
        <ul className="mt-2 space-y-1 text-xs">
          {reviews.map((r) => (
            <li key={r.id} className="flex items-center gap-2">
              <Badge tone={r.overdue ? "bad" : "mute"}>{r.overdue ? "已到期" : "待复习"}</Badge>
              <span>{r.concept_id}</span>
              <span className="text-muted">
                {new Date(r.due_at).toLocaleString()} · 间隔 {r.interval_days ?? "—"} 天 · {r.status}
              </span>
              <Button size="sm" variant="outline" disabled={busy} onClick={() => startReview(r.id, r.concept_id)}>
                开始复习
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
