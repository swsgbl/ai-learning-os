"""M16-005 间隔复习调度（升级方案 Phase 6 / Prompt 5）；M17-003 FSRS 接入。

两个调度器（settings.review_algorithm 选择，algorithm_version 随行记录
——复习数据可重算、算法版本清楚）：

- ``fsrs``（缺省，py-fsrs 6.x，MIT——license 审查 2026-10-22 通过）：
  记忆状态（stability/difficulty）跨轮持久在 learning_reviews.
  memory_state，逐次演化；正确性+信心映射 FSRS rating；
- ``intervals-mvp``（M16-005 模板档 +1/3/7/14/30 + 表现调整）——保留
  可回退；无 memory_state 语义。

方案边界不变：FSRS 只决定复习时机，不定义掌握（掌握由 attempts 证据
重算）；复习完成必须来自真实作答。状态机 scheduled → presented →
attempted → completed；只有 attempted（真实作答判分）才可 completed。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

#: 默认间隔档（天）——方案 Phase 6 模板；表现可跨档
DEFAULT_INTERVALS_DAYS: tuple[int, ...] = (1, 3, 7, 14, 30)
#: 独立答对且高信心 → 间隔增长系数（跳档增长）
ADVANCE_FACTOR = 1.6
ALGORITHM_VERSION = "intervals-mvp-1"
FSRS_ALGORITHM_VERSION = "fsrs-py-6.3"

REVIEW_STATUSES: tuple[str, ...] = ("scheduled", "presented", "attempted", "completed")


class ReviewScheduleError(ValueError):
    """非法调度输入。"""


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    """一次复习作答的结果（驱动下一轮排期）。"""

    correct: bool | None  # 三态：None=未判分 → 保持 presented 不排下一轮
    confidence: int | None = None
    independent: bool = True


@dataclass(frozen=True, slots=True)
class NextReview:
    concept_id: str
    due_in_days: int
    algorithm_version: str = ALGORITHM_VERSION
    note: str = ""
    #: FSRS 调度器的记忆状态（Card.to_dict）——跨轮持久；模板调度器 None
    memory_state: dict | None = None


def next_interval_days(
    previous_interval_days: int | None,
    outcome: ReviewOutcome,
) -> NextReview:
    """按最近间隔与作答表现给下一轮间隔。

    规则：
    - 首轮（无先前间隔）→ 最短档（1 天）；
    - 独立答对：先前间隔 × ADVANCE_FACTOR，向档位表对齐（取不小于该值
      的最近档；超表顶用计算值）；高信心（≥4）额外用满系数；
    - 非独立（提示下答对）→ 维持当前档（不长间隔）；
    - 答错 → 回最短档（1 天）——复习失败即近期重学信号；
    - 未判分 → 维持当前档但 note 标注（诚实：未判分不推进 completed）。
    """
    if outcome.correct is None:
        current = previous_interval_days or DEFAULT_INTERVALS_DAYS[0]
        return NextReview(
            concept_id="", due_in_days=current,
            note="未判分——保持当前档（completed 需真实判分作答）",
        )
    if outcome.correct is False:
        return NextReview(
            concept_id="", due_in_days=DEFAULT_INTERVALS_DAYS[0],
            note="复习失败——回最短档（触发定向再教学）",
        )
    if not outcome.independent:
        current = previous_interval_days or DEFAULT_INTERVALS_DAYS[0]
        return NextReview(
            concept_id="", due_in_days=current,
            note="提示下答对——维持当前档（不长间隔）",
        )
    high_confidence = outcome.confidence is not None and outcome.confidence >= 4
    base = previous_interval_days or DEFAULT_INTERVALS_DAYS[0]
    # 表现调整后为连续值（模板档只是默认起点；方案「算法根据表现调整」）
    grown = base * (ADVANCE_FACTOR if high_confidence else 1.3)
    return NextReview(
        concept_id="", due_in_days=max(1, round(grown)),
        note="独立答对" + ("（高信心跳档）" if high_confidence else ""),
    )



# ---------------------------------------------------------------- FSRS 调度器（M17-003）


def _rating_from_outcome(outcome: ReviewOutcome) -> int:
    """作答结果 → FSRS rating（1=Again 2=Hard 3=Good 4=Easy）。

    答错=Again；答对按信心分档（低信心 Hard、中 Good、高信心 Easy）。
    FSRS rating 是复习时机算法的输入，不是掌握度结论。
    """
    if outcome.correct is not True:
        return 1  # Again
    confidence = outcome.confidence or 3
    if confidence <= 2:
        return 2  # Hard
    if confidence >= 5:
        return 4  # Easy
    return 3  # Good


def next_review_fsrs(
    memory_state: dict | None,
    outcome: ReviewOutcome,
    *,
    last_review_at: datetime | None = None,
    now: datetime | None = None,
) -> NextReview:
    """FSRS 调度：memory_state（Card.to_dict）逐次演化 + 下一轮间隔。

    - 未判分 → 不演化不排期（诚实边界，与模板调度器一致）；
    - memory_state 为空 = 新卡（首复习）；
    - 返回的 memory_state 由调用方持久（learning_reviews.memory_state），
      下一轮 from_dict 恢复——记忆状态跨轮追踪是 FSRS 的核心价值。
    """
    from fsrs import Card, Rating, Scheduler

    if outcome.correct is None:
        return NextReview(
            concept_id="",
            due_in_days=0,
            note="未判分——不排期（completed 需真实判分作答）",
        )
    current = now or datetime.now(tz=timezone.utc)
    card = Card.from_dict(memory_state) if memory_state else Card()
    card, _log = Scheduler().review_card(
        card,
        Rating(_rating_from_outcome(outcome)),
        review_datetime=last_review_at or current,
    )
    interval_days = max(1, round((card.due - current).total_seconds() / 86400))
    return NextReview(
        concept_id="",
        due_in_days=interval_days,
        algorithm_version=FSRS_ALGORITHM_VERSION,
        note=f"fsrs（stability={card.stability or 0:.2f} difficulty={card.difficulty or 0:.2f}）",
        memory_state=card.to_dict(),
    )

def initial_schedule(
    concept_ids: list[str], now: datetime, intervals: tuple[int, ...] = DEFAULT_INTERVALS_DAYS
) -> list[tuple[str, datetime]]:
    """Sprint 结束（follow_up）时的初始复测队列：每概念最短档起步。"""
    return [(cid, now + timedelta(days=intervals[0])) for cid in concept_ids]


def review_status_transition(current: str, event: str) -> str:
    """复习状态机：scheduled→presented→attempted→completed。

    - 「presented」（展示给学习者）不等于 attempted；
    - 只有 attempted（提交作答）→ completed；
    - 未判分作答保持 attempted（不 completed——诚实边界）；
    - 任何状态可回 presented（重看）但 completed 仅经 attempted。
    """
    if current not in REVIEW_STATUSES:
        raise ReviewScheduleError(f"未知复习状态: {current!r}")
    legal = {
        "scheduled": {"presented"},
        "presented": {"attempted", "presented"},
        "attempted": {"completed", "presented"},
        "completed": {"presented"},  # 下一轮复习（新记录）——本记录可重看
    }
    if event not in legal[current]:
        raise ReviewScheduleError(f"非法复习状态转换: {current!r} → {event!r}")
    return event
