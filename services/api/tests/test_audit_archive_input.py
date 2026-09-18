r"""M14-55 契约测试：审计归档输入更新器（plan 只读 / apply 原子落盘）。

锁定 tools/ops/audit_archive_input.py 的契约面：

- 双命令 CLI（plan 零写入；apply 需 7 个必填参数，缺参由 argparse
  拒绝）；
- 校验链：``--now`` tz-aware 门 → 五文件门（常规/非 symlink/非空/
  ≤1 MiB）→ 锚恰一行 sequence=0 genesis 契约（strict JSON 拒重复键/
  NaN/Infinity、canonical 字节、anchor_hash 重算、genesis 常量）→
  verify 报告语义（status/problems/旗标/ended_at_utc）+ sidecar 摘要
  绑定 + source.sha256 同锚绑定 → 时序（anchor ≤ WORM ≤ 离线，且
  不晚于 --now）；
- policy/policy_bytes 精确字节与 state 任务结构（对 M14-50 调度器
  validate_state/validate_policy/evaluate_readiness 交叉验证）；
- 确定性输出（同输入同字节；路径无关；canonical 序列化回合）；
- apply 安全契约：确认语逐字门先于一切输出访问 → 同 plan 校验链
  构建 → 输出路径门（非 symlink / 非目录 / 父目录存在 / 不撞输入、
  sidecar 与另一输出）→ 既有 policy 字节漂移拒绝 → 既有 state strict
  装载 + schema 1 三任务形状 + 时刻回滚拒绝 → 每目标 tmp + fsync +
  原子替换、失败清理 tmp、诚实声明非跨文件事务；
- 源级纪律（零网络/零 env/零子进程/零 DB；写盘原语只出现在 apply 段）。

合成输入全部落 tmp_path 真实文件；锚与报告构造独立实现（不 import
被测实现的构造逻辑）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "audit_archive_input.py"
SCHEDULER_SCRIPT = REPO_ROOT / "tools" / "ops" / "audit_archive_scheduler.py"


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


aai = _load_module(SCRIPT, "audit_archive_input_under_test")


def _code_source() -> str:
    """被扫描源码：剔除模块 docstring（文档允许提及写原语名）。"""
    return SCRIPT.read_text(encoding="utf-8").split('"""', 2)[2]


def _apply_banner(source: str) -> str:
    """apply 段 banner 行（写盘原语只允许出现在其后）。"""
    for line in source.splitlines():
        if line.startswith("# ----") and line.endswith(" apply"):
            return line
    pytest.fail("apply section banner not found in tool source")


# ---------------------------------------------------------------- 时刻常量

NOW = "2026-09-18T00:00:00Z"
ANCHORED_AT = "2026-09-17T00:00:00.000000+00:00"
WORM_ENDED = "2026-09-17T06:00:00Z"
OFFLINE_ENDED = "2026-09-17T12:00:00Z"

# ---------------------------------------------------------------- 锚构造（独立实现）


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _payload_hash(anchor: dict) -> str:
    payload = {k: v for k, v in anchor.items() if k != "anchor_hash"}
    return hashlib.sha256(_canonical(payload)).hexdigest()


def make_anchor(sequence=0, head_hash=None, previous=None,
                anchored_at=ANCHORED_AT) -> dict:
    anchor = {"schema_version": 1, "algorithm": "sha256", "sequence": sequence,
              "head_hash": head_hash or "0" * 64, "anchored_at": anchored_at,
              "previous_anchor_hash": previous or "0" * 64}
    anchor["anchor_hash"] = _payload_hash(anchor)
    return anchor


def jsonl(anchor: dict) -> bytes:
    return _canonical(anchor) + b"\n"


GENESIS_ANCHOR = make_anchor()

# ---------------------------------------------------------------- 报告构造（独立实现）


def make_worm_report(anchor_sha: str, *, ended=WORM_ENDED,
                     overrides: dict | None = None) -> dict:
    """M14-43 audit_anchor_archive.py verify 成功报告的简化形态。"""
    payload = {
        "schema_version": 1, "milestone": "M14-43",
        "tool": "audit_anchor_archive", "command": "verify",
        "started_at_utc": "2026-09-17T05:59:00Z", "ended_at_utc": ended,
        "status": "pass", "problems": [], "worm_verified": True,
        "source": {"sha256": anchor_sha, "size_bytes": len(jsonl(GENESIS_ANCHOR)),
                   "anchor_count": 1},
    }
    if overrides:
        payload.update(overrides)
    return payload


