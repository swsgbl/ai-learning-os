"""user-ai-keys 扩展：个人 LLM 槽位可选 endpoint/model。

Revision ID: 0030_user_ai_key_endpoint_model
Revises: 0029_user_ai_keys
Create Date: 2026-10-19

语义：
- 个人密钥通常对应自己的云服务（DeepSeek/OpenAI 等 OpenAI 兼容网关），
  仅存密钥不足以路由——endpoint/model 为可选明文配置（非机密）；
- 空 = 沿用服务端全局 LLM 配置（同网关不同 key 场景）；
- 密钥本体仍加密存储（ciphertext 列不变）。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0030_user_ai_key_endpoint_model"
down_revision = "0029_user_ai_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_ai_keys", sa.Column("endpoint", sa.Text(), nullable=True))
    op.add_column("user_ai_keys", sa.Column("model", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("user_ai_keys", "model")
    op.drop_column("user_ai_keys", "endpoint")
