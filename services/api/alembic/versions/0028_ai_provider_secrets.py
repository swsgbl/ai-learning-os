"""ai-provider-secrets: M15-008 AI 能力密钥治理（在线密钥加密落库）。

Revision ID: 0028_ai_provider_secrets
Revises: 0027_audit_chain
Create Date: 2026-10-19

语义：
- ai_provider_secrets 每槽位一行（slot PK：llm / asr_cloud / tts_cloud——
  白名单在路由层，数据库不设 CHECK 以免治理槽位演进必须改表）；
- ciphertext 为 AUTH_SECRET 派生密钥（HKDF 域分离）AES-256-GCM 的
  base64(iv ‖ ciphertext ‖ tag)——明文密钥任何形态不落库；
- last4 是密钥尾 4 位脱敏展示（治理页状态显示用）；
- downgrade 整表删除（密钥治理数据，无业务引用）。

与 ORM（app/db/orm.py AiProviderSecretRow）同构；Pydantic/pydantic-settings
与加解密在应用层（app.core.secrets / app.api.routes.ai_providers）。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0028_ai_provider_secrets"
down_revision = "0027_audit_chain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_secrets",
        sa.Column("slot", sa.String(length=32), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("last4", sa.String(length=8), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("slot"),
    )


def downgrade() -> None:
    op.drop_table("ai_provider_secrets")
