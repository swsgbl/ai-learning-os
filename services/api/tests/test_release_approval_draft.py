"""M14-151 release-approval-draft：11 门 readiness 契约的审批 DRAFT 生成器。

fail-closed 边界覆盖矩阵：
1. 哈希绑定诚实：gate_evidence 只含当前实际存在的可绑定门（GATE_IDS -
   release-approval，含 optional turn-tls），每个 sha256 与文件字节独立
   重算一致；缺席门如实列入 gate_evidence_missing；required_coverage_gaps
   恰为「必需门（release-approval 除外）- 已 present」（optional turn-tls
   缺席不进 gaps，只进 missing——公网语音发布另行要求）；
2. 门状态只消费不重算：gate_statuses 与 run_release_readiness 逐门一致；
   DRAFT 任何位置不含 release_ready/production_ready（本工具永不作放行
   结论）；
3. DRAFT 语义：draft=true、notice 声明「改名只会被拒、必须从零组装」、
   manual_fields_required 列出全部人工必填字段、approval_file_present
   如实标注且不改动既有审批文件；
4. DRAFT 不可审批（改名防线）：底稿原样改名 release-approval.json =>
   malformed；补齐 gate 自声明与全部人工字段但保留底稿元数据 => 仍
   malformed（_eval_release_approval 对 APPROVAL_DRAFT_RESERVED_FIELDS
   fail-closed）；底稿输出的全部元数据键都被保留字段集登记（同步守卫）；
5. 只读与零敏感：两轮运行证据字节不变；敏感 marker（生产 ID/密码/token/
   key/URL 内嵌凭据）在全输出面零回显（load_problems 只报字段路径）；
   毒化环境变量不被读取；
6. CLI：子命令注册、无 --yes 形态、缺参 exit 2、--output 非 artifacts/
   temp 拒绝、输出名恰为 release-approval.json 一律拒绝（绝不创建或修改
   release-approval.json）、输出位于证据目录内拒绝、artifacts 内允许且
   原子写（无 .tmp 残留、文件与 stdout 一致）、写入失败不打印底稿正文、
   证据目录问题 exit 2；
7. 行为面：AST 级零 os.environ / 零 DB 引擎 / 零 HTTP/套接字引用。

全部测试只用临时目录与本地文件，不连接任何数据库、不发任何网络请求。
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ops import cli as cli_module
from app.ops.release_approval_draft import (
    MANUAL_APPROVAL_FIELDS,
    TOOL_ID,
    build_release_approval_draft,
)
from app.ops.release_readiness import (
    APPROVAL_DRAFT_RESERVED_FIELDS,
    GATE_IDS,
    GATES,
    REQUIRED_GATE_IDS,
    run_release_readiness,
)

#: 生产数据/密钥 marker：任何输出（JSON / stdout / 输出文件）都不得包含
PAPER_ID_MARKER = "PROD-PAPER-ID-88c1"
DRAFT_ID_MARKER = "PROD-DRAFT-ID-88c2"
PASSWORD_MARKER = "PROD-PW-88c3"
API_KEY_MARKER = "sk-PROD-KEY-88c4"
TOKEN_MARKER = "PROD-TOKEN-88c5"

_FILE_OF = {spec.gate_id: spec.evidence_file for spec in GATES}
#: 可绑定门 = 除 release-approval 外的全部门（含 optional turn-tls）
BINDABLE = GATE_IDS - {"release-approval"}


def _evidence_dir(tmp_path: Path, name: str = "evidence") -> Path:
    directory = tmp_path / name
    directory.mkdir()
    return directory


def _write_json(directory: Path, name: str, payload) -> None:
    (directory / name).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _smoke_providers(mode: str = "local") -> dict[str, dict]:
    voice_step = "local-voice-smoke" if mode == "local" else "cloud-voice-smoke"
    steps = {"voice": voice_step, "search": "search-smoke", "llm": "llm-smoke"}
    return {
        name: {"executed": True, "result": "pass", "evidence_step": step}
        for name, step in steps.items()
    }


def _soak_report() -> dict:
    return {
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
    }


def _passing_evidence() -> dict[str, dict]:
    """十门齐备且全部满足 pass 形态的证据（不含 release-approval）。"""
    return {
        "ci-main.json": {
            "gate": "ci-main",
            "run_id": 33844236243,
            "merge_commit": "38d90a0166dec73eb01e2263cacce7b7c984fec7",
            "conclusion": "success",
        },
        "release-check.json": {
            "gate": "release-check",
            "all_green": True,
            "total": 10,
            "passed": 10,
            "failed_ids": [],
        },
        "production-preflight.json": {
            "gate": "production-preflight",
            "phase": "post-migration",
            "summary": {"pass": 5, "pending": 0, "fail": 0, "not_configured": 0},
        },
        "backup-restore.json": {
            "gate": "backup-restore",
            "schema_version": "aios-backup-v1",
            "manifest_sha256": "ab" * 32,
            "created_at": "2026-09-04T08:00:00+00:00",
            "restore_drill": {"verified": True, "inserted_rows": 1234},
        },
        "audit-chain-anchor.json": {
            "gate": "audit-chain-anchor",
            "chain": {"valid": True, "entries": 69},
            "anchor": {"status": "up-to-date", "anchors": 2},
            "worm": {"archived": True},
        },
        "legacy-papers.json": {
            "gate": "legacy-papers",
            "pending_count": 0,
            "batches": [{"decision": "keep-public", "executed": True}],
        },
        "draft-ownership.json": {
            "gate": "draft-ownership",
            "pending_count": 0,
            "batches": [],
        },
        "long-soak.json": _soak_report(),
        "provider-smoke.json": {
            "gate": "provider-smoke",
            "topology": {"voice_mode": "local"},
            "providers": _smoke_providers(),
        },
        "turn-tls.json": {
            "gate": "turn-tls",
            "checks": {
                "stun_binding": "pass",
                "tls_relay": "pass",
                "symmetric_nat_e2e": "pass",
            },
        },
    }


def _write_evidence(
    directory: Path, evidence: dict[str, dict] | None = None
) -> dict[str, dict]:
    payload = _passing_evidence() if evidence is None else evidence
    for name, document in payload.items():
        _write_json(directory, name, document)
    return payload


def _cli(directory, *, output=None) -> int:
    return cli_module._run_release_approval_draft(
        SimpleNamespace(
            evidence_dir=str(directory),
            output=str(output) if output else None,
        )
    )


def _statuses(report: dict) -> dict[str, str]:
    return {gate["gate"]: gate["status"] for gate in report["gates"]}


# --- 1. 哈希绑定诚实：精确哈希 / 缺席门 / optional turn-tls ----------------------


def test_gate_bindings_match_file_bytes_exactly(tmp_path) -> None:
    """十门齐备：gate_evidence 恰覆盖全部可绑定门，每个 sha256 与文件字节
    独立重算一致；无缺席、无必需覆盖缺口。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)

    draft = build_release_approval_draft(directory)

    assert draft["draft"] is True
    assert draft["tool"] == TOOL_ID == "release-approval-draft"
    assert sorted(draft["gate_evidence"]) == sorted(BINDABLE)
    for gate_id, digest in draft["gate_evidence"].items():
        expected = hashlib.sha256(
            (directory / _FILE_OF[gate_id]).read_bytes()
        ).hexdigest()
        assert digest == expected, gate_id
    assert draft["gate_evidence_missing"] == []
    assert draft["required_coverage_gaps"] == []
    assert draft["approval_file_present"] is False


