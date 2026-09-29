"""Tests for tools.ops.public_edge_phase0_decision (M14-186 decision gate).

全部证据都是 tmp_path 下的 fake 文件——零网络、零探针、零子进程。
本工具是只读本地决策门：任何缺陷/不完整都必须落 phase0_inconclusive 且
phase1_sampling_authorized=false；只有完整零缺陷且慢窗频率 ≥5% 才 go。
"""

from __future__ import annotations

import json
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.ops import public_edge_phase0_decision as gate

MANIFEST_URL = "https://ndtool.cn/aios/download-manifest.json"
FAST_MS = 200.0
SLOW_MS = 3000.0
TZ = timezone(timedelta(hours=8))  # 操作者本地时区（UTC+8）


# ---------------------------------------------------------------- fakes / fixtures


def window_report(
    ttfbs: list[float | None], *, started_at: datetime, mode: str = "direct"
) -> dict[str, Any]:
    """构造一份 M14-182 parse 契约域内的探针窗报告（长度 = len(ttfbs)）。"""
    rows = []
    for i, ttfb in enumerate(ttfbs):
        rows.append(
            {
                "index": i,
                "asset": "primary",
                "started_at": (started_at + timedelta(seconds=i)).isoformat(),
                "probe_mode": mode,
                "http_version_requested": "1.1",
                "http_version": "1.1",
                "ssl_no_revoke": False,
                "status": 200,
                "elapsed_ms": (ttfb or 0.0) + 5.0,
                "ttfb_ms": ttfb,
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
                "failure_category": None,
            }
        )
    return {
        "schema": gate.WINDOW_SCHEMA,
        "tool": "public_edge_stability_probe",
        "generated_at": started_at.isoformat(timespec="seconds"),
        "curl_version": "curl 8.21.0 (fake)",
        "curl_features_has_http2": False,
        "config": {
            "url": MANIFEST_URL,
            "probe_mode": mode,
            "proxy": None,
            "http_version_requested": "1.1",
            "ssl_no_revoke": False,
            "samples": len(ttfbs),
            "interval_s": 1.0,
            "timeout_s": 15.0,
            "expect_content_type": "application/json",
            "large_asset": None,
        },
        "samples": rows,
        "summary": {},
    }


def day_start(day_index: int) -> datetime:
    return datetime(2026, 9, 27, 10, 0, 0, tzinfo=TZ) + timedelta(days=day_index)


def complete_windows(
    *, ttfbs_override: dict[tuple[int, int], list[float | None]] | None = None
) -> list[dict[str, Any]]:
    """3 本地日期 × 每日 3 窗 × 每窗 8 样本（默认全 FAST，可逐窗覆写 TTFB）。"""
    reports = []
    for day in range(3):
        for w in range(3):
            ttfbs: list[float | None] = [FAST_MS] * 8
            if ttfbs_override and (day, w) in ttfbs_override:
                ttfbs = ttfbs_override[(day, w)]
            reports.append(
                window_report(ttfbs, started_at=day_start(day) + timedelta(hours=2 * w))
            )
    return reports


def write_evidence(dir_path: Path, reports: list[dict[str, Any]]) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    for i, report in enumerate(reports):
        (dir_path / f"phase0-window-fake-{i:02d}.json").write_text(
            json.dumps(report), encoding="utf-8"
        )


