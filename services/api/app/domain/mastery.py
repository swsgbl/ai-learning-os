"""M16-002 概念掌握度证据状态机（升级方案 Phase 2 / Prompt 2）。

可解释规则 MVP（方案 §5：先规则，后建模——不上未验证 KT 模型）：

- 状态：unknown → emerging → practicing → provisional → mastered；
  mastered 之后延迟/迁移失败 → at_risk（复测失败是有意义的降级信号）。
- 证据 = MasteryEvidence（指向原始 Attempt + concept），输入为
  AttemptEvidence 值对象（领域层不依赖 ORM）。
- ConceptMastery 是**可重算的派生视图**：derive_mastery(attempts) 从
  原始作答证据全量重算，不静默覆盖原始答案。
- 阈值以常量显式给出（学科可在黄金集上校准后调整——方案明确反对
  「不经验证硬编码三连对=掌握」，此处阈值是 MVP 默认，标注待校准）。

规则要点（方案 §6 诚实掌握）：
- 同一题型反复答对不能直接 mastered（需不同上下文 + 独立 + 延迟/迁移）；
- 高信心错误是误概念信号（caller 可用于优先复测）；
- 「AI 觉得讲清楚了」「看完课程」不产生证据。
"""
from __future__ import annotations

from dataclasses import dataclass

MASTERY_STATES: tuple[str, ...] = (
    "unknown",
    "emerging",
    "practicing",
    "provisional",
    "mastered",
    "at_risk",
)

#: MVP 默认阈值（黄金集校准前的事先声明值；见 LEARNING_EVIDENCE_STATUS.md）
INDEPENDENT_CORRECT_FOR_PROVISIONAL = 3  # 独立正确证据条数
DISTINCT_CONTEXTS_FOR_PROVISIONAL = 2  # 不同作答上下文（题型/阶段）数
CORRECT_RATE_FOR_PRACTICING = 0.5  # practicing 门槛：独立作答正确率过半
CONFIDENCE_SCALE = (1, 5)  # 信心 1-5（提交前采集；反馈后采集无效——见方案 G 节）
HIGH_CONFIDENCE_THRESHOLD = 4  # ≥4 且答错 = 高信心错误（误概念信号）


class MasteryRuleError(ValueError):
    """非法证据输入（未知字段值等）。"""


@dataclass(frozen=True, slots=True)
class AttemptEvidence:
    """一次作答的掌握度证据（从 Attempt 行投影；领域层无 ORM 依赖）。

    independent：无提示独立作答；delayed：延迟复测；transfer：未见情境
    迁移题；context：作答上下文标签（题型/阶段，如 mcq:baseline——
    「不同上下文」判定用其去重数）。
    """

    attempt_id: str
    concept_id: str
    correct: bool | None  # None = 未判分（三态，不折算为错误）
    confidence: int | None = None
    hints_used: int = 0
    independent: bool = True
    delayed: bool = False
    transfer: bool = False
    context: str = "practice"

    def __post_init__(self) -> None:
        if self.confidence is not None and not (
            CONFIDENCE_SCALE[0] <= self.confidence <= CONFIDENCE_SCALE[1]
        ):
            raise MasteryRuleError(f"confidence 必须在 {CONFIDENCE_SCALE} 内: {self.confidence}")
        if self.correct is None and not self.independent:
            # 未判分证据不区分独立性无意义，但不是错误输入——容忍
            pass


@dataclass(frozen=True, slots=True)
class MasteryOutcome:
    """重算结果：状态 + 关键统计（给 UI/报告解释，不是黑盒分数）。"""

    state: str
    evidence_count: int
    independent_correct: int
    distinct_contexts: int
    delayed_or_transfer_correct: int
    high_confidence_errors: int
    estimator_version: str = "rule-mvp-1"


def is_high_confidence_error(evidence: AttemptEvidence) -> bool:
    """高信心错误：confidence ≥4 且判分为错（误概念优先复测信号）。"""
    return (
        evidence.correct is False
        and evidence.confidence is not None
        and evidence.confidence >= HIGH_CONFIDENCE_THRESHOLD
    )


def derive_mastery(evidence_list: list[AttemptEvidence]) -> MasteryOutcome:
    """从原始证据全量重算概念掌握状态（幂等：同一输入恒同一输出）。

    规则链（自顶向下，第一条命中即返回）：
    1. mastered：provisional 条件 + ≥1 条延迟或迁移的独立正确；
    2. at_risk：曾有 mastered 级证据（延迟/迁移通过过）但最新一条延迟/
       迁移作答失败（复测失败降级——时间序以列表传入顺序为准，caller
       保证按 occurred_at 升序投影）；
    3. provisional：≥N 条独立正确 + ≥C 个不同上下文 + 无延迟/迁移证据；
    4. practicing：有独立作答且正确率 ≥50%，但证据量/上下文不足；
    5. emerging：有证据但多为提示/未判分/低正确率；
    6. unknown：无有效证据。
    """
    if not evidence_list:
        return MasteryOutcome(
            state="unknown", evidence_count=0, independent_correct=0,
            distinct_contexts=0, delayed_or_transfer_correct=0, high_confidence_errors=0,
        )

    graded = [e for e in evidence_list if e.correct is not None]
    independent_correct = [e for e in graded if e.correct and e.independent]
    contexts = {e.context for e in independent_correct}
    delayed_transfer = [e for e in independent_correct if e.delayed or e.transfer]
    failed_delayed_transfer = [
        e for e in graded if (e.delayed or e.transfer) and e.correct is False and e.independent
    ]
    high_conf_errors = sum(1 for e in graded if is_high_confidence_error(e))

    stats = MasteryOutcome(
        state="unknown",
        evidence_count=len(evidence_list),
        independent_correct=len(independent_correct),
        distinct_contexts=len(contexts),
        delayed_or_transfer_correct=len(delayed_transfer),
        high_confidence_errors=high_conf_errors,
    )

    # at_risk：曾经通过过延迟/迁移，但之后（列表序=时间序）又失败
    if delayed_transfer and failed_delayed_transfer:
        last_pass_index = max(evidence_list.index(e) for e in delayed_transfer)
        last_fail_index = max(evidence_list.index(e) for e in failed_delayed_transfer)
        if last_fail_index > last_pass_index:
            return MasteryOutcome(state="at_risk", **_stats_dict(stats))

    if (
        len(independent_correct) >= INDEPENDENT_CORRECT_FOR_PROVISIONAL
        and len(contexts) >= DISTINCT_CONTEXTS_FOR_PROVISIONAL
    ):
        if delayed_transfer:
            return MasteryOutcome(state="mastered", **_stats_dict(stats))
        return MasteryOutcome(state="provisional", **_stats_dict(stats))

    # 独立正确占比过半但证据量/上下文不足 → practicing
    if graded and len(independent_correct) >= max(1, len(graded) * CORRECT_RATE_FOR_PRACTICING):
        return MasteryOutcome(state="practicing", **_stats_dict(stats))

    return MasteryOutcome(state="emerging", **_stats_dict(stats))


def _stats_dict(outcome: MasteryOutcome) -> dict[str, int | str]:
    return {
        "evidence_count": outcome.evidence_count,
        "independent_correct": outcome.independent_correct,
        "distinct_contexts": outcome.distinct_contexts,
        "delayed_or_transfer_correct": outcome.delayed_or_transfer_correct,
        "high_confidence_errors": outcome.high_confidence_errors,
        "estimator_version": outcome.estimator_version,
    }
