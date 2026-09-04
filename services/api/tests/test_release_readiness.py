"""M10-11 release readiness：只读证据 manifest 的门矩阵/状态语义/安全边界。

覆盖矩阵：
1. 门矩阵与 CLI 注册：GATES 覆盖十个发布审批门（无漏项/无虚设）、
   required/optional 划分、EVALUATORS 全覆盖、证据文件名唯一；CLI 子命令
   注册、无 --yes 执行形态（argparse exit 2）、main 分发；provider-smoke
   措辞口径：voice/LLM 需部署 key、search 需真实端点冒烟且
   SEARCH_CLOUD_API_KEY 可选（M10-12，不得回退「三类统一真实 key」旧口径）；
2. 全 pass：齐备证据 + 哈希绑定审批 -> release_ready=True / exit 0；每门
   evidence sha256 与文件字节独立重算一致；审批 data 记录覆盖门与零失配；
3. missing / malformed：空目录全 missing（exit 1）；单门缺失；非法 JSON /
   结构不符 / 自声明 gate 错位 / 自相矛盾计数一律 malformed；含敏感键的
   证据 malformed 且值零回显；
4. tampered：审批后改动证据 -> release-approval tampered（被改门自身仍
   pass）；审批绑定的门证据缺失 -> tampered；锚文件副本被改 / 申报计数与
   副本不一致 -> tampered；
5. pending 不伪装 pass：pre-migration 证据、治理计数 >0、冒烟未执行、恢复
   演练未 verified、锚定落后、WORM 未归档、审批未覆盖全部必需门 -> 该门
   pending、release_ready=False、exit 1、人类摘要写明仍需人工；turn-tls 是
   optional 门：pending 如实透出但不阻断 release_ready（本机/LAN 发布形态），
   manifest（not_pass_optional + optional_scope_note）与人类摘要必须声明公网
   语音发布仍需 turn-tls=pass，release_ready 不含公网语音就绪结论；
6. blocked：CI failure / release-check 未全绿 / preflight fail / 链 invalid /
   冒烟 fail -> blocked；
7. 路径护栏与 IO（exit 2）：--evidence-dir 不存在 / 普通文件 / symlink、
   目录内任何 symlink、证据文件名被目录占用；--output 非 artifacts/temp
   拒绝、artifacts 内写入、输出父级被普通文件占用时不打印门结论摘要；
8. 只读与零敏感：全部证据文件字节在两轮运行前后不变（含锚文件副本）；
   毒化环境变量不被读取（不读 env 的行为面证明）；生产 paper ID / 密码 /
   token / key marker 在 JSON manifest、人类摘要与输出文件零泄漏；未识别
   文件如实列出（仅文件名 + sha256，无内容回显）。

全部测试只用临时目录与本地文件，不连接任何数据库、不发任何网络请求。
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
from app.ops.release_readiness import (
    EVALUATORS,
    GATE_IDS,
    GATES,
    REQUIRED_GATE_IDS,
    format_readiness_summary,
    run_release_readiness,
)

#: 生产数据/密钥 marker：任何输出（JSON / 人类摘要 / 输出文件）都不得包含
PAPER_ID_MARKER = "PROD-PAPER-ID-77c3"
DRAFT_ID_MARKER = "PROD-DRAFT-ID-77c4"
PASSWORD_MARKER = "PROD-PW-77c5"
API_KEY_MARKER = "sk-PROD-KEY-77c6"
TOKEN_MARKER = "PROD-TOKEN-77c7"

EXPECTED_GATES = {
    "ci-main",
    "release-check",
    "production-preflight",
    "backup-restore",
    "audit-chain-anchor",
    "legacy-papers",
    "draft-ownership",
    "provider-smoke",
    "turn-tls",
    "release-approval",
}
EXPECTED_OPTIONAL = {"turn-tls"}

_FILE_OF = {spec.gate_id: spec.evidence_file for spec in GATES}


def _evidence_dir(tmp_path: Path, name: str = "evidence") -> Path:
    directory = tmp_path / name
    directory.mkdir()
    return directory


def _write_json(directory: Path, name: str, payload) -> None:
    (directory / name).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _passing_evidence() -> dict[str, dict]:
    """九门齐备且全部满足 pass 形态的证据（不含 release-approval）。"""
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
        "provider-smoke.json": {
            "gate": "provider-smoke",
            "providers": {
                "voice": {"executed": True, "result": "pass"},
                "search": {"executed": True, "result": "pass"},
                "llm": {"executed": True, "result": "pass"},
            },
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


def _write_approval(
    directory: Path,
    *,
    coverage: list[str] | None = None,
    note: str = "发布窗口 02:00-04:00 UTC；回滚=旧镜像 tag；观察期 24h",
) -> None:
    """按目录内当前证据文件字节计算 sha256，写入哈希绑定的审批记录。"""
    if coverage is None:
        coverage = sorted(GATE_IDS - {"release-approval"})
    gate_evidence = {
        gate_id: hashlib.sha256((directory / _FILE_OF[gate_id]).read_bytes()).hexdigest()
        for gate_id in coverage
    }
    _write_json(
        directory,
        "release-approval.json",
        {
            "gate": "release-approval",
            "schema_version": 1,
            "approved_at": "2026-09-04T12:00:00+00:00",
            "approved_by": "ops-oncall",
            "note": note,
            "window": {
                "start": "2026-09-05T02:00:00+00:00",
                "end": "2026-09-05T04:00:00+00:00",
            },
            "rollback_plan": "AIOS_IMAGE_TAG 旧 tag + docker compose up -d --no-build",
            "observation": "24h",
            "gate_evidence": gate_evidence,
        },
    )


def _anchor_companion_bytes() -> bytes:
    """两条自洽锚链（sequence=1 -> 2），字段与 M10-06 锚文件同格式。"""
    from app.ops.audit_chain_anchor import build_anchor, render_anchor_line

    moment = datetime(2026, 9, 4, 9, 0, 0, tzinfo=UTC)
    first = build_anchor(
        sequence=1,
        head_hash="bb" * 32,
        previous_anchor_hash="00" * 32,
        clock=lambda: moment,
    )
    second = build_anchor(
        sequence=2,
        head_hash="cc" * 32,
        previous_anchor_hash=first["anchor_hash"],
        clock=lambda: moment,
    )
    return render_anchor_line(first) + render_anchor_line(second)


def _run(directory: Path) -> dict:
    return run_release_readiness(directory)


def _cli(directory, *, as_json=False, output=None) -> int:
    return cli_module._run_release_readiness(
        SimpleNamespace(
            evidence_dir=str(directory),
            as_json=as_json,
            output=str(output) if output else None,
        )
    )


def _gate(report: dict, gate_id: str) -> dict:
    return next(gate for gate in report["gates"] if gate["gate"] == gate_id)


def _statuses(report: dict) -> dict[str, str]:
    return {gate["gate"]: gate["status"] for gate in report["gates"]}


# --- 1. 门矩阵与 CLI 注册 ------------------------------------------------------


def test_gate_matrix_complete_no_phantom() -> None:
    """十个发布审批门无漏项/无虚设；optional 仅 turn-tls；评估器全覆盖。"""
    assert GATE_IDS == EXPECTED_GATES
    files = [spec.evidence_file for spec in GATES]
    assert len(files) == len(set(files)), "证据文件名必须唯一"
    for spec in GATES:
        assert spec.title and spec.basis, f"门 {spec.gate_id} 缺 title/basis"
    optional = {spec.gate_id for spec in GATES if not spec.required}
    assert optional == EXPECTED_OPTIONAL
    assert "release-approval" in REQUIRED_GATE_IDS
    assert set(EVALUATORS) == GATE_IDS


def test_provider_smoke_wording_matches_provider_requirements(tmp_path) -> None:
    """provider-smoke 措辞与三类 provider 的真实要求一致：voice/LLM 需部署
    key，search 需真实端点冒烟且 SEARCH_CLOUD_API_KEY 可选（M10-12：无鉴权
    SearXNG 合法，不得把 search 冒烟说成必须有 key，也不得把「真实 key 冒烟」
    口径统一套在三类上）。措辞只是口径修正——门语义不放宽：仍是 required
    gate，not_executed -> pending、fail -> blocked 由既有语义测试守卫。"""
    spec = next(s for s in GATES if s.gate_id == "provider-smoke")
    assert spec.required is True
    for surface in (spec.title, spec.basis):
        assert "voice/LLM" in surface, surface
        assert "部署 key" in surface, surface
        assert "search" in surface and "真实端点" in surface, surface
    # key 可选语义在 basis 里显式声明（title 保持一行可读）
    assert "SEARCH_CLOUD_API_KEY" in spec.basis
    assert "可选" in spec.basis

    # pending：search/llm 未执行——指引区分部署 key 与真实端点（key 可选）
    directory = _evidence_dir(tmp_path, "smoke-wording-pending")
    evidence = _passing_evidence()
    evidence["provider-smoke.json"]["providers"] = {
        "voice": {"executed": True, "result": "pass"},
        "search": {"executed": False, "result": "not_executed"},
        "llm": {"executed": False, "result": "not_executed"},
    }
    _write_evidence(directory, evidence)
    _write_approval(directory)
    gate = _gate(_run(directory), "provider-smoke")
    assert gate["status"] == "pending"
    assert "search, llm" in gate["reason"]
    assert "部署 key" in gate["reason"]
    assert "真实端点" in gate["reason"]
    assert "SEARCH_CLOUD_API_KEY" in gate["reason"] and "可选" in gate["reason"]

    # blocked：search 冒烟失败（无 key 合法端点也可能失败）——指引同口径区分
    directory = _evidence_dir(tmp_path, "smoke-wording-fail")
    evidence = _passing_evidence()
    evidence["provider-smoke.json"]["providers"]["search"] = {
        "executed": True,
        "result": "fail",
    }
    _write_evidence(directory, evidence)
    _write_approval(directory)
    gate = _gate(_run(directory), "provider-smoke")
    assert gate["status"] == "blocked"
    assert "search" in gate["reason"]
    assert "部署 key" in gate["reason"] and "真实端点" in gate["reason"]
    assert "SEARCH_CLOUD_API_KEY" in gate["reason"] and "可选" in gate["reason"]

    # pass：全部通过——通过口径同样区分三类要求，不虚称「真实 key 冒烟」
    directory = _evidence_dir(tmp_path, "smoke-wording-pass")
    _write_evidence(directory)
    _write_approval(directory)
    report = _run(directory)
    gate = _gate(report, "provider-smoke")
    assert gate["status"] == "pass"
    assert "部署 key" in gate["reason"] and "真实端点" in gate["reason"]
    assert "SEARCH_CLOUD_API_KEY" in gate["reason"] and "可选" in gate["reason"]
    # 旧口径（三类统一「真实 key 冒烟」）不得回流到门文案
    assert "真实 key" not in gate["reason"]
    assert "真实 key" not in spec.title and "真实 key" not in spec.basis


def test_cli_registered_and_has_no_execute_flag(tmp_path, monkeypatch) -> None:
    """子命令注册三参数；不存在 --yes（无任何执行形态，argparse exit 2）。"""
    assert hasattr(cli_module, "_run_release_readiness")
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    for keyword in ("release-readiness", "evidence-dir"):
        assert keyword in source, keyword
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    _write_approval(directory)
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli", "release-readiness", "--evidence-dir", str(directory), "--yes"],
    )
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 2


def test_main_dispatch_release_readiness(tmp_path, monkeypatch, capsys) -> None:
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    _write_approval(directory)
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli", "release-readiness", "--evidence-dir", str(directory)],
    )
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "release readiness" in out
    assert "RELEASE READY" in out
    assert "不执行生产迁移" in out


def test_cli_missing_evidence_dir_arg_exits_2(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["cli", "release-readiness"])
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 2


# --- 2. 全 pass：齐备证据 + 哈希绑定审批 ---------------------------------------


def test_all_pass_ready_exit_0(tmp_path) -> None:
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    _write_approval(directory)

    report = _run(directory)
    assert set(_statuses(report).values()) == {"pass"}
    assert report["release_ready"] is True
    assert report["exit_code"] == 0
    assert report["summary"]["pass"] == len(GATES)
    assert report["summary"]["malformed"] == 0
    # evidence sha256 与文件字节独立重算一致（manifest 完整性真相源）
    for gate in report["gates"]:
        expected = hashlib.sha256(
            (directory / _FILE_OF[gate["gate"]]).read_bytes()
        ).hexdigest()
        assert gate["evidence"]["sha256"] == expected, gate["gate"]
    approval = _gate(report, "release-approval")
    assert approval["data"]["hash_mismatches"] == []
    assert approval["data"]["coverage_gaps"] == []
    assert sorted(approval["data"]["covered_gates"]) == sorted(
        GATE_IDS - {"release-approval"}
    )
    assert approval["data"]["approved_by_recorded"] is True
    assert "身份认证" in approval["data"]["identity_note"]


def test_ready_human_summary_honest(tmp_path) -> None:
    """ready 摘要也只声称「证据汇总」，不输出已执行生产操作的暗示。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    _write_approval(directory)
    summary = format_readiness_summary(_run(directory))
    assert "RELEASE READY" in summary
    assert "不执行生产迁移" in summary
    assert "RESULT:" in summary


