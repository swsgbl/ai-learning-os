"""M11-20 evidence-inventory：生产证据目录只读索引器。

定位：production-evidence-gap（M11-18）要求一个完整 cutover evidence 目录
才能给缺口结论，而本机现状是证据分散在多个目录（隔离 fixture/模板脚手架、
preflight/backup 碎片、历史汇总）。本工具回答「这些目录里到底有什么、
能否适用生产、缺什么」——对一或多个调用方显式提供的本地目录做**只读
inventory**：逐文件列出 name/相对路径/size/SHA-256/mtime/分类/适用性，
对已知证据文件名做极小白名单 JSON 元数据提取，并按目录与总体汇总
（13 步 root 顶层精确文件名覆盖/缺失、重复 sha256、可解析计数、
fixture/template/unknown/unverified 分类计数）。只做盘点，**不做**
cutover-rehearsal 的 13 步放行判定（coverage 只看 root 顶层是否存在
精确证据文件名——与 cutover-rehearsal 只消费 ``root/<evidence_file>``
的口径一致，嵌套同名文件列入 inventory 但不推进覆盖；与
pass/pending/blocked 结论无关——完整时间线判定仍以 cutover-rehearsal
manifest 为准）。

适用性诚实原则：模板（``*.template.json`` / ``*.jsonl.template``）只能是
``not_applicable``；目录 README 明确包含「隔离 fixture」声明时该目录非模板
条目标 ``isolation_fixture``（目录 summary 说明依据）；其余一律
``unverified``——本工具**绝不允许**把任何条目标为 production_verified，
生产适用性必须由运维按 runbook 人工判定。

安全边界（docs/DEVELOPMENT.md「生产证据目录只读索引器」节同步维护）：

- 只读本地文件系统：不连接数据库、不调用 API、不访问网络、不读取任何
  环境变量（``os.environ`` 零引用）；不运行任何 provider 冒烟、不执行任何
  迁移/治理/锚定/备份/部署/启停/发布/回滚——命令没有 ``--yes`` 执行形态；
  对输入目录零写入（文件字节与 mtime 保持不变）；
- **零内容回显与显示名脱敏**：为避免大文件内容泄漏，只按文件字节计算
  SHA-256/size/mtime，不回显文件正文；输出不使用绝对路径（目录以调用
  顺序编号 ``#N`` 标识）；**文件显示名脱敏**——文件名/相对路径本身可能
  携带业务 ID、key/token/password 等敏感值，只有 root 顶层的「已知精确
  安全文件名」白名单成员（13 步证据文件、锚副本、README.md）原样显示，
  未知文件名与任何嵌套路径（父目录名未证明安全）一律 ``[redacted]``，
  ``duplicate_sha256_groups`` 的 paths 用同一安全显示口径；内部排序、
  hash、coverage 判断可用真实相对路径，但进入 manifest/summary 的显示
  字段必须是安全值；JSON 只解析为 object 并提取极小白名单
  （declared step/phase/result/schema_version 等仅当值命中受控枚举或安全
  标量模式才透出，否则为 null）——不输出解析错误文本、业务 ID、标题、
  正文、URL、endpoint、key/token；未知/非 JSON 文件只列元数据不解析内容；
  ``database.json`` 这类超过解析上限的大文件只做哈希元数据
  （parse_status=skipped_size）；
- 最终 manifest 整体过 ``evidence_kit.scrub_sensitive`` 纵深防御（白名单
  键名不含敏感模式，字符串凭据段 ``://user:pass@`` 统一抹除）；
- 路径护栏 fail-closed（exit 2）：每个 ``--evidence-dir`` 必须已存在、为真
  目录、不是 symlink，**递归枚举时遇到任何 symlink 文件/目录（含 dangling）
  一律拒绝、绝不跟随**，**目录枚举本身失败（不可读/访问被拒绝的子目录）
  同样 fail-closed**（``os.walk`` 提供 ``onerror``，枚举错误转
  :class:`EvidenceInventoryInputError`——绝不静默跳过、绝不输出不完整
  清单）；同一目录不得重复传入、目录间不得互相嵌套（防重复
  计数歧义）；``--output`` 必须位于 gitignore 的 artifacts/temp（复用
  ``is_safe_artifact_path``）、任何已存在路径组件是 symlink 即拒绝、已存在
  且不是常规文件拒绝、**不得位于任一证据目录内或等于任一目录根**（拒绝
  覆盖证据输入，``resolve`` + ``os.path.normcase`` 归一比较，Windows
  大小写/``..`` 折叠不构成绕过）——输出护栏先于任何目录枚举执行；落盘由
  CLI 共享原子写完成（同目录临时文件 + fsync + os.replace，失败旧文件
  字节原样、无 partial）。

退出码：成功盘点=0（清单是事实记录，inventory 没有失败语义——缺什么是
清单内容，不是命令失败）；输入/路径或 IO 问题=2。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ops.cutover_rehearsal import ANCHOR_COMPANION_FILE, STEP_IDS, STEPS
from app.ops.evidence_kit import find_sensitive_key, scrub_sensitive
from app.ops.legacy_papers import is_safe_artifact_path

TOOL_ID = "evidence-inventory"

#: 文件分类（白名单枚举；分类只是提示性标签，不构成证据有效性判定）
CLASS_CUTOVER_STEP = "cutover_step_evidence"
CLASS_CUTOVER_TEMPLATE = "cutover_template"
CLASS_PRODUCTION_PREFLIGHT = "production_preflight"
CLASS_BACKUP_MANIFEST = "backup_manifest"
CLASS_AUDIT_ANCHOR_COPY = "audit_anchor_copy"
CLASS_OTHER = "other"
CLASSIFICATIONS = (
    CLASS_CUTOVER_STEP,
    CLASS_CUTOVER_TEMPLATE,
    CLASS_PRODUCTION_PREFLIGHT,
    CLASS_BACKUP_MANIFEST,
    CLASS_AUDIT_ANCHOR_COPY,
    CLASS_OTHER,
)

#: 适用性（诚实枚举：本工具不存在 production_verified 这个值）
APPLICABILITY_ISOLATION_FIXTURE = "isolation_fixture"
APPLICABILITY_NOT_APPLICABLE = "not_applicable"
APPLICABILITY_UNVERIFIED = "unverified"
APPLICABILITY_VALUES = (
    APPLICABILITY_ISOLATION_FIXTURE,
    APPLICABILITY_NOT_APPLICABLE,
    APPLICABILITY_UNVERIFIED,
)

#: JSON 解析状态（受控枚举；解析失败不输出错误文本）
PARSE_PARSED = "parsed"
PARSE_UNPARSED = "unparsed"
PARSE_SKIPPED_SIZE = "skipped_size"
PARSE_NOT_ATTEMPTED = "not_attempted"
PARSE_STATUSES = (
    PARSE_PARSED,
    PARSE_UNPARSED,
    PARSE_SKIPPED_SIZE,
    PARSE_NOT_ATTEMPTED,
)

#: cutover-evidence-pack（M10-16）的模板命名约定：模板不是证据
TEMPLATE_SUFFIXES = (".template.json", ".jsonl.template")
#: production-preflight 导出物命名（如 production-preflight.json、
#: production-preflight-pre-migration.json；13 步精确名不带该前缀，无冲突）
PREFLIGHT_NAME_PREFIX = "production-preflight"
BACKUP_MANIFEST_NAME = "manifest.json"
README_NAME = "README.md"
#: 目录 README 的隔离 fixture 声明 marker（中文口径为准，英文变体兜底）
FIXTURE_DECLARATION_MARKERS = ("隔离 fixture", "isolation fixture")

#: 超过该大小的文件不解析内容（database.json 这类大文件只做哈希元数据）
_MAX_PARSE_BYTES = 1_000_000
_MAX_README_BYTES = 1_000_000
_HASH_CHUNK_BYTES = 65536

#: 13 步证据文件名 -> step id（复用 cutover-rehearsal 清单，不重新定义）
_STEP_BY_EVIDENCE_FILE = {spec.evidence_file: spec.step_id for spec in STEPS}

#: 输出显示名白名单：只有这些「已知精确安全文件名」在 root 顶层时原样
#: 显示；未知文件名与任何嵌套路径（父目录名未证明安全）一律 redacted。
#: 白名单 = 13 步证据文件 + 锚副本 + README（fixture 声明依据）——模板/
#: preflight 等按模式匹配的分类名，剩余部分不受控，不进白名单。
SAFE_DISPLAY_FILENAMES = frozenset(_STEP_BY_EVIDENCE_FILE) | {
    ANCHOR_COMPANION_FILE,
    README_NAME,
}
#: 非白名单文件名/嵌套路径的统一显示占位符（不携带任何真实名成分）
REDACTED_PLACEHOLDER = "[redacted]"

#: declared 白名单：仅当值命中受控枚举/布尔/ISO 时间/安全 schema 模式才透出
_DECLARED_ENUM_FIELDS: dict[str, frozenset[str]] = {
    "step": frozenset(STEP_IDS),
    "phase": frozenset(("pre-migration", "post-migration")),
    "result": frozenset(("pass", "fail", "not_executed")),
}
_DECLARED_BOOL_FIELDS = ("valid", "executed", "all_green", "verified")
_DECLARED_TIME_FIELDS = (
    "generated_at",
    "created_at",
    "approved_at",
    "started_at",
    "completed_at",
)
_SCHEMA_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")

_ISOLATION_NOTE = (
    "隔离只读索引：本工具只盘点目录内容与元数据，不代表生产验收，也绝不"
    "把任何条目自动标为 production_verified——生产适用性必须由运维按 runbook"
    " 人工判定"
)
_NO_EXECUTION_NOTE = (
    "只读本地文件系统：不连接数据库、不调用 API、不访问网络、不读取任何"
    "环境变量、不运行任何 provider 冒烟；不执行任何迁移、治理、锚定、备份、"
    "部署、启停、发布或回滚——命令没有 --yes 执行形态"
)
_SCOPE_NOTE = (
    "本清单是 inventory 不是放行判定：cutover steps 覆盖只看 root 顶层是否"
    "存在精确证据文件名（嵌套同名文件不推进覆盖；与 pass/pending/blocked"
    " 结论无关），13 步放行判定仍以 cutover-rehearsal manifest 为准"
)
_PRODUCTION_READY_NOTE = (
    "production_ready 恒为 false：本输出是目录盘点清单，不构成生产放行、"
    "不构成 production readiness，也不授权任何生产操作"
)


class EvidenceInventoryInputError(Exception):
    """输入/护栏问题（CLI exit 2：不枚举、不写输出）。"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


