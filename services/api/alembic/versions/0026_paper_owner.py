"""paper-owner: M10-03 试卷归属与可见性（papers 加 nullable + indexed 的 owner_id）。

Revision ID: 0026_paper_owner
Revises: 0025_draft_ownership
Create Date: 2026-09-03

归属语义（M10-03 第一段）：
- owner_id NULL = 系统公共卷（内置 seed 卷；存量数据迁移后保持 NULL，全部保留）；
- auth on 时 /papers/import 必须归属 current_owner_id；auth off 导入保持 NULL；
- GET /papers 与 POST /papers/{id}/exams 同一可见性规则：
  系统公共卷 + 当前用户自有卷（admin 亦不放大——他人私有导入卷不可见/404）。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0026_paper_owner"
down_revision = "0025_draft_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("papers", sa.Column("owner_id", sa.String(length=32), nullable=True))
    op.create_index("ix_papers_owner_id", "papers", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_papers_owner_id", table_name="papers")
    op.drop_column("papers", "owner_id")
