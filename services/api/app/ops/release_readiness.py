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
  manifest 整体再做敏感键抹除与 `://user:pass@` 凭据段抹除（复用
  production_preflight.redact_secrets）——不含完整 DB URL、密码、token、
  key、生产 paper/draft/用户 ID；
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

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ops.audit_chain_anchor import load_anchor_file
from app.ops.production_preflight import PHASES, redact_secrets

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

#: provider 冒烟覆盖的三类云 provider（缺任一 = malformed，不虚报覆盖面）。
#: 三类要求不同：voice/LLM 需运维部署 key；search 打真实端点
#: （SEARCH_CLOUD_API_KEY 可选——无鉴权 SearXNG 合法，M10-12）。
SMOKE_PROVIDERS = ("voice", "search", "llm")

#: 公网 TURN/TLS 验证的三项检查
TURN_CHECKS = ("stun_binding", "tls_relay", "symmetric_nat_e2e")

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

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
#: 敏感键模式（递归扫描证据与最终 manifest；命中即拒绝/抹除）。
#: 覆盖常见凭据字段变体（含 authorization/cookie/credential），宁可误拒
#: 同名业务字段也不放行——证据目录只应存放脱敏导出物。
_SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key"
    r"|authorization|auth[_-]?header|cookie|credential)",
    re.IGNORECASE,
)


class EvidenceInputError(Exception):
    """证据目录/路径问题（CLI 退出码 2：不是门结论，是输入不可用）。"""


class _Malformed(Exception):
    """gate 证据结构与声明的 schema 不符（内部信号，统一转 malformed 状态）。"""


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
        "provider-smoke",
        "云 voice/LLM 部署 key 冒烟 + search 真实端点冒烟（只收脱敏结果文件）",
        "provider-smoke.json",
        True,
        "voice/LLM 需运维以部署 key 冒烟通过；search 需打真实端点冒烟通过"
        "（SEARCH_CLOUD_API_KEY 可选，无鉴权端点可空）；证据文件不得携带任何 key",
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


# --- 路径护栏与证据装载 -------------------------------------------------------


def check_evidence_dir(path: Path) -> Path:
    """证据目录护栏：必须已存在、为真目录、不是 symlink（fail-closed）。"""
    if path.is_symlink():
        raise EvidenceInputError(f"证据目录是符号链接，拒绝使用: {path}")
    if not os.path.lexists(path):
        raise EvidenceInputError(f"证据目录不存在: {path}")
    if not path.is_dir():
        raise EvidenceInputError(f"证据目录不是目录: {path}")
    for entry in sorted(path.iterdir()):
        # 目录内任何 symlink（含 dangling）都拒绝：证据可能逃逸出调用方
        # 显式提供的目录，不猜测链接目标。
        if entry.is_symlink():
            raise EvidenceInputError(
                f"证据目录内存在符号链接，拒绝使用: {entry.name}"
            )
    return path


def _check_regular_file(path: Path) -> None:
    if path.is_symlink():
        raise EvidenceInputError(f"证据文件是符号链接，拒绝读取: {path.name}")
    if not path.is_file():
        raise EvidenceInputError(f"证据文件不是常规文件: {path.name}")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """读 JSON 对象；返回 (obj, None) 或 (None, problem)。OSError 原样上抛
    （CLI 映射 exit 2）。"""
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return None, "不是有效 UTF-8"
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"不是合法 JSON: {exc.msg}（第 {exc.lineno} 行）"
    if not isinstance(obj, dict):
        return None, "顶层不是 JSON 对象"
    return obj, None


def _find_sensitive_key(value: Any, prefix: str = "") -> str | None:
    """递归扫描敏感键，返回首个命中的键路径（如 providers.voice.api_key）。

    证据文件应全为脱敏导出物：命中即由调用方按 malformed 拒绝，值从不回显。
    """
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key)
            if _SENSITIVE_KEY_RE.search(name):
                return f"{prefix}{name}"
            hit = _find_sensitive_key(item, f"{prefix}{name}.")
            if hit:
                return hit
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hit = _find_sensitive_key(item, f"{prefix}{index}.")
            if hit:
                return hit
    return None


def _find_embedded_credential(value: Any, prefix: str = "") -> str | None:
    """递归扫描内嵌凭据的字符串值（`://user:pass@` 形态，复用 redact_secrets
    判定），返回首个命中的字段路径——证据文件本不应携带任何凭据。"""
    if isinstance(value, dict):
        for key, item in value.items():
            hit = _find_embedded_credential(item, f"{prefix}{key}.")
            if hit:
                return hit
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hit = _find_embedded_credential(item, f"{prefix}{index}.")
            if hit:
                return hit
    elif isinstance(value, str) and redact_secrets(value) != value:
        return prefix.rstrip(".") or "(root)"
    return None


