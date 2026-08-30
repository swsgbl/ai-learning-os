"""M1-04 parse metadata columns on resources.

Revision ID: 0004_parse_metadata
Revises: 0003_resources
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004_parse_metadata"
down_revision = "0003_resources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("resources", sa.Column("parser_name", sa.String(64), nullable=True))
    op.add_column("resources", sa.Column("parse_metrics", sa.JSON(), nullable=True))
    op.add_column("resources", sa.Column("parse_error", sa.String(1024), nullable=True))


def downgrade() -> None:
    op.drop_column("resources", "parse_error")
    op.drop_column("resources", "parse_metrics")
    op.drop_column("resources", "parser_name")
