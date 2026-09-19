"""M11-12 governance-evidence：治理报告 + 成功批次 -> rehearsal 治理步证据。

定位：cutover-rehearsal（M10-15）的 ``legacy-papers`` / ``draft-ownership``
步与 release-readiness（M10-11）的同名门需要 ``pending_count`` + ``batches``
证据；此前只能人工从报告抄录计数拼装（M10-16 手册措辞「计数归零后导出」
——人工转抄没有任何交叉校验，抄错即证据失真）。本工具把「一份完整治理
报告 + 一或多个成功 migrate 批次」确定性推导为同契约脱敏证据文件：
pending_count 由报告明细 ID 集合减成功批次 eligible ID 并集**重算**得出，
不是对报告计数的转抄——批次覆盖缺口、报告计数与明细不一致都会如实
暴露。与 backup-restore-evidence（M11-04）同一动机：此前人工拼装的证据
改为机器可复现导出。

安全护栏（全部先于任何输出写入执行；违例 exit 2、不写输出文件）：

- 报告/每个批次/输出路径都必须位于 gitignore 的 artifacts/temp（复用
  ``is_safe_artifact_path``）且为常规文件；任何已存在路径组件是 symlink
  即拒绝（不猜测链接目标，与 cutover-evidence-pack 的 scaffold 护栏
  同口径）；
- **输出不得覆盖任何输入**：输出 resolved 路径不得等于报告或任何批次的
  resolved 路径（``resolve`` + ``os.path.normcase`` 归一比较——Windows
  大小写与路径分隔符形态差异不构成绕过）；违例先于任何内容读取与写入
  拒绝（exit 2），输入字节保持不变；
- **同一批次文件不得重复传入**（含等价路径规范化后的重复）：重复批次会
  虚增成功批次数、伪造覆盖面，一律 exit 2；
- 报告必须是 ``legacy-paper-report`` / ``draft-owner-report`` 的完整 JSON
  形态：明细列表（``papers`` / ``drafts``）逐项带可判别 ID（paper_id /
  kind+draft_id），``summary.total`` 与明细条数自洽，ID 无重复——结构
  不符即拒绝，绝不凭部分明细推导计数；
- 每个批次必须是**成功执行**的 migrate plan JSON（``executed=true`` 且
  ``dry_run`` **显式为 false** 且 ``exit_code=0`` 且无 ``failure`` 且无
  invalid ID 且 eligible ⊆ 请求 ID 且 ``requested`` 与 ID 列表长度一致，
  且 ``audit_action`` 与迁移路径/草稿 kind **精确匹配**——keep-public =>
  ``ops.legacy_paper.keep_public``、assign-owner =>
  ``ops.legacy_paper.assign_owner``、export-delete =>
  ``ops.legacy_paper.export_delete``、draft assign-owner =>
  ``ops.draft_owner.assign_owner``、draft keep-unowned =>
  ``ops.draft_owner.keep_unowned``）：dry-run（含 ``dry_run=true`` 即便
  ``executed=true`` 的畸形形态）、审计动作错配（跨动作/跨 kind 拼改）、
  未知 ID 拒绝、目标用户不存在、导出校验失败等任何非成功形态一律
  fail-closed 拒绝——从不完整的执行历史推导 pending_count 会低估剩余
  待处理数；批次的迁移路径与草稿 kind 必须是合法枚举；
- 批次类型必须与报告类型匹配（legacy 批次配 ``papers`` 报告、draft 批次
  配 ``drafts`` 报告）；输出文件名必须恰为 ``legacy-papers.json`` /
  ``draft-ownership.json`` 且与报告类型对应（manifest 工具只认精确证据
  文件名，防笔误产出不可消费文件）。

确定性推导与时序说明：``pending_count = 报告明细 ID 集合 - 全部成功批次
eligible ID 并集``。批次处理过的 ID 不必仍出现在报告中——assign-owner /
export-delete 执行后行已移出治理范围，重跑报告自然不含；keep-public /
keep-unowned 不改行、重跑报告仍在。「迁移前报告 + 全部批次」与「迁移后
报告 + 历史批次」两类时序形态由同一公式覆盖，不需要操作者声明时序。

输出零业务 ID / 零敏感值且**契约最小化**：证据只含白名单标量（step /
pending_count / ``batches[].executed``（每项仅此一键）与聚合计数
``batches_executed`` / 报告聚合计数 / 源报告 sha256 / 时间戳）——不输出
任何逐批迁移路径、逐批解决计数、逐批文件哈希或其它批次细节；报告与批次
正文一律不透传——生产 paper/draft ID 明细留在 artifacts 报告文件里，
不进入证据、不进入终端输出（人类摘要同样只打印聚合计数）。

隔离声明：本工具是纯本地文件推导器——不连接数据库、不读取环境变量、
不访问网络、不执行任何迁移/治理/锚定/部署，没有 ``--yes`` 执行形态；
对报告与批次文件零写入（字节保持不变）。

退出码：``pending_count=0`` => 0；``pending_count>0`` => 1（证据照常
原子落盘，如实记录治理未归零，不伪装 pass——与 backup-restore-evidence
的 verified=false 同口径）；输入/路径/结构/失败批次/写入失败 => 2。
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ops.legacy_papers import is_safe_artifact_path

#: 证据自声明：cutover-rehearsal 消费 step（同 backup-restore-evidence 模式）
TOOL_ID = "governance-evidence"

#: 报告类型 -> （证据 step、rehearsal 精确证据文件名）：输出文件名必须与
#: 报告类型一一对应（manifest 工具只认这两个精确文件名）。
LEGACY_STEP = "legacy-papers"
DRAFT_STEP = "draft-ownership"
STEP_OUTPUT_FILES: dict[str, str] = {
    LEGACY_STEP: "legacy-papers.json",
    DRAFT_STEP: "draft-ownership.json",
}

#: 两种 migrate plan 的合法迁移路径（与 legacy_papers/draft_ownership 的
#: MIGRATION_PATHS 保持同步；不直接 import 避免拉入 DB 依赖面）。
LEGACY_BATCH_PATHS = ("keep-public", "assign-owner", "export-delete")
DRAFT_BATCH_PATHS = ("assign-owner", "keep-unowned")
DRAFT_BATCH_KINDS = ("course-generation", "variant-question")

#: 迁移路径 -> 成功执行 plan 必须携带的精确审计动作（与两个 migrate 工具
#: 写审计/回填 plan 的 action 字符串一一对应）：错配即批次被拼改或错配
#: （跨动作/跨 kind），fail-closed 拒绝推导。
LEGACY_AUDIT_ACTIONS: dict[str, str] = {
    "keep-public": "ops.legacy_paper.keep_public",
    "assign-owner": "ops.legacy_paper.assign_owner",
    "export-delete": "ops.legacy_paper.export_delete",
}
DRAFT_AUDIT_ACTIONS: dict[str, str] = {
    "assign-owner": "ops.draft_owner.assign_owner",
    "keep-unowned": "ops.draft_owner.keep_unowned",
}

_NO_EXECUTION_NOTE = (
    "纯本地文件推导：不连数据库、不读环境变量、不访问网络、不执行任何"
    "迁移/治理/锚定/部署（无 --yes 执行形态）；报告与批次文件零写入"
)


class GovernanceInputError(Exception):
    """输入/护栏问题（CLI exit 2：不推导、不写输出）。"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


