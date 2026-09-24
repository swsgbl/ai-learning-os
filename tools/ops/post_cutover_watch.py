#!/usr/bin/env python
"""M14-118 post-cutover evidence watch：把 M14-117 生产切换后的 canonical
证据完整性检查从人工命令拼装固化为单一 fail-closed 只读仓库工具。防止
后续证据缺失、路径逃逸、哈希漂移或索引篡改被误认为仍可验收/回滚。

设计（与 tools/ops/long_soak_release_window.py 同款纪律：单文件、纯标准
库、零第三方依赖；一切 I/O 经 monitoring_history.Store 注入；零子进程/
零网络/零计划任务/零 env 读取/零墙钟/零生产接触；只读 canonical 证据树、
只写全新输出目录）：

- 双模式：默认 **plan（零副作用）**——零读取、零写入，仅打印计划；
  **execute** 需显式 ``--execute`` 旗标才读取证据。
- 输入（固定画像）：``--evidence-root`` M14-117 canonical 证据目录
  （默认 canonical gitignored ``.verify/artifacts/m14-117-production-cutover/
  canonical``）；``--sha256sums`` SHA256SUMS 索引（默认为该目录内同名
  文件）；``--output-dir`` 全新输出目录（默认 canonical gitignored
  ``.verify/artifacts/m14-118-post-cutover-watch``）。**输出目录必须不
  存在**（fresh——拒绝与旧报告混装；symlink 一律拒绝）。CLI stdout 与
  argparse help 同纪律：绝不回显任何绝对路径/secret。
- SHA256SUMS 严格解析（fail-closed 固定词汇）：仅接受 UTF-8 文本、每行
  恰为「64 位小写 hex + 两空格 + 相对 POSIX 路径」；允许恰一个行尾
  ``\\r``（Windows CRLF——canonical 索引即 PowerShell 生成的 CRLF 文本；
  行中间 ``\\r``/多个 ``\\r`` 仍拒绝）；空索引、坏 hex、单空格分隔、
  空行、首尾空白路径、控制字符一律拒绝（index-line-format/
  index-empty）；反斜杠/绝对路径/盘符/``..`` 穿越/``//`` 空段一律拒绝
  （index-path-escape）；同路径重复条目拒绝（index-duplicate-path）；
  索引自引用（SHA256SUMS 条目指向自身）拒绝（index-self-reference）。
- M14-117 证据契约（准确来源：docs/evidence/m14-117-production-cutover/
  README.md §10 + canonical SHA256SUMS，2026-09-24 采集）：25 个文件、
  9 项分组/关键文件集合检查——7 个分组（provider-smoke 8 / monitor 4 /
  browser 5 / rc-smoke 2 / cutover 3 / endpoints 2 / recovery 1，每组
  具名文件集合精确匹配，组内多余/缺失即失败）、key-files（README §10
  关键 SHA256 表的 8 个关键文件必须在索引中）、index-integrity（总条目
  恰 25 且索引集合 == 7 分组并集——未分组/多余条目即失败）。
- 文件级校验：逐条目拒绝 symlink（目标自身与现存祖先组件）、缺失
  （file-missing）、读取失败（file-read-error）、哈希不匹配
  （file-hash-mismatch）——绝不因「大部分文件正常」而放过任何漂移。
- 输出仅安全元数据：相对路径/字节数/SHA-256/固定词汇状态与原因——
  **绝无文件内容/env 值/token/password/完整 DB URL/容器日志正文**。
  tmp + fsync + os.replace 原子落盘双文件（JSON+MD）。
- 诚实边界：``release_ready=false`` / ``production_ready=false`` 恒不变；
  本工具**通过仅表示 evidence inventory verified**，不表示生产健康、
  不表示 provider/browser/monitor 窗口外仍有效、不表示回滚已演练；
  release-approval 仍是 human-only 门，本工具永不代拟。零墙钟：同输入
  两次运行输出逐字节相同（报告不含任何时间戳字段）。

用法（仓库根）：
  python tools/ops/post_cutover_watch.py              # plan（默认，零读取）
  python tools/ops/post_cutover_watch.py --execute    # 真实只读校验 + 报告
  python tools/ops/post_cutover_watch.py --execute \
      --evidence-root <canonical-dir> --sha256sums <SHA256SUMS> \
      --output-dir <fresh-dir>

退出码：0 plan 成功（零副作用）；1 execute 且 evidence inventory
verified（9 项契约 + 25 文件哈希全过）；2 execute 且可判定失败（报告已
落盘：索引行级/契约/文件级任一失败）；3 结构性拒绝（输出目录已存在/
symlink/索引缺失/根缺失/非 UTF-8/读写失败——零输出）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

try:  # 直接脚本运行：tools/ops 自身在 sys.path
    import monitoring_history as _history
except ImportError:  # 经 importlib 按路径加载（契约测试形态）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import monitoring_history as _history

EXIT_PLAN = 0
#: 1 = execute 且 evidence inventory verified（仅证据清单完整，非生产健康）
EXIT_VERIFIED = 1
#: 2 = execute 且可判定失败（索引/契约/文件任一失败；报告已落盘）
EXIT_FAILED = 2
#: 3 = 结构性拒绝（输出目录已存在/symlink/索引缺失/根缺失/非 UTF-8/IO）
EXIT_REFUSED = 3

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE_ROOT = (REPO_ROOT / ".verify" / "artifacts"
                         / "m14-117-production-cutover" / "canonical")
DEFAULT_SHA256SUMS = DEFAULT_EVIDENCE_ROOT / "SHA256SUMS"
DEFAULT_OUTPUT_DIR = (REPO_ROOT / ".verify" / "artifacts"
                      / "m14-118-post-cutover-watch")

INDEX_NAME = "SHA256SUMS"
REPORT_JSON_NAME = "post-cutover-watch.json"
REPORT_MD_NAME = "post-cutover-watch.md"

TAG = "[post-cutover-watch]"
REPORT_SCHEMA_VERSION = 1
TOOL_NAME = "tools/ops/post_cutover_watch.py"

#: 文件条目状态固定词汇（绝不携带文件内容文本）
FILE_VERIFIED = "verified"
FILE_MISSING = "missing"
FILE_HASH_MISMATCH = "hash-mismatch"
FILE_SYMLINK = "symlink"
FILE_READ_ERROR = "read-error"
STATUS_VERIFIED = "verified"
STATUS_FAILED = "failed"
CHECK_PASS = "pass"
CHECK_FAIL = "fail"
CHECK_NOT_EVALUATED = "not-evaluated"

#: M14-117 证据契约（准确来源：docs/evidence/m14-117-production-cutover/
#: README.md §10 分组表 + canonical SHA256SUMS，2026-09-24 采集）。
#: 每组为**具名文件集合**精确匹配——组内文件名漂移即失败。
EXPECTED_GROUPS: dict[str, frozenset[str]] = {
    "provider-smoke": frozenset({
        "provider-smoke/provider-smoke.json",
        "provider-smoke/search-smoke.json",
        "provider-smoke/search-smoke-attempt1-wslmiss.json",
        "provider-smoke/search-smoke-attempt2-timeout.json",
        "provider-smoke/search-smoke-attempt3-timeout.json",
        "provider-smoke/local-voice-smoke.json",
        "provider-smoke/local-voice-smoke-attempt1-sample-download.json",
        "provider-smoke/llm-smoke.json",
    }),
    "monitor": frozenset({
        "monitor/monitor-20260923-233117.json",
        "monitor/monitor-20260923-233117.md",
        "monitor/monitor-20260923-233127.json",
        "monitor/monitor-20260923-233127.md",
    }),
    "browser": frozenset({
        "browser/BROWSER-REPORT.txt",
        "browser/desktop-home.png",
        "browser/desktop-login.png",
        "browser/mobile-home.png",
        "browser/mobile-login.png",
    }),
    "rc-smoke": frozenset({
        "rc-smoke/rc-smoke-report.json",
        "rc-smoke/rc-smoke-report.md",
    }),
    "cutover": frozenset({
        "cutover/production-preflight.json",
        "cutover/env-cutover-proof.json",
        "cutover/manifest.json",
    }),
    "endpoints": frozenset({
        "endpoints/endpoint-probe-20260924.json",
        "endpoints/endpoint-probe-attempt1-wrong-port.json",
    }),
    "recovery": frozenset({
        "recovery/recovery-dry-run-20260924.log",
    }),
}
#: README §10「关键 SHA256」表的 8 个关键文件（必须在索引中）
KEY_FILES: frozenset[str] = frozenset({
    "cutover/production-preflight.json",
    "cutover/env-cutover-proof.json",
    "cutover/manifest.json",
    "provider-smoke/provider-smoke.json",
    "monitor/monitor-20260923-233127.json",
    "browser/BROWSER-REPORT.txt",
    "rc-smoke/rc-smoke-report.json",
    "recovery/recovery-dry-run-20260924.log",
})
EXPECTED_TOTAL_FILES = 25
#: 7 分组并集（= 25 个具名文件；由定义推导，加载时断言自洽）
EXPECTED_ALL_FILES: frozenset[str] = frozenset().union(*EXPECTED_GROUPS.values())
assert len(EXPECTED_ALL_FILES) == EXPECTED_TOTAL_FILES, "契约定义自洽性破损"

#: 索引行格式：64 位小写 hex + 恰两空格 + 路径（GNU sha256sum text 模式）
_LINE_RE = re.compile(r"^([0-9a-f]{64})  (.+)$")

REPORT_BOUNDARIES: tuple[str, ...] = (
    (
        "read-only over the canonical evidence tree; zero network, zero "
        "child-process spawns, zero env reads, zero wall clock (byte-identical "
        "outputs across reruns), zero production touch"
    ),
    (
        "pass means evidence inventory verified only — it does not assert "
        "production health, provider availability, or that a rollback has "
        "been rehearsed"
    ),
    (
        "release_ready and production_ready are permanently false; release "
        "approval remains human-only and is never represented by this tool"
    ),
    (
        "index lines are validated strictly (lowercase hex, two-space "
        "separator, relative POSIX paths only; a single trailing CR is "
        "tolerated for the Windows-generated canonical index); traversal, "
        "absolute paths, backslashes, drive letters, duplicates, "
        "self-reference and non-UTF-8 indexes fail closed"
    ),
    (
        "output goes to a fresh directory only; reports carry relative "
        "paths, byte sizes, SHA-256 digests and fixed-vocabulary statuses "
        "and reasons — never file contents, env values, tokens, passwords, "
        "full database URLs, or container log bodies"
    ),
)


class WatchRefused(RuntimeError):
    """结构性拒绝（固定词汇；零输出——在任何写出前 raise）。"""


class IndexParseError(Exception):
    """索引行级失败（固定词汇 + 行号；报告仍落盘——watch 结论是证据）。"""

    def __init__(self, reason: str, line: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.line = line


# ---------------------------------------------------------------- 路径与解析


def _is_safe_relpath(rel: str) -> bool:
    """相对 POSIX 路径安全校验：非空、无首尾空白、无控制字符/NUL、无
    反斜杠、无盘符冒号、不以 / 开头、无 ``.``/``..``/空段（含 ``//``）。"""
    if not rel or rel != rel.strip():
        return False
    if "\\" in rel or ":" in rel or "\x00" in rel:
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in rel):
        return False
    if rel.startswith("/"):
        return False
    parts = rel.split("/")
    return all(part not in ("", ".", "..") for part in parts)


