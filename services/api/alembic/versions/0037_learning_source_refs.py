"""learning-source-refs: M16-008 来源证据（升级方案 Phase 5）。

Revision ID: 0037_learning_source_refs
Revises: 0036_learning_reviews
Create Date: 2026-10-22

语义：
- learning_source_refs：合同概念的来源证据片段（检索经既有 search
  registry——local-searxng/cloud-web；url/title/snippet/content_hash/
  license_hint/fetched_at）；
- LLM 出题时附来源片段（题目可追溯；无来源时如实无引用，不虚构）。
- 纯新增表；downgrade 整表删除。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0037_learning_source_refs"
down_revision = "0036_learning_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_source_refs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(32), nullable=True, index=True),
        sa.Column("contract_id", sa.String(32), nullable=False, index=True),
        sa.Column("concept_id", sa.String(64), nullable=False, index=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("title", sa.String(512), nullable=False, server_default=""),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("license_hint", sa.String(64), nullable=False, server_default="unknown"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("learning_source_refs")
