"""tools/android_smoke/summary.py 的单元测试。"""

import json

import pytest

from tools.android_smoke.summary import build_summary

BASE_CONFIG = {
    "serial": "EMULATOR-5554",
    "apk_sha256": "a" * 64,
    "package_name": "com.example.app",
    "started_at_utc": "2026-09-07T10:00:00Z",
    "finished_at_utc": "2026-09-07T10:02:30Z",
    "output_dir": "out/smoke/2026-09-07T10-00-00Z",
}

BASE_REQUEST_STATS = {"expected": 8, "unexpected": 1, "total": 9}

BASE_LOGCAT_STATS = {"crashes": 0, "anrs": 0, "lines": 1234}


def make_stage(name="install", status="passed", detail="", artifacts=()):
    return {"name": name, "status": status, "detail": detail, "artifacts": list(artifacts)}


def test_all_passed() -> None:
    stages = [
        make_stage("install", "passed", artifacts=["out/install.log"]),
        make_stage("launch", "passed"),
        make_stage("requests", "passed"),
    ]
    summary = build_summary(dict(BASE_CONFIG), stages, dict(BASE_REQUEST_STATS), dict(BASE_LOGCAT_STATS))
    assert summary["overall_status"] == "passed"
    assert summary["exit_code"] == 0
    assert summary["has_blocking_issue"] is False
    assert summary["schema_version"] == 1


def test_any_failed_is_failed() -> None:
    stages = [make_stage("install", "passed"), make_stage("launch", "failed", detail="boom")]
    summary = build_summary(dict(BASE_CONFIG), stages, dict(BASE_REQUEST_STATS), dict(BASE_LOGCAT_STATS))
    assert summary["overall_status"] == "failed"
    assert summary["exit_code"] == 1
    assert summary["has_blocking_issue"] is True


@pytest.mark.parametrize("unfinished", ["running", "pending"])
def test_incomplete_when_running_or_pending(unfinished: str) -> None:
    stages = [make_stage("install", "passed"), make_stage("launch", unfinished)]
    summary = build_summary(dict(BASE_CONFIG), stages, dict(BASE_REQUEST_STATS), dict(BASE_LOGCAT_STATS))
    assert summary["overall_status"] == "incomplete"
    assert summary["exit_code"] == 2
    assert summary["has_blocking_issue"] is False


def test_failed_beats_incomplete() -> None:
    stages = [make_stage("a", "running"), make_stage("b", "failed")]
    summary = build_summary(dict(BASE_CONFIG), stages, dict(BASE_REQUEST_STATS), dict(BASE_LOGCAT_STATS))
    assert summary["overall_status"] == "failed"


def test_all_skipped() -> None:
    stages = [make_stage("a", "skipped"), make_stage("b", "skipped")]
    summary = build_summary(dict(BASE_CONFIG), stages, dict(BASE_REQUEST_STATS), dict(BASE_LOGCAT_STATS))
    assert summary["overall_status"] == "skipped"
    assert summary["exit_code"] == 0
    assert summary["has_blocking_issue"] is False


def test_passed_and_skipped_mix_is_passed() -> None:
    stages = [make_stage("a", "passed"), make_stage("b", "skipped")]
    summary = build_summary(dict(BASE_CONFIG), stages, dict(BASE_REQUEST_STATS), dict(BASE_LOGCAT_STATS))
    assert summary["overall_status"] == "passed"


def test_duration_seconds() -> None:
    summary = build_summary(
        dict(BASE_CONFIG),
        [make_stage()],
        dict(BASE_REQUEST_STATS),
        dict(BASE_LOGCAT_STATS),
    )
    assert summary["duration_seconds"] == 150.0


def test_duration_none_when_timestamps_missing() -> None:
    config = dict(BASE_CONFIG)
    config.pop("finished_at_utc")
    summary = build_summary(config, [make_stage()], dict(BASE_REQUEST_STATS), dict(BASE_LOGCAT_STATS))
    assert summary["duration_seconds"] is None


