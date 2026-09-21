r"""M14-79 tools/ops/soak_window_gate.py 契约测试：长稳窗口锚定/重启门
——尾部连续 ok 前置满足才允许锚定全新 24h soak 窗口；warn/critical/partial
残留即拒绝并逐条说明；零子进程/零网络/零计划任务/零 env 读取/零墙钟。

覆盖（全部 I/O 经真实临时目录；绝不触碰 canonical 仓库与真实 .verify）：
- 结构契约：源码零子进程/零网络/零 env/零墙钟 token（含 perf_counter）；
  socket+subprocess 双阻断下端到端照常成功；ops README 文档化；
- 门 open 面：N 连续 ok（15 分钟节奏）→ open exit 0；检查模式只落门报告
  （锚定记录零写出）；--anchor 才写锚定记录（锚点=最新样本、最早可判定
  时间=锚点+窗口、前置指纹、固定审计指引）；N=1 最小门；
- 拒绝面（closed，exit 1，门报告落盘、锚定记录零写出、逐条说明）：
  尾部 warn / critical / partial=true 残留（清单逐条列出 collected_at +
  状态）；尾部间隔超 max-gap；行数不足 N（insufficient-history）；
- 重启保护：锚定记录已存在 → anchor-exists 拒绝 exit 2（零写出）；
- 零墙钟：两次运行输出逐字节相同；锚定记录 generated_at == 锚样本
  collected_at（绝无系统钟）；
- 输入拒绝面（零输出 exit 2）：路径缺失/symlink（真实文件面）、
  malformed 行/非法字段（含 list 形 overall_status）、重复/非时序时间戳、
  项目冲突、空文件、行数超硬顶、consecutive-ok 超 retention、参数越界；
- 隐私：行内多余字段携带标记 token 绝不进入任何输出；
- CLI：默认值注册（8/20/1440/500）、--history 目录形态等价。
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
SCRIPT = REPO_ROOT / "tools" / "ops" / "soak_window_gate.py"
OPS_README = REPO_ROOT / "tools" / "ops" / "README.md"

#: 标记值（注入行内多余字段，断言绝不进入任何输出）
MARK_TOKEN = "sk-ZXmarker987654321"

PROJECT = "aios-m14-03-production-rehearsal"
FMT = "%Y-%m-%dT%H:%M:%SZ"
ANCHOR = datetime(2026, 9, 21, 6, 0, 1, tzinfo=timezone.utc)

GATE_JSON = "soak-window-gate.json"
GATE_MD = "soak-window-gate.md"
ANCHOR_JSON = "soak-window-anchor.json"
ANCHOR_MD = "soak-window-anchor.md"


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


swg = _load_module(SCRIPT, "soak_window_gate_under_test")


# ---------------------------------------------------------------- 工厂


def _iso(dt: datetime) -> str:
    return dt.strftime(FMT)


def _row(dt: datetime, *, project: str = PROJECT, overall: str = "ok",
         partial: bool = False, schema_version: int = 1,
         extra: dict[str, object] | None = None) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": schema_version,
        "collected_at": _iso(dt),
        "project": project,
        "overall_status": overall,
        "partial": partial,
    }
    if extra is not None:
        row.update(extra)
    return row


def _rows_back(*, hours: float, interval_minutes: int = 15,
               anchor: datetime = ANCHOR, **kwargs) -> list[dict[str, object]]:
    """自锚回溯 hours 小时、interval 分钟节奏的行序列（含锚，含起点）。"""
    count = int(hours * 60 / interval_minutes) + 1
    return [_row(anchor - timedelta(minutes=interval_minutes * i), **kwargs)
            for i in reversed(range(count))]


def _write_history(directory: Path, rows: list[dict[str, object]],
                   *, name: str = "history.jsonl") -> Path:
    path = directory / name
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")
    return path


def _run_cli(history: Path, output: Path, *extra: str) -> int:
    return swg.main(["--history", str(history), "--output-dir", str(output),
                     *extra])


def _gate_report(output: Path) -> dict[str, object]:
    return json.loads((output / GATE_JSON).read_text(encoding="utf-8"))


def _anchor_record(output: Path) -> dict[str, object]:
    return json.loads((output / ANCHOR_JSON).read_text(encoding="utf-8"))


def _assert_zero_output(output: Path) -> None:
    assert not output.exists()


def _block_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess is forbidden in this test")

    monkeypatch.setattr(subprocess_module, "run", _forbidden)

    def _no_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("network is forbidden in this test")

    monkeypatch.setattr(socket, "socket", _no_socket)


# ---------------------------------------------------------------- 结构契约


def test_source_contract_forbidden_tokens() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for token in ("subprocess", "socket", "urllib", "http.client", "os.system",
                  "Popen", "environ", "requests", "urlopen", "getenv", "docker",
                  "datetime.now", "utcnow", "time.time", "perf_counter"):
        assert token not in source, f"禁止出现的字面量: {token}"


def test_no_subprocess_no_network_end_to_end(monkeypatch, tmp_path) -> None:
    _block_side_effects(monkeypatch)
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_history(source, _rows_back(hours=2))
    assert _run_cli(source / "history.jsonl", output) == swg.EXIT_OPEN


def test_ops_readme_documents_tool() -> None:
    readme = OPS_README.read_text(encoding="utf-8")
    assert "soak_window_gate.py" in readme
    assert "M14-79" in readme


# ---------------------------------------------------------------- 门 open 面


def test_clean_tail_opens_gate_check_mode_only(tmp_path) -> None:
    """8 连续 ok（15 分钟节奏 = 2h 干净尾部）→ open；检查模式只落门报告。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_history(source, _rows_back(hours=2))
    assert _run_cli(source / "history.jsonl", output) == swg.EXIT_OPEN
    report = _gate_report(output)
    assert report["status"] == "open"
    assert report["reasons"] == []
    assert report["anchor_collected_at"] == _iso(ANCHOR)
    assert report["tail_sample_count"] == 8
    assert report["tail_non_ok_samples"] == []
    assert report["tail_max_gap_minutes"] == 15.0
    assert report["earliest_audit_collected_at"] == _iso(
        ANCHOR + timedelta(minutes=1440))
    assert (output / GATE_MD).exists()
    assert not (output / ANCHOR_JSON).exists()
    assert not (output / ANCHOR_MD).exists()