def test_missing_gates_reported_honestly(tmp_path) -> None:
    """只提供两门：绑定恰为在场门；缺席可绑定门如实列出；required 覆盖
    缺口恰为「必需门（审批除外）- 在场」；门状态与 readiness 一致。"""
    full = _passing_evidence()
    partial = {
        "ci-main.json": full["ci-main.json"],
        "release-check.json": full["release-check.json"],
        "turn-tls.json": full["turn-tls.json"],
    }
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory, partial)

    draft = build_release_approval_draft(directory)

    present = {"ci-main", "release-check", "turn-tls"}
    assert sorted(draft["gate_evidence"]) == sorted(present)
    assert sorted(draft["gate_evidence_missing"]) == sorted(BINDABLE - present)
    assert sorted(draft["required_coverage_gaps"]) == sorted(
        REQUIRED_GATE_IDS - {"release-approval"} - present
    )
    readiness = run_release_readiness(directory)
    assert {item["gate"]: item["status"] for item in draft["gate_statuses"]} == (
        _statuses(readiness)
    )
    assert draft["approval_file_present"] is False


def test_optional_turn_tls_binding_and_gap_semantics(tmp_path) -> None:
    """optional turn-tls：在场即绑定（哈希同口径）；缺席只进 missing 不进
    required 覆盖缺口（公网语音发布另行要求，readiness.optional_scope_note
    如实透出）。"""
    full = _passing_evidence()
    with_tls = dict(full)
    without_tls = {name: doc for name, doc in full.items() if name != "turn-tls.json"}

    dir_tls = _evidence_dir(tmp_path, "with_tls")
    _write_evidence(dir_tls, with_tls)
    draft = build_release_approval_draft(dir_tls)
    assert "turn-tls" in draft["gate_evidence"]
    assert draft["gate_evidence"]["turn-tls"] == hashlib.sha256(
        (dir_tls / "turn-tls.json").read_bytes()
    ).hexdigest()
    assert "turn-tls" not in draft["required_coverage_gaps"]

    dir_no_tls = _evidence_dir(tmp_path, "without_tls")
    _write_evidence(dir_no_tls, without_tls)
    draft = build_release_approval_draft(dir_no_tls)
    assert "turn-tls" in draft["gate_evidence_missing"]
    assert "turn-tls" not in draft["required_coverage_gaps"]
    assert "turn-tls" in draft["readiness"]["not_pass_optional"]
    assert "公网语音" in draft["readiness"]["optional_scope_note"]


