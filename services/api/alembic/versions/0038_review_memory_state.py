"""review-memory-state: M17-003 FSRS 记忆状态列（升级方案 Phase 6）。

Revision ID: 0038_review_memory_state
Revises: 0037_learning_source_refs
Create Date: 2026-10-22

语义：
- learning_reviews.memory_state（JSON，nullable）：FSRS Card.to_dict
  （stability/difficulty/step/state）——记忆状态跨轮演化持久；
- 模板调度器（intervals-mvp）恒 NULL（无记忆状态语义）；
- 纯新增列；downgrade 删列（既有行数据不迁移回填——FSRS 从新卡起步）。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0038_review_memory_state"
down_revision = "0037_learning_source_refs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "learning_reviews",
        sa.Column("memory_state", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("learning_reviews", "memory_state")
