"""M6-06 备份 CLI：python -m app.ops.cli backup|restore。

一条命令完成三件套备份/恢复；参数缺省回退环境变量（DATABASE_URL/S3_*）。
"""
from __future__ import annotations

import argparse
import asyncio
import errno
import os
import subprocess
import sys
import tempfile
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
def _run_backup_restore_evidence(args) -> int:
    """python -m app.ops.cli backup-restore-evidence --backup-dir DIR
    --restore-db-url URL --output PATH [--json]

    M11-04 backup-restore 机器可读演练证据导出器：对隔离恢复库执行
    alembic upgrade head -> run_restore（manifest 完整性校验先行）-> 只读
    全量导出与备份 database.json 逻辑数据全等比对 + inserted_rows 对账
    manifest.tables 计数总和 -> 原子写出 backup-restore.json（gate/step 双
    自声明，直接可作 release-readiness / cutover-rehearsal 的证据文件）。

    护栏先于执行（exit 2、不迁移/不恢复/不写输出）：备份目录必须在
    gitignore 的 artifacts/temp 内且 manifest.json/database.json 齐备合法；
    恢复目标 URL 必须过 evaluate_pg_test_url 隔离白名单（主库/维护库/缺
    库名/非 PG 连接前拒绝）；输出必须在 artifacts/temp 内且不得位于备份
    目录内或等于备份目录（保持 manifest 校验面不变）；symlink 一律拒绝。
    verified=false（数据/行数不一致）证据照常落盘、exit 1——如实记录失败，
    不伪装 pass；迁移/恢复失败 exit 2 不产证据（错误先抹凭据，旧 evidence
    字节原样保留、无 .tmp 残留）。--json 时 stdout 纯 JSON、提示走 stderr。
    """
    import json as _json

    from sqlalchemy.exc import SQLAlchemyError

    from app.ops.backup import BackupIntegrityError
    from app.ops.backup_restore_evidence import (
        DrillExecutionError,
        DrillInputError,
        format_drill_summary,
        run_backup_restore_evidence,
        validate_drill_inputs,
    )
    from app.ops.production_preflight import redact_secrets

    try:
        validate_drill_inputs(args.backup_dir, args.restore_db_url, args.output)
    except DrillInputError as cause:
        print(f"拒绝执行（输入或路径问题，未迁移/未恢复/未写输出）: {cause}")
        return 2
    try:
        evidence, exit_code = asyncio.run(
            run_backup_restore_evidence(args.backup_dir, args.restore_db_url)
        )
    except DrillExecutionError as cause:
        # 迁移子进程消息由抛出方先抹凭据；此处再抹一次（纵深防御，异常
        # 消息可能携带连接串）
        print(
            "恢复演练执行失败（未产生证据）: " + redact_secrets(str(cause))
        )
        return 2
    except BackupIntegrityError as cause:
        # run_restore 完整性校验先行：hash 不符 fail-closed，目标库未被触碰
        print(
            "备份完整性校验失败（恢复被拒绝，未产生证据）: "
            + redact_secrets(str(cause))
        )
        return 2
    except (SQLAlchemyError, OSError, ValueError, KeyError) as cause:
        # 连接失败/无效 URL/磁盘 IO/备份在护栏后被改动（TOCTOU）：输入环境
        # 问题，非演练结论；错误可能内嵌 DB URL，先抹凭据再输出
        print(
            "恢复演练执行失败（连接或 IO 问题，未产生证据）: "
            + redact_secrets(f"{type(cause).__name__}: {cause}")
        )
        return 2
    try:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_report_atomic(
            output_path,
            _json.dumps(evidence, ensure_ascii=False, indent=2),
        )
    except OSError as cause:
        # 目录无法创建/权限不足/磁盘满/replace 失败：证据未落盘或旧文件原样
        # 保留（原子写不产生 partial），不得再打印演练结论（防被误读）。
        print(
            f"报告写入失败（路径/权限/磁盘问题，未产生报告文件）: "
            f"{type(cause).__name__}: {cause}"
        )
        return 2
    # --json 模式下提示走 stderr，stdout 保持纯 JSON（可管道给 jq）
    print(
        f"报告已写入: {args.output}",
        file=sys.stderr if args.as_json else sys.stdout,
    )
    if args.as_json:
        print(_json.dumps(evidence, ensure_ascii=False, indent=2))
    else:
        print(format_drill_summary(evidence))
    return exit_code


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

    --output 把返回的批次报告原子落盘（供 governance-evidence 直接消费）：
    路径护栏/输入冲突/父目录/symlink 预检全部先于数据库访问（违例 exit 2、
    不连库、无输出文件）；写入走 _write_report_atomic，失败旧输出字节原样、
    无 .tmp 残留、exit 2 并如实说明数据库执行与证据落盘状态。带 --output
    时 stdout 只输出报告 JSON（可解析），[dry-run]/[失败]/"报告已写入"
    提示走 stderr。dry-run 与 failure 报告如实落盘并保留原退出码——
    governance-evidence 侧继续拒绝其作为成功批次。
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
    output_path = None
    if args.output:
        output_inputs = [("--ids-file 输入", args.ids_file)]
        if args.path == "export-delete":
            output_inputs.append(("--export 导出", args.export))
        try:
            output_path = _prepare_migrate_output(args.output, output_inputs)
        except (OSError, ValueError) as cause:
            print(f"拒绝写入 {args.output}: {cause}")
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
    report_text = _json.dumps(report, ensure_ascii=False, indent=2)
    if output_path is not None:
        try:
            _write_report_atomic(output_path, report_text)
        except OSError as cause:
            executed_note = (
                "数据库已执行（事务已提交）"
                if report.get("executed")
                else "数据库未修改（dry-run 计划或迁移失败已回滚）"
            )
            print(report_text)
            print(
                f"批次报告落盘失败（{executed_note}，证据未写入 {output_path}，"
                f"旧输出保持原样）: {cause}",
                file=sys.stderr,
            )
            return 2
    print(report_text)
    notice_stream = sys.stderr if output_path is not None else sys.stdout
    if output_path is not None:
        print(f"报告已写入: {output_path}", file=notice_stream)
    if not args.yes:
        print("[dry-run] 未修改数据库；确认计划后加 --yes 执行。", file=notice_stream)
    elif report.get("failure"):
        print(f"[失败] {report['failure']}", file=notice_stream)
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

    --output 把返回的批次报告原子落盘（供 governance-evidence 直接消费），
    护栏与退出码语义同 legacy-paper-migrate：预检（symlink/artifacts-temp/
    目录形态/与 --ids-file 同文件冲突/父目录）先于数据库访问，违例
    exit 2；写入失败旧输出原样、exit 2 并如实说明数据库执行状态；带
    --output 时 stdout 只输出报告 JSON，提示走 stderr；dry-run 与
    failure 报告如实落盘并保留原退出码。
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
    output_path = None
    if args.output:
        try:
            output_path = _prepare_migrate_output(
                args.output, [("--ids-file 输入", args.ids_file)]
            )
        except (OSError, ValueError) as cause:
            print(f"拒绝写入 {args.output}: {cause}")
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
    report_text = _json.dumps(report, ensure_ascii=False, indent=2)
    if output_path is not None:
        try:
            _write_report_atomic(output_path, report_text)
        except OSError as cause:
            executed_note = (
                "数据库已执行（事务已提交）"
                if report.get("executed")
                else "数据库未修改（dry-run 计划或迁移失败已回滚）"
            )
            print(report_text)
            print(
                f"批次报告落盘失败（{executed_note}，证据未写入 {output_path}，"
                f"旧输出保持原样）: {cause}",
                file=sys.stderr,
            )
            return 2
    print(report_text)
    notice_stream = sys.stderr if output_path is not None else sys.stdout
    if output_path is not None:
        print(f"报告已写入: {output_path}", file=notice_stream)
    if not args.yes:
        print("[dry-run] 未修改数据库；确认计划后加 --yes 执行。", file=notice_stream)
    elif report.get("failure"):
        print(f"[失败] {report['failure']}", file=notice_stream)
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
    [--local-only] [--json] [--output PATH]

    本地七项命令门禁默认全跑；--api-base 提供时 live 三项对运行中服务
    执行（E2E walkthrough/voice/license）。任一 fail 退出码 1。

    M11-03 机器可读证据导出：--json 向 stdout 输出纯 JSON（可管道给 jq；
    --output 同用时「报告已写入」提示走 stderr，不污染 JSON 流），
    --output 原子写入 JSON 文件（两者可同用，互不替代）。证据契约兼容
    release-readiness（gate 字段）/ cutover-rehearsal（step 字段）：
    all_green/total/passed/failed_ids/not_executed_ids/execution_scope/
    checks，计数自洽。--local-only 下 live 三项如实 not_executed、
    all_green=false、total 覆盖本地 7 + live 3、passed 只计真实 pass——
    缺席不冒充 pass；failed_ids 只放真实 fail。退出码与证据分工：退出码
    仍按已执行门禁判定（local-only 本地全过=0，不倒退），导出的证据不
    因此伪装全绿（完整门禁 all_green 需 full 模式 10 项全 pass）。

    --output 只允许 gitignore 的 artifacts/temp 目录（复用
    is_safe_artifact_path，越界/普通路径 exit 2 且不执行门禁）；原子
    落盘：同目录临时文件 + fsync + os.replace，symlink 拒绝；写入失败
    exit 2、无 traceback、旧报告字节原样保留、无 .tmp 残留，且不再打印
    门禁结论（防半途报告被误读为完整结论）。九项门禁的命令/超时/环境
    隔离/live 检查逻辑零改动。
    """
    import json as _json

    import httpx

    from app.ops.legacy_papers import is_safe_artifact_path
    from app.ops.release_check import (
        build_release_check_evidence,
        build_release_checks,
        run_release_check,
        summarize,
    )

    if args.output and not is_safe_artifact_path(args.output):
        print(
            f"拒绝写入 {args.output}：报告只能写入 gitignore 的 artifacts/ 或 temp/ 目录"
        )
        return 2
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
        evidence = build_release_check_evidence(
            results,
            skipped_live_checks=live_checks if args.local_only else (),
            execution_scope="local-only" if args.local_only else "full",
        )
    if args.output:
        try:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            _write_report_atomic(
                output_path,
                _json.dumps(evidence, ensure_ascii=False, indent=2),
            )
        except OSError as cause:
            # 目录无法创建/权限不足/磁盘满/replace 失败：证据未落盘或旧
            # 报告原样保留（原子写不产生 partial），不得再打印门禁结论
            # 摘要（避免被误读为完整报告）。
            print(
                f"报告写入失败（路径/权限/磁盘问题，未产生报告文件）: "
                f"{type(cause).__name__}: {cause}"
            )
            return 2
        # --json 模式下提示走 stderr，stdout 保持纯 JSON（可管道给 jq）
        print(
            f"报告已写入: {args.output}",
            file=sys.stderr if args.as_json else sys.stdout,
        )
    if args.as_json:
        print(_json.dumps(evidence, ensure_ascii=False, indent=2))
    else:
        print(report)
    return 0 if all_green else 1


def _run_release_check_isolated(args) -> int:
    """python -m app.ops.cli release-check-isolated [--workdir DIR]
    [--output PATH] [--health-timeout SECONDS] [--json]

    M11-10 隔离本地 full release-check 一键编排器：把 M11-09 的人工流程
    （一次性 gitignored SQLite -> alembic upgrade head -> 127.0.0.1 回环临时
    uvicorn -> /health 就绪 -> full 模式 10 项门禁 -> JSON 证据原子落盘 ->
    finally 关停临时 API）收敛为一条命令。环境显式隔离（APP_ENV=development
    / VOICE_MODE=local / HOST_BIND_IP=127.0.0.1 / DATABASE_URL=一次性 SQLite，
    继承的 AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL 剥离），不连接任何生产面。

    退出码：全绿=0 / 门禁真实 fail=1（证据照常落盘，execution_scope=full）
    / 护栏或编排失败=2（迁移失败、临时 API 未就绪、写入失败等，均不写证据）。
    all_green 只表示隔离本地运行面全过，不是 production readiness、不授权
    生产发布；production_ready=false 保持不变。
    """
    import json as _json

    from app.ops.release_check_isolated import BOUNDARY_NOTE, run_isolated_release_check

    result = run_isolated_release_check(
        workdir=args.workdir,
        output=args.output,
        health_timeout=args.health_timeout,
    )
    lines: list[str] = []
    if result.workspace is not None:
        lines.append(f"隔离工作区（gitignored，本地审计，不入 git）: {result.workspace}")
    if result.db_path is not None:
        lines.append(f"一次性 SQLite（保留本地，不入 git）: {result.db_path}")
    if result.api_base is not None:
        stop_note = f"，关停方式: {result.server_stop}" if result.server_stop else ""
        lines.append(f"临时 API（仅回环，已收尾{stop_note}）: {result.api_base}")
    if result.evidence_written and result.evidence_path is not None:
        lines.append(f"报告已写入: {result.evidence_path}")
    lines.append(result.summary)
    if BOUNDARY_NOTE not in result.summary:
        lines.append(BOUNDARY_NOTE)
    text = "\n".join(lines)
    if args.as_json and result.evidence is not None:
        # --json：stdout 纯 JSON（可管道给 jq），人读提示走 stderr
        print(_json.dumps(result.evidence, ensure_ascii=False, indent=2))
        print(text, file=sys.stderr)
    else:
        print(text)
    return result.exit_code


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


def _write_report_atomic(path: Path, text: str) -> None:
    """报告原子落盘：同目录临时文件写满 + fsync 后 os.replace 到目标。

    既有目标只在 replace 成功的瞬间被**整体**替换——写入/fsync/replace
    任一步 OSError 都先删除临时文件再上抛原始错误：旧报告字节保持不变，
    本次 partial 报告不留盘（磁盘满/IO 中途失败不再产生截断 JSON，也不
    再截断既有报告）。目标是符号链接时拒绝（打开前与 replace 前双重
    复核；POSIX os.replace 替换链接本身不跟随写穿，Windows 语义无保证
    ——统一 fail-closed 拒绝，不猜测，残余 swap 竞态窗口见 docs 声明）。
    """
    if path.is_symlink():
        raise OSError(errno.ELOOP, "报告目标路径是符号链接，拒绝写入", str(path))
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(tmp_fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        if path.is_symlink():  # 写入期间路径被换成链接：拒绝替换（防写穿 guard 外）
            raise OSError(
                errno.ELOOP, "报告目标路径是符号链接，拒绝写入", str(path)
            )
        os.replace(tmp_path, path)
    except OSError:
        try:
            tmp_path.unlink()
        except OSError:
            pass  # 临时文件清理失败不掩盖原始错误（残留是 .tmp 后缀，不是报告）
        raise


def _reject_output_symlink_components(path: Path, label: str) -> None:
    """路径任何已存在组件（含自身）是 symlink 即拒绝（fail-closed）。

    先于 is_safe_artifact_path 执行：后者内部 resolve() 会跟随 symlink，
    链接指向护栏外时会被误放行。语义对齐 governance-evidence 的同名护栏。
    """
    chain: list[Path] = []
    current = path
    while current.name:
        chain.append(current)
        current = current.parent
    for item in reversed(chain):
        if item.is_symlink():
            raise ValueError(f"{label}路径组件是符号链接，拒绝使用: {item}")


def _prepare_migrate_output(output: str, inputs: list[tuple[str, str | None]]) -> Path:
    """migrate 批次报告 ``--output`` 落盘预检（全部先于数据库访问）。

    违例抛 ValueError/OSError，由调用方 exit 2、不连数据库、不写任何输出：
    任何已存在路径组件（含自身）是 symlink 即拒绝；输出必须位于 gitignore
    的 artifacts/ 或 temp/ 内（批次报告含生产 ID）；已存在且不是常规文件
    （目录等）拒绝；不得与任何输入文件（--ids-file、export-delete 的
    --export）指向同一文件——resolve 后 normcase 归一比较，Windows 上
    大小写与路径分隔符书写形态差异不可绕过。通过则预创建父目录并返回
    目标路径（后续写入走 _write_report_atomic 原子替换）。
    """
    from app.ops.legacy_papers import is_safe_artifact_path

    target = Path(output)
    _reject_output_symlink_components(target, "输出")
    if not is_safe_artifact_path(target):
        raise ValueError(
            f"批次报告含生产 ID，只能写入 gitignore 的 artifacts/ 或 temp/ 目录: {target}"
        )
    if target.exists() and not target.is_file():
        raise ValueError(f"输出路径已存在且不是常规文件: {target}")
    output_key = os.path.normcase(str(target.resolve()))
    for label, source in inputs:
        if not source:
            continue
        if os.path.normcase(str(Path(source).resolve())) == output_key:
            raise ValueError(f"输出路径与{label}是同一文件（拒绝覆盖输入）: {output}")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _run_production_preflight(args) -> int:
    """python -m app.ops.cli production-preflight --db-url URL
    --phase pre-migration|post-migration [--anchor-file PATH] [--json] [--output PATH]

    M10-07 生产切换只读汇总预检（runbook 防呆汇总，非 release-check 替代）：
    不执行迁移、不写数据库、不写锚文件、不清理数据、不启停服务。检查项：
    连通与库名（不输出 URL/凭据）、alembic current/head 只读对账、
    audit-chain-verify 语义（pre 仅允许两链表同时缺失且 audit_log 存在=
    pending_migration，任何其他缺失形态 fail；post 必须 valid）、锚定
    verify-only（post 缺失=not_configured，按 runbook 人工完成，工具
    不自动创建；交叉核对消费主流程同一事务快照）、历史治理聚合计数
    （不输出生产 ID）。
    --output 原子落盘：同目录临时文件 + fsync + os.replace——失败时旧
    报告原样保留、不留 partial（symlink 目标拒绝）；写入失败 exit 2。
    退出码：无 fail=0 / 存在 fail=1 / 输入/锚路径/连接/Alembic 脚本解析/
    报告写入失败=2；pending 绝不包装成 pass。无 --yes 参数——本命令没有
    任何执行形态。
    """
    import json as _json

    from alembic.util.exc import CommandError as AlembicCommandError
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
    except (AlembicCommandError, SyntaxError) as cause:
        # 迁移脚本目录解析失败（脚本损坏/语法错误/目录异常）：输入环境
        # 问题，非检查结论；解析不连库，但错误信息统一先抹凭据再输出。
        # AlembicCommandError 是 alembic 全部异常的公共基类（util.exc）。
        print(
            "预检执行失败（Alembic 脚本目录解析失败，未产生检查结论）: "
            + redact_secrets(f"{type(cause).__name__}: {cause}")
        )
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
        try:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            _write_report_atomic(
                output_path,
                _json.dumps(report, ensure_ascii=False, indent=2),
            )
        except OSError as cause:
            # 目录无法创建/权限不足/磁盘满/replace 失败：报告未落盘或旧
            # 报告原样保留（原子写不产生 partial），不得再打印检查结论
            # 摘要（避免被误读为完整报告）。
            print(
                f"报告写入失败（路径/权限/磁盘问题，未产生报告文件）: "
                f"{type(cause).__name__}: {cause}"
            )
            return 2
        print(f"报告已写入: {args.output}")
    if args.as_json:
        print(_json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_preflight_summary(report))
    return report["exit_code"]


def _run_release_readiness(args) -> int:
    """python -m app.ops.cli release-readiness --evidence-dir DIR
    [--json] [--output PATH]

    M10-11 发布准备与人工审批证据 manifest（只读、fail-closed）：只读取调用方
    显式提供的本地 evidence 目录（JSON/JSONL），对关键证据计算 SHA-256，按门
    汇总 missing/malformed/tampered/blocked/pending/pass，并校验人工审批记录与
    证据哈希的绑定。不连接数据库、不调用 API、不访问网络、不读取环境变量
    （生产密钥物理上进不了本工具）；不执行生产迁移/锚定/清理/WORM/发布/回滚
    ——命令没有 --yes 执行形态。--output 复用 artifacts/temp gitignore 路径
    护栏并原子落盘（symlink 目标拒绝），写入失败 exit 2 且不打印门结论摘要。
    退出码：全部必需门 pass=0 / 任一必需门非 pass=1 / 目录或路径与 IO 问题=2。
    """
    import json as _json

    from app.ops.evidence_kit import EvidenceInputError
    from app.ops.legacy_papers import is_safe_artifact_path
    from app.ops.release_readiness import (
        format_readiness_summary,
        run_release_readiness,
    )

    if args.output and not is_safe_artifact_path(args.output):
        print(
            f"拒绝写入 {args.output}：manifest 只能写入 gitignore 的 artifacts/ 或 temp/ 目录"
        )
        return 2
    try:
        report = run_release_readiness(args.evidence_dir)
    except EvidenceInputError as cause:
        print(f"证据输入无效（目录或路径问题，未产生 manifest）: {cause}")
        return 2
    except OSError as cause:
        print(
            "证据读取失败（IO 问题，未产生 manifest）: "
            f"{type(cause).__name__}: {cause}"
        )
        return 2
    if args.output:
        try:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            _write_report_atomic(
                output_path,
                _json.dumps(report, ensure_ascii=False, indent=2),
            )
        except OSError as cause:
            # 目录无法创建/权限/磁盘满/replace 失败：manifest 未落盘或旧文件
            # 原样保留（原子写不产生 partial），不得再打印门结论摘要。
            print(
                f"报告写入失败（路径/权限/磁盘问题，未产生报告文件）: "
                f"{type(cause).__name__}: {cause}"
            )
            return 2
        print(f"报告已写入: {args.output}")
    if args.as_json:
        print(_json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_readiness_summary(report))
    return report["exit_code"]


def _run_evidence_cockpit(args) -> int:
    """python -m app.ops.cli evidence-cockpit --gate-source GATE=PATH
