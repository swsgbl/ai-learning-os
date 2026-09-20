r"""M14-72 tools/ops/soak_stability_audit.py 契约测试：history.jsonl →
真实连续 24 小时稳定窗口判定（pass/pending/blocked）的安全边界
（零真实容器面/零网络/零计划任务/零 env 读取/零墙钟/零子进程）。

覆盖（全部 I/O 经真实临时目录或 FakeStore 注入；绝不触碰 canonical 仓库
与真实 .verify 目录）：
- 结构契约：源码零子进程/零网络/零 env/零墙钟 token；socket+subprocess
  双阻断下端到端照常成功；
- pass 面：合成 24h/15 分钟节奏干净历史（97 行闭区间，含窗口两端）→
  pass（exit 0）；--history 目录形态与文件形态等价；两次运行输出逐字节
  相同（零墙钟）；
- 闭区间样本数边界（window // interval + 1）：97 行完整节奏 pass；
  96 行即使跨度完整覆盖窗口且全部间隔 ≤ max-gap，仍必须 pending
  （insufficient-sample-count）；
- pending 面：仅 5h 干净历史（24h 要求下）→ pending（exit 1，
  insufficient-clean-coverage）；更早 warn 不改变覆盖不足结论；
- 数据域 blocked 面（**写出报告**，exit 2）：窗口内 warn / critical /
  partial=true / 中段 warn / 间隔超限 → soak-audit-report.json 与 .md
  均落盘，classification=blocked、固定词汇原因、状态计数与非干净计数
  正确，且两次运行逐字节相同；
- 输入拒绝面（**零输出**，exit 2）：时间戳重复 / 全局非时序 /
  conflicting project / malformed JSON 行 / 空文件 / 非法行字段（含
  list/dict 形 overall_status——非哈希值必须受控拒绝而非 TypeError）/
  行数超硬顶 / 输入缺失 / 输入 symlink（真实文件面 + FakeStore）/
  参数越界 / 写失败；
- 隐私：行内多余字段携带标记 token 绝不进入任何输出；
- CLI：默认值注册（窗口 1440/间隔 15/max-gap 20/留存 500）、--help
  可用、ops README 文档化。
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
SCRIPT = REPO_ROOT / "tools" / "ops" / "soak_stability_audit.py"
OPS_README = REPO_ROOT / "tools" / "ops" / "README.md"

#: 标记值（注入行内多余字段，断言绝不进入任何输出）
MARK_TOKEN = "sk-ZXmarker987654321"

PROJECT = "aios-m14-03-production-rehearsal"
FMT = "%Y-%m-%dT%H:%M:%SZ"
ANCHOR = datetime(2026, 9, 20, 2, 15, 2, tzinfo=timezone.utc)

JSON_REPORT = "soak-audit-report.json"
MD_REPORT = "soak-audit-report.md"


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


sa = _load_module(SCRIPT, "soak_stability_audit_under_test")


# ---------------------------------------------------------------- 工厂


def _iso(dt: datetime) -> str:
    return dt.strftime(FMT)


def _row(dt: datetime, *, project: str = PROJECT, overall: str = "ok",
         partial: bool = False, schema_version: int = 1,
         collected_at: str | None = None,
         extra: dict[str, object] | None = None) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": schema_version,
        "collected_at": collected_at if collected_at is not None else _iso(dt),
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


def _rows_closed_interval(anchor: datetime,
                          gap_seconds: list[int]) -> list[dict[str, object]]:
    """自 anchor − sum(gap_seconds) 到 anchor 的闭区间行序列（时序升序）。"""
    start = anchor - timedelta(seconds=sum(gap_seconds))
    times = [start]
    for gap in gap_seconds:
        times.append(times[-1] + timedelta(seconds=gap))
    return [_row(t) for t in times]


def _write_history(directory: Path, rows: list[dict[str, object]],
                   *, name: str = "history.jsonl") -> Path:
    path = directory / name
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")
    return path


def _run_cli(history: Path, output: Path, *extra: str) -> int:
    return sa.main(["--history", str(history), "--output-dir", str(output), *extra])


def _report(output: Path) -> dict[str, object]:
    return json.loads((output / JSON_REPORT).read_text(encoding="utf-8"))


def _assert_zero_output(output: Path) -> None:
    """输入拒绝契约：输出目录未创建、任何输出文件未写出。"""
    assert not output.exists()


# ---------------------------------------------------------------- FakeStore


class FakeStore:
    """伪文件面：basename 键控；可注入 symlink 与写失败。"""

    def __init__(self, *, files: dict[str, bytes] | None = None,
                 dirs: tuple[str, ...] = (), symlinks: tuple[str, ...] = (),
                 fail_write_index: int | None = None) -> None:
        self.files: dict[str, bytes] = dict(files or {})
        self.dirs: set[str] = set(dirs)
        self.symlinks: set[str] = set(symlinks)
        self.fail_write_index = fail_write_index
        self.write_calls: list[str] = []
        self._write_count = 0

    def exists(self, path: Path) -> bool:
        return path.name in self.files or path.name in self.dirs or path.name in self.symlinks

    def is_symlink(self, path: Path) -> bool:
        return path.name in self.symlinks

    def list_dir(self, directory: Path) -> list[str]:
        return sorted(self.files)

    def read_bytes(self, path: Path) -> bytes:
        return self.files[path.name]

    def mkdirs(self, directory: Path) -> None:
        self.dirs.add(directory.name)

    def write_atomic(self, path: Path, text: str) -> None:
        self._write_count += 1
        self.write_calls.append(path.name)
        if self.fail_write_index is not None and self._write_count == self.fail_write_index:
            raise OSError("simulated write failure")
        self.files[path.name] = text.encode("utf-8")


def _fake_run(monkeypatch: pytest.MonkeyPatch, store: FakeStore, history: Path,
              output: Path, *extra: str) -> int:
    monkeypatch.setattr(sa._history, "RealStore", lambda: store)
    return _run_cli(history, output, *extra)


def _block_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess is forbidden in this test")

    monkeypatch.setattr(subprocess_module, "run", _forbidden)

    def _no_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("network is forbidden in this test")

    monkeypatch.setattr(socket, "socket", _no_socket)


def _history_bytes(rows: list[dict[str, object]]) -> bytes:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")


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
    source.mkdir()
    _write_history(source, _rows_back(hours=24))
    assert _run_cli(source / "history.jsonl", output) == sa.EXIT_PASS


# ---------------------------------------------------------------- pass 面


def test_synthetic_24h_pass_15min_cadence(tmp_path) -> None:
    """97 行 = 24h/15m 闭区间最小样本数（含窗口两端）→ pass 上界锁定。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    rows = _rows_back(hours=24)
    assert len(rows) == 97
    _write_history(source, rows)
    rc = _run_cli(source / "history.jsonl", output)
    assert rc == sa.EXIT_PASS
    report = _report(output)
    assert report["classification"] == "pass"
    assert report["reasons"] == []
    assert report["row_count"] == 97
    assert report["selected_row_count"] == 97
    assert report["anchor_collected_at"] == _iso(ANCHOR)
    assert report["window_status_counts"] == {"ok": 97, "warn": 0, "critical": 0}
    assert report["max_observed_gap_minutes"] == 15.0
    assert report["selected_span_minutes"] == 1440.0
    assert (output / MD_REPORT).read_text(encoding="utf-8").count("pass") >= 1