# --- 3. missing / malformed ----------------------------------------------------


def test_empty_dir_all_missing_exit_1(tmp_path, capsys) -> None:
    directory = _evidence_dir(tmp_path)
    assert _cli(directory) == 1
    report = _run(directory)
    assert set(_statuses(report).values()) == {"missing"}
    assert report["release_ready"] is False
    assert report["exit_code"] == 1
    for gate in report["gates"]:
        assert gate["evidence"] is None
        assert gate["reason"].startswith("未提供证据文件")
    out = capsys.readouterr().out
    assert "NOT READY" in out
    assert "仍需" in out


def test_single_required_missing_not_ready(tmp_path) -> None:
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    _write_approval(directory)
    (directory / "ci-main.json").unlink()
    report = _run(directory)
    assert _statuses(report)["ci-main"] == "missing"
    assert report["release_ready"] is False
    assert report["not_pass_required"] == ["ci-main", "release-approval"] or set(
        report["not_pass_required"]
    ) == {"ci-main", "release-approval"}


@pytest.mark.parametrize(
    "payload",
    [
        "not json {{{",
        '["not", "an", "object"]',
        '{"gate": "ci-main"}',  # 缺 run_id/merge_commit/conclusion
        '{"gate": "ci-main", "run_id": 1, "merge_commit": "38d90a0", "conclusion": "green"}',
        '{"gate": "release-check", "all_green": true, "total": 10, "passed": 9, "failed_ids": []}',
        '{"gate": "ci-main", "run_id": "abc", "merge_commit": "38d90a0", "conclusion": "success"}',
    ],
    ids=[
        "garbage",
        "top-level-array",
        "missing-fields",
        "bad-conclusion",
        "all-green-contradiction",
        "non-int-run-id",
    ],
)
def test_malformed_evidence_exit_1(tmp_path, payload) -> None:
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    _write_approval(directory)
    (directory / "ci-main.json").write_text(payload, encoding="utf-8")
    (directory / "release-check.json").write_text(payload, encoding="utf-8")

    report = _run(directory)
    assert _statuses(report)["ci-main"] == "malformed"
    assert _statuses(report)["release-check"] == "malformed"
    assert report["exit_code"] == 1
    # malformed 门仍记录 sha256（审批绑定校验需要当前哈希）
    assert _gate(report, "ci-main")["evidence"]["sha256"]