def parse_index(text: str) -> list[tuple[str, str]]:
    """严格解析 SHA256SUMS：返回 [(sha256, rel_path), ...]（索引原序）。

    允许恰一个行尾 ``\\r``（Windows CRLF——canonical 索引即此形态）；
    行中间 ``\\r``/多 ``\\r`` 剥离后仍残留即按路径不安全拒绝。任何行级
    违规 raise IndexParseError（固定词汇 + 1 起行号）；绝不把行文本带进
    异常——行内容可能含 secret。"""
    lines = text.split("\n")
    #: 末行换行产生的空尾段不算行；其余空行即畸形
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if not lines:
        raise IndexParseError("index-empty", 0)
    entries: list[tuple[str, str]] = []
    seen: dict[str, int] = {}
    for offset, line in enumerate(lines, start=1):
        line = line.removesuffix("\r")  # 恰一个行尾 CR（Windows CRLF） tolerated
        match = _LINE_RE.match(line)
        if match is None:
            raise IndexParseError("index-line-format", offset)
        digest, rel = match.group(1), match.group(2)
        if not _is_safe_relpath(rel):
            raise IndexParseError("index-path-escape", offset)
        if rel == INDEX_NAME:
            raise IndexParseError("index-self-reference", offset)
        if rel in seen:
            raise IndexParseError("index-duplicate-path", offset)
        seen[rel] = offset
        entries.append((digest, rel))
    return entries


