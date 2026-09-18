r"""M14-50 契约:audit/archive readiness 评估器(纯标准库、零 I/O)。

被测对象 ``tools/ops/audit_archive_scheduler.py``(单文件纯标准库,无
CLI/文件/网络/环境访问/子进程)。覆盖:

- validate_state/validate_policy:schema v1 严格拒绝——版本号、顶层/
  任务/字段级多余与缺失、非映射类型、naive 或非法时间戳、大写/短
  hash、空或非标量 facts、非正或 bool/float 的 days、负/bool/float 的
  grace_hours;合法输入返回 UTC 规范化 value;
- evaluate_readiness:fresh/due/overdue 语义与双边界(interval 含端、
  grace 远端含端)、grace=0 时 due 窗口为空、总体最坏严重级聚合
  (blocked > overdue > due > fresh)、next_due_at/overdue_at 精确值;
  两级隔离——文档级畸形(版本/顶层键/任务集合/非映射/grace)→ 全任务
  blocked,任务级字段问题(坏 hash、naive 时间戳、坏 days、多余字段)
  与未来 last_success_at → 仅该任务 blocked、其余任务照常评估;
- 确定性:同输入重复评估输出全等、报告 canonical JSON 可序列化;
- 源级纪律:被测源文件禁止 requests/boto3/subprocess/os.environ 英文
  token。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "audit_archive_scheduler.py"

NOW = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
HASHES = ("a" * 64, "b" * 64, "c" * 64)
FORBIDDEN_TOKENS = ("requests", "boto3", "subprocess", "os.environ")


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


sched = _load_module(SCRIPT, "audit_archive_scheduler_under_test")


def make_state(ages=(timedelta(days=7),) * 3) -> dict:
    """合法 state;任务 i 的 last_success_at 为 NOW 减 ages[i]。"""
    tasks = {}
    for name, age, digest in zip(sched.TASK_NAMES, ages, HASHES):
        tasks[name] = {
            "last_success_at": (NOW - age).isoformat(),
            "source_sha256": digest,
            "facts": {"kind": name},
        }
    return {"schema_version": 1, "tasks": tasks}


def make_policy(days=(30, 90, 180), grace_hours=24) -> dict:
    """合法 policy:三任务 days 与共享 grace_hours。"""
    intervals = {name: {"days": d} for name, d in zip(sched.TASK_NAMES, days)}
    return {"schema_version": 1, "intervals": intervals, "grace_hours": grace_hours}


def joined_problems(result_or_report) -> str:
    """把顶层 problems 拼成一段文本便于子串断言。"""
    return " | ".join(result_or_report["problems"])


class TestValidateState:
    def test_ok_normalizes_timestamps_to_utc(self):
        state = make_state()
        state["tasks"]["anchor"]["last_success_at"] = "2026-09-11T20:00:00+08:00"
        state["tasks"]["worm_archive"]["last_success_at"] = "2026-09-11T04:00:00Z"
        result = sched.validate_state(state)
        assert result["ok"] is True
        assert result["problems"] == []
        assert result["value"]["tasks"]["anchor"].last_success_at == datetime(
            2026, 9, 11, 12, 0, tzinfo=timezone.utc
        )
        assert result["value"]["tasks"]["worm_archive"].last_success_at == datetime(
            2026, 9, 11, 4, 0, tzinfo=timezone.utc
        )

    def test_rejects_non_mapping(self):
        for bad in (None, [], "state", 7):
            result = sched.validate_state(bad)
            assert result["ok"] is False
            assert result["value"] is None
            assert "state must be a mapping" in joined_problems(result)

    def test_rejects_wrong_schema_version_and_extra_top_level_key(self):
        state = make_state()
        state["schema_version"] = 2
        state["surprise"] = 1
        result = sched.validate_state(state)
        assert result["ok"] is False
        text = joined_problems(result)
        assert "'schema_version' must be the integer 1" in text
        assert "top-level key 'surprise' is not allowed" in text

    def test_rejects_missing_task_and_extra_task(self):
        missing = make_state()
        del missing["tasks"]["offline_copy"]
        result = sched.validate_state(missing)
        assert result["ok"] is False
        assert "missing task 'offline_copy'" in joined_problems(result)

        extra = make_state()
        extra["tasks"]["recycle"] = dict(extra["tasks"]["anchor"])
        result = sched.validate_state(extra)
        assert result["ok"] is False
        assert "unexpected task 'recycle'" in joined_problems(result)

    def test_rejects_task_level_field_errors(self):
        state = make_state()
        state["tasks"]["anchor"]["extra"] = 1
        del state["tasks"]["worm_archive"]["source_sha256"]
        result = sched.validate_state(state)
        assert result["ok"] is False
        text = joined_problems(result)
        assert "task 'anchor': unexpected field 'extra'" in text
        assert "task 'worm_archive': missing field 'source_sha256'" in text

    def test_rejects_naive_or_malformed_timestamp(self):
        for bad in ("2026-09-11T12:00:00", "not-a-date", 12345):
            state = make_state()
            state["tasks"]["anchor"]["last_success_at"] = bad
            result = sched.validate_state(state)
            assert result["ok"] is False, bad
        state = make_state()
        state["tasks"]["anchor"]["last_success_at"] = "2026-09-11T12:00:00"
        assert "is naive" in joined_problems(sched.validate_state(state))

    def test_rejects_bad_hashes(self):
        for bad in ("A" * 64, "a" * 63, "g" * 64, 42, None):
            state = make_state()
            state["tasks"]["anchor"]["source_sha256"] = bad
            result = sched.validate_state(state)
            assert result["ok"] is False, bad
        state = make_state()
        state["tasks"]["anchor"]["source_sha256"] = "A" * 64
        assert "must be 64 lowercase hex characters" in joined_problems(
            sched.validate_state(state)
        )

    def test_rejects_bad_facts(self):
        state = make_state()
        state["tasks"]["anchor"]["facts"] = {}
        assert "'facts' must not be empty" in joined_problems(sched.validate_state(state))

        state = make_state()
        state["tasks"]["anchor"]["facts"] = {"nested": ["v"]}
        assert "'facts'['nested'] must be a scalar value" in joined_problems(
            sched.validate_state(state)
        )

        state = make_state()
        state["tasks"]["anchor"]["facts"] = {"ok": 1, 5: "v"}
        result = sched.validate_state(state)
        assert result["ok"] is False
        assert "'facts' key 5 must be a string" in joined_problems(result)


class TestValidatePolicy:
    def test_ok_returns_normalized_value(self):
        result = sched.validate_policy(make_policy())
        assert result["ok"] is True
        assert result["problems"] == []
        assert result["value"]["grace_hours"] == 24
        assert result["value"]["intervals"]["anchor"].days == 30
        assert result["value"]["intervals"]["offline_copy"].days == 180

    def test_rejects_non_mapping_and_bad_version(self):
        assert sched.validate_policy(None)["ok"] is False
        policy = make_policy()
        policy["schema_version"] = "1"
        result = sched.validate_policy(policy)
        assert result["ok"] is False
        assert "'schema_version' must be the integer 1" in joined_problems(result)

    def test_rejects_bad_days(self):
        for bad in (0, -1, True, 30.0, "30"):
            policy = make_policy()
            policy["intervals"]["anchor"]["days"] = bad
            result = sched.validate_policy(policy)
            assert result["ok"] is False, bad
        policy = make_policy()
        policy["intervals"]["anchor"]["days"] = 0
        assert "'days' must be a positive integer" in joined_problems(
            sched.validate_policy(policy)
        )

    def test_rejects_bad_grace_hours(self):
        for bad in (-1, True, 2.5, "24"):
            policy = make_policy()
            policy["grace_hours"] = bad
            result = sched.validate_policy(policy)
            assert result["ok"] is False, bad
        policy = make_policy()
        policy["grace_hours"] = -1
        assert "'grace_hours' must be a non-negative integer" in joined_problems(
            sched.validate_policy(policy)
        )

    def test_rejects_extra_or_missing_interval_task(self):
        policy = make_policy()
        policy["intervals"]["recycle"] = {"days": 7}
        assert "unexpected task 'recycle'" in joined_problems(
            sched.validate_policy(policy)
        )
        policy = make_policy()
        del policy["intervals"]["offline_copy"]
        result = sched.validate_policy(policy)
        assert result["ok"] is False
        assert "missing task 'offline_copy'" in joined_problems(result)

    def test_rejects_extra_top_level_key(self):
        policy = make_policy()
        policy["holiday"] = []
        result = sched.validate_policy(policy)
        assert result["ok"] is False
        assert "top-level key 'holiday' is not allowed" in joined_problems(result)


class TestEvaluateReadiness:
    def test_all_fresh_within_interval(self):
        report = sched.evaluate_readiness(make_state(), make_policy(), NOW)
        assert report["schema_version"] == 1
        assert report["overall"] == "fresh"
        assert report["generated_for"] == NOW.isoformat()
        for name in sched.TASK_NAMES:
            assert report["tasks"][name]["status"] == "fresh"
            assert report["tasks"][name]["problems"] == []
        anchor = report["tasks"]["anchor"]
        last = NOW - timedelta(days=7)
        assert anchor["next_due_at"] == (last + timedelta(days=30)).isoformat()
        assert anchor["overdue_at"] == (last + timedelta(days=30, hours=24)).isoformat()

    def test_due_at_exact_interval_boundary(self):
        state = make_state(ages=(timedelta(days=30),) * 3)
        report = sched.evaluate_readiness(state, make_policy(), NOW)
        assert report["tasks"]["anchor"]["status"] == "due"
        assert report["tasks"]["worm_archive"]["status"] == "fresh"
        assert report["tasks"]["offline_copy"]["status"] == "fresh"
        assert report["overall"] == "due"  # any due → due

    def test_due_inside_grace_and_overdue_at_exact_grace_boundary(self):
        policy = make_policy()
        inside = sched.evaluate_readiness(
            make_state(ages=(timedelta(days=30, hours=23),) * 3), policy, NOW
        )
        assert inside["tasks"]["anchor"]["status"] == "due"

        exact = sched.evaluate_readiness(
            make_state(ages=(timedelta(days=31),) * 3), policy, NOW
        )
        assert exact["tasks"]["anchor"]["status"] == "overdue"

    def test_fresh_just_before_due_and_zero_grace_window(self):
        policy = make_policy(grace_hours=0)
        before = sched.evaluate_readiness(
            make_state(ages=(timedelta(days=30, seconds=-1),) * 3), policy, NOW
        )
        assert before["tasks"]["anchor"]["status"] == "fresh"

        exact = sched.evaluate_readiness(
            make_state(ages=(timedelta(days=30),) * 3), policy, NOW
        )
        assert exact["tasks"]["anchor"]["status"] == "overdue"  # due 窗口为空

    def test_overall_worst_severity_wins(self):
        state = make_state(
            ages=(timedelta(days=40), timedelta(days=90, hours=12), timedelta(days=7))
        )
        report = sched.evaluate_readiness(state, make_policy(), NOW)
        statuses = {n: report["tasks"][n]["status"] for n in sched.TASK_NAMES}
        assert statuses == {
            "anchor": "overdue",
            "worm_archive": "due",
            "offline_copy": "fresh",
        }
        assert report["overall"] == "overdue"

        report = sched.evaluate_readiness(
            make_state(ages=(timedelta(days=7), timedelta(days=90), timedelta(days=7))),
            make_policy(),
            NOW,
        )
        assert report["overall"] == "due"

    def test_future_last_success_blocks_only_that_task(self):
        state = make_state(ages=(timedelta(days=7), timedelta(days=-1), timedelta(days=7)))
        report = sched.evaluate_readiness(state, make_policy(), NOW)
        assert report["tasks"]["anchor"]["status"] == "fresh"
        assert report["tasks"]["worm_archive"]["status"] == "blocked"
        assert report["tasks"]["offline_copy"]["status"] == "fresh"
        assert report["overall"] == "blocked"
        assert "'last_success_at' is in the future" in joined_problems(report)
        assert "'last_success_at' is in the future" in " | ".join(
            report["tasks"]["worm_archive"]["problems"]
        )
        assert report["tasks"]["worm_archive"]["next_due_at"] is None

    def test_invalid_hash_blocks_only_that_task(self):
        state = make_state()
        state["tasks"]["anchor"]["source_sha256"] = "XYZ"
        report = sched.evaluate_readiness(state, make_policy(), NOW)
        assert report["tasks"]["anchor"]["status"] == "blocked"
        assert report["tasks"]["worm_archive"]["status"] == "fresh"
        assert report["tasks"]["offline_copy"]["status"] == "fresh"
        assert report["overall"] == "blocked"
        assert "must be 64 lowercase hex characters" in joined_problems(report)

    def test_naive_timestamp_blocks_only_that_task(self):
        state = make_state()
        state["tasks"]["worm_archive"]["last_success_at"] = "2026-09-11T04:00:00"
        report = sched.evaluate_readiness(state, make_policy(), NOW)
        assert report["tasks"]["anchor"]["status"] == "fresh"
        assert report["tasks"]["worm_archive"]["status"] == "blocked"
        assert report["tasks"]["offline_copy"]["status"] == "fresh"
        assert "is naive" in joined_problems(report)

    def test_bad_days_blocks_only_that_task(self):
        policy = make_policy()
        policy["intervals"]["offline_copy"]["days"] = 0
        report = sched.evaluate_readiness(make_state(), policy, NOW)
        assert report["tasks"]["anchor"]["status"] == "fresh"
        assert report["tasks"]["worm_archive"]["status"] == "fresh"
        assert report["tasks"]["offline_copy"]["status"] == "blocked"
        assert report["overall"] == "blocked"
        assert "'days' must be a positive integer" in joined_problems(report)

    def test_extra_task_field_blocks_only_that_task(self):
        state = make_state()
        state["tasks"]["anchor"]["extra"] = 1
        report = sched.evaluate_readiness(state, make_policy(), NOW)
        assert report["tasks"]["anchor"]["status"] == "blocked"
        assert report["tasks"]["worm_archive"]["status"] == "fresh"
        assert "unexpected field 'extra'" in joined_problems(report)

    def test_bad_grace_hours_blocks_every_task(self):
        policy = make_policy()
        policy["grace_hours"] = -1
        report = sched.evaluate_readiness(make_state(), policy, NOW)
        assert report["overall"] == "blocked"
        assert all(t["status"] == "blocked" for t in report["tasks"].values())
        assert "'grace_hours' must be a non-negative integer" in joined_problems(report)

    def test_malformed_state_blocks_every_task(self):
        state = make_state()
        state["extra"] = True
        report = sched.evaluate_readiness(state, make_policy(), NOW)
        assert report["overall"] == "blocked"
        assert all(t["status"] == "blocked" for t in report["tasks"].values())
        assert "top-level key 'extra' is not allowed" in joined_problems(report)

    def test_malformed_policy_blocks_every_task(self):
        policy = make_policy()
        del policy["intervals"]
        report = sched.evaluate_readiness(make_state(), policy, NOW)
        assert report["overall"] == "blocked"
        assert "missing top-level key 'intervals'" in joined_problems(report)

    def test_missing_task_in_state_blocks(self):
        state = make_state()
        del state["tasks"]["offline_copy"]
        report = sched.evaluate_readiness(state, make_policy(), NOW)
        assert report["overall"] == "blocked"
        assert "missing task 'offline_copy'" in joined_problems(report)

    def test_naive_or_non_datetime_now_is_reported_not_raised(self):
        report = sched.evaluate_readiness(make_state(), make_policy(), NOW.replace(tzinfo=None))
        assert report["overall"] == "blocked"
        assert report["generated_for"] is None
        assert "'now' must be timezone-aware" in joined_problems(report)

        report = sched.evaluate_readiness(make_state(), make_policy(), "2026-09-18")
        assert report["overall"] == "blocked"
        assert "'now' must be a datetime instance" in joined_problems(report)

    def test_non_mapping_inputs_do_not_raise(self):
        report = sched.evaluate_readiness("state", 42, NOW)
        assert report["overall"] == "blocked"
        assert report["schema_version"] == 1


class TestDeterminismAndSerialization:
    def test_repeat_evaluation_is_identical(self):
        state = make_state(ages=(timedelta(days=35),) * 3)
        first = sched.evaluate_readiness(state, make_policy(), NOW)
        second = sched.evaluate_readiness(state, make_policy(), NOW)
        assert first == second

    def test_report_is_canonical_json_serializable(self):
        report = sched.evaluate_readiness(
            make_state(ages=(timedelta(days=-1),) * 3), make_policy(), NOW
        )
        assert json.loads(json.dumps(report)) == report


class TestSourceDiscipline:
    def test_module_source_has_no_forbidden_tokens(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for token in FORBIDDEN_TOKENS:
            assert token not in source, f"forbidden token {token!r} found"

    def test_module_exposes_only_pure_stdlib_imports(self):
        source = SCRIPT.read_text(encoding="utf-8")
        import_lines = [
            line for line in source.splitlines() if line.startswith(("import ", "from "))
        ]
        assert import_lines, "expected at least one import statement"
        allowed_roots = {
            "re", "collections", "dataclasses", "datetime", "typing", "__future__",
        }
        for line in import_lines:
            root = line.split()[1].split(".")[0]
            assert root in allowed_roots, f"unexpected import: {line!r}"
