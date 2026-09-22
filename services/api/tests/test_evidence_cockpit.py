"""M14-91 release-evidence-cockpit：跨切片证据驾驶舱契约测试。

覆盖矩阵（任务验收面）：
1. 编排 happy path：显式来源登记（路径/字节/SHA-256）+ 字节一致一次性
   staging（写后重读断言）+ 复用 run_release_readiness 门语义 + 来源零改动
   （运行前后字节/哈希不变）；
2. stale 判定：内嵌声明（ci-main merge_commit）与旗标声明（--gate-declared-head）
   对比 current HEAD → current/stale；两处声明冲突 fail-closed；非 40-hex
   current-head 拒绝；
3. 分类口径：code-bound（ci-main/release-check）undeclared 计入 blocker，
   production-state undeclared 只如实呈现不计 blocker；未 stage 门
   not-staged；
4. blocked/missing：staged 门非 pass（CI failure）→ blocker/exit 1；只 stage
   部分门 → 其余门 evaluator 侧 missing、绑定侧 not-staged；
5. 审批硬边界：release-approval 作为输入传入即 fail-closed（零 staging
   产出）；报告 approval.accepted 恒 false、production_ready 恒 false、
   staging 目录永不出现 release-approval.json；
6. malformed/护栏：source 非 JSON 对象、gate 自声明错位、staging 目录已
   存在、source 是 symlink（无特权环境 skip）一律 CockpitInputError 且零
   staging 产出；
7. 报告落盘与 CLI --output 回归：write_cockpit_report 全路径（写入/回读/
   已存在拒绝/symlink 目标（含 dangling）拒绝且链接不被原地替换/replace
   失败无 .tmp 残留）；symlink 祖先（常规与 dangling）拒绝且零意外父目录
   创建；CLI --output 端到端（--json 模式 stdout 纯 JSON 可解析、「报告已
   写入」提示走 stderr、父目录预创建）；CLI 报告写入失败 → 本次 staging
   完整移除后 exit 2；清理本身失败 → staging 残留如实说明（不虚称零
   staging 产出）。

全部测试只用临时目录与本地文件，零网络/零 DB/零环境变量。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from app.ops import cli as cli_module
from app.ops.evidence_cockpit import (
    CODE_BOUND_GATES,
    FORBIDDEN_GATES,
    CockpitInputError,
    build_evidence_cockpit,
    format_cockpit_summary,
    write_cockpit_report,
)

HEAD = "a" * 40
OTHER_HEAD = "b" * 40


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _mk_sources(tmp_path: Path) -> Path:
    directory = tmp_path / "sources"
    directory.mkdir(exist_ok=True)
    return directory


def _ci_main_payload(commit: str = HEAD, conclusion: str = "success") -> dict:
    return {
        "gate": "ci-main",
        "run_id": 35642190403,
        "merge_commit": commit,
        "conclusion": conclusion,
    }


def _release_check_payload() -> dict:
    return {
        "gate": "release-check",
        "all_green": True,
        "total": 10,
        "passed": 10,
        "failed_ids": [],
    }


def _preflight_payload() -> dict:
    return {
        "gate": "production-preflight",
        "phase": "post-migration",
        "summary": {"pass": 5, "pending": 0, "fail": 0, "not_configured": 0},
    }


def _full_required_payloads() -> dict[str, dict]:
    """九个 required 门（除 release-approval）的 pass 形态 payload。"""
    return {
        "ci-main": _ci_main_payload(),
        "release-check": _release_check_payload(),
        "production-preflight": _preflight_payload(),
        "backup-restore": {
            "gate": "backup-restore",
            "schema_version": "aios-backup-v1",
            "manifest_sha256": "ab" * 32,
            "created_at": "2026-09-04T08:00:00+00:00",
            "restore_drill": {"verified": True, "inserted_rows": 1234},
        },
        "audit-chain-anchor": {
            "gate": "audit-chain-anchor",
            "chain": {"valid": True, "entries": 0},
            "anchor": {"status": "up-to-date", "anchors": 1},
            "worm": {"archived": True},
        },
        "legacy-papers": {
            "gate": "legacy-papers", "pending_count": 0, "batches": [],
        },
        "draft-ownership": {
            "gate": "draft-ownership", "pending_count": 0, "batches": [],
        },
        "long-soak": {
            "schema_version": 1,
            "audit_schema_version": 2,
            "gate": "long-soak",
            "tool": "tools/ops/soak_stability_audit.py",
            "input": {"sha256": "cd" * 32, "byte_size": 571084},
            "row_count": 97,
            "analyzed_row_count": 97,
            "omitted_older_count": 0,
            "settings": {
                "window_minutes": 1440,
                "expected_interval_minutes": 15,
                "max_gap_minutes": 20,
                "retention": 500,
            },
            "anchor_collected_at": "2026-09-20T02:15:02Z",
            "window_start_collected_at": "2026-09-19T02:15:02Z",
            "selected_row_count": 97,
            "window_status_counts": {"ok": 97, "warn": 0, "critical": 0},
            "window_non_ok_count": 0,
            "max_observed_gap_minutes": 15.0,
            "selected_span_minutes": 1440.0,
            "classification": "pass",
            "reasons": [],
        },
        "provider-smoke": {
            "gate": "provider-smoke",
            "topology": {"voice_mode": "local"},
            "providers": {
                "voice": {"executed": True, "result": "pass",
                          "evidence_step": "local-voice-smoke"},
                "search": {"executed": True, "result": "pass",
                           "evidence_step": "search-smoke"},
                "llm": {"executed": True, "result": "pass",
                        "evidence_step": "llm-smoke"},
            },
        },
    }


def _write_full_required(sources_dir: Path) -> dict[str, Path]:
    return {gate: _write_json(sources_dir / f"{gate}.json", payload)
            for gate, payload in _full_required_payloads().items()}


def _run(tmp_path: Path, sources: dict[str, Path], **kwargs) -> dict:
    staging = kwargs.pop("staging_dir", None) or (tmp_path / "staging")
    return build_evidence_cockpit(
        sources, current_head=kwargs.pop("current_head", HEAD),
        staging_dir=staging, **kwargs)


def test_stages_byte_identical_and_cockpit_ready(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    sources = _write_full_required(sources_dir)
    staging = tmp_path / "staging"

    report = _run(tmp_path, sources, declared_heads={"release-check": HEAD})

    # 全部 required 门（除 approval）staged 且 pass → cockpit_ready
    # （turn-tls optional 未 stage 不阻断；approval 按策略永不接受）
    assert report["cockpit_ready"] is True
    assert report["exit_code"] == 0
    assert report["cockpit_blockers"] == []
    assert report["required_not_staged"] == []
    for gate, path in sources.items():
        assert (staging / f"{gate}.json").read_bytes() == path.read_bytes()
    staged_names = sorted(p.name for p in staging.iterdir())
    assert staged_names == sorted(f"{gate}.json" for gate in sources)
    gate_status = {e["gate"]: e["status"] for e in report["readiness"]["gates"]}
    assert gate_status["ci-main"] == "pass"
    assert gate_status["provider-smoke"] == "pass"
    # release-approval 未 stage 但按策略非 blocker（显式呈现）
    assert report["gate_bindings"]["release-approval"]["staged"] is False
    assert "release-approval" not in report["required_not_staged"]
    summary = format_cockpit_summary(report)
    assert "按策略永不接受，非 blocker" in summary
    assert "optional：本机/LAN 范围不阻断" in summary


def test_source_registration_and_immutability(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json", _ci_main_payload())
    before = ci.read_bytes()
    before_hash = hashlib.sha256(before).hexdigest()

    report = _run(tmp_path, {"ci-main": ci})

    record = report["gate_bindings"]["ci-main"]["source"]
    assert record["path"] == str(ci)
    assert record["bytes"] == len(before)
    assert record["sha256"] == before_hash
    assert ci.read_bytes() == before  # 来源零改动（字节不变）
    assert hashlib.sha256(ci.read_bytes()).hexdigest() == before_hash


def test_stale_embedded_commit_blocks(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json", _ci_main_payload(commit=OTHER_HEAD))

    report = _run(tmp_path, {"ci-main": ci})

    binding = report["gate_bindings"]["ci-main"]
    assert binding["stale_status"] == "stale"
    assert binding["declared_head"] == OTHER_HEAD
    assert binding["declared_head_origin"] == "embedded"
    assert "ci-main:stale" in report["cockpit_blockers"]
    assert report["cockpit_ready"] is False
    assert report["exit_code"] == 1


def test_stale_flagged_declared_head_blocks(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    rc = _write_json(sources_dir / "rc.json", _release_check_payload())

    report = _run(tmp_path, {"release-check": rc},
                  declared_heads={"release-check": OTHER_HEAD})

    binding = report["gate_bindings"]["release-check"]
    assert binding["stale_status"] == "stale"
    assert binding["declared_head_origin"] == "flag"
    assert "release-check:stale" in report["cockpit_blockers"]
    assert report["exit_code"] == 1


def test_embedded_flag_conflict_rejected(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json", _ci_main_payload(commit=HEAD))

    with pytest.raises(CockpitInputError, match="不一致"):
        _run(tmp_path, {"ci-main": ci},
             declared_heads={"ci-main": OTHER_HEAD})
    assert not (tmp_path / "staging").exists()  # 零 staging 产出


def test_code_bound_undeclared_blocks(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    rc = _write_json(sources_dir / "rc.json", _release_check_payload())

    report = _run(tmp_path, {"release-check": rc})

    binding = report["gate_bindings"]["release-check"]
    assert binding["stale_status"] == "undeclared"
    assert "release-check:undeclared-code-bound" in report["cockpit_blockers"]
    assert report["exit_code"] == 1


def test_production_state_undeclared_presented_not_blocking(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    sources = _write_full_required(sources_dir)

    report = _run(tmp_path, sources, declared_heads={"release-check": HEAD})

    # production-state 门无 commit 声明 → undeclared 只如实呈现，
    # 不进入 blockers（全 required 已 stage，ready 不受影响）
    binding = report["gate_bindings"]["production-preflight"]
    assert binding["binding_class"] == "production-state"
    assert binding["stale_status"] == "undeclared"
    assert not any(b.startswith("production-preflight:")
                   for b in report["cockpit_blockers"])
    assert report["cockpit_ready"] is True
    assert report["exit_code"] == 0


def test_required_gate_not_staged_blocks(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    sources = _write_full_required(sources_dir)
    del sources["backup-restore"]  # 少 stage 一个 required 门

    report = _run(tmp_path, sources, declared_heads={"release-check": HEAD})

    # cockpit_ready 不是 staged 子集干净：required 门未 stage 即阻断
    assert "backup-restore:not-staged-required" in report["cockpit_blockers"]
    assert report["required_not_staged"] == ["backup-restore"]
    assert report["cockpit_ready"] is False
    assert report["exit_code"] == 1


def test_missing_gates_not_staged(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json", _ci_main_payload())

    report = _run(tmp_path, {"ci-main": ci})

    assert report["gate_bindings"]["backup-restore"]["staged"] is False
    assert report["gate_bindings"]["backup-restore"]["stale_status"] == "not-staged"
    gate_status = {e["gate"]: e["status"] for e in report["readiness"]["gates"]}
    assert gate_status["backup-restore"] == "missing"
    # required 门未 stage → blocker（release-approval 例外；turn-tls optional）
    assert "backup-restore:not-staged-required" in report["cockpit_blockers"]
    assert "release-approval" not in report["required_not_staged"]
    assert "turn-tls" not in report["required_not_staged"]
    assert report["exit_code"] == 1


def test_single_read_parse_and_stage_share_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TOCTOU 回归：source 文件恰好被读一次——解析/校验/登记/staging
    全部消费同一次读的字节（中途替换文件不可能分裂解析与落盘版本）。"""
    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json", _ci_main_payload())
    reads: list[int] = []
    real_read = Path.read_bytes

    def spy(self: Path) -> bytes:
        data = real_read(self)
        if self == ci:
            reads.append(len(data))
        return data

    monkeypatch.setattr(Path, "read_bytes", spy)
    staging = tmp_path / "staging"
    report = _run(tmp_path, {"ci-main": ci}, staging_dir=staging)

    assert reads == [ci.stat().st_size]  # 恰好一次读
    assert (staging / "ci-main.json").read_bytes() == real_read(ci)
    assert report["gate_bindings"]["ci-main"]["source"]["bytes"] == reads[0]