# --- 路径护栏 -------------------------------------------------------------------


def _reject_symlink_components(path: Path, label: str) -> None:
    """路径任何已存在组件（含自身）是 symlink 即拒绝（fail-closed）。

    先于 is_safe_artifact_path 执行：后者内部 resolve() 会跟随 symlink，
    链接目标落在护栏内时会被误放行。
    """
    chain: list[Path] = []
    current = path
    while current.name:
        chain.append(current)
        current = current.parent
    for item in reversed(chain):
        if item.is_symlink():
            raise GovernanceInputError(
                f"{label}路径组件是符号链接，拒绝使用: {item}"
            )


def _check_input_file(path: str | Path, label: str) -> Path:
    """报告/批次输入护栏：artifacts/temp 内、非 symlink、已存在的常规文件。"""
    target = Path(path)
    _reject_symlink_components(target, label)
    if not is_safe_artifact_path(target):
        raise GovernanceInputError(
            f"{label}必须位于 gitignore 的 artifacts/ 或 temp/ 目录"
            f"（含生产 ID 与治理明细）: {target}"
        )
    if not target.exists():
        raise GovernanceInputError(f"{label}不存在: {target}")
    if not target.is_file():
        raise GovernanceInputError(f"{label}不是常规文件: {target}")
    return target


