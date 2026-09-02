"""M6-06 备份 CLI：python -m app.ops.cli backup|restore。

一条命令完成三件套备份/恢复；参数缺省回退环境变量（DATABASE_URL/S3_*）。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

from app.ops.backup import MinioBackupSource, run_backup, run_restore
from app.ops.version import alembic_state


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
def _run_version(args) -> int:
    """python -m app.ops.cli version：版本 + git + alembic 真实状态。"""
    import json as _json

    from app.ops.version import build_version_info, git_commit

    info = build_version_info(git=git_commit(), alembic=alembic_state())
    if args.as_json:
        print(_json.dumps(info, ensure_ascii=False))
    else:
        for k, v in info.items():
            print(f"{k}: {v}")
    return 0


def _run_db_rollback(args) -> int:
    """python -m app.ops.cli db-rollback --steps N [--yes]

    数据库回滚 = alembic downgrade -N 的安全包装：默认 dry-run 只打印
    计划与涉及的 revision，不触碰数据库；--yes 才真正执行（破坏性
    操作显式确认）。回滚前打印 backup 提示。
    """
    steps = args.steps
    if steps < 1:
        print("steps 必须 >= 1")
        return 2
    api_dir = Path(__file__).resolve().parents[2]
    state = alembic_state(api_dir)
    current, head = state["current"], state["head"]
    print(f"alembic current={current or '(none)'} head={head or '(none)'}")
    if current is None:
        print("无法确定 current revision，拒绝回滚（先 alembic upgrade head）")
        return 2
    target_note = f"将执行: alembic downgrade -{steps}（{current} -> 前 {steps} 个 revision）"
    if not args.yes:
        print(f"[dry-run] {target_note}")
        print("[dry-run] 未触碰数据库。回滚是破坏性操作：先 backup（cli backup --out <dir>），")
        print('[dry-run] 确认后加 --yes 执行。')
        return 0
    print("[rollback] 回滚是破坏性操作 —— 强烈建议先执行 cli backup。")
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", f"-{steps}"],
        cwd=str(api_dir), capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        print(f"回滚失败: {proc.stdout.strip()} {proc.stderr.strip()}")
        return 1
    after = alembic_state(api_dir)
    print(f"回滚完成: {current} -> {after['current']}")
    return 0


def _make_users_repo(db_url: str | None):
    """admin CLI 共用：DATABASE_URL -> users 仓储（无库即明确失败）。"""
    from app.core.config import get_settings
    from app.db.session import create_engine, make_sessionmaker
    from app.repositories.users import UserRepository

    resolved = db_url or os.environ.get("DATABASE_URL") or get_settings().database_url
    if not resolved:
        print("需要数据库：用 --db-url 或环境变量 DATABASE_URL 指定")
        raise SystemExit(2)
    return UserRepository(make_sessionmaker(create_engine(resolved)))


def _run_admin(args) -> int:
    """python -m app.ops.cli admin promote|demote|list —— 角色运维（M9-04）。

    无默认管理员：首个 admin 必须由持有数据库访问权的运维显式提升。
    提升/降级均写审计（actor=cli，request_id 随机标识）。
    """
    import uuid

    action = args.action
    repo = _make_users_repo(getattr(args, "db_url", None))

    async def _audit(actor_id: str | None, actor_name: str, act: str,
                     target: str, before: dict | None, after: dict | None) -> None:
        from datetime import UTC, datetime

        from sqlalchemy import insert

        from app.db.orm import AuditLogRow

        audit = _make_audit_repo(getattr(args, "db_url", None))
        async with audit._sessionmaker() as session, session.begin():
            await session.execute(
                insert(AuditLogRow).values(
                    actor_id=actor_id,
                    actor_username=actor_name,
                    action=act,
                    target_type="user",
                    target_id=target,
                    before=before,
                    after=after,
                    request_id=f"cli-{uuid.uuid4().hex[:12]}",
                    created_at=datetime.now(UTC),
                )
            )

    async def run() -> int:
        if action == "list":
            users = await repo.list_users()
            for u in users:
                print(f"{u.username}	{u.role}	{u.id}")
            return 0
        username = args.username
        target_role = "admin" if action == "promote" else "learner"
        current = None
        for u in await repo.list_users():
            if u.username == username:
                current = u
                break
        if current is None:
            print(f"用户不存在: {username}")
            return 2
        if current.role == target_role:
            print(f"{username} 已是 {target_role}，无需变更")
            return 0
        updated = await repo.set_role(username, target_role)
        assert updated is not None
        await _audit(
            updated.id, username, f"role.{action}", username,
            {"role": current.role}, {"role": target_role},
        )
        print(f"{username}: {current.role} -> {target_role}")
        return 0

    return asyncio.run(run())


def _make_audit_repo(db_url: str | None):
    from app.core.config import get_settings
    from app.db.session import create_engine, make_sessionmaker
    from app.repositories.audit import AuditRepository

    resolved = db_url or os.environ.get("DATABASE_URL") or get_settings().database_url
    if not resolved:
        print("需要数据库：用 --db-url 或环境变量 DATABASE_URL 指定")
        raise SystemExit(2)
    return AuditRepository(make_sessionmaker(create_engine(resolved)))


def _run_release_check(args) -> int:
    """python -m app.ops.cli release-check [--api-base URL] [--db-url URL]

    本地七项命令门禁默认全跑；--api-base 提供时 live 三项对运行中服务
    执行（E2E walkthrough/voice/license）。任一 fail 退出码 1。
    """
    import httpx

    from app.ops.release_check import (
        build_release_checks,
        run_release_check,
        summarize,
    )

    cmd_checks, live_checks = build_release_checks(db_url=args.db_url)
    client = None
    if args.api_base and not args.local_only:
        client = httpx.Client(
            base_url=args.api_base, trust_env=False, timeout=600.0
        )
    with client or _nullcontext():
        results = run_release_check(
            cmd_checks, None if args.local_only else live_checks, client=client
        )
        all_green, report = summarize(results)
    print(report)
    return 0 if all_green else 1


class _nullcontext:
    """httpx.Client 缺席时的空上下文，保持 with 对称。"""

    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


async def _run_license_report(args) -> int:
    """license-report 子命令：输出四区段授权清单 JSON（无 DB 时含说明段）。"""
    import json as _json

    from app.core.config import get_settings
    from app.ops.license_report import (
        build_license_report,
        collect_api_dependencies,
        collect_model_slots,
        collect_web_dependencies,
        resource_view,
        source_view,
    )

    requirements = args.requirements or (Path(__file__).resolve().parents[2] / "requirements.txt")
    requirements_text = Path(requirements).read_text(encoding="utf-8")
    sources_list: list[dict] = []
    resources_list: list[dict] = []
    drafts_list: list[dict] = []
    db_note = "content_sources/derived_objects 需 --db-url（本次未提供，两段为空）"
    db_url = _db_url_of(args)
    if db_url:
        from sqlalchemy.ext.asyncio import (
            async_sessionmaker,
            create_async_engine,
        )

        from app.repositories.course_import_drafts import CourseImportDraftRepository
        from app.repositories.resources import ResourceRepository
        from app.repositories.sources import SourceRepository

        engine = create_async_engine(db_url)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        sources_list = [source_view(s) for s in await SourceRepository(sessionmaker).list()]
        resources_list = [
            resource_view(r) for r in await ResourceRepository(sessionmaker).list_all()
        ]
        drafts_list = [
            {
                "id": d["id"],
                "status": d["status"],
                "source_license_state": d["source_license_state"],
                "reuse_admission": d["reuse_admission"],
                "resource_id": d.get("resource_id"),
            }
            for d in await CourseImportDraftRepository(sessionmaker).list_by_status(None)
        ]
        await engine.dispose()
        db_note = None
    report = build_license_report(
        dependencies=collect_api_dependencies(requirements_text),
        web=collect_web_dependencies(),
        model_slots=collect_model_slots(get_settings()),
        content_sources=sources_list,
        resources=resources_list,
        course_import_drafts=drafts_list,
    )
    if db_note:
        report["db_note"] = db_note
    print(_json.dumps(report, ensure_ascii=False, indent=2))
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
    p_l = sub.add_parser("license-report", help="依赖/模型/内容源/派生对象授权清单")
    p_l.add_argument("--db-url", default=None)
    p_l.add_argument("--requirements", default=None)
    p_rc = sub.add_parser(
        "release-check", help="发布门禁汇总（lint/typecheck/test/build/migration/backup + live 项）"
    )
    p_rc.add_argument("--api-base", default="http://127.0.0.1:8000")
    p_rc.add_argument("--db-url", default=None)
    p_rc.add_argument("--local-only", action="store_true", help="跳过 live 项（不依赖运行中服务）")
    p_v = sub.add_parser("version", help="版本 + git + alembic 状态")
    p_v.add_argument("--json", dest="as_json", action="store_true")
    p_db = sub.add_parser("db-rollback", help="数据库回滚（默认 dry-run，--yes 执行）")
    p_db.add_argument("--steps", type=int, default=1)
    p_db.add_argument("--yes", action="store_true", help="真正执行（破坏性操作显式确认）")
    p_ad = sub.add_parser("admin", help="角色运维：promote/demote/list（M9-04）")
    p_ad.add_argument("action", choices=["promote", "demote", "list"])
    p_ad.add_argument("username", nargs="?", default=None)
    p_ad.add_argument("--db-url", default=None)
    args = parser.parse_args()
    if args.command == "backup":
        raise SystemExit(asyncio.run(_run_backup(args)))
    if args.command == "license-report":
        raise SystemExit(asyncio.run(_run_license_report(args)))
    if args.command == "release-check":
        raise SystemExit(_run_release_check(args))
    if args.command == "version":
        raise SystemExit(_run_version(args))
    if args.command == "db-rollback":
        raise SystemExit(_run_db_rollback(args))
    if args.command == "admin":
        if args.action != "list" and not args.username:
            print("promote/demote 需要用户名")
            raise SystemExit(2)
        raise SystemExit(_run_admin(args))
    raise SystemExit(asyncio.run(_run_restore(args)))


if __name__ == "__main__":
    main()
