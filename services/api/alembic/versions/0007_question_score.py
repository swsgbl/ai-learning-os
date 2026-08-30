"""M2-02 question score column for imported papers.

Revision ID: 0007_question_score
Revises: 0006_parse_jobs
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_question_score"
down_revision = "0006_parse_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("questions", sa.Column("score", sa.Float, nullable=False, server_default="1"))


def downgrade() -> None:
    op.drop_column("questions", "score")