# --- 输入与输出护栏（先于任何目录枚举） ------------------------------------------


def _check_roots(evidence_dirs: list[str | Path]) -> list[Path]:
    """每个 evidence root 必须已存在、为真目录、非 symlink。

    同一目录不得重复传入、目录间不得互相嵌套（resolve + normcase 归一
    比较——Windows 大小写/分隔符书写形态差异不构成绕过）。
    """
    if not evidence_dirs:
        raise EvidenceInventoryInputError("至少需要一个 --evidence-dir")
    roots: list[Path] = []
    keys: list[str] = []
    for index, raw in enumerate(evidence_dirs, start=1):
        root = Path(raw)
        if root.is_symlink():
            raise EvidenceInventoryInputError(
                f"证据目录 #{index} 是符号链接，拒绝使用（绝不跟随）"
            )
        if not os.path.lexists(root):
            raise EvidenceInventoryInputError(f"证据目录 #{index} 不存在")
        if not root.is_dir():
            raise EvidenceInventoryInputError(f"证据目录 #{index} 不是目录")
        key = os.path.normcase(str(root.resolve()))
        if key in keys:
            raise EvidenceInventoryInputError(
                f"证据目录 #{index} 与此前目录重复（拒绝重复计数）"
            )
        for seen in keys:
            nested = key.startswith(seen + os.sep) or seen.startswith(key + os.sep)
            if nested:
                raise EvidenceInventoryInputError(
                    f"证据目录 #{index} 与此前目录互相嵌套（拒绝重复计数）"
                )
        roots.append(root)
        keys.append(key)
    return roots


