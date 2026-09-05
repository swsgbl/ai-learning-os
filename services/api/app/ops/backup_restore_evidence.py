"""M11-04 backup-restore 演练证据导出器：隔离库恢复演练 -> backup-restore.json。

定位：release-readiness 的 ``backup-restore`` 门（M10-11）与 cutover-rehearsal
的 ``backup-restore`` 步（M10-15）需要一份**可复现、脱敏、原子落盘**的机器
可读演练证据（gate/step/schema_version/manifest_sha256/created_at/
restore_drill.verified/inserted_rows）。此前该证据只能人工拼装——本工具把
「对隔离库执行迁移 + 完整恢复 + 恢复后只读全量导出比对」编排成一条命令，
产出同契约的 JSON（复用 M6-06 的 run_backup/run_restore/manifest 校验语义，
零重构）。

安全护栏（全部先于任何数据库访问执行；违例 exit 2、不迁移/不恢复/不写输出）：

- 备份目录必须在 gitignore 的 artifacts/temp 内（复用 is_safe_artifact_path）、
  为真目录（symlink/普通文件拒绝），且已含 manifest.json/database.json
  （常规文件、非 symlink、合法 JSON 对象、schema_version=aios-backup-v1、
  tables 为非负整数计数表）；
- 恢复目标 URL 必须通过 app.db.test_gate.evaluate_pg_test_url 白名单
  （ai_learning_os_test / ai_learning_os_drill 及其 ``<名>_`` 前缀变体）——
  纯解析零连接：主库 ai_learning_os、维护库 postgres、缺库名、非 PG 一律在
  建立任何连接前拒绝（M10-04 门控全仓库唯一实现，不在此复制规则）；
- 输出必须在 artifacts/temp 内，且**不得位于备份目录内、不得等于备份目录**——
  备份目录的精确文件集是 manifest 校验面（verify_manifest 逐文件 sha256 +
  文件集全等），向其添加任何文件都会破坏未来对该备份的完整性校验；
- 演练只写隔离目标库：alembic upgrade head 子进程显式注入
  ``DATABASE_URL=<隔离URL>``；run_restore 的完整性校验先行（hash 不符
  fail-closed 拒绝恢复）；工具不读取/不修改备份源库（备份目录之外零数据库
  读访问），备份目录字节保持不变。

诚实语义（不得伪装 pass）：

- verified=true 仅当（a）恢复后对隔离目标的只读全量导出（dump_database）与
  备份 database.json 逻辑数据全等——表集合一致且逐表行多重集合全等（行序
  无关：关系表的行序不属于逻辑数据，PG 无 ORDER BY select 的返回序无保证）
  ——且（b）inserted_rows == manifest.tables 计数总和；
- 行数或数据不一致 => verified=false 证据照常原子落盘、exit 1（如实记录
  失败，绝不包装成 pass）；
- 迁移/恢复/导出执行失败 => 错误先抹 ``://user:pass@`` 凭据再输出、exit 2、
  不产证据（旧 evidence 文件字节原样保留、无 .tmp 残留——CLI 复用共享原子写）。

输出零敏感：证据只含白名单标量——不含 DB URL、凭据、备份内容、表名/业务 ID
（manifest.tables 只取计数总和）。manifest_sha256 是备份 manifest.json 文件
字节的 SHA-256（内容哈希绑定）。created_at 是证据生成时刻（演练执行时间，
即被审计事件发生时间；备份自身的 created_at 已被 manifest_sha256 哈希覆盖）。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.db.session import create_engine
from app.db.test_gate import evaluate_pg_test_url
from app.ops.backup import (
    DATABASE_FILE,
    MANIFEST_FILE,
    SCHEMA_VERSION,
    dump_database,
    run_restore,
)
from app.ops.legacy_papers import is_safe_artifact_path
from app.ops.production_preflight import redact_secrets

#: 证据自声明：readiness 消费 gate、rehearsal 消费 step（同 release-check 模式）
GATE_ID = "backup-restore"
STEP_ID = "backup-restore"
TOOL_ID = "backup-restore-evidence"

_NO_EXECUTION_NOTE = (
    "护栏先于执行：输入或路径问题不迁移、不恢复、不写输出；"
    "演练只写隔离目标库，备份目录字节保持不变"
)


class DrillInputError(Exception):
    """输入/护栏问题（CLI exit 2：不迁移、不恢复、不写输出）。"""


class DrillExecutionError(Exception):
    """迁移/恢复执行失败（CLI exit 2：不产证据；消息已抹凭据）。"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _path_within(child: Path, parent: Path) -> bool:
    """child 是否等于 parent 或位于 parent 内（resolve 后 normcase 比较，
    Windows 大小写不敏感路径与 .. 形态一并归一）。"""
    kid = os.path.normcase(str(child.resolve()))
    root = os.path.normcase(str(parent.resolve()))
    return kid == root or kid.startswith(root + os.sep)


