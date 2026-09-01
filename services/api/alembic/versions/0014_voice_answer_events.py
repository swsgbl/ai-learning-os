"""M4-05 voice answer events: normalized answer events with client idempotency key.

Revision ID: 0014_voice_answer_events
Revises: 0013_voice_sessions
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0014_voice_answer_events"
down_revision = "0013_voice_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_answer_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("exam_id", sa.String(64), nullable=False),
        sa.Column("question_id", sa.String(64), nullable=False),
        sa.Column("normalized_answer", sa.String(256), nullable=True),
        sa.Column("intent", sa.String(32), nullable=False),
        sa.Column("transcript", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("accepted", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_voice_answer_events_session_id", "voice_answer_events", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_voice_answer_events_session_id", table_name="voice_answer_events")
    op.drop_table("voice_answer_events")
