"""M10-11 release readiness：只读证据 manifest 的门矩阵/状态语义/安全边界。

覆盖矩阵：
1. 门矩阵与 CLI 注册：GATES 覆盖十一个发布审批门（无漏项/无虚设）、
   required/optional 划分、EVALUATORS 全覆盖、证据文件名唯一；CLI 子命令
   注册、无 --yes 执行形态（argparse exit 2）、main 分发；provider-smoke
   M14-70 聚合拓扑契约（topology.voice_mode 选轨 + evidence_step 配对 +
   键集合精确 fail-closed）与措辞口径：voice 按拓扑指向确切冒烟命令
   （local=smoke_voice_local.sh 本地链路探针；hybrid/cloud=M10-13
   smoke_voice_cloud.sh 部署 key + 真实短语音 WAV）、LLM 需部署 key、
   search 需真实端点冒烟且 SEARCH_CLOUD_API_KEY 可选（M10-12，不得回退
   「三类统一真实 key」旧口径）；
2. 全 pass：齐备证据 + 哈希绑定审批 -> release_ready=True / exit 0；每门
   evidence sha256 与文件字节独立重算一致；审批 data 记录覆盖门与零失配；
3. missing / malformed：空目录全 missing（exit 1）；单门缺失；非法 JSON /
   结构不符 / 自声明 gate 错位 / 自相矛盾计数一律 malformed；含敏感键的
   证据 malformed 且值零回显；
4. tampered：审批后改动证据 -> release-approval tampered（被改门自身仍
   pass）；审批绑定的门证据缺失 -> tampered；锚文件副本被改 / 申报计数与
   副本不一致 -> tampered；
5. pending 不伪装 pass：pre-migration 证据、治理计数 >0、冒烟未执行、恢复
   演练未 verified、锚定落后、WORM 未归档、24h 长稳窗口未满（固定词汇
   insufficient-*）、审批未覆盖全部必需门 -> 该门
   pending、release_ready=False、exit 1、人类摘要写明仍需人工；turn-tls 是
   optional 门：pending 如实透出但不阻断 release_ready（本机/LAN 发布形态），
   manifest（not_pass_optional + optional_scope_note）与人类摘要必须声明公网
   语音发布仍需 turn-tls=pass，release_ready 不含公网语音就绪结论；
6. blocked：CI failure / release-check 未全绿 / preflight fail / 链 invalid /
   冒烟 fail / 长稳窗口非 ok 样本或间隔超限 -> blocked；M14-73 long-soak 门
   另有专节：审计报告 v2 逐键契约（策略漂移、行数不变式、计数区间、时间窗
   恰 1440 分钟、96-97 边界、与工具 if/elif 判定序一致的单向蕴含、审批哈希
   绑定）全 fail-closed；
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
    "long-soak",
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


def _smoke_providers(mode: str = "local", **overrides) -> dict[str, dict]:
    """M14-70 聚合契约的 providers 满配形态（全 pass、evidence_step 按拓扑
    配对）；overrides 整项替换（构造 malformed/not_executed 形态用）。"""
    voice_step = "local-voice-smoke" if mode == "local" else "cloud-voice-smoke"
    steps = {"voice": voice_step, "search": "search-smoke", "llm": "llm-smoke"}
    providers = {
        name: {"executed": True, "result": "pass", "evidence_step": step}
        for name, step in steps.items()
    }
    providers.update(overrides)
    return providers


def _not_executed(step: str) -> dict:
    """聚合契约下的 not_executed 声明形态（executed=false 配对一致）。"""
    return {"executed": False, "result": "not_executed", "evidence_step": step}


def _smoke_document(
    mode: str = "local", providers: dict[str, dict] | None = None
) -> dict:
    """M14-70 聚合契约的 provider-smoke.json 形态（默认 local 拓扑）。"""
    return {
        "gate": "provider-smoke",
        "topology": {"voice_mode": mode},
        "providers": providers if providers is not None else _smoke_providers(mode),
    }


def _soak_report(**overrides) -> dict:
    """M14-73 long-soak.json 满配 pass 形态：audit_schema_version=2、97 样本
    全 ok、跨度恰 1440 分钟（闭区间 1440/15+1=97）；overrides 顶层整项替换
    （构造 pending/blocked/malformed 变体用）。"""
    report = {
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
    report.update(overrides)
    return report


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
        "provider-smoke.json": _smoke_document(),
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
    """十一个发布审批门无漏项/无虚设；optional 仅 turn-tls；评估器全覆盖。"""
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
    """provider-smoke 措辞与三类 provider 的真实要求一致，且语音指引按拓扑
    选轨（M14-70）：local 指向 bash infra/smoke_voice_local.sh 本地语音链路
    探针（无需云 key，evidence_step=local-voice-smoke）；hybrid/cloud 指向
    M10-13 bash infra/smoke_voice_cloud.sh（部署 key + 真实短语音 WAV，
    evidence_step=cloud-voice-smoke）；LLM 需部署 key，search 需真实端点冒烟
    且 SEARCH_CLOUD_API_KEY 可选（M10-12：无鉴权 SearXNG 合法，不得把 search
    冒烟说成必须有 key，也不得把「真实 key 冒烟」口径统一套在三类上）。
    措辞只是口径修正——门语义不放宽：仍是 required gate，not_executed ->
    pending、fail -> blocked 由既有语义测试守卫。"""
    spec = next(s for s in GATES if s.gate_id == "provider-smoke")
    assert spec.required is True
    # title 保持一行可读的合并口径；basis 按 provider 拆分（voice 按拓扑双轨）
    assert "voice" in spec.title and "按拓扑" in spec.title
    assert "部署 key" in spec.title
    assert "search" in spec.title and "真实端点" in spec.title
    for provider in ("voice", "LLM"):
        assert provider in spec.basis, spec.basis
    assert "部署 key" in spec.basis, spec.basis
    assert "search" in spec.basis and "真实端点" in spec.basis, spec.basis
    # 两条 voice 冒烟命令与各自 evidence_step 在 basis 里显式给出并配对
    # （M14-70 local 探针 + M10-13 云链路）
    assert "smoke_voice_local.sh" in spec.basis
    assert "local-voice-smoke" in spec.basis
    assert "smoke_voice_cloud.sh" in spec.basis
    assert "cloud-voice-smoke" in spec.basis
    assert "真实短语音" in spec.basis
    # key 可选语义在 basis 里显式声明（title 保持一行可读）
    assert "SEARCH_CLOUD_API_KEY" in spec.basis
    assert "可选" in spec.basis

    # pending（cloud 拓扑）：search/llm 未执行——指引区分部署 key 与真实
    # 端点（key 可选），voice 指向云链路命令
    directory = _evidence_dir(tmp_path, "smoke-wording-pending")
    evidence = _passing_evidence()
    evidence["provider-smoke.json"] = _smoke_document(
        mode="cloud",
        providers=_smoke_providers(
            "cloud",
            search=_not_executed("search-smoke"),
            llm=_not_executed("llm-smoke"),
        ),
    )
    _write_evidence(directory, evidence)
    _write_approval(directory)
    gate = _gate(_run(directory), "provider-smoke")
    assert gate["status"] == "pending"
    assert "search, llm" in gate["reason"]
    assert "部署 key" in gate["reason"]
    assert "真实端点" in gate["reason"]
    assert "smoke_voice_cloud.sh" in gate["reason"]
    assert "SEARCH_CLOUD_API_KEY" in gate["reason"] and "可选" in gate["reason"]

    # pending（local 拓扑）：voice 未执行——指引只指向本地探针，不声称需要
    # 云 key，也不得把 cloud 命令错挂到 local 拓扑
    evidence["provider-smoke.json"] = _smoke_document(
        providers=_smoke_providers(
            "local", voice=_not_executed("local-voice-smoke")
        ),
    )
    _write_evidence(directory, evidence)
    gate = _gate(_run(directory), "provider-smoke")
    assert gate["status"] == "pending"
    assert "未执行冒烟: voice" in gate["reason"]
    assert "smoke_voice_local.sh" in gate["reason"]
    assert "无需云 key" in gate["reason"]
    assert "smoke_voice_cloud.sh" not in gate["reason"]

    # blocked：search 冒烟失败（无 key 合法端点也可能失败）——指引同口径区分
    directory = _evidence_dir(tmp_path, "smoke-wording-fail")
    evidence = _passing_evidence()
    evidence["provider-smoke.json"] = _smoke_document(
        mode="cloud",
        providers=_smoke_providers(
            "cloud",
            search={
                "executed": True,
                "result": "fail",
                "evidence_step": "search-smoke",
            },
        ),
    )
    _write_evidence(directory, evidence)
    _write_approval(directory)
    gate = _gate(_run(directory), "provider-smoke")
    assert gate["status"] == "blocked"
    assert "search" in gate["reason"]
    assert "部署 key" in gate["reason"] and "真实端点" in gate["reason"]
    assert "smoke_voice_cloud.sh" in gate["reason"]
    assert "SEARCH_CLOUD_API_KEY" in gate["reason"] and "可选" in gate["reason"]

    # pass：全部通过（默认 local 拓扑）——通过口径区分三类要求并声明当前
    # 拓扑与 voice 证据源，不虚称「真实 key 冒烟」
    directory = _evidence_dir(tmp_path, "smoke-wording-pass")
    _write_evidence(directory)
    _write_approval(directory)
    report = _run(directory)
    gate = _gate(report, "provider-smoke")
    assert gate["status"] == "pass"
    assert "本地语音链路" in gate["reason"]
    assert "local-voice-smoke" in gate["reason"]
    assert "部署 key" in gate["reason"] and "真实端点" in gate["reason"]
    assert "SEARCH_CLOUD_API_KEY" in gate["reason"] and "可选" in gate["reason"]
    # 旧口径（三类统一「真实 key 冒烟」）不得回流到门文案
    assert "真实 key" not in gate["reason"]
    assert "真实 key" not in spec.title and "真实 key" not in spec.basis


@pytest.mark.parametrize("mode", ["local", "hybrid", "cloud"])
def test_provider_smoke_topology_tracks_all_pass(tmp_path, mode) -> None:
    """M14-70 聚合契约：三种拓扑全 pass 均放行——voice 的 evidence_step 按
    拓扑配对（local=local-voice-smoke，hybrid/cloud=cloud-voice-smoke），
    data 完整透出 topology 与逐 provider evidence_step。"""
    directory = _evidence_dir(tmp_path, f"smoke-topology-{mode}")
    evidence = _passing_evidence()
    evidence["provider-smoke.json"] = _smoke_document(mode)
    _write_evidence(directory, evidence)
    _write_approval(directory)

    gate = _gate(_run(directory), "provider-smoke")
    assert gate["status"] == "pass", gate["reason"]
    expected_voice_step = (
        "local-voice-smoke" if mode == "local" else "cloud-voice-smoke"
    )
    assert gate["data"]["topology"] == {"voice_mode": mode}
    assert gate["data"]["providers"]["voice"]["evidence_step"] == (
        expected_voice_step
    )
    assert gate["data"]["providers"]["search"]["evidence_step"] == "search-smoke"
    assert gate["data"]["providers"]["llm"]["evidence_step"] == "llm-smoke"
    # 通过口径声明当前拓扑与对应 voice 证据源
    assert mode in gate["reason"]
    assert expected_voice_step in gate["reason"]


@pytest.mark.parametrize(
    ("smoke_doc", "reason_fragment"),
    [
        # 缺 topology：M14-70 之前的旧形态整份证据直接 fail-closed
        (
            {"gate": "provider-smoke", "providers": _smoke_providers()},
            "缺字段 topology",
        ),
        # topology 多余键：恰为 voice_mode 单键
        (
            {
                "gate": "provider-smoke",
                "topology": {"voice_mode": "local", "region": "cn"},
                "providers": _smoke_providers(),
            },
            "topology 键必须恰为",
        ),
        # 非法 voice_mode 取值
        (
            {
                "gate": "provider-smoke",
                "topology": {"voice_mode": "cloud-only"},
                "providers": _smoke_providers(),
            },
            "voice_mode 必须是",
        ),
        # 缺 evidence_step：entry 键集合恰为 executed/result/evidence_step
        (
            _smoke_document(
                providers=_smoke_providers(
                    "local", voice={"executed": True, "result": "pass"}
                )
            ),
            "providers.voice 键必须恰为",
        ),
        # entry 多余键：单步 exit_code 等脚本细节不进本门证据
        (
            _smoke_document(
                providers=_smoke_providers(
                    "local",
                    voice={
                        "executed": True,
                        "result": "pass",
                        "evidence_step": "local-voice-smoke",
                        "exit_code": 0,
                    },
                )
            ),
            "providers.voice 键必须恰为",
        ),
        # providers 多余槽位
        (
            _smoke_document(
                providers={
                    **_smoke_providers(),
                    "tts": {
                        "executed": True,
                        "result": "pass",
                        "evidence_step": "local-voice-smoke",
                    },
                }
            ),
            "providers 键必须恰为",
        ),
        # 拓扑与 step 配对错误：local 声称 cloud-voice-smoke
        (
            _smoke_document(
                providers=_smoke_providers(
                    "local",
                    voice={
                        "executed": True,
                        "result": "pass",
                        "evidence_step": "cloud-voice-smoke",
                    },
                )
            ),
            "拓扑不符（应为 local-voice-smoke）",
        ),
        # 反向配对错误：cloud 拓扑声称 local-voice-smoke
        (
            _smoke_document(
                mode="cloud",
                providers=_smoke_providers(
                    "cloud",
                    voice={
                        "executed": True,
                        "result": "pass",
                        "evidence_step": "local-voice-smoke",
                    },
                ),
            ),
            "拓扑不符（应为 cloud-voice-smoke）",
        ),
    ],
    ids=[
        "missing-topology",
        "extra-topology-key",
        "invalid-voice-mode",
        "missing-evidence-step",
        "extra-entry-key",
        "extra-provider-key",
        "local-claims-cloud-step",
        "cloud-claims-local-step",
    ],
)
def test_provider_smoke_contract_violations_are_malformed(
    tmp_path, smoke_doc, reason_fragment
) -> None:
    """M14-70 聚合契约 fail-closed：缺 topology/evidence_step、topology 或
    entry/providers 多余键、非法 voice_mode、拓扑与 step 配对错误一律
    malformed——聚合器是本门证据的唯一合法生产者，旁路拼装不收。"""
    directory = _evidence_dir(tmp_path, "smoke-contract-malformed")
    evidence = _passing_evidence()
    evidence["provider-smoke.json"] = smoke_doc
    _write_evidence(directory, evidence)
    _write_approval(directory)

    report = _run(directory)
    gate = _gate(report, "provider-smoke")
    assert gate["status"] == "malformed"
    assert reason_fragment in gate["reason"], gate["reason"]
    assert report["exit_code"] == 1


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
        "long-soak": (
            "long-soak.json",
            # 工具 classify 单原因发射：覆盖未达窗口起点 -> 仅
            # insufficient-clean-coverage（96 样本、跨度 1425 分钟）
            _soak_report(
                classification="pending",
                reasons=["insufficient-clean-coverage"],
                selected_row_count=96,
                window_status_counts={"ok": 96, "warn": 0, "critical": 0},
                selected_span_minutes=1425.0,
            ),
        ),
        "provider-smoke": (
            "provider-smoke.json",
            _smoke_document(
                providers=_smoke_providers(
                    "local",
                    search=_not_executed("search-smoke"),
                    llm=_not_executed("llm-smoke"),
                )
            ),
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
        "long-soak": (
            "long-soak.json",
            # 工具 classify 单原因发射：非 ok 优先于间隔超限 -> 仅
            # non-ok-status-in-window（97 选 94 ok/3 warn）
            _soak_report(
                classification="blocked",
                reasons=["non-ok-status-in-window"],
                window_status_counts={"ok": 94, "warn": 3, "critical": 0},
                window_non_ok_count=3,
            ),
        ),
        "provider-smoke": (
            "provider-smoke.json",
            _smoke_document(
                providers=_smoke_providers(
                    "local",
                    search={
                        "executed": True,
                        "result": "fail",
                        "evidence_step": "search-smoke",
                    },
                )
            ),
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


# --- 6b. M14-73 long-soak：24h 长稳审计门 fail-closed 专节 ---------------------


def test_long_soak_pass_gate_semantics(tmp_path) -> None:
    """真实 24h 稳定窗口 pass：97 样本全 ok/跨度恰 1440/最大间隔 <= 20，
    结论携带 anchor 与输入 sha256 前缀供审批复核定位原始 history。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    _write_approval(directory)

    report = _run(directory)
    gate = _gate(report, "long-soak")
    assert gate["status"] == "pass"
    assert "真实 24h 稳定窗口 pass" in gate["reason"]
    assert "97 样本全 ok" in gate["reason"]
    assert "跨度恰 1440 分钟" in gate["reason"]
    assert "cdcdcdcdcdcd" in gate["reason"]  # input.sha256 前 12 位
    assert gate["data"]["classification"] == "pass"
    assert gate["data"]["audit_schema_version"] == 2
    assert gate["data"]["input"]["sha256"] == "cd" * 32


