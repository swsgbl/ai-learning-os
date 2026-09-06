"""M11-20 evidence-inventory：生产证据目录只读索引器的矩阵/护栏/边界。

覆盖矩阵：
1. CLI 注册与分发：子命令注册、无 --yes 执行形态（argparse 未知旗标
   exit 2）、--evidence-dir 必填、main 分发、--output 越界拒绝；
2. 分类与适用性：m11-02 形态（README「隔离 fixture」声明 + template +
   step 文件）不会被视为生产（非模板条目 isolation_fixture、模板
   not_applicable、适用性枚举无 production_verified 值）；无 README 声明
   => scope=unknown + unverified；README 无 marker / 超限 => 不认定；
   六类分类矩阵（step/template/preflight/backup manifest/锚副本/other）；
3. m11-11 形态：production-preflight.json 白名单解析（declared.phase）、
   backup manifest 解析（schema_version/created_at）、database.json 大文件
   只做哈希元数据（parse_status=skipped_size、sha256 与独立计算一致）；
4. declared 白名单：非枚举 step / 非 ISO 时间 / 恶意 schema_version / 业务
   字段一律 null 不透出；敏感键 JSON 只报布尔，键名与值零回显；
5. 元数据正确性：size/sha256/mtime 与独立 hashlib/stat 计算一致；
6. 多目录与汇总：root 编号、总体聚合计数、13 步覆盖并集、跨目录重复
   sha256 组；与 cutover-rehearsal STEPS 交叉锁定（不重新定义清单）；
   嵌套同名 step 文件列入 inventory 但不推进覆盖（coverage 只认 root
   顶层精确路径，与 rehearsal 消费口径一致）；
7. exact schema allowlist：顶层 / summary / roots / files / declared /
   cutover_steps / duplicate 组键集合恰为白名单（多键少键都红）；
8. production_ready 恒 false + isolation/no_execution/scope notes；
9. 路径与 IO 护栏（exit 2）：root 不存在/普通文件/symlink、树内 symlink
   文件与目录（绝不跟随）、目录枚举错误（os.walk onerror——不可读子目录
   fail-closed，绝不输出不完整清单或部分报告文件）、重复 root、嵌套
   root、--output 越界/位于证据目录内（含 ``..`` 折叠与大小写变体）/
   等于 root/symlink（含中间组件）/已存在目录；输出护栏先于任何枚举；
   原子写失败旧文件字节原样、无 .tmp 残留、不打印盘点结论；
10. 零敏感与只读：毒化 marker（生产 ID/密码/key/token）在 JSON manifest、
    人类摘要与输出文件三面零泄漏；marker 出现在**文件名与父目录名**时
    同样三面零泄漏（显示名脱敏：仅 root 顶层已知精确安全文件名白名单
    成员原样显示，未知文件名与任何嵌套路径一律 [redacted]，重复组
    paths 同口径；已知安全名保持可读）；输出零绝对路径（目录只以 #N
    编号、文件只用相对路径）；全部文件字节在运行（含 --output 落盘）
    前后不变；
11. 源码守卫：模块零 os.environ/getenv/DB/网络/subprocess/asyncio 引用；
    import 面恰为 cutover_rehearsal/evidence_kit/legacy_papers + 标准库；
    CLI 注册块无 --yes 旗标；--json stdout 纯 JSON、提示走 stderr。

全部测试只用临时目录与本地文件，不连接任何数据库、不发任何网络请求、
不读取任何环境变量。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ops import cli as cli_module
from app.ops import cutover_rehearsal as cr
from app.ops import evidence_inventory as ei
from app.ops.evidence_inventory import (
    EvidenceInventoryInputError,
    build_evidence_inventory,
    format_inventory_summary,
)

#: 生产数据/密钥 marker：任何输出（JSON / 人类摘要 / 输出文件）都不得包含
PAPER_ID_MARKER = "PROD-PAPER-ID-88c1"
PASSWORD_MARKER = "PROD-PW-88c3"
API_KEY_MARKER = "sk-PROD-KEY-88c4"
TOKEN_MARKER = "PROD-TOKEN-88c5"

TOP_LEVEL_KEYS = {
    "generated_at",
    "tool",
    "read_only",
    "isolation_note",
    "no_execution_note",
    "scope_note",
    "production_ready",
    "production_ready_note",
    "roots",
    "summary",
    "exit_code",
}
SUMMARY_KEYS = {
    "roots_total",
    "files_total",
    "dirs_total",
    "classifications",
    "applicabilities",
    "parse_statuses",
    "cutover_steps",
    "duplicate_sha256_groups",
}
ROOT_KEYS = {
    "root_index",
    "fixture_declaration",
    "fixture_declaration_source",
    "scope",
    "file_count",
    "dir_count",
    "classifications",
    "applicabilities",
    "parse_statuses",
    "cutover_steps_covered",
    "cutover_steps_missing",
    "duplicate_sha256_count",
    "files",
}
FILE_KEYS = {
    "root_index",
    "name",
    "relative_path",
    "classification",
    "applicability",
    "size_bytes",
    "sha256",
    "mtime_utc",
    "parse_status",
    "sensitive_key_detected",
    "declared",
}
DECLARED_KEYS = {
    "step",
    "phase",
    "result",
    "schema_version",
    "valid",
    "executed",
    "all_green",
    "verified",
    "generated_at",
    "created_at",
    "approved_at",
    "started_at",
    "completed_at",
}
CUTOVER_STEPS_KEYS = {"known_total", "covered", "missing"}
DUPLICATE_GROUP_KEYS = {"sha256", "count", "paths"}


def _dir(tmp_path: Path, name: str = "evidence") -> Path:
    directory = tmp_path / name
    directory.mkdir()
    return directory


def _artifacts(tmp_path: Path) -> Path:
    directory = tmp_path / "artifacts"
    directory.mkdir()
    return directory


def _write_json(directory: Path, name: str, payload) -> None:
    (directory / name).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _m11_02_like_dir(tmp_path: Path, name: str = "m11-02-like") -> Path:
    """m11-02/cutover-evidence 形态：README 隔离 fixture 声明 + 模板 +
    已填 step 文件 + 历史汇总（rehearsal-manifest*.json）。"""
    directory = _dir(tmp_path, name)
    (directory / "README.md").write_text(
        "# 脚手架\n\n隔离 fixture 形态说明：本目录是演练脚手架，不代表生产。\n",
        encoding="utf-8",
    )
    _write_json(
        directory,
        "ci-main.json",
        {
            "step": "ci-main",
            "run_id": 1,
            "merge_commit": "aa11bb22cc33",
            "conclusion": "success",
        },
    )
    _write_json(
        directory,
        "preflight-pre-migration.json",
        {
            "step": "preflight-pre-migration",
            "phase": "pre-migration",
            "summary": {"pass": 3, "pending": 2, "fail": 0, "not_configured": 1},
        },
    )
    _write_json(
        directory, "ci-main.template.json", {"step": "ci-main", "run_id": "REPLACE-ME"}
    )
    (directory / "audit-anchor.jsonl.template").write_text(
        "# REPLACE-ME\n", encoding="utf-8"
    )
    _write_json(directory, "rehearsal-manifest.json", {"tool": "cutover-rehearsal"})
    return directory


def _m11_11_like_dir(tmp_path: Path, name: str = "m11-11-like") -> Path:
    """m11-11 形态：production-preflight + backup 目录（manifest + 大
    database.json）碎片，无 README 声明。"""
    directory = _dir(tmp_path, name)
    readonly = directory / "production-readonly-01"
    readonly.mkdir()
    _write_json(
        readonly,
        "production-preflight.json",
        {
            "generated_at": "2026-09-05T20:00:00+00:00",
            "phase": "pre-migration",
            "read_only": True,
            "exit_code": 1,
            "summary": {"pass": 3, "pending": 2, "fail": 0, "not_configured": 1},
            "checks": [],
        },
    )
    backup = directory / "backup-01"
    backup.mkdir()
    _write_json(
        backup,
        "manifest.json",
        {
            "schema_version": "aios-backup-v1",
            "created_at": "2026-09-05T21:00:00+00:00",
            "tables": [],
            "files": [],
        },
    )
    (backup / "database.json").write_bytes(
        b'{"rows": "' + b"x" * (ei._MAX_PARSE_BYTES + 1) + b'"}'
    )
    return directory


def _build(evidence_dirs, output=None) -> dict:
    return build_evidence_inventory(
        [str(path) for path in evidence_dirs],
        str(output) if output else None,
    )


def _cli(evidence_dirs, *, as_json=False, output=None) -> int:
    return cli_module._run_evidence_inventory(
        SimpleNamespace(
            evidence_dir=[str(path) for path in evidence_dirs],
            as_json=as_json,
            output=str(output) if output else None,
        )
    )


def _snapshot(directory: Path) -> dict[str, tuple[bytes, int]]:
    return {
        entry.relative_to(directory).as_posix(): (
            entry.read_bytes(),
            entry.stat().st_mtime_ns,
        )
        for entry in sorted(directory.rglob("*"))
        if entry.is_file()
    }


def _file(report: dict, name: str) -> dict:
    return next(
        item
        for root in report["roots"]
        for item in root["files"]
        if item["name"] == name
    )


def _file_by_sha(report: dict, real_path: Path) -> dict:
    """按真实文件 sha256 定位条目（显示名脱敏后未知文件名与嵌套路径的
    条目 name 是 [redacted]，无法按显示名查找）。"""
    digest = hashlib.sha256(real_path.read_bytes()).hexdigest()
    return next(
        item
        for root in report["roots"]
        for item in root["files"]
        if item["sha256"] == digest
    )


# --- 1. CLI 注册与分发 ----------------------------------------------------------


def test_cli_registered_and_has_no_execute_flag(tmp_path, monkeypatch) -> None:
    """CLI 子命令注册、无 --yes 执行形态（argparse 对未知旗标 exit 2）。"""
    assert hasattr(cli_module, "_run_evidence_inventory")
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    assert '"evidence-inventory"' in source
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "evidence-inventory",
            "--evidence-dir",
            str(_dir(tmp_path)),
            "--yes",
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 2


def test_main_dispatch_evidence_inventory(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli", "evidence-inventory", "--evidence-dir", str(_dir(tmp_path))],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "production_ready=false" in out
    assert "不构成生产放行" in out


def test_cli_missing_evidence_dir_arg_exits_2(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["cli", "evidence-inventory"])
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 2


def test_cli_output_outside_artifacts_rejected(tmp_path, capsys) -> None:
    directory = _dir(tmp_path)
    target = tmp_path / "inventory.json"
    assert _cli([directory], output=target) == 2
    assert not target.exists()
    assert "artifacts" in capsys.readouterr().out


# --- 2. 分类与适用性（m11-02 形态不被当生产） ------------------------------------


def test_m11_02_shape_fixture_not_treated_as_production(tmp_path) -> None:
    """README 声明隔离 fixture：非模板条目 isolation_fixture、模板
    not_applicable——不会被当作生产证据（production_ready 仍 false）。
    显示名脱敏：13 步名/README 原样显示，模板与未知名 redacted。"""
    directory = _m11_02_like_dir(tmp_path)
    report = _build([directory])
    root = report["roots"][0]
    assert root["fixture_declaration"] == "isolation_fixture"
    assert root["fixture_declaration_source"] == "README.md"
    assert root["scope"] == "declared_isolation_fixture"
    assert root["applicabilities"]["isolation_fixture"] > 0
    assert root["applicabilities"]["not_applicable"] == 2  # 两个模板
    assert root["applicabilities"]["unverified"] == 0
    assert _file(report, "ci-main.json")["applicability"] == "isolation_fixture"
    assert _file(report, "ci-main.json")["classification"] == "cutover_step_evidence"
    template = _file_by_sha(report, directory / "ci-main.template.json")
    assert template["classification"] == "cutover_template"
    assert template["applicability"] == "not_applicable"
    assert template["name"] == ei.REDACTED_PLACEHOLDER
    assert template["relative_path"] == ei.REDACTED_PLACEHOLDER
    assert (
        _file_by_sha(report, directory / "audit-anchor.jsonl.template")[
            "classification"
        ]
        == "cutover_template"
    )
    assert _file(report, "README.md")["classification"] == "other"
    assert (
        _file_by_sha(report, directory / "rehearsal-manifest.json")["classification"]
        == "other"
    )
    assert report["production_ready"] is False


def test_applicability_never_production_verified(tmp_path) -> None:
    """适用性枚举只有三值：绝无 production_verified（诚实口径源码锁定——
    代码不出现该字面量，行为面所有条目值都在三值枚举内）。"""
    import ast

    assert set(ei.APPLICABILITY_VALUES) == {
        "isolation_fixture",
        "not_applicable",
        "unverified",
    }
    source = Path(ei.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_doc = ast.get_docstring(tree) or ""
    code = source.replace(module_doc, "")
    assert '"production_verified"' not in code
    assert "'production_verified'" not in code
    report = _build([_m11_02_like_dir(tmp_path), _m11_11_like_dir(tmp_path)])
    for root in report["roots"]:
        for item in root["files"]:
            assert item["applicability"] in ei.APPLICABILITY_VALUES


def test_no_readme_declaration_means_unknown_and_unverified(tmp_path) -> None:
    report = _build([_m11_11_like_dir(tmp_path)])
    root = report["roots"][0]
    assert root["fixture_declaration"] == "none"
    assert root["fixture_declaration_source"] is None
    assert root["scope"] == "unknown"
    assert root["applicabilities"]["unverified"] == root["file_count"]


def test_readme_without_marker_or_oversize_not_declared(tmp_path) -> None:
    plain = _dir(tmp_path, "plain")
    (plain / "README.md").write_text("普通说明，无声明\n", encoding="utf-8")
    _write_json(plain, "ci-main.json", {"step": "ci-main"})
    report = _build([plain])
    assert report["roots"][0]["fixture_declaration"] == "none"
    assert _file(report, "ci-main.json")["applicability"] == "unverified"

    oversize = _dir(tmp_path, "oversize")
    (oversize / "README.md").write_bytes(
        ("隔离 fixture" + "x" * ei._MAX_README_BYTES).encode("utf-8")
    )
    _write_json(oversize, "ci-main.json", {"step": "ci-main"})
    report = _build([oversize])
    assert report["roots"][0]["fixture_declaration"] == "none"
    assert _file(report, "ci-main.json")["applicability"] == "unverified"


def test_classification_matrix(tmp_path) -> None:
    """六类分类：13 步精确名 / 模板后缀 / preflight 前缀 / manifest.json /
    锚副本 / other（database.json 等未知名不解析内容）。按 sha 定位（多数
    名不在显示白名单内，name 是 [redacted]）。"""
    directory = _dir(tmp_path)
    names = {
        "ci-main.json": "cutover_step_evidence",
        "preflight-pre-migration.json": "cutover_step_evidence",
        "cutover-approval.json": "cutover_step_evidence",
        "audit-anchor.jsonl": "audit_anchor_copy",
        "ci-main.template.json": "cutover_template",
        "audit-anchor.jsonl.template": "cutover_template",
        "production-preflight.json": "production_preflight",
        "production-preflight-pre-migration.json": "production_preflight",
        "manifest.json": "backup_manifest",
        "database.json": "other",
        "notes.txt": "other",
    }
    for name in names:
        (directory / name).write_text(f"classify:{name}", encoding="utf-8")
    report = _build([directory])
    for name, expected in names.items():
        entry = _file_by_sha(report, directory / name)
        assert entry["classification"] == expected, name
        if name in ei.SAFE_DISPLAY_FILENAMES:
            assert entry["name"] == name, name
        else:
            assert entry["name"] == ei.REDACTED_PLACEHOLDER, name


# --- 3. m11-11 形态（preflight + backup + 大 JSON 只哈希） ------------------------


def test_m11_11_shape_preflight_backup_and_large_json(tmp_path) -> None:
    directory = _m11_11_like_dir(tmp_path)
    report = _build([directory])
    preflight = _file_by_sha(
        report, directory / "production-readonly-01" / "production-preflight.json"
    )
    assert preflight["classification"] == "production_preflight"
    assert preflight["parse_status"] == "parsed"
    assert preflight["declared"]["phase"] == "pre-migration"
    assert preflight["declared"]["generated_at"] == "2026-09-05T20:00:00+00:00"

    manifest = _file_by_sha(report, directory / "backup-01" / "manifest.json")
    assert manifest["classification"] == "backup_manifest"
    assert manifest["parse_status"] == "parsed"
    assert manifest["declared"]["schema_version"] == "aios-backup-v1"
    assert manifest["declared"]["created_at"] == "2026-09-05T21:00:00+00:00"

    database = _file_by_sha(report, directory / "backup-01" / "database.json")
    assert database["classification"] == "other"
    assert database["parse_status"] == "not_attempted"  # 未知文件名不解析内容
    assert database["declared"] is None
    raw = (directory / "backup-01" / "database.json").read_bytes()
    assert database["sha256"] == hashlib.sha256(raw).hexdigest()
    assert database["size_bytes"] == len(raw)

    # 已知分类的超限大文件（如数 MB 的 ci-main.json）也只做哈希元数据
    oversized = _dir(tmp_path, "oversized-step")
    (oversized / "ci-main.json").write_bytes(b"9" * (ei._MAX_PARSE_BYTES + 1))
    entry = _file(_build([oversized]), "ci-main.json")
    assert entry["classification"] == "cutover_step_evidence"
    assert entry["parse_status"] == "skipped_size"
    assert entry["declared"] is None
    assert entry["sha256"] == hashlib.sha256(b"9" * (ei._MAX_PARSE_BYTES + 1)).hexdigest()


def test_unparsed_json_reports_no_error_text(tmp_path) -> None:
    directory = _dir(tmp_path)
    (directory / "ci-main.json").write_bytes(b"\xff\xfe not json")
    report = _build([directory])
    entry = _file(report, "ci-main.json")
    assert entry["parse_status"] == "unparsed"
    assert entry["declared"] is None
    text = json.dumps(report, ensure_ascii=False)
    assert "不是合法 JSON" not in text and "不是有效 UTF-8" not in text


# --- 4. declared 白名单（零内容回显） ---------------------------------------------


def test_declared_whitelist_rejects_non_matching_values(tmp_path) -> None:
    directory = _dir(tmp_path)
    _write_json(
        directory,
        "ci-main.json",
        {
            "step": f"weird-{TOKEN_MARKER}",  # 非枚举 -> null
            "phase": "mid-migration",  # 非枚举 -> null
            "result": {"nested": PAPER_ID_MARKER},  # 非枚举 -> null
            "schema_version": f"v{PASSWORD_MARKER};;rm -rf",  # 恶意 -> null
            "generated_at": f"{PASSWORD_MARKER} at noon",  # 非 ISO -> null
            "valid": "yes",  # 非布尔 -> null
            "executed": 1,  # 非布尔（bool 伪装 int 拒绝） -> null
            "title": f"业务标题 {PAPER_ID_MARKER}",  # 业务字段不进 declared
            "endpoint": "https://prod.internal/api",
        },
    )
    entry = _file(_build([directory]), "ci-main.json")
    assert entry["declared"] == {key: None for key in DECLARED_KEYS}


def test_declared_whitelist_accepts_only_safe_scalars(tmp_path) -> None:
    directory = _dir(tmp_path)
    _write_json(
        directory,
        "search-smoke.json",
        {
            "step": "search-smoke",
            "result": "pass",
            "executed": True,
            "schema_version": "aios-v1",
            "generated_at": "2026-09-06T01:02:03+00:00",
            "approved_at": "2026-09-06",
        },
    )
    declared = _file(_build([directory]), "search-smoke.json")["declared"]
    assert declared["step"] == "search-smoke"
    assert declared["result"] == "pass"
    assert declared["executed"] is True
    assert declared["schema_version"] == "aios-v1"
    assert declared["generated_at"] == "2026-09-06T01:02:03+00:00"
    assert declared["approved_at"] == "2026-09-06"


def test_sensitive_key_json_reports_bool_only(tmp_path) -> None:
    """敏感键 JSON：只报 sensitive_key_detected 布尔，键名与值零回显。"""
    directory = _dir(tmp_path)
    _write_json(
        directory,
        "llm-smoke.json",
        {
            "step": "llm-smoke",
            "executed": True,
            "result": "pass",
            "api_key": API_KEY_MARKER,
        },
    )
    entry = _file(_build([directory]), "llm-smoke.json")
    assert entry["sensitive_key_detected"] is True
    text = json.dumps(entry, ensure_ascii=False)
    assert "api_key" not in text and API_KEY_MARKER not in text


# --- 5. 元数据正确性 -------------------------------------------------------------


def test_metadata_matches_independent_computation(tmp_path) -> None:
    directory = _dir(tmp_path)
    payload = '{"step": "ci-main", "run_id": 7}'
    path = directory / "ci-main.json"
    path.write_text(payload, encoding="utf-8")
    entry = _file(_build([directory]), "ci-main.json")
    stat = path.stat()
    assert entry["size_bytes"] == stat.st_size == len(payload.encode("utf-8"))
    assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert entry["mtime_utc"] == datetime.fromtimestamp(
        stat.st_mtime, tz=UTC
    ).isoformat()
    assert entry["relative_path"] == "ci-main.json"
    assert entry["root_index"] == 1


# --- 6. 多目录、13 步交叉锁定与重复 sha256 ---------------------------------------


def test_multiple_roots_indexed_and_aggregated(tmp_path) -> None:
    first = _m11_02_like_dir(tmp_path, "first")
    second = _m11_11_like_dir(tmp_path, "second")
    report = _build([first, second])
    assert [root["root_index"] for root in report["roots"]] == [1, 2]
    assert report["summary"]["roots_total"] == 2
    assert report["summary"]["files_total"] == (
        report["roots"][0]["file_count"] + report["roots"][1]["file_count"]
    )
    for item in report["roots"][0]["files"]:
        assert item["root_index"] == 1
    for item in report["roots"][1]["files"]:
        assert item["root_index"] == 2
    # 覆盖取并集：first 有 ci-main/preflight-pre，second 无 step 文件
    covered = set(report["summary"]["cutover_steps"]["covered"])
    assert covered == set(report["roots"][0]["cutover_steps_covered"])
    assert report["roots"][1]["cutover_steps_covered"] == []


def test_cutover_step_coverage_cross_locked_with_rehearsal(tmp_path) -> None:
    """known_total 与 rehearsal STEPS 同步；全 13 文件 => missing 空；
    coverage 只看文件名存在（inventory 不做放行判定）。"""
    directory = _dir(tmp_path)
    for spec in cr.STEPS:
        _write_json(directory, spec.evidence_file, {"step": spec.step_id})
    report = _build([directory])
    steps = report["summary"]["cutover_steps"]
    assert steps["known_total"] == len(cr.STEPS) == 13
    assert steps["missing"] == []
    assert set(steps["covered"]) == cr.STEP_IDS
    assert report["roots"][0]["cutover_steps_missing"] == []
    # 审批文件在 declared 里如实透出 step 枚举，但 coverage 不代表 pass
    assert _file(report, "cutover-approval.json")["declared"]["step"] == (
        "cutover-approval"
    )


def test_missing_steps_reported(tmp_path) -> None:
    report = _build([_m11_02_like_dir(tmp_path)])
    root = report["roots"][0]
    assert root["cutover_steps_covered"] == ["ci-main", "preflight-pre-migration"]
    assert root["cutover_steps_missing"] == sorted(
        cr.STEP_IDS - {"ci-main", "preflight-pre-migration"}
    )


def test_duplicate_sha256_groups_across_roots(tmp_path) -> None:
    first = _dir(tmp_path, "first")
    second = _dir(tmp_path, "second")
    payload = '{"step": "ci-main", "run_id": 1}'
    (first / "ci-main.json").write_text(payload, encoding="utf-8")
    (second / "release-check.json").write_text(payload, encoding="utf-8")
    (second / "ci-main.json").write_text(payload, encoding="utf-8")
    (first / "llm-smoke.json").write_text('{"step": "llm-smoke"}', encoding="utf-8")
    (second / "llm-smoke.json").write_text('{"step": "llm-smoke"}', encoding="utf-8")
    report = _build([first, second])
    groups = report["summary"]["duplicate_sha256_groups"]
    assert len(groups) == 2
    by_count = sorted(groups, key=lambda item: item["count"])
    assert by_count[0]["count"] == 2  # llm-smoke 跨目录重复
    assert by_count[0]["paths"] == ["#1/llm-smoke.json", "#2/llm-smoke.json"]
    assert by_count[1]["count"] == 3  # 同字节三份（跨目录 + 目录内）
    assert sorted(by_count[1]["paths"]) == [
        "#1/ci-main.json",
        "#2/ci-main.json",
        "#2/release-check.json",
    ]
    assert report["roots"][0]["duplicate_sha256_count"] == 0  # 目录内无重复
    assert report["roots"][1]["duplicate_sha256_count"] == 2  # ci-main/release-check


def test_nested_step_filename_listed_but_not_covering(tmp_path) -> None:
    """嵌套 arbitrary/ci-main.json 仍列入 inventory（classification 不变），
    但 coverage 只认 root 顶层精确相对路径——cutover-rehearsal 只消费
    root/<evidence_file>，嵌套同名不得推进 covered。"""
    directory = _dir(tmp_path)
    nested = directory / "arbitrary"
    nested.mkdir()
    _write_json(nested, "ci-main.json", {"step": "ci-main"})
    _write_json(nested, "release-check.json", {"step": "release-check"})
    (directory / "notes.txt").write_text("x", encoding="utf-8")
    report = _build([directory])
    root = report["roots"][0]
    assert root["file_count"] == 3  # 嵌套同名文件仍列入 inventory
    assert root["classifications"]["cutover_step_evidence"] == 2
    assert root["cutover_steps_covered"] == []
    assert root["cutover_steps_missing"] == sorted(cr.STEP_IDS)
    assert report["summary"]["cutover_steps"]["covered"] == []
    assert report["summary"]["cutover_steps"]["missing"] == sorted(cr.STEP_IDS)

    _write_json(directory, "ci-main.json", {"step": "ci-main"})  # 顶层精确名
    report = _build([directory])
    assert report["roots"][0]["cutover_steps_covered"] == ["ci-main"]
    assert report["summary"]["cutover_steps"]["covered"] == ["ci-main"]


# --- 7. exact schema allowlist ----------------------------------------------------


def test_output_schema_exact_allowlist(tmp_path) -> None:
    report = _build([_m11_02_like_dir(tmp_path), _m11_11_like_dir(tmp_path)])
    assert set(report) == TOP_LEVEL_KEYS
    assert report["tool"] == "evidence-inventory"
    assert report["read_only"] is True
    assert set(report["summary"]) == SUMMARY_KEYS
    assert set(report["summary"]["cutover_steps"]) == CUTOVER_STEPS_KEYS
    for group in report["summary"]["duplicate_sha256_groups"]:
        assert set(group) == DUPLICATE_GROUP_KEYS
    for root in report["roots"]:
        assert set(root) == ROOT_KEYS
        assert set(root["classifications"]) == set(ei.CLASSIFICATIONS)
        assert set(root["applicabilities"]) == set(ei.APPLICABILITY_VALUES)
        assert set(root["parse_statuses"]) == set(ei.PARSE_STATUSES)
        for item in root["files"]:
            assert set(item) == FILE_KEYS
            if item["declared"] is not None:
                assert set(item["declared"]) == DECLARED_KEYS


# --- 8. production_ready 恒 false 与 notes ----------------------------------------


def test_production_ready_always_false_with_notes(tmp_path) -> None:
    report = _build([_m11_02_like_dir(tmp_path)])
    assert report["production_ready"] is False
    assert report["exit_code"] == 0
    note = report["production_ready_note"]
    assert "production_ready" in note and "不构成生产放行" in note
    assert "绝不" in report["isolation_note"] and "production_verified" in (
        report["isolation_note"]
    )
    assert "--yes" in report["no_execution_note"]
    assert "cutover-rehearsal" in report["scope_note"]
    summary_text = format_inventory_summary(report)
    assert "production_ready=false（固定）" in summary_text
    assert "不构成生产放行" in summary_text


# --- 9. 路径与 IO 护栏 ------------------------------------------------------------


def test_root_missing_plain_file_or_symlink_rejected(tmp_path, capsys) -> None:
    assert _cli([tmp_path / "missing"]) == 2
    plain = tmp_path / "plain.json"
    plain.write_text("{}", encoding="utf-8")
    assert _cli([plain]) == 2
    assert "证据目录" in capsys.readouterr().out
    with pytest.raises(EvidenceInventoryInputError):
        build_evidence_inventory([str(tmp_path / "missing")])

    real = _dir(tmp_path, "real-dir")
    link = tmp_path / "link-dir"
    try:
        os.symlink(real, link, target_is_directory=True)
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    with pytest.raises(EvidenceInventoryInputError):
        build_evidence_inventory([str(link)])


def test_symlink_inside_tree_rejected(tmp_path) -> None:
    """递归枚举遇到 symlink 文件/目录（含子目录内）fail-closed，绝不跟随。"""
    directory = _dir(tmp_path)
    (directory / "ci-main.json").write_text("{}", encoding="utf-8")
    nested = directory / "nested"
    nested.mkdir()
    (nested / "real.json").write_text("{}", encoding="utf-8")

    file_link = directory / "link.json"
    try:
        os.symlink(directory / "ci-main.json", file_link)
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    with pytest.raises(EvidenceInventoryInputError):
        build_evidence_inventory([str(directory)])
    file_link.unlink()

    dir_link = nested / "link-dir"
    os.symlink(directory, dir_link, target_is_directory=True)
    with pytest.raises(EvidenceInventoryInputError):
        build_evidence_inventory([str(directory)])


def _patched_walk_that_fails_halfway(monkeypatch) -> None:
    """让 os.walk 在吐出真实结果后模拟一次枚举失败（等价于某个子目录
    scandir 报 PermissionError）——若实现没提供 onerror，这里直接红。"""
    real_walk = os.walk

    def fake_walk(top, followlinks=False, onerror=None):
        yield from real_walk(top, followlinks=followlinks, onerror=onerror)
        assert onerror is not None, "os.walk 必须提供 onerror（不得静默吞错）"
        onerror(PermissionError(13, "模拟不可读子目录"))

    monkeypatch.setattr(ei.os, "walk", fake_walk)


def test_walk_enumeration_error_fails_closed(tmp_path, monkeypatch) -> None:
    """os.walk 枚举失败（不可读/访问被拒绝的子目录）fail-closed：错误转
    EvidenceInventoryInputError——绝不静默跳过、绝不返回不完整清单。"""
    directory = _dir(tmp_path)
    (directory / "ci-main.json").write_text("{}", encoding="utf-8")
    _patched_walk_that_fails_halfway(monkeypatch)
    with pytest.raises(EvidenceInventoryInputError, match="枚举失败"):
        build_evidence_inventory([str(directory)])


def test_walk_error_via_cli_no_partial_output(tmp_path, monkeypatch, capsys) -> None:
    """枚举失败经 CLI exit 2：不产生任何部分报告/输出文件、不打印盘点
    结论摘要。"""
    directory = _dir(tmp_path)
    (directory / "ci-main.json").write_text("{}", encoding="utf-8")
    _patched_walk_that_fails_halfway(monkeypatch)
    target = _artifacts(tmp_path) / "inventory.json"
    capsys.readouterr()
    assert _cli([directory], output=target) == 2
    captured = capsys.readouterr()
    assert not target.exists(), "枚举失败不得产生部分报告文件"
    assert "RESULT" not in captured.out, "枚举失败不得打印盘点结论"
    assert "枚举失败" in captured.out
    assert "未产生清单" in captured.out


def test_duplicate_and_nested_roots_rejected(tmp_path) -> None:
    directory = _dir(tmp_path)
    child = directory / "child"
    child.mkdir()
    with pytest.raises(EvidenceInventoryInputError, match="重复"):
        build_evidence_inventory([str(directory), str(directory)])
    with pytest.raises(EvidenceInventoryInputError, match="嵌套"):
        build_evidence_inventory([str(directory), str(child)])
    if os.name == "nt":
        with pytest.raises(EvidenceInventoryInputError, match="重复"):
            build_evidence_inventory(
                [str(directory), str(directory).upper()]
            )


def test_output_inside_evidence_dir_rejected_before_enumeration(
    tmp_path, monkeypatch
) -> None:
    """输出位于任一证据目录内（含等于目录）拒绝；护栏先于任何枚举。"""
    first = _dir(tmp_path, "first")
    second = _dir(tmp_path, "second")
    calls: list[Path] = []
    monkeypatch.setattr(
        ei, "_enumerate_root", lambda *a: calls.append(a[0]) or ([], 0)
    )
    for target in (
        first / "inventory.json",
        second / "sub" / "inventory.json",
        second,
    ):
        with pytest.raises(EvidenceInventoryInputError):
            build_evidence_inventory([str(first), str(second)], target)
    assert not calls, "输出护栏必须先于任何目录枚举"


def test_output_path_equivalence_forms_rejected(tmp_path) -> None:
    """``..`` 折叠指向证据目录内不得绕过；Windows 上大小写变体同样拒绝。"""
    directory = _dir(tmp_path, "evi")
    folded = directory / ".." / directory.name / "inventory.json"
    with pytest.raises(EvidenceInventoryInputError):
        build_evidence_inventory([str(directory)], folded)
    if os.name != "nt":
        return  # 大小写不敏感 FS 语义仅 Windows 需要（normcase 归一）
    with pytest.raises(EvidenceInventoryInputError):
        build_evidence_inventory([str(directory)], directory / "INVENTORY.JSON")


def test_output_symlink_rejected(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    real = artifacts / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = artifacts / "link.json"
    try:
        os.symlink(real, link)
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    directory = _dir(tmp_path)
    with pytest.raises(EvidenceInventoryInputError):
        build_evidence_inventory([str(directory)], link)


def test_output_symlink_parent_component_rejected(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    real_dir = artifacts / "real-dir"
    real_dir.mkdir()
    link_dir = artifacts / "link-dir"
    try:
        os.symlink(real_dir, link_dir, target_is_directory=True)
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    directory = _dir(tmp_path)
    with pytest.raises(EvidenceInventoryInputError):
        build_evidence_inventory([str(directory)], link_dir / "inventory.json")


def test_output_existing_directory_rejected_via_cli(tmp_path) -> None:
    directory = _dir(tmp_path)
    target = _artifacts(tmp_path) / "as-dir"
    target.mkdir()
    assert _cli([directory], output=target) == 2


def test_output_written_atomically_and_matches_stdout(tmp_path, capsys) -> None:
    artifacts = _artifacts(tmp_path)
    directory = _m11_02_like_dir(tmp_path)
    target = artifacts / "inventory.json"
    capsys.readouterr()
    assert _cli([directory], as_json=True, output=target) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("{"), "stdout 必须是纯 JSON"
    assert "报告已写入" in captured.err, "人读提示走 stderr"
    assert json.loads(captured.out) == json.loads(target.read_text(encoding="utf-8"))
    assert not list(artifacts.glob("*.tmp"))


def test_write_failure_keeps_old_file_and_prints_no_verdict(
    tmp_path, monkeypatch, capsys
) -> None:
    artifacts = _artifacts(tmp_path)
    directory = _dir(tmp_path)
    target = artifacts / "inventory.json"
    target.write_text("OLD-CONTENT", encoding="utf-8")

    def _boom(path, text):
        raise OSError("disk full")

    monkeypatch.setattr(cli_module, "_write_report_atomic", _boom)
    capsys.readouterr()
    assert _cli([directory], output=target) == 2
    captured = capsys.readouterr()
    assert target.read_text(encoding="utf-8") == "OLD-CONTENT"
    assert not list(artifacts.glob("*.tmp"))
    assert "RESULT" not in captured.out, "写入失败不得打印盘点结论摘要"
    assert "写入失败" in captured.out


# --- 10. 零敏感与只读 -------------------------------------------------------------


def _poisoned_dir(tmp_path: Path) -> Path:
    """各类毒化形态：非白名单业务值、敏感键、未知文件内容毒化——任何值
    不得进入任何输出面。"""
    directory = _dir(tmp_path, "poisoned")
    _write_json(
        directory,
        "legacy-papers.json",
        {
            "step": "legacy-papers",
            "pending_count": 2,
            "batches": [{"executed": True, "note": PAPER_ID_MARKER}],
            "title": f"业务标题 {PAPER_ID_MARKER}",
        },
    )
    _write_json(
        directory,
        "ci-main.json",
        {
            "step": "ci-main",
            "run_id": 1,
            "merge_commit": "aa11bb22cc33",
            "conclusion": "success",
            "remark": f"{PASSWORD_MARKER} {TOKEN_MARKER}",
        },
    )
    _write_json(
        directory,
        "llm-smoke.json",
        {
            "step": "llm-smoke",
            "executed": True,
            "result": "pass",
            "api_key": API_KEY_MARKER,
        },
    )
    (directory / "secret-notes.txt").write_text(
        f"{PASSWORD_MARKER} {TOKEN_MARKER}", encoding="utf-8"
    )
    return directory


@pytest.mark.parametrize("surface", ("json", "summary", "file"))
def test_zero_sensitive_markers_on_all_surfaces(tmp_path, capsys, surface) -> None:
    directory = _poisoned_dir(tmp_path)
    if surface == "json":
        text = json.dumps(_build([directory]), ensure_ascii=False)
    elif surface == "summary":
        text = format_inventory_summary(_build([directory]))
    else:
        target = _artifacts(tmp_path) / "inventory.json"
        capsys.readouterr()
        assert _cli([directory], output=target) == 0
        text = target.read_text(encoding="utf-8")
    for marker in (PAPER_ID_MARKER, PASSWORD_MARKER, API_KEY_MARKER, TOKEN_MARKER):
        assert marker not in text
    assert "业务标题" not in text, "业务标题不得进入输出"


def test_sensitive_markers_in_names_and_parents_redacted_all_surfaces(
    tmp_path, capsys
) -> None:
    """敏感 marker 出现在**文件名与父目录名**：JSON manifest / 人类摘要 /
    输出文件三面零泄漏。显示名脱敏：仅 root 顶层已知精确安全文件名白名单
    成员原样显示（保留可读性），未知文件名与任何嵌套路径一律 [redacted]，
    duplicate_sha256_groups 的 paths 用同一安全显示口径。"""
    directory = _dir(tmp_path, "leaky")
    # 文件名本身携带 marker（未知名，不得进入显示字段）
    _write_json(directory, f"paper-{PAPER_ID_MARKER}.json", {"step": "ci-main"})
    (directory / f"pw-{PASSWORD_MARKER}.txt").write_text("x", encoding="utf-8")
    # 父目录名携带 marker（basename 即使是安全名，父路径未证明安全）
    deep = directory / f"dir-{TOKEN_MARKER}"
    deep.mkdir()
    _write_json(deep, "ci-main.json", {"step": "ci-main"})
    # root 顶层已知精确安全文件名：保持可读
    _write_json(directory, "llm-smoke.json", {"step": "llm-smoke"})

    report = _build([directory])
    visible = _file(report, "llm-smoke.json")
    assert visible["name"] == "llm-smoke.json"
    assert visible["relative_path"] == "llm-smoke.json"
    hidden = _file_by_sha(report, deep / "ci-main.json")
    assert hidden["name"] == ei.REDACTED_PLACEHOLDER
    assert hidden["relative_path"] == ei.REDACTED_PLACEHOLDER

    # paper-*.json 与 deep/ci-main.json 字节相同 => 重复组两条路径均 redacted
    groups = report["summary"]["duplicate_sha256_groups"]
    assert len(groups) == 1 and groups[0]["count"] == 2
    for path in groups[0]["paths"]:
        assert path.partition("/")[2] == ei.REDACTED_PLACEHOLDER

    json_text = json.dumps(report, ensure_ascii=False)
    summary_text = format_inventory_summary(report)
    target = _artifacts(tmp_path) / "inventory.json"
    capsys.readouterr()
    assert _cli([directory], output=target) == 0
    file_text = target.read_text(encoding="utf-8")
    for text in (json_text, summary_text, file_text):
        for marker in (PAPER_ID_MARKER, PASSWORD_MARKER, API_KEY_MARKER, TOKEN_MARKER):
            assert marker not in text


def test_output_contains_no_absolute_paths(tmp_path) -> None:
    """manifest 零绝对路径：目录只以 #N 编号、文件只用相对路径。"""
    directory = _m11_02_like_dir(tmp_path)
    report = _build([directory])
    text = json.dumps(report, ensure_ascii=False)
    assert str(directory.resolve()) not in text
    assert str(tmp_path.resolve()) not in text
    assert ":\\" not in text and ":/" not in text  # 无盘符形态绝对路径


