"""llm-provider-profiles: M15-018 多厂商 LLM 密钥库（每用户多配置+激活切换）。

Revision ID: 0031_llm_provider_profiles
Revises: 0030_user_ai_key_endpoint_model
Create Date: 2026-10-20

语义：
- user_llm_profiles 每用户最多 8 条（应用层校验）：label（用户命名，如
  "DeepSeek"/"OpenAI"）+ 密钥密文（SecretBox）+ 可选 endpoint/model +
  is_active（每用户恰一条激活，应用层保证）；
- 判分 resolver 取该用户 active profile（无 profile = 全局配置，语义不变）；
- 迁移：既有 user_ai_keys 的 llm 槽位行迁入新表为首个 active profile
  （label 固定「默认」）；迁移后旧行删除（表保留——BYOK 语音等未来槽位）。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0031_llm_provider_profiles"
down_revision = "0030_user_ai_key_endpoint_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_llm_profiles",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(32), nullable=False, index=True),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("last4", sa.String(8), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    # 存量迁移：user_ai_keys 的 llm 行 → 首个 active profile（SQL 直迁，幂等）
    op.execute(
        """
        INSERT INTO user_llm_profiles (id, user_id, label, ciphertext, last4, endpoint, model, is_active, created_at)
        SELECT
            'ulp_' || substr(u.user_id || '_llm', 1, 28),
            u.user_id,
            '默认',
            u.ciphertext,
            u.last4,
            u.endpoint,
            u.model,
            TRUE,
            u.updated_at
        FROM user_ai_keys u
        WHERE u.slot = 'llm'
          AND NOT EXISTS (SELECT 1 FROM user_llm_profiles p WHERE p.user_id = u.user_id)
        """
    )
    op.execute("DELETE FROM user_ai_keys WHERE slot = 'llm'")


def downgrade() -> None:
    # 回滚策略：profile 数据回迁 user_ai_keys（仅 active 首条），表删除
    op.execute(
        """
        INSERT INTO user_ai_keys (user_id, slot, ciphertext, last4, endpoint, model, updated_at)
        SELECT p.user_id, 'llm', p.ciphertext, p.last4, p.endpoint, p.model, p.created_at
        FROM user_llm_profiles p
        WHERE p.is_active
          AND NOT EXISTS (SELECT 1 FROM user_ai_keys u WHERE u.user_id = p.user_id AND u.slot = 'llm')
        """
    )
    op.drop_table("user_llm_profiles")
