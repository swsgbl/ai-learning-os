r"""M14-135 tools/ops/production_drift_watch_alert_dispatch.py 契约测试：
既有 M14-127 execute 模式 drift-watch JSON 报告的最小 drift=true 告警
分发闭环（fail-closed、可审计、绝不重判漂移）。

覆盖（全部 I/O 经真实临时目录或注入 fake——**构造上零真实网络**：
出站 HTTP 仅经注入 FakeTransport（monkeypatch 模块级 RealTransport 缝），
真实 Transport 在测试中绝不构造；socket+subprocess 双阻断下 plan 端到端
照常成功）：

- 结构契约：源码 import 白名单（纯标准库 + 同仓监控模块；零子进程/
  零 Docker 采集命令 token）、M14-119 安全面**同一实现对象复用**
  （validate_webhook_url/load_webhook_secret/RealTransport/RealStore/
  RealClock/write_report_files/ensure_output_dir_safe 逐一 is 同一）、
  plan 模式零 Transport 构造（计数工厂）、双阻断零网络、ops README
  文档化；
- 门禁 fail-closed：默认 plan（零网络零分发）、--execute 缺确认短语/
  近似短语（含 M14-119 的短语）拒绝且零 Transport 构造、execute 缺
  secret 拒绝且零 Transport 构造、超时界校验；
- 报告校验（malformed 一律固定词汇拒绝、零分发）：缺失/stem 非法
  （plan-*.json、伴生 .md、坏时间戳形态）、not-json、非对象、身份
  （schema_version/tool/milestone）、**plan 模式报告恒拒绝（无权威
  drift 结论，绝不猜测）**、时间戳形态、ended<started、文件名时间戳
  交叉校验（>2s 拒 / 恰 +2s 收 / 先于 stamp 拒）、config project/锚点、
  collectors、checks 字段、counts↔checks 不自洽、drift↔fail 不自洽、
  drift_reasons 固定词汇（封闭集合逐项 + 越域前缀/服务/未知串拒）、
  非空⟺drift、条数硬顶；
- SSRF/unsafe URL（M14-119 单一实现复用的抽样回归）与 secret 文件
  拒绝（缺失/invalid——值绝不回显）；
- 成功路径：drift=true → 恰一次 POST、payload 版本化+固定词汇+有界、
  台账恰一行 sent、exit 0；**drift=false → skipped-no-alerts、零
  Transport 调用、零台账、exit 0**；reasons 超 payload 界截断计数显式；
  回环 http test-only 旗标端到端；
- HTTP 失败：500/503/404/302/timeout/connection refused → failed、
  ledger 零追加、exit 2（失败绝不报告为 sent）；
- 幂等/ledger：同 report sha256 二次 execute → duplicate-dispatch 拒绝
  且零 Transport 调用（发送之前）；ledger malformed 行/字段非法/
  **他工具台账行** → fail-closed 拒绝；不同报告正常追加；**成功分发后
  台账写入失败 → sent-ledger-unrecorded 可见、exit 2、绝不谎报 sent**；
- 台账行**全字段严格 schema**（R1 修正）：canonical 行接受 + 成功路径
  写入行与校验器读写同构（防 schema 漂移）；多余键/缺失键/各关键族
  越类型（身份/状态/时间戳/指纹/counts/http/payload/drift/reasons/
  stem/dispatch_id）与固定词汇越域值逐项参数化拒绝；多余键与类型
  违规端到端拒绝且零 Transport 调用；
- 原子写/输出路径：报告文件名防碰撞（绝不覆盖既有文件）、输出 symlink
  拒绝、报告文件 symlink 拒绝；
- 脱敏：报告投毒 detail 携带 marker token 绝不进入 payload/ledger/
  dispatch 报告/stdout；secret url 与 token 绝不出现在任何输出面；
  payload/台账行键集精确（固定词汇）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess as subprocess_module
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "production_drift_watch_alert_dispatch.py"
OPS_README = REPO_ROOT / "tools" / "ops" / "README.md"

#: 投毒 marker（注入报告 detail / secret，断言绝不进入任何输出面）
MARK_TOKEN = "sk-ZXmarker0123456789"
SECRET_URL = "https://hooks.example.invalid/T000/B000/xyz"
SECRET_TOKEN = "tk-secret-value-0123456789"

#: 源报告身份（与 M14-127 输出一致；文件名 stamp 与 started 同秒）
STEM = "drift-watch-20260924-020000"
STARTED = "2026-09-24T02:00:00Z"
ENDED = "2026-09-24T02:00:01Z"
PROJECT = "aios-m14-03-production-rehearsal"
API_DIGEST = ("sha256:c99e28c905208bffbc1576f0c5c9e042af18fd356881cee9"
              "67078ee38323781f")
WEB_DIGEST = ("sha256:d596f0c726ab690359b196a3c3911f9842b94218b4361ee4"
              "52413d420a38194b")


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "production_drift_watch_alert_dispatch", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


pdwad = _load_module()


# ---------------------------------------------------------------- fixtures


def make_check(status: str = "pass", check_id: str = "compose-service-present",
               subject: str = "api", detail: str = "present") -> dict:
    return {"check_id": check_id, "subject": subject, "status": status,
            "detail": detail}


def make_report(*, drift: bool = True, reasons: list[str] | None = None,
                checks: list[dict] | None = None, counts: dict | None = None,
                started: str = STARTED, ended: str = ENDED, mode: str = "execute",
                schema_version: int = 1, tool: str = "tools/ops/production_drift_watch.py",
                milestone: str = "M14-127", project: str = PROJECT,
                drop: tuple[str, ...] = ()) -> dict:
    if checks is None:
        if drift:
            checks = [make_check(), make_check(subject="web"),
                      make_check(status="fail", check_id="container-health",
                                 subject="api", detail="state=running health=unhealthy")]
        else:
            checks = [make_check(), make_check(subject="web"),
                      make_check(check_id="container-health")]
    if counts is None:
        counts = {"pass": sum(1 for c in checks if c["status"] == "pass"),
                  "fail": sum(1 for c in checks if c["status"] == "fail")}
    if reasons is None:
        reasons = ["container-not-healthy:api"] if drift else []
    report = {
        "schema_version": schema_version,
        "tool": tool,
        "milestone": milestone,
        "mode": mode,
        "started_at_utc": started,
        "ended_at_utc": ended,
        "config": {
            "project": project,
            "profiles": ["local", "search"],
            "compose_file": "docker-compose.yml",
            "services": ["postgres", "redis", "minio", "api", "web", "livekit", "searxng"],
            "anchors": {
                "api": {"expected_tag": "aios/api:m14-124-production",
                        "expected_digest": API_DIGEST},
                "web": {"expected_tag": "aios/web:m14-124-production",
                        "expected_digest": WEB_DIGEST},
            },
        },
        "boundaries": ["read-only collection only"],
        "collectors": {"compose_ps": {"status": "ok"}, "containers": {"per_service": {}},
                       "image_refs": {}},
        "checks": checks,
        "counts": counts,
        "drift": drift,
        "drift_reasons": reasons,
    }
    for key in drop:
        report.pop(key, None)
    return report


def write_report(tmp_path: Path, report: dict, name: str = f"{STEM}.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return path


def write_secret(tmp_path: Path, payload: dict, name: str = "secret.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class FakeTransport:
    """注入 Transport：记录调用；可注入 HTTP 状态或异常。"""

    def __init__(self, *, status: int = 200, error: Exception | None = None) -> None:
        self.status = status
        self.error = error
        self.calls: list[dict] = []

    def post_json(self, host: str, port: int, path: str, *, body: bytes,
                  headers: dict[str, str], timeout: float):
        self.calls.append({"host": host, "port": port, "path": path,
                           "body": body, "headers": dict(headers),
                           "timeout": timeout})
        if self.error is not None:
            raise self.error
        return SimpleNamespace(status=self.status, error_category=None,
                               error_class=None)


class FakeClock:
    def __init__(self) -> None:
        self.n = 0

    def utc_now_iso(self) -> str:
        return "2026-09-24T03:00:00Z"

    def stamp(self) -> str:
        return "20260924-030000"

    def token_hex(self, nibbles: int) -> str:
        self.n += 1
        return f"{self.n:032x}"[:nibbles * 2]


def _patch(monkeypatch: pytest.MonkeyPatch, *, transport=None, clock=None):
    if transport is not None:
        monkeypatch.setattr(pdwad, "RealTransport", lambda scheme: transport)
    if clock is not None:
        monkeypatch.setattr(pdwad, "RealClock", lambda: clock)


def _execute(tmp_path, monkeypatch, report_path, secret_path, transport,
             *extra: str, allow_loopback: bool = False) -> int:
    _patch(monkeypatch, transport=transport, clock=FakeClock())
    argv = ["--report", str(report_path), "--secret-file", str(secret_path),
            "--execute", "--confirm", pdwad.CONFIRM_PHRASE,
            "--timeout-seconds", "5",
            "--artifact-dir", str(tmp_path / "out")]
    if allow_loopback:
        argv.append("--allow-loopback-http")
    argv.extend(extra)
    return pdwad.main(argv)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _dispatch_reports(directory: Path) -> list[Path]:
    return sorted(directory.glob("dispatch-*.json"))


def _ledger_rows(directory: Path) -> list[dict]:
    ledger = directory / pdwad.LEDGER_NAME
    if not ledger.exists():
        return []
    return [json.loads(line) for line in
            ledger.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------- 结构契约


def test_source_import_whitelist() -> None:
    """纯标准库 + 同仓监控模块；禁子进程/第三方 HTTP/env 值读取/Docker 采集命令。"""
    source = SCRIPT.read_text(encoding="utf-8")
    for banned in ("import subprocess", "from subprocess", "subprocess.",
                   "import requests", "import urllib.request",
                   "from urllib.request", "urlopen", "import httpx",
                   "import docker", "os.environ[", "os.getenv",
                   "os.environ.get"):
        assert banned not in source, f"banned token in source: {banned}"
    # 本工具绝不重判 Docker 状态：三种 M14-127 采集命令形态绝不在源码中
    for command in ("docker compose", "docker inspect", "docker image"):
        assert command not in source, f"collection command in source: {command}"
    assert "import monitoring_alert_dispatch" in source


def test_constants_single_source() -> None:
    """身份/边界常量与 M14-127/M14-133 单一事实源同值。"""
    assert pdwad.CONFIRM_PHRASE == "EXECUTE PRODUCTION DRIFT WATCH ALERT DISPATCH"
    assert pdwad.MILESTONE == "M14-135"
    assert pdwad.TOOL_NAME == "tools/ops/production_drift_watch_alert_dispatch.py"
    assert pdwad.SOURCE_TOOL_NAME == "tools/ops/production_drift_watch.py"
    assert pdwad.SOURCE_MILESTONE == "M14-127"
    assert pdwad.SOURCE_SCHEMA_VERSION == 1
    assert pdwad.LEDGER_NAME == "drift-dispatch-ledger.jsonl"
    assert pdwad.SINK_KIND == "generic-https-webhook"
    assert pdwad.PAYLOAD_EVENT == "drift-watch-alert"
    assert pdwad.SOURCE_FILE_NAME_RE.pattern.startswith("^drift-watch-")


def test_m14_119_safety_surface_reused_verbatim() -> None:
    """M14-119 已实证安全面 = 同一实现对象复用（绝不平行第二策略）。"""
    m119 = pdwad._dispatch
    assert m119.__name__ == "monitoring_alert_dispatch"
    assert pdwad.validate_webhook_url is m119.validate_webhook_url
    assert pdwad.load_webhook_secret is m119.load_webhook_secret
    assert pdwad.RealTransport is m119.RealTransport
    assert pdwad.RealStore is m119.RealStore
    assert pdwad.RealClock is m119.RealClock
    assert pdwad.DispatchResult is m119.DispatchResult
    assert pdwad.write_report_files is m119.write_report_files
    assert pdwad.ensure_output_dir_safe is m119.ensure_output_dir_safe


def test_plan_mode_never_constructs_transport(tmp_path, monkeypatch) -> None:
    """plan（默认）零 Transport 构造（计数工厂结构性证明）。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL, "token": SECRET_TOKEN})
    counter = {"n": 0}

    def factory(scheme):
        counter["n"] += 1
        raise AssertionError("plan mode must not construct RealTransport")

    monkeypatch.setattr(pdwad, "RealTransport", factory)
    rc = pdwad.main(["--report", str(report), "--secret-file", str(secret),
                     "--artifact-dir", str(tmp_path / "out")])
    assert rc == 0
    assert counter["n"] == 0


