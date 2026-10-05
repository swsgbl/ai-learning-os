#!/usr/bin/env python
"""M14-238 公网移动证据编排计划器（plan-only gap report）。

只读、fail-closed 的**计划层**工具：读一份显式提供的 JSON 证据清单
（manifest v1，角色路径一律相对清单目录），对 M14-237 公网移动发布聚合门
所需的六个证据角色做**文件面**盘点——在场性、basename、字节数、SHA-256
——并产出脱敏 JSON+Markdown 缺口报告与一条可直接复制的**精确引用**
M14-237 回放命令。

边界（与 M14-237 严格分工）：

- **绝不解析子报告语义**：角色文件只按字节读取做哈希锚定，绝不
  ``json.loads``、绝不检查其 schema/tool/status/时间戳——那是 M14-237
  聚合门的职责。字节内容为任意垃圾（非 JSON）也只如实报告
  present/bytes/sha256，不产生 blocker。
- **零副作用**：零子进程、零网络、零移动真机桥接、零容器与生产动作、
  零环境变量与凭据读取。唯一写入是显式要求的报告输出（同目录 tmp +
  fsync + ``os.replace`` 原子写 + 写后重读校验；失败零残留）。
- **脱敏**：报告只携带 basename/bytes/SHA-256/角色名/固定词汇 code——
  绝不携带绝对本地路径、文件内容或凭据。

## Manifest 契约（v1）

顶层对象，键恰为下列集合（未知键/重复键/缺失必需键一律 invalid）：

| 键 | 必需 | 形态 |
| --- | --- | --- |
| ``schema`` | 是 | 恰 ``aios-public-mobile-evidence-manifest/1`` |
| ``restore_preflight`` | 是 | 相对路径 str |
| ``edge_preflight`` | 是 | 相对路径 str |
| ``device_smoke`` | 是 | 相对路径 str（指向 smoke 输出目录内 report.json） |
| ``cloudflare_preflight`` | 否 | 相对路径 str 或 ``null`` |
| ``release_evidence`` | 是 | 相对路径 str |
| ``attestation`` | 是 | 相对路径 str |
| ``freshness_hours`` | 否 | int（bool 拒绝）∈ [1, 720]；缺省 24 |

相对路径约束（违者 invalid）：非空 str、不含反斜杠/冒号/控制字符、
不以 ``/`` 或 ``~`` 开头、无 ``..``/``.``/空组件、长度 ≤ 200；两个角色
解析到同一目标也属 invalid（``duplicate-role-path``）。路径一律相对
manifest 所在目录解析。

## 退出码

- 0 = complete（manifest 合法且六个角色文件面全部就绪；报告已写出）；
- 1 = blockers（manifest 合法但存在缺口：文件缺失/symlink/reparse/
  目录/空文件/不可读；报告已写出，含精确 M14-237 回放命令）；
- 2 = invalid（manifest 契约违规或输出写出/重读校验失败——零报告残留）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

if __package__ in (None, ""):  # 直接脚本运行
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.android_release.path_safety import is_link_or_reparse
from tools.ops.monitoring_history import (
    HistoryError,
    RealStore,
    Store,
    reject_symlinked_path,
)

TOOL_NAME = "android_release_public_mobile_evidence_plan"
SCHEMA = "aios-public-mobile-evidence-plan/1"
MANIFEST_SCHEMA = "aios-public-mobile-evidence-manifest/1"
REPORT_JSON_NAME = "public-mobile-evidence-plan.json"
REPORT_MD_NAME = "public-mobile-evidence-plan.md"

EXIT_COMPLETE = 0
EXIT_BLOCKERS = 1
EXIT_INVALID = 2

DEFAULT_FRESHNESS_HOURS = 24
MIN_FRESHNESS_HOURS = 1
MAX_FRESHNESS_HOURS = 24 * 30

RESTORE_KEY = "restore_preflight"
EDGE_KEY = "edge_preflight"
SMOKE_KEY = "device_smoke"
CF_KEY = "cloudflare_preflight"
EVIDENCE_KEY = "release_evidence"
ATTEST_KEY = "attestation"

REQUIRED_ROLES: tuple[str, ...] = (
    RESTORE_KEY,
    EDGE_KEY,
    SMOKE_KEY,
    EVIDENCE_KEY,
    ATTEST_KEY,
)
ALL_ROLE_KEYS: tuple[str, ...] = (
    RESTORE_KEY,
    EDGE_KEY,
    SMOKE_KEY,
    CF_KEY,
    EVIDENCE_KEY,
    ATTEST_KEY,
)
ALLOWED_TOP_KEYS = frozenset(
    {"schema", "freshness_hours", *ALL_ROLE_KEYS}
)

MAX_ROLE_PATH_CHARS = 200
CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
GATE_SCRIPT = "tools/android_release/public_mobile_release_gate.py"
ISO_UTC_OUTPUT = "%Y-%m-%dT%H:%M:%SZ"

TAG = "[public-mobile-evidence-plan]"


class PlanInvalid(RuntimeError):
    """fail-closed 拒绝（固定词汇原因；零报告写入）。"""


# ---------------------------------------------------------------- 时间


def format_utc(moment: datetime) -> str:
    return moment.strftime(ISO_UTC_OUTPUT)


# ---------------------------------------------------------------- manifest


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``json.loads`` object_pairs_hook：重复键 → ``PlanInvalid``。"""
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise PlanInvalid(f"duplicate-key:{key}")
        seen[key] = value
    return seen


