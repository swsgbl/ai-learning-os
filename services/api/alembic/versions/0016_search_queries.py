"""M5-01 search queries: recorded search executions (plan/results/skipped reasons).

Revision ID: 0016_search_queries
Revises: 0015_voice_trace_spans
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0016_search_queries"
down_revision = "0015_voice_trace_spans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_queries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("query", sa.String(512), nullable=False),
        sa.Column("providers_requested", sa.JSON, nullable=False),
        sa.Column("providers_skipped", sa.JSON, nullable=False),
        sa.Column("result_count", sa.Integer, nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=False),
        sa.Column("results", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("search_queries")