# ---------------------------------------------------------------- 契约（纯）


def _check(name: str, expected: frozenset[str], actual: frozenset[str],
           *, extra_allowed: bool = False) -> dict[str, object]:
    missing = sorted(expected - actual)
    extra = [] if extra_allowed else sorted(actual - expected)
    status = CHECK_PASS if not missing and not extra else CHECK_FAIL
    return {"name": name, "status": status,
            "expected_count": len(expected), "actual_count": len(actual),
            "missing": missing, "extra": extra}


def evaluate_contract(entry_paths: list[str]) -> list[dict[str, object]]:
    """9 项分组/关键文件集合检查（纯集合运算；词法解析成功即评估）。"""
    actual = frozenset(entry_paths)
    checks: list[dict[str, object]] = []
    for group in ("provider-smoke", "monitor", "browser", "rc-smoke",
                  "cutover", "endpoints", "recovery"):
        group_actual = frozenset(
            path for path in actual if path.split("/", 1)[0] == group)
        checks.append(_check(f"group:{group}", EXPECTED_GROUPS[group],
                             group_actual))
    checks.append(_check("key-files", KEY_FILES, actual, extra_allowed=True))
    checks.append(_check("index-integrity", EXPECTED_ALL_FILES, actual))
    return checks


# ---------------------------------------------------------------- 文件校验


