"""M11-18 production-evidence-gap：生产证据缺口 manifest（只读聚合器）。

定位：cutover-rehearsal（M10-15）回答「13 步演练时间线证据齐不齐」，本工具
回答「离实际生产切换还差哪几块证据」——把台账「下一任务」定义的四类生产
前置证据缺口（历史治理批次、审计链建链与锚定、真实 provider 冒烟、切换
审批）从 rehearsal 的只读评估结果中**聚合**为逐类缺口清单：每类给出状态、
缺口描述、已覆盖步骤、既有工具、缺失证据、运维动作、agent 可安全执行的
动作与必须运维显式授权的边界。四类之外的 5 步（CI、release-check、
preflight×2、备份恢复）不在本清单范围，完整时间线仍以 cutover-rehearsal
manifest 为准——四类全 pass 不代表 13 步全 pass，更不代表生产就绪。

安全边界（docs/DEVELOPMENT.md「生产证据缺口清单」节同步维护）：

- **只消费既有评估结果**：本工具直接调用 ``run_cutover_rehearsal`` 取得其
  manifest（其内部的路径护栏、JSON 装载、敏感键扫描、schema 校验、scrub
  全部原样复用），自身不重新解析任何证据文件、不重复实现证据 schema
  校验——step 状态与 reason/next_action 均来自 rehearsal 的白名单提取；
- 只读本地证据：不连接数据库、不调用 API、不访问网络、不读取任何环境
  变量（生产密钥物理上进不了本工具，``os.environ`` 零引用）；对证据目录
  零写入（字节保持不变）；
- 不执行任何生产操作：不迁移、不治理、不锚定、不备份、不部署、不启停
  服务、不发布、不回滚、不运行任何 provider 冒烟——命令没有 ``--yes``
  执行形态，是纯汇总器；
- ``production_ready`` 恒为 ``false``：本输出是缺口清单，不构成生产放行、
  不构成 production readiness，也不授权任何生产操作——真实 key、生产连接
  与执行批准必须由运维显式提供与授予，agent 不得虚拟生产就绪；
- 输出零敏感、零生产业务 ID：每类只透传 rehearsal 的白名单标量与文本
  （step/status/reason/next_action）加本模块的静态指引文本；来自 rehearsal
  的 reason/next_action 逐字段再过 ``scrub_sensitive`` 纵深防御（静态指引
  文本是代码内字面量、零敏感，不经运行时 scrub——且通用 scrub 会按敏感
  **键名**模式误抹 ``authorization_required`` 这类白名单字段）；证据目录内
  的敏感键证据已由 rehearsal 按 blocked（malformed）语义处理，值从不回显；
- 路径护栏 fail-closed（exit 2）：``--evidence-dir`` 护栏复用 rehearsal
  （symlink/非常规目录拒绝）；``--output`` 必须位于 gitignore 的
  artifacts/temp（复用 ``is_safe_artifact_path``）、任何已存在路径组件是
  symlink 即拒绝、**不得位于证据目录内或等于证据目录**（拒绝覆盖证据
  输入，normcase 归一比较，Windows 大小写/``..`` 折叠不构成绕过）——输出
  护栏先于任何证据内容读取执行；落盘由 CLI 共享原子写完成（同目录临时
  文件 + fsync + os.replace，失败旧文件字节原样、无 partial）。

状态聚合（诚实优先，与 rehearsal 四态同源常量）：类别内全部步骤 pass 才
pass；有 blocked 优先 blocked（结构不可信/结论为否/哈希失配必须先停下）；
否则 pending（待人工决策或执行）；否则 not_executed（证据未提供或动作未
发生）。绝不把部分通过伪装成 pass。

退出码：四类全 pass=0；任一类非 pass=1；目录/路径或 IO 问题=2（与
rehearsal CLI 同口径）。
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ops.cutover_rehearsal import (
    STATUS_BLOCKED,
    STATUS_NOT_EXECUTED,
    STATUS_ORDER,
    STATUS_PASS,
    STATUS_PENDING,
    run_cutover_rehearsal,
)
from app.ops.evidence_kit import scrub_sensitive
from app.ops.legacy_papers import is_safe_artifact_path

#: 证据自声明：状态与 reason/next_action 全部复用 cutover-rehearsal 评估结果
TOOL_ID = "production-evidence-gap"
SOURCE_TOOL = "cutover-rehearsal"

_ISOLATION_NOTE = (
    "隔离只读梳理：本工具只消费 cutover-rehearsal 的本地只读评估结果并按"
    "类别聚合缺口，不代表生产验收，也不授权任何生产写入/发布"
)
_NO_EXECUTION_NOTE = (
    "只读本地证据：不连接数据库、不调用 API、不访问网络、不读取任何环境"
    "变量；不执行任何迁移、治理、锚定、备份、部署、启停、发布或回滚，不"
    "运行任何 provider 冒烟——命令没有 --yes 执行形态，是纯汇总器"
)
_SCOPE_NOTE = (
    "本清单聚焦四类生产前置证据缺口，覆盖 cutover-rehearsal 演练时间线的"
    "部分步骤（见 source.steps_covered）；完整时间线（CI、release-check、"
    "preflight、备份恢复）仍以 cutover-rehearsal manifest 为准——四类全 "
    "pass 不代表演练时间线全 pass"
)
_PRODUCTION_READY_NOTE = (
    "production_ready 恒为 false：本输出是证据缺口清单，不构成生产放行、"
    "不构成 production readiness，也不授权任何生产操作；真实 key、生产连接"
    "与执行批准必须由运维显式提供与授予"
)
_SOURCE_NOTE = (
    "状态与 reason/next_action 全部复用 cutover-rehearsal 只读评估结果；"
    "本工具不重新解析证据文件、不重复实现证据 schema 校验"
)


class ProductionGapInputError(Exception):
    """输入/护栏问题（CLI exit 2：不聚合、不写输出）。"""


@dataclass(frozen=True)
class CategorySpec:
    """一类生产前置证据缺口的静态登记（步骤映射与动作指引，manifest 透出）。"""

    category: str
    title: str
    basis: str  # 为什么是生产前置缺口（manifest 原样透出，可审计）
    steps: tuple[str, ...]  # 对应 cutover-rehearsal 的 step id
    existing_tools: tuple[str, ...]  # 既有工具/命令/runbook（证据从哪来）
    agent_safe_actions: tuple[str, ...]  # agent 可安全执行的只读/本地动作
    authorization_required: str  # 必须运维显式授权的边界


#: 四类生产前置证据缺口（输出顺序即此顺序；steps 必须是 rehearsal step id，
#: 测试守卫与 cutover_rehearsal.STEP_IDS 交叉锁定，防漂移）。
CATEGORY_SPECS: tuple[CategorySpec, ...] = (
    CategorySpec(
        "governance",
        "历史治理批次执行与计数归零",
        "历史无归属非 seed 卷与 generation/variant NULL 归属草稿必须在切换"
        "窗口前人工决策并分批执行完毕（pending_count 归零），证据由"
        " governance-evidence 从报告与成功批次确定性推导",
        ("legacy-papers", "draft-ownership"),
        (
            (
                "legacy-paper-report / draft-owner-report（只读报告，含生产 ID，"
                "仅落 gitignore 的 artifacts/temp）"
            ),
            (
                "legacy-paper-migrate / draft-owner-migrate --yes --output"
                "（M11-13 批次报告原生落盘）"
            ),
            (
                "governance-evidence --report <报告> --batch <批次> --output "
                "legacy-papers.json|draft-ownership.json（M11-12 推导导出）"
            ),
        ),
        (
            "只读盘点既有治理报告与批次文件现状（本清单即是）",
            (
                "从运维已产出的成功批次文件用 governance-evidence 推导导出证据"
                "（纯本地文件推导，不连 DB）"
            ),
        ),
        "生产 DB 连接、逐批精确 ID 的人工决策与 --yes 治理执行必须由运维"
        "显式授权；agent 不代行任何迁移，生产 paper/draft ID 不进入本清单",
    ),
    CategorySpec(
        "audit-chain",
        "审计哈希链生产建链、校验与库外锚定",
        "0027_audit_chain 生产迁移后主库必须建链（verify valid）、锚定"
        " up-to-date 且锚文件归档 WORM/离线介质，head_hash 库外存证防御"
        "整链重算",
        ("audit-chain-verify", "audit-chain-anchor"),
        (
            "audit-chain-verify（只读校验，valid=0 / invalid=1）",
            "audit-chain-anchor --yes / --verify-only（追加锚点 / 交叉核对）",
            (
                "DEVELOPMENT.md「审计」节 0027_audit_chain 生产迁移 runbook"
                "（备份 -> quiesce 审计写入方 -> upgrade -> verify -> 锚定）"
            ),
        ),
        (
            "对运维提供的锚文件副本做纯本地完整性复核（不连 DB）",
            (
                "把 0027 生产迁移 runbook 物化为待执行清单（文档工作，不触碰"
                "生产）"
            ),
        ),
        "0027 生产迁移、锚定 --yes 追加、WORM/离线介质归档与外部 head_hash"
        " 存证均须运维按 runbook 显式执行；agent 不连接生产 DB",
    ),
    CategorySpec(
        "provider-smoke",
        "真实 provider 冒烟证据（search / 语音 local|hybrid|cloud 拓扑 / llm）",
        "三类 provider 需以真实端点与部署 key 冒烟通过（语音按拓扑选轨："
        "local=本地语音链路探针，无需云 key；hybrid/cloud=部署 key + 真实短"
        "语音），结论由 provider-smoke-export 从真实退出码机器导出（不接受"
        "人工抄录拼装），并聚合为 release-readiness 的 provider-smoke 门证据。"
        "本类别步骤沿用演练时间线的云语音步（cloud-voice-smoke 是"
        " cutover-rehearsal 步骤，local-voice-smoke 只是 M14-70 聚合证据轨道"
        "、不是新演练步）：release 门按聚合证据 topology.voice_mode 选轨"
        "——local 拓扑聚合消费 local-voice 单步证据，hybrid/cloud 拓扑聚合"
        "消费 cloud-voice 单步证据（hybrid 的本地轨道由单步导出独立承载）",
        ("search-smoke", "cloud-voice-smoke", "llm-smoke"),
        (
            (
                "provider-smoke-export search|cloud-voice|local-voice|llm"
                " --output <步证据文件>（M11-16；local-voice 是 M14-70 本地"
                "拓扑语音轨道的单步导出）"
            ),
            (
                "provider-smoke-aggregate --search <search-smoke.json> --voice"
                " <语音单步证据> --llm <llm-smoke.json> --voice-mode"
                " local|hybrid|cloud --output provider-smoke.json（M11-16/M14-70；"
                "--voice-mode local 必须以 --voice 传 local-voice-smoke.json，"
                "--cloud-voice 是仅承载 cloud/hybrid 拓扑的兼容形态）"
            ),
            (
                "infra/smoke_search.sh / smoke_voice_cloud.sh /"
                " smoke_voice_local.sh / smoke_llm.sh（真实端点/本地链路冒烟脚本）"
            ),
        ),
        (
            (
                "从运维已导出的单步证据按语音拓扑聚合 provider-smoke.json"
                "（纯本地文件聚合，不运行冒烟）"
            ),
        ),
        "真实 provider key 与端点由运维显式注入并亲自执行冒烟；agent 不"
        "触碰任何 key、不替跑冒烟、不连接任何 provider 端点",
    ),
    CategorySpec(
        "cutover-approval",
        "切换审批与发布窗口（人工哈希绑定审批）",
        "切换窗口/回滚预案/观察期须由审批人从证据哈希底稿从零组装"
        " cutover-approval.json，精确绑定全部步骤证据与 supporting 文件的"
        " sha256（DRAFT 底稿不可直接审批）",
        ("cutover-approval",),
        (
            (
                "cutover-evidence-pack approval-draft --evidence-dir <dir>"
                "（只读生成审批哈希 DRAFT 底稿）"
            ),
            "cutover-evidence-pack scaffold（13 步证据模板与操作手册）",
        ),
        (
            (
                "只读生成审批哈希 DRAFT 底稿供审批人核对（DRAFT 不可直接审批，"
                "补齐字段改名只会 blocked）"
            ),
        ),
        "审批签署、回滚预案确认与发布窗口决策由审批人人工完成；agent 只"
        "提供只读底稿，不代签、不代批、不虚拟审批",
    ),
)

CATEGORY_ORDER = tuple(spec.category for spec in CATEGORY_SPECS)

#: 类别状态聚合优先级：blocked > pending > not_executed（全 pass 才 pass）
_CATEGORY_PRECEDENCE = (STATUS_BLOCKED, STATUS_PENDING, STATUS_NOT_EXECUTED)

_STATUS_GAP_PREFIX = {
    STATUS_BLOCKED: "存在必须先停下排查的证据问题（结构不可信/结论为否/哈希失配）",
    STATUS_PENDING: "待人工决策或执行（证据在但未达放行形态）",
    STATUS_NOT_EXECUTED: "证据未提供或动作尚未发生",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


# --- 输出护栏（先于任何证据内容读取） ------------------------------------------


def _reject_symlink_components(path: Path) -> None:
    """路径任何已存在组件（含自身）是 symlink 即拒绝（fail-closed）。

    先于 is_safe_artifact_path 执行：后者内部 resolve() 会跟随 symlink，
    链接目标落在护栏内时会被误放行（与 governance-evidence 同口径）。
    """
    chain: list[Path] = []
    current = path
    while current.name:
        chain.append(current)
        current = current.parent
    for item in reversed(chain):
        if item.is_symlink():
            raise ProductionGapInputError(
                f"输出路径组件是符号链接，拒绝使用: {item}"
            )


def _check_output_path(output_path: str | Path, evidence_root: Path) -> None:
    """输出护栏：artifacts/temp 内、非 symlink、不得覆盖证据输入。

    输出 resolved 路径不得等于证据目录或位于证据目录内（``resolve`` +
    ``os.path.normcase`` 归一比较——Windows 大小写/``..`` 折叠等等价书写
    形态不构成绕过）；冲突时证据文件字节保持不变。只做校验，落盘由 CLI
    原子写完成。
    """
    target = Path(output_path)
    _reject_symlink_components(target)
    if not is_safe_artifact_path(target):
        raise ProductionGapInputError(
            f"缺口清单只能写入 gitignore 的 artifacts/ 或 temp/ 目录: {target}"
        )
    if target.exists() and not target.is_file():
        raise ProductionGapInputError(f"输出路径已存在且不是常规文件: {target}")
    output_key = os.path.normcase(str(target.resolve()))
    root_key = os.path.normcase(str(evidence_root.resolve()))
    if output_key == root_key or output_key.startswith(root_key + os.sep):
        raise ProductionGapInputError(
            f"输出路径不得位于证据目录内或等于证据目录（拒绝覆盖证据输入）: "
            f"{output_path}"
        )


# --- 聚合 -----------------------------------------------------------------------


def _aggregate_status(statuses: list[str]) -> str:
    """诚实聚合：全 pass 才 pass；blocked > pending > not_executed。"""
    for status in _CATEGORY_PRECEDENCE:
        if status in statuses:
            return status
    return STATUS_PASS


def _gap_text(status: str, total: int, not_pass: list[dict[str, Any]]) -> str:
    if status == STATUS_PASS:
        return (
            f"{total}/{total} 步全部 pass——本类别当前无证据缺口"
            "（仍不构成生产放行）"
        )
    listing = "、".join(
        f"{item['step']}={item['status']}" for item in not_pass
    )
    return f"{_STATUS_GAP_PREFIX[status]}: {listing}"


def build_production_evidence_gap(
    evidence_dir: str | Path,
    output_path: str | Path | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
) -> tuple[dict[str, Any], int]:
    """只读聚合四类生产证据缺口（不写任何文件；落盘由 CLI 原子完成）。

    返回 (report, exit_code)：四类全 pass -> 0；任一类非 pass -> 1（清单
    照常返回，如实记录缺口，``production_ready`` 恒为 false）。证据目录
    护栏问题抛 :class:`evidence_kit.EvidenceInputError` / OSError，输出
    护栏问题抛 :class:`ProductionGapInputError`（CLI 均 exit 2）。
    """
    root = Path(evidence_dir)
    # 输出护栏先于任何证据内容读取（覆盖证据输入的形态在读取前拒绝）。
    if output_path is not None:
        _check_output_path(output_path, root)
    # 唯一数据源：rehearsal 的只读评估结果（其内部完成目录护栏、装载、
    # 敏感键扫描、schema 校验与 scrub——本工具不重复实现任何一环）。
    rehearsal = run_cutover_rehearsal(root)
    steps_by_id = {record["step"]: record for record in rehearsal["steps"]}

    categories: list[dict[str, Any]] = []
    for spec in CATEGORY_SPECS:
        covered = [
            {
                "step": record["step"],
                "status": record["status"],
                # 透传自 rehearsal 的动态文本逐字段 scrub（纵深防御）；
                # 静态指引文本（basis/tools/actions/authorization）是代码内
                # 字面量、零敏感，不经运行时 scrub——通用 scrub 会按敏感键名
                # 模式把 authorization_required 这类白名单字段误抹成占位符。
                "reason": scrub_sensitive(record["reason"]),
                "next_action": scrub_sensitive(record["next_action"]),
            }
            for record in (steps_by_id[step_id] for step_id in spec.steps)
        ]
        statuses = [item["status"] for item in covered]
        status = _aggregate_status(statuses)
        not_pass = [item for item in covered if item["status"] != STATUS_PASS]
        categories.append(
            {
                "category": spec.category,
                "title": spec.title,
                "basis": spec.basis,
                "status": status,
                "gap": _gap_text(status, len(covered), not_pass),
                "covered_steps": covered,
                "existing_tools": list(spec.existing_tools),
                "missing_evidence": [
                    {"step": item["step"], "status": item["status"]}
                    for item in not_pass
                ],
                "operator_actions": [
                    item["next_action"] for item in not_pass if item["next_action"]
                ],
                "agent_safe_actions": list(spec.agent_safe_actions),
                "authorization_required": spec.authorization_required,
            }
        )

    summary = {
        status: sum(1 for item in categories if item["status"] == status)
        for status in STATUS_ORDER
    }
    overall_status = _aggregate_status([item["status"] for item in categories])
    report: dict[str, Any] = {
        "generated_at": (clock or _utc_now)().isoformat(),
        "tool": TOOL_ID,
        "evidence_dir": rehearsal["evidence_dir"],
        "read_only": True,
        "isolation_note": _ISOLATION_NOTE,
        "no_execution_note": _NO_EXECUTION_NOTE,
        "scope_note": _SCOPE_NOTE,
        "source": {
            "tool": SOURCE_TOOL,
            "steps_total": len(rehearsal["steps"]),
            "steps_covered": sum(len(spec.steps) for spec in CATEGORY_SPECS),
            "rehearsal_overall_status": rehearsal["overall_status"],
            "note": _SOURCE_NOTE,
        },
        "categories": categories,
        "summary": summary,
        "overall_status": overall_status,
        "production_ready": False,
        "production_ready_note": _PRODUCTION_READY_NOTE,
        "exit_code": 0 if overall_status == STATUS_PASS else 1,
    }
    return report, report["exit_code"]


def format_gap_summary(report: Mapping[str, Any]) -> str:
    """人类可读摘要（无凭据/无生产 ID/无证据正文），按类别分组。"""
    lines = [
        "生产证据缺口清单（production-evidence-gap，只读聚合）",
        f"生成时间: {report['generated_at']}",
        f"证据目录: {report['evidence_dir']}",
        (
            f"来源: {report['source']['tool']} 只读评估"
            f"（覆盖 {report['source']['steps_covered']}/"
            f"{report['source']['steps_total']} 步）"
        ),
        _ISOLATION_NOTE,
        "-" * 72,
    ]
    for item in report["categories"]:
        lines.append(f"—— {item['category']}｜{item['title']} ——")
        lines.append(f"[{item['status'].upper():<13}] {item['gap']}")
        if item["missing_evidence"]:
            names = "、".join(
                f"{miss['step']}({miss['status']})"
                for miss in item["missing_evidence"]
            )
            lines.append(f"{'':<16} 缺失/未达标: {names}")
        if item["operator_actions"]:
            lines.append("  运维动作（须人工执行，节选）:")
            for action in item["operator_actions"]:
                lines.append(f"    - {action}")
        lines.append(f"  agent 可安全执行: {len(item['agent_safe_actions'])} 项（纯只读/本地）")
        lines.append(f"  授权边界: {item['authorization_required']}")
        lines.append("")
    counts = "  ".join(f"{k}={v}" for k, v in report["summary"].items())
    lines.append("-" * 72)
    lines.append(
        f"RESULT: overall={report['overall_status']}  {counts}  "
        "production_ready=false（固定）"
    )
    lines.append(_PRODUCTION_READY_NOTE)
    lines.append(
        "退出码: 四类全 pass=0 / 任一类非 pass=1 / 目录或路径与 IO 问题=2。"
    )
    return "\n".join(lines)
