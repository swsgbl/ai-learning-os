"""M10-04 治理审计防篡改哈希链（sha256）。

所有业务事务内的审计写入统一走 ``append_audit``：插入 audit_log 行后，用
该行稳定字段 + audit_id + sequence + previous_hash 计算 entry_hash，同事务
写 audit_chain_entries 并推进 audit_chain_state 单行状态。

防篡改边界（docs/DEVELOPMENT.md 同步维护）：
- 可检测：audit 行内容被篡改（action/before/after 等）、链 entry 被篡改或
  删除、sequence 断裂、head/state 漂移——`audit-chain-verify` 全量重算比对；
- 不提供：加密签名与存储级 WORM。持有数据库写权限的攻击者理论上可整链
  重算；抵御整链重算需外部备份 / 对象锁等存储层手段，不在本模块范围。
- 哈希输入禁入 key/token/secret（递归扫描 before/after，命中即拒绝写入）。
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.orm import AuditChainEntryRow, AuditChainStateRow, AuditLogRow

#: 链算法标识（写入每条 entry 与 state；校验器只认这一个值）
AUDIT_CHAIN_ALGORITHM = "sha256"

#: genesis：首条 entry 的 previous_hash（64 个 0）
GENESIS_PREVIOUS_HASH = "0" * 64

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

#: before/after 任意层级命中即拒绝写入（secret 不入哈希也不入日志）；
#: 词段匹配覆盖 password_hash / access_token / api-key 等前后缀变体
_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|[_-])(password|passwd|secret|token|api[_-]?key|private[_-]?key)(?:$|[_-])"
)

_AUDIT_HASH_FIELDS = (
    "id",
    "actor_id",
    "actor_username",
    "action",
    "target_type",
    "target_id",
    "before",
    "after",
    "request_id",
)


def canonical_datetime(value: datetime) -> str:
    """datetime 的稳定文本形态：统一 UTC + 恒定微秒位。

    SQLite 读回 naive、PG 读回 aware；naive 视为 UTC。恒定 timespec 保证
    microsecond=0 的写入值与读回值序列化一致（哈希可重算的前提）。
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return canonical_datetime(value)
    raise TypeError(f"审计哈希载荷含不可序列化类型: {type(value)!r}")


def canonical_json_bytes(value: Any) -> bytes:
    """canonical JSON：sort keys + 紧凑分隔符 + UTF-8（哈希唯一输入形态）。"""
    text = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )
    return text.encode("utf-8")


def _field(audit: Any, name: str) -> Any:
    if isinstance(audit, Mapping):
        return audit[name]
    return getattr(audit, name)


def audit_hash_values(audit: Any) -> dict[str, Any]:
    """从 audit_log 行（ORM 对象或 Row mapping）提取哈希稳定字段。

    created_at 规范化为 canonical_datetime 字符串；id 含入哈希——entry 与
    audit 行一一对应且篡改行内容（换行重插）会得到不同 hash。
    """
    values = {name: _field(audit, name) for name in _AUDIT_HASH_FIELDS}
    values["created_at"] = canonical_datetime(_field(audit, "created_at"))
    return values


