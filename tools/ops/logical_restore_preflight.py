#!/usr/bin/env python
"""M14-232 逻辑恢复前置检查（logical restore preflight）——只读 fail-closed。

场景：M14-231 真实机器 preflight 实证生产彩排栈（compose 项目
``aios-m14-03-production-rehearsal``）的两个持久数据卷
（``<project>_postgres-data`` / ``<project>_minio-data``）缺失。本工具回答
一个问题：**缺失的持久卷能否安全地用 M14-193 已验证的逻辑备份
（schema ``aios-backup-v1``：30 表 / 41 行 / 4 文件，manifest SHA256 锚点
381C4987…0612EC）分阶段重建**，还是先有人工阻塞要解。本切片**没有
--execute 模式**：输出的是分阶段 runbook（永不执行），Docker 面只有三个
只读命令（version / volume ls / image inspect），绝不创建/删除卷、绝不
compose up/down、绝不 pull/build、绝不覆盖任何现有数据。

备份校验契约（fail-closed，任何漂移即 ``backup-invalid``）：
- 备份目录必须位于**批准的 gitignored artifacts/temp 位置**（本 checkout 与
  canonical 主 checkout 的 ``artifacts/``/``temp/`` 之下——canonical 根由
  worktree ``.git`` gitdir 指针运行时推导，源码零硬编码绝对路径）；目录
  本身、祖先与树内任何条目 **symlink/junction（reparse point）一律拒绝**
  （嵌套 link-like 路径同样拒绝）；
- manifest schema 必须精确等于 ``aios-backup-v1``；manifest 文件自身 SHA256
  必须精确等于锚点常量（与 M14-193 证据交叉锁定）；
- **精确表集/表数**：manifest ``tables`` 键集必须精确等于 30 表锚点集合
  （多表/少表均阻塞），``database.json`` 表集必须与 manifest 一致；
- **精确文件集/文件数**：manifest ``files`` 键集必须精确等于树内实际文件
  集（manifest.json 自身除外）——树内多出未声明文件或声明文件缺失均阻塞，
  计数 4；
- **声明行数 = 实际行数**：逐表 ``len(database.json[table])`` 必须等于
  manifest 声明计数，总数必须等于 41；
- **逐文件 SHA256**：每个声明文件按字节复算哈希必须与 manifest 一致；
  报告只含表名/文件名/计数/尺寸/SHA256——**绝不读取、解码或回显任何
  env/config 值**（env/compose 备份文件只做字节哈希，永不解码）。

只读 Docker 语义（fail-closed；与 M14-231 交叉锁定复用其常量/探针）：
- Docker 引擎不可用**或任何查询失败** → ``docker-state-unreadable``；
- 两个命名持久卷**任一已在场** → ``existing-data-must-not-be-overwritten``
  （逻辑重建只允许在两卷均缺失时进行——绝不能把重建演成对现有数据的
  覆盖）；``searxng-cache`` 可重建，缺失仅为提示；
- 必需本地镜像 = 精确 ``aios/minio`` 自建锚点（复用
  production_recovery.LOCAL_BUILD_IMAGE_REFS）+ ``postgres:17-alpine``
  （与 M14-231/compose 锚点交叉锁定）缺失 → ``required-image-missing``
  （本工具绝不 pull/build）；API/Web 全栈就绪判定**委托 M14-231**
  ``production_restore_preflight.py``，本工具不重复；
- 唯一放行形态：**备份有效 + Docker 可读 + 两持久卷均缺失 + 必需镜像
  齐备** → ``ready-to-reconstruct``（exit 0），其余一律 ``blocked``
  （exit 1）。

分阶段 runbook（仅输出、永不执行、零 secret/零绝对路径）：备份复核 →
获准窗口镜像构建/采纳 → 即时复核两数据卷仍缺失 → **显式创建且仅创建**
两个命名数据卷 → 仅启动 postgres/minio → 迁移 schema → 既有文档化
``app.ops.cli restore`` 恢复 DB/对象数据 → 行数/文件/manifest/对象完整性
对账 → 既有恢复路径拉起全栈 → M14-231 preflight + 本地/公网/语音验收。

日志/报告零 secret 零本机绝对路径：一切行先经 secret 标签脱敏（复用
RunLog.redact）再做盘符/UNC 私有绝对路径抹除（``<path>``）；JSON 报告
原子写（tmp + os.replace）。

退出码：0 = ``ready-to-reconstruct``；1 = ``blocked``（或报告写出失败）；
2 = 参数错误。

用法（仓库根）：
  python tools/ops/logical_restore_preflight.py            # 只读 preflight
  python tools/ops/logical_restore_preflight.py \
      --output .verify/m14-232/machine-preflight.json       # 另落 JSON 报告
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_USAGE = 2

TOOL_NAME = "logical_restore_preflight"
SCHEMA = "aios-logical-restore-preflight/1"
TAG = "[logical-restore]"

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = Path(__file__).resolve().parent
RESTORE_PREFLIGHT_SCRIPT = TOOLS_DIR / "production_restore_preflight.py"

#: 备份事实锚点（M14-193 证据交叉锁定：30 表 / 41 行 / 4 文件 / manifest 锚点）
BACKUP_SCHEMA = "aios-backup-v1"
MANIFEST_NAME = "manifest.json"
DATABASE_NAME = "database.json"
MANIFEST_SHA256_ANCHOR = (
    "381C49875C23F4CF35F49CED864522A200D6B46614C72D42B52DFB34EB0612EC"
)
EXPECTED_TABLE_COUNT = 30
EXPECTED_TOTAL_ROWS = 41
EXPECTED_FILE_COUNT = 4
DEFAULT_BACKUP_SUBPATH = Path("m14-193-production-cutover") / "pre-cutover-backup"

#: M14-193 pre-cutover backup 的 30 表精确集合（表名非 secret，报告可见）
EXPECTED_TABLES: frozenset[str] = frozenset({
    "alembic_version", "audit_chain_state", "audit_log", "concept_dag_versions",
    "concepts", "course_generation_drafts", "course_import_drafts", "eval_runs",
    "misconception_candidates", "paper_question_drafts", "papers",
    "search_queries", "sources", "student_concept_states", "users",
    "variant_question_drafts", "voice_answer_events", "voice_sessions",
    "voice_trace_spans", "voice_transcripts", "audit_chain_entries",
    "concept_edges", "exam_sessions", "questions", "resources",
    "answer_events", "chunks", "parse_jobs", "submissions", "evidence",
})

BLOCK_BACKUP_INVALID = "backup-invalid"
BLOCK_DOCKER_UNREADABLE = "docker-state-unreadable"
BLOCK_EXISTING_DATA = "existing-data-must-not-be-overwritten"
BLOCK_IMAGE_MISSING = "required-image-missing"

NOTE_CACHE_VOLUME_ABSENT = "searxng-cache-recreatable"
NOTE_RUNBOOK_STAGED_ONLY = "runbook-staged-never-executed"

VERDICT_BLOCKED = "blocked"
VERDICT_READY = "ready-to-reconstruct"

#: 备份校验失败明细码（进入 report["backup"]["failures"]，主阻塞恒为
#: backup-invalid——dispatch 词表优先，明细仅供人工裁决）
F_LOCATION_UNAPPROVED = "backup-location-unapproved"
F_BACKUP_MISSING = "backup-missing"
F_PATH_LINK_LIKE = "backup-path-link-like"
F_MANIFEST_UNREADABLE = "backup-manifest-unreadable"
F_SCHEMA_MISMATCH = "backup-schema-mismatch"
F_ANCHOR_MISMATCH = "backup-manifest-anchor-mismatch"
F_TABLE_SET_MISMATCH = "backup-table-set-mismatch"
F_FILE_SET_MISMATCH = "backup-file-set-mismatch"
F_ROW_COUNT_MISMATCH = "backup-row-count-mismatch"
F_FILE_HASH_MISMATCH = "backup-file-hash-mismatch"
F_FACTS_DRIFT = "backup-expected-facts-drift"
F_DATABASE_UNREADABLE = "backup-database-unreadable"

#: 盘符绝对路径 / UNC——日志与报告一律抹除（防本机路径外泄的最后防线；
#: 前瞻断言排除 https:// 等 URL 的伪匹配——只认独立的盘符/UNC 形态）
_DRIVE_PATH_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"',]*")
_UNC_PATH_RE = re.compile(r"\\\\[^\s\"',]+")


def _load_restore_preflight_module() -> Any:
    """importlib 复用 M14-231 的常量/探针/Runner（其自身无导入副作用）。"""
    spec = importlib.util.spec_from_file_location(
        "production_restore_preflight_for_logical_restore", RESTORE_PREFLIGHT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


m231 = _load_restore_preflight_module()

#: 与 M14-231 / production_recovery 交叉锁定的共享锚点（契约测试锁定两侧一致）
PERSISTENT_VOLUME_KEYS: tuple[str, ...] = tuple(m231.PERSISTENT_VOLUME_KEYS)
CACHE_VOLUME_KEYS: tuple[str, ...] = tuple(m231.CACHE_VOLUME_KEYS)
DEFAULT_PROJECT: str = m231.DEFAULT_PROJECT
compose_volume_name = m231.compose_volume_name  # <project>_<key>（下划线约定）
Runner = m231.Runner

#: 逻辑重建必需本地镜像：精确 aios/minio 自建锚点（registry 拉不到）+
#: postgres:17-alpine（compose 数据库锚点，与 M14-231 REGISTRY_IMAGE_ANCHORS
#: 及 infra/docker-compose.yml 交叉锁定）。API/Web 全栈就绪委托 M14-231。
SELF_MINIO_IMAGE_REFS: tuple[str, ...] = tuple(m231.recovery.LOCAL_BUILD_IMAGE_REFS)
REGISTRY_DB_IMAGE = "postgres:17-alpine"
REQUIRED_IMAGE_REFS: tuple[str, ...] = SELF_MINIO_IMAGE_REFS + (REGISTRY_DB_IMAGE,)


class PreflightLog(m231.recovery.RunLog):
    """复用 RunLog 的 secret 脱敏/行缓冲，追加私有绝对路径抹除与本工具前缀。"""

    def say(self, message: str) -> None:
        line = _scrub_private_paths(self.redact(message))
        self.lines.append(line)
        if self._echo:
            print(f"{TAG} {line}", flush=True)


def _scrub_private_paths(text: str) -> str:
    """盘符/UNC 绝对路径 → ``<path>``（报告/日志永不落本机绝对路径）。"""
    return _UNC_PATH_RE.sub("<path>", _DRIVE_PATH_RE.sub("<path>", text))


# ---------------------------------------------------------------- 位置与链接防护


def canonical_checkout_root(repo_root: Path) -> Path:
    """worktree ``.git`` 文件（gitdir 指针）→ canonical 主 checkout 根。

    源码零硬编码绝对路径：canonical 根一律运行时推导；推导不出（裸仓库/
    损坏指针）退回本 checkout 根（fail-closed：批准基只少不多）。
    """
    git = repo_root / ".git"
    try:
        if git.is_dir():
            return repo_root
        text = git.read_text(encoding="utf-8").strip()
    except OSError:
        return repo_root
    match = re.match(r"gitdir:\s*(.+)", text)
    if not match:
        return repo_root
    pointer = Path(match.group(1).strip())
    if not pointer.is_absolute():
        pointer = repo_root / pointer
    current = Path(os.path.abspath(pointer))
    while current.name.lower() != ".git" and current != current.parent:
        current = current.parent
    return current.parent if current.name.lower() == ".git" else repo_root


def approved_backup_bases(repo_root: Path = REPO_ROOT) -> tuple[Path, ...]:
    """批准基 = 本 checkout 与 canonical 主 checkout 的 gitignored
    ``artifacts/``/``temp/``（.gitignore 已忽略二者——备份含生产 env/config，
    绝不允许落入可提交位置）。语义镜像 app.ops.backup 侧 artifacts/temp
    纪律并收严为「必须位于这两个 checkout 的基之下」。
    """
    roots = [repo_root]
    canonical = canonical_checkout_root(repo_root)
    if canonical != repo_root:
        roots.append(canonical)
    return tuple(root / name for root in roots for name in ("artifacts", "temp"))


def locate_in_approved_base(path: Path,
                            bases: tuple[Path, ...] | None = None) -> str | None:
    """路径必须位于某批准基的**真子目录**——返回仓库相对 posix 串，否则 None。

    用 abspath 而非 resolve()：不跟随任何链接（链接判定独立做）。
    """
    target = Path(os.path.abspath(path))
    for base in bases or approved_backup_bases():
        base_abs = Path(os.path.abspath(base))
        try:
            relative = target.relative_to(base_abs)
        except ValueError:
            continue
        if relative.parts:  # 等于基本身不放行
            return relative.as_posix()
    return None


def _is_linklike(path: Path) -> bool:
    """symlink/junction（reparse point）判定——lstat 不跟随，失败按链接计。"""
    try:
        info = os.lstat(path)
    except OSError:
        return True
    if stat.S_ISLNK(info.st_mode):
        return True
    return getattr(info, "st_reparse_tag", 0) != 0


def _scan_linklike(root: Path) -> tuple[str, ...]:
    """全树扫描（不跟随链接）：root 自身、各级目录与文件任一是 link-like
    即收集其相对 posix 路径（fail-closed：备份树里不允许任何链接形态）。
    """
    offenders: list[str] = []
    if _is_linklike(root):
        return (".",)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in list(dirnames) + filenames:
            entry = Path(dirpath) / name
            if _is_linklike(entry):
                offenders.append(entry.relative_to(root).as_posix())
    return tuple(sorted(offenders))


def backup_root_files(root: Path) -> list[Path]:
    """全树文件清单（不跟随链接；link-like 由 _scan_linklike 独立判罚）。"""
    files: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            files.append(Path(dirpath) / name)
    return files


# ---------------------------------------------------------------- 备份校验


def _sha256_file(path: Path) -> tuple[str, int]:
    """字节面哈希（分块读取）——绝不解码内容，值无从泄漏。"""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


@dataclass(frozen=True)
class TableFacts:
    """表集/行数对账结果（纯数据；行值只计数绝不外带）。"""

    declared: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    declared_total: int = 0
    actual_total: int = 0
    row_mismatches: tuple[tuple[str, int, int], ...] = ()


def _validate_tables(manifest_tables: dict[str, Any],
                     database_tables: dict[str, Any]) -> tuple[TableFacts, list[str]]:
    """manifest 表集/行数 vs 30 表锚点 vs database.json 实际行数。纯函数。"""
    failures: list[str] = []
    declared = tuple(sorted(str(name) for name in manifest_tables))
    extra = tuple(name for name in declared if name not in EXPECTED_TABLES)
    missing = tuple(name for name in sorted(EXPECTED_TABLES) if name not in declared)
    if extra or missing or len(declared) != EXPECTED_TABLE_COUNT:
        failures.append(F_TABLE_SET_MISMATCH)
    database_extra = tuple(name for name in database_tables
                           if name not in EXPECTED_TABLES)
    if database_extra or any(name not in database_tables
                             for name in EXPECTED_TABLES):
        failures.append(F_TABLE_SET_MISMATCH)
    row_mismatches: list[tuple[str, int, int]] = []
    declared_total = 0
    actual_total = 0
    for name in declared:
        try:
            declared_count = int(manifest_tables[name])
        except (KeyError, TypeError, ValueError):
            failures.append(F_TABLE_SET_MISMATCH)
            continue
        declared_total += declared_count
        rows = database_tables.get(name)
        if not isinstance(rows, list):
            continue  # 表集失败已记录；行数对账只对可判定表进行
        actual_total += len(rows)
        if len(rows) != declared_count:
            row_mismatches.append((name, declared_count, len(rows)))
    if row_mismatches:
        failures.append(F_ROW_COUNT_MISMATCH)
    if declared_total != EXPECTED_TOTAL_ROWS or actual_total != EXPECTED_TOTAL_ROWS:
        failures.append(F_FACTS_DRIFT)
    facts = TableFacts(declared, extra, missing, declared_total, actual_total,
                       tuple(row_mismatches))
    return facts, failures


def _validate_files(manifest_files: dict[str, Any], actual_files: frozenset[str],
                    backup_root: Path) -> tuple[tuple[str, ...],
                                                tuple[str, ...],
                                                tuple[str, ...],
                                                tuple[dict[str, Any], ...],
                                                list[str]]:
    """文件集 + 逐文件 SHA256 对账（只做字节哈希，绝不解码内容）。

    返回 (声明集排序, 树内多出未声明, 声明而树内缺失, 逐文件哈希记录, 失败码)。
    """
    failures: list[str] = []
    declared_map = {str(key): str(value) for key, value in manifest_files.items()}
    declared = frozenset(declared_map)
    extra = tuple(sorted(actual_files - declared))    # 树内多出（manifest 之外）
    missing = tuple(sorted(declared - actual_files))  # 声明而树内缺失
    if extra or missing or len(declared) != EXPECTED_FILE_COUNT:
        failures.append(F_FILE_SET_MISMATCH)
    records: list[dict[str, Any]] = []
    for name in sorted(declared & actual_files):
        declared_hash = declared_map[name].lower()
        actual_hash, size = _sha256_file(backup_root.joinpath(*name.split("/")))
        ok = declared_hash == actual_hash
        if not ok:
            failures.append(F_FILE_HASH_MISMATCH)
        records.append({
            "file": name,
            "size": size,
            "declared_sha256": declared_hash,
            "actual_sha256": actual_hash,
            "ok": ok,
        })
    return (tuple(sorted(declared)), extra, missing, tuple(records), failures)


@dataclass(frozen=True)
class BackupFacts:
    """备份校验事实（纯数据；只含表名/文件名/计数/尺寸/哈希）。"""

    valid: bool = False
    failures: tuple[str, ...] = ()
    location_approved: bool = False
    location_relative: str = ""
    link_like_paths: tuple[str, ...] = ()
    manifest_sha256: str = ""
    schema: str = ""
    tables: TableFacts = TableFacts()
    declared_files: tuple[str, ...] = ()
    actual_files_count: int = 0
    extra_files: tuple[str, ...] = ()
    missing_files: tuple[str, ...] = ()
    file_hashes: tuple[dict[str, Any], ...] = ()


def _early_facts(failures: list[str], relative: str | None,
                 link_like: tuple[str, ...], manifest_sha: str = "",
                 schema: str = "") -> BackupFacts:
    return BackupFacts(
        failures=tuple(dict.fromkeys(failures)),
        location_approved=relative is not None,
        location_relative=relative or "",
        link_like_paths=link_like,
        manifest_sha256=manifest_sha,
        schema=schema,
    )


def validate_backup(backup_dir: Path,
                    bases: tuple[Path, ...] | None = None) -> BackupFacts:
    """备份目录 → 完整 fail-closed 校验（只读；绝不解码 env/config 值）。"""
    failures: list[str] = []
    relative = locate_in_approved_base(backup_dir, bases)
    if relative is None:
        # fail-fast：未批准位置连内容都不遍历/哈希（只回显目录名）
        return _early_facts([F_LOCATION_UNAPPROVED], None, ())
    if not backup_dir.is_dir():
        failures.append(F_BACKUP_MISSING)
        return _early_facts(failures, relative, ())
    link_like = _scan_linklike(backup_dir)
    if link_like:
        failures.append(F_PATH_LINK_LIKE)
    try:
        manifest_bytes = (backup_dir / MANIFEST_NAME).read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        failures.append(F_MANIFEST_UNREADABLE)
        return _early_facts(failures, relative, link_like)
    if not isinstance(manifest, dict):
        failures.append(F_MANIFEST_UNREADABLE)
        return _early_facts(failures, relative, link_like)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest().upper()
    schema = str(manifest.get("schema_version") or "")
    manifest_tables = manifest.get("tables")
    manifest_files = manifest.get("files")
    if schema != BACKUP_SCHEMA:
        failures.append(F_SCHEMA_MISMATCH)
    if manifest_sha != MANIFEST_SHA256_ANCHOR.upper():
        failures.append(F_ANCHOR_MISMATCH)
    if not isinstance(manifest_tables, dict) or not isinstance(manifest_files, dict):
        failures.append(F_MANIFEST_UNREADABLE)
        return _early_facts(failures, relative, link_like, manifest_sha, schema)
    database_tables = _load_database_tables(
        backup_dir, {str(key) for key in manifest_files}, failures)
    table_facts, table_failures = _validate_tables(manifest_tables, database_tables)
    failures.extend(table_failures)
    actual_files = frozenset(
        entry.relative_to(backup_dir).as_posix()
        for entry in backup_root_files(backup_dir) if entry.name != MANIFEST_NAME
    )
    declared_files, extra_files, missing_files, file_hashes, file_failures = (
        _validate_files(manifest_files, actual_files, backup_dir))
    failures.extend(file_failures)
    if len(manifest_files) != EXPECTED_FILE_COUNT or len(actual_files) != EXPECTED_FILE_COUNT:
        failures.append(F_FACTS_DRIFT)
    return BackupFacts(
        valid=not failures,
        failures=tuple(dict.fromkeys(failures)),
        location_approved=relative is not None,
        location_relative=relative or "",
        link_like_paths=link_like,
        manifest_sha256=manifest_sha,
        schema=schema,
        tables=table_facts,
        declared_files=declared_files,
        actual_files_count=len(actual_files),
        extra_files=extra_files,
        missing_files=missing_files,
        file_hashes=file_hashes,
    )


def _load_database_tables(backup_dir: Path, declared_names: set[str],
                          failures: list[str]) -> dict[str, Any]:
    """加载 database.json 行集合——只为计数；行值绝不进入任何输出。"""
    if DATABASE_NAME not in declared_names:
        return {}
    try:
        payload = json.loads(
            (backup_dir / DATABASE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        failures.append(F_DATABASE_UNREADABLE)
        return {}
    return payload if isinstance(payload, dict) else {}


# ---------------------------------------------------------------- 只读 Docker 面


@dataclass(frozen=True)
class DockerFacts:
    """只读 Docker 面事实（卷名/镜像引用均为 repo 公开锚点）。"""

    readable: bool
    server_version: str = "unavailable"
    persistent_present: tuple[str, ...] = ()
    cache_volume_present: bool = False
    missing_images: tuple[str, ...] = ()


def probe_docker(runner: Runner, project: str) -> DockerFacts:
    """只读 Docker 面：version → volume ls → image inspect；任何查询失败
    fail-closed 为 docker-state-unreadable（readable=False，不下任何卷/镜像
    结论）。镜像 inspect 的 rc!=0 = 镜像缺失（docker 自身语义）。
    """
    try:
        engine_ok, version = m231.probe_engine(runner)
    except m231.recovery.RunnerError:
        return DockerFacts(readable=False)
    if not engine_ok:
        return DockerFacts(readable=False, server_version=version)
    try:
        query_ok, names = m231.probe_volume_names(runner)
    except m231.recovery.RunnerError:
        return DockerFacts(readable=False, server_version=version)
    if not query_ok:
        return DockerFacts(readable=False, server_version=version)
    persistent = tuple(
        compose_volume_name(project, key)
        for key in PERSISTENT_VOLUME_KEYS
        if compose_volume_name(project, key) in names
    )
    cache_present = compose_volume_name(project, CACHE_VOLUME_KEYS[0]) in names
    missing: list[str] = []
    for ref in REQUIRED_IMAGE_REFS:
        try:
            result = runner.run(
                ["docker", "image", "inspect", ref, "--format", "{{.Id}}"],
                timeout=30.0,
            )
        except m231.recovery.RunnerError:
            return DockerFacts(readable=False, server_version=version,
                               persistent_present=persistent,
                               cache_volume_present=cache_present)
        if result.returncode != 0:
            missing.append(ref)
    return DockerFacts(readable=True, server_version=version,
                       persistent_present=persistent,
                       cache_volume_present=cache_present,
                       missing_images=tuple(missing))


# ---------------------------------------------------------------- 判定与 runbook


def collect_blockers(backup: BackupFacts, docker: DockerFacts) -> tuple[str, ...]:
    """阻塞归并（确定性顺序；词表与任务书一致）。"""
    blockers: list[str] = []
    if not backup.valid:
        blockers.append(BLOCK_BACKUP_INVALID)
    if not docker.readable:
        blockers.append(BLOCK_DOCKER_UNREADABLE)
    if docker.readable and docker.persistent_present:
        blockers.append(BLOCK_EXISTING_DATA)
    if docker.readable and docker.missing_images:
        blockers.append(BLOCK_IMAGE_MISSING)
    return tuple(blockers)


def build_actions(blockers: tuple[str, ...], backup: BackupFacts,
                  docker: DockerFacts) -> tuple[str, ...]:
    """阻塞 → 文档化动作（零 secret/零绝对路径；永不自动执行）。"""
    actions: list[str] = []
    codes = set(blockers)
    if BLOCK_BACKUP_INVALID in codes:
        detail = ", ".join(backup.failures) or "未知"
        actions.append(
            f"backup-invalid（{detail}）：按报告 backup 段的表名/文件名/哈希明细"
            "人工裁决——位置未批准→移入 gitignored artifacts/temp；链接形态→"
            "以真实目录替换；schema/锚点/表集/文件集/行数/哈希任一漂移→回到"
            "经校验的 M14-193 备份本体，绝不放宽校验"
        )
    if BLOCK_DOCKER_UNREADABLE in codes:
        actions.append(
            "docker-state-unreadable：启动 Docker Desktop 并等待引擎就绪后重试"
            "——查询不出不等于卷不在场（fail-closed，不下任何卷/镜像结论）"
        )
    if BLOCK_EXISTING_DATA in codes:
        actions.append(
            "existing-data-must-not-be-overwritten：命名持久卷 "
            f"{'、'.join(docker.persistent_present)} 已在场——逻辑重建只允许在"
            "两卷均缺失时进行，绝不覆盖现有数据；如确需重建，先按治理流程对"
            "现有卷显式备份/留证并获授权（本工具绝不创建/删除卷）"
        )
    if BLOCK_IMAGE_MISSING in codes:
        actions.append(
            f"required-image-missing（{'、'.join(docker.missing_images)}）：获准"
            "窗口构建/采纳 aios/minio 自建镜像（tools/ops/minio_image_adoption.py）"
            "或拉取 postgres:17-alpine——本工具与恢复路径绝不 pull/build"
        )
    if not blockers:
        actions.append(
            "ready-to-reconstruct：按报告 runbook 分阶段执行（本工具无执行面，"
            "阶段动作全部由操作者在获准窗口逐段执行并留证）"
        )
    return tuple(actions)


def build_runbook(project: str) -> tuple[str, ...]:
    """分阶段 runbook（仅输出永不执行；零 secret/零本机绝对路径）。

    语义：只在 verdict=ready-to-reconstruct 后逐段执行；每段完成后留证并
    复核前一段前置仍成立（尤其阶段 3 的卷缺失复核——任何时刻两卷已在场
    即停止，绝不覆盖）。
    """
    postgres_volume = compose_volume_name(project, "postgres-data")
    minio_volume = compose_volume_name(project, "minio-data")
    stages: list[str] = []
    stages.append(
        "阶段1 备份复核：重跑本 preflight，确认 verdict=ready-to-reconstruct、"
        f"{EXPECTED_TABLE_COUNT} 表 / {EXPECTED_TOTAL_ROWS} 行 / {EXPECTED_FILE_COUNT} 文件"
        "与 manifest 锚点全部一致（任何漂移即停止）")
    stages.append(
        "阶段2 镜像就绪（获准窗口）：aios/minio 自建镜像经 "
        "tools/ops/minio_image_adoption.py（build --execute 需其精确短语）构建或"
        "在场采纳核查；postgres:17-alpine 经获准窗口拉取或由 compose 首次 up "
        "自动拉取（本工具绝不 pull/build）")
    stages.append(
        f"阶段3 即时复核卷缺失：docker volume ls 确认 {postgres_volume} 与 "
        f"{minio_volume} 均不在场——任一已在场立即停止"
        "（existing-data-must-not-be-overwritten，绝不覆盖）")
    stages.append(
        f"阶段4 显式创建且仅创建两个命名数据卷：docker volume create "
        f"{postgres_volume}；docker volume create {minio_volume}"
        "（绝不创建其它卷、绝不 prune；searxng-cache 可由后续 up 重建）")
    stages.append(
        f"阶段5 仅启动数据服务：docker compose -p {project} --profile local "
        "--env-file infra/env.production-recovery up -d --no-build postgres minio，"
        "等待两服务 healthy（绝不启动其它服务）")
    stages.append(
        "阶段6 迁移 schema：对目标库执行 alembic upgrade head（与 M14-85 演练"
        "同口径——未迁移空库直接 restore 会被既有实现 fail-closed 拒绝）")
    stages.append(
        "阶段7 恢复 DB 数据：既有文档化路径 python -m app.ops.cli restore "
        "--backup-dir <批准的备份目录> --db-url <目标库>（manifest 完整性校验"
        "先行，哈希不符 fail-closed 不触碰目标库；stdout 行数对账 inserted rows）")
    stages.append(
        "阶段8 恢复对象数据：同一 cli restore 附 S3 参数回传上传对象"
        "（--s3-endpoint/--s3-bucket/--s3-access-key/--s3-secret-key 由操作者"
        "注入，值不入库不回显）")
    stages.append(
        f"阶段9 完整性对账：复算 manifest 锚点与 {EXPECTED_TABLE_COUNT} 表/"
        f"{EXPECTED_TOTAL_ROWS} 行/{EXPECTED_FILE_COUNT} 文件逐文件 SHA256；上传对象"
        "内容哈希须与 manifest 声明一致（可选用 python -m app.ops.cli "
        "backup-restore-evidence 机器可读对账）")
    stages.append(
        "阶段10 全栈恢复：既有文档化路径 python tools/ops/production_recovery.py "
        "--dry-run 先看计划，确认后 enforce（或 M14-231 production_restore_"
        'preflight.py --apply --confirm-phrase "APPLY PRODUCTION RESTORE" 守卫短语）')
    stages.append(
        "阶段11 验收：M14-231 production_restore_preflight.py 只读复核终态 + 本地"
        "监听/health 验收 + 公网按 docs/PUBLIC_EDGE_DEPLOYMENT.md §9 "
        "public_edge_preflight.py 与 frpc status 独立验收 + 语音验收"
        "（searxng-cache 缺失仅为提示）")
    return tuple(stages)


# ---------------------------------------------------------------- 编排


def _backup_report_section(backup: BackupFacts) -> dict[str, Any]:
    return {
        "valid": backup.valid,
        "failures": list(backup.failures),
        "location_approved": backup.location_approved,
        "location_relative": backup.location_relative,
        "link_like_paths": list(backup.link_like_paths),
        "schema": backup.schema,
        "manifest_sha256": backup.manifest_sha256,
        "expected": {
            "schema": BACKUP_SCHEMA,
            "manifest_sha256_anchor": MANIFEST_SHA256_ANCHOR,
            "table_count": EXPECTED_TABLE_COUNT,
            "total_rows": EXPECTED_TOTAL_ROWS,
            "file_count": EXPECTED_FILE_COUNT,
        },
        "tables": {
            "declared_count": len(backup.tables.declared),
            "extra": list(backup.tables.extra),
            "missing": list(backup.tables.missing),
            "declared_rows_total": backup.tables.declared_total,
            "actual_rows_total": backup.tables.actual_total,
            "row_mismatches": [
                {"table": table, "declared": declared, "actual": actual}
                for table, declared, actual in backup.tables.row_mismatches
            ],
        },
        "files": {
            "declared_count": len(backup.declared_files),
            "actual_count": backup.actual_files_count,
            "extra": list(backup.extra_files),
            "missing": list(backup.missing_files),
            "hashes": [dict(record) for record in backup.file_hashes],
        },
    }


def run_preflight(*, runner: Runner, log: PreflightLog, backup_dir: Path,
                  project: str = DEFAULT_PROJECT,
                  bases: tuple[Path, ...] | None = None) -> dict[str, Any]:
    """执行备份校验 + 只读 Docker 面并返回报告 dict（含 verdict/blockers/
    actions/runbook）。备份路径仅以仓库相对/名称形式回显。"""
    shown_dir = locate_in_approved_base(backup_dir, bases) or backup_dir.name
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "tool": TOOL_NAME,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": {"backup_dir": shown_dir, "project": project},
    }
    notes: list[str] = [NOTE_RUNBOOK_STAGED_ONLY]

    backup = validate_backup(backup_dir, bases)
    report["backup"] = _backup_report_section(backup)
    if backup.valid:
        log.say(
            f"backup: 有效（{len(backup.tables.declared)} 表 / "
            f"{backup.tables.actual_total} 行 / {len(backup.declared_files)} 文件；"
            "manifest 锚点匹配；位置已批准）"
        )
    else:
        log.say(f"backup: 无效——{', '.join(backup.failures)}（fail-closed）")

    docker = probe_docker(runner, project)
    report["docker"] = {
        "readable": docker.readable,
        "server_version": docker.server_version,
        "persistent_volumes_present": list(docker.persistent_present),
        "cache_volume_present": docker.cache_volume_present if docker.readable else None,
        "required_images": list(REQUIRED_IMAGE_REFS),
        "missing_images": list(docker.missing_images),
    }
    if not docker.readable:
        log.say("docker: 状态不可读——fail-closed（不下任何卷/镜像结论）")
    else:
        log.say(
            f"docker: 可读（{docker.server_version}）；持久卷在场 "
            f"{list(docker.persistent_present) or '无'}；必需镜像缺失 "
            f"{list(docker.missing_images) or '无'}"
        )
        if not docker.cache_volume_present:
            notes.append(NOTE_CACHE_VOLUME_ABSENT)

    blockers = collect_blockers(backup, docker)
    verdict = VERDICT_READY if not blockers else VERDICT_BLOCKED
    report["notes"] = notes
    report["blockers"] = list(blockers)
    report["actions"] = list(build_actions(blockers, backup, docker))
    report["runbook"] = list(build_runbook(project))
    report["verdict"] = verdict
    for code in blockers:
        log.say(f"blocker: {code}")
    log.say(f"verdict: {verdict}（runbook 仅输出，本工具无执行面）")
    return report


def _write_report_atomic(path: Path, report: dict[str, Any]) -> None:
    directory = path.resolve().parent
    directory.mkdir(parents=True, exist_ok=True)
    payload = _scrub_private_paths(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False
    ) as handle:
        handle.write(payload + "\n")
        temp_name = handle.name
    try:
        os.replace(temp_name, path)
    except OSError:
        try:
            os.unlink(temp_name)
        finally:
            raise


def default_backup_dir() -> Path:
    """默认备份目录 = canonical 主 checkout 的 gitignored artifacts 下既定
    子路径（运行时推导，源码零硬编码绝对路径）。"""
    return canonical_checkout_root(REPO_ROOT) / "artifacts" / DEFAULT_BACKUP_SUBPATH


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "逻辑恢复前置检查（只读 fail-closed）：判定缺失的 AIOS 持久卷能否"
            "安全地由 M14-193 已验证逻辑备份分阶段重建；输出分阶段 runbook 但"
            "永不执行（无 --execute 模式；Docker 面仅 version/volume ls/"
            "image inspect 三个只读命令）。"
        ),
    )
    parser.add_argument("--backup-dir", type=Path, default=None,
                        help="备份目录（默认 canonical artifacts 下的 M14-193 "
                             "pre-cutover-backup；必须位于批准的 gitignored "
                             "artifacts/temp 位置）")
    parser.add_argument("--project", default=DEFAULT_PROJECT,
                        help=f"compose 项目名（默认 {DEFAULT_PROJECT}）")
    parser.add_argument("--output", type=Path,
                        help="JSON 报告输出路径（原子写，不含绝对路径）")
    return parser


def main(argv: list[str] | None = None, *, runner: Runner | None = None,
         bases: tuple[Path, ...] | None = None) -> int:
    args = build_parser().parse_args(argv)
    backup_dir = args.backup_dir or default_backup_dir()
    log = PreflightLog()
    log.say(f"=== 逻辑恢复 preflight: project={args.project} ===")
    active_runner = runner or m231.recovery.RealRunner()
    report = run_preflight(
        runner=active_runner, log=log, backup_dir=backup_dir,
        project=args.project, bases=bases,
    )
    for action in report["actions"]:
        log.say(f"action: {action}")
    if args.output:
        try:
            _write_report_atomic(args.output, report)
            log.say(f"报告: {args.output.name}")
        except OSError as cause:
            log.say(f"报告写出失败: {type(cause).__name__}")
            return EXIT_BLOCKED
    return EXIT_OK if report["verdict"] == VERDICT_READY else EXIT_BLOCKED


if __name__ == "__main__":
    sys.exit(main())