def test_gate_self_id_mismatch_is_malformed(tmp_path) -> None:
    """证据自声明 gate 与文件名对应门不符：错位文件 fail-closed。"""
    directory = _evidence_dir(tmp_path)
    evidence = _passing_evidence()
    evidence["ci-main.json"]["gate"] = "release-check"
    _write_evidence(directory, evidence)

    report = _run(directory)
    assert _statuses(report)["ci-main"] == "malformed"


def test_sensitive_key_in_evidence_is_malformed_no_echo(tmp_path, capsys) -> None:
    """provider 冒烟证据携带 api_key：malformed，且 key 值零回显。"""
    directory = _evidence_dir(tmp_path)
    evidence = _passing_evidence()
    evidence["provider-smoke.json"]["providers"]["voice"]["api_key"] = API_KEY_MARKER
    _write_evidence(directory, evidence)

    assert _cli(directory, as_json=True) == 1
    report = json.loads(capsys.readouterr().out)
    gate = _gate(report, "provider-smoke")
    assert gate["status"] == "malformed"
    assert "敏感键" in gate["reason"]
    assert API_KEY_MARKER not in json.dumps(report)


#: 常见凭据字段键名变体：无论出现在证据哪个位置（顶层/嵌套/数组元素内）
#: 命中即 malformed——回归不只守 api_key 单一样例
SENSITIVE_KEY_NAMES = (
    "password",
    "db_password",
    "passwd",
    "secret",
    "client_secret",
    "token",
    "auth_token",
    "api_key",
    "api-key",
    "apikey",
    "access_key",
    "access-key",
    "private_key",
    "private-key",
    "authorization",
    "auth_header",
    "cookie",
    "credential",
    "credentials",
)


