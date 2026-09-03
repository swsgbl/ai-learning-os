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
from app.ops.data_hygiene import (
    DEFAULT_ACCEPTANCE_MARKERS,
    build_data_inventory,
    run_acceptance_clean,
    validate_markers,
)
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


def _run_data_inventory(args) -> int:
    """python -m app.ops.cli data-inventory：只读生产数据/风险盘点。"""
    import json as _json

    db_url = _db_url_of(args)
    if not db_url:
        print("缺少 --db-url 或 DATABASE_URL，拒绝盘点（fail-closed）")
        return 2
    print(_json.dumps(asyncio.run(build_data_inventory(db_url)), ensure_ascii=False, indent=2))
    return 0


def _run_legacy_paper_report(args) -> int:
    """python -m app.ops.cli legacy-paper-report：历史无归属试卷只读分类报告。

    默认输出人类可读摘要（不含生产 paper ID）；--json 输出完整明细；
    --output 写文件时强制落在 gitignore 的 artifacts/temp 目录（防误提交
    生产 ID 清单）。任何路径都不修改数据库。
    """
    import json as _json

    from app.ops.legacy_papers import (
        build_legacy_paper_report,
        format_report_summary,
        is_safe_artifact_path,
    )

    db_url = _db_url_of(args)
    if not db_url:
        print("缺少 --db-url 或 DATABASE_URL，拒绝生成报告（fail-closed）")
        return 2
    if args.output and not is_safe_artifact_path(args.output):
        print(
            f"拒绝写入 {args.output}：报告含生产 paper ID，只能写入 gitignore 的 "
            "artifacts/ 或 temp/ 目录"
        )
        return 2
    report = asyncio.run(build_legacy_paper_report(db_url))
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            _json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"报告已写入: {args.output}")
    if args.as_json:
        print(_json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report_summary(report))
    return 0


def _run_legacy_paper_migrate(args) -> int:
    """python -m app.ops.cli legacy-paper-migrate <path>：历史试卷安全迁移。

    keep-public / assign-owner / export-delete 三路径，默认 dry-run 只打印
    计划；--yes 才执行。只接受精确 paper ID（--paper-id 可重复或 --ids-file），
    未知 ID 整体拒绝；export-delete 必须先导出校验 JSONL 才删库，被历史
    考试引用的卷一律拒绝删除（人工处理）。
    """
    import json as _json

    from app.ops.legacy_papers import (
        is_safe_artifact_path,
        resolve_paper_ids,
        run_legacy_migrate,
    )

    db_url = _db_url_of(args)
    if not db_url:
        print("缺少 --db-url 或 DATABASE_URL，拒绝迁移（fail-closed）")
        return 2
    try:
        paper_ids = resolve_paper_ids(args.paper_id, args.ids_file)
    except (OSError, ValueError) as cause:
        print(f"试卷 ID 解析失败: {cause}")
        return 2
    export_path = None
    if args.path == "export-delete":
        if not args.export:
            print("export-delete 需要 --export <jsonl 路径>（导出校验通过才删库）")
            return 2
        if not is_safe_artifact_path(args.export):
            print(
                f"拒绝导出到 {args.export}：导出含生产数据，只能写入 gitignore 的 "
                "artifacts/ 或 temp/ 目录"
            )
            return 2
        export_path = Path(args.export)
    if args.path == "assign-owner" and not args.to:
        print("assign-owner 需要 --to <已存在用户名或用户 ID>")
        return 2
    try:
        report = asyncio.run(
            run_legacy_migrate(
                db_url,
                args.path,
                paper_ids,
                execute=args.yes,
                owner_ref=args.to,
                export_path=export_path,
            )
        )
    except RuntimeError as cause:
        print(f"执行失败（事务已回滚）: {cause}")
        return 1
    print(_json.dumps(report, ensure_ascii=False, indent=2))
    if not args.yes:
        print("[dry-run] 未修改数据库；确认计划后加 --yes 执行。")
    elif report.get("failure"):
        print(f"[失败] {report['failure']}")
    return int(report.get("exit_code", 0))



