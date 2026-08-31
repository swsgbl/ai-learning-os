"""M3-06 Daily planner：今日学习计划的确定性生成（pack F「解释为什么被选中」）。

- 聚合 M3-03/04/05 三个投影（概念状态、误解候选、复习队列）+ 题库池，
  生成三类今日任务（验收「今日任务包含新学、复习和错题重测」）：
  review（复习到期）、mistake_retry（长期误解重测）、new_learning（薄弱概念新题）。
- 每个任务必须带 reason（验收「解释为什么被选中」）：引用可解释证据
  （逾期比例/误解置信度/概念掌握度），不虚构模型推断。
- 选择规则全部显式：
  review = next_review_at <= now 的到期项（M3-05 overdue 降序）；
  mistake_retry = confirmed 误解候选涉及的题目（升级长期画像需重测确认）；
  new_learning = 从未作答的题，薄弱概念优先（mastery 越低越前）→
  全新概念次之 → 难度升序（从易到难）。
- 同一题不重复入选（review 优先，其次误解重测，最后新学）；各类有配额。
- 实时投影不落库（ADR 29 同款）：同 (事件集, 题库, now) 恒等输出。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.learning_events import LearningEventStream
from app.domain.misconceptions import STATUS_CONFIRMED, derive_misconceptions
from app.domain.models import Paper
from app.domain.review_scheduler import derive_review_queue
from app.domain.student_state import derive_concept_states

KIND_REVIEW = "review"
KIND_MISTAKE_RETRY = "mistake_retry"
KIND_NEW_LEARNING = "new_learning"
DEFAULT_REVIEW_QUOTA = 10
DEFAULT_MISTAKE_QUOTA = 5
DEFAULT_NEW_QUOTA = 10
TITLE_MAX_LEN = 80


@dataclass(frozen=True, slots=True)
class PlanTask:
    """单个今日任务：kind + 选题 + 可解释的入选理由。"""

    kind: str
    question_id: str
    concept_ids: tuple[str, ...]
    title: str
    reason: str
    priority: float  # 组内排序键（逾期比例/误解置信度/薄弱程度）


@dataclass(frozen=True, slots=True)
class DailyPlan:
    generated_at: datetime
    plan_date: str  # YYYY-MM-DD（now 的 UTC 日期）
    tasks: tuple[PlanTask, ...]  # review → mistake_retry → new_learning 分组有序


def build_daily_plan(
    streams: Iterable[LearningEventStream],
    papers: Iterable[Paper],
    *,
    now: datetime,
    review_quota: int = DEFAULT_REVIEW_QUOTA,
    mistake_quota: int = DEFAULT_MISTAKE_QUOTA,
    new_quota: int = DEFAULT_NEW_QUOTA,
) -> DailyPlan:
    """聚合三个学生模型投影与题库池，生成今日计划（纯函数：同输入恒同输出）。"""
    streams = tuple(streams)
    now_utc = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    concept_states = derive_concept_states(streams, now=now_utc)
    misconceptions = derive_misconceptions(streams, now=now_utc)
    review_queue = derive_review_queue(streams, now=now_utc)

    answered: set[str] = set()
    for stream in streams:
        for event in stream.events:
            answered.add(event.question_id)

    knowledge_of: dict[str, tuple[str, ...]] = {}
    stem_of: dict[str, str] = {}
    difficulty_of: dict[str, int] = {}
    for paper in papers:
        for question in paper.questions:
            knowledge_of[question.id] = tuple(question.knowledge)
            stem_of[question.id] = question.stem
            difficulty_of[question.id] = question.difficulty

    used: set[str] = set()
    review_tasks: list[PlanTask] = []
    mistake_tasks: list[PlanTask] = []
    new_tasks: list[PlanTask] = []

    # 1) 复习：到期项（M3-05 已按 overdue 降序；同 ratio retry 优先）
    for item in review_queue.items:
        if len(review_tasks) >= review_quota:
            break
        if item.next_review_at > now_utc or item.question_id in used:
            continue
        mastery = max(
            (
                concept_states[c].mastery
                for c in item.concept_ids
                if c in concept_states
            ),
            default=0.0,
        )
        due = "已逾期" if item.overdue_ratio >= 1.0 else "今日到期"
        review_tasks.append(
            PlanTask(
                kind=KIND_REVIEW,
                question_id=item.question_id,
                concept_ids=item.concept_ids,
                title=stem_of.get(item.question_id, item.question_id)[:TITLE_MAX_LEN],
                reason=(
                    f"复习{due}：上次作答判定为{'正确' if item.last_correctness else '错误'}，"
                    f"计划间隔 {item.interval_days:.1f} 天（概念掌握度 {mastery:.0%}）"
                ),
                priority=item.overdue_ratio,
            )
        )
        used.add(item.question_id)

    # 2) 错题重测：confirmed 误解候选涉及的题目（长期画像需重测确认是否已修复）
    for candidate in sorted(
        misconceptions.values(), key=lambda c: c.confidence, reverse=True
    ):
        if len(mistake_tasks) >= mistake_quota:
            break
        if candidate.status != STATUS_CONFIRMED:
            continue
        for question_id in candidate.question_ids:
            if len(mistake_tasks) >= mistake_quota:
                break
            if question_id in used:
                continue
            mistake_tasks.append(
                PlanTask(
                    kind=KIND_MISTAKE_RETRY,
                    question_id=question_id,
                    concept_ids=(candidate.concept_id,),
                    title=stem_of.get(question_id, question_id)[:TITLE_MAX_LEN],
                    reason=(
                        f"误解候选已升级：错误答案「{candidate.pattern}」在 "
                        f"{candidate.independent_count} 道独立题中重复出现"
                        f"（置信度 {candidate.confidence:.0%}），重测确认是否已修复"
                    ),
                    priority=candidate.confidence,
                )
            )
            used.add(question_id)

    # 3) 新学：从未作答的题；薄弱概念优先（2-mastery 越大越薄弱），
    #    全新概念恒排已知概念之后（2.0 为薄弱度上限），同优先级按难度升序（从易到难）
    def _new_score(question_id: str) -> tuple[float, int]:
        concepts = knowledge_of.get(question_id, ())
        known = [c for c in concepts if c in concept_states]
        weakness = 2.0 - min(concept_states[c].mastery for c in known) if known else 2.0
        return (weakness, difficulty_of.get(question_id, 3))

    untouched = sorted(
        (
            question_id
            for question_id in knowledge_of
            if question_id not in answered and question_id not in used
        ),
        key=_new_score,
    )
    for question_id in untouched[:new_quota]:
        concepts = knowledge_of.get(question_id, ())
        known = [c for c in concepts if c in concept_states]
        if known:
            mastery = min(concept_states[c].mastery for c in known)
            reason = (
                f"薄弱概念新学：概念 {','.join(known)} 掌握度 {mastery:.0%}，"
                f"推荐未做过的新题巩固"
            )
        elif concepts:
            reason = f"新概念起步：{','.join(concepts)} 尚无学习记录，从易到难建立基础"
        else:
            reason = "新题练习：该题概念未标注，作为补充练习"
        new_tasks.append(
            PlanTask(
                kind=KIND_NEW_LEARNING,
                question_id=question_id,
                concept_ids=concepts,
                title=stem_of.get(question_id, question_id)[:TITLE_MAX_LEN],
                reason=reason,
                priority=_new_score(question_id)[0],
            )
        )

    return DailyPlan(
        generated_at=now_utc,
        plan_date=now_utc.date().isoformat(),
        tasks=(*review_tasks, *mistake_tasks, *new_tasks),
    )