def test_release_ready_requires_long_soak_pass(tmp_path) -> None:
    """缺 long-soak.json：该门 missing、release_ready=False——M14-73 前的
    十门绿态不再构成 ready（fail-closed 升级回归锚）。"""
    directory = _evidence_dir(tmp_path)
    evidence = _passing_evidence()
    del evidence["long-soak.json"]
    _write_evidence(directory, evidence)
    _write_approval(
        directory, coverage=sorted(GATE_IDS - {"release-approval", "long-soak"})
    )

    report = _run(directory)
    assert _statuses(report)["long-soak"] == "missing"
    assert "long-soak" in report["not_pass_required"]
    assert report["release_ready"] is False
    assert report["exit_code"] == 1


def test_long_soak_pending_reasons_each_variant(tmp_path) -> None:
    """两种 pending 固定词汇各自成立（工具单原因发射），如实透出并指引
    补齐窗口——绝不伪装 pass。"""
    fragments = {
        "insufficient-clean-coverage": "干净覆盖不足",
        "insufficient-sample-count": "样本数不足",
    }
    variants = {
        # 覆盖未达窗口起点（样本可已凑满 97 但跨度不足 24h）
        "insufficient-clean-coverage": _soak_report(
            classification="pending",
            reasons=["insufficient-clean-coverage"],
            selected_span_minutes=1400.0,
        ),
        # 覆盖已达窗口起点但闭区间样本数不足 97
        "insufficient-sample-count": _soak_report(
            classification="pending",
            reasons=["insufficient-sample-count"],
            selected_row_count=96,
            window_status_counts={"ok": 96, "warn": 0, "critical": 0},
        ),
    }
    for name, payload in variants.items():
        directory = _evidence_dir(tmp_path, f"soak-pending-{name}")
        _write_evidence(directory)
        _write_json(directory, "long-soak.json", payload)
        _write_approval(directory)

        report = _run(directory)
        gate = _gate(report, "long-soak")
        assert gate["status"] == "pending", (name, gate["reason"])
        assert gate["data"]["reasons"] == [name]
        assert fragments[name] in gate["reason"]
        assert "补齐干净窗口" in gate["reason"]  # 下一步指引而非空泛等待
        assert "long-soak" in report["not_pass_required"], name
        assert report["release_ready"] is False, name
        assert report["exit_code"] == 1, name