def _run_draft_owner_report(args) -> int:
    """python -m app.ops.cli draft-owner-report：generation/variant 历史无归属
    草稿只读报告（M10-04 下一切片）。

    范围仅 owner_id IS NULL 的历史草稿；默认人类可读摘要（不含生产
    draft_id），--json 全量明细；--output 复用 legacy 报告的 artifacts/temp
    路径护栏。报告对数据库零写入，不猜归属（多 owner/NULL/缺失一律
    manual_review）。
    """
    import json as _json

    from app.ops.draft_ownership import (
        build_draft_owner_report,
        format_draft_owner_report_summary,
    )
    from app.ops.legacy_papers import is_safe_artifact_path

    db_url = _db_url_of(args)
    if not db_url:
        print("缺少 --db-url 或 DATABASE_URL，拒绝生成报告（fail-closed）")
        return 2
    if args.output and not is_safe_artifact_path(args.output):
        print(
            f"拒绝写入 {args.output}：报告含生产 draft ID，只能写入 gitignore 的 "
            "artifacts/ 或 temp/ 目录"
        )
        return 2
    report = asyncio.run(
        build_draft_owner_report(db_url, [args.kind] if args.kind else None)
    )
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            _json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"报告已写入: {args.output}")
    if args.as_json:
        print(_json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_draft_owner_report_summary(report))
    return 0


def _run_draft_owner_migrate(args) -> int:
    """python -m app.ops.cli draft-owner-migrate <path>：历史草稿归属安全迁移。

    assign-owner / keep-unowned 两路径，默认 dry-run 只打印计划；--yes 才
    执行。--kind 必填且只接受该 kind 的精确草稿 ID（--draft-id 可重复或
    --ids-file），未知 ID 或属于另一 kind 的 ID 整体拒绝；只处理
    owner_id IS NULL 的行，不改 status、不改业务 JSON、不删数据；执行事务
    内 FOR UPDATE 复核目标用户/行状态/行数，审计与更新同事务。
    """
    import json as _json

    from app.ops.draft_ownership import resolve_draft_ids, run_draft_migrate

    db_url = _db_url_of(args)
    if not db_url:
        print("缺少 --db-url 或 DATABASE_URL，拒绝迁移（fail-closed）")
        return 2
    try:
        draft_ids = resolve_draft_ids(args.draft_id, args.ids_file)
    except (OSError, ValueError) as cause:
        print(f"草稿 ID 解析失败: {cause}")
        return 2
    if args.path == "assign-owner" and not args.to:
        print("assign-owner 需要 --to <已存在用户名或用户 ID>")
        return 2
    try:
        report = asyncio.run(
            run_draft_migrate(
                db_url,
                args.kind,
                args.path,
                draft_ids,
                execute=args.yes,
                owner_ref=args.to,
            )
        )
    except RuntimeError as cause:
        print(f"执行失败（事务已回滚）: {cause}")
        return 1
    print(_json.dumps(report, ensure_ascii=False, indent=2))
    if not args.yes:
        print("[dry-run] 未修改数据库；确认计划后加 --yes 执行。")
    elif report.get("failure"):
        print(f"[失败] {report['failure']}")
    return int(report.get("exit_code", 0))


