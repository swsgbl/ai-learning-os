"""M10-15 cutover rehearsal：生产切换演练编排器（只读 manifest）。

定位：与 release-readiness（M10-11，发布审批门矩阵）分工——本工具按
**生产切换时间线**组织 13 个 required steps（pre-window / pre-migration /
post-migration / cutover 四阶段），演练「一次完整切换需要什么证据」的编排
视角：迁移前该冒烟的冒烟、该归零的治理计数归零；窗口内 pre 预检 + 备份
恢复演练；迁移后 post 预检 + 审计链校验 + 锚定；最后人工审批放行。
不是 release-readiness 的改名复制——同一份底层证据（production-preflight
拆 pre/post 两步、audit-chain 拆 verify/anchor 两步、provider-smoke 拆
search/cloud-voice/llm 三步）在时间线上各有独立通过条件与失败语义，任何
一步的问题不再被同文件其他 provider 的结果掩盖。

安全边界（docs/DEVELOPMENT.md「生产切换演练」节同步维护；硬边界与
release-readiness 同口径）：

- **隔离 rehearsal**：本工具是切换演练的只读编排器，不代表生产验收，
  也不授权任何生产写入/发布——`rehearsal_ready=true` 只说明演练时间线上
  13 步证据齐备且审批绑定完整，生产放行仍须按 runbook 人工执行；
- 只读本地证据：不连接数据库、不调用 API、不访问网络、不读取任何环境
  变量（生产密钥物理上进不了本工具，`os.environ` 零引用）；
- 不执行任何生产操作：不迁移、不锚定、不清理、不 WORM 归档、不部署、
  不启停服务、不发布、不回滚——命令没有 --yes 执行形态，是纯汇总器；
- 输出零敏感、零生产业务 ID：每步只提取白名单标量（计数/枚举/哈希/
  step id/时间戳），不回显证据正文（审批 note 只验证非空）；最终
  manifest 整体经 evidence_kit.scrub_sensitive 纵深防御——不含完整 DB
  URL、密码、token、key、生产 paper/draft/用户 ID；
- 证据文件含敏感键（password/passwd/secret/token/api_key/access_key/
  private_key/authorization/cookie/credential 等变体）或内嵌
  `://user:pass@` 凭据段即 blocked（结构不可信，值从不回显）；
- 路径护栏 fail-closed（exit 2）：--evidence-dir 必须存在且为真目录
  （symlink/普通文件/目录内任何 symlink 拒绝），证据文件必须是常规文件；
  --output 复用 legacy 报告的 artifacts/temp gitignore 护栏并经 CLI 原子
  落盘（同目录临时文件 + fsync + os.replace，symlink 目标拒绝）。

状态语义（四态，诚实优先；与 release-readiness 六态的映射）：
missing（无证据文件）/ 冒烟 not run / 缺审批或审批覆盖缺口 =>
not_executed；malformed（结构不符、自声明 step 错位、自相矛盾计数、
敏感键）/ tampered（审批哈希失配、锚文件副本校验失败）/ fail（证据结论
为否：CI failure、release-check 未全绿、preflight fail、链 invalid、
冒烟 fail）=> blocked；待人工决策或执行（治理计数 >0、锚定落后、WORM
未归档、恢复演练未 verified、post 预检有 pending）=> pending；pass。
绝不把 pending/not_executed 包装成 pass，不凭文件存在自动通过。

分步要点（preflight / audit-chain / provider smoke 的拆分语义）：

- preflight-pre-migration：通过条件=无 fail（迁移前 pending/
  not_configured 属预期，不阻断 pre 步；放行仍需 post 证据）；fail>0
  即 blocked——切换开始前已暴露的问题必须先停下；
- preflight-post-migration：放行形态=无 fail 且无 pending/
  not_configured 且 pass>0；fail>0 => blocked；余 pending；
- audit-chain-verify：链 valid => pass；invalid => blocked（先于锚定步：
  链已断时锚定无意义，runbook 先修复链）；
- audit-chain-anchor：锚定落后/WORM 未归档 => pending；invalid/副本
  校验失败 => blocked；
- search / cloud-voice / llm smoke：三个独立 step 各自证据与失败语义
  （voice 需运维部署 key 冒烟、LLM 需部署 key、search 打真实端点且
  SEARCH_CLOUD_API_KEY 可选——M10-12/M10-13 口径），任一失败不再被
  同文件其他 provider 的 pass 掩盖。

人工审批（cutover-approval.json）必须绑定 step id、主 step 证据 sha256
集合、supporting 文件 sha256 mapping（当前 supporting 文件仅
audit-chain-anchor 步的锚文件副本 audit-anchor.jsonl——审批必须精确覆盖
当前实际存在的 supporting 文件集合）、时间与说明；本工具只做**结构一致性
与哈希绑定**校验，不声称身份认证（approved_by 只是记录字符串）。缺审批、
审批未覆盖其余 12 步或未覆盖当前 supporting 文件 =>
cutover-approval=not_executed（审批动作尚未发生，不是失败也不是待办
确认）；审批哈希（主 step 或 supporting）与当前证据不匹配、或引用当前
不存在的 supporting 文件 => blocked（证据在审批后被改动/移除——单靠
主 step JSON 哈希无法发现锚副本被替换成另一份自洽副本，supporting 绑定
补上这个缺口）。

顶层判定优先级：blocked > pending > not_executed > ready——任一步
blocked 整场演练 blocked（先停下排查），否则任一 pending => pending，
否则任一 not_executed => not_executed，仅当 13 步全部 pass（审批绑定
完整蕴含其中）才 rehearsal_ready=true。

退出码：13 步全 pass=0；任一步非 pass=1；--evidence-dir/--output 路径
或 IO 问题=2（与 readiness/preflight CLI 同口径）。
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ops.audit_chain_anchor import load_anchor_file
from app.ops.evidence_kit import (
    HEX64_RE,
    MalformedEvidence,
    batches_executed,
    check_evidence_dir,
    check_regular_file,
    find_embedded_credential,
    find_sensitive_key,
    load_json_object,
    req,
    req_bool,
    req_choice,
    req_commit,
    req_dict,
    req_int,
    req_iso,
    req_str,
    scrub_sensitive,
    sha256_file,
)
from app.ops.release_readiness import CI_CONCLUSIONS

#: 四态（演练时间线只区分「可继续 / 待人工 / 必须停下 / 未发生」）
STATUS_PASS = "pass"
STATUS_PENDING = "pending"
STATUS_BLOCKED = "blocked"
STATUS_NOT_EXECUTED = "not_executed"
#: 汇总固定顺序（人类摘要与 summary dict 同序）
STATUS_ORDER = (STATUS_PASS, STATUS_PENDING, STATUS_BLOCKED, STATUS_NOT_EXECUTED)
#: 顶层判定优先级：blocked > pending > not_executed（ready 是四者皆空的兜底）
OVERALL_PRECEDENCE = (STATUS_BLOCKED, STATUS_PENDING, STATUS_NOT_EXECUTED)

#: 审计步的可选锚文件副本（真实 anchor JSONL；提供时做独立完整性校验）
ANCHOR_COMPANION_FILE = "audit-anchor.jsonl"

#: 审批 supporting_evidence 可绑定的 supporting 文件名集合（当前仅锚文件
#: 副本；未来新增 supporting 文件时在此登记，审批绑定覆盖语义自动跟进）
BINDABLE_SUPPORTING_FILES = frozenset({ANCHOR_COMPANION_FILE})

#: 切换时间线四阶段（steps 输出顺序即时间线顺序）
STAGES = ("pre-window", "pre-migration", "post-migration", "cutover")

STAGE_TITLES = {
    "pre-window": "切换窗口前（CI/发布门禁/provider 冒烟/历史治理归零）",
    "pre-migration": "切换窗口内、迁移执行前（pre 预检 + 备份恢复演练）",
    "post-migration": "迁移执行后（post 预检 + 审计链校验 + 锚定）",
    "cutover": "放行决策（人工审批）",
}

_ISOLATION_NOTE = (
    "隔离 rehearsal：本工具只编排演练证据，不代表生产验收，也不授权"
    "任何生产写入/发布；rehearsal_ready 仅说明演练时间线 13 步证据齐备"
    "且审批绑定完整，生产放行仍须按 runbook 人工执行"
)
_NO_EXECUTION_NOTE = (
    "只读证据汇总：不执行生产迁移/锚定/清理/WORM 归档/部署/启停服务/"
    "发布/回滚，不读取密钥，不连接数据库或网络"
)
_IDENTITY_NOTE = (
    "approved_by 仅为审批记录内的字符串，本工具只做结构一致性与哈希绑定"
    "校验，不做身份认证"
)


@dataclass(frozen=True)
class StepSpec:
    step_id: str
    title: str
    stage: str
    evidence_file: str
    basis: str  # 为什么必需（manifest 原样透出，可审计）
    not_executed_action: str  # 证据缺失时的下一步动作（next_actions 透出）
    required: bool = True  # 演练时间线步一律必需（显式字段防未来误加 optional）


#: 生产切换演练时间线（顺序即 manifest 输出顺序；测试用它守卫「无漏项」）。
#: 13 个 required steps——M10-15 设计定版：preflight 拆 pre/post、audit-chain
#: 拆 verify/anchor、provider smoke 拆 search/cloud-voice/llm，加 ci-main/
#: release-check/backup-restore/legacy-papers/draft-ownership/cutover-approval。
STEPS: tuple[StepSpec, ...] = (
    StepSpec(
        "ci-main",
        "main CI 门禁（run id / merge commit / conclusion）",
        "pre-window",
        "ci-main.json",
        "进入切换窗口的代码必须是远端 CI 全绿的 merge commit",
        "等待发布 merge commit 的远端 CI 全绿后导出 run id/commit/conclusion",
    ),
    StepSpec(
        "release-check",
        "release-check 发布门禁九字面汇总（M7-05）",
        "pre-window",
        "release-check.json",
        "lint/typecheck/test/build/E2E/migration/backup/voice/license 全绿",
        "本地执行 python -m app.ops.cli release-check --local-only 后导出汇总",
    ),
    StepSpec(
        "search-smoke",
        "search provider 真实端点冒烟（只收脱敏结果文件）",
        "pre-window",
        "search-smoke.json",
        "search 需打真实端点冒烟通过（SEARCH_CLOUD_API_KEY 可选，无鉴权端点"
        "可空——M10-12）；证据文件不得携带任何 key",
        "打真实 search 端点冒烟（SEARCH_CLOUD_API_KEY 可选）后导出脱敏结果",
    ),
    StepSpec(
        "cloud-voice-smoke",
        "云 voice 部署 key 冒烟（只收脱敏结果文件）",
        "pre-window",
        "cloud-voice-smoke.json",
        "voice 需运维执行 bash infra/smoke_voice_cloud.sh 冒烟通过（部署 key"
        " + 真实短语音 WAV，ASR/TTS 双探针——M10-13）；证据文件不得携带任何 key",
        "运维执行 bash infra/smoke_voice_cloud.sh（部署 key，不入库不入码）"
        "后导出脱敏结果",
    ),
    StepSpec(
        "llm-smoke",
        "云 LLM 部署 key 冒烟（只收脱敏结果文件）",
        "pre-window",
        "llm-smoke.json",
        "LLM 需运维以部署 key 冒烟通过（M10-01 网关真连通）；证据文件不得"
        "携带任何 key",
        "运维以部署 key 冒烟 LLM 网关后导出脱敏结果",
    ),
    StepSpec(
        "legacy-papers",
        "legacy papers 人工决策与分批执行状态（M10-04）",
        "pre-window",
        "legacy-papers.json",
        "历史无归属非 seed 卷必须在切换窗口前人工决策并分批执行完毕"
        "（计数归零）",
        "legacy-paper-report 复核后逐批人工决策/执行至计数归零，再导出",
    ),
    StepSpec(
        "draft-ownership",
        "generation/variant NULL owner 草稿人工决策与执行状态（M10-04）",
        "pre-window",
        "draft-ownership.json",
        "两类历史草稿必须在切换窗口前人工决策并执行完毕（计数归零）",
        "draft-owner-report 复核后逐批人工决策/执行至计数归零，再导出",
    ),
    StepSpec(
        "preflight-pre-migration",
        "production-preflight 迁移前预检（M10-07，pre 阶段证据）",
        "pre-migration",
        "preflight-pre-migration.json",
        "切换窗口开始、执行迁移前：无 fail（pending/not_configured 属迁移前"
        "预期，不阻断 pre 步；放行仍需 post-migration 证据）",
        "切换窗口开始时执行 python -m app.ops.cli production-preflight "
        "--phase pre-migration 后导出",
    ),
    StepSpec(
        "backup-restore",
        "备份 manifest 可恢复证据（M6-06）",
        "pre-migration",
        "backup-restore.json",
        "aios-backup-v1 manifest + 恢复演练 verified（迁移前可恢复证据闭合）",
        "cli backup + 恢复演练 verified 后导出 manifest 摘要",
    ),
    StepSpec(
        "preflight-post-migration",
        "production-preflight 迁移后预检（M10-07，post 阶段证据）",
        "post-migration",
        "preflight-post-migration.json",
        "迁移完成后：放行形态（无 fail 且无 pending/not_configured）",
        "迁移完成后执行 python -m app.ops.cli production-preflight "
        "--phase post-migration 后导出",
    ),
    StepSpec(
        "audit-chain-verify",
        "治理审计哈希链只读校验（M10-04）",
        "post-migration",
        "audit-chain-verify.json",
        "链 valid（verify 先于锚定：链已断时锚定无意义，先修复链）",
        "python -m app.ops.cli audit-chain-verify 确认 valid 后导出",
    ),
    StepSpec(
        "audit-chain-anchor",
        "审计链库外锚定 / WORM 归档（M10-06）",
        "post-migration",
        "audit-chain-anchor.json",
        "锚定 up-to-date + 锚文件已归档 WORM/离线介质（+ 可选锚文件副本"
        "独立校验）",
        "audit-chain-anchor --yes 追加锚点并归档 WORM 后导出",
    ),
    StepSpec(
        "cutover-approval",
        "切换窗口/回滚/观察期人工审批（哈希绑定）",
        "cutover",
        "cutover-approval.json",
        "审批记录必须绑定 step id + 主 step 证据 sha256 集合 + supporting"
        " 文件（锚文件副本）sha256 mapping + 时间 + 说明；缺审批/未覆盖其余"
        " 12 步/未覆盖当前 supporting 文件 => not_executed，哈希失配/引用"
        " 不存在的 supporting 文件 => blocked",
        "其余 12 步全 pass 后，由审批人填写绑定各步证据与 supporting 文件"
        " sha256 的 cutover-approval.json（不回显审批说明）",
    ),
)

STEP_IDS = frozenset(spec.step_id for spec in STEPS)
KNOWN_EVIDENCE_FILES = frozenset(spec.evidence_file for spec in STEPS) | {
    ANCHOR_COMPANION_FILE
}


def _require_step_self_id(obj: Mapping[str, Any], step_id: str) -> None:
    """证据文件必须自声明 step 且与文件名对应步一致（错位文件 fail-closed）。"""
    req_choice(obj, "step", (step_id,))


# --- 各步评估（只提取白名单标量；返回 status/reason/action/data/supporting） ---


def _step_ci_main(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_step_self_id(obj, "ci-main")
    run_id = req_int(obj, "run_id", minimum=1)
    commit = req_commit(obj, "merge_commit")
    conclusion = req_choice(obj, "conclusion", CI_CONCLUSIONS)
    data = {"run_id": run_id, "merge_commit": commit, "conclusion": conclusion}
    if conclusion == "success":
        return (
            STATUS_PASS,
            f"CI run {run_id} @ {commit[:12]}… conclusion=success",
            None,
            data,
            [],
        )
    return (
        STATUS_BLOCKED,
        f"CI run {run_id} conclusion={conclusion}——远端门禁未绿，不得进入切换窗口",
        "排查远端 CI 失败并修复/重新合并，全绿后重新导出 ci-main.json",
        data,
        [],
    )


def _step_release_check(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_step_self_id(obj, "release-check")
    all_green = req_bool(obj, "all_green")
    total = req_int(obj, "total", minimum=1)
    passed = req_int(obj, "passed")
    raw_failed = req(obj, "failed_ids")
    if not isinstance(raw_failed, list) or any(
        not isinstance(item, str) or not item for item in raw_failed
    ):
        raise MalformedEvidence("字段 failed_ids 必须是字符串列表")
    failed_ids = list(raw_failed)
    if passed > total:
        raise MalformedEvidence("字段 passed 大于 total（计数自相矛盾）")
    if all_green and (passed != total or failed_ids):
        raise MalformedEvidence("all_green=true 但计数/失败项非零（自相矛盾）")
    data = {"all_green": all_green, "total": total, "passed": passed, "failed_ids": failed_ids}
    if not all_green:
        return (
            STATUS_BLOCKED,
            f"release-check 未全绿（{passed}/{total}，failed: {', '.join(failed_ids) or '?'}）",
            "修复未通过项并重跑 release-check，全绿后重新导出",
            data,
            [],
        )
    return STATUS_PASS, f"all_green（{passed}/{total} 项 pass）", None, data, []


def _make_smoke_step(
    step_id: str, *, fail_action: str, run_action: str, pass_reason: str
) -> Callable[..., tuple[str, str, str | None, dict, list]]:
    """search / cloud-voice / llm 冒烟共用骨架：独立证据、独立失败语义。

    拆成三个独立 step（M10-15）的理由：release-readiness 把三类 provider
    合在一个 provider-smoke 门里，任一 fail 虽然也能挡住，但未跑/失败与
    通过混在同一个状态里互相掩盖；时间线视角要求每类 provider 的证据
    单独可审计、单独给出 next_action。
    """

    def _eval(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
        _require_step_self_id(obj, step_id)
        executed = req_bool(obj, "executed")
        result = req_choice(obj, "result", ("pass", "fail", "not_executed"))
        if executed and result == "not_executed":
            raise MalformedEvidence(
                "executed=true 但 result=not_executed（自相矛盾）"
            )
        if not executed and result != "not_executed":
            raise MalformedEvidence(
                f"executed=false 但 result={result}（自相矛盾）"
            )
        data = {"executed": executed, "result": result}
        if result == "fail":
            return STATUS_BLOCKED, f"{step_id} 冒烟失败", fail_action, data, []
        if result == "not_executed":
            return (
                STATUS_NOT_EXECUTED,
                f"{step_id} 冒烟未执行（证据文件已就位但结果为 not_executed）",
                run_action,
                data,
                [],
            )
        return STATUS_PASS, pass_reason, None, data, []

    return _eval


_step_search_smoke = _make_smoke_step(
    "search-smoke",
    fail_action=(
        "排查真实 search 端点连通/鉴权（SEARCH_CLOUD_API_KEY 可选）后重跑"
        "冒烟并导出脱敏结果"
    ),
    run_action=(
        "打真实 search 端点冒烟（SEARCH_CLOUD_API_KEY 可选，无鉴权端点可空）"
        "后导出脱敏结果文件"
    ),
    pass_reason="search 真实端点冒烟通过（脱敏结果文件）",
)
_step_cloud_voice_smoke = _make_smoke_step(
    "cloud-voice-smoke",
    fail_action=(
        "运维排查部署 key 与云 ASR/TTS 后重跑 bash infra/smoke_voice_cloud.sh"
        "并导出脱敏结果"
    ),
    run_action=(
        "运维执行 bash infra/smoke_voice_cloud.sh（部署 key + 真实短语音 WAV，"
        "ASR/TTS 双探针，key 不入库不入码）后导出脱敏结果文件"
    ),
    pass_reason="云 voice 部署 key 冒烟通过（ASR/TTS 双探针，脱敏结果文件）",
)
_step_llm_smoke = _make_smoke_step(
    "llm-smoke",
    fail_action="运维排查部署 key 与 LLM 网关后重跑冒烟并导出脱敏结果",
    run_action="运维以部署 key 冒烟 LLM 网关（key 不入库不入码）后导出脱敏结果文件",
    pass_reason="云 LLM 部署 key 冒烟通过（脱敏结果文件）",
)


def _make_governance_step(step_id: str, *, label: str):
    """legacy-papers / draft-ownership 共用骨架：计数 >0 => pending。"""

    def _eval(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
        _require_step_self_id(obj, step_id)
        pending_count = req_int(obj, "pending_count")
        executed = batches_executed(req(obj, "batches"), label="batches")
        data = {"pending_count": pending_count, "batches_executed": executed}
        if pending_count > 0:
            return (
                STATUS_PENDING,
                f"{pending_count} 条{label}待人工决策/分批执行（未归零）",
                (
                    f"{label}报告复核后逐批人工决策/执行（不输出生产 ID），"
                    "计数归零后重新导出"
                ),
                data,
                [],
            )
        return STATUS_PASS, f"无待处理{label}（已执行 {executed} 批）", None, data, []

    return _eval


_step_legacy_papers = _make_governance_step(
    "legacy-papers", label="历史无归属卷"
)
_step_draft_ownership = _make_governance_step(
    "draft-ownership", label="NULL owner 草稿"
)


def _preflight_counts(obj: dict[str, Any]) -> dict[str, int]:
    summary = req_dict(obj, "summary")
    counts = {
        name: req_int(summary, name)
        for name in ("pass", "pending", "fail", "not_configured")
    }
    if sum(counts.values()) == 0:
        raise MalformedEvidence("summary 四项计数全 0——不是任何合法 preflight 报告形态")
    return counts


def _step_preflight_pre(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_step_self_id(obj, "preflight-pre-migration")
    # phase 字段必须与文件名声明的阶段一致（错位即结构不可信）
    req_choice(obj, "phase", ("pre-migration",))
    counts = _preflight_counts(obj)
    data = {"phase": "pre-migration", "summary": counts}
    if counts["fail"] > 0:
        return (
            STATUS_BLOCKED,
            (
                f"迁移前预检存在 fail={counts['fail']}——切换窗口开始前已暴露的"
                "问题必须先停下排查"
            ),
            (
                "按 runbook 排查 fail 项并修复后，复跑 production-preflight "
                "--phase pre-migration 重新导出"
            ),
            data,
            [],
        )
    return (
        STATUS_PASS,
        (
            f"迁移前预检无 fail（pending={counts['pending']}/"
            f"not_configured={counts['not_configured']} 属迁移前预期，不阻断 "
            "pre 步；放行仍需 post-migration 证据）"
        ),
        None,
        data,
        [],
    )


def _step_preflight_post(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_step_self_id(obj, "preflight-post-migration")
    req_choice(obj, "phase", ("post-migration",))
    counts = _preflight_counts(obj)
    data = {"phase": "post-migration", "summary": counts}
    if counts["fail"] > 0:
        return (
            STATUS_BLOCKED,
            f"迁移后预检存在 fail={counts['fail']}——按 runbook 排查",
            (
                "按 runbook 排查 fail 项并修复后，复跑 production-preflight "
                "--phase post-migration 重新导出"
            ),
            data,
            [],
        )
    if counts["pending"] or counts["not_configured"]:
        return (
            STATUS_PENDING,
            (
                "迁移后预检仍有 pending="
                f"{counts['pending']} / not_configured={counts['not_configured']}"
                "（人工补齐后复跑 preflight 再导出）"
            ),
            (
                "人工补齐 pending/not_configured 项后复跑 production-preflight "
                "--phase post-migration 重新导出"
            ),
            data,
            [],
        )
    if counts["pass"] == 0:
        raise MalformedEvidence("summary.pass 为 0——不是任何合法 preflight 报告形态")
    return (
        STATUS_PASS,
        f"迁移后预检放行形态（pass={counts['pass']}，无 pending/fail）",
        None,
        data,
        [],
    )


def _step_audit_chain_verify(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_step_self_id(obj, "audit-chain-verify")
    valid = req_bool(obj, "valid")
    entries = req_int(obj, "entries")
    data = {"valid": valid, "entries": entries}
    if not valid:
        return (
            STATUS_BLOCKED,
            f"审计链校验 invalid（申报 entries={entries}）——按 runbook 先修复链",
            "按 runbook 排查修复链（verify 先于锚定），valid 后重新导出",
            data,
            [],
        )
    return STATUS_PASS, f"审计链 valid（{entries} entries）", None, data, []


def _step_audit_chain_anchor(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_step_self_id(obj, "audit-chain-anchor")
    anchor = req_dict(obj, "anchor")
    worm = req_dict(obj, "worm")
    # 链结论属于 verify 步；本步只看锚定/WORM/副本（拆分语义）
    anchor_status = req_choice(anchor, "status", ("up-to-date", "valid", "invalid"))
    anchors = req_int(anchor, "anchors")
    worm_archived = req_bool(worm, "archived")
    data = {
        "anchor_status": anchor_status,
        "anchors": anchors,
        "worm_archived": worm_archived,
    }
    supporting: list[dict[str, str]] = []
    companion_note = "未提供 audit-anchor.jsonl 副本（锚点计数按申报值）"
    companion = root / ANCHOR_COMPANION_FILE
    if os.path.lexists(companion):
        check_regular_file(companion)
        supporting.append(
            {"file": ANCHOR_COMPANION_FILE, "sha256": sha256_file(companion)}
        )
        # 独立校验锚文件副本（复用 anchor 工具的完整解析：anchor_hash 重算、
        # 锚链链接、sequence 递增——纯本地文件校验，零 DB 连接）。
        anchors_parsed, problems = load_anchor_file(companion)
        if problems:
            head = "; ".join(problems[:2])
            return (
                STATUS_BLOCKED,
                f"锚文件副本校验未通过（{len(problems)} 个问题）: {head}",
                (
                    "按锚文件问题排查（被改/损坏的副本不得用于演练），修复后"
                    "重新导出副本与证据"
                ),
                data,
                supporting,
            )
        if len(anchors_parsed) != anchors:
            return (
                STATUS_BLOCKED,
                f"申报锚点数 anchors={anchors} 与副本实际 {len(anchors_parsed)} 行不一致",
                "复核锚文件副本与申报计数，一致后重新导出",
                data,
                supporting,
            )
        companion_note = f"锚文件副本 {len(anchors_parsed)} 锚点校验自洽"
    if anchor_status == "invalid":
        return (
            STATUS_BLOCKED,
            "锚定 verify-only invalid——锚文件与 DB 链交叉核对失败",
            "先完成 audit-chain-verify=pass，再排查锚定交叉核对失败原因",
            data,
            supporting,
        )
    if anchor_status != "up-to-date":
        return (
            STATUS_PENDING,
            "锚定落后（DB head 超前最后锚点）——人工追加并归档后复跑",
            "人工 audit-chain-anchor --yes 追加锚点并归档 WORM 后重新导出",
            data,
            supporting,
        )
    if not worm_archived:
        return (
            STATUS_PENDING,
            "锚文件未归档 WORM/对象锁/离线介质——本机锚文件只是操作见证",
            "将锚文件归档 WORM/对象锁/离线介质后重新导出",
            data,
            supporting,
        )
    return (
        STATUS_PASS,
        f"锚定 up-to-date（{anchors} 锚点）+ WORM 已归档；{companion_note}",
        None,
        data,
        supporting,
    )


def _step_backup_restore(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_step_self_id(obj, "backup-restore")
    schema_version = req_choice(obj, "schema_version", ("aios-backup-v1",))
    created_at = req_iso(obj, "created_at")
    drill = req_dict(obj, "restore_drill")
    verified = req_bool(drill, "verified")
    inserted = req_int(drill, "inserted_rows")
    data = {
        "schema_version": schema_version,
        "created_at": created_at,
        "restore_verified": verified,
        "inserted_rows": inserted,
    }
    if not verified:
        return (
            STATUS_PENDING,
            "备份 manifest 在但恢复演练未 verified——可恢复证据未闭合",
            "人工完成 restore drill（verified）后重新导出",
            data,
            [],
        )
    return (
        STATUS_PASS,
        f"aios-backup-v1 manifest + 恢复演练 verified（回灌 {inserted} 行）",
        None,
        data,
        [],
    )


def _current_supporting(root: Path) -> dict[str, str]:
    """当前证据目录内实际存在的 supporting 文件及其 sha256。

    审批必须精确覆盖这个集合（目录没有 supporting 文件时为空 mapping）。
    路径护栏与主装载同口径：symlink/非常规文件 fail-closed（EvidenceInputError
    由 CLI 映射 exit 2）。
    """
    current: dict[str, str] = {}
    for name in sorted(BINDABLE_SUPPORTING_FILES):
        path = root / name
        if os.path.lexists(path):
            check_regular_file(path)
            current[name] = sha256_file(path)
    return current


def _step_cutover_approval(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_step_self_id(obj, "cutover-approval")
    schema_version = req_int(obj, "schema_version")
    if schema_version != 1:
        raise MalformedEvidence(f"schema_version 非 1: {schema_version}")
    approved_at = req_iso(obj, "approved_at")
    req_str(obj, "note")  # 只验证非空；内容不回显（防审批说明内嵌敏感值）
    window = req_dict(obj, "window")
    start = req_iso(window, "start")
    end = req_iso(window, "end")
    try:
        window_ordered = datetime.fromisoformat(start) < datetime.fromisoformat(end)
    except TypeError:
        # naive 与 aware 混合比较抛 TypeError：统一按结构不符拒绝
        raise MalformedEvidence(
            "window.start/end 必须同为带时区或同为本地时间"
        ) from None
    if not window_ordered:
        raise MalformedEvidence("window.start 必须早于 window.end")
    req_str(obj, "rollback_plan")
    req_str(obj, "observation")
    approved_by = obj.get("approved_by")
    if approved_by is not None and (
        not isinstance(approved_by, str) or not approved_by.strip()
    ):
        raise MalformedEvidence("字段 approved_by 必须是非空字符串")
    step_evidence = req_dict(obj, "step_evidence")
    bindable = STEP_IDS - {"cutover-approval"}
    for step_id, digest in step_evidence.items():
        if step_id not in bindable:
            raise MalformedEvidence(f"step_evidence 引用未知步骤 {step_id}")
        if not isinstance(digest, str) or not HEX64_RE.fullmatch(digest):
            raise MalformedEvidence(
                f"step_evidence.{step_id} 非 64 位小写十六进制"
            )
    # supporting 绑定（M10-15 返工补上）：锚文件副本等 supporting 文件
    # 不属于任何主 step JSON，主 step 哈希绑定覆盖不到——不单独绑定的话，
    # 审批后把副本换成另一份自洽副本不会被发现。
    supporting_evidence = req_dict(obj, "supporting_evidence")
    for name, digest in supporting_evidence.items():
        if name not in BINDABLE_SUPPORTING_FILES:
            raise MalformedEvidence(
                f"supporting_evidence 引用未知 supporting 文件 {name}"
            )
        if not isinstance(digest, str) or not HEX64_RE.fullmatch(digest):
            raise MalformedEvidence(
                f"supporting_evidence.{name} 非 64 位小写十六进制"
            )
    current_supporting = _current_supporting(root)
    data = {
        "approved_at": approved_at,
        "window": {"start": start, "end": end},
        "covered_steps": sorted(step_evidence),
        "hash_mismatches": [],
        "coverage_gaps": [],
        "covered_supporting": sorted(supporting_evidence),
        "supporting_hash_mismatches": [],
        "supporting_coverage_gaps": [],
        "approved_by_recorded": approved_by is not None,
        "identity_note": _IDENTITY_NOTE,
    }
    step_mismatches = sorted(
        step_id
        for step_id, digest in step_evidence.items()
        if sha.get(step_id) != digest
    )
    # 引用当前不存在的 supporting 文件（审批后文件被移除/审批错绑）与
    # 哈希失配同等对待：都是「审批绑定与当前证据不一致」。
    supporting_mismatches = sorted(
        name
        for name, digest in supporting_evidence.items()
        if current_supporting.get(name) != digest
    )
    if step_mismatches or supporting_mismatches:
        data["hash_mismatches"] = step_mismatches
        data["supporting_hash_mismatches"] = supporting_mismatches
        parts = []
        if step_mismatches:
            parts.append(f"step 证据 {step_mismatches}")
        if supporting_mismatches:
            parts.append(f"supporting 文件 {supporting_mismatches}")
        return (
            STATUS_BLOCKED,
            (
                f"审批绑定的证据哈希与当前文件不匹配（{'；'.join(parts)}）"
                "——证据在审批后被改动或移除，需重新审批"
            ),
            "重新导出被改动的证据/supporting 文件并重新审批（哈希绑定恢复一致）",
            data,
            [],
        )
    step_gaps = sorted(bindable - set(step_evidence))
    supporting_gaps = sorted(set(current_supporting) - set(supporting_evidence))
    if step_gaps or supporting_gaps:
        data["coverage_gaps"] = step_gaps
        data["supporting_coverage_gaps"] = supporting_gaps
        parts = []
        if step_gaps:
            parts.append(f"{len(step_gaps)} 步主证据 {step_gaps}")
        if supporting_gaps:
            parts.append(f"supporting 文件 {supporting_gaps}")
        return (
            STATUS_NOT_EXECUTED,
            f"审批未覆盖（{'；'.join(parts)}）——审批动作尚未完整发生",
            "补齐对应步骤/supporting 证据后，由审批人重新绑定全部哈希并重新审批",
            data,
            [],
        )
    return (
        STATUS_PASS,
        (
            f"人工审批记录结构完整且哈希绑定匹配当前证据（approved_at="
            f"{approved_at}，覆盖 {len(step_evidence)} 步 + "
            f"{len(supporting_evidence)} 个 supporting 文件；{_IDENTITY_NOTE}）"
        ),
        None,
        data,
        [],
    )


EVALUATORS: dict[str, Callable[..., tuple[str, str, str | None, dict, list]]] = {
    "ci-main": _step_ci_main,
    "release-check": _step_release_check,
    "search-smoke": _step_search_smoke,
    "cloud-voice-smoke": _step_cloud_voice_smoke,
    "llm-smoke": _step_llm_smoke,
    "legacy-papers": _step_legacy_papers,
    "draft-ownership": _step_draft_ownership,
    "preflight-pre-migration": _step_preflight_pre,
    "backup-restore": _step_backup_restore,
    "preflight-post-migration": _step_preflight_post,
    "audit-chain-verify": _step_audit_chain_verify,
    "audit-chain-anchor": _step_audit_chain_anchor,
    "cutover-approval": _step_cutover_approval,
}


# --- 主流程 -------------------------------------------------------------------


def _unrecognized_files(root: Path) -> list[dict[str, Any]]:
    """目录内不属于任何步骤的文件/目录（含 sha256，帮助发现文件名笔误）。"""
    out: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir()):
        if entry.name in KNOWN_EVIDENCE_FILES:
            continue
        if entry.is_dir():
            out.append({"file": entry.name, "kind": "directory", "sha256": None})
        else:
            out.append(
                {"file": entry.name, "kind": "file", "sha256": sha256_file(entry)}
            )
    return out


def run_cutover_rehearsal(evidence_dir: str | Path) -> dict[str, Any]:
    """只读汇总全部步骤并组装 manifest（对数据库/网络零访问、零写入）。

    两遍执行：先装载（存在性/路径护栏/SHA-256/JSON 解析/敏感键扫描），后
    评估（cutover-approval 最后评估，但 STEPS 顺序保证其哈希绑定校验拿到
    全部步骤的当前 sha256）。
    """
    root = check_evidence_dir(Path(evidence_dir))
    loaded: dict[str, dict[str, Any]] = {}
    sha_by_step: dict[str, str] = {}
    for spec in STEPS:
        path = root / spec.evidence_file
        state: dict[str, Any] = {"spec": spec, "obj": None, "problem": None}
        if os.path.lexists(path):
            check_regular_file(path)
            sha_by_step[spec.step_id] = sha256_file(path)
            obj, problem = load_json_object(path)
            if problem is None:
                sensitive = find_sensitive_key(obj)
                if sensitive is not None:
                    problem = f"证据含敏感键 {sensitive}（只接受脱敏证据，值不回显）"
            if problem is None:
                embedded = find_embedded_credential(obj)
                if embedded is not None:
                    problem = (
                        f"证据字段 {embedded} 内嵌凭据（://user:pass@ 形态，"
                        "值不回显）——证据文件不得携带任何凭据"
                    )
            # 解析成功但敏感扫描失败与解析失败同权：obj 不再进入评估器——
            # 只有整体拒绝才保证该步不会凭其余字段变 pass。
            state["obj"] = None if problem is not None else obj
            state["problem"] = problem
        loaded[spec.step_id] = state

    steps: list[dict[str, Any]] = []
    for spec in STEPS:
        state = loaded[spec.step_id]
        record: dict[str, Any] = {
            "step": spec.step_id,
            "title": spec.title,
            "stage": spec.stage,
            "required": True,
            "basis": spec.basis,
            "status": STATUS_NOT_EXECUTED,
            "reason": f"未提供证据文件 {spec.evidence_file}",
            "next_action": spec.not_executed_action,
            "data": {},
            "evidence": (
                {"file": spec.evidence_file, "sha256": sha_by_step.get(spec.step_id)}
                if spec.step_id in sha_by_step
                else None
            ),
            "supporting": [],
        }
        if state["obj"] is None and state["problem"] is not None:
            # malformed（含敏感键/结构不符）统一映射 blocked：结构不可信的
            # 证据与结论为否同样必须停下，不留给「待补」的宽松读法。
            record["status"] = STATUS_BLOCKED
            record["reason"] = f"{spec.evidence_file}: {state['problem']}"
            record["next_action"] = "修复证据结构/脱敏后重新导出（敏感值与凭据不得进入证据目录）"
        elif state["obj"] is not None:
            try:
                status, reason, action, data, supporting = EVALUATORS[spec.step_id](
                    state["obj"], root, sha_by_step
                )
            except MalformedEvidence as exc:
                status, reason, action, data, supporting = (
                    STATUS_BLOCKED,
                    f"{spec.evidence_file} 证据结构不符: {exc}",
                    "修复证据结构后重新导出（结构不可信的证据不得计入演练）",
                    {},
                    [],
                )
            record.update(
                {
                    "status": status,
                    "reason": reason,
                    "next_action": action,
                    "data": data,
                    "supporting": supporting,
                }
            )
        steps.append(record)

    summary = {
        status: sum(1 for step in steps if step["status"] == status)
        for status in STATUS_ORDER
    }
    overall_status = next(
        (status for status in OVERALL_PRECEDENCE if summary[status] > 0),
        STATUS_PASS,
    )
    rehearsal_ready = overall_status == STATUS_PASS
    blockers = [
        {"step": step["step"], "reason": step["reason"]}
        for step in steps
        if step["status"] == STATUS_BLOCKED
    ]
    next_actions = [
        {
            "step": step["step"],
            "stage": step["stage"],
            "status": step["status"],
            "action": step["next_action"],
        }
        for step in steps
        if step["status"] != STATUS_PASS
    ]
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "tool": "cutover-rehearsal",
        "evidence_dir": str(root),
        "read_only": True,
        "isolation_note": _ISOLATION_NOTE,
        "no_execution_note": _NO_EXECUTION_NOTE,
        "identity_note": _IDENTITY_NOTE,
        "steps": steps,
        "unrecognized_files": _unrecognized_files(root),
        "summary": summary,
        "not_pass_steps": [
            step["step"] for step in steps if step["status"] != STATUS_PASS
        ],
        "blockers": blockers,
        "next_actions": next_actions,
        "overall_status": overall_status,
        "rehearsal_ready": rehearsal_ready,
        "exit_code": 0 if rehearsal_ready else 1,
    }
    return scrub_sensitive(report)


def format_rehearsal_summary(report: Mapping[str, Any]) -> str:
    """人类可读摘要（无凭据/无生产 ID/无证据正文），按时间线阶段分组。"""
    lines = [
        "生产切换演练编排 manifest（cutover rehearsal，只读汇总）",
        f"生成时间: {report['generated_at']}",
        f"证据目录: {report['evidence_dir']}",
        _ISOLATION_NOTE,
        "-" * 72,
    ]
    for stage in STAGES:
        stage_steps = [
            step for step in report["steps"] if step["stage"] == stage
        ]
        lines.append(f"—— {stage}｜{STAGE_TITLES[stage]} ——")
        for step in stage_steps:
            lines.append(
                f"[{step['status'].upper():<13}] {step['step']}: {step['title']}"
            )
            lines.append(f"{'':<16} -> {step['reason']}")
        lines.append("")
    if report["unrecognized_files"]:
        names = ", ".join(item["file"] for item in report["unrecognized_files"])
        lines.append(f"[注意] 证据目录内未识别的条目: {names}（可能是文件名笔误）")
    counts = "  ".join(f"{k}={v}" for k, v in report["summary"].items())
    verdict = "REHEARSAL READY" if report["rehearsal_ready"] else "NOT READY"
    lines.append("-" * 72)
    lines.append(f"RESULT: {verdict}  overall={report['overall_status']}  {counts}")
    if report["rehearsal_ready"]:
        lines.append(
            "13 步全部 pass 且人工审批与当前证据哈希完整匹配——manifest 仅汇总"
            "演练证据，不代表生产验收，也不授权生产写入/发布。"
        )
    else:
        if report["blockers"]:
            head = "; ".join(
                f"{item['step']}: {item['reason']}" for item in report["blockers"]
            )
            lines.append(f"blockers（必须先停下排查）: {head}")
        lines.append("next_actions（演练未闭环，仍需人工执行；节选）:")
        for item in report["next_actions"]:
            lines.append(f"  - [{item['status']}] {item['step']}: {item['action']}")
        lines.append(
            f"注意: 顶层优先级 blocked > pending > not_executed（当前 "
            f"overall={report['overall_status']}）；本工具绝不代为迁移、锚定、"
            "清理、部署或发布。"
        )
    lines.append(
        "退出码: 13 步全 pass=0 / 任一步非 pass=1 / 目录或路径与 IO 问题=2。"
    )
    return "\n".join(lines)