def test_evidence_files_bytes_and_mtimes_unchanged(tmp_path) -> None:
    first = _m11_02_like_dir(tmp_path, "first")
    second = _m11_11_like_dir(tmp_path, "second")
    before = {**_snapshot(first), **_snapshot(second)}
    target = _artifacts(tmp_path) / "inventory.json"
    assert _cli([first, second], output=target) == 0
    _build([first, second])  # 再跑一遍纯读取
    assert {**_snapshot(first), **_snapshot(second)} == before


# --- 11. 源码守卫与 --json --------------------------------------------------------


def test_module_source_has_no_env_db_or_network_access() -> None:
    """源码级守卫：零环境变量/DB/网络/subprocess/asyncio 引用（docstring 除外）。"""
    import ast

    source = Path(ei.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_doc = ast.get_docstring(tree) or ""
    code = source.replace(module_doc, "")
    for banned in (
        "os.environ",
        "getenv",
        "httpx",
        "requests",
        "socket",
        "urllib",
        "create_engine",
        "asyncpg",
        "psycopg",
        "sqlalchemy",
        "subprocess",
        "asyncio",
    ):
        assert banned not in code, f"不得出现 {banned}"


def test_module_imports_only_shared_layers() -> None:
    """import 面恰为 cutover_rehearsal/evidence_kit/legacy_papers + 标准库：
    复用既有共享层（13 步清单/敏感键扫描/scrub/输出路径护栏），不引入新
    依赖面（DB/网络模块物理上进不来）。"""
    import ast

    tree = ast.parse(Path(ei.__file__).read_text(encoding="utf-8"))
    allowed = {"cutover_rehearsal", "evidence_kit", "legacy_papers"}
    stdlib = {
        "__future__",
        "hashlib",
        "json",
        "os",
        "re",
        "collections",
        "datetime",
        "pathlib",
        "typing",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            root = node.module.split(".")
            if root[0] == "app":
                assert node.module == f"app.ops.{root[2]}", node.module
                assert root[2] in allowed, f"不得依赖 {node.module}"
            else:
                assert root[0] in stdlib, f"不得依赖 {node.module}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in stdlib, f"不得依赖 {alias.name}"


def test_module_reuses_rehearsal_step_list_and_shared_layers() -> None:
    """13 步文件名映射与锚副本名复用 cutover-rehearsal 常量（不重新定义，
    防两处清单漂移）。"""
    assert ei._STEP_BY_EVIDENCE_FILE == {
        spec.evidence_file: spec.step_id for spec in cr.STEPS
    }
    source = Path(ei.__file__).read_text(encoding="utf-8")
    assert "from app.ops.cutover_rehearsal import" in source
    assert "from app.ops.evidence_kit import" in source
    assert "from app.ops.legacy_papers import" in source


def test_cli_parser_block_has_no_yes_flag() -> None:
    """parser 注册区（p_ei 块）不得有 --yes 旗标：命令没有执行形态。"""
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    block = source.split("p_ei = sub.add_parser(")[1]
    block = block.split("p_ep = sub.add_parser(")[0]
    assert "evidence-inventory" in block
    assert '"--yes"' not in block


def test_json_stdout_pure_and_hints_on_stderr(tmp_path, capsys) -> None:
    directory = _m11_02_like_dir(tmp_path)
    capsys.readouterr()
    assert _cli([directory], as_json=True) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["tool"] == "evidence-inventory"
    assert captured.err == ""  # 无 --output 时无提示


def test_human_summary_printed_without_json(tmp_path, capsys) -> None:
    directory = _m11_02_like_dir(tmp_path)
    capsys.readouterr()
    assert _cli([directory]) == 0
    out = capsys.readouterr().out
    assert out.startswith("生产证据目录只读索引")
    assert "RESULT: production_ready=false（固定）" in out
    assert "#1" in out  # 目录以编号标识而非绝对路径
