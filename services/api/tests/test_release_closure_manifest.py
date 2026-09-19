"""M14-68 release-closure-manifest：生产收口 manifest 聚合器的矩阵/边界。

覆盖矩阵：
1. 确定性：同输入（固定 clock + 显式 git HEAD）两次构建，JSON 与
   Markdown 逐字一致；
2. git HEAD：显式提供（大写规范化为小写、零子进程调用）、非法形态
   fail-closed、安全发现（成功/非零退出/OSError/垃圾输出四种形态，
   发现问题一律提示改用 --git-head）；
3. 证据清单：相对 posix 名排序、字节数、SHA-256 与 hashlib 独立计算
   逐文件一致（含子目录）；
4. 只消费既有评估器：readiness/gap 子集与独立运行的
   run_release_readiness / build_production_evidence_gap 结论逐字段一致
   （复用而非重复实现）；gap 四类全 pass 而 readiness 未 ready 时
   production_ready 仍 false（合取单边不放行）；空目录 blockers 覆盖
   十门与四类；
5. 诚实合取：两侧 stub 全 pass 才 production_ready=true / exit 0
   （stub 只测聚合逻辑，不发明任何真实审批）；
6. 零绝对路径与零敏感：JSON+Markdown 不含证据目录/当前目录字面量、
   盘符/UNC/常见 POSIX 绝对前缀；毒化 marker（生产 ID/密码/key/token）
   在任何输出表面零泄漏；
7. 输出护栏（exit 2、fail-closed）：越界路径、位于证据目录内（含
   ``..`` 折叠）、等于证据目录、symlink 输出（含中间组件）、已存在
   目录目标、--output-json 与 --output-md 同路径；护栏先于证据读取
   （冲突形态下 readiness 零调用）；
8. CLI：注册与分发、无 --yes 执行形态、--evidence-dir 必填、双输出
   原子落盘（stdout 与文件一致、无 .tmp 残留、提示走 stderr）、写入
   失败旧文件字节原样且不打印收口结论；
9. 无效/截断输入 fail-closed：截断 JSON 证据 → 对应门进 blockers（exit
   1、不崩溃、不误 pass）；证据目录不存在/内含 symlink → exit 2；
10. 源码守卫：模块零环境变量/DB/网络引用；subprocess 恰一次（git rev-parse
    HEAD 固定 argv）；CLI 注册块无 --yes 旗标。

全部测试只用临时目录与本地文件，不连接任何数据库、不发任何网络请求、
不读取任何环境变量。
"""
from __future__ import annotations

import ast
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
from app.ops import cutover_rehearsal as cr
from app.ops import release_closure_manifest as rcm
from app.ops.evidence_kit import EvidenceInputError
from app.ops.release_closure_manifest import (
    ClosureManifestInputError,
    build_release_closure_manifest,
    format_closure_markdown,
)

#: 生产数据/密钥 marker：任何输出（JSON / Markdown / 输出文件）都不得包含
PAPER_ID_MARKER = "PROD-PAPER-ID-77c1"
PASSWORD_MARKER = "PROD-PW-77c3"
API_KEY_MARKER = "sk-PROD-KEY-77c4"
TOKEN_MARKER = "PROD-TOKEN-77c5"

_GIT_HEAD = "ab" * 20
_FIXED_MOMENT = datetime(2026, 9, 20, 0, 0, 0, tzinfo=UTC)


def _clock() -> datetime:
    return _FIXED_MOMENT


def _evidence_dir(tmp_path: Path, name: str = "evidence") -> Path:
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


def _build(directory: Path, **kwargs):
    kwargs.setdefault("git_head", _GIT_HEAD)
    kwargs.setdefault("clock", _clock)
    return build_release_closure_manifest(directory, **kwargs)


