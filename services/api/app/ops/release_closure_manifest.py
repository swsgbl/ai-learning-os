"""M14-68 release-closure-manifest：生产收口 manifest（只读聚合器）。

定位：release-readiness（M10-11+M14-73 十一门）与 production-evidence-gap
（M11-18 四类缺口）各自回答一个侧面，本工具把「切换窗口前的收口状态」
收敛为一份确定性 JSON / Markdown closure manifest：git HEAD、证据目录逐
文件有界清单（相对名/字节/SHA-256）、两个既有聚合器的结论子集、诚实合取
的 production_ready、blockers 与七条精确下一步命令（占位符形态）。安全边界：

- **只消费既有评估结果**：readiness/gap 状态直接调用既有工具取得（其内部
  的路径护栏、装载、敏感键扫描、schema 校验与最终抹除原样生效），本模块
  不重复实现任何 gate 语义；证据清单只做有界枚举与哈希（复用共享层的
  check_evidence_dir / sha256_file）；
- 只读本地证据：不连接数据库、不调用 API、不访问网络、不读取任何环境
  变量（git HEAD 发现是唯一的子进程调用：固定 argv、cwd=仓库根、超时
  上限、无 shell）；对证据目录零写入；
- 不执行任何生产操作：不迁移、不治理、不锚定、不备份、不部署、不启停、
  不发布、不回滚——命令没有 --yes 执行形态，是纯汇总器；
- **production_ready 诚实合取**：readiness.release_ready 且 gap
  overall=pass 才为 true，否则恒 false；本清单不创建任何审批文件、不替代
  人工审批、不把 provider 失败转成 pass、不授权任何生产操作；
- 输出零敏感、零绝对本地路径：清单只含相对 posix 名/字节/哈希与白名单
  标量；next_steps 命令用 <evidence-dir>/<artifacts-dir> 占位符；序列化
  文本再过绝对路径纵深防御（证据目录/当前目录字面量 + 盘符/UNC/常见
  绝对前缀形态），命中即 fail-closed（exit 2 不产清单）；
- 路径护栏 fail-closed（exit 2）：--evidence-dir 护栏复用共享层；--
  output-json/--output-md 必须位于 gitignore 的 artifacts/temp、任何已
  存在路径组件是 symlink 即拒绝、已存在非常规文件拒绝、不得位于证据
  目录内或等于证据目录、两个输出不得指向同一路径——护栏先于任何证据
  读取；有界遍历超限（MAX_EVIDENCE_FILES）fail-closed；落盘由 CLI 共享
  原子写完成（逐文件原子，JSON 与 Markdown 跨文件非事务）。

退出码：production_ready=true=0；聚合未全 pass=1（清单照常返回，如实
记录缺口）；输入/路径/IO/git 发现问题=2（与既有 ops CLI 同口径）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ops.evidence_kit import check_evidence_dir, sha256_file
from app.ops.legacy_papers import is_safe_artifact_path
from app.ops.production_evidence_gap import build_production_evidence_gap
from app.ops.release_readiness import run_release_readiness

#: 证据自声明（manifest tool 字段）
TOOL_ID = "release-closure-manifest"
#: 证据目录有界遍历上限：超过即 fail-closed（防失控枚举/哈希放大，
#: 绝不输出不完整清单）
MAX_EVIDENCE_FILES = 500
#: git HEAD 发现命令（静态字符串，manifest 透出供审计；实际执行是固定
#: argv、无 shell、超时上限——本常量只是记录，不参与执行）
GIT_DISCOVERY_COMMAND = "git rev-parse HEAD"
_GIT_TIMEOUT_SECONDS = 10
#: 40 位（SHA-1）或 64 位（SHA-256）小写十六进制 commit SHA
_GIT_HEAD_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

_ISOLATION_NOTE = (
    "隔离只读梳理：本清单只消费 release-readiness 与 production-evidence-gap"
    " 对本地证据目录的只读评估结果，不代表生产验收，也不授权任何生产写入/发布"
)
_NO_EXECUTION_NOTE = (
    "只读本地证据：不连接数据库、不调用 API、不访问网络、不读取任何环境变量；"
    "不执行任何迁移、治理、锚定、备份、部署、启停、发布或回滚——命令没有 "
    "--yes 执行形态，是纯汇总器"
)
_PRODUCTION_READY_NOTE = (
    "production_ready 是 readiness.release_ready 与 production-evidence-gap "
    "overall=pass 的诚实合取；本清单不创建审批文件、不替代人工审批，也不授权"
    "任何生产操作——真实放行必须由审批人按既有 runbook 显式完成"
)

#: 七条精确下一步命令（静态模板；<evidence-dir>/<artifacts-dir> 占位符由
#: 运维替换，避免输出绝对本地路径；命令口径与 production-evidence-gap
#: CATEGORY_SPECS 与 tools/ops 既有工具表述一致）。顺序即输出顺序。
NEXT_STEPS: tuple[dict[str, str], ...] = (
    {
        "id": "cloud-voice-smoke",
        "command": (
            "python -m app.ops.cli provider-smoke-export cloud-voice "
            "--output <evidence-dir>/cloud-voice-smoke.json"
        ),
        "note": (
            "运维以部署 key 执行 bash infra/smoke_voice_cloud.sh 冒烟通过后"
            "导出单步脱敏证据（M11-16：机器导出，key 不入库不入码）"
        ),
    },
    {
        "id": "llm-smoke",
        "command": (
            "python -m app.ops.cli provider-smoke-export llm "
            "--output <evidence-dir>/llm-smoke.json"
        ),
        "note": "运维以部署 key 冒烟通过后导出单步脱敏证据（key 不入库不入码）",
    },
    {
        "id": "provider-smoke-aggregate",
        "command": (
            "python -m app.ops.cli provider-smoke-aggregate "
            "--search <evidence-dir>/search-smoke.json "
            "--cloud-voice <evidence-dir>/cloud-voice-smoke.json "
            "--llm <evidence-dir>/llm-smoke.json "
            "--output <evidence-dir>/provider-smoke.json"
        ),
        "note": (
            "三份单步证据齐备后聚合为 provider-smoke 门脱敏证据"
            "（M11-16：只收机器导出物，不接受手工拼装）"
        ),
    },
    {
        "id": "long-soak",
        "command": (
            "python tools/ops/soak_stability_audit.py "
            "--history <artifacts-dir>/history.jsonl "
            "--output-dir <artifacts-dir> && "
            "cp <artifacts-dir>/soak-audit-report.json <evidence-dir>/long-soak.json"
        ),
        "note": (
            "离线审计真实 24h 稳定窗口（M14-72 工具零时钟、确定性输出）；"
            "报告 soak-audit-report.json 必须逐字节复制/改名为 long-soak.json"
            "（不得手改、不得重新序列化），M14-73 起为必需门"
        ),
    },
    {
        "id": "cutover-approval",
        "command": (
            "python -m app.ops.cli cutover-evidence-pack approval-draft "
            "--evidence-dir <evidence-dir>"
        ),
        "note": (
            "只读生成审批哈希 DRAFT 底稿供审批人核对（DRAFT 不可直接审批，"
            "补齐字段改名只会 blocked）"
        ),
    },
    {
        "id": "release-approval",
        "command": (
            "python -m app.ops.cli release-readiness "
            "--evidence-dir <evidence-dir> --json"
        ),
        "note": (
            "先取各门证据 sha256 底稿，再由审批人从零手工组装 "
            "release-approval.json 并精确绑定哈希（agent 不代签、不代批）"
        ),
    },
    {
        "id": "production-evidence-gap",
        "command": (
            "python -m app.ops.cli production-evidence-gap "
            "--evidence-dir <evidence-dir> "
            "--output <artifacts-dir>/production-evidence-gap.json"
        ),
        "note": "复跑四类缺口聚合核对归零后，再行人工放行决策",
    },
)

#: 绝对本地路径纵深防御：盘符（C:/ C:\）与 UNC（\\\\server）形态
_DRIVE_RE = re.compile(r"[A-Za-z]:[\\/]")
#: 常见 POSIX 绝对路径前缀（命中即拒绝——证据目录只应含相对名，正常输出
#: 不含这些形态；宁误拒不漏放）
_ABS_POSIX_HINTS = ("/home/", "/Users/", "/tmp/", "/root/", "/var/", "/mnt/")


class ClosureManifestInputError(Exception):
    """输入/护栏问题（CLI exit 2：不聚合、不写输出）。"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


