"""M16-003 教学微循环与自适应练习策略（升级方案 Phase 3 / Prompt 3）。

纯函数、无状态、可复算：输入（已完成步骤 + 最近作答 + 错误标签）输出
下一步教学动作；调用方把步骤历史存 LearningEvent（可审计可恢复）。
LLM 只负责生成解释/案例/题目**内容**，策略推进由本域层决定。

微循环节奏（方案 §7 学习微循环，13 步骨架按作答证据动态裁剪）：
  first_attempt（学生先尝试，可失败）→ minimal_explanation（最小必要
  解释）→ worked_example（示例+为什么）→ contrast_case（对比/反例）→
  recall_practice（闭卷主动回忆）→ faded_practice（渐隐支架）→
  independent_practice（独立练习）→ exit_ticket（出口小测）

错误类型 → 再教学策略（方案 Prompt 3 六类）：
  missing_knowledge → 回先决概念（prerequisite_review）
  confusion → 对比案例（contrast_case）
  procedure_error → worked example 重走步骤
  reasoning_gap → 最小解释 + 渐进提示
  careless → 自查清单（不重教内容）
  material_failure → 换来源/降复杂度解释
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 微循环步骤（有序骨架）
LOOP_STEPS: tuple[str, ...] = (
    "first_attempt",
    "minimal_explanation",
    "worked_example",
    "contrast_case",
    "recall_practice",
    "faded_practice",
    "independent_practice",
    "exit_ticket",
)

ERROR_TAGS: tuple[str, ...] = (
    "missing_knowledge",
    "confusion",
    "procedure_error",
    "reasoning_gap",
    "careless",
    "material_failure",
)

# 错误标签 → 再教学动作（方案 Prompt 3 策略映射）
ERROR_STRATEGY: dict[str, str] = {
    "missing_knowledge": "prerequisite_review",
    "confusion": "contrast_case",
    "procedure_error": "worked_example",
    "reasoning_gap": "minimal_explanation",
    "careless": "self_check_list",
    "material_failure": "simpler_source",
}

#: 交错练习前置：概念达到该状态才参与交错（有目的交错——先基础后混合）
INTERLEAVE_READY_STATES = {"practicing", "provisional", "mastered"}


class PracticeLoopError(ValueError):
    """非法输入（未知步骤/错误标签）。"""


@dataclass(frozen=True, slots=True)
class AttemptSummary:
    """最近一次作答摘要（策略决策输入；不依赖 ORM）。"""

    correct: bool | None  # 三态
    confidence: int | None = None
    hints_used: int = 0
    error_tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NextStepDecision:
    """下一步教学动作 + 理由（给 UI/报告解释，非黑盒）。"""

    next_step: str
    strategy: str
    rationale: str
    loop_complete: bool = False
    # 错误驱动时给出回退目标（如先决概念复查）
    retry_after_step: str | None = None


def _validate_tags(tags: tuple[str, ...]) -> None:
    for tag in tags:
        if tag not in ERROR_TAGS:
            raise PracticeLoopError(f"未知错误标签: {tag!r}（合法：{ERROR_TAGS}）")


def next_step(
    completed_steps: list[str],
    last_attempt: AttemptSummary | None = None,
) -> NextStepDecision:
    """决定下一步教学动作。

    规则：
    1. 未开始 → first_attempt（学生先尝试——方案 A 节：先努力提取再看解释）；
    2. 有未判分/进行中的作答且当前步骤是作答型步骤 → 保持（等待判分/提交）；
    3. 作答型步骤答错（带错误标签）→ 错误策略动作（回退重学）；答错无
       标签 → 同步骤重试（retry）；
    4. 作答型步骤答对/非作答步骤完成 → 骨架下一步；
    5. exit_ticket 完成 → loop_complete（加入复习队列由 Phase 6 接）。
    """
    for step in completed_steps:
        if step not in LOOP_STEPS and step not in ERROR_STRATEGY.values():
            raise PracticeLoopError(f"未知步骤: {step!r}")

    if not completed_steps:
        return NextStepDecision(
            next_step="first_attempt",
            strategy="retrieval_first",
            rationale="学生先尝试（可失败）——先提取后解释（测试效应）",
        )

    current = completed_steps[-1]

    # 作答型步骤：按最近作答证据分支
    answering_steps = {"first_attempt", "recall_practice", "faded_practice", "independent_practice", "exit_ticket"}
    if current in answering_steps and last_attempt is not None:
        if last_attempt.correct is False:
            tags = tuple(last_attempt.error_tags)
            _validate_tags(tags)
            if tags:
                primary = tags[0]
                action = ERROR_STRATEGY[primary]
                return NextStepDecision(
                    next_step=action,
                    strategy=f"reteach:{primary}",
                    rationale=f"错误类型 {primary} → {action}（按类型再教学，不重放答案）",
                    retry_after_step=current,
                )
            return NextStepDecision(
                next_step=current,
                strategy="retry",
                rationale="作答错误且无错误标签——同步骤重试（等待标注错误类型以定向再教学）",
            )
        if last_attempt.correct is None:
            return NextStepDecision(
                next_step=current,
                strategy="await_grading",
                rationale="作答未判分（三态）——等待判分，不推进（诚实状态）",
            )

    # 非作答步骤 / 答对：沿骨架前进
    if current == "exit_ticket":
        return NextStepDecision(
            next_step="exit_ticket",
            strategy="loop_complete",
            rationale="出口小测通过——本概念微循环完成（复习队列见 Phase 6）",
            loop_complete=True,
        )
    # 骨架步骤优先判定（worked_example/contrast_case/minimal_explanation 与
    # 再教学动作同名——作为骨架当前步时按骨架前进；仅**非骨架**的纯再教学
    # 动作（prerequisite_review/self_check_list/simpler_source）走重试回退）
    try:
        index = LOOP_STEPS.index(current)
    except ValueError:
        index = None
    if index is None:
        if current in ERROR_STRATEGY.values():
            # 再教学动作完成 → 回到出错的那一步重试
            # 调用方应把 retry_after_step 记在事件 payload；此处回到作答型序列
            return NextStepDecision(
                next_step="recall_practice",
                strategy="retry_after_reteach",
                rationale="再教学动作完成——回到闭卷回忆重试（支架较出错步骤更细）",
            )
        raise PracticeLoopError(f"未知步骤: {current!r}")
    if index >= len(LOOP_STEPS) - 1:  # pragma: no cover - exit_ticket 已在上方分支
        return NextStepDecision(
            next_step="exit_ticket", strategy="loop_complete",
            rationale="骨架终点", loop_complete=True,
        )
    nxt = LOOP_STEPS[index + 1]
    return NextStepDecision(
        next_step=nxt,
        strategy="advance",
        rationale=f"步骤完成——骨架前进到 {nxt}",
    )


@dataclass(frozen=True, slots=True)
class ConceptForInterleave:
    concept_id: str
    state: str  # MASTERY_STATES 之一
    confusable_with: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InterleavePlan:
    """交错出题序：达到基础掌握的概念按易混淆组交替（有目的交错）。"""

    order: tuple[str, ...]
    rationale: str
    skipped: tuple[str, ...] = field(default=())


def interleave_queue(concepts: list[ConceptForInterleave]) -> InterleavePlan:
    """相似概念有目的交错（方案 C 节：非随机混合——先基础后交错）。

    规则：仅 INTERLEAVE_READY_STATES 概念参与；未达标概念单独列出
    （skipped，走各自微循环）；参与概念按「易混淆邻居在场时交替出列」
    贪心交错（相邻不同概念且优先易混淆对相邻，促使「何时用哪个」判断）。
    """
    ready = [c for c in concepts if c.state in INTERLEAVE_READY_STATES]
    skipped = tuple(c.concept_id for c in concepts if c.state not in INTERLEAVE_READY_STATES)
    if not ready:
        return InterleavePlan(
            order=(), skipped=skipped,
            rationale="无概念达到基础掌握——先完成各自微循环再交错",
        )

    pending = list(ready)
    order: list[str] = []
    while pending:
        current = pending.pop(0)
        order.append(current.concept_id)
        # 易混淆邻居紧接着出（交替判断训练），出队后放回队尾
        for neighbor_id in current.confusable_with:
            neighbor = next((c for c in pending if c.concept_id == neighbor_id), None)
            if neighbor is not None:
                pending.remove(neighbor)
                pending.append(neighbor)
    return InterleavePlan(
        order=tuple(order),
        skipped=skipped,
        rationale="达到基础掌握的概念按易混淆关系交替出列（有目的交错，非随机）",
    )