@pytest.mark.parametrize("key", SENSITIVE_KEY_NAMES)
def test_sensitive_key_variants_all_malformed_no_echo(tmp_path, key) -> None:
    """任意敏感键变体（含嵌套对象与数组元素内）命中即 malformed，值零回显。"""
    directory = _evidence_dir(tmp_path)
    evidence = _passing_evidence()
    evidence["provider-smoke.json"]["providers"]["voice"][key] = API_KEY_MARKER
    evidence["legacy-papers.json"]["batches"] = [
        {"decision": "keep-public", "executed": True, key: PASSWORD_MARKER}
    ]
    _write_evidence(directory, evidence)

    report = _run(directory)
    smoke = _gate(report, "provider-smoke")
    papers = _gate(report, "legacy-papers")
    assert smoke["status"] == "malformed", key
    assert papers["status"] == "malformed", key
    # 错误信息只报字段路径（嵌套路径与数组下标），不回显值
    assert f"providers.voice.{key}" in smoke["reason"]
    assert f"batches.0.{key}" in papers["reason"]
    dumped = json.dumps(report, ensure_ascii=False)
    assert API_KEY_MARKER not in dumped
    assert PASSWORD_MARKER not in dumped
    assert report["exit_code"] == 1


def test_embedded_credential_in_evidence_is_malformed_no_echo(tmp_path) -> None:
    """键名不含敏感词但值内嵌 `://user:pass@` 凭据段：malformed，值零回显。"""
    directory = _evidence_dir(tmp_path)
    evidence = _passing_evidence()
    evidence["ci-main.json"]["db_url"] = (
        f"postgresql+asyncpg://aios:{PASSWORD_MARKER}@127.0.0.1:5433/ai_learning_os"
    )
    _write_evidence(directory, evidence)

    report = _run(directory)
    gate = _gate(report, "ci-main")
    assert gate["status"] == "malformed"
    assert "内嵌凭据" in gate["reason"]
    assert "db_url" in gate["reason"]  # 只报字段路径
    dumped = json.dumps(report, ensure_ascii=False)
    assert PASSWORD_MARKER not in dumped
    assert "postgresql+asyncpg" not in dumped


