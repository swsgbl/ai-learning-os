r"""M14-06 tools/ops/production_recovery.py 契约测试：恢复编排决策与安全边界，零真实改动。

覆盖（全部不启动/停止任何容器、不碰 8010/8011、不发起真实 Docker/WSL 操作）：
- 语音调和决策矩阵（纯函数）：stopped→start；unmanaged/managed-running
  healthy→leave 不触碰；既有 managed-starting / port-mismatch /
  managed-mismatch(foreign) / unknown → 可见 fail（不 spawn/不发信号/不清理
  manifest——由「start 不被调用」断言锁定）；unmanaged 非 200 → 不触碰但
  DEGRADED；
- 决策状态空间与 voice_service_control.Inspection.state 的真实取值集合一致
  （交叉契约：构造 Inspection 实例枚举全部状态，编排侧无未覆盖状态）；
- pin check：env 文件缺失/缺键/与在线容器漂移 → 拒绝（仅报键名，绝不报值）；
  在线容器缺失 → 跳过比对放行；全部一致 → 放行；
- secret 不泄漏：伪 secret 标记值绝不出现在任何日志行/日志文件（防御性
  redact 兜底）；
- compose 命令纪律：up 恒为 up -d --no-build（绝不 --build）；dry-run 恒带
  --dry-run；enforce 在 pin 未就绪时绝不构造 up（fail-closed 零容器改动）；
  源码契约：绝不出现 stop/rm/kill/down/restart 子命令字面量；
- 端到端（全 fake）：绿灯 enforce / dry-run 计划 / dry-run 镜像拒绝 / 漂移
  拒绝 / 引擎等待超时 / 健康未达超时 / 语音 stopped×2 受控启动 / 语音
  fail 状态不触碰 / DEGRADED 退出。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "production_recovery.py"
VOICE_SCRIPT = REPO_ROOT / "tools" / "voice" / "voice_service_control.py"
SECRET_PATTERNS = ("sk-", "AKIA", "ghp_", "xoxb_", "-----BEGIN")
#: 与生产端口恒不冲突的伪 secret 标记（仅出现在 fake env/容器值里，
#: 断言其绝不进入任何日志行/文件）
MARK_AUTH = "ZX-markerauth-0123456789abcdef"
MARK_LIVEKIT = "ZX-markerlivekit-0123456789abcdef"
STACK_SERVICES = ("api", "livekit", "minio", "postgres", "redis", "web")


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


pr = _load_module(SCRIPT, "production_recovery_under_test")
voice_module = _load_module(VOICE_SCRIPT, "voice_service_control_for_recovery_test")


# ---------------------------------------------------------------- fakes

class FakeRunner:
    """伪 Docker/compose 命令面：按 argv 形态回放预制结果，记录全部调用。"""

    def __init__(self, *, engine_ready: bool = True, services: tuple[str, ...] = STACK_SERVICES,
                 ps_health: dict[str, str] | None = None, live_env: str | None = None,
                 live_image: str = "aios/api:m14-03-prod-rehearsal",
                 web_port: str = "127.0.0.1:3011", up_rc: int = 0,
                 up_out: str = "Container aios-m14-03-production-rehearsal-api-1  Running\n") -> None:
        self.engine_ready = engine_ready
        self.services = services
        self.ps_health = ps_health if ps_health is not None else {name: "healthy" for name in STACK_SERVICES}
        self.live_env = live_env
        self.live_image = live_image
        self.web_port = web_port
        self.up_rc = up_rc
        self.up_out = up_out
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, timeout: float = 60.0) -> pr.CommandResult:
        argv = tuple(str(item) for item in argv)
        self.calls.append(argv)
        joined = " ".join(argv)
        if argv[:2] == ("docker", "version"):
            if self.engine_ready:
                return pr.CommandResult(argv, 0, "27.5.1\n", "")
            return pr.CommandResult(argv, 1, "", "Cannot connect to the Docker daemon")
        if "config" in argv and "--quiet" in argv:
            return pr.CommandResult(argv, 0, "", "")
        if "--services" in argv:
            return pr.CommandResult(argv, 0, "\n".join(sorted(self.services)) + "\n", "")
        if argv[:2] == ("docker", "inspect") and ".Config.Env" in joined:
            if self.live_env is None:  # 模拟「api 容器不存在」（栈未起）
                return pr.CommandResult(argv, 1, "", "Error: No such object")
            return pr.CommandResult(argv, 0, self.live_env, "")
        if argv[:2] == ("docker", "inspect"):
            if self.live_image is None:  # 模拟「镜像事实探测失败/缺失」
                return pr.CommandResult(argv, 1, "", "Error: No such object")
            return pr.CommandResult(argv, 0, self.live_image + "\n", "")
        if argv[:2] == ("docker", "port"):
            return pr.CommandResult(argv, 0, self.web_port + "\n", "")
        if "ps" in argv and "--format" in argv:
            rows = "".join(
                json.dumps({"Service": name, "State": "running", "Health": health}) + "\n"
                for name, health in sorted(self.ps_health.items())
            )
            return pr.CommandResult(argv, 0, rows, "")
        if "up" in argv:
            return pr.CommandResult(argv, self.up_rc, self.up_out, "")
        return pr.CommandResult(argv, 0, "", "")


class FakeVoice:
    """伪语音网关：状态可预制；start 仅记录调用（绝不真实 spawn）。"""

    def __init__(self, states: dict[str, pr.VoiceStatus]) -> None:
        self.states = dict(states)
        self.starts: list[str] = []

    def inspect(self, engine: str) -> pr.VoiceStatus:
        return self.states[engine]

    def start(self, engine: str) -> tuple[int, str]:
        self.starts.append(engine)
        # 真实 start 后：manifest 归属进程存活、端口未起 = managed-starting
        self.states[engine] = pr.VoiceStatus(engine=engine, state="managed-starting", health_code=None)
        return 0, f"[voice-ctl] {engine}: 已启动（PID 4242，端口 8010）\n"


def _matching_env_text() -> str:
    return (
        f"AIOS_IMAGE_TAG=m14-03-prod-rehearsal\n"
        f"AIOS_APP_ENV=production\n"
        f"AIOS_WEB_PORT=3011\n"
        f"AIOS_AUTH_SECRET={MARK_AUTH}\n"
        f"AIOS_LIVEKIT_API_SECRET={MARK_LIVEKIT}\n"
    )


def _matching_live_env() -> str:
    return (
        "APP_ENV=production\n"
        f"AUTH_SECRET={MARK_AUTH}\n"
        f"LIVEKIT_API_SECRET={MARK_LIVEKIT}\n"
        "S3_BUCKET=aios-objects\n"
    )


def _healthy_voice() -> dict[str, pr.VoiceStatus]:
    return {
        "funasr": pr.VoiceStatus("funasr", "unmanaged-running", 200),
        "cosyvoice": pr.VoiceStatus("cosyvoice", "unmanaged-running", 200),
    }


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pr.time, "sleep", lambda _s: None, raising=True)


def _run(runner: FakeRunner, voice: FakeVoice, *, dry_run: bool, env_file: Path,
         engine_wait_seconds: int = 30, health_wait_seconds: int = 30) -> tuple[int, pr.RunLog]:
    log = pr.RunLog(redactions={MARK_AUTH: "***REDACTED:AIOS_AUTH_SECRET***",
                                MARK_LIVEKIT: "***REDACTED:AIOS_LIVEKIT_API_SECRET***"},
                    echo=False)
    code = pr.run_recovery(runner=runner, voice=voice, log=log, dry_run=dry_run,
                           env_file=env_file, engine_wait_seconds=engine_wait_seconds,
                           health_wait_seconds=health_wait_seconds)
    return code, log


def _up_calls(runner: FakeRunner) -> list[tuple[str, ...]]:
    return [argv for argv in runner.calls if "up" in argv]


def _assert_no_marker_leak(log: pr.RunLog) -> None:
    for line in log.lines:
        assert MARK_AUTH not in line, f"secret 标记泄漏到日志: {line}"
        assert MARK_LIVEKIT not in line, f"secret 标记泄漏到日志: {line}"


# ---------------------------------------------------------------- 决策矩阵

@pytest.mark.parametrize("state,health,expected_action,expected_severity", [
    ("stopped", None, "start", "ok"),
    ("unmanaged-running", 200, "leave", "ok"),
    ("unmanaged-running", 503, "leave", "warn"),
    ("unmanaged-running", None, "leave", "warn"),
    ("managed-running", 200, "leave", "ok"),
    ("managed-running", None, "leave", "warn"),
    ("managed-starting", None, "fail", "warn"),
    ("port-mismatch", None, "fail", "warn"),
    ("managed-mismatch", None, "fail", "warn"),
    ("unknown", None, "fail", "warn"),
])
def test_decide_voice_action_matrix(state, health, expected_action, expected_severity) -> None:
    decision = pr.decide_voice_action(state, health)
    assert decision.action == expected_action
    assert decision.severity == expected_severity


def test_decide_voice_action_started_this_run_variants() -> None:
    """本轮已启动：managed-starting / 非 200 升温 → leave ok（预期态，非失败）。"""
    starting = pr.decide_voice_action("managed-starting", None, started_this_run=True)
    assert (starting.action, starting.severity) == ("leave", "ok")
    warming = pr.decide_voice_action("managed-running", 503, started_this_run=True)
    assert (warming.action, warming.severity) == ("leave", "ok")


def test_decision_state_space_matches_voice_module() -> None:
    """交叉契约：决策覆盖 voice_service_control.Inspection.state 的全部真实取值。"""
    spec = voice_module.ENGINE_SPECS[0]
    manifest = voice_module.Manifest(
        engine=spec.name, pid=1234, port=8010, started_at="t", bootstrap=spec.bootstrap_script,
        cmd_markers=list(spec.cmd_markers), workdir="/wsl/repo", log="artifacts/voice/funasr/service/service.log",
    )

    def state_of(**kwargs) -> str:
        return voice_module.Inspection(spec=spec, port=8010, **kwargs).state

    actual = {
        state_of(manifest=None, listening=False),
        state_of(manifest=None, listening=True),
        state_of(manifest=manifest, ownership="port-mismatch"),
        state_of(manifest=manifest, ownership="ok", listening=True),
        state_of(manifest=manifest, ownership="ok", listening=False),
        state_of(manifest=manifest, ownership="foreign"),
        state_of(manifest=manifest, ownership="unknown"),
    }
    handled = {
        "stopped", "unmanaged-running", "managed-running",
        "managed-starting", "port-mismatch", "managed-mismatch", "unknown",
    }
    assert actual == handled  # 状态空间零漂移：编排对每个状态都有显式决策


# ---------------------------------------------------------------- pin check

def test_pin_missing_env_file_refuses(tmp_path: Path) -> None:
    runner = FakeRunner(live_env=_matching_live_env())
    log = pr.RunLog(echo=False)
    report = pr.check_pins(tmp_path / "absent.env", runner, "proj", log)
    assert not report.ok and not report.env_present
    assert report.missing_keys == pr.PIN_KEYS
    joined = "\n".join(log.lines)
    for key in pr.PIN_KEYS:
        assert key in joined  # 必需键名可见（值不存在，自然不泄漏）
    assert "拒绝" in joined


def test_pin_missing_required_keys(tmp_path: Path) -> None:
    env_file = tmp_path / "partial.env"
    env_file.write_text("AIOS_IMAGE_TAG=m14-03-prod-rehearsal\n", encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env())
    log = pr.RunLog(echo=False)
    report = pr.check_pins(env_file, runner, "proj", log)
    assert not report.ok
    assert "AIOS_APP_ENV" in report.missing_keys and "AIOS_AUTH_SECRET" in report.missing_keys


def test_pin_drift_reports_key_names_only(tmp_path: Path) -> None:
    env_file = tmp_path / "drift.env"
    env_file.write_text(_matching_env_text().replace("AIOS_WEB_PORT=3011", "AIOS_WEB_PORT=3999"),
                        encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env().replace("APP_ENV=production", "APP_ENV=staging"))
    log = pr.RunLog(echo=False)
    report = pr.check_pins(env_file, runner, "proj", log)
    assert not report.ok
    assert set(report.mismatched_keys) == {"AIOS_APP_ENV", "AIOS_WEB_PORT"}
    _assert_no_marker_leak(log)  # secret 在两侧一致，但任何输出都不得回显值
    joined = "\n".join(log.lines)
    assert "AIOS_WEB_PORT" in joined and "拒绝" in joined  # 键名可见；值恒不回显


def test_pin_no_live_containers_skips_compare(tmp_path: Path) -> None:
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=None)  # docker inspect api rc!=0 → 无在线容器
    log = pr.RunLog(echo=False)
    report = pr.check_pins(env_file, runner, "proj", log)
    assert report.ok and not report.live_present


def test_pin_all_match_ok(tmp_path: Path) -> None:
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env())
    log = pr.RunLog(echo=False)
    report = pr.check_pins(env_file, runner, "proj", log)
    assert report.ok and not report.mismatched_keys
    assert not report.missing_live_keys and not report.placeholder_keys
    _assert_no_marker_leak(log)


# ------------------------------------------- 回归：在线事实部分缺失（fail-open 修正）

def test_pin_partial_live_missing_web_port_refuses(tmp_path: Path) -> None:
    """docker port 无输出（AIOS_WEB_PORT 事实缺失）→ ok=False，键名可见，值不回显。"""
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env(), web_port="")
    log = pr.RunLog(echo=False)
    report = pr.check_pins(env_file, runner, "proj", log)
    assert not report.ok
    assert report.missing_live_keys == ("AIOS_WEB_PORT",)
    joined = "\n".join(log.lines)
    assert "AIOS_WEB_PORT" in joined and "事实缺失" in joined
    _assert_no_marker_leak(log)


def test_pin_partial_live_missing_image_fact_refuses(tmp_path: Path) -> None:
    """镜像 inspect 失败（AIOS_IMAGE_TAG 事实缺失）→ ok=False（不得按跳过放行）。"""
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env(), live_image=None)
    log = pr.RunLog(echo=False)
    report = pr.check_pins(env_file, runner, "proj", log)
    assert not report.ok
    assert report.missing_live_keys == ("AIOS_IMAGE_TAG",)


def test_pin_partial_live_missing_secret_fact_refuses(tmp_path: Path) -> None:
    """api 容器 env 缺 AUTH_SECRET 行（事实缺失）→ ok=False（与「值不等」同权重）。"""
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    partial_live = _matching_live_env().replace(f"AUTH_SECRET={MARK_AUTH}\n", "")
    runner = FakeRunner(live_env=partial_live)
    log = pr.RunLog(echo=False)
    report = pr.check_pins(env_file, runner, "proj", log)
    assert not report.ok
    assert report.missing_live_keys == ("AIOS_AUTH_SECRET",)
    _assert_no_marker_leak(log)


def test_pin_multiple_missing_live_facts_ok_false(tmp_path: Path) -> None:
    """多处在线事实同时缺失 → 全部键名可见、ok=False（缺事实 ≠ 跳过）。"""
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env="APP_ENV=production\n", live_image=None, web_port="")
    log = pr.RunLog(echo=False)
    report = pr.check_pins(env_file, runner, "proj", log)
    assert not report.ok
    assert set(report.missing_live_keys) == {"AIOS_AUTH_SECRET", "AIOS_LIVEKIT_API_SECRET",
                                             "AIOS_IMAGE_TAG", "AIOS_WEB_PORT"}
    assert report.mismatched_keys == ()  # 缺失与不等分类分离，报告清晰
    _assert_no_marker_leak(log)


def test_e2e_enforce_partial_live_facts_refuse_before_up(tmp_path: Path) -> None:
    """端到端等价：在线事实不完整 → enforce 在 up 之前拒绝（零容器改动）。"""
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env(), web_port="")
    voice = FakeVoice(_healthy_voice())
    code, log = _run(runner, voice, dry_run=False, env_file=env_file)
    assert code == pr.EXIT_ERROR
    assert _up_calls(runner) == []
    assert any("事实缺失" in line for line in log.lines)
    _assert_no_marker_leak(log)


# ------------------------------------------- 回归：模板占位 secret 恒拒绝

TEMPLATE_SECRET_PLACEHOLDER = "<部署时生成的真实值——绝不提交>"


def test_placeholder_pin_keys_pure() -> None:
    values = {
        "AIOS_IMAGE_TAG": "m14-03-prod-rehearsal",  # 真实值 → 非占位
        "AIOS_APP_ENV": "production",
        "AIOS_WEB_PORT": "3011",
        "AIOS_AUTH_SECRET": TEMPLATE_SECRET_PLACEHOLDER,  # 模板原文
        "AIOS_LIVEKIT_API_SECRET": "<fill-me>",  # 通用 <...> 包裹
    }
    assert pr.placeholder_pin_keys(values) == ("AIOS_AUTH_SECRET", "AIOS_LIVEKIT_API_SECRET")
    assert pr.placeholder_pin_keys({"AIOS_IMAGE_TAG": "x"}) == ()
    assert pr.placeholder_pin_keys({"AIOS_WEB_PORT": "<>"}) == ()  # 空尖括号不算（len>2 约束）


def test_pin_placeholder_secrets_refused_with_stack_up(tmp_path: Path) -> None:
    """照抄模板（占位 secret）+ 在线栈存在 → 拒绝；键名可见、占位文本不回显。"""
    env_file = tmp_path / "pin.env"
    env_file.write_text(
        "AIOS_IMAGE_TAG=m14-03-prod-rehearsal\nAIOS_APP_ENV=production\nAIOS_WEB_PORT=3011\n"
        f"AIOS_AUTH_SECRET={TEMPLATE_SECRET_PLACEHOLDER}\n"
        f"AIOS_LIVEKIT_API_SECRET={TEMPLATE_SECRET_PLACEHOLDER}\n",
        encoding="utf-8",
    )
    runner = FakeRunner(live_env=_matching_live_env())
    log = pr.RunLog(echo=False)
    report = pr.check_pins(env_file, runner, "proj", log)
    assert not report.ok
    assert report.placeholder_keys == ("AIOS_AUTH_SECRET", "AIOS_LIVEKIT_API_SECRET")
    joined = "\n".join(log.lines)
    assert TEMPLATE_SECRET_PLACEHOLDER not in joined  # 占位文本本身也不回显（键名-only 纪律）
    assert "AIOS_AUTH_SECRET" in joined and "占位" in joined


def test_e2e_placeholder_env_with_stack_down_refuses_up(tmp_path: Path) -> None:
    """原 fail-open 关口：栈未起（无在线容器可比对）+ 模板占位 secret →
    修正前 ok=True 会用占位值创建容器；修正后 enforce 拒绝 up（键名-only）。"""
    env_file = tmp_path / "pin.env"
    env_file.write_text(
        "AIOS_IMAGE_TAG=m14-03-prod-rehearsal\nAIOS_APP_ENV=production\nAIOS_WEB_PORT=3011\n"
        f"AIOS_AUTH_SECRET={TEMPLATE_SECRET_PLACEHOLDER}\n"
        f"AIOS_LIVEKIT_API_SECRET=<fill-me>\n",
        encoding="utf-8",
    )
    runner = FakeRunner(live_env=None)  # api 容器不存在 = 栈未起
    voice = FakeVoice(_healthy_voice())
    code, log = _run(runner, voice, dry_run=False, env_file=env_file)
    assert code == pr.EXIT_ERROR
    assert _up_calls(runner) == []
    joined = "\n".join(log.lines)
    assert "占位" in joined and TEMPLATE_SECRET_PLACEHOLDER not in joined and "<fill-me>" not in joined


def test_runlog_redacts_secrets(tmp_path: Path) -> None:
    log = pr.RunLog(redactions={MARK_AUTH: "***REDACTED:AIOS_AUTH_SECRET***"}, echo=False)
    log.say(f"compose 输出: AUTH_SECRET={MARK_AUTH} 附近内容")
    assert MARK_AUTH not in log.lines[-1]
    path = log.write_file(tmp_path)
    assert MARK_AUTH not in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- compose 命令纪律

def test_compose_up_argv_shape() -> None:
    argv = pr._compose_base(Path("c.yml"), "proj", "local", Path("e.env")) + ["up", "-d", "--no-build"]
    assert list(argv[:2]) == ["docker", "compose"]
    assert argv[argv.index("-p") + 1] == "proj"
    assert argv[argv.index("--env-file") + 1] == str(Path("e.env"))
    assert argv[argv.index("--profile") + 1] == "local"
    assert argv[-3:] == ["up", "-d", "--no-build"]
    assert "--build" not in argv


def test_source_never_issues_destructive_subcommands() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for literal in ('"down"', '"stop"', '"kill"', '"rm"', '"restart"', '"reset"'):
        assert literal not in source, f"禁止出现的子命令字面量: {literal}"
    assert "CREATE_NO_WINDOW" in source  # Windows 侧无弹窗纪律
    for pattern in SECRET_PATTERNS:
        assert pattern not in source


def test_parser_defaults() -> None:
    args = pr.build_parser().parse_args([])
    assert args.project == "aios-m14-03-production-rehearsal"
    assert args.profile == "local"
    assert args.env_file == pr.REPO_ROOT / "infra" / "env.production-recovery"
    assert not args.dry_run


# ---------------------------------------------------------------- 端到端（全 fake）

def test_e2e_enforce_green_with_matching_pins(tmp_path: Path) -> None:
    """绿灯 enforce：pin 一致 → up -d --no-build（无 --build）→ 全 healthy → 语音不触碰。"""
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env())
    voice = FakeVoice(_healthy_voice())
    code, log = _run(runner, voice, dry_run=False, env_file=env_file)
    assert code == pr.EXIT_OK
    ups = _up_calls(runner)
    assert len(ups) == 1
    assert ups[0][-3:] == ("up", "-d", "--no-build")
    assert "--dry-run" not in ups[0] and "--build" not in ups[0]
    assert voice.starts == []  # healthy unmanaged-running 恒不触碰
    _assert_no_marker_leak(log)
    assert any("结果: OK" in line for line in log.lines)


def test_e2e_dry_run_with_pins_uses_compose_dry_run(tmp_path: Path) -> None:
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env())
    voice = FakeVoice(_healthy_voice())
    code, log = _run(runner, voice, dry_run=True, env_file=env_file)
    assert code == pr.EXIT_OK
    ups = _up_calls(runner)
    assert len(ups) == 1 and "--dry-run" in ups[0]
    assert voice.starts == []
    assert any("stack health 快照" in line for line in log.lines)


def test_e2e_dry_run_missing_env_mirrors_refusal(tmp_path: Path) -> None:
    """dry-run + env 缺失：不构造任何 up；退出码镜像 enforce 拒绝（可见）。"""
    runner = FakeRunner(live_env=_matching_live_env())
    voice = FakeVoice(_healthy_voice())
    code, log = _run(runner, voice, dry_run=True, env_file=tmp_path / "absent.env")
    assert code == pr.EXIT_ERROR
    assert _up_calls(runner) == []
    assert any("pin-not-ready" in line for line in log.lines)


def test_e2e_enforce_drift_refuses_before_up(tmp_path: Path) -> None:
    env_file = tmp_path / "drift.env"
    env_file.write_text(_matching_env_text().replace(MARK_AUTH, "ZX-other-secret-9999xxxx"), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env())
    voice = FakeVoice(_healthy_voice())
    code, log = _run(runner, voice, dry_run=False, env_file=env_file)
    assert code == pr.EXIT_ERROR
    assert _up_calls(runner) == []  # fail-closed：up 之前拒绝，零容器改动
    joined = "\n".join(log.lines)
    assert "AIOS_AUTH_SECRET" in joined and "拒绝" in joined
    assert MARK_AUTH not in joined
    _assert_no_marker_leak(log)


def test_e2e_engine_wait_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _no_sleep(monkeypatch)
    runner = FakeRunner(engine_ready=False)
    voice = FakeVoice(_healthy_voice())
    code, log = _run(runner, voice, dry_run=False, env_file=tmp_path / "any.env", engine_wait_seconds=0)
    assert code == pr.EXIT_ERROR
    assert any("等待超时" in line for line in log.lines)
    assert _up_calls(runner) == []


def test_e2e_health_gate_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _no_sleep(monkeypatch)
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env(), ps_health={"api": "starting"})
    voice = FakeVoice(_healthy_voice())
    code, log = _run(runner, voice, dry_run=False, env_file=env_file, health_wait_seconds=0)
    assert code == pr.EXIT_ERROR
    assert any("stack-health" in line or "非 healthy" in line for line in log.lines)


@pytest.mark.parametrize("engine_states,expected_starts,expect_exit", [
    # stopped×2：两引擎均经受控 start，启动后 managed-starting 按本轮口径 = OK
    ({"funasr": "stopped", "cosyvoice": "stopped"}, ["funasr", "cosyvoice"], pr.EXIT_OK),
    # fail 状态：绝不调用 start（不 spawn/不发信号/不清理 manifest）
    ({"funasr": "port-mismatch", "cosyvoice": "unmanaged-running"}, [], pr.EXIT_ERROR),
    ({"funasr": "unmanaged-running", "cosyvoice": "unknown"}, [], pr.EXIT_ERROR),
    ({"funasr": "managed-mismatch", "cosyvoice": "stopped"}, ["cosyvoice"], pr.EXIT_ERROR),
    ({"funasr": "managed-starting", "cosyvoice": "stopped"}, ["cosyvoice"], pr.EXIT_ERROR),
])
def test_e2e_voice_reconciliation(engine_states, expected_starts, expect_exit, tmp_path: Path) -> None:
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env())
    states = {
        name: pr.VoiceStatus(name, state, 200 if state == "unmanaged-running" else None)
        for name, state in engine_states.items()
    }
    voice = FakeVoice(states)
    code, log = _run(runner, voice, dry_run=False, env_file=env_file)
    assert code == expect_exit
    assert voice.starts == expected_starts  # start 只作用于 stopped 引擎
    _assert_no_marker_leak(log)


def test_e2e_unmanaged_unhealthy_degrades_visibly(tmp_path: Path) -> None:
    """unmanaged-running 但 /health 非 200：不触碰（拒绝误杀边界）+ DEGRADED 退出。"""
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env())
    voice = FakeVoice({
        "funasr": pr.VoiceStatus("funasr", "unmanaged-running", 200),
        "cosyvoice": pr.VoiceStatus("cosyvoice", "unmanaged-running", 503),
    })
    code, log = _run(runner, voice, dry_run=False, env_file=env_file)
    assert code == pr.EXIT_ERROR
    assert voice.starts == []
    assert any("DEGRADED" in line for line in log.lines)


def test_e2e_voice_start_failure_visible(tmp_path: Path) -> None:
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env())

    class FailingVoice(FakeVoice):
        def start(self, engine: str) -> tuple[int, str]:
            self.starts.append(engine)
            return 3, f"[voice-ctl] {engine}: 拒绝启动——端口已被不受管进程占用\n"

    voice = FailingVoice({
        "funasr": pr.VoiceStatus("funasr", "stopped", None),
        "cosyvoice": pr.VoiceStatus("cosyvoice", "unmanaged-running", 200),
    })
    code, log = _run(runner, voice, dry_run=False, env_file=env_file)
    assert code == pr.EXIT_ERROR
    assert voice.starts == ["funasr"]
    assert any("start 失败" in line for line in log.lines)


def test_e2e_compose_up_failure_visible(tmp_path: Path) -> None:
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env(), up_rc=1, up_out="")
    voice = FakeVoice(_healthy_voice())
    code, log = _run(runner, voice, dry_run=False, env_file=env_file)
    assert code == pr.EXIT_ERROR
    assert any("compose-up" in line for line in log.lines)