def make_offline_report(anchor_sha: str, *, ended=OFFLINE_ENDED,
                        overrides: dict | None = None) -> dict:
    """M14-49 audit_worm_offline_copy.py verify 成功报告的简化形态
    （无 worm_verified 键——与真实 M14-49 报告一致）。"""
    payload = {
        "schema_version": 1, "milestone": "M14-49",
        "tool": "audit_worm_offline_copy", "command": "verify",
        "started_at_utc": "2026-09-17T11:59:00Z", "ended_at_utc": ended,
        "status": "pass", "problems": [], "offline_verified": True,
        "source": {"sha256": anchor_sha, "size_bytes": len(jsonl(GENESIS_ANCHOR)),
                   "anchor_count": 1},
    }
    if overrides:
        payload.update(overrides)
    return payload


def serialize_report(payload: dict) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True,
                      ensure_ascii=False).encode("utf-8") + b"\n"


def write_report(dir_path: Path, stem: str, payload: dict) -> Path:
    """写 ``<stem>.json`` + 匹配的 ``.json.sha256`` sidecar；返回报告路径。"""
    data = serialize_report(payload)
    report = dir_path / f"{stem}.json"
    report.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    (dir_path / f"{stem}.json.sha256").write_bytes(
        f"{digest}  {stem}.json\n".encode("ascii"))
    return report


def replace_report(path: Path, payload: dict) -> None:
    """以新载荷覆写既有报告 + 刷新 sidecar（语义变异测试用）。"""
    write_report(path.parent, path.name[: -len(".json")], payload)


def mutate_report_bytes(path: Path, transform) -> None:
    """字节级变异 + 刷新 sidecar（strict JSON 拒绝测试用）。"""
    data = transform(path.read_bytes())
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    (Path(str(path) + ".sha256")).write_bytes(
        f"{digest}  {path.name}\n".encode("ascii"))


def sidecar_path(report: Path) -> Path:
    return Path(str(report) + ".sha256")


# ---------------------------------------------------------------- 输入组装 / 运行


def build_inputs(tmp_path: Path, *, anchor: dict | None = None,
                 worm: dict | None = None, offline: dict | None = None,
                 worm_ended: str = WORM_ENDED,
                 offline_ended: str = OFFLINE_ENDED):
    """默认全绿的输入四元组（anchor/worm/offline 路径 + 锚文件 SHA）。"""
    anchor_path = tmp_path / "audit-anchor.jsonl"
    anchor_path.write_bytes(jsonl(anchor or GENESIS_ANCHOR))
    anchor_sha = hashlib.sha256(anchor_path.read_bytes()).hexdigest()
    worm_path = write_report(
        tmp_path, "worm-verify",
        worm or make_worm_report(anchor_sha, ended=worm_ended))
    offline_path = write_report(
        tmp_path, "offline-verify",
        offline or make_offline_report(anchor_sha, ended=offline_ended))
    return anchor_path, worm_path, offline_path, anchor_sha


def plan_args(anchor, worm, offline, now=NOW) -> list[str]:
    return ["plan", "--anchor", str(anchor), "--worm-report", str(worm),
            "--offline-report", str(offline), "--now", now]


CONFIRM = "EXECUTE AUDIT ARCHIVE INPUT UPDATE"


def apply_args(anchor, worm, offline, policy_out, state_out, now=NOW,
               confirm=CONFIRM) -> list[str]:
    return ["apply", "--anchor", str(anchor), "--worm-report", str(worm),
            "--offline-report", str(offline), "--now", now,
            "--policy-output", str(policy_out), "--state-output",
            str(state_out), "--confirm", confirm]


def synth_existing_state(last="2026-09-17T00:00:00+00:00") -> dict:
    """结构合法的既有 state 文档（时刻可调；供形状/回滚门独立构造）。"""
    tasks = {name: {"last_success_at": last, "source_sha256": "0" * 64,
                    "facts": {"algorithm": "sha256"}}
             for name in ("anchor", "worm_archive", "offline_copy")}
    return {"schema_version": 1, "tasks": tasks}


def run_plan(argv, capsys):
    """跑 CLI；返回 (exit_code, 解析后的 stdout JSON, 原始 stdout 文本)。"""
    code = aai.main(argv)
    captured = capsys.readouterr()
    return code, json.loads(captured.out), captured.out


def run_apply(argv, capsys):
    """跑 apply CLI（与 run_plan 同捕获语义）。"""
    return run_plan(argv, capsys)


def codes(payload: dict) -> list[str]:
    return [problem["code"] for problem in payload["problems"]]


def make_symlink_or_skip(target, link) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("platform cannot create symlinks")


# ---------------------------------------------------------------- 源级纪律


