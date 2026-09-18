#!/usr/bin/env python
r"""M14-49 审计锚点 WORM 离线第二副本工具（fail-closed，单文件纯标准库）。

把 M14-43/M14-44 已 WORM 归档且 ``audit_anchor_archive.py verify`` 通过的
审计锚点文件与其 verify 成功报告复制成**库外离线介质第二副本**——不替代
WORM 归档本身，只做可核验的离线副本。三命令（退出码统一 ``0`` 成功 /
``2`` 一切拒绝）：

- ``preflight``：只读预检——本地锚链完整校验（字段集/类型/canonical
  JSON/anchor_hash 重算/sequence 非负且严格递增——与 M14-42/M14-43 同
  契约，允许跳号不要求 +1/previous 链接/sequence 0 行 head 必须是
  genesis 常量/anchored_at 可按 ISO-8601 解析/symlink 源拒绝）+ verify
  报告装载与事实绑定 + 离线根目录/marker 契约校验 + 既有副本状态观察
  （absent/matching 通过；partial/mismatch 可见拒绝）。零写入。
- ``copy``：确认短语 ``EXECUTE AUDIT ANCHOR WORM OFFLINE COPY`` 一字
  不差（近似 → 零执行零报告零写入）；preflight 全过后在
  ``<offline-root>/audit-anchor/<sha256>/`` 原子二进制写三文件——
  ``audit-anchor.jsonl``（锚文件逐字节副本）、``verify-report.json``
  （verify 报告逐字节副本）、``manifest.json``（确定性事实清单——纯
  输入的函数，零时钟，幂等可逐字节复算）；三文件全部已存在且逐字节
  一致 → 幂等成功零覆盖；部分存在（残留）或任一字节不符 →
  fail-closed 零写入；写后重读任何漂移 → fail-closed。
- ``verify``：只读重算——重复全部 preflight 校验（锚链/报告绑定/根
  目录/marker）并逐字节核对离线副本三文件与确定性 manifest；不修复、
  不覆盖、绝不写入离线根。

verify 报告绑定链（防畸形/篡改/跨参数）：输入报告必须是
``audit_anchor_archive.py`` 的 **verify 成功报告**（milestone=M14-43、
tool=audit_anchor_archive、command=verify、status=pass、problems=[]、
worm_verified=true），其 source 块与本地锚文件重算事实逐项一致
（hash/size/anchor_count/first/last）、target key 与本地重算的内容寻址
key 一致、archive 事实齐全（version_id 非空、COMPLIANCE、retain_until
可按 ISO-8601 tz-aware 解析、content-type x-ndjson、对象 hash/size 与
本地源一致）、target 记录 endpoint host/bucket。离线副本无法也不试图
在线复核 WORM 对象本身——那是 M14-43 verify（在线）的职责；本工具绑定
的是「verify 报告 ↔ 本地锚文件 ↔ 离线副本」三方逐字节一致性。

离线根目录契约：``--offline-root`` 必须显式给出、绝对路径、已存在的
常规目录（本工具绝不创建根目录）、自身与全部祖先均非 symlink、不得
位于仓库内（涵盖 ``.verify/artifacts``），且必须含**操作者预创建**
marker 文件 ``AIOS-OFFLINE-COPY-ROOT.marker``，内容恰为
``AIOS audit anchor WORM offline copy root v1\n``（字节精确；本工具绝不
创建/修改 marker——marker 证明操作者显式选择了该离线根，而非脚本误写）。

设计纪律（与 tools/ops 既有工具同款）：FS/Clock/报告名随机后缀生成器
全注入，开发回合零网络、零 env 读取、零子进程、零 DB、零 S3。报告
原子写 gitignored ``.verify/artifacts/m14-49-audit-worm-offline-copy/``
（JSON + Markdown + ``.json.sha256`` sidecar；字节模式写盘防 Windows
行尾翻译破坏哈希）。报告名 ``<command>-<stamp>-<随机后缀>`` 真正防
碰撞：每份报告名带 ``secrets.token_hex`` CSPRNG 随机后缀（32 hex
chars = 128 bits，同 command 同秒并发撞名概率约 2**-128），存在性探测
循环（含残留孤儿工件）仍是 fail-closed 兜底、递增 ``-2``/``-3`` 换名，
绝不覆盖既有报告工件。报告如实记录操作者选择的离线根绝对路径（本机
路径事实，非凭据）；``redact_secrets`` 终防线折叠 key=value / Bearer
形态疑似秘密。诚实边界：三文件写入非单一事务（中断可留残留，后续
运行按 partial 拒绝零覆盖）；探测与写入之间存在非原子窗口（无全局
互斥）；``pass`` 不等于 production ready，``production_ready=false``
不变。真实离线介质复制仅由 supervisor 在获准窗口执行。
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

#: 仓库根（repo 内Containment 判定与报告工件目录锚点）
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 报告工件目录（gitignored）
ARTIFACT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-49-audit-worm-offline-copy"

#: copy 执行确认短语（一字不差，近似短语零执行零报告零写入）
CONFIRM_COPY = "EXECUTE AUDIT ANCHOR WORM OFFLINE COPY"

#: 离线根 marker 契约：操作者预创建；本工具绝不创建/修改（字节精确）
MARKER_NAME = "AIOS-OFFLINE-COPY-ROOT.marker"
MARKER_CONTENT = b"AIOS audit anchor WORM offline copy root v1\n"

#: 内容寻址布局（与 M14-43 WORM key 同前缀/后缀）
LAYOUT_PREFIX = "audit-anchor"
ANCHOR_COPY_NAME = "audit-anchor.jsonl"
REPORT_COPY_NAME = "verify-report.json"
MANIFEST_NAME = "manifest.json"
COPY_NAMES = (ANCHOR_COPY_NAME, REPORT_COPY_NAME, MANIFEST_NAME)

#: 上游 M14-43 verify 成功报告的形态常量
WORM_MILESTONE = "M14-43"
WORM_TOOL = "audit_anchor_archive"
WORM_COMMAND = "verify"
WORM_CONTENT_TYPE = "application/x-ndjson"
WORM_RETENTION_MODE = "COMPLIANCE"

#: 报告名随机后缀宽度：secrets.token_hex(16) → 32 hex chars（128 bits）
REPORT_SUFFIX_BYTES = 16

#: 与锚点生产端（app.domain.audit_chain）同源的锚行常量
GENESIS_PREVIOUS_HASH = "0" * 64
ANCHOR_SCHEMA_VERSION = 1
ANCHOR_ALGORITHM = "sha256"
ANCHOR_FIELDS = frozenset(
    {"schema_version", "algorithm", "sequence", "head_hash", "anchored_at",
     "previous_anchor_hash", "anchor_hash"})

EXIT_OK = 0
EXIT_REJECT = 2

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")

_SECRET_KV_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?key)\b"
    r"\s*[=:]\s*\S+")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+\S+")


def redact_secrets(text: str) -> str:
    """报告终防线：折叠 key=value / Bearer 形态的疑似秘密值。"""
    text = _SECRET_KV_RE.sub("<redacted>", text)
    return _BEARER_RE.sub("<redacted>", text)


def worm_object_key(sha256: str) -> str:
    """M14-43 WORM 对象 key（内容寻址；与上游工具同形态，用于绑定比对）。"""
    return f"{LAYOUT_PREFIX}/{sha256}/{ANCHOR_COPY_NAME}"


def layout_dir(root: Path, sha256: str) -> Path:
    """离线副本内容寻址布局目录：``<root>/audit-anchor/<sha256>``。"""
    return Path(root) / LAYOUT_PREFIX / sha256


def _parse_iso_utc(raw: str | None) -> datetime | None:
    """ISO-8601 → tz-aware UTC datetime；naive/不可解析返回 None。"""
    if not raw or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(UTC)


def _canonical_json_bytes(payload: dict) -> bytes:
    """与锚点生产端同源的 canonical JSON（sort_keys/紧凑分隔/utf-8）。"""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------- 锚链校验
# （与 M14-42 生产端 / M14-43 工具同契约；单文件纪律下独立实现）


@dataclass(frozen=True)
class AnchorSource:
    """本地锚文件全部校验通过后的事实快照（data 仅供副本体使用）。"""

    data: bytes
    sha256: str
    size_bytes: int
    anchors: tuple[dict, ...]


def _anchor_row_problem(row: bytes) -> str | None:
    """单行结构校验：字段集/类型/哈希格式/canonical 形态/哈希重算。"""
    try:
        anchor = json.loads(row.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "anchor-line-not-json"
    if not isinstance(anchor, dict):
        return "anchor-line-not-object"
    if set(anchor) != ANCHOR_FIELDS:
        return "anchor-field-set"
    if type(anchor["schema_version"]) is not int \
            or anchor["schema_version"] != ANCHOR_SCHEMA_VERSION:
        return "anchor-schema-version"
    if anchor["algorithm"] != ANCHOR_ALGORITHM:
        return "anchor-algorithm"
    if type(anchor["sequence"]) is not int:
        return "anchor-sequence-type"
    for field in ("head_hash", "anchored_at", "previous_anchor_hash",
                  "anchor_hash"):
        if type(anchor[field]) is not str:
            return "anchor-field-type"
    try:
        datetime.fromisoformat(anchor["anchored_at"])
    except ValueError:
        return "anchor-anchored-at-not-iso8601"
    for field in ("head_hash", "previous_anchor_hash", "anchor_hash"):
        if _HEX64.fullmatch(anchor[field]) is None:
            return "anchor-hash-format"
    if row != _canonical_json_bytes(anchor):
        return "anchor-not-canonical"
    payload = {k: v for k, v in anchor.items() if k != "anchor_hash"}
    recomputed = hashlib.sha256(
        _canonical_json_bytes(payload)).hexdigest()
    if recomputed != anchor["anchor_hash"]:
        return "anchor-hash-mismatch"
    return None


def _chain_problems(anchors: list[dict]) -> list[str]:
    """锚链语义：与 M14-42 生产端同契约。

    - sequence 非负；严格递增——允许跳号，不要求 +1 连续，首锚不必 0；
    - 首锚 previous_anchor_hash 必须是 genesis 常量；
    - sequence==0 的行（任意位置）head_hash 必须是 genesis 常量；
    - previous_anchor_hash 逐行链接前一锚 anchor_hash。
    """
    problems: list[str] = []
    for anchor in anchors:
        if anchor["sequence"] < 0:
            problems.append("anchor-sequence-negative")
    if anchors[0]["previous_anchor_hash"] != GENESIS_PREVIOUS_HASH:
        problems.append("anchor-genesis-previous")
    for anchor in anchors:
        if anchor["sequence"] == 0 \
                and anchor["head_hash"] != GENESIS_PREVIOUS_HASH:
            problems.append("anchor-genesis-head")
    for prev, cur in itertools.pairwise(anchors):
        if cur["sequence"] <= prev["sequence"]:
            problems.append("anchor-sequence-not-increasing")
        if cur["previous_anchor_hash"] != prev["anchor_hash"]:
            problems.append("anchor-previous-link-mismatch")
    return problems


def _load_anchor_source(fs, anchor_file: str, report: dict) -> AnchorSource | None:
    """读取并完整校验锚源；任何拒绝都记 problem 并返回 None。"""
    path = Path(anchor_file)
    if fs.is_symlink(path):
        _problem(report, "anchor-file-symlink", "拒绝 symlink 锚源")
        return None
    if not fs.exists(path):
        _problem(report, "anchor-file-missing", "锚文件不存在")
        return None
    if not fs.is_file(path):
        _problem(report, "anchor-file-not-regular", "锚源不是普通文件")
        return None
    data = fs.read_bytes(path)
    if not data:
        _problem(report, "anchor-file-empty", "锚文件为空")
        return None
    if not data.endswith(b"\n"):
        _problem(report, "anchor-file-no-trailing-newline",
                 "JSONL 必须以单个换行结束（一行一锚）")
        return None
    anchors: list[dict] = []
    for index, row in enumerate(data[:-1].split(b"\n")):
        problem = _anchor_row_problem(row) if row else "anchor-line-empty"
        if problem is not None:
            _problem(report, problem, f"第 {index + 1} 行校验失败")
            return None
        anchors.append(json.loads(row.decode("utf-8")))
    chain = _chain_problems(anchors)
    if chain:
        _problem(report, chain[0], f"锚链校验失败: {chain[0]}")
        return None
    return AnchorSource(data=data,
                        sha256=hashlib.sha256(data).hexdigest(),
                        size_bytes=len(data), anchors=tuple(anchors))


# ---------------------------------------------------------------- 注入协议


class SystemClock:
    """真实 UTC 时钟（测试注入 FakeClock）。"""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def utc_now_iso(self) -> str:
        return self.now().strftime("%Y-%m-%dT%H:%M:%SZ")

    def stamp(self) -> str:
        return self.now().strftime("%Y%m%d-%H%M%S")


class RealFS:
    """真实文件系统适配（原子写：tmp + os.replace；字节模式防 CRLF 翻译）。"""

    def exists(self, path) -> bool:
        return os.path.exists(path)

    def is_file(self, path) -> bool:
        return os.path.isfile(path)

    def is_dir(self, path) -> bool:
        return os.path.isdir(path)

    def is_symlink(self, path) -> bool:
        return os.path.islink(path)

    def read_bytes(self, path) -> bytes:
        return Path(path).read_bytes()

    def write_text_atomic(self, path, text: str) -> None:
        self.write_bytes_atomic(path, text.encode("utf-8"))

    def write_bytes_atomic(self, path, data: bytes) -> None:
        target = Path(path)
        tmp = target.with_name(target.name + ".tmp")
        # 字节落盘：Windows 文本模式会把 \n 翻译成 \r\n，逐字节副本与
        # manifest 哈希都是对字节算的——翻译后核验必然失败
        tmp.write_bytes(data)
        os.replace(tmp, target)

    def mkdirs(self, path) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 报告


def _problem(report: dict, code: str, detail: str) -> None:
    """追加 problem（detail 过 redact 终防线；绝不携带原始异常/凭据）。"""
    report["problems"].append({"code": code, "detail": redact_secrets(detail)})


def _anchor_fact(anchor: dict) -> dict:
    return {"sequence": anchor["sequence"], "head_hash": anchor["head_hash"],
            "anchor_hash": anchor["anchor_hash"]}


def _source_block(src: AnchorSource) -> dict:
    return {"sha256": src.sha256, "size_bytes": src.size_bytes,
            "anchor_count": len(src.anchors),
            "first": _anchor_fact(src.anchors[0]),
            "last": _anchor_fact(src.anchors[-1])}


def _new_report(command: str, clock) -> dict:
    return {"schema_version": 1, "milestone": "M14-49", "command": command,
            "tool": "audit_worm_offline_copy",
            "started_at_utc": clock.utc_now_iso(), "ended_at_utc": None,
            "status": "pass", "problems": [], "offline_verified": False,
            "source": None, "worm": None, "verify_report": None,
            "offline": None, "copy": None, "existing": None}


def _random_suffix() -> str:
    """CSPRNG 报告名随机后缀（默认 32 hex chars；测试注入替身）。"""
    return secrets.token_hex(REPORT_SUFFIX_BYTES)


def _unique_report_name(fs, command: str, clock, suffix_gen) -> str:
    """防碰撞报告名：command + 秒级 stamp + CSPRNG 随机后缀。

    每份报告名都带 ``suffix_gen()`` 生成的随机后缀（默认
    ``secrets.token_hex``，32 hex chars = 128 bits），把同 command 同秒
    并发进程的撞名概率压到约 2**-128。存在性探测循环保留为 fail-closed
    兜底：候选名的 ``.json``/``.json.sha256``/``.md`` 任一已存在（含
    残留孤儿工件）即换 ``-2``/``-3`` 递增名——绝不覆盖既有报告工件。
    残余假设：探测与写入之间存在非原子窗口（无全局互斥），随机后缀只
    把窗口内撞名压到密码学随机概率，不承诺为零。
    """
    base = f"{command}-{clock.stamp()}-{suffix_gen()}"
    name = base
    counter = 2
    while any(fs.exists(ARTIFACT_DIR / f"{name}{ext}")
              for ext in (".json", ".json.sha256", ".md")):
        name = f"{base}-{counter}"
        counter += 1
    return name


def _write_report(fs, clock, report: dict, suffix_gen) -> int:
    """原子写 JSON + Markdown + .sha256 sidecar（三命令统一）并定退出码。"""
    report["status"] = "fail" if report["problems"] else "pass"
    report["ended_at_utc"] = clock.utc_now_iso()
    name = _unique_report_name(fs, report["command"], clock, suffix_gen)
    json_text = json.dumps(report, indent=2, ensure_ascii=False,
                           sort_keys=True) + "\n"
    fs.mkdirs(ARTIFACT_DIR)
    fs.write_text_atomic(ARTIFACT_DIR / f"{name}.json", json_text)
    fs.write_text_atomic(ARTIFACT_DIR / f"{name}.md", _render_markdown(report))
    digest = hashlib.sha256(json_text.encode("utf-8")).hexdigest()
    fs.write_text_atomic(ARTIFACT_DIR / f"{name}.json.sha256",
                         f"{digest}  {name}.json\n")
    return EXIT_REJECT if report["problems"] else EXIT_OK


def _render_markdown(report: dict) -> str:
    """Markdown 报告：只记事实；绝不渲染凭据/原始异常。"""
    lines = [f"# M14-49 审计锚点 WORM 离线第二副本 — {report['command']}", "",
             f"- status: **{report['status']}**",
             f"- offline_verified: **{report['offline_verified']}**",
             f"- started_at_utc: {report['started_at_utc']}",
             f"- ended_at_utc: {report['ended_at_utc']}"]
    source = report.get("source")
    if source:
        lines += [f"- source sha256: `{source['sha256']}`",
                  f"- source size_bytes: {source['size_bytes']}",
                  f"- anchor_count: {source['anchor_count']}",
                  (f"- first: seq {source['first']['sequence']} / "
                   f"anchor `{source['first']['anchor_hash']}`"),
                  (f"- last: seq {source['last']['sequence']} / "
                   f"anchor `{source['last']['anchor_hash']}`")]
    worm = report.get("worm")
    if worm:
        lines += [f"- worm endpoint host: {worm['endpoint_host']}",
                  f"- worm bucket: `{worm['bucket']}`",
                  f"- worm key: `{worm['key']}`",
                  f"- worm version_id: `{worm['version_id']}`",
                  f"- retention_mode: {worm['retention_mode']}",
                  f"- retain_until: {worm['retain_until']}",
                  f"- object sha256: `{worm['object_sha256']}`",
                  f"- object size_bytes: {worm['object_size_bytes']}",
                  f"- content_type: `{worm['content_type']}`"]
    vr = report.get("verify_report")
    if vr:
        lines += [(f"- verify report: `{vr['name']}` "
                   f"(sha256 `{vr['sha256']}`, {vr['size_bytes']} bytes)")]
    offline = report.get("offline")
    if offline:
        lines += [f"- offline root: `{offline['root']}`",
                  f"- layout: `{offline['layout']}`",
                  f"- files: {', '.join(offline['files'])}"]
    copy = report.get("copy")
    if copy:
        lines += [f"- copy created: {copy['created']}",
                  f"- copy existing_matched: {copy['existing_matched']}",
                  f"- copy idempotent: {copy['idempotent']}"]
    existing = report.get("existing")
    if existing:
        lines += [f"- existing state: {existing['state']}",
                  f"- existing present: {', '.join(existing['present']) or '(none)'}"]
    problems = report.get("problems") or []
    if problems:
        lines += ["", "## problems"]
        lines += [f"- `{p['code']}`: {p['detail']}" for p in problems]
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- verify 报告绑定


@dataclass(frozen=True)
class BoundVerifyReport:
    """M14-43 verify 成功报告装载并绑定本地锚文件后的事实快照。"""

    data: bytes
    sha256: str
    size_bytes: int
    facts: dict


def _read_verify_report_file(fs, verify_report: str,
                             report: dict) -> dict | None:
    """读取报告文件并解析为 JSON 对象；任何拒绝记 problem 返回 None。"""
    path = Path(verify_report)
    if fs.is_symlink(path):
        _problem(report, "verify-report-symlink", "拒绝 symlink verify 报告")
        return None
    if not fs.exists(path):
        _problem(report, "verify-report-missing", "verify 报告文件不存在")
        return None
    if not fs.is_file(path):
        _problem(report, "verify-report-not-regular",
                 "verify 报告不是普通文件")
        return None
    data = fs.read_bytes(path)
    if not data:
        _problem(report, "verify-report-empty", "verify 报告为空")
        return None
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _problem(report, "verify-report-not-json", "verify 报告不是合法 JSON")
        return None
    if not isinstance(raw, dict):
        _problem(report, "verify-report-not-object", "verify 报告不是 JSON 对象")
        return None
    return raw


def _bind_verify_report_facts(raw: dict, src: AnchorSource,
                              report: dict) -> dict | None:
    """verify 报告绑定当前调用事实（任一不符 → problem 返回 None）。

    绑定链（顺序即防线）：
    1. 形态：milestone=M14-43 + tool=audit_anchor_archive + command=verify
       （上游 archive 报告不是可接受输入——本工具绑定的是 verify 通过
       事实）；
    2. status=pass + problems=[] + worm_verified=true（失败报告不可验）；
    3. source 块与当前锚文件重算事实逐项一致（防「报告对的是另一份锚
       文件」）；
    4. target key 与本地重算内容寻址 key 一致（锚文件漂移拒绝），
       endpoint host/bucket 非空在档；
    5. archive 事实：version_id 非空、COMPLIANCE、retain_until 可按
       ISO-8601 tz-aware 解析、content-type x-ndjson、对象 hash/size 与
       当前源一致。
    """
    if raw.get("milestone") != WORM_MILESTONE \
            or raw.get("tool") != WORM_TOOL \
            or raw.get("command") != WORM_COMMAND:
        _problem(report, "verify-report-unrecognized",
                 "仅接受 audit_anchor_archive.py 的 verify 命令报告")
        return None
    if raw.get("status") != "pass" or raw.get("worm_verified") is not True \
            or raw.get("problems") != []:
        _problem(report, "verify-report-not-pass",
                 "verify 报告不是成功报告（status 非 pass / worm_verified "
                 "非 true / 含 problems）")
        return None
    source = raw.get("source")
    target = raw.get("target")
    archive = raw.get("archive")
    if not isinstance(source, dict) or not isinstance(target, dict) \
            or not isinstance(archive, dict):
        _problem(report, "verify-report-incomplete", "verify 报告缺关键事实块")
        return None
    if source != _source_block(src):
        _problem(report, "verify-report-source-mismatch",
                 "verify 报告 source 块与当前锚文件事实不符")
        return None
    host = target.get("endpoint_host")
    bucket = target.get("bucket")
    if not isinstance(host, str) or not host \
            or not isinstance(bucket, str) or not bucket:
        _problem(report, "verify-report-incomplete",
                 "verify 报告 target 缺 endpoint host/bucket")
        return None
    if target.get("key") != worm_object_key(src.sha256):
        _problem(report, "verify-report-key-mismatch",
                 "verify 报告记录 key 与本地锚文件重算 key 不符")
        return None
    version_id = archive.get("version_id")
    retain_raw = archive.get("retain_until")
    if not isinstance(version_id, str) or not version_id:
        _problem(report, "verify-report-incomplete",
                 "verify 报告 archive 缺 version_id")
        return None
    if archive.get("retention_mode") != WORM_RETENTION_MODE:
        _problem(report, "verify-report-retention-mode-mismatch",
                 f"verify 报告 retention_mode 非 {WORM_RETENTION_MODE}")
        return None
    if not isinstance(retain_raw, str) or _parse_iso_utc(retain_raw) is None:
        _problem(report, "verify-report-retention-invalid",
                 "verify 报告 retain_until 不可按 ISO-8601 tz-aware 解析")
        return None
    if archive.get("content_type") != WORM_CONTENT_TYPE:
        _problem(report, "verify-report-content-type-mismatch",
                 f"verify 报告 content_type 非 {WORM_CONTENT_TYPE}")
        return None
    if archive.get("object_sha256") != src.sha256 \
            or archive.get("object_size_bytes") != src.size_bytes:
        _problem(report, "verify-report-object-facts-mismatch",
                 "verify 报告对象 hash/size 与当前锚文件不符")
        return None
    return {"endpoint_host": host, "bucket": bucket,
            "key": target["key"], "version_id": version_id,
            "retention_mode": WORM_RETENTION_MODE, "retain_until": retain_raw,
            "content_type": archive["content_type"],
            "object_sha256": src.sha256, "object_size_bytes": src.size_bytes}


def _load_bound_verify_report(fs, verify_report: str, src: AnchorSource,
                              report: dict) -> BoundVerifyReport | None:
    """装载 + 绑定 verify 报告；返回含原始字节的快照（供逐字节副本）。"""
    raw = _read_verify_report_file(fs, verify_report, report)
    if raw is None:
        return None
    facts = _bind_verify_report_facts(raw, src, report)
    if facts is None:
        return None
    data = fs.read_bytes(Path(verify_report))
    return BoundVerifyReport(data=data,
                             sha256=hashlib.sha256(data).hexdigest(),
                             size_bytes=len(data), facts=facts)


# ---------------------------------------------------------------- 离线根/marker


def _root_inside_repo(resolved_root: Path, resolved_repo: Path) -> bool:
    """repo Containment 判定（Windows 盘符路径大小写不敏感）。"""
    root_parts = resolved_root.parts
    repo_parts = resolved_repo.parts
    if sys.platform == "win32":
        root_parts = tuple(part.casefold() for part in root_parts)
        repo_parts = tuple(part.casefold() for part in repo_parts)
    return root_parts[:len(repo_parts)] == repo_parts


def _offline_root_gate(fs, offline_root: str, report: dict) -> Path | None:
    """离线根契约校验：显式绝对/已存在/常规目录/无 symlink（含祖先）/
    不在仓库内/marker 字节精确。全部通过返回根 Path。"""
    raw = Path(offline_root)
    if not raw.is_absolute():
        _problem(report, "offline-root-not-absolute",
                 "--offline-root 必须是绝对路径（拒绝 CWD 相对歧义）")
        return None
    if fs.is_symlink(raw):
        _problem(report, "offline-root-symlink", "拒绝 symlink 离线根")
        return None
    if not fs.exists(raw):
        _problem(report, "offline-root-missing",
                 "离线根目录不存在（操作者须预创建，本工具绝不建根）")
        return None
    if not fs.is_dir(raw):
        _problem(report, "offline-root-not-directory", "离线根不是目录")
        return None
    if any(fs.is_symlink(ancestor) for ancestor in raw.parents):
        _problem(report, "offline-root-ancestor-symlink",
                 "离线根祖先含 symlink，拒绝")
        return None
    try:
        resolved_root = raw.resolve()
        resolved_repo = REPO_ROOT.resolve()
    except OSError:
        _problem(report, "offline-root-unresolvable", "离线根路径不可解析")
        return None
    if _root_inside_repo(resolved_root, resolved_repo):
        _problem(report, "offline-root-inside-repo",
                 "离线根不得位于仓库内（涵盖 .verify/artifacts）")
        return None
    marker = raw / MARKER_NAME
    if fs.is_symlink(marker):
        _problem(report, "marker-symlink", "marker 是 symlink，拒绝")
        return None
    if not fs.exists(marker):
        _problem(report, "marker-missing",
                 f"离线根缺操作者预创建 marker {MARKER_NAME}（本工具绝不创建）")
        return None
    if not fs.is_file(marker):
        _problem(report, "marker-not-regular", "marker 不是普通文件")
        return None
    if fs.read_bytes(marker) != MARKER_CONTENT:
        _problem(report, "marker-content-mismatch",
                 "marker 内容与契约字节不精确一致")
        return None
    return raw


# ---------------------------------------------------------------- 离线副本布局


def _expected_manifest_bytes(src: AnchorSource, vr: BoundVerifyReport,
                             source_name: str) -> bytes:
    """确定性 manifest：纯输入（锚 + 绑定事实 + 报告字节）的函数，零时钟。

    幂等 copy 与 verify 都按本函数逐字节复算——任何输入变化（不同锚/
    不同报告/不同报告名）必然产出不同 manifest，跨参数副本自然被拒。
    """
    manifest = {
        "schema_version": 1, "milestone": "M14-49",
        "tool": "audit_worm_offline_copy",
        "layout": f"{LAYOUT_PREFIX}/{src.sha256}",
        "anchor": {"name": ANCHOR_COPY_NAME, "sha256": src.sha256,
                   "size_bytes": src.size_bytes},
        "verify_report": {"name": REPORT_COPY_NAME,
                          "source_name": source_name,
                          "sha256": vr.sha256,
                          "size_bytes": vr.size_bytes},
        "worm": dict(vr.facts),
    }
    return json.dumps(manifest, indent=2, ensure_ascii=False,
                      sort_keys=True).encode("utf-8") + b"\n"


def _expected_files(src: AnchorSource, vr: BoundVerifyReport,
                    source_name: str) -> dict[str, bytes]:
    """离线副本三文件的期望字节（copy 写入 / verify 逐字节核对共用）。"""
    return {ANCHOR_COPY_NAME: src.data,
            REPORT_COPY_NAME: vr.data,
            MANIFEST_NAME: _expected_manifest_bytes(src, vr, source_name)}


def _offline_copy_state(fs, dir_path: Path, expected: dict[str, bytes],
                        report: dict) -> tuple[str | None, list[str]]:
    """观察离线副本布局：返回 (state, present)。

    state：``"absent"``（三文件全缺）/ ``"matching"``（全在且逐字节
    一致）/ ``None``（已记 problem：symlink/部分残留/字节不符/布局目录
    非常规）。全程零写入。
    """
    if fs.is_symlink(dir_path):
        _problem(report, "offline-copy-dir-symlink",
                 "内容寻址布局目录是 symlink，拒绝")
        return None, []
    present: list[str] = []
    for name in COPY_NAMES:
        target = dir_path / name
        if fs.is_symlink(target):
            _problem(report, "offline-copy-symlink",
                     f"离线副本文件是 symlink: {name}")
            return None, present
        if fs.exists(target):
            present.append(name)
    if not present:
        if fs.exists(dir_path) and not fs.is_dir(dir_path):
            _problem(report, "offline-copy-dir-not-regular",
                     "内容寻址布局目录不是常规目录")
            return None, []
        return "absent", []
    if len(present) != len(COPY_NAMES):
        _problem(report, "offline-copy-partial",
                 f"离线副本部分残留（{', '.join(present)}），拒绝覆盖——"
                 "请人工核查残留后处置")
        return None, present
    for name, expected_bytes in expected.items():
        target = dir_path / name
        if not fs.is_file(target):
            _problem(report, "offline-copy-not-regular",
                     f"离线副本文件不是普通文件: {name}")
            return None, present
        if fs.read_bytes(target) != expected_bytes:
            _problem(report, "offline-copy-byte-mismatch",
                     f"离线副本字节与期望不符: {name}")
            return None, present
    return "matching", present


def _record_bound_facts(report: dict, src: AnchorSource,
                        vr: BoundVerifyReport, verify_report: str,
                        root: Path) -> None:
    """绑定全过后把三方事实写入报告（source/worm/verify_report/offline）。"""
    report["source"] = _source_block(src)
    report["worm"] = dict(vr.facts)
    report["verify_report"] = {"name": Path(verify_report).name,
                               "sha256": vr.sha256,
                               "size_bytes": vr.size_bytes}
    report["offline"] = {"root": str(root),
                         "layout": f"{LAYOUT_PREFIX}/{src.sha256}",
                         "files": list(COPY_NAMES)}


# ---------------------------------------------------------------- 命令


def _validated_chain(args, *, fs, report) -> tuple[AnchorSource | None,
                                                    BoundVerifyReport | None,
                                                    Path | None]:
    """公共校验链：锚源 → verify 报告绑定 → 离线根/marker。

    任一环节失败即记 problem 并短路后续环节（返回含 None 的三元组）。
    """
    src = _load_anchor_source(fs, args.anchor_file, report)
    if src is None:
        return None, None, None
    vr = _load_bound_verify_report(fs, args.verify_report, src, report)
    if vr is None:
        return src, None, None
    root = _offline_root_gate(fs, args.offline_root, report)
    if root is None:
        return src, vr, None
    _record_bound_facts(report, src, vr, args.verify_report, root)
    return src, vr, root


def _cmd_preflight(args, *, fs, clock, suffix_gen) -> int:
    report = _new_report("preflight", clock)
    src, vr, root = _validated_chain(args, fs=fs, report=report)
    if root is None or report["problems"]:
        return _write_report(fs, clock, report, suffix_gen)
    expected = _expected_files(src, vr, Path(args.verify_report).name)
    state, present = _offline_copy_state(
        fs, layout_dir(root, src.sha256), expected, report)
    report["existing"] = {"state": state if state is not None else "invalid",
                          "present": present}
    return _write_report(fs, clock, report, suffix_gen)


def _cmd_copy(args, *, fs, clock, suffix_gen) -> int:
    # 参数门（fail-closed 最前置）：近似短语 → 零执行、零写入、零报告
    if args.confirm != CONFIRM_COPY:
        return EXIT_REJECT

    report = _new_report("copy", clock)
    src, vr, root = _validated_chain(args, fs=fs, report=report)
    if root is None or report["problems"]:
        return _write_report(fs, clock, report, suffix_gen)
    dir_path = layout_dir(root, src.sha256)
    expected = _expected_files(src, vr, Path(args.verify_report).name)
    state, _present = _offline_copy_state(fs, dir_path, expected, report)
    if state is None:
        return _write_report(fs, clock, report, suffix_gen)
    if state == "matching":
        report["copy"] = {"created": False, "existing_matched": True,
                          "idempotent": True}
        report["offline_verified"] = True
        return _write_report(fs, clock, report, suffix_gen)
    # absent → 原子写三文件（布局目录尚不存在；绝不覆盖任何既有文件）
    fs.mkdirs(dir_path)
    for name, data in expected.items():
        fs.write_bytes_atomic(dir_path / name, data)
    # 写后重读（与 M14-43 put 后重读同纪律）：任何漂移 fail-closed
    for name, data in expected.items():
        if fs.read_bytes(dir_path / name) != data:
            _problem(report, "post-copy-byte-mismatch",
                     f"写后重读不一致: {name}")
    if report["problems"]:
        return _write_report(fs, clock, report, suffix_gen)
    report["copy"] = {"created": True, "existing_matched": False,
                      "idempotent": False}
    report["offline_verified"] = True
    return _write_report(fs, clock, report, suffix_gen)


def _cmd_verify(args, *, fs, clock, suffix_gen) -> int:
    report = _new_report("verify", clock)
    src, vr, root = _validated_chain(args, fs=fs, report=report)
    if root is None or report["problems"]:
        return _write_report(fs, clock, report, suffix_gen)
    expected = _expected_files(src, vr, Path(args.verify_report).name)
    state, present = _offline_copy_state(
        fs, layout_dir(root, src.sha256), expected, report)
    report["existing"] = {"state": state if state is not None else "invalid",
                          "present": present}
    if state != "matching":
        if state == "absent":
            _problem(report, "offline-copy-missing",
                     "离线副本不存在（先运行 copy）")
        return _write_report(fs, clock, report, suffix_gen)
    report["offline_verified"] = True
    return _write_report(fs, clock, report, suffix_gen)


# ---------------------------------------------------------------- CLI


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit_worm_offline_copy.py",
        description="审计锚点 WORM 离线第二副本工具（fail-closed；零网络/"
                    "零 env/零子进程，纯本地复制与核验）")
    sub = parser.add_subparsers(dest="command")

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--anchor-file", required=True,
                               metavar="PATH", help="审计锚点 JSONL 文件路径")
        subparser.add_argument("--verify-report", required=True,
                               metavar="PATH",
                               help="audit_anchor_archive.py 的 verify 成功"
                                    "报告 JSON 文件路径")
        subparser.add_argument("--offline-root", required=True,
                               metavar="DIR",
                               help="离线根目录（绝对路径；须为仓库外常规"
                                    "目录且含操作者预创建 marker）")

    add_common(sub.add_parser("preflight", help="只读预检（零写入）"))
    copier = sub.add_parser("copy", help="执行离线第二副本（需确认短语）")
    add_common(copier)
    copier.add_argument("--confirm", help="确认短语（一字不差）")
    add_common(sub.add_parser("verify", help="只读重算核验离线副本"))
    return parser


def main(argv=None, *, fs=None, clock=None, suffix_gen=None) -> int:
    """CLI 入口（fs/clock/suffix_gen 全注入；测试零网络零真实副作用）。

    终防线：命令执行中的任何意外异常（含报告写盘失败、离线写入中断）
    一律折叠为 exit 2——stderr 只记异常类型名，绝不上抛原始 traceback，
    也绝不携带秘密形态值（argparse 的 SystemExit 属 BaseException，
    不受影响）。三文件写入非单一事务：中断可留部分残留，后续运行按
    partial 拒绝零覆盖（fail-closed）。
    """
    args = _build_parser().parse_args(argv)
    fs = RealFS() if fs is None else fs
    clock = SystemClock() if clock is None else clock
    suffix_gen = _random_suffix if suffix_gen is None else suffix_gen
    try:
        if args.command == "preflight":
            return _cmd_preflight(args, fs=fs, clock=clock,
                                  suffix_gen=suffix_gen)
        if args.command == "copy":
            return _cmd_copy(args, fs=fs, clock=clock, suffix_gen=suffix_gen)
        if args.command == "verify":
            return _cmd_verify(args, fs=fs, clock=clock,
                               suffix_gen=suffix_gen)
    except Exception as exc:  # noqa: BLE001 —— 终防线 fail-closed（类型名之外零信息）
        print(f"audit_worm_offline_copy: unexpected {type(exc).__name__}; "
              "fail-closed exit 2", file=sys.stderr)
        return EXIT_REJECT
    return EXIT_REJECT


if __name__ == "__main__":
    sys.exit(main())
