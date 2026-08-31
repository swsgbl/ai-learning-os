"""M3-07 选题策略：下一次练习的题目选择（可解释三因素）。

验收（pack F）：第一次考试后的错题会影响第二日学习任务和选题；
backlog：第二次任务受弱概念、难度和历史错题影响。

三因素显式分层，全部引用可解释证据（ADR 30 延续，不虚构模型推断）：
1. retry 历史错题重做：复习队列中最后答错的题（status=retry），逾期最重的
   优先——刚答错的题第二次任务即重做，不看到期（区别于 M3-06 复习通道）；
2. weak 薄弱概念补强：mastery < 0.6 概念的未作答新题，概念越弱越优先；
   同优先级难度升序（弱概念配易题——难度影响选题之一）；
3. advanced 强概念提升：mastery >= 0.6 概念的未作答难题（difficulty >= 3），
   掌握度越高越先挑战、难度降序（高掌握配高难——难度影响选题之二）。

同一题不重复入选（retry > weak > advanced）；实时投影不落库，
同 (事件集, 题库, now) 恒等输出。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.learning_events import LearningEventStream
from app.domain.models import Paper
from app.domain.review_scheduler import derive_review_queue
from app.domain.student_state import WEAK_MASTERY_THRESHOLD, derive_concept_states

KIND_RETRY = "retry"
KIND_WEAK = "weak"
KIND_ADVANCED = "advanced"
DEFAULT_RETRY_QUOTA = 4
DEFAULT_WEAK_QUOTA = 4
DEFAULT_ADVANCED_QUOTA = 3
STRONG_MIN_DIFFICULTY = 3
REASON_MAX_LEN = 200


@dataclass(frozen=True, slots=True)
class SelectionItem:
    """单个选题：kind + 题目 + 可解释的入选理由。"""

    kind: str
    question_id: str
    concept_ids: tuple[str, ...]
    reason: str
    priority: float  # 组内排序键（逾期比例/薄弱度/掌握度）


@dataclass(frozen=True, slots=True)
class Selection:
    generated_at: datetime
    items: tuple[SelectionItem, ...]  # retry → weak → advanced 分组有序


def build_selection(
    streams: Iterable[LearningEventStream],
    papers: Iterable[Paper],
    *,
    now: datetime,
    retry_quota: int = DEFAULT_RETRY_QUOTA,
    weak_quota: int = DEFAULT_WEAK_QUOTA,
    advanced_quota: int = DEFAULT_ADVANCED_QUOTA,
) -> Selection:
    """聚合学生模型投影与题库池，生成下一次练习的选题（纯函数：同输入恒同输出）。"""
    streams = tuple(streams)
    now_utc = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    states = derive_concept_states(streams, now=now_utc)
    queue = derive_review_queue(streams, now=now_utc)
    answered = {
        event.question_id for stream in streams for event in stream.events
    }

    concepts_of: dict[str, tuple[str, ...]] = {}
    difficulty_of: dict[str, int] = {}
    for paper in papers:
        for question in paper.questions:
            concepts_of[question.id] = tuple(question.knowledge)
            difficulty_of[question.id] = question.difficulty

    used: set[str] = set()
    retry_items: list[SelectionItem] = []
    weak_items: list[SelectionItem] = []
    advanced_items: list[SelectionItem] = []

    # 1) 历史错题重做：最后答错的复习项（status=retry），逾期最重优先；
    #    选题不受到期约束——第二次任务即重做刚错的题
    for item in queue.items:
        if len(retry_items) >= retry_quota:
            break
        if item.last_correctness or item.question_id in used:
            continue
        overdue = "，已逾期" if item.overdue_ratio >= 1.0 else ""
        retry_items.append(
            SelectionItem(
                kind=KIND_RETRY,
                question_id=item.question_id,
                concept_ids=item.concept_ids,
                reason=(
                    f"历史错题重做：上次作答判定为错误，计划间隔 "
                    f"{item.interval_days:.1f} 天{overdue}，重做巩固"
                )[:REASON_MAX_LEN],
                priority=item.overdue_ratio,
            )
        )
        used.add(item.question_id)

    # 2) 薄弱概念补强：weak 概念（mastery < 0.6）的未作答新题；
    #    最弱概念优先，同优先级难度升序（弱概念配易题）
    weak_candidates: list[tuple[float, int, str]] = []
    for question_id, concepts in concepts_of.items():
        if question_id in answered or question_id in used:
            continue
        known_weak = [
            c for c in concepts
            if c in states and states[c].mastery < WEAK_MASTERY_THRESHOLD
        ]
        if known_weak:
            weakness = max(
                WEAK_MASTERY_THRESHOLD - states[c].mastery for c in known_weak
            )
            weak_candidates.append((-weakness, difficulty_of[question_id], question_id))
    weak_candidates.sort()
    for negative_weakness, difficulty, question_id in weak_candidates[:weak_quota]:
        concepts = concepts_of[question_id]
        mastery = min(
            states[c].mastery
            for c in concepts
            if c in states and states[c].mastery < WEAK_MASTERY_THRESHOLD
        )
        weak_items.append(
            SelectionItem(
                kind=KIND_WEAK,
                question_id=question_id,
                concept_ids=concepts,
                reason=(
                    f"薄弱概念补强：概念 {','.join(sorted(set(concepts)))} "
                    f"掌握度 {mastery:.0%} 低于阈值 "
                    f"{WEAK_MASTERY_THRESHOLD:.0%}，先练难度 {difficulty} 的基础题巩固"
                )[:REASON_MAX_LEN],
                priority=-negative_weakness,
            )
        )
        used.add(question_id)

    # 3) 强概念提升：mastery >= 0.6 概念的未作答难题（difficulty >= 3）；
    #    掌握度降序（最强的先挑战）、难度降序（高掌握配高难）
    advanced_candidates: list[tuple[float, int, str]] = []
    for question_id, concepts in concepts_of.items():
        if question_id in answered or question_id in used:
            continue
        if difficulty_of[question_id] < STRONG_MIN_DIFFICULTY:
            continue
        known_strong = [
            c for c in concepts
            if c in states and states[c].mastery >= WEAK_MASTERY_THRESHOLD
        ]
        if known_strong:
            mastery = max(states[c].mastery for c in known_strong)
            advanced_candidates.append((-mastery, -difficulty_of[question_id], question_id))
    advanced_candidates.sort()
    for negative_mastery, negative_difficulty, question_id in advanced_candidates[:advanced_quota]:
        concepts = concepts_of[question_id]
        mastery = max(
            states[c].mastery
            for c in concepts
            if c in states and states[c].mastery >= WEAK_MASTERY_THRESHOLD
        )
        advanced_items.append(
            SelectionItem(
                kind=KIND_ADVANCED,
                question_id=question_id,
                concept_ids=concepts,
                reason=(
                    f"强概念提升：概念 {','.join(sorted(set(concepts)))} "
                    f"掌握度 {mastery:.0%} 已达标，挑战难度 "
                    f"{difficulty_of[question_id]} 的进阶题"
                )[:REASON_MAX_LEN],
                priority=-negative_mastery,
            )
        )
        used.add(question_id)

    return Selection(
        generated_at=now_utc,
        items=(*retry_items, *weak_items, *advanced_items),
    )
