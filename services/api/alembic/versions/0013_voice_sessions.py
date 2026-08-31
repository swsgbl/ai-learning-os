"""M4-03 voice sessions: VoiceSession FSM persistence.

Revision ID: 0013_voice_sessions
Revises: 0012_voice_transcripts
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013_voice_sessions"
down_revision = "0012_voice_transcripts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("exam_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("question_index", sa.Integer, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_voice_sessions_exam_id", "voice_sessions", ["exam_id"])


def downgrade() -> None:
    op.drop_index("ix_voice_sessions_exam_id", table_name="voice_sessions")
    op.drop_table("voice_sessions")