def test_plan_end_to_end_with_socket_and_subprocess_blocked(
        tmp_path, monkeypatch) -> None:
    """socket+subprocess 双阻断下 plan 照常成功（零网络构造证明）。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL, "token": SECRET_TOKEN})

    def _blocked(*args, **kwargs):
        raise AssertionError("network/subprocess blocked")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(subprocess_module, "Popen", _blocked)
    monkeypatch.setattr(subprocess_module, "run", _blocked)
    rc = pdwad.main(["--report", str(report), "--secret-file", str(secret),
                     "--artifact-dir", str(tmp_path / "out")])
    assert rc == 0
    assert (tmp_path / "out" / pdwad.LEDGER_NAME).exists() is False


def test_ops_readme_documents_tool() -> None:
    text = OPS_README.read_text(encoding="utf-8")
    assert "production_drift_watch_alert_dispatch.py" in text
    assert "M14-135" in text


# ---------------------------------------------------------------- 门禁


def test_execute_requires_exact_confirm_phrase(tmp_path, monkeypatch) -> None:
    """--execute 缺短语/近似短语（含 M14-119 短语）→ 拒绝且零 Transport 构造。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    counter = {"n": 0}

    def factory(scheme):
        counter["n"] += 1
        raise AssertionError("must not construct transport without confirm")

    monkeypatch.setattr(pdwad, "RealTransport", factory)
    for phrase in ("", "EXECUTE PRODUCTION DRIFT WATCH ALERT DISPATCH ",
                   "execute production drift watch alert dispatch",
                   "EXECUTE MONITOR ALERT DISPATCH",
                   "EXECUTE READ-ONLY PRODUCTION DRIFT WATCH"):
        rc = pdwad.main(["--report", str(report), "--secret-file", str(secret),
                         "--execute", "--confirm", phrase,
                         "--artifact-dir", str(tmp_path / "out")])
        assert rc == 2, phrase
    assert counter["n"] == 0


