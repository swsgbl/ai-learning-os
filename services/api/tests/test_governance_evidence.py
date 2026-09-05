"""M11-12 governance-evidence：治理报告 + 成功批次 -> rehearsal 治理步证据。

覆盖矩阵：
1. CLI 注册与无执行形态：子命令注册（--help 列出）、必填参数缺失 exit 2、
   无 --yes 旗标、main 分发；
2. 推导语义（核心）：pending_count = 报告明细 ID 集合 - 成功批次 eligible
   ID 并集——全部覆盖 => 0/exit 0；部分覆盖 => 差额/exit 1（证据照常
   落盘，不伪装归零）；多批次并集；报告归零 + 历史批次（批次 ID 不在
   报告内——assign-owner/export-delete 执行后重跑报告的自然形态）=> 0；
   keep-public 形态（批次 ID 仍在报告内）=> 0；draft 两类 kind 独立
   （同 ID 不同 kind 不串、只解决一类 => 另一类计入 pending）；
3. 失败批次 fail-closed（参数化）：真实 dry-run 形态（dry_run=true 且
   executed=false）/ **dry_run=true 且 executed=true 的拼改畸形形态** /
   dry_run 缺失或非布尔 / exit_code=1（未知 ID）/ failure 字段 / 缺
   executed / executed 非 bool => exit 2、不写输出、不打印推导结论；
4. audit_action 精确匹配：legacy/draft 每条迁移路径必须携带对应审计动作
   ——跨动作（keep-public 批次带 assign_owner）、跨 kind（legacy 批次带
   draft_owner.*、反向）、缺失 audit_action 均 exit 2；
5. 结构校验：报告 summary.total 与明细条数不一致 / 重复 ID / papers
   非 list / drafts kind 非法 / papers 与 drafts 同时存在或同时缺失 /
   批次类型与报告不匹配（legacy 批次配 draft 报告、反向）/ 批次迁移
   路径非法 / eligible 超出请求集合 / requested 与 ID 条数不一致 =>
   exit 2；
6. 输出不得覆盖任何输入：output resolved 路径 == 报告/批次 => exit 2、
   输出零写入、输入字节不变；等价路径书写形态（.. 折叠、Windows 大小写
   变体）不得绕过冲突比较；同一批次文件重复传入（直接重复与等价路径
   规范化后的重复）=> exit 2（不虚增成功批次数）；
7. 输出文件名约束：legacy 报告输出必须叫 legacy-papers.json（report.json
   / draft-ownership.json 均拒绝）、draft 报告同理；
8. 路径护栏与 IO（exit 2）：报告/批次/输出越界（非 artifacts/temp）、
   报告不存在、报告是目录、报告/批次/输出 symlink（含中间目录组件）、
   输出父级被普通文件占用（旧文件保留、无 .tmp 残留、不打印结论）；
9. 输出契约最小化（精确锁定）：顶层字段集合恰为白名单；batches 每项仅
   {"executed": true}；聚合计数 batches_executed；JSON 文本与人类摘要
   均不出现 migration_path/resolved_in_report/batch_files_sha256/
   batches_validated 等任何逐批路径、逐批解决计数、逐批哈希字段；
10. 输出契约与消费：证据直接过 cutover-rehearsal 的
   _step_legacy_papers/_step_draft_ownership 评估器（pending=0 => pass、
   >0 => pending）；落盘文件放进证据目录经完整 run_cutover_rehearsal
   消费同语义；find_sensitive_key/find_embedded_credential 零命中；
11. 零业务 ID/零敏感值/只读：报告与批次植入生产 ID/密码/token marker，
   stdout/stderr/摘要/落盘文件零泄漏；报告与批次文件字节运行前后不变；
   毒化环境变量不被读取；模块行为面零 DB/零网络（源码无 os.environ/
   引擎/HTTP/套接字引用）；
12. --json：stdout 纯 JSON（提示走 stderr）、与落盘文件逐字段一致。

全部测试只用临时目录与本地文件，不连接任何数据库、不发任何网络请求。
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
from app.ops import governance_evidence as ge
from app.ops.cutover_rehearsal import (
    EVALUATORS,
    run_cutover_rehearsal,
)
from app.ops.evidence_kit import find_embedded_credential, find_sensitive_key
from app.ops.governance_evidence import (
    GovernanceInputError,
    build_governance_evidence,
    format_governance_summary,
)
from app.ops.legacy_papers import _assemble_report

#: 生产数据/密钥 marker：任何输出（JSON / 人类摘要 / stderr / 输出文件）都不得包含
PAPER_ID_MARKER = "PROD-PAPER-ID-77d1"
DRAFT_ID_MARKER = "PROD-DRAFT-ID-77d2"
PASSWORD_MARKER = "PROD-PW-77d3"
TOKEN_MARKER = "PROD-TOKEN-77d4"

#: 迁移路径 -> 成功批次必须携带的精确审计动作（与 governance_evidence 的
#: 精确匹配契约同步；fixture 按此派生，保证合法批次始终匹配）。
_LEGACY_AUDIT_ACTION = {
    "keep-public": "ops.legacy_paper.keep_public",
    "assign-owner": "ops.legacy_paper.assign_owner",
    "export-delete": "ops.legacy_paper.export_delete",
}
_DRAFT_AUDIT_ACTION = {
    "assign-owner": "ops.draft_owner.assign_owner",
    "keep-unowned": "ops.draft_owner.keep_unowned",
}


# --- fixture 构造（与既有工具输出同构，不连 DB） --------------------------------


def _artifacts(tmp_path: Path) -> Path:
    directory = tmp_path / "artifacts"
    directory.mkdir()
    return directory


def _legacy_entry(paper_id: str, *, exam_count: int = 0) -> dict:
    return {
        "paper_id": paper_id,
        "title": f"卷-{paper_id}",
        "source": "web",
        "created_at": None,
        "updated_at": None,
        "subject": "数学",
        "year": 2023,
        "license": None,
        "origin_url": None,
        "question_count": 2,
        "total_score": 20.0,
        "referenced_by_exams": exam_count > 0,
        "exam_count": exam_count,
        "first_exam_started_at": None,
        "last_exam_started_at": None,
        "suggested_path": "keep_public" if exam_count else "export_review",
        "suggestion_reason": "测试构造",
    }


def _legacy_report(ids: list[str]) -> dict:
    """legacy-paper-report 完整形态（复用生产组装函数，结构不漂移）。"""
    return _assemble_report([_legacy_entry(pid) for pid in ids])


def _draft_report(items: list[tuple[str, str]]) -> dict:
    """draft-owner-report 完整形态（kind, draft_id) -> 报告 JSON。"""
    drafts = []
    by_kind: dict[str, list] = {}
    for kind, draft_id in items:
        entry = {
            "kind": kind,
            "draft_id": draft_id,
            "status": "pending_review",
            "created_at": "2026-09-04T08:00:00+00:00",
            "reviewed_at": None,
            "referenced_resource_ids": [],
            "referenced_resource_count": 0,
            "resource_owner_ids": [],
            "unowned_resource_count": 0,
            "missing_resource_ids": [],
            "suggested_path": "manual_review",
            "suggested_owner_id": None,
            "suggestion_reason": "草稿未引用任何资源（无归属锚），需人工判断",
        }
        if kind == "course-generation":
            entry.update(
                {
                    "goal": "测试目标",
                    "chapter_count": 1,
                    "dag_version": "v1",
                    "lesson_count": 1,
                }
            )
        else:
            entry.update({"generation_note": None, "variant_count": 1})
        drafts.append(entry)
        by_kind.setdefault(kind, []).append(entry)
    return {
        "generated_at": "2026-09-05T00:00:00+00:00",
        "scope": {"owner_id": None, "kinds": sorted(by_kind), "read_only": True},
        "notes": ["extraction", "suggestion"],
        "drafts": drafts,
        "summary": {"total": len(drafts), "by_kind": {k: {"total": len(v)} for k, v in by_kind.items()}},
    }


def _legacy_batch(
    ids: list[str], *, path: str = "keep-public", **overrides
) -> dict:
    batch = {
        "path": path,
        "dry_run": False,
        "requested": len(ids),
        "paper_ids": list(ids),
        "invalid_paper_ids": [],
        "eligible_paper_ids": list(ids),
        "skipped": [],
        "blocked_paper_ids": [],
        "executed": True,
        "executed_count": len(ids) if path != "keep-public" else 0,
        "papers_modified": len(ids) if path == "assign-owner" else 0,
        "audit_action": _LEGACY_AUDIT_ACTION[path],
        "exit_code": 0,
    }
    batch.update(overrides)
    return batch


def _draft_batch(
    kind: str,
    ids: list[str],
    *,
    path: str = "assign-owner",
    **overrides,
) -> dict:
    batch = {
        "path": path,
        "kind": kind,
        "dry_run": False,
        "requested": len(ids),
        "draft_ids": list(ids),
        "invalid_draft_ids": [],
        "eligible_draft_ids": list(ids),
        "skipped": [],
        "executed": True,
        "executed_count": len(ids),
        "drafts_modified": len(ids) if path == "assign-owner" else 0,
        "audit_action": _DRAFT_AUDIT_ACTION[path],
        "exit_code": 0,
    }
    batch.update(overrides)
    return batch


def _write_json(directory: Path, name: str, payload) -> Path:
    target = directory / name
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return target


def _cli(report, batches: list, output, *, as_json=False) -> int:
    return cli_module._run_governance_evidence(
        SimpleNamespace(
            report=str(report),
            batch=[str(item) for item in batches],
            output=str(output),
            as_json=as_json,
        )
    )


def _step(report: dict, step_id: str) -> dict:
    return next(item for item in report["steps"] if item["step"] == step_id)


# --- 1. CLI 注册与无执行形态 ----------------------------------------------------


def test_cli_registered_with_required_args(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys, "argv", ["cli", "governance-evidence", "--report", "x", "--batch", "y", "--output", "z"]
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    # 参数解析成功进入 handler（护栏拒绝输出路径 z：exit 2 属输入问题）
    assert excinfo.value.code == 2
    assert "拒绝执行" in capsys.readouterr().out


def test_cli_missing_required_args_exit_2(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["cli", "governance-evidence"])
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 2


def test_cli_has_no_execute_flag(monkeypatch) -> None:
    """命令没有 --yes 执行形态（argparse 对未知旗标 exit 2）。"""
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli", "governance-evidence", "--report", "x", "--batch", "y", "--output", "z", "--yes"],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 2


# --- 2. 推导语义 ---------------------------------------------------------------


def test_legacy_all_resolved_pending_zero(tmp_path, capsys) -> None:
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1", "p2", "p3"]))
    batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1", "p2", "p3"]))
    output = artifacts / "legacy-papers.json"
    assert _cli(report, [batch], output) == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["step"] == "legacy-papers"
    assert evidence["pending_count"] == 0
    assert evidence["batches_executed"] == 1
    assert evidence["report"] == {
        "total": 3,
        "resolved": 3,
        "sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
    }
    assert evidence["batches"] == [{"executed": True}]
    assert evidence["tool"] == "governance-evidence"
    assert evidence["read_only"] is True
    assert "RESULT: 治理计数已归零" in capsys.readouterr().out


def test_legacy_partially_resolved_pending_counts_delta(tmp_path, capsys) -> None:
    """部分覆盖 => pending=差额、exit 1（证据照常落盘，不伪装归零）。"""
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1", "p2", "p3"]))
    batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1"]))
    output = artifacts / "legacy-papers.json"
    assert _cli(report, [batch], output) == 1
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["pending_count"] == 2
    assert evidence["report"]["resolved"] == 1
    assert "仍有 2 条待人工决策" in capsys.readouterr().out


def test_legacy_multiple_batches_union(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1", "p2", "p3"]))
    first = _write_json(artifacts, "b1.json", _legacy_batch(["p1"]))
    second = _write_json(
        artifacts, "b2.json", _legacy_batch(["p2", "p3"], path="assign-owner")
    )
    output = artifacts / "legacy-papers.json"
    assert _cli(report, [first, second], output) == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["batches_executed"] == 2
    assert evidence["batches"] == [{"executed": True}, {"executed": True}]


def test_legacy_zero_report_with_historical_batches(tmp_path) -> None:
    """报告归零 + 历史批次（批次 ID 不在报告内——assign-owner/export-delete
    执行后重跑报告的自然形态）=> pending=0；报告聚合 resolved=0。"""
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report([]))
    batch = _write_json(
        artifacts, "batch.json", _legacy_batch(["gone-1", "gone-2"], path="assign-owner")
    )
    output = artifacts / "legacy-papers.json"
    assert _cli(report, [batch], output) == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["pending_count"] == 0
    assert evidence["report"] == {"total": 0, "resolved": 0, "sha256": evidence["report"]["sha256"]}


def test_legacy_keep_public_ids_still_in_report(tmp_path) -> None:
    """keep-public 不改行：批次 ID 仍在重跑报告中，同样推导归零。"""
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1", "p2"]))
    batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1", "p2"]))
    output = artifacts / "legacy-papers.json"
    assert _cli(report, [batch], output) == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["pending_count"] == 0
    assert evidence["report"]["resolved"] == 2


def test_draft_kinds_are_independent(tmp_path) -> None:
    """两类 kind 各自匹配：只解决一类 => 另一类计入 pending；同 ID 不同
    kind 的批次不得串味。"""
    artifacts = _artifacts(tmp_path)
    report = _write_json(
        artifacts,
        "report.json",
        _draft_report(
            [
                ("course-generation", "d1"),
                ("course-generation", "d2"),
                ("variant-question", "d1"),
                ("variant-question", "d3"),
            ]
        ),
    )
    batch = _write_json(
        artifacts, "batch.json", _draft_batch("course-generation", ["d1", "d2"])
    )
    output = artifacts / "draft-ownership.json"
    assert _cli(report, [batch], output) == 1
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["step"] == "draft-ownership"
    assert evidence["pending_count"] == 2
    # 补上 variant-question 批次（其 d1 与 course-generation 的 d1 同名）
    second = _write_json(
        artifacts, "b2.json", _draft_batch("variant-question", ["d1", "d3"], path="keep-unowned")
    )
    assert _cli(report, [batch, second], output) == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["pending_count"] == 0


def test_draft_full_resolution(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    report = _write_json(
        artifacts,
        "report.json",
        _draft_report([("course-generation", "c1"), ("variant-question", "v1")]),
    )
    first = _write_json(
        artifacts, "b1.json", _draft_batch("course-generation", ["c1"])
    )
    second = _write_json(
        artifacts, "b2.json", _draft_batch("variant-question", ["v1"], path="keep-unowned")
    )
    output = artifacts / "draft-ownership.json"
    assert _cli(report, [first, second], output) == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["pending_count"] == 0
    assert evidence["batches_executed"] == 2


# --- 3. 失败批次 fail-closed（dry_run 显式 false 属成功判定的前提） ---------------


@pytest.mark.parametrize(
    "overrides",
    [
        # 真实 dry-run plan 形态：dry_run=true 且 executed=false
        {"dry_run": True, "executed": False, "exit_code": 0},
        # 拼改畸形形态：dry_run=true 却自称 executed=true（此前会被接受）
        {"dry_run": True, "executed": True},
        # dry_run 非布尔（字符串"false"不是显式 false）
        {"dry_run": "false"},
        {"dry_run": None},
        {"executed": True, "exit_code": 1, "failure": "未知试卷 ID 1 个，拒绝执行（精确行契约）", "invalid_paper_ids": ["p1"]},
        {"executed": True, "exit_code": 0, "failure": "遗留失败字段"},
        {"executed": None},
        {"executed": "yes"},
    ],
)
def test_non_success_batch_rejected_fail_closed(tmp_path, capsys, overrides) -> None:
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1"]))
    batch = _write_json(
        artifacts, "batch.json", _legacy_batch(["p1"], **overrides)
    )
    output = artifacts / "legacy-papers.json"
    assert _cli(report, [batch], output) == 2
    assert not output.exists(), "失败批次不得产出证据文件"
    assert "RESULT" not in capsys.readouterr().out


def test_batch_without_dry_run_field_rejected(tmp_path, capsys) -> None:
    """缺 dry_run 字段（无显式非 dry-run 声明）=> exit 2：不写输出。"""
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1"]))
    payload = _legacy_batch(["p1"])
    payload.pop("dry_run")
    batch = _write_json(artifacts, "batch.json", payload)
    output = artifacts / "legacy-papers.json"
    assert _cli(report, [batch], output) == 2
    assert not output.exists()
    assert "dry_run" in capsys.readouterr().out


# --- 4. audit_action 与 path/kind 精确匹配 ---------------------------------------


@pytest.mark.parametrize(
    ("kind_of", "path", "action"),
    [
        # 跨动作：keep-public 批次带 assign_owner 审计动作
        ("legacy", "keep-public", "ops.legacy_paper.assign_owner"),
        # 跨动作：export-delete 批次带 keep_public 审计动作
        ("legacy", "export-delete", "ops.legacy_paper.keep_public"),
        # 跨 kind：legacy 批次带 draft_owner 审计动作
        ("legacy", "keep-public", "ops.draft_owner.assign_owner"),
        # 缺失 audit_action
        ("legacy", "keep-public", None),
        # 跨动作：draft assign-owner 批次带 keep_unowned 审计动作
        ("draft", "assign-owner", "ops.draft_owner.keep_unowned"),
        # 跨动作：draft keep-unowned 批次带 assign_owner 审计动作
        ("draft", "keep-unowned", "ops.draft_owner.assign_owner"),
        # 跨 kind：draft 批次带 legacy_paper 审计动作
        ("draft", "keep-unowned", "ops.legacy_paper.export_delete"),
        # 缺失 audit_action
        ("draft", "assign-owner", None),
    ],
)
def test_audit_action_must_match_path_and_kind(
    tmp_path, capsys, kind_of, path, action
) -> None:
    artifacts = _artifacts(tmp_path)
    if kind_of == "legacy":
        report = _write_json(artifacts, "report.json", _legacy_report(["p1"]))
        payload = _legacy_batch(["p1"], path=path)
        output = artifacts / "legacy-papers.json"
    else:
        report = _write_json(
            artifacts, "report.json", _draft_report([("course-generation", "d1")])
        )
        payload = _draft_batch("course-generation", ["d1"], path=path)
        output = artifacts / "draft-ownership.json"
    if action is None:
        payload.pop("audit_action")
    else:
        payload["audit_action"] = action
    batch = _write_json(artifacts, "batch.json", payload)
    assert _cli(report, [batch], output) == 2
    assert not output.exists()
    assert "audit_action 与迁移路径/kind 不匹配" in capsys.readouterr().out


def test_one_bad_batch_among_good_rejects_all(tmp_path) -> None:
    """多批中任一失败 => 整体拒绝（fail-closed，不产出低估 pending 的证据）。"""
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1", "p2"]))
    good = _write_json(artifacts, "good.json", _legacy_batch(["p1"]))
    bad = _write_json(artifacts, "bad.json", _legacy_batch(["p2"], executed=False))
    output = artifacts / "legacy-papers.json"
    assert _cli(report, [good, bad], output) == 2
    assert not output.exists()


def test_no_batch_rejected(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report([]))
    output = artifacts / "legacy-papers.json"
    assert _cli(report, [], output) == 2
    assert not output.exists()


# --- 5. 结构校验 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (lambda r: r["summary"].update(total=99), "summary.total=99"),
        (lambda r: r["papers"].append(dict(r["papers"][0])), "重复 paper_id"),
        (lambda r: r.update(papers={"not": "a list"}), "缺 papers"),
        (lambda r: r["papers"][0].pop("paper_id"), "缺 paper_id"),
        (lambda r: r.update(drafts=[]), "无法判别类型"),
        (lambda r: r.pop("summary"), "缺 summary"),
    ],
)
def test_malformed_legacy_report_rejected(tmp_path, capsys, mutate, detail) -> None:
    artifacts = _artifacts(tmp_path)
    payload = _legacy_report(["p1", "p2"])
    mutate(payload)
    report = _write_json(artifacts, "report.json", payload)
    batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1"]))
    output = artifacts / "legacy-papers.json"
    assert _cli(report, [batch], output) == 2
    assert not output.exists()
    assert detail in capsys.readouterr().out


@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (lambda r: r["summary"].update(total=5), "不一致"),
        (lambda r: r["drafts"].append(dict(r["drafts"][0])), "重复 (kind, draft_id)"),
        (lambda r: r["drafts"][0].update(kind="unknown-kind"), "kind 非法"),
        (lambda r: r.update(papers=[]), "无法判别类型"),
    ],
)
def test_malformed_draft_report_rejected(tmp_path, capsys, mutate, detail) -> None:
    artifacts = _artifacts(tmp_path)
    payload = _draft_report([("course-generation", "d1")])
    mutate(payload)
    report = _write_json(artifacts, "report.json", payload)
    batch = _write_json(
        artifacts, "batch.json", _draft_batch("course-generation", ["d1"])
    )
    output = artifacts / "draft-ownership.json"
    assert _cli(report, [batch], output) == 2
    assert detail in capsys.readouterr().out


def test_batch_type_must_match_report_type(tmp_path, capsys) -> None:
    """legacy 批次配 draft 报告（或反向）=> 拒绝。"""
    artifacts = _artifacts(tmp_path)
    legacy_report = _write_json(artifacts, "lr.json", _legacy_report(["p1"]))
    draft_report = _write_json(
        artifacts, "dr.json", _draft_report([("course-generation", "d1")])
    )
    legacy_batch = _write_json(artifacts, "lb.json", _legacy_batch(["p1"]))
    draft_batch = _write_json(
        artifacts, "db.json", _draft_batch("course-generation", ["d1"])
    )
    assert _cli(legacy_report, [draft_batch], artifacts / "legacy-papers.json") == 2
    assert _cli(draft_report, [legacy_batch], artifacts / "draft-ownership.json") == 2
    assert "不是 legacy-paper-migrate 批次形态" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (lambda b: b.update(path="drop-all"), "迁移路径非法"),
        (lambda b: b.update(eligible_paper_ids=["p1", "ghost"]), "超出 paper_ids 集合"),
        (lambda b: b.update(requested=9), "requested=9"),
        (lambda b: b.pop("paper_ids"), "paper_ids"),
        (lambda b: b.update(paper_ids=["p1", 42]), "元素必须是非空字符串"),
        (lambda b: b.update(invalid_paper_ids=["p1"], exit_code=1), "exit_code 非 0"),
    ],
)
def test_malformed_legacy_batch_rejected(tmp_path, capsys, mutate, detail) -> None:
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1"]))
    payload = _legacy_batch(["p1"])
    mutate(payload)
    batch = _write_json(artifacts, "batch.json", payload)
    output = artifacts / "legacy-papers.json"
    assert _cli(report, [batch], output) == 2
    assert detail in capsys.readouterr().out


# --- 6. 输出不得覆盖任何输入 / 重复批次拒绝 --------------------------------------


@pytest.mark.parametrize("which", ["report", "batch"])
def test_output_must_not_overwrite_any_input(tmp_path, capsys, which) -> None:
    """输出 resolved 路径 == 报告/批次 => exit 2：输出零写入、输入字节不变。"""
    artifacts = _artifacts(tmp_path)
    if which == "report":
        # 报告文件名恰好占用合法输出名，才能构造 output == report 冲突
        report = _write_json(
            artifacts, "legacy-papers.json", _legacy_report(["p1", "p2"])
        )
        batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1"]))
        output = report
    else:
        report = _write_json(artifacts, "report.json", _legacy_report(["p1", "p2"]))
        batch = _write_json(artifacts, "legacy-papers.json", _legacy_batch(["p1"]))
        output = batch
    before = {p.name: p.read_bytes() for p in (report, batch)}
    assert _cli(report, [batch], output) == 2
    captured = capsys.readouterr()
    assert "拒绝覆盖输入" in captured.out
    assert "RESULT" not in captured.out
    after = {p.name: p.read_bytes() for p in (report, batch)}
    assert before == after, "冲突时输入字节必须保持不变（输出不得写入/修改）"


@pytest.mark.parametrize("variant", ["dotdot", "case"])
def test_path_spelling_cannot_bypass_conflict_guard(
    tmp_path, capsys, variant
) -> None:
    """等价路径书写形态（.. 折叠、大小写变体）不得绕过输出覆盖护栏。"""
    if variant == "case" and os.path.normcase("A") != os.path.normcase("a"):
        pytest.skip("此平台路径大小写敏感，大小写变体不是等价路径")
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1"]))
    batch = _write_json(artifacts, "legacy-papers.json", _legacy_batch(["p1"]))
    if variant == "dotdot":
        conflict = os.path.join(
            str(artifacts), "..", artifacts.name, batch.name
        )
    else:
        conflict = os.path.join(str(artifacts), batch.name.swapcase())
    before = batch.read_bytes()
    assert _cli(report, [batch], conflict) == 2
    assert "拒绝覆盖输入" in capsys.readouterr().out
    assert batch.read_bytes() == before, "输入字节必须保持不变"


def test_duplicate_batch_paths_rejected(tmp_path, capsys) -> None:
    """同一批次文件重复传入（直接重复与等价路径规范化后的重复）=> exit 2、
    不写输出——重复批次不得虚增成功批次数/伪造覆盖面。"""
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1"]))
    batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1"]))
    output = artifacts / "legacy-papers.json"
    assert _cli(report, [batch, batch], output) == 2
    captured = capsys.readouterr()
    assert "重复批次" in captured.out
    assert "RESULT" not in captured.out
    assert not output.exists()
    if os.path.normcase("A") == os.path.normcase("a"):
        duplicate = os.path.join(str(artifacts), batch.name.swapcase())
    else:
        duplicate = os.path.join(str(artifacts), "..", artifacts.name, batch.name)
    assert _cli(report, [batch, duplicate], output) == 2
    assert "重复批次" in capsys.readouterr().out
    assert not output.exists()


# --- 7. 输出文件名约束 ----------------------------------------------------------


@pytest.mark.parametrize("name", ["evidence.json", "draft-ownership.json", "legacy-papers"])
def test_output_filename_must_be_exact_evidence_file(tmp_path, capsys, name) -> None:
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1"]))
    batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1"]))
    assert _cli(report, [batch], artifacts / name) == 2
    assert "legacy-papers.json" in capsys.readouterr().out
    assert not (artifacts / name).exists()


def test_draft_output_filename_for_draft_report(tmp_path, capsys) -> None:
    artifacts = _artifacts(tmp_path)
    report = _write_json(
        artifacts, "report.json", _draft_report([("course-generation", "d1")])
    )
    batch = _write_json(
        artifacts, "batch.json", _draft_batch("course-generation", ["d1"])
    )
    assert _cli(report, [batch], artifacts / "legacy-papers.json") == 2
    assert "draft-ownership.json" in capsys.readouterr().out


# --- 8. 路径护栏与 IO ----------------------------------------------------------


@pytest.mark.parametrize("which", ["report", "batch", "output"])
def test_paths_outside_artifacts_rejected(tmp_path, which) -> None:
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1"]))
    batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1"]))
    if which == "report":
        report = _write_json(tmp_path, "report.json", _legacy_report(["p1"]))
    elif which == "batch":
        batch = _write_json(tmp_path, "batch.json", _legacy_batch(["p1"]))
    output = tmp_path / "legacy-papers.json" if which == "output" else artifacts / "legacy-papers.json"
    assert _cli(report, [batch], output) == 2
    assert not output.exists()


def test_missing_or_directory_report_rejected(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1"]))
    assert _cli(artifacts / "nope.json", [batch], artifacts / "legacy-papers.json") == 2
    (artifacts / "adir").mkdir()
    assert _cli(artifacts / "adir", [batch], artifacts / "legacy-papers.json") == 2


@pytest.mark.parametrize("which", ["report", "batch", "output"])
def test_symlinks_rejected(tmp_path, which) -> None:
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1"]))
    batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1"]))
    output = artifacts / "legacy-papers.json"
    real = _write_json(tmp_path, "real.json", _legacy_report(["p1"]))
    link = artifacts / "link.json"
    try:
        if which == "report":
            report = link
        elif which == "batch":
            batch = link
        else:
            output = link
        os.symlink(real, link)
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    assert _cli(report, [batch], output) == 2
    assert not link.exists() or link.is_symlink(), "symlink 本身不被改写"
    assert real.read_text(encoding="utf-8"), "symlink 目标保持原样"


def test_symlink_directory_component_rejected(tmp_path) -> None:
    """中间目录组件是 symlink 也拒绝（链接目标即使落在护栏内）。"""
    artifacts = _artifacts(tmp_path)
    real_dir = tmp_path / "real-dir"
    real_dir.mkdir()
    real_report = _write_json(real_dir, "report.json", _legacy_report(["p1"]))
    link_dir = artifacts / "link-dir"
    try:
        os.symlink(real_dir, link_dir, target_is_directory=True)
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1"]))
    assert (
        _cli(
            link_dir / "report.json",
            [batch],
            artifacts / "legacy-papers.json",
        )
        == 2
    )
    assert real_report.read_text(encoding="utf-8"), "目标文件保持原样"


def test_output_symlink_target_not_written_through(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1"]))
    batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1"]))
    real = artifacts / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = artifacts / "legacy-papers.json"
    try:
        os.symlink(real, link)
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    assert _cli(report, [batch], link) == 2
    assert real.read_text(encoding="utf-8") == "{}", "symlink 目标不得被写穿"


def test_output_write_failure_keeps_old_file_and_no_summary(
    tmp_path, capsys, monkeypatch
) -> None:
    """写入失败（磁盘满/IO）：exit 2、旧证据字节保留、不打印推导结论。"""
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1"]))
    batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1"]))
    existing = artifacts / "legacy-papers.json"
    existing.write_text('{"old": true}', encoding="utf-8")

    def boom(path, text):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cli_module, "_write_report_atomic", boom)
    assert _cli(report, [batch], existing) == 2
    assert existing.read_text(encoding="utf-8") == '{"old": true}'
    assert "RESULT" not in capsys.readouterr().out


def test_output_written_atomically_no_tmp_leftover(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1"]))
    batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1"]))
    output = artifacts / "legacy-papers.json"
    assert _cli(report, [batch], output) == 0
    leftovers = [p.name for p in artifacts.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], "原子写不得残留临时文件"


# --- 9. 输出契约最小化（精确锁定） -----------------------------------------------

#: 顶层字段白名单：多一个少一个都算契约漂移（评估器只需 step/pending_count/
#: batches，其余是脱敏元数据与聚合）
EXPECTED_TOP_LEVEL_KEYS = {
    "tool",
    "step",
    "pending_count",
    "batches",
    "batches_executed",
    "generated_at",
    "report",
    "read_only",
    "no_execution_note",
}

#: 不得出现在输出 JSON 文本与人类摘要中的逐批细节字段/形态
FORBIDDEN_DETAIL_KEYS = (
    "migration_path",
    "resolved_in_report",
    "batch_files_sha256",
    "batches_validated",
)


def test_output_contract_is_minimal(tmp_path, capsys) -> None:
    """输出契约精确锁定：顶层字段集合恰为白名单；batches 每项仅
    {"executed": true}；成功批次数只以聚合计数 batches_executed 表达；
    落盘 JSON 与人类摘要都不出现任何逐批路径/逐批解决计数/逐批哈希。"""
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1", "p2"]))
    first = _write_json(artifacts, "b1.json", _legacy_batch(["p1"]))
    second = _write_json(
        artifacts, "b2.json", _legacy_batch(["p2"], path="assign-owner")
    )
    output = artifacts / "legacy-papers.json"
    capsys.readouterr()
    assert _cli(report, [first, second], output) == 0
    file_text = output.read_text(encoding="utf-8")
    evidence = json.loads(file_text)
    assert set(evidence) == EXPECTED_TOP_LEVEL_KEYS
    assert evidence["batches"] == [{"executed": True}, {"executed": True}]
    assert evidence["batches_executed"] == 2
    assert set(evidence["report"]) == {"total", "resolved", "sha256"}
    for key in FORBIDDEN_DETAIL_KEYS:
        assert key not in file_text, f"输出不得含逐批细节字段 {key}"
    summary = format_governance_summary(evidence)
    for key in FORBIDDEN_DETAIL_KEYS:
        assert key not in summary
    # 摘要与 stdout 也不打印逐批迁移路径枚举（keep-public/assign-owner）
    assert "keep-public" not in summary
    assert "assign-owner" not in summary
    captured = capsys.readouterr()
    assert "migration_path" not in captured.out
    assert "成功批次: 2" in captured.out


# --- 10. 输出契约与消费 ---------------------------------------------------------


@pytest.mark.parametrize(
    ("pending", "expected_status"),
    [(0, "pass"), (2, "pending")],
)
def test_evidence_passes_rehearsal_evaluator_directly(pending, expected_status) -> None:
    """证据 dict 直接过 cutover-rehearsal 治理步评估器（白名单字段可提取）。"""
    evidence = {
        "step": "legacy-papers",
        "pending_count": pending,
        "batches": [{"executed": True}],
    }
    status, reason, _action, data, _supporting = EVALUATORS["legacy-papers"](
        evidence, None, {}
    )
    assert status == expected_status
    assert data["pending_count"] == pending
    assert reason


@pytest.mark.parametrize("pending", [0, 1])
def test_evidence_has_no_sensitive_surface(pending) -> None:
    evidence = {
        "step": "draft-ownership",
        "pending_count": pending,
        "batches": [{"executed": True}],
        "tool": "governance-evidence",
        "generated_at": "2026-09-05T00:00:00+00:00",
        "report": {"total": 3, "resolved": 3 - pending, "sha256": "ab" * 32},
        "batches_executed": 1,
        "read_only": True,
        "no_execution_note": "隔离声明",
    }
    assert find_sensitive_key(evidence) is None
    assert find_embedded_credential(evidence) is None


def test_written_file_consumed_by_full_rehearsal(tmp_path) -> None:
    """落盘证据放进证据目录经完整 run_cutover_rehearsal 消费：pending=0 =>
    该步 pass（其余步缺证据 not_executed）；pending>0 => 该步 pending。"""
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1", "p2"]))
    batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1"]))
    output = artifacts / "legacy-papers.json"
    assert _cli(report, [batch], output) == 1
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "legacy-papers.json").write_text(
        output.read_text(encoding="utf-8"), encoding="utf-8"
    )
    rehearsal = run_cutover_rehearsal(evidence_dir)
    record = _step(rehearsal, "legacy-papers")
    assert record["status"] == "pending"
    assert record["data"]["pending_count"] == 1
    # 补齐第二批后归零 => rehearsal 该步 pass
    second = _write_json(artifacts, "b2.json", _legacy_batch(["p2"]))
    assert _cli(report, [batch, second], output) == 0
    (evidence_dir / "legacy-papers.json").write_text(
        output.read_text(encoding="utf-8"), encoding="utf-8"
    )
    rehearsal = run_cutover_rehearsal(evidence_dir)
    assert _step(rehearsal, "legacy-papers")["status"] == "pass"


def test_written_draft_file_consumed_by_rehearsal(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    report = _write_json(
        artifacts, "report.json", _draft_report([("course-generation", "c1")])
    )
    batch = _write_json(
        artifacts, "batch.json", _draft_batch("course-generation", ["c1"])
    )
    output = artifacts / "draft-ownership.json"
    assert _cli(report, [batch], output) == 0
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "draft-ownership.json").write_text(
        output.read_text(encoding="utf-8"), encoding="utf-8"
    )
    rehearsal = run_cutover_rehearsal(evidence_dir)
    assert _step(rehearsal, "draft-ownership")["status"] == "pass"


# --- 11. 零业务 ID / 零敏感值 / 只读 --------------------------------------------


def test_no_production_ids_or_secrets_in_any_output(tmp_path, capsys) -> None:
    """报告与批次植入生产 ID/密码/token marker：stdout/stderr/摘要/落盘
    证据零泄漏（明细不透传，只有计数/枚举/哈希）。"""
    artifacts = _artifacts(tmp_path)
    payload = _legacy_report([PAPER_ID_MARKER, "p2"])
    payload["papers"][0]["title"] = f"标题含 {PASSWORD_MARKER}"
    report = _write_json(artifacts, "report.json", payload)
    batch_payload = _legacy_batch(
        [PAPER_ID_MARKER, "p2"], note=f"备注 {TOKEN_MARKER}"
    )
    batch = _write_json(artifacts, "batch.json", batch_payload)
    output = artifacts / "legacy-papers.json"
    capsys.readouterr()
    assert _cli(report, [batch], output, as_json=True) == 0
    captured = capsys.readouterr()
    file_text = output.read_text(encoding="utf-8")
    evidence = json.loads(file_text)
    summary = format_governance_summary(evidence)
    for marker in (PAPER_ID_MARKER, PASSWORD_MARKER, TOKEN_MARKER):
        assert marker not in captured.out
        assert marker not in captured.err
        assert marker not in file_text
        assert marker not in summary
    evidence_scanned = json.loads(file_text)
    assert find_sensitive_key(evidence_scanned) is None
    assert find_embedded_credential(evidence_scanned) is None


def test_report_and_batch_files_unchanged_after_runs(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1", "p2"]))
    batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1"]))
    output = artifacts / "legacy-papers.json"
    before = {p.name: p.read_bytes() for p in (report, batch)}
    assert _cli(report, [batch], output) == 1
    assert _cli(report, [batch], output, as_json=True) == 1
    after = {p.name: p.read_bytes() for p in (report, batch)}
    assert before == after


def test_poisoned_environment_not_consulted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"postgres://u:{PASSWORD_MARKER}@h/db")
    monkeypatch.setenv("LLM_API_KEY", TOKEN_MARKER)
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1"]))
    batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1"]))
    output = artifacts / "legacy-papers.json"
    assert _cli(report, [batch], output) == 0
    assert PASSWORD_MARKER not in output.read_text(encoding="utf-8")


def test_module_has_no_db_or_network_surface() -> None:
    """行为面证明：模块源码零 DB/零网络/零环境变量/零子进程引用。"""
    import ast

    source = Path(ge.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_doc = ast.get_docstring(tree) or ""
    code = source.replace(module_doc, "")
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
        "asyncio",
    ):
        assert banned not in code, f"不得出现 {banned}"


def test_cli_source_has_no_yes_flag() -> None:
    """parser 注册区（p_ge 块）不得有 --yes 旗标：命令没有执行形态。"""
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    block = source.split("p_ge = sub.add_parser(")[1].split("p_ep = sub.add_parser(")[0]
    assert '"--yes"' not in block


# --- 12. --json 模式 ------------------------------------------------------------


def test_json_stdout_pure_and_matches_file(tmp_path, capsys) -> None:
    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1"]))
    batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1"]))
    output = artifacts / "legacy-papers.json"
    capsys.readouterr()
    assert _cli(report, [batch], output, as_json=True) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("{"), "stdout 必须是纯 JSON"
    assert "证据已写入" in captured.err, "人读提示走 stderr"
    assert json.loads(captured.out) == json.loads(output.read_text(encoding="utf-8"))


# --- 直调 build_governance_evidence 的补充（注入 clock） ------------------------


def test_build_evidence_injected_clock_and_error_type(tmp_path) -> None:
    """纯函数直调：注入 clock 确定性时间；空批次与越界路径抛 GovernanceInputError。"""
    from datetime import UTC, datetime

    artifacts = _artifacts(tmp_path)
    report = _write_json(artifacts, "report.json", _legacy_report(["p1"]))
    batch = _write_json(artifacts, "batch.json", _legacy_batch(["p1"]))
    fixed = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)
    evidence, exit_code = build_governance_evidence(
        report, [batch], artifacts / "legacy-papers.json", clock=lambda: fixed
    )
    assert exit_code == 0
    assert evidence["generated_at"] == fixed.isoformat()
    with pytest.raises(GovernanceInputError):
        build_governance_evidence(report, [], artifacts / "legacy-papers.json")
    with pytest.raises(GovernanceInputError):
        build_governance_evidence(
            tmp_path / "outside.json", [batch], artifacts / "legacy-papers.json"
        )
