"""M11-18 production-evidence-gap：生产证据缺口 manifest 的矩阵/聚合/边界。

覆盖矩阵：
1. CLI 注册与分发：子命令注册、无 --yes 执行形态（argparse 未知旗标
   exit 2）、--evidence-dir 必填、main 分发、--output 越界拒绝；
2. 类别矩阵：恰四类（governance / audit-chain / provider-smoke /
   cutover-approval）且顺序固定，steps 全部是 cutover-rehearsal step id、
   类别间无重叠、并集恰为 8 步白名单（与 rehearsal 13 步交叉锁定），
   spec 静态字段齐备；provider-smoke 文案钉住 M14-70 拓扑聚合指引
   （--voice/--voice-mode 用法 + local-voice 单步导出），类别步骤仍精确
   为 search-smoke/cloud-voice-smoke/llm-smoke（local-voice-smoke 只是
   聚合证据轨道，不是新演练步）；
3. 状态聚合矩阵：类别内全 pass 才 pass；blocked > pending > not_executed
   （参数化直调 + 真实 fixture 行为级验证）；
4. 四类映射形态：全 pass fixture => 四类全 pass / exit 0；空目录 => 四类
   not_executed / exit 1；治理 pending_count>0 => governance=pending；
   敏感键证据 => 对应类 blocked（rehearsal malformed 语义透传）；链
   invalid / 冒烟 fail => blocked；缺审批 => cutover-approval=
   not_executed；跨类组合 blocked 优先 pending；
5. 输出 schema exact allowlist：顶层 / source / 每类 / covered_steps /
   missing_evidence 键集合恰为白名单（多键少键都红），tool 自声明，
   source.steps_covered 与类别并集、steps_total 与 rehearsal STEPS 同步；
6. production_ready 恒 false：全 pass fixture 下仍 false 且 note 声明
   缺口清单不构成生产放行（人类摘要同步含 production_ready=false）；
7. 路径与 IO 护栏（exit 2）：--output 越界、输出位于证据目录内（含
   ``..`` 折叠与大小写变体）、symlink 输出（含中间组件）、已存在目录
   目标；输出护栏先于证据读取（冲突形态下 rehearsal 零调用）；原子写
   失败旧文件字节原样、无 .tmp 残留、不打印缺口结论；
8. 零敏感与只读：毒化 marker（生产 ID/密码/key/token）在 JSON manifest、
   人类摘要与输出文件零泄漏（敏感键证据只透出 blocked 状态，值从不
   回显）；全部证据文件字节在运行（含 --output 落盘）前后不变；
9. 源码守卫：模块零 os.environ/getenv/DB/网络/subprocess/asyncio 引用；
   import 面恰为 cutover_rehearsal/evidence_kit/legacy_papers + 标准库；
   源码不含证据装载/schema 校验原语调用（不重复实现，全部复用
   cutover-rehearsal）；CLI 注册块无 --yes 旗标；
10. --json 模式：stdout 纯 JSON、与 --output 文件逐字一致、人读提示走
    stderr；
11. 端到端与复用证明：build 恰调用一次 run_cutover_rehearsal（复用而非
    重复实现）；全 pass fixture 下本工具的 covered 步状态与独立运行的
    rehearsal manifest 逐步一致。

全部测试只用临时目录与本地文件，不连接任何数据库、不发任何网络请求、
不读取任何环境变量。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ops import cli as cli_module
from app.ops import cutover_rehearsal as cr
from app.ops import production_evidence_gap as peg
from app.ops.production_evidence_gap import (
    CATEGORY_ORDER,
    CATEGORY_SPECS,
    ProductionGapInputError,
    build_production_evidence_gap,
    format_gap_summary,
)

#: 生产数据/密钥 marker：任何输出（JSON / 人类摘要 / 输出文件）都不得包含
PAPER_ID_MARKER = "PROD-PAPER-ID-77c1"
PASSWORD_MARKER = "PROD-PW-77c3"
API_KEY_MARKER = "sk-PROD-KEY-77c4"
TOKEN_MARKER = "PROD-TOKEN-77c5"

EXPECTED_CATEGORIES = ("governance", "audit-chain", "provider-smoke", "cutover-approval")
EXPECTED_CATEGORY_STEPS = {
    "governance": ("legacy-papers", "draft-ownership"),
    "audit-chain": ("audit-chain-verify", "audit-chain-anchor"),
    "provider-smoke": ("search-smoke", "cloud-voice-smoke", "llm-smoke"),
    "cutover-approval": ("cutover-approval",),
}

TOP_LEVEL_KEYS = {
    "generated_at",
    "tool",
    "evidence_dir",
    "read_only",
    "isolation_note",
    "no_execution_note",
    "scope_note",
    "source",
    "categories",
    "summary",
    "overall_status",
    "production_ready",
    "production_ready_note",
    "exit_code",
}
SOURCE_KEYS = {
    "tool",
    "steps_total",
    "steps_covered",
    "rehearsal_overall_status",
    "note",
}
CATEGORY_KEYS = {
    "category",
    "title",
    "basis",
    "status",
    "gap",
    "covered_steps",
    "existing_tools",
    "missing_evidence",
    "operator_actions",
    "agent_safe_actions",
    "authorization_required",
}
COVERED_STEP_KEYS = {"step", "status", "reason", "next_action"}
MISSING_EVIDENCE_KEYS = {"step", "status"}


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


def _passing_steps_evidence() -> dict[str, dict]:
    """12 步齐备且全部满足 pass 形态的证据（不含 cutover-approval；
    形态与 test_cutover_rehearsal 的同名 fixture 一致）。"""
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
    """按当前证据文件字节计算 sha256 的完整审批记录。"""
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


def _full_passing_dir(tmp_path: Path, name: str = "evidence") -> Path:
    directory = _evidence_dir(tmp_path, name)
    for filename, payload in _passing_steps_evidence().items():
        _write_json(directory, filename, payload)
    _write_json(directory, "cutover-approval.json", _approval_payload(directory))
    return directory


def _mutated_passing_dir(
    tmp_path: Path, mutations: dict[str, dict | None], name: str = "evidence"
) -> Path:
    """全 pass 基础上覆写/删除若干步证据（None = 删除文件）。

    覆写后按最终文件字节重签审批（相当于运维对变更后的证据重新审批），
    使测试只让目标步偏离 pass——除非 mutation 本身涉及审批文件。"""
    directory = _full_passing_dir(tmp_path, name)
    for filename, payload in mutations.items():
        path = directory / filename
        if payload is None:
            path.unlink()
        else:
            _write_json(directory, filename, payload)
    if "cutover-approval.json" not in mutations:
        _write_json(directory, "cutover-approval.json", _approval_payload(directory))
    return directory


def _build(directory: Path) -> dict:
    report, _ = build_production_evidence_gap(directory)
    return report


def _cli(directory, *, as_json=False, output=None) -> int:
    return cli_module._run_production_evidence_gap(
        SimpleNamespace(
            evidence_dir=str(directory),
            as_json=as_json,
            output=str(output) if output else None,
        )
    )


def _category(report: dict, category: str) -> dict:
    return next(item for item in report["categories"] if item["category"] == category)


def _statuses(report: dict) -> dict[str, str]:
    return {item["category"]: item["status"] for item in report["categories"]}


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {
        entry.name: entry.read_bytes()
        for entry in sorted(directory.iterdir())
        if entry.is_file()
    }


# --- 1. CLI 注册与分发 ----------------------------------------------------------


def test_cli_registered_and_has_no_execute_flag(tmp_path, monkeypatch) -> None:
    """CLI 子命令注册、无 --yes 执行形态（argparse 对未知旗标 exit 2）。"""
    assert hasattr(cli_module, "_run_production_evidence_gap")
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    assert '"production-evidence-gap"' in source
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "production-evidence-gap",
            "--evidence-dir",
            str(_evidence_dir(tmp_path)),
            "--yes",
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 2


def test_main_dispatch_production_evidence_gap(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli", "production-evidence-gap", "--evidence-dir", str(_full_passing_dir(tmp_path))],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "production_ready=false" in out
    assert "不构成生产放行" in out


def test_cli_missing_evidence_dir_arg_exits_2(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["cli", "production-evidence-gap"])
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 2


def test_cli_output_outside_artifacts_rejected(tmp_path, capsys) -> None:
    directory = _full_passing_dir(tmp_path)
    target = tmp_path / "gap.json"
    assert _cli(directory, output=target) == 2
    assert not target.exists()
    assert "artifacts" in capsys.readouterr().out


# --- 2. 类别矩阵 ----------------------------------------------------------------


def test_category_matrix_is_four_categories_over_eight_steps() -> None:
    """恰四类、顺序固定、步骤是 rehearsal step id、无重叠、并集为 8 步。"""
    assert CATEGORY_ORDER == EXPECTED_CATEGORIES
    all_steps: list[str] = []
    for spec in CATEGORY_SPECS:
        assert spec.steps == EXPECTED_CATEGORY_STEPS[spec.category]
        assert set(spec.steps) <= cr.STEP_IDS, "步骤必须是 rehearsal step id"
        assert spec.title and spec.basis and spec.authorization_required
        assert spec.existing_tools and spec.agent_safe_actions
        all_steps.extend(spec.steps)
    assert len(all_steps) == len(set(all_steps)), "类别间步骤不得重叠"
    assert len(all_steps) == 8


def test_provider_smoke_category_copy_pins_topology_guidance() -> None:
    """provider-smoke 类别文案承载 M14-70 拓扑聚合指引：语音按拓扑选轨
    （local=本地语音链路探针，hybrid/cloud=部署 key + 真实短语音），修复命令
    给出 --voice/--voice-mode 聚合用法与 local-voice 单步导出；同时类别步骤
    保持演练时间线的云语音步——cloud-voice-smoke 是 cutover-rehearsal 步骤、
    local-voice-smoke 只是聚合证据轨道（不是新演练步），类别步骤清单不因
    拓扑拆分而放宽。"""
    spec = next(s for s in CATEGORY_SPECS if s.category == "provider-smoke")
    # 步骤仍精确为三步（与 EXPECTED_CATEGORY_STEPS 同步的显式重复断言）
    assert spec.steps == ("search-smoke", "cloud-voice-smoke", "llm-smoke")
    # 类别名义显式承载三拓扑选轨
    assert "local|hybrid|cloud" in spec.title
    # basis 说明按拓扑选轨与演练步兼容性（local-voice-smoke 不是新演练步）
    assert "topology.voice_mode" in spec.basis
    assert "local-voice-smoke" in spec.basis
    assert "cloud-voice-smoke" in spec.basis
    assert "不是新演练步" in spec.basis
    # 修复命令：local-voice 单步导出 + --voice/--voice-mode 聚合用法 + 本地
    # 探针脚本
    tools_text = "\n".join(spec.existing_tools)
    assert "provider-smoke-export search|cloud-voice|local-voice|llm" in (
        tools_text
    )
    assert "provider-smoke-aggregate" in tools_text
    assert "--voice " in tools_text
    assert "--voice-mode local|hybrid|cloud" in tools_text
    assert "--voice-mode local 必须以 --voice 传 local-voice-smoke.json" in (
        tools_text
    )
    assert "smoke_voice_local.sh" in tools_text
    # 旧「三类统一真实 key 冒烟」口径不得回流到类别文案
    assert "真实 key" not in spec.title and "真实 key" not in spec.basis


def test_source_counts_pin_rehearsal_step_totals(tmp_path) -> None:
    """source.steps_total 与 rehearsal STEPS 同步、steps_covered 与类别并集同步。"""
    report = _build(_full_passing_dir(tmp_path))
    covered = {step for spec in CATEGORY_SPECS for step in spec.steps}
    assert report["source"]["steps_total"] == len(cr.STEPS)
    assert report["source"]["steps_covered"] == len(covered)
    assert report["source"]["tool"] == "cutover-rehearsal"
    # 演练时间线中确有四类之外的步骤（清单是聚焦视图，不是完整 rehearsal）
    assert cr.STEP_IDS - covered


# --- 3. 状态聚合矩阵 ------------------------------------------------------------


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (("pass", "pass"), "pass"),
        (("pass", "blocked"), "blocked"),
        (("blocked", "pending", "not_executed"), "blocked"),
        (("pass", "pending"), "pending"),
        (("pending", "not_executed"), "pending"),
        (("pass", "not_executed"), "not_executed"),
        (("blocked",), "blocked"),
        (("pending",), "pending"),
        (("not_executed",), "not_executed"),
    ],
)
def test_aggregate_status_precedence(statuses, expected) -> None:
    """全 pass 才 pass；blocked > pending > not_executed。"""
    assert peg._aggregate_status(list(statuses)) == expected


# --- 4. 四类映射形态（真实 fixture 行为级） --------------------------------------


def test_full_passing_fixture_all_four_pass(tmp_path) -> None:
    report, exit_code = build_production_evidence_gap(_full_passing_dir(tmp_path))
    assert _statuses(report) == {
        "governance": "pass",
        "audit-chain": "pass",
        "provider-smoke": "pass",
        "cutover-approval": "pass",
    }
    assert report["overall_status"] == "pass"
    assert report["summary"] == {"pass": 4, "pending": 0, "blocked": 0, "not_executed": 0}
    assert exit_code == 0
    for item in report["categories"]:
        assert item["missing_evidence"] == []
        assert item["operator_actions"] == []
        assert "无证据缺口" in item["gap"]
        for covered in item["covered_steps"]:
            assert covered["status"] == "pass"
            assert covered["next_action"] is None


def test_empty_dir_all_four_not_executed(tmp_path) -> None:
    report, exit_code = build_production_evidence_gap(_evidence_dir(tmp_path))
    assert set(_statuses(report).values()) == {"not_executed"}
    assert report["overall_status"] == "not_executed"
    assert report["exit_code"] == 1
    assert exit_code == 1
    for item in report["categories"]:
        assert item["missing_evidence"] and item["operator_actions"]
        assert "证据未提供或动作尚未发生" in item["gap"]


def test_governance_pending_when_counts_not_zero(tmp_path) -> None:
    directory = _mutated_passing_dir(
        tmp_path,
        {
            "legacy-papers.json": {
                "step": "legacy-papers",
                "pending_count": 5,
                "batches": [{"executed": True}],
            },
            "draft-ownership.json": {
                "step": "draft-ownership",
                "pending_count": 3,
                "batches": [{"executed": True}],
            },
        },
    )
    report = _build(directory)
    assert _statuses(report)["governance"] == "pending"
    assert "待人工决策或执行" in _category(report, "governance")["gap"]
    assert report["overall_status"] == "pending"


def test_sensitive_key_evidence_maps_to_blocked(tmp_path) -> None:
    """敏感键证据按 rehearsal blocked（malformed）语义映射；值从不回显。"""
    directory = _mutated_passing_dir(
        tmp_path,
        {
            "llm-smoke.json": {
                "step": "llm-smoke",
                "executed": True,
                "result": "pass",
                "api_key": API_KEY_MARKER,
            },
        },
    )
    report = _build(directory)
    assert _statuses(report)["provider-smoke"] == "blocked"
    assert report["overall_status"] == "blocked"
    provider = _category(report, "provider-smoke")
    assert any(
        item["status"] == "blocked" for item in provider["covered_steps"]
    )
    assert provider["operator_actions"]


def test_audit_chain_invalid_maps_to_blocked(tmp_path) -> None:
    directory = _mutated_passing_dir(
        tmp_path,
        {
            "audit-chain-verify.json": {
                "step": "audit-chain-verify",
                "valid": False,
                "entries": 10,
            },
        },
    )
    assert _statuses(_build(directory))["audit-chain"] == "blocked"


def test_search_smoke_fail_maps_to_blocked(tmp_path) -> None:
    directory = _mutated_passing_dir(
        tmp_path,
        {
            "search-smoke.json": {
                "step": "search-smoke",
                "executed": True,
                "result": "fail",
            },
        },
    )
    assert _statuses(_build(directory))["provider-smoke"] == "blocked"


def test_missing_approval_maps_to_not_executed(tmp_path) -> None:
    directory = _mutated_passing_dir(tmp_path, {"cutover-approval.json": None})
    assert _statuses(_build(directory))["cutover-approval"] == "not_executed"


def test_blocked_takes_precedence_over_pending_across_categories(tmp_path) -> None:
    """跨类组合：一类 blocked + 一类 pending => overall=blocked（先停下）。"""
    directory = _mutated_passing_dir(
        tmp_path,
        {
            "legacy-papers.json": {
                "step": "legacy-papers",
                "pending_count": 5,
                "batches": [{"executed": True}],
            },
            "search-smoke.json": {
                "step": "search-smoke",
                "executed": True,
                "result": "fail",
            },
        },
    )
    report = _build(directory)
    statuses = _statuses(report)
    assert statuses["governance"] == "pending"
    assert statuses["provider-smoke"] == "blocked"
    assert report["overall_status"] == "blocked"


# --- 5. 输出 schema exact allowlist ---------------------------------------------


def test_output_schema_exact_allowlist(tmp_path) -> None:
    report = _build(_mutated_passing_dir(tmp_path, {"cutover-approval.json": None}))
    assert set(report) == TOP_LEVEL_KEYS
    assert set(report["source"]) == SOURCE_KEYS
    assert report["tool"] == "production-evidence-gap"
    assert set(report["summary"]) == set(cr.STATUS_ORDER)
    assert [item["category"] for item in report["categories"]] == list(EXPECTED_CATEGORIES)
    for item in report["categories"]:
        assert set(item) == CATEGORY_KEYS
        for covered in item["covered_steps"]:
            assert set(covered) == COVERED_STEP_KEYS
        for missing in item["missing_evidence"]:
            assert set(missing) == MISSING_EVIDENCE_KEYS
        # 静态指引字段非空且为字符串列表/字符串
        assert item["existing_tools"] and all(
            isinstance(tool, str) and tool for tool in item["existing_tools"]
        )
        assert item["agent_safe_actions"] and all(
            isinstance(action, str) and action for action in item["agent_safe_actions"]
        )
        assert isinstance(item["authorization_required"], str) and item["authorization_required"]


# --- 6. production_ready 恒 false -----------------------------------------------


def test_production_ready_always_false_even_when_all_pass(tmp_path) -> None:
    report = _build(_full_passing_dir(tmp_path))
    assert report["production_ready"] is False
    assert report["exit_code"] == 0  # exit 0 只说明四类无缺口
    note = report["production_ready_note"]
    assert "production_ready" in note and "不构成生产放行" in note
    summary_text = format_gap_summary(report)
    assert "production_ready=false（固定）" in summary_text
    assert "不构成生产放行" in summary_text


def test_isolation_and_scope_notes_present(tmp_path) -> None:
    report = _build(_full_passing_dir(tmp_path))
    assert "不代表生产验收" in report["isolation_note"]
    assert "--yes" in report["no_execution_note"]
    assert "cutover-rehearsal manifest" in report["scope_note"]
    assert report["read_only"] is True


# --- 7. 路径与 IO 护栏 -----------------------------------------------------------


def test_output_inside_evidence_dir_rejected(tmp_path, monkeypatch) -> None:
    """输出位于证据目录内（含等于证据文件）拒绝；护栏先于证据读取。"""
    directory = _full_passing_dir(tmp_path)
    calls = []
    monkeypatch.setattr(
        peg, "run_cutover_rehearsal", lambda root: calls.append(root) or {}
    )
    for target in (
        directory / "gap.json",
        directory / "sub" / "gap.json",
    ):
        with pytest.raises(ProductionGapInputError):
            build_production_evidence_gap(directory, target)
    assert not calls, "输出护栏必须先于任何证据读取"


def test_output_path_equivalence_forms_rejected(tmp_path) -> None:
    """``..`` 折叠指向证据目录内不得绕过；Windows 上大小写变体同样拒绝。"""
    directory = _full_passing_dir(tmp_path)
    folded = directory / ".." / directory.name / "gap.json"
    with pytest.raises(ProductionGapInputError):
        build_production_evidence_gap(directory, folded)
    if os.name != "nt":
        return  # 大小写不敏感 FS 语义仅 Windows 需要（normcase 归一）
    with pytest.raises(ProductionGapInputError):
        build_production_evidence_gap(directory, directory / "GAP.JSON")


def test_output_equal_to_evidence_dir_rejected(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    with pytest.raises(ProductionGapInputError):
        build_production_evidence_gap(directory, directory)


def test_output_symlink_rejected(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    real = artifacts / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = artifacts / "link.json"
    try:
        os.symlink(real, link)
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    directory = _full_passing_dir(tmp_path)
    with pytest.raises(ProductionGapInputError):
        build_production_evidence_gap(directory, link)


def test_output_symlink_parent_component_rejected(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    real_dir = artifacts / "real-dir"
    real_dir.mkdir()
    link_dir = artifacts / "link-dir"
    try:
        os.symlink(real_dir, link_dir, target_is_directory=True)
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    directory = _full_passing_dir(tmp_path)
    with pytest.raises(ProductionGapInputError):
        build_production_evidence_gap(directory, link_dir / "gap.json")


def test_output_existing_directory_rejected_via_cli(tmp_path) -> None:
    """输出已存在且是目录：CLI 侧 mkdir/原子写拒绝（exit 2、无结论输出）。"""
    directory = _full_passing_dir(tmp_path)
    target = _artifacts(tmp_path) / "as-dir"
    target.mkdir()
    assert _cli(directory, output=target) == 2


def test_output_written_atomically_and_matches_stdout(tmp_path, capsys) -> None:
    artifacts = _artifacts(tmp_path)
    directory = _full_passing_dir(tmp_path)
    target = artifacts / "gap.json"
    capsys.readouterr()
    assert _cli(directory, as_json=True, output=target) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("{"), "stdout 必须是纯 JSON"
    assert "报告已写入" in captured.err, "人读提示走 stderr"
    assert json.loads(captured.out) == json.loads(target.read_text(encoding="utf-8"))
    assert not list(artifacts.glob("*.tmp"))


def test_write_failure_keeps_old_file_and_prints_no_verdict(
    tmp_path, monkeypatch, capsys
) -> None:
    artifacts = _artifacts(tmp_path)
    directory = _full_passing_dir(tmp_path)
    target = artifacts / "gap.json"
    target.write_text("OLD-CONTENT", encoding="utf-8")

    def _boom(path, text):
        raise OSError("disk full")

    monkeypatch.setattr(cli_module, "_write_report_atomic", _boom)
    capsys.readouterr()
    assert _cli(directory, output=target) == 2
    captured = capsys.readouterr()
    assert target.read_text(encoding="utf-8") == "OLD-CONTENT"
    assert not list(artifacts.glob("*.tmp"))
    assert "RESULT" not in captured.out, "写入失败不得打印缺口结论摘要"
    assert "写入失败" in captured.out


# --- 8. 零敏感与只读 -------------------------------------------------------------


def _poisoned_dir(tmp_path: Path) -> Path:
    """证据值内嵌生产 marker（合法脱敏形态外的多余字段，评估器忽略多余键，
    值不得进入任何输出）；另带一个敏感键步（blocked 语义）。"""
    return _mutated_passing_dir(
        tmp_path,
        {
            "legacy-papers.json": {
                "step": "legacy-papers",
                "pending_count": 2,
                "batches": [{"executed": True, "note": PAPER_ID_MARKER}],
            },
            "ci-main.json": {
                "step": "ci-main",
                "run_id": 1,
                "merge_commit": "aa11bb22cc33",
                "conclusion": "success",
                "remark": f"{PASSWORD_MARKER} {TOKEN_MARKER}",
            },
        },
    )


@pytest.mark.parametrize("surface", ("json", "summary", "file"))
def test_zero_sensitive_markers_on_all_surfaces(tmp_path, capsys, surface) -> None:
    directory = _poisoned_dir(tmp_path)
    if surface == "json":
        text = json.dumps(_build(directory), ensure_ascii=False)
    elif surface == "summary":
        text = format_gap_summary(_build(directory))
    else:
        target = _artifacts(tmp_path) / "gap.json"
        capsys.readouterr()
        assert _cli(directory, output=target) == 1
        text = target.read_text(encoding="utf-8")
    for marker in (PAPER_ID_MARKER, PASSWORD_MARKER, API_KEY_MARKER, TOKEN_MARKER):
        assert marker not in text


def test_evidence_files_bytes_unchanged_after_run_and_output(tmp_path) -> None:
    directory = _full_passing_dir(tmp_path)
    before = _snapshot(directory)
    target = _artifacts(tmp_path) / "gap.json"
    assert _cli(directory, output=target) == 0
    _build(directory)  # 再跑一遍纯读取
    assert _snapshot(directory) == before


def test_sensitive_key_value_never_echoed(tmp_path) -> None:
    """敏感键证据：输出只透出 blocked 状态与白名单文本，键名/值不复制。"""
    directory = _mutated_passing_dir(
        tmp_path,
        {
            "legacy-papers.json": {
                "step": "legacy-papers",
                "pending_count": 0,
                "batches": [{"executed": True}],
                "password": PASSWORD_MARKER,
            },
        },
    )
    report = _build(directory)
    assert _statuses(report)["governance"] == "blocked"
    text = json.dumps(report, ensure_ascii=False) + format_gap_summary(report)
    assert PASSWORD_MARKER not in text


# --- 9. 源码守卫 -----------------------------------------------------------------


def test_module_source_has_no_env_db_or_network_access() -> None:
    """源码级守卫：零环境变量/DB/网络/subprocess 引用（docstring 除外）。"""
    import ast

    source = Path(peg.__file__).read_text(encoding="utf-8")
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
    """import 面恰为 rehearsal/evidence_kit/legacy_papers + 标准库：复用既有
    装载与校验层，不引入新依赖面（DB/网络模块物理上进不来）。"""
    import ast

    tree = ast.parse(Path(peg.__file__).read_text(encoding="utf-8"))
    allowed = {"cutover_rehearsal", "evidence_kit", "legacy_papers"}
    stdlib = {"__future__", "os", "collections", "dataclasses", "datetime", "pathlib", "typing"}
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


def test_module_does_not_reimplement_evidence_validation() -> None:
    """源码不含证据装载/schema 校验原语调用：全部复用 cutover-rehearsal。"""
    source = Path(peg.__file__).read_text(encoding="utf-8")
    for primitive in (
        "load_json_object",
        "req_choice",
        "req_bool",
        "req_int",
        "req_dict",
        "find_sensitive_key",
        "find_embedded_credential",
        "sha256_file",
        "check_evidence_dir",
        "check_regular_file",
        "HEX64_RE",
    ):
        assert primitive not in source, f"不得自行实现 {primitive}（复用 rehearsal）"


def test_cli_parser_block_has_no_yes_flag() -> None:
    """parser 注册区（p_pg 块）不得有 --yes 旗标：命令没有执行形态。"""
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    block = source.split("p_pg = sub.add_parser(")[1]
    block = block.split("p_ep = sub.add_parser(")[0]
    assert "production-evidence-gap" in block
    assert '"--yes"' not in block


# --- 10. --json 模式 ------------------------------------------------------------


def test_json_stdout_pure_and_hints_on_stderr(tmp_path, capsys) -> None:
    directory = _full_passing_dir(tmp_path)
    capsys.readouterr()
    assert _cli(directory, as_json=True) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["tool"] == "production-evidence-gap"
    assert captured.err == ""  # 无 --output 时无提示


def test_human_summary_printed_without_json(tmp_path, capsys) -> None:
    directory = _full_passing_dir(tmp_path)
    capsys.readouterr()
    assert _cli(directory) == 0
    out = capsys.readouterr().out
    assert out.startswith("生产证据缺口清单")
    assert "RESULT: overall=pass" in out
    assert "production_ready=false（固定）" in out


# --- 11. 端到端与复用证明 -------------------------------------------------------


def test_build_reuses_cutover_rehearsal_exactly_once(tmp_path, monkeypatch) -> None:
    """build 恰调用一次 run_cutover_rehearsal：聚合唯一数据源，不自行评估。"""
    directory = _full_passing_dir(tmp_path)
    calls = []
    original = peg.run_cutover_rehearsal

    def _spy(root):
        calls.append(root)
        return original(root)

    monkeypatch.setattr(peg, "run_cutover_rehearsal", _spy)
    build_production_evidence_gap(directory)
    assert calls == [directory]


def test_covered_statuses_match_independent_rehearsal_run(tmp_path) -> None:
    """全 pass fixture 下 covered 步状态与独立运行的 rehearsal 逐步一致。"""
    directory = _full_passing_dir(tmp_path)
    rehearsal = cr.run_cutover_rehearsal(directory)
    rehearsal_statuses = {
        item["step"]: item["status"] for item in rehearsal["steps"]
    }
    report = _build(directory)
    for item in report["categories"]:
        for covered in item["covered_steps"]:
            assert covered["status"] == rehearsal_statuses[covered["step"]]
        assert item["status"] == "pass"
    assert report["source"]["rehearsal_overall_status"] == rehearsal["overall_status"]


def test_evidence_dir_guardrails_from_rehearsal(tmp_path, capsys) -> None:
    """--evidence-dir 不存在/普通文件 => exit 2（目录护栏复用 rehearsal）。"""
    assert _cli(tmp_path / "missing") == 2
    plain = tmp_path / "plain.json"
    plain.write_text("{}", encoding="utf-8")
    assert _cli(plain) == 2
    assert "证据目录" in capsys.readouterr().out