def test_execute_requires_secret_file(tmp_path, monkeypatch) -> None:
    """execute 缺 --secret-file → 拒绝且零 Transport 构造。"""
    report = write_report(tmp_path, make_report())
    counter = {"n": 0}

    def factory(scheme):
        counter["n"] += 1
        raise AssertionError("must not construct transport without secret")

    monkeypatch.setattr(pdwad, "RealTransport", factory)
    rc = pdwad.main(["--report", str(report),
                     "--execute", "--confirm", pdwad.CONFIRM_PHRASE,
                     "--artifact-dir", str(tmp_path / "out")])
    assert rc == 2
    assert counter["n"] == 0


def test_timeout_bounds_validated_in_plan(tmp_path) -> None:
    report = write_report(tmp_path, make_report())
    for bad in ("0", "0.1", "31", "nan", "inf"):
        rc = pdwad.main(["--report", str(report),
                         "--timeout-seconds", bad,
                         "--artifact-dir", str(tmp_path / "out")])
        assert rc == 2, bad


# ---------------------------------------------------------------- 报告校验（malformed）


def test_missing_report_refused(tmp_path) -> None:
    rc = pdwad.main(["--report", str(tmp_path / "nope.json"),
                     "--artifact-dir", str(tmp_path / "out")])
    assert rc == 2


@pytest.mark.parametrize("name", ["plan-20260924-020000.json", "drift-watch-bad.json",
                                  "drift-watch-20260924-02.json", "notes.txt",
                                  "drift-watch-20260924-020000.md"])
def test_bad_report_stem_refused(tmp_path, name) -> None:
    (tmp_path / name).write_text("{}", encoding="utf-8")
    rc = pdwad.main(["--report", str(tmp_path / name),
                     "--artifact-dir", str(tmp_path / "out")])
    assert rc == 2


def test_report_not_json_refused(tmp_path) -> None:
    path = tmp_path / f"{STEM}.json"
    path.write_text("not json", encoding="utf-8")
    assert pdwad.main(["--report", str(path),
                       "--artifact-dir", str(tmp_path / "out")]) == 2


@pytest.mark.parametrize("kwargs", [
    {"schema_version": 2},
    {"tool": "tools/ops/other.py"},
    {"milestone": "M14-99"},
    {"started": "2026-09-24 02:00:00"},
    {"started": "garbage"},
    {"ended": "2026-09-24T01:59:59Z"},  # ended < started
])
def test_report_identity_or_timestamp_refused(tmp_path, kwargs) -> None:
    report = write_report(tmp_path, make_report(**kwargs))
    rc = pdwad.main(["--report", str(report),
                     "--artifact-dir", str(tmp_path / "out")])
    assert rc == 2


def test_plan_mode_source_report_refused_never_guessed(
        tmp_path, monkeypatch, capsys) -> None:
    """plan 模式源报告无权威 drift 结论——plan/execute 一律拒绝，绝不猜测。"""
    report = write_report(tmp_path, make_report(mode="plan", drift=False,
                                                drop=("checks", "counts", "drift",
                                                      "drift_reasons")))
    counter = {"n": 0}

    def factory(scheme):
        counter["n"] += 1
        raise AssertionError("plan-mode source report must never dispatch")

    monkeypatch.setattr(pdwad, "RealTransport", factory)
    for argv in (["--report", str(report), "--artifact-dir", str(tmp_path / "out")],
                 ["--report", str(report), "--secret-file",
                  str(write_secret(tmp_path, {"url": SECRET_URL})),
                  "--execute", "--confirm", pdwad.CONFIRM_PHRASE,
                  "--artifact-dir", str(tmp_path / "out")]):
        assert pdwad.main(argv) == 2
    assert counter["n"] == 0
    out = capsys.readouterr().out
    assert "权威" in out  # 明示「无权威 drift 结论」而非泛化拒绝


@pytest.mark.parametrize("started", ["2026-09-24T02:00:05Z",   # 滞后 stamp >2s
                                     "2026-09-23T01:59:58Z"])  # 先于 stamp
def test_filename_timestamp_mismatch_refused(tmp_path, started) -> None:
    report = write_report(tmp_path, make_report(started=started, ended=started))
    assert pdwad.main(["--report", str(report),
                       "--artifact-dir", str(tmp_path / "out")]) == 2


def test_filename_timestamp_plus_two_seconds_accepted(tmp_path) -> None:
    """恰 +2s 容差边界（匹配 M14-127 先取 stamp 后取 started 实现序）→ 放行。"""
    report = write_report(tmp_path, make_report(started="2026-09-24T02:00:02Z",
                                                ended="2026-09-24T02:00:03Z"))
    assert pdwad.main(["--report", str(report),
                       "--artifact-dir", str(tmp_path / "out")]) == 0


@pytest.mark.parametrize("project", ["", "../etc", "bad project!", "x" * 65])
def test_report_project_invalid_refused(tmp_path, project) -> None:
    report = write_report(tmp_path, make_report(project=project))
    assert pdwad.main(["--report", str(report),
                       "--artifact-dir", str(tmp_path / "out")]) == 2


def test_report_anchors_invalid_refused(tmp_path) -> None:
    for mutate in ("del-api", "del-web", "bad-digest", "no-tag"):
        data = make_report()
        if mutate == "del-api":
            del data["config"]["anchors"]["api"]
        elif mutate == "del-web":
            del data["config"]["anchors"]["web"]
        elif mutate == "bad-digest":
            data["config"]["anchors"]["api"]["expected_digest"] = "notsha256"
        else:
            data["config"]["anchors"]["web"]["expected_tag"] = ""
        report = write_report(tmp_path, data)
        assert pdwad.main(["--report", str(report),
                           "--artifact-dir", str(tmp_path / "out")]) == 2, mutate


def test_report_collectors_missing_refused(tmp_path) -> None:
    report = write_report(tmp_path, make_report(drop=("collectors",)))
    assert pdwad.main(["--report", str(report),
                       "--artifact-dir", str(tmp_path / "out")]) == 2