def test_draft_never_claims_readiness(tmp_path) -> None:
    """DRAFT 全输出不含 release_ready/production_ready——本工具只算绑定与
    状态，永不作放行结论（诚实子集）。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)

    draft = build_release_approval_draft(directory)

    def _assert_no_verdict(value, path="draft") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                assert key not in ("release_ready", "production_ready"), (
                    f"{path}.{key}"
                )
                _assert_no_verdict(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                _assert_no_verdict(item, f"{path}[{index}]")

    _assert_no_verdict(draft)
    # 字符串级只禁 JSON 键形态：optional_scope_note 等边界声明文本允许提及
    # 这两个词（恰是反放行护栏措辞），但绝不出现判定字段。
    dumped = json.dumps(draft)
    assert '"release_ready"' not in dumped
    assert '"production_ready"' not in dumped


# --- 2. DRAFT 语义与人工字段 ------------------------------------------------------


def test_draft_markers_and_manual_fields_listed(tmp_path) -> None:
    """draft=true、notice 声明改名被拒与从零组装、人工必填字段清单齐备、
    confirmation_required 到场。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)

    draft = build_release_approval_draft(directory)

    assert draft["draft"] is True
    assert "DRAFT" in draft["notice"]
    assert "release-approval.json" in draft["notice"]
    assert "从零组装" in draft["notice"]
    assert set(MANUAL_APPROVAL_FIELDS) == {
        "schema_version",
        "approved_at",
        "note",
        "window.start",
        "window.end",
        "rollback_plan",
        "observation",
        "approved_by",
    }
    assert draft["manual_fields_required"] == MANUAL_APPROVAL_FIELDS
    for hint in MANUAL_APPROVAL_FIELDS.values():
        assert "REPLACE-ME" in hint or "固定" in hint
    assert "人工" in draft["confirmation_required"]
    assert draft["generated_at"]
    datetime.fromisoformat(draft["generated_at"])  # ISO 8601 可解析


