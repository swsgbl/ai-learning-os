"""M10-07 production cutover preflight：生产切换前/后只读汇总预检。

定位：生产切换 runbook 的**防呆汇总**，不是 release-check 的替代品。只做只读
检查并汇总为 pass / pending / fail / not_configured 四态，绝不代劳：

- 不执行迁移（alembic 只读对账 current/head，绝不 upgrade/downgrade）；
- 不写数据库（全部 SELECT / inspector，链校验复用 load/verify snapshot）；
- 不写锚文件（锚定检查复用 audit-chain-anchor 的 --verify-only 语义，
  锚文件缺失按 runbook 标为需人工完成——本工具永不创建锚文件）；
- 不清理数据、不启动/停止服务。

检查项（每项聚合输出，不输出生产 paper/draft ID，不含 audit before/after
正文，不输出完整 DB URL 或凭据）：

1. db-connect：连通性与当前库名（PG 用 current_database()，SQLite 用 URL
   库名；错误信息经 redact_secrets 抹掉可能内嵌的 user:pass）；
2. alembic：只读对账。pre-migration 允许 current 落后 head（pending，迁移
   窗口执行）；post-migration 必须 current == head（否则 fail）。current
   缺失/未知 revision、脚本目录多 head 一律 fail；
3. audit-chain：复用 audit_chain_verify 的 load/verify snapshot 语义。
   pre-migration 只允许「audit_chain_entries 与 audit_chain_state 同时缺失
   且 audit_log 存在」这一种缺失形态算 pending_migration（0026 -> 0027 的
   正常未迁移形态；0024 建 audit_log、0027 原子建两张链表）；仅缺一张
   链表、audit_log 缺失或三表全缺不对应任何迁移可达形态（schema 部分
   损坏/连错库/库早于 0024），一律 fail；post-migration 任何链表缺失或
   校验 invalid 都 fail；
4. audit-anchor：pre-migration 未锚定是 expected_pending；post-migration
   提供锚文件时只跑 verify-only（up-to-date=pass / head 超前=pending /
   invalid=fail），未提供或文件不存在输出 not_configured（按 runbook 人工
   完成锚定 + WORM 归档）。锚定交叉核对消费主流程的同一份链快照，不另开
   数据库连接；
5. legacy-governance：历史治理聚合计数（无归属非 seed 卷、generation/
   variant NULL owner 草稿）——计数 > 0 即 pending（人工决策项，绝不包装成
   pass），只输出聚合计数不输出任何生产 ID。

退出码：无 fail=0；存在 fail=1；输入/锚文件路径/数据库连接/Alembic 脚本
目录解析/报告写入失败=2（与 audit-chain-verify/anchor CLI 同口径）。
pending / not_configured 不计入 fail——它们是「生产仍需人工决策/执行」的
如实提示，人类摘要必须写明。
"""
from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import make_url

from app.db.orm import CourseGenerationDraftRow, PaperRow, VariantQuestionDraftRow
from app.db.session import create_engine
from app.ops.audit_chain_anchor import check_anchor_path, run_anchor
from app.ops.audit_chain_verify import load_chain_snapshot, verify_chain_snapshot
from app.ops.legacy_papers import SEED_SOURCE
from app.ops.version import REPO_ROOT

PHASE_PRE_MIGRATION = "pre-migration"
PHASE_POST_MIGRATION = "post-migration"
PHASES = (PHASE_PRE_MIGRATION, PHASE_POST_MIGRATION)

STATUS_PASS = "pass"
STATUS_PENDING = "pending"
STATUS_FAIL = "fail"
STATUS_NOT_CONFIGURED = "not_configured"

#: 汇总/错误信息里可能内嵌 DB URL：抹掉 scheme://user:password@ 的凭据段
_CREDENTIAL_RE = re.compile(r"://([^/@:\s]+):([^@\s/]+)@")


def redact_secrets(text: str) -> str:
    """抹掉 `://user:password@` 形态的凭据（连接错误信息可能携带 URL）。"""
    return _CREDENTIAL_RE.sub(r"://\1:***@", text)


def _check(
    check_id: str, title: str, status: str, detail: str = "", data: dict | None = None
) -> dict[str, Any]:
    return {
        "id": check_id,
        "title": title,
        "status": status,
        "detail": redact_secrets(detail),
        "data": data or {},
    }


