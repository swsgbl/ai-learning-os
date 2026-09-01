"""M4-09 voice trace spans: per-stage latency observation (append-only).

Revision ID: 0015_voice_trace_spans
Revises: 0014_voice_answer_events
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0015_voice_trace_spans"
down_revision = "0014_voice_answer_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_trace_spans",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("exam_id", sa.String(64), nullable=True),
        sa.Column("question_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_voice_trace_spans_stage", "voice_trace_spans", ["stage"])


def downgrade() -> None:
    op.drop_index("ix_voice_trace_spans_stage", table_name="voice_trace_spans")
    op.drop_table("voice_trace_spans")