# --- git HEAD（显式或安全发现） ------------------------------------------------


def _repo_root() -> Path:
    """本模块所在仓库根（与 is_safe_artifact_path 同一推导口径）。"""
    return Path(__file__).resolve().parents[4]


def _discover_git_head() -> str:
    """安全发现 git HEAD：固定 argv、无 shell、超时上限；任何失败均
    fail-closed 抛 ClosureManifestInputError（提示改用 --git-head 显式提供）。"""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            shell=False,
            cwd=str(_repo_root()),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as cause:
        raise ClosureManifestInputError(
            f"git HEAD 发现失败（{GIT_DISCOVERY_COMMAND} 不可执行）: "
            f"{type(cause).__name__}——请用 --git-head 显式提供"
        ) from cause
    if proc.returncode != 0:
        raise ClosureManifestInputError(
            "git HEAD 发现失败（仓库根不是有效 git 仓库）——"
            "请用 --git-head 显式提供"
        )
    head = proc.stdout.strip().lower()
    if not _GIT_HEAD_RE.fullmatch(head):
        raise ClosureManifestInputError(
            "git HEAD 发现输出不是 40/64 位小写十六进制 commit SHA"
            "——请用 --git-head 显式提供"
        )
    return head


def _resolve_git_head(explicit: str | None) -> dict[str, str]:
    """显式优先（规范化为小写后校验）；缺省安全发现。"""
    if explicit is not None:
        candidate = explicit.strip().lower()
        if not _GIT_HEAD_RE.fullmatch(candidate):
            raise ClosureManifestInputError(
                "--git-head 必须是 40 或 64 位小写十六进制 commit SHA"
            )
        return {
            "head": candidate,
            "source": "explicit",
            "discovery_command": GIT_DISCOVERY_COMMAND,
        }
    return {
        "head": _discover_git_head(),
        "source": "discovered",
        "discovery_command": GIT_DISCOVERY_COMMAND,
    }