def test_staging_io_failure_cleans_created_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """staging 创建后 IO 失败：本次新建目录（含已写入文件/.tmp 残留）
    被完整移除，调用以失败告终且不残留半成品现场。"""
    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json", _ci_main_payload())
    rc = _write_json(sources_dir / "rc.json", _release_check_payload())
    staging = tmp_path / "staging"

    from app.ops import evidence_cockpit as cockpit_module
    real_stage = cockpit_module._stage_bytes_atomic
    calls: list[str] = []

    def flaky(staging_dir: Path, name: str, data: bytes) -> None:
        calls.append(name)
        if len(calls) >= 2:  # 至少一次成功写入后注入失败
            raise OSError("injected staging IO failure")
        real_stage(staging_dir, name, data)

    monkeypatch.setattr(cockpit_module, "_stage_bytes_atomic", flaky)
    with pytest.raises(CockpitInputError):
        _run(tmp_path, {"ci-main": ci, "release-check": rc},
             staging_dir=staging)
    assert len(calls) >= 2  # 确有写入发生在失败前
    assert not staging.exists()  # 本次新建目录已被清理


def test_blocked_gate_reported(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json",
                     _ci_main_payload(conclusion="failure"))

    report = _run(tmp_path, {"ci-main": ci})

    gate_status = {e["gate"]: e["status"] for e in report["readiness"]["gates"]}
    assert gate_status["ci-main"] == "blocked"
    assert "ci-main:blocked" in report["cockpit_blockers"]
    assert report["exit_code"] == 1


