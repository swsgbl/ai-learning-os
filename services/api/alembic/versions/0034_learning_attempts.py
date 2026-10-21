"""learning-attempts: M16-002 作答证据（升级方案 Phase 2）。

Revision ID: 0034_learning_attempts
Revises: 0033_learning_contracts
Create Date: 2026-10-22

语义：
- learning_attempts：每次作答的完整证据——判分三态（correct 可空 =
  未判分，不折算）、提交前信心 1-5、耗时、提示次数、独立/延迟/迁移
  标记、错误类型标签、作答上下文（题型/阶段，供「不同上下文」判定）。
- ConceptMastery 是可重算派生视图（domain/mastery.py derive_mastery），
  **不落表**——天然与原始证据一致，避免缓存同步负担。
- 纯新增表，不触碰既有数据；downgrade 整表删除。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0034_learning_attempts"
down_revision = "0033_learning_contracts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_attempts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(32), nullable=True, index=True),
        sa.Column("sprint_id", sa.String(32), nullable=True, index=True),
        sa.Column("contract_id", sa.String(32), nullable=True, index=True),
        sa.Column("item_id", sa.String(64), nullable=False),
        sa.Column("concept_id", sa.String(64), nullable=False, index=True),
        sa.Column("response", sa.Text(), nullable=False, server_default=""),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("correct", sa.Boolean(), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("hints_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("independent", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("delayed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("transfer", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_tags", sa.JSON(), nullable=False),
        sa.Column("context", sa.String(64), nullable=False, server_default="practice"),
        sa.Column("grading_method", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("grader_version", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("learning_attempts")
