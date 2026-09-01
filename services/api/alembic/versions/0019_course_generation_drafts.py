"""M5-07 course generation drafts: workflow draft review queue.

Revision ID: 0019_course_generation_drafts
Revises: 0018_paper_question_drafts
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0019_course_generation_drafts"
down_revision = "0018_paper_question_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "course_generation_drafts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("goal", sa.String(512), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending_review"),
        sa.Column("dag_version", sa.Integer, nullable=False),
        sa.Column("plan", sa.JSON, nullable=False),
        sa.Column("chapter_count", sa.Integer, nullable=False),
        sa.Column("generation_note", sa.String(512), nullable=False),
        sa.Column("review_note", sa.String(512), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_course_generation_drafts_status", "course_generation_drafts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_course_generation_drafts_status", table_name="course_generation_drafts")
    op.drop_table("course_generation_drafts")
