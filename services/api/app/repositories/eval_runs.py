"""M6-01 评测运行记录持久化：记录/回查（评测是运行记录，无审核流程）。"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import EvalRunRow
from app.repositories.memory import utc_now


class EvalRunRepository:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def record(
        self,
        kind: str,
        report: dict,
        agreement: float | None = None,
        case_count: int | None = None,
    ) -> dict:
        """落库一条评测运行；agreement/case_count 缺省取 report 的头部指标。

        grading 用 report["agreement"]/report["case_count"]；voice 头部分数是
        accuracy、citation 抽样数是 sampled_count——显式传入同名列
        （列语义 = 头部分数 + 参与判定的样本数）。
        """
        async with self._sessionmaker() as session, session.begin():
            row = EvalRunRow(
                kind=kind,
                rule_version=report["rule_versions"]["eval"],
                case_count=case_count if case_count is not None else report["case_count"],
                agreement=agreement if agreement is not None else report["agreement"],
                report=report,
                created_at=self._clock(),
            )
            session.add(row)
            await session.flush()
            return self._view(row)

    async def list_runs(self, kind: str | None = None, limit: int = 20) -> list[dict]:
        query = select(EvalRunRow).order_by(EvalRunRow.id.desc()).limit(limit)
        if kind is not None:
            query = query.where(EvalRunRow.kind == kind)
        async with self._sessionmaker() as session:
            rows = (await session.execute(query)).scalars().all()
            return [self._view(row) for row in rows]

    async def get(self, run_id: int) -> dict | None:
        async with self._sessionmaker() as session:
            row = await session.get(EvalRunRow, run_id)
            if row is None:
                return None
            return self._view(row)

    def _view(self, row: EvalRunRow) -> dict:
        return {
            "id": row.id,
            "kind": row.kind,
            "rule_version": row.rule_version,
            "case_count": row.case_count,
            "agreement": row.agreement,
            "report": dict(row.report or {}),
            "created_at": row.created_at.isoformat(),
        }
