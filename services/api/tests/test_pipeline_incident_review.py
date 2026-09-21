r"""M14-79 tools/ops/pipeline_incident_review.py 契约测试：pipeline 报告 ×
history.jsonl 交叉只读复盘——区分「管道执行失败（瞬态/持续）」与
「监控状态劣化」、记录超时/补录/恢复语义且绝不遮蔽失败
（零真实容器面/零网络/零计划任务/零 env 读取/零墙钟/零子进程）。

覆盖（全部 I/O 经真实临时目录；绝不触碰 canonical 仓库与真实 .verify）：
- 结构契约：源码零子进程/零网络/零 env/零墙钟 token；socket+subprocess
  双阻断下端到端照常成功；
- 2026-09-21 真实事件形态回放：monitor 非零但有工件 = 状态域（critical
  样本入史）；monitor 非零且无工件 = 执行域（该槽位零样本）；monitor ok
  而 history 超时 = 执行域瞬态（工件已落盘、补录事实如实、绝不改记成功）；
  skipped-after-verdict 不计执行失败；
- 事件聚合与恢复：连续失败合并、恢复运行归因、最新失败保持开放；
- 判定与退出码：all-clear=0 / 开放项=1（可见结论）/ 拒绝=2（零输出）；
- 零墙钟：两次运行输出逐字节相同；generated_at = max(最新运行, 最新样本)；
- 输入拒绝面（零输出）：目录缺失 / 未知 json stem / stage 状态越词汇 /
  monitor skipped / schema_version 漂移 / pipeline-* 携 mode=plan /
  started_at 重复或非时序 / history 全套拒绝（malformed/重复/非时序/
  项目冲突/空文件/行数超顶）/ 参数越界；
- M14-21 前两步形态（无 insights）可解析；--runs 切片省略计数；
- CLI 默认值与 ops README 文档化。
"""
from __future__ import annotations

import importlib.util
import json
import socket
import subprocess as subprocess_module
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "pipeline_incident_review.py"
OPS_README = REPO_ROOT / "tools" / "ops" / "README.md"

MARK_TOKEN = "sk-ZXmarker987654321"

PROJECT = "aios-m14-03-production-rehearsal"
FMT = "%Y-%m-%dT%H:%M:%SZ"
BASE = datetime(2026, 9, 21, 4, 0, 1, tzinfo=timezone.utc)

JSON_REPORT = "pipeline-incident-review.json"
MD_REPORT = "pipeline-incident-review.md"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


pir = _load_module(SCRIPT, "pipeline_incident_review_under_test")


# ---------------------------------------------------------------- 工厂


def _iso(dt: datetime) -> str:
    return dt.strftime(FMT)


def _history_row(dt: datetime, *, overall: str = "ok",
                 partial: bool = False) -> dict[str, object]:
    return {"schema_version": 1, "collected_at": _iso(dt),
            "project": PROJECT, "overall_status": overall, "partial": partial}


def _stage(status: str, *, exit_code: int | None = 0,
           timed_out: bool = False) -> dict[str, object]:
    return {"status": status, "exit_code": exit_code, "timed_out": timed_out,
            "failure_category": None, "error_class": None,
            "failure_detail": None, "skipped_reason": None,
            "started_at_utc": _iso(BASE), "ended_at_utc": _iso(BASE),
            "duration_seconds": 1.0, "timeout_seconds": 45.0, "artifacts": None}


def _monitor_stage(status: str, *, exit_code: int | None,
                   artifact_stem: str | None) -> dict[str, object]:
    stage = _stage(status, exit_code=exit_code)
    if artifact_stem is not None:
        stage["artifacts"] = [{"name": f"{artifact_stem}.json",
                               "sha256": "0" * 64, "bytes": 100}]
    else:
        stage["artifacts"] = []
    return stage


