"""M11-04 backup-restore 演练证据导出器：护栏/演练语义/证据契约/安全边界。

覆盖矩阵：
1. 护栏先于执行（exit 2、不迁移/不恢复/不写输出，注入的 fake 一次都不能被
   调用）：备份目录不在 artifacts/temp、缺 manifest.json/database.json、
   manifest 结构非法（非法 JSON / schema_version 错 / tables 非计数表）、
   备份目录是 symlink、恢复目标 URL 指向主库/维护库/缺库名/非 PG、输出不在
   artifacts/temp、输出位于备份目录内或等于备份目录、输出是 symlink；
2. 成功路径：隔离库迁移 -> 完整恢复 -> 只读导出与 database.json 逻辑数据
   全等 + inserted_rows 对账 manifest.tables 总和 => verified=true、exit 0、
   证据字段契约（gate/step 双自声明、manifest_sha256 == manifest.json 字节
   sha256、created_at ISO、restore_drill 两字段）；真实 SQLite 端到端演练
   （真 run_restore + 真 dump，无 PG 依赖）；备份目录字节全程不变；
3. 诚实失败：inserted_rows 与 manifest 计数不符 / 恢复后数据不一致 =>
   verified=false 证据照常落盘、exit 1（真实 SQLite 数据不一致路径 + 注入
   路径都锁定）；行序无关（恢复后行序打乱仍全等）；
4. 执行失败：迁移失败 / 恢复完整性校验失败 / 导出连接失败 => exit 2、不产
   证据、旧 evidence 字节原样保留、无 .tmp 残留、不打印演练结论；
5. 证据契约与下游消费：verified=true 直接过 release-readiness
   _eval_backup_restore（pass）与 cutover-rehearsal _step_backup_restore
   （pass）；verified=false => pending；落盘文件经完整 manifest 工具消费同
   语义；敏感键/内嵌凭据扫描零命中；
6. 零敏感：含密码 marker 的 drill URL 不进任何输出（stdout/stderr/证据文件），
   表名/业务 ID 不进证据；
7. CLI 注册与 --json：argparse 真实注册（--backup-dir/--restore-db-url/
   --output/--json 解析进 handler）、main 分发、--help 列出子命令、
   --json 时 stdout 纯 JSON 且「报告已写入」提示走 stderr。

单测注入 fake 迁移/恢复/导出，不连 PG；真实 PG 不强制（跳过门控与
test_backup_drill 的 PG drill 既有覆盖一致）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ops import cli as cli_module
from app.ops.backup import BackupIntegrityError
from app.ops.backup_restore_evidence import (
    DrillExecutionError,
    DrillInputError,
    format_drill_summary,
    run_backup_restore_evidence,
    validate_drill_inputs,
)

#: 生产密钥 marker：任何输出（JSON / 摘要 / stderr / 证据文件）都不得包含
SECRET_MARKER = "PROD-PW-88d9"
#: 隔离 drill 库 URL（密码段带 marker，验证脱敏面）
DRILL_URL = (
    f"postgresql+asyncpg://aios:{SECRET_MARKER}@127.0.0.1:5433/ai_learning_os_drill"
)

BACKUP_DATA = {
    "papers": [
        {"id": "paper-1", "title": "functions-basics", "owner_id": None},
        {"id": "paper-2", "title": "derivatives", "owner_id": None},
    ],
    "exam_sessions": [{"id": "exam-1", "paper_id": "paper-1", "status": "submitted"}],
}


def _make_backup(
    tmp_path: Path, *, data: dict | None = None, tables: dict | None = None
) -> Path:
    """在 artifacts/backup 造一份结构合法的最小备份（manifest + database）。"""
    backup = tmp_path / "artifacts" / "backup"
    backup.mkdir(parents=True, exist_ok=True)
    payload = BACKUP_DATA if data is None else data
    (backup / "database.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    counts = (
        {name: len(rows) for name, rows in payload.items()}
        if tables is None
        else tables
    )
    manifest = {
        "schema_version": "aios-backup-v1",
        "created_at": "2026-09-05T00:00:00+00:00",
        "tables": counts,
        "files": {
            "database.json": hashlib.sha256(
                (backup / "database.json").read_bytes()
            ).hexdigest()
        },
    }
    (backup / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return backup


def _rewrite_manifest(
    backup: Path,
    *,
    schema_version: str | None = None,
    drop_tables: bool = False,
    counts: dict | None = None,
) -> None:
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    if schema_version is not None:
        manifest["schema_version"] = schema_version
    if drop_tables:
        manifest.pop("tables", None)
    if counts is not None:
        manifest["tables"] = counts
    (backup / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )


def _output_path(tmp_path: Path, name: str = "backup-restore.json") -> Path:
    return tmp_path / "artifacts" / name


class _Fakes:
    """记录调用并可脚本化结果的迁移/恢复/导出替身（不连任何数据库）。"""

    def __init__(
        self,
        *,
        inserted: int | None = None,
        dumped: dict | None = None,
        migrate_error: Exception | None = None,
        restore_error: Exception | None = None,
        dump_error: Exception | None = None,
    ) -> None:
        self.inserted = (
            sum(len(rows) for rows in BACKUP_DATA.values())
            if inserted is None
            else inserted
        )
        self.dumped = dict(BACKUP_DATA) if dumped is None else dumped
        self.migrate_error = migrate_error
        self.restore_error = restore_error
        self.dump_error = dump_error
        self.migrate_calls: list[str] = []
        self.restore_calls: list[tuple[str, str]] = []
        self.dump_calls: list[str] = []

    def migrate(self, db_url: str) -> None:
        self.migrate_calls.append(db_url)
        if self.migrate_error is not None:
            raise self.migrate_error

    async def restore(self, backup_dir, db_url: str) -> int:
        self.restore_calls.append((str(backup_dir), db_url))
        if self.restore_error is not None:
            raise self.restore_error
        return self.inserted

    async def dump(self, db_url: str) -> dict:
        self.dump_calls.append(db_url)
        if self.dump_error is not None:
            raise self.dump_error
        return self.dumped


def _patch_fakes(monkeypatch, fakes: _Fakes) -> None:
    """把替身装进 backup_restore_evidence 模块命名空间（默认实现在调用点
    才解析模块全局——monkeypatch 即生效，真实 PG/alembic 子进程零触发）。"""
    from app.ops import backup_restore_evidence as bre

    monkeypatch.setattr(bre, "_alembic_upgrade_head", fakes.migrate)
    monkeypatch.setattr(bre, "run_restore", fakes.restore)
    monkeypatch.setattr(bre, "_dump_target", fakes.dump)


def _cli(backup: Path, output: Path, *, url: str = DRILL_URL, as_json: bool = False):
    return cli_module._run_backup_restore_evidence(
        SimpleNamespace(
            backup_dir=str(backup),
            restore_db_url=url,
            output=str(output),
            as_json=as_json,
        )
    )


def _assert_no_execution(fakes: _Fakes, artifacts: Path) -> None:
    assert fakes.migrate_calls == []
    assert fakes.restore_calls == []
    assert fakes.dump_calls == []
    assert list(artifacts.glob("*.json")) == [], "不得产生任何输出文件"


# --- 1. 护栏先于执行（exit 2、零调用、零输出） ---------------------------------


def test_guardrails_run_before_any_execution(tmp_path, monkeypatch, capsys) -> None:
    """四类护栏违例逐一见 exit 2：迁移/恢复/导出零调用、输出文件不产生、
    拒绝消息不回显 URL 凭据。"""
    fakes = _Fakes()
    _patch_fakes(monkeypatch, fakes)
    cases: list[tuple[Path, Path, str]] = []

    # 备份目录不在 artifacts/temp
    outside = tmp_path / "elsewhere" / "backup"
    outside.mkdir(parents=True)
    (outside / "manifest.json").write_text("{}", encoding="utf-8")
    (outside / "database.json").write_text("{}", encoding="utf-8")
    cases.append((outside, _output_path(tmp_path), DRILL_URL))

    # 缺 manifest.json / database.json
    no_manifest = tmp_path / "artifacts" / "no-manifest"
    no_manifest.mkdir(parents=True)
    (no_manifest / "database.json").write_text("{}", encoding="utf-8")
    cases.append((no_manifest, _output_path(tmp_path), DRILL_URL))
    no_db = tmp_path / "artifacts" / "no-db"
    no_db.mkdir(parents=True)
    (no_db / "manifest.json").write_text("{}", encoding="utf-8")
    cases.append((no_db, _output_path(tmp_path), DRILL_URL))

    # 输出不在 artifacts/temp
    backup = _make_backup(tmp_path)
    cases.append((backup, tmp_path / "out" / "ev.json", DRILL_URL))

    # 输出位于备份目录内 / 等于备份目录
    cases.append((backup, backup / "evidence.json", DRILL_URL))
    cases.append((backup, backup, DRILL_URL))

    # 恢复目标：主库 / 维护库 / 缺库名 / 非 PG / 空
    for bad_url in (
        f"postgresql+asyncpg://aios:{SECRET_MARKER}@127.0.0.1:5433/ai_learning_os",
        "postgresql+asyncpg://aios:x@127.0.0.1:5433/postgres",
        "postgresql+asyncpg://aios:x@127.0.0.1:5433/",
        "sqlite+aiosqlite:///" + str(tmp_path / "t.db").replace("\\", "/"),
        "",
    ):
        cases.append((backup, _output_path(tmp_path), bad_url))

    for backup_arg, output_arg, url in cases:
        code = _cli(backup_arg, output_arg, url=url)
        assert code == 2, (backup_arg, output_arg, url)
        text = capsys.readouterr().out
        assert "拒绝执行" in text
        if SECRET_MARKER in url:
            assert SECRET_MARKER not in text, "拒绝消息不得回显完整 URL 凭据"
    _assert_no_execution(fakes, tmp_path / "artifacts")


@pytest.mark.parametrize(
    ("mutate", "problem"),
    [
        (
            lambda b: (b / "manifest.json").write_text("{broken", encoding="utf-8"),
            "合法 JSON",
        ),
        (
            lambda b: (b / "manifest.json").write_text("[]", encoding="utf-8"),
            "JSON 对象",
        ),
        (
            lambda b: _rewrite_manifest(b, schema_version="aios-backup-v2"),
            "schema_version",
        ),
        (lambda b: _rewrite_manifest(b, drop_tables=True), "tables"),
        (lambda b: _rewrite_manifest(b, counts={"papers": "many"}), "非负整数"),
        (
            lambda b: (b / "database.json").write_text("[1,2]", encoding="utf-8"),
            "JSON 对象",
        ),
    ],
)
def test_guardrail_rejects_malformed_backup(
    tmp_path, monkeypatch, mutate, problem
) -> None:
    """manifest/database 结构非法（含 schema_version 与计数表形态）一律
    exit 2，先于任何执行。"""
    fakes = _Fakes()
    _patch_fakes(monkeypatch, fakes)
    backup = _make_backup(tmp_path)
    mutate(backup)
    assert _cli(backup, _output_path(tmp_path)) == 2
    _assert_no_execution(fakes, tmp_path / "artifacts")


def test_guardrail_rejects_symlink_backup_dir(tmp_path, monkeypatch) -> None:
    """备份目录是 symlink：fail-closed（Windows 无特权环境跳过）。"""
    fakes = _Fakes()
    _patch_fakes(monkeypatch, fakes)
    real = _make_backup(tmp_path)
    link = tmp_path / "artifacts" / "link-backup"
    try:
        os.symlink(real, link, target_is_directory=True)
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    assert _cli(link, _output_path(tmp_path)) == 2
    assert fakes.migrate_calls == []


def test_guardrail_rejects_symlink_output(tmp_path, monkeypatch) -> None:
    """输出是 symlink：护栏在演练前拒绝（不写穿目标）。"""
    fakes = _Fakes()
    _patch_fakes(monkeypatch, fakes)
    backup = _make_backup(tmp_path)
    victim = _output_path(tmp_path, "real.json")
    victim.write_text("{}", encoding="utf-8")
    link = _output_path(tmp_path)
    try:
        os.symlink(victim, link)
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    assert _cli(backup, link) == 2
    assert victim.read_text(encoding="utf-8") == "{}"


def test_guardrail_accepts_whitelisted_variants(tmp_path) -> None:
    """白名单库名（含下划线前缀变体）合法通过护栏；前缀必须带下划线边界。"""
    backup = _make_backup(tmp_path)
    output = _output_path(tmp_path)
    for name in ("ai_learning_os_test", "ai_learning_os_drill", "ai_learning_os_test_2"):
        validate_drill_inputs(
            backup, f"postgresql+asyncpg://u:p@127.0.0.1:5433/{name}", output
        )
    with pytest.raises(DrillInputError):
        validate_drill_inputs(
            backup,
            "postgresql+asyncpg://u:p@127.0.0.1:5433/ai_learning_os_tests",
            output,
        )


# --- 2. 成功路径 ---------------------------------------------------------------


def test_success_verified_true_exit_0(tmp_path, monkeypatch, capsys) -> None:
    """迁移->恢复->全等：verified=true、exit 0、证据契约完整、备份目录
    字节不变、迁移/恢复只打隔离 URL。"""
    fakes = _Fakes()
    _patch_fakes(monkeypatch, fakes)
    backup = _make_backup(tmp_path)
    before = {
        p.name: p.read_bytes() for p in backup.rglob("*") if p.is_file()
    }
    output = _output_path(tmp_path)
    code = _cli(backup, output)
    assert code == 0
    assert fakes.migrate_calls == [DRILL_URL]
    assert fakes.restore_calls == [(str(backup), DRILL_URL)]
    assert fakes.dump_calls == [DRILL_URL]
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["gate"] == "backup-restore"
    assert evidence["step"] == "backup-restore"
    assert evidence["schema_version"] == "aios-backup-v1"
    assert evidence["manifest_sha256"] == hashlib.sha256(
        (backup / "manifest.json").read_bytes()
    ).hexdigest()
    datetime.fromisoformat(evidence["created_at"])
    assert evidence["restore_drill"] == {
        "verified": True,
        "inserted_rows": 3,
    }
    after = {p.name: p.read_bytes() for p in backup.rglob("*") if p.is_file()}
    assert after == before, "备份目录字节必须全程不变"
    out = capsys.readouterr().out
    assert "VERIFIED" in out and "inserted_rows=3" in out


def test_runner_evidence_shape_with_injected_clock(tmp_path) -> None:
    """runner 纯函数面：注入 clock/migrate/restore/dump，字段契约与退出码
    确定（不经过 CLI，不写文件）。"""
    backup = _make_backup(tmp_path)
    moment = datetime(2026, 9, 5, 8, 0, 0, tzinfo=UTC)
    evidence, code = asyncio.run(
        run_backup_restore_evidence(
            backup,
            "postgresql+asyncpg://u:p@127.0.0.1:5433/ai_learning_os_drill",
            migrate=lambda url: None,
            restore=_fake_restore,
            dump=_fake_dump,
            clock=lambda: moment,
        )
    )
    assert code == 0
    assert evidence["created_at"] == "2026-09-05T08:00:00+00:00"
    assert set(evidence) == {
        "tool",
        "gate",
        "step",
        "schema_version",
        "manifest_sha256",
        "created_at",
        "restore_drill",
    }


async def _fake_restore(backup_dir, db_url: str) -> int:
    return sum(len(rows) for rows in BACKUP_DATA.values())


async def _fake_dump(db_url: str) -> dict:
    return BACKUP_DATA


def test_row_order_is_not_logical_data(tmp_path) -> None:
    """行序不属于逻辑数据：恢复后行序打乱/键序不同仍全等 verified=true。"""

    async def _dump_shuffled(db_url: str) -> dict:
        return {
            "papers": [
                dict(reversed(list(row.items())))
                for row in reversed(BACKUP_DATA["papers"])
            ],
            "exam_sessions": BACKUP_DATA["exam_sessions"],
        }

    backup = _make_backup(tmp_path)
    evidence, code = asyncio.run(
        run_backup_restore_evidence(
            backup,
            DRILL_URL,
            migrate=lambda url: None,
            restore=_fake_restore,
            dump=_dump_shuffled,
        )
    )
    assert code == 0 and evidence["restore_drill"]["verified"] is True


def test_real_sqlite_end_to_end_drill_verified(tmp_path) -> None:
    """真实链路（无 PG）：SQLAlchemy 建两表造数 -> run_backup -> 对第二个
    SQLite 库跑真 run_restore + 真 dump 全等对账 => verified=true。"""
    from sqlalchemy import Column, MetaData, String, Table, text
    from sqlalchemy import create_engine as sync_engine

    from app.db.session import create_engine as async_engine
    from app.ops.backup import dump_database, run_backup

    meta = MetaData()
    Table("papers", meta, Column("id", String, primary_key=True), Column("title", String))
    Table(
        "exam_sessions",
        meta,
        Column("id", String, primary_key=True),
        Column("paper_id", String),
    )
    source = sync_engine("sqlite:///" + str(tmp_path / "source.db"))
    meta.create_all(source)
    with source.begin() as conn:
        conn.execute(
            text("INSERT INTO papers (id, title) VALUES ('p1', 't1'), ('p2', 't2')")
        )
        conn.execute(
            text("INSERT INTO exam_sessions (id, paper_id) VALUES ('e1', 'p1')")
        )
    source_url = "sqlite+aiosqlite:///" + str(tmp_path / "source.db")
    backup = tmp_path / "artifacts" / "backup-real"
    backup.mkdir(parents=True)
    asyncio.run(run_backup(source_url, backup, None, []))
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))

    target_path = tmp_path / "target.db"
    target_url = "sqlite+aiosqlite:///" + str(target_path)
    sync_engine("sqlite:///" + str(target_path))  # 目标库已存在（前置条件）
    meta.create_all(sync_engine("sqlite:///" + str(target_path)))  # schema 就位

    evidence, code = asyncio.run(
        run_backup_restore_evidence(
            backup,
            target_url,
            migrate=lambda url: None,  # schema 已就位；真实迁移语义由注入测试覆盖
        )
    )
    assert code == 0
    assert evidence["restore_drill"] == {
        "verified": True,
        "inserted_rows": sum(manifest["tables"].values()),
    }
    engine = async_engine(target_url)
    dumped = asyncio.run(dump_database(engine))
    assert len(dumped["papers"]) == 2
    asyncio.run(engine.dispose())


# --- 3. 诚实失败（verified=false => 证据照常落盘、exit 1） ----------------------


def test_inserted_rows_mismatch_verified_false_exit_1(tmp_path, monkeypatch) -> None:
    """行数对账失败：manifest 计数总和 != 实际回灌行数 => verified=false、
    证据照常原子落盘、exit 1。"""
    fakes = _Fakes(inserted=2)  # 备份实际 3 行
    _patch_fakes(monkeypatch, fakes)
    backup = _make_backup(tmp_path)
    output = _output_path(tmp_path)
    code = _cli(backup, output)
    assert code == 1
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["restore_drill"] == {"verified": False, "inserted_rows": 2}


def test_data_mismatch_verified_false_exit_1(tmp_path, monkeypatch, capsys) -> None:
    """恢复后数据不一致（多一行/值被改/多一张表）=> verified=false、exit 1，
    摘要写明失败不伪装。"""
    variants = [
        {
            **BACKUP_DATA,
            "papers": BACKUP_DATA["papers"] + [{"id": "paper-3"}],
        },
        {
            **BACKUP_DATA,
            "exam_sessions": [{"id": "exam-1", "paper_id": "paper-1", "status": "active"}],
        },
        {**BACKUP_DATA, "extra_table": [{"x": 1}]},
    ]
    for dumped in variants:
        fakes = _Fakes(dumped=dumped)
        _patch_fakes(monkeypatch, fakes)
        backup = _make_backup(tmp_path)
        output = _output_path(tmp_path)
        capsys.readouterr()
        assert _cli(backup, output) == 1
        evidence = json.loads(output.read_text(encoding="utf-8"))
        assert evidence["restore_drill"]["verified"] is False
        assert "NOT VERIFIED" in capsys.readouterr().out


def test_real_sqlite_rowcount_mismatch_verified_false(tmp_path) -> None:
    """真实数据路径的行数不一致：篡改 manifest 计数（manifest 不在自身校验
    面）=> 真恢复成功但计数对账失败 => verified=false。"""
    from sqlalchemy import Column, MetaData, String, Table, text
    from sqlalchemy import create_engine as sync_engine

    from app.ops.backup import run_backup

    meta = MetaData()
    Table("papers", meta, Column("id", String, primary_key=True))
    source = sync_engine("sqlite:///" + str(tmp_path / "s.db"))
    meta.create_all(source)
    with source.begin() as conn:
        conn.execute(text("INSERT INTO papers (id) VALUES ('p1'), ('p2')"))
    backup = tmp_path / "artifacts" / "backup-tampered"
    backup.mkdir(parents=True)
    asyncio.run(
        run_backup("sqlite+aiosqlite:///" + str(tmp_path / "s.db"), backup, None, [])
    )
    _rewrite_manifest(backup, counts={"papers": 5})  # 申报 5 行，实际 2 行
    target_url = "sqlite+aiosqlite:///" + str(tmp_path / "t.db")
    meta.create_all(sync_engine("sqlite:///" + str(tmp_path / "t.db")))
    evidence, code = asyncio.run(
        run_backup_restore_evidence(
            backup, target_url, migrate=lambda url: None
        )
    )
    assert code == 1
    assert evidence["restore_drill"] == {"verified": False, "inserted_rows": 2}


# --- 4. 执行失败（exit 2、不产证据、旧 evidence 完整、无 tmp） ------------------


def _old_evidence(tmp_path: Path) -> Path:
    output = _output_path(tmp_path)
    output.write_text('{"old": true}', encoding="utf-8")
    return output


def test_migration_failure_exit_2_keeps_old_evidence(
    tmp_path, monkeypatch, capsys
) -> None:
    """alembic 迁移失败：exit 2、旧 evidence 字节原样、无 .tmp、不打印演练
    结论、错误消息抹凭据。"""
    fakes = _Fakes(
        migrate_error=DrillExecutionError(
            "alembic upgrade head 失败: postgresql+asyncpg://aios:"
            + SECRET_MARKER
            + "@127.0.0.1:5433/ai_learning_os_drill 连接拒绝"
        )
    )
    _patch_fakes(monkeypatch, fakes)
    backup = _make_backup(tmp_path)
    output = _old_evidence(tmp_path)
    code = _cli(backup, output)
    assert code == 2
    captured = capsys.readouterr()
    assert "恢复演练执行失败" in captured.out
    assert SECRET_MARKER not in captured.out, "异常消息必须先抹凭据再输出"
    assert "VERIFIED" not in captured.out, "失败不得打印演练结论"
    assert captured.err == ""  # 无 traceback
    assert output.read_text(encoding="utf-8") == '{"old": true}'
    assert list((tmp_path / "artifacts").glob("*.tmp")) == []
    assert fakes.restore_calls == [], "迁移失败不得继续恢复"


def test_restore_integrity_failure_exit_2(tmp_path, monkeypatch, capsys) -> None:
    """run_restore 完整性校验失败（BackupIntegrityError）：exit 2、不产证据。"""
    fakes = _Fakes(restore_error=BackupIntegrityError("备份文件 hash 不符: database.json"))
    _patch_fakes(monkeypatch, fakes)
    backup = _make_backup(tmp_path)
    output = _old_evidence(tmp_path)
    assert _cli(backup, output) == 2
    assert "备份完整性校验失败" in capsys.readouterr().out
    assert output.read_text(encoding="utf-8") == '{"old": true}'
    assert fakes.dump_calls == [], "恢复失败不得继续导出"


def test_dump_failure_exit_2(tmp_path, monkeypatch, capsys) -> None:
    """恢复后导出失败（连接/IO）：exit 2、不产证据、旧文件保留。"""
    fakes = _Fakes(dump_error=OSError("connection reset"))
    _patch_fakes(monkeypatch, fakes)
    backup = _make_backup(tmp_path)
    output = _old_evidence(tmp_path)
    assert _cli(backup, output) == 2
    assert "连接或 IO 问题" in capsys.readouterr().out
    assert output.read_text(encoding="utf-8") == '{"old": true}'


def test_output_write_failure_keeps_old_evidence(tmp_path, monkeypatch, capsys) -> None:
    """演练成功但原子写失败：exit 2、旧文件字节原样、无 .tmp、不打印演练
    结论（防半途结论被误读）。"""
    fakes = _Fakes()
    _patch_fakes(monkeypatch, fakes)
    backup = _make_backup(tmp_path)
    output = _old_evidence(tmp_path)

    def boom(path, text):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cli_module, "_write_report_atomic", boom)
    assert _cli(backup, output) == 2
    captured = capsys.readouterr()
    assert "报告写入失败" in captured.out
    assert "VERIFIED" not in captured.out
    assert output.read_text(encoding="utf-8") == '{"old": true}'
    assert list((tmp_path / "artifacts").glob("*.tmp")) == []


# --- 5. 证据契约与下游消费 ------------------------------------------------------


def _verified_evidence(verified: bool, inserted: int = 3) -> dict:
    return {
        "tool": "backup-restore-evidence",
        "gate": "backup-restore",
        "step": "backup-restore",
        "schema_version": "aios-backup-v1",
        "manifest_sha256": "ab" * 32,
        "created_at": "2026-09-05T08:00:00+00:00",
        "restore_drill": {"verified": verified, "inserted_rows": inserted},
    }


@pytest.mark.parametrize("verified", [True, False])
def test_generated_evidence_consumed_by_evaluators(verified) -> None:
    """生成的证据（verified 两种形态）直接过两个消费方评估器：白名单字段
    可提取、扩展字段（tool）不致 malformed；verified=true => pass、
    false => pending（不伪装）。"""
    from app.ops.cutover_rehearsal import _step_backup_restore
    from app.ops.evidence_kit import find_embedded_credential, find_sensitive_key
    from app.ops.release_readiness import _eval_backup_restore

    for evidence in (
        _verified_evidence(verified),
        asyncio.run(_drill_evidence(tmp_path=None, verified=verified)),
    ):
        assert find_sensitive_key(evidence) is None
        assert find_embedded_credential(evidence) is None
        status, _, data, _ = _eval_backup_restore(evidence, Path("."), {})
        status2, _, _, data2, _ = _step_backup_restore(evidence, Path("."), {})
        expected = "pass" if verified else "pending"
        assert status == expected and status2 == expected
        assert data["restore_verified"] is verified
        assert data2["inserted_rows"] == evidence["restore_drill"]["inserted_rows"]


async def _drill_evidence(tmp_path, verified: bool) -> dict:
    """真实 runner 组装的同形态证据（verified 由 dump/restore 决定）。"""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        backup = _make_backup(root)
        if not verified:
            _rewrite_manifest(backup, counts={"papers": 9, "exam_sessions": 9})
        evidence, _ = await run_backup_restore_evidence(
            backup,
            DRILL_URL,
            migrate=lambda url: None,
            restore=_fake_restore,
            dump=_fake_dump,
        )
        return evidence


@pytest.mark.parametrize("verified", [True, False])
def test_generated_evidence_file_consumed_by_full_manifest_tools(
    tmp_path, verified
) -> None:
    """落盘的生成证据经完整（非裁剪）manifest 工具消费：readiness 的
    backup-restore 门与 rehearsal 的 backup-restore 步给出一致状态
    （pass / pending），绝不 malformed/missing——同一份导出文件同时满足
    两侧的自声明（gate/step）。"""
    from app.ops.cutover_rehearsal import run_cutover_rehearsal
    from app.ops.release_readiness import run_release_readiness

    evidence_text = json.dumps(_verified_evidence(verified), ensure_ascii=False)

    dir_ready = tmp_path / "readiness"
    dir_ready.mkdir()
    (dir_ready / "backup-restore.json").write_text(evidence_text, encoding="utf-8")
    readiness = run_release_readiness(dir_ready)
    gate = next(g for g in readiness["gates"] if g["gate"] == "backup-restore")
    assert gate["status"] == ("pass" if verified else "pending")
    assert gate["evidence"]["sha256"] == hashlib.sha256(
        (dir_ready / "backup-restore.json").read_bytes()
    ).hexdigest()

    dir_rehearsal = tmp_path / "rehearsal"
    dir_rehearsal.mkdir()
    (dir_rehearsal / "backup-restore.json").write_text(evidence_text, encoding="utf-8")
    rehearsal = run_cutover_rehearsal(dir_rehearsal)
    step = next(s for s in rehearsal["steps"] if s["step"] == "backup-restore")
    assert step["status"] == ("pass" if verified else "pending")
    assert rehearsal["exit_code"] == 1 and not rehearsal["rehearsal_ready"]


# --- 6. 零敏感 ------------------------------------------------------------------


def test_outputs_contain_no_secrets_or_business_ids(
    tmp_path, monkeypatch, capsys
) -> None:
    """含密码 marker 的 drill URL 不进 stdout/stderr/证据文件；表名与
    业务 ID 不进证据（只输出计数与哈希）。"""
    fakes = _Fakes()
    _patch_fakes(monkeypatch, fakes)
    backup = _make_backup(tmp_path)
    output = _output_path(tmp_path)
    assert _cli(backup, output, as_json=True) == 0
    captured = capsys.readouterr()
    stdout_json = json.loads(captured.out)  # stdout 是纯 JSON
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert stdout_json == evidence
    dumped = json.dumps(evidence, ensure_ascii=False)
    for surface in (dumped, captured.out, captured.err):
        assert SECRET_MARKER not in surface
        assert "postgresql" not in surface and "5433" not in surface
    for table_name in BACKUP_DATA:
        assert table_name not in dumped, "表名不得进证据"
    assert "paper-1" not in dumped and "functions-basics" not in dumped
    assert "报告已写入" in captured.err and captured.err.count("报告已写入") == 1


def test_summary_and_evidence_have_no_sensitive_keys(tmp_path) -> None:
    """人类摘要与证据 JSON 的敏感面：字段名不含敏感键模式。"""
    evidence = _verified_evidence(True)
    summary = format_drill_summary(evidence)
    assert "VERIFIED" in summary and "退出码" in summary
    assert SECRET_MARKER not in summary


# --- 7. CLI 注册与 --json --------------------------------------------------------


def test_cli_args_registered(monkeypatch, tmp_path) -> None:
    """argparse 真实注册：--backup-dir/--restore-db-url/--output/--json 解析
    进 handler 并进入 main 分发。"""
    import pytest as _pytest

    captured: dict = {}

    def fake_run(args) -> int:
        captured.update(vars(args))
        return 0

    monkeypatch.setattr(cli_module, "_run_backup_restore_evidence", fake_run)
    backup = _make_backup(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "app.ops.cli",
            "backup-restore-evidence",
            "--backup-dir",
            str(backup),
            "--restore-db-url",
            DRILL_URL,
            "--output",
            str(_output_path(tmp_path)),
            "--json",
        ],
    )
    with _pytest.raises(SystemExit) as exc_info:
        cli_module.main()
    assert exc_info.value.code == 0
    assert captured["command"] == "backup-restore-evidence"
    assert captured["backup_dir"] == str(backup)
    assert captured["restore_db_url"] == DRILL_URL
    assert captured["output"] == str(_output_path(tmp_path))
    assert captured["as_json"] is True


def test_cli_help_lists_subcommand() -> None:
    """python -m app.ops.cli --help 列出 backup-restore-evidence（端到端
    注册证明，无需数据库）。"""
    proc = subprocess.run(
        [sys.executable, "-m", "app.ops.cli", "--help"],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    assert proc.returncode == 0
    assert "backup-restore-evidence" in (proc.stdout or "")
    assert "M11-04" in (proc.stdout or "")


def test_cli_json_stdout_pure_notice_on_stderr(tmp_path, monkeypatch, capsys) -> None:
    """--json：stdout 纯 JSON 可解析、与落盘文件解析后一致（含墙钟
    created_at 逐字相等）；「报告已写入」提示走 stderr。"""
    fakes = _Fakes()
    _patch_fakes(monkeypatch, fakes)
    backup = _make_backup(tmp_path)
    output = _output_path(tmp_path)
    assert _cli(backup, output, as_json=True) == 0
    captured = capsys.readouterr()
    evidence_file = json.loads(output.read_text(encoding="utf-8"))
    assert json.loads(captured.out) == evidence_file
    assert "报告已写入" in captured.err
    assert "报告已写入" not in captured.out


# --- 8. 操作者指引同步（cutover 时间线与证据包手册指向新命令） -------------------


def test_cutover_guidance_references_new_command() -> None:
    """cutover-rehearsal 的 backup-restore 步动作建议与 cutover-evidence-pack
    手册的来源命令都指向 M11-04 子命令（操作者按文档执行能拿到同契约证据）。"""
    from app.ops.cutover_evidence_pack import MANUAL_ENTRIES
    from app.ops.cutover_rehearsal import STEPS

    spec = next(s for s in STEPS if s.step_id == "backup-restore")
    assert "backup-restore-evidence" in spec.not_executed_action
    assert "--backup-dir" in spec.not_executed_action
    assert "--restore-db-url" in spec.not_executed_action
    manual = MANUAL_ENTRIES["backup-restore"]
    assert "backup-restore-evidence" in manual["source"]
    assert "不含备份内容" in manual["redaction"]