def test_report_check_field_invalid_refused(tmp_path) -> None:
    for bad in ({"status": "bogus"}, {"check_id": ""}, {"subject": ""},
                {"check_id": "x" * 200}, {"status": None}):
        checks = [make_check(), make_check(**bad)]
        data = make_report(checks=checks, counts={"pass": 2, "fail": 0},
                           reasons=[])
        report = write_report(tmp_path, data)
        rc = pdwad.main(["--report", str(report),
                         "--artifact-dir", str(tmp_path / "out")])
        assert rc == 2, bad


def test_report_counts_checks_mismatch_refused(tmp_path) -> None:
    data = make_report(counts={"pass": 9, "fail": 0})  # 实际 2 pass 1 fail
    report = write_report(tmp_path, data)
    assert pdwad.main(["--report", str(report),
                       "--artifact-dir", str(tmp_path / "out")]) == 2


def test_report_drift_mismatch_refused(tmp_path) -> None:
    # drift=true 但零失败检查
    data = make_report(drift=True, checks=[make_check(), make_check(subject="web"),
                                           make_check(check_id="container-health")],
                       counts={"pass": 3, "fail": 0}, reasons=["compose-service-missing:api"])
    report = write_report(tmp_path, data)
    assert pdwad.main(["--report", str(report),
                       "--artifact-dir", str(tmp_path / "out")]) == 2
    # drift=false 但存在失败检查
    data = make_report(drift=False,
                       checks=[make_check(), make_check(status="fail",
                                                        check_id="container-health")],
                       counts={"pass": 1, "fail": 1}, reasons=[])
    report = write_report(tmp_path, data)
    assert pdwad.main(["--report", str(report),
                       "--artifact-dir", str(tmp_path / "out")]) == 2


def test_drift_reason_vocabulary_closed_set() -> None:
    """固定词汇封闭集合：11 前缀 × 服务域逐项放行；越域/未知逐项拒绝。"""
    services = ("postgres", "redis", "minio", "api", "web", "livekit", "searxng")
    any_prefixes = ("compose-service-missing", "compose-service-unhealthy",
                    "container-facts-missing", "container-not-healthy")
    anchor_prefixes = ("anchor-facts-missing", "image-tag-mismatch",
                       "image-digest-unobtainable", "image-digest-mismatch",
                       "image-tag-resolution-unavailable",
                       "image-tag-resolution-mismatch")
    assert pdwad.DRIFT_REASON_RE.match("collector-failed:compose-ps") is not None
    for prefix in any_prefixes:
        for service in services:
            assert pdwad.DRIFT_REASON_RE.match(f"{prefix}:{service}") is not None
    for prefix in anchor_prefixes:
        for service in ("api", "web"):
            assert pdwad.DRIFT_REASON_RE.match(f"{prefix}:{service}") is not None
    # 越域：锚点前缀配非锚点服务
    for prefix in anchor_prefixes:
        assert pdwad.DRIFT_REASON_RE.match(f"{prefix}:postgres") is None
    # 未知前缀/未知服务/空白变形
    for bad in ("bogus-reason:api", "container-not-healthy:etcd",
                "collector-failed:compose-ps ", " collector-failed:compose-ps",
                "image-digest-mismatch:api:extra", "",
                "container-not-healthy:API"):
        assert pdwad.DRIFT_REASON_RE.match(bad) is None, bad


@pytest.mark.parametrize("reason", ["bogus-reason:api", "container-not-healthy:etcd",
                                    "image-tag-mismatch:postgres",
                                    "collector-failed:compose-ps "])
def test_report_reason_vocabulary_refused(tmp_path, reason) -> None:
    data = make_report(reasons=[reason])
    report = write_report(tmp_path, data)
    assert pdwad.main(["--report", str(report),
                       "--artifact-dir", str(tmp_path / "out")]) == 2


def test_report_reason_presence_mismatch_refused(tmp_path) -> None:
    # drift=true 但 reasons 空
    data = make_report(reasons=[])
    report = write_report(tmp_path, data)
    assert pdwad.main(["--report", str(report),
                       "--artifact-dir", str(tmp_path / "out")]) == 2
    # drift=false 但 reasons 非空
    data = make_report(drift=False,
                       checks=[make_check(), make_check(check_id="container-health")],
                       counts={"pass": 2, "fail": 0},
                       reasons=["compose-service-missing:api"])
    report = write_report(tmp_path, data)
    assert pdwad.main(["--report", str(report),
                       "--artifact-dir", str(tmp_path / "out")]) == 2


def test_report_reasons_input_hard_cap_refused(tmp_path) -> None:
    """drift_reasons 超输入硬顶 → 报告不可信，拒绝（绝不静默截断语义）。"""
    data = make_report(reasons=["compose-service-unhealthy:api"]
                       * (pdwad.MAX_DRIFT_REASONS_INPUT + 1))
    report = write_report(tmp_path, data)
    assert pdwad.main(["--report", str(report),
                       "--artifact-dir", str(tmp_path / "out")]) == 2


# ---------------------------------------------------------------- SSRF / secret（M14-119 复用面抽样）


@pytest.mark.parametrize("url", [
    "http://example.invalid/hook",
    "https://user:pw@example.invalid/hook",
    "https://example.invalid/hook?a=1",
    "https://localhost/hook",
    "https://127.0.0.1:9000/hook",
    "https://10.0.0.5/hook",
    "https://169.254.169.254/latest/meta-data",
    "https://[::1]:9000/hook",
    "https:///hook",
])
def test_unsafe_urls_rejected(url) -> None:
    assert pdwad.validate_webhook_url(url) is not None, url


def test_loopback_http_only_with_explicit_flag() -> None:
    assert pdwad.validate_webhook_url("http://127.0.0.1:9000/hook",
                                      allow_loopback_http=True) is None
    assert pdwad.validate_webhook_url("http://localhost:9000/hook",
                                      allow_loopback_http=True) is not None
    assert pdwad.validate_webhook_url("http://10.0.0.5:9000/hook",
                                      allow_loopback_http=True) is not None


def test_public_https_url_accepted() -> None:
    assert pdwad.validate_webhook_url(SECRET_URL) is None


def test_execute_rejects_unsafe_secret_url(tmp_path, monkeypatch) -> None:
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": "http://169.254.169.254/latest/meta-data"})
    counter = {"n": 0}

    def factory(scheme):
        counter["n"] += 1
        raise AssertionError("unsafe url must fail before transport")

    monkeypatch.setattr(pdwad, "RealTransport", factory)
    rc = pdwad.main(["--report", str(report), "--secret-file", str(secret),
                     "--execute", "--confirm", pdwad.CONFIRM_PHRASE,
                     "--artifact-dir", str(tmp_path / "out")])
    assert rc == 2
    assert counter["n"] == 0


