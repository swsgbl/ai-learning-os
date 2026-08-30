"""M1-03 resources table (upload dedup by content hash).

Revision ID: 0003_resources
Revises: 0002_sources
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003_resources"
down_revision = "0002_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resources",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("source_id", sa.String(64), nullable=True),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("media_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("access_state", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("license_state", sa.String(32), nullable=False, server_default="UNKNOWN"),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("content_type", sa.String(128), nullable=True),
        sa.Column("parse_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name=op.f("fk_resources_source_id_sources")
        ),
    )


def downgrade() -> None:
    op.drop_table("resources")
