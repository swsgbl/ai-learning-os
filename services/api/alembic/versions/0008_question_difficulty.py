"""M3-01 question difficulty column for learning events.

Revision ID: 0008_question_difficulty
Revises: 0007_question_score
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008_question_difficulty"
down_revision = "0007_question_score"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("questions", sa.Column("difficulty", sa.Integer, nullable=False, server_default="3"))


def downgrade() -> None:
    op.drop_column("questions", "difficulty")