def compute_entry_hash(
    previous_hash: str, sequence: int, audit_values: Mapping[str, Any]
) -> str:
    """SHA-256：canonical(previous_hash + sequence + audit 稳定字段)。"""
    if not _HEX64_RE.fullmatch(previous_hash or ""):
        raise ValueError("previous_hash 必须是 64 位小写十六进制")
    payload = {
        "algorithm": AUDIT_CHAIN_ALGORITHM,
        "previous_hash": previous_hash,
        "sequence": int(sequence),
        "audit": dict(audit_values),
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def audit_insert_values(payload: dict, *, clock: Callable[[], datetime]) -> dict:
    """把审计 payload 标准化为 AuditLogRow insert 值（M9-05 同事务审计共用）。

    必需键：action/target_type/target_id/request_id；可选：actor_id/actor_username/
    before/after。缺 request_id 视为构造错误（审计必须可关联请求）。
    M10-04：before/after 递归扫描敏感键（password/token/secret/api_key 等），
    命中即拒绝——secret 既不入日志也不入哈希。
    """
    missing = {"action", "target_type", "target_id", "request_id"} - payload.keys()
    if missing:
        raise ValueError(f"审计 payload 缺字段: {sorted(missing)}")
    _assert_no_secret("before", payload.get("before"))
    _assert_no_secret("after", payload.get("after"))
    return {
        "actor_id": payload.get("actor_id"),
        "actor_username": payload.get("actor_username"),
        "action": payload["action"],
        "target_type": payload["target_type"],
        "target_id": payload["target_id"],
        "before": payload.get("before"),
        "after": payload.get("after"),
        "request_id": payload["request_id"],
        "created_at": clock(),
    }


def _assert_no_secret(path: str, value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if isinstance(key, str) and _SENSITIVE_KEY_RE.search(key.lower()):
                raise ValueError(f"审计 payload 含敏感键，拒绝写入: {child_path}")
            _assert_no_secret(child_path, child)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_no_secret(f"{path}[{index}]", child)


async def append_audit(
    session: AsyncSession,
    payload: dict,
    *,
    clock: Callable[[], datetime],
) -> AuditLogRow:
    """在调用方当前事务内追加一条链化审计（M10-04 全部写入点的唯一入口）。

    步骤：标准化 -> FOR UPDATE 锁 state 单行 -> flush 拿 audit_id ->
    计算 entry_hash -> 同事务写 entry 并推进 state；任一步失败随调用方
    事务整体回滚（fail-closed）。SQLite 忽略 FOR UPDATE，由库级写锁兜底。
    """
    values = audit_insert_values(payload, clock=clock)
    state = await _locked_state(session, clock)
    sequence = state.last_sequence + 1

    row = AuditLogRow(**values)
    session.add(row)
    await session.flush()  # 拿数据库分配的 audit_id（哈希输入之一）

    entry_hash = compute_entry_hash(state.last_hash, sequence, audit_hash_values(row))
    session.add(
        AuditChainEntryRow(
            audit_id=row.id,
            sequence=sequence,
            previous_hash=state.last_hash,
            entry_hash=entry_hash,
            algorithm=AUDIT_CHAIN_ALGORITHM,
            created_at=clock(),
        )
    )
    state.last_sequence = sequence
    state.last_hash = entry_hash
    state.updated_at = clock()
    await session.flush()  # entry/state 约束违例在此抛出，随事务回滚
    return row


async def _locked_state(
    session: AsyncSession, clock: Callable[[], datetime]
) -> AuditChainStateRow:
    """FOR UPDATE 锁定单行 state；缺失时原子初始化 genesis。

    create_all 建出的新库没有 state 行：INSERT ... ON CONFLICT DO NOTHING
    吸收并发双初始化，随后 FOR UPDATE 读回必得唯一行。其他方言退化为普通
    insert（并发冲突直接 IntegrityError，fail-closed 不静默分叉）。
    """
    state = (
        await session.execute(
            select(AuditChainStateRow)
            .where(AuditChainStateRow.id == 1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if state is not None:
        return state

    initial = {
        "id": 1,
        "last_sequence": 0,
        "last_hash": GENESIS_PREVIOUS_HASH,
        "algorithm": AUDIT_CHAIN_ALGORITHM,
        "updated_at": clock(),
    }
    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect == "sqlite":
        statement = (
            sqlite_insert(AuditChainStateRow)
            .values(**initial)
            .on_conflict_do_nothing(index_elements=[AuditChainStateRow.id])
        )
    elif dialect == "postgresql":
        statement = (
            pg_insert(AuditChainStateRow)
            .values(**initial)
            .on_conflict_do_nothing(index_elements=[AuditChainStateRow.id])
        )
    else:
        statement = insert(AuditChainStateRow).values(**initial)
    await session.execute(statement)
    state = (
        await session.execute(
            select(AuditChainStateRow)
            .where(AuditChainStateRow.id == 1)
            .with_for_update()
        )
    ).scalar_one()
    return state
