"""course-units: M18-005 章节树候选（审计升级包 Phase 4 / Prompt 5）。

Revision ID: 0041_course_units
Revises: 0040_ingest_tables
Create Date: 2026-10-23

语义：
- course_units：教材章节树（PDF outline 候选 → 人工确认两态）；
  (resource_id, sequence) 唯一——重提取整树替换；页锚点保留。
- 纯新增表；downgrade 整表删除。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0041_course_units"
down_revision = "0040_ingest_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "course_units",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("resource_id", sa.String(64), sa.ForeignKey("resources.id"), nullable=False, index=True),
        sa.Column("parent_id", sa.String(32), sa.ForeignKey("course_units.id"), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("unit_type", sa.String(16), nullable=False, server_default="chapter"),
        sa.Column("level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="candidate"),
        sa.Column("anchor", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("resource_id", "sequence", name="uq_course_unit_resource_seq"),
    )


def downgrade() -> None:
    op.drop_table("course_units")
