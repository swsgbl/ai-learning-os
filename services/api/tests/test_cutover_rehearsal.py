"""M10-15 cutover rehearsal：生产切换演练编排器的矩阵/四态语义/安全边界。

覆盖矩阵：
1. step 矩阵与 CLI 注册：STEPS 恰为 13 个 required steps（时间线顺序：
   pre-window 7 步 -> pre-migration 2 步 -> post-migration 3 步 -> cutover
   1 步；preflight-pre 先于 preflight-post、audit-chain-verify 先于
   audit-chain-anchor、cutover-approval 收尾），step_id/证据文件名唯一，
   EVALUATORS 全覆盖，无 --yes 执行形态，main 分发；
2. 全 pass：12 步齐备 + 哈希绑定审批 -> rehearsal_ready=True / exit 0 /
   overall=pass；每步 evidence sha256 与文件字节独立重算一致；
3. 四态映射：missing => not_executed（空目录全 not_executed）；malformed
   （非法 JSON / 结构不符 / 自声明 step 错位 / 计数自相矛盾 / 敏感键）=>
   blocked；fail（CI failure / release-check 未全绿 / preflight fail / 链
   invalid / 冒烟 fail）=> blocked；tampered（审批哈希失配 / 锚文件副本被改）
   => blocked；待人工（治理计数 >0 / 锚定落后 / WORM 未归档 / 恢复演练未
   verified / post 预检 pending）=> pending；
4. 拆分语义：preflight-pre 无 fail 即 pass（pending 属预期）且 fail>0 =>
   blocked；preflight-post 有 pending => pending、放行形态才 pass；
   audit-chain-verify 与 anchor 互不掩盖；search/cloud-voice/llm 三冒烟
   独立（一 fail 一 pass 不互相遮蔽、not run => not_executed）；
5. 审批绑定：缺审批 / 覆盖缺口（主 step 或 supporting 文件）=>
   not_executed（coverage_gaps / supporting_coverage_gaps 透出）；哈希失配
   （主 step 或 supporting，含引用当前不存在的 supporting 文件）=> blocked
   （hash_mismatches / supporting_hash_mismatches 透出）；supporting_evidence
   必须精确覆盖当前实际 supporting 文件集合（目前仅 audit-anchor.jsonl；目录
   无 supporting 文件时必须为空 mapping）；未知 supporting 文件名 / 非 64
   hex / 缺 supporting_evidence 字段 => malformed => blocked；note 只验证
   非空不回显；审批后把锚副本替换为另一份仍自洽、锚点数相同的副本——
   anchor 步仍 pass 但 approval 变 blocked（主 step 哈希盖不到的缺口由
   supporting 绑定补上）；**审批记录携带任一 DRAFT 底稿保留元数据字段
   （APPROVAL_DRAFT_RESERVED_FIELDS 十字段逐一参数化）=> malformed =>
   blocked——底稿补齐人工字段后改名/拼装不能 pass（M10-16 返工），
   合法审批不携带这些字段仍 pass，拒绝名单与底稿输出字段同步**；
6. 顶层优先级 blocked > pending > not_executed > ready（组合场景逐一锁定）；
7. 路径护栏与 IO（exit 2）：--evidence-dir 不存在 / 普通文件 / symlink、
   目录内任何 symlink、证据文件名被目录占用；--output 非 artifacts/temp
   拒绝、artifacts 内写入、输出父级被普通文件占用时不打印步骤结论摘要；
8. 只读与零敏感/零 DB/零网络：全部证据文件字节在两轮运行前后不变（含
   锚文件副本）；毒化环境变量不被读取；生产 paper ID / 密码 / token / key
   marker 在 JSON manifest、人类摘要与输出文件零泄漏；isolation_note 声明
   隔离 rehearsal 不代表生产验收也不授权生产写入/发布；模块行为面零
   DB/零网络（源码无 os.environ/引擎/HTTP/套接字引用）；未识别文件如实
   列出（仅文件名 + sha256）。

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
from app.ops import cutover_rehearsal as cr
from app.ops.cutover_rehearsal import (
    EVALUATORS,
    STAGES,
    STEP_IDS,
    STEPS,
    format_rehearsal_summary,
    run_cutover_rehearsal,
)

#: 生产数据/密钥 marker：任何输出（JSON / 人类摘要 / 输出文件）都不得包含
PAPER_ID_MARKER = "PROD-PAPER-ID-88c1"
DRAFT_ID_MARKER = "PROD-DRAFT-ID-88c2"
PASSWORD_MARKER = "PROD-PW-88c3"
API_KEY_MARKER = "sk-PROD-KEY-88c4"
TOKEN_MARKER = "PROD-TOKEN-88c5"

EXPECTED_STEPS = {
    "ci-main",
    "release-check",
    "search-smoke",
    "cloud-voice-smoke",
    "llm-smoke",
    "legacy-papers",
    "draft-ownership",
    "preflight-pre-migration",
    "backup-restore",
    "preflight-post-migration",
    "audit-chain-verify",
    "audit-chain-anchor",
    "cutover-approval",
}
EXPECTED_STAGE_OF = {
    "ci-main": "pre-window",
    "release-check": "pre-window",
    "search-smoke": "pre-window",
    "cloud-voice-smoke": "pre-window",
    "llm-smoke": "pre-window",
    "legacy-papers": "pre-window",
    "draft-ownership": "pre-window",
    "preflight-pre-migration": "pre-migration",
    "backup-restore": "pre-migration",
    "preflight-post-migration": "post-migration",
    "audit-chain-verify": "post-migration",
    "audit-chain-anchor": "post-migration",
    "cutover-approval": "cutover",
}

_FILE_OF = {spec.step_id: spec.evidence_file for spec in STEPS}


def _evidence_dir(tmp_path: Path, name: str = "evidence") -> Path:
    directory = tmp_path / name
    directory.mkdir()
    return directory


def _write_json(directory: Path, name: str, payload) -> None:
    (directory / name).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _passing_steps_evidence() -> dict[str, dict]:
    """12 步齐备且全部满足 pass 形态的证据（不含 cutover-approval）。"""
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


def _approval_payload(
    directory: Path, *, skip: str | None = None, skip_supporting: bool = False
) -> dict:
    """按当前证据文件字节计算 sha256 生成审批记录（可跳过某步制造主 step
    覆盖缺口；skip_supporting 置空 supporting_evidence 制造 supporting 覆盖
    缺口——默认精确绑定当前实际存在的 supporting 文件）。"""
    step_evidence = {}
    for spec in STEPS:
        if spec.step_id == "cutover-approval" or spec.step_id == skip:
            continue
        raw = (directory / spec.evidence_file).read_bytes()
        step_evidence[spec.step_id] = hashlib.sha256(raw).hexdigest()
    supporting: dict[str, str] = {}
    if not skip_supporting:
        for name in sorted(cr.BINDABLE_SUPPORTING_FILES):
            path = directory / name
            if path.exists():
                supporting[name] = hashlib.sha256(path.read_bytes()).hexdigest()
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
        "supporting_evidence": supporting,
    }


def _full_passing_dir(tmp_path: Path, name: str = "evidence") -> Path:
    directory = _evidence_dir(tmp_path, name)
    for filename, payload in _passing_steps_evidence().items():
        _write_json(directory, filename, payload)
    _write_json(directory, "cutover-approval.json", _approval_payload(directory))
    return directory


def _anchor_lines(first_head: str = "bb" * 32, second_head: str = "cc" * 32) -> bytes:
    """两条自洽锚链（sequence=1 -> 2），字段与 M10-06 锚文件同格式（bytes）；
    head_hash 可参数化——用于构造「另一份仍自洽、锚点数相同」的替换副本。"""
    from app.ops.audit_chain_anchor import build_anchor, render_anchor_line

    moment = datetime(2026, 9, 4, 9, 0, 0, tzinfo=UTC)
    first = build_anchor(
        sequence=1,
        head_hash=first_head,
        previous_anchor_hash="00" * 32,
        clock=lambda: moment,
    )
    second = build_anchor(
        sequence=2,
        head_hash=second_head,
        previous_anchor_hash=first["anchor_hash"],
        clock=lambda: moment,
    )
    return render_anchor_line(first) + render_anchor_line(second)


def _run(directory: Path) -> dict:
    return run_cutover_rehearsal(directory)


def _cli(directory, *, as_json=False, output=None) -> int:
    return cli_module._run_cutover_rehearsal(
        SimpleNamespace(
            evidence_dir=str(directory),
            as_json=as_json,
            output=str(output) if output else None,
        )
    )


def _step(report: dict, step_id: str) -> dict:
    return next(item for item in report["steps"] if item["step"] == step_id)


def _statuses(report: dict) -> dict[str, str]:
    return {item["step"]: item["status"] for item in report["steps"]}


def _replace(pairs: dict[str, dict], directory: Path) -> None:
    for filename, payload in pairs.items():
        _write_json(directory, filename, payload)


# --- 1. step 矩阵与 CLI 注册 --------------------------------------------------


def test_step_matrix_is_13_required_steps_on_timeline() -> None:
    """13 个 required steps、时间线阶段归属、评估器全覆盖、文件名唯一。"""
    assert STEP_IDS == EXPECTED_STEPS
    assert all(spec.required for spec in STEPS)
    files = [spec.evidence_file for spec in STEPS]
    assert len(files) == len(set(files)), "证据文件名必须唯一"
    for spec in STEPS:
        assert spec.title and spec.basis and spec.not_executed_action
        assert EXPECTED_STAGE_OF[spec.step_id] == spec.stage
    assert set(EVALUATORS) == STEP_IDS
    assert STAGES == ("pre-window", "pre-migration", "post-migration", "cutover")
    # 时间线顺序锚点：pre 先于 post、verify 先于 anchor、审批收尾
    order = [spec.step_id for spec in STEPS]
    assert order.index("preflight-pre-migration") < order.index(
        "preflight-post-migration"
    )
    assert order.index("audit-chain-verify") < order.index("audit-chain-anchor")
    assert order[-1] == "cutover-approval"


def test_rehearsal_timeline_unchanged_for_provider_gap_deferral(tmp_path) -> None:
    """M14-74 兼容性：production-evidence-gap 的 provider-smoke 类别 defer
    到 release-readiness 聚合门后，演练时间线保持不变——cloud-voice-smoke
    仍是演练步（pre-window 云语音冒烟）、local-voice-smoke 不是演练步
    （只是 M14-70 聚合证据轨道）；聚合文件 provider-smoke.json 出现在
    证据目录时 rehearsal 只记 unrecognized、不阻断时间线评估（13 步全
    pass 结论不受影响——聚合证据由 release-readiness 门消费）。"""
    assert "cloud-voice-smoke" in STEP_IDS
    assert "local-voice-smoke" not in STEP_IDS
    assert {"search-smoke", "llm-smoke"} <= STEP_IDS
    directory = _full_passing_dir(tmp_path)
    _write_json(
        directory,
        "provider-smoke.json",
        {
            "gate": "provider-smoke",
            "topology": {"voice_mode": "local"},
            "providers": {
                "voice": {
                    "executed": True,
                    "result": "pass",
                    "evidence_step": "local-voice-smoke",
                },
                "search": {
                    "executed": True,
                    "result": "pass",
                    "evidence_step": "search-smoke",
                },
                "llm": {
                    "executed": True,
                    "result": "pass",
                    "evidence_step": "llm-smoke",
                },
            },
        },
    )
    report = _run(directory)
    assert set(_statuses(report).values()) == {"pass"}
    assert report["overall_status"] == "pass"
    assert any(
        item["file"] == "provider-smoke.json"
        for item in report["unrecognized_files"]
    ), "聚合文件只如实记录，不进入演练时间线评估"


def test_cli_registered_and_has_no_execute_flag(tmp_path, monkeypatch) -> None:
    """CLI 子命令注册、无 --yes 执行形态（argparse 对未知旗标 exit 2）。"""
    assert hasattr(cli_module, "_run_cutover_rehearsal")
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    assert '"cutover-rehearsal"' in source
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli", "cutover-rehearsal", "--evidence-dir", str(_evidence_dir(tmp_path)), "--yes"],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 2


def test_main_dispatch_cutover_rehearsal(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys, "argv", ["cli", "cutover-rehearsal", "--evidence-dir", str(_full_passing_dir(tmp_path))]
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "REHEARSAL READY" in out


def test_cli_missing_evidence_dir_arg_exits_2(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["cli", "cutover-rehearsal"])
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 2


# --- 2. 全 pass ---------------------------------------------------------------


def test_full_passing_timeline_is_ready(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    report = _run(directory)
    statuses = _statuses(report)
    assert set(statuses.values()) == {"pass"}
    assert report["rehearsal_ready"] is True
    assert report["overall_status"] == "pass"
    assert report["exit_code"] == 0
    assert report["blockers"] == []
    assert report["next_actions"] == []
    assert report["not_pass_steps"] == []
    # 每步 evidence sha256 与文件字节独立重算一致
    for spec in STEPS:
        digest = hashlib.sha256((directory / spec.evidence_file).read_bytes()).hexdigest()
        assert _step(report, spec.step_id)["evidence"] == {
            "file": spec.evidence_file,
            "sha256": digest,
        }
    # 本目录无 supporting 文件：审批 supporting_evidence 必须为空 mapping
    approval = _step(report, "cutover-approval")
    assert approval["data"]["covered_supporting"] == []
    assert approval["data"]["supporting_coverage_gaps"] == []


def test_summary_counts_match_steps(tmp_path) -> None:
    report = _run(_full_passing_dir(tmp_path))
    assert report["summary"] == {"pass": 13, "pending": 0, "blocked": 0, "not_executed": 0}


# --- 3. 四态映射 --------------------------------------------------------------


def test_empty_dir_all_not_executed(tmp_path) -> None:
    report = _run(_evidence_dir(tmp_path))
    statuses = _statuses(report)
    assert set(statuses.values()) == {"not_executed"}
    assert report["overall_status"] == "not_executed"
    assert report["rehearsal_ready"] is False
    assert report["exit_code"] == 1
    # 缺证据也有 next_action（每步的动作建议非空）
    assert len(report["next_actions"]) == 13
    assert all(item["action"] for item in report["next_actions"])
    assert report["blockers"] == []


@pytest.mark.parametrize(
    ("filename", "payload", "step_id"),
    [
        ("ci-main.json", {"step": "ci-main", "run_id": 1, "merge_commit": "a" * 40, "conclusion": "failure"}, "ci-main"),
        (
            "release-check.json",
            {"step": "release-check", "all_green": False, "total": 10, "passed": 8, "failed_ids": ["voice"]},
            "release-check",
        ),
        (
            "search-smoke.json",
            {"step": "search-smoke", "executed": True, "result": "fail"},
            "search-smoke",
        ),
        (
            "audit-chain-verify.json",
            {"step": "audit-chain-verify", "valid": False, "entries": 10},
            "audit-chain-verify",
        ),
    ],
)
def test_fail_verdicts_map_to_blocked(tmp_path, filename, payload, step_id) -> None:
    """fail 类证据 => blocked（CI failure / 门禁未全绿 / 冒烟 fail / 链 invalid）。"""
    directory = _full_passing_dir(tmp_path)
    _replace({filename: payload}, directory)
    # 换证据后审批哈希失配：审批 blocked 属预期，被测步仍须 blocked
    report = _run(directory)
    assert _step(report, step_id)["status"] == "blocked"
    assert report["overall_status"] == "blocked"
    assert any(item["step"] == step_id for item in report["blockers"])


@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        ("ci-main.json", {"gate": "ci-main", "run_id": 1, "merge_commit": "a" * 40, "conclusion": "success"}),
        ("release-check.json", {"step": "release-check", "all_green": True, "total": 10, "passed": 9, "failed_ids": []}),
        (
            "legacy-papers.json",
            {"step": "legacy-papers", "pending_count": -1, "batches": []},
        ),
        (
            "search-smoke.json",
            {"step": "search-smoke", "executed": False, "result": "pass"},
        ),
        (
            "audit-chain-verify.json",
            {"step": "audit-chain-verify", "valid": "yes", "entries": 3},
        ),
    ],
)
def test_malformed_evidence_maps_to_blocked(tmp_path, filename, payload) -> None:
    """自声明 step 错位 / 计数自相矛盾 / 冒烟自相矛盾 / 类型不符 => blocked。"""
    directory = _full_passing_dir(tmp_path)
    _replace({filename: payload}, directory)
    report = _run(directory)
    step_id = next(s for s, f in _FILE_OF.items() if f == filename)
    record = _step(report, step_id)
    assert record["status"] == "blocked"
    assert record["evidence"]["sha256"], "malformed 也必须留哈希便于审计"


def test_invalid_json_maps_to_blocked(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    (directory / "llm-smoke.json").write_text("{not json", encoding="utf-8")
    report = _run(directory)
    assert _step(report, "llm-smoke")["status"] == "blocked"


def test_sensitive_key_evidence_blocked_and_value_never_echoed(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    payload = dict(_passing_steps_evidence()["llm-smoke.json"])
    payload["api_key"] = API_KEY_MARKER
    _replace({"llm-smoke.json": payload}, directory)
    report = _run(directory)
    record = _step(report, "llm-smoke")
    assert record["status"] == "blocked"
    rendered = json.dumps(report, ensure_ascii=False)
    assert API_KEY_MARKER not in rendered


def test_embedded_credential_evidence_blocked(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    payload = dict(_passing_steps_evidence()["search-smoke.json"])
    payload["endpoint"] = f"https://user:{TOKEN_MARKER}@search.example.internal/api"
    _replace({"search-smoke.json": payload}, directory)
    report = _run(directory)
    assert _step(report, "search-smoke")["status"] == "blocked"
    assert TOKEN_MARKER not in json.dumps(report, ensure_ascii=False)


# --- 4. 拆分语义（preflight / audit-chain / provider smoke） --------------------


def test_preflight_pre_passes_with_expected_pending(tmp_path) -> None:
    """pre 步通过条件=无 fail：pending/not_configured 属迁移前预期不阻断。"""
    directory = _full_passing_dir(tmp_path)
    report = _run(directory)
    record = _step(report, "preflight-pre-migration")
    assert record["status"] == "pass"
    assert "属迁移前预期" in record["reason"]
    assert "post-migration 证据" in record["reason"]


def test_preflight_pre_fail_blocks(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    _replace(
        {
            "preflight-pre-migration.json": {
                "step": "preflight-pre-migration",
                "phase": "pre-migration",
                "summary": {"pass": 2, "pending": 1, "fail": 1, "not_configured": 0},
            }
        },
        directory,
    )
    report = _run(directory)
    assert _step(report, "preflight-pre-migration")["status"] == "blocked"


def test_preflight_phase_mismatch_is_malformed_blocked(tmp_path) -> None:
    """phase 字段与文件名声明的阶段错位 => 结构不可信 => blocked。"""
    directory = _full_passing_dir(tmp_path)
    _replace(
        {
            "preflight-pre-migration.json": {
                "step": "preflight-pre-migration",
                "phase": "post-migration",
                "summary": {"pass": 3, "pending": 0, "fail": 0, "not_configured": 0},
            }
        },
        directory,
    )
    report = _run(directory)
    assert _step(report, "preflight-pre-migration")["status"] == "blocked"


def test_preflight_post_pending_is_pending_not_pass(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    _replace(
        {
            "preflight-post-migration.json": {
                "step": "preflight-post-migration",
                "phase": "post-migration",
                "summary": {"pass": 4, "pending": 1, "fail": 0, "not_configured": 1},
            }
        },
        directory,
    )
    report = _run(directory)
    assert _step(report, "preflight-post-migration")["status"] == "pending"


def test_preflight_zero_counts_malformed(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    _replace(
        {
            "preflight-pre-migration.json": {
                "step": "preflight-pre-migration",
                "phase": "pre-migration",
                "summary": {"pass": 0, "pending": 0, "fail": 0, "not_configured": 0},
            }
        },
        directory,
    )
    report = _run(directory)
    assert _step(report, "preflight-pre-migration")["status"] == "blocked"


def test_audit_verify_invalid_blocks_independently_of_anchor(tmp_path) -> None:
    """链 invalid => verify 步 blocked；anchor 步不被 verify 结论掩盖（独立评估）。"""
    directory = _full_passing_dir(tmp_path)
    _replace(
        {
            "audit-chain-verify.json": {
                "step": "audit-chain-verify",
                "valid": False,
                "entries": 9,
            }
        },
        directory,
    )
    report = _run(directory)
    assert _step(report, "audit-chain-verify")["status"] == "blocked"
    # anchor 证据本身仍是 up-to-date + WORM：pass（时间线语义：先修链再锚定）
    assert _step(report, "audit-chain-anchor")["status"] == "pass"


def test_audit_anchor_stale_and_worm_missing_pending(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    _replace(
        {
            "audit-chain-anchor.json": {
                "step": "audit-chain-anchor",
                "anchor": {"status": "valid", "anchors": 1},
                "worm": {"archived": False},
            }
        },
        directory,
    )
    report = _run(directory)
    assert _step(report, "audit-chain-anchor")["status"] == "pending"


def test_anchor_companion_verified_and_tampered(tmp_path) -> None:
    """锚文件副本：完好自洽 => pass + supporting；被改 => blocked。"""
    directory = _full_passing_dir(tmp_path)
    (directory / "audit-anchor.jsonl").write_bytes(_anchor_lines())
    report = _run(directory)
    record = _step(report, "audit-chain-anchor")
    assert record["status"] == "pass"
    assert record["supporting"][0]["file"] == "audit-anchor.jsonl"
    digest = hashlib.sha256((directory / "audit-anchor.jsonl").read_bytes()).hexdigest()
    assert record["supporting"][0]["sha256"] == digest
    # 篡改副本后 => blocked（tampered 映射；canonical JSON 是紧凑格式无空格）
    lines = _anchor_lines().splitlines()
    (directory / "audit-anchor.jsonl").write_bytes(
        lines[0] + b"\n" + lines[1].replace(b'"sequence":2', b'"sequence":3') + b"\n"
    )
    report = _run(directory)
    assert _step(report, "audit-chain-anchor")["status"] == "blocked"


def test_provider_smokes_are_independent(tmp_path) -> None:
    """search fail + cloud-voice pass + llm 未跑：三步状态互不掩盖。"""
    directory = _full_passing_dir(tmp_path)
    _replace(
        {
            "search-smoke.json": {"step": "search-smoke", "executed": True, "result": "fail"},
            "cloud-voice-smoke.json": {
                "step": "cloud-voice-smoke",
                "executed": True,
                "result": "pass",
            },
            "llm-smoke.json": {"step": "llm-smoke", "executed": False, "result": "not_executed"},
        },
        directory,
    )
    report = _run(directory)
    statuses = _statuses(report)
    assert statuses["search-smoke"] == "blocked"
    assert statuses["cloud-voice-smoke"] == "pass"
    assert statuses["llm-smoke"] == "not_executed"
    # blocked 优先于 not_executed：整体 blocked
    assert report["overall_status"] == "blocked"
    actions = {item["step"]: item for item in report["next_actions"]}
    assert actions["llm-smoke"]["action"]


def test_backup_restore_unverified_pending(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    _replace(
        {
            "backup-restore.json": {
                "step": "backup-restore",
                "schema_version": "aios-backup-v1",
                "created_at": "2026-09-04T08:00:00+00:00",
                "restore_drill": {"verified": False, "inserted_rows": 0},
            }
        },
        directory,
    )
    report = _run(directory)
    assert _step(report, "backup-restore")["status"] == "pending"


def test_governance_counts_pending(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    _replace(
        {
            "legacy-papers.json": {
                "step": "legacy-papers",
                "pending_count": 3,
                "batches": [{"executed": True}, {"executed": False}],
            }
        },
        directory,
    )
    report = _run(directory)
    assert _step(report, "legacy-papers")["status"] == "pending"


# --- 5. 审批绑定 --------------------------------------------------------------


def test_missing_approval_is_not_executed(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    (directory / "cutover-approval.json").unlink()
    report = _run(directory)
    record = _step(report, "cutover-approval")
    assert record["status"] == "not_executed"
    assert report["overall_status"] == "not_executed"
    assert report["rehearsal_ready"] is False


def test_approval_coverage_gap_is_not_executed(tmp_path) -> None:
    """审批未覆盖其余 12 步 => not_executed（不是 pending/blocked）。"""
    directory = _full_passing_dir(tmp_path)
    _write_json(
        directory,
        "cutover-approval.json",
        _approval_payload(directory, skip="llm-smoke"),
    )
    report = _run(directory)
    record = _step(report, "cutover-approval")
    assert record["status"] == "not_executed"
    assert record["data"]["coverage_gaps"] == ["llm-smoke"]
    assert report["overall_status"] == "not_executed"


def test_approval_hash_mismatch_is_blocked(tmp_path) -> None:
    """审批后改动证据 => 哈希失配 => blocked；被改步自身仍 pass。"""
    directory = _full_passing_dir(tmp_path)
    _replace(
        {
            "llm-smoke.json": {
                "step": "llm-smoke",
                "executed": True,
                "result": "pass",
                "note_pad": "changed-after-approval",
            }
        },
        directory,
    )
    report = _run(directory)
    record = _step(report, "cutover-approval")
    assert record["status"] == "blocked"
    assert record["data"]["hash_mismatches"] == ["llm-smoke"]
    assert _step(report, "llm-smoke")["status"] == "pass"


def test_approval_note_never_echoed(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    payload = _approval_payload(directory)
    payload["note"] = f"窗口说明 {PASSWORD_MARKER} 勿泄"
    _write_json(directory, "cutover-approval.json", payload)
    report = _run(directory)
    assert PASSWORD_MARKER not in json.dumps(report, ensure_ascii=False)
    assert _step(report, "cutover-approval")["status"] == "pass"


def test_approval_unknown_step_reference_malformed(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    payload = _approval_payload(directory)
    payload["step_evidence"]["phantom-step"] = "ab" * 32
    _write_json(directory, "cutover-approval.json", payload)
    report = _run(directory)
    assert _step(report, "cutover-approval")["status"] == "blocked"


# --- 5a. 审批记录禁带 DRAFT 底稿元数据（M10-16 返工：DRAFT 不可审批边界） -----


@pytest.mark.parametrize(
    "field", sorted(cr.APPROVAL_DRAFT_RESERVED_FIELDS)
)
def test_approval_with_draft_metadata_field_is_blocked(tmp_path, field) -> None:
    """合法审批混入任一 approval-draft 底稿保留元数据字段（draft/
    manual_fields_required/confirmation_required/step_evidence_missing/
    load_problems/approval_file_present/generated_at/notice/tool/
    evidence_dir）=> malformed => blocked——字段存在即拒绝（与值无关），
    整体 blocked、绝不 ready；底稿补齐人工字段后改名/拼装不再有 pass 读法。"""
    directory = _full_passing_dir(tmp_path)
    payload = _approval_payload(directory)  # 哈希绑定本来精确匹配
    payload[field] = "draft-metadata"  # 值任意：字段存在即 fail-closed
    _write_json(directory, "cutover-approval.json", payload)
    report = _run(directory)
    record = _step(report, "cutover-approval")
    assert record["status"] == "blocked"
    assert field in record["reason"]
    assert report["overall_status"] == "blocked"
    assert report["rehearsal_ready"] is False
    # 只有审批步 blocked：其余 12 步不受影响
    assert all(
        item["status"] == "pass"
        for item in report["steps"]
        if item["step"] != "cutover-approval"
    )


def test_clean_approval_without_draft_metadata_still_passes(tmp_path) -> None:
    """对照锚：合法人工审批（不携带任何底稿元数据字段）=> pass/ready——
    拒绝名单只挡 DRAFT 底稿残留，不放宽也不收紧既有合法形态。"""
    report = _run(_full_passing_dir(tmp_path))
    assert _step(report, "cutover-approval")["status"] == "pass"
    assert report["rehearsal_ready"] is True


def test_reserved_draft_fields_are_draft_only_metadata(tmp_path) -> None:
    """拒绝名单与 approval-draft 底稿输出字段精确同步：恰为底稿全部字段
    减去审批合法共享的 step_evidence/supporting_evidence——底稿新增元数据
    字段而漏登记会在此红（跨模块单一事实源守卫）。"""
    from app.ops.cutover_evidence_pack import build_approval_draft

    directory = _full_passing_dir(tmp_path)
    (directory / "cutover-approval.json").unlink()  # 底稿不哈希审批自身
    draft = build_approval_draft(directory)
    assert set(cr.APPROVAL_DRAFT_RESERVED_FIELDS) == (
        set(draft) - {"step_evidence", "supporting_evidence"}
    )


# --- 5b. 审批 supporting 绑定（M10-15 返工：锚副本等 supporting 文件哈希） -----


def test_full_passing_with_supporting_binding_ready(tmp_path) -> None:
    """含锚副本的全 pass：审批绑定 12 主 step 哈希 + supporting 哈希全部匹配。"""
    directory = _full_passing_dir(tmp_path)
    (directory / "audit-anchor.jsonl").write_bytes(_anchor_lines())
    _write_json(directory, "cutover-approval.json", _approval_payload(directory))
    report = _run(directory)
    assert report["rehearsal_ready"] is True
    assert report["summary"] == {"pass": 13, "pending": 0, "blocked": 0, "not_executed": 0}
    record = _step(report, "cutover-approval")
    assert record["status"] == "pass"
    assert record["data"]["covered_supporting"] == ["audit-anchor.jsonl"]
    assert record["data"]["supporting_hash_mismatches"] == []
    digest = hashlib.sha256((directory / "audit-anchor.jsonl").read_bytes()).hexdigest()
    assert _step(report, "audit-chain-anchor")["supporting"][0]["sha256"] == digest


def test_approval_supporting_swap_after_approval_blocked(tmp_path) -> None:
    """审批后把锚副本换成另一份仍自洽、锚点数相同的副本：anchor 步仍 pass
    但 approval 变 blocked——主 step JSON 哈希盖不到的缺口由 supporting 绑定
    补上（Codex 审核发现的完整性漏洞回归测试）。"""
    directory = _full_passing_dir(tmp_path)
    (directory / "audit-anchor.jsonl").write_bytes(_anchor_lines())
    _write_json(directory, "cutover-approval.json", _approval_payload(directory))
    assert _statuses(_run(directory))["cutover-approval"] == "pass"
    # 审批后替换：不同 head_hash 构建的另一份自洽 2 锚点副本（load_anchor_file
    # 校验通过、锚点数与申报 anchors=2 一致，anchor 步自身无法发现）
    swapped = _anchor_lines("dd" * 32, "ee" * 32)
    (directory / "audit-anchor.jsonl").write_bytes(swapped)
    report = _run(directory)
    assert _step(report, "audit-chain-anchor")["status"] == "pass"
    record = _step(report, "cutover-approval")
    assert record["status"] == "blocked"
    assert record["data"]["supporting_hash_mismatches"] == ["audit-anchor.jsonl"]
    assert record["data"]["hash_mismatches"] == []
    assert report["overall_status"] == "blocked"
    # 输出零敏感：替换副本的正文不得回显（manifest 只透出文件名与 sha256）
    assert swapped.decode() not in json.dumps(report, ensure_ascii=False)


def test_approval_supporting_coverage_gap_not_executed(tmp_path) -> None:
    """锚副本存在但审批未绑定 supporting => not_executed（非 blocked）。"""
    directory = _full_passing_dir(tmp_path)
    (directory / "audit-anchor.jsonl").write_bytes(_anchor_lines())
    _write_json(
        directory,
        "cutover-approval.json",
        _approval_payload(directory, skip_supporting=True),
    )
    report = _run(directory)
    record = _step(report, "cutover-approval")
    assert record["status"] == "not_executed"
    assert record["data"]["supporting_coverage_gaps"] == ["audit-anchor.jsonl"]
    assert record["data"]["coverage_gaps"] == []
    assert report["overall_status"] == "not_executed"


def test_approval_supporting_hash_mismatch_blocked(tmp_path) -> None:
    """审批绑定的 supporting 哈希与当前副本不符 => blocked。"""
    directory = _full_passing_dir(tmp_path)
    (directory / "audit-anchor.jsonl").write_bytes(_anchor_lines())
    payload = _approval_payload(directory)
    payload["supporting_evidence"]["audit-anchor.jsonl"] = "00" * 32
    _write_json(directory, "cutover-approval.json", payload)
    report = _run(directory)
    record = _step(report, "cutover-approval")
    assert record["status"] == "blocked"
    assert record["data"]["supporting_hash_mismatches"] == ["audit-anchor.jsonl"]
    assert record["data"]["supporting_coverage_gaps"] == []


def test_approval_stale_supporting_entry_blocked(tmp_path) -> None:
    """审批引用当前不存在的 supporting 文件（审批后副本被移除）=> blocked；
    无副本时 anchor 步按申报锚点数评估不受影响。"""
    directory = _full_passing_dir(tmp_path)
    (directory / "audit-anchor.jsonl").write_bytes(_anchor_lines())
    payload = _approval_payload(directory)
    (directory / "audit-anchor.jsonl").unlink()
    _write_json(directory, "cutover-approval.json", payload)
    report = _run(directory)
    record = _step(report, "cutover-approval")
    assert record["status"] == "blocked"
    assert record["data"]["supporting_hash_mismatches"] == ["audit-anchor.jsonl"]
    assert _step(report, "audit-chain-anchor")["status"] == "pass"


@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (
            lambda p: p["supporting_evidence"].update({"phantom.txt": "ab" * 32}),
            "未知 supporting 文件",
        ),
        (
            lambda p: p["supporting_evidence"].update({"audit-anchor.jsonl": "AB" * 32}),
            "非 64 位小写十六进制",
        ),
        (lambda p: p.pop("supporting_evidence"), "缺字段 supporting_evidence"),
    ],
)
def test_approval_supporting_malformed_blocked(tmp_path, mutate, detail) -> None:
    """未知 supporting 文件名 / 非 64 位小写 hex / 缺字段 => malformed => blocked。"""
    directory = _full_passing_dir(tmp_path)
    (directory / "audit-anchor.jsonl").write_bytes(_anchor_lines())
    payload = _approval_payload(directory)
    mutate(payload)
    _write_json(directory, "cutover-approval.json", payload)
    report = _run(directory)
    record = _step(report, "cutover-approval")
    assert record["status"] == "blocked"
    assert detail in record["reason"]


def test_approval_empty_supporting_mapping_passes_without_files(tmp_path) -> None:
    """目录没有 supporting 文件时 supporting_evidence 必须为空 mapping（通过）。"""
    directory = _full_passing_dir(tmp_path)
    assert _approval_payload(directory)["supporting_evidence"] == {}
    report = _run(directory)
    record = _step(report, "cutover-approval")
    assert record["status"] == "pass"
    assert record["data"]["covered_supporting"] == []
    assert report["rehearsal_ready"] is True


# --- 6. 顶层优先级 ------------------------------------------------------------


def test_overall_precedence_blocked_over_pending_and_not_executed(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    _replace(
        {
            "search-smoke.json": {"step": "search-smoke", "executed": True, "result": "fail"},
            "legacy-papers.json": {
                "step": "legacy-papers",
                "pending_count": 2,
                "batches": [],
            },
        },
        directory,
    )
    (directory / "cutover-approval.json").unlink()
    report = _run(directory)
    statuses = _statuses(report)
    assert statuses["search-smoke"] == "blocked"
    assert statuses["legacy-papers"] == "pending"
    assert statuses["cutover-approval"] == "not_executed"
    assert report["overall_status"] == "blocked"
    assert report["rehearsal_ready"] is False


def test_overall_precedence_pending_over_not_executed(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    _replace(
        {
            "legacy-papers.json": {
                "step": "legacy-papers",
                "pending_count": 2,
                "batches": [],
            }
        },
        directory,
    )
    (directory / "cutover-approval.json").unlink()
    report = _run(directory)
    assert report["overall_status"] == "pending"
    assert report["rehearsal_ready"] is False


def test_overall_not_executed_when_only_missing_approval(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    (directory / "cutover-approval.json").unlink()
    report = _run(directory)
    assert report["overall_status"] == "not_executed"


# --- 7. 路径护栏与 IO ---------------------------------------------------------


def test_evidence_dir_missing_exits_2(tmp_path, capsys) -> None:
    code = _cli(tmp_path / "nope")
    assert code == 2
    assert "未产生 manifest" in capsys.readouterr().out


def test_evidence_dir_is_file_exits_2(tmp_path, capsys) -> None:
    target = tmp_path / "afile"
    target.write_text("x", encoding="utf-8")
    assert _cli(target) == 2


def test_evidence_dir_symlink_rejected(tmp_path, capsys) -> None:
    real = _evidence_dir(tmp_path, "real")
    link = tmp_path / "link"
    try:
        os.symlink(real, link, target_is_directory=True)
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    assert _cli(link) == 2
    assert "符号链接" in capsys.readouterr().out


def test_evidence_file_symlink_rejected(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    link = directory / "search-smoke.json"
    link.unlink()
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    assert _cli(directory) == 2


def test_evidence_filename_occupied_by_directory(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    (directory / "llm-smoke.json").unlink()
    (directory / "llm-smoke.json").mkdir()
    assert _cli(directory) == 2


def test_output_outside_artifacts_rejected(tmp_path, capsys) -> None:
    directory = _full_passing_dir(tmp_path)
    target = tmp_path / "report.json"
    assert _cli(directory, output=target) == 2
    assert not target.exists()
    assert "artifacts" in capsys.readouterr().out


def test_output_written_atomically_in_artifacts(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    target = tmp_path / "artifacts" / "rehearsal.json"
    assert _cli(directory, output=target) == 0
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["rehearsal_ready"] is True
    leftovers = [p.name for p in target.parent.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], "原子写不得残留临时文件"


def test_output_symlink_rejected_fail_closed(tmp_path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    real = artifacts / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = artifacts / "rehearsal.json"
    try:
        os.symlink(real, link)
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    directory = _full_passing_dir(tmp_path)
    assert _cli(directory, output=link) == 2
    assert real.read_text(encoding="utf-8") == "{}", "symlink 目标不得被写穿"


def test_output_failure_keeps_old_report_and_no_summary(tmp_path, capsys) -> None:
    """输出父级被普通文件占用：exit 2、旧报告保留、不打印步骤结论摘要。"""
    directory = _full_passing_dir(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    existing = artifacts / "rehearsal.json"
    existing.write_text('{"old": true}', encoding="utf-8")
    blocked = tmp_path / "blocker"
    blocked.write_text("not a dir", encoding="utf-8")
    # 用已存在但父级是普通文件的路径制造 mkdir 失败
    target = blocked / "rehearsal.json"
    code = _cli(directory, output=target)
    assert code == 2
    assert existing.read_text(encoding="utf-8") == '{"old": true}'
    assert "REHEARSAL READY" not in capsys.readouterr().out


# --- 8. 只读与零敏感 / 零 DB / 零网络 ------------------------------------------


def test_all_evidence_files_unchanged_after_runs(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    (directory / "audit-anchor.jsonl").write_bytes(_anchor_lines())
    # 补绑 supporting 哈希，保证两轮运行均为完整 pass 形态（exit 0）
    _write_json(directory, "cutover-approval.json", _approval_payload(directory))
    before = {
        p.name: p.read_bytes() for p in sorted(directory.iterdir())
    }
    assert _run(directory)["exit_code"] == 0
    assert _cli(directory) == 0
    after = {p.name: p.read_bytes() for p in sorted(directory.iterdir())}
    assert before == after


def test_poisoned_environment_not_consulted(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", f"postgres://u:{PASSWORD_MARKER}@h/db")
    monkeypatch.setenv("LLM_API_KEY", API_KEY_MARKER)
    monkeypatch.setenv("AIOS_PG_TEST_URL", f"postgres://u:{TOKEN_MARKER}@h/db")
    directory = _full_passing_dir(tmp_path)
    assert _cli(directory, as_json=True) == 0
    out = capsys.readouterr().out
    for marker in (PASSWORD_MARKER, API_KEY_MARKER, TOKEN_MARKER):
        assert marker not in out


def test_no_production_ids_in_any_output(tmp_path, capsys) -> None:
    """生产业务 ID marker 在 JSON manifest、人类摘要、输出文件零泄漏。"""
    directory = _full_passing_dir(tmp_path)
    # 在证据里植入生产 ID（治理步 batches 元数据额外键不参与白名单提取）
    _replace(
        {
            "legacy-papers.json": {
                "step": "legacy-papers",
                "pending_count": 0,
                "batches": [{"executed": True, "note": PAPER_ID_MARKER}],
            },
            "draft-ownership.json": {
                "step": "draft-ownership",
                "pending_count": 0,
                "batches": [{"executed": True, "note": DRAFT_ID_MARKER}],
            },
        },
        directory,
    )
    _write_json(directory, "cutover-approval.json", _approval_payload(directory))
    target = tmp_path / "artifacts" / "rehearsal.json"
    capsys.readouterr()
    assert _cli(directory, output=target) == 0
    human = capsys.readouterr().out
    report = _run(directory)
    rendered = json.dumps(report, ensure_ascii=False)
    file_text = target.read_text(encoding="utf-8")
    for marker in (PAPER_ID_MARKER, DRAFT_ID_MARKER):
        assert marker not in rendered
        assert marker not in human
        assert marker not in file_text
    assert _statuses(report)["legacy-papers"] == "pass"


def test_unrecognized_files_listed_without_content(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    (directory / "typo-cii-main.json").write_text("{}", encoding="utf-8")
    report = _run(directory)
    names = [item["file"] for item in report["unrecognized_files"]]
    assert names == ["typo-cii-main.json"]
    entry = report["unrecognized_files"][0]
    assert entry["sha256"] == hashlib.sha256(b"{}").hexdigest()
    assert set(entry) == {"file", "kind", "sha256"}


def test_isolation_note_present_in_manifest_and_summary(tmp_path) -> None:
    report = _run(_full_passing_dir(tmp_path))
    note = report["isolation_note"]
    assert "不代表生产验收" in note
    assert "不授权" in note
    assert "生产写入/发布" in note
    assert note in format_rehearsal_summary(report)


def _module_code_without_docstrings() -> str:
    """模块源码剥离模块级 docstring 后的代码面（docstring 允许描述边界）。"""
    import ast

    tree = ast.parse(Path(cr.__file__).read_text(encoding="utf-8"))
    module_doc = ast.get_docstring(tree) or ""
    source = Path(cr.__file__).read_text(encoding="utf-8")
    return source.replace(module_doc, "")


def test_module_has_no_db_or_network_surface() -> None:
    """行为面证明：模块代码零 DB/零网络/零环境变量引用。"""
    code = _module_code_without_docstrings()
    for banned in (
        "os.environ",
        "httpx",
        "requests",
        "socket",
        "urllib",
        "create_engine",
        "asyncpg",
        "psycopg",
        "subprocess",
    ):
        assert banned not in code, f"不得出现 {banned}"


def test_no_yes_flag_in_cli_source() -> None:
    """parser 注册区（p_cr 块）不得有 --yes 旗标：命令没有执行形态。"""
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    block = source.split("p_cr = sub.add_parser(")[1].split("p_ad = sub.add_parser(")[0]
    assert '"--yes"' not in block
