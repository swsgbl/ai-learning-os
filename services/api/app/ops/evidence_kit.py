"""ops 只读证据 manifest 工具的共享装载/校验层（M10-15 提取）。

release-readiness（M10-11）与 cutover-rehearsal（M10-15）是两个语义不同的
manifest 工具（发布门矩阵 vs 生产切换时间线），但底层安全语义必须同一实现，
不允许各自漂移：证据目录/文件路径护栏（symlink fail-closed）、JSON 对象
装载、SHA-256、敏感键与内嵌凭据扫描、最终 manifest scrub、严格 schema
校验原语（req*）、治理批次计数。本模块从 release_readiness 原样提取
（零行为变更，现有测试保持全绿），是纯函数集合——不读环境变量、不连
数据库、不访问网络、不写任何文件。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from app.ops.production_preflight import redact_secrets

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
#: 敏感键模式（递归扫描证据与最终 manifest；命中即拒绝/抹除）。
#: 覆盖常见凭据字段变体（含 authorization/cookie/credential），宁可误拒
#: 同名业务字段也不放行——证据目录只应存放脱敏导出物。
SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key"
    r"|authorization|auth[_-]?header|cookie|credential)",
    re.IGNORECASE,
)


class EvidenceInputError(Exception):
    """证据目录/路径问题（CLI 退出码 2：不是门结论，是输入不可用）。"""


class MalformedEvidence(Exception):
    """gate 证据结构与声明的 schema 不符（内部信号，统一转 malformed 语义）。"""


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


def check_regular_file(path: Path) -> None:
    if path.is_symlink():
        raise EvidenceInputError(f"证据文件是符号链接，拒绝读取: {path.name}")
    if not path.is_file():
        raise EvidenceInputError(f"证据文件不是常规文件: {path.name}")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
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


def find_sensitive_key(value: Any, prefix: str = "") -> str | None:
    """递归扫描敏感键，返回首个命中的键路径（如 providers.voice.api_key）。

    证据文件应全为脱敏导出物：命中即由调用方按 malformed 拒绝，值从不回显。
    """
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key)
            if SENSITIVE_KEY_RE.search(name):
                return f"{prefix}{name}"
            hit = find_sensitive_key(item, f"{prefix}{name}.")
            if hit:
                return hit
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hit = find_sensitive_key(item, f"{prefix}{index}.")
            if hit:
                return hit
    return None


def find_embedded_credential(value: Any, prefix: str = "") -> str | None:
    """递归扫描内嵌凭据的字符串值（`://user:pass@` 形态，复用 redact_secrets
    判定），返回首个命中的字段路径——证据文件本不应携带任何凭据。"""
    if isinstance(value, dict):
        for key, item in value.items():
            hit = find_embedded_credential(item, f"{prefix}{key}.")
            if hit:
                return hit
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hit = find_embedded_credential(item, f"{prefix}{index}.")
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
            key: (
                "[REDACTED]"
                if SENSITIVE_KEY_RE.search(str(key))
                else scrub_sensitive(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub_sensitive(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


# --- 白名单字段提取（严格 schema，违例即 MalformedEvidence） --------------------


def req(obj: Mapping[str, Any], key: str) -> Any:
    if key not in obj:
        raise MalformedEvidence(f"缺字段 {key}")
    return obj[key]


def req_str(obj: Mapping[str, Any], key: str, *, max_len: int = 512) -> str:
    value = req(obj, key)
    if not isinstance(value, str) or not value.strip():
        raise MalformedEvidence(f"字段 {key} 必须是非空字符串")
    if len(value) > max_len:
        raise MalformedEvidence(f"字段 {key} 超长（>{max_len}）")
    return value


def req_bool(obj: Mapping[str, Any], key: str) -> bool:
    value = req(obj, key)
    if not isinstance(value, bool):
        raise MalformedEvidence(f"字段 {key} 必须是布尔值")
    return value


def req_int(obj: Mapping[str, Any], key: str, *, minimum: int = 0) -> int:
    value = req(obj, key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise MalformedEvidence(f"字段 {key} 必须是 >= {minimum} 的整数")
    return value


def req_dict(obj: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = req(obj, key)
    if not isinstance(value, dict):
        raise MalformedEvidence(f"字段 {key} 必须是对象")
    return value


def req_choice(obj: Mapping[str, Any], key: str, choices: tuple[str, ...]) -> str:
    value = req(obj, key)
    if value not in choices:
        raise MalformedEvidence(f"字段 {key} 必须是 {list(choices)} 之一")
    return value


def req_hex(obj: Mapping[str, Any], key: str) -> str:
    value = req(obj, key)
    if not isinstance(value, str) or not HEX64_RE.fullmatch(value):
        raise MalformedEvidence(f"字段 {key} 必须是 64 位小写十六进制")
    return value


def req_iso(obj: Mapping[str, Any], key: str) -> str:
    value = req_str(obj, key, max_len=64)
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise MalformedEvidence(f"字段 {key} 不是合法 ISO 8601 时间") from None
    return value


def req_commit(obj: Mapping[str, Any], key: str) -> str:
    value = req_str(obj, key, max_len=64)
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", value):
        raise MalformedEvidence(f"字段 {key} 必须是 7-40 位十六进制 commit")
    return value.lower()


def batches_executed(raw: Any, *, label: str) -> int:
    """治理批次列表计数：元素必须是对象且带 executed 布尔字段。"""
    if not isinstance(raw, list):
        raise MalformedEvidence(f"字段 {label} 必须是列表")
    executed = 0
    for batch in raw:
        if not isinstance(batch, dict):
            raise MalformedEvidence(f"{label} 元素必须是对象")
        if not isinstance(batch.get("executed"), bool):
            raise MalformedEvidence(f"{label} 元素缺 executed 布尔字段")
        executed += 1 if batch["executed"] else 0
    return executed