def test_approval_refused_hard_boundary(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    approval = _write_json(sources_dir / "approval.json", {"gate": "release-approval"})

    with pytest.raises(CockpitInputError, match="release-approval"):
        _run(tmp_path, {"release-approval": approval})
    staging = tmp_path / "staging"
    assert not staging.exists()  # fail-closed：零 staging 产出


def test_report_never_claims_ready_or_approval(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json", _ci_main_payload())

    report = _run(tmp_path, {"ci-main": ci})

    assert report["production_ready"] is False
    assert report["approval"]["accepted"] is False
    assert report["readiness"]["release_ready"] is False  # approval 恒 missing
    assert not (tmp_path / "staging" / "release-approval.json").exists()
    summary = format_cockpit_summary(report)
    assert "production_ready 恒 false" in summary


def test_malformed_source_not_json_rejected(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    broken = sources_dir / "broken.json"
    broken.write_text("not json {{{", encoding="utf-8")

    with pytest.raises(CockpitInputError, match="不是合法 JSON"):
        _run(tmp_path, {"ci-main": broken})
    assert not (tmp_path / "staging").exists()


def test_malformed_source_gate_mismatch_rejected(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    misplaced = _write_json(sources_dir / "wrong.json", _ci_main_payload())

    with pytest.raises(CockpitInputError, match="错位"):
        _run(tmp_path, {"release-check": misplaced})
    assert not (tmp_path / "staging").exists()


def test_staging_dir_must_not_exist(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json", _ci_main_payload())
    staging = tmp_path / "staging"
    staging.mkdir()

    with pytest.raises(CockpitInputError, match="已存在"):
        _run(tmp_path, {"ci-main": ci}, staging_dir=staging)
    assert list(staging.iterdir()) == []  # 既有目录零写入


def test_current_head_must_be_hex40(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json", _ci_main_payload())

    with pytest.raises(CockpitInputError, match="current-head"):
        _run(tmp_path, {"ci-main": ci}, current_head="xyz")


def test_unknown_or_forbidden_gate_rejected(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json", _ci_main_payload())

    with pytest.raises(CockpitInputError, match="未知发布门"):
        _run(tmp_path, {"nonexistent-gate": ci})
    with pytest.raises(CockpitInputError, match="永不接受"):
        _run(tmp_path, {gate: ci for gate in FORBIDDEN_GATES})
    assert not (tmp_path / "staging").exists()


def test_anchor_companion_staged_byte_identical(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json", _ci_main_payload())
    companion = sources_dir / "audit-anchor.jsonl"
    companion.write_bytes(b"anchor-bytes\n")
    staging = tmp_path / "staging"

    report = _run(tmp_path, {"ci-main": ci}, anchor_companion=companion)

    staged = staging / "audit-anchor.jsonl"
    assert staged.read_bytes() == companion.read_bytes()
    assert report["anchor_companion"]["sha256"] == \
        hashlib.sha256(b"anchor-bytes\n").hexdigest()


def test_symlink_source_rejected(tmp_path: Path) -> None:
    sources_dir = _mk_sources(tmp_path)
    real = _write_json(sources_dir / "real.json", _ci_main_payload())
    link = tmp_path / "link.json"
    try:
        os.symlink(real, link)
    except OSError:
        pytest.skip("此环境无 symlink 特权")
    with pytest.raises(CockpitInputError):
        _run(tmp_path, {"ci-main": link})
    assert not (tmp_path / "staging").exists()


def test_classification_constants() -> None:
    assert FORBIDDEN_GATES == frozenset({"release-approval"})
    assert CODE_BOUND_GATES == frozenset({"ci-main", "release-check"})


def test_cli_registration_and_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json", _ci_main_payload())
    staging = tmp_path / "cli-staging"
    monkeypatch.setattr(
        "sys.argv", [
            "aios-backup", "evidence-cockpit",
            "--gate-source", f"ci-main={ci}",
            "--current-head", HEAD,
            "--staging-dir", str(staging),
            "--json",
        ])
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    # 单门 staged：其余 required 门 not-staged → blocker → exit 1（如实）
    assert excinfo.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["tool"] == "evidence-cockpit"
    assert payload["cockpit_ready"] is False
    assert "ci-main" not in payload["required_not_staged"]
    assert "long-soak:not-staged-required" in payload["cockpit_blockers"]
    assert (staging / "ci-main.json").read_bytes() == ci.read_bytes()
    # 无 --yes 执行形态（纯汇总器，argparse 拒绝未知参数）
    monkeypatch.setattr(
        "sys.argv", [
            "aios-backup", "evidence-cockpit",
            "--gate-source", f"ci-main={ci}",
            "--current-head", HEAD, "--staging-dir", str(tmp_path / "s2"),
            "--yes",
        ])
    with pytest.raises(SystemExit):
        cli_module.main()


# --- 报告落盘与 CLI --output 回归（PR #178 remediation） --------------------


def test_write_cockpit_report_roundtrip(tmp_path: Path) -> None:
    """write_cockpit_report 全路径：原子写入、回读一致、已存在拒绝覆盖。"""
    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json", _ci_main_payload())
    report = _run(tmp_path, {"ci-main": ci})
    out_dir = tmp_path / "artifacts"
    out_dir.mkdir()
    target = out_dir / "cockpit.json"

    write_cockpit_report(report, target)

    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["tool"] == "evidence-cockpit"
    assert written["exit_code"] == report["exit_code"]
    with pytest.raises(CockpitInputError, match="拒绝覆盖"):
        write_cockpit_report(report, target)
    assert list(out_dir.iterdir()) == [target]  # 无 .tmp 残留


def test_write_cockpit_report_rejects_symlink_target(tmp_path: Path) -> None:
    """目标自身是 symlink（常规与 dangling）即拒绝：不跟随、不原地替换
    链接（exists() 跟随链接会放行 dangling 形态——lstat 语义堵住）。"""
    report = {"tool": "evidence-cockpit"}
    out_dir = tmp_path / "artifacts"
    out_dir.mkdir()
    real = out_dir / "real.json"
    real.write_text("{}", encoding="utf-8")
    dangling = out_dir / "dangling.json"
    linked = out_dir / "linked.json"
    try:
        os.symlink(out_dir / "no-such-file", dangling)
        os.symlink(real, linked)
    except OSError:
        pytest.skip("此环境无 symlink 特权")
    with pytest.raises(CockpitInputError, match="symlink"):
        write_cockpit_report(report, dangling)
    with pytest.raises(CockpitInputError, match="symlink"):
        write_cockpit_report(report, linked)
    # 链接本身未被 replace 成常规文件；dangling 目标也未被隐式创建
    assert dangling.is_symlink()
    assert linked.is_symlink()
    assert not (out_dir / "no-such-file").exists()
    # 无 .tmp 残留（拒绝发生在 mkstemp 之前）
    assert sorted(p.name for p in out_dir.iterdir()) == [
        "dangling.json", "linked.json", "real.json"]


def test_write_cockpit_report_failure_leaves_no_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写入/fsync/replace 失败：tmp 先被删除再上抛原始错误——不留 .tmp
    残留、不产生半成品报告（对齐 cli._write_report_atomic 纪律）。"""
    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json", _ci_main_payload())
    report = _run(tmp_path, {"ci-main": ci})
    out_dir = tmp_path / "artifacts"
    out_dir.mkdir()
    target = out_dir / "cockpit.json"

    def broken_replace(src, dst):
        raise OSError("injected replace failure")

    monkeypatch.setattr("os.replace", broken_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        write_cockpit_report(report, target)
    assert not target.exists()
    assert list(out_dir.iterdir()) == []  # mkstemp 的 tmp 已清理


def test_symlink_ancestor_rejected_regular_and_dangling(
    tmp_path: Path
) -> None:
    """staging 路径祖先含 symlink（常规与 dangling）即拒绝，且拒绝发生在
    任何 mkdir 之前——dangling 祖先不再依赖 mkdir 的 OSError 兜底，也
    不产生中间父目录。"""
    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json", _ci_main_payload())
    base = tmp_path / "anc"
    base.mkdir()
    regular_dir = base / "real-dir"
    regular_dir.mkdir()
    dangling_link = base / "dangling-link"  # 指向不存在的目录
    regular_link = base / "regular-link"
    try:
        os.symlink(base / "no-such-dir", dangling_link)
        os.symlink(regular_dir, regular_link)
    except OSError:
        pytest.skip("此环境无 symlink 特权")
    for ancestor_link in (dangling_link, regular_link):
        staging = ancestor_link / "staging"
        with pytest.raises(CockpitInputError, match="路径祖先含 symlink"):
            _run(tmp_path, {"ci-main": ci}, staging_dir=staging)
    # dangling 指向位置未被 mkdir 隐式创建；常规链接目标目录零写入
    assert not (base / "no-such-dir").exists()
    assert list(regular_dir.iterdir()) == []


def test_cli_output_writes_report_json_stdout_pure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """CLI --output + --json 端到端：stdout 是纯 JSON（可解析、无提示
    混入）；「报告已写入」提示走 stderr；输出父目录预创建（兄弟子命令
    同款）；报告文件与 stdout JSON 同源。"""
    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json", _ci_main_payload())
    staging = tmp_path / "cli-staging"
    output = tmp_path / "artifacts" / "cockpit.json"  # 父目录不存在
    monkeypatch.setattr(
        "sys.argv", [
            "aios-backup", "evidence-cockpit",
            "--gate-source", f"ci-main={ci}",
            "--current-head", HEAD,
            "--staging-dir", str(staging),
            "--json", "--output", str(output),
        ])
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    # 单门 staged：其余 required not-staged → blocker → exit 1（如实）
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)  # stdout 纯 JSON：可解析（M2 回归）
    assert payload["tool"] == "evidence-cockpit"
    assert "报告已写入" not in captured.out
    assert "报告已写入" in captured.err  # 提示走 stderr（M2 回归）
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["tool"] == "evidence-cockpit"  # 父目录已预创建（L4 回归）
    assert (staging / "ci-main.json").read_bytes() == ci.read_bytes()


def test_cli_output_failure_removes_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """CLI 报告写入失败 → 本次新建 staging 完整移除 + exit 2 + 如实说明
    （报告文件不产生）。"""
    from app.ops import evidence_cockpit as cockpit_module

    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json", _ci_main_payload())
    staging = tmp_path / "cli-staging"
    output = tmp_path / "artifacts" / "cockpit.json"

    def broken_write(report, out):
        raise OSError("injected report write failure")

    monkeypatch.setattr(cockpit_module, "write_cockpit_report", broken_write)
    monkeypatch.setattr(
        "sys.argv", [
            "aios-backup", "evidence-cockpit",
            "--gate-source", f"ci-main={ci}",
            "--current-head", HEAD,
            "--staging-dir", str(staging),
            "--output", str(output),
        ])
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 2
    assert not staging.exists()  # 写入失败 → staging 已完整移除
    assert not output.exists()  # 报告文件不产生
    captured = capsys.readouterr()
    assert "已移除本次 staging" in captured.out
    assert "报告写入失败" in captured.out


def test_cli_output_failure_cleanup_failure_reports_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """报告写入失败且 staging 清理本身失败：目录残留如实说明——绝不
    虚称「零 staging」（L3 回归；对应 _remove_created_staging 折叠分支）。"""
    import shutil as shutil_module

    from app.ops import evidence_cockpit as cockpit_module

    sources_dir = _mk_sources(tmp_path)
    ci = _write_json(sources_dir / "ci.json", _ci_main_payload())
    staging = tmp_path / "cli-staging"
    output = tmp_path / "artifacts" / "cockpit.json"

    def broken_write(report, out):
        raise OSError("injected report write failure")

    def broken_rmtree(path, *args, **kwargs):
        raise OSError("injected cleanup failure")

    monkeypatch.setattr(cockpit_module, "write_cockpit_report", broken_write)
    monkeypatch.setattr(shutil_module, "rmtree", broken_rmtree)
    monkeypatch.setattr(
        "sys.argv", [
            "aios-backup", "evidence-cockpit",
            "--gate-source", f"ci-main={ci}",
            "--current-head", HEAD,
            "--staging-dir", str(staging),
            "--output", str(output),
        ])
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 2
    assert staging.exists()  # 清理失败：目录实际残留（如实）
    captured = capsys.readouterr()
    assert "清理失败" in captured.out
    assert str(staging) in captured.out  # 残留路径可见
    assert "零 staging" not in captured.out  # 绝不虚称零产出（L3 回归）