def test_duration_negative_raises() -> None:
    config = dict(BASE_CONFIG)
    config["started_at_utc"] = "2026-09-07T10:03:00Z"
    with pytest.raises(ValueError, match="earlier"):
        build_summary(config, [make_stage()], dict(BASE_REQUEST_STATS), dict(BASE_LOGCAT_STATS))


@pytest.mark.parametrize(
    "arg_name, bad_value",
    [
        ("config", []),
        ("stages", {}),
        ("request_stats", None),
        ("logcat_stats", 42),
    ],
)
def test_non_dict_args_raise(arg_name: str, bad_value: object) -> None:
    kwargs = {
        "config": dict(BASE_CONFIG),
        "stages": [make_stage()],
        "request_stats": dict(BASE_REQUEST_STATS),
        "logcat_stats": dict(BASE_LOGCAT_STATS),
    }
    kwargs[arg_name] = bad_value
    with pytest.raises(ValueError, match=arg_name):
        build_summary(**kwargs)


def test_stages_must_be_list_of_dicts() -> None:
    with pytest.raises(ValueError, match="stages"):
        build_summary(dict(BASE_CONFIG), ["install"], dict(BASE_REQUEST_STATS), dict(BASE_LOGCAT_STATS))


def test_stage_missing_key_raises() -> None:
    stage = make_stage()
    del stage["detail"]
    with pytest.raises(ValueError, match="detail"):
        build_summary(dict(BASE_CONFIG), [stage], dict(BASE_REQUEST_STATS), dict(BASE_LOGCAT_STATS))


def test_stage_invalid_status_raises() -> None:
    with pytest.raises(ValueError, match="status"):
        build_summary(
            dict(BASE_CONFIG),
            [make_stage(status="DONE")],
            dict(BASE_REQUEST_STATS),
            dict(BASE_LOGCAT_STATS),
        )


def test_request_stats_missing_key_raises() -> None:
    stats = dict(BASE_REQUEST_STATS)
    del stats["unexpected"]
    with pytest.raises(ValueError, match="unexpected"):
        build_summary(dict(BASE_CONFIG), [make_stage()], stats, dict(BASE_LOGCAT_STATS))


def test_request_stats_total_mismatch_raises() -> None:
    stats = {"expected": 5, "unexpected": 2, "total": 9}
    with pytest.raises(ValueError, match="total"):
        build_summary(dict(BASE_CONFIG), [make_stage()], stats, dict(BASE_LOGCAT_STATS))


def test_config_unknown_key_raises() -> None:
    config = dict(BASE_CONFIG)
    config["api_token"] = "secret"
    with pytest.raises(ValueError, match="api_token"):
        build_summary(config, [make_stage()], dict(BASE_REQUEST_STATS), dict(BASE_LOGCAT_STATS))


def test_device_type_allowed_and_passed_through() -> None:
    config = dict(BASE_CONFIG)
    config["device_type"] = "physical"
    summary = build_summary(config, [make_stage()], dict(BASE_REQUEST_STATS), dict(BASE_LOGCAT_STATS))
    assert summary["device_type"] == "physical"


def test_device_type_defaults_to_none_when_absent() -> None:
    summary = build_summary(
        dict(BASE_CONFIG), [make_stage()], dict(BASE_REQUEST_STATS), dict(BASE_LOGCAT_STATS)
    )
    assert summary["device_type"] is None


def test_json_serializable_without_sensitive_fields() -> None:
    stages = [
        make_stage("install", "passed", artifacts=["out/install.log"]),
        make_stage("requests", "failed", detail="1 unexpected", artifacts=["out/requests.json"]),
    ]
    summary = build_summary(dict(BASE_CONFIG), stages, dict(BASE_REQUEST_STATS), dict(BASE_LOGCAT_STATS))
    dumped = json.dumps(summary)
    assert "api_token" not in dumped
    assert "password" not in dumped
    assert "secret" not in dumped
    assert BASE_CONFIG["apk_sha256"] in dumped
    assert summary["logcat"] == BASE_LOGCAT_STATS
    # round-trip 保持一致
    assert json.loads(dumped) == summary