def _check_output_path(path: str | Path, step_id: str) -> Path:
    """输出护栏：artifacts/temp 内、非 symlink、文件名恰为该步证据文件名。"""
    target = Path(path)
    _reject_symlink_components(target, "输出")
    if not is_safe_artifact_path(target):
        raise GovernanceInputError(
            f"输出必须位于 gitignore 的 artifacts/ 或 temp/ 目录: {target}"
        )
    expected = STEP_OUTPUT_FILES[step_id]
    if target.name != expected:
        raise GovernanceInputError(
            f"{step_id} 步的输出文件名必须是 {expected}（manifest 工具只认"
            f"精确证据文件名，防笔误产出不可消费文件）: {target.name}"
        )
    return target


def _path_key(path: Path) -> str:
    """输入/输出冲突比较键：``resolve`` 后 ``normcase`` 归一（Windows 上
    大小写与路径分隔符形态差异归为同一键——``LEGACY-PAPERS.JSON`` 与
    ``legacy-papers.json``、``./b.json`` 与 ``b.json`` 都判同一文件，
    无法借书写形态差异绕过冲突检查）。symlink 已在前置护栏拒绝，
    ``resolve`` 不会跟随到意外目标。"""
    return os.path.normcase(str(path.resolve()))


def _reject_output_overwriting_input(
    output: Path, report: Path, batches: Sequence[Path]
) -> None:
    """输出不得覆盖任何输入（先于读取内容与写入；违例 exit 2、字节不动）。"""
    output_key = _path_key(output)
    if output_key == _path_key(report):
        raise GovernanceInputError(
            f"输出路径与报告是同一文件（拒绝覆盖输入）: {output}"
        )
    for index, batch in enumerate(batches, start=1):
        if output_key == _path_key(batch):
            raise GovernanceInputError(
                f"输出路径与批次[{index}]是同一文件（拒绝覆盖输入）: {output}"
            )


def _reject_duplicate_batches(batches: Sequence[Path]) -> None:
    """同一批次文件（含等价路径规范化后的重复）不得重复传入——重复会虚增
    成功批次数、伪造覆盖面。"""
    seen: dict[str, int] = {}
    for index, batch in enumerate(batches, start=1):
        key = _path_key(batch)
        if key in seen:
            raise GovernanceInputError(
                f"批次[{index}]与批次[{seen[key]}]是同一文件（重复批次输入，"
                "拒绝——重复传入会虚增成功批次数）"
            )
        seen[key] = index


# --- 装载与结构校验 --------------------------------------------------------------


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise GovernanceInputError(f"{label}不是有效 UTF-8: {path.name}") from None
    except OSError as cause:
        raise GovernanceInputError(f"{label}无法读取: {cause}") from None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as cause:
        raise GovernanceInputError(
            f"{label}不是合法 JSON: {cause.msg}（第 {cause.lineno} 行）"
        ) from None
    if not isinstance(obj, dict):
        raise GovernanceInputError(f"{label}顶层不是 JSON 对象: {path.name}")
    return obj


def _require_id_list(raw: Any, *, label: str) -> list[str]:
    """ID 列表校验：非空字符串元素（形态与 resolve_paper_ids 产物一致）。"""
    if not isinstance(raw, list):
        raise GovernanceInputError(f"{label}必须是列表")
    ids: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise GovernanceInputError(f"{label}元素必须是非空字符串: {item!r}")
        ids.append(item)
    return ids


