"""audit-chain: M10-04 治理审计防篡改哈希链（entries + 单行 state + 存量建链）。

Revision ID: 0027_audit_chain
Revises: 0026_paper_owner
Create Date: 2026-09-04

语义：
- audit_chain_entries 与 audit_log 一一对应（audit_id PK + FK ON DELETE RESTRICT，
  删审计行不留痕在数据库层即被拒绝）；sequence 从 1 连续递增且全局唯一；
- audit_chain_state 单行（CHECK id=1）持链尾 last_sequence/last_hash，
  是应用层 append_audit 的 FOR UPDATE 锁点；
- upgrade 对既有 audit_log 按 id 升序一次性建链（genesis previous_hash = 64 个 0），
  哈希算法与应用层同源（import app.domain.audit_chain，杜绝 SQL/Python 两套规则漂移）；
- downgrade 只删两张新表，不动 audit_log 任何数据。

注意：Alembic 版本图构建先于 env.py 执行，services/api 的 sys.path 由本文件
自行补齐；离线 dry-run（--sql）只产出 DDL，数据建链需要真实连接故跳过。
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from alembic import context, op

_API_DIR = Path(__file__).resolve().parents[2]
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

from app.domain.audit_chain import (
    AUDIT_CHAIN_ALGORITHM,
    GENESIS_PREVIOUS_HASH,
    audit_hash_values,
    compute_entry_hash,
)

revision = "0027_audit_chain"
down_revision = "0026_paper_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_chain_entries",
        sa.Column("audit_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("previous_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("entry_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("algorithm", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["audit_id"], ["audit_log.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("audit_id"),
        sa.UniqueConstraint("sequence", name="uq_audit_chain_entries_sequence"),
        sa.UniqueConstraint("entry_hash", name="uq_audit_chain_entries_entry_hash"),
    )
    op.create_table(
        "audit_chain_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("last_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("algorithm", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_audit_chain_state_singleton"),
    )
    if context.is_offline_mode():
        return
    _backfill_chain(op.get_bind())


def downgrade() -> None:
    op.drop_table("audit_chain_entries")
    op.drop_table("audit_chain_state")


_AUDIT_LOG_COLUMNS = (
    "id",
    "actor_id",
    "actor_username",
    "action",
    "target_type",
    "target_id",
    "before",
    "after",
    "request_id",
    "created_at",
)


def _audit_log_table(bind: sa.Connection) -> sa.Table:
    """按数据库实态反射 audit_log（0027 不改该表，反射即迁移时点真相）。"""
    return sa.Table("audit_log", sa.MetaData(), autoload_with=bind)


def _entries_table() -> sa.Table:
    return sa.table(
        "audit_chain_entries",
        sa.column("audit_id", sa.Integer),
        sa.column("sequence", sa.Integer),
        sa.column("previous_hash", sa.CHAR(length=64)),
        sa.column("entry_hash", sa.CHAR(length=64)),
        sa.column("algorithm", sa.String(length=16)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )


def _state_table() -> sa.Table:
    return sa.table(
        "audit_chain_state",
        sa.column("id", sa.Integer),
        sa.column("last_sequence", sa.Integer),
        sa.column("last_hash", sa.CHAR(length=64)),
        sa.column("algorithm", sa.String(length=16)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def _backfill_chain(bind: sa.Connection) -> None:
    """既有 audit_log 按 id 升序建链并写 state；空库也落 genesis state。"""
    audit_log = _audit_log_table(bind)
    rows = bind.execute(
        sa.select(*(audit_log.c[name] for name in _AUDIT_LOG_COLUMNS)).order_by(
            audit_log.c.id
        )
    ).mappings().all()

    now = datetime.now(UTC)
    entries: list[dict] = []
    previous_hash = GENESIS_PREVIOUS_HASH
    sequence = 0
    for row in rows:
        sequence += 1
        entry_hash = compute_entry_hash(previous_hash, sequence, audit_hash_values(row))
        entries.append(
            {
                "audit_id": row["id"],
                "sequence": sequence,
                "previous_hash": previous_hash,
                "entry_hash": entry_hash,
                "algorithm": AUDIT_CHAIN_ALGORITHM,
                "created_at": now,
            }
        )
        previous_hash = entry_hash

    if entries:
        bind.execute(sa.insert(_entries_table()), entries)
    bind.execute(
        sa.insert(_state_table()).values(
            id=1,
            last_sequence=sequence,
            last_hash=previous_hash,
            algorithm=AUDIT_CHAIN_ALGORITHM,
            updated_at=now,
        )
    )