def test_secret_file_missing_refused(tmp_path, monkeypatch) -> None:
    report = write_report(tmp_path, make_report())
    counter = {"n": 0}

    def factory(scheme):
        counter["n"] += 1
        raise AssertionError("missing secret must fail before transport")

    monkeypatch.setattr(pdwad, "RealTransport", factory)
    rc = pdwad.main(["--report", str(report), "--secret-file",
                     str(tmp_path / "absent.json"),
                     "--execute", "--confirm", pdwad.CONFIRM_PHRASE,
                     "--artifact-dir", str(tmp_path / "out")])
    assert rc == 2
    assert counter["n"] == 0


def test_secret_file_invalid_refused(tmp_path, monkeypatch) -> None:
    report = write_report(tmp_path, make_report())
    bad_payloads = [
        "not json",
        json.dumps(["not", "object"]),
        json.dumps({"token": "x"}),
        json.dumps({"url": "https://ok.example/hook", "extra": 1}),
        json.dumps({"url": ""}),
        json.dumps({"url": "https://ok.example/hook", "token": ""}),
    ]
    counter = {"n": 0}

    def factory(scheme):
        counter["n"] += 1
        raise AssertionError("bad secret must fail before transport")

    monkeypatch.setattr(pdwad, "RealTransport", factory)
    for payload in bad_payloads:
        secret = tmp_path / "sec.json"
        secret.write_text(payload, encoding="utf-8")
        rc = pdwad.main(["--report", str(report), "--secret-file", str(secret),
                         "--execute", "--confirm", pdwad.CONFIRM_PHRASE,
                         "--artifact-dir", str(tmp_path / "out")])
        assert rc == 2, payload[:40]
    assert counter["n"] == 0


# ---------------------------------------------------------------- 成功路径


def test_execute_dispatch_success(tmp_path, monkeypatch) -> None:
    report = write_report(tmp_path, make_report())  # drift=true + 1 reason
    secret = write_secret(tmp_path, {"url": SECRET_URL, "token": SECRET_TOKEN})
    transport = FakeTransport(status=200)
    rc = _execute(tmp_path, monkeypatch, report, secret, transport)
    assert rc == 0

    # 恰一次 POST；Authorization 携带 secret token（只在请求头，绝无输出面）
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["host"] == "hooks.example.invalid"
    assert call["port"] == 443
    assert call["path"] == "/T000/B000/xyz"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["headers"]["Authorization"] == f"Bearer {SECRET_TOKEN}"
    assert call["headers"]["User-Agent"] == pdwad.USER_AGENT
    assert call["timeout"] == 5.0

    # payload：版本化 + 固定词汇 + 有界
    payload = json.loads(call["body"].decode("utf-8"))
    assert payload["schema_version"] == 1
    assert payload["tool"] == pdwad.TOOL_NAME
    assert payload["milestone"] == "M14-135"
    assert payload["event"] == "drift-watch-alert"
    assert payload["report"]["stem"] == STEM
    assert payload["report"]["started_at_utc"] == STARTED
    assert payload["report"]["project"] == PROJECT
    assert payload["report"]["drift"] is True
    assert payload["report"]["counts"] == {"pass": 2, "fail": 1}
    assert payload["report"]["drift_reasons"] == ["container-not-healthy:api"]
    assert payload["report"]["drift_reasons_truncated_count"] == 0
    assert len(call["body"]) <= pdwad.MAX_PAYLOAD_BYTES

    # 台账恰一行 sent 记录
    rows = _ledger_rows(tmp_path / "out")
    assert len(rows) == 1
    assert rows[0]["dispatch_status"] == "sent"
    assert rows[0]["drift"] is True
    assert rows[0]["sink"] == "generic-https-webhook"
    assert rows[0]["http_status"] == 200

    # dispatch 报告 sent + 不含 URL/token
    reports = _dispatch_reports(tmp_path / "out")
    assert len(reports) == 1
    record = _read_json(reports[0])
    assert record["dispatch_status"] == "sent"
    assert record["dispatch_decision"] == "dispatch"
    assert SECRET_URL not in reports[0].read_text(encoding="utf-8")
    assert SECRET_TOKEN not in reports[0].read_text(encoding="utf-8")


def test_no_drift_report_skips_dispatch(tmp_path, monkeypatch) -> None:
    """合法 execute 报告 drift=false → skipped-no-alerts、零网络、零台账、exit 0。"""
    report = write_report(tmp_path, make_report(drift=False))
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    transport = FakeTransport(status=200)
    rc = _execute(tmp_path, monkeypatch, report, secret, transport)
    assert rc == 0
    assert transport.calls == []
    assert _ledger_rows(tmp_path / "out") == []
    reports = _dispatch_reports(tmp_path / "out")
    assert len(reports) == 1
    record = _read_json(reports[0])
    assert record["dispatch_status"] == "skipped-no-alerts"
    assert record["dispatch_decision"] == "skip"
    assert record["ledger"] == {"status": "not-applicable", "entries": None}


def test_reasons_bounded_and_truncation_counted(tmp_path, monkeypatch) -> None:
    """reasons 超 payload 界 → 截断到固定界且截断计数显式（绝不静默丢弃语义）。"""
    reasons = ["compose-service-unhealthy:api"] * (pdwad.MAX_DRIFT_REASONS + 8)
    report = write_report(tmp_path, make_report(reasons=reasons))
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    transport = FakeTransport(status=200)
    rc = _execute(tmp_path, monkeypatch, report, secret, transport)
    assert rc == 0
    payload = json.loads(transport.calls[0]["body"].decode("utf-8"))
    assert len(payload["report"]["drift_reasons"]) == pdwad.MAX_DRIFT_REASONS
    assert payload["report"]["drift_reasons_truncated_count"] == 8
    row = _ledger_rows(tmp_path / "out")[0]
    assert row["drift_reasons_count"] == pdwad.MAX_DRIFT_REASONS + 8


