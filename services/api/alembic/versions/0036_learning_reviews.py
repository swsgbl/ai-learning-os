"""learning-reviews: M16-005 间隔复习队列（升级方案 Phase 6）。

Revision ID: 0036_learning_reviews
Revises: 0035_assessment_blueprints
Create Date: 2026-10-22

语义：
- learning_reviews：每概念一条活跃复习记录（status: scheduled→
  presented→attempted→completed；completed 后新一轮新建记录）；
- 复习完成只能来自真实作答（attempt_id 关联判分作答）——「看了」
  不算（域层 review_schedule 状态机强制）；
- 纯新增表；downgrade 整表删除。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0036_learning_reviews"
down_revision = "0035_assessment_blueprints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_reviews",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(32), nullable=True, index=True),
        sa.Column("sprint_id", sa.String(32), nullable=True, index=True),
        sa.Column("contract_id", sa.String(32), nullable=True, index=True),
        sa.Column("concept_id", sa.String(64), nullable=False, index=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="scheduled"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("interval_days", sa.Integer(), nullable=True),
        sa.Column("attempt_id", sa.String(32), nullable=True),
        sa.Column("algorithm_version", sa.String(32), nullable=False, server_default="intervals-mvp-1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("learning_reviews")
