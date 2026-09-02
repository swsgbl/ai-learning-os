"""users: M9-01 多用户与认证基座。

Revision ID: 0022_users
Revises: 0021_eval_runs
Create Date: 2026-09-02
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0022_users"
down_revision = "0021_eval_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