def _load_legacy_report(obj: dict[str, Any]) -> set[str]:
    """legacy-paper-report 完整形态 -> 待处理 paper ID 集合（结构 fail-closed）。"""
    entries = obj.get("papers")
    if not isinstance(entries, list):
        raise GovernanceInputError("报告缺 papers 明细列表（legacy-paper-report 完整 JSON 形态）")
    ids: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            raise GovernanceInputError("papers 元素必须是对象")
        paper_id = item.get("paper_id")
        if not isinstance(paper_id, str) or not paper_id.strip():
            raise GovernanceInputError("papers 元素缺 paper_id（非空字符串）")
        ids.add(paper_id)
    if len(ids) != len(entries):
        raise GovernanceInputError("papers 明细存在重复 paper_id（结构不可信）")
    summary = obj.get("summary")
    if not isinstance(summary, dict):
        raise GovernanceInputError("报告缺 summary 对象")
    total = summary.get("total")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise GovernanceInputError("summary.total 必须是非负整数")
    if total != len(entries):
        raise GovernanceInputError(
            f"summary.total={total} 与 papers 明细条数 {len(entries)} 不一致（自相矛盾）"
        )
    return ids


def _load_draft_report(obj: dict[str, Any]) -> set[tuple[str, str]]:
    """draft-owner-report 完整形态 -> 待处理 (kind, draft_id) 集合。"""
    entries = obj.get("drafts")
    if not isinstance(entries, list):
        raise GovernanceInputError("报告缺 drafts 明细列表（draft-owner-report 完整 JSON 形态）")
    ids: set[tuple[str, str]] = set()
    for item in entries:
        if not isinstance(item, dict):
            raise GovernanceInputError("drafts 元素必须是对象")
        kind = item.get("kind")
        if kind not in DRAFT_BATCH_KINDS:
            raise GovernanceInputError(f"drafts 元素 kind 非法: {kind!r}")
        draft_id = item.get("draft_id")
        if not isinstance(draft_id, str) or not draft_id.strip():
            raise GovernanceInputError("drafts 元素缺 draft_id（非空字符串）")
        ids.add((kind, draft_id))
    if len(ids) != len(entries):
        raise GovernanceInputError("drafts 明细存在重复 (kind, draft_id)（结构不可信）")
    summary = obj.get("summary")
    if not isinstance(summary, dict):
        raise GovernanceInputError("报告缺 summary 对象")
    total = summary.get("total")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise GovernanceInputError("summary.total 必须是非负整数")
    if total != len(entries):
        raise GovernanceInputError(
            f"summary.total={total} 与 drafts 明细条数 {len(entries)} 不一致（自相矛盾）"
        )
    return ids


def _require_success(batch: dict[str, Any], label: str) -> None:
    """成功批次判定（fail-closed）：executed=true 且 dry_run **显式为 false**
    且 exit_code=0 且无失败语义字段——``dry_run=true`` 即便 ``executed=true``
    也拒绝（自称成功执行的 dry-run 是拼改畸形形态，不是可信执行历史）。"""
    dry_run = batch.get("dry_run")
    if not isinstance(dry_run, bool) or dry_run:
        raise GovernanceInputError(
            f"{label}未显式声明 dry_run=false（dry-run 或缺声明/非布尔的"
            "畸形批次，绝不从不完整执行历史推导 pending_count）"
        )
    executed = batch.get("executed")
    if not isinstance(executed, bool) or not executed:
        raise GovernanceInputError(
            f"{label}不是成功执行的批次（executed 非真——dry-run 或未执行，"
            "绝不从不完整执行历史推导 pending_count）"
        )
    exit_code = batch.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code != 0:
        raise GovernanceInputError(
            f"{label}exit_code 非 0（未知 ID 拒绝/目标用户不存在/导出校验失败等"
            "失败形态，fail-closed 拒绝推导）"
        )
    if batch.get("failure") is not None:
        raise GovernanceInputError(f"{label}携带 failure 字段（失败批次，拒绝推导）")


def _require_audit_action(
    batch: dict[str, Any], label: str, expected: str
) -> None:
    """audit_action 必须与迁移路径/草稿 kind 精确匹配（错配即拼改/错配）。"""
    actual = batch.get("audit_action")
    if actual != expected:
        raise GovernanceInputError(
            f"{label}audit_action 与迁移路径/kind 不匹配（应为 {expected}，"
            f"实为 {actual!r}——批次被拼改或错配，拒绝推导）"
        )


