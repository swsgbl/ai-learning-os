"""M6-06 备份 CLI：python -m app.ops.cli backup|restore。

一条命令完成三件套备份/恢复；参数缺省回退环境变量（DATABASE_URL/S3_*）。
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from app.ops.backup import MinioBackupSource, run_backup, run_restore


def _s3_from_args_or_env(args):
    """S3 参数可选：args 显式 > 环境变量；不齐返回 None（只备 DB+配置）。"""
    endpoint = args.s3_endpoint or os.environ.get("S3_ENDPOINT")
    bucket = args.s3_bucket or os.environ.get("S3_BUCKET")
    ak = args.s3_access_key or os.environ.get("S3_ACCESS_KEY")
    sk = args.s3_secret_key or os.environ.get("S3_SECRET_KEY")
    if endpoint and bucket and ak and sk:
        return MinioBackupSource(endpoint, bucket, ak, sk)
    return None


def _db_url_of(args) -> str:
    return args.db_url or os.environ.get("DATABASE_URL") or ""


async def _run_backup(args) -> int:
    db_url = _db_url_of(args)
    if not db_url:
        raise SystemExit("缺少 --db-url 或 DATABASE_URL")
    source = _s3_from_args_or_env(args)
    manifest = await run_backup(
        db_url, args.out, source, [Path(p) for p in args.config or []]
    )
    print("backup ok:", manifest["schema_version"], "tables:", len(manifest["tables"]),
          "files:", len(manifest["files"]))
    return 0


async def _run_restore(args) -> int:
    db_url = _db_url_of(args)
    if not db_url:
        raise SystemExit("缺少 --db-url 或 DATABASE_URL")
    source = _s3_from_args_or_env(args)
    inserted = await run_restore(args.backup_dir, db_url, source,
                                 Path(args.config_target) if args.config_target else None)
    print("restore ok: inserted", inserted, "rows")
    return 0
def main() -> None:
    parser = argparse.ArgumentParser(prog="aios-backup")
    sub = parser.add_subparsers(dest="command", required=True)
    p_b = sub.add_parser("backup", help="三件套备份")
    p_b.add_argument("--db-url", default=None)
    p_b.add_argument("--out", required=True)
    p_b.add_argument("--s3-endpoint", default=None)
    p_b.add_argument("--s3-bucket", default=None)
    p_b.add_argument("--s3-access-key", default=None)
    p_b.add_argument("-" + "-s3-secret-key", default=None)
    p_b.add_argument("--config", action="append", default=None)
    p_r = sub.add_parser("restore", help="三件恢复")
    p_r.add_argument("--backup-dir", required=True)
    p_r.add_argument("--db-url", default=None)
    p_r.add_argument("--s3-endpoint", default=None)
    p_r.add_argument("--s3-bucket", default=None)
    p_r.add_argument("--s3-access-key", default=None)
    p_r.add_argument("--s3-secret-key", default=None)
    p_r.add_argument("--config-target", default=None)
    args = parser.parse_args()
    if args.command == "backup":
        raise SystemExit(asyncio.run(_run_backup(args)))
    raise SystemExit(asyncio.run(_run_restore(args)))


if __name__ == "__main__":
    main()
