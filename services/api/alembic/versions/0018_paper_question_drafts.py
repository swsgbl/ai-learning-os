"""M5-06 paper question drafts: exam question extraction review queue.

Revision ID: 0018_paper_question_drafts
Revises: 0017_course_import_drafts
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0018_paper_question_drafts"
down_revision = "0017_course_import_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_question_drafts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending_review"),
        sa.Column("questions", sa.JSON, nullable=False),
        sa.Column("question_count", sa.Integer, nullable=False),
        sa.Column("extraction_note", sa.String(256), nullable=False),
        sa.Column("review_note", sa.String(512), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_paper_question_drafts_status", "paper_question_drafts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_paper_question_drafts_status", table_name="paper_question_drafts")
    op.drop_table("paper_question_drafts")
