"""构建 Android 冒烟测试运行摘要的纯函数模块。

不执行任何设备/命令操作，只做数据校验与汇总，
方便单测与下游序列化（json.dumps）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

SCHEMA_VERSION = 1

# stages 中允许的状态集合
ALLOWED_STAGE_STATUSES = frozenset(
    {"pending", "running", "passed", "failed", "skipped"}
)

# overall_status 推导顺序
_STATUS_PRIORITY = ("failed", "incomplete", "passed", "skipped")

# config 中允许的非敏感元数据字段
_ALLOWED_CONFIG_KEYS = (
    "serial",
    "device_type",
    "apk_sha256",
    "package_name",
    "started_at_utc",
    "finished_at_utc",
    "output_dir",
)

_REQUIRED_STAGE_KEYS = ("name", "status", "detail", "artifacts")
_REQUIRED_REQUEST_STATS_KEYS = ("expected", "unexpected", "total")


def _ensure_dict(value: object, arg_name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{arg_name} must be a dict, got {type(value).__name__}")
    return value


def _parse_utc(value: object, field: str) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"config.{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"config.{field} is not a valid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _compute_duration_seconds(config: dict) -> float | None:
    started = _parse_utc(config.get("started_at_utc"), "started_at_utc")
    finished = _parse_utc(config.get("finished_at_utc"), "finished_at_utc")
    if started is None or finished is None:
        return None
    duration = (finished - started).total_seconds()
    if duration < 0:
        raise ValueError("finished_at_utc is earlier than started_at_utc")
    return duration


def _validate_stages(stages: object) -> list[dict]:
    stages = _ensure_dict_list(stages, "stages")
    for index, stage in enumerate(stages):
        stage = _ensure_dict(stage, f"stages[{index}]")
        for key in _REQUIRED_STAGE_KEYS:
            if key not in stage:
                raise ValueError(f"stages[{index}] is missing required key {key!r}")
        if not isinstance(stage["name"], str) or not stage["name"]:
            raise ValueError(f"stages[{index}].name must be a non-empty string")
        if stage["status"] not in ALLOWED_STAGE_STATUSES:
            raise ValueError(
                f"stages[{index}].status must be one of "
                f"{sorted(ALLOWED_STAGE_STATUSES)}, got {stage['status']!r}"
            )
    return stages


def _ensure_dict_list(value: object, arg_name: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{arg_name} must be a list, got {type(value).__name__}")
    return value


def _validate_request_stats(request_stats: object) -> dict:
    request_stats = _ensure_dict(request_stats, "request_stats")
    for key in _REQUIRED_REQUEST_STATS_KEYS:
        if key not in request_stats:
            raise ValueError(f"request_stats is missing required key {key!r}")
        value = request_stats[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"request_stats.{key} must be a non-negative int")
    if request_stats["expected"] + request_stats["unexpected"] != request_stats["total"]:
        raise ValueError(
            "request_stats.total must equal expected + unexpected, "
            f"got {request_stats['total']} != "
            f"{request_stats['expected']} + {request_stats['unexpected']}"
        )
    return request_stats


def _derive_overall_status(stages: list[dict]) -> str:
    statuses = {stage["status"] for stage in stages}
    if "failed" in statuses:
        return "failed"
    if "running" in statuses or "pending" in statuses:
        return "incomplete"
    if statuses == {"passed"}:
        return "passed"
    if statuses == {"skipped"}:
        return "skipped"
    # 混合 passed/skipped 且无失败/未完成，视为通过
    return "passed"


_OVERALL_EXIT_CODES = {
    "passed": 0,
    "skipped": 0,
    "failed": 1,
    "incomplete": 2,
}


def build_summary(
    config: dict,
    stages: list[dict],
    request_stats: dict,
    logcat_stats: dict,
) -> dict:
    """根据运行元数据与各阶段结果构建可序列化的摘要 dict。

    纯函数：不产生副作用，不访问设备或文件系统。
    任何参数缺失或类型不符时抛出 ValueError。
    """
    config = _ensure_dict(config, "config")
    stages = _validate_stages(stages)
    request_stats = _validate_request_stats(request_stats)
    logcat_stats = _ensure_dict(logcat_stats, "logcat_stats")

    unknown_keys = set(config) - set(_ALLOWED_CONFIG_KEYS)
    if unknown_keys:
        raise ValueError(
            f"config contains unexpected keys: {sorted(unknown_keys)}; "
            f"allowed: {list(_ALLOWED_CONFIG_KEYS)}"
        )

    duration_seconds = _compute_duration_seconds(config)
    overall_status = _derive_overall_status(stages)

    summary = {
        "schema_version": SCHEMA_VERSION,
        "serial": config.get("serial"),
        "device_type": config.get("device_type"),
        "apk_sha256": config.get("apk_sha256"),
        "package_name": config.get("package_name"),
        "started_at_utc": config.get("started_at_utc"),
        "finished_at_utc": config.get("finished_at_utc"),
        "output_dir": config.get("output_dir"),
        "duration_seconds": duration_seconds,
        "overall_status": overall_status,
        "stages": [
            {
                "name": stage["name"],
                "status": stage["status"],
                "detail": stage["detail"],
                "artifacts": list(stage["artifacts"]),
            }
            for stage in stages
        ],
        "request_stats": {
            "expected": request_stats["expected"],
            "unexpected": request_stats["unexpected"],
            "total": request_stats["total"],
        },
        "logcat": dict(logcat_stats),
        "exit_code": _OVERALL_EXIT_CODES[overall_status],
        "has_blocking_issue": overall_status == "failed",
    }

    # 确保产物本身可直接 json.dumps（提前暴露不可序列化对象）
    json.dumps(summary)
    return summary