--current-head SHA --staging-dir DIR [--gate-declared-head GATE=SHA]
[--anchor-companion PATH] [--json] [--output PATH]

    M14-91 跨切片发布证据驾驶舱（只读聚合器 + 一次性 staging）：显式接受
    各切片 canonical 门证据文件（来源登记路径/字节/SHA-256，零改动），按
    evaluator 的 gate→文件名映射逐字节 stage 进一次性新目录（绝不覆盖既有
    内容），对 staging 复用 run_release_readiness 全量门语义，并按
    code-bound / production-state 分类 + 声明 commit（内嵌或旗标）与
    current HEAD 的比对标记 current/stale/undeclared。**永不接受、stage
    或生成 release-approval**（传入即 fail-closed）；production_ready 恒
    false。退出码：cockpit_ready=true=0（全部必需门（release-approval
    除外）staged 且 pass 且无 stale/undeclared-code-bound blocker——不是
    staged 子集干净）/ 有 blocker（staged 门非 pass、stale、code-bound
    undeclared、**required 门未 stage（not-staged-required）**；唯一例外
    release-approval 按策略永不接受不计 blocker，turn-tls 为 optional 门
    在本机/LAN 发布范围不阻断）=1 / 输入/路径/护栏问题=2（零 staging）。
    """
    import json as _json
    from pathlib import Path

    from app.ops.evidence_cockpit import (
        CockpitInputError,
        _collect_declared_heads,
        _collect_gate_sources,
        build_evidence_cockpit,
        format_cockpit_summary,
        write_cockpit_report,
    )
    from app.ops.legacy_papers import is_safe_artifact_path

    if args.output and not is_safe_artifact_path(args.output):
        print(
            f"拒绝写入 {args.output}：cockpit 报告只能写入 gitignore 的 "
            "artifacts/ 或 temp/ 目录"
        )
        return 2
    try:
        sources = _collect_gate_sources(list(args.gate_source))
        declared = _collect_declared_heads(
            list(args.gate_declared_head or []), sources)
        report = build_evidence_cockpit(
            sources,
            current_head=args.current_head,
            staging_dir=args.staging_dir,
            declared_heads=declared,
            anchor_companion=args.anchor_companion,
        )
        if args.output:
            try:
                write_cockpit_report(report, args.output)
            except (CockpitInputError, OSError) as cause:
                # 报告落盘失败：staging 虽已建成也不留半成品现场——移除
                # 本次新建目录后如实 exit 2（清理失败时异常自带残留说明）。
                from app.ops.evidence_cockpit import _remove_created_staging
                _remove_created_staging(
                    Path(report["staging"]["dir"]), cause)
                print(
                    f"cockpit 报告写入失败（已移除本次 staging）: "
                    f"{type(cause).__name__}: {cause}"
                )
                return 2
            print(f"报告已写入: {args.output}")
    except CockpitInputError as cause:
        print(f"cockpit 输入无效（未产生报告/零 staging）: {cause}")
        return 2
    except OSError as cause:
        print(
            "cockpit IO 失败（未产生报告）: "
            f"{type(cause).__name__}: {cause}"
        )
        return 2
    if args.as_json:
        print(_json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_cockpit_summary(report))
    return report["exit_code"]


def _run_cutover_rehearsal(args) -> int:
    """python -m app.ops.cli cutover-rehearsal --evidence-dir DIR
    [--json] [--output PATH]

    M10-15 生产切换演练编排器（只读、fail-closed、隔离 rehearsal）：按生产
    切换时间线组织 13 个 required steps（pre-window/pre-migration/
    post-migration/cutover），只读取调用方显式提供的本地 evidence 目录内的
    JSON/JSONL 证据文件，逐步给出四态 pass/pending/blocked/not_executed，
    顶层优先级 blocked > pending > not_executed > ready，并输出 blockers 与
    next_actions。不连接数据库、不调用 API、不访问网络、不读取环境变量；
    不执行生产迁移/锚定/清理/WORM/部署/启停/发布/回滚——命令没有 --yes
    执行形态。rehearsal_ready 不代表生产验收，也不授权生产写入/发布。
    --output 复用 artifacts/temp gitignore 路径护栏并原子落盘（symlink
    目标拒绝），写入失败 exit 2 且不打印步骤结论摘要。
    退出码：13 步全 pass=0 / 任一步非 pass=1 / 目录或路径与 IO 问题=2。
    """
    import json as _json

    from app.ops.cutover_rehearsal import (
        format_rehearsal_summary,
        run_cutover_rehearsal,
    )
    from app.ops.evidence_kit import EvidenceInputError
    from app.ops.legacy_papers import is_safe_artifact_path

    if args.output and not is_safe_artifact_path(args.output):
        print(
            f"拒绝写入 {args.output}：manifest 只能写入 gitignore 的 artifacts/ 或 temp/ 目录"
        )
        return 2
    try:
        report = run_cutover_rehearsal(args.evidence_dir)
    except EvidenceInputError as cause:
        print(f"证据输入无效（目录或路径问题，未产生 manifest）: {cause}")
        return 2
    except OSError as cause:
        print(
            "证据读取失败（IO 问题，未产生 manifest）: "
            f"{type(cause).__name__}: {cause}"
        )
        return 2
    if args.output:
        try:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            _write_report_atomic(
                output_path,
                _json.dumps(report, ensure_ascii=False, indent=2),
            )
        except OSError as cause:
            # 目录无法创建/权限/磁盘满/replace 失败：manifest 未落盘或旧文件
            # 原样保留（原子写不产生 partial），不得再打印步骤结论摘要。
            print(
                f"报告写入失败（路径/权限/磁盘问题，未产生报告文件）: "
                f"{type(cause).__name__}: {cause}"
            )
            return 2
        print(f"报告已写入: {args.output}")
    if args.as_json:
        print(_json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_rehearsal_summary(report))
    return report["exit_code"]


def _run_cutover_evidence_pack(args) -> int:
    """python -m app.ops.cli cutover-evidence-pack scaffold --target-dir DIR
    python -m app.ops.cli cutover-evidence-pack approval-draft
    --evidence-dir DIR [--output PATH]

    M10-16 生产切换证据包脚手架与操作手册（本地生成，纯脱敏模板与文档）：
    scaffold 在用户显式指定且通过 artifacts/temp gitignore 护栏的新目录内
    生成 M10-15 全部 13 步的证据模板（.template.json 命名，预填值全部
    REPLACE-ME 形态——改名直用只会 blocked）+ 锚文件副本模板 + 逐项操作
    手册 README（来源命令/脱敏要求/通过失败语义/人工授权/审批哈希计算/
    隔离 fixture 全链路）。不连接数据库、不调用 API、不访问网络、不读取
    环境变量；不执行任何生产迁移/锚定/清理/备份/部署/启停/发布/回滚——
    命令没有 --yes 执行形态，真实生产操作必须人工逐项授权。目标目录护栏
    fail-closed（exit 2）：任何已存在路径组件是 symlink、目标是 artifacts/
    temp 本身、越界路径一律拒绝；已存在且非空则必须与本工具脚手架字节
    一致（幂等重放，零改写）否则拒绝覆盖。approval-draft 只读计算当前
    证据目录各步与 supporting 文件的 SHA-256 底稿——输出仍是 DRAFT（缺
    step/必填审批字段，直接改名只会 blocked），必须人工逐项确认后由审批
    人自行组装 cutover-approval.json；--output 复用 artifacts/temp 护栏
    并原子落盘（symlink 目标拒绝），写入失败 exit 2。
    退出码：成功（含幂等重放）=0 / 目标目录或路径与 IO 问题=2。
    """
    import json as _json

    from app.ops.cutover_evidence_pack import (
        PackInputError,
        build_approval_draft,
        scaffold_pack,
    )
    from app.ops.evidence_kit import EvidenceInputError
    from app.ops.legacy_papers import is_safe_artifact_path

    if args.action == "scaffold":
        if not args.target_dir:
            print("scaffold 需要 --target-dir <artifacts/temp 内的新目录>")
            return 2
        try:
            report = scaffold_pack(args.target_dir)
        except PackInputError as cause:
            print(f"脚手架目标目录无效（未创建/未改写任何文件）: {cause}")
            return 2
        except OSError as cause:
            print(f"脚手架写入失败（IO 问题）: {type(cause).__name__}: {cause}")
            return 2
        if report["idempotent"]:
            print(
                f"脚手架已存在且内容一致（幂等，未改写任何文件）: "
                f"{report['target_dir']}"
            )
        else:
            print(
                f"脚手架已创建: {report['target_dir']}"
                f"（{len(report['files_written'])} 个文件）"
            )
        print(
            "下一步: 复制到隔离演练目录后填写模板（全部 REPLACE-ME），或按 "
            "README 手册逐项准备证据；模板与手册不代表生产验收。"
        )
        return 0

    # approval-draft
    if not args.evidence_dir:
        print("approval-draft 需要 --evidence-dir <本地证据目录>")
        return 2
    if args.output and not is_safe_artifact_path(args.output):
        print(
            f"拒绝写入 {args.output}：审批底稿只能写入 gitignore 的 artifacts/ 或 temp/ 目录"
        )
        return 2
    try:
        draft = build_approval_draft(args.evidence_dir)
    except EvidenceInputError as cause:
        print(f"证据输入无效（目录或路径问题，未产生底稿）: {cause}")
        return 2
    except OSError as cause:
        print(
            f"证据读取失败（IO 问题，未产生底稿）: {type(cause).__name__}: {cause}"
        )
        return 2
    text = _json.dumps(draft, ensure_ascii=False, indent=2)
    if args.output:
        try:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            _write_report_atomic(output_path, text + "\n")
        except OSError as cause:
            # 目录无法创建/权限/磁盘满/replace 失败：底稿未落盘或旧文件原样
            # 保留（原子写不产生 partial），不得再打印底稿正文（防被误读）。
            print(
                f"底稿写入失败（路径/权限/磁盘问题，未产生底稿文件）: "
                f"{type(cause).__name__}: {cause}"
            )
            return 2
        print(f"审批底稿已写入: {args.output}")
    print(text)
    print(
        "DRAFT：以上是哈希底稿，不是审批记录；人工逐项确认并填写全部 "
        "REPLACE-ME 字段后才能构成 cutover-approval.json。"
    )
    return 0


def _run_governance_evidence(args) -> int:
    """python -m app.ops.cli governance-evidence --report <报告JSON>
    [--batch <批次JSON> ...] --output <artifacts路径> [--json]

    M11-12 治理证据推导器：从 artifacts/temp 内的 legacy-paper-report /
    draft-owner-report 完整 JSON 与零或多个成功 migrate 批次 JSON 确定性
    推导 pending_count（报告明细 ID 集合 - 成功批次 eligible ID 并集，
    非转抄报告计数），原子生成 cutover-rehearsal / release-readiness 可
    直接消费且不含业务 ID/敏感值的 legacy-papers.json / draft-ownership.json。
    纯本地文件推导：不连数据库、不读环境变量、不访问网络、不执行任何
    迁移/治理/锚定/部署（无 --yes 执行形态）；报告与批次文件零写入。
    批次是条件必需（M14-67）：报告仍有待决策项时至少一个成功批次，
    否则 fail-closed 拒绝；报告已归零时允许零批次（pending_count=0）。

    护栏先于推导（exit 2、不写输出、输入字节不变）：报告/批次/输出都必须
    位于 gitignore 的 artifacts/temp 且为常规文件、任何已存在路径组件是
    symlink 即拒绝；**输出 resolved 路径不得等于报告或任何批次的 resolved
    路径**（normcase 归一比较，Windows 大小写/分隔符形态不构成绕过）；
    同一批次文件不得重复传入（含等价路径规范化后的重复——虚增成功批次
    数）；报告结构自洽（summary.total == 明细条数、ID 唯一）；每个批次
    必须是成功执行形态（executed=true 且 dry_run 显式 false 且
    exit_code=0 且无 failure/invalid 且 audit_action 与迁移路径/kind 精确
    匹配——dry-run（含 dry_run=true 而 executed=true 的畸形形态）、审计
    动作错配或任何失败批次 fail-closed 拒绝，绝不从不完整执行历史推导
    计数）；批次类型与报告类型匹配；输出文件名恰为对应步的精确证据文件名。
    输出契约最小化：batches 每项仅 {"executed": true}，成功批次数量只以
    聚合计数 batches_executed 表达——无逐批路径/逐批解决计数/逐批哈希。

    退出码：pending_count=0 => 0 / pending_count>0 => 1（证据照常原子落盘，
    如实记录治理未归零）/ 输入或路径或写入失败 => 2。
    """
    import json as _json

    from app.ops.governance_evidence import (
        GovernanceInputError,
        build_governance_evidence,
        format_governance_summary,
    )

    try:
        evidence, exit_code = build_governance_evidence(
            args.report, args.batch or [], args.output
        )
    except GovernanceInputError as cause:
        print(f"拒绝执行（输入或路径问题，未写输出）: {cause}")
        return 2
    try:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_report_atomic(
            output_path,
            _json.dumps(evidence, ensure_ascii=False, indent=2),
        )
    except OSError as cause:
        # 目录无法创建/权限不足/磁盘满/replace 失败：证据未落盘或旧文件原样
        # 保留（原子写不产生 partial），不得再打印推导结论（防被误读）。
        print(
            f"证据写入失败（路径/权限/磁盘问题，未产生证据文件）: "
            f"{type(cause).__name__}: {cause}"
        )
        return 2
    # --json 模式下提示走 stderr，stdout 保持纯 JSON（可管道给 jq）
    print(
        f"证据已写入: {args.output}",
        file=sys.stderr if args.as_json else sys.stdout,
    )
    if args.as_json:
        print(_json.dumps(evidence, ensure_ascii=False, indent=2))
    else:
        print(format_governance_summary(evidence))
    return exit_code


def _run_provider_smoke_export(args) -> int:
    """python -m app.ops.cli provider-smoke-export
    <search|cloud-voice|local-voice|llm> --output PATH [--json]

    M11-16 单 provider 冒烟证据导出器：以 bash 运行既有冒烟脚本
    （infra/smoke_search.sh / smoke_voice_cloud.sh / smoke_voice_local.sh /
    smoke_llm.sh，cwd=仓库根、相对 POSIX 路径，子进程整体继承当前环境与
    终端——脚本脱敏摘要直通运维终端，不捕获不保存），把真实执行结论导出
    为 cutover-rehearsal 对应步（search-smoke/cloud-voice-smoke/llm-smoke）
    可直接消费的脱敏原子证据；M14-70 起另有 local-voice-smoke 单步证据
    （provider-smoke-aggregate --voice-mode local 的语音聚合输入）。
    证据只含 tool/schema_version/step/executed/result/exit_code/起止时间与
    耗时——无 stdout/stderr、命令行、endpoint、模型名或任何摘要文本；本
    命令不读取任何敏感环境变量（冒烟所需 key/端点由运维在调用前显式注入；
    唯一环境访问是 shutil.which 经 PATH 解析 bash，子进程整体继承环境），
    不自动补跑任何冒烟、不改变冒烟脚本判定逻辑。

    护栏与退出码：provider 枚举与输出路径护栏（artifacts/temp、symlink
    拒绝、文件名恰为对应步证据文件名、已存在非常规文件拒绝）先于 runner
    （exit 2 不运行冒烟、不写证据、不创建输出/父目录）；bash 或脚本不可用
    exit 2；runner 退出码 0 => pass/exit 0，非零 => 失败证据照常原子落盘/
    exit 1（如实记录，不伪装 pass）；写入失败 exit 2、旧文件字节原样、无
    .tmp 残留且不打印结论。--json 时 stdout 纯 JSON、提示走 stderr。
    """
    import json as _json

    from app.ops.provider_smoke_evidence import (
        ProviderSmokeExecutionError,
        ProviderSmokeInputError,
        build_step_evidence,
        format_step_summary,
    )

    try:
        evidence, exit_code = build_step_evidence(args.provider, args.output)
    except ProviderSmokeInputError as cause:
        print(f"拒绝执行（输入或路径问题，未运行冒烟、未写证据）: {cause}")
        return 2
    except ProviderSmokeExecutionError as cause:
        print(f"冒烟编排失败（未产生证据）: {cause}")
        return 2
    try:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_report_atomic(
            output_path,
            _json.dumps(evidence, ensure_ascii=False, indent=2),
        )
    except OSError as cause:
        # 目录无法创建/权限不足/磁盘满/replace 失败：证据未落盘或旧文件原样
        # 保留（原子写不产生 partial），不得再打印冒烟结论（防被误读）。
        print(
            f"证据写入失败（路径/权限/磁盘问题，未产生证据文件）: "
            f"{type(cause).__name__}: {cause}"
        )
        return 2
    # --json 模式下提示走 stderr，stdout 保持纯 JSON（可管道给 jq）
    print(
        f"证据已写入: {args.output}",
        file=sys.stderr if args.as_json else sys.stdout,
    )
    if args.as_json:
        print(_json.dumps(evidence, ensure_ascii=False, indent=2))
    else:
        print(format_step_summary(evidence))
    return exit_code


def _run_provider_smoke_aggregate(args) -> int:
    """python -m app.ops.cli provider-smoke-aggregate --search PATH
    (--voice PATH | --cloud-voice PATH) --llm PATH --output PATH
    [--voice-mode {local,hybrid,cloud}] [--json]

    M11-16 provider 冒烟聚合证据导出器：把三份**本工具导出的**单步证据
    （provider-smoke-export 产物）确定性聚合为 release-readiness 的
    provider-smoke 门可直接消费的 provider-smoke.json——providers.voice/
    search/llm 每项仅 executed 与 result（M14-70 起另含 evidence_step），
    不透传单步 exit_code/时间/脚本细节。语音拓扑（M14-70 slice 4）：
    语音单步证据必须恰好提供一个——新形态 ``--voice``（local/hybrid/cloud
    拓扑通用）或兼容形态 ``--cloud-voice``（只承载 cloud/hybrid 拓扑的
    cloud-voice 证据；``--voice-mode local`` 时必须改用 ``--voice`` 传
    local-voice-smoke.json）；``--voice-mode`` 默认 cloud（兼容既有无旗标
    调用），local 换用 local-voice 槽位、hybrid/cloud 恒用 cloud-voice
    槽位——同给/都不给/local 模式误用 legacy 旗标一律 exit 2、不写输出。

    三份输入必须都位于 artifacts/temp 且通过本工具形态校验
    （exact schema：顶层键集合恰为九键白名单、tool/schema_version 精确
    匹配、step 与槽位精确匹配、executed=true、result 只能 pass/fail 且与
    exit_code 结论一致、起止时间 timezone-aware 且不倒置、duration_ms
    非 bool 非负 int）——手工拼装/错位/未执行/metadata 漂移形态
    fail-closed 拒绝；输出不得等于任何输入、同一输入不得重复传入；
    纯本地文件推导：不运行冒烟、不连数据库、不读取任何敏感环境变量、
    不访问网络。

    退出码：全 pass=0 / 任一 fail=1（聚合证据照常原子落盘，如实记录）/
    输入、路径或写入失败=2（不写输出、输入字节不变）。--json 时 stdout
    纯 JSON、提示走 stderr。
    """
    import json as _json

    from app.ops.provider_smoke_evidence import (
        ProviderSmokeInputError,
        build_provider_smoke_evidence,
        format_aggregate_summary,
    )

    # 语音拓扑旗标校验先于一切聚合输入处理（M14-70 slice 4）：恰好一个
    # 语音输入；--voice-mode local 的语音证据必须经 --voice 提供（legacy
    # --cloud-voice 只承载 cloud-voice 拓扑轨道）。违例 exit 2、不写输出。
    if args.voice and args.cloud_voice:
        print(
            "拒绝执行（输入或路径问题，未写输出）: --voice 与 --cloud-voice "
            "只能提供一个（语音证据槽位唯一，同给不得虚增拓扑覆盖面）"
        )
        return 2
    if not args.voice and not args.cloud_voice:
        print(
            "拒绝执行（输入或路径问题，未写输出）: 必须恰好提供一个语音单步"
            "证据路径（--voice 新形态或 --cloud-voice 兼容形态）"
        )
        return 2
    if args.voice_mode == "local" and not args.voice:
        print(
            "拒绝执行（输入或路径问题，未写输出）: --voice-mode local 的语音"
            "证据必须经 --voice 提供（--cloud-voice 只承载 cloud-voice 拓扑"
            "轨道）"
        )
        return 2
    # 拓扑选轨与核心函数槽位契约一致：local -> local-voice 槽位；hybrid/
    # cloud 把接受的语音输入（--voice 优先，否则 legacy --cloud-voice）
    # 映射到 cloud-voice 槽位。
    voice_slot = "local-voice" if args.voice_mode == "local" else "cloud-voice"
    try:
        evidence, exit_code = build_provider_smoke_evidence(
            {
                voice_slot: args.voice or args.cloud_voice,
                "search": args.search,
                "llm": args.llm,
            },
            args.output,
            voice_mode=args.voice_mode,
        )
    except ProviderSmokeInputError as cause:
        print(f"拒绝执行（输入或路径问题，未写输出）: {cause}")
        return 2
    try:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_report_atomic(
            output_path,
            _json.dumps(evidence, ensure_ascii=False, indent=2),
        )
    except OSError as cause:
        # 同上：写入失败旧文件原样保留、无 partial，不打印聚合结论。
        print(
            f"证据写入失败（路径/权限/磁盘问题，未产生证据文件）: "
            f"{type(cause).__name__}: {cause}"
        )
        return 2
    print(
        f"证据已写入: {args.output}",
        file=sys.stderr if args.as_json else sys.stdout,
    )
    if args.as_json:
        print(_json.dumps(evidence, ensure_ascii=False, indent=2))
    else:
        print(format_aggregate_summary(evidence))
    return exit_code


def _run_production_evidence_gap(args) -> int:
    """python -m app.ops.cli production-evidence-gap --evidence-dir DIR
    [--output <artifacts/temp路径>] [--json]

    M11-18/M14-74 生产证据缺口 manifest（只读聚合器、fail-closed）：只消费
    cutover-rehearsal（M10-15）与 release-readiness（M10-11）对本地
    evidence 目录的只读评估结果，把四类生产前置证据缺口（governance：
    legacy-papers/draft-ownership；audit-chain：audit-chain-verify/anchor；
    provider-smoke：voice/search/llm 三槽位，M14-74 起状态 defer 到
    release-readiness 的 provider-smoke 门——provider-smoke.json 聚合按
    topology.voice_mode 拓扑选轨，本地拓扑语音证据经聚合门闭合；
    cutover-approval）聚合为逐类缺口清单（状态/缺口/已覆盖步骤/既有工具/
    缺失证据/运维动作/agent 可安全动作/授权边界）。状态聚合诚实优先：类别
    内全 pass 才 pass，blocked > pending > not_executed（provider-smoke
    类别映射权威门六态：missing -> not_executed，malformed/tampered ->
    blocked）；production_ready 恒为
    false——缺口清单不构成生产放行。不连接数据库、不调用 API、不访问网络、
    不读取任何环境变量；不执行任何迁移/治理/锚定/备份/部署/启停/发布/回滚、
    不运行任何 provider 冒烟——命令没有 --yes 执行形态。--output 必须位于
    gitignore 的 artifacts/temp（symlink 组件拒绝）且不得位于证据目录内
    （拒绝覆盖证据输入），护栏先于任何证据读取；原子落盘，写入失败 exit 2
    且不打印缺口结论摘要。
    退出码：四类全 pass=0 / 任一类非 pass=1 / 目录或路径与 IO 问题=2。
    """
    import json as _json

    from app.ops.evidence_kit import EvidenceInputError
    from app.ops.legacy_papers import is_safe_artifact_path
    from app.ops.production_evidence_gap import (
        ProductionGapInputError,
        build_production_evidence_gap,
        format_gap_summary,
    )

    if args.output and not is_safe_artifact_path(args.output):
        print(
            f"拒绝写入 {args.output}：缺口清单只能写入 gitignore 的 artifacts/ 或 temp/ 目录"
        )
        return 2
    try:
        report, exit_code = build_production_evidence_gap(
            args.evidence_dir, args.output
        )
    except (ProductionGapInputError, EvidenceInputError) as cause:
        print(f"证据输入无效（目录或路径问题，未产生 manifest）: {cause}")
        return 2
    except OSError as cause:
        print(
            "证据读取失败（IO 问题，未产生 manifest）: "
            f"{type(cause).__name__}: {cause}"
        )
        return 2
    if args.output:
        try:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            _write_report_atomic(
                output_path,
                _json.dumps(report, ensure_ascii=False, indent=2),
            )
        except OSError as cause:
            # 目录无法创建/权限/磁盘满/replace 失败：manifest 未落盘或旧文件
            # 原样保留（原子写不产生 partial），不得再打印缺口结论摘要。
            print(
                f"报告写入失败（路径/权限/磁盘问题，未产生报告文件）: "
                f"{type(cause).__name__}: {cause}"
            )
            return 2
        print(
            f"报告已写入: {args.output}",
            file=sys.stderr if args.as_json else sys.stdout,
        )
    if args.as_json:
        print(_json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_gap_summary(report))
    return exit_code


def _run_release_closure_manifest(args) -> int:
    """python -m app.ops.cli release-closure-manifest --evidence-dir DIR
    [--git-head SHA] [--output-json <artifacts/temp路径>]
    [--output-md <artifacts/temp路径>] [--json]

    M14-68 生产收口 manifest（只读聚合器、fail-closed）：把「切换窗口前的
    收口状态」收敛为一份确定性 JSON / Markdown closure manifest——git HEAD
    （显式 --git-head 优先，缺省固定 argv/cwd/超时的安全发现）、证据目录
    逐文件有界清单（相对 posix 名/字节/SHA-256，超上限 fail-closed）、
    release-readiness 与 production-evidence-gap 两个既有聚合器的结论子集
    （只消费既有评估结果，不重复实现任何 gate 语义）、诚实合取的
    production_ready（readiness.release_ready 且 gap overall=pass 才 true，
    否则恒 false——本清单不创建审批文件、不替代人工审批、不授权任何生产
    操作）、blockers 与六条占位符形态下一步命令。不连接数据库、不调用
    API、不访问网络、不读取任何环境变量；对证据目录零写入；命令没有
    --yes 执行形态。--output-json/--output-md 必须位于 gitignore 的
    artifacts/temp（任何已存在 symlink 组件拒绝、已存在非常规文件拒绝），
    不得位于证据目录内、两输出不得同路径；护栏先于任何证据读取；逐文件
    原子落盘（JSON 与 Markdown 跨文件非事务），写入失败 exit 2 且不打印
    收口结论。
    退出码：production_ready=true=0 / 聚合未全 pass=1 / 输入或路径与 IO
    问题=2。
    """
    import json as _json

    from app.ops.evidence_kit import EvidenceInputError
    from app.ops.legacy_papers import is_safe_artifact_path
    from app.ops.release_closure_manifest import (
        ClosureManifestInputError,
        build_release_closure_manifest,
        format_closure_markdown,
    )

    for label, output in (
        ("--output-json", args.output_json),
        ("--output-md", args.output_md),
    ):
        if output and not is_safe_artifact_path(output):
            print(
                f"拒绝写入 {output}：收口清单只能写入 gitignore 的 "
                f"artifacts/ 或 temp/ 目录（{label}）"
            )
            return 2
    try:
        report, exit_code = build_release_closure_manifest(
            args.evidence_dir,
            args.output_json,
            args.output_md,
            args.git_head,
        )
    except (ClosureManifestInputError, EvidenceInputError) as cause:
        print(f"证据输入无效（目录或路径问题，未产生 manifest）: {cause}")
        return 2
    except OSError as cause:
        print(
            "证据读取失败（IO 问题，未产生 manifest）: "
            f"{type(cause).__name__}: {cause}"
        )
        return 2
    if args.output_json or args.output_md:
        try:
            for output, text in (
                (
                    args.output_json,
                    _json.dumps(report, ensure_ascii=False, indent=2),
                ),
                (args.output_md, format_closure_markdown(report)),
            ):
                if not output:
                    continue
                output_path = Path(output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                _write_report_atomic(output_path, text)
        except OSError as cause:
            # 目录无法创建/权限/磁盘满/replace 失败：清单未落盘或旧文件
            # 原样保留（原子写不产生 partial），不得再打印收口结论。
            print(
                f"报告写入失败（路径/权限/磁盘问题，未产生报告文件）: "
                f"{type(cause).__name__}: {cause}"
            )
            return 2
        written = [
            str(output)
            for output in (args.output_json, args.output_md)
            if output
        ]
        print(
            "报告已写入: " + ", ".join(written),
            file=sys.stderr if args.as_json else sys.stdout,
        )
    if args.as_json:
        print(_json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_closure_markdown(report))
    return exit_code


def _run_evidence_inventory(args) -> int:
    """python -m app.ops.cli evidence-inventory --evidence-dir DIR
    [--evidence-dir DIR ...] [--output <artifacts/temp路径>] [--json]

    M11-20 生产证据目录只读索引器：对一或多个本地证据目录做 inventory——
    逐文件列出 name/相对路径/size/SHA-256/mtime/分类/适用性（模板
    not_applicable、README 声明隔离 fixture 的目录非模板条目
    isolation_fixture、其余一律 unverified——绝无 production_verified），
    对已知证据文件名做极小白名单 JSON 提取（database.json 这类大文件只哈希
    元数据），按目录与总体汇总 13 步文件名覆盖/缺失、重复 sha256、可解析
    计数。coverage 只看 root 顶层是否存在精确证据文件名（嵌套同名文件列入
    inventory 但不推进覆盖），不是 cutover-rehearsal 的放行判定。
    纯本地只读：不连接数据库、不调用 API、不访问网络、不读取任何环境
    变量、不运行任何 provider、不执行任何生产操作——命令没有 --yes 执行
    形态；输出不使用绝对路径、不回显文件正文或解析错误文本；文件显示名
    脱敏——只有 root 顶层的已知精确安全文件名白名单成员原样显示，未知
    文件名与任何嵌套路径一律 [redacted]（重复 sha256 组 paths 同口径）。

    护栏先于任何目录枚举（exit 2、不写输出）：每个 --evidence-dir 必须
    已存在、为真目录、非 symlink，递归枚举遇到任何 symlink 文件/目录
    （含 dangling）一律 fail-closed 拒绝、绝不跟随，目录枚举本身失败
    （不可读/访问被拒绝的子目录，os.walk onerror）同样 fail-closed、
    绝不输出不完整清单；同一目录不得重复传入、
    目录间不得互相嵌套；--output 必须位于 gitignore 的 artifacts/temp
    （symlink 组件拒绝）且不得位于任一证据目录内或等于任一目录根；原子
    落盘，写入失败 exit 2、旧文件字节原样、无 .tmp 残留且不打印盘点结论。
    --json 时 stdout 纯 JSON、提示走 stderr。
    退出码：成功盘点=0 / 输入或路径与 IO 问题=2（inventory 没有失败语义，
    缺什么是清单内容，不是命令失败）。
    """
    import json as _json

    from app.ops.evidence_inventory import (
        EvidenceInventoryInputError,
        build_evidence_inventory,
        format_inventory_summary,
    )

    try:
        report = build_evidence_inventory(args.evidence_dir or [], args.output)
    except EvidenceInventoryInputError as cause:
        print(f"拒绝执行（输入或路径问题，未产生清单）: {cause}")
        return 2
    except OSError as cause:
        print(
            f"证据读取失败（IO 问题，未产生清单）: {type(cause).__name__}: {cause}"
        )
        return 2
    if args.output:
        try:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            _write_report_atomic(
                output_path,
                _json.dumps(report, ensure_ascii=False, indent=2),
            )
        except OSError as cause:
            # 目录无法创建/权限/磁盘满/replace 失败：清单未落盘或旧文件原样
            # 保留（原子写不产生 partial），不得再打印盘点结论摘要。
            print(
                f"报告写入失败（路径/权限/磁盘问题，未产生报告文件）: "
                f"{type(cause).__name__}: {cause}"
            )
            return 2
        print(
            f"报告已写入: {args.output}",
            file=sys.stderr if args.as_json else sys.stdout,
        )
    if args.as_json:
        print(_json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_inventory_summary(report))
    return report["exit_code"]


def _run_release_candidate(args) -> int:
    """python -m app.ops.cli release-candidate manifest --output-dir DIR ...
    python -m app.ops.cli release-candidate verify --package-dir DIR
    [--version-file PATH]

    M10-17 本地 Release Candidate 包 manifest 助手（编排由
    infra/build_release_candidate.sh 完成）：manifest 子命令在护栏内
    （gitignore 的 artifacts/temp）空目录原子写入 release-manifest.json 与
    SHA256SUMS（含两个镜像归档与 manifest 自身的哈希；失败清理，不留看似
    有效的半成品）；verify 子命令独立校验 schema/必填字段/version-tag
    一致性/校验和覆盖面/逐档哈希——只重算文件哈希，绝不 docker load。
    不读环境变量、不连 DB/网络、不执行任何命令；产出是本地 RC，不是
    production readiness 声明，不推仓库、不打 git tag、不发 GitHub Release。
    退出码：成功=0 / verify 校验失败=1 / 参数或路径与 IO 问题=2。
    """
    import json as _json

    from app.ops.release_candidate import (
        ReleaseCandidateError,
        verify_release_package,
        write_release_package,
    )

    if args.action == "manifest":
        missing = [
            name
            for name in (
                "output_dir",
                "tag",
                "version_file",
                "git_commit",
                "compose_file",
                "api_image_id",
                "web_image_id",
                "api_archive",
                "web_archive",
            )
            if not getattr(args, name)
        ]
        if missing:
            print("manifest 缺少必填参数: " + ", ".join(f"--{m.replace('_', '-')}" for m in missing))
            return 2
        try:
            report = write_release_package(
                args.output_dir,
                tag=args.tag,
                version_file=args.version_file,
                git_commit=args.git_commit,
                compose_file=args.compose_file,
                api_image_id=args.api_image_id,
                web_image_id=args.web_image_id,
                api_archive=args.api_archive,
                web_archive=args.web_archive,
                build_context=args.build_context,
                web_build_arg=args.web_build_arg,
                smoke_script=args.smoke_script,
                generated_at=args.generated_at,
            )
        except ReleaseCandidateError as cause:
            print(f"Release Candidate 包输入无效（未写入/未改写任何文件）: {cause}")
            return 2
        except OSError as cause:
            print(f"包写入失败（IO 问题，已清理半成品）: {type(cause).__name__}: {cause}")
            return 2
        print(_json.dumps(report, ensure_ascii=False, indent=2))
        print(
            "边界：以上是本地 Release Candidate（local build + local verify），"
            "不是 production readiness 声明；不推镜像仓库、不打 git tag、"
            "不发 GitHub Release。"
        )
        return 0

    # verify
    if not args.package_dir:
        print("verify 需要 --package-dir <RC 包目录>")
        return 2
    try:
        report = verify_release_package(
            args.package_dir, version_file=args.version_file
        )
    except ReleaseCandidateError as cause:
        print(f"包目录输入无效: {cause}")
        return 2
    except OSError as cause:
        print(f"包读取失败（IO 问题）: {type(cause).__name__}: {cause}")
        return 2
    print(_json.dumps(report, ensure_ascii=False, indent=2))
    if report["ok"]:
        print("VERIFY OK：manifest/校验和/逐档哈希全部一致（未加载 Docker 镜像）。")
        return 0
    print(f"VERIFY FAILED：{len(report['problems'])} 个问题（见 problems 列表）。")
    return 1


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
    p_br = sub.add_parser(
        "backup-restore-evidence",
        help=(
            "备份恢复演练证据导出（M11-04；隔离库迁移 + 恢复 + 逻辑全等校验，"
            "原子写 backup-restore.json；verified=0 / verified=false=1 / "
            "输入或执行错误=2）"
        ),
    )
    p_br.add_argument(
        "--backup-dir",
        required=True,
        help=(
            "既有备份目录（必须位于 gitignore 的 artifacts/temp 内，且已含 "
            "manifest.json/database.json；演练只读该目录，字节保持不变）"
        ),
    )
    p_br.add_argument(
        "--restore-db-url",
        required=True,
        help=(
            "隔离恢复库 URL（只允许 ai_learning_os_test / ai_learning_os_drill "
            "及其下划线前缀变体；主库/维护库/缺库名/非 PG 连接前拒绝；"
            "演练会升级并覆盖恢复该隔离库）"
        ),
    )
    p_br.add_argument(
        "--output",
        required=True,
        help=(
            "证据输出路径（必须位于 gitignore 的 artifacts/temp 目录，且不得"
            "位于备份目录内或等于备份目录；原子落盘：临时文件 + rename，失败"
            "保留旧报告、symlink 拒绝）"
        ),
    )
    p_br.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="stdout 输出纯 JSON 证据（契约兼容 release-readiness/cutover-rehearsal）",
    )
    p_l = sub.add_parser("license-report", help="依赖/模型/内容源/派生对象授权清单")
    p_l.add_argument("--db-url", default=None)
    p_l.add_argument("--requirements", default=None)
    p_rc = sub.add_parser(
        "release-check", help="发布门禁汇总（lint/typecheck/test/build/migration/backup + live 项）"
    )
    p_rc.add_argument("--api-base", default="http://127.0.0.1:8000")
    p_rc.add_argument("--db-url", default=None)
    p_rc.add_argument("--local-only", action="store_true", help="跳过 live 项（不依赖运行中服务）")
    p_rc.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help=(
            "输出机器可读 JSON 证据（契约兼容 release-readiness/"
            "cutover-rehearsal；local-only 下 live 项如实 not_executed、"
            "all_green=false，不冒充全绿）"
        ),
    )
    p_rc.add_argument(
        "--output",
        default=None,
        help=(
            "写 JSON 证据到文件（必须位于 gitignore 的 artifacts/temp 目录；"
            "原子落盘：临时文件 + rename，失败保留旧报告、symlink 拒绝；"
            "可与 --json 同用）"
        ),
    )
    p_ri = sub.add_parser(
        "release-check-isolated",
        help=(
            "隔离本地 full release-check 一键编排（M11-10；一次性 SQLite + "
            "回环临时 API + 10 项门禁 + JSON 证据；全绿=0 / 门禁 fail=1 / "
            "编排失败=2；all_green 仅隔离本地证据，非生产授权）"
        ),
    )
    p_ri.add_argument(
        "--workdir",
        default=None,
        help=(
            "隔离工作区目录（必须位于 gitignore 的 artifacts/ 或 temp/；存放"
            "一次性 SQLite、uvicorn 日志与证据；缺省自动生成唯一 run 目录；"
            "目录内既有同名数据库会被拒绝以保护证据）"
        ),
    )
    p_ri.add_argument(
        "--output",
        default=None,
        help=(
            "证据 JSON 输出路径（必须位于 gitignore 的 artifacts/ 或 temp/；"
            "缺省 <工作区>/release-check-isolated.json；原子落盘，失败保留"
            "旧报告、symlink 拒绝）"
        ),
    )
    p_ri.add_argument(
        "--health-timeout",
        type=float,
        default=60.0,
        help="等待临时 API /health 就绪的限时秒数（默认 60）",
    )
    p_ri.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="stdout 输出纯 JSON 证据（人读提示走 stderr；编排失败无证据时不启用）",
    )
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
        "--output",
        default=None,
        help=(
            "批次报告 JSON 原子落盘路径（必须位于 gitignore 的 artifacts/temp "
            "目录，不得与 --ids-file/--export 同一文件；stdout 仍输出同一 JSON，"
            "提示走 stderr）"
        ),
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
        "--output",
        default=None,
        help=(
            "批次报告 JSON 原子落盘路径（必须位于 gitignore 的 artifacts/temp "
            "目录，不得与 --ids-file 同一文件；stdout 仍输出同一 JSON，提示走 stderr）"
        ),
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
        help=(
            "写 JSON 报告到文件（必须位于 gitignore 的 artifacts/temp 目录；"
            "原子落盘：临时文件 + rename，失败保留旧报告、symlink 拒绝；默认不落盘）"
        ),
    )
    p_rr = sub.add_parser(
        "release-readiness",
        help=(
            "发布准备与人工审批证据 manifest（M10-11；只读本地证据目录，"
            "不连 DB/网络、不读密钥、不执行任何生产操作，无 --yes 形态）"
        ),
    )
    p_rr.add_argument(
        "--evidence-dir",
        required=True,
        help="本地证据目录（每门一个 JSON 证据文件 + 可选 audit-anchor.jsonl 副本）",
    )
    p_rr.add_argument(
        "--json", dest="as_json", action="store_true", help="输出完整 JSON manifest"
    )
    p_rr.add_argument(
        "--output",
        default=None,
        help=(
            "写 JSON manifest 到文件（必须位于 gitignore 的 artifacts/temp 目录；"
            "原子落盘：临时文件 + rename，失败保留旧报告、symlink 拒绝；默认不落盘）"
        ),
    )
    p_ec = sub.add_parser(
        "evidence-cockpit",
        help=(
            "跨切片发布证据驾驶舱（M14-91；显式 canonical 证据来源登记 + "
            "字节一致一次性 staging + release-readiness 复跑 + code-bound/"
            "production-state 分类与 stale 标记；永不接受 release-approval，"
            "production_ready 恒 false，无 --yes 形态）"
        ),
    )
    p_ec.add_argument(
        "--gate-source",
        action="append",
        required=True,
        metavar="GATE=PATH",
        help="显式 canonical 门证据文件（可重复；GATE 为发布门 id，"
             "release-approval 传入即拒绝）",
    )
    p_ec.add_argument(
        "--current-head",
        required=True,
        metavar="SHA",
        help="当前 HEAD（40 位十六进制；stale 判定比较基准）",
    )
    p_ec.add_argument(
        "--staging-dir",
        required=True,
        metavar="DIR",
        help="一次性 staging 目录（必须不存在，工具创建；绝不覆盖既有内容）",
    )
    p_ec.add_argument(
        "--gate-declared-head",
        action="append",
        default=None,
        metavar="GATE=SHA",
        help="显式声明某门证据执行/绑定的树（可重复；与内嵌声明冲突即拒绝）",
    )
    p_ec.add_argument(
        "--anchor-companion",
        default=None,
        metavar="PATH",
        help="audit-anchor.jsonl 伴生锚文件副本（同规则字节一致 stage）",
    )
    p_ec.add_argument(
        "--json", dest="as_json", action="store_true",
        help="输出完整 JSON cockpit 报告",
    )
    p_ec.add_argument(
        "--output",
        default=None,
        help="写 JSON 报告到文件（必须位于 gitignore 的 artifacts/temp 目录；"
             "原子落盘，目标已存在即拒绝覆盖）",
    )
    p_cr = sub.add_parser(
        "cutover-rehearsal",
        help=(
            "生产切换演练编排器（M10-15；按切换时间线组织 13 个 required "
            "steps，只读本地证据目录，不连 DB/网络、不读密钥、不执行任何"
            "生产操作，无 --yes 形态；隔离 rehearsal，不代表生产验收）"
        ),
    )
    p_cr.add_argument(
        "--evidence-dir",
        required=True,
        help="本地证据目录（每步一个 JSON 证据文件 + 可选 audit-anchor.jsonl 副本）",
    )
    p_cr.add_argument(
        "--json", dest="as_json", action="store_true", help="输出完整 JSON manifest"
    )
    p_cr.add_argument(
        "--output",
        default=None,
        help=(
            "写 JSON manifest 到文件（必须位于 gitignore 的 artifacts/temp 目录；"
            "原子落盘：临时文件 + rename，失败保留旧报告、symlink 拒绝；默认不落盘）"
        ),
    )
    p_ge = sub.add_parser(
        "governance-evidence",
        help=(
            "治理证据推导器（M11-12；报告 + 成功批次 -> pending_count 推导 + "
            "脱敏证据落盘 legacy-papers.json / draft-ownership.json；纯本地文件"
            "推导，不连 DB/网络、不执行迁移；归零=0 / 未归零=1 / 输入或路径"
            "问题=2）"
        ),
    )
    p_ge.add_argument(
        "--report",
        required=True,
        help=(
            "治理报告 JSON 路径（legacy-paper-report 或 draft-owner-report 的"
            "完整 --output/--json 输出；必须位于 gitignore 的 artifacts/temp，"
            "含生产 ID——本工具只从中推导计数，明细不进证据）"
        ),
    )
    p_ge.add_argument(
        "--batch",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "成功 migrate 批次 JSON 路径（legacy-paper-migrate / "
            "draft-owner-migrate 的 plan 输出；可重复提供多批；必须位于 "
            "gitignore 的 artifacts/temp；dry-run 或任何失败批次 fail-closed "
            "拒绝推导）。报告仍有待决策项时必需；报告已归零时可省略"
            "（零批次 + 零待决策 => pending_count=0，M14-67）"
        ),
    )
    p_ge.add_argument(
        "--output",
        required=True,
        help=(
            "证据输出路径（必须位于 gitignore 的 artifacts/temp，文件名恰为 "
            "legacy-papers.json 或 draft-ownership.json 且与报告类型对应；"
            "原子落盘：临时文件 + rename，失败保留旧文件、symlink 拒绝）"
        ),
    )
    p_ge.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="stdout 输出纯 JSON 证据（提示走 stderr；契约兼容 cutover-rehearsal）",
    )
    p_ps = sub.add_parser(
        "provider-smoke-export",
        help=(
            "单 provider 冒烟证据导出（M11-16；以 bash 运行既有冒烟脚本并"
            "导出脱敏原子单步证据，不改变冒烟判定逻辑；M14-70 起含 local-voice"
            " 本地语音拓扑轨道；pass=0 / fail=1（证据照常落盘）/ 护栏或编排"
            "问题=2）"
        ),
    )
    p_ps.add_argument(
        "provider",
        choices=sorted(("search", "cloud-voice", "local-voice", "llm")),
        help=(
            "provider 槽位：search / cloud-voice / local-voice / llm"
            "（各自对应既有冒烟脚本，与 PROVIDERS 注册一致）"
        ),
    )
    p_ps.add_argument(
        "--output",
        required=True,
        help=(
            "单步证据输出路径（必须位于 gitignore 的 artifacts/temp，文件名"
            "恰为 search-smoke.json / cloud-voice-smoke.json / "
            "local-voice-smoke.json / llm-smoke.json；原子落盘：临时文件 + "
            "rename，失败保留旧文件、symlink 拒绝；冒烟所需的 key/端点由运维"
            "在调用前显式注入环境，本命令不读取任何环境变量、不自动补跑）"
        ),
    )
    p_ps.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="stdout 输出纯 JSON 证据（提示走 stderr；契约兼容 cutover-rehearsal）",
    )
    p_pa = sub.add_parser(
        "provider-smoke-aggregate",
        help=(
            "provider 冒烟聚合证据导出（M11-16；三份本工具导出的单步证据 -> "
            "provider-smoke.json，providers 每项仅 executed/result；M14-70 起"
            "语音拓扑可选 local/hybrid/cloud（--voice-mode）；全 pass=0 / "
            "任一 fail=1（证据照常落盘）/ 输入或路径问题=2）"
        ),
    )
    p_pa.add_argument(
        "--search",
        required=True,
        metavar="PATH",
        help="search 单步证据路径（provider-smoke-export 产物 search-smoke.json）",
    )
    p_pa.add_argument(
        "--voice",
        default=None,
        metavar="PATH",
        help=(
            "语音单步证据路径（M14-70 新形态：--voice-mode local 传"
            " local-voice-smoke.json，cloud/hybrid 传 cloud-voice-smoke.json；"
            "与 --cloud-voice 互斥、必居其一）"
        ),
    )
    p_pa.add_argument(
        "--cloud-voice",
        default=None,
        metavar="PATH",
        help=(
            "cloud-voice 单步证据路径（兼容形态：provider-smoke-export 产物"
            " cloud-voice-smoke.json，只承载 cloud/hybrid 拓扑——"
            "--voice-mode local 时必须改用 --voice；与 --voice 互斥、必居其一）"
        ),
    )
    p_pa.add_argument(
        "--llm",
        required=True,
        metavar="PATH",
        help="llm 单步证据路径（provider-smoke-export 产物 llm-smoke.json）",
    )
    p_pa.add_argument(
        "--voice-mode",
        default="cloud",
        choices=("local", "hybrid", "cloud"),
        help=(
            "语音拓扑（M14-70）：local=聚合消费 local-voice 单步证据（须配"
            " --voice）；cloud/hybrid=聚合消费 cloud-voice 单步证据（hybrid "
            "的本地轨道由单步导出独立承载）；默认 cloud（兼容既有无旗标调用）"
        ),
    )
    p_pa.add_argument(
        "--output",
        required=True,
        help=(
            "聚合证据输出路径（必须位于 gitignore 的 artifacts/temp，文件名"
            "恰为 provider-smoke.json；原子落盘：临时文件 + rename，失败保留"
            "旧文件、symlink 拒绝）"
        ),
    )
    p_pa.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="stdout 输出纯 JSON 证据（提示走 stderr；契约兼容 release-readiness）",
    )
    p_pg = sub.add_parser(
        "production-evidence-gap",
        help=(
            "生产证据缺口清单（M11-18；只读聚合 cutover-rehearsal 评估结果为"
            "治理/审计链/provider 冒烟/审批四类缺口，production_ready 恒 false；"
            "不连 DB/网络、不读密钥、不执行任何生产操作，无 --yes 形态）"
        ),
    )
    p_pg.add_argument(
        "--evidence-dir",
        required=True,
        help=(
            "本地证据目录（复用 cutover-rehearsal 目录护栏与证据校验，"
            "本工具不重复实现 schema 校验）"
        ),
    )
    p_pg.add_argument(
        "--output",
        default=None,
        help=(
            "写 JSON manifest 到文件（必须位于 gitignore 的 artifacts/ 或 temp/ "
            "目录，且不得位于证据目录内；原子落盘：临时文件 + rename，失败保留"
            "旧报告、symlink 拒绝；默认不落盘）"
        ),
    )
    p_pg.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="stdout 输出纯 JSON manifest（提示走 stderr）",
    )
    p_cm = sub.add_parser(
        "release-closure-manifest",
        help=(
            "生产收口 manifest（M14-68；只读聚合 release-readiness 与 "
            "production-evidence-gap 为确定性 JSON/Markdown 收口清单：git "
            "HEAD+证据哈希清单+诚实合取 production_ready+blockers+下一步"
            "占位命令；不连 DB/网络、不读密钥、不执行任何生产操作，"
            "无 --yes 形态）"
        ),
    )
    p_cm.add_argument(
        "--evidence-dir",
        required=True,
        help=(
            "本地证据目录（复用 readiness/gap 既有目录护栏与证据校验，"
            "本工具不重复实现任何 gate 语义）"
        ),
    )
    p_cm.add_argument(
        "--git-head",
        default=None,
        help=(
            "显式提供 git HEAD commit SHA（40/64 位十六进制；缺省在仓库根"
            "固定 argv、无 shell、10s 超时安全发现 git rev-parse HEAD）"
        ),
    )
    p_cm.add_argument(
        "--output-json",
        default=None,
        help=(
            "写 JSON manifest 到文件（必须位于 gitignore 的 artifacts/ 或 "
            "temp/ 目录，且不得位于证据目录内；原子落盘：临时文件 + "
            "rename，失败保留旧报告、symlink 拒绝；默认不落盘）"
        ),
    )
    p_cm.add_argument(
        "--output-md",
        default=None,
        help=(
            "写 Markdown manifest 到文件（护栏同 --output-json；与 "
            "--output-json 不得指向同一路径；逐文件原子落盘，两文件跨"
            "文件非事务）"
        ),
    )
    p_cm.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="stdout 输出纯 JSON manifest（提示走 stderr；默认输出 Markdown）",
    )
    p_ei = sub.add_parser(
        "evidence-inventory",
        help=(
            "生产证据目录只读索引器（M11-20；一或多个目录的文件级 inventory："
            "分类/适用性/SHA-256/mtime/白名单元数据 + 13 步覆盖与重复 sha256 "
            "汇总；不连 DB/网络、不读密钥、不执行任何生产操作，无 --yes 形态；"
            "适用性绝无 production_verified）"
        ),
    )
    p_ei.add_argument(
        "--evidence-dir",
        action="append",
        required=True,
        metavar="DIR",
        help=(
            "本地证据目录（可重复提供多个；必须已存在、为真目录、非 symlink，"
            "递归枚举遇到任何 symlink 文件/目录一律拒绝；同一目录不得重复"
            "传入、目录间不得互相嵌套）"
        ),
    )
    p_ei.add_argument(
        "--output",
        default=None,
        help=(
            "写 JSON manifest 到文件（必须位于 gitignore 的 artifacts/ 或 temp/ "
            "目录，且不得位于任一证据目录内或等于任一目录根；原子落盘：临时"
            "文件 + rename，失败保留旧报告、symlink 拒绝；默认不落盘）"
        ),
    )
    p_ei.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="stdout 输出纯 JSON manifest（提示走 stderr）",
    )
    p_ep = sub.add_parser(
        "cutover-evidence-pack",
        help=(
            "生产切换证据包脚手架与操作手册（M10-16；13 步脱敏模板 + 手册 "
            "README + 审批 DRAFT 哈希底稿，不连 DB/网络、不读密钥、不执行"
            "任何生产操作，无 --yes 形态；模板不代表生产验收）"
        ),
    )
    p_ep.add_argument(
        "action",
        choices=["scaffold", "approval-draft"],
        help=(
            "scaffold：在 artifacts/temp 内的新目录生成模板与手册；"
            "approval-draft：只读计算证据目录的审批哈希底稿（仍是 DRAFT）"
        ),
    )
    p_ep.add_argument(
        "--target-dir",
        default=None,
        help=(
            "scaffold 目标目录（必须位于 gitignore 的 artifacts/ 或 temp/ 内、"
            "不是 artifacts/temp 本身；不存在或为空，同内容重复执行幂等，"
            "非空且不一致拒绝覆盖）"
        ),
    )
    p_ep.add_argument(
        "--evidence-dir",
        default=None,
        help="approval-draft：本地证据目录（只读，产出 DRAFT 哈希底稿）",
    )
    p_ep.add_argument(
        "--output",
        default=None,
        help=(
            "approval-draft：写底稿到文件（必须位于 gitignore 的 artifacts/ 或 "
            "temp/ 目录；原子落盘：临时文件 + rename，失败保留旧文件、symlink "
            "拒绝；默认只打印）"
        ),
    )
    p_rc = sub.add_parser(
        "release-candidate",
        help=(
            "本地 Release Candidate 包 manifest 助手（M10-17；manifest 原子写 "
            "release-manifest.json + SHA256SUMS，verify 独立校验不加载镜像；"
            "编排入口是 infra/build_release_candidate.sh）"
        ),
    )
    p_rc.add_argument(
        "action",
        choices=["manifest", "verify"],
        help=(
            "manifest：在护栏内空目录写入 manifest 与校验和（归档需已由 "
            "build 脚本保存到位）；verify：独立校验既有 RC 包"
        ),
    )
    p_rc.add_argument(
        "--output-dir",
        default=None,
        help=(
            "manifest：包输出目录（必须位于 gitignore 的 artifacts/ 或 temp/ 内、"
            "不是 artifacts/temp 本身、不存在或为空——非空拒绝）"
        ),
    )
    p_rc.add_argument(
        "--package-dir",
        default=None,
        help="verify：待校验的 RC 包目录（只读）",
    )
    p_rc.add_argument(
        "--tag",
        default=None,
        help="manifest：发布 tag（严格 vX.Y.Z，必须与 VERSION 文件逐字一致）",
    )
    p_rc.add_argument(
        "--version-file",
        default=None,
        help="VERSION 文件路径（manifest 必填；verify 可选——提供时交叉核对）",
    )
    p_rc.add_argument(
        "--git-commit",
        default=None,
        help="manifest：构建时的完整 git commit SHA（40/64 位十六进制）",
    )
    p_rc.add_argument(
        "--compose-file",
        default=None,
        help="manifest：compose 文件路径（哈希写入 manifest）",
    )
    p_rc.add_argument("--api-image-id", default=None, help="aios/api 镜像 ID（sha256:<64hex>）")
    p_rc.add_argument("--web-image-id", default=None, help="aios/web 镜像 ID（sha256:<64hex>）")
    p_rc.add_argument("--api-archive", default=None, help="api 镜像归档文件名（输出目录内纯文件名）")
    p_rc.add_argument("--web-archive", default=None, help="web 镜像归档文件名（输出目录内纯文件名）")
    p_rc.add_argument(
        "--build-context",
        default=".",
        help="docker build context（两个镜像同源同上下文，默认仓库根 .）",
    )
    p_rc.add_argument(
        "--web-build-arg",
        default="http://127.0.0.1:8000",
        help="web 构建参数 NEXT_PUBLIC_API_BASE_URL（默认与 compose 缺省一致）",
    )
    p_rc.add_argument(
        "--smoke-script",
        default="infra/smoke_docker.sh",
        help="构建时执行的冒烟脚本（记录进 manifest）",
    )
    p_rc.add_argument(
        "--generated-at",
        default=None,
        help="可选生成时间戳（ISO-8601，由 build 脚本传入）",
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
    if args.command == "release-check-isolated":
        raise SystemExit(_run_release_check_isolated(args))
    if args.command == "backup-restore-evidence":
        raise SystemExit(_run_backup_restore_evidence(args))
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
    if args.command == "release-readiness":
        raise SystemExit(_run_release_readiness(args))
    if args.command == "evidence-cockpit":
        raise SystemExit(_run_evidence_cockpit(args))
    if args.command == "cutover-rehearsal":
        raise SystemExit(_run_cutover_rehearsal(args))
    if args.command == "governance-evidence":
        raise SystemExit(_run_governance_evidence(args))
    if args.command == "provider-smoke-export":
        raise SystemExit(_run_provider_smoke_export(args))
    if args.command == "provider-smoke-aggregate":
        raise SystemExit(_run_provider_smoke_aggregate(args))
    if args.command == "production-evidence-gap":
        raise SystemExit(_run_production_evidence_gap(args))
    if args.command == "release-closure-manifest":
        raise SystemExit(_run_release_closure_manifest(args))
    if args.command == "evidence-inventory":
        raise SystemExit(_run_evidence_inventory(args))
    if args.command == "cutover-evidence-pack":
        raise SystemExit(_run_cutover_evidence_pack(args))
    if args.command == "release-candidate":
        raise SystemExit(_run_release_candidate(args))
    if args.command == "admin":
        if args.action != "list" and not args.username:
            print("promote/demote 需要用户名")
            raise SystemExit(2)
        raise SystemExit(_run_admin(args))
    raise SystemExit(asyncio.run(_run_restore(args)))


if __name__ == "__main__":
    main()
