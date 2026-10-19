"""user-ai-keys: M15-014 个人 AI 密钥（BYOK）。

Revision ID: 0029_user_ai_keys
Revises: 0028_ai_provider_secrets
Create Date: 2026-10-19

语义：
- user_ai_keys 每用户每槽位一行（user_id+slot 联合 PK）；
- ciphertext 与 ai_provider_secrets 同款加密（AUTH_SECRET 派生密钥
  HKDF+AES-256-GCM），明文任何形态不落库；last4 脱敏展示；
- 请求装配优先级：个人密钥 > 全局在线槽位 > 部署 env；
- downgrade 整表删除（无业务引用）。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0029_user_ai_keys"
down_revision = "0028_ai_provider_secrets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_ai_keys",
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("slot", sa.String(length=32), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("last4", sa.String(length=8), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "slot"),
    )
    op.create_index("ix_user_ai_keys_user_id", "user_ai_keys", ["user_id"])


def downgrade() -> None:
    op.drop_table("user_ai_keys")
