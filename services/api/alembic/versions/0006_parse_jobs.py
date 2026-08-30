"""M1-07 parse_jobs table.

Revision ID: 0006_parse_jobs
Revises: 0005_chunks_evidence
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006_parse_jobs"
down_revision = "0005_chunks_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parse_jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("resource_id", sa.String(64),
                  sa.ForeignKey("resources.id", name="fk_parse_jobs_resource_id_resources"), nullable=False),
        sa.Column("parser_name", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
        sa.Column("last_error", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("resource_id", "parser_name", name="uq_parse_jobs_resource_parser"),
    )
    op.create_index("ix_parse_jobs_resource_id", "parse_jobs", ["resource_id"])


def downgrade() -> None:
    op.drop_index("ix_parse_jobs_resource_id", table_name="parse_jobs")
    op.drop_table("parse_jobs")