# --- 有界证据清单 --------------------------------------------------------------


def _walk_onerror(error: OSError) -> None:
    raise ClosureManifestInputError(
        f"证据目录枚举失败: {type(error).__name__}: {error}"
    ) from error


def _enumerate_evidence_files(root: Path) -> list[dict[str, Any]]:
    """有界、fail-closed 的证据清单：相对 posix 名/字节/SHA-256。

    followlinks=False；目录名排序保证遍历顺序确定；任何 symlink 文件/目录
    （含 dangling）拒绝；文件数超过 MAX_EVIDENCE_FILES 即拒绝——绝不输出
    不完整清单。
    """
    entries: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(
        root, followlinks=False, onerror=_walk_onerror
    ):
        dirnames.sort()
        base = Path(dirpath)
        for name in dirnames:
            if (base / name).is_symlink():
                raise ClosureManifestInputError(
                    f"证据目录内存在符号链接目录，拒绝使用: {name}"
                )
        for name in sorted(filenames):
            if len(entries) >= MAX_EVIDENCE_FILES:
                raise ClosureManifestInputError(
                    f"证据文件数超过上限 {MAX_EVIDENCE_FILES}"
                    "（fail-closed，不输出不完整清单）"
                )
            path = base / name
            if path.is_symlink():
                raise ClosureManifestInputError(
                    f"证据目录内存在符号链接文件，拒绝使用: {name}"
                )
            entries.append(
                {
                    "name": path.relative_to(root).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    entries.sort(key=lambda item: item["name"])
    return entries


# --- 输出护栏（先于任何证据内容读取） ------------------------------------------


def _reject_symlink_components(path: Path) -> None:
    """路径任何已存在组件（含自身）是 symlink 即拒绝（fail-closed）。

    先于 is_safe_artifact_path 执行：后者内部 resolve() 会跟随 symlink，
    链接目标落在护栏内时会被误放行（与 production-evidence-gap 同口径）。
    """
    chain: list[Path] = []
    current = path
    while current.name:
        chain.append(current)
        current = current.parent
    for item in reversed(chain):
        if item.is_symlink():
            raise ClosureManifestInputError(
                f"输出路径组件是符号链接，拒绝使用: {item}"
            )


def _check_output_path(output_path: str | Path, evidence_root: Path) -> None:
    """单输出护栏：artifacts/temp 内、非 symlink、不覆盖证据输入。

    resolved + os.path.normcase 归一比较——Windows 大小写/``..`` 折叠等
    等价书写形态不构成绕过。只做校验，落盘由 CLI 原子写完成。
    """
    target = Path(output_path)
    _reject_symlink_components(target)
    if not is_safe_artifact_path(target):
        raise ClosureManifestInputError(
            f"收口清单只能写入 gitignore 的 artifacts/ 或 temp/ 目录: {target}"
        )
    if target.exists() and not target.is_file():
        raise ClosureManifestInputError(f"输出路径已存在且不是常规文件: {target}")
    output_key = os.path.normcase(str(target.resolve()))
    root_key = os.path.normcase(str(evidence_root.resolve()))
    if output_key == root_key or output_key.startswith(root_key + os.sep):
        raise ClosureManifestInputError(
            "输出路径不得位于证据目录内或等于证据目录（拒绝覆盖证据输入）: "
            f"{output_path}"
        )


# --- 绝对路径纵深防御 ----------------------------------------------------------


def _assert_no_local_paths(text: str, root: Path) -> None:
    """序列化输出（JSON + Markdown）不得含绝对本地路径：证据目录/当前目录
    字面量、盘符/UNC 形态、常见 POSIX 绝对前缀——命中即 fail-closed。"""
    forbidden: list[str] = []
    for candidate in (root, root.resolve(), Path.cwd()):
        for form in (str(candidate), candidate.as_posix()):
            if form and form not in forbidden:
                forbidden.append(form)
    for item in forbidden:
        if item in text:
            raise ClosureManifestInputError(
                "输出含绝对本地路径（fail-closed 拒绝产出）"
            )
    if _DRIVE_RE.search(text):
        raise ClosureManifestInputError(
            "输出含盘符路径形态（fail-closed 拒绝产出）"
        )
    if "\\\\" in text:
        raise ClosureManifestInputError(
            "输出含 UNC 路径形态（fail-closed 拒绝产出）"
        )
    for hint in _ABS_POSIX_HINTS:
        if hint in text:
            raise ClosureManifestInputError(
                f"输出含常见绝对路径前缀 {hint}（fail-closed 拒绝产出）"
            )


# --- 主流程 -------------------------------------------------------------------


def build_release_closure_manifest(
    evidence_dir: str | Path,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
    git_head: str | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
) -> tuple[dict[str, Any], int]:
    """只读聚合收口 manifest（不写任何文件；落盘由 CLI 原子完成）。

    返回 (report, exit_code)：production_ready=true -> 0；聚合未全 pass ->
    1（清单照常返回，如实记录缺口）。证据目录护栏问题抛
    :class:`evidence_kit.EvidenceInputError` / OSError，输出护栏与 git
    发现问题抛 :class:`ClosureManifestInputError`（CLI 均 exit 2）。
    """
    root = Path(evidence_dir)
    # 输出护栏先于任何证据内容读取（覆盖证据输入的形态在读取前拒绝）。
    if output_json is not None:
        _check_output_path(output_json, root)
    if output_md is not None:
        _check_output_path(output_md, root)
    if (
        output_json is not None
        and output_md is not None
        and os.path.normcase(str(Path(output_json).resolve()))
        == os.path.normcase(str(Path(output_md).resolve()))
    ):
        raise ClosureManifestInputError(
            "--output-json 与 --output-md 不得指向同一路径"
        )
    root = check_evidence_dir(root)
    git_info = _resolve_git_head(git_head)
    files = _enumerate_evidence_files(root)
    # 复用既有聚合器：不重复实现任何 gate 语义（其内部的目录护栏、装载、
    # schema 校验与最终抹除全部原样生效）。两个子集都剔除各自的
    # evidence_dir 绝对路径键。
    readiness = run_release_readiness(root)
    gap_report, _gap_exit = build_production_evidence_gap(root)
    readiness_subset = {
        "tool": readiness["tool"],
        "release_ready": readiness["release_ready"],
        "summary": dict(readiness["summary"]),
        "not_pass_required": list(readiness["not_pass_required"]),
        "not_pass_optional": list(readiness["not_pass_optional"]),
        "exit_code": readiness["exit_code"],
    }
    gap_subset = {
        "tool": gap_report["tool"],
        "overall_status": gap_report["overall_status"],
        "categories": [
            {"category": item["category"], "status": item["status"]}
            for item in gap_report["categories"]
        ],
        "summary": dict(gap_report["summary"]),
        "exit_code": gap_report["exit_code"],
    }
    blockers: list[str] = []
    for gate in readiness_subset["not_pass_required"]:
        blockers.append(f"release-readiness 必需门未通过: {gate}")
    for item in gap_subset["categories"]:
        if item["status"] != "pass":
            blockers.append(
                f"production-evidence-gap 类别未通过: "
                f"{item['category']}={item['status']}"
            )
    # 诚实合取：任一聚合器未全 pass 即 false——不创建审批文件、不把
    # provider 失败转成 pass、本清单不构成放行授权。
    production_ready = bool(readiness_subset["release_ready"]) and (
        gap_subset["overall_status"] == "pass"
    )
    report: dict[str, Any] = {
        "generated_at": (clock or _utc_now)().isoformat(),
        "tool": TOOL_ID,
        "read_only": True,
        "isolation_note": _ISOLATION_NOTE,
        "no_execution_note": _NO_EXECUTION_NOTE,
        "git": git_info,
        "evidence": {
            "file_count": len(files),
            "total_bytes": sum(item["size_bytes"] for item in files),
            "files": files,
        },
        "readiness": readiness_subset,
        "gap": gap_subset,
        "blockers": blockers,
        "production_ready": production_ready,
        "production_ready_note": _PRODUCTION_READY_NOTE,
        "next_steps": [dict(item) for item in NEXT_STEPS],
        "exit_code": 0 if production_ready else 1,
    }
    # 纵深防御：JSON 与 Markdown 序列化文本都不得含绝对本地路径。
    _assert_no_local_paths(
        json.dumps(report, ensure_ascii=False)
        + "\n"
        + format_closure_markdown(report),
        root,
    )
    return report, report["exit_code"]


def _md_escape(name: str) -> str:
    """Markdown 表格单元格转义（管道符/换行）。"""
    return name.replace("|", "\\|").replace("\n", "\\n")


def format_closure_markdown(report: Mapping[str, Any]) -> str:
    """确定性 Markdown 渲染（只消费已脱路径的 report 字段，无凭据/无
    生产 ID/无绝对路径）。"""
    readiness = report["readiness"]
    gap = report["gap"]
    lines: list[str] = [
        "# 生产收口 manifest（release-closure-manifest，只读汇总）",
        "",
        f"- 生成时间: {report['generated_at']}",
        f"- 工具: {report['tool']}（只读；不执行任何生产操作）",
        (
            f"- git HEAD: {report['git']['head']}"
            f"（来源: {report['git']['source']}；"
            f"发现命令: {report['git']['discovery_command']}）"
        ),
        f"- {report['isolation_note']}",
        f"- {report['no_execution_note']}",
        "",
        "## 证据清单",
        (
            f"（{report['evidence']['file_count']} 个文件，"
            f"共 {report['evidence']['total_bytes']} 字节）"
        ),
        "",
        "| # | 文件（相对路径） | 字节 | SHA-256 |",
        "|---|---|---|---|",
    ]
    for index, item in enumerate(report["evidence"]["files"], start=1):
        lines.append(
            f"| {index} | {_md_escape(item['name'])} "
            f"| {item['size_bytes']} | {item['sha256']} |"
        )
    required = ", ".join(readiness["not_pass_required"]) or "无"
    optional = ", ".join(readiness["not_pass_optional"]) or "无"
    counts = "  ".join(f"{k}={v}" for k, v in readiness["summary"].items())
    lines += [
        "",
        "## 只读聚合状态",
        "",
        (
            "- release-readiness: "
            f"release_ready={str(readiness['release_ready']).lower()}"
        ),
        f"  - 未通过必需门: {required}",
        f"  - 未通过 optional 门: {optional}",
        f"  - 门状态计数: {counts}",
        f"- production-evidence-gap: overall={gap['overall_status']}",
    ]
    for item in gap["categories"]:
        lines.append(f"  - {item['category']}={item['status']}")
    lines += ["", f"## Blockers（{len(report['blockers'])} 项）", ""]
    if report["blockers"]:
        lines.extend(f"- {item}" for item in report["blockers"])
    else:
        lines.append("- 无（两个聚合器当前均无未过项——仍不构成放行授权）")
    lines += [
        "",
        "## 下一步命令（占位符 `<evidence-dir>` / `<artifacts-dir>` 由运维替换）",
        "",
    ]
    for index, step in enumerate(report["next_steps"], start=1):
        lines.append(f"{index}. `{step['command']}`")
        lines.append(f"   - {step['note']}")
    lines += [
        "",
        "## 结论",
        "",
        (
            "RESULT: production_ready="
            f"{str(report['production_ready']).lower()}"
            "（readiness.release_ready 与 gap overall=pass 的诚实合取）"
        ),
        "",
        report["production_ready_note"],
        "退出码: production_ready=true=0 / 聚合未全 pass=1 / 输入或路径与 IO 问题=2。",
        "",
    ]
    return "\n".join(lines)
