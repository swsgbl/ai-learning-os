"""M6-06 Backup/restore drill：数据库、对象存储、配置三件套备份与恢复。

验收（backlog M6-06）：数据库、对象存储和配置可备份；恢复后考试与报告仍可读。

设计原则（ADR 惯例延续）：
- 零外部依赖真实实现：备份为 SQLAlchemy 逻辑备份（ORM 反射全表 -> JSON 快照），
  SQLite 与 PostgreSQL 同一路径；部署级工具（pg_dump/mc mirror）可按同协议替换。
- manifest 为备份完整性真相源：记录表计数与全文件 sha256；
  恢复前强制校验，hash 不符 fail-closed 拒绝恢复（BackupIntegrityError），
  且校验先行——目标库/存储在未通过校验前不被触碰。
- restore 语义 = 全量替换（逆拓扑序清空 + 拓扑序重灌，单事务），
  恢复目标可重复演练（同目标可反复 restore）。
- created_at 显式传参（None 则 UTC now）——manifest 结构确定性可测。
"""
from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, unquote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import create_engine

SCHEMA_VERSION = "aios-backup-v1"

DATABASE_FILE = "database.json"
MANIFEST_FILE = "manifest.json"
OBJECTS_DIR = "objects"
CONFIGS_DIR = "configs"


class BackupIntegrityError(Exception):
    """备份完整性校验失败：恢复被拒绝（fail-closed）。"""


class ObjectBackupSource(Protocol):
    """对象存储备份源最小接口：list_keys/get/put。"""

    def list_keys(self) -> list[str]: ...

    def get(self, key: str) -> bytes: ...

    def put(self, key: str, data: bytes) -> None: ...


class MinioBackupSource:
    """boto3 适配：bucket 显式传参（drill 用独立 bucket 隔离，不碰主 bucket）。"""

    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str) -> None:
        import boto3

        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def list_keys(self) -> list[str]:
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket):
            for item in page.get("Contents", []):
                keys.append(item["Key"])
        return keys

    def get(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()

    def put(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data)


def _serialize(value):
    """JSON 安全化：datetime/Decimal/bytes -> JSON 标量；其余原样透传。"""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__bytes_b64__": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, (dict, list)):
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    return value


def _coerce(row: dict, table) -> dict:
    """restore 端按 ORM 列 python_type 把 JSON 标量转回原生类型。"""
    out: dict = {}
    for key, value in row.items():
        col = table.c.get(key)
        py = getattr(getattr(col, "type", None), "python_type", None) if col is not None else None
        if value is None:
            out[key] = None
        elif py is datetime and isinstance(value, str):
            out[key] = datetime.fromisoformat(value)
        elif py is Decimal and isinstance(value, (int, float, str)):
            out[key] = Decimal(str(value))
        elif py is bytes and isinstance(value, dict) and "__bytes_b64__" in value:
            out[key] = base64.b64decode(value["__bytes_b64__"])
        elif isinstance(value, str) and py in (dict, list):
            out[key] = json.loads(value)
        else:
            out[key] = value
    return out


def _object_file(key: str) -> str:
    """对象 key -> 备份目录内相对文件名（URL 转义保留层级）。"""
    return quote(key, safe="/")


async def dump_database(engine: AsyncEngine) -> dict[str, list[dict]]:
    """反射全表 select 全量，拓扑序产出 {表: [行 dicts]}（值经 _serialize）。"""
    async with engine.connect() as conn:
        return await conn.run_sync(_dump_sync)


def _dump_sync(conn):
    from sqlalchemy import MetaData

    meta = MetaData()
    meta.reflect(bind=conn)
    data: dict[str, list[dict]] = {}
    for table in meta.sorted_tables:
        rows = conn.execute(select(table)).mappings().all()
        data[table.name] = [{k: _serialize(v) for k, v in dict(r).items()} for r in rows]
    return data


async def restore_database(engine: AsyncEngine, data: Mapping[str, list[dict]]) -> int:
    """全量替换：目标库已有表 -> 逆拓扑序 delete + 拓扑序重灌，单事务；返回插入行数。"""
    from sqlalchemy import MetaData

    def _restore_sync(conn):
        meta = MetaData()
        meta.reflect(bind=conn)
        by_name = {t.name: t for t in meta.sorted_tables}
        missing = set(data) - set(by_name)
        if missing:
            raise BackupIntegrityError(f"目标库缺表 {sorted(missing)}——先建表/迁移再恢复")
        payload = {}
        for name, rows in data.items():
            payload[name] = [_coerce(r, by_name[name]) for r in rows]
        for table in reversed(meta.sorted_tables):
            conn.execute(table.delete())
        inserted = 0
        for table in meta.sorted_tables:
            rows = payload.get(table.name, [])
            if rows:
                conn.execute(table.insert(), rows)
                inserted += len(rows)
        return inserted

    async with engine.begin() as conn:
        return await conn.run_sync(_restore_sync)


def dump_objects(source: ObjectBackupSource, out_dir: Path) -> dict[str, str]:
    """对象存储 dump：全部 key 下载到 objects/ 子目录，返回 {key: sha256}。"""
    objects_dir = Path(out_dir) / OBJECTS_DIR
    objects_dir.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for key in source.list_keys():
        data = source.get(key)
        fpath = objects_dir / _object_file(key)
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_bytes(data)
        hashes[key] = hashlib.sha256(data).hexdigest()
    return hashes


def restore_objects(source, backup_dir: Path) -> int:
    """objects/ 子目录回传到存储（key 由文件路径反转义还原）。"""
    objects_dir = Path(backup_dir) / OBJECTS_DIR
    if not objects_dir.exists():
        return 0
    restored = 0
    for fpath in sorted(objects_dir.rglob("*")):
        if fpath.is_dir():
            continue
        rel = fpath.relative_to(objects_dir).as_posix()
        key = unquote(rel)
        source.put(key, fpath.read_bytes())
        restored += 1
    return restored


def backup_configs(paths: Sequence[Path], out_dir: Path) -> dict[str, str]:
    """配置文件复制进 configs/（保留相对结构），返回 {相对路径: sha256}。"""
    configs_dir = Path(out_dir) / CONFIGS_DIR
    configs_dir.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for path in paths:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"待备份配置不存在: {p}")
        data = p.read_bytes()
        rel = _config_relpath(p)
        dest = configs_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        hashes[rel] = hashlib.sha256(data).hexdigest()
    return hashes


