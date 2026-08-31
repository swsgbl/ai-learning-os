"""M3-03 StudentConceptState repository：学生概念状态的物化存储。

重算语义 = 全量 replace：事件源（答案事件）append-only 永不修改，
状态表可随时从事件整体重建——跑两次行集合一致（更新幂等的存储保证）。
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import ExamSessionRow, StudentConceptStateRow
from app.domain.student_state import ConceptState
from app.repositories.memory import utc_now


def _to_db(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _from_db(value: datetime) -> datetime:
    """读回统一 UTC-aware：SQLite naive 补 UTC；asyncpg 本机时区 aware 转 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class StudentStateRepository:
    """Persistence for materialized per-concept student state. Shares the sessionmaker."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def list_exam_ids(self) -> list[str]:
        """重算扫描范围：全部考试会话（含未交卷——active 作答同样是学习证据）。"""
        async with self._sessionmaker() as session:
            rows = await session.execute(select(ExamSessionRow.exam_id))
            return list(rows.scalars().all())

    async def recompute(self, states: Iterable[ConceptState]) -> list[ConceptState]:
        """全量 replace：写入口径与重算函数输出一一对应，重复执行行集合一致。"""
        ordered = sorted(states, key=lambda state: state.concept_id)
        async with self._sessionmaker() as session, session.begin():
            await session.execute(delete(StudentConceptStateRow))
            session.add_all(
                StudentConceptStateRow(
                    concept_id=state.concept_id,
                    mastery=state.mastery,
                    confidence=state.confidence,
                    forgetting_risk=state.forgetting_risk,
                    evidence_count=state.evidence_count,
                    correct_count=state.correct_count,
                    wrong_count=state.wrong_count,
                    first_event_at=_to_db(state.first_event_at),
                    last_event_at=_to_db(state.last_event_at),
                    updated_at=_to_db(state.updated_at),
                )
                for state in ordered
            )
        return await self.list_states()

    async def list_states(self) -> list[ConceptState]:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(StudentConceptStateRow).order_by(StudentConceptStateRow.concept_id)
                )
            ).scalars().all()
            return [self._state_from_row(row) for row in rows]

    async def get_state(self, concept_id: str) -> ConceptState | None:
        async with self._sessionmaker() as session:
            row = await session.get(StudentConceptStateRow, concept_id)
            return None if row is None else self._state_from_row(row)

    @staticmethod
    def _state_from_row(row: StudentConceptStateRow) -> ConceptState:
        return ConceptState(
            concept_id=row.concept_id,
            mastery=row.mastery,
            confidence=row.confidence,
            forgetting_risk=row.forgetting_risk,
            evidence_count=row.evidence_count,
            correct_count=row.correct_count,
            wrong_count=row.wrong_count,
            first_event_at=_from_db(row.first_event_at),
            last_event_at=_from_db(row.last_event_at),
            updated_at=_from_db(row.updated_at),
        )
