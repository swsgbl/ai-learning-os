"""admin-audit: M9-04 用户角色 + 搜索归属 + 治理审计日志。

Revision ID: 0024_admin_audit
Revises: 0023_ownership
Create Date: 2026-09-02
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0024_admin_audit"
down_revision = "0023_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=16), nullable=False, server_default="learner"),
    )
    op.add_column("search_queries", sa.Column("owner_id", sa.String(length=32), nullable=True))
    op.create_index("ix_search_queries_owner_id", "search_queries", ["owner_id"])
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor_id", sa.String(length=32), nullable=True),
        sa.Column("actor_username", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_search_queries_owner_id", table_name="search_queries")
    op.drop_column("search_queries", "owner_id")
    op.drop_column("users", "role")
