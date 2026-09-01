"""variant_question_drafts: M5-08 变式题生成草稿（审核队列，同 M5-05/06/07 模式）。

Revision ID: 0020_variant_question_drafts
Revises: 0019_course_generation_drafts
Create Date: 2026-09-01
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0020_variant_question_drafts"
down_revision = "0019_course_generation_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "variant_question_drafts",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending_review"),
        sa.Column("variants", sa.JSON(), nullable=False),
        sa.Column("variant_count", sa.Integer(), nullable=False),
        sa.Column("generation_note", sa.String(length=512), nullable=False),
        sa.Column("review_note", sa.String(length=512), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_variant_question_drafts_status", "variant_question_drafts", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_variant_question_drafts_status", table_name="variant_question_drafts")
    op.drop_table("variant_question_drafts")