def test_approval_file_present_flag_and_untouched(tmp_path) -> None:
    """既有 release-approval.json：approval_file_present 如实 True，且底稿
    生成前后其字节不变（绝不创建或修改审批文件）。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    gate_evidence = {
        gate_id: hashlib.sha256((directory / _FILE_OF[gate_id]).read_bytes()).hexdigest()
        for gate_id in BINDABLE
    }
    approval = {
        "gate": "release-approval",
        "schema_version": 1,
        "approved_at": "2026-09-04T12:00:00+00:00",
        "approved_by": "ops-oncall",
        "note": "窗口 02:00-04:00 UTC",
        "window": {
            "start": "2026-09-05T02:00:00+00:00",
            "end": "2026-09-05T04:00:00+00:00",
        },
        "rollback_plan": "旧镜像 tag 回滚",
        "observation": "24h",
        "gate_evidence": gate_evidence,
    }
    _write_json(directory, "release-approval.json", approval)
    before = (directory / "release-approval.json").read_bytes()

    draft = build_release_approval_draft(directory)

    assert draft["approval_file_present"] is True
    assert (directory / "release-approval.json").read_bytes() == before


# --- 3. DRAFT 不可审批（改名防线） ------------------------------------------------


def test_draft_renamed_raw_is_rejected(tmp_path) -> None:
    """底稿原样改名为 release-approval.json => 该门 malformed、不可放行。"""
    source = _evidence_dir(tmp_path, "source")
    _write_evidence(source)
    draft = build_release_approval_draft(source)

    target = _evidence_dir(tmp_path, "target")
    _write_evidence(target)
    _write_json(target, "release-approval.json", draft)

    report = run_release_readiness(target)
    assert _statuses(report)["release-approval"] == "malformed"
    assert report["release_ready"] is False
    assert report["exit_code"] == 1


def test_filled_draft_with_metadata_kept_is_rejected(tmp_path) -> None:
    """补齐 gate 自声明与全部人工字段、哈希取自底稿，但保留任一底稿元数据
    字段 => 仍 malformed（即使哈希全部匹配也不放行）。"""
    source = _evidence_dir(tmp_path, "source")
    _write_evidence(source)
    draft = build_release_approval_draft(source)

    filled = {
        "gate": "release-approval",
        "schema_version": 1,
        "approved_at": "2026-09-26T04:00:00+00:00",
        "approved_by": "ops-oncall",
        "note": "窗口审批说明（人工填写）",
        "window": {
            "start": "2026-09-27T02:00:00+00:00",
            "end": "2026-09-27T04:00:00+00:00",
        },
        "rollback_plan": "AIOS_IMAGE_TAG 旧 tag + docker compose up -d --no-build",
        "observation": "24h",
        "gate_evidence": dict(draft["gate_evidence"]),
        # 底稿元数据保留（只留一个最小集合也会被拒——这里留全量更严）
        "draft": True,
        "tool": draft["tool"],
        "generated_at": draft["generated_at"],
        "evidence_dir": draft["evidence_dir"],
        "notice": draft["notice"],
        "approval_file_present": draft["approval_file_present"],
        "gate_evidence_missing": draft["gate_evidence_missing"],
        "required_coverage_gaps": draft["required_coverage_gaps"],
        "gate_statuses": draft["gate_statuses"],
        "readiness": draft["readiness"],
        "load_problems": draft["load_problems"],
        "manual_fields_required": draft["manual_fields_required"],
        "confirmation_required": draft["confirmation_required"],
    }
    target = _evidence_dir(tmp_path, "target")
    _write_evidence(target)
    _write_json(target, "release-approval.json", filled)

    report = run_release_readiness(target)
    gate = next(g for g in report["gates"] if g["gate"] == "release-approval")
    assert gate["status"] == "malformed"
    assert "底稿保留元数据" in gate["reason"]
    assert report["release_ready"] is False


def test_single_reserved_field_alone_is_rejected(tmp_path) -> None:
    """合法审批 + 仅追加一个保留字段（draft: true）=> malformed——单一
    底稿标记即 fail-closed，不要求成组出现。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    gate_evidence = {
        gate_id: hashlib.sha256((directory / _FILE_OF[gate_id]).read_bytes()).hexdigest()
        for gate_id in BINDABLE
    }
    approval = {
        "gate": "release-approval",
        "schema_version": 1,
        "approved_at": "2026-09-26T04:00:00+00:00",
        "approved_by": "ops-oncall",
        "note": "窗口审批说明",
        "window": {
            "start": "2026-09-27T02:00:00+00:00",
            "end": "2026-09-27T04:00:00+00:00",
        },
        "rollback_plan": "旧镜像 tag 回滚",
        "observation": "24h",
        "gate_evidence": gate_evidence,
        "draft": True,
    }
    _write_json(directory, "release-approval.json", approval)

    report = run_release_readiness(directory)
    assert _statuses(report)["release-approval"] == "malformed"
    assert report["release_ready"] is False


