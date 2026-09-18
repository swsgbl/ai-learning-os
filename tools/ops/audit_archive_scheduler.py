"""M14-50 audit/archive readiness evaluator (pure standard library).

Schema v1 state and policy documents are validated with strict rejection
semantics, then each tracked task (``anchor``, ``worm_archive``,
``offline_copy``) is classified as of a caller-supplied ``now``:

* ``fresh``   -- ``now < last_success_at + days``
* ``due``     -- interval elapsed (inclusive) but still inside grace
* ``overdue`` -- grace window exhausted (``elapsed >= days + grace``)
* ``blocked`` -- document-level malformation, a task-level field
  problem (invalid hash, naive/unparsable timestamp, bad interval),
  or a ``last_success_at`` in the future

``validate_state``/``validate_policy`` reject a whole document on any
problem (all-or-nothing).  ``evaluate_readiness`` keeps the same
document-level gate for structural/schema errors but isolates
task-level field problems: only the affected task is ``blocked``.

Functions perform no I/O of any kind (no CLI, files, network,
environment access, or child processes) and never raise for malformed
input: errors surface as precise ``problems`` strings.  Every report is
deterministic and canonical-serializable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

SCHEMA_VERSION = 1
TASK_NAMES = ("anchor", "worm_archive", "offline_copy")
TASK_FIELDS = ("last_success_at", "source_sha256", "facts")
INTERVAL_FIELDS = ("days",)
STATE_KEYS = ("schema_version", "tasks")
POLICY_KEYS = ("schema_version", "intervals", "grace_hours")

STATUS_FRESH = "fresh"
STATUS_DUE = "due"
STATUS_OVERDUE = "overdue"
STATUS_BLOCKED = "blocked"
# Worst-of aggregation order; fresh is the implicit fallback.
_SEVERITY_ORDER = (STATUS_BLOCKED, STATUS_OVERDUE, STATUS_DUE)

_HEX64_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class TaskRecord:
    """One validated task entry; ``last_success_at`` normalized to UTC."""

    last_success_at: datetime
    source_sha256: str
    facts: Mapping[str, Any]


@dataclass(frozen=True)
class TaskInterval:
    """One validated per-task interval from the policy document."""

    days: int


def validate_state(state: Any) -> dict:
    """Validate a state document (schema v1) without raising.

    Returns ``{"ok": bool, "value": normalized-state-or-None,
    "problems": [...]}``; any problem rejects the whole document.
    """
    problems: list[str] = []
    records: dict[str, TaskRecord] = {}
    raw_tasks = _check_state_skeleton(state, problems)
    if raw_tasks is not None:
        for name in TASK_NAMES:
            record = _build_task_entry(raw_tasks[name], name, problems)
            if record is not None:
                records[name] = record
    ok = not problems and set(records) == set(TASK_NAMES)
    value = {"schema_version": SCHEMA_VERSION, "tasks": records} if ok else None
    return {"ok": ok, "value": value, "problems": problems}


def validate_policy(policy: Any) -> dict:
    """Validate a policy document (schema v1) without raising.

    Returns ``{"ok": bool, "value": normalized-policy-or-None,
    "problems": [...]}``; any problem rejects the whole document.
    """
    problems: list[str] = []
    intervals: dict[str, TaskInterval] = {}
    skeleton = _check_policy_skeleton(policy, problems)
    if skeleton is not None:
        for name in TASK_NAMES:
            interval = _build_interval_entry(skeleton[0][name], name, problems)
            if interval is not None:
                intervals[name] = interval
    ok = not problems and set(intervals) == set(TASK_NAMES)
    if ok:
        value = {
            "schema_version": SCHEMA_VERSION,
            "intervals": intervals,
            "grace_hours": skeleton[1],
        }
    else:
        value = None
    return {"ok": ok, "value": value, "problems": problems}


def evaluate_readiness(state: Any, policy: Any, now: Any) -> dict:
    """Classify every tracked task against the policy as of ``now``.

    Never raises for evaluation errors: document-level malformation
    blocks every task, while task-level field problems (bad hash,
    naive timestamp, bad interval) or a future ``last_success_at``
    block only the affected task.  Returns a deterministic report dict
    with ``schema_version``, ``generated_for`` (UTC ISO 8601),
    ``overall`` (worst-of severity), per-task ``status`` /
    ``next_due_at`` / ``overdue_at`` / ``problems``, and the merged
    top-level ``problems`` list.
    """
    problems: list[str] = []
    raw_tasks = _check_state_skeleton(state, problems)
    skeleton = _check_policy_skeleton(policy, problems)
    raw_intervals: Any = None
    grace_hours: Any = None
    if skeleton is not None:
        raw_intervals, grace_hours = skeleton
    now_dt, now_reason = _normalize_now(now)
    if now_reason is not None:
        problems.append(now_reason)
    usable = raw_tasks is not None and raw_intervals is not None and now_dt is not None
    tasks_report: dict[str, dict[str, Any]] = {}
    for name in TASK_NAMES:
        entry_problems: list[str] = []
        record: TaskRecord | None = None
        interval: TaskInterval | None = None
        if usable:
            record = _build_task_entry(raw_tasks[name], name, entry_problems)
            interval = _build_interval_entry(raw_intervals[name], name, entry_problems)
        status = STATUS_BLOCKED
        next_due_at = None
        overdue_at = None
        if record is not None and interval is not None:
            if record.last_success_at > now_dt:
                entry_problems.append(
                    f"task '{name}': 'last_success_at' is in the future"
                    " relative to 'now'"
                )
            else:
                due_at = record.last_success_at + timedelta(days=interval.days)
                late_at = due_at + timedelta(hours=grace_hours)
                if now_dt < due_at:
                    status = STATUS_FRESH
                elif now_dt < late_at:
                    status = STATUS_DUE
                else:
                    status = STATUS_OVERDUE
                next_due_at = _format_utc(due_at)
                overdue_at = _format_utc(late_at)
        tasks_report[name] = {
            "status": status,
            "next_due_at": next_due_at,
            "overdue_at": overdue_at,
            "problems": entry_problems,
        }
        problems.extend(entry_problems)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_for": None if now_dt is None else _format_utc(now_dt),
        "overall": _overall_status(tasks_report),
        "tasks": tasks_report,
        "problems": problems,
    }


def _check_state_skeleton(state: Any, problems: list[str]) -> dict | None:
    """Check document-level structure; return the raw tasks mapping.

    Any document-level problem (non-mapping, bad version, extra or
    missing top-level keys, non-mapping or wrong-task-set ``tasks``)
    reports a problem and yields ``None``.
    """
    before = len(problems)
    if _reject_non_mapping(state, "state", problems):
        return None
    _check_envelope(state, "state", STATE_KEYS, problems)
    raw_tasks = state.get("tasks")
    if not isinstance(raw_tasks, dict):
        if "tasks" in state:
            problems.append("state: 'tasks' must be a mapping")
        return None
    _check_exact_keys(raw_tasks, "state: tasks", TASK_NAMES, "task", problems)
    if len(problems) != before or set(raw_tasks) != set(TASK_NAMES):
        return None
    return raw_tasks


def _check_policy_skeleton(policy: Any, problems: list[str]) -> tuple[dict, int] | None:
    """Check document-level structure; return (raw intervals, grace)."""
    before = len(problems)
    if _reject_non_mapping(policy, "policy", problems):
        return None
    _check_envelope(policy, "policy", POLICY_KEYS, problems)
    if "grace_hours" not in policy:
        return None  # missing key already reported by _check_envelope
    grace_hours = policy["grace_hours"]
    if type(grace_hours) is not int or grace_hours < 0:
        problems.append("policy: 'grace_hours' must be a non-negative integer")
        return None
    raw_intervals = policy.get("intervals")
    if not isinstance(raw_intervals, dict):
        if "intervals" in policy:
            problems.append("policy: 'intervals' must be a mapping")
        return None
    _check_exact_keys(raw_intervals, "policy: intervals", TASK_NAMES, "task", problems)
    if len(problems) != before or set(raw_intervals) != set(TASK_NAMES):
        return None
    return raw_intervals, grace_hours


def _build_task_entry(entry: Any, name: str, problems: list[str]) -> TaskRecord | None:
    """Validate one task entry (key set + field values); task-level."""
    label = f"state: task '{name}'"
    before = len(problems)
    if _reject_non_mapping(entry, label, problems):
        return None
    _check_exact_keys(entry, label, TASK_FIELDS, "field", problems)
    record = _build_task_record(entry, label, problems)
    if len(problems) != before:
        return None
    return record


def _build_interval_entry(entry: Any, name: str, problems: list[str]) -> TaskInterval | None:
    """Validate one interval entry (key set + days value); task-level."""
    label = f"policy: intervals: task '{name}'"
    before = len(problems)
    if _reject_non_mapping(entry, label, problems):
        return None
    _check_exact_keys(entry, label, INTERVAL_FIELDS, "field", problems)
    if "days" in entry:
        days = entry["days"]
        if type(days) is not int or days < 1:
            problems.append(f"{label}: 'days' must be a positive integer")
    if len(problems) != before:
        return None
    if "days" not in entry:
        return None  # missing key already reported by _check_exact_keys
    return TaskInterval(days=entry["days"])


def _reject_non_mapping(value: Any, label: str, problems: list[str]) -> bool:
    """Report and return True when ``value`` is not a plain mapping."""
    if not isinstance(value, dict):
        problems.append(f"{label} must be a mapping")
        return True
    return False


def _check_envelope(doc: dict, label: str, allowed: tuple[str, ...], problems: list[str]) -> None:
    """Check the shared v1 envelope: version, extra keys, missing keys."""
    if "schema_version" not in doc:
        problems.append(f"{label}: missing top-level key 'schema_version'")
    else:
        version = doc["schema_version"]
        if type(version) is not int or version != SCHEMA_VERSION:
            problems.append(
                f"{label}: 'schema_version' must be the integer {SCHEMA_VERSION}"
            )
    for key in sorted((key for key in doc if key not in allowed), key=repr):
        problems.append(f"{label}: top-level key {key!r} is not allowed")
    for key in allowed:
        if key not in doc:
            problems.append(f"{label}: missing top-level key {key!r}")


def _check_exact_keys(
    mapping: dict, label: str, allowed: tuple[str, ...], noun: str, problems: list[str]
) -> None:
    """Require exactly ``allowed`` keys; report extras (sorted) then missing."""
    for key in sorted((key for key in mapping if key not in allowed), key=repr):
        problems.append(f"{label}: unexpected {noun} {key!r}")
    for key in allowed:
        if key not in mapping:
            problems.append(f"{label}: missing {noun} {key!r}")


def _parse_timestamp(text: str) -> tuple[datetime | None, str | None]:
    """Parse tz-aware ISO 8601; return (UTC-normalized dt, failure reason)."""
    raw = f"{text[:-1]}+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None, f"is not a valid ISO 8601 timestamp: {text!r}"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None, f"is naive; a timezone-aware timestamp is required: {text!r}"
    return parsed.astimezone(timezone.utc), None


def _is_scalar(value: Any) -> bool:
    """True for JSON scalar values (str/int/float/bool/None)."""
    return value is None or isinstance(value, (bool, int, float, str))


def _check_facts(value: Any, label: str, problems: list[str]) -> None:
    """Validate a non-empty str-to-scalar ``facts`` mapping."""
    if not isinstance(value, dict):
        problems.append(f"{label}: 'facts' must be a mapping")
        return
    if not value:
        problems.append(f"{label}: 'facts' must not be empty")
        return
    for key, item in value.items():
        if not isinstance(key, str):
            problems.append(f"{label}: 'facts' key {key!r} must be a string")
        if not _is_scalar(item):
            problems.append(f"{label}: 'facts'[{key!r}] must be a scalar value")


def _build_task_record(entry: dict, label: str, problems: list[str]) -> TaskRecord | None:
    """Validate field values; return a TaskRecord or None on any problem."""
    before = len(problems)
    last_dt: datetime | None = None
    if "last_success_at" in entry:
        raw_last = entry["last_success_at"]
        if not isinstance(raw_last, str):
            problems.append(f"{label}: 'last_success_at' must be an ISO 8601 string")
        else:
            last_dt, reason = _parse_timestamp(raw_last)
            if reason is not None:
                problems.append(f"{label}: 'last_success_at' {reason}")
    if "source_sha256" in entry:
        digest = entry["source_sha256"]
        if not isinstance(digest, str) or _HEX64_RE.fullmatch(digest) is None:
            problems.append(f"{label}: 'source_sha256' must be 64 lowercase hex characters")
    if "facts" in entry:
        _check_facts(entry["facts"], label, problems)
    if len(problems) != before:
        return None
    if last_dt is None or any(field not in entry for field in TASK_FIELDS):
        return None  # missing-field problems already reported upstream
    return TaskRecord(
        last_success_at=last_dt,
        source_sha256=entry["source_sha256"],
        facts=dict(entry["facts"]),
    )


def _normalize_now(now: Any) -> tuple[datetime | None, str | None]:
    """Require a timezone-aware datetime; return (UTC dt, failure reason)."""
    if not isinstance(now, datetime):
        return None, "'now' must be a datetime instance"
    if now.tzinfo is None or now.utcoffset() is None:
        return None, "'now' must be timezone-aware"
    return now.astimezone(timezone.utc), None


def _overall_status(tasks_report: Mapping[str, Mapping[str, Any]]) -> str:
    """Aggregate per-task statuses: blocked > overdue > due > fresh."""
    statuses = {entry["status"] for entry in tasks_report.values()}
    for candidate in _SEVERITY_ORDER:
        if candidate in statuses:
            return candidate
    return STATUS_FRESH


def _format_utc(moment: datetime) -> str:
    """Canonical UTC ISO 8601 rendering used by every report timestamp."""
    return moment.astimezone(timezone.utc).isoformat()
