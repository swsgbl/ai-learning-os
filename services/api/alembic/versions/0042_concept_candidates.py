"""concept-candidates: M18-006 概念/边候选（审计升级包 Phase 4 / Prompt 5 后半）。

Revision ID: 0042_concept_candidates
Revises: 0041_course_units
Create Date: 2026-10-23

语义：
- concept_candidates：LLM 从教材章节提取的概念节点候选（两态确认——
  确认后经既有 M3-02 DAG 发布管道进正式图；候选永不直接成为事实）；
- concept_edge_candidates：typed relation 候选（prerequisite_of/part_of/
  similar_to/often_confused_with/applies_to——正式 DAG 本期只消费
  prerequisite_of，其余留待 DAG 模型扩展）。
- 纯新增表；downgrade 整表删除。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0042_concept_candidates"
down_revision = "0041_course_units"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "concept_candidates",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("resource_id", sa.String(64), sa.ForeignKey("resources.id"), nullable=False, index=True),
        sa.Column("owner_id", sa.String(32), nullable=True, index=True),
        sa.Column("concept_id", sa.String(64), nullable=False),
        sa.Column("canonical_name", sa.String(256), nullable=False),
        sa.Column("subject", sa.String(64), nullable=False, server_default=""),
        sa.Column("description", sa.String(2048), nullable=False, server_default=""),
        sa.Column("difficulty", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="candidate"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("resource_id", "concept_id", name="uq_concept_cand_resource_concept"),
    )
    op.create_table(
        "concept_edge_candidates",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("resource_id", sa.String(64), sa.ForeignKey("resources.id"), nullable=False, index=True),
        sa.Column("owner_id", sa.String(32), nullable=True, index=True),
        sa.Column("from_concept", sa.String(64), nullable=False),
        sa.Column("to_concept", sa.String(64), nullable=False),
        sa.Column("relation", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="candidate"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "resource_id", "from_concept", "to_concept", "relation",
            name="uq_concept_edge_cand_natural",
        ),
    )


def downgrade() -> None:
    op.drop_table("concept_edge_candidates")
    op.drop_table("concept_candidates")