def _pipeline_report(*, started: datetime, monitor: dict[str, object],
                     history: dict[str, object],
                     insights: dict[str, object] | None,
                     overall: str) -> dict[str, object]:
    stages: dict[str, object] = {"monitor": monitor, "history": history}
    if insights is not None:
        stages["insights"] = insights
    return {"schema_version": 1, "tool": "tools/ops/monitoring_pipeline.py",
            "milestone": "M14-14", "mode": "execute",
            "started_at_utc": _iso(started), "ended_at_utc": _iso(started),
            "config": {}, "stages": stages, "overall_status": overall,
            "lock": None, "boundaries": []}


def _write_report(directory: Path, report: dict[str, object], *,
                  name: str | None = None) -> None:
    started = str(report["started_at_utc"])
    stamp = started.replace("-", "").replace(":", "").replace("Z", "").replace("T", "")
    file_name = name or f"pipeline-{stamp[:8]}-{stamp[8:]}.json"
    (directory / file_name).write_text(
        json.dumps(report, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_history(directory: Path, rows: list[dict[str, object]]) -> Path:
    path = directory / "history.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                            for r in rows), encoding="utf-8")
    return path


def _run_cli(pipeline_dir: Path, history: Path, output: Path,
             *extra: str) -> int:
    return pir.main(["--pipeline-dir", str(pipeline_dir), "--history",
                     str(history), "--output-dir", str(output), *extra])


def _report(output: Path) -> dict[str, object]:
    return json.loads((output / JSON_REPORT).read_text(encoding="utf-8"))


def _assert_zero_output(output: Path) -> None:
    assert not output.exists()


def _block_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess is forbidden in this test")

    monkeypatch.setattr(subprocess_module, "run", _forbidden)

    def _no_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("network is forbidden in this test")

    monkeypatch.setattr(socket, "socket", _no_socket)


def _incident_fixture(directory: Path, *, sample_backfilled: bool = True,
                      ) -> tuple[Path, Path]:
    """回放 2026-09-21 事件形态（四份报告 + 对应 history）。

    - 04:30 运行：monitor exit 2 无工件（执行域，槽位真空）
    - 04:45 运行：monitor ok + history 超时（执行域瞬态；样本 04:48:13）
    - 05:00 运行：全链 ok（恢复）
    - history 尾部 warn（状态劣化开放）
    sample_backfilled=False 时 04:48:13 样本尚未入史（未补录形态）。"""
    reports_dir = directory / "pipeline"
    reports_dir.mkdir(parents=True)
    ok_run = _pipeline_report(
        started=datetime(2026, 9, 21, 4, 15, 1, tzinfo=timezone.utc),
        monitor=_monitor_stage("ok", exit_code=0,
                               artifact_stem="monitor-20260921-041501"),
        history=_stage("ok"), insights=_stage("ok"), overall="ok")
    gap_run = _pipeline_report(
        started=datetime(2026, 9, 21, 4, 30, 2, tzinfo=timezone.utc),
        monitor=_monitor_stage("failed", exit_code=2, artifact_stem=None),
        history=_stage("skipped", exit_code=None),
        insights=_stage("skipped", exit_code=None), overall="failed")
    timeout_run = _pipeline_report(
        started=datetime(2026, 9, 21, 4, 45, 25, tzinfo=timezone.utc),
        monitor=_monitor_stage("ok", exit_code=0,
                               artifact_stem="monitor-20260921-044813"),
        history=_stage("timeout", exit_code=None, timed_out=True),
        insights=_stage("skipped", exit_code=None), overall="failed")
    recovered_run = _pipeline_report(
        started=datetime(2026, 9, 21, 5, 0, 1, tzinfo=timezone.utc),
        monitor=_monitor_stage("ok", exit_code=0,
                               artifact_stem="monitor-20260921-050001"),
        history=_stage("ok"), insights=_stage("ok"), overall="ok")
    for report in (ok_run, gap_run, timeout_run, recovered_run):
        _write_report(reports_dir, report)
    rows = [
        _history_row(datetime(2026, 9, 21, 4, 15, 1, tzinfo=timezone.utc)),
        _history_row(datetime(2026, 9, 21, 4, 48, 13, tzinfo=timezone.utc),
                     overall="warn"),
        _history_row(datetime(2026, 9, 21, 5, 0, 1, tzinfo=timezone.utc),
                     overall="warn"),
    ]
    if not sample_backfilled:
        rows = rows[:1]
    history = _write_history(directory, rows)
    return reports_dir, history


# ---------------------------------------------------------------- 结构契约


def test_source_contract_forbidden_tokens() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for token in ("subprocess", "socket", "urllib", "http.client", "os.system",
                  "Popen", "environ", "requests", "urlopen", "getenv", "docker",
                  "datetime.now", "utcnow", "time.time"):
        assert token not in source, f"禁止出现的字面量: {token}"


def test_no_subprocess_no_network_end_to_end(monkeypatch, tmp_path) -> None:
    _block_side_effects(monkeypatch)
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir, history = _incident_fixture(source)
    assert _run_cli(reports_dir, history, output) == pir.EXIT_OPEN


def test_ops_readme_documents_tool() -> None:
    readme = OPS_README.read_text(encoding="utf-8")
    assert "pipeline_incident_review.py" in readme
    assert "M14-79" in readme


# ---------------------------------------------------------------- 事件形态回放


def test_monitor_nonzero_with_artifact_is_status_domain(tmp_path) -> None:
    """monitor 非零但写出样本工件 = 状态域裁决；skipped 不计执行失败。"""
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir = source / "pipeline"
    reports_dir.mkdir(parents=True)
    critical_run = _pipeline_report(
        started=BASE,
        monitor=_monitor_stage("failed", exit_code=2,
                               artifact_stem="monitor-20260921-040001"),
        history=_stage("skipped", exit_code=None),
        insights=_stage("skipped", exit_code=None), overall="failed")
    _write_report(reports_dir, critical_run)
    history = _write_history(source, [
        _history_row(BASE, overall="critical")])
    assert _run_cli(reports_dir, history, output) == pir.EXIT_OPEN
    report = _report(output)
    run = report["runs"][0]
    assert run["failure_domain"] == "status"
    assert run["execution_failure_kinds"] == []
    assert run["monitor_sample_indexed"] is True
    assert run["monitor_sample_status"] == "critical"


def test_monitor_nonzero_without_artifact_is_execution_gap(tmp_path) -> None:
    """monitor 非零且无工件 = 执行域失败，该槽位零样本（12:30 形态）。"""
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir = source / "pipeline"
    reports_dir.mkdir(parents=True)
    gap_run = _pipeline_report(
        started=BASE,
        monitor=_monitor_stage("failed", exit_code=2, artifact_stem=None),
        history=_stage("skipped", exit_code=None),
        insights=_stage("skipped", exit_code=None), overall="failed")
    _write_report(reports_dir, gap_run)
    history = _write_history(source, [_history_row(BASE - timedelta(hours=1))])
    assert _run_cli(reports_dir, history, output) == pir.EXIT_OPEN
    run = _report(output)["runs"][0]
    assert run["failure_domain"] == "execution"
    assert run["execution_failure_kinds"] == ["monitor-no-artifact"]
    assert run["monitor_sample_artifact"] is None
    assert run["monitor_sample_indexed"] is False
    assert run["monitor_sample_status"] is None


def test_history_timeout_backfill_semantics(tmp_path) -> None:
    """monitor ok + history 超时 = 执行域瞬态；样本事后补录是事实陈述，
    超时绝不改记成功（kinds/timeout_stages 恒保留）。"""
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir, history = _incident_fixture(source)
    assert _run_cli(reports_dir, history, output) == pir.EXIT_OPEN
    runs = _report(output)["runs"]
    timeout_run = next(r for r in runs if r["report_name"].endswith("044525"))
    assert timeout_run["failure_domain"] == "execution"
    assert timeout_run["execution_failure_kinds"] == ["history-timeout"]
    assert timeout_run["timeout_stages"] == ["history"]
    assert timeout_run["monitor_sample_artifact"] == "monitor-20260921-044813.json"
    assert timeout_run["monitor_sample_indexed"] is True
    assert timeout_run["monitor_sample_status"] == "warn"


def test_history_timeout_without_backfill_reports_missing_index(tmp_path) -> None:
    """同上但样本尚未补录：indexed=False、status=None——两件事分开陈述。
    （此形态下 history 尾部干净 → all-clear exit 0；分类面不受影响。）"""
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir, history = _incident_fixture(source, sample_backfilled=False)
    assert _run_cli(reports_dir, history, output) == pir.EXIT_OK
    runs = _report(output)["runs"]
    timeout_run = next(r for r in runs if r["report_name"].endswith("044525"))
    assert timeout_run["execution_failure_kinds"] == ["history-timeout"]
    assert timeout_run["monitor_sample_indexed"] is False
    assert timeout_run["monitor_sample_status"] is None


def test_incident_windows_and_recovery(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir, history = _incident_fixture(source)
    assert _run_cli(reports_dir, history, output) == pir.EXIT_OPEN
    report = _report(output)
    incidents = report["incidents"]
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident["kind"] == "execution"  # 成员域 {execution} 恰一类
    assert incident["first_run"].endswith("043002")
    assert incident["last_run"].endswith("044525")
    assert incident["recovered_by"].endswith("050001")
    assert incident["recovered_at"] == "2026-09-21T05:00:01Z"


def test_mixed_domain_incident(tmp_path) -> None:
    """状态域（monitor 裁决）与执行域（无工件）相邻失败 → mixed 窗口。"""
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir = source / "pipeline"
    reports_dir.mkdir(parents=True)
    verdict_run = _pipeline_report(
        started=BASE,
        monitor=_monitor_stage("failed", exit_code=1,
                               artifact_stem="monitor-20260921-040001"),
        history=_stage("skipped", exit_code=None),
        insights=_stage("skipped", exit_code=None), overall="failed")
    gap_run = _pipeline_report(
        started=BASE + timedelta(minutes=15),
        monitor=_monitor_stage("failed", exit_code=2, artifact_stem=None),
        history=_stage("skipped", exit_code=None),
        insights=_stage("skipped", exit_code=None), overall="failed")
    ok_run = _pipeline_report(
        started=BASE + timedelta(minutes=30),
        monitor=_monitor_stage("ok", exit_code=0,
                               artifact_stem="monitor-20260921-043001"),
        history=_stage("ok"), insights=_stage("ok"), overall="ok")
    for report in (verdict_run, gap_run, ok_run):
        _write_report(reports_dir, report)
    history = _write_history(source, [
        _history_row(BASE, overall="warn"),
        _history_row(BASE + timedelta(minutes=30))])
    # 管道已恢复且尾部样本干净 → all-clear；本测试只锁 mixed 事件分类
    assert _run_cli(reports_dir, history, output) == pir.EXIT_OK
    report = _report(output)
    assert report["incidents"][0]["kind"] == "mixed"
    assert len(report["incidents"][0]["run_names"]) == 2


def test_latest_run_failure_stays_open(tmp_path) -> None:
    """最新运行仍失败 → pipeline_execution_state=failing，事件无恢复。"""
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir = source / "pipeline"
    reports_dir.mkdir(parents=True)
    failing = _pipeline_report(
        started=BASE,
        monitor=_monitor_stage("failed", exit_code=2, artifact_stem=None),
        history=_stage("skipped", exit_code=None),
        insights=_stage("skipped", exit_code=None), overall="failed")
    _write_report(reports_dir, failing)
    history = _write_history(source, [_history_row(BASE - timedelta(hours=1))])
    assert _run_cli(reports_dir, history, output) == pir.EXIT_OPEN
    report = _report(output)
    assert report["incidents"][0]["recovered_by"] is None
    assert report["verdict"]["pipeline_execution_state"] == "failing"
    assert "pipeline-latest-run-failed" in report["verdict"]["open_items"]


def test_history_degradation_verdict(tmp_path) -> None:
    """管道恢复但 history 尾部 warn → degraded 开放（真实 13:00 形态）。"""
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir, history = _incident_fixture(source)
    assert _run_cli(reports_dir, history, output) == pir.EXIT_OPEN
    verdict = _report(output)["verdict"]
    assert verdict["pipeline_execution_state"] == "recovered"
    assert verdict["monitoring_status_state"] == "degraded"
    assert verdict["open_items"] == ["history-latest-sample-non-ok"]
    history_block = _report(output)["history"]
    assert history_block["latest_status"] == "warn"
    assert history_block["trailing_consecutive_ok"] == 0


def test_all_clear_exits_zero(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir = source / "pipeline"
    reports_dir.mkdir(parents=True)
    ok_run = _pipeline_report(
        started=BASE,
        monitor=_monitor_stage("ok", exit_code=0,
                               artifact_stem="monitor-20260921-040001"),
        history=_stage("ok"), insights=_stage("ok"), overall="ok")
    _write_report(reports_dir, ok_run)
    history = _write_history(source, [_history_row(BASE)])
    assert _run_cli(reports_dir, history, output) == pir.EXIT_OK
    verdict = _report(output)["verdict"]
    assert verdict["pipeline_execution_state"] == "never-failed"
    assert verdict["monitoring_status_state"] == "clean"
    assert verdict["all_clear"] is True


def test_status_recovered_after_clean_tail(tmp_path) -> None:
    """早前 warn + 尾部干净 ok → monitoring_status_state=recovered。"""
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir = source / "pipeline"
    reports_dir.mkdir(parents=True)
    ok_run = _pipeline_report(
        started=BASE,
        monitor=_monitor_stage("ok", exit_code=0,
                               artifact_stem="monitor-20260921-040001"),
        history=_stage("ok"), insights=_stage("ok"), overall="ok")
    _write_report(reports_dir, ok_run)
    history = _write_history(source, [
        _history_row(BASE - timedelta(minutes=30), overall="warn"),
        _history_row(BASE)])
    assert _run_cli(reports_dir, history, output) == pir.EXIT_OK
    assert _report(output)["verdict"]["monitoring_status_state"] == "recovered"


# ---------------------------------------------------------------- 兼容与切片


def test_pre_m1421_two_step_report_parses(tmp_path) -> None:
    """M14-21 前两步形态（无 insights）合法：缺席步不计失败。"""
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir = source / "pipeline"
    reports_dir.mkdir(parents=True)
    two_step = {"schema_version": 1,
                "tool": "tools/ops/monitoring_pipeline.py",
                "milestone": "M14-14", "mode": "execute",
                "started_at_utc": _iso(BASE), "ended_at_utc": _iso(BASE),
                "config": {},
                "stages": {"monitor": _monitor_stage(
                    "ok", exit_code=0,
                    artifact_stem="monitor-20260921-040001"),
                    "history": _stage("ok")},
                "overall_status": "ok", "lock": None, "boundaries": []}
    _write_report(reports_dir, two_step)
    history = _write_history(source, [_history_row(BASE)])
    assert _run_cli(reports_dir, history, output) == pir.EXIT_OK
    run = _report(output)["runs"][0]
    assert run["failure_domain"] == "none"
    assert run["execution_failure_kinds"] == []


def test_runs_slice_omits_older_with_count(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir = source / "pipeline"
    reports_dir.mkdir(parents=True)
    for index in range(4):
        report = _pipeline_report(
            started=BASE + timedelta(minutes=15 * index),
            monitor=_monitor_stage("ok", exit_code=0,
                                   artifact_stem=f"monitor-20260921-04000{index}"),
            history=_stage("ok"), insights=_stage("ok"), overall="ok")
        _write_report(reports_dir, report)
    history = _write_history(
        source, [_history_row(BASE + timedelta(minutes=45))])
    assert _run_cli(reports_dir, history, output, "--runs", "2") == pir.EXIT_OK
    report = _report(output)
    assert report["inputs"]["execute_report_count"] == 4
    assert report["inputs"]["reviewed_run_count"] == 2
    assert report["inputs"]["run_omitted_older_count"] == 2


# ---------------------------------------------------------------- 零墙钟


def test_outputs_byte_identical_across_reruns(tmp_path) -> None:
    source = tmp_path / "src"
    reports_dir, history = _incident_fixture(source)
    out1, out2 = tmp_path / "out1", tmp_path / "out2"
    assert _run_cli(reports_dir, history, out1) == pir.EXIT_OPEN
    assert _run_cli(reports_dir, history, out2) == pir.EXIT_OPEN
    for name in (JSON_REPORT, MD_REPORT):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes()


def test_generated_at_is_max_of_input_timestamps(tmp_path) -> None:
    """生成锚 = max(最新运行 started_at, 最新样本 collected_at)——零墙钟。"""
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir = source / "pipeline"
    reports_dir.mkdir(parents=True)
    ok_run = _pipeline_report(
        started=BASE,
        monitor=_monitor_stage("ok", exit_code=0,
                               artifact_stem="monitor-20260921-040001"),
        history=_stage("ok"), insights=_stage("ok"), overall="ok")
    _write_report(reports_dir, ok_run)
    later_sample = BASE + timedelta(minutes=10)
    history = _write_history(source, [_history_row(later_sample)])
    assert _run_cli(reports_dir, history, output) == pir.EXIT_OK
    assert _report(output)["generated_at"] == _iso(later_sample)


# ---------------------------------------------------------------- 输入拒绝（零输出）


def test_reject_pipeline_dir_missing(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    history = _write_history(source, [_history_row(BASE)])
    assert _run_cli(tmp_path / "nope", history, output) == pir.EXIT_REFUSED
    _assert_zero_output(output)


def test_reject_unknown_json_stem(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir = source / "pipeline"
    reports_dir.mkdir(parents=True)
    (reports_dir / "other-20260921-040001.json").write_text("{}", encoding="utf-8")
    history = _write_history(source, [_history_row(BASE)])
    assert _run_cli(reports_dir, history, output) == pir.EXIT_REFUSED
    _assert_zero_output(output)


def test_reject_execute_name_with_plan_mode(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir = source / "pipeline"
    reports_dir.mkdir(parents=True)
    plan_body = {"schema_version": 1,
                 "tool": "tools/ops/monitoring_pipeline.py",
                 "mode": "plan", "started_at_utc": _iso(BASE),
                 "overall_status": "planned", "stages": {}}
    (reports_dir / "pipeline-20260921-040001.json").write_text(
        json.dumps(plan_body) + "\n", encoding="utf-8")
    history = _write_history(source, [_history_row(BASE)])
    assert _run_cli(reports_dir, history, output) == pir.EXIT_REFUSED
    _assert_zero_output(output)


def test_plan_reports_counted_not_reviewed(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir = source / "pipeline"
    reports_dir.mkdir(parents=True)
    (reports_dir / "plan-20260921-035901.json").write_text("{}", encoding="utf-8")
    report = _pipeline_report(
        started=BASE,
        monitor=_monitor_stage("ok", exit_code=0,
                               artifact_stem="monitor-20260921-040001"),
        history=_stage("ok"), insights=_stage("ok"), overall="ok")
    _write_report(reports_dir, report)
    history = _write_history(source, [_history_row(BASE)])
    assert _run_cli(reports_dir, history, output) == pir.EXIT_OK
    assert _report(output)["inputs"]["plan_report_count"] == 1


def test_reject_bad_stage_status_vocabulary(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir = source / "pipeline"
    reports_dir.mkdir(parents=True)
    report = _pipeline_report(
        started=BASE,
        monitor=_monitor_stage("crashed", exit_code=None,
                               artifact_stem=None),
        history=_stage("skipped", exit_code=None),
        insights=_stage("skipped", exit_code=None), overall="failed")
    _write_report(reports_dir, report)
    history = _write_history(source, [_history_row(BASE)])
    assert _run_cli(reports_dir, history, output) == pir.EXIT_REFUSED
    _assert_zero_output(output)


def test_reject_monitor_skipped_structural_invariant(tmp_path) -> None:
    """monitor 是首步，skipped 在结构上不可能 → fail-closed。"""
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir = source / "pipeline"
    reports_dir.mkdir(parents=True)
    report = _pipeline_report(
        started=BASE, monitor=_stage("skipped", exit_code=None),
        history=_stage("skipped", exit_code=None),
        insights=_stage("skipped", exit_code=None), overall="failed")
    _write_report(reports_dir, report)
    history = _write_history(source, [_history_row(BASE)])
    assert _run_cli(reports_dir, history, output) == pir.EXIT_REFUSED
    _assert_zero_output(output)


def test_reject_schema_version_drift(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir = source / "pipeline"
    reports_dir.mkdir(parents=True)
    report = _pipeline_report(
        started=BASE,
        monitor=_monitor_stage("ok", exit_code=0,
                               artifact_stem="monitor-20260921-040001"),
        history=_stage("ok"), insights=_stage("ok"), overall="ok")
    report["schema_version"] = 2
    _write_report(reports_dir, report)
    history = _write_history(source, [_history_row(BASE)])
    assert _run_cli(reports_dir, history, output) == pir.EXIT_REFUSED
    _assert_zero_output(output)


def test_reject_duplicate_run_started_at(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir = source / "pipeline"
    reports_dir.mkdir(parents=True)
    report = _pipeline_report(
        started=BASE,
        monitor=_monitor_stage("ok", exit_code=0,
                               artifact_stem="monitor-20260921-040001"),
        history=_stage("ok"), insights=_stage("ok"), overall="ok")
    _write_report(reports_dir, report)
    _write_report(reports_dir, report,
                  name="pipeline-20260921-040002.json")
    history = _write_history(source, [_history_row(BASE)])
    assert _run_cli(reports_dir, history, output) == pir.EXIT_REFUSED
    _assert_zero_output(output)


def test_reject_malformed_history_row(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir, _ = _incident_fixture(source)
    bad = source / "bad.jsonl"
    bad.write_text('{"schema_version": 1}\n', encoding="utf-8")
    assert _run_cli(reports_dir, bad, output) == pir.EXIT_REFUSED
    _assert_zero_output(output)


@pytest.mark.parametrize("rows_text,reason_part", [
    ('{"schema_version": 9, "collected_at": "2026-09-21T04:00:01Z",'
     ' "project": "p", "overall_status": "ok", "partial": false}\n', "schema-version"),
    ('{"schema_version": 1, "collected_at": "2026-09-21T04:00:01Z",'
     ' "project": "p", "overall_status": "maybe", "partial": false}\n', "overall-status"),
    ('{"schema_version": 1, "collected_at": "2026-09-21T04:00:01Z",'
     ' "project": "p", "overall_status": ["ok"], "partial": false}\n', "overall-status"),
    ('not json\n', "not-json"),
    ('', "empty-history"),
])
def test_reject_history_variants(tmp_path, rows_text, reason_part) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir, _ = _incident_fixture(source)
    bad = source / "history.jsonl"
    bad.write_text(rows_text, encoding="utf-8")
    assert _run_cli(reports_dir, bad, output) == pir.EXIT_REFUSED
    _assert_zero_output(output)


def test_reject_history_duplicate_and_non_chronological(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir, _ = _incident_fixture(source)
    duplicate = source / "history.jsonl"
    duplicate.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in
                                 [_history_row(BASE), _history_row(BASE)]),
                         encoding="utf-8")
    assert _run_cli(reports_dir, duplicate, output) == pir.EXIT_REFUSED
    _assert_zero_output(output)
    out2 = tmp_path / "out2"
    backwards = source / "history2.jsonl"
    backwards.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in
                                 [_history_row(BASE),
                                  _history_row(BASE - timedelta(minutes=15))]),
                         encoding="utf-8")
    assert _run_cli(reports_dir, backwards, out2) == pir.EXIT_REFUSED
    _assert_zero_output(out2)


def test_reject_conflicting_project(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir, _ = _incident_fixture(source)
    conflict = source / "history.jsonl"
    other = _history_row(BASE - timedelta(minutes=15))
    other["project"] = "another-project"
    conflict.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in
                                [other, _history_row(BASE)]), encoding="utf-8")
    assert _run_cli(reports_dir, conflict, output) == pir.EXIT_REFUSED
    _assert_zero_output(output)


def test_reject_out_of_range_args(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir, history = _incident_fixture(source)
    assert _run_cli(reports_dir, history, output, "--runs", "0"
                    ) == pir.EXIT_REFUSED
    _assert_zero_output(output)
    assert _run_cli(reports_dir, history, output, "--runs", "5001"
                    ) == pir.EXIT_REFUSED
    assert _run_cli(reports_dir, history, output, "--retention", "0"
                    ) == pir.EXIT_REFUSED


def test_reject_no_execute_reports(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir = source / "pipeline"
    reports_dir.mkdir(parents=True)
    history = _write_history(source, [_history_row(BASE)])
    assert _run_cli(reports_dir, history, output) == pir.EXIT_REFUSED
    _assert_zero_output(output)


# ---------------------------------------------------------------- 隐私与 CLI


def test_history_extra_fields_never_leak(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir = source / "pipeline"
    reports_dir.mkdir(parents=True)
    report = _pipeline_report(
        started=BASE,
        monitor=_monitor_stage("ok", exit_code=0,
                               artifact_stem="monitor-20260921-040001"),
        history=_stage("ok"), insights=_stage("ok"), overall="ok")
    _write_report(reports_dir, report)
    row = _history_row(BASE, overall="warn")
    row["secret_note"] = MARK_TOKEN
    history = _write_history(source, [row])
    assert _run_cli(reports_dir, history, output) == pir.EXIT_OPEN
    for name in (JSON_REPORT, MD_REPORT):
        assert MARK_TOKEN not in (output / name).read_text(encoding="utf-8")


def test_cli_defaults_registered() -> None:
    parser = pir.build_parser()
    args = parser.parse_args([])
    assert args.runs == pir.DEFAULT_RUNS == 500
    assert args.retention == pir.DEFAULT_RETENTION == 500
    assert args.pipeline_dir == pir.DEFAULT_PIPELINE_DIR
    assert args.history == pir.DEFAULT_HISTORY_PATH
    assert args.output_dir == pir.DEFAULT_OUTPUT_DIR


def test_markdown_renders_runs_and_boundaries(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    reports_dir, history = _incident_fixture(source)
    assert _run_cli(reports_dir, history, output) == pir.EXIT_OPEN
    md = (output / MD_REPORT).read_text(encoding="utf-8")
    assert "逐运行归因" in md
    assert "事件窗口与恢复" in md
    assert "production_ready=false" in md
    assert "边界：" in md
    assert "monitor-no-artifact" in md  # 无工件真空槽位如实入表