def test_reserved_fields_cover_all_draft_metadata(tmp_path) -> None:
    """同步守卫：底稿输出除 gate_evidence 外的全部顶层键都被
    APPROVAL_DRAFT_RESERVED_FIELDS 登记——底稿新增元数据字段而漏登记会
    直接红（携带它改名将绕过防线）。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)

    draft = build_release_approval_draft(directory)

    metadata = set(draft) - {"gate_evidence"}
    assert metadata, "底稿必须携带元数据字段（draft/notice/...）"
    uncovered = metadata - set(APPROVAL_DRAFT_RESERVED_FIELDS)
    assert not uncovered, f"底稿元数据字段未被保留集登记: {sorted(uncovered)}"
    assert "gate_evidence" not in APPROVAL_DRAFT_RESERVED_FIELDS


# --- 4. 只读与零敏感 --------------------------------------------------------------


def test_never_echoes_secrets(tmp_path, capsys) -> None:
    """证据内嵌 marker（生产 ID/URL 凭据）：底稿 JSON 与 CLI stdout 零泄漏；
    load_problems 只报字段路径（敏感键/内嵌凭据命中的文件名）。"""
    directory = _evidence_dir(tmp_path)
    evidence = _passing_evidence()
    evidence["legacy-papers.json"]["batches"] = [
        {"decision": "keep-public", "executed": True, "paper_ids": [PAPER_ID_MARKER]}
    ]
    evidence["draft-ownership.json"]["batches"] = [
        {"kind": "course-generation", "executed": True, "draft_ids": [DRAFT_ID_MARKER]}
    ]
    evidence["ci-main.json"]["db_url"] = (
        f"postgresql+asyncpg://aios:{PASSWORD_MARKER}@127.0.0.1:5433/ai_learning_os"
    )
    _write_evidence(directory, evidence)

    draft = build_release_approval_draft(directory)
    dumped = json.dumps(draft, ensure_ascii=False)
    for marker in (PAPER_ID_MARKER, DRAFT_ID_MARKER, PASSWORD_MARKER):
        assert marker not in dumped, marker
    assert "postgresql+asyncpg" not in dumped
    # ci-main 携带内嵌凭据 => load_problems 如实记录该文件问题（不含值）
    assert "ci-main.json" in draft["load_problems"]
    problem_text = json.dumps(draft["load_problems"], ensure_ascii=False)
    assert PASSWORD_MARKER not in problem_text

    assert _cli(directory) == 0
    out = capsys.readouterr().out
    for marker in (PAPER_ID_MARKER, DRAFT_ID_MARKER, PASSWORD_MARKER):
        assert marker not in out, marker
    assert "postgresql+asyncpg" not in out


def test_run_is_read_only(tmp_path) -> None:
    """两轮底稿生成后全部证据文件字节不变；目录集合不变。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)

    before = {
        entry.name: entry.read_bytes()
        for entry in sorted(directory.iterdir())
        if entry.is_file()
    }
    build_release_approval_draft(directory)
    build_release_approval_draft(directory)
    after = {
        entry.name: entry.read_bytes()
        for entry in sorted(directory.iterdir())
        if entry.is_file()
    }
    assert before == after


def test_poisoned_environment_not_consulted(tmp_path, monkeypatch) -> None:
    """毒化 DB/API/key 环境变量：不读 env，输出不受影响。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    monkeypatch.setenv(
        "DATABASE_URL",
        f"postgresql://u:{PASSWORD_MARKER}@127.0.0.1:5433/ai_learning_os",
    )
    monkeypatch.setenv("LLM_API_KEY", API_KEY_MARKER)
    monkeypatch.setenv("AIOS_PG_TEST_URL", f"postgres://u:{TOKEN_MARKER}@h/db")

    draft = build_release_approval_draft(directory)
    dumped = json.dumps(draft)
    for marker in (PASSWORD_MARKER, API_KEY_MARKER, TOKEN_MARKER):
        assert marker not in dumped


# --- 5. CLI ------------------------------------------------------------------------


def test_cli_registered_and_has_no_execute_flag(tmp_path, monkeypatch) -> None:
    """子命令注册；不存在 --yes（无任何执行形态，argparse exit 2）。"""
    assert hasattr(cli_module, "_run_release_approval_draft")
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    for keyword in ("release-approval-draft", "evidence-dir"):
        assert keyword in source, keyword
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "release-approval-draft",
            "--evidence-dir",
            str(directory),
            "--yes",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 2


def test_main_dispatch_prints_draft(tmp_path, monkeypatch, capsys) -> None:
    """main 分发：打印 DRAFT 底稿与显著 DRAFT 提示，exit 0（无论门状态——
    底稿是助手不是门裁决）。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli", "release-approval-draft", "--evidence-dir", str(directory)],
    )
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert '"draft": true' in out
    assert "DRAFT" in out
    assert "不是审批记录" in out


def test_cli_missing_evidence_dir_arg_exits_2(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["cli", "release-approval-draft"])
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 2