def test_long_soak_blocked_reasons_each_variant(tmp_path) -> None:
    """两种 blocked 固定词汇各自成立：窗口非 ok 样本 / 间隔超限。"""
    variants = {
        "non-ok-status-in-window": _soak_report(
            classification="blocked",
            reasons=["non-ok-status-in-window"],
            window_status_counts={"ok": 94, "warn": 3, "critical": 0},
            window_non_ok_count=3,
        ),
        "excessive-gap-in-window": _soak_report(
            classification="blocked",
            reasons=["excessive-gap-in-window"],
            max_observed_gap_minutes=35.0,
        ),
    }
    for name, payload in variants.items():
        directory = _evidence_dir(tmp_path, f"soak-blocked-{name}")
        _write_evidence(directory)
        _write_json(directory, "long-soak.json", payload)
        _write_approval(directory)

        report = _run(directory)
        gate = _gate(report, "long-soak")
        assert gate["status"] == "blocked", (name, gate["reason"])
        assert gate["data"]["reasons"] == [name]
        assert "存在稳定性问题" in gate["reason"]
        assert "不得发布" in gate["reason"]
        assert "long-soak" in report["not_pass_required"], name
        assert report["release_ready"] is False, name
        assert report["exit_code"] == 1, name


def test_long_soak_contract_violations_are_malformed(tmp_path) -> None:
    """v2 报告逐键契约：schema/工具/策略漂移/行数不变式/计数区间/时间窗/
    自相矛盾原因/96-97 边界一律 malformed（exit 1，绝不放行）。"""

    def _policy(**kw) -> dict:
        base = {
            "window_minutes": 1440,
            "expected_interval_minutes": 15,
            "max_gap_minutes": 20,
            "retention": 500,
        }
        base.update(kw)
        return base

    missing_reasons = _soak_report()
    del missing_reasons["reasons"]
    cases = {
        "gate-self-id": (_soak_report(gate="ci-main"), "['long-soak'] 之一"),
        "extra-top-key": ({**_soak_report(), "synthetic": True}, "顶层键必须恰为"),
        "missing-top-key": (missing_reasons, "顶层键必须恰为"),
        "schema-version-drift": (_soak_report(schema_version=2), "schema_version 非 1"),
        "audit-schema-v1": (
            _soak_report(audit_schema_version=1),
            "v1 报告不再被本门接受",
        ),
        "wrong-tool": (_soak_report(tool="tools/ops/other.py"), "tool 必须是"),
        "policy-window": (
            _soak_report(settings=_policy(window_minutes=720)),
            "settings.window_minutes=720 与本门策略值 1440 不符",
        ),
        "policy-interval": (
            _soak_report(settings=_policy(expected_interval_minutes=5)),
            "expected_interval_minutes=5 与本门策略值 15 不符",
        ),
        "policy-gap": (
            _soak_report(settings=_policy(max_gap_minutes=30)),
            "max_gap_minutes=30 与本门策略值 20 不符",
        ),
        "policy-retention": (
            _soak_report(settings=_policy(retention=200)),
            "retention=200 与本门策略值 500 不符",
        ),
        "input-shape": (
            _soak_report(input={"sha256": "cd" * 32}),
            "input 键必须恰为",
        ),
        "row-invariant": (_soak_report(analyzed_row_count=96), "行数不变式不成立"),
        "omitted-invariant": (
            _soak_report(row_count=520, analyzed_row_count=505, omitted_older_count=15),
            "omitted_older_count(15)",
        ),
        "window-delta": (
            _soak_report(anchor_collected_at="2026-09-20T03:15:02Z"),
            "不等于 1440 分钟",
        ),
        "selected-above-analyzed": (
            _soak_report(
                selected_row_count=98,
                window_status_counts={"ok": 98, "warn": 0, "critical": 0},
            ),
            "selected_row_count(98) > analyzed_row_count(97)",
        ),
        "counts-sum": (
            _soak_report(window_status_counts={"ok": 94, "warn": 2, "critical": 0}),
            "状态计数之和 96",
        ),
        "counts-keys": (
            _soak_report(window_status_counts={"ok": 97, "warn": 0}),
            "window_status_counts 键必须恰为",
        ),
        "non-ok-below-interval": (
            _soak_report(
                classification="blocked",
                reasons=["non-ok-status-in-window"],
                window_status_counts={"ok": 94, "warn": 3, "critical": 0},
                window_non_ok_count=2,
            ),
            "不在 [warn+critical=3, selected=97] 区间",
        ),
        "non-ok-above-selected": (
            _soak_report(window_non_ok_count=98),
            "不在 [warn+critical=0, selected=97] 区间",
        ),
        # 96/97 边界：闭区间完整序列恰 97，96 个样本不构成 pass
        "pass-below-min-samples": (
            _soak_report(
                selected_row_count=96,
                window_status_counts={"ok": 96, "warn": 0, "critical": 0},
            ),
            "闭区间最少样本 97",
        ),
        "pass-with-reasons": (
            _soak_report(reasons=["insufficient-clean-coverage"]),
            "classification=pass 但 reasons",
        ),
        "pass-span-short": (
            _soak_report(selected_span_minutes=1400.0),
            "!= 恰 1440 分钟",
        ),
        "pass-gap-over": (
            _soak_report(max_observed_gap_minutes=21.0),
            "max_observed_gap_minutes=21.0 > 20",
        ),
        "unknown-reason": (
            _soak_report(classification="blocked", reasons=["made-up-reason"]),
            "固定词汇外原因",
        ),
        "pending-with-non-ok": (
            _soak_report(
                classification="pending",
                reasons=["insufficient-clean-coverage"],
                selected_span_minutes=1400.0,
                selected_row_count=97,
                window_status_counts={"ok": 96, "warn": 1, "critical": 0},
                window_non_ok_count=1,
            ),
            "应为 blocked",
        ),
        "pending-clean-coverage-span-full": (
            _soak_report(
                classification="pending",
                reasons=["insufficient-clean-coverage"],
            ),
            "已 >= 1440（自相矛盾）",
        ),
        "pending-sample-count-satisfied": (
            _soak_report(
                classification="pending",
                reasons=["insufficient-sample-count"],
            ),
            "声称样本数不足但 selected=97",
        ),
        "blocked-claim-non-ok-but-clean": (
            _soak_report(
                classification="blocked",
                reasons=["non-ok-status-in-window"],
            ),
            "window_non_ok_count=0（自相矛盾）",
        ),
        "blocked-claim-gap-but-compliant": (
            _soak_report(
                classification="blocked",
                reasons=["excessive-gap-in-window"],
            ),
            "间隔超限但 max_observed_gap_minutes=15.0",
        ),
        # 工具 if/elif 判定序：非 ok 存在时只会发 non-ok 原因——
        # 非 ok > 0 却申报 gap 原因，与唯一合法生产者矛盾
        "blocked-non-ok-unclaimed": (
            _soak_report(
                classification="blocked",
                reasons=["excessive-gap-in-window"],
                window_status_counts={"ok": 94, "warn": 3, "critical": 0},
                window_non_ok_count=3,
                max_observed_gap_minutes=35.0,
            ),
            "未声明 non-ok-status-in-window",
        ),
    }
    for name, (payload, fragment) in cases.items():
        directory = _evidence_dir(tmp_path, f"soak-bad-{name}")
        _write_evidence(directory)
        _write_json(directory, "long-soak.json", payload)
        _write_approval(directory)

        report = _run(directory)
        gate = _gate(report, "long-soak")
        assert gate["status"] == "malformed", (name, gate["reason"])
        assert fragment in gate["reason"], (name, gate["reason"])
        assert report["release_ready"] is False, name
        assert report["exit_code"] == 1, name


def test_long_soak_approval_binding_tampered(tmp_path) -> None:
    """审批后再改 long-soak.json 字节 -> release-approval tampered（哈希
    失配精确指向 long-soak），release_ready 回落 False——第十一门纳入
    审批哈希绑定面。"""
    directory = _evidence_dir(tmp_path)
    _write_evidence(directory)
    _write_approval(directory)
    assert _run(directory)["release_ready"] is True  # 先确立含 long-soak 的绿态

    edited = _soak_report(
        classification="pending",
        reasons=["insufficient-sample-count"],
        selected_row_count=96,
        window_status_counts={"ok": 96, "warn": 0, "critical": 0},
    )
    _write_json(directory, "long-soak.json", edited)

    report = _run(directory)
    assert _statuses(report)["long-soak"] == "pending"
    approval = _gate(report, "release-approval")
    assert approval["status"] == "tampered"
    assert approval["data"]["hash_mismatches"] == ["long-soak"]
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