def _verify_one(store: _history.Store, evidence_root: Path,
                digest: str, rel: str) -> dict[str, object]:
    target = evidence_root.joinpath(*rel.split("/"))
    try:
        _history.reject_symlinked_path(store, target)
    except _history.HistoryError:
        return {"path": rel, "expected_sha256": digest, "actual_sha256": None,
                "byte_size": None, "status": FILE_SYMLINK}
    if not store.exists(target):
        return {"path": rel, "expected_sha256": digest, "actual_sha256": None,
                "byte_size": None, "status": FILE_MISSING}
    try:
        raw = store.read_bytes(target)
    except OSError:
        return {"path": rel, "expected_sha256": digest, "actual_sha256": None,
                "byte_size": None, "status": FILE_READ_ERROR}
    actual = hashlib.sha256(raw).hexdigest()
    status = (FILE_VERIFIED if actual == digest else FILE_HASH_MISMATCH)
    return {"path": rel, "expected_sha256": digest,
            "actual_sha256": actual if status == FILE_HASH_MISMATCH else None,
            "byte_size": len(raw), "status": status}


def verify_files(store: _history.Store, evidence_root: Path,
                 entries: list[tuple[str, str]]) -> list[dict[str, object]]:
    """逐条目只读校验（目标 + 祖先 symlink、缺失、读取、哈希）。"""
    return [_verify_one(store, evidence_root, digest, rel)
            for digest, rel in entries]