def test_loopback_http_test_flag_end_to_end(tmp_path, monkeypatch) -> None:
    """显式 --allow-loopback-http + 字面回环 IP → http 端到端放行（test-only）。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": "http://127.0.0.1:9099/hook"})
    transport = FakeTransport(status=200)
    rc = _execute(tmp_path, monkeypatch, report, secret, transport,
                  allow_loopback=True)
    assert rc == 0
    assert transport.calls[0]["port"] == 9099


# ---------------------------------------------------------------- HTTP 失败


@pytest.mark.parametrize("status", [500, 503, 404, 302, 301])
def test_non_2xx_dispatch_failed(tmp_path, monkeypatch, status) -> None:
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    transport = FakeTransport(status=status)
    rc = _execute(tmp_path, monkeypatch, report, secret, transport)
    assert rc == 2
    assert len(transport.calls) == 1  # 单次发送、零重试
    assert _ledger_rows(tmp_path / "out") == []  # 失败绝不入 ledger
    reports = _dispatch_reports(tmp_path / "out")
    assert len(reports) == 1
    record = _read_json(reports[0])
    assert record["dispatch_status"] == "failed"
    assert record["http_status"] == status


@pytest.mark.parametrize("error", [
    TimeoutError("timed out"),
    ConnectionRefusedError("refused"),
    OSError("boom"),
])
def test_transport_exception_dispatch_failed(tmp_path, monkeypatch, error) -> None:
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    transport = FakeTransport(error=error)
    rc = _execute(tmp_path, monkeypatch, report, secret, transport)
    assert rc == 2
    assert _ledger_rows(tmp_path / "out") == []
    record = _read_json(_dispatch_reports(tmp_path / "out")[0])
    assert record["dispatch_status"] == "failed"
    assert record["http_status"] is None
    assert record["transport_error"]["category"]


# ---------------------------------------------------------------- 幂等 / ledger


def test_duplicate_dispatch_refused_before_sending(tmp_path, monkeypatch) -> None:
    """同 report sha256 已 sent → duplicate 拒绝且零 Transport 调用（发送之前）。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    first = FakeTransport(status=200)
    assert _execute(tmp_path, monkeypatch, report, secret, first) == 0
    assert len(first.calls) == 1

    second = FakeTransport(status=200)
    rc = _execute(tmp_path, monkeypatch, report, secret, second)
    assert rc == 2
    assert second.calls == []
    assert len(_ledger_rows(tmp_path / "out")) == 1  # ledger 未追加


def test_ledger_malformed_line_refused(tmp_path, monkeypatch) -> None:
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    assert _execute(tmp_path, monkeypatch, report, secret,
                    FakeTransport(status=200)) == 0
    ledger = tmp_path / "out" / pdwad.LEDGER_NAME
    text = ledger.read_text(encoding="utf-8")
    ledger.write_text(text + "not json\n", encoding="utf-8")

    again = FakeTransport(status=200)
    other = write_report(tmp_path, make_report(), name="drift-watch-20260924-020200.json")
    rc = _execute(tmp_path, monkeypatch, other, secret, again)
    assert rc == 2
    assert again.calls == []


def test_ledger_bad_field_refused(tmp_path, monkeypatch) -> None:
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    assert _execute(tmp_path, monkeypatch, report, secret,
                    FakeTransport(status=200)) == 0
    ledger = tmp_path / "out" / pdwad.LEDGER_NAME
    rows = _ledger_rows(tmp_path / "out")
    rows[0]["report_sha256"] = "zz-not-hex"
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                      encoding="utf-8")
    other = write_report(tmp_path, make_report(), name="drift-watch-20260924-020200.json")
    again = FakeTransport(status=200)
    rc = _execute(tmp_path, monkeypatch, other, secret, again)
    assert rc == 2
    assert again.calls == []


def test_ledger_foreign_tool_row_refused(tmp_path, monkeypatch) -> None:
    """M14-119 台账行混入本里程碑目录 → 归属校验拒绝（独立台账绝不混用）。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    assert _execute(tmp_path, monkeypatch, report, secret,
                    FakeTransport(status=200)) == 0
    ledger = tmp_path / "out" / pdwad.LEDGER_NAME
    rows = _ledger_rows(tmp_path / "out")
    rows[0]["tool"] = "tools/ops/monitoring_alert_dispatch.py"
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                      encoding="utf-8")
    other = write_report(tmp_path, make_report(), name="drift-watch-20260924-020200.json")
    again = FakeTransport(status=200)
    rc = _execute(tmp_path, monkeypatch, other, secret, again)
    assert rc == 2
    assert again.calls == []


def test_second_distinct_report_dispatches(tmp_path, monkeypatch) -> None:
    """不同报告（不同 sha256）在既有 ledger 上正常追加。"""
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    first = write_report(tmp_path, make_report())
    assert _execute(tmp_path, monkeypatch, first, secret,
                    FakeTransport(status=200)) == 0
    second = write_report(tmp_path,
                          make_report(started="2026-09-24T02:02:00Z",
                                      ended="2026-09-24T02:02:01Z"),
                          name="drift-watch-20260924-020200.json")
    assert _execute(tmp_path, monkeypatch, second, secret,
                    FakeTransport(status=200)) == 0
    rows = _ledger_rows(tmp_path / "out")
    assert len(rows) == 2
    assert rows[0]["report_sha256"] != rows[1]["report_sha256"]


def test_sent_ledger_unrecorded_visible(tmp_path, monkeypatch) -> None:
    """成功分发后台账写入失败 → sent-ledger-unrecorded 可见、exit 2、绝不谎报。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    transport = FakeTransport(status=200)
    monkeypatch.setattr(pdwad, "append_ledger",
                        lambda *args, **kwargs: "ledger-write-error")
    rc = _execute(tmp_path, monkeypatch, report, secret, transport)
    assert rc == 2
    assert len(transport.calls) == 1  # 发送确已发生（事实不可掩盖）
    assert _ledger_rows(tmp_path / "out") == []  # 台账确未落
    record = _read_json(_dispatch_reports(tmp_path / "out")[0])
    assert record["dispatch_status"] == "sent-ledger-unrecorded"
    assert record["ledger"] == {"status": "write-error", "entries": 0}


# ---------------------------------------------------------------- 台账行全字段严格 schema（R1 修正）


#: canonical 台账行：与成功路径写入行完全同构的精确形态（16 键）
CANONICAL_LEDGER_ROW: dict = {
    "schema_version": 1,
    "tool": "tools/ops/production_drift_watch_alert_dispatch.py",
    "milestone": "M14-135",
    "dispatch_id": "20260924-030000-00000001",
    "dispatched_at": "2026-09-24T03:00:00Z",
    "report_stem": "drift-watch-20260924-020000",
    "report_sha256": "a" * 64,
    "report_started_at_utc": "2026-09-24T02:00:00Z",
    "drift": True,
    "counts": {"pass": 2, "fail": 1},
    "drift_reasons_count": 1,
    "dispatch_status": "sent",
    "sink": "generic-https-webhook",
    "http_status": 200,
    "payload_sha256": "b" * 64,
    "payload_bytes": 512,
}


def _write_ledger_row(directory: Path, row: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / pdwad.LEDGER_NAME).write_text(
        json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n",
        encoding="utf-8")


def test_ledger_canonical_row_accepted(tmp_path) -> None:
    """canonical 行（与成功路径写入行精确同构）→ load_ledger 完整接受。"""
    _write_ledger_row(tmp_path, dict(CANONICAL_LEDGER_ROW))
    rows, problem = pdwad.load_ledger(pdwad.RealStore(), tmp_path)
    assert problem is None
    assert rows == [CANONICAL_LEDGER_ROW]