def load_manifest(store: Store, manifest_path: Path) -> tuple[dict[str, Any], bytes]:
    """读取并解析 manifest；文件面安全前置（symlink/reparse/非常规拒绝）。"""
    if not store.exists(manifest_path):
        raise PlanInvalid("manifest-missing")
    try:
        reject_symlinked_path(store, manifest_path)
    except HistoryError:
        raise PlanInvalid("manifest-symlink") from None
    if is_link_or_reparse(manifest_path):
        raise PlanInvalid("manifest-reparse")
    if not manifest_path.is_file():
        raise PlanInvalid("manifest-not-regular-file")
    raw = store.read_bytes(manifest_path)
    if not raw:
        raise PlanInvalid("manifest-empty")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise PlanInvalid("manifest-not-utf8") from None
    try:
        model = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except PlanInvalid:
        raise
    except ValueError:
        raise PlanInvalid("manifest-invalid-json") from None
    if not isinstance(model, dict):
        raise PlanInvalid("manifest-not-an-object")
    return model, raw


def validate_freshness(model: dict[str, Any]) -> int:
    """``freshness_hours`` 形态/范围校验（缺省 24；bool 一律拒绝）。"""
    if "freshness_hours" not in model:
        return DEFAULT_FRESHNESS_HOURS
    value = model["freshness_hours"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlanInvalid("freshness-not-integer")
    if not MIN_FRESHNESS_HOURS <= value <= MAX_FRESHNESS_HOURS:
        raise PlanInvalid("freshness-out-of-range")
    return value


def validate_role_path(role: str, value: Any) -> str:
    """角色路径字符串契约：非空相对 POSIX 风格、无遍历/绝对/保留形态。"""
    if not isinstance(value, str) or not value.strip():
        raise PlanInvalid(f"role-invalid:{role}")
    if len(value) > MAX_ROLE_PATH_CHARS:
        raise PlanInvalid(f"path-too-long:{role}")
    if "\\" in value or ":" in value:
        raise PlanInvalid(f"path-invalid:{role}")
    if CONTROL_CHARS_RE.search(value):
        raise PlanInvalid(f"path-invalid:{role}")
    if value.startswith(("/", "~")):
        raise PlanInvalid(f"path-absolute:{role}")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise PlanInvalid(f"path-traversal:{role}")
    return str(PurePosixPath(*parts))


def validate_manifest(model: dict[str, Any]) -> dict[str, str | None]:
    """形状校验：schema/未知键/必需角色/可选角色/freshness/路径契约。"""
    if model.get("schema") != MANIFEST_SCHEMA:
        raise PlanInvalid("schema-mismatch")
    unknown = sorted(set(model) - ALLOWED_TOP_KEYS)
    if unknown:
        raise PlanInvalid(f"unknown-key:{unknown[0]}")
    for role in REQUIRED_ROLES:
        if role not in model:
            raise PlanInvalid(f"role-missing:{role}")
    roles: dict[str, str | None] = {}
    for role in ALL_ROLE_KEYS:
        if role == CF_KEY and role not in model:
            roles[role] = None
            continue
        if role == CF_KEY and model[role] is None:
            roles[role] = None
            continue
        if model[role] is None:
            raise PlanInvalid(f"role-invalid:{role}")
        roles[role] = validate_role_path(role, model[role])
    resolved = {
        role: path for role, path in roles.items() if path is not None
    }
    by_target: dict[str, str] = {}
    for role, path in resolved.items():
        canonical = str(PurePosixPath(path))
        if canonical in by_target:
            raise PlanInvalid(
                f"duplicate-role-path:{by_target[canonical]}+{role}"
            )
        by_target[canonical] = role
    return roles


# ---------------------------------------------------------------- 文件面盘点


def inspect_role(
    store: Store, role: str, path: Path | None
) -> dict[str, Any]:
    """单角色文件面事实：present/basename/bytes/sha256/blockers。

    绝不解析文件内容语义——只读字节。可选角色（cloudflare_preflight）
    未提供 → present=false 且**零 blocker**——与 M14-237 一致，可选项
    缺席是合法计划形态，不算缺口；必需角色缺失才构成 ``missing-file``。
    """
    entry: dict[str, Any] = {
        "required": role != CF_KEY,
        "present": False,
        "basename": None,
        "bytes": None,
        "sha256": None,
        "blockers": [],
    }
    if path is None:
        return entry
    entry["basename"] = path.name
    if not store.exists(path):
        entry["blockers"] = [f"missing-file:{role}"]
        return entry
    try:
        reject_symlinked_path(store, path)
    except HistoryError:
        entry["blockers"] = [f"symlink-file:{role}"]
        return entry
    if is_link_or_reparse(path):
        entry["blockers"] = [f"reparse-file:{role}"]
        return entry
    if path.is_dir():
        entry["blockers"] = [f"directory-not-file:{role}"]
        return entry
    if not path.is_file():
        entry["blockers"] = [f"not-regular-file:{role}"]
        return entry
    try:
        raw = store.read_bytes(path)
    except OSError:
        entry["blockers"] = [f"unreadable-file:{role}"]
        return entry
    if not raw:
        entry["blockers"] = [f"empty-file:{role}"]
        return entry
    entry.update(
        {
            "present": True,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    )
    return entry


# ---------------------------------------------------------------- 回放命令


def build_replay_command(
    roles: dict[str, str | None], freshness_hours: int
) -> str:
    """精确引用的 M14-237 回放命令（路径 = manifest 相对 POSIX 形态）。"""
    parts = [
        "python",
        GATE_SCRIPT,
        f"--restore-preflight {shlex.quote(roles[RESTORE_KEY] or '')}",
        f"--edge-preflight {shlex.quote(roles[EDGE_KEY] or '')}",
        f"--device-smoke {shlex.quote(roles[SMOKE_KEY] or '')}",
    ]
    if roles[CF_KEY] is not None:
        parts.append(
            f"--cloudflare-preflight {shlex.quote(roles[CF_KEY] or '')}"
        )
    parts.extend(
        [
            f"--release-evidence {shlex.quote(roles[EVIDENCE_KEY] or '')}",
            f"--attestation {shlex.quote(roles[ATTEST_KEY] or '')}",
            "--output <output-dir>",
            f"--freshness-hours {freshness_hours}",
        ]
    )
    return " ".join(parts)


# ---------------------------------------------------------------- 报告


def build_report(
    *,
    manifest_name: str,
    manifest_bytes: int,
    manifest_sha256: str,
    roles: dict[str, dict[str, Any]],
    freshness_hours: int,
    replay_command: str,
    generated_at: datetime,
) -> dict[str, Any]:
    blockers = sorted(
        {
            code
            for entry in roles.values()
            for code in entry["blockers"]
        }
    )
    return {
        "schema": SCHEMA,
        "tool": TOOL_NAME,
        "generated_at": format_utc(generated_at),
        "manifest": {
            "basename": manifest_name,
            "bytes": manifest_bytes,
            "sha256": manifest_sha256,
        },
        "freshness_hours": freshness_hours,
        "roles": roles,
        "blockers": blockers,
        "complete": not blockers,
        "replay": {
            "slice": "M14-237",
            "command": replay_command,
            "working_directory": "directory containing the manifest",
            "note": (
                "paths are manifest-relative; --output is the operator's "
                "explicit choice (placeholder)"
            ),
        },
        "boundary": (
            "plan-only file-level gap report; child report semantics are "
            "never parsed and no readiness is asserted"
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Public mobile evidence plan (gap report)",
        "",
        f"- tool: `{report['tool']}` (schema `{report['schema']}`)",
        f"- generated at: `{report['generated_at']}`",
        (
            f"- manifest: `{report['manifest']['basename']}`, "
            f"{report['manifest']['bytes']} bytes, "
            f"SHA-256 `{report['manifest']['sha256']}`"
        ),
        f"- freshness hours: {report['freshness_hours']}",
        f"- **complete: {report['complete']}**",
        (
            "- boundary: plan-only file-level facts; child report semantics "
            "are never parsed; no readiness is asserted"
        ),
        "",
        "## Roles",
        "",
        "| role | required | present | file | bytes | sha256 | blockers |",
        "| --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for role in ALL_ROLE_KEYS:
        entry = report["roles"][role]
        lines.append(
            f"| {role} | {entry['required']} | {entry['present']} | "
            f"{entry['basename'] or '—'} | "
            f"{entry['bytes'] if entry['bytes'] is not None else '—'} | "
            f"{entry['sha256'] or '—'} | "
            f"{', '.join(entry['blockers']) or '—'} |"
        )
    lines.extend(["", "## Blockers", ""])
    if report["blockers"]:
        lines.extend(f"- `{blocker}`" for blocker in report["blockers"])
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## M14-237 replay command",
            "",
            "Run from the directory containing the manifest:",
            "",
            "```sh",
            report["replay"]["command"],
            "```",
            "",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------- 输出（原子写 + 重读校验）


def write_report(store: Store, output_dir: Path, report: dict[str, Any]) -> None:
    """原子写 JSON+MD 并重读校验；失败 → ``PlanInvalid``（零残留）。"""
    if is_link_or_reparse(output_dir):
        raise PlanInvalid("reparse-output-dir")
    try:
        reject_symlinked_path(store, output_dir)
    except HistoryError:
        raise PlanInvalid("symlink-output-dir") from None
    store.mkdirs(output_dir)
    if is_link_or_reparse(output_dir):
        raise PlanInvalid("reparse-output-dir")
    json_text = json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    md_text = render_markdown(report)
    json_path = output_dir / REPORT_JSON_NAME
    md_path = output_dir / REPORT_MD_NAME
    written: list[Path] = []
    try:
        store.write_atomic(json_path, json_text)
        written.append(json_path)
        store.write_atomic(md_path, md_text)
        written.append(md_path)
    except OSError as cause:
        _discard(written)
        raise PlanInvalid(f"output-write-failed:{cause.__class__.__name__}") from None
    for path, expected in ((json_path, json_text), (md_path, md_text)):
        try:
            actual = store.read_bytes(path).decode("utf-8")
        except (OSError, UnicodeDecodeError):
            _discard(written)
            raise PlanInvalid(f"output-verify-failed:{path.name}") from None
        if actual != expected:
            _discard(written)
            raise PlanInvalid(f"output-verify-failed:{path.name}")


def _discard(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- 主流程


def run_plan(
    store: Store,
    *,
    manifest: Path,
    output_dir: Path,
    now: datetime,
) -> tuple[dict[str, Any], int]:
    """执行计划器；返回 (report, exit_code)。拒绝场景抛 ``PlanInvalid``。

    ``now`` 是评估时刻（报告顶层 ``generated_at`` 的语义）：CLI 传入真实
    当前 UTC；契约测试注入固定 UTC。
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise PlanInvalid("now-not-tz-aware")
    now = now.astimezone(timezone.utc)
    model, raw = load_manifest(store, manifest)
    freshness_hours = validate_freshness(model)
    role_values = validate_manifest(model)
    base = manifest.parent
    roles: dict[str, dict[str, Any]] = {
        role: inspect_role(
            store, role, (base / value) if value is not None else None
        )
        for role, value in role_values.items()
    }
    report = build_report(
        manifest_name=manifest.name,
        manifest_bytes=len(raw),
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
        roles=roles,
        freshness_hours=freshness_hours,
        replay_command=build_replay_command(role_values, freshness_hours),
        generated_at=now,
    )
    write_report(store, output_dir, report)
    exit_code = EXIT_COMPLETE if report["complete"] else EXIT_BLOCKERS
    return report, exit_code


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="public_mobile_evidence_plan",
        description=(
            "plan-only 缺口报告：对 M14-237 公网移动发布聚合门所需的六个"
            "证据角色做文件面盘点（present/basename/bytes/SHA-256），产出"
            "脱敏 JSON+Markdown 报告与精确引用的 M14-237 回放命令。"
            "不解析子报告语义；零子进程、零网络、零真机/环境/凭据访问。"
        ),
    )
    parser.add_argument("--manifest", required=True, type=Path,
                        help="证据清单 JSON（v1，角色路径相对清单目录）")
    parser.add_argument("--output", required=True, type=Path,
                        help="报告输出目录（原子写 JSON+Markdown；零其它写入）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = RealStore()
    try:
        report, exit_code = run_plan(
            store,
            manifest=args.manifest,
            output_dir=args.output,
            now=datetime.now(timezone.utc),
        )
    except PlanInvalid as cause:
        print(f"{TAG} INVALID: {cause}", file=sys.stderr)
        return EXIT_INVALID
    marker = "COMPLETE" if exit_code == EXIT_COMPLETE else "BLOCKERS"
    print(
        f"{TAG} {marker}: complete={report['complete']} "
        f"blockers={len(report['blockers'])}"
    )
    for blocker in report["blockers"]:
        print(f"{TAG} blocker: {blocker}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
