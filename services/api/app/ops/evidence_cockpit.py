"""M14-91 release-evidence-cockpit：跨切片发布证据驾驶舱（只读聚合器 + 一次性 staging）。

痛点（本工具固化的风险面）：M14 系列各证据切片的 canonical 门证据分散在
各 worktree 的 gitignored ``.verify/artifacts/<slice>/`` 目录，发布前的门
拼装长期依赖人工复制与一次性脚本（M14-83 §3.3 转录脚本、M14-86 §2.1
derive_ci_main、M14-87 §3.5 组装器）——手工拼装 gate JSON 正是各切片反复
声明「绝不」的操作。本工具把该流程固化为确定性、可测试的仓库内工具：

1. **显式输入**：调用方逐门声明 canonical 证据文件（``gate=path``），
   绝不扫描/猜测证据位置；
2. **来源登记零改动**：每份 source 记录路径/字节/SHA-256（复用共享层
   check_regular_file/sha256_file/load_json_object），运行前后源文件
   字节不变（symlink 拒绝、JSON 必须是合法对象且自声明 gate 与声明的
   gate_id 一致——错位即 fail-closed）；
3. **字节一致 staging**：按 release-readiness 的 gate→文件名映射，把
   通过校验的门文件**逐字节**复制进一次性 staging 目录（目录必须不
   存在，工具创建；原子写 + 写后重读断言；绝不覆盖既有证据）；可选
   ``audit-anchor.jsonl`` 伴生副本同规则 stage；
4. **复用既有 evaluator**：对 staging 目录原样运行
   ``run_release_readiness``——门语义/malformed/tampered/unrecognized
   检测全部由既有工具承载，本模块零重复实现；
5. **code-bound vs production-state 显式分类 + stale 判定**：
   ``ci-main``/``release-check`` 是代码绑定门（证据只对其执行时点的树
   成立）；其余为生产状态门（绑定生产状态执行时点）。stale 判定只用
   声明事实 + 调用方给出的 current HEAD：声明来源 = 文件内嵌
   （ci-main 的 ``merge_commit``）或显式旗标（``--gate-declared-head``，
   声明该证据执行/绑定的树）；两处同时声明且不一致 → fail-closed。
   声明 == current HEAD → ``current``；≠ → ``stale``；无声明 →
   ``undeclared``（如实，不猜）。口径：**stale 一律计入 blocker**；
   code-bound 门 ``undeclared`` 计入 blocker（无法判 stale 的代码绑定
   证据按保守处理）；production-state 门 ``undeclared`` 只如实呈现不计
   blocker（M14-83/85/87 canonical 均无内嵌 commit 声明载体，其效力
   绑定生产时点而非代码树——是否随 main 前移失效由 supervisor 判断，
   本工具不代答）。未 stage 的门 ``not-staged``（evaluator 侧如实
   missing）；
6. **审批与生产就绪的硬边界**：本工具**永不接受、stage 或生成
   release-approval**（传入即 fail-closed）——审批是 human-only 动作，
   驾驶舱不是审批人；``production_ready`` 恒 false（本工具是编排/
   汇总器，不授权任何生产操作，与 M14-68 closure-manifest 同款诚实
   合取纪律）；evaluator 的 ``release_ready`` 因 approval 永远 missing
   而恒 false，原样透传（``release_ready_from_evaluator``）。

安全边界：零网络、零 DB、零环境变量、零生产操作（无 --yes 形态）；
对 source 与 canonical 目录零写入；报告零敏感值回显（只含路径事实/
字节/哈希/白名单标量）。staging 目录创建后任何失败（写入/evaluator/
报告构造）都会尽力完整移除本次新建目录（含 ``.tmp`` 残留）；清理本身
失败时不虚称零产出（错误如实携带残留路径）。
退出码：``cockpit_ready=true``=0（**全部必需门（release-approval 除外）
staged 且 pass 且无 stale/undeclared-code-bound blocker**——不是
staged 子集干净；optional turn-tls 未 stage 不阻断）；有 blocker=1
（报告照常产出，如实记录缺口，含 required 未 stage / 非 pass / stale /
code-bound undeclared）；输入/路径/护栏问题=2（fail-closed，零 staging
产出）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ops.evidence_kit import (
    EvidenceInputError,
    check_regular_file,
)
from app.ops.release_readiness import (
    GATES,
    REQUIRED_GATE_IDS,
    run_release_readiness,
)

#: 证据自声明（报告 tool 字段）
TOOL_ID = "evidence-cockpit"

#: 驾驶舱永不触碰的门：release-approval 是 human-only 审批，本工具不是
#: 审批人——不接受其作为输入（fail-closed），也不生成/签署任何审批文件
FORBIDDEN_GATES = frozenset({"release-approval"})

#: 代码绑定门：证据只对其执行时点的树成立（M14-81/86/90 口径）
CODE_BOUND_GATES = frozenset({"ci-main", "release-check"})

#: ci-main 证据内嵌的绑定 commit 字段（release-readiness 白名单契约）
EMBEDDED_COMMIT_FIELDS: dict[str, str] = {"ci-main": "merge_commit"}

#: audit-chain-anchor 门的伴生锚文件副本（与 evaluator 契约同名）
ANCHOR_COMPANION_FILE = "audit-anchor.jsonl"

_HEX40_RE = re.compile(r"\A[0-9a-f]{40}\Z")

#: gate_id -> 证据文件名（与 evaluator 的 KNOWN_EVIDENCE_FILES 同源）
GATE_FILE_OF: dict[str, str] = {spec.gate_id: spec.evidence_file for spec in GATES}


class CockpitInputError(Exception):
    """输入/护栏问题（CLI exit 2：不 stage、不写报告）。"""


def _require_hex40_commit(value: str, label: str) -> str:
    if _HEX40_RE.fullmatch(value) is None:
        raise CockpitInputError(f"{label} 必须是 40 位小写十六进制 commit：{value!r}")
    return value


def _reject_symlink_ancestors(path: Path) -> None:
    """已存在的祖先组件含 symlink 即拒绝（fail-closed 防路径漂移）。"""
    for ancestor in path.parents:
        if ancestor.exists() and ancestor.is_symlink():
            raise CockpitInputError(f"路径祖先含 symlink：{ancestor}")


def _parse_gate_mapping(raw: str, label: str) -> tuple[str, str]:
    """解析 ``GATE=PATH`` / ``GATE=SHA`` 形态（首个 = 分隔，余下为值）。"""
    if "=" not in raw:
        raise CockpitInputError(f"{label} 需要 GATE=值 形态：{raw!r}")
    gate, value = raw.split("=", 1)
    if not gate or not value:
        raise CockpitInputError(f"{label} 的 GATE 或值为空：{raw!r}")
    return gate, value


def _collect_gate_sources(
    gate_source_args: list[str],
) -> dict[str, Path]:
    """解析并校验 --gate-source 重复参数（重复 gate 拒绝；approval 拒绝）。"""
    sources: dict[str, Path] = {}
    for raw in gate_source_args:
        gate, value = _parse_gate_mapping(raw, "--gate-source")
        if gate not in GATE_FILE_OF:
            raise CockpitInputError(f"未知发布门：{gate!r}")
        if gate in FORBIDDEN_GATES:
            raise CockpitInputError(
                f"驾驶舱永不接受 {gate}（审批是 human-only 动作，本工具不代签）")
        if gate in sources:
            raise CockpitInputError(f"门 {gate} 的 source 重复声明")
        sources[gate] = Path(value)
    if not sources:
        raise CockpitInputError("至少需要一个 --gate-source GATE=PATH")
    return sources


def _collect_declared_heads(
    declared_head_args: list[str], sources: Mapping[str, Path]
) -> dict[str, str]:
    """解析 --gate-declared-head（gate 必须 已有 source；值为 40-hex）。"""
    declared: dict[str, str] = {}
    for raw in declared_head_args:
        gate, value = _parse_gate_mapping(raw, "--gate-declared-head")
        if gate not in GATE_FILE_OF:
            raise CockpitInputError(f"未知发布门：{gate!r}")
        if gate in FORBIDDEN_GATES:
            raise CockpitInputError(f"驾驶舱永不接受 {gate}")
        if gate not in sources:
            raise CockpitInputError(
                f"{gate} 声明了 --gate-declared-head 但没有对应 --gate-source")
        if gate in declared:
            raise CockpitInputError(f"门 {gate} 的 declared head 重复声明")
        declared[gate] = _require_hex40_commit(value, f"{gate} declared head")
    return declared


def _parse_json_object(data: bytes) -> tuple[dict[str, Any] | None, str | None]:
    """解析 JSON 对象（输入 bytes；与 evidence_kit.load_json_object 同语义，
    但消费调用方已读取的字节——解析与后续 staging/哈希共用同一次读）。"""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, "不是有效 UTF-8"
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"不是合法 JSON: {exc.msg}（第 {exc.lineno} 行）"
    if not isinstance(obj, dict):
        return None, "顶层不是 JSON 对象"
    return obj, None


def _load_source(gate: str, path: Path) -> tuple[dict[str, Any], bytes]:
    """读取并完整校验单份 source，返回 (document, data)。

    **单次读盘纪律（TOCTOU 防护）**：文件字节恰好读取一次；JSON 解析、
    gate 自声明校验、来源登记（字节/SHA-256）与后续 staging 全部消费同一
    bytes 对象——解析通过的版本与落盘的版本不可能来自不同文件状态。
    """
    try:
        check_regular_file(path)
    except EvidenceInputError as cause:
        raise CockpitInputError(f"门 {gate} 的 source 路径无效：{cause}") from cause
    data = path.read_bytes()  # 唯一一次读
    document, problem = _parse_json_object(data)
    if document is None or problem:
        raise CockpitInputError(f"门 {gate} 的 source 不是合法 JSON 对象：{problem}")
    if document.get("gate") != gate:
        raise CockpitInputError(
            f"门 {gate} 的 source 自声明 gate={document.get('gate')!r} 与"
            f"声明的 gate_id 不一致（错位拒绝）")
    return document, data


def _declared_head_for(
    gate: str,
    document: Mapping[str, Any],
    flagged: Mapping[str, str],
) -> tuple[str | None, str | None]:
    """合成声明绑定 head：(head, origin)。embedded 与 flag 同时且不一致
    即 fail-closed（自相矛盾的声明拒绝）。"""
    embedded_field = EMBEDDED_COMMIT_FIELDS.get(gate)
    embedded = None
    if embedded_field is not None:
        embedded = document.get(embedded_field)
        if embedded is not None:
            embedded = _require_hex40_commit(str(embedded), f"{gate}.{embedded_field}")
    flagged_head = flagged.get(gate)
    if embedded is not None and flagged_head is not None and embedded != flagged_head:
        raise CockpitInputError(
            f"门 {gate} 内嵌声明 {embedded} 与旗标声明 {flagged_head} 不一致")
    if embedded is not None:
        return embedded, "embedded"
    if flagged_head is not None:
        return flagged_head, "flag"
    return None, None


def _stage_bytes_atomic(staging: Path, name: str, data: bytes) -> None:
    """原子写 staged 文件（tmp + os.replace）+ 写后重读字节断言。"""
    target = staging / name
    tmp = target.with_name(name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)
    if target.read_bytes() != data:
        raise CockpitInputError(f"staged 文件写后重读不一致：{name}")


def _remove_created_staging(staging: Path, cause: BaseException) -> None:
    """staging 创建后失败：尽力完整移除本次新建目录（含 .tmp 残留）。

    清理本身失败时**绝不声称零 staging 产出**——改抛携带残留路径与
    原异常类型的事实性错误（目录可能残留，调用方需人工核查）。
    """
    try:
        shutil.rmtree(staging)
    except OSError as cleanup:
        raise CockpitInputError(
            f"staging 创建后失败（{type(cause).__name__}）且清理失败"
            f"（{type(cleanup).__name__}），目录残留待人工核查：{staging}"
        ) from cause


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_evidence_cockpit(
    gate_sources: Mapping[str, str | Path],
    *,
    current_head: str,
    staging_dir: str | Path,
    declared_heads: Mapping[str, str] | None = None,
    anchor_companion: str | Path | None = None,
) -> dict[str, Any]:
    """构建 evidence cockpit 报告（含一次性 staging + evaluator 复跑）。

    返回报告 dict（含 ``exit_code``）；任何输入/护栏问题抛
    :class:`CockpitInputError`（调用方 exit 2，零 staging 产出——staging
    目录仅在全部 source 校验通过后才创建）。
    """
    _require_hex40_commit(current_head, "--current-head")
    sources = {gate: Path(path) for gate, path in gate_sources.items()}
    for gate in sources:
        if gate not in GATE_FILE_OF:
            raise CockpitInputError(f"未知发布门：{gate!r}")
        if gate in FORBIDDEN_GATES:
            raise CockpitInputError(
                f"驾驶舱永不接受 {gate}（审批是 human-only 动作，本工具不代签）")
    if not sources:
        raise CockpitInputError("至少需要一个 gate source")
    flagged = dict(declared_heads or {})
    for gate, head in flagged.items():
        if gate not in sources:
            raise CockpitInputError(
                f"{gate} 声明了 declared head 但没有对应 source")
        _require_hex40_commit(head, f"{gate} declared head")

    # 1) 来源装载与登记（零改动：只读一次 + 哈希缓存，staging 复用同字节）
    loaded: dict[str, dict[str, Any]] = {}
    source_bytes: dict[str, bytes] = {}
    bindings: dict[str, dict[str, Any]] = {}
    for gate in sorted(GATE_FILE_OF):
        record: dict[str, Any] = {
            "binding_class": ("code-bound" if gate in CODE_BOUND_GATES
                              else "production-state"),
            "staged": False,
            "source": None,
            "declared_head": None,
            "declared_head_origin": None,
            "stale_status": "not-staged",
        }
        if gate in sources:
            document, data = _load_source(gate, sources[gate])
            head, origin = _declared_head_for(gate, document, flagged)
            source_bytes[gate] = data
            loaded[gate] = document
            record.update({
                "staged": True,
                "source": {
                    "path": str(sources[gate]),
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                },
                "declared_head": head,
                "declared_head_origin": origin,
                "stale_status": ("undeclared" if head is None
                                 else ("current" if head == current_head
                                       else "stale")),
            })
        bindings[gate] = record

    companion_data: bytes | None = None
    companion_record: dict[str, Any] | None = None
    if anchor_companion is not None:
        companion_path = Path(anchor_companion)
        try:
            check_regular_file(companion_path)
        except EvidenceInputError as cause:
            raise CockpitInputError(
                f"anchor 伴生文件路径无效：{cause}") from cause
        companion_data = companion_path.read_bytes()
        companion_record = {
            "path": str(companion_path),
            "bytes": len(companion_data),
            "sha256": hashlib.sha256(companion_data).hexdigest(),
        }

    # 2) 一次性 staging（全部 source 校验通过后才创建目录）
    staging = Path(staging_dir)
    if staging.exists():
        raise CockpitInputError(
            f"staging 目录已存在（一次性使用，绝不覆盖既有内容）：{staging}")
    _reject_symlink_ancestors(staging)
    staging.mkdir(parents=True)
    staged_files: list[dict[str, Any]] = []
    try:
        for gate in sorted(loaded):
            name = GATE_FILE_OF[gate]
            _stage_bytes_atomic(staging, name, source_bytes[gate])
            staged_files.append({
                "gate": gate, "name": name,
                "bytes": bindings[gate]["source"]["bytes"],
                "sha256": bindings[gate]["source"]["sha256"],
                "byte_identical": True,
            })
        if companion_data is not None:
            _stage_bytes_atomic(staging, ANCHOR_COMPANION_FILE, companion_data)
            staged_files.append({
                "gate": "audit-chain-anchor",
                "name": ANCHOR_COMPANION_FILE,
                "bytes": companion_record["bytes"],
                "sha256": companion_record["sha256"],
                "byte_identical": True,
            })

        # 3) 复用既有 evaluator（staging 目录即其 evidence-dir）
        try:
            readiness = run_release_readiness(staging)
        except EvidenceInputError as cause:
            raise CockpitInputError(f"staging 证据目录校验失败：{cause}") from cause
    except (CockpitInputError, OSError) as cause:
        # staging 创建后失败：移除本次新建目录（含 .tmp 残留）；清理本身
        # 失败时 _remove_created_staging 改抛如实残留错误（绝不虚称零产出）。
        # OSError 统一折叠为 CockpitInputError（保留异常链，API 面单一类型）。
        _remove_created_staging(staging, cause)
        if isinstance(cause, CockpitInputError):
            raise
        raise CockpitInputError(
            f"staging IO 失败：{type(cause).__name__}: {cause}") from cause

    # 4) blocker 合成——cockpit_ready 不是「staged 子集干净」：必需门未
    #    stage 即阻断。唯一例外 release-approval（本工具按策略永不接受，
    #    非 blocker，如实呈现在 approval 块）；turn-tls 是 optional 门
    #    （本机/LAN 发布范围不阻断；公网语音发布需另行要求 turn-tls=pass）。
    blockers: list[str] = []
    required_not_staged: list[str] = []
    gate_status = {entry["gate"]: entry["status"] for entry in readiness["gates"]}
    for gate in sorted(bindings):
        record = bindings[gate]
        if not record["staged"]:
            if gate in REQUIRED_GATE_IDS and gate != "release-approval":
                blockers.append(f"{gate}:not-staged-required")
                required_not_staged.append(gate)
            continue
        status = gate_status.get(gate)
        if status != "pass":
            blockers.append(f"{gate}:{status}")
        elif record["stale_status"] == "stale":
            blockers.append(f"{gate}:stale")
        elif record["stale_status"] == "undeclared" \
                and gate in CODE_BOUND_GATES:
            blockers.append(f"{gate}:undeclared-code-bound")

    cockpit_ready = not blockers
    return {
        "schema_version": 1,
        "milestone": "M14-91",
        "tool": TOOL_ID,
        "generated_at": _utc_now_iso(),
        "current_head": current_head,
        "gate_bindings": bindings,
        "anchor_companion": companion_record,
        "staging": {"dir": str(staging), "files": staged_files},
        "readiness": readiness,
        "approval": {
            "accepted": False,
            "policy": ("evidence-cockpit 永不接受、stage 或生成 release-approval"
                       "（审批 human-only；本工具不是审批人；其 not-staged "
                       "按策略呈现，不计入 blocker）"),
        },
        "required_not_staged": required_not_staged,
        "production_ready": False,
        "cockpit_blockers": blockers,
        "cockpit_ready": cockpit_ready,
        "exit_code": 0 if cockpit_ready else 1,
    }


def format_cockpit_summary(report: Mapping[str, Any]) -> str:
    """人类可读摘要（stdout 用；零敏感值——只含白名单标量）。"""
    lines = [
        f"evidence-cockpit @ current_head {report['current_head']}",
        (
            f"cockpit_ready: {report['cockpit_ready']} "
            f"(production_ready 恒 false；审批不接受)"
        ),
    ]
    for gate, record in report["gate_bindings"].items():
        if not record["staged"]:
            if gate == "release-approval":
                note = "（按策略永不接受，非 blocker）"
            elif gate == "turn-tls":
                note = "（optional：本机/LAN 范围不阻断）"
            elif gate in report["required_not_staged"]:
                note = "（required 未 stage → blocker）"
            else:
                note = ""
            lines.append(f"  {gate}: not-staged {note}".rstrip())
            continue
        lines.append(
            f"  {gate}: {record['binding_class']} / "
            f"{record['stale_status']}"
            + (f" (declared {record['declared_head'][:12]}… "
               f"via {record['declared_head_origin']})"
               if record["declared_head"] else " (无声明)"))
    if report["cockpit_blockers"]:
        lines.append(f"blockers: {', '.join(report['cockpit_blockers'])}")
    readiness = report["readiness"]
    lines.append(
        "evaluator: pass={pass} missing={missing} blocked={blocked} "
        "release_ready={release_ready}（approval 永不接受，故恒 false）".format(
            **readiness["summary"], release_ready=readiness["release_ready"]))
    return "\n".join(lines)


def write_cockpit_report(report: Mapping[str, Any], output: str | Path) -> None:
    """报告 JSON 原子落盘（tmp + os.replace；目标已存在即拒绝覆盖）。"""
    target = Path(output)
    if target.exists():
        raise CockpitInputError(f"输出文件已存在（拒绝覆盖）：{target}")
    _reject_symlink_ancestors(target)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(
        (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    os.replace(tmp, target)
