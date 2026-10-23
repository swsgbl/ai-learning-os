"""question-lifecycle: M18-007 题目发布门（审计 Prompt 6 / Phase 5）。

Revision ID: 0043_question_lifecycle
Revises: 0042_concept_candidates
Create Date: 2026-10-24

语义：
- question_lifecycle：生成题生命周期（candidate→published→retired）。
  生成题入池即 candidate（练习可用）；等值前后测蓝图只收 published
  （人工发布门——未验证题目不进正式考试，Prompt 6 红线）；
- 无行 = legacy published 语义（历史题与既有蓝图零破坏——门只对
  新生成题生效）。
- 纯新增表；downgrade 整表删除。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0043_question_lifecycle"
down_revision = "0042_concept_candidates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "question_lifecycle",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("question_id", sa.String(64), nullable=False, index=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="candidate"),
        sa.Column("verify_method", sa.String(32), nullable=False, server_default=""),
        sa.Column("reviewed_by", sa.String(64), nullable=False, server_default=""),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("question_id", name="uq_question_lifecycle_question"),
    )


def downgrade() -> None:
    op.drop_table("question_lifecycle")