def _load_legacy_batch(obj: dict[str, Any], label: str) -> set[str]:
    """legacy-paper-migrate 成功 plan -> 已治理 paper ID 集合（eligible）。"""
    if "kind" in obj or "draft_ids" in obj:
        raise GovernanceInputError(f"{label}不是 legacy-paper-migrate 批次形态")
    path = obj.get("path")
    if path not in LEGACY_BATCH_PATHS:
        raise GovernanceInputError(f"{label}迁移路径非法: {path!r}")
    _require_success(obj, label)
    _require_audit_action(obj, label, LEGACY_AUDIT_ACTIONS[path])
    requested_ids = _require_id_list(obj.get("paper_ids"), label=f"{label} paper_ids")
    eligible = _require_id_list(
        obj.get("eligible_paper_ids"), label=f"{label} eligible_paper_ids"
    )
    invalid = _require_id_list(
        obj.get("invalid_paper_ids"), label=f"{label} invalid_paper_ids"
    )
    requested = obj.get("requested")
    if isinstance(requested, bool) or not isinstance(requested, int):
        raise GovernanceInputError(f"{label} requested 必须是整数")
    if requested != len(requested_ids):
        raise GovernanceInputError(
            f"{label} requested={requested} 与 paper_ids 条数 {len(requested_ids)} 不一致"
        )
    if invalid:
        raise GovernanceInputError(f"{label}含 invalid_paper_ids（成功批次不应有未知 ID）")
    if not set(eligible) <= set(requested_ids):
        raise GovernanceInputError(f"{label} eligible_paper_ids 超出 paper_ids 集合（自相矛盾）")
    return set(eligible)


def _load_draft_batch(obj: dict[str, Any], label: str) -> set[tuple[str, str]]:
    """draft-owner-migrate 成功 plan -> 已治理 (kind, draft_id) 集合。"""
    if "paper_ids" in obj or "eligible_paper_ids" in obj:
        raise GovernanceInputError(f"{label}不是 draft-owner-migrate 批次形态")
    kind = obj.get("kind")
    if kind not in DRAFT_BATCH_KINDS:
        raise GovernanceInputError(f"{label}缺合法 kind（course-generation/variant-question）")
    path = obj.get("path")
    if path not in DRAFT_BATCH_PATHS:
        raise GovernanceInputError(f"{label}迁移路径非法: {path!r}")
    _require_success(obj, label)
    _require_audit_action(obj, label, DRAFT_AUDIT_ACTIONS[path])
    requested_ids = _require_id_list(obj.get("draft_ids"), label=f"{label} draft_ids")
    eligible = _require_id_list(
        obj.get("eligible_draft_ids"), label=f"{label} eligible_draft_ids"
    )
    invalid = _require_id_list(
        obj.get("invalid_draft_ids"), label=f"{label} invalid_draft_ids"
    )
    requested = obj.get("requested")
    if isinstance(requested, bool) or not isinstance(requested, int):
        raise GovernanceInputError(f"{label} requested 必须是整数")
    if requested != len(requested_ids):
        raise GovernanceInputError(
            f"{label} requested={requested} 与 draft_ids 条数 {len(requested_ids)} 不一致"
        )
    if invalid:
        raise GovernanceInputError(f"{label}含 invalid_draft_ids（成功批次不应有未知 ID）")
    if not set(eligible) <= set(requested_ids):
        raise GovernanceInputError(f"{label} eligible_draft_ids 超出 draft_ids 集合（自相矛盾）")
    return {(kind, draft_id) for draft_id in eligible}


# --- 主流程 ---------------------------------------------------------------------


