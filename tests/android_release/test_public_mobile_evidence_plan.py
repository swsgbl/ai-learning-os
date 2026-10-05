"""Contract tests for tools.android_release.public_mobile_evidence_plan (M14-238).

All fixtures are synthetic — no network, no device, no Docker, no secret, no
subprocess, no env access. The planner is a plan-only, fail-closed, sanitized
gap report over **file-level facts** for the six M14-237 evidence roles; these
tests pin that contract: manifest v1 shape (schema/unknown/duplicate keys/
absolute/traversal paths/duplicate role targets/freshness bounds), per-role
present/basename/bytes/SHA-256 without ever parsing child report semantics
(garbage bytes stay clean), symlink/reparse/directory/empty-file blockers via
an injected Store and monkeypatched link detection, atomic output with
read-back verification and zero-residue refusals, an injected **fixed time**
for generated_at, sanitization (no absolute local paths in any report), and
the exact quoted M14-237 replay command.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.android_release import public_mobile_evidence_plan as plan
from tools.ops.monitoring_history import RealStore

FIXED_NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
FIXED_NOW_TEXT = "2026-10-05T12:00:00Z"


# ---------------------------------------------------------------- fixtures


def make_manifest(
    tmp: Path,
    *,
    schema=plan.MANIFEST_SCHEMA,
    restore="restore/report.json",
    edge="edge/report.json",
    smoke="smoke/report.json",
    cloudflare="cf/report.json",
    evidence="release-evidence.json",
    attestation="attestation.json",
    freshness=24,
    extra: dict | None = None,
    drop: tuple[str, ...] = (),
) -> Path:
    payload: dict = {"schema": schema}
    if restore is not None:
        payload["restore_preflight"] = restore
    if edge is not None:
        payload["edge_preflight"] = edge
    if smoke is not None:
        payload["device_smoke"] = smoke
    if cloudflare is not None:
        payload["cloudflare_preflight"] = cloudflare
    if evidence is not None:
        payload["release_evidence"] = evidence
    if attestation is not None:
        payload["attestation"] = attestation
    if freshness is not None:
        payload["freshness_hours"] = freshness
    if extra:
        payload.update(extra)
    for key in drop:
        payload.pop(key, None)
    path = tmp / "manifest.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def seed_role_files(tmp: Path, *, include_cf=True) -> dict[str, bytes]:
    """合成角色文件（内容故意非任何真实报告语义——planner 不解析语义）。"""
    payloads = {
        "restore/report.json": b'{"anything": true}\n',
        "edge/report.json": b"not even json \xe2\x9c\x93\n",
        "smoke/report.json": b"\x00binary-garbage-ok\xff",
        "release-evidence.json": b"{}",
        "attestation.json": b"human attestation placeholder\n",
    }
    if include_cf:
        payloads["cf/report.json"] = b"cf report bytes\n"
    for rel, data in payloads.items():
        target = tmp / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return payloads


def run(tmp: Path, manifest: Path, out_name="out", store=None):
    return plan.run_plan(
        store or RealStore(),
        manifest=manifest,
        output_dir=tmp / out_name,
        now=FIXED_NOW,
    )


def read_report(tmp: Path, out_name="out") -> dict:
    return json.loads(
        (tmp / out_name / plan.REPORT_JSON_NAME).read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------- 完整形态


def test_complete_plan_exit_zero_with_file_facts(tmp_path):
    manifest = make_manifest(tmp_path)
    payloads = seed_role_files(tmp_path)
    report, exit_code = run(tmp_path, manifest)
    assert exit_code == plan.EXIT_COMPLETE
    assert report["complete"] is True
    assert report["blockers"] == []
    assert report["generated_at"] == FIXED_NOW_TEXT
    assert report["schema"] == plan.SCHEMA
    assert report["tool"] == plan.TOOL_NAME
    for role, rel in (
        ("restore_preflight", "restore/report.json"),
        ("edge_preflight", "edge/report.json"),
        ("device_smoke", "smoke/report.json"),
        ("cloudflare_preflight", "cf/report.json"),
        ("release_evidence", "release-evidence.json"),
        ("attestation", "attestation.json"),
    ):
        entry = report["roles"][role]
        assert entry["present"] is True
        assert entry["basename"] == Path(rel).name
        assert entry["bytes"] == len(payloads[rel])
        assert entry["sha256"] == hashlib.sha256(payloads[rel]).hexdigest()
        assert entry["blockers"] == []
    # manifest 自身字节锚定
    raw = manifest.read_bytes()
    assert report["manifest"] == {
        "basename": "manifest.json",
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def test_reports_written_atomically_and_readable_back(tmp_path):
    manifest = make_manifest(tmp_path)
    seed_role_files(tmp_path)
    report, _ = run(tmp_path, manifest)
    out = tmp_path / "out"
    json_text = (out / plan.REPORT_JSON_NAME).read_text(encoding="utf-8")
    md_text = (out / plan.REPORT_MD_NAME).read_text(encoding="utf-8")
    assert json.loads(json_text) == report  # 写后重读与返回值一致
    assert json_text.endswith("\n")
    assert "complete" in md_text
    assert "M14-237 replay command" in md_text
    assert report["replay"]["command"] in md_text


def test_fixed_now_injected_not_wall_clock(tmp_path):
    manifest = make_manifest(tmp_path)
    seed_role_files(tmp_path)
    report, _ = run(tmp_path, manifest)
    assert report["generated_at"] == FIXED_NOW_TEXT


# ---------------------------------------------------------------- 缺口形态


def test_missing_required_file_is_blocker_exit_one(tmp_path):
    manifest = make_manifest(tmp_path)
    seed_role_files(tmp_path)
    (tmp_path / "smoke" / "report.json").unlink()
    report, exit_code = run(tmp_path, manifest)
    assert exit_code == plan.EXIT_BLOCKERS
    assert report["complete"] is False
    assert report["blockers"] == ["missing-file:device_smoke"]
    entry = report["roles"]["device_smoke"]
    assert entry["present"] is False
    assert entry["basename"] == "report.json"
    assert entry["bytes"] is None and entry["sha256"] is None


def test_optional_cloudflare_absent_is_not_a_blocker(tmp_path):
    manifest = make_manifest(tmp_path, cloudflare=None)
    seed_role_files(tmp_path, include_cf=False)
    report, exit_code = run(tmp_path, manifest)
    assert exit_code == plan.EXIT_COMPLETE
    entry = report["roles"]["cloudflare_preflight"]
    assert entry["present"] is False
    assert entry["required"] is False
    assert entry["blockers"] == []
    assert "--cloudflare-preflight" not in report["replay"]["command"]


def test_optional_cloudflare_key_omitted_entirely(tmp_path):
    manifest = make_manifest(tmp_path, cloudflare=None, drop=("cloudflare_preflight",))
    seed_role_files(tmp_path, include_cf=False)
    report, exit_code = run(tmp_path, manifest)
    assert exit_code == plan.EXIT_COMPLETE
    assert "--cloudflare-preflight" not in report["replay"]["command"]


def test_garbage_non_json_role_content_stays_clean(tmp_path):
    """核心契约：不解析子报告语义——任意字节只做哈希锚定，不产生 blocker。"""
    manifest = make_manifest(tmp_path)
    seed_role_files(tmp_path)
    report, exit_code = run(tmp_path, manifest)
    assert exit_code == plan.EXIT_COMPLETE
    for role in plan.ALL_ROLE_KEYS:
        assert report["roles"][role]["blockers"] == []


def test_directory_instead_of_file_is_blocker(tmp_path):
    manifest = make_manifest(tmp_path)
    seed_role_files(tmp_path)
    target = tmp_path / "attestation.json"
    target.unlink()
    target.mkdir()
    report, exit_code = run(tmp_path, manifest)
    assert exit_code == plan.EXIT_BLOCKERS
    assert report["blockers"] == ["directory-not-file:attestation"]


def test_empty_role_file_is_blocker(tmp_path):
    manifest = make_manifest(tmp_path)
    seed_role_files(tmp_path)
    (tmp_path / "release-evidence.json").write_bytes(b"")
    report, exit_code = run(tmp_path, manifest)
    assert exit_code == plan.EXIT_BLOCKERS
    assert report["blockers"] == ["empty-file:release_evidence"]


def test_unreadable_role_file_is_blocker(tmp_path):
    class FailingReadStore(RealStore):
        def read_bytes(self, path: Path) -> bytes:
            if path.name == "report.json" and path.parent.name == "edge":
                raise OSError("boom")
            return super().read_bytes(path)

    manifest = make_manifest(tmp_path)
    seed_role_files(tmp_path)
    report, exit_code = run(tmp_path, manifest, store=FailingReadStore())
    assert exit_code == plan.EXIT_BLOCKERS
    assert report["blockers"] == ["unreadable-file:edge_preflight"]


def test_symlinked_role_file_is_blocker_via_injected_store(tmp_path):
    class AlwaysSymlinkStore(RealStore):
        def is_symlink(self, path: Path) -> bool:
            return path.name == "report.json" and path.parent.name == "smoke"

    manifest = make_manifest(tmp_path)
    seed_role_files(tmp_path)
    report, exit_code = run(tmp_path, manifest, store=AlwaysSymlinkStore())
    assert exit_code == plan.EXIT_BLOCKERS
    assert report["blockers"] == ["symlink-file:device_smoke"]


def test_reparse_role_file_is_blocker(tmp_path, monkeypatch):
    monkeypatch.setattr(
        plan, "is_link_or_reparse",
        lambda path: path.name == "attestation.json",
    )
    manifest = make_manifest(tmp_path)
    seed_role_files(tmp_path)
    report, exit_code = run(tmp_path, manifest)
    assert exit_code == plan.EXIT_BLOCKERS
    assert report["blockers"] == ["reparse-file:attestation"]


# ---------------------------------------------------------------- manifest 契约（invalid / exit 2）


@pytest.mark.parametrize(
    "payload_text,code",
    [
        (f'{{"schema": "{plan.MANIFEST_SCHEMA}", "restore_preflight": "a"',
         "manifest-invalid-json"),
        ("[]", "manifest-not-an-object"),
        ('{"schema": "wrong/1"}', "schema-mismatch"),
        ('{"no_schema": true}', "schema-mismatch"),
    ],
)
def test_invalid_manifest_shapes_refuse_zero_write(tmp_path, payload_text, code):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(payload_text, encoding="utf-8")
    with pytest.raises(plan.PlanInvalid) as caught:
        run(tmp_path, manifest)
    assert code in str(caught.value)
    assert not (tmp_path / "out").exists()


def test_manifest_missing_refused(tmp_path):
    with pytest.raises(plan.PlanInvalid) as caught:
        run(tmp_path, tmp_path / "nope.json")
    assert "manifest-missing" in str(caught.value)
    assert not (tmp_path / "out").exists()


def test_manifest_empty_file_refused(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b"")
    with pytest.raises(plan.PlanInvalid) as caught:
        run(tmp_path, manifest)
    assert "manifest-empty" in str(caught.value)


def test_unknown_top_level_key_refused(tmp_path):
    manifest = make_manifest(tmp_path, extra={"unexpected": "x"})
    seed_role_files(tmp_path)
    with pytest.raises(plan.PlanInvalid) as caught:
        run(tmp_path, manifest)
    assert "unknown-key:unexpected" in str(caught.value)


def test_duplicate_top_level_key_refused(tmp_path):
    payload = json.dumps({
        "schema": plan.MANIFEST_SCHEMA,
        "restore_preflight": "restore/report.json",
        "edge_preflight": "edge/report.json",
        "device_smoke": "smoke/report.json",
        "release_evidence": "release-evidence.json",
        "attestation": "attestation.json",
    })
    text = payload[:-1] + ', "attestation": "other.json"}'
    manifest = tmp_path / "manifest.json"
    manifest.write_text(text, encoding="utf-8")
    seed_role_files(tmp_path)
    with pytest.raises(plan.PlanInvalid) as caught:
        run(tmp_path, manifest)
    assert "duplicate-key:attestation" in str(caught.value)


def test_missing_required_role_key_refused(tmp_path):
    manifest = make_manifest(tmp_path, smoke=None)
    seed_role_files(tmp_path)
    with pytest.raises(plan.PlanInvalid) as caught:
        run(tmp_path, manifest)
    assert "role-missing:device_smoke" in str(caught.value)


def test_null_required_role_refused(tmp_path):
    payload = {
        "schema": plan.MANIFEST_SCHEMA,
        "restore_preflight": "restore/report.json",
        "edge_preflight": None,
        "device_smoke": "smoke/report.json",
        "release_evidence": "release-evidence.json",
        "attestation": "attestation.json",
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    seed_role_files(tmp_path)
    with pytest.raises(plan.PlanInvalid) as caught:
        run(tmp_path, manifest)
    assert "role-invalid:edge_preflight" in str(caught.value)


@pytest.mark.parametrize(
    "bad_path,code",
    [
        ("/etc/passwd", "path-absolute"),
        ("s3://bucket/x.json", "path-invalid"),
        ("win\\\\relative\\\\x.json", "path-invalid"),
        ("~/home/x.json", "path-absolute"),
        ("../escape.json", "path-traversal"),
        ("restore/../../escape.json", "path-traversal"),
        ("./report.json", "path-traversal"),
        ("restore//report.json", "path-traversal"),
        ("https://example.com/x.json", "path-invalid"),
        ("", "role-invalid"),
        ("   ", "role-invalid"),
        ("x" * 201, "path-too-long"),
    ],
)
def test_absolute_or_traversal_role_paths_refused(tmp_path, bad_path, code):
    manifest = make_manifest(tmp_path, attestation=bad_path)
    seed_role_files(tmp_path)
    with pytest.raises(plan.PlanInvalid) as caught:
        run(tmp_path, manifest)
    assert code in str(caught.value)


def test_duplicate_role_target_refused(tmp_path):
    manifest = make_manifest(
        tmp_path, edge="restore/report.json"
    )  # restore 与 edge 指向同一目标
    seed_role_files(tmp_path)
    with pytest.raises(plan.PlanInvalid) as caught:
        run(tmp_path, manifest)
    assert "duplicate-role-path:restore_preflight+edge_preflight" in str(caught.value)


@pytest.mark.parametrize("bad", [0, 721, -1, True, False, "24", 1.5, None])
def test_freshness_out_of_contract_refused(tmp_path, bad):
    payload = {
        "schema": plan.MANIFEST_SCHEMA,
        "restore_preflight": "restore/report.json",
        "edge_preflight": "edge/report.json",
        "device_smoke": "smoke/report.json",
        "release_evidence": "release-evidence.json",
        "attestation": "attestation.json",
        "freshness_hours": bad,
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    seed_role_files(tmp_path)
    with pytest.raises(plan.PlanInvalid):
        run(tmp_path, manifest)


@pytest.mark.parametrize("good", [1, 720, 48])
def test_freshness_bounds_accepted(tmp_path, good):
    manifest = make_manifest(tmp_path, freshness=good)
    seed_role_files(tmp_path)
    report, exit_code = run(tmp_path, manifest)
    assert exit_code == plan.EXIT_COMPLETE
    assert f"--freshness-hours {good}" in report["replay"]["command"]


def test_freshness_defaults_to_24_when_absent(tmp_path):
    manifest = make_manifest(tmp_path, freshness=None)
    seed_role_files(tmp_path)
    report, exit_code = run(tmp_path, manifest)
    assert exit_code == plan.EXIT_COMPLETE
    assert report["freshness_hours"] == 24
    assert "--freshness-hours 24" in report["replay"]["command"]


# ---------------------------------------------------------------- replay 命令


def test_replay_command_exact_quoted_shape(tmp_path):
    manifest = make_manifest(tmp_path, freshness=12)
    seed_role_files(tmp_path)
    report, _ = run(tmp_path, manifest)
    expected = (
        "python tools/android_release/public_mobile_release_gate.py "
        "--restore-preflight restore/report.json "
        "--edge-preflight edge/report.json "
        "--device-smoke smoke/report.json "
        "--cloudflare-preflight cf/report.json "
        "--release-evidence release-evidence.json "
        "--attestation attestation.json "
        "--output <output-dir> "
        "--freshness-hours 12"
    )
    assert report["replay"]["command"] == expected
    assert report["replay"]["slice"] == "M14-237"


def test_replay_command_quotes_paths_with_spaces(tmp_path):
    manifest = make_manifest(tmp_path, evidence="my evidence/re port.json")
    seed_role_files(tmp_path)
    (tmp_path / "my evidence").mkdir(exist_ok=True)
    (tmp_path / "my evidence" / "re port.json").write_bytes(b"x\n")
    report, exit_code = run(tmp_path, manifest)
    assert exit_code == plan.EXIT_COMPLETE
    assert "'my evidence/re port.json'" in report["replay"]["command"]


# ---------------------------------------------------------------- 输出面


def test_output_write_failure_refuses_zero_residue(tmp_path):
    class FailingMdStore(RealStore):
        def write_atomic(self, path: Path, text: str) -> None:
            if path.name == plan.REPORT_MD_NAME:
                raise OSError("disk full")
            super().write_atomic(path, text)

    manifest = make_manifest(tmp_path)
    seed_role_files(tmp_path)
    with pytest.raises(plan.PlanInvalid) as caught:
        run(tmp_path, manifest, store=FailingMdStore())
    assert "output-write-failed" in str(caught.value)
    out = tmp_path / "out"
    assert out.exists()
    assert list(out.iterdir()) == []  # 先写的 json 已被清理，零残留


def test_output_verify_failure_refuses_zero_residue(tmp_path):
    class LyingReadStore(RealStore):
        def read_bytes(self, path: Path) -> bytes:
            if path.name == plan.REPORT_JSON_NAME and path.parent.name == "out":
                return b"tampered"
            return super().read_bytes(path)

    manifest = make_manifest(tmp_path)
    seed_role_files(tmp_path)
    with pytest.raises(plan.PlanInvalid) as caught:
        run(tmp_path, manifest, store=LyingReadStore())
    assert "output-verify-failed" in str(caught.value)
    assert list((tmp_path / "out").iterdir()) == []


def test_report_is_sanitized_no_absolute_local_paths(tmp_path):
    manifest = make_manifest(tmp_path)
    seed_role_files(tmp_path)
    _report, _ = run(tmp_path, manifest)
    out = tmp_path / "out"
    for artifact in (out / plan.REPORT_JSON_NAME, out / plan.REPORT_MD_NAME):
        text = artifact.read_text(encoding="utf-8")
        assert tmp_path.as_posix() not in text
        assert str(tmp_path) not in text
        assert "D:" not in text and "/tmp/" not in text


def test_naive_now_refused(tmp_path):
    manifest = make_manifest(tmp_path)
    seed_role_files(tmp_path)
    with pytest.raises(plan.PlanInvalid) as caught:
        plan.run_plan(
            RealStore(),
            manifest=manifest,
            output_dir=tmp_path / "out",
            now=datetime(2026, 10, 5, 12, 0, 0),  # noqa: DTZ001 —— naive 正是被测契约
        )
    assert "now-not-tz-aware" in str(caught.value)


# ---------------------------------------------------------------- CLI 端到端


def test_cli_end_to_end_complete(tmp_path, capsys):
    manifest = make_manifest(tmp_path)
    seed_role_files(tmp_path)
    out = tmp_path / "cli-out"
    code = plan.main(["--manifest", str(manifest), "--output", str(out)])
    assert code == plan.EXIT_COMPLETE
    assert (out / plan.REPORT_JSON_NAME).is_file()
    assert (out / plan.REPORT_MD_NAME).is_file()
    captured = capsys.readouterr()
    assert "COMPLETE" in captured.out


def test_cli_end_to_end_invalid_manifest(tmp_path, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{not json", encoding="utf-8")
    code = plan.main(["--manifest", str(manifest), "--output", str(tmp_path / "o")])
    assert code == plan.EXIT_INVALID
    assert "INVALID" in capsys.readouterr().err


# ---------------------------------------------------------------- 只读守卫（源码级）


def test_source_has_no_subprocess_network_device_env_access():
    """ast 守卫：plan-only 工具零子进程/网络/设备/环境/secret 访问面。"""
    source = Path(plan.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    banned_calls = {"system", "popen", "run", "check_output", "urlopen"}
    banned_imports = {
        "subprocess", "socket", "urllib", "http", "requests", "ftplib",
        "telnetlib", "smtplib", "os.environ", "getenv",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in banned_imports, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").lower()
            for banned in ("subprocess", "socket", "urllib", "requests"):
                assert module != banned, module
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "os":
                assert node.attr not in ("environ", "getenv", "system", "popen"), node.attr
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                assert func.id not in banned_calls, func.id
    lowered = source.lower()
    for banned in ("adb", "docker", "wsl", "secret", "password", "token"):
        assert banned not in lowered, banned