def _reject_output_symlink_components(path: Path) -> None:
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
            raise EvidenceInventoryInputError(
                f"输出路径组件是符号链接，拒绝使用: {item.name}"
            )


def _check_output_path(output_path: str | Path, roots: list[Path]) -> None:
    """输出护栏：artifacts/temp 内、非 symlink、不覆盖任何证据目录。

    输出 resolved 路径不得等于任何证据目录根或位于其内（``resolve`` +
    ``os.path.normcase`` 归一比较）。只做校验，落盘由 CLI 原子写完成。
    """
    target = Path(output_path)
    _reject_output_symlink_components(target)
    if not is_safe_artifact_path(target):
        raise EvidenceInventoryInputError(
            f"盘点清单只能写入 gitignore 的 artifacts/ 或 temp/ 目录: {target}"
        )
    if target.exists() and not target.is_file():
        raise EvidenceInventoryInputError(
            f"输出路径已存在且不是常规文件: {target.name}"
        )
    output_key = os.path.normcase(str(target.resolve()))
    for index, root in enumerate(roots, start=1):
        root_key = os.path.normcase(str(root.resolve()))
        if output_key == root_key or output_key.startswith(root_key + os.sep):
            raise EvidenceInventoryInputError(
                f"输出路径不得位于证据目录 #{index} 内或等于该目录"
                "（拒绝覆盖证据输入）"
            )