def build_governance_evidence(
    report_path: str | Path,
    batch_paths: Sequence[str | Path],
    output_path: str | Path,
    *,
    clock: Callable[[], datetime] | None = None,
) -> tuple[dict[str, Any], int]:
    """只读推导治理证据（不写任何文件；落盘由 CLI 原子完成）。

    返回 (evidence, exit_code)：``pending_count=0`` -> 0；``>0`` -> 1
    （证据照常返回，由 CLI 原子落盘——如实记录，不伪装 pass）。
    任何护栏/结构/失败批次违例抛 :class:`GovernanceInputError`（CLI exit 2）。
    批次是条件必需：报告仍有待决策项时至少一个成功批次；报告已归零
    （明细为空）时允许零批次——治理完成后重跑的报告本就没有历史批次文件。
    """
    report = _check_input_file(report_path, "报告")
    batches = [
        _check_input_file(batch_path, f"批次[{index}]")
        for index, batch_path in enumerate(batch_paths, start=1)
    ]
    # 冲突护栏先于任何内容读取与写入：输出不得覆盖任何输入；同一批次文件
    # 不得重复传入（虚增成功批次数即伪造覆盖面）。
    _reject_output_overwriting_input(Path(output_path), report, batches)
    _reject_duplicate_batches(batches)
    report_obj = _load_json_object(report, "报告")
    # 报告类型判别：papers / drafts 明细键互斥（legacy-paper-report vs
    # draft-owner-report 的完整 JSON 形态）。
    has_papers = "papers" in report_obj
    has_drafts = "drafts" in report_obj
    if has_papers == has_drafts:
        raise GovernanceInputError(
            "报告无法判别类型：恰需 papers（legacy-paper-report）或 drafts"
            "（draft-owner-report）其一的完整 JSON 形态"
        )
    if has_papers:
        step_id = LEGACY_STEP
        pending_ids = _load_legacy_report(report_obj)
        load_batch = _load_legacy_batch
    else:
        step_id = DRAFT_STEP
        pending_ids = _load_draft_report(report_obj)
        load_batch = _load_draft_batch
    # 批次条件必需：报告仍有待决策项却没有任何成功批次 => 无法推导归零，
    # fail-closed 拒绝；报告明细为空时零批次是合法自然形态（M14-67）。
    if pending_ids and not batches:
        raise GovernanceInputError(
            "报告仍有待决策项，至少需要一个成功 migrate 批次（--batch 可重复）"
        )
    # 输出护栏只做校验（路径/文件名/symlink）；落盘由 CLI 原子完成
    _check_output_path(output_path, step_id)

    resolved_ids: set[Any] = set()
    for index, batch in enumerate(batches, start=1):
        label = f"批次[{index}]"
        batch_obj = _load_json_object(batch, label)
        resolved_ids |= load_batch(batch_obj, label)

    remaining = pending_ids - resolved_ids
    pending_count = len(remaining)
    # 输出契约最小化：batches 只保留评估器必需的 executed 布尔（每项仅此
    # 一键），成功批次数量以聚合计数 batches_executed 表达——任何逐批
    # 路径、逐批解决计数、逐批文件哈希或其它批次细节都不进入证据。
    evidence: dict[str, Any] = {
        "tool": TOOL_ID,
        "step": step_id,
        "gate": step_id,
        "pending_count": pending_count,
        "batches": [{"executed": True} for _ in batches],
        "batches_executed": len(batches),
        "generated_at": (clock or _utc_now)().isoformat(),
        "report": {
            "total": len(pending_ids),
            "resolved": len(pending_ids & resolved_ids),
            "sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
        },
        "read_only": True,
        "no_execution_note": _NO_EXECUTION_NOTE,
    }
    return evidence, 0 if pending_count == 0 else 1


def format_governance_summary(evidence: Mapping[str, Any]) -> str:
    """人类可读摘要（无业务 ID/无敏感值；只有聚合计数与报告哈希前缀——
    不打印任何逐批迁移路径、逐批解决数或逐批哈希）。"""
    pending = evidence["pending_count"]
    lines = [
        "治理证据推导（governance-evidence，纯本地文件推导）",
        f"生成时间: {evidence['generated_at']}",
        (
            f"step: {evidence['step']}  pending_count={pending}  "
            f"成功批次: {evidence['batches_executed']}"
        ),
        (
            f"报告: total={evidence['report']['total']}  "
            f"resolved={evidence['report']['resolved']}  "
            f"sha256={evidence['report']['sha256'][:12]}…"
        ),
    ]
    if pending == 0:
        lines.append(
            "RESULT: 治理计数已归零——报告明细全部被成功批次覆盖（或报告已为空），"
            "可作 cutover-rehearsal / release-readiness 治理步 pass 证据。"
        )
    else:
        lines.append(
            f"RESULT: 仍有 {pending} 条待人工决策/执行——证据如实记录 pending；"
            "逐批人工决策/执行（不输出生产 ID）后重跑本命令至归零。"
        )
    lines.append(_NO_EXECUTION_NOTE)
    lines.append(
        "退出码: pending_count=0 => 0 / pending_count>0 => 1 / 输入或路径或写入问题=2。"
    )
    return "\n".join(lines)
