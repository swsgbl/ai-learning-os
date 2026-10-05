"""Contract tests for tools.android_release.public_mobile_release_gate (M14-237).

All fixtures are synthetic — no network, no device, no Docker, no secret, no
subprocess. The gate is an aggregation-only, fail-closed consumer of
machine-exported evidence plus one constrained human attestation; these tests
pin that contract: schema/tool identity (including the repository-existing
provider-smoke-aggregate / release-check allowlist for release evidence),
byte/hash anchoring, timestamp freshness anchored to an **injected fixed
evaluation time** (CLI uses real current UTC; top-level generated_at is the
evaluation time, reference.evidence_frontier is the max machine evidence
timestamp), status consumption, cross-report consistency,
release-authorization separation, path/symlink refusal, atomic output with
read-back verification, and actionable read-only next-step commands.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.android_release import public_mobile_release_gate as gate
from tools.ops.monitoring_history import RealStore

# 合成端点/标识（绝无真实数据）：example.com 族保留域，仅作 fixture。
EDGE_HOST = "edge.example.com"
CF_ZONE = "example.com"
T0 = "2026-10-05T00:00:00Z"
T0_LATE = "2026-10-05T00:05:00+00:00"  # 同一时刻的 +00:00 形态（解析兼容）
EVAL_TIME = "2026-10-05T00:30:00Z"  # 注入的固定评估时刻（CLI 为真实 UTC）
FUTURE = "2026-10-05T01:30:00Z"  # 评估时刻 + 1h：未来时间用
STALE = "2026-10-03T00:00:00Z"  # 评估时刻 - 54h：陈旧证据用
VALID_UNTIL = "2026-10-12T00:00:00Z"


# ---------------------------------------------------------------- fixtures


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_restore(tmp: Path, *, generated_at=T0_LATE, verdict="healthy",
                 blockers=None, schema=True, tool=True) -> Path:
    payload = {
        "schema": gate.RESTORE_SCHEMA if schema else "aios-production-restore-preflight/1",
        "tool": gate.RESTORE_TOOL if tool else "some_other_tool",
        "generated_at": generated_at,
        "verdict": verdict,
        "blockers": blockers or [],
    }
    return _write_json(tmp / "restore" / "report.json", payload)


def make_edge(tmp: Path, *, generated_at=T0_LATE, exit_code=0, attested=True,
              schema=True, tool=True) -> Path:
    payload = {
        "schema": gate.EDGE_SCHEMA if schema else "aios-public-edge-preflight/2",
        "tool": gate.EDGE_TOOL if tool else "some_other_tool",
        "generated_at": generated_at,
        "endpoints": {
            "app_url": f"https://{EDGE_HOST}",
            "api_url": f"https://{EDGE_HOST}/aios",
            "livekit_url": f"https://{EDGE_HOST}",
            "turn_host": EDGE_HOST,
        },
        "summary": {"total": 12, "passed": 12, "failed": 0},
        "mobile_attestation": {"status": "attested" if attested else "pending"},
        "exit_code": exit_code,
    }
    return _write_json(tmp / "edge" / "report.json", payload)


def make_smoke(tmp: Path, *, generated_at=T0_LATE, status="passed",
               tamper_evidence=False, schema_version=1, tool=True) -> Path:
    out = tmp / "smoke"
    out.mkdir(parents=True, exist_ok=True)
    evidence_bytes = b"synthetic-device-smoke-launch.png"
    evidence_name = "launch.png"
    (out / evidence_name).write_bytes(evidence_bytes)
    digest = _sha256_bytes(evidence_bytes)
    if tamper_evidence:
        digest = "0" * 64
    payload = {
        "schema_version": schema_version,
        "tool": gate.SMOKE_TOOL if tool else "some_other_tool",
        "generated_at": generated_at,
        "status": status,
        "exit_code": 0 if status == "passed" else 2,
        "public_ready": False,
        "boundary": "single public artifact physical-device smoke",
        "serial": "SYNTH-Serial-001",
        "artifact": {
            "manifest_url": f"https://{EDGE_HOST}/download-manifest.json",
            "apk_url": f"https://{EDGE_HOST}/app-release.apk",
        },
        "evidence": {
            "files": [
                {"name": evidence_name, "size_bytes": len(evidence_bytes),
                 "sha256": digest}
            ]
        },
        "failures": [],
    }
    return _write_json(out / "report.json", payload)


def make_cloudflare(tmp: Path, *, generated_at=T0_LATE, status="pass",
                    zone=CF_ZONE) -> Path:
    payload = {
        "schema": gate.CF_SCHEMA,
        "tool": gate.CF_TOOL,
        "generated_at": generated_at,
        "mode": "execute",
        "zone_name": zone,
        "status": status,
        "exit_code": 0 if status == "pass" else 1,
    }
    return _write_json(tmp / "cloudflare" / "report.json", payload)


def make_release_evidence(
    tmp: Path, *, generated_at=T0_LATE, tool=None, schema=None, gate=None,
    provider_result="pass",
) -> Path:
    """provider-smoke-aggregate 权威契约（真实导出器
    services/api/app/ops/provider_smoke_evidence.py 的聚合形态）。"""
    payload = {
        "tool": tool if tool is not None else "provider-smoke-evidence",
        "schema_version": schema
        if schema is not None else "provider-smoke-evidence-v1",
        "gate": gate if gate is not None else "provider-smoke",
        "providers": {
            key: {"executed": True, "result": provider_result,
                  "evidence_step": f"provider-smoke-{key}"}
            for key in ("voice", "search", "llm")
        },
        "generated_at": generated_at,
    }
    return _write_json(tmp / "release-evidence" / "status.json", payload)


def make_release_check(tmp: Path, *, generated_at=T0_LATE, all_green=True) -> Path:
    """release-check 权威契约（真实导出器 services/api/app/ops/release_check.py）。"""
    payload = {
        "tool": "release-check",
        "gate": "release-check",
        "step": "release-check",
        "generated_at": generated_at,
        "execution_scope": "isolated",
        "all_green": all_green,
        "total": 6,
        "passed": 6 if all_green else 5,
        "failed_ids": [],
        "not_executed_ids": [] if all_green else ["live-api"],
        "checks": [],
    }
    return _write_json(tmp / "release-evidence" / "release-check.json", payload)


def make_attestation(tmp: Path, *, observed_at=T0, valid_until=VALID_UNTIL,
                     covered=None, anchors=None, conclusion="pass",
                     observed_by="synthetic-observer") -> Path:
    """anchors: list[(basename, sha256)]；None = 自动锚定全部机器输入。"""
    if covered is None:
        covered = list(gate.REQUIRED_ATTESTED_ITEMS)
    files = []
    for name, digest in (anchors if anchors is not None else []):
        files.append({"name": name, "sha256": digest})
    payload = {
        "schema_version": gate.ATTESTATION_SCHEMA_VERSION,
        "kind": gate.ATTESTATION_KIND,
        "observed_by": observed_by,
        "observed_at": observed_at,
        "valid_until": valid_until,
        "conclusion": conclusion,
        "covered_items": covered,
        "evidence_files": files,
    }
    return _write_json(tmp / "attestation" / "attestation.json", payload)


def build_all(tmp: Path, **overrides) -> dict:
    """标准全绿 fixture；overrides 键同 run_gate 参数名（值为 Path 或 None）。

    override 的 Path 由调用方先构造（写盘），这里**不再**用默认 fixture
    覆盖同一路径——默认构造仅在未提供时发生。
    """
    restore = overrides.pop("restore", None) or make_restore(tmp)
    edge = overrides.pop("edge", None) or make_edge(tmp)
    smoke = overrides.pop("smoke", None) or make_smoke(tmp)
    evidence = (
        overrides.pop("release_evidence", None) or make_release_evidence(tmp)
    )
    anchors = [
        (item.name, _sha256_bytes(item.read_bytes()))
        for item in (restore, edge, smoke, evidence)
        if item.exists()
    ]
    attestation = (
        overrides.pop("attestation", None) or make_attestation(tmp, anchors=anchors)
    )
    inputs = {
        "restore": restore,
        "edge": edge,
        "smoke": smoke,
        "cloudflare": overrides.pop("cloudflare", None),
        "release_evidence": evidence,
        "attestation": attestation,
    }
    assert not overrides, f"unknown overrides: {sorted(overrides)}"
    return inputs


def run_inputs(tmp: Path, inputs: dict, *, freshness_hours=24,
               evaluation_time=None):
    return gate.run_gate(
        RealStore(),
        restore=inputs["restore"],
        edge=inputs["edge"],
        smoke=inputs["smoke"],
        cloudflare=inputs["cloudflare"],
        release_evidence=inputs["release_evidence"],
        attestation=inputs["attestation"],
        output_dir=tmp / "out",
        freshness_hours=freshness_hours,
        evaluation_time=evaluation_time
        if evaluation_time is not None
        else _fixed_eval(),
    )


def _fixed_eval():
    return gate.parse_utc_timestamp(EVAL_TIME)


# ---------------------------------------------------------------- happy path


def test_all_pass_ready_with_atomic_output_and_readback(tmp_path):
    inputs = build_all(tmp_path)
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_READY
    assert report["public_mobile_ready"] is True
    assert report["blockers"] == []
    # 顶层 generated_at = 注入的评估时刻；reference.evidence_frontier =
    # 机器证据最大时间戳（T0_LATE 的 UTC 规范形）。两者语义分离。
    assert report["generated_at"] == "2026-10-05T00:30:00Z"
    assert report["reference"]["evaluation_time"] == "2026-10-05T00:30:00Z"
    assert report["reference"]["evidence_frontier"] == "2026-10-05T00:05:00Z"
    # 输入显式分类。
    assert report["inputs"]["restore_preflight"]["classification"] == "machine-report"
    assert report["inputs"]["attestation"]["classification"] == "human-attestation"
    assert report["inputs"]["release_evidence"]["classification"] == "evidence-status"
    # 字节级锚定。
    smoke_entry = report["inputs"]["device_smoke"]
    assert smoke_entry["sha256"] == _sha256_bytes(inputs["smoke"].read_bytes())
    # 原子写 + 重读校验：输出文件与报告逐字节一致。
    out_json = tmp_path / "out" / gate.REPORT_JSON_NAME
    out_md = tmp_path / "out" / gate.REPORT_MD_NAME
    assert json.loads(out_json.read_text(encoding="utf-8")) == report
    assert "public_mobile_ready: True" in out_md.read_text(encoding="utf-8")
    # 与 release authorization 分离（即使 ready 也不伪造）。
    auth = report["release_authorization"]
    assert auth["human_release_approval"] == "not-asserted"
    assert auth["production_readiness"] == "not-asserted"


def test_optional_cloudflare_pass_keeps_ready(tmp_path):
    inputs = build_all(tmp_path, cloudflare=make_cloudflare(tmp_path))
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_READY
    assert report["inputs"]["cloudflare_preflight"]["present"] is True


# ---------------------------------------------------------------- 缺失/损坏


def test_missing_attestation_file_is_blocked_with_next_action(tmp_path):
    inputs = build_all(tmp_path)
    inputs["attestation"] = tmp_path / "attestation" / "nope.json"
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert report["public_mobile_ready"] is False
    assert "missing-input:attestation" in report["blockers"]
    assert report["inputs"]["attestation"]["present"] is False
    assert report["next_actions"], "blocked 必须给出下一步命令"
    assert any(
        "public_mobile_release_gate.py" in action["command"]
        for action in report["next_actions"]
    )


def test_no_machine_timestamp_anchor_refused_zero_write(tmp_path):
    inputs = build_all(
        tmp_path,
        restore=tmp_path / "absent" / "r.json",
        edge=tmp_path / "absent" / "e.json",
        smoke=tmp_path / "absent" / "s.json",
        release_evidence=tmp_path / "absent" / "v.json",
    )
    with pytest.raises(gate.GateRefused):
        run_inputs(tmp_path, inputs)
    assert not (tmp_path / "out").exists()


def test_malformed_json_is_blocked(tmp_path):
    bad = tmp_path / "edge" / "report.json"
    make_edge(tmp_path)
    bad.write_text("{ not json", encoding="utf-8")
    inputs = build_all(tmp_path, edge=bad)
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert "malformed-input:edge_preflight" in report["blockers"]


# ------------------------------------------------------------ schema / tool


def test_schema_and_tool_mismatch_blocked(tmp_path):
    inputs = build_all(
        tmp_path,
        restore=make_restore(tmp_path, schema=False),
        edge=make_edge(tmp_path, tool=False),
    )
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert "schema-mismatch:restore_preflight" in report["blockers"]
    assert "tool-mismatch:edge_preflight" in report["blockers"]


# --------------------------------------------------------------- hash / 篡改


def test_attestation_hash_mismatch_and_unanchored(tmp_path):
    attestation = make_attestation(
        tmp_path,
        anchors=[("report.json", "f" * 64)],  # 名字对不上任何输入
    )
    inputs = build_all(tmp_path, attestation=attestation)
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert "attestation-unanchored" in report["blockers"]
    # 名字锚定但哈希错 → hash-mismatch。
    attestation2 = make_attestation(
        tmp_path,
        anchors=[(inputs["edge"].name, "0" * 64)],
    )
    inputs2 = build_all(tmp_path, attestation=attestation2)
    report2, exit_code2 = run_inputs(tmp_path, inputs2)
    assert exit_code2 == gate.EXIT_BLOCKED
    assert f"hash-mismatch:attestation:{inputs2['edge'].name}" in report2["blockers"]
    assert "attestation-unanchored" in report2["blockers"]


def test_smoke_internal_evidence_tamper_blocked(tmp_path):
    inputs = build_all(
        tmp_path, smoke=make_smoke(tmp_path, tamper_evidence=True)
    )
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert "evidence-hash-mismatch:device_smoke:launch.png" in report["blockers"]


# ---------------------------------------------------------------- 时间语义


def test_future_attestation_observation_blocked(tmp_path):
    attestation = make_attestation(tmp_path, observed_at=FUTURE)
    inputs = build_all(tmp_path, attestation=attestation)
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert "attestation-invalid:observed-in-future" in report["blockers"]


def test_stale_attestation_blocked(tmp_path):
    attestation = make_attestation(
        tmp_path, observed_at=STALE, valid_until="2026-10-20T00:00:00Z"
    )
    inputs = build_all(tmp_path, attestation=attestation)
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert "attestation-stale" in report["blockers"]


def test_attestation_expired_blocked(tmp_path):
    attestation = make_attestation(
        tmp_path, valid_until="2026-10-05T00:29:59Z"  # 早于评估时刻
    )
    inputs = build_all(tmp_path, attestation=attestation)
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert "attestation-expired" in report["blockers"]


def test_future_machine_timestamp_blocked(tmp_path):
    """机器时间戳晚于评估时刻+容差 → future-timestamp（真实未来时间拒绝）。"""
    inputs = build_all(tmp_path, restore=make_restore(tmp_path, generated_at=FUTURE))
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert "future-timestamp:restore_preflight" in report["blockers"]


def test_stale_machine_input_blocked(tmp_path):
    inputs = build_all(tmp_path, restore=make_restore(tmp_path, generated_at=STALE))
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert "stale-input:restore_preflight" in report["blockers"]


def test_stale_frontier_blocked(tmp_path):
    """整批机器证据距评估时刻过旧 → stale-frontier（互相背书不得放行）。"""
    inputs = build_all(
        tmp_path,
        restore=make_restore(tmp_path, generated_at=STALE),
        edge=make_edge(tmp_path, generated_at=STALE),
        smoke=make_smoke(tmp_path, generated_at=STALE),
        release_evidence=make_release_evidence(tmp_path, generated_at=STALE),
    )
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert "stale-frontier" in report["blockers"]
    assert report["reference"]["evidence_frontier"] == "2026-10-03T00:00:00Z"


def test_malformed_timestamp_cannot_bypass_freshness(tmp_path):
    """时间戳 malformed 的机器输入自带 blocker——不参与前沿也不放行。"""
    bad_edge = make_edge(tmp_path)
    payload = json.loads(bad_edge.read_text(encoding="utf-8"))
    payload["generated_at"] = "not-a-timestamp"
    bad_edge.write_text(json.dumps(payload), encoding="utf-8")
    inputs = build_all(tmp_path, edge=bad_edge)
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert "timestamp-format:edge_preflight" in report["blockers"]
    assert report["public_mobile_ready"] is False


def test_naive_evaluation_time_refused(tmp_path):
    inputs = build_all(tmp_path)
    from datetime import datetime

    with pytest.raises(gate.GateRefused):
        gate.run_gate(
            RealStore(),
            restore=inputs["restore"],
            edge=inputs["edge"],
            smoke=inputs["smoke"],
            cloudflare=None,
            release_evidence=inputs["release_evidence"],
            attestation=inputs["attestation"],
            output_dir=tmp_path / "out",
            freshness_hours=24,
            evaluation_time=datetime(2026, 10, 5, 0, 30, 0),  # noqa: DTZ001 故意 naive
        )
    assert not (tmp_path / "out").exists()


def test_freshness_window_configurable(tmp_path):
    inputs = build_all(
        tmp_path,
        restore=make_restore(tmp_path, generated_at=STALE),
        edge=make_edge(tmp_path, generated_at=STALE),
        smoke=make_smoke(tmp_path, generated_at=STALE),
        release_evidence=make_release_evidence(tmp_path, generated_at=STALE),
    )
    anchors = [
        (inputs[key].name, _sha256_bytes(inputs[key].read_bytes()))
        for key in ("restore", "edge", "smoke", "release_evidence")
    ]
    inputs["attestation"] = make_attestation(
        tmp_path,
        observed_at=STALE,
        valid_until="2026-10-20T00:00:00Z",
        anchors=anchors,
    )
    report, exit_code = run_inputs(tmp_path, inputs, freshness_hours=72)
    assert exit_code == gate.EXIT_READY
    assert report["reference"]["freshness_hours"] == 72


# --------------------------------------------------------- 状态 blocked/partial


def test_non_passing_statuses_blocked(tmp_path):
    inputs = build_all(
        tmp_path,
        restore=make_restore(tmp_path, verdict="blocked"),
        smoke=make_smoke(tmp_path, status="blocked"),
        release_evidence=make_release_evidence(tmp_path, provider_result="fail"),
    )
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert "status-not-passing:restore_preflight:blocked" in report["blockers"]
    assert "status-not-passing:device_smoke:blocked" in report["blockers"]
    assert "release-evidence-status:provider-smoke:voice:fail" in report["blockers"]
    assert "release-evidence-status:provider-smoke:search:fail" in report["blockers"]
    assert "release-evidence-status:provider-smoke:llm:fail" in report["blockers"]


# --------------------------------------------------- release evidence 白名单


def test_release_evidence_arbitrary_tool_rejected(tmp_path):
    """Defect-2：任意/未来发明的 tool 字符串不得被接受。"""
    inputs = build_all(
        tmp_path,
        release_evidence=make_release_evidence(tmp_path, tool="some-invented-exporter"),
    )
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert (
        "release-evidence-contract:tool-not-allowed:some-invented-exporter"
        in report["blockers"]
    )


def test_release_evidence_schema_gate_mismatch_rejected(tmp_path):
    inputs = build_all(
        tmp_path,
        release_evidence=make_release_evidence(
            tmp_path, schema="provider-smoke-evidence-v2", gate="other-gate"
        ),
    )
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert "release-evidence-contract:schema-version" in report["blockers"]
    assert "release-evidence-contract:gate:other-gate" in report["blockers"]


def test_release_evidence_provider_not_executed_rejected(tmp_path):
    path = tmp_path / "release-evidence" / "status.json"
    make_release_evidence(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["providers"]["search"] = {
        "executed": False, "result": "pass", "evidence_step": "x",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    inputs = build_all(tmp_path, release_evidence=path)
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert "release-evidence-status:provider-smoke:search:pass" in report["blockers"]


def test_release_check_contract_accepted(tmp_path):
    """真实第二契约：release-check 报告（all_green=true）被接受。"""
    inputs = build_all(
        tmp_path, release_evidence=make_release_check(tmp_path)
    )
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_READY
    assert report["inputs"]["release_evidence"]["tool"] == "release-check"


def test_release_check_not_green_rejected(tmp_path):
    inputs = build_all(
        tmp_path,
        release_evidence=make_release_check(tmp_path, all_green=False),
    )
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert "release-evidence-status:release-check:all_green=False" in report["blockers"]


def test_release_evidence_next_action_names_real_exporters(tmp_path):
    inputs = build_all(
        tmp_path,
        release_evidence=make_release_evidence(tmp_path, tool="bogus-tool"),
    )
    report, _ = run_inputs(tmp_path, inputs)
    commands = [action["command"] for action in report["next_actions"]]
    assert any("provider-smoke-aggregate" in command for command in commands)


def test_edge_attestation_pending_blocked(tmp_path):
    inputs = build_all(tmp_path, edge=make_edge(tmp_path, attested=False))
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert any(
        blocker.startswith("status-not-passing:edge_preflight")
        for blocker in report["blockers"]
    )


def test_restore_blockers_list_surfaced(tmp_path):
    inputs = build_all(
        tmp_path,
        restore=make_restore(
            tmp_path, verdict="blocked", blockers=["local-image-missing"]
        ),
    )
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert "restore-preflight-blockers:1" in report["blockers"]


# ------------------------------------------------------------ 跨报告矛盾


def test_cloudflare_zone_conflict_blocked(tmp_path):
    inputs = build_all(
        tmp_path, cloudflare=make_cloudflare(tmp_path, zone="other.example")
    )
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert (
        "cross-report-conflict:cloudflare-zone-vs-edge-host" in report["blockers"]
    )


def test_cloudflare_zone_subdomain_match_accepted(tmp_path):
    inputs = build_all(tmp_path, cloudflare=make_cloudflare(tmp_path))
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_READY
    assert report["inputs"]["cloudflare_preflight"]["present"] is True


# ------------------------------------------------------------ attestation 契约


def test_attestation_missing_required_item_blocked(tmp_path):
    covered = [item for item in gate.REQUIRED_ATTESTED_ITEMS
               if item != "mobile-turn-relay"]
    inputs = build_all(
        tmp_path, attestation=make_attestation(tmp_path, covered=covered)
    )
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert "attestation-incomplete:mobile-turn-relay" in report["blockers"]


def test_attestation_conclusion_fail_blocked(tmp_path):
    inputs = build_all(
        tmp_path,
        attestation=make_attestation(tmp_path, conclusion="fail"),
    )
    report, exit_code = run_inputs(tmp_path, inputs)
    assert exit_code == gate.EXIT_BLOCKED
    assert "attestation-invalid:conclusion:fail" in report["blockers"]


# ------------------------------------------------------------ 下一步命令


def test_next_actions_route_to_sub_tools_with_report_data(tmp_path):
    inputs = build_all(tmp_path, smoke=make_smoke(tmp_path, status="failed"))
    report, _ = run_inputs(tmp_path, inputs)
    commands = [action["command"] for action in report["next_actions"]]
    # 缺什么给什么：smoke 失败 → 参数取自既有 smoke 报告的只读重跑命令。
    assert any(
        "public_device_smoke.py" in command
        and "--serial SYNTH-Serial-001" in command
        and "--manifest-url" in command
        for command in commands
    )


# ------------------------------------------------------------ 路径/symlink 拒绝


class FakeLinkStore(RealStore):
    """is_symlink 恒真——结构性地证明 symlink 输入被拒绝（零写入）。"""

    def is_symlink(self, path: Path) -> bool:
        return True


def test_symlinked_input_refused_zero_write(tmp_path):
    inputs = build_all(tmp_path)
    with pytest.raises(gate.GateRefused) as caught:
        gate.run_gate(
            FakeLinkStore(),
            restore=inputs["restore"],
            edge=inputs["edge"],
            smoke=inputs["smoke"],
            cloudflare=None,
            release_evidence=inputs["release_evidence"],
            attestation=inputs["attestation"],
            output_dir=tmp_path / "out",
            freshness_hours=24,
            evaluation_time=_fixed_eval(),
        )
    assert "symlink" in str(caught.value)
    assert not (tmp_path / "out").exists()


def test_directory_input_refused(tmp_path):
    inputs = build_all(tmp_path)
    inputs["attestation"] = tmp_path / "attestation"  # 目录不是常规文件
    with pytest.raises(gate.GateRefused) as caught:
        run_inputs(tmp_path, inputs)
    assert "not-regular-file" in str(caught.value)


# ------------------------------------------------------------ 输出 fail-closed


class FailingWriteStore(RealStore):
    def write_atomic(self, path: Path, text: str) -> None:
        raise OSError("synthetic write failure")


class TamperedReadStore(RealStore):
    """write 成功但读回内容不同——重读校验必须拦截。"""

    def read_bytes(self, path: Path) -> bytes:
        if path.name == gate.REPORT_JSON_NAME and path.parent.name == "out":
            return b'{"tampered": true}'
        return super().read_bytes(path)


def test_write_failure_refused_no_partial_output(tmp_path):
    inputs = build_all(tmp_path)
    with pytest.raises(gate.GateRefused):
        gate.run_gate(
            FailingWriteStore(),
            restore=inputs["restore"],
            edge=inputs["edge"],
            smoke=inputs["smoke"],
            cloudflare=None,
            release_evidence=inputs["release_evidence"],
            attestation=inputs["attestation"],
            output_dir=tmp_path / "out",
            freshness_hours=24,
            evaluation_time=_fixed_eval(),
        )
    assert not (tmp_path / "out" / gate.REPORT_JSON_NAME).exists()


def test_readback_verification_failure_refused(tmp_path):
    inputs = build_all(tmp_path)
    with pytest.raises(gate.GateRefused) as caught:
        gate.run_gate(
            TamperedReadStore(),
            restore=inputs["restore"],
            edge=inputs["edge"],
            smoke=inputs["smoke"],
            cloudflare=None,
            release_evidence=inputs["release_evidence"],
            attestation=inputs["attestation"],
            output_dir=tmp_path / "out",
            freshness_hours=24,
            evaluation_time=_fixed_eval(),
        )
    assert "output-verify-failed" in str(caught.value)


# ------------------------------------------------------------ CLI 边界


def test_cli_missing_required_argument_fails_closed(capsys):
    with pytest.raises(SystemExit) as caught:
        gate.main(["--restore-preflight", "x.json"])
    assert caught.value.code == 2


def test_cli_freshness_out_of_range_refused(tmp_path):
    inputs = build_all(tmp_path)
    exit_code = gate.main(
        [
            "--restore-preflight", str(inputs["restore"]),
            "--edge-preflight", str(inputs["edge"]),
            "--device-smoke", str(inputs["smoke"]),
            "--release-evidence", str(inputs["release_evidence"]),
            "--attestation", str(inputs["attestation"]),
            "--output", str(tmp_path / "out"),
            "--freshness-hours", "0",
        ]
    )
    assert exit_code == gate.EXIT_REFUSED
    assert not (tmp_path / "out" / gate.REPORT_JSON_NAME).exists()


def test_cli_blocked_prints_blockers(tmp_path, capsys):
    """CLI 用真实当前 UTC 作评估时刻——fixture 时间以 now 锚定（日期无关）。"""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=10)).isoformat(timespec="seconds")
    valid_until = (now + timedelta(days=7)).isoformat(timespec="seconds")
    evidence = make_release_evidence(
        tmp_path, generated_at=recent, provider_result="fail"
    )
    attestation = make_attestation(
        tmp_path,
        observed_at=recent,
        valid_until=valid_until,
        anchors=[(evidence.name, _sha256_bytes(evidence.read_bytes()))],
    )
    inputs = build_all(
        tmp_path,
        restore=make_restore(tmp_path, generated_at=recent),
        edge=make_edge(tmp_path, generated_at=recent),
        smoke=make_smoke(tmp_path, generated_at=recent),
        release_evidence=evidence,
        attestation=attestation,
    )
    exit_code = gate.main(
        [
            "--restore-preflight", str(inputs["restore"]),
            "--edge-preflight", str(inputs["edge"]),
            "--device-smoke", str(inputs["smoke"]),
            "--release-evidence", str(inputs["release_evidence"]),
            "--attestation", str(inputs["attestation"]),
            "--output", str(tmp_path / "out"),
        ]
    )
    assert exit_code == gate.EXIT_BLOCKED
    captured = capsys.readouterr()
    assert "BLOCKED" in captured.out
    assert "release-evidence-status:provider-smoke:voice:fail" in captured.out
    # CLI 报告的顶层 generated_at 是真实评估时刻（与前沿分离）。
    report = json.loads(
        (tmp_path / "out" / gate.REPORT_JSON_NAME).read_text(encoding="utf-8")
    )
    assert report["reference"]["evaluation_time"] == report["generated_at"]
    assert report["reference"]["evidence_frontier"] <= report["generated_at"]


# ------------------------------------------------------------ 无副作用（静态）


def test_source_has_no_network_subprocess_env_secret_surface():
    source = (REPO_ROOT / "tools" / "android_release"
              / "public_mobile_release_gate.py").read_text(encoding="utf-8")
    for forbidden in (
        "import subprocess",
        "import socket",
        "urllib.request",
        "import requests",
        "os.environ",
        "os.getenv",
        "docker",
        "adb",
    ):
        assert forbidden not in source, f"forbidden surface: {forbidden}"