def test_written_row_matches_validator(tmp_path, monkeypatch) -> None:
    """成功路径写入行 = 校验器接受的精确形态（读写同构，防 schema 漂移）。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    assert _execute(tmp_path, monkeypatch, report, secret,
                    FakeTransport(status=200)) == 0
    row = _ledger_rows(tmp_path / "out")[0]
    assert pdwad._is_valid_ledger_row(row) is True
    assert set(row) == set(CANONICAL_LEDGER_ROW)


#: 逐族越界 mutation（多余/缺失键、身份、状态/固定词汇、时间戳形态与
#: 日历、指纹、counts、drift、reasons 计数、http、payload、stem、
#: dispatch_id——bool 冒充 int 的家庭逐项覆盖）
LEDGER_ROW_MUTATIONS: list[tuple[str, object]] = [
    ("extra-key", lambda r: r.update({"extra": 1})),
    ("missing-drift-key", lambda r: r.pop("drift")),
    ("missing-dispatch-id-key", lambda r: r.pop("dispatch_id")),
    ("schema-version-2", lambda r: r.update({"schema_version": 2})),
    ("schema-version-bool", lambda r: r.update({"schema_version": True})),
    ("tool-foreign", lambda r: r.update(
        {"tool": "tools/ops/monitoring_alert_dispatch.py"})),
    ("milestone-foreign", lambda r: r.update({"milestone": "M14-119"})),
    ("dispatch-status-failed", lambda r: r.update({"dispatch_status": "failed"})),
    ("sink-wrong-vocabulary", lambda r: r.update({"sink": "https-webhook"})),
    ("dispatched-at-bad-shape", lambda r: r.update(
        {"dispatched_at": "2026-09-24 03:00:00"})),
    ("dispatched-at-impossible-calendar", lambda r: r.update(
        {"dispatched_at": "2026-13-99T25:99:99Z"})),
    ("report-started-at-invalid", lambda r: r.update(
        {"report_started_at_utc": "not-a-time"})),
    ("report-sha-uppercase", lambda r: r.update(
        {"report_sha256": "A" * 64})),
    ("report-sha-short", lambda r: r.update({"report_sha256": "a" * 63})),
    ("payload-sha-nonhex", lambda r: r.update(
        {"payload_sha256": "g" + "0" * 63})),
    ("counts-missing-key", lambda r: r.update({"counts": {"pass": 2}})),
    ("counts-extra-key", lambda r: r.update(
        {"counts": {"pass": 2, "fail": 1, "extra": 0}})),
    ("counts-negative", lambda r: r.update({"counts": {"pass": -1, "fail": 1}})),
    ("counts-bool", lambda r: r.update({"counts": {"pass": True, "fail": 1}})),
    ("counts-string", lambda r: r.update({"counts": {"pass": "2", "fail": 1}})),
    ("counts-not-dict", lambda r: r.update({"counts": [2, 1]})),
    ("drift-string", lambda r: r.update({"drift": "true"})),
    ("drift-int", lambda r: r.update({"drift": 1})),
    ("reasons-count-negative", lambda r: r.update({"drift_reasons_count": -1})),
    ("reasons-count-bool", lambda r: r.update({"drift_reasons_count": True})),
    ("reasons-count-over-cap", lambda r: r.update(
        {"drift_reasons_count": pdwad.MAX_DRIFT_REASONS_INPUT + 1})),
    ("http-500", lambda r: r.update({"http_status": 500})),
    ("http-199", lambda r: r.update({"http_status": 199})),
    ("http-string", lambda r: r.update({"http_status": "200"})),
    ("http-bool", lambda r: r.update({"http_status": True})),
    ("payload-bytes-zero", lambda r: r.update({"payload_bytes": 0})),
    ("payload-bytes-negative", lambda r: r.update({"payload_bytes": -5})),
    ("payload-bytes-over-limit", lambda r: r.update(
        {"payload_bytes": pdwad.MAX_PAYLOAD_BYTES + 1})),
    ("payload-bytes-string", lambda r: r.update({"payload_bytes": "123"})),
    ("stem-wrong-shape", lambda r: r.update(
        {"report_stem": "monitor-20260924-020000"})),
    ("stem-unparseable-stamp", lambda r: r.update(
        {"report_stem": "drift-watch-99999999-999999"})),
    ("dispatch-id-malformed", lambda r: r.update({"dispatch_id": "not-an-id"})),
    ("dispatch-id-unparseable-stamp", lambda r: r.update(
        {"dispatch_id": "99999999-999999-00000001"})),
    ("dispatch-id-nonhex-suffix", lambda r: r.update(
        {"dispatch_id": "20260924-030000-0000000G"})),
    ("dispatch-id-extra-segment", lambda r: r.update(
        {"dispatch_id": "20260924-030000-00000001-extra"})),
]


@pytest.mark.parametrize("name,mutate", LEDGER_ROW_MUTATIONS,
                         ids=[name for name, _ in LEDGER_ROW_MUTATIONS])
def test_ledger_row_strict_schema_rejections(tmp_path, name, mutate) -> None:
    """台账行全字段严格 schema：任何越界 mutation → ledger-row-schema 拒绝。"""
    row = dict(CANONICAL_LEDGER_ROW)
    mutate(row)
    _write_ledger_row(tmp_path, row)
    rows, problem = pdwad.load_ledger(pdwad.RealStore(), tmp_path)
    assert problem == "ledger-row-schema", name
    assert rows is None


def test_ledger_extra_key_refused_end_to_end(tmp_path, monkeypatch) -> None:
    """端到端：多余键 → execute 拒绝且零 Transport 调用（发送之前）。"""
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    first = write_report(tmp_path, make_report())
    assert _execute(tmp_path, monkeypatch, first, secret,
                    FakeTransport(status=200)) == 0
    ledger = tmp_path / "out" / pdwad.LEDGER_NAME
    rows = _ledger_rows(tmp_path / "out")
    rows[0]["extra"] = 1
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                      encoding="utf-8")
    other = write_report(tmp_path, make_report(started="2026-09-24T02:02:00Z",
                                               ended="2026-09-24T02:02:01Z"),
                         name="drift-watch-20260924-020200.json")
    again = FakeTransport(status=200)
    rc = _execute(tmp_path, monkeypatch, other, secret, again)
    assert rc == 2
    assert again.calls == []


def test_ledger_wrong_type_refused_end_to_end(tmp_path, monkeypatch) -> None:
    """端到端：类型违规（http_status 字符串冒充）→ 拒绝且零 Transport 调用。"""
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    first = write_report(tmp_path, make_report())
    assert _execute(tmp_path, monkeypatch, first, secret,
                    FakeTransport(status=200)) == 0
    ledger = tmp_path / "out" / pdwad.LEDGER_NAME
    rows = _ledger_rows(tmp_path / "out")
    rows[0]["http_status"] = "200"
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                      encoding="utf-8")
    other = write_report(tmp_path, make_report(started="2026-09-24T02:02:00Z",
                                               ended="2026-09-24T02:02:01Z"),
                         name="drift-watch-20260924-020200.json")
    again = FakeTransport(status=200)
    rc = _execute(tmp_path, monkeypatch, other, secret, again)
    assert rc == 2
    assert again.calls == []


# ---------------------------------------------------------------- 输出路径 / 原子写


def test_dispatch_report_never_overwrites_existing(tmp_path, monkeypatch) -> None:
    """同名输出工件已存在 → fail-closed 换名，绝不覆盖既有证据。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    assert _execute(tmp_path, monkeypatch, report, secret,
                    FakeTransport(status=200)) == 0
    first_reports = _dispatch_reports(tmp_path / "out")
    existing = first_reports[0]
    marker = json.dumps({"sentinel": True})
    existing.write_text(marker, encoding="utf-8")

    other = write_report(tmp_path,
                         make_report(started="2026-09-24T02:02:00Z",
                                     ended="2026-09-24T02:02:01Z"),
                         name="drift-watch-20260924-020200.json")
    # 同一 FakeClock stamp：强制与首轮同名碰撞
    rc = _execute(tmp_path, monkeypatch, other, secret, FakeTransport(status=200))
    assert rc == 0
    assert existing.read_text(encoding="utf-8") == marker  # 既有文件逐字节未动
    assert len(_dispatch_reports(tmp_path / "out")) == 2