# --- 4. tampered：审批哈希绑定与锚文件副本 -------------------------------------


def test_evidence_edited_after_approval_is_tampered(tmp_path) -> None:
    """审批后改动任一证据 -> release-approval tampered（被改门自身仍 pass）。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    _write_approval(directory)
    edited = dict(_passing_evidence()["ci-main.json"])
    edited["run_id"] = 99999999999
    _write_json(directory, "ci-main.json", edited)

    report = _run(directory)
    assert _statuses(report)["ci-main"] == "pass"
    approval = _gate(report, "release-approval")
    assert approval["status"] == "tampered"
    assert approval["data"]["hash_mismatches"] == ["ci-main"]
    assert report["release_ready"] is False
    assert report["exit_code"] == 1


def test_approval_bound_evidence_deleted_is_tampered(tmp_path) -> None:
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    _write_approval(directory)
    (directory / "draft-ownership.json").unlink()

    report = _run(directory)
    assert _statuses(report)["draft-ownership"] == "missing"
    assert _gate(report, "release-approval")["status"] == "tampered"


def test_anchor_companion_verified_and_tamper_detected(tmp_path) -> None:
    """锚文件副本：自洽时 pass 且 supporting 记录 sha256；被改 -> tampered。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    (directory / "audit-anchor.jsonl").write_bytes(_anchor_companion_bytes())
    _write_approval(directory)

    report = _run(directory)
    gate = _gate(report, "audit-chain-anchor")
    assert gate["status"] == "pass"
    assert gate["supporting"] == [
        {
            "file": "audit-anchor.jsonl",
            "sha256": hashlib.sha256(_anchor_companion_bytes()).hexdigest(),
        }
    ]

    # 篡改副本首行 anchor_hash：anchor_hash 重算失败 -> tampered
    lines = _anchor_companion_bytes().decode("utf-8").splitlines()
    first = json.loads(lines[0])
    first["anchor_hash"] = ("f" if first["anchor_hash"][0] != "f" else "e") + (
        first["anchor_hash"][1:]
    )
    (directory / "audit-anchor.jsonl").write_text(
        json.dumps(first) + "\n" + lines[1] + "\n", encoding="utf-8"
    )
    report = _run(directory)
    gate = _gate(report, "audit-chain-anchor")
    assert gate["status"] == "tampered"
    assert "锚文件副本校验未通过" in gate["reason"]


def test_anchor_count_mismatch_with_companion_is_tampered(tmp_path) -> None:
    """申报锚点数与副本实际行数不一致：证据与副本互相矛盾 -> tampered。"""
    directory = _evidence_dir(tmp_path)
    evidence = _passing_evidence()
    evidence["audit-chain-anchor.json"]["anchor"]["anchors"] = 3
    _write_evidence(directory, evidence)
    (directory / "audit-anchor.jsonl").write_bytes(_anchor_companion_bytes())

    report = _run(directory)
    gate = _gate(report, "audit-chain-anchor")
    assert gate["status"] == "tampered"
    assert "不一致" in gate["reason"]


# --- 5. pending 不伪装 pass ----------------------------------------------------


