#!/usr/bin/env python
r"""M14-51 audit readiness file-input and report CLI (single-file, stdlib only).

Thin CLI wrapper around the M14-50 pure evaluator
(``audit_archive_scheduler.evaluate_readiness``): read two real local
schema-v1 documents (``--state``, ``--policy``), classify every tracked
task deterministically as of ``--now`` (or current UTC when omitted),
then atomically write a canonical readiness report plus a matching
SHA-256 sidecar (``<output>.sha256`` containing
``<report-sha256>  <output-basename>\n``).

Input reading is fail-closed and read-only:

- regular files only -- missing files, directories, symlinks, and
  unresolved/unsafe paths are rejected;
- a conservative 1 MiB per-document size cap (checked before and after
  reading, so a file that grows mid-read is still rejected);
- strict UTF-8 decoding and strict JSON parsing -- duplicate keys and
  non-finite constants (``NaN``/``Infinity``) are rejected, and the
  document must be a JSON object;
- neither input is ever mutated.

Report discipline:

- the report records input metadata using the logical role and basename
  only, plus byte size and SHA-256 -- never an absolute local path;
- output is canonical JSON (sorted keys, compact separators, UTF-8,
  exactly one trailing newline) written in binary mode (immune to
  Windows CRLF translation), so identical inputs and ``--now`` yield
  byte-identical reports;
- both artifacts are written via temp files then ``os.replace``
  (atomic replacement); a write failure removes the temp files and
  exits non-zero without leaving debris;
- the output path (and its sidecar) may never overwrite either input.

Exit codes stay simple and auditable: ``0`` for successful report
generation -- even when readiness itself is ``due``/``overdue``/
``blocked`` (the report's ``overall`` field carries that state) -- and
``2`` for any usage/input/output failure.  Design discipline shared
with the other tools/ops CLIs: FS and clock injection, zero network,
zero environment access, zero child processes, zero DB/S3.  Honest
boundaries: this slice is file-input/report-output only; it does not
discover evidence automatically, integrate Windows Task Scheduler,
contact WORM/offline storage, run production, or prove
``production_ready=true``.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: 报告 schema 版本(与 M14-50 evaluator 同版)
SCHEMA_VERSION = 1

#: 工具名(报告 ``tool`` 字段;报告绝不记录绝对本地路径)
TOOL_NAME = "audit_archive_readiness"

#: 单个输入文档的保守大小上限(1 MiB)
MAX_INPUT_BYTES = 1 << 20

#: 退出码:0 成功生成报告(含 due/overdue/blocked);2 一切 usage/输入/输出失败
EXIT_OK = 0
EXIT_REJECT = 2

_SCHEDULER_FILE = Path(__file__).resolve().with_name("audit_archive_scheduler.py")


def _load_scheduler():
    """加载同目录的 M14-50 评估器模块(单文件纪律,零 sys.path 副作用)。

    必须先注册进 ``sys.modules`` 再 exec:被加载模块顶层 ``@dataclass``
    在类创建时会经 ``cls.__module__`` 反查本模块,未注册会在导入期即崩
    (AttributeError),这是 importlib 动态加载的官方推荐顺序。
    """
    spec = importlib.util.spec_from_file_location(
        "audit_archive_scheduler", _SCHEDULER_FILE
    )
    if spec is None or spec.loader is None:
        raise ImportError("audit_archive_scheduler.py is not loadable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


SCHEDULER = _load_scheduler()


class Rejected(Exception):
    """fail-closed 拒绝:消息只含角色与原因,绝不含绝对路径/凭据。"""


class _DuplicateKey(Exception):
    """object_pairs_hook 抛出以精确定位重复 JSON 键。"""


@dataclass(frozen=True)
class InputFacts:
    """一个输入文档的可审计元数据(仅 role/basename/字节大小/SHA-256)。"""

    role: str
    name: str
    size_bytes: int
    sha256: str


class RealFS:
    """真实文件系统适配(只读输入 + tmp/replace 原子写,字节模式防 CRLF)。"""

    def exists(self, path) -> bool:
        return os.path.exists(path)

    def is_file(self, path) -> bool:
        return os.path.isfile(path)

    def is_dir(self, path) -> bool:
        return os.path.isdir(path)

    def is_symlink(self, path) -> bool:
        return os.path.islink(path)

    def size(self, path) -> int:
        return os.stat(path).st_size

    def read_bytes(self, path) -> bytes:
        with open(path, "rb") as handle:
            return handle.read()

    def write_bytes(self, path, data: bytes) -> None:
        with open(path, "wb") as handle:
            handle.write(data)

    def replace(self, src, dst) -> None:
        os.replace(src, dst)

    def unlink_quiet(self, path) -> None:
        try:
            os.unlink(path)
        except OSError:
            pass


def _reject(message: str) -> Rejected:
    return Rejected(message)


def _read_input(
    fs: RealFS, role: str, raw: str
) -> tuple[dict[str, Any], InputFacts, Path]:
    """读取并严格解析一个输入文档;返回 (doc, 元数据, resolved 路径)。

    只读、fail-closed:任何问题抛 ``Rejected``;绝不写输入。
    resolved 路径仅用于输出守卫比较,绝不进入报告。
    """
    if not isinstance(raw, str) or not raw.strip():
        raise _reject(f"{role} input: path must be a non-empty string")
    path = Path(raw)
    if fs.is_symlink(path):
        raise _reject(f"{role} input: symlinks are not allowed")
    if not fs.exists(path):
        raise _reject(f"{role} input: file not found")
    if not fs.is_file(path):
        raise _reject(f"{role} input: not a regular file")
    try:
        resolved = path.resolve()
    except OSError:
        raise _reject(f"{role} input: path cannot be resolved") from None
    size = fs.size(path)
    if size > MAX_INPUT_BYTES:
        raise _reject(
            f"{role} input: {size} bytes exceeds the {MAX_INPUT_BYTES}-byte limit"
        )
    data = fs.read_bytes(path)
    if len(data) > MAX_INPUT_BYTES:
        raise _reject(
            f"{role} input: {len(data)} bytes exceeds the {MAX_INPUT_BYTES}-byte limit"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise _reject(f"{role} input: not valid UTF-8") from None
    document = _strict_loads(role, text)
    facts = InputFacts(
        role=role,
        name=path.name,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    return document, facts, resolved


def _strict_loads(role: str, text: str) -> dict[str, Any]:
    """严格 JSON 对象解析:拒绝重复键、非有限常数、非对象文档。"""

    def pairs_hook(pairs):
        seen = set()
        for key, _ in pairs:
            if key in seen:
                raise _DuplicateKey(key)
            seen.add(key)
        return dict(pairs)

    def constant_hook(name: str):
        raise ValueError(name)

    try:
        document = json.loads(
            text, object_pairs_hook=pairs_hook, parse_constant=constant_hook
        )
    except _DuplicateKey as dup:
        raise _reject(f"{role} input: duplicate JSON key {dup.args[0]!r}") from None
    except (json.JSONDecodeError, ValueError):
        raise _reject(f"{role} input: not valid strict JSON") from None
    if not isinstance(document, dict):
        raise _reject(f"{role} input: JSON document must be an object")
    return document


def _canonical_json_bytes(payload: dict) -> bytes:
    """canonical JSON(sort_keys/紧凑分隔/utf-8)+ 恰一个尾随 LF。"""
    body = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return body.encode("utf-8") + b"\n"


def _parse_now(raw: str) -> datetime | None:
    """解析 tz-aware ISO 8601(接受 ``Z``);naive/非法返回 None。"""
    text = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _write_outputs(
    fs: RealFS,
    output: Path,
    state_resolved: Path,
    policy_resolved: Path,
    report_bytes: bytes,
    sidecar_bytes: bytes,
) -> Path:
    """守卫 + 原子写 report 与 sidecar;返回 sidecar 的 resolved 路径。

    守卫:输出、其 sidecar 或任一 tmp 路径绝不允许覆盖任一输入;输出父
    目录必须已存在(本工具绝不创建目录)。写盘:两个 tmp 先全量写好,
    再依次 ``os.replace``;任何失败清理两个 tmp 后上抛(fail-closed,零残留)。
    """
    try:
        resolved_out = output.resolve()
    except OSError:
        raise _reject("output path cannot be resolved") from None
    sidecar_resolved = resolved_out.with_name(resolved_out.name + ".sha256")
    tmp_report = resolved_out.with_name(resolved_out.name + ".tmp")
    tmp_sidecar = resolved_out.with_name(resolved_out.name + ".sha256.tmp")
    if resolved_out in (state_resolved, policy_resolved):
        raise _reject("output path must not overwrite the state or policy input")
    if sidecar_resolved in (state_resolved, policy_resolved):
        raise _reject("sidecar output must not overwrite the state or policy input")
    if tmp_report in (state_resolved, policy_resolved):
        raise _reject("tmp report path must not overwrite the state or policy input")
    if tmp_sidecar in (state_resolved, policy_resolved):
        raise _reject("tmp sidecar path must not overwrite the state or policy input")
    if not fs.is_dir(resolved_out.parent):
        raise _reject("output parent directory must already exist")
    try:
        fs.write_bytes(tmp_report, report_bytes)
        fs.write_bytes(tmp_sidecar, sidecar_bytes)
        fs.replace(tmp_report, resolved_out)
        fs.replace(tmp_sidecar, sidecar_resolved)
    except Exception:
        fs.unlink_quiet(tmp_report)
        fs.unlink_quiet(tmp_sidecar)
        raise
    return sidecar_resolved


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit_archive_readiness.py",
        description=(
            "Audit readiness report CLI: read local schema-v1 state/policy "
            "JSON documents, evaluate them with the M14-50 scheduler "
            "evaluator, and atomically write a canonical report plus a "
            "SHA-256 sidecar (fail-closed; zero network/zero env/zero "
            "child processes). Exit 0 on successful report generation "
            "(even when readiness is due/overdue/blocked); exit 2 on any "
            "usage/input/output failure."
        ),
    )
    parser.add_argument(
        "--state", required=True, metavar="PATH", help="schema-v1 state JSON file"
    )
    parser.add_argument(
        "--policy", required=True, metavar="PATH", help="schema-v1 policy JSON file"
    )
    parser.add_argument(
        "--output",
        required=True,
        metavar="PATH",
        help="canonical readiness report output path (plus .sha256 sidecar)",
    )
    parser.add_argument(
        "--now",
        metavar="ISO8601",
        help=(
            "timezone-aware ISO 8601 evaluation instant for deterministic "
            "tests (omitted: current UTC)"
        ),
    )
    return parser


def _run(args, *, fs: RealFS, now_provider: Callable[[], datetime]) -> int:
    """执行一次报告生成;fail-closed 抛 ``Rejected``。"""
    if args.now is not None:
        now = _parse_now(args.now)
        if now is None:
            raise _reject("--now must be a timezone-aware ISO 8601 timestamp")
    else:
        now = now_provider()
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        raise _reject("evaluation instant must be timezone-aware")
    now = now.astimezone(timezone.utc)

    state_doc, state_facts, state_resolved = _read_input(fs, "state", args.state)
    policy_doc, policy_facts, policy_resolved = _read_input(fs, "policy", args.policy)

    evaluation = SCHEDULER.evaluate_readiness(state_doc, policy_doc, now)
    report = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "generated_for": evaluation["generated_for"],
        "overall": evaluation["overall"],
        "inputs": {
            "state": {
                "role": "state",
                "name": state_facts.name,
                "size_bytes": state_facts.size_bytes,
                "sha256": state_facts.sha256,
            },
            "policy": {
                "role": "policy",
                "name": policy_facts.name,
                "size_bytes": policy_facts.size_bytes,
                "sha256": policy_facts.sha256,
            },
        },
        "evaluation": evaluation,
        "problems": [],
    }
    report_bytes = _canonical_json_bytes(report)
    sidecar_bytes = (
        f"{hashlib.sha256(report_bytes).hexdigest()}  {Path(args.output).name}\n"
    ).encode("utf-8")
    _write_outputs(
        fs,
        Path(args.output),
        state_resolved,
        policy_resolved,
        report_bytes,
        sidecar_bytes,
    )
    return EXIT_OK


def main(argv=None, *, fs=None, now_provider=None) -> int:
    """CLI 入口(fs/clock 注入;测试零网络零真实副作用)。

    终防线:命令执行中的任何意外异常(含写盘失败)一律折叠为
    exit 2——stderr 只记异常类型名,绝不上抛原始 traceback,也绝不
    携带秘密形态值(argparse 的 SystemExit 属 BaseException,不受
    影响,usage 错误仍以 2 退出)。
    """
    args = _build_parser().parse_args(argv)
    fs = RealFS() if fs is None else fs
    if now_provider is None:
        def now_provider() -> datetime:
            return datetime.now(timezone.utc)

    try:
        return _run(args, fs=fs, now_provider=now_provider)
    except Rejected as exc:
        print(f"{TOOL_NAME}: {exc}", file=sys.stderr)
        return EXIT_REJECT
    except Exception as exc:  # noqa: BLE001 —— 终防线 fail-closed(类型名之外零信息)
        print(
            f"{TOOL_NAME}: unexpected {type(exc).__name__}; fail-closed exit 2",
            file=sys.stderr,
        )
        return EXIT_REJECT


if __name__ == "__main__":
    sys.exit(main())
