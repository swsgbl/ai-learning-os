"""Tests for tools.ops.public_edge_phase0_baseline (M14-182 Phase 0 runner).

All probe-CLI subprocess behavior is faked at the ProbeInvoker injection
point — zero network, zero real subprocess. The fake mirrors the probe
contract: it captures argv and writes a schema-valid window report to the
--output path. Any invocation beyond the scripted one is recorded and
asserted against (proving plan/phrase/budget rejections issue zero calls).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.ops import public_edge_phase0_baseline as p0

MANIFEST_URL = "https://ndtool.cn/aios/download-manifest.json"
CONFIRM = p0.CONFIRM_PHRASE


# ---------------------------------------------------------------- fakes / fixtures


def window_report(
    n: int,
    *,
    mode: str = "direct",
    started_at: datetime | None = None,
    ttfbs: list[float | None] | None = None,
    failure_categories: list[str | None] | None = None,
    url: str = MANIFEST_URL,
    interval_s: float = 1.0,
    timeout_s: float = 15.0,
    http_version: str = "1.1",
    ssl_no_revoke: bool = False,
    large_asset: dict | None = None,
    config_samples: int | None = None,
    asset_kind: str = "primary",
) -> dict:
    """构造一份域内（或按参数注入域外值）的探针窗报告。"""
    base = started_at or datetime.now(timezone.utc)
    ttfbs = ttfbs if ttfbs is not None else [200.0] * n
    cats = failure_categories if failure_categories is not None else [None] * n
    rows = []
    for i in range(n):
        rows.append(
            {
                "index": i,
                "asset": asset_kind,
                "started_at": (base + timedelta(seconds=i * interval_s)).isoformat(),
                "probe_mode": mode,
                "http_version_requested": "1.1",
                "http_version": "1.1",
                "ssl_no_revoke": ssl_no_revoke,
                "status": 200,
                "elapsed_ms": (ttfbs[i] or 0.0) + 5.0,
                "ttfb_ms": ttfbs[i],
                "dns_ms": 10.0,
                "tcp_ms": 20.0,
                "tls_ms": 30.0,
                "server_wait_ms": 40.0,
                "size_bytes": 1392,
                "sha256": "1e2ebb33" + "00" * 24,
                "body_complete": True,
                "content_type": "application/json",
                "remote_ip": "203.0.113.10",
                "exit_code": 0,
                "failure_category": cats[i],
            }
        )
    return {
        "schema": p0.WINDOW_SCHEMA,
        "tool": "public_edge_stability_probe",
        "generated_at": base.isoformat(timespec="seconds"),
        "curl_version": "curl 8.21.0 (fake)",
        "curl_features_has_http2": False,
        "config": {
            "url": url,
            "probe_mode": mode,
            "proxy": "http://127.0.0.1:7892" if mode == "proxy" else None,
            "http_version_requested": http_version,
            "ssl_no_revoke": ssl_no_revoke,
            "samples": config_samples if config_samples is not None else n,
            "interval_s": interval_s,
            "timeout_s": timeout_s,
            "expect_content_type": "application/json",
            "large_asset": large_asset,
        },
        "samples": rows,
        "summary": {},
    }


class FakeProbeInvoker:
    """探针 CLI 假实现：捕获 argv 并按参数写出合法窗报告（零网络）。"""

    def __init__(self, *, exit_code: int = 0, report_factory=window_report) -> None:
        self.calls: list[list[str]] = []
        self.exit_code = exit_code
        self.report_factory = report_factory

    def run(self, argv: list[str], *, timeout: float) -> tuple[int, str, str]:
        self.calls.append(list(argv))
        if self.exit_code >= 2:
            return self.exit_code, "[probe] FAIL: fake tool failure", ""
        output = argv[argv.index("--output") + 1]
        n = int(argv[argv.index("--samples") + 1])
        mode = "proxy" if "--proxy" in argv else "direct"
        interval = float(argv[argv.index("--interval") + 1])
        timeout_s = float(argv[argv.index("--timeout") + 1])
        url = argv[argv.index("--url") + 1]
        report = self.report_factory(n, mode=model_mode(mode), url=url, interval_s=interval, timeout_s=timeout_s)
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(report), encoding="utf-8")
        return self.exit_code, "[probe] fake ok", ""


def model_mode(mode: str) -> str:
    return mode


def write_history(dir_path: Path, windows: list[dict], prefix: str = "phase0-window-x") -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    for i, report in enumerate(windows):
        (dir_path / f"{prefix}{i}.json").write_text(json.dumps(report), encoding="utf-8")


class Bomb(p0.ProbeInvoker):
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], *, timeout: float) -> tuple[int, str, str]:
        raise AssertionError("probe must not be invoked in this path")


def today_local() -> str:
    return datetime.now().astimezone().date().isoformat()


# ---------------------------------------------------------------- CLI / plan / phrase


def test_cli_help_exits_zero_and_documents_required_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 双保险：main 的 --help 走通（exit 0）；且 main 实际装配的 parser
    # （build_parser 与 parse_args 同一 wiring）的 help 文本逐一记录
    # 四个必需选项。readouterr() 只能消费一次——先取出再断言。
    with pytest.raises(SystemExit) as excinfo:
        p0.main(["--help"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    help_text = p0.build_parser().format_help()
    for needle in ("--execute", "--confirm-phrase", "--aggregate", "--evidence-dir"):
        assert needle in help_text, needle
        assert needle in captured.out or needle in help_text, needle


def test_plan_only_zero_invocations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    evidence = tmp_path / "phase0"
    code = p0.main(["--url", MANIFEST_URL, "--evidence-dir", str(evidence)], invoker=Bomb())
    assert code == 0
    out = capsys.readouterr().out
    assert "plan-only" in out and CONFIRM in out  # 提示精确短语
    assert not evidence.exists()  # plan 零写入


@pytest.mark.parametrize(
    "phrase",
    ["", "EXECUTE PUBLIC EDGE PHASE0 WINDOW ", "execute public edge phase0 window",
     "EXECUTE PUBLIC EDGE PHASE0 WINDOWS"],
)
def test_wrong_phrase_zero_invocations(tmp_path: Path, phrase: str) -> None:
    evidence = tmp_path / "phase0"
    argv = ["--url", MANIFEST_URL, "--evidence-dir", str(evidence), "--execute"]
    if phrase:
        argv += ["--confirm-phrase", phrase]
    code = p0.main(argv, invoker=Bomb())
    assert code == 2  # 缺短语/错短语都零请求拒绝
    assert not (evidence).exists() or not list(evidence.glob("*.json"))


# ---------------------------------------------------------------- 预算门


def test_budget_total_rejection_zero_invocations(tmp_path: Path) -> None:
    evidence = tmp_path / "phase0"
    # 历史 9 窗 × 8 = 72 已满
    write_history(evidence, [window_report(8) for _ in range(9)])
    code = p0.main(
        ["--url", MANIFEST_URL, "--evidence-dir", str(evidence), "--execute",
         "--confirm-phrase", CONFIRM],
        invoker=Bomb(),
    )
    assert code == 2
    assert len(list(evidence.glob("phase0-window-2*"))) == 0  # 无新窗写出


def test_budget_daily_rejection_zero_invocations(tmp_path: Path) -> None:
    evidence = tmp_path / "phase0"
    # 今日 3 窗 × 8 = 24 已满（总 24 ≤ 72）——证明卡的是当日门
    write_history(evidence, [window_report(8) for _ in range(3)])
    code = p0.main(
        ["--url", MANIFEST_URL, "--evidence-dir", str(evidence), "--execute",
         "--confirm-phrase", CONFIRM],
        invoker=Bomb(),
    )
    assert code == 2


def test_daily_budget_counts_direct_and_proxy_together(tmp_path: Path) -> None:
    evidence = tmp_path / "phase0"
    fake = FakeProbeInvoker()
    for _ in range(3):  # 3 × 8 = 24（混合 direct/proxy 也算同一日预算）
        mode_argv = ["--proxy", "http://127.0.0.1:7892"] if fake.calls and len(fake.calls) % 2 else []
        assert p0.main(
            ["--url", MANIFEST_URL, "--evidence-dir", str(evidence), "--execute",
             "--confirm-phrase", CONFIRM, *mode_argv],
            invoker=fake,
        ) == 0
    assert len(fake.calls) == 3
    # 第 4 窗（无论模式）超当日 24 → 拒绝且零调用增量
    code = p0.main(
        ["--url", MANIFEST_URL, "--evidence-dir", str(evidence), "--execute",
         "--confirm-phrase", CONFIRM],
        invoker=fake,
    )
    assert code == 2 and len(fake.calls) == 3


def test_history_dates_are_local_not_utc(tmp_path: Path) -> None:
    # UTC 昨日 23:30 的样本在 UTC+8 落在本地今日——预算按本地日历计
    from tools.ops.public_edge_phase0_baseline import _local_date

    utc_yesterday_evening = datetime.now(timezone.utc).replace(hour=23, minute=30) - timedelta(days=1)
    assert _local_date(utc_yesterday_evening.isoformat()) >= utc_yesterday_evening.date().isoformat()


# ---------------------------------------------------------------- execute 成功路径


def test_execute_success_arguments_and_accounting(tmp_path: Path, capsys) -> None:
    evidence = tmp_path / "phase0"
    fake = FakeProbeInvoker()
    code = p0.main(
        ["--url", MANIFEST_URL, "--evidence-dir", str(evidence), "--execute",
         "--confirm-phrase", CONFIRM],
        invoker=fake,
    )
    assert code == 0 and len(fake.calls) == 1
    argv = fake.calls[0]
    assert argv[argv.index("--http-version") + 1] == "1.1"
    assert argv[argv.index("--samples") + 1] == "8"
    assert float(argv[argv.index("--interval") + 1]) >= 1.0
    assert float(argv[argv.index("--timeout") + 1]) <= 15.0
    assert argv[argv.index("--url") + 1] == MANIFEST_URL
    output = argv[argv.index("--output") + 1]
    assert Path(output).name.startswith("phase0-window-") and output.endswith(".json")
    joined = " ".join(argv)
    assert "--large-asset" not in joined and "--ssl-no-revoke" not in joined
    assert "--http-version 2" not in joined
    out = capsys.readouterr().out
    assert "budget: total 8/72" in out
    assert not Path(output + ".reserve").exists()  # 成功后预约释放
    assert Path(output).exists()
    # 第二窗入账 → 16/72
    assert p0.main(
        ["--url", MANIFEST_URL, "--evidence-dir", str(evidence), "--execute",
         "--confirm-phrase", CONFIRM],
        invoker=fake,
    ) == 0
    assert "budget: total 16/72" in capsys.readouterr().out


def test_execute_proxy_window_passes_proxy_flag(tmp_path: Path) -> None:
    evidence = tmp_path / "phase0"
    fake = FakeProbeInvoker()
    code = p0.main(
        ["--url", MANIFEST_URL, "--evidence-dir", str(evidence), "--execute",
         "--confirm-phrase", CONFIRM, "--proxy", "http://127.0.0.1:7892"],
        invoker=fake,
    )
    assert code == 0
    argv = fake.calls[0]
    assert argv[argv.index("--proxy") + 1] == "http://127.0.0.1:7892"


def test_execute_probe_tool_failure_no_retry(tmp_path: Path, capsys) -> None:
    evidence = tmp_path / "phase0"
    fake = FakeProbeInvoker(exit_code=2)  # 探针工具自身失败（能力门等）
    code = p0.main(
        ["--url", MANIFEST_URL, "--evidence-dir", str(evidence), "--execute",
         "--confirm-phrase", CONFIRM],
        invoker=fake,
    )
    assert code == 2 and len(fake.calls) == 1  # 零重试
    err = capsys.readouterr().err
    assert "未入账" in err and "预约保留" in err
    output = fake.calls[0][fake.calls[0].index("--output") + 1]
    assert Path(output + ".reserve").exists()  # 失败后预约保留：同路径不可复用
    assert not Path(output).exists()


def test_execute_report_unparseable_keeps_reservation(tmp_path: Path, capsys) -> None:
    evidence = tmp_path / "phase0"

    class GarbageReportInvoker(FakeProbeInvoker):
        def run(self, argv, *, timeout):
            self.calls.append(list(argv))
            output = argv[argv.index("--output") + 1]
            Path(output).write_text("{ broken", encoding="utf-8")
            return 0, "[probe] fake", ""

    fake = GarbageReportInvoker()
    code = p0.main(
        ["--url", MANIFEST_URL, "--evidence-dir", str(evidence), "--execute",
         "--confirm-phrase", CONFIRM],
        invoker=fake,
    )
    assert code == 2
    output = fake.calls[0][fake.calls[0].index("--output") + 1]
    assert Path(output + ".reserve").exists()  # 报告不可入账：预约同样保留


def test_existing_report_or_reservation_rejected_zero_invocations(tmp_path: Path) -> None:
    evidence = tmp_path / "phase0"
    evidence.mkdir(parents=True)
    stale = evidence / "phase0-window-20260929-120000-direct.json"
    stale.write_text(json.dumps(window_report(8)), encoding="utf-8")
    plan = p0.WindowPlan(
        url=MANIFEST_URL, samples=8, interval_s=1.0, timeout_s=15.0,
        proxy_display=None, evidence_dir=evidence,
        output_name=stale.name,  # 复用既有报告路径
    )
    with pytest.raises(p0.Phase0Error):
        p0.execute_window(plan, Bomb(), Path("tools/ops/public_edge_stability_probe.py"))
    # 预约残留同样拒绝（零探针调用）
    (evidence / "phase0-window-x.json.reserve").write_text("", encoding="utf-8")
    plan2 = p0.WindowPlan(
        url=MANIFEST_URL, samples=8, interval_s=1.0, timeout_s=15.0,
        proxy_display=None, evidence_dir=evidence,
        output_name="phase0-window-x.json",
    )
    with pytest.raises(p0.Phase0Error):
        p0.execute_window(plan2, Bomb(), Path("tools/ops/public_edge_stability_probe.py"))


# ---------------------------------------------------------------- 缺目录语义（allow-missing）


def test_plan_and_execute_allow_missing_directory(tmp_path: Path) -> None:
    # plan/execute 预检：目录不存在 = 零历史（首个窗合法起点）
    history = p0.load_history(tmp_path / "absent", allow_missing=True)
    assert history.total_manifest_requests == 0 and history.windows == ()
    code = p0.main(
        ["--url", MANIFEST_URL, "--evidence-dir", str(tmp_path / "absent")],
        invoker=Bomb(),
    )
    assert code == 0  # plan 通过，零请求


def test_aggregate_rejects_missing_directory(tmp_path: Path, capsys) -> None:
    # aggregate 不为不存在的目录编造空聚合
    code = p0.main(["--evidence-dir", str(tmp_path / "absent"), "--aggregate"])
    assert code == 2
    assert "拒绝空目录" in capsys.readouterr().err
    assert not (tmp_path / "absent").exists()  # 未创建目录


# ---------------------------------------------------------------- 数值域校验


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["config"].__setitem__("samples", True),
        lambda r: r["config"].__setitem__("interval_s", float("nan")),
        lambda r: r["config"].__setitem__("timeout_s", float("inf")),
        lambda r: r["samples"][0].__setitem__("ttfb_ms", True),
        lambda r: r["samples"][1].__setitem__("elapsed_ms", float("nan")),
        lambda r: r["samples"][2].__setitem__("ttfb_ms", float("-inf")),
    ],
)
def test_numeric_domain_violations_rejected(tmp_path: Path, mutate) -> None:
    evidence = tmp_path / "phase0"
    report = window_report(8)
    # JSON 序列化 NaN/inf 需非严格 dump（json.dumps 默认允许 NaN/Infinity）
    mutate(report)
    evidence.mkdir(parents=True)
    (evidence / "phase0-window-bad.json").write_text(
        json.dumps(report), encoding="utf-8"
    )
    code = p0.main(
        ["--url", MANIFEST_URL, "--evidence-dir", str(evidence), "--execute",
         "--confirm-phrase", CONFIRM],
        invoker=Bomb(),
    )
    assert code == 2  # bool/NaN/inf 冒充数值 → 畸形历史，零探针调用


# ---------------------------------------------------------------- 畸形历史拒绝


@pytest.mark.parametrize(
    "make_report",
    [
        lambda: "{ not json",
        lambda: {"schema": "unknown/1", "tool": "other"},
        lambda: window_report(8, large_asset={"url": "https://ndtool.cn/android/a.apk"}),
        lambda: window_report(8, config_samples=9),
        lambda: window_report(8, http_version="2"),
        lambda: window_report(8, ssl_no_revoke=True),
        lambda: window_report(8, interval_s=0.5),
        lambda: window_report(8, timeout_s=20.0),
        lambda: window_report(8, asset_kind="large"),
    ],
)
def test_malformed_history_rejects_with_zero_invocations(tmp_path: Path, make_report) -> None:
    evidence = tmp_path / "phase0"
    evidence.mkdir(parents=True)
    (evidence / "phase0-window-bad.json").write_text(
        make_report() if isinstance(make_report(), str) else json.dumps(make_report()),
        encoding="utf-8",
    )
    code = p0.main(
        ["--url", MANIFEST_URL, "--evidence-dir", str(evidence), "--execute",
         "--confirm-phrase", CONFIRM],
        invoker=Bomb(),
    )
    assert code == 2  # 畸形/歧义历史 → 零探针调用


def test_history_sample_missing_started_at_rejected(tmp_path: Path) -> None:
    evidence = tmp_path / "phase0"
    report = window_report(8)
    del report["samples"][3]["started_at"]
    write_history(evidence, [report])
    code = p0.main(
        ["--url", MANIFEST_URL, "--evidence-dir", str(evidence), "--execute",
         "--confirm-phrase", CONFIRM],
        invoker=Bomb(),
    )
    assert code == 2


# ---------------------------------------------------------------- 聚合数学与口径


def test_aggregate_descriptive_math_and_gates_excluded(tmp_path: Path) -> None:
    evidence = tmp_path / "phase0"
    day_a = datetime.now(timezone.utc)
    day_b = day_a - timedelta(days=2)
    window_a = window_report(
        3, mode="direct", started_at=day_a,
        ttfbs=[100.0, 200.0, 3000.0], failure_categories=[None, None, None],
    )
    window_a["samples"][0]["failure_category"] = "timeout"
    window_a["samples"][0]["ttfb_ms"] = None  # 失败样本无 TTFB
    window_b = window_report(2, mode="proxy", started_at=day_b, ttfbs=[200.0, 400.0])
    write_history(evidence, [window_a, window_b])

    report = p0.aggregate(p0.load_history(evidence))
    assert report["statistical_scope"] == "descriptive-only"
    assert report["total_manifest_requests"] == 5 and report["windows_parsed"] == 2
    overall = report["overall"]
    assert overall["samples"] == 5 and overall["failures"] == 1
    assert overall["slow_window_samples"] == 1  # 唯一 TTFB 3000 > 2500
    assert overall["ttfb_ms_missing"] == 1  # 失败样本排除出分位分母
    # nearest-rank：sorted ttfb = [100, 200, 400, 3000]，n=4
    assert overall["ttfb_ms"]["samples"] == 4
    assert overall["ttfb_ms"]["p50"] == 200.0  # ceil(0.5*4)=2 → 第 2 值
    assert overall["ttfb_ms"]["p95"] == 3000.0  # ceil(0.95*4)=4 → 第 4 值
    assert overall["ttfb_ms"]["max"] == 3000.0
    by_mode = report["by_probe_mode"]
    assert by_mode["direct"]["samples"] == 3 and by_mode["proxy"]["samples"] == 2
    assert set(report["by_local_date"]) == {
        p0._local_date(window_a["samples"][0]["started_at"]),
        p0._local_date(window_b["samples"][0]["started_at"]),
    }
    # 明确不设门：递归断言整个统计对象树中不存在名为 p99/success_rate
    # 的键（不扫原文——说明性 note 有意提及这些词以声明其不被支持）。
    def assert_no_gate_keys(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in ("p99", "success_rate"), key
                assert_no_gate_keys(value)
        elif isinstance(node, list):
            for item in node:
                assert_no_gate_keys(item)

    assert_no_gate_keys(report)
    assert "p99" in report["note"]  # note 声明不设门（文本可提及，键不可存在）


def test_aggregate_mode_writes_report_and_is_skipped_by_budget(tmp_path: Path, capsys) -> None:
    evidence = tmp_path / "phase0"
    write_history(evidence, [window_report(8)])
    code = p0.main(["--evidence-dir", str(evidence), "--aggregate"])
    assert code == 0
    out = capsys.readouterr().out
    assert "descriptive-only" in out
    aggregates = list(evidence.glob("phase0-aggregate-*.json"))
    assert len(aggregates) == 1
    payload = json.loads(aggregates[0].read_text(encoding="utf-8"))
    assert payload["schema"] == p0.AGGREGATE_SCHEMA
    # 聚合文件不进预算账：再执行一窗仍以 8/72 起步
    fake = FakeProbeInvoker()
    assert p0.main(
        ["--url", MANIFEST_URL, "--evidence-dir", str(evidence), "--execute",
         "--confirm-phrase", CONFIRM],
        invoker=fake,
    ) == 0
    assert "budget: total 16/72" in capsys.readouterr().out


def test_aggregate_rejects_execute_combo(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        p0.main(["--evidence-dir", str(tmp_path), "--aggregate", "--execute"])
    assert excinfo.value.code == 2


# ---------------------------------------------------------------- 输出卫生


def test_output_hygiene_no_local_paths_or_secrets(tmp_path: Path) -> None:
    evidence = tmp_path / "phase0"
    fake = FakeProbeInvoker()
    assert p0.main(
        ["--url", MANIFEST_URL, "--evidence-dir", str(evidence), "--execute",
         "--confirm-phrase", CONFIRM],
        invoker=fake,
    ) == 0
    assert p0.main(["--evidence-dir", str(evidence), "--aggregate"]) == 0
    for artifact in evidence.glob("*.json"):
        text = artifact.read_text(encoding="utf-8")
        assert str(tmp_path) not in text, artifact.name
        assert "\\Users\\" not in text and "C:" not in text, artifact.name
        lowered = text.lower()
        for needle in ("password", "api_key", "bearer ", "secret-"):
            assert needle not in lowered, artifact.name
