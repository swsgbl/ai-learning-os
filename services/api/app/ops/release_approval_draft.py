"""M14-151 release-approval-draft：发布审批 DRAFT 底稿生成器（只读）。

定位：release-readiness（M10-11+M14-73 十一门）的 release-approval 门要求
审批人从零人工组装 release-approval.json 并精确绑定各门证据 sha256——本
工具把「当前证据目录里每门哈希是多少、哪些门缺席、哪些必需门还缺证据、
各门状态如何」收敛为一份**哈希底稿**，供审批人逐项人工核对，免去手工
逐文件算哈希的机械部分。与 cutover-evidence-pack approval-draft（M10-16，
13 步切换契约）同构，但作用于 11 门 readiness 契约。

安全边界（docs/DEVELOPMENT.md「发布准备 readiness」节同步维护）：

- **DRAFT 底稿不是审批记录**：输出固定带 ``draft: true``、人工必填字段
  清单（manual_fields_required，全部 REPLACE-ME 提示）与「改名只会
  malformed」声明。底稿改名为 release-approval.json 必然被
  ``_eval_release_approval`` 拒绝：携带任一底稿保留元数据字段
  （``release_readiness.APPROVAL_DRAFT_RESERVED_FIELDS``，测试守卫与
  本模块输出字段同步）即 malformed——即使补齐 gate 自声明与全部人工
  字段、哈希精确匹配也不放行。合法审批必须从底稿哈希出发**从零组装**；
- **绝不创建或修改 release-approval.json、绝不签署、绝不放行**：本工具
  不写证据目录内的任何文件；输出全称不含 release_ready/production_ready
  （readiness 子集只透出 summary 计数与未过门清单——本工具永不作放行
  结论）；approved_by 等人工字段只列名与提示，绝不代填；
- 只读本地证据：不连接数据库、不调用 API、不访问网络、不读取任何环境
  变量（``os.environ`` 零引用）；不执行任何生产操作（无 --yes 执行形态）；
- 门语义零重复：门状态只消费 ``run_release_readiness`` 的既有评估结果
  （其内部的路径护栏、装载、敏感键扫描、schema 校验与最终抹除原样生效）；
  本模块自算的哈希仅用于 gate_evidence 底稿（与 readiness 的装载层同
  口径：symlink/非常规文件 fail-closed）；
- 输出零敏感：哈希与白名单问题摘要（敏感键/内嵌凭据命中只报字段路径，
  值从不回显），最终经 evidence_kit.scrub_sensitive 兜底；
- CLI 落盘护栏：--output 必须位于 gitignore 的 artifacts/temp 且不得位于
  证据目录内（不覆盖证据输入），输出文件名**不得是 release-approval.json**
  （一律拒绝——绝不创建或修改审批文件）；原子落盘（同目录临时文件 +
  fsync + os.replace，symlink 目标拒绝）。
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ops.evidence_kit import (
    check_evidence_dir,
    check_regular_file,
    find_embedded_credential,
    find_sensitive_key,
    load_json_object,
    scrub_sensitive,
    sha256_file,
)
from app.ops.release_readiness import (
    GATE_IDS,
    GATES,
    REQUIRED_GATE_IDS,
    run_release_readiness,
)

#: 证据自声明（底稿 tool 字段）
TOOL_ID = "release-approval-draft"

#: 可绑定门 = 除 release-approval 外的全部门（含 optional turn-tls——审批
#: gate_evidence 允许绑定它，公网语音发布时必须绑定；缺席不构成必需覆盖
#: 缺口，只如实列入 gate_evidence_missing）
BINDABLE_GATE_IDS = GATE_IDS - {"release-approval"}

APPROVAL_DRAFT_NOTICE = (
    "DRAFT（草稿底稿）：本文件只是哈希底稿与人工确认清单，不是审批记录——"
    "直接改名为 release-approval.json 只会得到 malformed。审批人必须逐项"
    "人工确认每门证据内容，从本底稿的 gate_evidence 哈希出发**从零组装** "
    "release-approval.json（填写 manual_fields_required 列出的全部字段并"
    "补 gate: release-approval 自声明），不得在本文件上补字段改名——本"
    "文件的全部其余字段（draft/tool/generated_at/evidence_dir/notice/"
    "approval_file_present/gate_evidence_missing/required_coverage_gaps/"
    "gate_statuses/readiness/load_problems/manual_fields_required/"
    "confirmation_required）都是底稿保留元数据，release-readiness 对携带"
    "任一上述字段的审批记录一律判 malformed；本工具不签署、不放行、不"
    "执行任何生产操作。"
)

#: 审批人必须人工填写的字段（底稿只算哈希，不代填、不代签、不代批）
MANUAL_APPROVAL_FIELDS: dict[str, str] = {
    "schema_version": "整数：固定 1",
    "approved_at": "REPLACE-ME：审批时间（ISO 8601，人工填写）",
    "note": "REPLACE-ME：审批说明（人工填写；readiness 只验证非空，不回显）",
    "window.start": "REPLACE-ME：发布窗口开始（ISO 8601，须早于 end）",
    "window.end": "REPLACE-ME：发布窗口结束（ISO 8601）",
    "rollback_plan": "REPLACE-ME：回滚计划（人工填写）",
    "observation": "REPLACE-ME：观察期安排（人工填写）",
    "approved_by": "REPLACE-ME：审批人记录（只做记录，不做身份认证）",
}

_CONFIRMATION_REQUIRED = (
    "审批人必须逐项人工确认每门证据内容并核对哈希后，从零组装 "
    "release-approval.json（本底稿不是审批记录；审批记录须含 "
    "gate: release-approval 自声明 + manual_fields_required 全部字段 + "
    "gate_evidence 哈希绑定，且不得保留本底稿的任何元数据字段——保留即 "
    "malformed）；审批是人工决策动作，本工具不代签、不代批、不放行，"
    "任何真实生产操作仍须人工逐项授权执行"
)


def build_release_approval_draft(evidence_dir: str | Path) -> dict[str, Any]:
    """只读计算当前证据目录的审批哈希底稿（DRAFT；不写任何文件）。

    哈希范围 = 审批应绑定的其余 10 门主证据（release-approval 自身不绑定
    自己，已存在时只在 approval_file_present 如实标注且字节不动）。门状态
    只消费 run_release_readiness 的既有评估结果（零语义重复）。装载层
    问题（非法 JSON/敏感键/内嵌凭据）如实摘要列出（只报字段路径，值不
    回显），不阻断底稿生成——最终判定仍以 release-readiness 为准。
    证据目录护栏问题抛 :class:`evidence_kit.EvidenceInputError` / OSError
    （CLI 映射 exit 2）。
    """
    root = check_evidence_dir(Path(evidence_dir))
    readiness = run_release_readiness(root)
    status_by_gate = {gate["gate"]: gate["status"] for gate in readiness["gates"]}

    approval_present = False
    gate_evidence: dict[str, str] = {}
    missing: list[str] = []
    problems: dict[str, str] = {}
    for spec in GATES:
        path = root / spec.evidence_file
        if spec.gate_id == "release-approval":
            approval_present = os.path.lexists(path)
            continue  # 审批只绑定其余 10 门，不绑定自身
        if not os.path.lexists(path):
            missing.append(spec.gate_id)
            continue
        check_regular_file(path)
        gate_evidence[spec.gate_id] = sha256_file(path)
        obj, problem = load_json_object(path)
        if problem is None:
            hit = find_sensitive_key(obj)
            if hit is not None:
                problem = (
                    "证据含敏感键 " + hit + "（readiness 将判 malformed；值不回显）"
                )
        if problem is None:
            embedded = find_embedded_credential(obj)
            if embedded is not None:
                problem = (
                    "证据字段 " + embedded + " 内嵌凭据（readiness 将判 "
                    "malformed；值不回显）"
                )
        if problem is not None:
            problems[spec.evidence_file] = problem

    # 诚实子集：只透出 summary 计数/未过门清单/optional 边界声明——绝不
    # 携带 release_ready/production_ready（底稿不是门裁决，永不作放行结论）
    readiness_subset = {
        "summary": dict(readiness["summary"]),
        "not_pass_required": list(readiness["not_pass_required"]),
        "not_pass_optional": list(readiness["not_pass_optional"]),
        "optional_scope_note": readiness["optional_scope_note"],
    }
    draft: dict[str, Any] = {
        "draft": True,
        "tool": TOOL_ID,
        "generated_at": datetime.now(UTC).isoformat(),
        "evidence_dir": str(root),
        "notice": APPROVAL_DRAFT_NOTICE,
        "approval_file_present": approval_present,
        "gate_evidence": gate_evidence,
        "gate_evidence_missing": sorted(missing),
        "required_coverage_gaps": sorted(
            REQUIRED_GATE_IDS - {"release-approval"} - set(gate_evidence)
        ),
        "gate_statuses": [
            {
                "gate": spec.gate_id,
                "required": spec.required,
                "status": status_by_gate[spec.gate_id],
            }
            for spec in GATES
        ],
        "readiness": readiness_subset,
        "load_problems": problems,
        "manual_fields_required": dict(MANUAL_APPROVAL_FIELDS),
        "confirmation_required": _CONFIRMATION_REQUIRED,
    }
    return scrub_sensitive(draft)