def test_history_directory_form_equivalent(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_history(source, _rows_back(hours=24))
    assert _run_cli(source, output) == sa.EXIT_PASS  # 目录形态
    first = _report(output)
    assert first["classification"] == "pass"


def test_outputs_deterministic_byte_identical(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, _rows_back(hours=24))
    out1, out2 = tmp_path / "out1", tmp_path / "out2"
    assert _run_cli(source, out1) == sa.EXIT_PASS
    assert _run_cli(source, out2) == sa.EXIT_PASS
    for name in (JSON_REPORT, MD_REPORT):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes()


def test_input_sha256_and_byte_size_anchored(tmp_path) -> None:
    import hashlib
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    raw = _history_bytes(_rows_back(hours=24))
    (source / "history.jsonl").write_bytes(raw)
    assert _run_cli(source, output) == sa.EXIT_PASS
    report = _report(output)
    assert report["input"] == {"sha256": hashlib.sha256(raw).hexdigest(),
                               "byte_size": len(raw)}


# ------------------------------------------------- 闭区间样本数边界（97/96）


def test_cadence_boundary_96_spanning_window_pending(tmp_path) -> None:
    """96 行下界锁定：跨度完整覆盖 24h 窗口、全部间隔 ≤ max-gap，
    仍必须 pending（insufficient-sample-count），绝不 pass。"""
    gaps = [909] * 50 + [910] * 45  # 95 个间隔共 86400s = 恰 1440 分钟
    assert len(gaps) == 95 and sum(gaps) == 86400
    rows = _rows_closed_interval(ANCHOR, gaps)
    assert len(rows) == 96
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_history(source, rows)
    rc = _run_cli(source / "history.jsonl", output)
    assert rc == sa.EXIT_PENDING
    report = _report(output)
    assert report["classification"] == "pending"
    assert report["reasons"] == ["insufficient-sample-count"]
    assert report["selected_row_count"] == 96
    assert report["selected_span_minutes"] == 1440.0  # 跨度确已覆盖窗口
    assert report["window_status_counts"] == {"ok": 96, "warn": 0, "critical": 0}
    assert report["max_observed_gap_minutes"] == 15.167  # 全部间隔 ≤ max-gap
    settings = report["settings"]
    minimum = settings["window_minutes"] // settings["expected_interval_minutes"] + 1
    assert minimum == 97  # 闭区间最小样本数：96 < 97 → 绝不 pass


# ---------------------------------------------------------------- pending 面


def test_five_hours_clean_history_must_not_pass(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_history(source, _rows_back(hours=5))  # 21 行干净但覆盖仅 5h
    rc = _run_cli(source / "history.jsonl", output)
    assert rc == sa.EXIT_PENDING
    report = _report(output)
    assert report["classification"] == "pending"
    assert report["reasons"] == ["insufficient-clean-coverage"]
    assert report["selected_row_count"] == 21


def test_pending_history_with_prior_warn_not_pass(tmp_path) -> None:
    """更早 warn + 5h 干净：覆盖不足 → pending（绝非 pass）。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    rows = _rows_back(hours=5)
    rows = [_row(ANCHOR - timedelta(hours=30), overall="warn")] + rows
    _write_history(source, rows)
    assert _run_cli(source / "history.jsonl", output) == sa.EXIT_PENDING
    assert _report(output)["classification"] == "pending"


# ------------------------------------------- 数据域 blocked 面（写出报告）


@pytest.mark.parametrize("make_rows,expected_reasons,expected_counts,expected_non_ok", [
    # 锚点 warn
    (lambda: _rows_back(hours=24)[:-1] + [_row(ANCHOR, overall="warn")],
     ["non-ok-status-in-window"], {"ok": 96, "warn": 1, "critical": 0}, 1),
    # 锚点 critical
    (lambda: _rows_back(hours=24)[:-1] + [_row(ANCHOR, overall="critical")],
     ["non-ok-status-in-window"], {"ok": 96, "warn": 0, "critical": 1}, 1),
    # 锚点 partial=true（overall 仍 ok：状态计数计 ok，非干净计数计 1）
    (lambda: _rows_back(hours=24)[:-1] + [_row(ANCHOR, partial=True)],
     ["non-ok-status-in-window"], {"ok": 97, "warn": 0, "critical": 0}, 1),
    # 窗口中段 warn（非锚点）
    (lambda: _rows_back(hours=24)[:48]
     + [_row(ANCHOR - timedelta(hours=12), overall="warn")]
     + _rows_back(hours=24)[49:],
     ["non-ok-status-in-window"], {"ok": 96, "warn": 1, "critical": 0}, 1),
    # 中段挖洞 75 分钟（> max-gap 20）→ excessive-gap
    (lambda: _rows_back(hours=24)[:48] + _rows_back(hours=24)[52:],
     ["excessive-gap-in-window"], {"ok": 93, "warn": 0, "critical": 0}, 0),
])
def test_data_domain_blocked_writes_reports(tmp_path, make_rows, expected_reasons,
                                            expected_counts,
                                            expected_non_ok) -> None:
    """数据域 blocked = 合法审计结论：确定性 JSON+Markdown 报告必须落盘。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_history(source, make_rows())
    assert _run_cli(source / "history.jsonl", output) == sa.EXIT_REFUSED
    report = _report(output)
    assert report["classification"] == "blocked"
    assert report["reasons"] == expected_reasons
    assert report["window_status_counts"] == expected_counts
    assert report["window_non_ok_count"] == expected_non_ok
    md = (output / MD_REPORT).read_text(encoding="utf-8")
    assert "blocked" in md
    for reason in expected_reasons:
        assert reason in md


def test_data_domain_blocked_outputs_deterministic(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_history(source, _rows_back(hours=24)[:-1] + [_row(ANCHOR, overall="warn")])
    out1, out2 = tmp_path / "out1", tmp_path / "out2"
    assert _run_cli(source / "history.jsonl", out1) == sa.EXIT_REFUSED
    assert _run_cli(source / "history.jsonl", out2) == sa.EXIT_REFUSED
    for name in (JSON_REPORT, MD_REPORT):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes()


def test_data_domain_blocked_writes_both_outputs_via_fake_store(
        monkeypatch, tmp_path) -> None:
    rows = _rows_back(hours=24)[:-1] + [_row(ANCHOR, overall="warn")]
    store = FakeStore(files={"history.jsonl": _history_bytes(rows)}, dirs=("src",))
    rc = _fake_run(monkeypatch, store, tmp_path / "src" / "history.jsonl",
                   tmp_path / "out")
    assert rc == sa.EXIT_REFUSED
    assert store.write_calls == [JSON_REPORT, MD_REPORT]


# ------------------------------------------ 输入拒绝面（零输出，exit 2）


@pytest.mark.parametrize("make_rows", [
    # 时间戳重复
    lambda: _rows_back(hours=24) + [_row(ANCHOR)],
    # 全局非时序（末两行交换）
    lambda: _rows_back(hours=24)[:-2]
     + [_rows_back(hours=24)[-1], _rows_back(hours=24)[-2]],
    # conflicting project
    lambda: _rows_back(hours=24)
     + [_row(ANCHOR + timedelta(minutes=15), project="other-project-xyz")],
])
def test_input_rejection_writes_zero_output(tmp_path, make_rows) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_history(source, make_rows())
    assert _run_cli(source / "history.jsonl", output) == sa.EXIT_REFUSED
    _assert_zero_output(output)


@pytest.mark.parametrize("text", [
    "{not json}\n",
    "\n",
    "",
])
def test_malformed_or_empty_history_zero_output(tmp_path, text) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    (source / "history.jsonl").write_text(text, encoding="utf-8")
    assert _run_cli(source / "history.jsonl", output) == sa.EXIT_REFUSED
    _assert_zero_output(output)


@pytest.mark.parametrize("extra", [
    {"schema_version": 2},
    {"collected_at": "2026-09-20 02:15:02"},       # 非法时间戳格式
    {"collected_at": 12345},
    {"project": "bad project!"},
    {"overall_status": "incomplete"},              # 超词汇
    {"overall_status": ["ok"]},                    # 非哈希 list：受控拒绝
    {"overall_status": {"status": "ok"}},          # 非哈希 dict：受控拒绝
    {"partial": "false"},
])
def test_invalid_row_fields_zero_output(tmp_path, extra) -> None:
    """非法行字段（含非哈希 overall_status）→ 受控拒绝，零输出。

    非 str 的 overall_status 对 frozenset 成员测试会抛 TypeError——
    fail-closed 契约要求先 isinstance 再成员测试，落入固定词汇拒绝。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    rows = _rows_back(hours=5)
    rows.append(_row(ANCHOR + timedelta(minutes=15), extra=extra))
    _write_history(source, rows)
    assert _run_cli(source / "history.jsonl", output) == sa.EXIT_REFUSED
    _assert_zero_output(output)


def test_row_limit_exceeded_zero_output(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    rows = _rows_back(hours=24) * 60  # 远超硬顶 5000
    # 重排时间戳保证合法时序（同内容不同时间戳）
    fixed = []
    for i, row in enumerate(rows):
        fixed.append({**row, "collected_at":
                      _iso(datetime(2026, 1, 1, tzinfo=timezone.utc)
                           + timedelta(minutes=i))})
    _write_history(source, fixed)
    assert _run_cli(source / "history.jsonl", output) == sa.EXIT_REFUSED
    _assert_zero_output(output)


# ---------------------------------------------------------------- 路径防御


def test_history_path_missing_zero_output(tmp_path) -> None:
    output = tmp_path / "out"
    assert _run_cli(tmp_path / "nope", output) == sa.EXIT_REFUSED
    _assert_zero_output(output)


def test_symlinked_history_refused_real_filesystem(tmp_path) -> None:
    real_dir, link_dir, output = tmp_path / "real", tmp_path / "link", tmp_path / "out"
    real_dir.mkdir()
    _write_history(real_dir, _rows_back(hours=24))
    try:
        link_dir.symlink_to(real_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用（权限/平台）")
    assert _run_cli(link_dir, output) == sa.EXIT_REFUSED
    _assert_zero_output(output)


def test_symlinked_history_file_refused_real_filesystem(tmp_path) -> None:
    real_dir, output = tmp_path / "real", tmp_path / "out"
    real_dir.mkdir()
    real = _write_history(real_dir, _rows_back(hours=24))
    link = tmp_path / "history.jsonl"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用（权限/平台）")
    assert _run_cli(link, output) == sa.EXIT_REFUSED
    _assert_zero_output(output)


def test_symlinked_history_refused_via_fake_store(monkeypatch, tmp_path) -> None:
    store = FakeStore(files={"history.jsonl": _history_bytes(_rows_back(hours=24))},
                      dirs=("src", "out"), symlinks=("history.jsonl",))
    rc = _fake_run(monkeypatch, store, tmp_path / "src" / "history.jsonl",
                   tmp_path / "out")
    assert rc == sa.EXIT_REFUSED
    assert store.write_calls == []


def test_history_dir_without_file_blocked_via_fake_store(monkeypatch, tmp_path) -> None:
    store = FakeStore(dirs=("src",))
    rc = _fake_run(monkeypatch, store, tmp_path / "src", tmp_path / "out")
    assert rc == sa.EXIT_REFUSED
    assert store.write_calls == []


# ---------------------------------------------------------------- 隐私与原子性


def test_privacy_marker_excluded_from_outputs(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_history(source, _rows_back(hours=24,
                                      extra={"leaked": MARK_TOKEN}))
    assert _run_cli(source / "history.jsonl", output) == sa.EXIT_PASS
    for name in (JSON_REPORT, MD_REPORT):
        assert MARK_TOKEN not in (output / name).read_text(encoding="utf-8")


def test_malformed_history_writes_nothing_via_fake_store(monkeypatch, tmp_path) -> None:
    store = FakeStore(files={"history.jsonl": b'{"schema_version": 1, "oops": true}\n'})
    rc = _fake_run(monkeypatch, store, tmp_path / "src" / "history.jsonl",
                   tmp_path / "out")
    assert rc == sa.EXIT_REFUSED
    assert store.write_calls == []
    assert "out" not in store.dirs


def test_write_failure_visible_exit_refused(monkeypatch, tmp_path) -> None:
    store = FakeStore(files={"history.jsonl": _history_bytes(_rows_back(hours=24))},
                      dirs=("src", "out"), fail_write_index=1)
    rc = _fake_run(monkeypatch, store, tmp_path / "src" / "history.jsonl",
                   tmp_path / "out")
    assert rc == sa.EXIT_REFUSED
    assert store.write_calls == [JSON_REPORT]


# ---------------------------------------------------------------- CLI


def test_cli_defaults_registered() -> None:
    args = sa.build_parser().parse_args([])
    assert args.history == sa.DEFAULT_HISTORY_PATH
    assert args.output_dir == sa.DEFAULT_OUTPUT_DIR
    assert args.window_minutes == 1440
    assert args.expected_interval_minutes == 15
    assert args.max_gap_minutes == 20
    assert args.retention == 500


def test_cli_help_usable(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        sa.build_parser().parse_args(["--help"])
    assert excinfo.value.code == 0
    assert "soak" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [
    ("--window-minutes", "0"),
    ("--expected-interval-minutes", "0"),
    ("--max-gap-minutes", "10"),              # < 期望间隔 15
    ("--retention", "0"),
    ("--retention", "5001"),
    ("--max-rows", "5001"),
])
def test_cli_invalid_settings_refused(tmp_path, argv) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_history(source, _rows_back(hours=24))
    assert _run_cli(source / "history.jsonl", output, *argv) == sa.EXIT_REFUSED
    _assert_zero_output(output)


def test_cli_stdout_summary_lines(tmp_path, capsys) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_history(source, _rows_back(hours=24))
    assert _run_cli(source / "history.jsonl", output) == sa.EXIT_PASS
    out = capsys.readouterr().out
    assert "分类: pass" in out
    assert "锚点" in out


def test_retention_omits_older_rows(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    rows = _rows_back(hours=24)
    _write_history(source, rows)
    assert _run_cli(source / "history.jsonl", output,
                    "--retention", str(len(rows) - 10)) == sa.EXIT_PENDING
    report = _report(output)
    assert report["analyzed_row_count"] == len(rows) - 10
    assert report["omitted_older_count"] == 10


# ---------------------------------------------------------------- 文档契约


def test_ops_readme_documents_tool() -> None:
    text = OPS_README.read_text(encoding="utf-8")
    assert "soak_stability_audit.py" in text
    assert "M14-72" in text
