"""eval_runs: M6-01 评测运行记录表。

Revision ID: 0021_eval_runs
Revises: 0020_variant_question_drafts
Create Date: 2026-09-01
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0021_eval_runs"
down_revision = "0020_variant_question_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("agreement", sa.Float(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_eval_runs_kind", "eval_runs", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_eval_runs_kind", table_name="eval_runs")
    op.drop_table("eval_runs")