def test_pending_semantics_each_gate(tmp_path) -> None:
    """各「必需门」的「待人工」形态全部 pending：release_ready=False、exit 1；
    turn-tls 是 optional 门，pending 如实透出但不阻断 release_ready（本机/LAN
    发布形态），其 ready 边界由 test_turn_tls_optional_pending_ready 断言。"""
    cases = {
        "production-preflight": (
            "production-preflight.json",
            {
                "gate": "production-preflight",
                "phase": "pre-migration",
                "summary": {"pass": 3, "pending": 2, "fail": 0, "not_configured": 0},
            },
        ),
        "backup-restore": (
            "backup-restore.json",
            {
                "gate": "backup-restore",
                "schema_version": "aios-backup-v1",
                "manifest_sha256": "ab" * 32,
                "created_at": "2026-09-04T08:00:00+00:00",
                "restore_drill": {"verified": False, "inserted_rows": 0},
            },
        ),
        "audit-chain-anchor": (
            "audit-chain-anchor.json",
            {
                "gate": "audit-chain-anchor",
                "chain": {"valid": True, "entries": 69},
                "anchor": {"status": "valid", "anchors": 1},
                "worm": {"archived": False},
            },
        ),
        "legacy-papers": (
            "legacy-papers.json",
            {
                "gate": "legacy-papers",
                "pending_count": 744,
                "batches": [{"decision": "keep-public", "executed": True}],
            },
        ),
        "draft-ownership": (
            "draft-ownership.json",
            {"gate": "draft-ownership", "pending_count": 6, "batches": []},
        ),
        "provider-smoke": (
            "provider-smoke.json",
            {
                "gate": "provider-smoke",
                "providers": {
                    "voice": {"executed": True, "result": "pass"},
                    "search": {"executed": False, "result": "not_executed"},
                    "llm": {"executed": False, "result": "not_executed"},
                },
            },
        ),
    }
    for gate_id, (filename, payload) in cases.items():
        directory = _evidence_dir(tmp_path, f"pending-{gate_id}")
        _write_evidence(directory)
        _write_json(directory, filename, payload)
        _write_approval(directory)

        report = _run(directory)
        status = _statuses(report)[gate_id]
        assert status == "pending", (gate_id, _gate(report, gate_id)["reason"])
        assert report["summary"]["pass"] == len(GATES) - 1, gate_id
        assert gate_id in report["not_pass_required"], gate_id
        assert report["release_ready"] is False, gate_id
        assert report["exit_code"] == 1, gate_id

    # optional 门 turn-tls：门自身如实 pending、summary 如实统计，但不进入
    # not_pass_required（不阻断本机/LAN 发布形态的 release_ready）。
    directory = _evidence_dir(tmp_path, "pending-turn-tls")
    _write_evidence(directory)
    _write_json(
        directory,
        "turn-tls.json",
        {
            "gate": "turn-tls",
            "checks": {
                "stun_binding": "pass",
                "tls_relay": "not_executed",
                "symmetric_nat_e2e": "not_executed",
            },
        },
    )
    _write_approval(directory)

    report = _run(directory)
    assert _statuses(report)["turn-tls"] == "pending"
    assert report["summary"]["pending"] == 1
    assert report["summary"]["pass"] == len(GATES) - 1
    assert report["not_pass_required"] == []
    assert report["not_pass_optional"] == ["turn-tls"]


def test_turn_tls_optional_pending_ready_with_scope_note(tmp_path, capsys) -> None:
    """turn-tls（optional 门）pending：本机/LAN 发布形态 release_ready=True /
    exit 0；但 manifest 与人类摘要必须明确声明 optional 边界与公网语音要求
    ——公网语音发布必须另行要求 turn-tls=pass，release_ready 不构成公网
    语音就绪结论（为绿灯弱化该声明才是回归）。"""
    directory = _evidence_dir(tmp_path, "turn-tls-optional-pending")
    evidence = _passing_evidence()
    evidence["turn-tls.json"]["checks"] = {
        "stun_binding": "pass",
        "tls_relay": "not_executed",
        "symmetric_nat_e2e": "not_executed",
    }
    _write_evidence(directory, evidence)
    _write_approval(directory)

    assert _cli(directory) == 0
    report = _run(directory)
    assert _statuses(report)["turn-tls"] == "pending"
    assert report["release_ready"] is True
    assert report["exit_code"] == 0
    assert report["not_pass_required"] == []
    # manifest 必须机器可读地透出 optional 边界（防 JSON 消费者误读 ready）
    assert report["not_pass_optional"] == ["turn-tls"]
    scope = report["optional_scope_note"]
    assert "turn-tls" in scope
    assert "公网语音" in scope
    assert "不得解释为公网语音就绪" in scope
    # 人类摘要同样声明边界，且明确 turn-tls=pass 才能放行公网语音
    out = capsys.readouterr().out
    assert "RELEASE READY" in out
    assert "optional" in out
    assert "公网语音发布必须另行要求 turn-tls=pass" in out
    assert "turn-tls" in out


def test_approval_missing_and_partial_coverage_pending(tmp_path, capsys) -> None:
    """无审批文件 -> missing；审批未覆盖全部必需门 -> pending（不是 pass）。"""
    directory = _evidence_dir(tmp_path, "no-approval")
    _write_evidence(directory)
    report = _run(directory)
    assert _statuses(report)["release-approval"] == "missing"
    assert report["release_ready"] is False

    directory = _evidence_dir(tmp_path, "partial-approval")
    _write_evidence(directory)
    _write_approval(directory, coverage=["ci-main", "release-check"])
    assert _cli(directory) == 1
    report = _run(directory)
    approval = _gate(report, "release-approval")
    assert approval["status"] == "pending"
    assert set(approval["data"]["coverage_gaps"]) == (
        REQUIRED_GATE_IDS - {"release-approval", "ci-main", "release-check"}
    )
    out = capsys.readouterr().out
    assert "NOT READY" in out
    assert "人工" in out


