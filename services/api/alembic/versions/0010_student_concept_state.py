"""M3-03 student concept states: materialized per-concept mastery state.

Revision ID: 0010_student_state
Revises: 0009_concept_dag
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0010_student_state"
down_revision = "0009_concept_dag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "student_concept_states",
        sa.Column("concept_id", sa.String(64), primary_key=True),
        sa.Column("mastery", sa.Float, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("forgetting_risk", sa.Float, nullable=False),
        sa.Column("evidence_count", sa.Integer, nullable=False),
        sa.Column("correct_count", sa.Integer, nullable=False),
        sa.Column("wrong_count", sa.Integer, nullable=False),
        sa.Column("first_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("student_concept_states")