def scrub_sensitive(value: Any) -> Any:
    """最终 manifest 的纵深防御：敏感键值整体替换、字符串过凭据抹除。

    正常情况下门的白名单提取已经保证输出零敏感；本函数兜底任何未来的
    提取面扩张（错误信息/路径字符串等仍统一抹 `://user:pass@`）。
    """
    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if _SENSITIVE_KEY_RE.search(str(key)) else scrub_sensitive(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub_sensitive(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


# --- 白名单字段提取（严格 schema，违例即 _Malformed） -------------------------


def _req(obj: Mapping[str, Any], key: str) -> Any:
    if key not in obj:
        raise _Malformed(f"缺字段 {key}")
    return obj[key]


def _req_str(obj: Mapping[str, Any], key: str, *, max_len: int = 512) -> str:
    value = _req(obj, key)
    if not isinstance(value, str) or not value.strip():
        raise _Malformed(f"字段 {key} 必须是非空字符串")
    if len(value) > max_len:
        raise _Malformed(f"字段 {key} 超长（>{max_len}）")
    return value


def _req_bool(obj: Mapping[str, Any], key: str) -> bool:
    value = _req(obj, key)
    if not isinstance(value, bool):
        raise _Malformed(f"字段 {key} 必须是布尔值")
    return value


def _req_int(obj: Mapping[str, Any], key: str, *, minimum: int = 0) -> int:
    value = _req(obj, key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _Malformed(f"字段 {key} 必须是 >= {minimum} 的整数")
    return value


def _req_dict(obj: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = _req(obj, key)
    if not isinstance(value, dict):
        raise _Malformed(f"字段 {key} 必须是对象")
    return value


def _req_choice(obj: Mapping[str, Any], key: str, choices: tuple[str, ...]) -> str:
    value = _req(obj, key)
    if value not in choices:
        raise _Malformed(f"字段 {key} 必须是 {list(choices)} 之一")
    return value


def _req_hex(obj: Mapping[str, Any], key: str) -> str:
    value = _req(obj, key)
    if not isinstance(value, str) or not _HEX64_RE.fullmatch(value):
        raise _Malformed(f"字段 {key} 必须是 64 位小写十六进制")
    return value


def _req_iso(obj: Mapping[str, Any], key: str) -> str:
    value = _req_str(obj, key, max_len=64)
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise _Malformed(f"字段 {key} 不是合法 ISO 8601 时间") from None
    return value


def _req_commit(obj: Mapping[str, Any], key: str) -> str:
    value = _req_str(obj, key, max_len=64)
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", value):
        raise _Malformed(f"字段 {key} 必须是 7-40 位十六进制 commit")
    return value.lower()


def _require_gate_self_id(obj: Mapping[str, Any], gate_id: str) -> None:
    """证据文件必须自声明 gate 且与文件名对应门一致（错位文件 fail-closed）。"""
    _req_choice(obj, "gate", (gate_id,))


# --- 各门评估（只提取白名单标量；返回 status/reason/data/supporting） ---------


def _eval_ci_main(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_gate_self_id(obj, "ci-main")
    run_id = _req_int(obj, "run_id", minimum=1)
    commit = _req_commit(obj, "merge_commit")
    conclusion = _req_choice(obj, "conclusion", CI_CONCLUSIONS)
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
    all_green = _req_bool(obj, "all_green")
    total = _req_int(obj, "total", minimum=1)
    passed = _req_int(obj, "passed")
    raw_failed = _req(obj, "failed_ids")
    if not isinstance(raw_failed, list) or any(
        not isinstance(item, str) or not item for item in raw_failed
    ):
        raise _Malformed("字段 failed_ids 必须是字符串列表")
    failed_ids = list(raw_failed)
    if passed > total:
        raise _Malformed("字段 passed 大于 total（计数自相矛盾）")
    if all_green and (passed != total or failed_ids):
        raise _Malformed("all_green=true 但计数/失败项非零（自相矛盾）")
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
    phase = _req_choice(obj, "phase", PHASES)
    summary = _req_dict(obj, "summary")
    counts = {
        name: _req_int(summary, name)
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
        raise _Malformed("summary.pass 为 0——不是任何合法 preflight 报告形态")
    return (
        STATUS_PASS,
        f"post-migration 放行形态（pass={counts['pass']}，无 pending/fail）",
        data,
        [],
    )


def _eval_backup_restore(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_gate_self_id(obj, "backup-restore")
    schema_version = _req_choice(obj, "schema_version", ("aios-backup-v1",))
    manifest_sha = _req_hex(obj, "manifest_sha256")
    created_at = _req_iso(obj, "created_at")
    drill = _req_dict(obj, "restore_drill")
    verified = _req_bool(drill, "verified")
    inserted = _req_int(drill, "inserted_rows")
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
    chain = _req_dict(obj, "chain")
    anchor = _req_dict(obj, "anchor")
    worm = _req_dict(obj, "worm")
    chain_valid = _req_bool(chain, "valid")
    entries = _req_int(chain, "entries")
    anchor_status = _req_choice(anchor, "status", ("up-to-date", "valid", "invalid"))
    anchors = _req_int(anchor, "anchors")
    worm_archived = _req_bool(worm, "archived")
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
        _check_regular_file(companion)
        supporting.append(
            {"file": ANCHOR_COMPANION_FILE, "sha256": _sha256_file(companion)}
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


def _batches_executed(raw: Any, *, label: str) -> int:
    if not isinstance(raw, list):
        raise _Malformed(f"字段 {label} 必须是列表")
    executed = 0
    for batch in raw:
        if not isinstance(batch, dict):
            raise _Malformed(f"{label} 元素必须是对象")
        if not isinstance(batch.get("executed"), bool):
            raise _Malformed(f"{label} 元素缺 executed 布尔字段")
        executed += 1 if batch["executed"] else 0
    return executed


def _eval_legacy_papers(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_gate_self_id(obj, "legacy-papers")
    pending_count = _req_int(obj, "pending_count")
    executed = _batches_executed(_req(obj, "batches"), label="batches")
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
    pending_count = _req_int(obj, "pending_count")
    executed = _batches_executed(_req(obj, "batches"), label="batches")
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


def _eval_provider_smoke(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_gate_self_id(obj, "provider-smoke")
    providers = _req_dict(obj, "providers")
    data: dict[str, dict[str, Any]] = {}
    fails: list[str] = []
    not_run: list[str] = []
    for name in SMOKE_PROVIDERS:
        entry = providers.get(name)
        if not isinstance(entry, dict):
            raise _Malformed(f"providers.{name} 缺失或不是对象")
        executed = _req_bool(entry, "executed")
        result = _req_choice(entry, "result", ("pass", "fail", "not_executed"))
        if executed and result == "not_executed":
            raise _Malformed(
                f"providers.{name}.executed=true 但 result=not_executed（自相矛盾）"
            )
        if not executed and result != "not_executed":
            raise _Malformed(
                f"providers.{name}.executed=false 但 result={result}（自相矛盾）"
            )
        data[name] = {"executed": executed, "result": result}
        if result == "fail":
            fails.append(name)
        elif result == "not_executed":
            not_run.append(name)
    if fails:
        return (
            STATUS_BLOCKED,
            (
                f"provider 冒烟失败: {', '.join(fails)}——voice/LLM 排查部署 key、"
                "search 排查真实端点（SEARCH_CLOUD_API_KEY 可选）后重跑冒烟并导出脱敏结果"
            ),
            data,
            [],
        )
    if not_run:
        return (
            STATUS_PENDING,
            (
                f"未执行冒烟: {', '.join(not_run)}——voice/LLM 需运维以部署 key 执行"
                "（key 不入库不入码）；search 需打真实端点冒烟"
                "（SEARCH_CLOUD_API_KEY 可选）；证据只收脱敏结果文件"
            ),
            data,
            [],
        )
    return (
        STATUS_PASS,
        (
            "voice/LLM 部署 key 冒烟 + search 真实端点冒烟"
            "（SEARCH_CLOUD_API_KEY 可选）全部通过（脱敏结果文件）"
        ),
        data,
        [],
    )


def _eval_turn_tls(obj: dict[str, Any], root: Path, sha: Mapping[str, str]):
    _require_gate_self_id(obj, "turn-tls")
    checks = _req_dict(obj, "checks")
    data = {
        name: _req_choice(checks, name, ("pass", "fail", "not_executed"))
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
    schema_version = _req_int(obj, "schema_version")
    if schema_version != 1:
        raise _Malformed(f"schema_version 非 1: {schema_version}")
    approved_at = _req_iso(obj, "approved_at")
    _req_str(obj, "note")  # 只验证非空；内容不回显（防审批说明内嵌敏感值）
    window = _req_dict(obj, "window")
    start = _req_iso(window, "start")
    end = _req_iso(window, "end")
    try:
        window_ordered = datetime.fromisoformat(start) < datetime.fromisoformat(end)
    except TypeError:
        # naive 与 aware 混合比较抛 TypeError：统一按结构不符拒绝
        raise _Malformed("window.start/end 必须同为带时区或同为本地时间") from None
    if not window_ordered:
        raise _Malformed("window.start 必须早于 window.end")
    _req_str(obj, "rollback_plan")
    _req_str(obj, "observation")
    approved_by = obj.get("approved_by")
    if approved_by is not None and (
        not isinstance(approved_by, str) or not approved_by.strip()
    ):
        raise _Malformed("字段 approved_by 必须是非空字符串")
    gate_evidence = _req_dict(obj, "gate_evidence")
    bindable = GATE_IDS - {"release-approval"}
    for gate_id, digest in gate_evidence.items():
        if gate_id not in bindable:
            raise _Malformed(f"gate_evidence 引用未知门 {gate_id}")
        if not isinstance(digest, str) or not _HEX64_RE.fullmatch(digest):
            raise _Malformed(f"gate_evidence.{gate_id} 非 64 位小写十六进制")
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
                {"file": entry.name, "kind": "file", "sha256": _sha256_file(entry)}
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
            _check_regular_file(path)
            digest = _sha256_file(path)
            sha_by_gate[spec.gate_id] = digest
            obj, problem = _load_json_object(path)
            if problem is None:
                sensitive = _find_sensitive_key(obj)
                if sensitive is not None:
                    problem = f"证据含敏感键 {sensitive}（只接受脱敏证据，值不回显）"
            if problem is None:
                embedded = _find_embedded_credential(obj)
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
            except _Malformed as exc:
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
