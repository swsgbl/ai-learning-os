"""M10-04 audit-chain-verify：治理审计哈希链只读校验器。

全量重算比对（不信任任何存储的中间结论）：
- audit_log 与 audit_chain_entries 一一对应（多出的 audit 行 = 漏记，
  多出的 entry = 伪造；FK RESTRICT 让删审计行在 DB 层即被拒绝）；
- sequence 从 1 连续递增；首条 previous_hash == genesis（64 个 0）；
- 每条 previous_hash == 前一条 entry_hash；entry_hash == 用 audit 行
  稳定字段重算的 SHA-256；algorithm 一律 sha256；
- audit_chain_state 单行且 last_sequence/last_hash 指向真实链尾。

输出边界：problems 只含 sequence/audit_id 与原因，绝不打印 before/after
正文或任何可能承载敏感值的字段内容。本命令对数据库零写入。

可检测：行内容篡改、entry 篡改/删除、sequence 断裂、head/state 漂移。
不可检测（持数据库写权限者的整链重算）：抵御需库外锚定（M10-06
`app/ops/audit_chain_anchor.py` 把 head_hash 记录到库外 append-only
锚文件），见 docs/DEVELOPMENT.md。

M10-06：读取与校验拆开——`load_chain_snapshot(conn)` 在**同一个连接**
（调用方可包显式事务拿一致快照）上读三组行，`verify_chain_snapshot`
做纯校验。锚定工具复用二者，保证「verify 结论 + 锚点交叉核对 + head
提取」来自同一份快照，消除多连接间的竞态（docs 并发边界说明）。
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.orm import AuditChainEntryRow, AuditChainStateRow, AuditLogRow
from app.db.session import create_engine
from app.domain.audit_chain import (
    AUDIT_CHAIN_ALGORITHM,
    GENESIS_PREVIOUS_HASH,
    audit_hash_values,
    compute_entry_hash,
)

#: problems 报告上限：防海量断链刷屏；超出部分汇总额外计数
_MAX_PROBLEMS = 50

_REQUIRED_TABLES = ("audit_log", "audit_chain_entries", "audit_chain_state")


async def load_chain_snapshot(conn: AsyncConnection) -> dict[str, Any]:
    """在给定连接上一次读取三组行（含表存在性检查）。

    调用方可在同一连接外包显式事务（`async with conn.begin()`；PG 可再
    提升隔离级别到 REPEATABLE READ）获得一致快照；三条 SELECT 读取期间
    数据不漂移是锚定工具交叉核对正确性的前提。对数据库零写入。
    """
    missing_tables = [
        name
        for name in _REQUIRED_TABLES
        if not await conn.run_sync(
            lambda sync_conn, n=name: sa.inspect(sync_conn).has_table(n)
        )
    ]
    if missing_tables:
        return {
            "missing_tables": missing_tables,
            "audit_rows": [],
            "entries": [],
            "states": [],
        }
    # core 连接上 ORM-entity select 不物化实体（scalars 只给主键值），
    # 统一取 RowMapping：audit_hash_values 按 Mapping 提取，与迁移
    # 脚本读回形态一致。
    audit_rows = (
        await conn.execute(sa.select(AuditLogRow).order_by(AuditLogRow.id))
    ).mappings().all()
    entries = (
        await conn.execute(
            sa.select(AuditChainEntryRow).order_by(AuditChainEntryRow.sequence)
        )
    ).mappings().all()
    states = (
        await conn.execute(
            sa.select(AuditChainStateRow).order_by(AuditChainStateRow.id)
        )
    ).mappings().all()
    return {
        "missing_tables": [],
        "audit_rows": list(audit_rows),
        "entries": list(entries),
        "states": list(states),
    }


def verify_chain_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """对 load_chain_snapshot 的结果做纯校验（无 IO），返回 report。"""
    problems: list[str] = []
    report: dict[str, Any] = {
        "algorithm": AUDIT_CHAIN_ALGORITHM,
        "entries": 0,
        "audit_rows": 0,
    }
    if snapshot["missing_tables"]:
        problems.append(
            "表缺失: " + ", ".join(snapshot["missing_tables"])
            + "（0027_audit_chain 未执行或库不是本应用Schema）"
        )
        return _finish(report, problems)
    audit_rows = snapshot["audit_rows"]
    entries = snapshot["entries"]
    states = snapshot["states"]
    report["entries"] = len(entries)
    report["audit_rows"] = len(audit_rows)
    _verify_pairing(audit_rows, entries, problems)
    _verify_sequence_chain(entries, problems)
    _verify_hashes(audit_rows, entries, problems)
    _verify_state(states, entries, problems)
    return _finish(report, problems)


async def verify_audit_chain(db_url: str) -> dict[str, Any]:
    """对 db_url 只读校验哈希链，返回 report dict（不抛业务异常）。"""
    engine = create_engine(db_url)
    try:
        async with engine.connect() as conn:
            snapshot = await load_chain_snapshot(conn)
    finally:
        await engine.dispose()
    return verify_chain_snapshot(snapshot)


def _verify_pairing(
    audit_rows: list[Mapping[str, Any]],
    entries: list[Mapping[str, Any]],
    problems: list[str],
) -> None:
    """一一对应：audit 行缺 entry = 漏记；entry 缺 audit 行 = 伪造/破坏。"""
    audit_ids = {row["id"] for row in audit_rows}
    entry_ids = {entry["audit_id"] for entry in entries}
    missing_entries = sorted(audit_ids - entry_ids)
    if missing_entries:
        sample = ", ".join(map(str, missing_entries[:5]))
        problems.append(
            f"{len(missing_entries)} 条 audit_log 无链 entry（audit_id: {sample}"
            f"{'...' if len(missing_entries) > 5 else ''}）"
        )
    orphan_entries = sorted(entry_ids - audit_ids)
    if orphan_entries:
        sample = ", ".join(map(str, orphan_entries[:5]))
        problems.append(
            f"{len(orphan_entries)} 条 entry 无对应 audit_log 行（audit_id: {sample}"
            f"{'...' if len(orphan_entries) > 5 else ''}）"
        )


def _verify_sequence_chain(
    entries: list[Mapping[str, Any]], problems: list[str]
) -> None:
    """sequence 必须从 1 连续；首条 previous_hash 必须是 genesis。"""
    sequences = [entry["sequence"] for entry in entries]
    for expect, actual in enumerate(sequences, start=1):
        if actual != expect:
            problems.append(
                f"sequence 断裂: 期望 {expect}，实际 {actual}"
                "（entry 被删除或链被跳写）"
            )
            break
    if sequences and sequences[0] == 1:
        first = entries[0]
        if first["previous_hash"] != GENESIS_PREVIOUS_HASH:
            problems.append(
                f"genesis previous_hash 非法: sequence=1 audit_id={first['audit_id']}"
            )


def _verify_hashes(
    audit_rows: list[Mapping[str, Any]],
    entries: list[Mapping[str, Any]],
    problems: list[str],
) -> None:
    """逐条全量重算：previous 链接 + entry_hash + algorithm。"""
    audits_by_id = {row["id"]: row for row in audit_rows}
    previous_hash = GENESIS_PREVIOUS_HASH
    for entry in entries:
        label = f"sequence={entry['sequence']} audit_id={entry['audit_id']}"
        if entry["previous_hash"] != previous_hash:
            problems.append(f"previous_hash 链接断裂: {label}")
        if entry["algorithm"] != AUDIT_CHAIN_ALGORITHM:
            problems.append(f"algorithm 非 {AUDIT_CHAIN_ALGORITHM}: {label}")
        audit = audits_by_id.get(entry["audit_id"])
        if audit is not None:
            recomputed = compute_entry_hash(
                entry["previous_hash"], entry["sequence"], audit_hash_values(audit)
            )
            if recomputed != entry["entry_hash"]:
                problems.append(
                    f"entry_hash 与 audit 行内容不匹配（行被篡改）: {label}"
                )
        previous_hash = entry["entry_hash"]


def _verify_state(
    states: list[Mapping[str, Any]],
    entries: list[Mapping[str, Any]],
    problems: list[str],
) -> None:
    """state 单行且 head 与真实链尾一致（N=0 时尾 = genesis）。"""
    if len(states) == 0:
        problems.append("audit_chain_state 无行（链状态未初始化）")
        return
    if len(states) > 1:
        problems.append(f"audit_chain_state 应单行，实际 {len(states)} 行")
    state = states[0]
    if state["algorithm"] != AUDIT_CHAIN_ALGORITHM:
        problems.append(
            f"state algorithm 非 {AUDIT_CHAIN_ALGORITHM}: {state['algorithm']}"
        )
    expected_last_sequence = len(entries)
    expected_last_hash = (
        entries[-1]["entry_hash"] if entries else GENESIS_PREVIOUS_HASH
    )
    if state["last_sequence"] != expected_last_sequence:
        problems.append(
            f"state.last_sequence={state['last_sequence']} 与链尾 "
            f"{expected_last_sequence} 不一致（head 漂移或漏记）"
        )
    if state["last_hash"] != expected_last_hash:
        problems.append("state.last_hash 与链尾 entry_hash 不一致（head 漂移）")


def _finish(report: dict[str, Any], problems: list[str]) -> dict[str, Any]:
    total = len(problems)
    report["problems"] = problems[:_MAX_PROBLEMS]
    if total > _MAX_PROBLEMS:
        report["problems"].append(
            f"...另有 {total - _MAX_PROBLEMS} 个问题未展开"
        )
    report["valid"] = total == 0
    return report


def format_verify_summary(report: dict[str, Any]) -> str:
    """人类可读摘要（不含 before/after 正文与任何敏感值）。"""
    if report["valid"]:
        return (
            f"audit chain: VALID（{report['entries']} entries / "
            f"{report['audit_rows']} audit rows, algorithm={report['algorithm']}）"
        )
    lines = [
        f"audit chain: INVALID（{len(report['problems'])} 个问题）",
        f"entries={report['entries']} audit_rows={report['audit_rows']}",
    ]
    lines.extend(f"- {problem}" for problem in report["problems"])
    return "\n".join(lines)


def run_verify(db_url: str) -> dict[str, Any]:
    return asyncio.run(verify_audit_chain(db_url))
