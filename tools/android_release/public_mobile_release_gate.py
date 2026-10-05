#!/usr/bin/env python
"""M14-237 公网移动发布聚合门（public mobile release gate）。

只读、fail-closed 的**聚合**门：把既有工具机器导出的证据合成为一个专门
结论——「public mobile ready」——替代人工拼装结论。它**绝不重复任何子
工具的探测语义**：不连网络、不访问设备、不读 secret/env、不执行
Docker/生产动作、零子进程。唯一写入是显式要求的报告输出（tmp+fsync+
os.replace 原子写 + 写后重读校验）。

## 输入（全部显式提供；缺失/损坏/篡改/矛盾/过期一律 blocked）

| CLI 参数 | 分类 | 消费的子工具报告 |
| --- | --- | --- |
| ``--restore-preflight`` | machine-report | ``tools/ops/production_restore_preflight.py``（schema ``aios-production-restore-preflight/2``） |
| ``--edge-preflight`` | machine-report | ``tools/ops/public_edge_preflight.py``（schema ``aios-public-edge-preflight/1``） |
| ``--device-smoke`` | machine-report | ``tools/android_release/public_device_smoke.py``（schema_version 1，report.json） |
| ``--cloudflare-preflight`` | machine-report（可选） | ``tools/ops/cloudflare_ingress_preflight.py``（schema ``aios-cloudflare-ingress-preflight/1``；消费其**已脱敏报告**，绝不读凭据） |
| ``--release-evidence`` | evidence-status | **仓库既有权威导出器**二选一：``provider-smoke-aggregate``（``services/api/app/ops/provider_smoke_evidence.py``：tool ``provider-smoke-evidence`` + schema_version ``provider-smoke-evidence-v1`` + gate ``provider-smoke``，voice/search/llm 三槽位全部 executed=true 且 result=pass）或 ``release-check``（``services/api/app/ops/release_check.py``：tool/gate ``release-check`` + all_green=true）。**白名单之外（含任意其他 tool 字符串）一律拒绝**——不发明未来导出器 |
| ``--attestation`` | human-attestation | 受约束人工签认（版本/观察时间/有效期/观察者/结论/覆盖项/文件哈希——不接受一句自声明） |

## 判定（只消费机器导出字段 + 聚合层一致性）

- 每份机器报告：schema/tool 自标识、顶层时间戳、该工具自己的成功语义
  （restore verdict=healthy；edge exit_code=0 且 summary.failed=0 且
  mobile_attestation=attested；smoke status=passed 且其自带
  evidence.files 清单与同目录实际文件逐字节 SHA-256 一致；cloudflare
  status=pass；release evidence 按上述双契约白名单）。
- 人工签认：五项必需覆盖（4G/5G 实网、跨源 cookie、真实语音、TURN
  relay、APK 下载安装）+ conclusion=pass + 有效期覆盖评估时刻 +
  ``evidence_files`` 至少锚定一份本门实际消费的输入文件（basename +
  SHA-256 全等），锚不上即 blocked（防自声明）。
- 时序（评估时刻 ``evaluation_time``：CLI 为真实当前 UTC；库调用显式
  注入——契约测试用固定 UTC）：报告顶层 ``generated_at`` 即评估时刻
  （真实评估时间，非证据时间）；``reference.evidence_frontier`` 为全部
  可解析**机器**报告时间戳的最大值。三重新鲜度校验：
  1. 每份机器报告时间戳距 frontier ≤ ``--freshness-hours``（默认 24）
     → 超限 ``stale-input``；
  2. 机器时间戳 > 评估时刻 + 300s 容差 → ``future-timestamp``（未来
     时间拒绝）；frontier 本身距评估时刻 ≤ freshness → 超限
     ``stale-frontier``（防止整批陈旧证据互相背书）；
  3. 人工 observed_at ≤ 评估时刻+300s、距评估时刻 ≤ freshness、
     valid_until ≥ 评估时刻——违者分别拒绝/判陈旧/判过期。时间戳
     malformed/缺失的机器输入自带 blocker，不参与 frontier 也不放行。
- 跨报告一致：cloudflare 报告 zone 与 edge 报告全部公网 host 的归属
  域一致；报告自标识与输入分类错位拒绝。

## 与 release authorization 分离（保守边界）

``public_mobile_ready=true`` **只**表示上述证据在同一证据前沿下全部满
足；输出固定携带 ``release_authorization.human_release_approval =
"not-asserted"`` 与 ``production_readiness = "not-asserted"``——本门
绝不伪造人工发布批准或整体生产就绪。

## 退出码

- 0 = ready（报告已写出）；
- 1 = blocked（可见结论：报告已写出、blockers 与下一步只读命令逐条
  列出）；
- 2 = 拒绝（参数越界 / 评估时刻非 tz-aware / 输入路径为
  symlink/reparse point/非常规文件 / 连一个机器时间锚都无法解析 /
  输出写出或重读校验失败——零写入或输出保持原子）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
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

TOOL_NAME = "android_release_public_mobile_release_gate"
SCHEMA = "aios-public-mobile-release-gate/1"
REPORT_JSON_NAME = "public-mobile-release-gate.json"
REPORT_MD_NAME = "public-mobile-release-gate.md"

EXIT_READY = 0
EXIT_BLOCKED = 1
EXIT_REFUSED = 2

DEFAULT_FRESHNESS_HOURS = 24
MIN_FRESHNESS_HOURS = 1
MAX_FRESHNESS_HOURS = 24 * 30
CLOCK_TOLERANCE = timedelta(seconds=300)

RESTORE_KEY = "restore_preflight"
EDGE_KEY = "edge_preflight"
SMOKE_KEY = "device_smoke"
CF_KEY = "cloudflare_preflight"
EVIDENCE_KEY = "release_evidence"
ATTEST_KEY = "attestation"

RESTORE_SCHEMA = "aios-production-restore-preflight/2"
RESTORE_TOOL = "production_restore_preflight"
EDGE_SCHEMA = "aios-public-edge-preflight/1"
EDGE_TOOL = "public_edge_preflight"
SMOKE_SCHEMA_VERSION = 1
SMOKE_TOOL = "android_release_public_device_smoke"
CF_SCHEMA = "aios-cloudflare-ingress-preflight/1"
CF_TOOL = "cloudflare_ingress_preflight"
SMOKE_REPORT_JSON = "report.json"

ATTESTATION_SCHEMA_VERSION = 1
ATTESTATION_KIND = "public-mobile-attestation"
ATTESTATION_CONCLUSION_PASS = "pass"
REQUIRED_ATTESTED_ITEMS: tuple[str, ...] = (
    "mobile-4g5g-open",
    "mobile-cross-origin-cookie",
    "mobile-voice-connect",
    "mobile-turn-relay",
    "mobile-android-apk",
)

# Defect-2 修复：release evidence 输入只接受**仓库既有**权威导出器产物
# （精确 tool/schema/gate 白名单——任意其他 tool 字符串一律拒绝）：
# 1. provider-smoke 聚合证据：services/api/app/ops/provider_smoke_evidence.py
#    经 `python -m app.ops.cli provider-smoke-aggregate` 导出；
# 2. release-check 报告：services/api/app/ops/release_check.py
#    经 `python -m app.ops.cli release-check` 导出。
PROVIDER_SMOKE_TOOL = "provider-smoke-evidence"
PROVIDER_SMOKE_SCHEMA_VERSION = "provider-smoke-evidence-v1"
PROVIDER_SMOKE_GATE = "provider-smoke"
PROVIDER_SMOKE_KEYS = ("voice", "search", "llm")
RELEASE_CHECK_TOOL = "release-check"

CLASS_MACHINE = "machine-report"
CLASS_HUMAN = "human-attestation"
CLASS_EVIDENCE = "evidence-status"

INPUT_CLASSIFICATIONS: dict[str, str] = {
    RESTORE_KEY: CLASS_MACHINE,
    EDGE_KEY: CLASS_MACHINE,
    SMOKE_KEY: CLASS_MACHINE,
    CF_KEY: CLASS_MACHINE,
    EVIDENCE_KEY: CLASS_EVIDENCE,
    ATTEST_KEY: CLASS_HUMAN,
}

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ISO_UTC_OUTPUT = "%Y-%m-%dT%H:%M:%SZ"

TAG = "[public-mobile-release-gate]"


class GateRefused(RuntimeError):
    """fail-closed 拒绝（固定词汇原因；零写入）。"""


# ---------------------------------------------------------------- 时间解析


def parse_utc_timestamp(value: Any) -> datetime:
    """严格 ISO-8601 UTC 时间解析（接受 ``Z`` 与 ``+00:00`` 尾形）。

    非 str / 非 UTC 偏移 / 解析失败 → ``GateRefused``（时间锚是聚合门
    的时间真相源，坏时间戳绝不静默放行）。
    """
    if not isinstance(value, str) or not value:
        raise GateRefused("timestamp-format")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise GateRefused("timestamp-format") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise GateRefused("timestamp-not-utc")
    return parsed.astimezone(timezone.utc)


def format_utc(moment: datetime) -> str:
    return moment.strftime(ISO_UTC_OUTPUT)


# ---------------------------------------------------------------- 输入读取


@dataclass
class LoadedInput:
    """已读取的输入（字节级锚定；绝不携带文件内容进入任何输出）。"""

    key: str
    path: Path
    basename: str
    payload: dict[str, Any]
    raw: bytes
    timestamp: datetime | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()


def _refuse_unsafe_path(store: Store, path: Path, key: str) -> None:
    """symlink / Windows reparse point / 非常规文件 → 拒绝（exit 2）。"""
    if not store.exists(path):
        return  # 缺失走 blocked（可见结论），不是安全拒绝
    try:
        reject_symlinked_path(store, path)
    except HistoryError:
        raise GateRefused(f"symlink-input:{key}") from None
    if is_link_or_reparse(path):
        raise GateRefused(f"reparse-input:{key}")
    if not path.is_file():
        raise GateRefused(f"not-regular-file:{key}")


def load_input(
    store: Store, key: str, path: Path | None
) -> tuple[LoadedInput | None, str | None]:
    """读取并字节锚定一个输入；返回 (loaded, missing_reason)。"""
    if path is None:
        return None, f"missing-input:{key}"
    _refuse_unsafe_path(store, path, key)
    if not store.exists(path):
        return None, f"missing-input:{key}"
    raw = store.read_bytes(path)
    if not raw:
        return None, f"empty-input:{key}"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, f"malformed-input:{key}"
    if not isinstance(payload, dict):
        return None, f"malformed-input:{key}:not-an-object"
    return LoadedInput(
        key=key, path=path, basename=path.name, payload=payload, raw=raw
    ), None


# ------------------------------------------------------- 每类输入的契约校验


def _require_str(payload: dict[str, Any], field_name: str) -> str | None:
    value = payload.get(field_name)
    return value if isinstance(value, str) and value else None


def _require_sha256(value: Any) -> str | None:
    return value if isinstance(value, str) and SHA256_RE.match(value) else None


@dataclass
class InputCheck:
    reasons: list[str] = field(default_factory=list)
    timestamp: datetime | None = None
    hosts: tuple[str, ...] = ()
    zone_name: str | None = None


def check_restore(item: LoadedInput, out: InputCheck) -> None:
    payload = item.payload
    if payload.get("schema") != RESTORE_SCHEMA:
        out.reasons.append(f"schema-mismatch:{RESTORE_KEY}")
    if payload.get("tool") != RESTORE_TOOL:
        out.reasons.append(f"tool-mismatch:{RESTORE_KEY}")
    stamp = payload.get("generated_at")
    if stamp is None:
        out.reasons.append(f"timestamp-missing:{RESTORE_KEY}")
    else:
        try:
            out.timestamp = parse_utc_timestamp(stamp)
        except GateRefused:
            out.reasons.append(f"timestamp-format:{RESTORE_KEY}")
    verdict = payload.get("verdict")
    if verdict != "healthy":
        out.reasons.append(f"status-not-passing:{RESTORE_KEY}:{verdict}")
    blockers = payload.get("blockers")
    if isinstance(blockers, list) and blockers:
        out.reasons.append(f"restore-preflight-blockers:{len(blockers)}")


def check_edge(item: LoadedInput, out: InputCheck) -> None:
    payload = item.payload
    if payload.get("schema") != EDGE_SCHEMA:
        out.reasons.append(f"schema-mismatch:{EDGE_KEY}")
    if payload.get("tool") != EDGE_TOOL:
        out.reasons.append(f"tool-mismatch:{EDGE_KEY}")
    stamp = payload.get("generated_at")
    if stamp is None:
        out.reasons.append(f"timestamp-missing:{EDGE_KEY}")
    else:
        try:
            out.timestamp = parse_utc_timestamp(stamp)
        except GateRefused:
            out.reasons.append(f"timestamp-format:{EDGE_KEY}")
    summary = payload.get("summary")
    attestation = payload.get("mobile_attestation")
    exit_code = payload.get("exit_code")
    summary_ok = (
        isinstance(summary, dict)
        and isinstance(summary.get("total"), int)
        and summary.get("total", 0) > 0
        and summary.get("failed") == 0
    )
    attested = isinstance(attestation, dict) and attestation.get("status") == "attested"
    if exit_code != 0 or not summary_ok or not attested:
        out.reasons.append(f"status-not-passing:{EDGE_KEY}:exit={exit_code}")
    endpoints = payload.get("endpoints")
    if isinstance(endpoints, dict):
        for key in ("app_url", "api_url", "livekit_url"):
            url = endpoints.get(key)
            if isinstance(url, str) and url.startswith("https://"):
                host = url.split("/", 3)[2]
                if host:
                    out.hosts += (host,)


def _smoke_internal_evidence(
    store: Store, item: LoadedInput, out: InputCheck
) -> None:
    """smoke 自带 evidence.files 清单 vs 同目录实际文件逐字节 SHA-256。"""
    evidence = item.payload.get("evidence")
    files = evidence.get("files") if isinstance(evidence, dict) else None
    if not isinstance(files, list) or not files:
        out.reasons.append(f"evidence-missing:{SMOKE_KEY}")
        return
    for record in files:
        name = record.get("name") if isinstance(record, dict) else None
        expected = _require_sha256(record.get("sha256")) if isinstance(record, dict) else None
        if (
            not isinstance(name, str)
            or not name
            or expected is None
            or "/" in name
            or "\\" in name
            or name in (".", "..")
        ):
            out.reasons.append(f"evidence-record-invalid:{SMOKE_KEY}")
            continue
        sibling = item.path.parent / name
        try:
            reject_symlinked_path(store, sibling)
        except HistoryError:
            out.reasons.append(f"evidence-file-unsafe:{SMOKE_KEY}:{name}")
            continue
        if not store.exists(sibling):
            out.reasons.append(f"evidence-file-missing:{SMOKE_KEY}:{name}")
            continue
        actual = hashlib.sha256(store.read_bytes(sibling)).hexdigest()
        if actual != expected:
            out.reasons.append(f"evidence-hash-mismatch:{SMOKE_KEY}:{name}")


def check_smoke(store: Store, item: LoadedInput, out: InputCheck) -> None:
    payload = item.payload
    if payload.get("schema_version") != SMOKE_SCHEMA_VERSION:
        out.reasons.append(f"schema-mismatch:{SMOKE_KEY}")
    if payload.get("tool") != SMOKE_TOOL:
        out.reasons.append(f"tool-mismatch:{SMOKE_KEY}")
    stamp = payload.get("generated_at")
    if stamp is None:
        out.reasons.append(f"timestamp-missing:{SMOKE_KEY}")
    else:
        try:
            out.timestamp = parse_utc_timestamp(stamp)
        except GateRefused:
            out.reasons.append(f"timestamp-format:{SMOKE_KEY}")
    if payload.get("status") != "passed":
        out.reasons.append(f"status-not-passing:{SMOKE_KEY}:{payload.get('status')}")
    _smoke_internal_evidence(store, item, out)


def check_cloudflare(item: LoadedInput, out: InputCheck) -> None:
    payload = item.payload
    if payload.get("schema") != CF_SCHEMA:
        out.reasons.append(f"schema-mismatch:{CF_KEY}")
    if payload.get("tool") != CF_TOOL:
        out.reasons.append(f"tool-mismatch:{CF_KEY}")
    stamp = payload.get("generated_at")
    if stamp is None:
        out.reasons.append(f"timestamp-missing:{CF_KEY}")
    else:
        try:
            out.timestamp = parse_utc_timestamp(stamp)
        except GateRefused:
            out.reasons.append(f"timestamp-format:{CF_KEY}")
    if payload.get("status") != "pass" or payload.get("exit_code") != 0:
        out.reasons.append(f"status-not-passing:{CF_KEY}:{payload.get('status')}")
    zone = _require_str(payload, "zone_name")
    if zone is None:
        out.reasons.append(f"zone-missing:{CF_KEY}")
    out.zone_name = zone


def _check_provider_smoke_evidence(payload: dict[str, Any]) -> list[str]:
    """provider-smoke-aggregate 权威契约（providers 三槽位全 pass）。"""
    reasons: list[str] = []
    if payload.get("tool") != PROVIDER_SMOKE_TOOL:
        reasons.append(f"release-evidence-contract:tool:{payload.get('tool')}")
        return reasons
    if payload.get("schema_version") != PROVIDER_SMOKE_SCHEMA_VERSION:
        reasons.append("release-evidence-contract:schema-version")
    if payload.get("gate") != PROVIDER_SMOKE_GATE:
        reasons.append(f"release-evidence-contract:gate:{payload.get('gate')}")
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        reasons.append("release-evidence-contract:providers")
        return reasons
    for key in PROVIDER_SMOKE_KEYS:
        record = providers.get(key)
        if not isinstance(record, dict):
            reasons.append(f"release-evidence-contract:provider:{key}")
            continue
        if record.get("executed") is not True or record.get("result") != "pass":
            reasons.append(
                f"release-evidence-status:provider-smoke:{key}"
                f":{record.get('result')}"
            )
    return reasons


def _check_release_check(payload: dict[str, Any]) -> list[str]:
    """release-check 权威契约（all_green=true）。"""
    reasons: list[str] = []
    if payload.get("tool") != RELEASE_CHECK_TOOL:
        reasons.append(f"release-evidence-contract:tool:{payload.get('tool')}")
        return reasons
    if payload.get("gate") != RELEASE_CHECK_TOOL:
        reasons.append(f"release-evidence-contract:gate:{payload.get('gate')}")
    if payload.get("all_green") is not True:
        reasons.append(
            f"release-evidence-status:release-check:"
            f"all_green={payload.get('all_green')}"
        )
    return reasons


def check_release_evidence(item: LoadedInput, out: InputCheck) -> None:
    """双契约白名单：只认 provider-smoke 聚合证据或 release-check 报告。"""
    payload = item.payload
    tool = payload.get("tool")
    if tool == PROVIDER_SMOKE_TOOL:
        out.reasons.extend(_check_provider_smoke_evidence(payload))
    elif tool == RELEASE_CHECK_TOOL:
        out.reasons.extend(_check_release_check(payload))
    else:
        out.reasons.append(
            f"release-evidence-contract:tool-not-allowed:{tool}"
        )
    stamp = payload.get("generated_at")
    if stamp is None:
        out.reasons.append(f"timestamp-missing:{EVIDENCE_KEY}")
    else:
        try:
            out.timestamp = parse_utc_timestamp(stamp)
        except GateRefused:
            out.reasons.append(f"timestamp-format:{EVIDENCE_KEY}")


def check_attestation(
    item: LoadedInput,
    out: InputCheck,
    *,
    evaluation_time: datetime,
    freshness: timedelta,
    input_hashes: dict[str, set[str]],
) -> None:
    """受约束人工签认校验（结论=pass/覆盖五项/有效期/证据文件锚定）。

    时间语义全部相对**评估时刻**：observed_at 不得晚于评估时刻+容差、
    不得距评估时刻超过 freshness、valid_until 必须覆盖评估时刻。
    """
    payload = item.payload
    if payload.get("schema_version") != ATTESTATION_SCHEMA_VERSION:
        out.reasons.append("attestation-invalid:schema-version")
    if payload.get("kind") != ATTESTATION_KIND:
        out.reasons.append("attestation-invalid:kind")
    if _require_str(payload, "observed_by") is None:
        out.reasons.append("attestation-invalid:observed-by")
    observed_at = payload.get("observed_at")
    valid_until = payload.get("valid_until")
    observed: datetime | None = None
    try:
        observed = parse_utc_timestamp(observed_at)
        out.timestamp = observed
    except GateRefused:
        out.reasons.append("attestation-invalid:observed-at")
    try:
        expires = parse_utc_timestamp(valid_until)
    except GateRefused:
        expires = None
        out.reasons.append("attestation-invalid:valid-until")
    if payload.get("conclusion") != ATTESTATION_CONCLUSION_PASS:
        out.reasons.append(f"attestation-invalid:conclusion:{payload.get('conclusion')}")
    covered = payload.get("covered_items")
    if not isinstance(covered, list) or not all(
        isinstance(entry, str) for entry in covered
    ):
        out.reasons.append("attestation-invalid:covered-items")
        covered = []
    missing_items = [req for req in REQUIRED_ATTESTED_ITEMS if req not in covered]
    if missing_items:
        out.reasons.append(f"attestation-incomplete:{','.join(missing_items)}")
    anchored = False
    files = payload.get("evidence_files")
    if not isinstance(files, list) or not files:
        out.reasons.append("attestation-invalid:evidence-files")
        files = []
    for record in files:
        if not isinstance(record, dict):
            out.reasons.append("attestation-invalid:evidence-file-record")
            continue
        name = record.get("name")
        digest = _require_sha256(record.get("sha256"))
        if not isinstance(name, str) or not name or digest is None:
            out.reasons.append("attestation-invalid:evidence-file-record")
            continue
        if name in input_hashes:
            if digest not in input_hashes[name]:
                out.reasons.append(f"hash-mismatch:{ATTEST_KEY}:{name}")
            else:
                anchored = True
    if not anchored:
        out.reasons.append("attestation-unanchored")
    if observed is not None:
        if observed > evaluation_time + CLOCK_TOLERANCE:
            out.reasons.append("attestation-invalid:observed-in-future")
        if evaluation_time - observed > freshness:
            out.reasons.append("attestation-stale")
    if expires is not None and expires < evaluation_time:
        out.reasons.append("attestation-expired")


# ---------------------------------------------------------------- 聚合


@dataclass
class GateResult:
    blockers: list[str]
    entries: dict[str, dict[str, Any]]
    frontier: datetime | None
    attestation_hosts_note: str


def _host_matches_zone(host: str, zone: str) -> bool:
    return host == zone or host.endswith(f".{zone}")


def run_gate(
    store: Store,
    *,
    restore: Path | None,
    edge: Path | None,
    smoke: Path | None,
    cloudflare: Path | None,
    release_evidence: Path,
    attestation: Path,
    output_dir: Path,
    freshness_hours: int,
    evaluation_time: datetime,
) -> tuple[dict[str, Any], int]:
    """执行聚合门；返回 (report, exit_code)。拒绝场景抛 ``GateRefused``。

    ``evaluation_time`` 是评估时刻（报告顶层 ``generated_at`` 的语义）：
    CLI 传入真实当前 UTC；契约测试注入固定 UTC。机器证据前沿
    （``reference.evidence_frontier``）为机器报告时间戳最大值，三重
    新鲜度校验全部以评估时刻为锚——见模块 docstring「时序」。
    """
    if not isinstance(evaluation_time, datetime) or evaluation_time.tzinfo is None:
        raise GateRefused("evaluation-time-not-tz-aware")
    evaluation_time = evaluation_time.astimezone(timezone.utc)
    paths: dict[str, Path | None] = {
        RESTORE_KEY: restore,
        EDGE_KEY: edge,
        SMOKE_KEY: smoke,
        CF_KEY: cloudflare,
        EVIDENCE_KEY: release_evidence,
        ATTEST_KEY: attestation,
    }
    loaded: dict[str, LoadedInput | None] = {}
    blockers: list[str] = []
    for key, path in paths.items():
        if key == CF_KEY and path is None:
            loaded[key] = None  # 可选输入：未提供不是 blocker
            continue
        item, missing = load_input(store, key, path)
        loaded[key] = item
        if missing:
            blockers.append(missing)

    freshness = timedelta(hours=freshness_hours)
    checks: dict[str, InputCheck] = {key: InputCheck() for key in paths}
    # basename → {sha256}：跨目录同名输入合法；attestation 锚定按
    # (basename, sha256) 对匹配，同名不同字节不产生歧义。
    input_hashes: dict[str, set[str]] = {}
    for key, item in loaded.items():
        if item is None:
            continue
        input_hashes.setdefault(item.basename, set()).add(item.sha256)

    if loaded[RESTORE_KEY] is not None:
        check_restore(loaded[RESTORE_KEY], checks[RESTORE_KEY])  # type: ignore[arg-type]
    if loaded[EDGE_KEY] is not None:
        check_edge(loaded[EDGE_KEY], checks[EDGE_KEY])  # type: ignore[arg-type]
    if loaded[SMOKE_KEY] is not None:
        check_smoke(store, loaded[SMOKE_KEY], checks[SMOKE_KEY])  # type: ignore[arg-type]
    if loaded[CF_KEY] is not None:
        check_cloudflare(loaded[CF_KEY], checks[CF_KEY])  # type: ignore[arg-type]
    if loaded[EVIDENCE_KEY] is not None:
        check_release_evidence(loaded[EVIDENCE_KEY], checks[EVIDENCE_KEY])  # type: ignore[arg-type]

    machine_keys = (RESTORE_KEY, EDGE_KEY, SMOKE_KEY, CF_KEY, EVIDENCE_KEY)
    stamps = [
        checks[key].timestamp
        for key in machine_keys
        if loaded[key] is not None and checks[key].timestamp is not None
    ]
    frontier = max(stamps) if stamps else None
    if frontier is None:
        # 连一个机器时间锚都没有：无法定义证据前沿 → 拒绝（零写入）。
        raise GateRefused("no-machine-timestamp")

    # Defect-1 修复：时间语义以**评估时刻**为锚，证据前沿只是聚合窗口。
    # malformed/missing 时间戳的机器输入已各自携带 blocker（上方
    # check_*），不参与 frontier 也不可能放行——不存在绕过路径。
    for key in machine_keys:
        if loaded[key] is None:
            continue
        stamp = checks[key].timestamp
        if stamp is None:
            continue  # 已按 timestamp-missing/-format 记 blocker
        if stamp > evaluation_time + CLOCK_TOLERANCE:
            blockers.append(f"future-timestamp:{key}")
        if frontier - stamp > freshness:
            blockers.append(f"stale-input:{key}")
    if evaluation_time - frontier > freshness:
        # 整批机器证据相对评估时刻过旧（互相背书不得替代真实新鲜度）。
        blockers.append("stale-frontier")

    if loaded[ATTEST_KEY] is not None:
        check_attestation(
            loaded[ATTEST_KEY],
            checks[ATTEST_KEY],
            evaluation_time=evaluation_time,
            freshness=freshness,
            input_hashes=input_hashes,
        )

    cf_check = checks[CF_KEY]
    if cf_check.zone_name and checks[EDGE_KEY].hosts:
        for host in checks[EDGE_KEY].hosts:
            if not _host_matches_zone(host, cf_check.zone_name):
                blockers.append("cross-report-conflict:cloudflare-zone-vs-edge-host")
                break

    entries: dict[str, dict[str, Any]] = {}
    for key in paths:
        item = loaded[key]
        entry: dict[str, Any] = {
            "classification": INPUT_CLASSIFICATIONS[key],
            "present": item is not None,
        }
        if item is not None:
            entry.update(
                {
                    "file": item.basename,
                    "bytes": len(item.raw),
                    "sha256": item.sha256,
                    "schema": item.payload.get("schema", item.payload.get("schema_version")),
                    "tool": item.payload.get("tool"),
                    "observed_at": (
                        format_utc(checks[key].timestamp)
                        if checks[key].timestamp is not None
                        else None
                    ),
                }
            )
        entry["reasons"] = sorted(checks[key].reasons)
        blockers.extend(checks[key].reasons)
        entries[key] = entry

    # 去重 + 确定性排序
    blockers = sorted(set(blockers))
    ready = not blockers
    report = build_report(
        entries=entries,
        blockers=blockers,
        frontier=frontier,
        evaluation_time=evaluation_time,
        freshness_hours=freshness_hours,
        ready=ready,
        edge_payload=loaded[EDGE_KEY].payload if loaded[EDGE_KEY] else None,
        smoke_payload=loaded[SMOKE_KEY].payload if loaded[SMOKE_KEY] else None,
        gate_args=_gate_replay_args(
            restore, edge, smoke, cloudflare, release_evidence, attestation,
            output_dir, freshness_hours,
        ),
    )
    write_report(store, output_dir, report)
    exit_code = EXIT_READY if ready else EXIT_BLOCKED
    return report, exit_code


def _gate_replay_args(
    restore: Path | None,
    edge: Path | None,
    smoke: Path | None,
    cloudflare: Path | None,
    release_evidence: Path,
    attestation: Path,
    output_dir: Path,
    freshness_hours: int,
) -> str:
    parts = [
        f"--restore-preflight {_display(restore)}",
        f"--edge-preflight {_display(edge)}",
        f"--device-smoke {_display(smoke)}",
    ]
    if cloudflare is not None:
        parts.append(f"--cloudflare-preflight {_display(cloudflare)}")
    parts.extend(
        [
            f"--release-evidence {_display(release_evidence)}",
            f"--attestation {_display(attestation)}",
            f"--output {_display(output_dir)}",
            f"--freshness-hours {freshness_hours}",
        ]
    )
    return "python tools/android_release/public_mobile_release_gate.py " + " ".join(parts)


def _display(path: Path | None) -> str:
    """命令回显形态：正斜杠；未提供 → 占位符（绝不编造路径）。"""
    if path is None:
        return "<path>"
    return path.as_posix()


# ---------------------------------------------------------------- 报告


SUB_TOOL_KEYS = (RESTORE_KEY, EDGE_KEY, SMOKE_KEY, CF_KEY, EVIDENCE_KEY)


def _next_actions(
    blockers: list[str],
    *,
    edge_payload: dict[str, Any] | None,
    smoke_payload: dict[str, Any] | None,
    gate_args: str,
) -> list[dict[str, str]]:
    """每个 blocker 一条可直接执行的只读命令（人工项给回放 + 说明）。"""
    actions: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(blocker: str, command: str, note: str) -> None:
        marker = (command, note)
        if marker in seen:
            return
        seen.add(marker)
        actions.append({"for": blocker, "command": command, "note": note})

    def edge_url_args() -> str:
        if isinstance(edge_payload, dict):
            endpoints = edge_payload.get("endpoints")
            if isinstance(endpoints, dict):
                parts = []
                for key, flag in (
                    ("app_url", "--app-url"),
                    ("api_url", "--api-url"),
                    ("livekit_url", "--livekit-url"),
                ):
                    value = endpoints.get(key)
                    if isinstance(value, str) and value:
                        parts.append(f"{flag} {value}")
                if parts:
                    return " ".join(parts)
        return "--app-url <app-url> --api-url <api-url>"

    def smoke_args() -> str:
        if isinstance(smoke_payload, dict):
            serial = smoke_payload.get("serial")
            manifest = None
            artifact = smoke_payload.get("artifact")
            if isinstance(artifact, dict):
                manifest = artifact.get("manifest_url")
            parts = []
            if isinstance(serial, str) and serial:
                parts.append(f"--serial {serial}")
            if isinstance(manifest, str) and manifest:
                parts.append(f"--manifest-url {manifest}")
            if parts:
                return " ".join(parts)
        return "--serial <serial> --manifest-url <manifest-url>"

    sub_tool_commands: dict[str, tuple[str, str]] = {
        RESTORE_KEY: (
            (
                "python tools/ops/production_restore_preflight.py "
                "--output <restore-report.json>"
            ),
            "只读本地 preflight（无 --apply、不触 Docker 数据面）",
        ),
        EDGE_KEY: (
            "python tools/ops/public_edge_preflight.py", "只读公网边缘 preflight"
        ),
        SMOKE_KEY: (
            "python tools/android_release/public_device_smoke.py",
            "单设备单制品物理 smoke（参数取自既有报告）",
        ),
        CF_KEY: (
            (
                "python tools/ops/cloudflare_ingress_preflight.py "
                "--config <cf-config.json> --execute --output <cf-report.json>"
            ),
            "可选凭据预检的 execute 报告（凭据由操作者自备，本门不读）",
        ),
        EVIDENCE_KEY: (
            (
                "python -m app.ops.cli provider-smoke-aggregate "
                "--search <search-step.json> --voice <voice-step.json> "
                "--llm <llm-step.json> --output <release-evidence.json>"
            ),
            (
                "重新机器导出 provider-smoke 聚合证据（services/api；单步证据由 "
                "provider-smoke-export 导出）；或改用 release-check 报告"
                "（python -m app.ops.cli release-check）"
            ),
        ),
    }

    def sub_tool_command(key: str) -> tuple[str, str]:
        command, note = sub_tool_commands[key]
        if key == EDGE_KEY:
            command = f"{command} {edge_url_args()} --output <edge-report.json>"
        elif key == SMOKE_KEY:
            command = f"{command} {smoke_args()} --output <smoke-dir>"
        return command, note

    attestation_note = (
        "人工观察需重签受约束 attestation（覆盖 "
        + ", ".join(REQUIRED_ATTESTED_ITEMS)
        + "）；命令为重放本门的只读回放"
    )
    for blocker in blockers:
        family = blocker.split(":", 1)[0]
        if family.startswith("release-evidence"):
            # 连字符族（release-evidence-contract/-status）不携带下划线
            # key——显式路由到权威导出器再生成命令。
            command, note = sub_tool_command(EVIDENCE_KEY)
            add(blocker, command, note)
            continue
        routed = False
        for part in blocker.split(":")[1:]:
            for key in SUB_TOOL_KEYS:
                if part.startswith(key):
                    command, note = sub_tool_command(key)
                    add(blocker, command, note)
                    routed = True
                    break
            if routed:
                break
        if routed:
            continue
        if family.startswith("attestation") or family == "hash-mismatch":
            add(blocker, gate_args, attestation_note)
        elif family == "cross-report-conflict":
            add(blocker, gate_args,
                "cloudflare zone 与 edge 端点 host 不属同一公网拓扑——校正后再聚合")
        else:
            add(blocker, gate_args, "按对应源工具重新机器导出证据后重放本门（只读）")
    return actions


def build_report(
    *,
    entries: dict[str, dict[str, Any]],
    blockers: list[str],
    frontier: datetime,
    evaluation_time: datetime,
    freshness_hours: int,
    ready: bool,
    edge_payload: dict[str, Any] | None,
    smoke_payload: dict[str, Any] | None,
    gate_args: str,
) -> dict[str, Any]:
    actions = _next_actions(
        blockers,
        edge_payload=edge_payload,
        smoke_payload=smoke_payload,
        gate_args=gate_args,
    )
    return {
        "schema": SCHEMA,
        "tool": TOOL_NAME,
        "generated_at": format_utc(evaluation_time),
        "reference": {
            "evaluation_time": format_utc(evaluation_time),
            "evidence_frontier": format_utc(frontier),
            "frontier_source": "max(generated_at of machine inputs)",
            "freshness_hours": freshness_hours,
            "clock_tolerance_seconds": int(CLOCK_TOLERANCE.total_seconds()),
        },
        "inputs": entries,
        "blockers": blockers,
        "next_actions": actions,
        "public_mobile_ready": ready,
        "release_authorization": {
            "human_release_approval": "not-asserted",
            "production_readiness": "not-asserted",
            "note": (
                "public mobile ready 仅表示本门消费的证据在同一证据前沿下"
                "全部满足；绝不构成人工发布批准或整体生产就绪"
            ),
        },
        "boundary": (
            "aggregation-only gate over machine-exported evidence and a "
            "constrained human attestation; re-runs no sub-tool probing"
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Public mobile release gate",
        "",
        f"- tool: `{report['tool']}` (schema `{report['schema']}`)",
        f"- evidence frontier: `{report['reference']['evidence_frontier']}`",
        f"- evaluated at: `{report['reference']['evaluation_time']}`",
        (
            f"- freshness: {report['reference']['freshness_hours']}h, "
            f"clock tolerance {report['reference']['clock_tolerance_seconds']}s"
        ),
        f"- **public_mobile_ready: {report['public_mobile_ready']}**",
        (
            "- human release approval: `not-asserted`; production readiness: "
            "`not-asserted` (separate ledgers)"
        ),
        "",
        "## Inputs",
        "",
        "| input | classification | present | file | sha256 | reasons |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for key, entry in report["inputs"].items():
        lines.append(
            f"| {key} | {entry['classification']} | {entry['present']} | "
            f"{entry.get('file') or '—'} | {entry.get('sha256') or '—'} | "
            f"{', '.join(entry['reasons']) or '—'} |"
        )
    lines.extend(["", "## Blockers", ""])
    if report["blockers"]:
        lines.extend(f"- `{blocker}`" for blocker in report["blockers"])
    else:
        lines.append("- none")
    lines.extend(["", "## Next actions", ""])
    if report["next_actions"]:
        for action in report["next_actions"]:
            lines.append(f"- for `{action['for']}`: `{action['command']}`")
            lines.append(f"  - {action['note']}")
    else:
        lines.append("- none (gate ready)")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 输出（原子写 + 重读校验）


def write_report(store: Store, output_dir: Path, report: dict[str, Any]) -> None:
    """原子写 JSON+MD 并重读校验；失败 → ``GateRefused``（不留部分输出）。"""
    if is_link_or_reparse(output_dir):
        raise GateRefused("reparse-output-dir")
    try:
        reject_symlinked_path(store, output_dir)
    except HistoryError:
        raise GateRefused("symlink-output-dir") from None
    store.mkdirs(output_dir)
    if is_link_or_reparse(output_dir):
        raise GateRefused("reparse-output-dir")
    json_text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
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
        raise GateRefused(f"output-write-failed:{cause.__class__.__name__}") from None
    # 写后重读校验（字节级）——失败视为写出失败并清理，不留半成品。
    for path, expected in ((json_path, json_text), (md_path, md_text)):
        try:
            actual = store.read_bytes(path).decode("utf-8")
        except OSError:
            _discard(written)
            raise GateRefused(f"output-verify-failed:{path.name}") from None
        if actual != expected:
            _discard(written)
            raise GateRefused(f"output-verify-failed:{path.name}")


def _discard(paths: list[Path]) -> None:
    """尽力移除失败写出的文件（尽力而为；失败不掩盖原始原因）。"""
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="public_mobile_release_gate",
        description=(
            "只读 fail-closed 聚合门：把 production restore preflight / "
            "public edge preflight / Android public device smoke / "
            "cloudflare ingress preflight（可选）/ provider-release evidence "
            "状态 / 受约束人工 attestation 合成为 public mobile ready 专门结论。"
        ),
    )
    parser.add_argument("--restore-preflight", required=True, type=Path)
    parser.add_argument("--edge-preflight", required=True, type=Path)
    parser.add_argument("--device-smoke", required=True, type=Path,
                        help=f"{SMOKE_TOOL} 输出目录下的 {SMOKE_REPORT_JSON}")
    parser.add_argument("--cloudflare-preflight", type=Path, default=None,
                        help="可选：cloudflare ingress preflight 已脱敏报告（本门绝不读凭据）")
    parser.add_argument("--release-evidence", required=True, type=Path)
    parser.add_argument("--attestation", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--freshness-hours", type=int, default=DEFAULT_FRESHNESS_HOURS,
                        help=f"证据新鲜度窗口小时（默认 {DEFAULT_FRESHNESS_HOURS}）")
    return parser


def validate_settings(freshness_hours: int) -> None:
    if not MIN_FRESHNESS_HOURS <= freshness_hours <= MAX_FRESHNESS_HOURS:
        raise GateRefused("freshness-out-of-range")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = RealStore()
    try:
        validate_settings(args.freshness_hours)
        report, exit_code = run_gate(
            store,
            restore=args.restore_preflight,
            edge=args.edge_preflight,
            smoke=args.device_smoke,
            cloudflare=args.cloudflare_preflight,
            release_evidence=args.release_evidence,
            attestation=args.attestation,
            output_dir=args.output,
            freshness_hours=args.freshness_hours,
            # CLI 评估时刻 = 真实当前 UTC（契约测试经 run_gate 注入固定值）。
            evaluation_time=datetime.now(timezone.utc),
        )
    except GateRefused as cause:
        print(f"{TAG} REFUSED: {cause}", file=sys.stderr)
        return EXIT_REFUSED
    marker = "READY" if exit_code == EXIT_READY else "BLOCKED"
    print(
        f"{TAG} {marker}: public_mobile_ready={report['public_mobile_ready']} "
        f"evaluated_at={report['reference']['evaluation_time']} "
        f"frontier={report['reference']['evidence_frontier']} "
        f"blockers={len(report['blockers'])}"
    )
    for blocker in report["blockers"]:
        print(f"{TAG} blocker: {blocker}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
