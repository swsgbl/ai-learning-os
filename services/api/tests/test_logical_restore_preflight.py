r"""M14-232 tools/ops/logical_restore_preflight.py 契约测试：只读逻辑恢复
前置检查，零真实改动、零网络、零 Docker。

覆盖（全部离线：合成备份 fixture + FakeRunner 回放 Docker 只读面——不启动/
停止任何容器、不创建/删除任何卷、不碰真实备份目录）：
- 备份校验 fail-closed 矩阵：位置未批准（fail-fast，不遍历内容）/目录缺失/
  symlink/junction 与嵌套链接形态/schema 漂移/manifest 锚点漂移/表集漂移
  （多表/少表/表数漂移）/文件集漂移（树内多出/声明缺失/计数漂移）/
  逐文件 SHA256 漂移/行数漂移（逐表 + 总数）/期望事实 30 表 41 行 4 文件
  任何漂移即 backup-invalid；
- 只读 Docker 语义：引擎不可用或任何查询失败（含 image inspect 传输失败）
  = docker-state-unreadable 且不下任何卷/镜像结论；任一持久卷在场 =
  existing-data-must-not-be-overwritten；searxng-cache 缺失仅提示；
  必需镜像（aios/minio 自建锚点 + postgres:17-alpine）缺失 =
  required-image-missing；唯一放行形态 = 备份有效 + Docker 可读 + 两卷
  均缺失 + 镜像齐备 = ready-to-reconstruct；
- 分阶段 runbook：仅输出永不执行，覆盖备份复核/镜像/卷缺失复核/显式且仅
  创建两命名卷/仅启动 postgres+minio/alembic 迁移/app.ops.cli restore/
  完整性对账/既有恢复路径全栈/M14-231 preflight + 公网/语音验收；
- secret 抑制：fixture env 文件与 database.json 行值里的 marker secret 绝不
  出现在日志/stdout/JSON 报告（env/compose 备份只做字节哈希，永不解码）；
- 报告零本机绝对路径：盘符/UNC 抹除器单测 + 全报告扫描；URL 不被误伤；
- 源码契约：Docker 面仅 version/volume ls/image inspect 三命令（正则抽取
  argv token 断言），绝不出现 up/down/stop/kill/rm/restart/pull/build/
  run/exec/create/prune 破坏性子命令 token 字面量，绝不直接 subprocess；
- 常量交叉锁定：与 M14-231 production_restore_preflight /
  production_recovery / infra/docker-compose.yml 两侧一致；manifest 锚点
  与 M14-193 证据字面量逐字锁定。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "logical_restore_preflight.py"
M231_SCRIPT = REPO_ROOT / "tools" / "ops" / "production_restore_preflight.py"
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"
SECRET_PATTERNS = ("sk-", "AKIA", "ghp_", "xoxb_", "-----BEGIN")

PROJECT = "aios-m14-03-production-rehearsal"
PG_VOLUME = f"{PROJECT}_postgres-data"
MINIO_VOLUME = f"{PROJECT}_minio-data"
CACHE_VOLUME = f"{PROJECT}_searxng-cache"
MINIO_IMAGE = "aios/minio:RELEASE.2025-10-15T17-29-55Z"
DB_IMAGE = "postgres:17-alpine"
MANIFEST_ANCHOR_LITERAL = (
    "381C49875C23F4CF35F49CED864522A200D6B46614C72D42B52DFB34EB0612EC"
)
MARK_SECRET = "ZX-markerbackupsecret-0123456789abcdef"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


pr = _load_module(SCRIPT, "logical_restore_preflight_under_test")


# ---------------------------------------------------------------- fixture 构建

#: 与真实 M14-193 备份同形的有表行分布（合计 41；其余 17 表 0 行）
ROW_TABLES: dict[str, int] = {
    "users": 6, "sources": 6, "exam_sessions": 7, "questions": 6, "papers": 2,
    "search_queries": 2, "voice_trace_spans": 2, "alembic_version": 1,
    "audit_chain_state": 1, "voice_transcripts": 1, "resources": 1,
    "answer_events": 4, "chunks": 1, "evidence": 1,
}

OBJECT_REL = "objects/uploads/c2/c20f45a3557a726fbdacf4be3b7d0d57bc1e11950ecd104c0367fdd803a43acb"
FILE_RELS = (
    "database.json",
    "configs/m14-193-docker-compose.yml",
    "configs/m14-193-env.production-recovery",
    OBJECT_REL,
)


def _table_counts() -> dict[str, int]:
    counts = {name: 0 for name in pr.EXPECTED_TABLES}
    counts.update(ROW_TABLES)
    return counts


def _write_payload_files(backup_dir: Path) -> None:
    """写 4 个载荷文件（env/compose 含 marker secret——只应被字节哈希）。"""
    counts = _table_counts()
    database = {
        name: [{"secret": MARK_SECRET, "id": index} for index in range(count)]
        for name, count in counts.items()
    }
    (backup_dir / "database.json").write_text(
        json.dumps(database, ensure_ascii=False), encoding="utf-8")
    (backup_dir / "configs").mkdir(parents=True, exist_ok=True)
    (backup_dir / "configs" / "m14-193-docker-compose.yml").write_text(
        f"# compose backup\nAUTH_SECRET={MARK_SECRET}\n", encoding="utf-8")
    (backup_dir / "configs" / "m14-193-env.production-recovery").write_text(
        f"AIOS_AUTH_SECRET={MARK_SECRET}\nAIOS_LIVEKIT_API_SECRET=x\n",
        encoding="utf-8")
    object_path = backup_dir.joinpath(*OBJECT_REL.split("/"))
    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_bytes(b"fixture-object-bytes")


def _file_hashes(backup_dir: Path) -> dict[str, str]:
    hashes = {}
    for rel in FILE_RELS:
        path = backup_dir.joinpath(*rel.split("/"))
        hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _fixture_backup(tmp_path: Path, monkeypatch, *,
                    mutate_manifest=None,
                    after_write=None) -> Path:
    """在批准基（tmp_path/artifacts）下建合成有效备份并安装锚点。"""
    backup_dir = tmp_path / "artifacts" / "fixture-backup"
    backup_dir.mkdir(parents=True)
    _write_payload_files(backup_dir)
    manifest = {
        "schema_version": "aios-backup-v1",
        "created_at": "2026-09-30T06:40:32+00:00",
        "tables": _table_counts(),
        "files": _file_hashes(backup_dir),
    }
    if mutate_manifest is not None:
        mutate_manifest(manifest, backup_dir)
    manifest_path = backup_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    if after_write is not None:
        after_write(manifest, backup_dir)
    monkeypatch.setattr(
        pr, "MANIFEST_SHA256_ANCHOR",
        hashlib.sha256(manifest_path.read_bytes()).hexdigest().upper())
    return backup_dir


def _bases(tmp_path: Path) -> tuple[Path, ...]:
    return (tmp_path / "artifacts",)


def _make_junction(link: Path, target: Path) -> bool:
    """Windows junction（免管理员）；POSIX 用 symlink。失败返回 False。"""
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True, check=False,
            encoding="utf-8", errors="replace")
        return completed.returncode == 0
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    return True


def _remove_junction(link: Path) -> None:
    try:
        os.rmdir(link)  # 只删重解析点，不动目标
    except OSError:
        pass


class FakeRunner:
    """伪只读 Docker 面：按 argv 形态回放，记录全部调用。"""

    def __init__(self, *, engine_ready: bool = True, volume_ls_rc: int = 0,
                 existing_volumes: tuple[str, ...] = (),
                 missing_images: frozenset[str] = frozenset(),
                 raise_on: tuple[tuple[str, ...], ...] = ()) -> None:
        self.engine_ready = engine_ready
        self.volume_ls_rc = volume_ls_rc
        self.existing_volumes = tuple(existing_volumes)
        self.missing_images = frozenset(missing_images)
        self.raise_on = tuple(raise_on)
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, timeout: float = 60.0, encoding: str | None = None):
        argv = tuple(str(item) for item in argv)
        self.calls.append(argv)
        for prefix in self.raise_on:
            if argv[:len(prefix)] == prefix:
                raise pr.m231.recovery.RunnerError("transport 不可用（fixture）")
        if argv[:2] == ("docker", "version"):
            if self.engine_ready:
                return pr.m231.recovery.CommandResult(argv, 0, "29.8.1\n", "")
            return pr.m231.recovery.CommandResult(
                argv, 1, "", "Cannot connect to the Docker daemon")
        if argv[:3] == ("docker", "volume", "ls"):
            if self.volume_ls_rc != 0:
                return pr.m231.recovery.CommandResult(
                    argv, self.volume_ls_rc, "", "daemon error")
            out = "".join(f"{name}\n" for name in self.existing_volumes)
            return pr.m231.recovery.CommandResult(argv, 0, out, "")
        if argv[:3] == ("docker", "image", "inspect"):
            ref = argv[3]
            if ref in self.missing_images:
                return pr.m231.recovery.CommandResult(
                    argv, 1, "", "Error: No such image")
            return pr.m231.recovery.CommandResult(argv, 0, "sha256:0000…\n", "")
        raise AssertionError(f"fixture 未覆盖的命令面: {argv}")


def _assess(runner: FakeRunner, backup_dir: Path, tmp_path: Path,
            *, bases: tuple[Path, ...] | None = None,
            echo: bool = False):
    log = pr.PreflightLog(echo=echo)
    report = pr.run_preflight(
        runner=runner, log=log, backup_dir=backup_dir,
        bases=bases if bases is not None else _bases(tmp_path))
    return report, log


# ---------------------------------------------------------------- 放行形态

def test_ready_to_reconstruct_happy_path(tmp_path: Path, monkeypatch) -> None:
    backup_dir = _fixture_backup(tmp_path, monkeypatch)
    report, log = _assess(FakeRunner(), backup_dir, tmp_path)
    assert report["backup"]["valid"] is True
    assert report["backup"]["failures"] == []
    assert report["backup"]["tables"]["declared_count"] == 30
    assert report["backup"]["tables"]["declared_rows_total"] == 41
    assert report["backup"]["tables"]["actual_rows_total"] == 41
    assert report["backup"]["files"]["declared_count"] == 4
    assert report["backup"]["files"]["actual_count"] == 4
    assert all(record["ok"] for record in report["backup"]["files"]["hashes"])
    assert report["docker"]["readable"] is True
    assert report["docker"]["persistent_volumes_present"] == []
    assert report["docker"]["missing_images"] == []
    assert report["blockers"] == []
    assert report["verdict"] == pr.VERDICT_READY
    assert "有效" in "\n".join(log.lines)


def test_cache_volume_absent_is_note_not_blocker(tmp_path, monkeypatch) -> None:
    backup_dir = _fixture_backup(tmp_path, monkeypatch)
    report, _ = _assess(FakeRunner(), backup_dir, tmp_path)
    assert pr.NOTE_CACHE_VOLUME_ABSENT in report["notes"]
    assert report["docker"]["cache_volume_present"] is False
    assert report["blockers"] == []


def test_exit_code_zero_when_ready(tmp_path, monkeypatch) -> None:
    backup_dir = _fixture_backup(tmp_path, monkeypatch)
    output = tmp_path / "artifacts" / "report.json"
    code = pr.main(["--backup-dir", str(backup_dir), "--output", str(output)],
                   runner=FakeRunner(), bases=_bases(tmp_path))
    assert code == pr.EXIT_OK
    assert json.loads(output.read_text(encoding="utf-8"))["verdict"] == pr.VERDICT_READY


# ---------------------------------------------------------------- 位置与链接防护

def test_unapproved_location_fails_fast_without_reading(tmp_path, monkeypatch) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "decoy.txt").write_text("decoy", encoding="utf-8")
    report, _ = _assess(FakeRunner(), elsewhere, tmp_path)
    assert report["verdict"] == pr.VERDICT_BLOCKED
    assert pr.BLOCK_BACKUP_INVALID in report["blockers"]
    assert pr.F_LOCATION_UNAPPROVED in report["backup"]["failures"]
    assert report["inputs"]["backup_dir"] == "elsewhere"  # 仅目录名，非绝对路径
    assert report["backup"]["files"]["hashes"] == []      # 未批准 → 内容不遍历


def test_approved_base_boundary_rejects_base_itself(tmp_path) -> None:
    base = tmp_path / "artifacts"
    base.mkdir()
    assert pr.locate_in_approved_base(base, _bases(tmp_path)) is None
    assert pr.locate_in_approved_base(
        base / "sub", _bases(tmp_path)) == "sub"


def test_missing_backup_dir_blocks(tmp_path, monkeypatch) -> None:
    report, _ = _assess(
        FakeRunner(), tmp_path / "artifacts" / "absent", tmp_path)
    assert pr.F_BACKUP_MISSING in report["backup"]["failures"]
    assert pr.BLOCK_BACKUP_INVALID in report["blockers"]


def test_junction_backup_root_rejected(tmp_path, monkeypatch) -> None:
    real = _fixture_backup(tmp_path, monkeypatch)
    junction = tmp_path / "artifacts" / "junction-backup"
    if not _make_junction(junction, real):
        pytest.skip("本平台无法创建目录链接")
    try:
        facts = pr.validate_backup(junction, _bases(tmp_path))
        assert pr.F_PATH_LINK_LIKE in facts.failures
        assert facts.link_like_paths == (".",)
        assert facts.valid is False
    finally:
        _remove_junction(junction)


def test_nested_junction_inside_tree_rejected(tmp_path, monkeypatch) -> None:
    backup_dir = _fixture_backup(tmp_path, monkeypatch)
    junction = backup_dir / "configs" / "link-dir"
    if not _make_junction(junction, tmp_path):
        pytest.skip("本平台无法创建目录链接")
    try:
        facts = pr.validate_backup(backup_dir, _bases(tmp_path))
        assert pr.F_PATH_LINK_LIKE in facts.failures
        assert "configs/link-dir" in facts.link_like_paths
    finally:
        _remove_junction(junction)


def test_nested_symlink_file_rejected(tmp_path, monkeypatch) -> None:
    backup_dir = _fixture_backup(tmp_path, monkeypatch)
    target = tmp_path / "outside-secret.txt"
    target.write_text("outside", encoding="utf-8")
    link = backup_dir / "configs" / "env-link"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("本平台无法创建文件符号链接（junction 用例已覆盖链接形态）")
    try:
        facts = pr.validate_backup(backup_dir, _bases(tmp_path))
        assert pr.F_PATH_LINK_LIKE in facts.failures
        assert "configs/env-link" in facts.link_like_paths
    finally:
        link.unlink(missing_ok=True)


# ---------------------------------------------------------------- 备份校验矩阵

def test_schema_mismatch_blocks(tmp_path, monkeypatch) -> None:
    backup_dir = _fixture_backup(
        tmp_path, monkeypatch,
        mutate_manifest=lambda m, d: m.__setitem__("schema_version", "aios-backup-v2"))
    report, _ = _assess(FakeRunner(), backup_dir, tmp_path)
    assert pr.F_SCHEMA_MISMATCH in report["backup"]["failures"]
    assert report["backup"]["schema"] == "aios-backup-v2"
    assert pr.BLOCK_BACKUP_INVALID in report["blockers"]


def test_manifest_anchor_mismatch_blocks(tmp_path, monkeypatch) -> None:
    backup_dir = _fixture_backup(tmp_path, monkeypatch)
    monkeypatch.setattr(pr, "MANIFEST_SHA256_ANCHOR", "0" * 64)  # 漂移锚点
    facts = pr.validate_backup(backup_dir, _bases(tmp_path))
    assert pr.F_ANCHOR_MISMATCH in facts.failures
    assert facts.manifest_sha256 != "0" * 64


def test_missing_table_blocks_by_name(tmp_path, monkeypatch) -> None:
    def drop_table(manifest, backup_dir):
        manifest["tables"].pop("concepts")
        database = json.loads((backup_dir / "database.json").read_text(encoding="utf-8"))
        database.pop("concepts")
        (backup_dir / "database.json").write_text(
            json.dumps(database, ensure_ascii=False), encoding="utf-8")
    backup_dir = _fixture_backup(tmp_path, monkeypatch, mutate_manifest=drop_table)
    report, _ = _assess(FakeRunner(), backup_dir, tmp_path)
    assert pr.F_TABLE_SET_MISMATCH in report["backup"]["failures"]
    assert report["backup"]["tables"]["missing"] == ["concepts"]  # 表名可见
    assert report["backup"]["tables"]["declared_count"] == 29


def test_extra_table_blocks_by_name(tmp_path, monkeypatch) -> None:
    backup_dir = _fixture_backup(
        tmp_path, monkeypatch,
        mutate_manifest=lambda m, d: m["tables"].__setitem__("zzz_foreign", 0))
    report, _ = _assess(FakeRunner(), backup_dir, tmp_path)
    assert pr.F_TABLE_SET_MISMATCH in report["backup"]["failures"]
    assert report["backup"]["tables"]["extra"] == ["zzz_foreign"]
    assert report["backup"]["tables"]["declared_count"] == 31


def test_row_count_mismatch_lists_table_and_counts(tmp_path, monkeypatch) -> None:
    def shrink_rows(_manifest, backup_dir):
        database = json.loads((backup_dir / "database.json").read_text(encoding="utf-8"))
        database["users"] = database["users"][:5]
        (backup_dir / "database.json").write_text(
            json.dumps(database, ensure_ascii=False), encoding="utf-8")
    backup_dir = _fixture_backup(tmp_path, monkeypatch, mutate_manifest=shrink_rows)
    report, _ = _assess(FakeRunner(), backup_dir, tmp_path)
    assert pr.F_ROW_COUNT_MISMATCH in report["backup"]["failures"]
    assert report["backup"]["tables"]["row_mismatches"] == [
        {"table": "users", "declared": 6, "actual": 5}]


def test_total_rows_drift_blocks_even_when_tables_consistent(
        tmp_path, monkeypatch) -> None:
    def drop_one_row(manifest, backup_dir):
        manifest["tables"]["users"] = 5
        database = json.loads((backup_dir / "database.json").read_text(encoding="utf-8"))
        database["users"] = database["users"][:5]
        (backup_dir / "database.json").write_text(
            json.dumps(database, ensure_ascii=False), encoding="utf-8")
    backup_dir = _fixture_backup(tmp_path, monkeypatch, mutate_manifest=drop_one_row)
    report, _ = _assess(FakeRunner(), backup_dir, tmp_path)
    assert pr.F_ROW_COUNT_MISMATCH not in report["backup"]["failures"]
    assert pr.F_FACTS_DRIFT in report["backup"]["failures"]  # 40 ≠ 41
    assert pr.BLOCK_BACKUP_INVALID in report["blockers"]


def test_extra_undeclared_file_blocks(tmp_path, monkeypatch) -> None:
    def add_rogue(_manifest, backup_dir):
        (backup_dir / "rogue.txt").write_text("rogue", encoding="utf-8")
    backup_dir = _fixture_backup(tmp_path, monkeypatch, after_write=add_rogue)
    report, _ = _assess(FakeRunner(), backup_dir, tmp_path)
    assert pr.F_FILE_SET_MISMATCH in report["backup"]["failures"]
    assert report["backup"]["files"]["extra"] == ["rogue.txt"]
    assert report["backup"]["files"]["actual_count"] == 5


def test_missing_declared_file_blocks(tmp_path, monkeypatch) -> None:
    def drop_object(_manifest, backup_dir):
        backup_dir.joinpath(*OBJECT_REL.split("/")).unlink()
    backup_dir = _fixture_backup(tmp_path, monkeypatch, after_write=drop_object)
    report, _ = _assess(FakeRunner(), backup_dir, tmp_path)
    assert pr.F_FILE_SET_MISMATCH in report["backup"]["failures"]
    assert report["backup"]["files"]["missing"] == [OBJECT_REL]
    assert all(r["file"] != OBJECT_REL for r in report["backup"]["files"]["hashes"])


def test_file_hash_tamper_blocks(tmp_path, monkeypatch) -> None:
    def tamper_object(_manifest, backup_dir):
        backup_dir.joinpath(*OBJECT_REL.split("/")).write_bytes(b"tampered")
    backup_dir = _fixture_backup(tmp_path, monkeypatch, after_write=tamper_object)
    report, _ = _assess(FakeRunner(), backup_dir, tmp_path)
    assert pr.F_FILE_HASH_MISMATCH in report["backup"]["failures"]
    record = next(r for r in report["backup"]["files"]["hashes"]
                  if r["file"] == OBJECT_REL)
    assert record["ok"] is False
    assert record["declared_sha256"] != record["actual_sha256"]


def test_manifest_not_json_blocks(tmp_path) -> None:
    backup_dir = tmp_path / "artifacts" / "broken"
    backup_dir.mkdir(parents=True)
    (backup_dir / "manifest.json").write_text("not-json{", encoding="utf-8")
    facts = pr.validate_backup(backup_dir, _bases(tmp_path))
    assert pr.F_MANIFEST_UNREADABLE in facts.failures


# ---------------------------------------------------------------- 只读 Docker 面

def test_docker_unavailable_blocks_without_further_probes(tmp_path, monkeypatch) -> None:
    backup_dir = _fixture_backup(tmp_path, monkeypatch)
    runner = FakeRunner(engine_ready=False)
    report, _ = _assess(runner, backup_dir, tmp_path)
    assert pr.BLOCK_DOCKER_UNREADABLE in report["blockers"]
    assert report["docker"]["readable"] is False
    # 引擎不可用 → volume/image 探测一律不发起（version 是唯一调用面）
    assert [c[:2] for c in runner.calls] == [("docker", "version")]
    assert report["docker"]["persistent_volumes_present"] == []


def test_engine_transport_error_is_unreadable(tmp_path, monkeypatch) -> None:
    backup_dir = _fixture_backup(tmp_path, monkeypatch)
    runner = FakeRunner(raise_on=(("docker", "version"),))
    report, _ = _assess(runner, backup_dir, tmp_path)
    assert pr.BLOCK_DOCKER_UNREADABLE in report["blockers"]


def test_volume_query_failure_is_unreadable_no_conclusions(
        tmp_path, monkeypatch) -> None:
    backup_dir = _fixture_backup(tmp_path, monkeypatch)
    runner = FakeRunner(volume_ls_rc=1)
    report, _ = _assess(runner, backup_dir, tmp_path)
    assert pr.BLOCK_DOCKER_UNREADABLE in report["blockers"]
    # 查询失败 ≠ 卷不在场：不得把「查不出」伪装成任何卷/镜像结论
    assert report["docker"]["persistent_volumes_present"] == []
    assert report["docker"]["missing_images"] == []
    assert not any(c[:3] == ("docker", "image", "inspect") for c in runner.calls)


def test_image_inspect_transport_error_is_unreadable(tmp_path, monkeypatch) -> None:
    backup_dir = _fixture_backup(tmp_path, monkeypatch)
    runner = FakeRunner(raise_on=(("docker", "image", "inspect"),))
    report, _ = _assess(runner, backup_dir, tmp_path)
    assert pr.BLOCK_DOCKER_UNREADABLE in report["blockers"]


def test_existing_persistent_volume_blocks_overwrite(tmp_path, monkeypatch) -> None:
    """真实机器当前形态：备份有效、Docker 可读，但持久卷已在场 → 绝不覆盖。"""
    backup_dir = _fixture_backup(tmp_path, monkeypatch)
    runner = FakeRunner(existing_volumes=(PG_VOLUME, MINIO_VOLUME, CACHE_VOLUME))
    report, _ = _assess(runner, backup_dir, tmp_path)
    assert pr.BLOCK_EXISTING_DATA in report["blockers"]
    assert report["docker"]["persistent_volumes_present"] == [PG_VOLUME, MINIO_VOLUME]
    joined = " ".join(report["actions"])
    assert "绝不覆盖" in joined and PG_VOLUME in joined
    assert report["verdict"] == pr.VERDICT_BLOCKED
    # runbook 恒在场（分阶段计划永不因阻塞隐藏），但 verdict 不放行
    assert report["runbook"]


def test_single_existing_persistent_volume_blocks(tmp_path, monkeypatch) -> None:
    backup_dir = _fixture_backup(tmp_path, monkeypatch)
    runner = FakeRunner(existing_volumes=(MINIO_VOLUME,))
    report, _ = _assess(runner, backup_dir, tmp_path)
    assert pr.BLOCK_EXISTING_DATA in report["blockers"]
    assert report["docker"]["persistent_volumes_present"] == [MINIO_VOLUME]


def test_required_image_missing_blocks(tmp_path, monkeypatch) -> None:
    backup_dir = _fixture_backup(tmp_path, monkeypatch)
    runner = FakeRunner(missing_images=frozenset({DB_IMAGE}))
    report, _ = _assess(runner, backup_dir, tmp_path)
    assert pr.BLOCK_IMAGE_MISSING in report["blockers"]
    assert report["docker"]["missing_images"] == [DB_IMAGE]
    joined = " ".join(report["actions"])
    assert "minio_image_adoption" in joined and "绝不 pull/build" in joined


def test_minio_self_image_missing_blocks(tmp_path, monkeypatch) -> None:
    backup_dir = _fixture_backup(tmp_path, monkeypatch)
    runner = FakeRunner(missing_images=frozenset({MINIO_IMAGE}))
    report, _ = _assess(runner, backup_dir, tmp_path)
    assert pr.BLOCK_IMAGE_MISSING in report["blockers"]


def test_blocker_order_is_deterministic(tmp_path, monkeypatch) -> None:
    backup_dir = _fixture_backup(
        tmp_path, monkeypatch,
        mutate_manifest=lambda m, d: m.__setitem__("schema_version", "wrong"))
    runner = FakeRunner(
        existing_volumes=(PG_VOLUME,), missing_images=frozenset({DB_IMAGE}))
    report, _ = _assess(runner, backup_dir, tmp_path)
    assert report["blockers"] == [
        pr.BLOCK_BACKUP_INVALID, pr.BLOCK_EXISTING_DATA, pr.BLOCK_IMAGE_MISSING]


# ---------------------------------------------------------------- runbook

def test_runbook_covers_all_stages_and_both_volumes(tmp_path) -> None:
    runbook = " ".join(pr.build_runbook(PROJECT))
    assert f"docker volume create {PG_VOLUME}" in runbook
    assert f"docker volume create {MINIO_VOLUME}" in runbook
    assert runbook.count("docker volume create") == 2  # 显式且仅创建两个卷
    assert "up -d --no-build postgres minio" in runbook
    assert "alembic upgrade head" in runbook
    assert "app.ops.cli restore" in runbook
    assert "backup-restore-evidence" in runbook
    assert "production_recovery.py" in runbook
    assert "production_restore_preflight" in runbook
    assert "public_edge_preflight" in runbook
    assert "docker volume ls" in runbook  # 阶段3 复核卷缺失
    assert "语音验收" in runbook
    assert pr.NOTE_RUNBOOK_STAGED_ONLY.startswith("runbook-staged")


# ---------------------------------------------------------------- secret / 路径抑制

def test_secrets_never_leak_to_logs_or_report(tmp_path, monkeypatch, capsys) -> None:
    backup_dir = _fixture_backup(tmp_path, monkeypatch)
    runner = FakeRunner(existing_volumes=(PG_VOLUME,))
    report, log = _assess(runner, backup_dir, tmp_path, echo=True)
    blob = ("\n".join(log.lines) + json.dumps(report, ensure_ascii=False)
            + capsys.readouterr().out)
    assert MARK_SECRET not in blob  # env/行值只被哈希/计数，绝不回显
    for pattern in SECRET_PATTERNS:
        assert pattern not in blob


def test_report_contains_no_absolute_paths(tmp_path, monkeypatch) -> None:
    backup_dir = _fixture_backup(tmp_path, monkeypatch)
    report, log = _assess(FakeRunner(), backup_dir, tmp_path, echo=True)
    blob = "\n".join(log.lines) + json.dumps(report, ensure_ascii=False)
    for needle in ("D:\\", "D:/", "/d/AI", "/tmp/", str(tmp_path), str(REPO_ROOT)):
        assert needle not in blob


def test_private_path_scrubber_replaces_drives_and_unc_only() -> None:
    assert pr._scrub_private_paths(r"Z:\aa\bb cc") == "<path> cc"  # 合成盘符样本
    assert pr._scrub_private_paths(r"\\server\share\dir") == "<path>"
    assert pr._scrub_private_paths("Q:/foo/bar,baz") == "<path>,baz"
    # URL 不被误伤（前瞻断言排除 https:// 的伪盘符匹配）
    assert pr._scrub_private_paths("https://ndtool.cn/aios") == "https://ndtool.cn/aios"
    assert pr._scrub_private_paths("ws://127.0.0.1:7880") == "ws://127.0.0.1:7880"


def test_report_atomic_write_scrubs_paths(tmp_path, monkeypatch) -> None:
    backup_dir = _fixture_backup(tmp_path, monkeypatch)
    report, _ = _assess(FakeRunner(), backup_dir, tmp_path)
    output = tmp_path / "artifacts" / "nested" / "report.json"
    pr._write_report_atomic(output, report)
    text = output.read_text(encoding="utf-8")
    assert str(tmp_path) not in text and "D:" not in text
    assert json.loads(text)["schema"] == pr.SCHEMA


# ---------------------------------------------------------------- CLI / 源码契约

def test_parser_defaults_and_derived_backup_dir() -> None:
    args = pr.build_parser().parse_args([])
    assert args.project == pr.DEFAULT_PROJECT == PROJECT
    assert args.backup_dir is None and args.output is None
    default = pr.default_backup_dir()
    assert default.parts[-3:] == ("artifacts", "m14-193-production-cutover",
                                  "pre-cutover-backup")
    assert pr.locate_in_approved_base(default, pr.approved_backup_bases()) is not None


def test_exit_code_one_when_blocked(tmp_path, monkeypatch) -> None:
    backup_dir = _fixture_backup(tmp_path, monkeypatch)
    code = pr.main(["--backup-dir", str(backup_dir)],
                   runner=FakeRunner(existing_volumes=(PG_VOLUME,)),
                   bases=_bases(tmp_path))
    assert code == pr.EXIT_BLOCKED


def test_source_contract_docker_face_is_read_only() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    # 本工具自身 docker argv 的第二 token 只允许 version/volume/image
    # （version/volume ls 经 M14-231 probe_engine/probe_volume_names 委托，
    #  本文件直接构造的只有 image inspect）
    heads = set(re.findall(r'docker",\s*"([a-z]+)"', source))
    assert heads <= {"version", "volume", "image"}, heads
    image_subs = set(re.findall(r'image",\s*"([a-z]+)"', source))
    assert image_subs == {"inspect"}, image_subs
    assert "m231.probe_engine(runner)" in source
    assert "m231.probe_volume_names(runner)" in source
    # 委托的 M14-231 探针函数同为只读面（其卷子命令恒 ls）
    m231_source = M231_SCRIPT.read_text(encoding="utf-8")
    assert set(re.findall(r'volume",\s*"([a-z]+)"', m231_source)) == {"ls"}
    # 破坏性子命令 token 字面量一律禁止（runbook 文本中的裸词不受影响——
    # 这里断言的是 argv token 形态 "xxx"）
    for literal in ('"up"', '"down"', '"stop"', '"kill"', '"rm"', '"restart"',
                    '"pull"', '"build"', '"run"', '"exec"', '"create"', '"prune"'):
        assert literal not in source, f"禁止出现的子命令字面量: {literal}"
        assert literal not in m231_source
    # 绝不直接起子进程：执行面一律经注入 Runner（RealRunner 复用 recovery）
    assert "import subprocess" not in source
    assert "subprocess.run" not in source
    for pattern in SECRET_PATTERNS:
        assert pattern not in source
    assert "shutil" not in source  # 也不做文件系统删除面


def test_source_contract_no_execute_mode() -> None:
    """本工具自身无执行面：parser 不提供 --execute/--apply/--confirm-phrase
    （runbook 文本引用 M14-231/minio 工具的短语属于阶段说明，非本工具面）。"""
    option_names = {
        option
        for action in pr.build_parser()._actions  # 契约断言面（argparse 内部结构只读取）
        for option in action.option_strings
    }
    assert not {"--execute", "--apply", "--confirm-phrase"} & option_names
    source = SCRIPT.read_text(encoding="utf-8")
    assert "def main(" in source
    # 唯一动作是 preflight 报告落盘（无任何恢复/重建执行路径）
    assert "execute_guarded" not in source and "recovery_calls" not in source


def test_constants_cross_locked_with_m231_and_compose() -> None:
    m231 = _load_module(M231_SCRIPT, "production_restore_preflight_for_l232_xlock")
    assert pr.PERSISTENT_VOLUME_KEYS == m231.PERSISTENT_VOLUME_KEYS == (
        "postgres-data", "minio-data")
    assert pr.CACHE_VOLUME_KEYS == m231.CACHE_VOLUME_KEYS == ("searxng-cache",)
    assert pr.DEFAULT_PROJECT == m231.DEFAULT_PROJECT
    assert pr.compose_volume_name(PROJECT, "postgres-data") == PG_VOLUME
    assert pr.compose_volume_name(PROJECT, "minio-data") == MINIO_VOLUME
    assert set(pr.SELF_MINIO_IMAGE_REFS) == set(m231.recovery.LOCAL_BUILD_IMAGE_REFS)
    assert pr.REGISTRY_DB_IMAGE in m231.REGISTRY_IMAGE_ANCHORS
    assert set(pr.REQUIRED_IMAGE_REFS) == {MINIO_IMAGE, DB_IMAGE}
    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    assert "postgres:17-alpine" in compose
    assert MINIO_IMAGE in compose
    assert "aios-m14-03-production-rehearsal" in m231.recovery.DEFAULT_PROJECT


def test_expected_facts_and_anchor_pinned_to_m14_193() -> None:
    assert len(pr.EXPECTED_TABLES) == pr.EXPECTED_TABLE_COUNT == 30
    assert pr.EXPECTED_TOTAL_ROWS == 41
    assert pr.EXPECTED_FILE_COUNT == 4
    assert pr.BACKUP_SCHEMA == "aios-backup-v1"
    assert pr.MANIFEST_SHA256_ANCHOR == MANIFEST_ANCHOR_LITERAL
    assert sum(ROW_TABLES.values()) == pr.EXPECTED_TOTAL_ROWS  # fixture 同真实分布
    assert pr.collect_blockers(
        pr.BackupFacts(valid=True),
        pr.DockerFacts(readable=True)) == ()
    assert pr.collect_blockers(
        pr.BackupFacts(valid=False),
        pr.DockerFacts(readable=True)) == (pr.BLOCK_BACKUP_INVALID,)


def test_scrub_private_paths_handles_none_input_safely() -> None:
    assert pr._scrub_private_paths("") == ""


# ---------------------------------------------------------------- fixture 卫生

def test_fixture_cleanup_leaves_no_links(tmp_path: Path) -> None:
    """junction 用例自清理后 tmp_path 无残留重解析点（防影响其它用例）。"""
    for dirpath, dirnames, _filenames in os.walk(tmp_path):
        for name in dirnames:
            assert not pr._is_linklike(Path(dirpath) / name), name
    shutil.rmtree(tmp_path / "artifacts", ignore_errors=True)
