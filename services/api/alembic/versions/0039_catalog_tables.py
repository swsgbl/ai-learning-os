"""catalog: M18-001 课程目录四表（审计升级包 Phase 1 / Prompt 1）。

Revision ID: 0039_catalog_tables
Revises: 0038_review_memory_state
Create Date: 2026-10-23

语义：
- resource_providers：目录来源方（ChinaTextbook 等）——Mode A 元数据外链，
  policy_default=metadata_only（未知许可不默认共享原文）；
- resource_records：tree blob 归一化条目（学段/学科/年级/册别/分片清单）；
  (provider_id, source_path) 唯一——source_path 保真不变形；
- catalog_sync_runs：同步运行与差量计数（discovered/created/updated/
  unchanged/unavailable/policy_blocked/failed）；
- content_permissions：六态许可（unknown/metadata_only/user_private/
  approved_shared/review_required/blocked）——逐资源核验，不整仓打标。
- 纯新增表；downgrade 整表删除（依赖表顺序逆序）。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0039_catalog_tables"
down_revision = "0038_review_memory_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resource_providers",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("base_url", sa.String(255), nullable=False),
        sa.Column("provider_type", sa.String(32), nullable=False, server_default="git_tree"),
        sa.Column("sync_adapter", sa.String(32), nullable=False, server_default="github_tree"),
        sa.Column("policy_default", sa.String(32), nullable=False, server_default="metadata_only"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "resource_records",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("provider_id", sa.String(32), sa.ForeignKey("resource_providers.id"), nullable=False, index=True),
        sa.Column("source_path", sa.String(512), nullable=False),
        sa.Column("source_url", sa.String(1024), nullable=False, server_default=""),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("title_normalized", sa.String(255), nullable=False, server_default=""),
        sa.Column("education_stage", sa.String(32), nullable=False, server_default=""),
        sa.Column("subject", sa.String(64), nullable=False, server_default=""),
        sa.Column("grade", sa.String(32), nullable=False, server_default=""),
        sa.Column("semester", sa.String(32), nullable=False, server_default=""),
        sa.Column("volume", sa.String(32), nullable=False, server_default=""),
        sa.Column("edition_hint", sa.String(64), nullable=False, server_default=""),
        sa.Column("mime_type", sa.String(64), nullable=False, server_default=""),
        sa.Column("source_commit_sha", sa.String(40), nullable=False, server_default=""),
        sa.Column("parts_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider_id", "source_path", name="uq_resource_provider_path"),
    )
    op.create_table(
        "catalog_sync_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("provider_id", sa.String(32), sa.ForeignKey("resource_providers.id"), nullable=False, index=True),
        sa.Column("commit_sha", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("counters_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "content_permissions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("resource_id", sa.String(32), sa.ForeignKey("resource_records.id"), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="metadata_only"),
        sa.Column("license_spdx", sa.String(64), nullable=False, server_default=""),
        sa.Column("license_uri", sa.String(255), nullable=False, server_default=""),
        sa.Column("attribution_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("review_evidence_uri", sa.String(255), nullable=False, server_default=""),
        sa.Column("reviewed_by", sa.String(64), nullable=False, server_default=""),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("content_permissions")
    op.drop_table("catalog_sync_runs")
    op.drop_table("resource_records")
    op.drop_table("resource_providers")
