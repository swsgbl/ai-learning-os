r"""M14-133 tools/ops/production_drift_watch_history.py 契约测试：
production drift watch 历史审计——只读扫描 M14-127 drift-watch 报告目录，
严格 schema 校验 + 文件名时间戳交叉校验 + PT15M slot 连续性审计 +
重复/digest 漂移检测 + 确定性 JSON/MD 审计报告。

覆盖（全部合成临时 fixtures，绝不触碰真实 gitignored 工件、生产栈、
计划任务或任何报告目录默认值；零 Docker、零网络、零子进程、零 env）：
- 结构契约：AST import 白名单（零网络/零 env 模块）、源码零墙钟 token
  （datetime.now/utcnow/time.time）、零 subprocess、零 shell=True、
  零 docker 引用；
- 快乐路径：连续 PT15M 自然轮 → exit 0、全绿摘要（streak/slot/digest
  anchored）、固定输出文件名、原子写无 tmp 残留；
- scheduled 选择契约：minute 00/15/30/45 + second 00-30 才入选；
  manual/off-slot（含 second=31 上界外）→ excluded 可见保留
  （reason=manual-off-slot），伴生 .md / plan-*.json / 无关条目只计数；
- fail-closed 发现：缺失 slot、slot 断链重置 streak、malformed（非
  JSON/非 UTF-8/schema 逐字段错/counts-checks/drift-counts 矛盾）、
  文件名时间戳非法与越界（+2s 容差边界）、重复 started_at、同 slot
  双跑、drift=true、digest expected/running 不一致与未锚定、零
  valid / 零 selected、slot 范围超硬顶；
- 路径拒绝：输入目录含 .. 组件 / 不存在 / symlink 目录组件 /
  symlink 报告文件 / symlink 输出目录 → exit 2（前四类零输出）；
- 确定性与安全：同输入两次运行逐字节相同、输出无绝对路径、
  报告 schema 顶层键契约、redact 终防线。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "production_drift_watch_history.py"

JSON_REPORT = "drift-watch-history.json"
MD_REPORT = "drift-watch-history.md"

ANCHOR = datetime(2026, 9, 25, 4, 30, 1, tzinfo=timezone.utc)
API_DIGEST = "sha256:" + "a" * 64
WEB_DIGEST = "sha256:" + "b" * 64
OTHER_DIGEST = "sha256:" + "c" * 64
SOURCE_TOOL = "tools/ops/production_drift_watch.py"


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


dwh = _load_module(SCRIPT, "production_drift_watch_history_under_test")


# ---------------------------------------------------------------- 合成 fixtures


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%d-%H%M%S")


def _report(started: datetime, *, ended: datetime | None = None,
            drift: bool = False, schema_version: int = 1,
            tool: str = SOURCE_TOOL, milestone: str = "M14-127",
            mode: str = "execute", started_at: str | None = None,
            api_expected: str = API_DIGEST, web_expected: str = WEB_DIGEST,
            api_running: str | None = None, web_running: str | None = None,
            drift_reasons: list[str] | None = None,
            omit: tuple[str, ...] = (),
            ) -> dict[str, object]:
    """合成一份与 M14-127 execute 报告同构的 drift-watch 报告。

    默认三重 digest 一致且 drift=False；drift=True 时自动补一个 fail
    check 与 reason，保持 counts/checks/drift 三方一致（除非 omit 故意
    破坏某字段）。"""
    ended = ended or (started + timedelta(seconds=1))
    api_running = api_running if api_running is not None else api_expected
    web_running = web_running if web_running is not None else web_expected
    checks: list[dict[str, str]] = [
        {"check_id": "collector:compose-ps", "subject": "compose-ps",
         "status": "pass", "detail": "ok"},
        {"check_id": "compose-service-present", "subject": "api",
         "status": "pass", "detail": "present"},
        {"check_id": "container-health", "subject": "api",
         "status": "pass", "detail": "state=running health=healthy"},
    ]
    if drift:
        checks.append({"check_id": "image-running-digest", "subject": "api",
                       "status": "fail", "detail": "image-digest-mismatch:api"})
    report: dict[str, object] = {
        "schema_version": schema_version,
        "tool": tool,
        "milestone": milestone,
        "mode": mode,
        "started_at_utc": started_at if started_at is not None else _iso(started),
        "ended_at_utc": _iso(ended),
        "config": {
            "project": "aios-m14-03-production-rehearsal",
            "profiles": ["local", "search"],
            "compose_file": "docker-compose.yml",
            "services": ["postgres", "redis", "minio", "api", "web",
                         "livekit", "searxng"],
            "anchors": {
                "api": {"expected_tag": "aios/api:m14-124-production",
                        "expected_digest": api_expected},
                "web": {"expected_tag": "aios/web:m14-124-production",
                        "expected_digest": web_expected},
            },
        },
        "boundaries": ["synthetic fixture boundary"],
        "collectors": {
            "compose_ps": {"status": "ok", "failure_category": None,
                           "error_class": None, "services": {}},
            "containers": {"per_service": {
                "api": {"status": "ok", "failure_category": None,
                        "error_class": None, "state": "running",
                        "health": "healthy",
                        "config_image": "aios/api:m14-124-production",
                        "image_id": api_running},
                "web": {"status": "ok", "failure_category": None,
                        "error_class": None, "state": "running",
                        "health": "healthy",
                        "config_image": "aios/web:m14-124-production",
                        "image_id": web_running},
            }},
            "image_refs": {
                "api": {"status": "ok", "failure_category": None,
                        "error_class": None, "image_id": api_running},
                "web": {"status": "ok", "failure_category": None,
                        "error_class": None, "image_id": web_running},
            },
        },
        "checks": checks,
        "counts": {"pass": sum(1 for c in checks if c["status"] == "pass"),
                   "fail": sum(1 for c in checks if c["status"] == "fail")},
        "drift": drift,
        "drift_reasons": drift_reasons if drift_reasons is not None else (
            ["image-digest-mismatch:api"] if drift else []),
    }
    for key in omit:
        report.pop(key, None)
    return report


def _write_report(directory: Path, started: datetime, *,
                  name_stamp: datetime | None = None, **kwargs) -> Path:
    payload = kwargs.pop("payload", None)
    if payload is None:
        payload = _report(started, **kwargs)
    stamp = _stamp(name_stamp if name_stamp is not None else started)
    path = directory / f"drift-watch-{stamp}.json"
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return path


def _slot_runs(count: int, *, first: datetime = ANCHOR,
               **kwargs) -> list[datetime]:
    """count 份连续 PT15M 槽位自然轮（各 +1 秒启动，模拟真实调度）。"""
    return [first + timedelta(minutes=15 * i) for i in range(count)]


def _run_cli(input_dir: Path, output_dir: Path, *extra: str) -> int:
    return dwh.main(["--input-dir", str(input_dir),
                     "--output-dir", str(output_dir), *extra])


def _report_json(output_dir: Path) -> dict[str, object]:
    return json.loads((output_dir / JSON_REPORT).read_text(encoding="utf-8"))


def _assert_zero_output(output_dir: Path) -> None:
    """fail-closed 契约：输出目录未创建。"""
    assert not output_dir.exists()


def _prepare(tmp_path: Path, starts: list[datetime], **kwargs) -> tuple[Path, Path]:
    source = tmp_path / "src"
    output = tmp_path / "out"
    source.mkdir()
    for started in starts:
        _write_report(source, started, **kwargs)
    return source, output


# ---------------------------------------------------------------- 结构契约


def test_source_contract_forbidden_tokens() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for token in ("subprocess", "socket", "urllib", "http.client", "os.system",
                  "Popen", "environ", "requests", "urlopen", "getenv",
                  "datetime.now", "utcnow", "time.time", "shell=True",
                  "docker"):
        assert token not in source, f"禁止出现的字面量: {token}"


def test_source_contract_import_whitelist() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    allowed = {"argparse", "json", "os", "re", "sys", "dataclasses",
               "datetime", "pathlib", "__future__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            assert (node.module or "").split(".")[0] in allowed


# ---------------------------------------------------------------- 快乐路径


def test_consecutive_scheduled_runs_pass(tmp_path) -> None:
    """四份连续 PT15M 自然轮（各 +1 秒）→ exit 0、全绿摘要。"""
    source, output = _prepare(tmp_path, _slot_runs(4))
    assert _run_cli(source, output) == dwh.EXIT_OK
    report = _report_json(output)
    assert report["schema_version"] == 1
    assert report["tool"] == "tools/ops/production_drift_watch_history.py"
    assert report["milestone"] == "M14-133"
    assert report["source"]["tool"] == SOURCE_TOOL
    assert report["counts"] == {"matching_files": 4, "ignored_entries": 0,
                                "valid": 4, "invalid": 0, "selected": 4,
                                "excluded": 0, "clean": 4, "drifted": 0}
    assert report["first_selected_started_at_utc"] == _iso(ANCHOR)
    assert report["last_selected_started_at_utc"] == _iso(ANCHOR + timedelta(minutes=45))
    assert report["slots"] == {"expected": 4, "present": 4, "missing": 0,
                               "missing_slots": [],
                               "missing_slots_truncated": False,
                               "range_too_large": False}
    assert report["longest_clean_streak"]["runs"] == 4
    assert report["longest_clean_streak"]["start_slot_utc"] == "2026-09-25T04:30:00Z"
    assert report["longest_clean_streak"]["end_slot_utc"] == "2026-09-25T05:15:00Z"
    assert report["duplicates"] == {"started_at": [], "slots": []}
    for service, digest in (("api", API_DIGEST), ("web", WEB_DIGEST)):
        info = report["digest_stability"][service]
        assert info == {"expected_digests": [digest],
                        "running_digests": [digest],
                        "expected_consistent": True,
                        "running_consistent": True, "anchored": True}
    assert report["audit"] == {"pass": True, "findings": []}
    assert report["runs_truncated"] is False
    assert [run["status"] for run in report["runs"]] == ["selected"] * 4
    assert (output / MD_REPORT).exists()


def test_single_selected_run_pass(tmp_path) -> None:
    source, output = _prepare(tmp_path, [ANCHOR])
    assert _run_cli(source, output) == dwh.EXIT_OK
    report = _report_json(output)
    assert report["slots"]["expected"] == 1
    assert report["slots"]["missing"] == 0
    assert report["longest_clean_streak"]["runs"] == 1


def test_atomic_output_no_tmp_residue(tmp_path) -> None:
    source, output = _prepare(tmp_path, _slot_runs(2))
    assert _run_cli(source, output) == dwh.EXIT_OK
    assert sorted(p.name for p in output.iterdir()) == [JSON_REPORT, MD_REPORT]


def test_write_failure_fails_closed(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    source, output = _prepare(tmp_path, _slot_runs(2))
    real_replace = dwh.os.replace

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(dwh.os, "replace", _boom)
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    monkeypatch.setattr(dwh.os, "replace", real_replace)


# ---------------------------------------------------------------- scheduled 选择契约


def test_manual_off_slot_excluded_visible(tmp_path) -> None:
    """off-slot 手动轮 → excluded 可见保留，不破坏全绿。"""
    starts = [ANCHOR, ANCHOR + timedelta(minutes=7, seconds=32),
              ANCHOR + timedelta(minutes=15)]
    source, output = _prepare(tmp_path, starts)
    assert _run_cli(source, output) == dwh.EXIT_OK
    report = _report_json(output)
    assert report["counts"]["excluded"] == 1
    assert report["counts"]["selected"] == 2
    excluded = [run for run in report["runs"] if run["status"] == "excluded"]
    assert len(excluded) == 1
    assert excluded[0]["reason"] == "manual-off-slot"
    assert excluded[0]["started_at_utc"] == _iso(ANCHOR + timedelta(minutes=7, seconds=32))
    assert excluded[0]["slot_utc"] is None


def test_second_window_boundaries(tmp_path) -> None:
    """second=30 入选（闭上界）；second=31 排除；minute 07 排除。"""
    in_30 = ANCHOR.replace(second=30)
    out_31 = ANCHOR.replace(second=31) + timedelta(minutes=15)
    out_min = ANCHOR + timedelta(minutes=7)
    source, output = _prepare(tmp_path, [in_30, out_31, out_min])
    assert _run_cli(source, output) == dwh.EXIT_OK
    report = _report_json(output)
    assert report["counts"]["selected"] == 1
    assert report["counts"]["excluded"] == 2
    assert report["first_selected_started_at_utc"] == _iso(in_30)


def test_companion_and_foreign_entries_ignored(tmp_path) -> None:
    """伴生 .md、plan-*.json、无关文件只计数，绝不解析。"""
    source, output = _prepare(tmp_path, _slot_runs(2))
    (source / "drift-watch-20260925-043001.md").write_text("# companion", encoding="utf-8")
    plan = _report(ANCHOR, mode="plan")
    plan.pop("checks", None)
    plan.pop("counts", None)
    plan.pop("drift", None)
    plan.pop("drift_reasons", None)
    (source / "plan-20260925-035715.json").write_text(
        json.dumps(plan), encoding="utf-8")
    (source / "notes.txt").write_text("noise", encoding="utf-8")
    assert _run_cli(source, output) == dwh.EXIT_OK
    report = _report_json(output)
    assert report["counts"]["ignored_entries"] == 3
    assert report["counts"]["matching_files"] == 2


# ---------------------------------------------------------------- fail-closed 发现


def test_missing_slot_detected(tmp_path) -> None:
    starts = [ANCHOR, ANCHOR + timedelta(minutes=30)]
    source, output = _prepare(tmp_path, starts)
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    report = _report_json(output)
    assert report["slots"]["expected"] == 3
    assert report["slots"]["missing"] == 1
    assert report["slots"]["missing_slots"] == ["2026-09-25T04:45:00Z"]
    assert "missing-slot:2026-09-25T04:45:00Z" in report["audit"]["findings"]
    assert report["audit"]["pass"] is False


def test_streak_resets_on_gap(tmp_path) -> None:
    """04:30/04:45 + 05:15/05:30（缺 05:00）→ streak=2 非 4。"""
    starts = [ANCHOR, ANCHOR + timedelta(minutes=15),
              ANCHOR + timedelta(minutes=45), ANCHOR + timedelta(minutes=60)]
    source, output = _prepare(tmp_path, starts)
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    report = _report_json(output)
    assert report["longest_clean_streak"]["runs"] == 2


def test_drift_report_fails_audit(tmp_path) -> None:
    starts = _slot_runs(2)
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_report(source, starts[0])
    _write_report(source, starts[1], drift=True)
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    report = _report_json(output)
    assert report["counts"]["drifted"] == 1
    drifted = [run for run in report["runs"] if run["drift"] is True]
    assert len(drifted) == 1
    assert drifted[0]["check_fail"] == 1
    assert any(f.startswith("drift-report:") for f in report["audit"]["findings"])


@pytest.mark.parametrize("kwargs,reason", [
    ({"schema_version": 2}, "schema-version"),
    ({"tool": "tools/ops/other.py"}, "tool"),
    ({"milestone": "M14-099"}, "milestone"),
    ({"mode": "plan"}, "mode"),
    ({"started_at": "2026-09-25 04:30:01"}, "started-or-ended-at"),
    ({"omit": ("ended_at_utc",)}, "started-or-ended-at"),
    ({"omit": ("config",)}, "config"),
    ({"omit": ("collectors",)}, "collectors"),
    ({"omit": ("checks",)}, "checks"),
    ({"omit": ("counts",)}, "counts"),
    ({"omit": ("drift",)}, "drift"),
    ({"omit": ("drift_reasons",)}, "drift-reasons"),
    ({"api_expected": "sha256:xyz"}, "config-anchors"),
])
def test_schema_rejections(tmp_path, kwargs: dict, reason: str) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    payload = _report(ANCHOR, **kwargs)
    path = source / "drift-watch-20260925-043001.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    report = _report_json(output)
    assert report["counts"]["invalid"] == 1
    assert report["runs"][0]["reason"] == reason
    assert "invalid-report:drift-watch-20260925-043001.json" in report["audit"]["findings"]


def test_ended_before_started_rejected(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_report(source, ANCHOR,
                  ended=ANCHOR - timedelta(seconds=1))
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    assert _report_json(output)["runs"][0]["reason"] == "ended-before-started"


def test_counts_checks_and_drift_mismatch_rejected(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    payload = _report(ANCHOR)
    payload["counts"] = {"pass": 99, "fail": 0}
    _write_report(source, ANCHOR, payload=payload)
    payload2 = _report(ANCHOR + timedelta(minutes=15))
    payload2["drift"] = True
    payload2["drift_reasons"] = ["synthetic"]
    _write_report(source, ANCHOR + timedelta(minutes=15), payload=payload2)
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    report = _report_json(output)
    reasons = {run["reason"] for run in report["runs"]}
    assert reasons == {"counts-checks-mismatch", "drift-counts-mismatch"}


def test_not_json_and_not_utf8_rejected(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_report(source, ANCHOR, payload=b"{ not json")
    _write_report(source, ANCHOR + timedelta(minutes=15),
                  payload=b"\xff\xfe\x00broken")
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    report = _report_json(output)
    reasons = {run["reason"] for run in report["runs"]}
    assert reasons == {"not-json", "not-utf8"}


def test_filename_timestamp_mismatch_and_tolerance(tmp_path) -> None:
    """started 超出 [stamp, stamp+2s] → mismatch；恰 +2s → 放行。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    # stamp 04:30:01 / started 04:29:55（早于 stamp）→ mismatch
    _write_report(source, ANCHOR, name_stamp=ANCHOR,
                  started_at=_iso(ANCHOR - timedelta(seconds=6)))
    # stamp 04:45:01 / started 04:45:03（滞后 +2s，容差边界）→ valid
    later = ANCHOR + timedelta(minutes=15)
    _write_report(source, later, name_stamp=later,
                  started_at=_iso(later + timedelta(seconds=2)),
                  ended=later + timedelta(seconds=3))
    # stamp 05:00:05 / started 05:00:01（stamp 晚于 started）→ mismatch
    third = ANCHOR + timedelta(minutes=30)
    _write_report(source, third, name_stamp=third + timedelta(seconds=4),
                  started_at=_iso(third))
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    report = _report_json(output)
    reasons = {run["reason"] for run in report["runs"]}
    assert reasons == {"filename-timestamp-mismatch", None}