# ---------------------------------------------------------------- 报告（纯）


def _file_counts(files: list[dict[str, object]]) -> dict[str, int]:
    counts = {FILE_VERIFIED: 0, FILE_MISSING: 0, FILE_HASH_MISMATCH: 0,
              FILE_SYMLINK: 0, FILE_READ_ERROR: 0}
    for entry in files:
        counts[str(entry["status"])] += 1
    return counts


def build_report(*, status: str, reasons: list[str],
                 index_meta: dict[str, object],
                 contract_checks: list[dict[str, object]],
                 files: list[dict[str, object]],
                 first_bad_line: int | None) -> dict[str, object]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "mode": "execute",
        "status": status,
        "reasons": reasons,
        "release_ready": False,
        "production_ready": False,
        "index": index_meta,
        "first_bad_line": first_bad_line,
        "contract": {
            "expected_total_files": EXPECTED_TOTAL_FILES,
            "checks": contract_checks,
        },
        "files": {"counts": _file_counts(files), "entries": files},
        "boundaries": list(REPORT_BOUNDARIES),
    }


def render_markdown(report: dict[str, object]) -> str:
    index_meta = report["index"]
    assert isinstance(index_meta, dict)
    contract = report["contract"]
    assert isinstance(contract, dict)
    files_block = report["files"]
    assert isinstance(files_block, dict)
    counts = files_block["counts"]
    assert isinstance(counts, dict)
    reasons = report["reasons"]
    assert isinstance(reasons, list)
    bad_line = report["first_bad_line"]
    lines: list[str] = [
        (f"# M14-118 post-cutover evidence watch（schema_version="
         f"{report['schema_version']}）"),
        "",
        f"- 状态：**{report['status']}**"
        + (f"（原因：{', '.join(str(r) for r in reasons)}）" if reasons else ""),
        (f"- 索引：SHA-256 `{index_meta['sha256']}`，"
         f"{index_meta['byte_size']} bytes，{index_meta['line_count']} 行 / "
         f"{index_meta['entry_count']} 条目")
        + (f"，首个坏行：{bad_line}" if bad_line is not None else ""),
        (f"- 就绪语义：`release_ready={report['release_ready']}` / "
         f"`production_ready={report['production_ready']}` 恒定——"
         "通过仅表示 evidence inventory verified"),
        "",
        "## 契约检查（9 项）",
        "",
        "| 检查 | 状态 | 期望 | 实际 | missing | extra |",
        "|------|------|------|------|---------|-------|",
    ]
    for check in contract["checks"]:
        assert isinstance(check, dict)
        lines.append(
            f"| {check['name']} | {check['status']} | {check['expected_count']}"
            f" | {check['actual_count']} | {len(check['missing'])}"
            f" | {len(check['extra'])} |")
    for check in contract["checks"]:
        assert isinstance(check, dict)
        if check["status"] == CHECK_FAIL:
            missing = check["missing"]
            extra = check["extra"]
            assert isinstance(missing, list) and isinstance(extra, list)
            detail = [f"### {check['name']} 明细", ""]
            detail += [f"- missing: `{path}`" for path in missing]
            detail += [f"- extra: `{path}`" for path in extra]
            lines += ["", *detail]
    lines += [
        "",
        "## 文件校验",
        "",
        (f"- 计数：verified {counts[FILE_VERIFIED]} / missing "
         f"{counts[FILE_MISSING]} / hash-mismatch {counts[FILE_HASH_MISMATCH]}"
         f" / symlink {counts[FILE_SYMLINK]} / read-error "
         f"{counts[FILE_READ_ERROR]}"),
        "",
        "| 相对路径 | 字节数 | 状态 |",
        "|----------|--------|------|",
    ]
    for entry in files_block["entries"]:
        assert isinstance(entry, dict)
        size = entry["byte_size"]
        size_text = "-" if size is None else str(size)
        lines.append(f"| `{entry['path']}` | {size_text} | {entry['status']} |")
    lines += ["", "边界："] + [f"- {item}" for item in report["boundaries"]]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 写出（原子）


