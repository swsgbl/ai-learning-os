#!/usr/bin/env python
"""M14-133 production drift watch 历史审计：M14-127 只读单轮报告 →
确定性的历史完整性/连续性/锚点稳定性判定（补 M14-129 PT15M 调度
「只有单轮报告、没有可测试的历史审计器」的缺口）。

设计（与 tools/ops/soak_stability_audit.py 同款纪律：单文件、纯标准库、
零第三方依赖、零子进程、零网络、零 env 读取、零墙钟）：

- 输入（固定画像）：默认 canonical gitignored
  ``.verify/artifacts/m14-127-production-drift-watch``（可用 ``--input-dir``
  覆盖）。仅接受文件名**严格匹配** ``drift-watch-YYYYMMDD-HHMMSS.json``
  的普通文件；伴生 ``.md``、``plan-*.json`` 与其它条目只计数
  （``ignored_entries``）绝不解析。输入路径含 ``..`` 组件或任何现存
  symlink 组件 → fail-closed 拒绝（零输出）；单份报告文件自身是
  symlink → 该报告计 invalid（``symlink-target``）并按审计发现处理。
- 严格 schema 校验（对齐 M14-127 execute 报告实际字段）：
  ``schema_version==1``、``tool==tools/ops/production_drift_watch.py``、
  ``milestone==M14-127``、``mode==execute``、``started_at_utc``/
  ``ended_at_utc`` 严格 ``YYYY-MM-DDTHH:MM:SSZ`` 形态且
  ended>=started、``config.anchors`` 含 api/web 的 ``expected_tag`` +
  ``expected_digest``（sha256:<64 小写 hex> 形态）、``collectors``/
  ``checks``/``counts``/``drift``/``drift_reasons`` 在场且类型正确、
  ``counts`` 与 ``checks`` 实际计数一致、``drift`` 与失败检查数一致。
  任何违规 → invalid（固定词汇 reason），绝不猜测修复。
- 文件名 UTC 时间戳交叉校验：``started_at_utc`` 必须落在
  ``[文件名时间戳, 文件名时间戳+2s]``（M14-127 先取 stamp 后取
  started_at 的实现序），越界 → ``filename-timestamp-mismatch``。
- scheduled-run 选择（唯一模式）：``started_at_utc`` 的 minute ∈
  {00,15,30,45} 且 second ∈ [00,30] 才入选；manual/off-slot 报告保持
  **可见的 excluded**（计入 runs 索引与 ``excluded`` 计数，reason=
  ``manual-off-slot``），绝不静默删除。
- PT15M slot 审计：仅对入选报告的时间闭区间（首末 slot 含端）枚举
  期望 slot，缺失 slot 显式列出；**绝不向首末报告之外外推完整性**；
  slot 期望数超硬顶（``slot-range-too-large``）亦 fail-closed。
- 重复检测：入选报告间 ``started_at_utc`` 重复（duplicate-started-at）
  与同 slot 双跑（duplicate-slot）都按审计发现处理。
- 确定性摘要：总文件数、valid/invalid/excluded、clean/drifted、首末
  时间戳、期望/缺失 slot、最长连续 clean streak（相邻入选 clean 报告
  slot 恰差一个 PT15M 才延续；missing/drift/duplicate 一律断链）、
  API/Web digest 稳定性（expected 跨报告唯一、running 双通道跨报告
  唯一、running==expected 即 anchored）、有界逐报告索引
  （``runs``，硬顶 5000，超出截断并标记）。**零墙钟**：输出不含任何
  当前时刻，同输入逐字节可复现；报告绝不包含绝对路径或 secret 形态值
  （写盘前再经 redact_secrets 终防线）。
- 判定（exit 0 仅当）：入选报告全部 valid 且 clean（drift=false）、
  无 duplicate、无缺失 slot、slot 范围未超限、api/web digest 锚点
  三重稳定；零入选（含零 valid）恒为发现（``no-valid-reports``/
  ``no-selected-reports``——无证据绝不冒充全绿）。任何审计发现 →
  诚实写出失败报告后 exit 2；参数/路径/schema 域拒绝（输入缺失、
  traversal、symlink 组件、输出写失败）→ exit 2 且**零报告写出**。
- 边界：本工具只读消费既有报告文件，绝不运行 M14-127 watcher、绝不
  触碰计划任务、绝不接触生产栈；历史审计不证明 production readiness、
  长期稳定性、跨重启存活或 drift 告警送达，release-approval 仍是
  human-only 门。

用法（仓库根）：
  python tools/ops/production_drift_watch_history.py               # 默认输入/输出
  python tools/ops/production_drift_watch_history.py --input-dir <dir> --output-dir <dir>

退出码：0 审计全绿；2 任何审计发现（invalid/drift/duplicate/缺失
slot/digest 漂移/零入选）或 fail-closed 拒绝（参数/路径/写失败）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

EXIT_OK = 0
#: 2 = 审计发现或 fail-closed 拒绝（统一可见拒绝口径）
EXIT_REFUSE = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = (REPO_ROOT / ".verify" / "artifacts"
                     / "m14-127-production-drift-watch")
DEFAULT_OUTPUT_DIR = (REPO_ROOT / ".verify" / "artifacts"
                      / "m14-133-drift-watch-history")
REPORT_STEM = "drift-watch-history"
TOOL_NAME = "tools/ops/production_drift_watch_history.py"
MILESTONE = "M14-133"
HISTORY_SCHEMA_VERSION = 1
TAG = "[drift-watch-history]"

#: 被审计的 M14-127 报告自描述事实源（严格相等才 valid）
SOURCE_TOOL_NAME = "tools/ops/production_drift_watch.py"
SOURCE_MILESTONE = "M14-127"
SOURCE_SCHEMA_VERSION = 1
SOURCE_MODE_EXECUTE = "execute"
ANCHOR_SERVICES: tuple[str, ...] = ("api", "web")

#: 文件名严格形态：drift-watch-YYYYMMDD-HHMMSS.json（伴生 .md 天然不匹配）
FILE_NAME_RE = re.compile(r"^drift-watch-(\d{8}-\d{6})\.json$")
FILE_STAMP_FORMAT = "%Y%m%d-%H%M%S"
#: started_at_utc/ended_at_utc 严格形态（与 M14-127 RealClock 输出一致）
ISO_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ISO_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

#: scheduled-run 选择契约（M14-129 PT15M 槽位 + 槽位内早窗）
SLOT_INTERVAL = timedelta(minutes=15)
SCHEDULED_MINUTES: frozenset[int] = frozenset({0, 15, 30, 45})
SCHEDULED_SECOND_MAX = 30
#: 文件名 stamp 先取、started_at 后取 → started 允许滞后 stamp 至多 2 秒
FILENAME_TS_TOLERANCE = timedelta(seconds=2)

#: 有界输出契约
MAX_RUNS_IN_INDEX = 5000
MAX_EXPECTED_SLOTS = 10000
MAX_LISTED_ITEMS = 100

CHECK_PASS = "pass"
CHECK_FAIL = "fail"


class HistoryError(RuntimeError):
    """fail-closed 拒绝（固定词汇 reason；绝不携带文件内容文本）。"""


# ---------------------------------------------------------------- 脱敏（防御性终防线）

SECRET_SHAPE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), "[REDACTED:token]"),
    (re.compile(r"ghp_[A-Za-z0-9]{8,}"), "[REDACTED:token]"),
    (re.compile(r"gho_[A-Za-z0-9]{8,}"), "[REDACTED:token]"),
    (re.compile(r"AKIA[0-9A-Z]{12,}"), "[REDACTED:key]"),
    (re.compile(r"xoxb-[A-Za-z0-9-]{8,}"), "[REDACTED:token]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"), "[REDACTED:token]"),
    (re.compile(r"(?i)(api[_-]?key|secret|password)\s*[=:]\s*\S{8,}"), "[REDACTED:credential]"),
)


def redact_secrets(text: str) -> str:
    for pattern, label in SECRET_SHAPE_PATTERNS:
        text = pattern.sub(label, text)
    return text


class SafeLog:
    """行式日志：stdout 输出前经 redact_secrets 防御性脱敏。"""

    def __init__(self, echo: bool = True) -> None:
        self.lines: list[str] = []
        self._echo = echo

    def say(self, message: str) -> None:
        line = redact_secrets(message)
        self.lines.append(line)
        if self._echo:
            print(f"{TAG} {line}", flush=True)


# ---------------------------------------------------------------- 路径校验（traversal + symlink 拒绝）


def reject_path_problems(path: Path, *, must_exist_as_dir: bool = False) -> None:
    """拒绝 ``..`` 组件、symlink 目标与 symlink 祖先组件；可选要求
    现存目录。违规 raise HistoryError（固定词汇，零副作用）。"""
    for part in path.parts:
        if part == "..":
            raise HistoryError("path-traversal")
    if must_exist_as_dir:
        if not path.exists():
            raise HistoryError("input-dir-missing")
        if not path.is_dir():
            raise HistoryError("input-dir-not-a-directory")
    if path.is_symlink():
        raise HistoryError("symlink-target")
    for ancestor in path.parents:
        if ancestor.exists() and ancestor.is_symlink():
            raise HistoryError("symlink-in-path")


# ---------------------------------------------------------------- 时间解析（严格、零墙钟）


def parse_iso_utc(value: object) -> datetime:
    """严格解析 ``YYYY-MM-DDTHH:MM:SSZ`` → aware datetime；违规 raise。"""
    if not isinstance(value, str) or ISO_TS_RE.match(value) is None:
        raise HistoryError("ts-shape")
    return datetime.strptime(value, ISO_TS_FORMAT).replace(tzinfo=timezone.utc)


def parse_file_stamp(stamp: str) -> datetime:
    try:
        return datetime.strptime(stamp, FILE_STAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        raise HistoryError("filename-timestamp-invalid") from None


def slot_floor(dt: datetime) -> datetime:
    minute = (dt.minute // 15) * 15
    return dt.replace(minute=minute, second=0, microsecond=0)


def slot_iso(dt: datetime) -> str:
    return slot_floor(dt).strftime(ISO_TS_FORMAT)


def is_scheduled_run(started_dt: datetime) -> bool:
    return (started_dt.minute in SCHEDULED_MINUTES
            and started_dt.second <= SCHEDULED_SECOND_MAX)


# ---------------------------------------------------------------- 单报告 schema 校验


@dataclass(frozen=True)
class ValidReport:
    """已通过严格校验的 M14-127 报告（仅审计所需字段的提取面）。"""

    filename: str
    started_dt: datetime
    started_at: str
    ended_at: str
    drift: bool
    check_pass: int
    check_fail: int
    expected_digests: dict[str, str]
    running_digests: dict[str, list[str]]


def _expect_mapping(value: object, reason: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise HistoryError(reason)
    return value


def _validate_anchor_config(config: dict[str, object]) -> dict[str, str]:
    anchors = _expect_mapping(config.get("anchors"), "config-anchors")
    expected: dict[str, str] = {}
    for service in ANCHOR_SERVICES:
        anchor = _expect_mapping(anchors.get(service), "config-anchors")
        tag = anchor.get("expected_tag")
        digest = anchor.get("expected_digest")
        if not isinstance(tag, str) or not tag:
            raise HistoryError("config-anchors")
        if not isinstance(digest, str) or _DIGEST_RE.match(digest) is None:
            raise HistoryError("config-anchors")
        expected[service] = digest
    return expected


def _validate_checks(checks: object) -> tuple[int, int]:
    if not isinstance(checks, list):
        raise HistoryError("checks")
    pass_count = fail_count = 0
    for item in checks:
        check = _expect_mapping(item, "checks")
        status = check.get("status")
        if (not isinstance(check.get("check_id"), str)
                or not isinstance(check.get("subject"), str)
                or status not in (CHECK_PASS, CHECK_FAIL)):
            raise HistoryError("checks")
        if status == CHECK_PASS:
            pass_count += 1
        else:
            fail_count += 1
    return pass_count, fail_count


def _extract_running_digests(collectors: dict[str, object]) -> dict[str, list[str]]:
    """尽力提取 API/Web 运行 digest 双通道（容器 inspect / tag 解析）；
    通道缺失或采集失败 → 该通道不计（报告级 drift 判定不在此重复）。"""
    running: dict[str, list[str]] = {service: [] for service in ANCHOR_SERVICES}
    containers = collectors.get("containers")
    if isinstance(containers, dict):
        per_service = containers.get("per_service")
        if isinstance(per_service, dict):
            for service in ANCHOR_SERVICES:
                item = per_service.get(service)
                if isinstance(item, dict) and item.get("status") == "ok":
                    image_id = item.get("image_id")
                    if isinstance(image_id, str):
                        running[service].append(image_id)
    image_refs = collectors.get("image_refs")
    if isinstance(image_refs, dict):
        for service in ANCHOR_SERVICES:
            item = image_refs.get(service)
            if isinstance(item, dict) and item.get("status") == "ok":
                image_id = item.get("image_id")
                if isinstance(image_id, str):
                    running[service].append(image_id)
    return running


def validate_report(filename: str, stamp: str, data: object) -> ValidReport:
    """严格校验单份报告；任何违规 raise HistoryError（固定词汇 reason）。"""
    report = _expect_mapping(data, "not-an-object")
    if report.get("schema_version") != SOURCE_SCHEMA_VERSION:
        raise HistoryError("schema-version")
    if report.get("tool") != SOURCE_TOOL_NAME:
        raise HistoryError("tool")
    if report.get("milestone") != SOURCE_MILESTONE:
        raise HistoryError("milestone")
    if report.get("mode") != SOURCE_MODE_EXECUTE:
        raise HistoryError("mode")
    started_at = report.get("started_at_utc")
    ended_at = report.get("ended_at_utc")
    try:
        started_dt = parse_iso_utc(started_at)
        ended_dt = parse_iso_utc(ended_at)
    except HistoryError:
        raise HistoryError("started-or-ended-at") from None
    if ended_dt < started_dt:
        raise HistoryError("ended-before-started")
    file_dt = parse_file_stamp(stamp)
    if started_dt < file_dt or started_dt > file_dt + FILENAME_TS_TOLERANCE:
        raise HistoryError("filename-timestamp-mismatch")
    config = _expect_mapping(report.get("config"), "config")
    expected = _validate_anchor_config(config)
    collectors = _expect_mapping(report.get("collectors"), "collectors")
    check_pass, check_fail = _validate_checks(report.get("checks"))
    counts = _expect_mapping(report.get("counts"), "counts")
    counts_pass = counts.get("pass")
    counts_fail = counts.get("fail")
    for value in (counts_pass, counts_fail):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise HistoryError("counts")
    if counts_pass != check_pass or counts_fail != check_fail:
        raise HistoryError("counts-checks-mismatch")
    drift = report.get("drift")
    if not isinstance(drift, bool):
        raise HistoryError("drift")
    if drift != (check_fail > 0):
        raise HistoryError("drift-counts-mismatch")
    if not isinstance(report.get("drift_reasons"), list):
        raise HistoryError("drift-reasons")
    running = _extract_running_digests(collectors)
    return ValidReport(
        filename=filename,
        started_dt=started_dt,
        started_at=str(started_at),
        ended_at=str(ended_at),
        drift=drift,
        check_pass=check_pass,
        check_fail=check_fail,
        expected_digests=expected,
        running_digests=running,
    )


# ---------------------------------------------------------------- 目录扫描


@dataclass
class ScannedFile:
    filename: str
    stamp: str
    status: str  # "valid" | "invalid"
    reason: str | None = None
    report: ValidReport | None = None


@dataclass
class ScanResult:
    files: list[ScannedFile] = field(default_factory=list)
    ignored_entries: int = 0

    @property
    def valid(self) -> list[ScannedFile]:
        return [item for item in self.files if item.status == "valid"]

    @property
    def invalid(self) -> list[ScannedFile]:
        return [item for item in self.files if item.status == "invalid"]


def scan_directory(input_dir: Path) -> ScanResult:
    """只读枚举 + 解析 + 校验；symlink 文件计 invalid（fail-closed 发现）。"""
    result = ScanResult()
    with os.scandir(input_dir) as entries:
        for entry in sorted(entries, key=lambda item: item.name):
            match = FILE_NAME_RE.match(entry.name)
            if match is None:
                result.ignored_entries += 1
                continue
            if entry.is_symlink():
                result.files.append(ScannedFile(entry.name, match.group(1),
                                                "invalid", "symlink-target"))
                continue
            if not entry.is_file():
                result.files.append(ScannedFile(entry.name, match.group(1),
                                                "invalid", "not-a-regular-file"))
                continue
            try:
                raw = (input_dir / entry.name).read_bytes()
                text = raw.decode("utf-8")
                data = json.loads(text)
            except UnicodeDecodeError:
                result.files.append(ScannedFile(entry.name, match.group(1),
                                                "invalid", "not-utf8"))
                continue
            except OSError:
                result.files.append(ScannedFile(entry.name, match.group(1),
                                                "invalid", "unreadable"))
                continue
            except ValueError:
                result.files.append(ScannedFile(entry.name, match.group(1),
                                                "invalid", "not-json"))
                continue
            try:
                report = validate_report(entry.name, match.group(1), data)
            except HistoryError as cause:
                result.files.append(ScannedFile(entry.name, match.group(1),
                                                "invalid", str(cause)))
                continue
            result.files.append(ScannedFile(entry.name, match.group(1),
                                            "valid", None, report))
    return result


# ---------------------------------------------------------------- 审计（纯）


@dataclass
class Selection:
    selected: list[ValidReport]
    excluded: list[tuple[str, str]]  # (filename, started_at)


def select_scheduled(valid_files: list[ScannedFile]) -> Selection:
    selected: list[ValidReport] = []
    excluded: list[tuple[str, str]] = []
    for item in sorted(valid_files, key=lambda f: (f.report.started_at, f.filename)):
        if is_scheduled_run(item.report.started_dt):
            selected.append(item.report)
        else:
            excluded.append((item.filename, item.report.started_at))
    return Selection(selected=selected, excluded=excluded)


def audit_slots(selected: list[ValidReport]) -> dict[str, object]:
    """PT15M slot 审计：仅首末入选 slot 闭区间，绝不外推。"""
    if not selected:
        return {"expected": None, "present": None, "missing": None,
                "missing_slots": [], "missing_slots_truncated": False,
                "range_too_large": False}
    present = {slot_floor(r.started_dt) for r in selected}
    first, last = min(present), max(present)
    span = int((last - first) / SLOT_INTERVAL) + 1
    if span > MAX_EXPECTED_SLOTS:
        return {"expected": None, "present": len(present), "missing": None,
                "missing_slots": [], "missing_slots_truncated": False,
                "range_too_large": True}
    expected: list[datetime] = []
    cursor = first
    while cursor <= last:
        expected.append(cursor)
        cursor += SLOT_INTERVAL
    missing = sorted(slot for slot in expected if slot not in present)
    truncated = len(missing) > MAX_LISTED_ITEMS
    return {"expected": len(expected), "present": len(present),
            "missing": len(missing),
            "missing_slots": [slot.strftime(ISO_TS_FORMAT)
                              for slot in missing[:MAX_LISTED_ITEMS]],
            "missing_slots_truncated": truncated,
            "range_too_large": False}


def find_duplicates(selected: list[ValidReport]) -> dict[str, list[dict[str, object]]]:
    started_groups: dict[str, list[str]] = {}
    slot_groups: dict[str, list[str]] = {}
    for report in selected:
        started_groups.setdefault(report.started_at, []).append(report.filename)
        slot_groups.setdefault(slot_iso(report.started_dt), []).append(report.filename)
    duplicates = {
        "started_at": [
            {"started_at_utc": key, "filenames": sorted(names)}
            for key, names in sorted(started_groups.items()) if len(names) > 1
        ],
        "slots": [
            {"slot_utc": key, "filenames": sorted(names)}
            for key, names in sorted(slot_groups.items()) if len(names) > 1
        ],
    }
    return duplicates


def longest_clean_streak(selected: list[ValidReport]) -> dict[str, object]:
    """相邻入选 clean 报告 slot 恰差一个 PT15M 才延续；missing/drift/
    duplicate 断链（duplicate 由 slot 相等差 0 自然断链）。"""
    best = current = 0
    best_start = best_end = None
    current_start: datetime | None = None
    previous: datetime | None = None
    for report in selected:
        slot = slot_floor(report.started_dt)
        clean = not report.drift
        contiguous = previous is not None and slot - previous == SLOT_INTERVAL
        if clean and (current == 0 or contiguous):
            current += 1
            if current == 1:
                current_start = slot
            if current > best:
                best, best_start, best_end = current, current_start, slot
        else:
            current = 1 if clean else 0
            current_start = slot if clean else None
            if clean and current > best:
                best, best_start, best_end = current, current_start, slot
        previous = slot
    return {
        "runs": best,
        "start_slot_utc": best_start.strftime(ISO_TS_FORMAT) if best_start else None,
        "end_slot_utc": best_end.strftime(ISO_TS_FORMAT) if best_end else None,
    }


def digest_stability(selected: list[ValidReport]) -> dict[str, dict[str, object]]:
    summary: dict[str, dict[str, object]] = {}
    for service in ANCHOR_SERVICES:
        expected = sorted({r.expected_digests[service] for r in selected})
        running = sorted({value for r in selected
                          for value in r.running_digests[service]})
        summary[service] = {
            "expected_digests": expected,
            "running_digests": running,
            "expected_consistent": len(expected) == 1,
            "running_consistent": len(running) == 1,
            "anchored": (len(expected) == 1 and len(running) == 1
                         and expected == running),
        }
    return summary


def collect_findings(scan: ScanResult, selection: Selection, slots: dict[str, object],
                     duplicates: dict[str, list[dict[str, object]]],
                     digests: dict[str, dict[str, object]]) -> list[str]:
    findings: list[str] = []
    if not scan.valid:
        findings.append("no-valid-reports")
    for item in sorted(scan.invalid, key=lambda f: f.filename):
        findings.append(f"invalid-report:{item.filename}")
    if scan.valid and not selection.selected:
        findings.append("no-selected-reports")
    for report in selection.selected:
        if report.drift:
            findings.append(f"drift-report:{report.filename}")
    for group in duplicates["started_at"]:
        findings.append(f"duplicate-started-at:{group['started_at_utc']}")
    for group in duplicates["slots"]:
        findings.append(f"duplicate-slot:{group['slot_utc']}")
    if slots.get("range_too_large"):
        findings.append("slot-range-too-large")
    elif slots.get("missing"):
        for slot in slots["missing_slots"]:
            findings.append(f"missing-slot:{slot}")
    for service in ANCHOR_SERVICES:
        info = digests[service]
        if not info["expected_consistent"]:
            findings.append(f"digest-expected-inconsistent:{service}")
        if not info["running_consistent"]:
            findings.append(f"digest-running-inconsistent:{service}")
        if not info["anchored"]:
            findings.append(f"digest-not-anchored:{service}")
    return findings


def build_runs_index(scan: ScanResult, selection: Selection) -> tuple[list[dict[str, object]], bool]:
    """有界逐报告索引（固定字段；绝不包含绝对路径/报告原文）。"""
    selected_names = {r.filename for r in selection.selected}
    rows: list[tuple[tuple[str, str, str], dict[str, object]]] = []
    for item in scan.files:
        if item.status == "invalid":
            key = ("2", "", item.filename)
            rows.append((key, {"filename": item.filename, "status": "invalid",
                               "reason": item.reason, "started_at_utc": None,
                               "ended_at_utc": None, "slot_utc": None,
                               "drift": None, "check_pass": None,
                               "check_fail": None}))
            continue
        report = item.report
        assert report is not None
        status = ("selected" if item.filename in selected_names
                  else "excluded")
        reason = None if status == "selected" else "manual-off-slot"
        slot = slot_iso(report.started_dt) if status == "selected" else None
        key = ("0" if status == "selected" else "1", report.started_at,
               item.filename)
        rows.append((key, {"filename": item.filename, "status": status,
                           "reason": reason,
                           "started_at_utc": report.started_at,
                           "ended_at_utc": report.ended_at,
                           "slot_utc": slot, "drift": report.drift,
                           "check_pass": report.check_pass,
                           "check_fail": report.check_fail}))
    rows.sort(key=lambda row: row[0])
    truncated = len(rows) > MAX_RUNS_IN_INDEX
    return [row[1] for row in rows[:MAX_RUNS_IN_INDEX]], truncated


# ---------------------------------------------------------------- 报告构建（确定性、零墙钟）


REPORT_BOUNDARIES: tuple[str, ...] = (
    (
        "read-only audit of existing M14-127 report files only: the tool"
        " never runs the drift watcher, never touches the scheduler or"
        " any task registration, and never contacts the production stack"
    ),
    (
        "zero child-process execution, zero network, zero env-var"
        " reads, and zero wall clock: output is byte-for-byte"
        " reproducible for the same inputs"
    ),
    (
        "input filenames are strictly matched as"
        " drift-watch-YYYYMMDD-HHMMSS.json; companion .md and other"
        " entries are counted as ignored and never parsed; directory"
        " traversal and symlinked path components are rejected"
    ),
    (
        "scheduled selection keeps manual/off-slot reports visible as"
        " excluded (reason manual-off-slot) instead of silently deleting"
        " them; slot completeness is audited only over the inclusive"
        " range of the selected reports and never extrapolated beyond"
        " the first and last selected report"
    ),
    (
        "exit 0 requires every selected report to be valid and clean with"
        " no duplicate or missing slot and stable api/web digest anchors;"
        " zero valid or zero selected reports is a finding, never a pass"
    ),
    (
        "reports never contain absolute paths or secret-like values; all"
        " output text passes redact_secrets as a final defense before"
        " the atomic write"
    ),
    (
        "a history audit does not prove production readiness, long-term"
        " stability, reboot survival, or alert delivery; release"
        " approval remains human-only"
    ),
)


def build_history_report(scan: ScanResult, selection: Selection) -> dict[str, object]:
    slots = audit_slots(selection.selected)
    duplicates = find_duplicates(selection.selected)
    streak = longest_clean_streak(selection.selected)
    digests = digest_stability(selection.selected)
    findings = collect_findings(scan, selection, slots, duplicates, digests)
    runs, runs_truncated = build_runs_index(scan, selection)
    clean = sum(1 for r in selection.selected if not r.drift)
    first = selection.selected[0].started_at if selection.selected else None
    last = selection.selected[-1].started_at if selection.selected else None
    return {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "milestone": MILESTONE,
        "source": {
            "tool": SOURCE_TOOL_NAME,
            "milestone": SOURCE_MILESTONE,
            "schema_version": SOURCE_SCHEMA_VERSION,
            "anchor_services": list(ANCHOR_SERVICES),
        },
        "selection": {
            "mode": "scheduled",
            "scheduled_minutes": sorted(SCHEDULED_MINUTES),
            "scheduled_second_max": SCHEDULED_SECOND_MAX,
            "slot_interval_minutes": 15,
            "filename_timestamp_tolerance_seconds": 2,
        },
        "counts": {
            "matching_files": len(scan.files),
            "ignored_entries": scan.ignored_entries,
            "valid": len(scan.valid),
            "invalid": len(scan.invalid),
            "selected": len(selection.selected),
            "excluded": len(selection.excluded),
            "clean": clean,
            "drifted": len(selection.selected) - clean,
        },
        "first_selected_started_at_utc": first,
        "last_selected_started_at_utc": last,
        "slots": slots,
        "duplicates": duplicates,
        "longest_clean_streak": streak,
        "digest_stability": digests,
        "audit": {"pass": not findings, "findings": findings},
        "runs": runs,
        "runs_truncated": runs_truncated,
        "boundaries": list(REPORT_BOUNDARIES),
    }


def render_markdown(report: dict[str, object]) -> str:
    counts = report["counts"]
    assert isinstance(counts, dict)
    slots = report["slots"]
    assert isinstance(slots, dict)
    streak = report["longest_clean_streak"]
    assert isinstance(streak, dict)
    audit = report["audit"]
    assert isinstance(audit, dict)
    duplicates = report["duplicates"]
    assert isinstance(duplicates, dict)
    lines: list[str] = [
        f"# {report['milestone']} production drift watch 历史审计报告",
        "",
        f"- 工具：`{report['tool']}`（schema_version={report['schema_version']}）",
        (f"- 审计对象：M14-127 `{report['source']['tool']}` 报告"
         f"（schema_version={report['source']['schema_version']}，"
         f"锚点服务 {'/'.join(str(s) for s in report['source']['anchor_services'])}）"),
        "",
        "## 摘要",
        "",
        (f"- 文件：匹配 {counts['matching_files']}（valid {counts['valid']} / "
         f"invalid {counts['invalid']}），忽略条目 {counts['ignored_entries']}"),
        (f"- 选择：selected {counts['selected']}（clean {counts['clean']} / "
         f"drifted {counts['drifted']}），excluded {counts['excluded']}"
         "（manual-off-slot，可见保留）"),
        (f"- 首末入选时间（UTC）：{report['first_selected_started_at_utc']}"
         f" → {report['last_selected_started_at_utc']}"),
    ]
    if slots.get("expected") is None:
        lines.append("- slot 审计：不适用（零入选或范围超限）")
    else:
        lines.append(f"- slot：期望 {slots['expected']} / 在场 {slots['present']}"
                     f" / 缺失 {slots['missing']}")
        missing = slots["missing_slots"]
        assert isinstance(missing, list)
        for slot in missing:
            lines.append(f"  - 缺失 slot：{slot}")
        if slots.get("missing_slots_truncated"):
            lines.append("  -（缺失列表超上限已截断）")
    lines += [
        (f"- 最长连续 clean streak：{streak['runs']} 轮"
         + (f"（{streak['start_slot_utc']} → {streak['end_slot_utc']}）"
            if streak["runs"] else "")),
        "",
        "## digest 稳定性（API/Web）",
        "",
    ]
    digests = report["digest_stability"]
    assert isinstance(digests, dict)
    for service, info in digests.items():
        assert isinstance(info, dict)
        lines.append(
            f"- {service}: expected 唯一={info['expected_consistent']}，"
            f"running 唯一={info['running_consistent']}，"
            f"anchored={info['anchored']}")
    dup_started = duplicates["started_at"]
    dup_slots = duplicates["slots"]
    assert isinstance(dup_started, list) and isinstance(dup_slots, list)
    lines += [
        "",
        "## 重复检测",
        "",
        f"- started_at 重复组：{len(dup_started)}",
        f"- slot 重复组：{len(dup_slots)}",
    ]
    lines += [
        "",
        "## 审计结论",
        "",
        f"- audit pass=**{str(audit['pass']).lower()}**",
    ]
    findings = audit["findings"]
    assert isinstance(findings, list)
    for finding in findings:
        lines.append(f"  - 发现：{finding}")
    lines += ["", "## 逐报告索引（有界）", ""]
    runs = report["runs"]
    assert isinstance(runs, list)
    lines += [
        "| 文件 | 状态 | 原因 | started（UTC） | slot | drift | pass/fail |",
        "|---|---|---|---|---|---|---|",
    ]
    for run in runs:
        assert isinstance(run, dict)
        lines.append(
            f"| {run['filename']} | {run['status']} | {run['reason'] or '-'} "
            f"| {run['started_at_utc'] or '-'} | {run['slot_utc'] or '-'} "
            f"| {run['drift'] if run['drift'] is not None else '-'} "
            f"| {run['check_pass'] if run['check_pass'] is not None else '-'}"
            f"/{run['check_fail'] if run['check_fail'] is not None else '-'} |")
    if report.get("runs_truncated"):
        lines.append("")
        lines.append("（runs 索引超上限已截断）")
    lines += ["", "边界："]
    lines += [f"- {item}" for item in report["boundaries"]]  # type: ignore[arg-type]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 写出（原子）


class ReportPathError(RuntimeError):
    """报告路径非法（symlink 组件）——可见拒绝，零写入。"""


def _reject_symlinks(*paths: Path) -> None:
    for path in paths:
        if path.is_symlink():
            raise ReportPathError("symlink-target")
        for ancestor in path.parents:
            if ancestor.exists() and ancestor.is_symlink():
                raise ReportPathError("symlink-in-path")


def write_reports_atomic(report: dict[str, object], directory: Path) -> tuple[Path, Path]:
    """JSON + Markdown 双写：内容先经 redact_secrets 终防线，再同目录 tmp +
    fsync + os.replace 原子落盘；symlink 路径一律拒绝（零写入）。"""
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{REPORT_STEM}.json"
    md_path = directory / f"{REPORT_STEM}.md"
    _reject_symlinks(json_path, md_path, directory)
    json_text = redact_secrets(json.dumps(report, ensure_ascii=False,
                                          indent=2, sort_keys=True) + "\n")
    md_text = redact_secrets(render_markdown(report))
    for final_path, suffix, text in ((json_path, "json", json_text),
                                     (md_path, "md", md_text)):
        tmp_path = directory / f".{REPORT_STEM}.{suffix}.tmp"
        if tmp_path.is_symlink():
            raise ReportPathError("symlink-tmp")
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, final_path)
    return json_path, md_path


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="production_drift_watch_history.py",
        description=("M14-133 production drift watch 历史审计：只读扫描 M14-127 "
                     "drift-watch 报告目录，严格校验 schema、交叉校验文件名时间戳、"
                     "按 PT15M 槽位审计连续性与缺失、检测重复与 digest 漂移，输出"
                     "确定性 JSON+MD 审计报告（零墙钟、零子进程、零网络、零 env 读取；"
                     "任何审计发现或输入拒绝均 exit 2）"),
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR,
                        help=("待审计的 drift-watch 报告目录（默认 .verify/artifacts/"
                              "m14-127-production-drift-watch，gitignored；含 .. 或 "
                              "symlink 组件一律拒绝）"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help=("审计报告输出目录（默认 .verify/artifacts/"
                              "m14-133-drift-watch-history，gitignored；输出文件名固定"
                              " drift-watch-history.json/.md，同输入逐字节可复现）"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = SafeLog()
    # 1) 路径 fail-closed：traversal / symlink 组件 / 输入目录缺失（零输出）
    try:
        reject_path_problems(args.input_dir, must_exist_as_dir=True)
        reject_path_problems(args.output_dir)
    except HistoryError as cause:
        log.say(f"拒绝: 输入/输出路径非法（原因: {cause}）")
        return EXIT_REFUSE
    # 2) 只读扫描 + 严格校验 + 纯审计
    log.say(f"=== M14-133 production drift watch 历史审计（只读；输入 {args.input_dir.name}） ===")
    scan = scan_directory(args.input_dir)
    selection = select_scheduled(scan.valid)
    report = build_history_report(scan, selection)
    # 3) 原子写出确定性报告（数据域审计发现照常落盘——证据不可失）
    try:
        json_path, md_path = write_reports_atomic(report, args.output_dir)
        log.say(f"审计报告: {json_path.name} / {md_path.name}")
    except (ReportPathError, OSError) as cause:
        log.say(f"审计报告写入失败（按拒绝处理）: {type(cause).__name__}")
        return EXIT_REFUSE
    counts = report["counts"]
    assert isinstance(counts, dict)
    audit = report["audit"]
    assert isinstance(audit, dict)
    log.say(f"文件: 匹配 {counts['matching_files']}（valid {counts['valid']} / "
            f"invalid {counts['invalid']}） + 忽略 {counts['ignored_entries']}")
    log.say(f"选择: selected {counts['selected']}（clean {counts['clean']} / "
            f"drifted {counts['drifted']}） excluded {counts['excluded']}")
    for finding in audit["findings"]:  # type: ignore[index]
        log.say(f"审计发现: {finding}")
    log.say("=== 结果: audit pass=" + str(audit["pass"]).lower()
            + "（历史审计不证明 production readiness / 长期稳定性 / 重启存活 / 告警送达） ===")
    return EXIT_OK if audit["pass"] else EXIT_REFUSE


if __name__ == "__main__":
    sys.exit(main())