def _config_relpath(p: Path) -> str:
    """配置文件相对命名：infra/ 下保留 infra/ 前缀，其余取文件名。"""
    parts = p.resolve().parts
    if "infra" in parts:
        idx = parts.index("infra")
        return "/".join(parts[idx:])
    return p.name


def restore_configs(backup_dir: Path, target_dir: Path) -> list[str]:
    """configs/ 内容复制回目标目录，返回恢复的相对路径列表。"""
    configs_dir = Path(backup_dir) / CONFIGS_DIR
    target = Path(target_dir)
    restored: list[str] = []
    for fpath in sorted(configs_dir.rglob("*")):
        if fpath.is_dir():
            continue
        rel = fpath.relative_to(configs_dir).as_posix()
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(fpath.read_bytes())
        restored.append(rel)
    return restored


def _file_hashes(root: Path) -> dict[str, str]:
    """目录内全部文件相对路径 -> sha256（manifest 校验面）。"""
    hashes: dict[str, str] = {}
    for fpath in sorted(Path(root).rglob("*")):
        if fpath.is_file():
            rel = fpath.relative_to(root).as_posix()
            hashes[rel] = hashlib.sha256(fpath.read_bytes()).hexdigest()
    return hashes


def build_manifest(
    out_dir: Path, db_counts: Mapping[str, int], created_at: datetime | str | None = None
) -> dict:
    """manifest：schema/时间/表计数/全文件 sha256（database.json/objects/configs 全覆盖）。"""
    root = Path(out_dir)
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": created_at or datetime.now(UTC).isoformat(),
        "tables": dict(db_counts),
        "files": _file_hashes(root),
    }
    # manifest 自身不入 files（校验对象 = 除 manifest 外全部文件）
    manifest["files"].pop(MANIFEST_FILE, None)
    return manifest


def verify_manifest(backup_dir: Path, manifest: Mapping) -> None:
    """恢复前强制校验：文件集一致且逐文件 sha256 相符，不符 fail-closed。"""
    actual = _file_hashes(backup_dir)
    actual.pop(MANIFEST_FILE, None)
    expected = dict(manifest.get("files", {}))
    expected.pop(MANIFEST_FILE, None)
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise BackupIntegrityError(f"备份文件集不一致 missing={missing} extra={extra}")
    for rel, digest in expected.items():
        if actual[rel] != digest:
            raise BackupIntegrityError(f"备份文件 hash 不符: {rel}")


async def run_backup(db_url: str, out_dir, objects: ObjectBackupSource | None,
                     config_paths: Sequence[Path], created_at: datetime | str | None = None) -> dict:
    """三件套备份编排：DB dump + 对象 dump + 配置复制 + manifest。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(db_url)
    try:
        data = await dump_database(engine)
    finally:
        await engine.dispose()
    rows_by_table = {name: len(rows) for name, rows in data.items()}
    (out_dir / DATABASE_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if objects is not None:
        dump_objects(objects, out_dir)
    backup_configs(config_paths, out_dir)
    manifest = build_manifest(out_dir, rows_by_table, created_at)
    (out_dir / MANIFEST_FILE).write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


async def run_restore(backup_dir, db_url: str, objects=None, config_target: Path | None = None) -> int:
    """完整性校验先行 -> DB 全量替换 + 对象回传 + 配置回写；返回 DB 插入行数。"""
    backup_dir = Path(backup_dir)
    manifest_path = backup_dir / MANIFEST_FILE
    if not manifest_path.is_file():
        raise BackupIntegrityError(f"备份目录缺 manifest.json: {backup_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verify_manifest(backup_dir, manifest)
    data = json.loads((backup_dir / DATABASE_FILE).read_text(encoding="utf-8"))
    engine = create_engine(db_url)
    try:
        inserted = await restore_database(engine, data)
    finally:
        await engine.dispose()
    if objects is not None:
        restore_objects(objects, backup_dir)
    if config_target is not None:
        restore_configs(backup_dir, config_target)
    return inserted
