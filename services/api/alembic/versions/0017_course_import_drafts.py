"""M5-05 course import drafts: human review queue for course import.

Revision ID: 0017_course_import_drafts
Revises: 0016_search_queries
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0017_course_import_drafts"
down_revision = "0016_search_queries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "course_import_drafts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending_review"),
        sa.Column("source_resource_id", sa.String(64), nullable=False),
        sa.Column("source_license_state", sa.String(32), nullable=False),
        sa.Column("reuse_admission", sa.String(32), nullable=False),
        sa.Column("concepts", sa.JSON, nullable=False),
        sa.Column("resource_refs", sa.JSON, nullable=False),
        sa.Column("extraction_note", sa.String(256), nullable=False),
        sa.Column("review_note", sa.String(512), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_course_import_drafts_status", "course_import_drafts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_course_import_drafts_status", table_name="course_import_drafts")
    op.drop_table("course_import_drafts")
