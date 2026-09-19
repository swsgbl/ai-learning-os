#!/usr/bin/env python
r"""M14-43 审计锚点 WORM/对象锁归档工具（fail-closed，单文件纯标准库）。

把 M14-42 落地的生产审计锚点文件（app.ops.audit_chain_anchor 产出的
锚点 JSONL）复制进可验证的 WORM/对象锁归档，并证明归档字节与保留
元数据。三命令：

- ``preflight``：本地锚链完整校验（字段集/类型/canonical JSON/
  anchor_hash 重算/sequence 非负且严格递增——与 M14-42 生产端
  ``app.ops.audit_chain_anchor`` 同契约，允许跳号、不要求 +1 连续/
  anchored_at 必须可按 ISO-8601 解析/previous 链接/sequence 0 行
  head 必须是 genesis 常量/symlink 源拒绝）+ endpoint 策略（公网必须
  HTTPS，loopback/RFC1918 可 HTTP，userinfo 内嵌凭据拒绝）+ 凭据只认
  环境变量（缺失 → 零 S3 访问）+ bucket 存在/versioning Enabled/
  Object Lock enabled；全程零写操作。
- ``archive``：先跑全部 preflight 检查；确认短语一字不差 +
  ``--retention-mode COMPLIANCE``（不实现可被绕过的 GOVERNANCE）+
  tz-aware 严格未来 ``--retain-until`` 三道参数门（不满足 → 零执行
  零报告）。对象 key 内容寻址 ``audit-anchor/<sha256>/audit-anchor.jsonl``；
  已存在：字节不符 fail-closed，字节相符则核验 retention 事实后绝不
  覆盖；新对象恰好一次 put（x-ndjson + SHA-256 checksum + COMPLIANCE
  + retain-until + 显式 ``if_none_match="*"`` 条件创建——经注入协议
  传递、仅在真实适配器边界映射为 PutObject ``IfNoneMatch="*"``：head
  判定不存在后被并发抢占时服务端拒绝，fail-closed 绝不覆盖竞态写入），
  put 后重读字节与元数据，任何漂移 fail-closed；报告 retain_until 的
  canonical UTC 表示保留非零微秒（fractional 秒不截断，verify 复算才
  精确一致）。
- ``verify``：重复本地校验 + bucket WORM preflight + 归档报告伴生
  sidecar 哈希核验（sidecar 空/非 UTF-8/畸形 → exit 2 报告 problem，
  绝不抛异常）+ 归档报告绑定当前调用事实（status=pass、worm_verified
  true、source 块、endpoint host、bucket、key、对象 hash/size/
  retention/content-type；跨 bucket/endpoint 或失败归档报告一律拒绝）
  + head/get 按报告记录的 version 定向 + 对象逐字节 SHA-256、version
  ID 与报告记录值精确一致、COMPLIANCE/retain-until/content-type/size
  精确核验（元数据 size 同时对照本地源与回读字节数）。

设计纪律（与 tools/ops 既有工具同款）：S3Client/FS/Clock/env/报告名
随机后缀生成器全注入，开发回合零网络零真实 S3；boto3 仅在真实执行
适配器工厂函数体内懒导入；报告原子写 gitignored
``.verify/artifacts/m14-43-audit-worm-archive/``（三命令统一三工件：
JSON + Markdown + 字节精确 ``.json.sha256`` sidecar——sha256sum 形态；
M14-58 前仅 archive 写 sidecar，preflight/verify 报告缺伴生摘要，
M14-55 更新器对历史真实 verify 报告按 ``worm-sidecar-missing``
fail-closed 拒绝；失败证据与成功证据同样可被摘要校验）。报告名
``<command>-<stamp>-<随机后缀>`` 真正防碰撞：每份报告名带
``secrets.token_hex`` CSPRNG 随机后缀——仅靠顺序探测防不了并发（两个
进程可在各自探测-写入窗口内同时观察到同一候选名不存在而双双选中、
事后互相覆盖证据），随机后缀把同 command 同秒并发撞名压到约 2**-128；
存在性探测循环仍是 fail-closed 兜底（候选名任一工件、含残留孤儿
``.md`` 已存在即递增 ``-2``/``-3``），绝不覆盖既有报告工件。残余
假设（如实声明）：探测与写入之间存在非原子窗口、本工具不做全局
互斥——随机后缀只把窗口内撞名压到密码学随机概率，不是零。
endpoint 只记 host，绝不含凭据、完整 endpoint 或原始异常；
一切拒绝 exit 2。

本工具不删除、不覆盖任何已归档对象；``pass`` 不等于 production ready，
全局 ``production_ready=false`` 不变。真实 WORM 执行仅由 supervisor 在
获准窗口进行。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import itertools
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

#: 仓库根（报告工件目录锚点）
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 报告工件目录（gitignored）
ARTIFACT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-43-audit-worm-archive"

#: S3 凭据只认这两个环境变量；绝不接受 CLI 值，绝不记录其值
ENV_ACCESS_KEY = "AIOS_AUDIT_ARCHIVE_ACCESS_KEY"
ENV_SECRET_KEY = "AIOS_AUDIT_ARCHIVE_SECRET_KEY"

#: archive 执行确认短语（一字不差，近似短语零执行零报告）
CONFIRM_ARCHIVE = "EXECUTE AUDIT ANCHOR WORM ARCHIVE"

#: 归档对象内容类型 / 唯一允许的保留模式 / 内容寻址 key 形态
CONTENT_TYPE = "application/x-ndjson"
RETENTION_MODE_COMPLIANCE = "COMPLIANCE"
KEY_PREFIX = "audit-anchor"
KEY_SUFFIX = "audit-anchor.jsonl"

#: 新对象 put 的条件创建哨兵：经注入协议显式传递，仅在真实适配器
#: 边界映射为 AWS PutObject ``IfNoneMatch="*"`` 头——head 判定不存在
#: 之后、put 之前被并发抢占时由服务端拒绝创建，客户端 fail-closed
IF_NONE_MATCH_CREATE = "*"

#: 报告名随机后缀宽度：secrets.token_hex(16) → 32 hex chars（128 bits）。
#: 同 command 同秒的并发进程在各自探测-写入窗口内撞名概率约 2**-128
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

class S3Error(Exception):
    """S3 访问失败（真实适配器把底层客户端异常统一折叠为本类型）。"""


@dataclass(frozen=True)
class ObjectHead:
    """head_object 的结构化元数据（retain_until 为 tz-aware datetime）。"""

    version_id: str
    object_lock_mode: str | None
    retain_until: datetime | None
    content_type: str | None
    size: int


_SECRET_KV_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?key)\b"
    r"\s*[=:]\s*\S+")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+\S+")


def redact_secrets(text: str) -> str:
    """报告终防线：折叠 key=value / Bearer 形态的疑似秘密值。"""
    text = _SECRET_KV_RE.sub("<redacted>", text)
    return _BEARER_RE.sub("<redacted>", text)


def _host_is_local(host: str) -> bool:
    """loopback / RFC1918 / 唯一本地地址——安全本机 MinIO 形态。"""
    if host == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private


def _endpoint_policy(endpoint: str) -> tuple[str | None, str | None]:
    """(host, problem)。公网必须 HTTPS；本地地址可 HTTP；拒绝 userinfo。"""
    if not endpoint or not endpoint.strip():
        return None, "endpoint-empty"
    parts = urlsplit(endpoint.strip())
    if parts.username is not None or parts.password is not None:
        return None, "endpoint-embeds-credentials"
    if parts.scheme not in ("http", "https"):
        return None, "endpoint-scheme-not-allowed"
    host = parts.hostname
    if not host:
        return None, "endpoint-invalid"
    if parts.scheme == "http" and not _host_is_local(host):
        return None, "endpoint-http-not-allowed"
    return host, None


def object_key(sha256: str) -> str:
    """内容寻址 key：全文件 SHA-256 决定对象路径。"""
    return f"{KEY_PREFIX}/{sha256}/{KEY_SUFFIX}"


def _utc_z(dt: datetime) -> str:
    """canonical UTC Z 形态；非零微秒原样保留（6 位小数）。

    截断 fractional-second ``--retain-until`` 会让 archive 报告记录值
    与对象真实 retain_until 不一致，verify 复算必然误报漂移——故
    微秒非零时必须出现在报告 canonical 表示里（与 ``_parse_iso_utc``
    精确往返）。
    """
    dt = dt.astimezone(UTC)
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    if dt.microsecond:
        return f"{base}.{dt.microsecond:06d}Z"
    return f"{base}Z"


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


def _parse_retain_until(raw: str | None, now: datetime) -> datetime | None:
    """ISO-8601 + tz-aware + 严格未来；任何不满足返回 None。"""
    dt = _parse_iso_utc(raw)
    if dt is None or dt <= now:
        return None
    return dt


def _canonical_json_bytes(payload: dict) -> bytes:
    """与锚点生产端同源的 canonical JSON（sort_keys/紧凑分隔/utf-8）。"""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True)
class AnchorSource:
    """本地锚文件全部校验通过后的事实快照（data 仅供归档体使用）。"""

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

    - sequence 非负（负值行级/链级都拒绝）；
    - sequence 严格递增——允许跳号（锚文件从 DB 既有进度开始锚定是
      合法形态），不要求 +1 连续；首锚也不必是 sequence=0；
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
    """读取并完整校验锚源；任何拒绝都记 problem 并返回 None（零 S3）。"""
    path = Path(anchor_file)
    if not fs.exists(path):
        _problem(report, "anchor-file-missing", "锚文件不存在")
        return None
    if fs.is_symlink(path):
        _problem(report, "anchor-file-symlink", "拒绝 symlink 锚源")
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


class SystemClock:
    """真实 UTC 时钟（测试注入 FakeClock）。"""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def utc_now_iso(self) -> str:
        return self.now().strftime("%Y-%m-%dT%H:%M:%SZ")

    def stamp(self) -> str:
        return self.now().strftime("%Y%m%d-%H%M%S")


class RealFS:
    """真实文件系统适配（原子写：tmp + os.replace）。"""

    def exists(self, path) -> bool:
        return os.path.exists(path)

    def is_file(self, path) -> bool:
        return os.path.isfile(path)

    def is_symlink(self, path) -> bool:
        return os.path.islink(path)

    def read_bytes(self, path) -> bytes:
        return Path(path).read_bytes()

    def write_text_atomic(self, path, text: str) -> None:
        target = Path(path)
        tmp = target.with_name(target.name + ".tmp")
        # 字节落盘：Windows 文本模式会把 \n 翻译成 \r\n，sidecar 的
        # SHA-256 是对报告字节算的——翻译后 verify 复算必然失败
        tmp.write_bytes(text.encode("utf-8"))
        os.replace(tmp, target)

    def mkdirs(self, path) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)


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
    return {"schema_version": 1, "milestone": "M14-43", "command": command,
            "tool": "audit_anchor_archive",
            "started_at_utc": clock.utc_now_iso(), "ended_at_utc": None,
            "status": "pass", "problems": [], "worm_verified": False,
            "source": None, "target": None}


def _random_suffix() -> str:
    """CSPRNG 报告名随机后缀（默认 32 hex chars；测试注入替身）。"""
    return secrets.token_hex(REPORT_SUFFIX_BYTES)


def _unique_report_name(fs, command: str, clock, suffix_gen) -> str:
    """防碰撞报告名：command + 秒级 stamp + CSPRNG 随机后缀。

    仅靠顺序探测防不了并发：两个进程可在各自的探测-写入窗口内同时
    观察到同一候选名不存在而双双选中，事后互相覆盖证据。此处每份
    报告名都带 ``suffix_gen()`` 生成的随机后缀（默认
    ``secrets.token_hex``，32 hex chars = 128 bits），把同 command 同秒
    并发进程的撞名概率压到约 2**-128。存在性探测循环保留为 fail-closed
    兜底：候选名的 ``.json``/``.json.sha256``/``.md`` 任一已存在（含
    残留孤儿工件）即换 ``-2``/``-3`` 递增名——绝不覆盖既有报告工件。
    残余假设：探测与写入之间存在非原子窗口（无全局互斥），随机后缀
    只把窗口内撞名压到密码学随机概率，不承诺为零。
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
    """原子写 JSON + Markdown + 字节精确 .sha256 sidecar 并定退出码。

    M14-58 起三命令（preflight/archive/verify）统一三工件：M14-58 前
    仅 archive 命令写 ``.json.sha256`` sidecar，verify/preflight 报告缺
    伴生摘要——M14-55 更新器对真实 verify 报告按 ``worm-sidecar-missing``
    fail-closed 拒绝。失败证据与成功证据同样可被摘要校验（fail-closed
    报告不是二等证据）。sidecar 为 sha256sum 形态
    ``digest␣␣name.json\\n``，摘要对所写 JSON 字节精确计算。
    """
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
    """Markdown 报告：只记 host/事实，绝不渲染 scheme/URL/凭据。"""
    lines = [f"# M14-43 审计锚点 WORM 归档 — {report['command']}", "",
             f"- status: **{report['status']}**",
             f"- worm_verified: **{report['worm_verified']}**",
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
    target = report.get("target")
    if target:
        lines += [f"- endpoint host: {target['endpoint_host']}",
                  f"- bucket: `{target['bucket']}`",
                  f"- key: `{target['key']}`"]
    archive = report.get("archive")
    if archive:
        lines += [f"- created: {archive['created']}",
                  f"- existing_matched: {archive['existing_matched']}",
                  f"- version_id: `{archive['version_id']}`",
                  f"- retention_mode: {archive['retention_mode']}",
                  f"- retain_until: {archive['retain_until']}",
                  f"- object sha256: `{archive['object_sha256']}`",
                  f"- object size_bytes: {archive['object_size_bytes']}",
                  f"- content_type: `{archive['content_type']}`"]
    archive_report = report.get("archive_report")
    if archive_report:
        lines += [(f"- archive report: `{archive_report['name']}` "
                   f"(sha256 `{archive_report['sha256']}`)")]
    problems = report.get("problems") or []
    if problems:
        lines += ["", "## problems"]
        lines += [f"- `{p['code']}`: {p['detail']}" for p in problems]
    lines.append("")
    return "\n".join(lines)


def _client_for(s3, args, env):
    """注入优先；未注入时构建真实适配器（boto3 懒导入）。"""
    if s3 is not None:
        return s3
    return Boto3S3Client.from_endpoint(args.endpoint, env)


def _local_gate(args, env, fs, report) -> tuple[AnchorSource | None,
                                                 str | None]:
    """锚源校验 + endpoint 策略 + 凭据存在性；三者全过才允许触 S3。

    返回 (src, host)：verify 的报告绑定需要当前调用的 host 事实，
    故与 src 一并返回（报告 source/target 块仅在两者均有效时记录）。
    """
    src = _load_anchor_source(fs, args.anchor_file, report)
    host, code = _endpoint_policy(args.endpoint)
    if code:
        _problem(report, code, "endpoint 策略拒绝（公网须 HTTPS、本地可 HTTP、"
                               "拒绝 userinfo）")
    if not env.get(ENV_ACCESS_KEY) or not env.get(ENV_SECRET_KEY):
        _problem(report, "s3-credentials-missing",
                 "环境变量凭据缺失（凭据值永不记录）")
    if src is not None and host is not None:
        report["source"] = _source_block(src)
        report["target"] = {"endpoint_host": host, "bucket": args.bucket,
                            "key": object_key(src.sha256)}
    return src, host


def _bucket_worm_problems(client, bucket: str, report: dict) -> bool:
    """bucket 存在 + versioning Enabled + Object Lock enabled；零写操作。"""
    try:
        client.head_bucket(bucket=bucket)
        versioning = client.get_bucket_versioning(bucket=bucket)
        object_lock = client.get_object_lock_configuration(bucket=bucket)
    except S3Error:
        _problem(report, "s3-bucket-not-reachable", "bucket 不可达或不存在")
        return False
    ok = True
    if versioning != "Enabled":
        _problem(report, "s3-bucket-versioning-not-enabled",
                 f"versioning 状态非 Enabled: {versioning!r}")
        ok = False
    if not object_lock:
        _problem(report, "s3-bucket-object-lock-not-enabled",
                 "bucket Object Lock 未启用")
        ok = False
    return ok


def _cmd_preflight(args, *, s3, fs, clock, env, suffix_gen) -> int:
    report = _new_report("preflight", clock)
    _local_gate(args, env, fs, report)
    if not report["problems"]:
        client = _client_for(s3, args, env)
        _bucket_worm_problems(client, args.bucket, report)
    return _write_report(fs, clock, report, suffix_gen)


def _archive_block(*, created: bool, existing_matched: bool,
                   version_id: str, retain_until: datetime, sha: str,
                   size: int, content_type: str | None) -> dict:
    return {"created": created, "existing_matched": existing_matched,
            "version_id": version_id,
            "retention_mode": RETENTION_MODE_COMPLIANCE,
            "retain_until": _utc_z(retain_until), "object_sha256": sha,
            "object_size_bytes": size, "content_type": content_type}


def _archive_new(client, args, src: AnchorSource, key: str,
                 retain_until: datetime, report: dict) -> None:
    """新对象：恰好一次条件创建 put，随后按新 version 定向重读，任何漂移 fail-closed。

    put 显式携带 ``if_none_match="*"``（仅创建语义）：存在性 head 判定
    「不存在」之后被并发抢占写入时，服务端按条件拒绝本次 put，本工具
    按 ``s3-put-failed`` fail-closed——绝不覆盖竞态写入者。
    """
    try:
        version_id = client.put_object(
            bucket=args.bucket, key=key, body=src.data,
            content_type=CONTENT_TYPE, checksum_sha256=src.sha256,
            object_lock_mode=RETENTION_MODE_COMPLIANCE,
            retain_until=retain_until,
            if_none_match=IF_NONE_MATCH_CREATE)
    except S3Error:
        _problem(report, "s3-put-failed", "put_object 失败（归档未确认）")
        return
    if not version_id:
        _problem(report, "post-put-version-missing", "put 未返回 version ID")
        return
    try:
        head = client.head_object(bucket=args.bucket, key=key,
                                  version_id=version_id)
        body = client.get_object(bucket=args.bucket, key=key,
                                 version_id=version_id)
    except S3Error:
        _problem(report, "post-put-read-failed", "put 后重读失败")
        return
    if head is None:
        _problem(report, "post-put-head-missing", "put 后对象元数据缺失")
        return
    if head.version_id != version_id:
        _problem(report, "post-put-version-mismatch",
                 "version ID 与 put 返回不一致")
    if head.object_lock_mode != RETENTION_MODE_COMPLIANCE:
        _problem(report, "post-put-retention-mode-mismatch",
                 "对象锁模式非 COMPLIANCE")
    if head.retain_until != retain_until:
        _problem(report, "post-put-retain-until-mismatch",
                 "retain-until 与请求不一致")
    if head.content_type != CONTENT_TYPE:
        _problem(report, "post-put-content-type-mismatch",
                 f"put 后对象 content-type 非 {CONTENT_TYPE}")
    if head.size != len(body):
        _problem(report, "post-put-size-mismatch",
                 "put 后元数据 size 与回读字节数不符")
    if hashlib.sha256(body).hexdigest() != src.sha256:
        _problem(report, "post-put-byte-mismatch", "回读字节 SHA-256 不符")
    if report["problems"]:
        return
    report["archive"] = _archive_block(
        created=True, existing_matched=False, version_id=version_id,
        retain_until=retain_until, sha=src.sha256, size=len(body),
        content_type=head.content_type)
    report["worm_verified"] = True


def _archive_existing(client, args, src: AnchorSource, key: str,
                      retain_until: datetime, head, report: dict) -> None:
    """已存在对象：按其 version 定向读取；任何事实不符 fail-closed，绝不覆盖。"""
    try:
        body = client.get_object(bucket=args.bucket, key=key,
                                 version_id=head.version_id or None)
    except S3Error:
        _problem(report, "s3-get-failed", "已存在对象读取失败")
        return
    if hashlib.sha256(body).hexdigest() != src.sha256:
        _problem(report, "existing-object-byte-mismatch",
                 "已存在对象字节与锚文件不符")
    if not head.version_id:
        _problem(report, "existing-object-version-missing",
                 "已存在对象缺 version ID")
    if head.object_lock_mode != RETENTION_MODE_COMPLIANCE:
        _problem(report, "existing-object-retention-mode-mismatch",
                 "已存在对象锁模式非 COMPLIANCE")
    if head.retain_until != retain_until:
        _problem(report, "existing-object-retain-until-mismatch",
                 "已存在对象 retain-until 与请求不符")
    if head.content_type != CONTENT_TYPE:
        _problem(report, "existing-object-content-type-mismatch",
                 f"已存在对象 content-type 非 {CONTENT_TYPE}")
    if head.size != len(body):
        _problem(report, "existing-object-size-mismatch",
                 "已存在对象元数据 size 与读取字节不符")
    if report["problems"]:
        return
    report["archive"] = _archive_block(
        created=False, existing_matched=True, version_id=head.version_id,
        retain_until=retain_until, sha=src.sha256, size=len(body),
        content_type=head.content_type)
    report["worm_verified"] = True


def _cmd_archive(args, *, s3, fs, clock, env, suffix_gen) -> int:
    # 参数门（fail-closed 最前置）：不满足 → 零执行、零 S3、零报告
    if args.confirm != CONFIRM_ARCHIVE:
        return EXIT_REJECT
    if args.retention_mode != RETENTION_MODE_COMPLIANCE:
        return EXIT_REJECT
    retain_until = _parse_retain_until(args.retain_until, clock.now())
    if retain_until is None:
        return EXIT_REJECT

    report = _new_report("archive", clock)
    src, _host = _local_gate(args, env, fs, report)
    if src is None or report["problems"]:
        return _write_report(fs, clock, report, suffix_gen)
    client = _client_for(s3, args, env)
    if not _bucket_worm_problems(client, args.bucket, report):
        return _write_report(fs, clock, report, suffix_gen)

    key = object_key(src.sha256)
    try:
        head = client.head_object(bucket=args.bucket, key=key)
    except S3Error:
        _problem(report, "s3-head-failed", "对象存在性探测失败")
        return _write_report(fs, clock, report, suffix_gen)
    if head is None:
        _archive_new(client, args, src, key, retain_until, report)
    else:
        _archive_existing(client, args, src, key, retain_until, head, report)
    return _write_report(fs, clock, report, suffix_gen)


def _load_archive_report(fs, report_arg, src: AnchorSource, host: str,
                         bucket: str,
                         report: dict) -> tuple[dict, datetime] | None:
    """装载归档报告并核验其绑定当前调用事实（任一不符 → exit 2 problem）。

    核验链（顺序即防线）：
    1. sidecar fail-closed：空/非 UTF-8/缺 64 位十六进制摘要 →
       ``verify-archive-report-sidecar-invalid``（绝不抛异常）；摘要与
       报告字节不一致 → ``verify-archive-report-sidecar-mismatch``；
    2. 合法 JSON + M14-43 archive 报告形态；
    3. status=pass 且 worm_verified=true（失败归档报告不可验）；
    4. source 块与当前锚文件重算事实一致（防「报告对的是另一份锚文件」）；
    5. endpoint host / bucket 与当前调用一致（跨 bucket/endpoint 拒绝）；
    6. 记录 key 与本地重算 key 一致（锚文件漂移拒绝）；
    7. 对象事实（hash/size/retention mode/content-type）与当前源一致；
    8. version ID 非空 + retain_until 可解析。
    """
    if not report_arg:
        _problem(report, "verify-report-argument-missing", "缺少 --report")
        return None
    name = Path(report_arg)
    if name.is_absolute() or name.parent != Path(".") \
            or ".." in name.parts or name.name != report_arg:
        _problem(report, "verify-report-path-outside-artifacts",
                 "--report 只接受工件目录内的裸文件名")
        return None
    json_path = ARTIFACT_DIR / name
    sidecar_path = ARTIFACT_DIR / f"{name.name}.sha256"
    if not fs.exists(json_path) or not fs.exists(sidecar_path):
        _problem(report, "verify-archive-report-missing",
                 "归档报告或其 sha256 sidecar 缺失")
        return None
    report_bytes = fs.read_bytes(json_path)
    try:
        sidecar_text = fs.read_bytes(sidecar_path).decode("utf-8")
    except UnicodeDecodeError:
        _problem(report, "verify-archive-report-sidecar-invalid",
                 "sidecar 不是有效 UTF-8")
        return None
    fields = sidecar_text.split()
    if not fields or _HEX64.fullmatch(fields[0]) is None:
        _problem(report, "verify-archive-report-sidecar-invalid",
                 "sidecar 缺 64 位十六进制 SHA-256 摘要")
        return None
    digest = hashlib.sha256(report_bytes).hexdigest()
    if digest != fields[0]:
        _problem(report, "verify-archive-report-sidecar-mismatch",
                 "归档报告与其 sha256 sidecar 不一致")
        return None
    try:
        recorded = json.loads(report_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _problem(report, "verify-archive-report-not-json",
                 "归档报告不是合法 JSON")
        return None
    if not isinstance(recorded, dict) \
            or recorded.get("milestone") != "M14-43" \
            or recorded.get("command") != "archive":
        _problem(report, "verify-archive-report-unrecognized",
                 "归档报告形态不符")
        return None
    if recorded.get("status") != "pass" \
            or recorded.get("worm_verified") is not True:
        _problem(report, "verify-archive-report-not-pass",
                 "归档报告不是成功报告（status 非 pass 或 worm_verified "
                 "非 true），失败归档不可验")
        return None
    archive = recorded.get("archive")
    target = recorded.get("target")
    source = recorded.get("source")
    if not isinstance(archive, dict) or not isinstance(target, dict) \
            or not isinstance(source, dict):
        _problem(report, "verify-archive-report-incomplete",
                 "归档报告缺关键事实")
        return None
    if source != report.get("source"):
        _problem(report, "verify-archive-report-source-mismatch",
                 "归档报告 source 块与当前锚文件事实不符")
        return None
    if target.get("endpoint_host") != host or target.get("bucket") != bucket:
        _problem(report, "verify-archive-report-target-mismatch",
                 "归档报告来自其他 endpoint/bucket，拒绝核验")
        return None
    if target.get("key") != object_key(src.sha256):
        _problem(report, "verify-anchor-file-drifted",
                 "锚文件与归档报告记录的 key 不符")
        return None
    if archive.get("object_sha256") != src.sha256 \
            or archive.get("object_size_bytes") != src.size_bytes \
            or archive.get("retention_mode") != RETENTION_MODE_COMPLIANCE \
            or archive.get("content_type") != CONTENT_TYPE:
        _problem(report, "verify-archive-report-archive-facts-mismatch",
                 "归档报告对象事实（hash/size/retention/content-type）"
                 "与当前源不符")
        return None
    retain_dt = _parse_iso_utc(archive.get("retain_until"))
    if retain_dt is None or not archive.get("version_id"):
        _problem(report, "verify-archive-report-incomplete",
                 "归档报告缺保留事实")
        return None
    report["archive_report"] = {"sha256": digest, "name": name.name}
    return archive, retain_dt


def _cmd_verify(args, *, s3, fs, clock, env, suffix_gen) -> int:
    report = _new_report("verify", clock)
    src, host = _local_gate(args, env, fs, report)
    if src is None or report["problems"]:
        return _write_report(fs, clock, report, suffix_gen)
    loaded = _load_archive_report(fs, args.report, src, host, args.bucket,
                                  report)
    if loaded is None or report["problems"]:
        return _write_report(fs, clock, report, suffix_gen)
    archive_recorded, retain_until = loaded
    version_id = archive_recorded["version_id"]

    client = _client_for(s3, args, env)
    if not _bucket_worm_problems(client, args.bucket, report):
        return _write_report(fs, clock, report, suffix_gen)

    key = object_key(src.sha256)
    try:
        head = client.head_object(bucket=args.bucket, key=key,
                                  version_id=version_id)
    except S3Error:
        _problem(report, "verify-object-head-failed",
                 "归档对象元数据读取失败")
        return _write_report(fs, clock, report, suffix_gen)
    if head is None:
        _problem(report, "verify-object-missing", "归档对象不存在")
        return _write_report(fs, clock, report, suffix_gen)
    if not head.version_id:
        _problem(report, "verify-object-version-missing", "对象缺 version ID")
    if head.version_id != version_id:
        _problem(report, "verify-object-version-mismatch",
                 "对象 version ID 与归档报告记录值不符")
    if head.object_lock_mode != RETENTION_MODE_COMPLIANCE:
        _problem(report, "verify-object-retention-mode-mismatch",
                 "对象锁模式非 COMPLIANCE")
    if head.retain_until != retain_until:
        _problem(report, "verify-object-retain-until-mismatch",
                 "retain-until 与归档报告不符")
    if head.content_type != CONTENT_TYPE:
        _problem(report, "verify-object-content-type-mismatch",
                 f"对象 content-type 非 {CONTENT_TYPE}")
    if head.size != src.size_bytes:
        _problem(report, "verify-object-size-mismatch",
                 "对象元数据 size 与本地锚文件不符")
    if report["problems"]:
        return _write_report(fs, clock, report, suffix_gen)
    try:
        body = client.get_object(bucket=args.bucket, key=key,
                                 version_id=version_id)
    except S3Error:
        _problem(report, "verify-object-read-failed", "归档对象字节读取失败")
        return _write_report(fs, clock, report, suffix_gen)
    if hashlib.sha256(body).hexdigest() != src.sha256:
        _problem(report, "verify-object-byte-mismatch",
                 "对象字节 SHA-256 与锚文件不符")
        return _write_report(fs, clock, report, suffix_gen)
    if len(body) != head.size:
        _problem(report, "verify-object-size-mismatch",
                 "回读字节数与对象元数据 size 不符")
        return _write_report(fs, clock, report, suffix_gen)
    report["archive"] = {"created": archive_recorded.get("created"),
                         "existing_matched":
                             archive_recorded.get("existing_matched"),
                         "version_id": head.version_id,
                         "retention_mode": RETENTION_MODE_COMPLIANCE,
                         "retain_until": archive_recorded["retain_until"],
                         "object_sha256": src.sha256,
                         "object_size_bytes": len(body),
                         "content_type": head.content_type}
    report["worm_verified"] = True
    return _write_report(fs, clock, report, suffix_gen)


class Boto3S3Client:
    """真实执行 S3 适配器（仅 supervisor 获准窗口使用；测试一律注入替身）。

    底层客户端异常统一折叠为 :class:`S3Error`（固定词汇，绝不外泄原始
    异常文本）；head 的未命中折叠为 ``None`` 以匹配注入协议。
    """

    _NOT_FOUND = frozenset({"404", "NoSuchKey", "NotFound"})

    def __init__(self, client) -> None:
        self._client = client

    @classmethod
    def from_endpoint(cls, endpoint: str, env) -> Boto3S3Client:
        import boto3  # 懒导入：仅真实执行触达，测试路径永不加载
        credentials = {"aws_access_key_id": env.get(ENV_ACCESS_KEY, ""),
                       "aws_secret_access_key": env.get(ENV_SECRET_KEY, "")}
        return cls(boto3.client("s3", endpoint_url=endpoint,
                                **credentials))

    @staticmethod
    def _not_found(exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        if not isinstance(response, dict):
            return False
        code = response.get("Error", {}).get("Code")
        return code in Boto3S3Client._NOT_FOUND

    def head_bucket(self, *, bucket: str) -> None:
        try:
            self._client.head_bucket(Bucket=bucket)
        except Exception as exc:
            raise S3Error("s3-bucket-request-failed") from exc

    def get_bucket_versioning(self, *, bucket: str) -> str | None:
        try:
            return self._client.get_bucket_versioning(
                Bucket=bucket).get("Status")
        except Exception as exc:
            raise S3Error("s3-versioning-request-failed") from exc

    def get_object_lock_configuration(self, *, bucket: str) -> bool:
        try:
            resp = self._client.get_object_lock_configuration(Bucket=bucket)
            enabled = resp["ObjectLockConfiguration"]["ObjectLockEnabled"]
            return enabled == "Enabled"
        except Exception as exc:
            raise S3Error("s3-object-lock-request-failed") from exc

    def head_object(self, *, bucket: str, key: str,
                    version_id: str | None = None) -> ObjectHead | None:
        params: dict = {"Bucket": bucket, "Key": key}
        if version_id:
            params["VersionId"] = version_id
        try:
            resp = self._client.head_object(**params)
        except Exception as exc:
            if self._not_found(exc):
                return None
            raise S3Error("s3-head-request-failed") from exc
        retain = resp.get("ObjectLockRetainUntilDate")
        return ObjectHead(
            version_id=str(resp.get("VersionId") or ""),
            object_lock_mode=resp.get("ObjectLockMode"),
            retain_until=retain if isinstance(retain, datetime) else None,
            content_type=resp.get("ContentType"),
            size=int(resp.get("ContentLength") or 0))

    def get_object(self, *, bucket: str, key: str,
                   version_id: str | None = None) -> bytes:
        params: dict = {"Bucket": bucket, "Key": key}
        if version_id:
            params["VersionId"] = version_id
        try:
            return self._client.get_object(**params)["Body"].read()
        except Exception as exc:
            raise S3Error("s3-get-request-failed") from exc

    def put_object(self, *, bucket: str, key: str, body: bytes,
                   content_type: str, checksum_sha256: str,
                   object_lock_mode: str,
                   retain_until: datetime,
                   if_none_match: str | None = None) -> str:
        try:
            # AWS ChecksumSHA256 线上形态是 base64；注入协议保持 hex，
            # hex→base64 转换只发生在本适配器边界
            wire = base64.b64encode(
                bytes.fromhex(checksum_sha256)).decode("ascii")
        except ValueError as exc:
            raise S3Error("s3-checksum-encoding-failed") from exc
        params: dict = {"Bucket": bucket, "Key": key, "Body": body,
                        "ContentType": content_type,
                        "ChecksumSHA256": wire,
                        "ObjectLockMode": object_lock_mode,
                        "ObjectLockRetainUntilDate": retain_until}
        if if_none_match is not None:
            # 条件创建哨兵只在真实适配器边界映射为 PutObject IfNoneMatch
            params["IfNoneMatch"] = if_none_match
        try:
            resp = self._client.put_object(**params)
            return str(resp.get("VersionId") or "")
        except Exception as exc:
            raise S3Error("s3-put-request-failed") from exc


def _add_common_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--anchor-file", required=True, metavar="PATH",
                     help="审计锚点 JSONL 文件路径")
    sub.add_argument("--endpoint", required=True, metavar="URL",
                     help="S3 endpoint（公网必须 HTTPS；凭据只认环境变量）")
    sub.add_argument("--bucket", required=True, metavar="NAME",
                     help="WORM 归档 bucket 名")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit_anchor_archive.py",
        description="审计锚点 WORM/对象锁归档工具（fail-closed；"
                    "凭据只认环境变量，绝不接受 CLI 值）")
    sub = parser.add_subparsers(dest="command")
    pre = sub.add_parser("preflight", help="只读校验（零写操作）")
    _add_common_arguments(pre)
    arc = sub.add_parser("archive", help="执行 WORM 归档（需确认短语）")
    _add_common_arguments(arc)
    arc.add_argument("--retain-until", required=True, metavar="ISO-8601",
                     help="保留截止（tz-aware 且严格未来）")
    arc.add_argument("--retention-mode", required=True,
                     help=f"只接受 {RETENTION_MODE_COMPLIANCE}")
    arc.add_argument("--confirm", help="确认短语（一字不差）")
    ver = sub.add_parser("verify", help="核验已归档对象与归档报告")
    _add_common_arguments(ver)
    ver.add_argument("--report",
                     help="工件目录内的归档报告 JSON 文件名")
    return parser


def main(argv=None, *, s3=None, fs=None, clock=None, env=None,
         suffix_gen=None) -> int:
    """CLI 入口（s3/fs/clock/env/suffix_gen 全注入；测试零网络零真实 S3）。

    终防线：命令执行中的任何意外异常（含报告写盘失败）一律折叠为
    exit 2——stderr 只记异常类型名，绝不上抛原始 traceback，也绝不
    携带秘密形态值（argparse 的 SystemExit 属 BaseException，不受影响）。
    """
    args = _build_parser().parse_args(argv)
    fs = RealFS() if fs is None else fs
    clock = SystemClock() if clock is None else clock
    env = os.environ if env is None else env
    suffix_gen = _random_suffix if suffix_gen is None else suffix_gen
    try:
        if args.command == "preflight":
            return _cmd_preflight(args, s3=s3, fs=fs, clock=clock, env=env,
                                  suffix_gen=suffix_gen)
        if args.command == "archive":
            return _cmd_archive(args, s3=s3, fs=fs, clock=clock, env=env,
                                suffix_gen=suffix_gen)
        if args.command == "verify":
            return _cmd_verify(args, s3=s3, fs=fs, clock=clock, env=env,
                               suffix_gen=suffix_gen)
    except Exception as exc:  # noqa: BLE001 —— 终防线 fail-closed（类型名之外零信息）
        print(f"audit_anchor_archive: unexpected {type(exc).__name__}; "
              "fail-closed exit 2", file=sys.stderr)
        return EXIT_REJECT
    return EXIT_REJECT


if __name__ == "__main__":
    sys.exit(main())
