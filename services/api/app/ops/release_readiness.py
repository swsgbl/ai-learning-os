"""M10-11 release readiness：发布准备与人工审批证据的只读汇总 manifest。

定位：收口 release-check（构建门禁）与 production-preflight（切换预检）之间
的信息割裂——把分散的发布审批门证据集中成一份可验收 manifest。工具**只读
调用方显式提供的 --evidence-dir 内的 JSON/JSONL 证据文件**，对每份关键证据
计算 SHA-256，按门（gate）给出诚实状态，并校验人工审批记录与证据哈希的
绑定关系。

安全边界（docs/DEVELOPMENT.md「发布准备 readiness」节同步维护）：

- 只读本地证据：不连接数据库、不调用 API、不访问网络、不读取任何环境变量
  （生产密钥物理上进不了本工具，`os.environ` 零引用）；
- 不执行任何生产操作：不迁移、不锚定、不清理、不 WORM 归档、不发布、不
  回滚——命令没有 --yes 执行形态，是纯汇总器，绝不代跑任何 runbook 步骤；
- 输出零敏感：每门只提取白名单标量（计数/枚举/哈希/门 id/时间戳），不回显
  证据正文（审批 note 只验证非空、approved_by 只记录是否存在）；最终
  manifest 整体再做敏感键抹除与 `://user:pass@` 凭据段抹除（M10-15 起复用
  app.ops.evidence_kit 共享层，底层同 production_preflight.redact_secrets）
  ——不含完整 DB URL、密码、token、key、生产 paper/draft/用户 ID；
- 证据文件含敏感键（password/passwd/secret/token/api_key/access_key/
  private_key/authorization/cookie/credential 等变体，值从不回显）或内嵌
  `://user:pass@` 凭据段即 malformed——证据目录只应存放脱敏导出物
  （provider 冒烟只收脱敏结果文件）；
- 路径护栏 fail-closed（exit 2）：--evidence-dir 必须存在且为真目录
  （symlink/普通文件/目录内任何 symlink 拒绝），证据文件必须是常规文件；
  --output 复用 legacy 报告的 artifacts/temp gitignore 护栏并经 CLI 原子
  落盘（同目录临时文件 + fsync + os.replace，symlink 目标拒绝）。

状态语义（诚实优先）：missing（无证据文件）/ malformed（结构不符、自声明
gate 不匹配或含敏感键）/ tampered（审批哈希绑定与当前证据不匹配、锚文件
副本校验失败）/ blocked（证据在但结论为否：CI failure、链 invalid、冒烟
fail）/ pending（待人工决策或执行：审批未覆盖全部必需门、治理计数 >0、
pre-migration 证据、恢复演练未验证）/ pass。绝不把 pending 包装成 pass，
不凭文件存在自动通过。

人工审批记录（release-approval.json）必须绑定 gate id、evidence sha256
集合、时间与说明；本工具只做**结构一致性与哈希绑定**校验，不声称身份认证
（approved_by 只是记录字符串）。缺审批/缺绑定/绑定失配时发布动作保持
missing/pending/tampered——manifest 只有在「全部必需门 pass 且审批与当前
证据哈希完整匹配」时才给出 release_ready=true。

退出码：全部必需门 pass=0；任一必需门非 pass=1（optional 门 turn-tls 如实
透出为 not_pass_optional + optional_scope_note，不计入必需门——release_ready
不含公网语音就绪结论）；--evidence-dir/--output 路径或 IO 问题=2（与
preflight/anchor CLI 同口径）。
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
    req_hex,
    req_int,
    req_iso,
    req_str,
    scrub_sensitive,
    sha256_file,
)
from app.ops.production_preflight import PHASES

STATUS_PASS = "pass"
STATUS_PENDING = "pending"
STATUS_BLOCKED = "blocked"
STATUS_MISSING = "missing"
STATUS_MALFORMED = "malformed"
STATUS_TAMPERED = "tampered"
#: 汇总固定顺序（人类摘要与 summary dict 同序）
STATUS_ORDER = (
    STATUS_PASS,
    STATUS_PENDING,
    STATUS_BLOCKED,
    STATUS_MISSING,
    STATUS_MALFORMED,
    STATUS_TAMPERED,
)

#: 审计门的可选锚文件副本（真实 anchor JSONL；提供时做独立完整性校验）
ANCHOR_COMPANION_FILE = "audit-anchor.jsonl"

#: GitHub Actions run conclusion 白名单（仅 success 构成 pass）
CI_CONCLUSIONS = (
    "success",
    "failure",
    "cancelled",
    "startup_failure",
    "timed_out",
    "action_required",
)

#: provider 冒烟聚合覆盖的三类 provider 槽位（多任一/缺任一 = malformed，
#: 不虚报覆盖面）。三类要求不同：voice 按拓扑选轨（local=本地语音链路，
#: 无需云 key；hybrid/cloud=云 voice 部署 key）；LLM 需运维部署 key；search
#: 打真实端点（SEARCH_CLOUD_API_KEY 可选——无鉴权 SearXNG 合法，M10-12）。
SMOKE_PROVIDERS = ("voice", "search", "llm")

#: M14-70 聚合契约：topology 恰为 voice_mode 单键，取值恰为三拓扑之一
#: （与 provider_smoke_evidence.VOICE_MODES 同步，防漂移）。
SMOKE_VOICE_MODES = ("local", "hybrid", "cloud")

#: M14-70 聚合契约：providers 每项键恰为 executed/result/evidence_step
#: （evidence_step 是拓扑溯源的精确 step id；单步 exit_code/脚本细节不
#: 透传，多余键 = malformed）。
SMOKE_ENTRY_KEYS = ("executed", "result", "evidence_step")

#: 公网 TURN/TLS 验证的三项检查
TURN_CHECKS = ("stun_binding", "tls_relay", "symmetric_nat_e2e")

#: M14-73 long-soak 门策略：证据必须是 tools/ops/soak_stability_audit.py
#: 在**恰为此默认策略**下产出的 audit_schema_version=2 报告（逐字节复制为
#: long-soak.json）。任何策略漂移（窗口/间隔/间隔上限/留存）即 malformed——
#: 非该策略下的 pass 不构成「真实连续 24 小时稳定窗口」证据。
SOAK_AUDIT_TOOL = "tools/ops/soak_stability_audit.py"
SOAK_AUDIT_SCHEMA_VERSION = 2
SOAK_HISTORY_SCHEMA_VERSION = 1  # 报告顶层 schema_version（monitoring_history）
SOAK_WINDOW_MINUTES = 1440
SOAK_EXPECTED_INTERVAL_MINUTES = 15
SOAK_MAX_GAP_MINUTES = 20
SOAK_RETENTION = 500
#: soak 审计报告 v2 的顶层键全集（与工具 build_report 逐键一致；多余/缺失
#: 键 = 手工拼装或旧版形态，fail-closed 拒绝）
SOAK_REPORT_KEYS = frozenset({
    "schema_version", "audit_schema_version", "gate", "tool", "input",
    "row_count", "analyzed_row_count", "omitted_older_count", "settings",
    "anchor_collected_at", "window_start_collected_at", "selected_row_count",
    "window_status_counts", "window_non_ok_count", "max_observed_gap_minutes",
    "selected_span_minutes", "classification", "reasons",
})
#: 分类固定词汇原因（与工具 classify 输出一致；词汇外原因 = 不可信证据）
SOAK_BLOCK_REASONS = ("non-ok-status-in-window", "excessive-gap-in-window")
SOAK_PENDING_REASONS = ("insufficient-clean-coverage", "insufficient-sample-count")

_IDENTITY_NOTE = (
    "approved_by 仅为审批记录内的字符串，本工具只做结构一致性与哈希绑定校验，"
    "不做身份认证"
)
_NO_EXECUTION_NOTE = (
    "只读证据汇总：不执行生产迁移/锚定/清理/WORM 归档/发布/回滚，"
    "不读取密钥，不连接数据库或网络"
)
#: release_ready 的适用边界（manifest 固定透出，防被误读为公网语音就绪）
_OPTIONAL_SCOPE_NOTE = (
    "release_ready 只断言必需门全 pass（本机/LAN 发布形态）；optional 门 "
    "turn-tls 不计入——公网语音发布必须另行要求 turn-tls=pass 方可放行，"
    "release_ready 不得解释为公网语音就绪"
)


@dataclass(frozen=True)
class GateSpec:
    gate_id: str
    title: str
    evidence_file: str
    required: bool
    basis: str  # 为什么必需/可选（manifest 原样透出，可审计）


#: 发布审批门矩阵（顺序即 manifest 输出顺序；测试用它守卫「无漏项」）
GATES: tuple[GateSpec, ...] = (
    GateSpec(
        "ci-main",
        "main CI 门禁（run id / merge commit / conclusion）",
        "ci-main.json",
        True,
        "发布 merge commit 的远端 CI 必须全绿，未验证代码不得进入生产",
    ),
    GateSpec(
        "release-check",
        "release-check 发布门禁九字面汇总（M7-05）",
        "release-check.json",
        True,
        "lint/typecheck/test/build/E2E/migration/backup/voice/license 全绿",
    ),
    GateSpec(
        "production-preflight",
        "production-preflight 生产切换预检（M10-07）",
        "production-preflight.json",
        True,
        "post-migration 放行形态：无 fail 且无 pending/not_configured",
    ),
    GateSpec(
        "backup-restore",
        "备份 manifest 可恢复证据（M6-06）",
        "backup-restore.json",
        True,
        "aios-backup-v1 manifest + 恢复演练 verified（可恢复证据闭合）",
    ),
    GateSpec(
        "audit-chain-anchor",
        "审计链 / 库外锚定 / WORM 归档（M10-04/M10-06）",
        "audit-chain-anchor.json",
        True,
        "链 valid + 锚定 up-to-date + 锚文件已归档 WORM/离线介质",
    ),
    GateSpec(
        "legacy-papers",
        "legacy papers 人工决策与分批执行状态（M10-04）",
        "legacy-papers.json",
        True,
        "历史无归属非 seed 卷必须人工决策并分批执行完毕（计数归零）",
    ),
    GateSpec(
        "draft-ownership",
        "generation/variant NULL owner 草稿人工决策与执行状态（M10-04）",
        "draft-ownership.json",
        True,
        "两类历史草稿必须人工决策并执行完毕（计数归零）",
    ),
    GateSpec(
        "long-soak",
        "M14-72 真实 24h 长稳审计（soak_stability_audit，audit_schema_version=2）",
        "long-soak.json",
        True,
        "发布前必须有真实连续 24 小时稳定窗口证据：窗口 1440 分钟/期望间隔 15"
        " 分钟/最大间隔 20 分钟策略下的 pass 审计报告（固定词汇原因 fail-closed，"
        "绝不从总历史跨度或合成时长推导）",
    ),
    GateSpec(
        "provider-smoke",
        "voice 按拓扑冒烟（local/hybrid/cloud）+ LLM 部署 key 冒烟 + search "
        "真实端点冒烟（只收脱敏结果文件）",
        "provider-smoke.json",
        True,
        "语音冒烟按拓扑选轨（M14-70 topology.voice_mode）：local 需运维执行 "
        "bash infra/smoke_voice_local.sh 冒烟通过（本地语音链路 ASR/TTS 探针，"
        "无需云 key，evidence_step=local-voice-smoke）；hybrid/cloud 需运维执行 "
        "bash infra/smoke_voice_cloud.sh 冒烟通过（部署 key + 真实短语音 WAV，"
        "ASR/TTS 双探针，evidence_step=cloud-voice-smoke）；LLM 需运维以部署 key "
        "冒烟通过；search 需打真实端点冒烟通过（SEARCH_CLOUD_API_KEY 可选，"
        "无鉴权端点可空）；证据文件不得携带任何 key",
    ),
    GateSpec(
        "turn-tls",
        "公网 TURN/TLS 与对称 NAT 验证（M10-05）",
        "turn-tls.json",
        False,
        "optional：本机/LAN 发布形态不需要公网 TURN；公网语音发布必须补齐，"
        "状态如实透出不计入 fail",
    ),
    GateSpec(
        "release-approval",
        "发布窗口/回滚/观察期人工审批（哈希绑定）",
        "release-approval.json",
        True,
        "审批记录必须绑定 gate id + 证据 sha256 集合 + 时间 + 说明，"
        "缺审批或绑定失配不得放行",
    ),
)

GATE_IDS = frozenset(spec.gate_id for spec in GATES)
REQUIRED_GATE_IDS = frozenset(spec.gate_id for spec in GATES if spec.required)
KNOWN_EVIDENCE_FILES = frozenset(spec.evidence_file for spec in GATES) | {
    ANCHOR_COMPANION_FILE
}

#: 审批记录禁止携带的 release-approval-draft 底稿保留元数据字段（M14-151）：
#: 底稿输出中除 gate_evidence 哈希外的全部字段都是「草稿专用」元数据。人工
#: 审批记录携带任一即 malformed（fail-closed：即使补齐 gate 自声明与全部
#: 人工字段、哈希精确匹配也不放行）——审批人应从底稿哈希出发**从零组装**
#: 合法审批记录，而不是在底稿文件上补字段改名。与
#: release_approval_draft.build_release_approval_draft 的输出字段保持同步
#: （测试守卫：底稿新增元数据字段而漏登记会直接红）。
APPROVAL_DRAFT_RESERVED_FIELDS = frozenset(
    (
        "draft",
        "manual_fields_required",
        "confirmation_required",
        "gate_evidence_missing",
        "required_coverage_gaps",
        "gate_statuses",
        "readiness",
        "load_problems",
        "approval_file_present",
        "generated_at",
        "notice",
        "tool",
        "evidence_dir",
    )
)


# --- 白名单字段提取（严格 schema，违例即 MalformedEvidence） -------------------------


def _require_gate_self_id(obj: Mapping[str, Any], gate_id: str) -> None:
    """证据文件必须自声明 gate 且与文件名对应门一致（错位文件 fail-closed）。"""
    req_choice(obj, "gate", (gate_id,))


# --- 各门评估（只提取白名单标量；返回 status/reason/data/supporting） ---------


def _eval_ci_main(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_gate_self_id(obj, "ci-main")
    run_id = req_int(obj, "run_id", minimum=1)
    commit = req_commit(obj, "merge_commit")
    conclusion = req_choice(obj, "conclusion", CI_CONCLUSIONS)
    data = {"run_id": run_id, "merge_commit": commit, "conclusion": conclusion}
    if conclusion == "success":
        return (
            STATUS_PASS,
            f"CI run {run_id} @ {commit[:12]}… conclusion=success",
            data,
            [],
        )
    return (
        STATUS_BLOCKED,
        f"CI run {run_id} conclusion={conclusion}——远端门禁未绿，不得发布",
        data,
        [],
    )


def _eval_release_check(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_gate_self_id(obj, "release-check")
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
            data,
            [],
        )
    return STATUS_PASS, f"all_green（{passed}/{total} 项 pass）", data, []


def _eval_production_preflight(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_gate_self_id(obj, "production-preflight")
    phase = req_choice(obj, "phase", PHASES)
    summary = req_dict(obj, "summary")
    counts = {
        name: req_int(summary, name)
        for name in ("pass", "pending", "fail", "not_configured")
    }
    data = {"phase": phase, "summary": counts}
    if counts["fail"] > 0:
        return (
            STATUS_BLOCKED,
            f"preflight 存在 fail={counts['fail']}（phase={phase}）——先停下排查",
            data,
            [],
        )
    if phase != "post-migration":
        return (
            STATUS_PENDING,
            f"证据为 phase={phase}——发布就绪需要 post-migration 放行形态证据",
            data,
            [],
        )
    if counts["pending"] or counts["not_configured"]:
        return (
            STATUS_PENDING,
            (
                "preflight 仍有 pending="
                f"{counts['pending']} / not_configured={counts['not_configured']}"
                "（人工补齐后复跑 preflight 再导出证据）"
            ),
            data,
            [],
        )
    if counts["pass"] == 0:
        raise MalformedEvidence("summary.pass 为 0——不是任何合法 preflight 报告形态")
    return (
        STATUS_PASS,
        f"post-migration 放行形态（pass={counts['pass']}，无 pending/fail）",
        data,
        [],
    )


def _eval_backup_restore(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_gate_self_id(obj, "backup-restore")
    schema_version = req_choice(obj, "schema_version", ("aios-backup-v1",))
    manifest_sha = req_hex(obj, "manifest_sha256")
    created_at = req_iso(obj, "created_at")
    drill = req_dict(obj, "restore_drill")
    verified = req_bool(drill, "verified")
    inserted = req_int(drill, "inserted_rows")
    data = {
        "schema_version": schema_version,
        "manifest_sha256": manifest_sha,
        "created_at": created_at,
        "restore_verified": verified,
        "inserted_rows": inserted,
    }
    if not verified:
        return (
            STATUS_PENDING,
            (
                "备份 manifest 在但恢复演练未 verified——可恢复证据未闭合，"
                "人工完成 restore drill 后导出"
            ),
            data,
            [],
        )
    return (
        STATUS_PASS,
        f"aios-backup-v1 manifest + 恢复演练 verified（回灌 {inserted} 行）",
        data,
        [],
    )


def _eval_audit_chain_anchor(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_gate_self_id(obj, "audit-chain-anchor")
    chain = req_dict(obj, "chain")
    anchor = req_dict(obj, "anchor")
    worm = req_dict(obj, "worm")
    chain_valid = req_bool(chain, "valid")
    entries = req_int(chain, "entries")
    anchor_status = req_choice(anchor, "status", ("up-to-date", "valid", "invalid"))
    anchors = req_int(anchor, "anchors")
    worm_archived = req_bool(worm, "archived")
    data = {
        "chain_valid": chain_valid,
        "entries": entries,
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
                STATUS_TAMPERED,
                f"锚文件副本校验未通过（{len(problems)} 个问题）: {head}",
                data,
                supporting,
            )
        if len(anchors_parsed) != anchors:
            return (
                STATUS_TAMPERED,
                f"申报锚点数 anchors={anchors} 与副本实际 {len(anchors_parsed)} 行不一致",
                data,
                supporting,
            )
        companion_note = f"锚文件副本 {len(anchors_parsed)} 锚点校验自洽"
    if not chain_valid:
        return (
            STATUS_BLOCKED,
            f"审计链校验 invalid（申报 entries={entries}）——按 runbook 排查",
            data,
            supporting,
        )
    if anchor_status == "invalid":
        return (
            STATUS_BLOCKED,
            "锚定 verify-only invalid——锚文件与 DB 链交叉核对失败",
            data,
            supporting,
        )
    if anchor_status != "up-to-date":
        return (
            STATUS_PENDING,
            (
                "锚定落后（DB head 超前最后锚点）——人工 audit-chain-anchor --yes "
                "追加并归档 WORM 后复跑"
            ),
            data,
            supporting,
        )
    if not worm_archived:
        return (
            STATUS_PENDING,
            "锚文件未归档 WORM/对象锁/离线介质——本机锚文件只是操作见证",
            data,
            supporting,
        )
    return (
        STATUS_PASS,
        (
            f"链 valid（{entries} entries）+ 锚定 up-to-date（{anchors} 锚点）+ "
            f"WORM 已归档；{companion_note}"
        ),
        data,
        supporting,
    )


def _eval_legacy_papers(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_gate_self_id(obj, "legacy-papers")
    pending_count = req_int(obj, "pending_count")
    executed = batches_executed(req(obj, "batches"), label="batches")
    data = {"pending_count": pending_count, "batches_executed": executed}
    if pending_count > 0:
        return (
            STATUS_PENDING,
            (
                f"{pending_count} 张历史无归属卷待人工决策/分批执行——"
                "legacy-paper-report 复核后逐批 legacy-paper-migrate --yes（不输出生产 ID）"
            ),
            data,
            [],
        )
    return STATUS_PASS, f"无待归属历史卷（已执行 {executed} 批）", data, []


def _eval_draft_ownership(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_gate_self_id(obj, "draft-ownership")
    pending_count = req_int(obj, "pending_count")
    executed = batches_executed(req(obj, "batches"), label="batches")
    data = {"pending_count": pending_count, "batches_executed": executed}
    if pending_count > 0:
        return (
            STATUS_PENDING,
            (
                f"{pending_count} 条 NULL owner 草稿待人工决策/执行——"
                "draft-owner-report 复核后逐批 draft-owner-migrate --yes（不输出生产 ID）"
            ),
            data,
            [],
        )
    return STATUS_PASS, f"无待归属草稿（已执行 {executed} 批）", data, []


def _req_number(obj: Mapping[str, Any], key: str) -> float:
    """非布尔的 int/float 数值且 >= 0（gap/span 分钟数在报告里可为小数）。"""
    value = req(obj, key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise MalformedEvidence(f"字段 {key} 必须是 >= 0 的数值")
    return float(value)


def _eval_long_soak(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_gate_self_id(obj, "long-soak")
    # M14-73 fail-closed：证据必须是 soak_stability_audit v2 报告的逐字节副本
    # （顶层键全集与工具 build_report 逐键一致）——多余/缺失键即手工拼装或
    # 旧版形态，旁路拼装不收。
    if set(obj) != set(SOAK_REPORT_KEYS):
        diff = sorted(set(obj) ^ set(SOAK_REPORT_KEYS))
        raise MalformedEvidence(
            f"顶层键必须恰为 soak 审计 v2 报告的 {len(SOAK_REPORT_KEYS)} 键，"
            f"差异: {diff}"
        )
    schema_version = req_int(obj, "schema_version")
    if schema_version != SOAK_HISTORY_SCHEMA_VERSION:
        raise MalformedEvidence(
            f"schema_version 非 {SOAK_HISTORY_SCHEMA_VERSION}: {schema_version}"
        )
    audit_version = req_int(obj, "audit_schema_version")
    if audit_version != SOAK_AUDIT_SCHEMA_VERSION:
        raise MalformedEvidence(
            f"audit_schema_version 非 {SOAK_AUDIT_SCHEMA_VERSION}"
            f"（v1 报告不再被本门接受）: {audit_version}"
        )
    tool = req_str(obj, "tool")
    if tool != SOAK_AUDIT_TOOL:
        raise MalformedEvidence(f"tool 必须是 {SOAK_AUDIT_TOOL}: {tool}")
    # 策略漂移即拒绝：非本门策略（1440/15/20/500）下的结论不构成本门证据
    settings = req_dict(obj, "settings")
    expected_settings = {
        "window_minutes": SOAK_WINDOW_MINUTES,
        "expected_interval_minutes": SOAK_EXPECTED_INTERVAL_MINUTES,
        "max_gap_minutes": SOAK_MAX_GAP_MINUTES,
        "retention": SOAK_RETENTION,
    }
    if set(settings) != set(expected_settings):
        raise MalformedEvidence(
            f"settings 键必须恰为 {sorted(expected_settings)}，"
            f"实际: {sorted(settings)}"
        )
    for name, expected in expected_settings.items():
        actual = req_int(settings, name)
        if actual != expected:
            raise MalformedEvidence(
                f"settings.{name}={actual} 与本门策略值 {expected} 不符（策略漂移拒绝）"
            )
    input_meta = req_dict(obj, "input")
    if set(input_meta) != {"sha256", "byte_size"}:
        raise MalformedEvidence(
            f"input 键必须恰为 ['byte_size', 'sha256']，实际: {sorted(input_meta)}"
        )
    input_sha = req_hex(input_meta, "sha256")
    input_bytes = req_int(input_meta, "byte_size", minimum=1)
    row_count = req_int(obj, "row_count", minimum=1)
    analyzed = req_int(obj, "analyzed_row_count", minimum=1)
    omitted = req_int(obj, "omitted_older_count")
    # 行数不变式与工具 run_audit 一致：analyzed + omitted == row_count 且
    # omitted 恰为被 retention 截断的旧行数
    if analyzed + omitted != row_count:
        raise MalformedEvidence(
            f"行数不变式不成立: analyzed({analyzed}) + omitted({omitted})"
            f" != row_count({row_count})"
        )
    if omitted != max(0, row_count - SOAK_RETENTION):
        raise MalformedEvidence(
            f"omitted_older_count({omitted}) != max(0, row_count - "
            f"retention {SOAK_RETENTION})"
        )
    anchor_at = req_iso(obj, "anchor_collected_at")
    window_start = req_iso(obj, "window_start_collected_at")
    try:
        delta = datetime.fromisoformat(anchor_at) - datetime.fromisoformat(window_start)
    except (TypeError, ValueError):
        raise MalformedEvidence(
            "anchor/window_start 时间解析失败（naive 与 aware 混合）"
        ) from None
    if delta != timedelta(minutes=SOAK_WINDOW_MINUTES):
        raise MalformedEvidence(
            f"anchor 与 window_start 之差 {delta} 不等于 {SOAK_WINDOW_MINUTES} 分钟"
        )
    selected = req_int(obj, "selected_row_count")
    if selected > analyzed:
        raise MalformedEvidence(
            f"selected_row_count({selected}) > analyzed_row_count({analyzed})"
        )
    counts = req_dict(obj, "window_status_counts")
    if set(counts) != {"ok", "warn", "critical"}:
        raise MalformedEvidence(
            f"window_status_counts 键必须恰为 ['critical', 'ok', 'warn']，"
            f"实际: {sorted(counts)}"
        )
    ok_count = req_int(counts, "ok")
    warn_count = req_int(counts, "warn")
    critical_count = req_int(counts, "critical")
    if ok_count + warn_count + critical_count != selected:
        raise MalformedEvidence(
            f"状态计数之和 {ok_count + warn_count + critical_count}"
            f" != selected_row_count({selected})"
        )
    non_ok = req_int(obj, "window_non_ok_count")
    if not warn_count + critical_count <= non_ok <= selected:
        raise MalformedEvidence(
            f"window_non_ok_count={non_ok} 不在 [warn+critical="
            f"{warn_count + critical_count}, selected={selected}] 区间"
        )
    max_gap = _req_number(obj, "max_observed_gap_minutes")
    span = _req_number(obj, "selected_span_minutes")
    classification = req_choice(obj, "classification", ("pass", "pending", "blocked"))
    raw_reasons = req(obj, "reasons")
    if (
        not isinstance(raw_reasons, list)
        or any(not isinstance(item, str) or not item for item in raw_reasons)
        or len(set(raw_reasons)) != len(raw_reasons)
    ):
        raise MalformedEvidence("字段 reasons 必须是无重复的非空字符串列表")
    reasons = list(raw_reasons)
    unknown = [
        item for item in reasons if item not in SOAK_BLOCK_REASONS + SOAK_PENDING_REASONS
    ]
    if unknown:
        raise MalformedEvidence(
            f"reasons 含固定词汇外原因 {unknown}（唯一合法生产者是审计工具）"
        )
    min_required = SOAK_WINDOW_MINUTES // SOAK_EXPECTED_INTERVAL_MINUTES + 1
    data = {
        "audit_schema_version": audit_version,
        "input": {"sha256": input_sha, "byte_size": input_bytes},
        "row_count": row_count,
        "analyzed_row_count": analyzed,
        "omitted_older_count": omitted,
        "settings": dict(expected_settings),
        "anchor_collected_at": anchor_at,
        "selected_row_count": selected,
        "window_status_counts": {
            "ok": ok_count,
            "warn": warn_count,
            "critical": critical_count,
        },
        "window_non_ok_count": non_ok,
        "max_observed_gap_minutes": max_gap,
        "selected_span_minutes": span,
        "classification": classification,
        "reasons": reasons,
    }
    if classification == "pass":
        if reasons:
            raise MalformedEvidence(
                f"classification=pass 但 reasons={reasons}（自相矛盾）"
            )
        if selected < min_required:
            raise MalformedEvidence(
                f"pass 但 selected_row_count={selected} < 闭区间最少样本 "
                f"{min_required}（1440 分钟/15 分钟间隔）"
            )
        if span != float(SOAK_WINDOW_MINUTES):
            raise MalformedEvidence(
                f"pass 但 selected_span_minutes={span} 分钟 != 恰 "
                f"{SOAK_WINDOW_MINUTES} 分钟"
            )
        if max_gap > SOAK_MAX_GAP_MINUTES:
            raise MalformedEvidence(
                f"pass 但 max_observed_gap_minutes={max_gap} > {SOAK_MAX_GAP_MINUTES}"
            )
        if non_ok or warn_count or critical_count or ok_count != selected:
            raise MalformedEvidence(
                f"pass 但窗口非全 ok（ok={ok_count}/warn={warn_count}/"
                f"critical={critical_count}/non_ok={non_ok}/selected={selected}）"
            )
        return (
            STATUS_PASS,
            (
                f"真实 24h 稳定窗口 pass：{selected} 样本全 ok、最大间隔 "
                f"{max_gap:g} 分钟 <= {SOAK_MAX_GAP_MINUTES}、跨度恰 "
                f"{SOAK_WINDOW_MINUTES} 分钟（anchor={anchor_at}，输入 sha256="
                f"{input_sha[:12]}…，{row_count} 行中取末 {analyzed} 行分析）"
            ),
            data,
            [],
        )
    if classification == "pending":
        if not reasons or not set(reasons) <= set(SOAK_PENDING_REASONS):
            raise MalformedEvidence(
                f"classification=pending 但 reasons={reasons} 不构成合法 pending "
                f"原因集（只能是 {list(SOAK_PENDING_REASONS)}）"
            )
        # 工具判定顺序：非 ok / 间隔超限会先判 blocked，pending 只能建立在
        # 干净且间隔合规的窗口上
        if non_ok or warn_count or critical_count or ok_count != selected:
            raise MalformedEvidence(
                f"pending 但窗口含非 ok 样本（non_ok={non_ok}/warn={warn_count}/"
                f"critical={critical_count}）——应为 blocked（自相矛盾）"
            )
        if max_gap > SOAK_MAX_GAP_MINUTES:
            raise MalformedEvidence(
                f"pending 但 max_observed_gap_minutes={max_gap} > "
                f"{SOAK_MAX_GAP_MINUTES}——应为 blocked（自相矛盾）"
            )
        if "insufficient-sample-count" in reasons and selected >= min_required:
            raise MalformedEvidence(
                f"reasons 声称样本数不足但 selected={selected} >= {min_required}"
                "（自相矛盾）"
            )
        if (
            "insufficient-clean-coverage" in reasons
            and span >= float(SOAK_WINDOW_MINUTES)
        ):
            raise MalformedEvidence(
                f"reasons 声称干净覆盖不足但 selected_span_minutes={span} 分钟 "
                f"已 >= {SOAK_WINDOW_MINUTES}（自相矛盾）"
            )
        hints = {
            "insufficient-clean-coverage": (
                f"干净覆盖不足（span={span:g} 分钟 < {SOAK_WINDOW_MINUTES}）"
            ),
            "insufficient-sample-count": (
                f"样本数不足（selected={selected} < {min_required}）"
            ),
        }
        detail = "；".join(
            hints[item] for item in SOAK_PENDING_REASONS if item in reasons
        )
        return (
            STATUS_PENDING,
            (
                f"真实 24h 窗口未满（{detail}）——监测继续稳定运行补齐干净窗口后，"
                "离线重跑 soak_stability_audit 并将新报告逐字节复制为 "
                "long-soak.json 重新导出本门证据"
            ),
            data,
            [],
        )
    # classification == "blocked"
    if not reasons or not set(reasons) <= set(SOAK_BLOCK_REASONS):
        raise MalformedEvidence(
            f"classification=blocked 但 reasons={reasons} 不构成合法 blocked "
            f"原因集（只能是 {list(SOAK_BLOCK_REASONS)}）"
        )
    # 与工具 classify 的 if/elif 判定序一致：非 ok 优先于间隔超限，两类原因
    # 可同时为真但工具只发先命中的一条——因此这里校验单向蕴含而非双向等价
    if "non-ok-status-in-window" in reasons and non_ok <= 0:
        raise MalformedEvidence(
            "reasons 声称窗口内非 ok 状态但 window_non_ok_count=0（自相矛盾）"
        )
    if "excessive-gap-in-window" in reasons and max_gap <= SOAK_MAX_GAP_MINUTES:
        raise MalformedEvidence(
            f"reasons 声称间隔超限但 max_observed_gap_minutes={max_gap} <= "
            f"{SOAK_MAX_GAP_MINUTES}（自相矛盾）"
        )
    if non_ok > 0 and "non-ok-status-in-window" not in reasons:
        raise MalformedEvidence(
            f"window_non_ok_count={non_ok} > 0 但 reasons 未声明 "
            f"{SOAK_BLOCK_REASONS[0]}（自相矛盾）"
        )
    if (
        non_ok == 0
        and max_gap > SOAK_MAX_GAP_MINUTES
        and "excessive-gap-in-window" not in reasons
    ):
        raise MalformedEvidence(
            f"无非 ok 样本但 max_observed_gap_minutes={max_gap} > "
            f"{SOAK_MAX_GAP_MINUTES} 且 reasons 未声明 excessive-gap-in-window"
            "（自相矛盾）"
        )
    parts = []
    if "non-ok-status-in-window" in reasons:
        parts.append(
            f"窗口内 {non_ok} 个非 ok 样本（warn={warn_count}/"
            f"critical={critical_count}）"
        )
    if "excessive-gap-in-window" in reasons:
        parts.append(f"最大观测间隔 {max_gap:g} 分钟 > {SOAK_MAX_GAP_MINUTES}")
    return (
        STATUS_BLOCKED,
        (
            f"真实 24h 窗口存在稳定性问题: {'；'.join(parts)}——排查修复并重新获得"
            "满 24 小时干净窗口前不得发布（绝不从总历史跨度推导稳定）"
        ),
        data,
        [],
    )


def _eval_provider_smoke(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_gate_self_id(obj, "provider-smoke")
    # M14-70 聚合契约 fail-closed：topology 恰为 voice_mode 单键且为合法拓扑；
    # providers 键恰为三类槽位；每项键恰为 executed/result/evidence_step 且
    # evidence_step 与拓扑配对正确。缺 topology/step、多余键、拓扑与 step 配对
    # 错误一律 malformed——聚合器是本门证据的唯一合法生产者，旁路拼装不收。
    topology = req_dict(obj, "topology")
    if set(topology) != {"voice_mode"}:
        raise MalformedEvidence(
            f"topology 键必须恰为 ['voice_mode']（M14-70 聚合契约），"
            f"实际: {sorted(topology)}"
        )
    voice_mode = req_choice(topology, "voice_mode", SMOKE_VOICE_MODES)
    expected_steps = {
        "voice": "local-voice-smoke" if voice_mode == "local" else "cloud-voice-smoke",
        "search": "search-smoke",
        "llm": "llm-smoke",
    }
    providers = req_dict(obj, "providers")
    if set(providers) != set(SMOKE_PROVIDERS):
        raise MalformedEvidence(
            f"providers 键必须恰为 {list(SMOKE_PROVIDERS)}（M14-70 聚合契约），"
            f"实际: {sorted(providers)}"
        )
    data: dict[str, Any] = {
        "topology": {"voice_mode": voice_mode},
        "providers": {},
    }
    fails: list[str] = []
    not_run: list[str] = []
    for name in SMOKE_PROVIDERS:
        entry = providers.get(name)
        if not isinstance(entry, dict):
            raise MalformedEvidence(f"providers.{name} 缺失或不是对象")
        if set(entry) != set(SMOKE_ENTRY_KEYS):
            raise MalformedEvidence(
                f"providers.{name} 键必须恰为 {list(SMOKE_ENTRY_KEYS)}"
                f"（M14-70 聚合契约），实际: {sorted(entry)}"
            )
        executed = req_bool(entry, "executed")
        result = req_choice(entry, "result", ("pass", "fail", "not_executed"))
        step = req_str(entry, "evidence_step")
        if step != expected_steps[name]:
            raise MalformedEvidence(
                f"providers.{name}.evidence_step={step} 与 topology.voice_mode="
                f"{voice_mode} 拓扑不符（应为 {expected_steps[name]}）"
            )
        if executed and result == "not_executed":
            raise MalformedEvidence(
                f"providers.{name}.executed=true 但 result=not_executed（自相矛盾）"
            )
        if not executed and result != "not_executed":
            raise MalformedEvidence(
                f"providers.{name}.executed=false 但 result={result}（自相矛盾）"
            )
        data["providers"][name] = {
            "executed": executed,
            "result": result,
            "evidence_step": step,
        }
        if result == "fail":
            fails.append(name)
        elif result == "not_executed":
            not_run.append(name)
    # 措辞按拓扑选轨（M14-70）：local 只指向本地语音链路，不声称 cloud-voice
    # 被测；hybrid/cloud 仍指向 smoke_voice_cloud.sh 与部署 key。
    local_track = voice_mode == "local"
    voice_fail_hint = (
        "voice 排查本地语音链路（bash infra/smoke_voice_local.sh 本地 ASR/TTS"
        " 探针，无需云 key）"
        if local_track
        else "voice 排查部署 key（bash infra/smoke_voice_cloud.sh 重跑）"
    )
    voice_pending_hint = (
        "voice 需运维执行 bash infra/smoke_voice_local.sh（本地语音链路 ASR/TTS"
        " 探针，无需云 key）"
        if local_track
        else "voice 需运维执行 bash infra/smoke_voice_cloud.sh（部署 key + 真实"
        "短语音 WAV，key 不入库不入码）"
    )
    if fails:
        return (
            STATUS_BLOCKED,
            (
                f"provider 冒烟失败: {', '.join(fails)}——{voice_fail_hint}、"
                "search 排查真实端点（SEARCH_CLOUD_API_KEY 可选）、LLM 排查部署"
                " key；排查后重跑冒烟，并以 provider-smoke-export 逐 provider "
                f"重新导出单步证据、provider-smoke-aggregate --voice-mode "
                f"{voice_mode} 重新聚合本门证据（M11-16：机器导出，不接受手工"
                "拼装）"
            ),
            data,
            [],
        )
    if not_run:
        return (
            STATUS_PENDING,
            (
                f"未执行冒烟: {', '.join(not_run)}——{voice_pending_hint}；LLM 需"
                "运维以部署 key 执行（key 不入库不入码）；search 需打真实端点"
                "冒烟（SEARCH_CLOUD_API_KEY 可选）；冒烟通过后以 "
                "provider-smoke-export 逐 provider 导出单步证据、"
                f"provider-smoke-aggregate --voice-mode {voice_mode} 聚合为本门"
                "脱敏证据（M11-16：机器导出，不接受手工拼装）"
            ),
            data,
            [],
        )
    if local_track:
        pass_reason = (
            "voice 本地语音链路冒烟（拓扑 local，local-voice-smoke）+ LLM 部署"
            " key 冒烟 + search 真实端点冒烟（SEARCH_CLOUD_API_KEY 可选）全部"
            "通过（脱敏结果文件）"
        )
    else:
        pass_reason = (
            "voice 云链路部署 key 冒烟（bash infra/smoke_voice_cloud.sh，拓扑 "
            f"{voice_mode}，cloud-voice-smoke）+ LLM 部署 key 冒烟 + search 真实"
            "端点冒烟（SEARCH_CLOUD_API_KEY 可选）全部通过（脱敏结果文件）"
        )
    return STATUS_PASS, pass_reason, data, []


def _eval_turn_tls(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_gate_self_id(obj, "turn-tls")
    checks = req_dict(obj, "checks")
    data = {
        name: req_choice(checks, name, ("pass", "fail", "not_executed"))
        for name in TURN_CHECKS
    }
    fails = [name for name, verdict in data.items() if verdict == "fail"]
    not_run = [name for name, verdict in data.items() if verdict == "not_executed"]
    if fails:
        return (
            STATUS_BLOCKED,
            f"TURN/TLS 验证失败: {', '.join(fails)}——公网语音发布不得放行",
            data,
            [],
        )
    if not_run:
        return (
            STATUS_PENDING,
            (
                f"TURN/TLS 验证未执行: {', '.join(not_run)}（optional 门：本机/LAN "
                "发布可缺省，公网语音发布必须补齐）"
            ),
            data,
            [],
        )
    return STATUS_PASS, "STUN binding / TLS relay / 对称 NAT e2e 全部通过", data, []


def _eval_release_approval(
    obj: dict[str, Any], root: Path, sha: Mapping[str, str]
):
    _require_gate_self_id(obj, "release-approval")
    # M14-151 DRAFT 不可审批边界 fail-closed：审批记录携带任一
    # release-approval-draft 底稿保留元数据字段即结构不可信 => malformed
    # ——否则「底稿补齐 gate 自声明与全部人工字段后改名」可能 pass，
    # DRAFT 边界只剩命名自觉。合法人工审批不得携带这些字段（值是什么
    # 不重要）。
    reserved_hits = sorted(APPROVAL_DRAFT_RESERVED_FIELDS & set(obj))
    if reserved_hits:
        raise MalformedEvidence(
            "审批记录携带 DRAFT 底稿保留元数据字段 "
            + ", ".join(reserved_hits)
            + "——release-approval-draft 底稿不是审批记录，合法审批不得"
            "携带这些字段（请从底稿哈希出发从零组装，不要在底稿上补字段"
            "改名）"
        )
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
        raise MalformedEvidence("window.start/end 必须同为带时区或同为本地时间") from None
    if not window_ordered:
        raise MalformedEvidence("window.start 必须早于 window.end")
    req_str(obj, "rollback_plan")
    req_str(obj, "observation")
    approved_by = obj.get("approved_by")
    if approved_by is not None and (
        not isinstance(approved_by, str) or not approved_by.strip()
    ):
        raise MalformedEvidence("字段 approved_by 必须是非空字符串")
    gate_evidence = req_dict(obj, "gate_evidence")
    bindable = GATE_IDS - {"release-approval"}
    for gate_id, digest in gate_evidence.items():
        if gate_id not in bindable:
            raise MalformedEvidence(f"gate_evidence 引用未知门 {gate_id}")
        if not isinstance(digest, str) or not HEX64_RE.fullmatch(digest):
            raise MalformedEvidence(f"gate_evidence.{gate_id} 非 64 位小写十六进制")
    data = {
        "approved_at": approved_at,
        "window": {"start": start, "end": end},
        "covered_gates": sorted(gate_evidence),
        "hash_mismatches": [],
        "coverage_gaps": [],
        "approved_by_recorded": approved_by is not None,
        "identity_note": _IDENTITY_NOTE,
    }
    mismatches = sorted(
        gate_id
        for gate_id, digest in gate_evidence.items()
        if sha.get(gate_id) != digest
    )
    if mismatches:
        data["hash_mismatches"] = mismatches
        return (
            STATUS_TAMPERED,
            (
                f"审批绑定的证据哈希与当前文件不匹配: {mismatches}"
                "——证据在审批后被改动，需重新审批"
            ),
            data,
            [],
        )
    gaps = sorted(REQUIRED_GATE_IDS - {"release-approval"} - set(gate_evidence))
    if gaps:
        data["coverage_gaps"] = gaps
        return (
            STATUS_PENDING,
            f"审批未覆盖全部必需门: {gaps}——补齐对应证据并重新审批",
            data,
            [],
        )
    return (
        STATUS_PASS,
        (
            f"人工审批记录结构完整且哈希绑定匹配当前证据（approved_at={approved_at}，"
            f"覆盖 {len(gate_evidence)} 门；{_IDENTITY_NOTE}）"
        ),
        data,
        [],
    )


EVALUATORS: dict[str, Callable[..., tuple[str, str, dict, list]]] = {
    "ci-main": _eval_ci_main,
    "release-check": _eval_release_check,
    "production-preflight": _eval_production_preflight,
    "backup-restore": _eval_backup_restore,
    "audit-chain-anchor": _eval_audit_chain_anchor,
    "legacy-papers": _eval_legacy_papers,
    "draft-ownership": _eval_draft_ownership,
    "long-soak": _eval_long_soak,
    "provider-smoke": _eval_provider_smoke,
    "turn-tls": _eval_turn_tls,
    "release-approval": _eval_release_approval,
}


# --- 主流程 -------------------------------------------------------------------


def _unrecognized_files(root: Path) -> list[dict[str, Any]]:
    """目录内不属于任何门的文件/目录（含 sha256，帮助发现文件名笔误）。"""
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


def run_release_readiness(evidence_dir: str | Path) -> dict[str, Any]:
    """只读汇总全部门并组装 manifest（对数据库/网络零访问、零写入）。

    两遍执行：先装载（存在性/路径护栏/SHA-256/JSON 解析/敏感键扫描），后
    评估（release-approval 最后评估，但 GATES 顺序保证其哈希绑定校验拿到
    全部门的当前 sha256）。
    """
    root = check_evidence_dir(Path(evidence_dir))
    loaded: dict[str, dict[str, Any]] = {}
    sha_by_gate: dict[str, str] = {}
    for spec in GATES:
        path = root / spec.evidence_file
        state: dict[str, Any] = {"spec": spec, "obj": None, "problem": None}
        if os.path.lexists(path):
            check_regular_file(path)
            digest = sha256_file(path)
            sha_by_gate[spec.gate_id] = digest
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
            # 白名单提取只约束「被提取的字段」，挡不住证据正文里游离的敏感
            # 值；只有整体拒绝（malformed）才保证该门不会凭其余字段变 pass。
            state["obj"] = None if problem is not None else obj
            state["problem"] = problem
        loaded[spec.gate_id] = state

    gates: list[dict[str, Any]] = []
    for spec in GATES:
        state = loaded[spec.gate_id]
        record: dict[str, Any] = {
            "gate": spec.gate_id,
            "title": spec.title,
            "required": spec.required,
            "basis": spec.basis,
            "status": STATUS_MISSING,
            "reason": f"未提供证据文件 {spec.evidence_file}",
            "data": {},
            "evidence": (
                {"file": spec.evidence_file, "sha256": sha_by_gate.get(spec.gate_id)}
                if spec.gate_id in sha_by_gate
                else None
            ),
            "supporting": [],
        }
        if state["obj"] is None and state["problem"] is not None:
            record["status"] = STATUS_MALFORMED
            record["reason"] = f"{spec.evidence_file}: {state['problem']}"
        elif state["obj"] is not None:
            try:
                status, reason, data, supporting = EVALUATORS[spec.gate_id](
                    state["obj"], root, sha_by_gate
                )
            except MalformedEvidence as exc:
                status, reason, data, supporting = (
                    STATUS_MALFORMED,
                    f"{spec.evidence_file} 证据结构不符: {exc}",
                    {},
                    [],
                )
            record.update(
                {"status": status, "reason": reason, "data": data, "supporting": supporting}
            )
        gates.append(record)

    summary = {
        status: sum(1 for gate in gates if gate["status"] == status)
        for status in STATUS_ORDER
    }
    not_pass_required = [
        gate["gate"] for gate in gates if gate["required"] and gate["status"] != STATUS_PASS
    ]
    not_pass_optional = [
        gate["gate"]
        for gate in gates
        if not gate["required"] and gate["status"] != STATUS_PASS
    ]
    release_ready = not not_pass_required
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "tool": "release-readiness",
        "evidence_dir": str(root),
        "read_only": True,
        "no_execution_note": _NO_EXECUTION_NOTE,
        "identity_note": _IDENTITY_NOTE,
        "gates": gates,
        "unrecognized_files": _unrecognized_files(root),
        "summary": summary,
        "not_pass_required": not_pass_required,
        "not_pass_optional": not_pass_optional,
        "optional_scope_note": _OPTIONAL_SCOPE_NOTE,
        "release_ready": release_ready,
        "exit_code": 0 if release_ready else 1,
    }
    return scrub_sensitive(report)


def format_readiness_summary(report: Mapping[str, Any]) -> str:
    """人类可读摘要（无凭据/无生产 ID/无证据正文）。"""
    lines = [
        "发布准备与人工审批证据 manifest（release readiness，只读汇总）",
        f"生成时间: {report['generated_at']}",
        f"证据目录: {report['evidence_dir']}",
        _NO_EXECUTION_NOTE,
        "-" * 72,
    ]
    for gate in report["gates"]:
        optional = "" if gate["required"] else "（optional）"
        lines.append(f"[{gate['status'].upper():<9}] {gate['gate']}{optional}: {gate['title']}")
        lines.append(f"{'':<11} -> {gate['reason']}")
        if gate["supporting"]:
            names = ", ".join(item["file"] for item in gate["supporting"])
            lines.append(f"{'':<11} -> 附证据副本: {names}")
    if report["unrecognized_files"]:
        names = ", ".join(item["file"] for item in report["unrecognized_files"])
        lines.append(f"[注意] 证据目录内未识别的条目: {names}（可能是文件名笔误）")
    lines.append("-" * 72)
    counts = "  ".join(f"{k}={v}" for k, v in report["summary"].items())
    verdict = "RELEASE READY" if report["release_ready"] else "NOT READY"
    lines.append(f"RESULT: {verdict}  {counts}")
    if report["release_ready"]:
        lines.append(
            "全部必需门 pass，且人工审批记录与当前证据哈希完整匹配——"
            "manifest 仅汇总证据，不执行任何生产操作。"
        )
        optional = ", ".join(report["not_pass_optional"]) or "无"
        lines.append(
            f"注意: {_OPTIONAL_SCOPE_NOTE}（当前未 pass 的 optional 门: {optional}）。"
        )
    else:
        lines.append(
            "注意: pending/missing/malformed/tampered/blocked 都不是 pass——发布仍需"
            "人工决策/执行（未通过门: "
            f"{', '.join(report['not_pass_required']) or '无'}）；"
            "本工具绝不代为迁移、锚定、清理、发布或回滚。"
        )
    lines.append("退出码: 全部必需门 pass=0 / 任一必需门非 pass=1 / 输入或路径错误=2。")
    return "\n".join(lines)
