"""M3-03 StudentConceptState：从学习事件流确定性重算概念掌握状态（pack F）。

- 可解释 BKT-like 增量更新，不训练深度模型（00 号文档「可解释 heuristic/BKT-like」）：
  答对 → mastery 向 1 逼近 ALPHA·w·(1-mastery)；答错 → 向 0 逼近 BETA·w·mastery。
  证据权重 w = attempt 折减（首答 1.0 / 重答 0.5）× 提示折减（预留：无提示 1.0）×
  难度系数（0.8 + 0.1·difficulty，难题做对证据更强）。
- confidence = n/(n+K)：独立证据越多越自信（K=4），有上界。
- forgetting_risk = 1 - exp(-Δh/τ)，τ = 24h·(1+4·mastery)：掌握越高遗忘越慢；
  以重算时刻 now 为锚——幂等语义是「同一 (事件集, now) 恒等输出」，不是与时钟无关。
- correctness=None（复核，ADR 21 三态）不计入证据；concept_ids 为空的事件不归属概念。
- 跨考试合并：全部事件按 occurred_at 时间序应用（BKT 更新依赖顺序，与输入流顺序无关）。
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.domain.learning_events import LearningEvent, LearningEventStream

ALPHA = 0.35  # 答对的学习增益
BETA = 0.45  # 答错的失稳系数（略大于 ALPHA：错题对掌握的打击更重）
CONFIDENCE_K = 4.0  # confidence = n/(n+K) 的饱和常数
RISK_TAU_HOURS = 24.0  # 零掌握概念的时间常数
RISK_MASTERY_GAIN = 4.0  # 满掌握概念的时间常数倍率（24h → 120h）
WEAK_MASTERY_THRESHOLD = 0.6  # 低于此值为薄弱概念（M3-07 选题输入）
ATTEMPT_WEIGHT_FIRST = 1.0
ATTEMPT_WEIGHT_RETRY = 0.5
DIFFICULTY_BASE = 0.8
DIFFICULTY_STEP = 0.1


@dataclass(frozen=True, slots=True)
class ConceptState:
    """单个概念的掌握状态：纯函数输出，可整体重建。"""

    concept_id: str
    mastery: float
    confidence: float
    forgetting_risk: float
    evidence_count: int
    correct_count: int
    wrong_count: int
    first_event_at: datetime
    last_event_at: datetime
    updated_at: datetime  # 重算锚点（= now）


@dataclass(slots=True)
class _Accumulator:
    """按时间序累积的事件证据；mastery 为未截断的原始值。"""

    mastery: float = 0.0
    evidence_count: int = 0
    correct_count: int = 0
    wrong_count: int = 0
    first_event_at: datetime | None = field(default=None)
    last_event_at: datetime | None = field(default=None)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _evidence_weight(event: LearningEvent) -> float:
    attempt_weight = ATTEMPT_WEIGHT_FIRST if event.attempt_number <= 1 else ATTEMPT_WEIGHT_RETRY
    hint_weight = 0.5 if event.hint_used else 1.0  # 提示功能未上线（M3-01 预留恒 False）
    difficulty_factor = DIFFICULTY_BASE + DIFFICULTY_STEP * event.difficulty
    return attempt_weight * hint_weight * difficulty_factor


def derive_concept_states(
    streams: Iterable[LearningEventStream], *, now: datetime
) -> dict[str, ConceptState]:
    """从学习事件流确定性重算全部概念状态（纯函数：同输入恒同输出）。"""
    events = sorted(
        (event for stream in streams for event in stream.events),
        key=lambda event: event.occurred_at,
    )
    accumulators: dict[str, _Accumulator] = {}
    for event in events:
        if event.correctness is None or not event.concept_ids:
            continue
        weight = _evidence_weight(event)
        for concept_id in event.concept_ids:
            acc = accumulators.setdefault(concept_id, _Accumulator())
            if acc.first_event_at is None:
                acc.first_event_at = event.occurred_at
            acc.last_event_at = event.occurred_at
            acc.evidence_count += 1
            if event.correctness:
                acc.correct_count += 1
                acc.mastery += ALPHA * weight * (1.0 - acc.mastery)
            else:
                acc.wrong_count += 1
                acc.mastery -= BETA * weight * acc.mastery

    now_utc = _utc(now)
    states: dict[str, ConceptState] = {}
    for concept_id, acc in accumulators.items():
        assert acc.first_event_at is not None and acc.last_event_at is not None
        mastery = min(1.0, max(0.0, acc.mastery))
        hours = max(0.0, (now_utc - _utc(acc.last_event_at)).total_seconds() / 3600.0)
        tau = RISK_TAU_HOURS * (1.0 + RISK_MASTERY_GAIN * mastery)
        states[concept_id] = ConceptState(
            concept_id=concept_id,
            mastery=mastery,
            confidence=acc.evidence_count / (acc.evidence_count + CONFIDENCE_K),
            forgetting_risk=1.0 - math.exp(-hours / tau),
            evidence_count=acc.evidence_count,
            correct_count=acc.correct_count,
            wrong_count=acc.wrong_count,
            first_event_at=acc.first_event_at,
            last_event_at=acc.last_event_at,
            updated_at=now_utc,
        )
    return states


def weak_concepts(states: Iterable[ConceptState]) -> list[str]:
    """薄弱概念：mastery 低于阈值，按 mastery 升序（最薄弱最先）。"""
    return [
        state.concept_id
        for state in sorted(states, key=lambda state: state.mastery)
        if state.mastery < WEAK_MASTERY_THRESHOLD
    ]