def test_output_symlink_refused(tmp_path, monkeypatch) -> None:
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    real_out = tmp_path / "real-out"
    real_out.mkdir()
    link = tmp_path / "linked-out"
    try:
        os.symlink(real_out, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")
    transport = FakeTransport(status=200)
    monkeypatch.setattr(pdwad, "RealTransport", lambda scheme: transport)
    monkeypatch.setattr(pdwad, "RealClock", lambda: FakeClock())
    rc = pdwad.main(["--report", str(report), "--secret-file", str(secret),
                     "--execute", "--confirm", pdwad.CONFIRM_PHRASE,
                     "--artifact-dir", str(link)])
    assert rc == 2
    assert transport.calls == []
    assert list(real_out.iterdir()) == []


def test_report_file_symlink_refused_real_fs(tmp_path) -> None:
    target = write_report(tmp_path, make_report())
    link = tmp_path / f"{STEM}.json"
    target.rename(tmp_path / "real.json")
    try:
        os.symlink(tmp_path / "real.json", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")
    assert pdwad.main(["--report", str(link),
                       "--artifact-dir", str(tmp_path / "out")]) == 2


# ---------------------------------------------------------------- 脱敏 / 输出卫生


def test_redaction_marker_never_leaks(tmp_path, monkeypatch, capsys) -> None:
    """报告投毒 detail 携带 secret 形态 marker → 绝不进入 payload/ledger/
    dispatch 报告/stdout。"""
    data = make_report(checks=[make_check(), make_check(
        status="fail", check_id="container-health", subject="api",
        detail=f"token={MARK_TOKEN} boom")])
    report = write_report(tmp_path, data)
    secret = write_secret(tmp_path, {"url": SECRET_URL, "token": SECRET_TOKEN})
    transport = FakeTransport(status=200)
    rc = _execute(tmp_path, monkeypatch, report, secret, transport)
    assert rc == 0
    capsys.readouterr()

    payload_text = transport.calls[0]["body"].decode("utf-8")
    assert MARK_TOKEN not in payload_text
    assert "detail" not in payload_text  # payload 无 detail 字段（固定词汇）
    assert "unhealthy" not in payload_text  # detail 自由文本绝不入 payload

    out_dir = tmp_path / "out"
    for path in out_dir.iterdir():
        text = path.read_text(encoding="utf-8")
        assert MARK_TOKEN not in text, path.name
        assert SECRET_TOKEN not in text, path.name
        assert SECRET_URL not in text, path.name

    for row in _ledger_rows(out_dir):
        assert MARK_TOKEN not in json.dumps(row)


def test_secret_values_never_echoed_on_stdout(tmp_path, monkeypatch, capsys) -> None:
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL, "token": SECRET_TOKEN})
    _execute(tmp_path, monkeypatch, report, secret, FakeTransport(status=200))
    out = capsys.readouterr().out
    assert SECRET_URL not in out
    assert SECRET_TOKEN not in out


def test_payload_fixed_vocabulary(tmp_path, monkeypatch) -> None:
    """payload 键集精确（无任何自由文本字段）。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    transport = FakeTransport(status=200)
    assert _execute(tmp_path, monkeypatch, report, secret, transport) == 0
    payload = json.loads(transport.calls[0]["body"].decode("utf-8"))
    assert set(payload) == {"schema_version", "tool", "milestone", "event",
                            "dispatched_at", "report"}
    assert set(payload["report"]) == {
        "stem", "sha256", "started_at_utc", "ended_at_utc", "project",
        "drift", "counts", "drift_reasons",
        "drift_reasons_truncated_count",
    }
    # 报告身份 hash 是 64 位 hex
    assert len(payload["report"]["sha256"]) == 64
    int(payload["report"]["sha256"], 16)


def test_ledger_row_fixed_vocabulary(tmp_path, monkeypatch) -> None:
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    transport = FakeTransport(status=200)
    assert _execute(tmp_path, monkeypatch, report, secret, transport) == 0
    row = _ledger_rows(tmp_path / "out")[0]
    assert set(row) == {
        "schema_version", "tool", "milestone", "dispatch_id", "dispatched_at",
        "report_stem", "report_sha256", "report_started_at_utc", "drift",
        "counts", "drift_reasons_count", "dispatch_status", "sink",
        "http_status", "payload_sha256", "payload_bytes",
    }
    assert row["dispatch_status"] == "sent"


def test_plan_writes_plan_report_and_no_ledger(tmp_path, monkeypatch) -> None:
    """plan 报告落盘（planned 状态 + 分发判定）且 ledger 零写入。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    _patch(monkeypatch, clock=FakeClock())
    rc = pdwad.main(["--report", str(report), "--secret-file", str(secret),
                     "--artifact-dir", str(tmp_path / "out")])
    assert rc == 0
    out_dir = tmp_path / "out"
    plans = sorted(out_dir.glob("plan-*.json"))
    assert len(plans) == 1
    record = _read_json(plans[0])
    assert record["mode"] == "plan"
    assert record["dispatch_status"] == "planned"
    assert record["dispatch_decision"] == "dispatch"
    assert record["ledger"] == {"status": "not-attempted", "entries": None}
    assert not (out_dir / pdwad.LEDGER_NAME).exists()


def test_plan_on_no_drift_report_decision_skip(tmp_path, monkeypatch) -> None:
    """plan 对 drift=false 报告的判定 = skip（不发不记，仅计划可见）。"""
    report = write_report(tmp_path, make_report(drift=False))
    _patch(monkeypatch, clock=FakeClock())
    rc = pdwad.main(["--report", str(report),
                     "--artifact-dir", str(tmp_path / "out")])
    assert rc == 0
    plans = sorted((tmp_path / "out").glob("plan-*.json"))
    assert len(plans) == 1
    record = _read_json(plans[0])
    assert record["dispatch_decision"] == "skip"
    assert record["dispatch_status"] == "planned"