class TestSourceContract:
    def test_no_network_env_subprocess_db_surface(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for token in ("import socket", "urlopen", "urllib", "http.client",
                      "requests", "boto3", "subprocess", "os.environ",
                      "getenv", "psycopg", "sqlite3"):
            assert token not in source, f"源码不得出现 {token}"

    def test_write_surface_confined_to_apply(self):
        source = _code_source()
        banner = _apply_banner(source)
        assert source.count(banner) == 1
        plan_part, apply_part = source.split(banner, 1)
        # plan 段（docstring 后的全部校验链）零写盘原语
        for token in ("write_bytes", "write_text", "os.replace", "mkdir",
                      ".unlink", ".touch", "open("):
            assert token not in plan_part, f"plan 段不得出现 {token}"
        # 写盘原语只出现在 apply 段（banner 之后）
        assert "os.replace" in apply_part
        assert ".unlink" in apply_part


# ---------------------------------------------------------------- 有效 plan


class TestValidPlan:
    def test_valid_plan_payload(self, tmp_path, capsys):
        anchor, worm, offline, anchor_sha = build_inputs(tmp_path)
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 0
        assert payload["schema_version"] == 1
        assert payload["milestone"] == "M14-55"
        assert payload["tool"] == "audit_archive_input"
        assert payload["command"] == "plan"
        assert payload["status"] == "pass"
        assert payload["problems"] == []
        assert capsys.readouterr().err == ""  # 成功路径 stderr 静默
        # now / anchor 摘要（Z 后缀统一规范化为 +00:00）
        assert payload["now"] == "2026-09-18T00:00:00+00:00"
        assert payload["anchor"] == {
            "sha256": anchor_sha, "size_bytes": len(jsonl(GENESIS_ANCHOR)),
            "algorithm": "sha256", "sequence": 0,
            "anchor_hash": GENESIS_ANCHOR["anchor_hash"],
            "anchored_at": "2026-09-17T00:00:00+00:00"}
        # policy 常量文档 + 精确 canonical 字节
        assert payload["policy"] == {
            "schema_version": 1,
            "intervals": {"anchor": {"days": 30},
                          "worm_archive": {"days": 90},
                          "offline_copy": {"days": 180}},
            "grace_hours": 24}
        assert payload["policy_bytes"] == (
            '{"grace_hours":24,"intervals":{"anchor":{"days":30},'
            '"offline_copy":{"days":180},"worm_archive":{"days":90}},'
            '"schema_version":1}')
        # state：三任务各三字段，同锚绑定，时刻取各自事实源
        state = payload["state"]
        assert state["schema_version"] == 1
        assert set(state["tasks"]) == {"anchor", "worm_archive", "offline_copy"}
        tasks = state["tasks"]
        for task in tasks.values():
            assert set(task) == {"last_success_at", "source_sha256", "facts"}
            assert task["source_sha256"] == anchor_sha
            assert task["facts"]  # 非空且全标量
            assert all(isinstance(v, (str, int, float, bool))
                       for v in task["facts"].values())
        assert tasks["anchor"]["last_success_at"] == \
            "2026-09-17T00:00:00+00:00"
        assert tasks["anchor"]["facts"] == {
            "algorithm": "sha256", "sequence": 0,
            "anchor_hash": GENESIS_ANCHOR["anchor_hash"]}
        assert tasks["worm_archive"]["last_success_at"] == \
            "2026-09-17T06:00:00+00:00"
        assert tasks["worm_archive"]["facts"] == {
            "report_sha256": hashlib.sha256(
                worm.read_bytes()).hexdigest(),
            "report_size_bytes": worm.stat().st_size}
        assert tasks["offline_copy"]["last_success_at"] == \
            "2026-09-17T12:00:00+00:00"
        assert tasks["offline_copy"]["facts"] == {
            "report_sha256": hashlib.sha256(
                offline.read_bytes()).hexdigest(),
            "report_size_bytes": offline.stat().st_size}

    def test_plan_creates_no_files(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        before = sorted(p.name for p in tmp_path.iterdir())
        assert run_plan(plan_args(anchor, worm, offline), capsys)[0] == 0
        assert sorted(p.name for p in tmp_path.iterdir()) == before

    def test_plan_output_valid_for_scheduler(self, tmp_path, capsys):
        """plan 输出的 policy/state 必须被 M14-50 调度器原样接受。"""
        sched = _load_module(SCHEDULER_SCRIPT,
                             "audit_archive_scheduler_m14_55_crosscheck")
        anchor, worm, offline, _ = build_inputs(tmp_path)
        _, payload, _ = run_plan(plan_args(anchor, worm, offline), capsys)
        policy = json.loads(payload["policy_bytes"])
        assert sched.validate_policy(policy)["ok"] is True
        assert sched.validate_state(payload["state"])["ok"] is True
        readiness = sched.evaluate_readiness(
            payload["state"], policy, datetime(2026, 9, 18, tzinfo=UTC))
        assert readiness["overall"] == "fresh"
        assert readiness["problems"] == []


# ---------------------------------------------------------------- CLI 形态


class TestCliShape:
    def test_apply_missing_required_args_rejected(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            aai.main(["apply", "--anchor", "x"])
        assert excinfo.value.code == 2  # argparse 拒绝（7 参数全必填）

    def test_missing_required_args_rejected(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            aai.main(["plan", "--anchor", "x"])
        assert excinfo.value.code == 2

    def test_no_command_rejected(self, capsys):
        assert aai.main([]) == 2
        assert capsys.readouterr().out == ""


# ---------------------------------------------------------------- strict JSON


class TestStrictJsonRejection:
    def test_duplicate_key_in_anchor_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        anchor.write_bytes(anchor.read_bytes().replace(
            b'"algorithm":"sha256"',
            b'"algorithm":"sha256","algorithm":"sha256"'))
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["anchor-line-not-strict-json"]

    def test_duplicate_key_in_report_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        mutate_report_bytes(
            worm, lambda d: d.replace(b'"status": "pass"',
                                      b'"status": "pass", "status": "fail"'))
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["worm-report-not-strict-json"]

    @pytest.mark.parametrize("literal", [
        b"NaN", b"Infinity", b"-Infinity", b"1e999",
    ])
    def test_non_finite_in_report_rejected(self, tmp_path, capsys, literal):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        mutate_report_bytes(
            worm, lambda d: d.replace(b'"problems": []',
                                      b'"problems": [], "leak": ' + literal))
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["worm-report-not-strict-json"]


# ---------------------------------------------------------------- 文件门


class TestFileGates:
    def test_oversized_anchor_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        anchor.write_bytes(b"x" * (aai.MAX_INPUT_BYTES + 1))
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["anchor-too-large"]

    def test_oversized_report_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        worm.write_bytes(b"x" * (aai.MAX_INPUT_BYTES + 1))
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["worm-report-too-large"]

    def test_anchor_missing_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        anchor.unlink()
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["anchor-missing"]

    def test_anchor_symlink_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        link = tmp_path / "anchor-link.jsonl"
        make_symlink_or_skip(anchor, link)
        code, payload, _ = run_plan(plan_args(link, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["anchor-symlink"]

    def test_report_symlink_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        link = tmp_path / "worm-link.json"
        make_symlink_or_skip(worm, link)
        code, payload, _ = run_plan(plan_args(anchor, link, offline), capsys)
        assert code == 2
        assert codes(payload) == ["worm-report-symlink"]


# ---------------------------------------------------------------- sidecar 契约


class TestSidecarContract:
    def test_worm_sidecar_missing_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        sidecar_path(worm).unlink()
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["worm-sidecar-missing"]

    def test_worm_sidecar_mismatch_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        sidecar_path(worm).write_bytes(
            f"{'0' * 64}  {worm.name}\n".encode("ascii"))
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["worm-sidecar-mismatch"]

    def test_offline_sidecar_mismatch_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        sidecar_path(offline).write_bytes(
            f"{'0' * 64}  {offline.name}\n".encode("ascii"))
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["offline-sidecar-mismatch"]

    def test_sidecar_garbage_format_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        sidecar_path(worm).write_bytes(b"not-a-digest\n")
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["worm-sidecar-format"]


# ---------------------------------------------------------------- 报告语义


class TestReportSemantics:
    def test_worm_status_fail_rejected(self, tmp_path, capsys):
        anchor, worm, offline, sha = build_inputs(
            tmp_path, worm=make_worm_report(
                "0" * 64, overrides={"status": "fail",
                                     "problems": [{"code": "x",
                                                   "detail": "y"}]}))
        # 重新绑定真实锚 SHA（本测试只针对 status）
        replace_report(worm, make_worm_report(
            sha, overrides={"status": "fail",
                            "problems": [{"code": "x", "detail": "y"}]}))
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["worm-report-status-not-pass"]

    def test_worm_problems_nonempty_rejected(self, tmp_path, capsys):
        anchor, worm, offline, sha = build_inputs(tmp_path)
        replace_report(worm, make_worm_report(
            sha, overrides={"problems": [{"code": "x", "detail": "y"}]}))
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["worm-report-problems-not-empty"]

    def test_worm_flag_false_rejected(self, tmp_path, capsys):
        anchor, worm, offline, sha = build_inputs(tmp_path)
        replace_report(worm, make_worm_report(
            sha, overrides={"worm_verified": False}))
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["worm-report-worm_verified-not-true"]

    def test_offline_flag_missing_rejected(self, tmp_path, capsys):
        anchor, worm, offline, sha = build_inputs(tmp_path)
        payload_bad = make_offline_report(sha)
        del payload_bad["offline_verified"]
        replace_report(offline, payload_bad)
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["offline-report-offline_verified-not-true"]

    def test_reports_swapped_rejected(self, tmp_path, capsys):
        # 离线报告（无 worm_verified 键）冒充 WORM 报告 → 旗标拒绝
        anchor, worm, offline, _ = build_inputs(tmp_path)
        code, payload, _ = run_plan(
            plan_args(anchor, offline, worm), capsys)
        assert code == 2
        assert codes(payload) == ["worm-report-worm_verified-not-true"]

    def test_worm_missing_ended_at_rejected(self, tmp_path, capsys):
        anchor, worm, offline, sha = build_inputs(tmp_path)
        payload_bad = make_worm_report(sha)
        del payload_bad["ended_at_utc"]
        replace_report(worm, payload_bad)
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["worm-report-ended-at-missing"]

    def test_worm_naive_ended_at_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(
            tmp_path, worm_ended="2026-09-17T06:00:00")
        assert worm is not None
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["worm-report-ended-at-invalid"]


# ---------------------------------------------------------------- 同锚绑定


class TestAnchorBinding:
    def test_worm_wrong_anchor_sha_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(
            tmp_path, worm=make_worm_report("e" * 64))
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["worm-report-anchor-binding-mismatch"]

    def test_offline_wrong_anchor_sha_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(
            tmp_path, offline=make_offline_report("e" * 64))
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["offline-report-anchor-binding-mismatch"]


# ---------------------------------------------------------------- 锚单行契约


class TestAnchorContract:
    def test_two_anchor_lines_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(
            tmp_path, anchor=GENESIS_ANCHOR)
        second = make_anchor(1, "a" * 64, GENESIS_ANCHOR["anchor_hash"],
                             anchored_at="2026-09-17T00:01:00Z")
        anchor.write_bytes(jsonl(GENESIS_ANCHOR) + jsonl(second))
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["anchor-not-single-line"]

    def test_no_trailing_newline_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        anchor.write_bytes(anchor.read_bytes()[:-1])
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["anchor-no-trailing-newline"]

    def test_sequence_nonzero_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(
            tmp_path, anchor=make_anchor(1, "a" * 64))
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["anchor-sequence-not-zero"]

    def test_tampered_anchor_hash_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(
            tmp_path, anchor={**GENESIS_ANCHOR, "anchor_hash": "c" * 64})
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["anchor-hash-mismatch"]

    def test_non_canonical_row_rejected(self, tmp_path, capsys):
        # 值合法、hash 正确，但行字节非 canonical（键序/空白漂移）
        anchor, worm, offline, _ = build_inputs(tmp_path)
        anchor.write_bytes(json.dumps(
            GENESIS_ANCHOR, sort_keys=True,
            separators=(", ", ": ")).encode("utf-8") + b"\n")
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["anchor-not-canonical"]

    def test_wrong_schema_version_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(
            tmp_path, anchor=make_anchor())
        data = json.loads(anchor.read_bytes().decode("utf-8"))
        data["schema_version"] = 2
        anchor.write_bytes(_canonical(data) + b"\n")
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["anchor-schema-version"]

    def test_wrong_algorithm_rejected(self, tmp_path, capsys):
        data = dict(GENESIS_ANCHOR)
        data["algorithm"] = "sha512"
        data["anchor_hash"] = _payload_hash(data)
        anchor, worm, offline, _ = build_inputs(tmp_path, anchor=data)
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["anchor-algorithm"]

    def test_naive_anchored_at_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(
            tmp_path, anchor=make_anchor(anchored_at="2026-09-17T00:00:00"))
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["anchor-anchored-at-invalid"]

    def test_missing_field_rejected(self, tmp_path, capsys):
        data = {k: v for k, v in GENESIS_ANCHOR.items() if k != "head_hash"}
        anchor, worm, offline, _ = build_inputs(tmp_path, anchor=data)
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["anchor-field-set"]

    def test_genesis_previous_violation_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(
            tmp_path, anchor=make_anchor(0, "0" * 64, "f" * 64))
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["anchor-genesis-previous"]

    def test_genesis_head_violation_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(
            tmp_path, anchor=make_anchor(0, "e" * 64, "0" * 64))
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["anchor-genesis-head"]


# ---------------------------------------------------------------- 时序


class TestOrdering:
    def test_anchor_after_worm_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(
            tmp_path, anchor=make_anchor(anchored_at="2026-09-17T07:30:00Z"))
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["ordering-anchor-after-worm"]

    def test_worm_after_offline_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(
            tmp_path, worm_ended="2026-09-17T13:00:00Z")
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["ordering-worm-after-offline"]

    def test_future_anchor_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(
            tmp_path, anchor=make_anchor(anchored_at="2026-09-17T18:00:00Z"),
            worm_ended="2026-09-17T19:00:00Z",
            offline_ended="2026-09-17T20:00:00Z")
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline, now="2026-09-17T12:00:00Z"),
            capsys)
        assert code == 2
        assert codes(payload) == ["future-anchor-anchored-at"]

    def test_future_worm_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(
            tmp_path, worm_ended="2026-09-18T01:00:00Z",
            offline_ended="2026-09-18T01:30:00Z")
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline, now="2026-09-18T00:30:00Z"),
            capsys)
        assert code == 2
        assert codes(payload) == ["future-worm-ended-at"]

    def test_future_offline_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(
            tmp_path, offline_ended="2026-09-18T01:00:00Z")
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        assert code == 2
        assert codes(payload) == ["future-offline-ended-at"]

    @pytest.mark.parametrize("now", ["2026-09-18T00:00:00", "not-a-date", ""])
    def test_invalid_now_rejected(self, tmp_path, capsys, now):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        code, payload, _ = run_plan(
            plan_args(anchor, worm, offline, now=now), capsys)
        assert code == 2
        assert codes(payload) == ["now-invalid"]


# ---------------------------------------------------------------- 确定性


class TestDeterministicOutput:
    def test_same_inputs_same_bytes(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        code1, _, out1 = run_plan(plan_args(anchor, worm, offline), capsys)
        code2, _, out2 = run_plan(plan_args(anchor, worm, offline), capsys)
        assert (code1, code2) == (0, 0)
        assert out1 == out2
        assert out1.endswith("\n") and out1.count("\n") > 1
        # canonical 序列化回合：sort_keys + indent=2 + 单尾换行
        assert json.dumps(json.loads(out1), indent=2, ensure_ascii=False,
                          sort_keys=True) + "\n" == out1

    def test_output_independent_of_paths(self, tmp_path, capsys):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        a_anchor, a_worm, a_offline, _ = build_inputs(dir_a)
        b_anchor, b_worm, b_offline, _ = build_inputs(dir_b)
        _, _, out_a = run_plan(
            plan_args(a_anchor, a_worm, a_offline), capsys)
        _, _, out_b = run_plan(
            plan_args(b_anchor, b_worm, b_offline), capsys)
        # 同内容不同路径/文件名 → 逐字节相同（输出零路径泄漏）
        assert out_a == out_b


# ---------------------------------------------------------------- apply 成功路径


class TestApplySuccess:
    def test_successful_new_file_apply(self, tmp_path, capsys):
        inputs = tmp_path / "inputs"
        outputs = tmp_path / "outputs"
        inputs.mkdir()
        outputs.mkdir()
        anchor, worm, offline, _ = build_inputs(inputs)
        policy_out = outputs / "audit-policy.json"
        state_out = outputs / "audit-state.json"
        _, plan_payload, _ = run_plan(
            plan_args(anchor, worm, offline), capsys)
        code, payload, raw = run_apply(
            apply_args(anchor, worm, offline, policy_out, state_out),
            capsys)
        assert code == 0
        assert payload["command"] == "apply"
        assert payload["status"] == "pass"
        assert payload["problems"] == []
        assert payload["policy"] == {"already_matched": False,
                                     "written": True}
        assert payload["state"] == {"already_matched": False,
                                    "written": True}
        assert payload["cross_file_transaction"] is False
        assert capsys.readouterr().err == ""  # 成功路径 stderr 静默
        # 落盘字节 = POLICY_BYTES / plan state 的 canonical JSON
        assert policy_out.read_bytes() == aai.POLICY_BYTES
        assert state_out.read_bytes() == _canonical(plan_payload["state"])
        assert str(tmp_path) not in raw  # 输出零路径泄漏
        # 落盘产物必须被 M14-50 调度器原样接受
        sched = _load_module(SCHEDULER_SCRIPT,
                             "audit_archive_scheduler_m14_55_apply_check")
        policy_doc = json.loads(policy_out.read_bytes())
        state_doc = json.loads(state_out.read_bytes())
        assert sched.validate_policy(policy_doc)["ok"] is True
        assert sched.validate_state(state_doc)["ok"] is True
        readiness = sched.evaluate_readiness(
            state_doc, policy_doc, datetime(2026, 9, 18, tzinfo=UTC))
        assert readiness["overall"] == "fresh"
        assert readiness["problems"] == []
        # 输出目录零 tmp 残留
        assert sorted(p.name for p in outputs.iterdir()) == [
            "audit-policy.json", "audit-state.json"]

    def test_successful_rerun_is_noop(self, tmp_path, capsys):
        inputs = tmp_path / "inputs"
        outputs = tmp_path / "outputs"
        inputs.mkdir()
        outputs.mkdir()
        anchor, worm, offline, _ = build_inputs(inputs)
        policy_out = outputs / "audit-policy.json"
        state_out = outputs / "audit-state.json"
        argv = apply_args(anchor, worm, offline, policy_out, state_out)
        assert run_apply(argv, capsys)[0] == 0
        snapshot = {p.name: p.read_bytes() for p in outputs.iterdir()}
        code, payload, _ = run_apply(argv, capsys)
        assert code == 0
        assert payload["policy"] == {"already_matched": True,
                                     "written": False}
        assert payload["state"] == {"already_matched": True,
                                    "written": False}
        assert {p.name: p.read_bytes()
                for p in outputs.iterdir()} == snapshot


# ---------------------------------------------------------------- apply 确认语门


class TestApplyConfirmGate:
    @pytest.mark.parametrize("bad", [
        "execute audit archive input update",
        "EXECUTE AUDIT ARCHIVE INPUT UPDATE ",
        " EXECUTE AUDIT ARCHIVE INPUT UPDATE",
        "EXECUTE AUDIT ARCHIVE INPUT",
        "yes",
    ])
    def test_wrong_confirm_rejected_before_output_access(
            self, tmp_path, capsys, bad):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        missing_dir = tmp_path / "no-such-dir"
        code, payload, _ = run_apply(
            apply_args(anchor, worm, offline, missing_dir / "p.json",
                       missing_dir / "s.json", confirm=bad), capsys)
        assert code == 2
        assert codes(payload) == ["apply-confirm-phrase"]
        # 确认语门先于一切输出路径访问（目录从未被触碰）
        assert not missing_dir.exists()


# ---------------------------------------------------------------- apply policy 门


class TestApplyPolicyGate:
    def test_policy_drift_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        policy_out = tmp_path / "audit-policy.json"
        state_out = tmp_path / "audit-state.json"
        policy_out.write_bytes(b'{"schema_version":1}\n')
        code, payload, _ = run_apply(
            apply_args(anchor, worm, offline, policy_out, state_out),
            capsys)
        assert code == 2
        assert codes(payload) == ["apply-policy-drift"]
        assert policy_out.read_bytes() == b'{"schema_version":1}\n'
        assert not state_out.exists()  # 拒绝时零写入


# ---------------------------------------------------------------- apply state 门


class TestApplyStateGate:
    @pytest.mark.parametrize("raw", [
        b"not-json",
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version":1,"leak":NaN}',
    ])
    def test_malformed_existing_state_rejected(self, tmp_path, capsys,
                                               raw):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        policy_out = tmp_path / "audit-policy.json"
        state_out = tmp_path / "audit-state.json"
        state_out.write_bytes(raw)
        code, payload, _ = run_apply(
            apply_args(anchor, worm, offline, policy_out, state_out),
            capsys)
        assert code == 2
        assert codes(payload) == ["apply-state-not-strict-json"]
        assert state_out.read_bytes() == raw
        assert not policy_out.exists()

    @pytest.mark.parametrize("mutate", [
        lambda s: {**s, "schema_version": 2},
        lambda s: {"schema_version": 1,
                   "tasks": {k: v for k, v in s["tasks"].items()
                             if k != "offline_copy"}},
        lambda s: {"schema_version": 1,
                   "tasks": {**s["tasks"], "audit": s["tasks"]["anchor"]}},
        lambda s: {"schema_version": 1,
                   "tasks": {k: {f: v for f, v in task.items()
                                 if f != "facts"}
                             for k, task in s["tasks"].items()}},
        lambda s: {"schema_version": 1,
                   "tasks": {k: {**task, "last_success_at":
                                 "2026-09-17T00:00:00"}
                             for k, task in s["tasks"].items()}},
        lambda s: {"schema_version": 1, "tasks": {}},
    ])
    def test_bad_shape_existing_state_rejected(self, tmp_path, capsys,
                                               mutate):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        policy_out = tmp_path / "audit-policy.json"
        state_out = tmp_path / "audit-state.json"
        state_out.write_bytes(_canonical(mutate(synth_existing_state())))
        code, payload, _ = run_apply(
            apply_args(anchor, worm, offline, policy_out, state_out),
            capsys)
        assert code == 2
        assert codes(payload) == ["apply-state-shape"]
        assert not policy_out.exists()

    def test_state_rollback_rejected(self, tmp_path, capsys):
        inputs = tmp_path / "inputs"
        outputs = tmp_path / "outputs"
        inputs.mkdir()
        outputs.mkdir()
        anchor, worm, offline, _ = build_inputs(inputs)
        policy_out = outputs / "audit-policy.json"
        state_out = outputs / "audit-state.json"
        assert run_apply(apply_args(anchor, worm, offline, policy_out,
                                    state_out), capsys)[0] == 0
        before = state_out.read_bytes()
        # 更早结束的报告重放 → 时刻回滚，拒绝且字节不变
        anchor2, worm2, offline2, _ = build_inputs(
            inputs, worm_ended="2026-09-17T05:00:00Z",
            offline_ended="2026-09-17T11:00:00Z")
        code, payload, _ = run_apply(
            apply_args(anchor2, worm2, offline2, policy_out, state_out),
            capsys)
        assert code == 2
        assert codes(payload) == ["apply-state-rollback"]
        assert state_out.read_bytes() == before


# ---------------------------------------------------------------- apply 输出路径门


class TestApplyOutputPathGates:
    def test_output_symlink_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        real = tmp_path / "real-policy.json"
        real.write_bytes(aai.POLICY_BYTES)
        link = tmp_path / "policy-link.json"
        make_symlink_or_skip(real, link)
        state_out = tmp_path / "audit-state.json"
        code, payload, _ = run_apply(
            apply_args(anchor, worm, offline, link, state_out), capsys)
        assert code == 2
        assert codes(payload) == ["apply-policy-output-symlink"]
        assert real.read_bytes() == aai.POLICY_BYTES

    def test_missing_parent_and_directory_rejected(self, tmp_path,
                                                   capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        policy_out = tmp_path / "audit-policy.json"
        code, payload, _ = run_apply(
            apply_args(anchor, worm, offline, policy_out,
                       tmp_path / "no-such-dir" / "s.json"), capsys)
        assert code == 2
        assert codes(payload) == ["apply-state-output-parent-missing"]
        assert not policy_out.exists()
        code, payload, _ = run_apply(
            apply_args(anchor, worm, offline, tmp_path, policy_out),
            capsys)
        assert code == 2
        assert codes(payload) == ["apply-policy-output-is-directory"]

    def test_output_input_collision_rejected(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        state_out = tmp_path / "audit-state.json"
        anchor_before = anchor.read_bytes()
        code, payload, _ = run_apply(
            apply_args(anchor, worm, offline, anchor, state_out), capsys)
        assert code == 2
        assert codes(payload) == ["apply-output-collision"]
        assert anchor.read_bytes() == anchor_before
        code, payload, _ = run_apply(
            apply_args(anchor, worm, offline, state_out,
                       sidecar_path(worm)), capsys)
        assert code == 2
        assert codes(payload) == ["apply-output-collision"]
        same = tmp_path / "same.json"
        code, payload, _ = run_apply(
            apply_args(anchor, worm, offline, same, same), capsys)
        assert code == 2
        assert codes(payload) == ["apply-output-collision"]
        assert not same.exists()


# ---------------------------------------------------------------- apply 原子写


class TestApplyAtomicWrite:
    def test_tmp_cleanup_on_injected_replace_failure(self, tmp_path,
                                                     capsys,
                                                     monkeypatch):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        policy_out = tmp_path / "audit-policy.json"
        state_out = tmp_path / "audit-state.json"
        real_replace = os.replace

        def fail_state_replace(src, dst):
            if Path(dst).name == state_out.name:
                raise OSError("injected replace failure")
            return real_replace(src, dst)

        monkeypatch.setattr(aai.os, "replace", fail_state_replace)
        code = aai.main(
            apply_args(anchor, worm, offline, policy_out, state_out))
        captured = capsys.readouterr()
        assert code == 2
        assert "unexpected OSError" in captured.err
        assert captured.out == ""  # 终防线拒绝不打印 stdout JSON
        # 诚实非事务：policy 已生效，state 未落地
        assert policy_out.read_bytes() == aai.POLICY_BYTES
        assert not state_out.exists()
        assert not [p for p in tmp_path.iterdir()
                    if p.name.endswith(".tmp")]


# ---------------------------------------------------------------- apply 不触碰 plan


class TestApplyDoesNotTouchPlan:
    def test_plan_remains_read_only(self, tmp_path, capsys):
        anchor, worm, offline, _ = build_inputs(tmp_path)
        policy_out = tmp_path / "audit-policy.json"
        state_out = tmp_path / "audit-state.json"
        policy_out.write_bytes(b"stale-policy\n")
        state_out.write_bytes(b"stale-state\n")
        assert run_plan(plan_args(anchor, worm, offline), capsys)[0] == 0
        assert policy_out.read_bytes() == b"stale-policy\n"
        assert state_out.read_bytes() == b"stale-state\n"
