"""sources table for Source Registry

Revision ID: 0002_sources
Revises: 0001_initial
Create Date: 2026-08-31
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_sources"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("authority_score", sa.Integer(), nullable=False),
        sa.Column("homepage", sa.String(length=1024), nullable=False),
        sa.Column("terms_url", sa.String(length=1024), nullable=True),
        sa.Column("robots_policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("rate_limit", sa.JSON(), nullable=False),
        sa.Column("trust_tier", sa.String(length=4), nullable=False),
        sa.Column("license_state", sa.String(length=32), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(length=1024), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
        sa.UniqueConstraint("name", name=op.f("uq_sources_name")),
    )


def downgrade() -> None:
    op.drop_table("sources")