def test_filename_stamp_invalid_date_rejected(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_report(source, ANCHOR)  # 一份合法入选，避免 no-selected 干扰
    path = source / "drift-watch-20261399-999999.json"
    path.write_text(json.dumps(_report(ANCHOR)), encoding="utf-8")
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    reasons = {run["reason"] for run in _report_json(output)["runs"]}
    assert reasons == {"filename-timestamp-invalid", None}


def test_duplicate_started_at_detected(tmp_path) -> None:
    """stamp 043000 与 043001 两份同 started 04:30:01Z → 重复。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_report(source, ANCHOR, name_stamp=ANCHOR - timedelta(seconds=1))
    _write_report(source, ANCHOR, name_stamp=ANCHOR)
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    report = _report_json(output)
    assert len(report["duplicates"]["started_at"]) == 1
    group = report["duplicates"]["started_at"][0]
    assert group["started_at_utc"] == _iso(ANCHOR)
    assert len(group["filenames"]) == 2
    assert "duplicate-started-at:" + _iso(ANCHOR) in report["audit"]["findings"]


def test_duplicate_slot_detected(tmp_path) -> None:
    """同 slot 双跑（04:30:01 与 04:30:20，均 second<=30）→ duplicate-slot。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_report(source, ANCHOR)
    _write_report(source, ANCHOR.replace(second=20))
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    report = _report_json(output)
    assert len(report["duplicates"]["slots"]) == 1
    assert report["duplicates"]["slots"][0]["slot_utc"] == "2026-09-25T04:30:00Z"
    assert "duplicate-slot:2026-09-25T04:30:00Z" in report["audit"]["findings"]


def test_digest_inconsistency_detected(tmp_path) -> None:
    """一份报告 api running digest 漂移 → running 不一致 + 未锚定。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_report(source, ANCHOR)
    _write_report(source, ANCHOR + timedelta(minutes=15),
                  api_running=OTHER_DIGEST, drift=True)
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    report = _report_json(output)
    findings = report["audit"]["findings"]
    assert "digest-running-inconsistent:api" in findings
    assert "digest-not-anchored:api" in findings
    info = report["digest_stability"]["api"]
    assert info["running_digests"] == sorted([API_DIGEST, OTHER_DIGEST])
    assert info["anchored"] is False


def test_digest_expected_inconsistency_detected(tmp_path) -> None:
    """一份报告 api expected_digest 与其它不同 → expected 不一致。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_report(source, ANCHOR)
    _write_report(source, ANCHOR + timedelta(minutes=15),
                  api_expected=OTHER_DIGEST)
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    findings = _report_json(output)["audit"]["findings"]
    assert "digest-expected-inconsistent:api" in findings


def test_empty_input_dir_fail_closed(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    report = _report_json(output)
    assert "no-valid-reports" in report["audit"]["findings"]
    assert report["counts"]["matching_files"] == 0


def test_only_manual_reports_no_selection_fail_closed(tmp_path) -> None:
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    _write_report(source, ANCHOR + timedelta(minutes=7))
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    report = _report_json(output)
    assert "no-selected-reports" in report["audit"]["findings"]
    assert report["slots"]["expected"] is None
    assert report["slots"]["missing"] is None


def test_slot_range_too_large_fail_closed(tmp_path) -> None:
    """首末入选相距超 MAX_EXPECTED_SLOTS → 范围超限发现。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    far = ANCHOR + timedelta(minutes=15 * (dwh.MAX_EXPECTED_SLOTS + 1))
    _write_report(source, ANCHOR)
    _write_report(source, far)
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    report = _report_json(output)
    assert "slot-range-too-large" in report["audit"]["findings"]
    assert report["slots"]["range_too_large"] is True
    assert report["slots"]["missing"] is None


# ---------------------------------------------------------------- 路径拒绝


def test_input_dir_traversal_rejected(tmp_path) -> None:
    source, output = _prepare(tmp_path, _slot_runs(1))
    traversal = source / ".." / "src"
    assert _run_cli(traversal, output) == dwh.EXIT_REFUSE
    _assert_zero_output(output)


def test_input_dir_missing_rejected(tmp_path) -> None:
    output = tmp_path / "out"
    assert _run_cli(tmp_path / "nope", output) == dwh.EXIT_REFUSE
    _assert_zero_output(output)


def test_input_dir_is_file_rejected(tmp_path) -> None:
    source, output = _prepare(tmp_path, _slot_runs(1))
    some_file = next(p for p in source.iterdir())
    assert _run_cli(some_file, output) == dwh.EXIT_REFUSE
    _assert_zero_output(output)


def test_symlinked_input_dir_refused(tmp_path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    _write_report(real_dir, ANCHOR)
    link_dir = tmp_path / "link"
    try:
        link_dir.symlink_to(real_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用（权限/平台）")
    output = tmp_path / "out"
    assert _run_cli(link_dir, output) == dwh.EXIT_REFUSE
    _assert_zero_output(output)


def test_symlinked_report_file_marked_invalid(tmp_path) -> None:
    source, output = _prepare(tmp_path, _slot_runs(1))
    real = source / "real.json"
    real.write_text(json.dumps(_report(ANCHOR + timedelta(minutes=15))),
                    encoding="utf-8")
    try:
        (source / "drift-watch-20260925-044501.json").symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用（权限/平台）")
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    report = _report_json(output)
    reasons = {run["reason"] for run in report["runs"]}
    assert "symlink-target" in reasons


def test_symlinked_output_dir_refused(tmp_path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    _write_report(source, ANCHOR)
    real_out = tmp_path / "real-out"
    real_out.mkdir()
    link_out = tmp_path / "link-out"
    try:
        link_out.symlink_to(real_out, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用（权限/平台）")
    assert _run_cli(source, link_out) == dwh.EXIT_REFUSE
    assert sorted(p.name for p in real_out.iterdir()) == []


# ---------------------------------------------------------------- 确定性与安全


def test_deterministic_repeated_output(tmp_path) -> None:
    """同输入两次运行 → JSON/MD 逐字节相同（零墙钟）。"""
    source, output = _prepare(tmp_path, _slot_runs(3))
    assert _run_cli(source, output) == dwh.EXIT_OK
    first_json = (output / JSON_REPORT).read_bytes()
    first_md = (output / MD_REPORT).read_bytes()
    assert _run_cli(source, output) == dwh.EXIT_OK
    assert (output / JSON_REPORT).read_bytes() == first_json
    assert (output / MD_REPORT).read_bytes() == first_md


def test_no_absolute_paths_in_output(tmp_path) -> None:
    """输出绝不包含绝对路径（输入/输出目录的真实路径均不出现）。"""
    source, output = _prepare(tmp_path, _slot_runs(2))
    assert _run_cli(source, output) == dwh.EXIT_OK
    combined = ((output / JSON_REPORT).read_text(encoding="utf-8")
                + (output / MD_REPORT).read_text(encoding="utf-8"))
    for forbidden in (str(tmp_path), str(tmp_path.resolve()),
                      str(source), str(source.resolve())):
        assert forbidden not in combined
    assert ":\\\\" not in combined and ":/" not in combined


def test_redaction_final_defense(tmp_path) -> None:
    """输出面经严格校验不携带报告原文；终防线 redact_secrets 单元行为锁定。"""
    source, output = tmp_path / "src", tmp_path / "out"
    source.mkdir()
    payload = _report(ANCHOR, drift=True,
                      drift_reasons=["password=ZXpwdmarker99887766"])
    path = source / "drift-watch-20260925-043001.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _run_cli(source, output) == dwh.EXIT_REFUSE
    combined = ((output / JSON_REPORT).read_text(encoding="utf-8")
                + (output / MD_REPORT).read_text(encoding="utf-8"))
    # 输入报告里的 secret 形态值绝不进入审计输出（drift_reasons 不被复制）
    assert "ZXpwdmarker99887766" not in combined
    # 终防线单元契约：写盘前 redact_secrets 对各 secret 形态生效
    assert dwh.redact_secrets("password=ZXpwdmarker99887766") == "[REDACTED:credential]"
    assert dwh.redact_secrets("token sk-ZXmarker4567890123") == "token [REDACTED:token]"
    assert dwh.redact_secrets("plain text stays") == "plain text stays"


def test_report_top_level_schema_contract(tmp_path) -> None:
    source, output = _prepare(tmp_path, _slot_runs(2))
    assert _run_cli(source, output) == dwh.EXIT_OK
    report = _report_json(output)
    assert set(report) == {"schema_version", "tool", "milestone", "source",
                           "selection", "counts",
                           "first_selected_started_at_utc",
                           "last_selected_started_at_utc", "slots",
                           "duplicates", "longest_clean_streak",
                           "digest_stability", "audit", "runs",
                           "runs_truncated", "boundaries"}
    assert report["selection"]["scheduled_minutes"] == [0, 15, 30, 45]
    assert report["selection"]["scheduled_second_max"] == 30
    assert report["selection"]["filename_timestamp_tolerance_seconds"] == 2
    run = report["runs"][0]
    assert set(run) == {"filename", "status", "reason", "started_at_utc",
                        "ended_at_utc", "slot_utc", "drift", "check_pass",
                        "check_fail"}