def test_anchor_mode_writes_anchor_record(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_history(source, _rows_back(hours=2))
    assert _run_cli(source / "history.jsonl", output, "--anchor"
                    ) == swg.EXIT_OPEN
    anchor = _anchor_record(output)
    assert anchor["schema_version"] == swg.ANCHOR_SCHEMA_VERSION
    assert anchor["tool"] == "tools/ops/soak_window_gate.py"
    assert anchor["anchor_collected_at"] == _iso(ANCHOR)
    assert anchor["window_minutes"] == 1440
    assert anchor["earliest_audit_collected_at"] == _iso(
        ANCHOR + timedelta(minutes=1440))
    assert anchor["precondition"]["consecutive_ok"] == 8
    assert anchor["precondition"]["tail_sample_count"] == 8
    assert anchor["precondition"]["tail_non_ok_count"] == 0
    assert anchor["follow_up_audit_tool"] == "tools/ops/soak_stability_audit.py"
    assert anchor["generated_at"] == _iso(ANCHOR)  # 零墙钟：生成锚=锚样本
    md = (output / ANCHOR_MD).read_text(encoding="utf-8")
    assert "锚定 ≠ soak 通过" in md
    assert "production_ready=false" in md


def test_minimal_gate_n1_single_ok_row(tmp_path) -> None:
    """N=1 最小门：单行 ok 即 open（边界下限）。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_history(source, [_row(ANCHOR)])
    assert _run_cli(source / "history.jsonl", output, "--consecutive-ok", "1"
                    ) == swg.EXIT_OPEN
    report = _gate_report(output)
    assert report["status"] == "open"
    assert report["tail_sample_count"] == 1


def test_earlier_non_ok_outside_tail_does_not_block(tmp_path) -> None:
    """尾部 N 行之前的 warn 不阻断门（未来审计窗口自锚点起算）。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    rows = _rows_back(hours=4)
    rows[0]["overall_status"] = "warn"  # 最早一行（尾部 8 行之外）
    _write_history(source, rows)
    assert _run_cli(source / "history.jsonl", output) == swg.EXIT_OPEN
    assert _gate_report(output)["status"] == "open"


# ---------------------------------------------------------------- 拒绝面（closed）


def test_refuse_on_warn_tail_with_enumeration(tmp_path) -> None:
    """尾部残留 warn → closed + 逐条清单（时间戳/状态/partial）。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    rows = _rows_back(hours=2)
    rows[-1]["overall_status"] = "warn"   # 最新样本
    rows[-3]["overall_status"] = "critical"
    _write_history(source, rows)
    assert _run_cli(source / "history.jsonl", output) == swg.EXIT_CLOSED
    report = _gate_report(output)
    assert report["status"] == "closed"
    assert report["reasons"] == ["non-ok-in-tail"]
    listed = report["tail_non_ok_samples"]
    assert len(listed) == 2
    assert {s["overall_status"] for s in listed} == {"warn", "critical"}
    assert all(s["partial"] is False for s in listed)
    stamps = {s["collected_at"] for s in listed}
    assert stamps == {_iso(ANCHOR), _iso(ANCHOR - timedelta(minutes=30))}
    md = (output / GATE_MD).read_text(encoding="utf-8")
    assert _iso(ANCHOR) in md and "critical" in md  # 逐条说明进入 Markdown
    assert "拒绝即因此" in md


def test_refuse_on_partial_true_tail(tmp_path) -> None:
    """partial=true 的 ok 样本同样算非干净 → closed。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    rows = _rows_back(hours=2)
    rows[-2]["partial"] = True
    _write_history(source, rows)
    assert _run_cli(source / "history.jsonl", output) == swg.EXIT_CLOSED
    report = _gate_report(output)
    assert report["reasons"] == ["non-ok-in-tail"]
    assert report["tail_non_ok_samples"][0]["partial"] is True


def test_refuse_on_excessive_gap_in_tail(tmp_path) -> None:
    """尾部相邻间隔超 max-gap → closed（附最大观测间隔）。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    rows = [_row(ANCHOR - timedelta(minutes=75)),
            _row(ANCHOR - timedelta(minutes=60)),
            _row(ANCHOR - timedelta(minutes=30)),  # 30 分钟缺口 > 20
            _row(ANCHOR - timedelta(minutes=15)),
            _row(ANCHOR)]
    _write_history(source, rows)
    assert _run_cli(source / "history.jsonl", output, "--consecutive-ok", "5"
                    ) == swg.EXIT_CLOSED
    report = _gate_report(output)
    assert report["reasons"] == ["excessive-gap-in-tail"]
    assert report["tail_max_gap_minutes"] == 30.0


def test_refuse_on_insufficient_history(tmp_path) -> None:
    """总行数 < N → closed（insufficient-history）。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_history(source, _rows_back(hours=1))  # 5 行 < 默认 8
    assert _run_cli(source / "history.jsonl", output) == swg.EXIT_CLOSED
    report = _gate_report(output)
    assert report["reasons"] == ["insufficient-history"]
    assert report["tail_sample_count"] == 5


def test_closed_gate_with_anchor_writes_no_anchor(tmp_path) -> None:
    """--anchor 但门 closed：只落门报告，锚定记录零写出，exit 1。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    rows = _rows_back(hours=2)
    rows[-1]["overall_status"] = "warn"
    _write_history(source, rows)
    assert _run_cli(source / "history.jsonl", output, "--anchor"
                    ) == swg.EXIT_CLOSED
    assert (output / GATE_JSON).exists()
    assert not (output / ANCHOR_JSON).exists()
    assert not (output / ANCHOR_MD).exists()


def test_reanchor_refused_while_anchor_exists(tmp_path) -> None:
    """重启保护：锚定记录已存在 → anchor-exists 拒绝 exit 2（零写出）。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_history(source, _rows_back(hours=2))
    assert _run_cli(source / "history.jsonl", output, "--anchor"
                    ) == swg.EXIT_OPEN
    assert (output / ANCHOR_JSON).exists()
    source2, output2 = tmp_path / "src2", tmp_path / "out2"
    source2.mkdir()
    _write_history(source2, _rows_back(hours=2))
    assert _run_cli(source2 / "history.jsonl", output2, "--anchor"
                    ) == swg.EXIT_OPEN  # 不同目录不受影响
    # 同目录重锚（历史更新后）→ 拒绝
    rows = _rows_back(hours=2, anchor=ANCHOR + timedelta(minutes=15))
    _write_history(source, rows)
    assert _run_cli(source / "history.jsonl", output, "--anchor"
                    ) == swg.EXIT_REFUSED
    # 原锚定记录原样保留（未被覆盖/删除）
    assert _anchor_record(output)["anchor_collected_at"] == _iso(ANCHOR)


# ---------------------------------------------------------------- 零墙钟


def test_outputs_byte_identical_across_reruns(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    rows = _rows_back(hours=2)
    rows[-1]["overall_status"] = "warn"
    _write_history(source, rows)
    out1, out2 = tmp_path / "out1", tmp_path / "out2"
    assert _run_cli(source / "history.jsonl", out1) == swg.EXIT_CLOSED
    assert _run_cli(source / "history.jsonl", out2) == swg.EXIT_CLOSED
    for name in (GATE_JSON, GATE_MD):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes()


def test_no_clock_fakery_anchor_from_sample_only(tmp_path) -> None:
    """锚点/生成锚/最早可判定时间全部取自样本——绝无系统钟参与。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_history(source, _rows_back(hours=2))
    assert _run_cli(source / "history.jsonl", output, "--anchor"
                    ) == swg.EXIT_OPEN
    anchor = _anchor_record(output)
    assert anchor["anchor_collected_at"] == _iso(ANCHOR)
    assert anchor["generated_at"] == _iso(ANCHOR)
    assert anchor["earliest_audit_collected_at"] == _iso(
        ANCHOR + timedelta(minutes=1440))


# ---------------------------------------------------------------- 输入拒绝（零输出）


def test_reject_history_path_missing(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    assert _run_cli(source / "history.jsonl", output) == swg.EXIT_REFUSED
    _assert_zero_output(output)


def test_reject_input_symlink_real_fs(tmp_path) -> None:
    """真实文件面 symlink 拒绝（与 soak 审计同款：不可用平台即 skip）。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    real = _write_history(source, _rows_back(hours=2))
    link = tmp_path / "history.jsonl"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用（权限/平台）")
    assert _run_cli(link, output) == swg.EXIT_REFUSED
    _assert_zero_output(output)


@pytest.mark.parametrize("rows,reason_part", [
    ([_row(ANCHOR, schema_version=9)], "schema-version"),
    ([_row(ANCHOR, overall="maybe")], "overall-status"),
    ([{"schema_version": 1, "collected_at": _iso(ANCHOR), "project": PROJECT,
       "overall_status": ["ok"], "partial": False}], "overall-status"),
    ([_row(ANCHOR), _row(ANCHOR)], "duplicate-timestamp"),
    ([_row(ANCHOR), _row(ANCHOR - timedelta(minutes=15))],
     "non-chronological"),
    ([_row(ANCHOR - timedelta(minutes=15), project="another-project"),
      _row(ANCHOR)], "conflicting-project"),
    ([], "empty-history"),
])
def test_reject_history_variants(tmp_path, rows, reason_part) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    history = _write_history(source, rows)
    rc = _run_cli(history, output)
    assert rc == swg.EXIT_REFUSED
    assert reason_part
    _assert_zero_output(output)


def test_reject_row_limit_exceeded(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    rows = [_row(ANCHOR - timedelta(minutes=i)) for i in range(5001)]
    rows.reverse()
    history = _write_history(source, rows)
    assert _run_cli(history, output) == swg.EXIT_REFUSED
    _assert_zero_output(output)


def test_reject_malformed_json_line(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    history = source / "history.jsonl"
    history.write_text("not json\n", encoding="utf-8")
    assert _run_cli(history, output) == swg.EXIT_REFUSED
    _assert_zero_output(output)


def test_reject_out_of_range_args(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    history = _write_history(source, _rows_back(hours=2))
    assert _run_cli(history, output, "--consecutive-ok", "0"
                    ) == swg.EXIT_REFUSED
    _assert_zero_output(output)
    assert _run_cli(history, output, "--consecutive-ok", "5001"
                    ) == swg.EXIT_REFUSED
    assert _run_cli(history, output, "--max-gap-minutes", "0"
                    ) == swg.EXIT_REFUSED
    assert _run_cli(history, output, "--window-minutes", "0"
                    ) == swg.EXIT_REFUSED
    assert _run_cli(history, output, "--retention", "0"
                    ) == swg.EXIT_REFUSED


def test_reject_consecutive_ok_above_retention(tmp_path) -> None:
    """consecutive-ok 不得超过 retention——尾部 N 行必须落在分析留存内。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    history = _write_history(source, _rows_back(hours=2))
    assert _run_cli(history, output, "--consecutive-ok", "9",
                    "--retention", "8") == swg.EXIT_REFUSED
    _assert_zero_output(output)


# ---------------------------------------------------------------- 兼容与隐私


def test_history_directory_form_equivalent(tmp_path) -> None:
    source, out_file, out_dir = tmp_path / "src", tmp_path / "o1", tmp_path / "o2"
    source.mkdir()
    _write_history(source, _rows_back(hours=2))
    assert _run_cli(source / "history.jsonl", out_file) == swg.EXIT_OPEN
    assert _run_cli(source, out_dir) == swg.EXIT_OPEN  # 目录形态
    assert (_gate_report(out_file)["anchor_collected_at"]
            == _gate_report(out_dir)["anchor_collected_at"])


def test_history_extra_fields_never_leak(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    rows = _rows_back(hours=2)
    rows[-1]["overall_status"] = "warn"
    rows[-1]["secret_note"] = MARK_TOKEN
    history = _write_history(source, rows)
    assert _run_cli(history, output) == swg.EXIT_CLOSED
    for name in (GATE_JSON, GATE_MD):
        assert MARK_TOKEN not in (output / name).read_text(encoding="utf-8")


def test_cli_defaults_registered() -> None:
    parser = swg.build_parser()
    args = parser.parse_args([])
    assert args.consecutive_ok == swg.DEFAULT_CONSECUTIVE_OK == 8
    assert args.max_gap_minutes == swg.DEFAULT_MAX_GAP_MINUTES == 20
    assert args.window_minutes == swg.DEFAULT_WINDOW_MINUTES == 1440
    assert args.retention == swg.DEFAULT_RETENTION == 500
    assert args.history == swg.DEFAULT_HISTORY_PATH
    assert args.output_dir == swg.DEFAULT_OUTPUT_DIR
    assert args.anchor is False