def _cli(
    directory,
    *,
    git_head=...,
    as_json=False,
    output_json=None,
    output_md=None,
) -> int:
    return cli_module._run_release_closure_manifest(
        SimpleNamespace(
            evidence_dir=str(directory),
            git_head=_GIT_HEAD if git_head is ... else git_head,
            as_json=as_json,
            output_json=str(output_json) if output_json else None,
            output_md=str(output_md) if output_md else None,
        )
    )


def _passing_steps_evidence() -> dict[str, dict]:
    """12 步齐备且全部满足 pass 形态的证据（不含 cutover-approval；
    形态与 test_cutover_rehearsal / test_production_evidence_gap 的同名
    fixture 一致）。"""
    return {
        "ci-main.json": {
            "step": "ci-main",
            "run_id": 33844236243,
            "merge_commit": "38d90a0166dec73eb01e2263cacce7b7c984fec7",
            "conclusion": "success",
        },
        "release-check.json": {
            "step": "release-check",
            "all_green": True,
            "total": 10,
            "passed": 10,
            "failed_ids": [],
        },
        "search-smoke.json": {"step": "search-smoke", "executed": True, "result": "pass"},
        "cloud-voice-smoke.json": {
            "step": "cloud-voice-smoke",
            "executed": True,
            "result": "pass",
        },
        "llm-smoke.json": {"step": "llm-smoke", "executed": True, "result": "pass"},
        "legacy-papers.json": {
            "step": "legacy-papers",
            "pending_count": 0,
            "batches": [{"executed": True}],
        },
        "draft-ownership.json": {
            "step": "draft-ownership",
            "pending_count": 0,
            "batches": [{"executed": True}],
        },
        "preflight-pre-migration.json": {
            "step": "preflight-pre-migration",
            "phase": "pre-migration",
            "summary": {"pass": 3, "pending": 2, "fail": 0, "not_configured": 1},
        },
        "backup-restore.json": {
            "step": "backup-restore",
            "schema_version": "aios-backup-v1",
            "created_at": "2026-09-04T08:00:00+00:00",
            "restore_drill": {"verified": True, "inserted_rows": 1234},
        },
        "preflight-post-migration.json": {
            "step": "preflight-post-migration",
            "phase": "post-migration",
            "summary": {"pass": 6, "pending": 0, "fail": 0, "not_configured": 0},
        },
        "audit-chain-verify.json": {
            "step": "audit-chain-verify",
            "valid": True,
            "entries": 4210,
        },
        "audit-chain-anchor.json": {
            "step": "audit-chain-anchor",
            "anchor": {"status": "up-to-date", "anchors": 2},
            "worm": {"archived": True},
        },
    }


def _approval_payload(directory: Path) -> dict:
    """按当前证据文件字节计算 sha256 的完整审批记录（rehearsal 形态）。"""
    step_evidence = {}
    for spec in cr.STEPS:
        if spec.step_id == "cutover-approval":
            continue
        raw = (directory / spec.evidence_file).read_bytes()
        step_evidence[spec.step_id] = hashlib.sha256(raw).hexdigest()
    return {
        "step": "cutover-approval",
        "schema_version": 1,
        "approved_at": "2026-09-04T10:00:00+00:00",
        "note": "切换窗口/回滚/观察期确认",
        "window": {"start": "2026-09-04T12:00:00+00:00", "end": "2026-09-04T15:00:00+00:00"},
        "rollback_plan": "backup restore + db-rollback",
        "observation": "观察 1 小时关键指标",
        "approved_by": "ops-lead",
        "step_evidence": step_evidence,
        "supporting_evidence": {},
    }


def _gap_passing_dir(tmp_path: Path, name: str = "evidence") -> Path:
    """rehearsal 13 步全 pass（gap 四类 pass）但 release-readiness 十门
    未齐（缺 ci/release-check 等 readiness 门形态与 release-approval）"""
    directory = _evidence_dir(tmp_path, name)
    for filename, payload in _passing_steps_evidence().items():
        _write_json(directory, filename, payload)
    _write_json(directory, "cutover-approval.json", _approval_payload(directory))
    return directory


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        str(entry.relative_to(directory)): entry.read_bytes()
        for entry in sorted(directory.rglob("*"))
        if entry.is_file()
    }