def test_malformed_approval_variants(tmp_path) -> None:
    """审批记录结构问题一律 malformed：schema 版本、窗口倒置、混时区、未知门。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    _write_approval(directory)
    good = json.loads((directory / "release-approval.json").read_text(encoding="utf-8"))

    variants = {
        "schema": {"schema_version": 2},
        "window-reversed": {
            "window": {
                "start": "2026-09-05T04:00:00+00:00",
                "end": "2026-09-05T02:00:00+00:00",
            }
        },
        "window-mixed-tz": {
            "window": {
                "start": "2026-09-05T02:00:00",
                "end": "2026-09-05T04:00:00+00:00",
            }
        },
        "unknown-gate": {"gate_evidence": {"ci-main": "ab" * 32, "no-such-gate": "cd" * 32}},
        "bad-hash-format": {"gate_evidence": {"ci-main": "not-hex"}},
        "empty-note": {"note": "  "},
    }
    for name, patch in variants.items():
        payload = {**good, **patch}
        _write_json(directory, "release-approval.json", payload)
        report = _run(directory)
        assert _statuses(report)["release-approval"] == "malformed", name
        assert "身份认证" not in _gate(report, "release-approval")["reason"]


# --- 6. blocked：证据在但结论为否 ----------------------------------------------


def test_blocked_semantics(tmp_path) -> None:
    cases = {
        "ci-main": (
            "ci-main.json",
            {
                "gate": "ci-main",
                "run_id": 1,
                "merge_commit": "38d90a0",
                "conclusion": "failure",
            },
        ),
        "release-check": (
            "release-check.json",
            {
                "gate": "release-check",
                "all_green": False,
                "total": 10,
                "passed": 9,
                "failed_ids": ["api-lint"],
            },
        ),
        "production-preflight": (
            "production-preflight.json",
            {
                "gate": "production-preflight",
                "phase": "post-migration",
                "summary": {"pass": 4, "pending": 0, "fail": 1, "not_configured": 0},
            },
        ),
        "audit-chain-anchor": (
            "audit-chain-anchor.json",
            {
                "gate": "audit-chain-anchor",
                "chain": {"valid": False, "entries": 69},
                "anchor": {"status": "up-to-date", "anchors": 2},
                "worm": {"archived": True},
            },
        ),
        "provider-smoke": (
            "provider-smoke.json",
            {
                "gate": "provider-smoke",
                "providers": {
                    "voice": {"executed": True, "result": "pass"},
                    "search": {"executed": True, "result": "fail"},
                    "llm": {"executed": True, "result": "pass"},
                },
            },
        ),
    }
    for gate_id, (filename, payload) in cases.items():
        directory = _evidence_dir(tmp_path, f"blocked-{gate_id}")
        _write_evidence(directory)
        _write_json(directory, filename, payload)
        _write_approval(directory)

        report = _run(directory)
        assert _statuses(report)[gate_id] == "blocked", gate_id
        assert report["release_ready"] is False
        assert report["exit_code"] == 1


# --- 7. 路径护栏与 IO：exit 2 --------------------------------------------------


def test_evidence_dir_guards_exit_2(tmp_path, capsys) -> None:
    # 不存在
    assert _cli(tmp_path / "no-such-dir") == 2
    assert "证据输入无效" in capsys.readouterr().out
    # 普通文件冒充目录
    fake = tmp_path / "not-a-dir"
    fake.write_text("x", encoding="utf-8")
    assert _cli(fake) == 2
    assert "不是目录" in capsys.readouterr().out


def test_evidence_dir_symlink_rejected(tmp_path, capsys) -> None:
    real = _evidence_dir(tmp_path, "real-evidence")
    link = tmp_path / "evidence-link"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    assert _cli(link) == 2
    assert "符号链接" in capsys.readouterr().out


def test_evidence_file_symlink_rejected(tmp_path, capsys) -> None:
    directory = _evidence_dir(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    link = directory / "ci-main.json"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    assert _cli(directory) == 2
    assert "符号链接" in capsys.readouterr().out


def test_evidence_file_name_occupied_by_directory(tmp_path, capsys) -> None:
    directory = _evidence_dir(tmp_path)
    (directory / "ci-main.json").mkdir()
    assert _cli(directory) == 2
    assert "不是常规文件" in capsys.readouterr().out


def test_output_path_guard(tmp_path, capsys) -> None:
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    _write_approval(directory)
    # 仓库外普通路径：拒绝（exit 2，manifest 不落盘）
    assert _cli(directory, output=tmp_path / "manifest.json") == 2
    assert "拒绝写入" in capsys.readouterr().out
    assert not (tmp_path / "manifest.json").exists()
    # artifacts/ 目录：允许并写入，内容与报告一致
    safe = tmp_path / "artifacts" / "readiness.json"
    assert _cli(directory, output=safe) == 0
    assert "报告已写入" in capsys.readouterr().out
    written = json.loads(safe.read_text(encoding="utf-8"))
    assert written["release_ready"] is True
    # generated_at 是墙钟时间，两次生成必然不同：剥离该字段后比较完整结构
    # （其余全部字段一致即守住「写入的就是本次生成的 manifest」）。
    fresh = _run(directory)
    written.pop("generated_at")
    fresh.pop("generated_at")
    assert written == fresh


def test_output_write_failure_keeps_silent(tmp_path, capsys) -> None:
    """输出父级被普通文件占用（mkdir 失败）：exit 2、不打印门结论摘要。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    _write_approval(directory)
    (tmp_path / "artifacts").write_text("occupied", encoding="utf-8")

    assert _cli(directory, output=tmp_path / "artifacts" / "r.json") == 2
    captured = capsys.readouterr()
    assert "报告写入失败" in captured.out
    assert "RESULT:" not in captured.out
    assert captured.err == ""


