"""assessment-blueprints: M16-004 等值前后测蓝图（升级方案 Phase 4）。

Revision ID: 0035_assessment_blueprints
Revises: 0034_learning_attempts
Create Date: 2026-10-22

语义：
- assessment_blueprints：合同的评估蓝图——每概念 pretest/posttest
  题 id 分配（等值不重叠：后测绝不复用前测原题）；complete=False
  如实记录覆盖缺口（不虚报）。
- 蓝图由域层 build_blueprint 纯函数构建；此处仅持久化。
- 纯新增表；downgrade 整表删除。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0035_assessment_blueprints"
down_revision = "0034_learning_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assessment_blueprints",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("contract_id", sa.String(32), nullable=False, index=True),
        sa.Column("owner_id", sa.String(32), nullable=True, index=True),
        sa.Column("concept_id", sa.String(64), nullable=False),
        sa.Column("pretest_item_ids", sa.JSON(), nullable=False),
        sa.Column("posttest_item_ids", sa.JSON(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("assessment_blueprints")