def test_cli_output_guard_and_atomic_write(tmp_path, capsys) -> None:
    """--output 越界拒绝 exit 2；artifacts 内允许（原子写）且文件内容与
    stdout 完全一致、无 .tmp 残留。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)

    assert _cli(directory, output=tmp_path / "draft.json") == 2
    assert "拒绝写入" in capsys.readouterr().out
    assert not (tmp_path / "draft.json").exists()

    guarded = tmp_path / "artifacts" / "release-approval.DRAFT.json"
    assert _cli(directory, output=guarded) == 0
    capsys.readouterr()
    written = guarded.read_text(encoding="utf-8")
    parsed = json.loads(written)
    assert parsed["draft"] is True
    assert parsed["gate_evidence"] == build_release_approval_draft(directory)[
        "gate_evidence"
    ]
    leftovers = [
        entry.name for entry in guarded.parent.iterdir() if entry.name.endswith(".tmp")
    ]
    assert leftovers == []


def test_cli_output_named_release_approval_rejected(tmp_path, capsys) -> None:
    """输出名恰为 release-approval.json（即使在 artifacts/temp 内）一律拒绝
    ——绝不创建或修改 release-approval.json。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)

    target = tmp_path / "artifacts" / "release-approval.json"
    assert _cli(directory, output=target) == 2
    out = capsys.readouterr().out
    assert "release-approval.json" in out
    assert "拒绝" in out
    assert not target.exists()


def test_cli_output_inside_evidence_dir_rejected(tmp_path, capsys) -> None:
    """输出位于证据目录内：拒绝（不覆盖证据输入）——证据目录本身恰为
    artifacts/ 目录时第二层护栏放行（直接父目录名是 artifacts），第三层
    必须接住：绝不往证据目录里写任何文件。"""
    directory = tmp_path / "artifacts"
    directory.mkdir(parents=True)
    _write_evidence(directory)
    before = {entry.name for entry in directory.iterdir()}

    target = directory / "release-approval.DRAFT.json"
    assert _cli(directory, output=target) == 2
    out = capsys.readouterr().out
    assert "证据目录" in out
    assert "拒绝" in out
    assert not target.exists()
    assert {entry.name for entry in directory.iterdir()} == before


def test_cli_bad_evidence_dir_exit_2(tmp_path, capsys) -> None:
    """证据目录不存在 => exit 2，打印输入无效说明。"""
    assert _cli(tmp_path / "missing") == 2
    assert "证据输入无效" in capsys.readouterr().out


def test_cli_write_failure_keeps_silent(tmp_path, capsys) -> None:
    """输出父级被普通文件占用（mkdir 失败）：exit 2、不打印底稿正文。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    (tmp_path / "artifacts").write_text("occupied", encoding="utf-8")

    assert _cli(directory, output=tmp_path / "artifacts" / "d.json") == 2
    captured = capsys.readouterr()
    assert "底稿写入失败" in captured.out
    assert '"draft"' not in captured.out
    assert captured.err == ""


# --- 6. 行为面：零 env / 零 DB / 零网络 --------------------------------------------


def test_module_has_no_env_db_or_network_surface() -> None:
    """AST 级零 os.environ / 零 DB 引擎 / 零 HTTP/套接字引用（只看真实
    代码，文档字符串与注释里的说明性字样不计）。"""
    from app.ops import release_approval_draft as rad

    tree = ast.parse(Path(rad.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names = {(node.module or "").split(".")[0]}
        else:
            names = set()
        assert names.isdisjoint(
            {"httpx", "socket", "requests", "urllib", "asyncpg", "psycopg",
             "sqlalchemy", "psycopg2", "aiohttp"}
        ), f"模块不得导入 {names}"
        if isinstance(node, ast.Attribute) and node.attr == "environ":
            raise AssertionError("模块不得访问环境变量（os.environ）")
        if isinstance(node, ast.Name) and node.id == "create_engine":
            raise AssertionError("模块不得创建数据库引擎")


def test_utc_timestamps_are_z_offset_aware(tmp_path) -> None:
    """generated_at 是带时区的 UTC ISO 8601（与 readiness manifest 同口径）。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    draft = build_release_approval_draft(directory)
    moment = datetime.fromisoformat(draft["generated_at"])
    assert moment.tzinfo is not None
    assert moment.utcoffset() == UTC.utcoffset(moment)