def write_report(store: _history.Store, output_dir: Path,
                 report: dict[str, object]) -> None:
    """报告双文件原子落盘（先于 mkdir 拒绝目录/祖先 symlink，mkdir 后
    复查目标——TOCTOU 防御）。"""
    try:
        _history.reject_symlinked_path(store, output_dir)
    except _history.HistoryError:
        raise WatchRefused("output-symlink") from None
    store.mkdirs(output_dir)
    json_path = output_dir / REPORT_JSON_NAME
    md_path = output_dir / REPORT_MD_NAME
    try:
        _history.reject_symlinked_path(store, json_path, md_path, output_dir)
    except _history.HistoryError:
        raise WatchRefused("output-symlink") from None
    store.write_atomic(json_path, json.dumps(
        report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    store.write_atomic(md_path, render_markdown(report))


# ---------------------------------------------------------------- 主管道


def run_watch(*, store: _history.Store, evidence_root: Path,
              sha256sums: Path, output_dir: Path) -> tuple[dict[str, object],
                                                           int]:
    """主管道：结构性护栏（零写）→ 索引严格解析 → 9 项契约 → 25 文件
    哈希 → 汇总报告（全新目录原子落盘）。

    行级/契约/文件失败是**可判定结论**——failed 报告照常落盘（watch 的
    价值即把漂移固化成证据）；结构性拒绝在任何写出前 raise（零输出）。"""
    if store.exists(output_dir):
        raise WatchRefused("output-dir-exists")
    try:
        _history.reject_symlinked_path(store, output_dir)
    except _history.HistoryError:
        raise WatchRefused("output-symlink") from None
    if not store.exists(evidence_root):
        raise WatchRefused("evidence-root-missing")
    try:
        _history.reject_symlinked_path(store, evidence_root)
    except _history.HistoryError:
        raise WatchRefused("evidence-root-symlink") from None
    if not store.exists(sha256sums):
        raise WatchRefused("index-missing")
    try:
        _history.reject_symlinked_path(store, sha256sums)
    except _history.HistoryError:
        raise WatchRefused("index-symlink") from None
    try:
        raw = store.read_bytes(sha256sums)
    except OSError:
        raise WatchRefused("index-read-error") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise WatchRefused("index-not-utf8") from None
    index_sha = hashlib.sha256(raw).hexdigest()
    line_count = len(text.splitlines())
    index_meta: dict[str, object] = {
        "sha256": index_sha, "byte_size": len(raw),
        "line_count": line_count, "entry_count": 0,
    }
    try:
        entries = parse_index(text)
    except IndexParseError as cause:
        report = build_report(
            status=STATUS_FAILED, reasons=[cause.reason],
            index_meta=index_meta, contract_checks=_not_evaluated_checks(),
            files=[], first_bad_line=(cause.line or None))
        write_report(store, output_dir, report)
        return report, EXIT_FAILED
    index_meta["entry_count"] = len(entries)
    contract_checks = evaluate_contract([rel for _sha, rel in entries])
    files = verify_files(store, evidence_root, entries)
    reasons: list[str] = [str(check["name"]) for check in contract_checks
                          if check["status"] == CHECK_FAIL]
    counts = _file_counts(files)
    if counts[FILE_MISSING]:
        reasons.append("files-missing")
    if counts[FILE_HASH_MISMATCH]:
        reasons.append("files-hash-mismatch")
    if counts[FILE_SYMLINK]:
        reasons.append("files-symlink")
    if counts[FILE_READ_ERROR]:
        reasons.append("files-read-error")
    status = STATUS_VERIFIED if not reasons else STATUS_FAILED
    report = build_report(status=status, reasons=reasons,
                          index_meta=index_meta,
                          contract_checks=contract_checks, files=files,
                          first_bad_line=None)
    write_report(store, output_dir, report)
    return report, (EXIT_VERIFIED if status == STATUS_VERIFIED
                    else EXIT_FAILED)


def _not_evaluated_checks() -> list[dict[str, object]]:
    names = [f"group:{group}" for group in EXPECTED_GROUPS]
    names += ["key-files", "index-integrity"]
    return [{"name": name, "status": CHECK_NOT_EVALUATED,
             "expected_count": 0, "actual_count": 0,
             "missing": [], "extra": []} for name in names]


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    """argparse 构造：help 只作通用描述，绝不插值 DEFAULT_* 绝对路径值
    （默认路径行为不变——默认值本身注册在 parser 上，仅不进入任何输出）。"""
    parser = argparse.ArgumentParser(
        prog="post_cutover_watch.py",
        description=("M14-118 post-cutover evidence watch：只读校验 M14-117 "
                     "canonical 证据树完整性（默认 plan 零副作用；--execute "
                     "才读取证据；25 文件契约 + SHA256SUMS 严格解析 + 哈希"
                     "复核；零网络/零子进程/零 env 读取/零墙钟/零生产接触）"),
    )
    parser.add_argument("--execute", action="store_true",
                        help="真实只读校验（默认 plan：零读取、零写入）")
    parser.add_argument("--evidence-root", type=Path,
                        default=DEFAULT_EVIDENCE_ROOT,
                        help=("M14-117 canonical 证据目录"
                              "（默认为 canonical gitignored cutover 证据"
                              "目录内 canonical 子目录；只读；可覆写）"))
    parser.add_argument("--sha256sums", type=Path, default=DEFAULT_SHA256SUMS,
                        help=("SHA256SUMS 索引文件"
                              "（默认为该证据目录内同名索引；只读）"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help=("全新输出目录，必须不存在"
                              "（默认为 canonical gitignored watch 输出"
                              "目录）"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.execute:
        print(f"{TAG} 模式: plan（默认，零读取、零写入；真实校验需 --execute）",
              flush=True)
        print(f"{TAG} 计划输入: M14-117 canonical 证据根目录 + {INDEX_NAME}"
              " 索引（只读；路径不回显）", flush=True)
        print(f"{TAG} 计划校验: 索引行格式/路径安全/重复/自引用 → 9 项契约"
              "检查 → 逐文件 SHA-256 复核（fail-closed）", flush=True)
        print(f"{TAG} 计划输出: {REPORT_JSON_NAME} / {REPORT_MD_NAME}"
              "（全新目录；仅相对路径/字节数/哈希/固定词汇状态）", flush=True)
        print(f"{TAG} 边界: release_ready/production_ready 恒 false；"
              "通过仅表示 evidence inventory verified", flush=True)
        return EXIT_PLAN
    try:
        report, exit_code = run_watch(
            store=_history.RealStore(), evidence_root=args.evidence_root,
            sha256sums=args.sha256sums, output_dir=args.output_dir)
    except WatchRefused as cause:
        print(f"{TAG} 拒绝: {cause}（fail-closed，输出零写入）", flush=True)
        return EXIT_REFUSED
    except OSError as cause:
        print(f"{TAG} 写失败: {type(cause).__name__}（输出保持原子，无残留）",
              flush=True)
        return EXIT_REFUSED
    reasons = report["reasons"]
    assert isinstance(reasons, list)
    print(f"{TAG} 状态: {report['status']}"
          + (f"（{', '.join(str(r) for r in reasons)}）" if reasons else ""),
          flush=True)
    print(f"{TAG} 索引条目: {report['index']['entry_count']} / 契约检查:"
          f" {len(report['contract']['checks'])} 项", flush=True)
    print(f"{TAG} 输出: {REPORT_JSON_NAME} / {REPORT_MD_NAME}", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
