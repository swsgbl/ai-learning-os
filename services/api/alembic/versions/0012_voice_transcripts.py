"""M4-02 voice transcripts: transcript persistence with privacy-gated audio storage.

Revision ID: 0012_voice_transcripts
Revises: 0011_misconceptions
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0012_voice_transcripts"
down_revision = "0011_misconceptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_transcripts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("audio_bytes", sa.Integer, nullable=False),
        sa.Column("audio_object_key", sa.String(256), nullable=True),
        sa.Column("audio_stored", sa.Boolean, nullable=False),
        sa.Column("exam_id", sa.String(64), nullable=True),
        sa.Column("question_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_voice_transcripts_exam_id", "voice_transcripts", ["exam_id"])


def downgrade() -> None:
    op.drop_index("ix_voice_transcripts_exam_id", table_name="voice_transcripts")
    op.drop_table("voice_transcripts")
