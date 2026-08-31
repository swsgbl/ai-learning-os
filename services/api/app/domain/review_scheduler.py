"""M3-05 FSRS-like scheduler：错题复习调度的确定性投影（pack F 要求 4）。

- 调度单元 = 题目；从学习事件按时间序重放每题的答题历史，推导 next_review_at
  （验收「错题生成 next_review_at」）。实时投影不落库——与 LearningEvent 同款
  事件源语义，天然幂等（同输入恒同输出），无需物化表（ADR 29，YAGNI）。
- 从未答错的题不进队列（没有要巩固的误解）；最后答错 → retry，错过但最后答对
  → reinforce。复核事件（correctness=None）不驱动调度。
- FSRS-lite 间隔演化（全部显式常数，可解释可测）：
  答错 → 间隔重置为初始间隔（难度调制：难题忘得更快）；
  答对 → 间隔 × GROWTH；实际间隔相对计划间隔的比值决定增长修正——
  提前复习（<EARLY_THRESHOLD）增长打折（记忆未巩固）、延迟复习（>LATE_THRESHOLD）
  增长加成（间隔证明保持得久）——验收「提前/延迟复习策略可测试」。
- overdue_ratio = (now - last_seen)/interval：>1 已到期，越大越紧急，队列按其降序。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.domain.learning_events import LearningEvent, LearningEventStream

INITIAL_INTERVAL_DAYS = 1.0  # 答错后的初始复习间隔
DIFFICULTY_BASE_DAYS = 0.2  # 难度对初始间隔的调制步长（diff 3 → 1.0 天）
BASE_GROWTH = 2.0  # 正常回忆的间隔倍率
EARLY_THRESHOLD = 0.6  # 实际间隔 < 0.6×计划 → 提前复习
EARLY_PENALTY = 0.5  # 提前复习的增长折扣
LATE_THRESHOLD = 2.0  # 实际间隔 > 2×计划 → 延迟复习
LATE_BONUS = 1.25  # 延迟复习的增长加成
MAX_INTERVAL_DAYS = 180.0  # 间隔封顶
STATUS_RETRY = "retry"
STATUS_REINFORCE = "reinforce"


@dataclass(frozen=True, slots=True)
class ReviewItem:
    """单题复习任务：FSRS-lite 状态 + 下次复习时间。"""

    question_id: str
    concept_ids: tuple[str, ...]
    difficulty: int
    status: str  # retry（最后答错）| reinforce（错过但最后答对）
    interval_days: float
    last_correctness: bool
    last_seen_at: datetime
    next_review_at: datetime
    overdue_ratio: float  # (now - last_seen)/interval，>=1 已到期


@dataclass(frozen=True, slots=True)
class ReviewQueue:
    generated_at: datetime
    items: tuple[ReviewItem, ...]  # overdue_ratio 降序（同 ratio retry 优先）


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def initial_interval(difficulty: int) -> float:
    """初始复习间隔：难题忘得更快（difficulty 1 → 1.4 天，5 → 0.6 天）。"""
    return INITIAL_INTERVAL_DAYS + DIFFICULTY_BASE_DAYS * (3 - difficulty)


def derive_review_queue(
    streams: Iterable[LearningEventStream], *, now: datetime
) -> ReviewQueue:
    """从学习事件流确定性重放每题历史，生成复习队列（纯函数：同输入恒同输出）。"""
    per_question: dict[str, list[LearningEvent]] = {}
    for stream in streams:
        for event in stream.events:
            per_question.setdefault(event.question_id, []).append(event)

    now_utc = _utc(now)
    items: list[ReviewItem] = []
    for events in per_question.values():
        item = _replay(events, now_utc)
        if item is not None:
            items.append(item)
    items.sort(key=lambda item: (-item.overdue_ratio, item.status != STATUS_RETRY))
    return ReviewQueue(generated_at=now_utc, items=tuple(items))


def _replay(events: list[LearningEvent], now_utc: datetime) -> ReviewItem | None:
    """按时间序重放单题事件；无需复习（从未错/未判定）返回 None。"""
    ordered = sorted(events, key=lambda event: event.occurred_at)
    interval: float | None = None
    last_correct: bool | None = None
    last_seen: datetime | None = None
    concept_ids: tuple[str, ...] = ()
    difficulty = 3
    for event in ordered:
        if event.correctness is None:
            continue  # 复核不驱动调度
        concept_ids = event.concept_ids or concept_ids
        difficulty = event.difficulty
        if interval is None:
            if event.correctness:
                last_correct, last_seen = event.correctness, event.occurred_at
                continue  # 首答正确且从未错过：不进队列
            interval = initial_interval(event.difficulty)
        else:
            elapsed_days = (
                _utc(event.occurred_at) - _utc(last_seen)  # type: ignore[arg-type]
            ).total_seconds() / 86400.0
            if event.correctness:
                growth = BASE_GROWTH
                ratio = elapsed_days / interval
                if ratio < EARLY_THRESHOLD:
                    growth *= EARLY_PENALTY  # 提前复习：记忆未巩固
                elif ratio > LATE_THRESHOLD:
                    growth *= LATE_BONUS  # 延迟复习：间隔证明保持得久
                interval = min(interval * growth, MAX_INTERVAL_DAYS)
            else:
                interval = initial_interval(event.difficulty)  # 答错重置
        last_correct, last_seen = event.correctness, event.occurred_at
    if interval is None or last_correct is None or last_seen is None:
        return None
    if last_correct:
        status = STATUS_REINFORCE  # 错过但最后答对：巩固
    else:
        status = STATUS_RETRY
    last_seen_utc = _utc(last_seen)
    next_review_at = last_seen_utc + timedelta(days=interval)
    overdue = max(0.0, (now_utc - last_seen_utc).total_seconds() / 86400.0 / interval)
    return ReviewItem(
        question_id=ordered[-1].question_id,
        concept_ids=concept_ids,
        difficulty=difficulty,
        status=status,
        interval_days=interval,
        last_correctness=last_correct,
        last_seen_at=last_seen_utc,
        next_review_at=next_review_at,
        overdue_ratio=overdue,
    )