def run_and_parse(argv: list[str], capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    """跑一次 CLI 并把 stdout 解析为单个 JSON 文档（stdout 必须是纯 JSON）。"""
    code = gate.main(argv)
    assert code == 0
    return json.loads(capsys.readouterr().out)


def clean_complete_facts(**overrides: Any) -> dict[str, Any]:
    """decide() 的完整零缺陷基线参数（可覆写单项做规则表单元测试）。"""
    facts: dict[str, Any] = {
        "directory_missing": False,
        "malformed_names": (),
        "windows_per_date": (3, 3, 3),
        "samples_per_window": (8,) * 9,
        "samples_per_date": (24, 24, 24),
        "total_samples": 72,
        "failures": 0,
        "ttfb_missing": 0,
        "ttfb_samples": 72,
        "slow_window_frequency": 0.0,
        "ttfb_p95_ms": FAST_MS,
    }
    facts.update(overrides)
    return facts


def assert_no_gate_keys(node: Any) -> None:
    """递归断言键域不存在 p99 / success_rate（M14-181 统计诚实边界）。"""
    if isinstance(node, dict):
        for key, value in node.items():
            assert key not in ("p99", "success_rate"), key
            assert_no_gate_keys(value)
    elif isinstance(node, list):
        for item in node:
            assert_no_gate_keys(item)


# ---------------------------------------------------------------- CLI help


def test_cli_help_documents_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        gate.main(["--help"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()  # readouterr 只能消费一次——先取出再断言
    help_text = gate.build_parser().format_help()
    for needle in ("--evidence-dir", "--output"):
        assert needle in help_text, needle
        assert needle in captured.out or needle in help_text, needle


# ---------------------------------------------------------------- 缺目录 / 空目录


def test_missing_directory_is_decidable_incomplete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 目录不存在 = 尚未开始采样：可判定的 inconclusive（非运行错误），绝不授权
    report = run_and_parse(["--evidence-dir", str(tmp_path / "absent")], capsys)
    assert report["decision"] == gate.DECISION_INCONCLUSIVE
    assert "evidence_directory_missing" in report["reason_codes"]
    assert report["phase1_sampling_authorized"] is False
    assert report["counts"]["windows"] == 0


def test_empty_directory_is_incomplete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "phase0").mkdir()
    report = run_and_parse(["--evidence-dir", str(tmp_path / "phase0")], capsys)
    assert report["decision"] == gate.DECISION_INCONCLUSIVE
    assert report["phase1_sampling_authorized"] is False
    assert "distinct_dates_below_required" in report["reason_codes"]
    assert "total_samples_below_required" in report["reason_codes"]


# ---------------------------------------------------------------- 不完整（fixture 形态，非 canonical 现状）


def test_incomplete_single_day_two_windows_fixture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "phase0"
    # fixture 形态：单日 2 窗 × 8 = 16 样本——只测"少数据 = incomplete"
    # 语义，不代表 canonical 证据现状（实现收口时点真实状态见 evidence
    # README §4）
    write_evidence(evidence, complete_windows()[:2])
    report = run_and_parse(["--evidence-dir", str(evidence)], capsys)
    assert report["decision"] == gate.DECISION_INCONCLUSIVE
    assert report["phase1_sampling_authorized"] is False
    for reason in ("distinct_dates_below_required", "total_samples_below_required"):
        assert reason in report["reason_codes"], reason
    checks = report["completion_checks"]
    assert checks["exactly_3_distinct_local_dates"] is False
    assert checks["complete"] is False
    assert checks["daily_budget_within_24"] is True  # 数据少不等于越界
    assert checks["total_budget_within_72"] is True
    assert report["counts"]["samples"] == 16


def test_short_window_is_incomplete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "phase0"
    reports = complete_windows()
    reports[4] = window_report([FAST_MS] * 7, started_at=day_start(1) + timedelta(hours=2))
    write_evidence(evidence, reports)
    report = run_and_parse(["--evidence-dir", str(evidence)], capsys)
    assert report["decision"] == gate.DECISION_INCONCLUSIVE
    for reason in (
        "samples_per_window_below_required",
        "daily_samples_below_required",
        "total_samples_below_required",
    ):
        assert reason in report["reason_codes"], reason


# ---------------------------------------------------------------- 完整 go / no-go


def test_complete_evidence_with_slow_window_is_go(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "phase0"
    write_evidence(
        evidence, complete_windows(ttfbs_override={(0, 0): [FAST_MS] * 7 + [SLOW_MS]})
    )
    report = run_and_parse(["--evidence-dir", str(evidence)], capsys)
    assert report["schema"] == gate.DECISION_SCHEMA
    assert report["tool"] == "public_edge_phase0_decision"
    assert report["decision"] == gate.DECISION_GO
    assert report["phase1_sampling_authorized"] is True
    assert "slow_window_frequency_ge_go_threshold" in report["reason_codes"]
    assert report["counts"]["samples"] == 72
    assert report["counts"]["ttfb_samples"] == 72
    assert report["counts"]["slow_samples"] == 1
    assert report["counts"]["slow_windows"] == 1
    per_date = report["counts"]["per_local_date"]
    assert len(per_date) == 3
    assert all(row["windows"] == 3 and row["samples"] == 24 for row in per_date.values())
    metrics = report["metrics"]
    assert metrics["slow_window_frequency"] == pytest.approx(1 / 9)  # ≥5% → go
    assert metrics["slow_sample_frequency"] == pytest.approx(1 / 72)
    # nearest-rank：71×200 + 1×3000 → p50/p95 都取第 36/69 位 = 200，max = 3000
    assert metrics["ttfb_ms"]["p50"] == FAST_MS
    assert metrics["ttfb_ms"]["p95"] == FAST_MS
    assert metrics["ttfb_ms"]["max"] == SLOW_MS
    assert len(metrics["slow_window_files"]) == 1
    assert all(report["completion_checks"][key] for key in report["completion_checks"])


def test_complete_fast_evidence_closes_phase0(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "phase0"
    write_evidence(evidence, complete_windows())  # 0 慢窗、全 TTFB 200ms
    report = run_and_parse(["--evidence-dir", str(evidence)], capsys)
    assert report["decision"] == gate.DECISION_NO_GO_CLOSE
    assert report["phase1_sampling_authorized"] is False  # 关闭同样不授权采样
    assert "slow_window_frequency_below_no_go_threshold" in report["reason_codes"]
    assert "ttfb_p95_within_no_go_threshold" in report["reason_codes"]
    assert report["metrics"]["slow_window_frequency"] == 0.0


def test_decide_middle_band_and_p95_branch() -> None:
    # 规则表单元测试：完成数据恰 9 窗时频率量化为 0% 或 ≥1/9≈11.1%，
    # [1%,5%) 中间带经文件不可达——但规则必须独立成立（防窗数契约演进）。
    decision, reasons, _ = gate.decide(**clean_complete_facts(slow_window_frequency=0.03))
    assert decision == gate.DECISION_INCONCLUSIVE
    assert "slow_window_frequency_in_middle_band" in reasons
    decision, reasons, _ = gate.decide(
        **clean_complete_facts(slow_window_frequency=0.005, ttfb_p95_ms=SLOW_MS)
    )
    assert decision == gate.DECISION_INCONCLUSIVE
    assert "ttfb_p95_above_no_go_threshold" in reasons


# ---------------------------------------------------------------- 数据缺陷


@pytest.mark.parametrize(
    "payload",
    ["{ broken", '{"schema": "unknown/1", "tool": "other"}'],
)
def test_malformed_report_is_inconclusive_never_go(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], payload: str
) -> None:
    evidence = tmp_path / "phase0"
    write_evidence(evidence, complete_windows())
    (evidence / "phase0-window-bad.json").write_text(payload, encoding="utf-8")
    report = run_and_parse(["--evidence-dir", str(evidence)], capsys)
    assert report["decision"] == gate.DECISION_INCONCLUSIVE
    assert report["phase1_sampling_authorized"] is False
    assert "malformed_report:phase0-window-bad.json" in report["reason_codes"]
    assert report["counts"]["windows"] == 9  # 畸形文件不入窗账，其余如实计数
    malformed = report["input_files"]["malformed"]
    assert [entry["file"] for entry in malformed] == ["phase0-window-bad.json"]
    assert "C:" not in json.dumps(malformed)  # 畸形摘要不含本机绝对路径


def test_failure_sample_blocks_go(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "phase0"
    reports = complete_windows()
    reports[0]["samples"][3]["failure_category"] = "timeout"
    reports[0]["samples"][3]["ttfb_ms"] = None  # 失败样本无 TTFB
    write_evidence(evidence, reports)
    report = run_and_parse(["--evidence-dir", str(evidence)], capsys)
    assert report["decision"] == gate.DECISION_INCONCLUSIVE
    assert report["phase1_sampling_authorized"] is False
    assert "window_failures_present" in report["reason_codes"]
    assert "missing_ttfb_present" in report["reason_codes"]
    assert report["counts"]["failures"] == 1
    assert report["counts"]["ttfb_missing"] == 1


def test_missing_ttfb_blocks_go(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "phase0"
    reports = complete_windows()
    reports[0]["samples"][3]["ttfb_ms"] = None  # 无失败但 TTFB 缺失
    write_evidence(evidence, reports)
    report = run_and_parse(["--evidence-dir", str(evidence)], capsys)
    assert report["decision"] == gate.DECISION_INCONCLUSIVE
    assert "missing_ttfb_present" in report["reason_codes"]
    assert "window_failures_present" not in report["reason_codes"]


def test_distribution_not_formed_below_floor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "phase0"
    write_evidence(evidence, complete_windows()[:2])  # 16 个 TTFB < 24 下限
    report = run_and_parse(["--evidence-dir", str(evidence)], capsys)
    assert report["decision"] == gate.DECISION_INCONCLUSIVE
    assert "distribution_not_formed" in report["reason_codes"]
    assert report["completion_checks"]["distribution_formed"] is False


# ---------------------------------------------------------------- 预算/窗数越界


def test_daily_budget_violation_never_go(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "phase0"
    reports = complete_windows()
    reports.append(window_report([FAST_MS] * 8, started_at=day_start(0) + timedelta(hours=6)))
    write_evidence(evidence, reports)  # 首日 4 窗 32 样本 > 24
    report = run_and_parse(["--evidence-dir", str(evidence)], capsys)
    assert report["decision"] == gate.DECISION_INCONCLUSIVE
    assert report["phase1_sampling_authorized"] is False
    assert "windows_per_date_above_required" in report["reason_codes"]
    assert "daily_budget_exceeded" in report["reason_codes"]
    assert report["completion_checks"]["daily_budget_within_24"] is False


def test_fourth_date_is_total_budget_violation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "phase0"
    reports = complete_windows()
    for w in range(3):  # 第 4 个本地日期 × 3 窗：逐日合规但总数 96 > 72
        reports.append(window_report([FAST_MS] * 8, started_at=day_start(3) + timedelta(hours=2 * w)))
    write_evidence(evidence, reports)
    report = run_and_parse(["--evidence-dir", str(evidence)], capsys)
    assert report["decision"] == gate.DECISION_INCONCLUSIVE
    assert "distinct_dates_above_required" in report["reason_codes"]
    assert "total_budget_exceeded" in report["reason_codes"]
    assert report["completion_checks"]["total_budget_within_72"] is False
    assert report["completion_checks"]["daily_budget_within_24"] is True


def test_duplicate_window_file_counts_as_extra_window(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "phase0"
    write_evidence(evidence, complete_windows())
    first = min(evidence.glob("phase0-window-*.json"))
    (evidence / "phase0-window-duplicate.json").write_text(
        first.read_text(encoding="utf-8"), encoding="utf-8"
    )  # 同窗重复落档：该日第 4 窗
    report = run_and_parse(["--evidence-dir", str(evidence)], capsys)
    assert report["decision"] == gate.DECISION_INCONCLUSIVE
    assert report["phase1_sampling_authorized"] is False
    assert "windows_per_date_above_required" in report["reason_codes"]
    assert "daily_budget_exceeded" in report["reason_codes"]


def test_midnight_crossing_window_spans_two_dates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 窗按首样本日期归窗、样本逐个按自身日期入账：跨本地午夜的窗让
    # 次日成为"有样本无窗"的日历日 → 不完整（日期数以样本归账为准）。
    evidence = tmp_path / "phase0"
    before = window_report([FAST_MS] * 4, started_at=datetime(2026, 9, 27, 23, 59, 58, tzinfo=TZ))
    after = window_report([FAST_MS] * 8, started_at=day_start(1))
    write_evidence(evidence, [before, after])
    report = run_and_parse(["--evidence-dir", str(evidence)], capsys)
    assert report["decision"] == gate.DECISION_INCONCLUSIVE
    assert "distinct_dates_below_required" in report["reason_codes"]
    per_date = report["counts"]["per_local_date"]
    assert len(per_date) == 2  # 2026-09-27（1 窗 4 样本）+ 09-28（1 窗 8 样本）
    assert sum(row["windows"] for row in per_date.values()) == 2
    assert sum(row["samples"] for row in per_date.values()) == 12


# ---------------------------------------------------------------- 目录成员分类


def test_aggregate_and_reserve_files_ignored(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "phase0"
    write_evidence(
        evidence, complete_windows(ttfbs_override={(0, 0): [FAST_MS] * 7 + [SLOW_MS]})
    )
    (evidence / "phase0-aggregate-fake.json").write_text(
        json.dumps({"schema": gate.AGGREGATE_SCHEMA, "tool": "public_edge_phase0_baseline"}),
        encoding="utf-8",
    )
    (evidence / "phase0-window-fake-00.json.reserve").write_text("", encoding="utf-8")
    (evidence / "phase0-window-fake-01.json.reserve").write_text("", encoding="utf-8")
    report = run_and_parse(["--evidence-dir", str(evidence)], capsys)
    assert report["decision"] == gate.DECISION_GO  # 聚合/预约残留不影响决策
    assert report["phase1_sampling_authorized"] is True
    assert report["input_files"]["aggregates_skipped"] == ["phase0-aggregate-fake.json"]
    assert report["input_files"]["reserve_files_ignored"] == 2
    assert report["counts"]["windows"] == 9


# ---------------------------------------------------------------- 输出路径


def test_output_written_and_reserve_released(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "phase0"
    write_evidence(
        evidence, complete_windows(ttfbs_override={(0, 0): [FAST_MS] * 7 + [SLOW_MS]})
    )
    output = tmp_path / "decision.json"
    assert gate.main(["--evidence-dir", str(evidence), "--output", str(output)]) == 0
    captured = capsys.readouterr()
    printed = json.loads(captured.out)  # stdout 恒为单个可解析 JSON 文档
    assert printed["decision"] == gate.DECISION_GO
    assert json.loads(output.read_text(encoding="utf-8")) == printed
    assert not output.with_name(output.name + ".reserve").exists()  # 成功释放预约
    assert "written" in captured.err  # 信息行走 stderr，不污染 stdout JSON


def test_output_collision_refuses_without_touching_existing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "phase0"
    write_evidence(evidence, complete_windows())
    output = tmp_path / "decision.json"
    output.write_text("KEEP", encoding="utf-8")
    assert gate.main(["--evidence-dir", str(evidence), "--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""  # 运行失败不打印决策 JSON
    assert output.read_text(encoding="utf-8") == "KEEP"
    # 预约残留同样拒绝且不产生新文件
    second = tmp_path / "decision2.json"
    second.with_name(second.name + ".reserve").write_text("", encoding="utf-8")
    assert gate.main(["--evidence-dir", str(evidence), "--output", str(second)]) == 2
    assert not second.exists()


# ---------------------------------------------------------------- 诚实边界与零能力


def test_no_p99_or_availability_gate_keys(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "phase0"
    write_evidence(evidence, complete_windows())
    report = run_and_parse(["--evidence-dir", str(evidence)], capsys)
    assert_no_gate_keys(report)
    boundaries = report["honest_boundaries"]
    assert boundaries["statistical_scope"] == "descriptive-only"
    assert boundaries["no_p99_gate"] is True
    assert boundaries["no_availability_rate_gate"] is True


def test_no_network_and_no_probe_capabilities(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 模块命名空间不引入任何网络/子进程能力（只读本地工具）
    for banned in ("subprocess", "urllib", "socket", "requests", "http"):
        assert not hasattr(gate, banned), banned
    # 行为级：拆掉 socket 后仍能完整决策——任何网络路径都会炸
    def bombed(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("decision gate must not open network connections")

    monkeypatch.setattr(socket, "socket", bombed)
    monkeypatch.setattr(socket, "create_connection", bombed)
    evidence = tmp_path / "phase0"
    write_evidence(evidence, complete_windows())
    report = run_and_parse(["--evidence-dir", str(evidence)], capsys)
    assert report["decision"] == gate.DECISION_NO_GO_CLOSE


def test_report_deterministic_for_same_input(tmp_path: Path) -> None:
    evidence = tmp_path / "phase0"
    write_evidence(evidence, complete_windows())
    first = gate.build_report(evidence)
    second = gate.build_report(evidence)
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second


def test_output_hygiene_no_local_paths_or_secrets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "phase0"
    write_evidence(evidence, complete_windows())
    output = tmp_path / "decision.json"
    assert gate.main(["--evidence-dir", str(evidence), "--output", str(output)]) == 0
    capsys.readouterr()
    text = output.read_text(encoding="utf-8")
    assert str(tmp_path) not in text
    assert "C:" not in text and "\\Users\\" not in text
    lowered = text.lower()
    for needle in ("password", "api_key", "bearer ", "secret-"):
        assert needle not in lowered, needle
