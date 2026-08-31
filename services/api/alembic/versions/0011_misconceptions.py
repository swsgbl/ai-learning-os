"""M3-04 misconception candidates: per-concept error-pattern candidates.

Revision ID: 0011_misconceptions
Revises: 0010_student_state
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011_misconceptions"
down_revision = "0010_student_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "misconception_candidates",
        sa.Column("concept_id", sa.String(64), primary_key=True),
        sa.Column("pattern", sa.String(64), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("independent_count", sa.Integer, nullable=False),
        sa.Column("occurrence_count", sa.Integer, nullable=False),
        sa.Column("question_ids", sa.JSON, nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_misconception_candidates_concept_id", "misconception_candidates", ["concept_id"])


def downgrade() -> None:
    op.drop_index("ix_misconception_candidates_concept_id", table_name="misconception_candidates")
    op.drop_table("misconception_candidates")