def _gap_statuses(report: dict) -> dict[str, str]:
    return {item["category"]: item["status"] for item in report["gap"]["categories"]}


# --- 1. 确定性 -------------------------------------------------------------------


def test_deterministic_json_and_markdown(tmp_path) -> None:
    """同输入两次构建：JSON 与 Markdown 逐字一致（固定 clock + 显式 HEAD）。"""
    directory = _evidence_dir(tmp_path)
    first, first_exit = _build(directory)
    second, second_exit = _build(directory)
    assert first_exit == second_exit == 1
    assert json.dumps(first, ensure_ascii=False) == json.dumps(
        second, ensure_ascii=False
    )
    assert format_closure_markdown(first) == format_closure_markdown(second)


# --- 2. git HEAD -----------------------------------------------------------------


def test_git_head_explicit_normalized_and_no_subprocess(tmp_path, monkeypatch) -> None:
    """显式 HEAD：大写规范化为小写、source=explicit、零子进程调用。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        rcm.subprocess,
        "run",
        lambda *a, **k: calls.append(a) or subprocess.CompletedProcess([], 1),
    )
    report, _ = _build(_evidence_dir(tmp_path), git_head=_GIT_HEAD.upper())
    assert report["git"] == {
        "head": _GIT_HEAD,
        "source": "explicit",
        "discovery_command": rcm.GIT_DISCOVERY_COMMAND,
    }
    assert calls == []


@pytest.mark.parametrize(
    "bad_head",
    ["zz" * 20, "abc", "ab" * 21, "ab" * 32 + "g", ""],
)
def test_git_head_explicit_invalid_rejected(tmp_path, bad_head) -> None:
    with pytest.raises(ClosureManifestInputError):
        _build(_evidence_dir(tmp_path), git_head=bad_head)


def test_git_head_discovered(tmp_path, monkeypatch) -> None:
    """缺省安全发现：固定 argv、无 shell、超时上限、成功取 HEAD。"""
    recorded: dict = {}

    def _fake_run(argv, **kwargs):
        recorded["argv"] = argv
        recorded["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            argv, 0, stdout=_GIT_HEAD + "\n", stderr=""
        )

    monkeypatch.setattr(rcm.subprocess, "run", _fake_run)
    report, _ = _build(_evidence_dir(tmp_path), git_head=None)
    assert report["git"]["head"] == _GIT_HEAD
    assert report["git"]["source"] == "discovered"
    assert recorded["argv"] == ["git", "rev-parse", "HEAD"]
    assert recorded["kwargs"]["shell"] is False
    assert recorded["kwargs"]["timeout"] == rcm._GIT_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    "mode",
    ["nonzero", "oserror", "garbage"],
)
def test_git_head_discovery_failure_fail_closed(
    tmp_path, monkeypatch, mode
) -> None:
    """发现问题一律 fail-closed，并提示改用 --git-head。"""

    def _fake_run(argv, **kwargs):
        if mode == "nonzero":
            return subprocess.CompletedProcess(argv, 128, stdout="", stderr="err")
        if mode == "oserror":
            raise OSError("no git")
        return subprocess.CompletedProcess(argv, 0, stdout="not-a-sha\n", stderr="")

    monkeypatch.setattr(rcm.subprocess, "run", _fake_run)
    with pytest.raises(ClosureManifestInputError, match="--git-head"):
        _build(_evidence_dir(tmp_path), git_head=None)


# --- 3. 证据清单哈希 --------------------------------------------------------------


def test_evidence_inventory_hashes_and_order(tmp_path) -> None:
    """相对 posix 名排序、字节数、SHA-256 与 hashlib 独立计算一致。"""
    directory = _evidence_dir(tmp_path)
    (directory / "b.json").write_bytes(b'{"b":1}')
    (directory / "a.json").write_bytes(b"xx")
    sub = directory / "sub"
    sub.mkdir()
    (sub / "c.json").write_bytes(b'{"c":3}')
    report, _ = _build(directory)
    files = report["evidence"]["files"]
    assert [item["name"] for item in files] == [
        "a.json",
        "b.json",
        "sub/c.json",
    ]
    expected_hashes = {
        "a.json": hashlib.sha256(b"xx").hexdigest(),
        "b.json": hashlib.sha256(b'{"b":1}').hexdigest(),
        "sub/c.json": hashlib.sha256(b'{"c":3}').hexdigest(),
    }
    for item in files:
        assert item["sha256"] == expected_hashes[item["name"]]
        assert item["size_bytes"] == (directory / item["name"]).stat().st_size
    assert report["evidence"]["file_count"] == 3
    assert report["evidence"]["total_bytes"] == 2 + 7 + 7


# --- 4. 只消费既有评估器 -----------------------------------------------------------


def test_readiness_and_gap_subsets_match_independent_runs(tmp_path) -> None:
    """子集与独立运行的既有聚合器结论逐字段一致（复用而非重复实现）。"""
    from app.ops.production_evidence_gap import build_production_evidence_gap
    from app.ops.release_readiness import run_release_readiness

    directory = _evidence_dir(tmp_path)
    report, exit_code = _build(directory)
    readiness = run_release_readiness(directory)
    gap_report, _ = build_production_evidence_gap(directory)
    assert report["readiness"] == {
        "tool": readiness["tool"],
        "release_ready": readiness["release_ready"],
        "summary": readiness["summary"],
        "not_pass_required": readiness["not_pass_required"],
        "not_pass_optional": readiness["not_pass_optional"],
        "exit_code": readiness["exit_code"],
    }
    assert report["gap"] == {
        "tool": gap_report["tool"],
        "overall_status": gap_report["overall_status"],
        "categories": [
            {"category": item["category"], "status": item["status"]}
            for item in gap_report["categories"]
        ],
        "summary": gap_report["summary"],
        "exit_code": gap_report["exit_code"],
    }
    # 空目录：十门全缺 + 四类全 not_executed -> 诚实合取 false / exit 1
    assert readiness["release_ready"] is False
    assert gap_report["overall_status"] != "pass"
    assert report["production_ready"] is False
    assert exit_code == 1
    blockers = "\n".join(report["blockers"])
    for gate in readiness["not_pass_required"]:
        assert gate in blockers
    for category, status in _gap_statuses(report).items():
        assert f"{category}={status}" in blockers


def test_gap_passing_but_readiness_not_ready_still_false(tmp_path) -> None:
    """gap 四类全 pass 而 readiness 未 ready：合取单边不放行。"""
    directory = _gap_passing_dir(tmp_path)
    report, exit_code = _build(directory)
    assert set(_gap_statuses(report).values()) == {"pass"}
    assert report["gap"]["overall_status"] == "pass"
    assert report["readiness"]["release_ready"] is False
    assert report["production_ready"] is False
    assert exit_code == 1
    assert report["blockers"], "readiness 未 ready 必须产生 blockers"


def test_evidence_files_bytes_unchanged_after_run(tmp_path) -> None:
    """只读：证据文件字节（含子目录）在运行前后不变。"""
    directory = _gap_passing_dir(tmp_path)
    before = _snapshot(directory)
    _build(directory)
    assert _snapshot(directory) == before


# --- 5. 诚实合取（stub 只测聚合逻辑，不发明审批） ----------------------------------


def _readiness_stub(release_ready: bool) -> dict:
    not_pass = [] if release_ready else ["provider-smoke"]
    return {
        "tool": "release-readiness",
        "release_ready": release_ready,
        "summary": {"pass": 9 if release_ready else 8, "fail": 0},
        "not_pass_required": not_pass,
        "not_pass_optional": [],
        "exit_code": 0 if release_ready else 1,
    }


def _gap_stub(overall: str) -> tuple[dict, int]:
    status_by = {
        "pass": ("pass",) * 4,
        "blocked": ("blocked", "pass", "pass", "pass"),
    }
    categories = [
        {"category": name, "status": status}
        for name, status in zip(
            ("governance", "audit-chain", "provider-smoke", "cutover-approval"),
            status_by[overall],
            strict=True,
        )
    ]
    report = {
        "tool": "production-evidence-gap",
        "overall_status": overall,
        "categories": categories,
        "summary": {"pass": 4 if overall == "pass" else 3},
        "exit_code": 0 if overall == "pass" else 1,
    }
    return report, report["exit_code"]


def test_conjunction_true_only_when_both_pass(tmp_path, monkeypatch) -> None:
    """两侧全 pass 才 production_ready=true / exit 0 / blockers 空。"""
    directory = _evidence_dir(tmp_path)
    monkeypatch.setattr(rcm, "run_release_readiness", lambda root: _readiness_stub(True))
    monkeypatch.setattr(
        rcm, "build_production_evidence_gap", lambda root: _gap_stub("pass")
    )
    report, exit_code = _build(directory)
    assert report["production_ready"] is True
    assert report["blockers"] == []
    assert exit_code == 0
    markdown = format_closure_markdown(report)
    assert "production_ready=true" in markdown


def test_conjunction_false_when_either_side_fails(tmp_path, monkeypatch) -> None:
    """单边未 pass（readiness true + gap blocked / 反之）均 false / exit 1。"""
    directory = _evidence_dir(tmp_path)
    monkeypatch.setattr(rcm, "run_release_readiness", lambda root: _readiness_stub(True))
    monkeypatch.setattr(
        rcm, "build_production_evidence_gap", lambda root: _gap_stub("blocked")
    )
    report, exit_code = _build(directory)
    assert report["production_ready"] is False
    assert exit_code == 1
    assert report["blockers"]

    monkeypatch.setattr(
        rcm, "run_release_readiness", lambda root: _readiness_stub(False)
    )
    monkeypatch.setattr(
        rcm, "build_production_evidence_gap", lambda root: _gap_stub("pass")
    )
    report, exit_code = _build(directory)
    assert report["production_ready"] is False
    assert exit_code == 1


# --- 6. 零绝对路径与零敏感 ---------------------------------------------------------


def test_no_absolute_paths_in_json_or_markdown(tmp_path) -> None:
    """JSON+Markdown 不含证据目录/当前目录字面量、盘符/UNC/POSIX 前缀。"""
    directory = _gap_passing_dir(tmp_path)
    report, _ = _build(directory)
    text = (
        json.dumps(report, ensure_ascii=False)
        + "\n"
        + format_closure_markdown(report)
    )
    for candidate in (
        str(directory),
        directory.as_posix(),
        str(Path(directory).resolve()),
        str(Path.cwd()),
        Path.cwd().as_posix(),
    ):
        assert candidate not in text
    import re as _re

    assert not _re.search(r"[A-Za-z]:[\\/]", text), "不得含盘符路径形态"
    assert "\\\\" not in text, "不得含 UNC 路径形态"
    for hint in ("/home/", "/Users/", "/tmp/", "/root/", "/var/", "/mnt/"):
        assert hint not in text


def test_sensitive_markers_never_leak(tmp_path) -> None:
    """证据值内嵌生产 marker：任何输出表面零泄漏（内容只进哈希不进文本）。"""
    directory = _evidence_dir(tmp_path)
    _write_json(
        directory,
        "search-smoke.json",
        {
            "step": "search-smoke",
            "executed": True,
            "result": "pass",
            "remark": f"{PASSWORD_MARKER} {TOKEN_MARKER}",
        },
    )
    report, _ = _build(directory)
    text = json.dumps(report, ensure_ascii=False) + format_closure_markdown(report)
    for marker in (PAPER_ID_MARKER, PASSWORD_MARKER, API_KEY_MARKER, TOKEN_MARKER):
        assert marker not in text
    # 文件本身仍在有界清单里（名字/字节/哈希，不含内容）
    assert any(
        item["name"] == "search-smoke.json"
        for item in report["evidence"]["files"]
    )


# --- 7. 输出护栏（fail-closed） ----------------------------------------------------


def test_output_inside_evidence_dir_rejected(tmp_path) -> None:
    directory = _evidence_dir(tmp_path)
    with pytest.raises(ClosureManifestInputError):
        _build(directory, output_json=directory / "out.json")
    with pytest.raises(ClosureManifestInputError):
        _build(directory, output_md=directory / "out.md")
    # ``..`` 折叠形态不构成绕过
    artifacts = _artifacts(tmp_path)
    sneaky = artifacts / ".." / "evidence" / "out.json"
    with pytest.raises(ClosureManifestInputError):
        _build(directory, output_json=sneaky)


def test_output_equal_to_evidence_dir_rejected(tmp_path) -> None:
    directory = _evidence_dir(tmp_path)
    with pytest.raises(ClosureManifestInputError):
        _build(directory, output_json=directory)


def test_output_symlink_rejected(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    real = artifacts / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = artifacts / "link.json"
    try:
        os.symlink(real, link)
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    with pytest.raises(ClosureManifestInputError):
        _build(_evidence_dir(tmp_path), output_json=link)


def test_output_symlink_parent_component_rejected(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    real_dir = artifacts / "real-dir"
    real_dir.mkdir()
    link_dir = artifacts / "link-dir"
    try:
        os.symlink(real_dir, link_dir, target_is_directory=True)
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    with pytest.raises(ClosureManifestInputError):
        _build(_evidence_dir(tmp_path), output_json=link_dir / "out.json")


def test_output_existing_directory_rejected(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    target = artifacts / "as-dir"
    target.mkdir()
    with pytest.raises(ClosureManifestInputError):
        _build(_evidence_dir(tmp_path), output_json=target)


def test_output_same_path_for_json_and_md_rejected(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    target = artifacts / "same.json"
    with pytest.raises(ClosureManifestInputError):
        _build(_evidence_dir(tmp_path), output_json=target, output_md=target)


def test_output_guard_precedes_evidence_read(tmp_path, monkeypatch) -> None:
    """输出冲突形态下既有评估器零调用（护栏先于任何证据内容读取）。"""
    calls: list[Path] = []
    monkeypatch.setattr(
        rcm,
        "run_release_readiness",
        lambda root: calls.append(root) or _readiness_stub(True),
    )
    directory = _evidence_dir(tmp_path)
    with pytest.raises(ClosureManifestInputError):
        _build(directory, output_json=directory / "out.json")
    assert calls == []


# --- 8. CLI -----------------------------------------------------------------------


def test_cli_registered_and_has_no_execute_flag(tmp_path, monkeypatch) -> None:
    """子命令注册、无 --yes 执行形态（argparse 未知旗标 exit 2）。"""
    assert hasattr(cli_module, "_run_release_closure_manifest")
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    assert '"release-closure-manifest"' in source
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "release-closure-manifest",
            "--evidence-dir",
            str(_evidence_dir(tmp_path)),
            "--yes",
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 2


def test_main_dispatch_release_closure_manifest(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "release-closure-manifest",
            "--evidence-dir",
            str(_evidence_dir(tmp_path)),
            "--git-head",
            _GIT_HEAD,
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "production_ready=false" in out


def test_cli_missing_evidence_dir_arg_exits_2(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["cli", "release-closure-manifest"])
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 2


def test_cli_output_outside_artifacts_rejected(tmp_path, capsys) -> None:
    directory = _evidence_dir(tmp_path)
    target = tmp_path / "closure.json"
    capsys.readouterr()
    assert _cli(directory, output_json=target) == 2
    captured = capsys.readouterr()
    assert not target.exists()
    assert "artifacts" in captured.out
    assert "RESULT" not in captured.out


def test_cli_writes_json_and_md_atomically(tmp_path, capsys) -> None:
    """双输出原子落盘：--json stdout 与文件逐字一致、md 与渲染一致、
    无 .tmp 残留、人读提示走 stderr。"""
    artifacts = _artifacts(tmp_path)
    directory = _gap_passing_dir(tmp_path)
    json_target = artifacts / "closure.json"
    md_target = artifacts / "closure.md"
    capsys.readouterr()
    assert (
        _cli(directory, as_json=True, output_json=json_target, output_md=md_target)
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out.startswith("{"), "stdout 必须是纯 JSON"
    assert "报告已写入" in captured.err
    report = json.loads(captured.out)
    assert report == json.loads(json_target.read_text(encoding="utf-8"))
    markdown = md_target.read_text(encoding="utf-8")
    assert markdown == format_closure_markdown(report)
    assert markdown.startswith("# 生产收口 manifest")
    assert not list(artifacts.glob("*.tmp"))


def test_cli_write_failure_keeps_old_file_and_prints_no_verdict(
    tmp_path, monkeypatch, capsys
) -> None:
    """写入失败：旧文件字节原样、exit 2、不打印收口结论、无 .tmp。"""
    artifacts = _artifacts(tmp_path)
    directory = _evidence_dir(tmp_path)
    json_target = artifacts / "closure.json"
    json_target.write_text("OLD-CONTENT", encoding="utf-8")

    def _boom(path, text):
        raise OSError("disk full")

    monkeypatch.setattr(cli_module, "_write_report_atomic", _boom)
    capsys.readouterr()
    assert _cli(directory, output_json=json_target) == 2
    captured = capsys.readouterr()
    assert json_target.read_text(encoding="utf-8") == "OLD-CONTENT"
    assert not list(artifacts.glob("*.tmp"))
    assert "RESULT" not in captured.out, "写入失败不得打印收口结论"
    assert "写入失败" in captured.out


# --- 9. 无效/截断输入 fail-closed ---------------------------------------------------


def test_truncated_json_evidence_maps_to_blockers(tmp_path) -> None:
    """截断 JSON 证据：对应门/类进 blockers（exit 1、不崩溃、不误 pass）。"""
    directory = _evidence_dir(tmp_path)
    (directory / "search-smoke.json").write_text('{"step": "search-smok', encoding="utf-8")
    report, exit_code = _build(directory)
    assert exit_code == 1
    assert report["production_ready"] is False
    blockers = "\n".join(report["blockers"])
    assert "provider-smoke" in blockers or "search-smoke" in blockers


def test_missing_evidence_dir_rejected(tmp_path) -> None:
    with pytest.raises(EvidenceInputError):
        _build(tmp_path / "no-such-dir")


def test_missing_evidence_dir_cli_exits_2(tmp_path, capsys) -> None:
    capsys.readouterr()
    assert _cli(tmp_path / "no-such-dir") == 2
    assert "未产生 manifest" in capsys.readouterr().out


def test_evidence_dir_with_symlink_rejected(tmp_path) -> None:
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    directory = _evidence_dir(tmp_path)
    try:
        os.symlink(real, directory / "link.json")
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    with pytest.raises(EvidenceInputError):
        _build(directory)


# --- 10. 源码守卫 ------------------------------------------------------------------


def test_module_source_guard() -> None:
    """源码级守卫：零环境变量/DB/网络；subprocess 恰一次且 argv 固定。"""
    source = Path(rcm.__file__).read_text(encoding="utf-8")
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
    ):
        assert banned not in code, f"模块不得引用 {banned}"
    assert code.count("subprocess.run") == 1, "子进程调用恰一次（git HEAD 发现）"
    assert '["git", "rev-parse", "HEAD"]' in code
    cli_source = Path(cli_module.__file__).read_text(encoding="utf-8")
    dispatch_block = cli_source.split('"release-closure-manifest"', 1)
    assert len(dispatch_block) >= 2, "CLI 必须注册 release-closure-manifest"
