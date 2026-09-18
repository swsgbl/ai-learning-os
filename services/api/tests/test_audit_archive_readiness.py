r"""M14-51 契约:audit readiness 文件输入 + 报告 CLI(tools/ops/audit_archive_readiness.py)。

被测对象为单文件纯标准库 CLI(`main(argv, *, fs, now_provider)` 注入 FS 与
时钟)。覆盖 TASK.md 第 6 条全部要求:

- 成功:合法 state/policy → exit 0、canonical 报告 + SHA-256 sidecar、
  报告结构(schema_version/tool/generated_for/overall/inputs/evaluation)、
  与 M14-50 evaluator 直算结果全等(包装一致性,含 stub 探针);
- 确定性:同输入同 --now 两次运行报告与 sidecar 逐字节相同;
- 输入哈希:inputs 只记 role/basename/字节大小/SHA-256,绝不记绝对路径;
- 输入拒绝(fail-closed,exit 2):missing、目录、非常规文件、symlink
  (FakeFS 建模 + 真实盘 skipif)、超 1 MiB(FakeFS + 真实盘)、malformed
  JSON、非对象文档、重复 JSON 键、NaN/Infinity 非有限常数、非法 UTF-8、
  空路径串;
- 输出守卫:output 不得覆盖任一输入、sidecar 不得覆盖任一输入、输出父
  目录必须已存在、输出指向已存在目录 → exit 2 零残留;
- 原子替换:旧报告被完整替换、无 .tmp 残留;写失败(中途异常)清理全部
  tmp 文件;第二 replace 失败的诚实边界(报告已换、sidecar 未写);
- 输入不可变:运行前后输入字节不变;
- now 行为:--now 固定(含 Z 后缀与偏移归一)、省略 --now 用注入时钟、
  naive/垃圾 --now 拒绝;
- CLI 退出码:成功 0(含 overdue/blocked——overall 字段承载状态)、
  一切 usage/输入/输出失败 2(argparse 缺参 SystemExit 2);
- 源级纪律:被测源文件禁止 requests/boto3/subprocess/os.environ token,
  import 仅纯标准库白名单。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "audit_archive_readiness.py"
SCHED_SCRIPT = REPO_ROOT / "tools" / "ops" / "audit_archive_scheduler.py"

NOW = datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc)
NOW_ISO = NOW.isoformat()
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


cli = _load_module(SCRIPT, "audit_archive_readiness_under_test")
sched = _load_module(SCHED_SCRIPT, "audit_archive_scheduler_under_test")


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


def dump(document: dict) -> bytes:
    """确定性 UTF-8 序列化(内容无关紧要,CLI 会重新严格解析)。"""
    return json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")


def run_cli(fs, state, policy, output, *, now=NOW_ISO, now_provider=None):
    """以注入 FS/时钟调用 CLI;now=None 且不给 provider 时测省略 --now 路径。"""
    argv = ["--state", str(state), "--policy", str(policy), "--output", str(output)]
    if now is not None:
        argv += ["--now", now]
    kwargs = {"fs": fs}
    if now_provider is not None:
        kwargs["now_provider"] = now_provider
    return cli.main(argv, **kwargs)


class FakeFS:
    """内存 FS:files/dirs/symlinks/not_regular 四象限 + 写/替换/清理记录。"""

    def __init__(self, files=None, dirs=(), symlinks=(), not_regular=(),
                 fail_writes=(), fail_replace_dst=()):
        self.files: dict[Path, bytes] = {
            Path(p): bytes(b) for p, b in (files or {}).items()
        }
        self.dirs = {Path(p) for p in dirs}
        self.symlinks = {Path(p) for p in symlinks}
        self.not_regular = {Path(p) for p in not_regular}
        self.fail_writes = {Path(p) for p in fail_writes}
        self.fail_replace_dst = {Path(p) for p in fail_replace_dst}
        self.writes: list[Path] = []
        self.replaced: list[Path] = []
        self.unlinked: list[Path] = []

    def exists(self, path) -> bool:
        path = Path(path)
        return path in self.files or path in self.dirs or path in self.not_regular

    def is_file(self, path) -> bool:
        return Path(path) in self.files

    def is_dir(self, path) -> bool:
        return Path(path) in self.dirs

    def is_symlink(self, path) -> bool:
        return Path(path) in self.symlinks

    def size(self, path) -> int:
        return len(self.files[Path(path)])

    def read_bytes(self, path) -> bytes:
        return self.files[Path(path)]

    def write_bytes(self, path, data: bytes) -> None:
        path = Path(path)
        if path in self.fail_writes:
            raise OSError("simulated write failure")
        self.files[path] = bytes(data)
        self.writes.append(path)

    def replace(self, src, dst) -> None:
        src, dst = Path(src), Path(dst)
        if dst in self.fail_replace_dst:
            raise OSError("simulated replace failure")
        self.files[dst] = self.files.pop(src)
        self.replaced.append(dst)

    def unlink_quiet(self, path) -> None:
        path = Path(path)
        self.files.pop(path, None)
        self.unlinked.append(path)


def fake_env(tmp_path):
    """标准 FakeFS 环境:base 下 state.json/policy.json + 输出 readiness.json。"""
    base = tmp_path.resolve()
    fs = FakeFS(
        files={base / "state.json": dump(make_state()),
               base / "policy.json": dump(make_policy())},
        dirs=[base],
    )
    return fs, base / "state.json", base / "policy.json", base / "readiness.json"


class TestSuccessAndReport:
    def test_success_writes_canonical_report_and_sidecar(self, tmp_path):
        base = tmp_path.resolve()
        state, policy, output = (
            base / "state.json", base / "policy.json", base / "readiness.json"
        )
        state.write_bytes(dump(make_state()))
        policy.write_bytes(dump(make_policy()))
        assert run_cli(cli.RealFS(), state, policy, output) == 0

        report_bytes = output.read_bytes()
        report = json.loads(report_bytes.decode("utf-8"))
        assert report["schema_version"] == 1
        assert report["tool"] == "audit_archive_readiness"
        assert report["generated_for"] == NOW_ISO
        assert report["overall"] == "fresh"
        assert report["problems"] == []
        # canonical:排序键 + 紧凑分隔 + 恰一个尾随 LF,字节级重序列化全等
        assert report_bytes == (
            json.dumps(report, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8") + b"\n"
        )
        # inputs 只记 role/basename/size/sha256,绝不记绝对本地路径
        state_raw, policy_raw = state.read_bytes(), policy.read_bytes()
        assert report["inputs"] == {
            "state": {"role": "state", "name": "state.json",
                      "size_bytes": len(state_raw),
                      "sha256": hashlib.sha256(state_raw).hexdigest()},
            "policy": {"role": "policy", "name": "policy.json",
                       "size_bytes": len(policy_raw),
                       "sha256": hashlib.sha256(policy_raw).hexdigest()},
        }
        assert str(base) not in report_bytes.decode("utf-8")
        # sidecar:<report-sha256>  <output-basename>\n
        sidecar = base / "readiness.json.sha256"
        assert sidecar.read_bytes() == (
            f"{hashlib.sha256(report_bytes).hexdigest()}  readiness.json\n"
        ).encode()

    def test_report_embeds_scheduler_evaluation_verbatim(self, tmp_path):
        base = tmp_path.resolve()
        state, policy, output = (
            base / "state.json", base / "policy.json", base / "readiness.json"
        )
        state.write_bytes(dump(make_state(ages=(timedelta(days=40),) * 3)))
        policy.write_bytes(dump(make_policy()))
        assert run_cli(cli.RealFS(), state, policy, output) == 0
        report = json.loads(output.read_bytes().decode("utf-8"))
        expected = sched.evaluate_readiness(
            json.loads(state.read_bytes().decode("utf-8")),
            json.loads(policy.read_bytes().decode("utf-8")),
            NOW,
        )
        assert report["evaluation"] == expected
        assert report["overall"] == expected["overall"]

    def test_deterministic_bytes_across_runs(self, tmp_path):
        base = tmp_path.resolve()
        dir_one, dir_two = base / "one", base / "two"
        dir_one.mkdir()
        dir_two.mkdir()
        outputs = []
        for directory in (dir_one, dir_two):
            state = directory / "state.json"
            policy = directory / "policy.json"
            output = directory / "readiness.json"
            state.write_bytes(dump(make_state()))
            policy.write_bytes(dump(make_policy()))
            assert run_cli(cli.RealFS(), state, policy, output) == 0
            outputs.append((output.read_bytes(),
                            (directory / "readiness.json.sha256").read_bytes()))
        assert outputs[0][0] == outputs[1][0]
        assert outputs[0][1] == outputs[1][1]

    def test_overdue_and_blocked_scenarios_still_exit_zero(self, tmp_path):
        base = tmp_path.resolve()
        state, policy = base / "state.json", base / "policy.json"
        output = base / "readiness.json"
        state.write_bytes(dump(make_state(ages=(timedelta(days=400),) * 3)))
        policy.write_bytes(dump(make_policy()))
        assert run_cli(cli.RealFS(), state, policy, output) == 0
        report = json.loads(output.read_bytes().decode("utf-8"))
        assert report["overall"] == "overdue"

        blocked = make_state()
        blocked["tasks"]["anchor"]["source_sha256"] = "XYZ"
        state.write_bytes(dump(blocked))
        assert run_cli(cli.RealFS(), state, policy, output) == 0
        report = json.loads(output.read_bytes().decode("utf-8"))
        assert report["overall"] == "blocked"
        assert report["evaluation"]["problems"]

    def test_omitted_now_uses_injected_clock(self, tmp_path):
        fs, state, policy, output = fake_env(tmp_path)
        assert run_cli(fs, state, policy, output,
                       now=None, now_provider=lambda: NOW) == 0
        report = json.loads(fs.read_bytes(output).decode("utf-8"))
        assert report["generated_for"] == NOW_ISO

    def test_now_accepts_z_suffix_and_normalizes_offsets(self, tmp_path):
        fs, state, policy, output = fake_env(tmp_path)
        assert run_cli(fs, state, policy, output,
                       now="2026-09-18T12:00:00Z") == 0
        report = json.loads(fs.read_bytes(output).decode("utf-8"))
        assert report["generated_for"] == NOW_ISO

        assert run_cli(fs, state, policy, output,
                       now="2026-09-18T20:00:00+08:00") == 0
        report = json.loads(fs.read_bytes(output).decode("utf-8"))
        assert report["generated_for"] == NOW_ISO


class TestInputRejection:
    def test_missing_file_rejected(self, tmp_path):
        fs, state, policy, output = fake_env(tmp_path)
        del fs.files[state]
        assert run_cli(fs, state, policy, output) == 2
        assert output not in fs.files

    def test_directory_rejected(self, tmp_path):
        fs, state, policy, output = fake_env(tmp_path)
        del fs.files[state]
        fs.dirs.add(state)
        assert run_cli(fs, state, policy, output) == 2

    def test_not_regular_file_rejected(self, tmp_path):
        fs, state, policy, output = fake_env(tmp_path)
        del fs.files[state]
        fs.not_regular.add(state)
        assert run_cli(fs, state, policy, output) == 2

    def test_symlink_rejected_fake(self, tmp_path):
        fs, state, policy, output = fake_env(tmp_path)
        del fs.files[state]
        fs.symlinks.add(state)
        assert run_cli(fs, state, policy, output) == 2

    def test_symlink_rejected_real(self, tmp_path):
        base = tmp_path.resolve()
        target = base / "real-state.json"
        target.write_bytes(dump(make_state()))
        link = base / "link-state.json"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError, AttributeError):
            pytest.skip("os.symlink unavailable without privilege on this host")
        output = base / "readiness.json"
        policy = base / "policy.json"
        policy.write_bytes(dump(make_policy()))
        assert run_cli(cli.RealFS(), link, policy, output) == 2
        assert not output.exists()
        assert not (base / "readiness.json.sha256").exists()

    def test_oversized_rejected_fake(self, tmp_path):
        fs, state, policy, output = fake_env(tmp_path)
        fs.files[state] = b"a" * (cli.MAX_INPUT_BYTES + 1)
        assert run_cli(fs, state, policy, output) == 2

    def test_oversized_rejected_real(self, tmp_path):
        base = tmp_path.resolve()
        state, policy, output = (
            base / "state.json", base / "policy.json", base / "readiness.json"
        )
        state.write_bytes(b"a" * (cli.MAX_INPUT_BYTES + 1))
        policy.write_bytes(dump(make_policy()))
        assert run_cli(cli.RealFS(), state, policy, output) == 2
        assert not output.exists()

    def test_invalid_utf8_rejected(self, tmp_path):
        base = tmp_path.resolve()
        state, policy, output = (
            base / "state.json", base / "policy.json", base / "readiness.json"
        )
        state.write_bytes(b'\xff\xfe{"schema_version": 1}')
        policy.write_bytes(dump(make_policy()))
        assert run_cli(cli.RealFS(), state, policy, output) == 2

    @pytest.mark.parametrize("payload", [
        b"not json at all",
        b"",
        b"[1, 2, 3]",
        b"null",
        b'"a string"',
    ])
    def test_malformed_or_non_object_rejected(self, tmp_path, payload):
        base = tmp_path.resolve()
        state, policy, output = (
            base / "state.json", base / "policy.json", base / "readiness.json"
        )
        state.write_bytes(payload)
        policy.write_bytes(dump(make_policy()))
        assert run_cli(cli.RealFS(), state, policy, output) == 2
        assert not output.exists()

    def test_duplicate_key_rejected(self, tmp_path):
        base = tmp_path.resolve()
        state, policy, output = (
            base / "state.json", base / "policy.json", base / "readiness.json"
        )
        state.write_bytes(b'{"schema_version": 1, "schema_version": 1}')
        policy.write_bytes(dump(make_policy()))
        assert run_cli(cli.RealFS(), state, policy, output) == 2

    @pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
    def test_non_finite_constant_rejected(self, tmp_path, constant):
        base = tmp_path.resolve()
        state, policy, output = (
            base / "state.json", base / "policy.json", base / "readiness.json"
        )
        state.write_bytes(
            b'{"schema_version": 1, "value": ' + constant + b"}"
        )
        policy.write_bytes(dump(make_policy()))
        assert run_cli(cli.RealFS(), state, policy, output) == 2

    def test_empty_path_string_rejected(self, tmp_path):
        fs, _state, policy, output = fake_env(tmp_path)
        assert cli.main(
            ["--state", "", "--policy", str(policy), "--output", str(output)],
            fs=fs,
        ) == 2

    def test_rejection_message_has_no_absolute_path(self, tmp_path, capsys):
        fs, state, policy, output = fake_env(tmp_path)
        del fs.files[state]
        assert run_cli(fs, state, policy, output) == 2
        err = capsys.readouterr().err
        assert "state input" in err
        assert str(tmp_path.resolve()) not in err


class TestOutputGuardrails:
    def test_output_cannot_overwrite_state_or_policy(self, tmp_path):
        fs, state, policy, _output = fake_env(tmp_path)
        original_state = fs.files[state]
        original_policy = fs.files[policy]
        assert run_cli(fs, state, policy, state) == 2
        assert run_cli(fs, state, policy, policy) == 2
        assert fs.files[state] == original_state
        assert fs.files[policy] == original_policy
        # sidecar 也未产生
        assert (state.parent / (state.name + ".sha256")) not in fs.files

    def test_sidecar_cannot_overwrite_state(self, tmp_path):
        base = tmp_path.resolve()
        state = base / "state.json.sha256"  # 输出名使其 sidecar 恰命中 state
        policy = base / "policy.json"
        output = base / "state.json"
        fs = FakeFS(files={state: dump(make_state()), policy: dump(make_policy())},
                    dirs=[base])
        original = fs.files[state]
        assert run_cli(fs, state, policy, output) == 2
        assert fs.files[state] == original

    def test_tmp_report_cannot_overwrite_state(self, tmp_path):
        # 监工反例:--state <dir>/state.json.tmp + --output <dir>/state.json
        # 会使 tmp_report 恰命中 state 输入;必须在写盘前 fail-closed 拒绝。
        base = tmp_path.resolve()
        state = base / "state.json.tmp"
        policy = base / "policy.json"
        output = base / "state.json"
        state.write_bytes(dump(make_state()))
        policy.write_bytes(dump(make_policy()))
        state_before, policy_before = state.read_bytes(), policy.read_bytes()
        assert run_cli(cli.RealFS(), state, policy, output) == 2
        assert state.read_bytes() == state_before
        assert policy.read_bytes() == policy_before
        assert not output.exists()
        assert not (base / "state.json.sha256").exists()

    def test_tmp_sidecar_cannot_overwrite_policy(self, tmp_path):
        # output 的 .sha256.tmp 中间文件恰命中 policy 输入时同样拒绝。
        base = tmp_path.resolve()
        state = base / "state.json"
        policy = base / "readiness.json.sha256.tmp"
        output = base / "readiness.json"
        state.write_bytes(dump(make_state()))
        policy.write_bytes(dump(make_policy()))
        state_before, policy_before = state.read_bytes(), policy.read_bytes()
        assert run_cli(cli.RealFS(), state, policy, output) == 2
        assert state.read_bytes() == state_before
        assert policy.read_bytes() == policy_before
        assert not output.exists()
        assert not (base / "readiness.json.sha256").exists()

    def test_output_parent_must_exist(self, tmp_path):
        fs, state, policy, _output = fake_env(tmp_path)
        missing_dir_output = tmp_path.resolve() / "nope" / "readiness.json"
        assert run_cli(fs, state, policy, missing_dir_output) == 2

    def test_output_replacing_existing_directory_fails_clean(self, tmp_path):
        base = tmp_path.resolve()
        state, policy = base / "state.json", base / "policy.json"
        output_dir = base / "readiness.json"
        output_dir.mkdir()
        state.write_bytes(dump(make_state()))
        policy.write_bytes(dump(make_policy()))
        assert run_cli(cli.RealFS(), state, policy, output_dir) == 2
        assert output_dir.is_dir()
        assert sorted(p.name for p in base.iterdir() if p.name.endswith(".tmp")) == []


class TestAtomicWriteAndImmutability:
    def test_replaces_previous_report_without_tmp_leftover(self, tmp_path):
        base = tmp_path.resolve()
        state, policy, output = (
            base / "state.json", base / "policy.json", base / "readiness.json"
        )
        state.write_bytes(dump(make_state()))
        policy.write_bytes(dump(make_policy()))
        output.write_bytes(b"stale previous report\n")
        (base / "readiness.json.sha256").write_bytes(b"stale sidecar\n")
        assert run_cli(cli.RealFS(), state, policy, output) == 0
        assert output.read_bytes() != b"stale previous report\n"
        assert (base / "readiness.json.sha256").read_bytes() != b"stale sidecar\n"
        assert sorted(p.name for p in base.iterdir() if p.name.endswith(".tmp")) == []

    def test_inputs_never_mutated(self, tmp_path):
        base = tmp_path.resolve()
        state, policy, output = (
            base / "state.json", base / "policy.json", base / "readiness.json"
        )
        state.write_bytes(dump(make_state()))
        policy.write_bytes(dump(make_policy()))
        state_before, policy_before = state.read_bytes(), policy.read_bytes()
        assert run_cli(cli.RealFS(), state, policy, output) == 0
        assert state.read_bytes() == state_before
        assert policy.read_bytes() == policy_before

    def test_write_failure_cleans_both_tmp_files(self, tmp_path):
        fs, state, policy, output = fake_env(tmp_path)
        base = tmp_path.resolve()
        fs.fail_writes = {base / "readiness.json.tmp"}
        assert run_cli(fs, state, policy, output) == 2
        assert output not in fs.files
        assert (base / "readiness.json.tmp") not in fs.files
        assert (base / "readiness.json.sha256.tmp") not in fs.files
        assert (base / "readiness.json.tmp") in fs.unlinked

    def test_second_replace_failure_cleans_tmp_and_fails_closed(self, tmp_path):
        # 诚实边界:两 replace 非单一事务——报告已替换、sidecar 未写,
        # 但两个 tmp 均被清理且 exit 2(fail-closed 可见失败)。
        fs, state, policy, output = fake_env(tmp_path)
        base = tmp_path.resolve()
        fs.fail_replace_dst = {base / "readiness.json.sha256"}
        assert run_cli(fs, state, policy, output) == 2
        assert output in fs.files  # 第一 replace 已落地(文档化残留窗口)
        assert (base / "readiness.json.sha256") not in fs.files
        assert (base / "readiness.json.tmp") not in fs.files
        assert (base / "readiness.json.sha256.tmp") not in fs.files
        assert (base / "readiness.json.sha256.tmp") in fs.unlinked


class TestNowValidation:
    @pytest.mark.parametrize("bad", [
        "2026-09-18T12:00:00",  # naive
        "not-a-timestamp",
        "2026-13-45T99:99:99Z",
    ])
    def test_invalid_now_rejected(self, tmp_path, bad):
        fs, state, policy, output = fake_env(tmp_path)
        assert run_cli(fs, state, policy, output, now=bad) == 2

    def test_naive_injected_clock_rejected(self, tmp_path):
        fs, state, policy, output = fake_env(tmp_path)
        assert run_cli(fs, state, policy, output,
                       now=None,
                       now_provider=lambda: NOW.replace(tzinfo=None)) == 2


class TestWrapperContract:
    def test_evaluator_receives_parsed_documents_and_now(self, tmp_path, monkeypatch):
        fs, state, policy, output = fake_env(tmp_path)
        calls = []

        def stub_evaluate(state_doc, policy_doc, now):
            calls.append((state_doc, policy_doc, now))
            return {
                "schema_version": 1,
                "generated_for": "2026-09-18T12:00:00+00:00",
                "overall": "fresh",
                "tasks": {},
                "problems": [],
            }

        monkeypatch.setattr(cli, "SCHEDULER",
                            type("Stub", (), {"evaluate_readiness": stub_evaluate}))
        assert run_cli(fs, state, policy, output) == 0
        assert len(calls) == 1
        state_doc, policy_doc, now = calls[0]
        assert state_doc == json.loads(fs.files[state].decode("utf-8"))
        assert policy_doc == json.loads(fs.files[policy].decode("utf-8"))
        assert now == NOW
        report = json.loads(fs.read_bytes(output).decode("utf-8"))
        assert report["evaluation"]["overall"] == "fresh"
        assert report["overall"] == "fresh"


class TestCliExitCodes:
    def test_missing_required_args_exit_two(self):
        with pytest.raises(SystemExit) as excinfo:
            cli.main([])
        assert excinfo.value.code == 2

    def test_success_exit_zero_even_when_overdue(self, tmp_path):
        fs, state, policy, output = fake_env(tmp_path)
        fs.files[state] = dump(make_state(ages=(timedelta(days=400),) * 3))
        assert run_cli(fs, state, policy, output) == 0


class TestSourceDiscipline:
    def test_module_source_has_no_forbidden_tokens(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for token in FORBIDDEN_TOKENS:
            assert token not in source, f"forbidden token {token!r} found"

    def test_module_exposes_only_pure_stdlib_imports(self):
        source = SCRIPT.read_text(encoding="utf-8")
        import_lines = [
            line for line in source.splitlines()
            if line.startswith(("import ", "from "))
        ]
        assert import_lines, "expected at least one import statement"
        allowed_roots = {
            "argparse", "hashlib", "importlib", "json", "os", "sys",
            "collections", "dataclasses", "datetime", "pathlib", "typing",
            "__future__",
        }
        for line in import_lines:
            root = line.split()[1].split(".")[0]
            assert root in allowed_roots, f"unexpected import: {line!r}"
