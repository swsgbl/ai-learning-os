"""ownership: M9-02 数据归属拆分（resources / exam_sessions / voice_transcripts 加 owner_id）。

Revision ID: 0023_ownership
Revises: 0022_users
Create Date: 2026-09-02

归属语义（ADR 66）：
- owner_id NULL = 无主数据（认证关闭的本地调试模式产生）；
- 认证开启时写入 owner=me；读取严格 owner==me，NULL 行不可见；
- answer_events/submissions/voice_* 经父实体（exam/session）归属，不加列；
- resources dedup 从全局唯一改为 (owner_id, content_hash) 复合唯一——
  跨用户同内容各自保留，杜绝「B 通过 dedup 读到 A 的对象」。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0023_ownership"
down_revision = "0022_users"
branch_labels = None
depends_on = None


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    op.add_column("resources", sa.Column("owner_id", sa.String(length=32), nullable=True))
    # 约束切换仅存量 PG 需要（SQLite 不支持 ALTER 约束；SQLite 测试库由 ORM
    # create_all 全新建表，复合唯一已按 ORM 定义生成）
    if not _is_sqlite():
        # content_hash 的旧唯一约束名跨环境不同（unnamed 建表 -> PG 自动名；
        # 部分存量卷为 uq_resources_content_hash）—— 内省后全部摘除再建复合唯一
        existing = op.get_bind().execute(
            sa.text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'resources'::regclass AND contype = 'u'"
            )
        ).scalars().all()
        for name in existing:
            op.drop_constraint(name, "resources", type_="unique")
        op.create_unique_constraint(
            "uq_resources_owner_content", "resources", ["owner_id", "content_hash"]
        )
    op.create_index("ix_resources_owner_id", "resources", ["owner_id"])

    op.add_column("exam_sessions", sa.Column("owner_id", sa.String(length=32), nullable=True))
    op.create_index("ix_exam_sessions_owner_id", "exam_sessions", ["owner_id"])

    op.add_column("voice_transcripts", sa.Column("owner_id", sa.String(length=32), nullable=True))
    op.create_index("ix_voice_transcripts_owner_id", "voice_transcripts", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_voice_transcripts_owner_id", table_name="voice_transcripts")
    op.drop_column("voice_transcripts", "owner_id")
    op.drop_index("ix_exam_sessions_owner_id", table_name="exam_sessions")
    op.drop_column("exam_sessions", "owner_id")
    op.drop_index("ix_resources_owner_id", table_name="resources")
    if not _is_sqlite():
        op.drop_constraint("uq_resources_owner_content", "resources", type_="unique")
        op.create_unique_constraint(
            "uq_resources_content_hash", "resources", ["content_hash"]
        )  # 单列唯一重建为存量卷同款名（downgrade 幂等）
    op.drop_column("resources", "owner_id")