# --- 文件元数据（只哈希，不回显内容） --------------------------------------------


def _hash_file_chunked(path: Path) -> str:
    """分块 SHA-256（evidence_kit.sha256_file 是整读实现，对备份级大文件
    不安全；分块内存占用恒定，结果与逐字节哈希完全一致）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mtime_utc(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()


def _classify(name: str) -> str:
    """已知证据文件名/类型的白名单分类（其余归 other，仍列出元数据）。"""
    if name.endswith(TEMPLATE_SUFFIXES):
        return CLASS_CUTOVER_TEMPLATE
    if name == ANCHOR_COMPANION_FILE:
        return CLASS_AUDIT_ANCHOR_COPY
    if name in _STEP_BY_EVIDENCE_FILE:
        return CLASS_CUTOVER_STEP
    if name.startswith(PREFLIGHT_NAME_PREFIX) and name.endswith(".json"):
        return CLASS_PRODUCTION_PREFLIGHT
    if name == BACKUP_MANIFEST_NAME:
        return CLASS_BACKUP_MANIFEST
    return CLASS_OTHER


def _fixture_declared_by_readme(root: Path) -> bool:
    """root 顶层 README.md 明确包含「隔离 fixture」声明才认定（超限/缺失/
    非 README.md 一律视为无声明——没有声明则 scope=unknown、条目 unverified，
    绝不猜测）。"""
    readme = root / README_NAME
    if readme.is_symlink() or not readme.is_file():
        return False
    if readme.stat().st_size > _MAX_README_BYTES:
        return False
    try:
        text = readme.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return False
    return any(marker in text for marker in FIXTURE_DECLARATION_MARKERS)


def _safe_declared_time(value: Any) -> str | None:
    """ISO 时间短标量：字符串、<=64 字符且能 fromisoformat（该格式不可能
    携带敏感 marker），否则 null。"""
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


def _extract_declared(obj: Mapping[str, Any]) -> dict[str, Any]:
    """极小白名单提取：只透出受控枚举/布尔/ISO 时间/安全 schema 标量，
    任何不匹配/缺失值一律 null——错误文本、业务 ID、标题、正文、URL、
    endpoint、key/token 物理上进不了输出。"""
    declared: dict[str, Any] = {}
    for field, allowed in _DECLARED_ENUM_FIELDS.items():
        value = obj.get(field)
        declared[field] = (
            value if isinstance(value, str) and value in allowed else None
        )
    raw_schema = obj.get("schema_version")
    declared["schema_version"] = (
        raw_schema
        if isinstance(raw_schema, str) and _SCHEMA_VERSION_RE.fullmatch(raw_schema)
        else None
    )
    for field in _DECLARED_BOOL_FIELDS:
        value = obj.get(field)
        declared[field] = value if isinstance(value, bool) else None
    for field in _DECLARED_TIME_FIELDS:
        declared[field] = _safe_declared_time(obj.get(field))
    return declared


def _parse_json_meta(path: Path, size: int) -> tuple[str, dict[str, Any] | None, bool]:
    """尝试把已知类别的 JSON 文件解析为 object 并提取白名单。

    返回 (parse_status, declared, sensitive_key_detected)。解析失败不输出
    错误文本（统一 unparsed）；超过大小上限只哈希（skipped_size）；敏感键
    只报布尔，键名与值从不回显。
    """
    if size > _MAX_PARSE_BYTES:
        return PARSE_SKIPPED_SIZE, None, False
    try:
        text = path.read_bytes().decode("utf-8")
        obj = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return PARSE_UNPARSED, None, False
    if not isinstance(obj, dict):
        return PARSE_UNPARSED, None, False
    return (
        PARSE_PARSED,
        _extract_declared(obj),
        find_sensitive_key(obj) is not None,
    )


# --- 递归枚举（symlink fail-closed，绝不跟随） ------------------------------------


def _walk_onerror(error: OSError) -> None:
    """os.walk 枚举失败（不可读/访问被拒绝的子目录）fail-closed：默认
    onerror=None 会静默跳过该子目录、输出不完整清单却 exit 0——这里把
    枚举错误转 :class:`EvidenceInventoryInputError`（CLI exit 2），绝不
    产生部分报告。"""
    raise EvidenceInventoryInputError(
        "证据目录枚举失败（存在不可读或访问被拒绝的子目录），"
        "拒绝输出不完整清单"
    ) from error


def _enumerate_root(
    root: Path, root_index: int, fixture_declared: bool
) -> tuple[list[dict[str, Any]], int]:
    """递归枚举一个 evidence root（只读）：返回 (文件条目列表, 子目录数)。

    os.walk 不跟随 symlink 目录，但会把 symlink 目录列入 dirnames、symlink
    文件列入 filenames——两者（含 dangling）一律 fail-closed 拒绝，绝不
    跟随；目录枚举本身的错误（onerror）同样 fail-closed，绝不静默跳过。
    非 JSON/未知分类/模板/锚副本不解析内容；条目顺序按相对路径排序
    （输出稳定可审计）。条目的 name/relative_path 先保留真实值供内部
    排序/coverage/重复组计算，进入 manifest 的显示字段由
    :func:`_display_entry` 统一脱敏。
    """
    files: list[dict[str, Any]] = []
    dir_count = 0
    root_key = os.path.normcase(str(root.resolve()))
    for dirpath, dirnames, filenames in os.walk(
        root, followlinks=False, onerror=_walk_onerror
    ):
        dirnames.sort()
        for name in dirnames:
            if (Path(dirpath) / name).is_symlink():
                raise EvidenceInventoryInputError(
                    "证据目录内存在符号链接目录，拒绝使用（绝不跟随）"
                )
        # 目录计数：walk 产生的每个非根 dirpath 即一个已进入的子目录
        if os.path.normcase(str(Path(dirpath).resolve())) != root_key:
            dir_count += 1
        for name in sorted(filenames):
            entry_path = Path(dirpath) / name
            if entry_path.is_symlink():
                raise EvidenceInventoryInputError(
                    "证据目录内存在符号链接文件，拒绝使用（绝不跟随）"
                )
            relative = entry_path.relative_to(root).as_posix()
            classification = _classify(name)
            applicability = (
                APPLICABILITY_NOT_APPLICABLE
                if classification == CLASS_CUTOVER_TEMPLATE
                else (
                    APPLICABILITY_ISOLATION_FIXTURE
                    if fixture_declared
                    else APPLICABILITY_UNVERIFIED
                )
            )
            size = entry_path.stat().st_size
            if (
                classification
                in (
                    CLASS_CUTOVER_STEP,
                    CLASS_PRODUCTION_PREFLIGHT,
                    CLASS_BACKUP_MANIFEST,
                )
                and name.endswith(".json")
            ):
                parse_status, declared, sensitive = _parse_json_meta(
                    entry_path, size
                )
            else:
                parse_status, declared, sensitive = (
                    PARSE_NOT_ATTEMPTED,
                    None,
                    False,
                )
            files.append(
                {
                    "root_index": root_index,
                    "name": name,
                    "relative_path": relative,
                    "classification": classification,
                    "applicability": applicability,
                    "size_bytes": size,
                    "sha256": _hash_file_chunked(entry_path),
                    "mtime_utc": _mtime_utc(entry_path),
                    "parse_status": parse_status,
                    "sensitive_key_detected": sensitive,
                    "declared": declared,
                }
            )
    files.sort(key=lambda item: item["relative_path"])
    return files, dir_count


def _count_by(
    files: list[dict[str, Any]], field: str, values: tuple[str, ...]
) -> dict[str, int]:
    return {
        value: sum(1 for item in files if item[field] == value) for value in values
    }


def _covered_step_ids(files: list[dict[str, Any]]) -> set[str]:
    """coverage 只统计 root 顶层的精确证据文件名（真实 relative_path 无
    目录段才命中）——与 cutover-rehearsal 只消费 ``root/<evidence_file>``
    的口径一致；嵌套同名文件仍列入 inventory（classification 不变），
    但不推进覆盖。"""
    return {
        _STEP_BY_EVIDENCE_FILE[item["name"]]
        for item in files
        if item["classification"] == CLASS_CUTOVER_STEP
        and "/" not in item["relative_path"]
    }


def _display_relative_path(relative: str) -> str:
    """安全显示相对路径：仅 root 顶层的已知精确安全文件名原样显示，
    未知文件名与任何嵌套路径（父目录名未证明安全）一律 redacted。"""
    if "/" not in relative and relative in SAFE_DISPLAY_FILENAMES:
        return relative
    return REDACTED_PLACEHOLDER


def _display_entry(item: Mapping[str, Any]) -> dict[str, Any]:
    """条目显示脱敏：name/relative_path 替换为安全显示值（顶层安全名
    两者即真名且相等），其余字段（sha256/size/mtime/分类/declared 等）
    本就不含路径成分，原样保留。"""
    display = _display_relative_path(item["relative_path"])
    return {**item, "name": display, "relative_path": display}


# --- 主流程 -----------------------------------------------------------------------


def build_evidence_inventory(
    evidence_dirs: list[str | Path],
    output_path: str | Path | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """只读盘点一或多个证据目录（不写任何文件；落盘由 CLI 原子完成）。

    输入/输出护栏先于任何目录枚举执行；目录枚举错误（不可读子目录）同样
    fail-closed 抛 :class:`EvidenceInventoryInputError`，绝不返回部分清单。
    返回 manifest（``exit_code`` 恒为 0——清单是事实记录，inventory 没有
    失败语义）。护栏问题抛 :class:`EvidenceInventoryInputError`，IO 问题抛
    OSError（CLI 均 exit 2）。进入 manifest 的 name/relative_path/重复组
    paths 均为安全显示值（见 :data:`SAFE_DISPLAY_FILENAMES`）。
    """
    roots = _check_roots(list(evidence_dirs))
    if output_path is not None:
        _check_output_path(output_path, roots)

    root_sections: list[dict[str, Any]] = []
    all_files: list[dict[str, Any]] = []
    dirs_total = 0
    for index, root in enumerate(roots, start=1):
        fixture_declared = _fixture_declared_by_readme(root)
        files, dir_count = _enumerate_root(root, index, fixture_declared)
        dirs_total += dir_count
        all_files.extend(files)
        covered = _covered_step_ids(files)
        sha_seen: dict[str, int] = {}
        for item in files:
            sha_seen[item["sha256"]] = sha_seen.get(item["sha256"], 0) + 1
        root_sections.append(
            {
                "root_index": index,
                "fixture_declaration": (
                    "isolation_fixture" if fixture_declared else "none"
                ),
                "fixture_declaration_source": README_NAME if fixture_declared else None,
                "scope": (
                    "declared_isolation_fixture" if fixture_declared else "unknown"
                ),
                "file_count": len(files),
                "dir_count": dir_count,
                "classifications": _count_by(files, "classification", CLASSIFICATIONS),
                "applicabilities": _count_by(
                    files, "applicability", APPLICABILITY_VALUES
                ),
                "parse_statuses": _count_by(files, "parse_status", PARSE_STATUSES),
                "cutover_steps_covered": sorted(covered),
                "cutover_steps_missing": sorted(STEP_IDS - covered),
                "duplicate_sha256_count": sum(
                    count for count in sha_seen.values() if count > 1
                ),
                "files": [_display_entry(item) for item in files],
            }
        )

    sha_locations: dict[str, list[str]] = {}
    for item in all_files:
        sha_locations.setdefault(item["sha256"], []).append(
            f"#{item['root_index']}/{_display_relative_path(item['relative_path'])}"
        )
    duplicate_groups = [
        {"sha256": digest, "count": len(paths), "paths": sorted(paths)}
        for digest, paths in sorted(sha_locations.items())
        if len(paths) > 1
    ]
    overall_covered = _covered_step_ids(all_files)
    report: dict[str, Any] = {
        "generated_at": (clock or _utc_now)().isoformat(),
        "tool": TOOL_ID,
        "read_only": True,
        "isolation_note": _ISOLATION_NOTE,
        "no_execution_note": _NO_EXECUTION_NOTE,
        "scope_note": _SCOPE_NOTE,
        "production_ready": False,
        "production_ready_note": _PRODUCTION_READY_NOTE,
        "roots": root_sections,
        "summary": {
            "roots_total": len(roots),
            "files_total": len(all_files),
            "dirs_total": dirs_total,
            "classifications": _count_by(
                all_files, "classification", CLASSIFICATIONS
            ),
            "applicabilities": _count_by(
                all_files, "applicability", APPLICABILITY_VALUES
            ),
            "parse_statuses": _count_by(all_files, "parse_status", PARSE_STATUSES),
            "cutover_steps": {
                "known_total": len(STEPS),
                "covered": sorted(overall_covered),
                "missing": sorted(STEP_IDS - overall_covered),
            },
            "duplicate_sha256_groups": duplicate_groups,
        },
        "exit_code": 0,
    }
    return scrub_sensitive(report)


def format_inventory_summary(report: Mapping[str, Any]) -> str:
    """人类可读摘要（无凭据/无生产 ID/无正文/无绝对路径；manifest 文件
    显示名已脱敏——未知文件名与嵌套路径不出现在任何输出面），按目录编号
    分组。"""
    summary = report["summary"]
    steps = summary["cutover_steps"]
    lines = [
        "生产证据目录只读索引（evidence-inventory，inventory 不是放行判定）",
        f"生成时间: {report['generated_at']}",
        (
            f"证据目录: {summary['roots_total']} 个（按传入顺序编号 #1…"
            f"#{summary['roots_total']}，清单内不使用绝对路径）"
        ),
        _ISOLATION_NOTE,
        "-" * 72,
    ]
    for section in report["roots"]:
        lines.append(
            f"—— #{section['root_index']}｜{section['file_count']} 个文件、"
            f"{section['dir_count']} 个子目录｜fixture 声明: "
            f"{section['fixture_declaration']}"
            + (
                f"（依据 {section['fixture_declaration_source']}）"
                if section["fixture_declaration_source"]
                else ""
            )
            + f"｜scope={section['scope']} ——"
        )
        counts = "  ".join(
            f"{key}={value}"
            for key, value in section["classifications"].items()
            if value
        )
        lines.append(f"  分类: {counts or 'other=0'}")
        apps = "  ".join(
            f"{key}={value}"
            for key, value in section["applicabilities"].items()
            if value
        )
        lines.append(f"  适用性: {apps or 'unverified=0'}")
        lines.append(
            f"  cutover 步骤文件覆盖: {len(section['cutover_steps_covered'])}/"
            f"{steps['known_total']}（覆盖只看 root 顶层精确文件名存在，"
            "不是放行判定）"
        )
        if section["cutover_steps_missing"]:
            lines.append(
                "  缺失: " + "、".join(section["cutover_steps_missing"])
            )
        if section["duplicate_sha256_count"]:
            lines.append(
                f"  [注意] 目录内重复 sha256 的文件数: "
                f"{section['duplicate_sha256_count']}"
            )
        lines.append("")
    lines.append("-" * 72)
    lines.append(
        f"总体: {summary['files_total']} 个文件、{summary['dirs_total']} 个子目录；"
        f"cutover 步骤覆盖 {len(steps['covered'])}/{steps['known_total']}"
    )
    if summary["duplicate_sha256_groups"]:
        lines.append(f"跨目录重复 sha256 组: {len(summary['duplicate_sha256_groups'])}")
    lines.append(
        "RESULT: production_ready=false（固定）——本清单是目录盘点，不构成"
        "生产放行，也不授权任何生产操作"
    )
    lines.append(_PRODUCTION_READY_NOTE)
    lines.append("退出码: 成功盘点=0 / 输入或路径与 IO 问题=2。")
    return "\n".join(lines)
