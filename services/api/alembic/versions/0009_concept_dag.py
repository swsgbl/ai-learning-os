"""M3-02 concept DAG tables: concepts, concept_dag_versions, concept_edges.

Revision ID: 0009_concept_dag
Revises: 0008_question_difficulty
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009_concept_dag"
down_revision = "0008_question_difficulty"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "concepts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("canonical_name", sa.String(256), nullable=False),
        sa.Column("aliases", sa.JSON, nullable=False),
        sa.Column("subject", sa.String(64), nullable=False),
        sa.Column("description", sa.String(2048), nullable=False),
        sa.Column("difficulty", sa.Integer, nullable=False, server_default="3"),
        sa.Column("parent_id", sa.String(64), nullable=True),
        sa.Column("evidence_ids", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "concept_dag_versions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("note", sa.String(512), nullable=False),
        sa.Column("nodes", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("version", name="uq_concept_dag_versions_version"),
    )
    op.create_table(
        "concept_edges",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("version_id", sa.String(64), sa.ForeignKey("concept_dag_versions.id"), nullable=False),
        sa.Column("prerequisite_id", sa.String(64), sa.ForeignKey("concepts.id"), nullable=False),
        sa.Column("concept_id", sa.String(64), sa.ForeignKey("concepts.id"), nullable=False),
        sa.UniqueConstraint("version_id", "prerequisite_id", "concept_id", name="uq_concept_edges_triple"),
    )
    op.create_index("ix_concept_edges_version_id", "concept_edges", ["version_id"])
    op.create_index("ix_concept_edges_prerequisite_id", "concept_edges", ["prerequisite_id"])
    op.create_index("ix_concept_edges_concept_id", "concept_edges", ["concept_id"])


def downgrade() -> None:
    op.drop_index("ix_concept_edges_concept_id", table_name="concept_edges")
    op.drop_index("ix_concept_edges_prerequisite_id", table_name="concept_edges")
    op.drop_index("ix_concept_edges_version_id", table_name="concept_edges")
    op.drop_table("concept_edges")
    op.drop_table("concept_dag_versions")
    op.drop_table("concepts")
