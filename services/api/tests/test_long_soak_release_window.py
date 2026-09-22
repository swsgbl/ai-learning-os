r"""M14-93 tools/ops/long_soak_release_window.py 契约测试：长稳到期
审计/导出 runner——M14-79 权威锚定窗口到点前固定词汇 not-due 拒绝
（零审计零证据）；到点后复用 soak_stability_audit 语义审计；pending
拒绝导出；pass/blocked 才把 soak-audit-report.json 逐字节复制为
evidence/long-soak.json 并登记哈希；绝不改写/重序列化门证据、绝不
伪造 pass。

覆盖（全部 I/O 经真实临时目录；绝不触碰 canonical 仓库与真实 .verify；
锚定记录由**真实 soak_window_gate --anchor** 在合成历史上产出——测试
锚定记录的唯一样本来源即产锚工具本身）：
- 结构契约：源码零子进程/零网络/零 env/零墙钟 token（含 perf_counter）；
  socket+subprocess 双阻断下端到端照常成功；ops README 文档化；
- not-due：最新样本早于 earliest_audit_collected_at → exit 1、runner
  报告固定词汇 window-not-due、零审计（无 soak-audit-report.json）、
  零证据（无 evidence/long-soak.json）；
- due-pass（恰在到期边界：最新样本 == earliest）：exit 0、审计报告
  落盘、evidence/long-soak.json 与 soak-audit-report.json **逐字节
  相同**、runner 报告登记双哈希相等、键集与 release-readiness
  long-soak 门接受的 v2 报告键集一致；
- due-blocked：窗口内 warn → exit 3、blocked 证据照常逐字节导出
  （诚实 blocked 结论非崩溃）；
- due-pending：干净覆盖不足 → exit 2、审计报告落盘、证据导出拒绝
  （固定词汇 evidence-export-refused-pending + 审计原因）；
- 锚定记录严格校验（零输出 exit 4）：schema_version/tool/follow_up_
  audit_tool 错位、window_minutes 漂移、earliest_audit 被手改提前、
  generated_at 偏移、precondition 各字段（含 tail_non_ok_count>0 篡改、
  尾计数不等于 consecutive_ok、坏 hex sha）、boundaries 篡改、
  键集多余/缺失、非 JSON、非对象、路径缺失、symlink；
- 历史拒绝面（零输出 exit 4）：malformed 行/非法字段/重复/非时序/
  项目冲突/空文件/行数超硬顶/路径缺失；最新样本早于锚点
  （history-before-anchor）；
- 输出目录护栏：已存在拒绝；dangling symlink 拒绝；
- 哈希/篡改防御：注入写损坏 Store（evidence 写入翻转字节）→
  read-back 复核拒绝并移除坏副本、runner 报告零写出；
- 零墙钟：同输入两次运行（各自全新输出目录）runner JSON 逐字节相同；
  generated_at == 最新样本 collected_at（绝无系统钟）；
- 隐私：行内多余字段携带标记 token 绝不进入任何输出；
- CLI：默认值注册（canonical gitignored 三路径）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import socket
import subprocess as subprocess_module
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.ops.release_readiness import SOAK_REPORT_KEYS

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE_SCRIPT = REPO_ROOT / "tools" / "ops" / "soak_window_gate.py"
RUNNER_SCRIPT = REPO_ROOT / "tools" / "ops" / "long_soak_release_window.py"
OPS_README = REPO_ROOT / "tools" / "ops" / "README.md"

#: 标记值（注入行内多余字段，断言绝不进入任何输出）
MARK_TOKEN = "sk-ZXmarker987654321"

PROJECT = "aios-m14-03-production-rehearsal"
FMT = "%Y-%m-%dT%H:%M:%SZ"
#: 锚点 = M14-79 门产锚时刻（合成）；earliest = ANCHOR + 24h
ANCHOR = datetime(2026, 9, 21, 15, 0, 1, tzinfo=timezone.utc)

GATE_ANCHOR_JSON = "soak-window-anchor.json"
RUNNER_JSON = "long-soak-window-runner.json"
RUNNER_MD = "long-soak-window-runner.md"
AUDIT_JSON = "soak-audit-report.json"
EVIDENCE_REL = "evidence/long-soak.json"


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


swg = _load_module(GATE_SCRIPT, "soak_window_gate_fixture")
rnr = _load_module(RUNNER_SCRIPT, "long_soak_release_window_under_test")


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


def _range_rows(start: datetime, end: datetime, *,
                interval_minutes: int = 15,
                **kwargs) -> list[dict[str, object]]:
    """[start, end] 闭区间、interval 分钟节奏的升序行序列（含两端）。"""
    count = int((end - start).total_seconds() / 60 / interval_minutes) + 1
    return [_row(start + timedelta(minutes=interval_minutes * i), **kwargs)
            for i in range(count)]


def _write_history(directory: Path, rows: list[dict[str, object]],
                   *, name: str = "history.jsonl") -> Path:
    path = directory / name
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")
    return path


def _make_genuine_anchor(tmp_path: Path) -> tuple[Path, Path]:
    """用真实 soak_window_gate --anchor 在合成干净历史上产出锚定记录。

    返回（锚定记录 json 路径, 产锚历史目录）。产锚历史 = 锚点前 8 连续
    ok（15 分钟节奏 = 2 小时干净尾部）——门 open 的最小真实形态。"""
    src = tmp_path / "gate-src"
    src.mkdir()
    pre_rows = _range_rows(ANCHOR - timedelta(minutes=105), ANCHOR)
    assert len(pre_rows) == 8
    _write_history(src, pre_rows)
    gate_out = tmp_path / "gate-out"
    code = swg.main(["--history", str(src), "--output-dir", str(gate_out),
                     "--anchor"])
    assert code == swg.EXIT_OPEN
    anchor_path = gate_out / GATE_ANCHOR_JSON
    assert anchor_path.is_file()
    return anchor_path, src


def _pre_minus_due_rows() -> list[dict[str, object]]:
    """产锚历史 + 追加到锚点后 23h（最新样本 < earliest → not-due）。"""
    return (_range_rows(ANCHOR - timedelta(minutes=105), ANCHOR)
            + _range_rows(ANCHOR + timedelta(minutes=15),
                          ANCHOR + timedelta(hours=23)))


def _pre_plus_window_rows(**kwargs) -> list[dict[str, object]]:
    """产锚历史 + 完整 24h 干净窗口（最新样本 == earliest → 恰在到期
    边界）。单行kwargs 可注入 warn 等变体由调用方自行改写。"""
    rows = _range_rows(ANCHOR - timedelta(minutes=105),
                       ANCHOR + timedelta(minutes=1440), **kwargs)
    return rows


def _pending_rows() -> list[dict[str, object]]:
    """锚点后 2h 起至 earliest+15m 的干净历史（留存轮换叙事：不含锚前
    样本合法）——审计窗口起点（latest-1440 = 锚+15m）早于首行 →
    insufficient-clean-coverage → pending。"""
    return _range_rows(ANCHOR + timedelta(hours=2),
                       ANCHOR + timedelta(minutes=1440 + 15))


def _run_runner(anchor: Path, history_dir: Path, output: Path,
                *extra: str) -> int:
    return rnr.main(["--anchor", str(anchor), "--history", str(history_dir),
                     "--output-dir", str(output), *extra])


def _runner_report(output: Path) -> dict[str, object]:
    return json.loads((output / RUNNER_JSON).read_text(encoding="utf-8"))


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
    source = RUNNER_SCRIPT.read_text(encoding="utf-8")
    for token in ("subprocess", "socket", "urllib", "http.client", "os.system",
                  "Popen", "environ", "requests", "urlopen", "getenv", "docker",
                  "datetime.now", "utcnow", "time.time", "perf_counter"):
        assert token not in source, f"禁止出现的字面量: {token}"


def test_no_subprocess_no_network_end_to_end(monkeypatch, tmp_path) -> None:
    _block_side_effects(monkeypatch)
    anchor, _src = _make_genuine_anchor(tmp_path)
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    _write_history(hist_dir, _pre_minus_due_rows())
    output = tmp_path / "out"
    assert _run_runner(anchor, hist_dir, output) == rnr.EXIT_NOT_DUE


def test_ops_readme_documents_tool() -> None:
    readme = OPS_README.read_text(encoding="utf-8")
    assert "long_soak_release_window.py" in readme
    assert "M14-93" in readme


# ---------------------------------------------------------------- not-due 面


def test_not_due_refuses_without_audit_or_evidence(tmp_path) -> None:
    """最新样本早于 earliest → exit 1；runner 报告固定词汇；零审计零证据。"""
    anchor, _src = _make_genuine_anchor(tmp_path)
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    _write_history(hist_dir, _pre_minus_due_rows())
    output = tmp_path / "out"
    assert _run_runner(anchor, hist_dir, output) == rnr.EXIT_NOT_DUE
    report = _runner_report(output)
    assert report["status"] == "not-due"
    assert report["reasons"] == ["window-not-due"]
    assert report["audit"] is None
    assert report["latest_sample_collected_at"] == _iso(
        ANCHOR + timedelta(hours=23))
    assert not (output / AUDIT_JSON).exists()
    assert not (output / EVIDENCE_REL).exists()


# ---------------------------------------------------------------- 到期面


def test_due_pass_exports_byte_identical_evidence(tmp_path) -> None:
    """最新样本 == earliest（恰在到期边界）→ 审计 pass → 证据逐字节导出。"""
    anchor, _src = _make_genuine_anchor(tmp_path)
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    _write_history(hist_dir, _pre_plus_window_rows())
    output = tmp_path / "out"
    assert _run_runner(anchor, hist_dir, output) == rnr.EXIT_EXPORTED_PASS
    report = _runner_report(output)
    assert report["status"] == "pass-exported"
    assert report["reasons"] == []
    audit_block = report["audit"]
    assert isinstance(audit_block, dict)
    assert audit_block["classification"] == "pass"
    source_bytes = (output / AUDIT_JSON).read_bytes()
    evidence_bytes = (output / EVIDENCE_REL).read_bytes()
    #: 逐字节相同——绝不改写/重序列化门证据
    assert evidence_bytes == source_bytes
    source_meta = audit_block["soak_audit_report"]
    evidence_meta = audit_block["long_soak_evidence"]
    assert isinstance(source_meta, dict) and isinstance(evidence_meta, dict)
    assert source_meta["sha256"] == evidence_meta["sha256"]
    assert source_meta["byte_size"] == evidence_meta["byte_size"] == len(
        evidence_bytes)
    assert evidence_meta["sha256"] == hashlib.sha256(evidence_bytes).hexdigest()
    #: 导出的证据就是 release-readiness long-soak 门接受的 v2 报告键集
    evidence_obj = json.loads(evidence_bytes)
    assert set(evidence_obj) == set(SOAK_REPORT_KEYS)
    assert evidence_obj["classification"] == "pass"
    assert evidence_obj["gate"] == "long-soak"


def test_due_blocked_exports_evidence(tmp_path) -> None:
    """窗口内 warn → 审计 blocked → exit 3、blocked 证据照常逐字节导出。"""
    anchor, _src = _make_genuine_anchor(tmp_path)
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    rows = _pre_plus_window_rows()
    warned = False
    for row in rows:
        if (row["collected_at"] == _iso(ANCHOR + timedelta(hours=12))
                and not warned):
            row["overall_status"] = "warn"
            warned = True
    assert warned
    _write_history(hist_dir, rows)
    output = tmp_path / "out"
    assert _run_runner(anchor, hist_dir, output) == rnr.EXIT_BLOCKED_EXPORTED
    report = _runner_report(output)
    assert report["status"] == "blocked-exported"
    audit_block = report["audit"]
    assert isinstance(audit_block, dict)
    assert audit_block["classification"] == "blocked"
    assert "non-ok-status-in-window" in audit_block["reasons"]
    assert (output / EVIDENCE_REL).read_bytes() == (
        output / AUDIT_JSON).read_bytes()
    evidence_obj = json.loads((output / EVIDENCE_REL).read_text(encoding="utf-8"))
    assert evidence_obj["classification"] == "blocked"


def test_due_pending_refuses_export(tmp_path) -> None:
    """干净覆盖不足 → 审计 pending → exit 2、审计报告落盘、证据导出拒绝。"""
    anchor, _src = _make_genuine_anchor(tmp_path)
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    _write_history(hist_dir, _pending_rows())
    output = tmp_path / "out"
    assert _run_runner(anchor, hist_dir, output) == rnr.EXIT_PENDING
    report = _runner_report(output)
    assert report["status"] == "pending-no-export"
    assert report["reasons"][0] == "evidence-export-refused-pending"
    assert "insufficient-clean-coverage" in report["reasons"]
    audit_block = report["audit"]
    assert isinstance(audit_block, dict)
    assert audit_block["classification"] == "pending"
    assert audit_block["long_soak_evidence"] is None
    assert (output / AUDIT_JSON).is_file()
    assert not (output / EVIDENCE_REL).exists()


# ---------------------------------------------------------------- 锚定拒绝面


def _mutate_anchor(base: dict[str, object], mutation: str,
                   value: object) -> dict[str, object]:
    mutated = json.loads(json.dumps(base))
    if mutation == "del":
        del mutated[str(value)]
    else:
        mutated[mutation] = value
    return mutated


@pytest.mark.parametrize("mutation,value,reason_part", [
    ("schema_version", 2, "anchor-schema-version"),
    ("tool", "tools/ops/other.py", "anchor-tool"),
    ("follow_up_audit_tool", "tools/ops/other.py", "anchor-follow-up-tool"),
    ("window_minutes", 720, "anchor-window-minutes"),
    ("earliest_audit_collected_at", _iso(ANCHOR + timedelta(hours=23)),
     "anchor-earliest-mismatch"),
    ("generated_at", _iso(ANCHOR + timedelta(minutes=1)),
     "anchor-generated-at-mismatch"),
    ("boundaries", [], "anchor-boundaries"),
    ("precondition", [], "anchor-precondition-keys"),
    ("extra-key", {"x": 1}, "anchor-keys"),
    ("del", "precondition", "anchor-keys"),
])
def test_reject_tampered_anchor_variants(tmp_path, mutation, value,
                                         reason_part) -> None:
    anchor, _src = _make_genuine_anchor(tmp_path)
    base = json.loads(anchor.read_text(encoding="utf-8"))
    if mutation == "extra-key":
        base["rogue_key"] = value
        mutated = base
    else:
        mutated = _mutate_anchor(base, mutation, value)
    anchor2 = tmp_path / "anchor-tampered.json"
    anchor2.write_text(json.dumps(mutated, ensure_ascii=False), encoding="utf-8")
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    _write_history(hist_dir, _pre_plus_window_rows())
    output = tmp_path / "out"
    assert _run_runner(anchor2, hist_dir, output) == rnr.EXIT_REFUSED
    _assert_zero_output(output)


@pytest.mark.parametrize("field,value,reason_part", [
    ("tail_non_ok_count", 1, "anchor-precondition-non-ok"),
    ("tail_sample_count", 7, "anchor-precondition-tail-count"),
    ("input_sha256", "zz", "anchor-precondition-sha256"),
    ("consecutive_ok", 0, "anchor-precondition-consecutive-ok"),
    ("max_gap_minutes", 0, "anchor-precondition-max-gap"),
    ("tail_max_gap_minutes", 999.0, "anchor-precondition-tail-gap"),
])
def test_reject_precondition_variants(tmp_path, field, value,
                                      reason_part) -> None:
    anchor, _src = _make_genuine_anchor(tmp_path)
    base = json.loads(anchor.read_text(encoding="utf-8"))
    base["precondition"][field] = value
    anchor2 = tmp_path / "anchor-tampered.json"
    anchor2.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    _write_history(hist_dir, _pre_plus_window_rows())
    output = tmp_path / "out"
    assert _run_runner(anchor2, hist_dir, output) == rnr.EXIT_REFUSED
    _assert_zero_output(output)


def test_reject_anchor_not_json_or_not_object(tmp_path) -> None:
    _anchor, _src = _make_genuine_anchor(tmp_path)
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    _write_history(hist_dir, _pre_plus_window_rows())
    bad1 = tmp_path / "not-json.json"
    bad1.write_text("{not json", encoding="utf-8")
    bad2 = tmp_path / "not-object.json"
    bad2.write_text("[]", encoding="utf-8")
    for bad in (bad1, bad2):
        output = tmp_path / "out"
        assert _run_runner(bad, hist_dir, output) == rnr.EXIT_REFUSED
        _assert_zero_output(output)


def test_reject_anchor_path_missing(tmp_path) -> None:
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    _write_history(hist_dir, _pre_plus_window_rows())
    output = tmp_path / "out"
    assert _run_runner(tmp_path / "absent.json", hist_dir,
                       output) == rnr.EXIT_REFUSED
    _assert_zero_output(output)


def test_reject_anchor_symlink_real_fs(tmp_path) -> None:
    """真实文件面 symlink 拒绝（不可用平台即 skip）。"""
    anchor, _src = _make_genuine_anchor(tmp_path)
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    _write_history(hist_dir, _pre_plus_window_rows())
    link = tmp_path / "anchor-link.json"
    try:
        link.symlink_to(anchor)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用（权限/平台）")
    output = tmp_path / "out"
    assert _run_runner(link, hist_dir, output) == rnr.EXIT_REFUSED
    _assert_zero_output(output)


# ---------------------------------------------------------------- 历史拒绝面


@pytest.mark.parametrize("rows,reason_part", [
    ([_row(ANCHOR + timedelta(hours=25), schema_version=9)], "schema-version"),
    ([_row(ANCHOR + timedelta(hours=25), overall="maybe")], "overall-status"),
    ([_row(ANCHOR + timedelta(hours=25)),
      _row(ANCHOR + timedelta(hours=25))], "duplicate-timestamp"),
    ([_row(ANCHOR + timedelta(hours=25)),
      _row(ANCHOR + timedelta(hours=24, minutes=45))], "non-chronological"),
    ([_row(ANCHOR + timedelta(hours=24, minutes=45),
           project="another-project"),
      _row(ANCHOR + timedelta(hours=25))], "conflicting-project"),
    ([], "empty-history"),
])
def test_reject_history_variants(tmp_path, rows, reason_part) -> None:
    anchor, _src = _make_genuine_anchor(tmp_path)
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    _write_history(hist_dir, rows)
    output = tmp_path / "out"
    assert _run_runner(anchor, hist_dir, output) == rnr.EXIT_REFUSED
    _assert_zero_output(output)


def test_reject_history_row_limit_exceeded(tmp_path) -> None:
    anchor, _src = _make_genuine_anchor(tmp_path)
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    start = ANCHOR + timedelta(hours=25) - timedelta(
        minutes=15 * (rnr._audit.MAX_RETENTION + 1))
    _write_history(hist_dir,
                   _range_rows(start, ANCHOR + timedelta(hours=25)))
    output = tmp_path / "out"
    assert _run_runner(anchor, hist_dir, output) == rnr.EXIT_REFUSED
    _assert_zero_output(output)


def test_reject_history_path_missing(tmp_path) -> None:
    anchor, _src = _make_genuine_anchor(tmp_path)
    output = tmp_path / "out"
    assert _run_runner(anchor, tmp_path / "absent-dir",
                       output) == rnr.EXIT_REFUSED
    _assert_zero_output(output)


def test_reject_history_before_anchor(tmp_path) -> None:
    """最新样本早于锚点本身 → 该历史不可能覆盖锚定窗口，fail-closed。"""
    anchor, _src = _make_genuine_anchor(tmp_path)
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    _write_history(hist_dir, _range_rows(ANCHOR - timedelta(hours=3),
                                         ANCHOR - timedelta(minutes=15)))
    output = tmp_path / "out"
    assert _run_runner(anchor, hist_dir, output) == rnr.EXIT_REFUSED
    _assert_zero_output(output)


# ---------------------------------------------------------------- 输出目录护栏


def test_reject_output_dir_exists(tmp_path) -> None:
    anchor, _src = _make_genuine_anchor(tmp_path)
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    _write_history(hist_dir, _pre_plus_window_rows())
    output = tmp_path / "out"
    output.mkdir()
    assert _run_runner(anchor, hist_dir, output) == rnr.EXIT_REFUSED
    assert list(output.iterdir()) == []


def test_reject_output_dir_symlink(tmp_path) -> None:
    """dangling symlink 输出目录 → symlink 拒绝（不可用平台即 skip）。"""
    anchor, _src = _make_genuine_anchor(tmp_path)
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    _write_history(hist_dir, _pre_minus_due_rows())
    output = tmp_path / "out-link"
    try:
        output.symlink_to(tmp_path / "nowhere")
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用（权限/平台）")
    assert _run_runner(anchor, hist_dir, output) == rnr.EXIT_REFUSED


# ---------------------------------------------------------------- 哈希/篡改防御


class _CorruptingEvidenceStore:
    """写 evidence/long-soak.json 时翻转首字节——read-back 复核必须拒绝。"""

    def __init__(self) -> None:
        self._real = rnr._history.RealStore()

    def exists(self, path: Path) -> bool:
        return self._real.exists(path)

    def is_symlink(self, path: Path) -> bool:
        return self._real.is_symlink(path)

    def list_dir(self, directory: Path) -> list[str]:
        return self._real.list_dir(directory)

    def read_bytes(self, path: Path) -> bytes:
        return self._real.read_bytes(path)

    def mkdirs(self, directory: Path) -> None:
        self._real.mkdirs(directory)

    def write_atomic(self, path: Path, text: str) -> None:
        if path.name == "long-soak.json":
            text = ("#" + text[1:]) if text.startswith("{") else text + "#"
        self._real.write_atomic(path, text)


def test_corrupted_evidence_write_refused_and_removed(tmp_path) -> None:
    anchor, _src = _make_genuine_anchor(tmp_path)
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    _write_history(hist_dir, _pre_plus_window_rows())
    output = tmp_path / "out"
    with pytest.raises(rnr.RunnerError, match="evidence-byte-mismatch"):
        rnr.run_runner(store=_CorruptingEvidenceStore(), anchor=anchor,
                       history=hist_dir, output_dir=output)
    #: 坏副本被移除；runner 报告零写出（审计报告为 run_audit 先行产物，保留）
    assert not (output / EVIDENCE_REL).exists()
    assert not (output / RUNNER_JSON).exists()
    assert (output / AUDIT_JSON).is_file()


# ---------------------------------------------------------------- 零墙钟


def test_outputs_byte_identical_across_reruns(tmp_path) -> None:
    anchor, _src = _make_genuine_anchor(tmp_path)
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    _write_history(hist_dir, _pre_plus_window_rows())
    first, second = tmp_path / "out1", tmp_path / "out2"
    assert _run_runner(anchor, hist_dir, first) == rnr.EXIT_EXPORTED_PASS
    assert _run_runner(anchor, hist_dir, second) == rnr.EXIT_EXPORTED_PASS
    assert (first / RUNNER_JSON).read_bytes() == (
        second / RUNNER_JSON).read_bytes()
    assert (first / RUNNER_MD).read_bytes() == (second / RUNNER_MD).read_bytes()


def test_no_clock_fakery_generated_at_from_latest_sample(tmp_path) -> None:
    anchor, _src = _make_genuine_anchor(tmp_path)
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    _write_history(hist_dir, _pre_plus_window_rows())
    output = tmp_path / "out"
    assert _run_runner(anchor, hist_dir, output) == rnr.EXIT_EXPORTED_PASS
    report = _runner_report(output)
    expected_latest = _iso(ANCHOR + timedelta(minutes=1440))
    assert report["latest_sample_collected_at"] == expected_latest
    assert report["generated_at"] == expected_latest
    assert report["anchor_collected_at"] == _iso(ANCHOR)
    assert report["earliest_audit_collected_at"] == expected_latest


# ---------------------------------------------------------------- 隐私 / CLI


def test_history_extra_fields_never_leak(tmp_path) -> None:
    anchor, _src = _make_genuine_anchor(tmp_path)
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    rows = _pre_plus_window_rows()
    rows[0]["leak_note"] = MARK_TOKEN
    rows[-1]["leak_note"] = MARK_TOKEN
    _write_history(hist_dir, rows)
    output = tmp_path / "out"
    assert _run_runner(anchor, hist_dir, output) == rnr.EXIT_EXPORTED_PASS
    for name in (RUNNER_JSON, RUNNER_MD, AUDIT_JSON,
                 "soak-audit-report.md", EVIDENCE_REL):
        content = (output / name).read_text(encoding="utf-8")
        assert MARK_TOKEN not in content, f"{name} 泄漏了标记值"


def test_cli_defaults_registered() -> None:
    parser = rnr.build_parser()
    args = parser.parse_args([])
    assert args.anchor == rnr.DEFAULT_ANCHOR_PATH
    assert args.history == rnr.DEFAULT_HISTORY_PATH
    assert args.output_dir == rnr.DEFAULT_OUTPUT_DIR


# ------------------------------------------------- R2：CLI 输出零绝对路径

#: 盘符形态（Windows 绝对路径）——CLI stdout/help 绝不允许出现
_DRIVE_PATH_RE = re.compile(r"[A-Za-z]:[\\/]")


def _assert_no_absolute_path_text(text: str) -> None:
    assert _DRIVE_PATH_RE.search(text) is None, (
        f"CLI 输出含盘符/绝对路径形态文本: {text!r}")
    for default in (rnr.DEFAULT_ANCHOR_PATH, rnr.DEFAULT_HISTORY_PATH,
                    rnr.DEFAULT_OUTPUT_DIR, rnr.REPO_ROOT):
        assert str(default) not in text, f"CLI 输出插值了绝对路径: {default}"


def test_cli_stdout_success_no_absolute_paths(tmp_path, capsys) -> None:
    """R2 回归：成功路径 CLI stdout 只打印文件名/状态/时间戳/固定词汇，
    绝不回显 args 锚定/历史/输出目录路径。"""
    anchor, _src = _make_genuine_anchor(tmp_path)
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    _write_history(hist_dir, _pre_plus_window_rows())
    capsys.readouterr()  # 清空产锚阶段（soak_window_gate CLI）的输出
    output = tmp_path / "out"
    assert _run_runner(anchor, hist_dir, output) == rnr.EXIT_EXPORTED_PASS
    captured = capsys.readouterr()
    _assert_no_absolute_path_text(captured.out)
    _assert_no_absolute_path_text(captured.err)
    assert rnr.RUNNER_JSON_NAME in captured.out  # 仍有可用落盘反馈


def test_cli_stdout_refused_no_absolute_paths(tmp_path, capsys) -> None:
    """R2 回归：拒绝路径 CLI stdout 同样零绝对路径文本。"""
    anchor, _src = _make_genuine_anchor(tmp_path)
    hist_dir = tmp_path / "hist"
    hist_dir.mkdir()
    _write_history(hist_dir, _pre_plus_window_rows())
    output = tmp_path / "out"
    output.mkdir()
    capsys.readouterr()
    assert _run_runner(anchor, hist_dir, output) == rnr.EXIT_REFUSED
    captured = capsys.readouterr()
    _assert_no_absolute_path_text(captured.out)
    _assert_no_absolute_path_text(captured.err)
    assert "output-dir-exists" in captured.out


def test_parser_help_generic_no_absolute_paths() -> None:
    """R2 回归：argparse help 只作通用输入描述、绝不插值 DEFAULT_* 绝对
    路径值；三个选项仍在；默认路径行为不变（默认值仍注册在 parser 上）。"""
    help_text = rnr.build_parser().format_help()
    _assert_no_absolute_path_text(help_text)
    for option in ("--anchor", "--history", "--output-dir"):
        assert option in help_text
    args = rnr.build_parser().parse_args([])
    assert args.anchor == rnr.DEFAULT_ANCHOR_PATH
    assert args.history == rnr.DEFAULT_HISTORY_PATH
    assert args.output_dir == rnr.DEFAULT_OUTPUT_DIR