# --- 8. 只读、零敏感、环境无关 --------------------------------------------------


def test_run_is_read_only(tmp_path) -> None:
    """两轮运行后全部证据文件（含锚文件副本）字节不变；目录集合不变。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    (directory / "audit-anchor.jsonl").write_bytes(_anchor_companion_bytes())
    _write_approval(directory)

    before = {
        entry.name: entry.read_bytes()
        for entry in sorted(directory.iterdir())
        if entry.is_file()
    }
    assert _run(directory)["exit_code"] == 0
    assert _run(directory)["exit_code"] == 0
    after = {
        entry.name: entry.read_bytes()
        for entry in sorted(directory.iterdir())
        if entry.is_file()
    }
    assert before == after


def test_poisoned_environment_not_consulted(tmp_path, monkeypatch) -> None:
    """毒化 DB/API/key 环境变量：工具不读 env，行为与输出不受影响。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    _write_approval(directory)
    monkeypatch.setenv(
        "DATABASE_URL",
        f"postgresql://u:{PASSWORD_MARKER}@127.0.0.1:5433/ai_learning_os",
    )
    monkeypatch.setenv("LLM_API_KEY", API_KEY_MARKER)
    monkeypatch.setenv("AIOS_PG_TEST_URL", f"postgres://u:{TOKEN_MARKER}@h/db")

    report = _run(directory)
    assert report["exit_code"] == 0
    dumped = json.dumps(report)
    for marker in (PASSWORD_MARKER, API_KEY_MARKER, TOKEN_MARKER):
        assert marker not in dumped


def test_sensitive_markers_never_leak(tmp_path, capsys) -> None:
    """证据里嵌 marker（生产 ID/URL 凭据/审批说明内 token）：全输出面零泄漏。"""
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
    _write_approval(directory, note=f"审批说明内嵌 {TOKEN_MARKER}（不应回显）")

    safe = tmp_path / "artifacts" / "readiness.json"
    # ci-main 证据携带内嵌凭据 -> 该门 malformed（不凭其余字段变 pass），exit 1
    assert _cli(directory, as_json=True, output=safe) == 1
    json_out = capsys.readouterr().out
    human = format_readiness_summary(_run(directory))
    file_out = safe.read_text(encoding="utf-8")
    assert _statuses(json.loads(file_out))["ci-main"] == "malformed"
    for surface, text in (("json", json_out), ("human", human), ("file", file_out)):
        for marker in (PAPER_ID_MARKER, DRAFT_ID_MARKER, PASSWORD_MARKER, TOKEN_MARKER):
            assert marker not in text, (surface, marker)
        assert "postgresql+asyncpg" not in text, surface


def test_manifest_evidence_dir_is_caller_supplied_path_only(tmp_path) -> None:
    """evidence_dir 只回显调用方显式提供的目录（供审批复核定位证据来源）；
    manifest 其余字段不含任何其它绝对路径（不泄漏用户名/主机目录结构）。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    _write_approval(directory)

    report = _run(directory)
    assert report["evidence_dir"] == str(directory)
    dumped = json.dumps(report, ensure_ascii=False)
    # 剔除 evidence_dir 自身后，manifest 不再含本机绝对路径
    stripped = dumped.replace(str(directory), "")
    assert str(tmp_path) not in stripped
    assert json.dumps(report["unrecognized_files"], ensure_ascii=False) == "[]"


def test_unrecognized_files_reported_with_sha_only(tmp_path) -> None:
    """缺正确文件且存在笔误文件：门如实 missing，笔误文件只回显名 + sha256。"""
    directory = _evidence_dir(tmp_path)
    evidence = _passing_evidence()
    del evidence["ci-main.json"]  # 未提供正确文件（笔误场景的另一半）
    _write_evidence(directory, evidence)
    _write_approval(
        directory, coverage=sorted(GATE_IDS - {"release-approval", "ci-main"})
    )
    stray = directory / "ci_main.json"  # 下划线笔误
    stray.write_text(json.dumps({"gate": "ci-main"}), encoding="utf-8")

    report = _run(directory)
    assert _statuses(report)["ci-main"] == "missing"
    names = {item["file"] for item in report["unrecognized_files"]}
    assert names == {"ci_main.json"}
    entry = report["unrecognized_files"][0]
    assert entry["sha256"] == hashlib.sha256(stray.read_bytes()).hexdigest()
    assert "gate" not in json.dumps(entry)
