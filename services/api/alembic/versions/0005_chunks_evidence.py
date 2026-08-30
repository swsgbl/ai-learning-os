"""M1-06 chunks + evidence tables.

Revision ID: 0005_chunks_evidence
Revises: 0004_parse_metadata
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_chunks_evidence"
down_revision = "0004_parse_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chunks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("resource_id", sa.String(64),
                  sa.ForeignKey("resources.id", name="fk_chunks_resource_id_resources"), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("chunk_hash", sa.String(64), nullable=False),
        sa.Column("page_start", sa.Integer, nullable=True),
        sa.Column("page_end", sa.Integer, nullable=True),
        sa.Column("slide", sa.Integer, nullable=True),
        sa.Column("block_types", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("embedding_status", sa.String(32), nullable=False, server_default="pending"),
    )
    op.create_index("ix_chunks_resource_id", "chunks", ["resource_id"])
    op.create_table(
        "evidence",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("chunk_id", sa.String(64),
                  sa.ForeignKey("chunks.id", name="fk_evidence_chunk_id_chunks"), nullable=False),
        sa.Column("resource_id", sa.String(64),
                  sa.ForeignKey("resources.id", name="fk_evidence_resource_id_resources"), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=True),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("parser_name", sa.String(64), nullable=False),
        sa.Column("locator", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("snippet_hash", sa.String(64), nullable=False),
        sa.Column("license_state", sa.String(32), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evidence_chunk_id", "evidence", ["chunk_id"])
    op.create_index("ix_evidence_resource_id", "evidence", ["resource_id"])


def downgrade() -> None:
    op.drop_index("ix_evidence_resource_id", table_name="evidence")
    op.drop_index("ix_evidence_chunk_id", table_name="evidence")
    op.drop_table("evidence")
    op.drop_index("ix_chunks_resource_id", table_name="chunks")
    op.drop_table("chunks")