def _load_backup_json(backup_dir: Path, name: str, *, label: str) -> Any:
    """读取并解析备份目录内 JSON 文件（护栏阶段即拒绝非法输入）。"""
    path = backup_dir / name
    if path.is_symlink():
        raise DrillInputError(f"备份文件 {name} 是符号链接，拒绝读取")
    if not path.is_file():
        raise DrillInputError(f"备份目录缺 {name}: {backup_dir}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as cause:
        raise DrillInputError(f"{label} 无法读取或不是合法 JSON: {cause}") from None


def validate_drill_inputs(
    backup_dir: str | Path, restore_db_url: str, output_path: str | Path
) -> None:
    """全部护栏先行（fail-closed）：任何违例在数据库连接前拒绝。

    通过后调用方才启动迁移/恢复子进程与引擎。
    """
    backup = Path(backup_dir)
    output = Path(output_path)
    # 1) 备份目录：gitignore artifacts/temp 内的真目录，manifest/database 齐备且结构合法
    if not is_safe_artifact_path(backup):
        raise DrillInputError(
            f"备份目录必须位于 gitignore 的 artifacts/ 或 temp/ 目录: {backup}"
        )
    if backup.is_symlink():
        raise DrillInputError(f"备份目录是符号链接，拒绝使用: {backup}")
    if not backup.is_dir():
        raise DrillInputError(f"备份目录不存在或不是目录: {backup}")
    manifest = _load_backup_json(backup, MANIFEST_FILE, label="manifest.json")
    if not isinstance(manifest, dict):
        raise DrillInputError("manifest.json 顶层必须是 JSON 对象")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise DrillInputError(
            f"manifest.json schema_version 非 {SCHEMA_VERSION}: "
            f"{manifest.get('schema_version')!r}"
        )
    tables = manifest.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise DrillInputError("manifest.json 缺 tables 或 tables 不是非空计数表")
    if any(
        isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in tables.values()
    ):
        raise DrillInputError("manifest.json tables 计数必须是非负整数")
    database = _load_backup_json(backup, DATABASE_FILE, label="database.json")
    if not isinstance(database, dict):
        raise DrillInputError("database.json 顶层必须是 JSON 对象")
    # 2) 恢复目标：隔离库白名单（纯解析零连接；拒绝消息不回显完整 URL）
    gate = evaluate_pg_test_url(restore_db_url)
    if not gate.enabled:
        raise DrillInputError(
            "恢复目标库被安全门控拒绝（主库/维护库/缺库名/非 PG 一律在连接前"
            "拒绝，只允许 ai_learning_os_test / ai_learning_os_drill 及其下划线"
            f"前缀变体）: {gate.reason}"
        )
    # 3) 输出：artifacts/temp 内，且不得写入备份目录（保持 manifest 校验面不变）
    if not is_safe_artifact_path(output):
        raise DrillInputError(
            f"输出必须位于 gitignore 的 artifacts/ 或 temp/ 目录: {output}"
        )
    if output.is_symlink():
        raise DrillInputError(f"输出路径是符号链接，拒绝写入: {output}")
    if _path_within(output, backup):
        raise DrillInputError(
            f"输出不得位于备份目录内或等于备份目录: {output}（备份目录的精确"
            "文件集是 manifest 完整性校验面，不得添加文件）"
        )


def _truncate(text: str, limit: int = 600) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _alembic_upgrade_head(db_url: str) -> None:
    """隔离目标库 schema 升级：子进程 alembic upgrade head，DATABASE_URL 显式
    指向隔离目标（覆盖继承环境的任何 DATABASE_URL）。失败抛
    DrillExecutionError（stdout/stderr 合并、截断、抹凭据）。"""
    api_dir = Path(__file__).resolve().parents[2]
    env = {**os.environ, "DATABASE_URL": db_url}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(api_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",  # 子进程输出编码不可控（迁移脚本/DB 错误文案），不因解码失败丢结论
        check=False,
        env=env,
    )
    if proc.returncode != 0:
        parts = [p.strip() for p in (proc.stdout, proc.stderr) if p and p.strip()]
        detail = _truncate(" ".join(parts))
        raise DrillExecutionError(
            redact_secrets(
                f"alembic upgrade head 失败（returncode={proc.returncode}）: {detail}"
            )
        )


async def _dump_target(db_url: str) -> dict[str, list[dict]]:
    """恢复后只读全量导出隔离目标（dump_database：反射全表 select，零写入）。"""
    engine = create_engine(db_url)
    try:
        return await dump_database(engine)
    finally:
        await engine.dispose()


def _canonical_rows(rows: list[dict]) -> list[str]:
    """行集合的规范形态：逐行 JSON 排序键序列化后再排序（行序无关全等）。"""
    return sorted(
        json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        for row in rows
    )


def _logical_data_equal(
    expected: Mapping[str, list[dict]], actual: Mapping[str, list[dict]]
) -> bool:
    """逻辑数据全等：表集合一致 + 逐表行多重集合全等（行序不属于逻辑数据）。"""
    if set(expected) != set(actual):
        return False
    return all(
        _canonical_rows(expected[name]) == _canonical_rows(actual[name])
        for name in expected
    )


async def run_backup_restore_evidence(
    backup_dir: str | Path,
    restore_db_url: str,
    *,
    migrate: Callable[[str], None] | None = None,
    restore: Callable[..., Awaitable[int]] | None = None,
    dump: Callable[[str], Awaitable[dict[str, list[dict]]]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[dict[str, Any], int]:
    """执行演练并组装证据（migrate/restore/dump/clock 可注入，测试不连 PG）。

    返回 (evidence, exit_code)：verified=true -> 0；verified=false -> 1
    （证据照常返回，由 CLI 原子落盘）。迁移/恢复失败直接上抛（CLI exit 2、
    不产证据）。
    """
    backup = Path(backup_dir)
    manifest_sha256 = hashlib.sha256((backup / MANIFEST_FILE).read_bytes()).hexdigest()
    manifest = json.loads((backup / MANIFEST_FILE).read_text(encoding="utf-8"))
    expected_data = json.loads((backup / DATABASE_FILE).read_text(encoding="utf-8"))
    migrate_fn = migrate or _alembic_upgrade_head
    restore_fn = restore or run_restore
    dump_fn = dump or _dump_target
    # 只写隔离目标：先迁移 schema，再完整恢复（run_restore 内部完整性校验先行）
    migrate_fn(restore_db_url)
    inserted = await restore_fn(backup, restore_db_url)
    actual_data = await dump_fn(restore_db_url)
    declared_rows = sum(int(count) for count in manifest["tables"].values())
    verified = (
        _logical_data_equal(expected_data, actual_data) and inserted == declared_rows
    )
    evidence: dict[str, Any] = {
        "tool": TOOL_ID,
        "gate": GATE_ID,
        "step": STEP_ID,
        "schema_version": SCHEMA_VERSION,
        "manifest_sha256": manifest_sha256,
        "created_at": (clock or _utc_now)().isoformat(),
        "restore_drill": {"verified": verified, "inserted_rows": inserted},
    }
    return evidence, 0 if verified else 1


def format_drill_summary(evidence: Mapping[str, Any]) -> str:
    """人类可读摘要（无凭据/无 DB URL/无表名与业务 ID）。"""
    drill = evidence["restore_drill"]
    verdict = "VERIFIED" if drill["verified"] else "NOT VERIFIED"
    lines = [
        "backup-restore 恢复演练证据（隔离库演练，只写隔离目标库）",
        f"生成时间: {evidence['created_at']}",
        f"备份 manifest: {evidence['manifest_sha256'][:12]}…（{evidence['schema_version']}）",
        f"RESULT: {verdict}  inserted_rows={drill['inserted_rows']}",
    ]
    if drill["verified"]:
        lines.append(
            "恢复后逻辑数据与备份全等，且回灌行数与 manifest 计数一致——"
            "可恢复证据闭合（本证据只覆盖演练时点的备份目录，不代表持续可恢复）。"
        )
    else:
        lines.append(
            "恢复后逻辑数据与备份不一致，或回灌行数与 manifest 计数不符——"
            "verified=false 如实记录失败，不构成可恢复证据；排查备份/恢复链路后重跑。"
        )
    lines.append(_NO_EXECUTION_NOTE)
    lines.append("退出码: verified=0 / verified=false=1 / 输入或执行错误=2。")
    return "\n".join(lines)