# --- 检查 1：数据库连通与当前库名 --------------------------------------------


async def _check_database(conn, db_url: str) -> dict[str, Any]:
    dialect = conn.dialect.name
    if dialect == "postgresql":
        database = await conn.scalar(sa.text("SELECT current_database()"))
    else:
        database = make_url(db_url).database or "(memory)"
    return _check(
        "db-connect",
        "数据库连通与当前库名",
        STATUS_PASS,
        f"{dialect} / {database}",
        {"dialect": dialect, "database": database},
    )


# --- 检查 2：alembic current/head 只读对账 -----------------------------------


def _alembic_dir() -> Path:
    """alembic 脚本目录：布局规则与 app.ops.version._alembic_dir 一致
    （源码仓库 services/api 与容器 /app 双兼容），独立小实现不引私有函数。"""
    for candidate in (REPO_ROOT / "services" / "api", REPO_ROOT):
        if (candidate / "alembic.ini").is_file():
            return candidate
    return REPO_ROOT / "services" / "api"


def _alembic_script_state() -> tuple[list[str], set[str]]:
    """只读解析迁移脚本目录：返回 (heads, 全部已知 revision)。

    直接用 alembic 的 ScriptDirectory（不 spawn 子进程——不向子进程环境传
    DATABASE_URL，也不连接数据库）；`alembic current` 的等价信息由调用方
    直接查 alembic_version 表获得。
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    api_dir = _alembic_dir()
    cfg = Config(str(api_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_dir / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    return list(script.get_heads()), {rev.revision for rev in script.walk_revisions()}


async def _check_alembic(conn, phase: str) -> dict[str, Any]:
    heads, known = _alembic_script_state()
    current_rows: list[str] = []
    if await conn.run_sync(
        lambda sync_conn: sa.inspect(sync_conn).has_table("alembic_version")
    ):
        current_rows = [
            row[0] for row in await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
        ]
    if len(heads) != 1:
        return _check(
            "alembic",
            "Alembic current/head 只读对账（绝不 upgrade/downgrade）",
            STATUS_FAIL,
            f"迁移脚本目录有 {len(heads)} 个 head: {sorted(heads)}（分叉需人工处理）",
        )
    head = heads[0]
    if len(current_rows) != 1:
        return _check(
            "alembic",
            "Alembic current/head 只读对账（绝不 upgrade/downgrade）",
            STATUS_FAIL,
            f"alembic_version 表缺失或行数异常（{len(current_rows)} 行）——"
            "不是 alembic 管理的库或版本表损坏，先排查连接目标",
            {"head": head, "current": None},
        )
    current = current_rows[0]
    if current not in known:
        return _check(
            "alembic",
            "Alembic current/head 只读对账（绝不 upgrade/downgrade）",
            STATUS_FAIL,
            f"current revision {current} 不在迁移脚本目录中"
            "（脚本过旧或连错库），先排查再迁移",
            {"head": head, "current": current},
        )
    if current == head:
        return _check(
            "alembic",
            "Alembic current/head 只读对账（绝不 upgrade/downgrade）",
            STATUS_PASS,
            f"current == head == {head}（迁移已就位，切换窗口 upgrade head 幂等无操作）",
            {"head": head, "current": current},
        )
    if phase == PHASE_POST_MIGRATION:
        return _check(
            "alembic",
            "Alembic current/head 只读对账（绝不 upgrade/downgrade）",
            STATUS_FAIL,
            f"post-migration 要求 current == head：current={current} head={head}"
            "（迁移未完成或未执行）",
            {"head": head, "current": current},
        )
    return _check(
        "alembic",
        "Alembic current/head 只读对账（绝不 upgrade/downgrade）",
        STATUS_PENDING,
        f"待执行迁移：current={current} -> head={head}"
        "（切换窗口人工执行 alembic upgrade head，本工具不执行迁移）",
        {"head": head, "current": current},
    )


# --- 检查 3：审计哈希链（复用 load/verify snapshot） --------------------------

#: 0026 -> 0027 正常未迁移形态**唯一**允许的链表缺失集合：两张链表由 0027
#: 原子创建（audit_log 由 0024 更早建好）。仅缺其一/audit_log 缺失/三表全缺
#: 都不是任何迁移可达形态，pre-migration 也一律 fail（schema 部分损坏或
#: 连错库；三表全缺=库早于 0024，不是本 runbook 的 preflight 起点）。
_PRE_MIGRATION_EXPECTED_MISSING = frozenset({"audit_chain_entries", "audit_chain_state"})


def _check_audit_chain(
    phase: str, snapshot: dict[str, Any], verify_report: dict[str, Any]
) -> dict[str, Any]:
    data = {
        "valid": verify_report["valid"],
        "entries": verify_report["entries"],
        "audit_rows": verify_report["audit_rows"],
        "missing_tables": list(snapshot["missing_tables"]),
    }
    title = "治理审计哈希链只读校验（audit-chain-verify 语义）"
    if snapshot["missing_tables"]:
        missing = list(snapshot["missing_tables"])
        if (
            phase == PHASE_PRE_MIGRATION
            and set(missing) == _PRE_MIGRATION_EXPECTED_MISSING
        ):
            return _check(
                "audit-chain",
                title,
                STATUS_PENDING,
                "pending_migration: 0027_audit_chain 未执行（链表缺失: "
                f"{', '.join(missing)}）——迁移前预期形态，"
                "迁移建链后必须 audit-chain-verify valid 才恢复服务",
                data,
            )
        if phase == PHASE_PRE_MIGRATION:
            return _check(
                "audit-chain",
                title,
                STATUS_FAIL,
                "链表缺失形态非法: " + ", ".join(missing)
                + "——合法的未迁移形态只有 audit_chain_entries 与 "
                "audit_chain_state 同时缺失且 audit_log 存在（0024 建 "
                "audit_log、0027 原子建两链表）；部分缺失、audit_log 缺失"
                "或三表全缺不对应任何迁移可达形态（schema 部分损坏或库早于"
                " 0024），先停下排查",
                data,
            )
        return _check(
            "audit-chain",
            title,
            STATUS_FAIL,
            "迁移后链表仍缺失: " + ", ".join(snapshot["missing_tables"])
            + "（0027 未执行或连错库），不得恢复服务",
            data,
        )
    if verify_report["valid"]:
        return _check(
            "audit-chain",
            title,
            STATUS_PASS,
            f"valid（{verify_report['entries']} entries / "
            f"{verify_report['audit_rows']} audit rows）",
            data,
        )
    problems = "; ".join(verify_report["problems"][:3])
    return _check(
        "audit-chain",
        title,
        STATUS_FAIL,
        f"invalid（{len(verify_report['problems'])} 个问题）: {problems}",
        data,
    )


# --- 检查 4：库外锚定状态 -----------------------------------------------------


async def _check_anchor(
    snapshot: dict[str, Any],
    phase: str,
    anchor_path: Path | None,
) -> dict[str, Any]:
    title = "审计链库外锚定状态（verify-only，绝不写锚文件）"
    if anchor_path is None:
        if phase == PHASE_PRE_MIGRATION:
            return _check(
                "audit-anchor",
                title,
                STATUS_PENDING,
                "expected_pending: 锚定在 0027 迁移后执行"
                "（runbook：verifier valid 后人工 audit-chain-anchor --yes 并归档 WORM）",
            )
        return _check(
            "audit-anchor",
            title,
            STATUS_NOT_CONFIGURED,
            "not_configured: 未提供 --anchor-file——按 runbook 需人工完成初始锚定"
            "并归档 WORM（本工具不创建锚文件）",
        )
    if not os.path.lexists(anchor_path):
        if phase == PHASE_PRE_MIGRATION:
            return _check(
                "audit-anchor",
                title,
                STATUS_PENDING,
                "expected_pending: 锚文件不存在——迁移后人工执行初始锚定"
                "（本工具不创建锚文件）",
                {"anchor_file": str(anchor_path), "anchors": 0},
            )
        return _check(
            "audit-anchor",
            title,
            STATUS_NOT_CONFIGURED,
            "not_configured: 锚文件尚未创建——按 runbook 人工执行 "
            "audit-chain-anchor --yes 创建并归档 WORM（本工具不自动创建）",
            {"anchor_file": str(anchor_path), "anchors": 0},
        )
    # 只跑 verify-only：DB 链 + 锚文件链 + head 交叉一致（对锚文件零写入）。
    # 把主流程同一事务快照交给锚定校验（run_anchor 不再自建连接，verify
    # 结论从快照纯重算）——DB head 交叉核对与上面的 audit-chain 检查看到
    # 的是同一份链数据。
    report = await run_anchor(
        None,
        anchor_path,
        verify_only=True,
        snapshot=snapshot,
    )
    anchors = report["anchor_file"]["anchors"]
    data = {"anchor_file": str(anchor_path), "anchors": anchors}
    if report["status"] == "up-to-date":
        return _check(
            "audit-anchor",
            title,
            STATUS_PASS,
            f"verify-only up-to-date（DB head 与最后锚点一致，共 {anchors} 锚点）",
            {**data, "anchor_status": "up-to-date"},
        )
    if report["status"] == "valid":
        return _check(
            "audit-anchor",
            title,
            STATUS_PENDING,
            "verify-only 自洽但 DB head 超前最后锚点——人工执行 "
            "audit-chain-anchor --yes 追加并归档 WORM",
            {**data, "anchor_status": "valid"},
        )
    problems = "; ".join(report["problems"][:3])
    return _check(
        "audit-anchor",
        title,
        STATUS_FAIL,
        f"verify-only invalid（{len(report['problems'])} 个问题）: {problems}",
        {**data, "anchor_status": "invalid"},
    )


# --- 检查 5：历史治理聚合计数 -------------------------------------------------


async def _count(conn, model, *conditions) -> int:
    query = sa.select(sa.func.count()).select_from(model)
    if conditions:
        query = query.where(*conditions)
    return int(await conn.scalar(query) or 0)


async def _check_legacy_governance(conn) -> dict[str, Any]:
    title = "历史治理聚合计数（无归属卷/草稿，只输出计数不输出 ID）"
    tables = ("papers", "course_generation_drafts", "variant_question_drafts")
    missing = [
        name
        for name in tables
        if not await conn.run_sync(
            lambda sync_conn, n=name: sa.inspect(sync_conn).has_table(n)
        )
    ]
    if missing:
        return _check(
            "legacy-governance",
            title,
            STATUS_FAIL,
            f"表缺失: {', '.join(missing)}——目标库不是本应用 Schema，先排查连接目标",
        )
    counts = {
        "unowned_non_seed_papers": await _count(
            conn, PaperRow, PaperRow.owner_id.is_(None), PaperRow.source != SEED_SOURCE
        ),
        "course_generation_null_owner_drafts": await _count(
            conn, CourseGenerationDraftRow, CourseGenerationDraftRow.owner_id.is_(None)
        ),
        "variant_question_null_owner_drafts": await _count(
            conn, VariantQuestionDraftRow, VariantQuestionDraftRow.owner_id.is_(None)
        ),
    }
    total = sum(counts.values())
    if total == 0:
        return _check(
            "legacy-governance", title, STATUS_PASS, "无待归属历史卷/草稿", counts
        )
    return _check(
        "legacy-governance",
        title,
        STATUS_PENDING,
        f"{total} 项待人工归属决策（无归属非 seed 卷 + NULL owner 历史草稿；"
        "本工具只报计数不迁移）——用 legacy-paper-report / draft-owner-report "
        "复核后逐批 legacy-paper-migrate / draft-owner-migrate --yes",
        counts,
    )


# --- 主流程 -------------------------------------------------------------------


def _snapshot_execution_options(dialect_name: str) -> dict[str, Any] | None:
    """快照事务的方言级执行选项（返回 None = 无需方言级选项）。

    PG：数据库层 READ ONLY + REPEATABLE READ——`postgresql_readonly`
    让 SQLAlchemy 以 `BEGIN READ ONLY` 开事务，任何写入语句在数据库处
    即被拒绝（只读不依赖「本模块只发 SELECT」的语句面自律）；隔离级别
    保证全部读取共享同一事务快照。SQLite：aiosqlite 无等价的 READ ONLY
    事务语法，返回 None 走默认显式事务——只读边界是**语句面**的（本
    模块只发 SELECT / inspector），不伪造数据库层能力。
    """
    if dialect_name == "postgresql":
        return {"isolation_level": "REPEATABLE READ", "postgresql_readonly": True}
    return None


async def run_preflight(
    db_url: str, phase: str, *, anchor_file: str | Path | None = None
) -> dict[str, Any]:
    """执行全部只读检查并汇总（连接失败抛 SQLAlchemyError 由 CLI 映射 exit 2）。

    全部数据库读取在**单一连接的显式只读事务**内完成：PG 在数据库层
    READ ONLY + REPEATABLE READ（`postgresql_readonly`，写入在数据库处
    被拒绝且全部读取同一事务快照），SQLite 走显式事务的语句面只读快照
    （aiosqlite 无等价 READ ONLY 事务语法，不伪造数据库层能力——与
    audit-chain-anchor 的快照口径一致）：db-connect / alembic /
    audit-chain / legacy-governance 与锚定交叉核对的 DB head 消费同一份
    快照（`_check_anchor` 把快照交给 `run_anchor(verify_only=True)`，
    不再开第二个数据库连接）。对数据库与锚文件零写入。
    """
    if phase not in PHASES:
        raise ValueError(f"未知 phase: {phase}（可选 {PHASES}）")
    anchor_path = Path(anchor_file) if anchor_file else None
    if anchor_path is not None:
        # 输入护栏前置：symlink/目录/父目录缺失立即失败（AnchorInputError -> exit 2），
        # 不在跑完一半检查后才发现路径不可用。
        check_anchor_path(anchor_path)

    checks: list[dict[str, Any]] = []
    engine = create_engine(db_url)
    try:
        async with engine.connect() as conn:
            options = _snapshot_execution_options(conn.dialect.name)
            if options is not None:
                # AsyncConnection.execution_options 是 async 方法：漏 await 时
                # 协程被静默丢弃、选项完全不生效（M10-08 真实 PG 实测发现的
                # 缺陷——事务实际落在默认 read committed 且可写）
                await conn.execution_options(**options)
            async with conn.begin():
                checks.append(await _check_database(conn, db_url))
                checks.append(await _check_alembic(conn, phase))
                snapshot = await load_chain_snapshot(conn)
                verify_report = verify_chain_snapshot(snapshot)  # 纯校验，无 IO
                checks.append(_check_audit_chain(phase, snapshot, verify_report))
                checks.append(await _check_legacy_governance(conn))
    finally:
        await engine.dispose()
    checks.append(await _check_anchor(snapshot, phase, anchor_path))
    return _assemble_report(phase, checks)


def _assemble_report(phase: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        status: sum(1 for check in checks if check["status"] == status)
        for status in (STATUS_PASS, STATUS_PENDING, STATUS_FAIL, STATUS_NOT_CONFIGURED)
    }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "phase": phase,
        "read_only": True,
        "checks": checks,
        "summary": summary,
        # pending / not_configured 不计入 fail：人工事项如实透出，不包装成 pass
        "exit_code": 1 if summary[STATUS_FAIL] else 0,
    }


def format_preflight_summary(report: dict[str, Any]) -> str:
    """人类可读摘要（无生产 ID / 无凭据 / 无 before-after 正文）。"""
    lines = [
        f"生产切换只读 preflight（phase={report['phase']}）",
        f"生成时间: {report['generated_at']}",
        "只读检查：不执行迁移 / 不写数据库 / 不写锚文件 / 不清理数据 / 不启停服务",
        "-" * 72,
    ]
    for check in report["checks"]:
        lines.append(f"[{check['status'].upper():<14}] {check['id']}: {check['title']}")
        if check["detail"]:
            lines.append(f"{'':<16} -> {check['detail']}")
    lines.append("-" * 72)
    summary = report["summary"]
    verdict = "FAIL" if report["exit_code"] else "OK"
    lines.append(
        f"RESULT: {verdict}  pass={summary['pass']}  pending={summary['pending']}  "
        f"fail={summary['fail']}  not_configured={summary['not_configured']}"
    )
    if summary[STATUS_PENDING] or summary[STATUS_NOT_CONFIGURED]:
        lines.append(
            "注意: pending / not_configured 不是 pass——生产切换仍需按 runbook 人工"
            "决策/执行（本工具绝不代为迁移、锚定或清理）。"
        )
    lines.append("退出码: 无 fail=0 / 存在 fail=1 / 输入/路径/连接错误=2。")
    return "\n".join(lines)
