"""ingest: M18-003 用户私有导入（审计升级包 Phase 2 / Prompt 3）。

Revision ID: 0040_ingest_tables
Revises: 0039_catalog_tables
Create Date: 2026-10-23

语义：
- ingest_jobs：导入任务（状态机 queued→downloading→assembled→ready /
  incomplete_parts / failed）；(owner_id, resource_id) 唯一——同资源
  重复导入幂等返回既有 job；
- content_blobs：私有内容对象（owner_scope=user_private——用户隔离；
  object_key 带租户命名空间但授权不依赖 key 结构；sha256 完整性锚点）。
- 纯新增表；downgrade 整表删除。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0040_ingest_tables"
down_revision = "0039_catalog_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingest_jobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(32), nullable=False, index=True),
        sa.Column("resource_id", sa.String(32), sa.ForeignKey("resource_records.id"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("parts_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parts_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("assembled_object_key", sa.String(255), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "resource_id", name="uq_ingest_owner_resource"),
    )
    op.create_table(
        "content_blobs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(32), nullable=False, index=True),
        sa.Column("resource_id", sa.String(32), sa.ForeignKey("resource_records.id"), nullable=False),
        sa.Column("job_id", sa.String(32), sa.ForeignKey("ingest_jobs.id"), nullable=False),
        sa.Column("owner_scope", sa.String(24), nullable=False, server_default="user_private"),
        sa.Column("object_key", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(64), nullable=False, server_default="application/pdf"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("content_blobs")
    op.drop_table("ingest_jobs")
