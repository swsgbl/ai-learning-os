"""M3-02 Concept DAG repository：版本化概念图的持久化（concepts + concept_edges + 版本行）。

发布语义：单事务内 upsert 概念本体（不删除，保留历史）、写新版本行（version 递增）、
插入该版本的先修边。历史版本的行永不变更——可版本化的存储保证。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import AuditLogRow, ConceptDagVersionRow, ConceptEdgeRow, ConceptRow
from app.domain.concept_dag import ConceptDag, ConceptEdge, ConceptNode, validate_dag
from app.repositories.audit import audit_insert_values
from app.repositories.memory import utc_now


def _iso_utc(value: datetime) -> str:
    """SQLite 存 naive UTC 字符串读回 naive；统一补 UTC 再序列化（与 exams 时间列同约定）。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


class ConceptDagRepository:
    """Persistence for versioned concept DAGs. Shares the async sessionmaker."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def publish(
        self,
        nodes: tuple[ConceptNode, ...],
        edges: tuple[ConceptEdge, ...],
        note: str = "",
        audit: dict | None = None,
    ) -> ConceptDag:
        """校验并发布新版本（不可变快照）；返回带版本号的完整图。"""
        validate_dag(nodes, edges)
        now = self._clock()
        async with self._sessionmaker() as session, session.begin():
            max_version = await session.scalar(
                select(func.max(ConceptDagVersionRow.version)).select_from(ConceptDagVersionRow)
            )
            version = (max_version or 0) + 1
            version_row = ConceptDagVersionRow(
                id=f"cdv_{uuid4().hex}",
                version=version,
                note=note,
                nodes=[
                    {
                        "id": node.id,
                        "canonical_name": node.canonical_name,
                        "aliases": list(node.aliases),
                        "subject": node.subject,
                        "description": node.description,
                        "difficulty": node.difficulty,
                        "parent_id": node.parent_id,
                        "evidence_ids": list(node.evidence_ids),
                    }
                    for node in nodes
                ],
                created_at=now.astimezone(UTC),
            )
            session.add(version_row)
            for node in nodes:
                await session.merge(
                    ConceptRow(
                        id=node.id,
                        canonical_name=node.canonical_name,
                        aliases=list(node.aliases),
                        subject=node.subject,
                        description=node.description,
                        difficulty=node.difficulty,
                        parent_id=node.parent_id,
                        evidence_ids=list(node.evidence_ids),
                        created_at=now.astimezone(UTC),
                    )
                )
            # PG 下 merge 的 upsert 排序可能晚于后续 insert，先 flush 保证 FK 可见
            await session.flush()
            for edge in edges:
                session.add(
                    ConceptEdgeRow(
                        id=f"ce_{uuid4().hex}",
                        version_id=version_row.id,
                        prerequisite_id=edge.prerequisite_id,
                        concept_id=edge.concept_id,
                    )
                )
            if audit is not None:
                # M9-05: 审计与发布同事务（失败即整体回滚）
                session.add(AuditLogRow(**audit_insert_values(audit, clock=self._clock)))
        return ConceptDag(
            version=version, note=note, nodes=nodes, edges=edges, created_at=now.isoformat()
        )

    async def latest(self) -> ConceptDag | None:
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(ConceptDagVersionRow)
                    .order_by(ConceptDagVersionRow.version.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return await self._assemble(session, row)

    async def get_version(self, version: int) -> ConceptDag | None:
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(ConceptDagVersionRow).where(ConceptDagVersionRow.version == version)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return await self._assemble(session, row)

    async def list_versions(self) -> list[dict]:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(ConceptDagVersionRow).order_by(ConceptDagVersionRow.version.desc())
                )
            )
            return [
                {
                    "version": row.version,
                    "note": row.note,
                    "created_at": _iso_utc(row.created_at),
                }
                for row in rows.scalars().all()
            ]

    async def _assemble(self, session: AsyncSession, row: ConceptDagVersionRow) -> ConceptDag:
        # 节点从版本行快照反序列化：概念本体可演进，历史版本必须看到当时的属性
        version_nodes = tuple(
            ConceptNode(
                id=node["id"],
                canonical_name=node["canonical_name"],
                aliases=tuple(node.get("aliases", ())),
                subject=node.get("subject", ""),
                description=node.get("description", ""),
                difficulty=node.get("difficulty", 3),
                parent_id=node.get("parent_id"),
                evidence_ids=tuple(node.get("evidence_ids", ())),
            )
            for node in row.nodes
        )
        edge_rows = (
            await session.execute(
                select(ConceptEdgeRow).where(ConceptEdgeRow.version_id == row.id)
            )
        ).scalars().all()
        return ConceptDag(
            version=row.version,
            note=row.note,
            nodes=version_nodes,
            edges=tuple(
                ConceptEdge(prerequisite_id=edge.prerequisite_id, concept_id=edge.concept_id)
                for edge in edge_rows
            ),
            created_at=_iso_utc(row.created_at),
        )