def _run_acceptance_clean(args) -> int:
    """python -m app.ops.cli acceptance-clean：默认 dry-run 的窄范围验收清理。"""
    import json as _json

    db_url = _db_url_of(args)
    if not db_url:
        print("缺少 --db-url 或 DATABASE_URL，拒绝清理（fail-closed）")
        return 2
    markers = tuple(args.marker or DEFAULT_ACCEPTANCE_MARKERS)
    try:
        validate_markers(markers)
    except ValueError as cause:
        print(f"验收标记无效: {cause}")
        return 2
    report = asyncio.run(run_acceptance_clean(db_url, markers, execute=args.yes))
    print(_json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("object_store", {}).get("errors"):
        return 1
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

    action = args.action
    repo = _make_users_repo(getattr(args, "db_url", None))

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
        import uuid as _uuid

        updated = await repo.set_role(
            username,
            target_role,
            audit={
                "action": f"role.{action}",
                "target_type": "user",
                "target_id": username,
                "request_id": f"cli-{_uuid.uuid4().hex[:12]}",
                "actor_id": updated_id_of(current),
                "actor_username": "cli-operator",
                "before": {"role": current.role},
                "after": {"role": target_role},
            },
        )
        assert updated is not None
        print(f"{username}: {current.role} -> {target_role}")
        return 0

    return asyncio.run(run())


def updated_id_of(record):
    return record.id


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


def _run_audit_chain_verify(args) -> int:
    """python -m app.ops.cli audit-chain-verify [--db-url URL] [--json]

    M10-04 治理审计哈希链只读校验：全量重算 entries/state 与 audit_log 的
    一一对应、sequence 连续性、genesis/previous/entry hash、head 一致性。
    valid 退出 0，invalid 退出 1，缺 --db-url/连接失败退出 2。
    输出不含 before/after 正文与任何敏感值；对数据库零写入。
    """
    import json as _json

    from sqlalchemy.exc import SQLAlchemyError

    from app.ops.audit_chain_verify import (
        format_verify_summary,
        run_verify,
    )

    db_url = _db_url_of(args)
    if not db_url:
        print("缺少 --db-url 或 DATABASE_URL，拒绝校验（fail-closed）")
        return 2
    try:
        report = run_verify(db_url)
    except (SQLAlchemyError, OSError, ValueError) as cause:
        # 连接失败/无效 URL 等：参数环境问题，非链结论
        print(f"数据库连接失败: {type(cause).__name__}: {cause}")
        return 2
    if getattr(args, "as_json", False):
        print(_json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_verify_summary(report))
    return 0 if report["valid"] else 1


def _run_audit_chain_anchor(args) -> int:
    """python -m app.ops.cli audit-chain-anchor --db-url URL --anchor-file PATH
    [--yes | --verify-only] [--json]

    M10-06 审计链库外锚定：默认 dry-run 只打印将追加的锚行；--yes 才落盘
    （单行追加 + fsync）；--verify-only 只做「DB 链 + 锚文件链 + head 交叉
    一致」校验。追加前 DB 链必须 verify valid、锚文件必须完整自洽、全部
    历史锚点必须命中当前 DB 同 sequence 的 entry_hash（整链重算/回退拒绝）。
    退出码 valid/up-to-date/anchored/dry-run=0，invalid=1，缺参/路径/连接
    失败=2。锚文件每行只含 schema/algorithm/sequence/hash/时间，无敏感值。
    """
    import json as _json

    from sqlalchemy.exc import SQLAlchemyError

    from app.ops.audit_chain_anchor import (
        AnchorInputError,
        format_anchor_summary,
        run_anchor,
    )

    db_url = _db_url_of(args)
    if not db_url:
        print("缺少 --db-url 或 DATABASE_URL，拒绝锚定（fail-closed）")
        return 2
    try:
        report = asyncio.run(
            run_anchor(
                db_url,
                args.anchor_file,
                execute=args.yes,
                verify_only=args.verify_only,
            )
        )
    except AnchorInputError as cause:
        print(f"锚定输入无效（参数或锚文件路径）: {cause}")
        return 2
    except (SQLAlchemyError, OSError, ValueError) as cause:
        # 连接失败/无效 URL/磁盘 IO 等：输入环境问题，非链结论
        print(f"锚定执行失败: {type(cause).__name__}: {cause}")
        return 2
    if getattr(args, "as_json", False):
        print(_json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_anchor_summary(report))
    return 0 if report["valid"] else 1


def _run_production_preflight(args) -> int:
    """python -m app.ops.cli production-preflight --db-url URL
    --phase pre-migration|post-migration [--anchor-file PATH] [--json] [--output PATH]

    M10-07 生产切换只读汇总预检（runbook 防呆汇总，非 release-check 替代）：
    不执行迁移、不写数据库、不写锚文件、不清理数据、不启停服务。检查项：
    连通与库名（不输出 URL/凭据）、alembic current/head 只读对账、
    audit-chain-verify 语义（pre 允许 0027 表缺失=pending，post 必须 valid）、
    锚定 verify-only（post 缺失=not_configured，按 runbook 人工完成，工具
    不自动创建）、历史治理聚合计数（不输出生产 ID）。
    退出码：无 fail=0 / 存在 fail=1 / 输入/锚路径/连接错误=2；pending 绝不
    包装成 pass。无 --yes 参数——本命令没有任何执行形态。
    """
    import json as _json

    from sqlalchemy.exc import SQLAlchemyError

    from app.ops.audit_chain_anchor import AnchorInputError
    from app.ops.legacy_papers import is_safe_artifact_path
    from app.ops.production_preflight import (
        format_preflight_summary,
        redact_secrets,
        run_preflight,
    )

    db_url = _db_url_of(args)
    if not db_url:
        print("缺少 --db-url 或 DATABASE_URL，拒绝预检（fail-closed）")
        return 2
    if args.output and not is_safe_artifact_path(args.output):
        print(
            f"拒绝写入 {args.output}：报告只能写入 gitignore 的 artifacts/ 或 temp/ 目录"
        )
        return 2
    try:
        report = asyncio.run(
            run_preflight(db_url, args.phase, anchor_file=args.anchor_file)
        )
    except AnchorInputError as cause:
        print(f"锚文件输入无效（参数或锚文件路径）: {cause}")
        return 2
    except (SQLAlchemyError, OSError, ValueError) as cause:
        # 连接失败/无效 URL/磁盘 IO：输入环境问题，非检查结论；错误信息
        # 可能内嵌 DB URL，先抹凭据再输出。
        print(
            "预检执行失败（输入或连接问题，未产生检查结论）: "
            + redact_secrets(f"{type(cause).__name__}: {cause}")
        )
        return 2
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            _json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"报告已写入: {args.output}")
    if args.as_json:
        print(_json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_preflight_summary(report))
    return report["exit_code"]


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
    p_di = sub.add_parser("data-inventory", help="生产数据与风险只读盘点")
    p_di.add_argument("--db-url", default=None)
    p_lp = sub.add_parser(
        "legacy-paper-report",
        help="历史无归属试卷只读分类报告（M10-04；默认人类可读摘要，不含生产 ID）",
    )
    p_lp.add_argument("--db-url", default=None)
    p_lp.add_argument(
        "--json", dest="as_json", action="store_true", help="输出完整 JSON 明细（含 paper_id）"
    )
    p_lp.add_argument(
        "--output",
        default=None,
        help="写 JSON 报告到文件（必须位于 gitignore 的 artifacts/temp 目录）",
    )
    p_lm = sub.add_parser(
        "legacy-paper-migrate",
        help="历史试卷安全迁移（keep-public/assign-owner/export-delete；默认 dry-run，--yes 执行）",
    )
    p_lm.add_argument(
        "path", choices=["keep-public", "assign-owner", "export-delete"]
    )
    p_lm.add_argument("--db-url", default=None)
    p_lm.add_argument(
        "--paper-id", action="append", default=None, help="精确试卷 ID，可重复提供"
    )
    p_lm.add_argument(
        "--ids-file", default=None, help="每行一个试卷 ID 的文件（空行与 # 注释忽略）"
    )
    p_lm.add_argument(
        "--to", default=None, help="assign-owner 目标用户（精确用户名或用户 ID，必须已存在）"
    )
    p_lm.add_argument(
        "--export",
        default=None,
        help="export-delete 的 JSONL 导出路径（必须位于 gitignore 的 artifacts/temp 目录）",
    )
    p_lm.add_argument(
        "--yes", action="store_true", help="真正执行（默认仅输出计划，不修改数据库）"
    )
    p_dr = sub.add_parser(
        "draft-owner-report",
        help=(
            "generation/variant 历史无归属草稿只读报告（M10-04；默认人类可读"
            "摘要，不含生产 draft_id）"
        ),
    )
    p_dr.add_argument("--db-url", default=None)
    p_dr.add_argument(
        "--kind",
        choices=["course-generation", "variant-question"],
        default=None,
        help="只报一类草稿（缺省两类都报）",
    )
    p_dr.add_argument(
        "--json", dest="as_json", action="store_true", help="输出完整 JSON 明细（含 draft_id）"
    )
    p_dr.add_argument(
        "--output",
        default=None,
        help="写 JSON 报告到文件（必须位于 gitignore 的 artifacts/temp 目录）",
    )
    p_dm = sub.add_parser(
        "draft-owner-migrate",
        help=(
            "历史草稿归属安全迁移（assign-owner/keep-unowned；默认 dry-run，"
            "--yes 执行）"
        ),
    )
    p_dm.add_argument("path", choices=["assign-owner", "keep-unowned"])
    p_dm.add_argument(
        "--kind",
        required=True,
        choices=["course-generation", "variant-question"],
        help="草稿类型（course-generation / variant-question，必填）",
    )
    p_dm.add_argument("--db-url", default=None)
    p_dm.add_argument(
        "--draft-id", action="append", default=None, help="精确草稿 ID，可重复提供"
    )
    p_dm.add_argument(
        "--ids-file", default=None, help="每行一个草稿 ID 的文件（空行与 # 注释忽略）"
    )
    p_dm.add_argument(
        "--to", default=None, help="assign-owner 目标用户（精确用户名或用户 ID，必须已存在）"
    )
    p_dm.add_argument(
        "--yes", action="store_true", help="真正执行（默认仅输出计划，不修改数据库）"
    )
    p_ac = sub.add_parser(
        "acceptance-clean", help="验收标记数据清理（默认 dry-run，必须 --yes 才执行）"
    )
    p_ac.add_argument("--db-url", default=None)
    p_ac.add_argument(
        "--marker",
        action="append",
        default=None,
        help="显式验收用户名前缀，可重复；缺省 smoke_/voice_smoke_，不支持通配符",
    )
    p_ac.add_argument("--yes", action="store_true", help="真正执行（默认仅输出计划）")
    p_db = sub.add_parser("db-rollback", help="数据库回滚（默认 dry-run，--yes 执行）")
    p_db.add_argument("--steps", type=int, default=1)
    p_db.add_argument("--yes", action="store_true", help="真正执行（破坏性操作显式确认）")
    p_av = sub.add_parser(
        "audit-chain-verify",
        help="治理审计哈希链只读校验（valid=0 / invalid=1 / 参数或连接错误=2）",
    )
    p_av.add_argument("--db-url", default=None)
    p_av.add_argument(
        "--json", dest="as_json", action="store_true", help="输出完整 JSON 报告"
    )
    p_an = sub.add_parser(
        "audit-chain-anchor",
        help=(
            "审计链库外锚定（M10-06；默认 dry-run，--yes 追加，"
            "--verify-only 仅校验；valid=0 / invalid=1 / 参数或路径错误=2）"
        ),
    )
    p_an.add_argument("--db-url", default=None)
    p_an.add_argument(
        "--anchor-file",
        required=True,
        help="append-only JSONL 锚文件路径（拒绝 symlink/目录；父目录必须已存在）",
    )
    p_an.add_argument(
        "--yes", action="store_true", help="真正追加锚行（默认 dry-run 不落盘）"
    )
    p_an.add_argument(
        "--verify-only",
        action="store_true",
        help="只做 DB 链 + 锚文件链 + head 交叉一致校验，不追加（与 --yes 互斥）",
    )
    p_an.add_argument(
        "--json", dest="as_json", action="store_true", help="输出完整 JSON 报告"
    )
    p_pf = sub.add_parser(
        "production-preflight",
        help=(
            "生产切换只读汇总预检（M10-07；不迁移/不写库/不写锚/不启停服务，"
            "无 --yes 执行形态）"
        ),
    )
    p_pf.add_argument("--db-url", default=None)
    p_pf.add_argument(
        "--phase",
        required=True,
        choices=["pre-migration", "post-migration"],
        help=(
            "切换阶段（必选：迁移后忘带 phase 会误用 pre 的宽松语义，"
            "强制显式选择防呆）"
        ),
    )
    p_pf.add_argument(
        "--anchor-file",
        default=None,
        help=(
            "可选锚文件路径；提供且文件存在时只做 verify-only 交叉校验（不写锚）；"
            "post-migration 未提供或文件不存在输出 not_configured（runbook 人工完成）"
        ),
    )
    p_pf.add_argument(
        "--json", dest="as_json", action="store_true", help="输出完整 JSON 报告"
    )
    p_pf.add_argument(
        "--output",
        default=None,
        help="写 JSON 报告到文件（必须位于 gitignore 的 artifacts/temp 目录；默认不落盘）",
    )
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
    if args.command == "data-inventory":
        raise SystemExit(_run_data_inventory(args))
    if args.command == "legacy-paper-report":
        raise SystemExit(_run_legacy_paper_report(args))
    if args.command == "legacy-paper-migrate":
        raise SystemExit(_run_legacy_paper_migrate(args))
    if args.command == "draft-owner-report":
        raise SystemExit(_run_draft_owner_report(args))
    if args.command == "draft-owner-migrate":
        raise SystemExit(_run_draft_owner_migrate(args))
    if args.command == "acceptance-clean":
        raise SystemExit(_run_acceptance_clean(args))
    if args.command == "db-rollback":
        raise SystemExit(_run_db_rollback(args))
    if args.command == "audit-chain-verify":
        raise SystemExit(_run_audit_chain_verify(args))
    if args.command == "audit-chain-anchor":
        raise SystemExit(_run_audit_chain_anchor(args))
    if args.command == "production-preflight":
        raise SystemExit(_run_production_preflight(args))
    if args.command == "admin":
        if args.action != "list" and not args.username:
            print("promote/demote 需要用户名")
            raise SystemExit(2)
        raise SystemExit(_run_admin(args))
    raise SystemExit(asyncio.run(_run_restore(args)))


if __name__ == "__main__":
    main()
