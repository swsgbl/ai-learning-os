r"""M14-06 tools/ops/production_recovery.py 契约测试：恢复编排决策与安全边界，零真实改动。

覆盖（全部不启动/停止任何容器、不碰 8010/8011、不发起真实 Docker/WSL 操作）：
- 语音调和决策矩阵（纯函数）：stopped→start；unmanaged/managed-running
  healthy→leave 不触碰；既有 managed-starting / port-mismatch /
  managed-mismatch(foreign) / unknown → 可见 fail（不 spawn/不发信号/不清理
  manifest——由「start 不被调用」断言锁定）；unmanaged 非 200 → 不触碰但
  DEGRADED；
- 决策状态空间与 voice_service_control.Inspection.state 的真实取值集合一致
  （交叉契约：构造 Inspection 实例枚举全部状态，编排侧无未覆盖状态）；
- pin check（M14-09 独立 AIOS_WEB_IMAGE_TAG；M14-38 九键，增三拓扑键
  AIOS_BIND_IP/AIOS_LIVEKIT_BIND_IP/AIOS_PUBLIC_LIVEKIT_URL——LAN cutover
  防漂移）：env 文件缺失/缺键/与在线容器漂移 → 拒绝（仅报键名，绝不报值）；
  在线容器缺失 → 跳过比对放行；全部一致 → 放行；
- secret 不泄漏：伪 secret 标记值绝不出现在任何日志行/日志文件（防御性
  redact 兜底）；
- compose 命令纪律：up 恒为 up -d --no-build（绝不 --build）；dry-run 恒带
  --dry-run；enforce 在 pin 未就绪时绝不构造 up（fail-closed 零容器改动）；
  源码契约：绝不出现 stop/rm/kill/down/restart 子命令字面量，绝不出现
  --no-deps/--no-recreate（恢复路径整栈 up 口径，与 M14-38 cutover 的
  最小范围重建分工）；
- M14-39 恢复边界：pin 通过后先做只读栈健康快照——六服务全部
  healthy/running 时 dry-run 与 enforce 一致跳过 compose up（健康栈无需
  up；startup recovery 恢复健康，不做部署/config drift 收敛）；栈有缺失/
  不健康时保留原唯一 up 语义；up 前只读本地镜像预检，aios/minio 自建
  镜像缺失且栈非全健康 → minio-local-image-missing fail-closed（绝不
  pull/build）；预检常量与 compose image: 锚点交叉锁定；
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
#: M14-13 起的 minio 自建镜像锚点（infra/docker-compose.yml image: 同步）
MINIO_IMAGE = "aios/minio:RELEASE.2025-10-15T17-29-55Z"


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
    """伪 Docker/compose 命令面：按 argv 形态回放预制结果，记录全部调用。

    M14-09：live_web_image 独立于 live_image——web 容器镜像事实与 api 容器
    分别回放（各自可独立置 None 模拟事实缺失）。
    """

    def __init__(self, *, engine_ready: bool = True, services: tuple[str, ...] = STACK_SERVICES,
                 ps_health: dict[str, str] | None = None, live_env: str | None = None,
                 live_image: str = "aios/api:m14-03-prod-rehearsal",
                 live_web_image: str = "aios/web:m14-03-prod-rehearsal",
                 web_port: str = "127.0.0.1:3011", livekit_port: str = "127.0.0.1:7880",
                 up_rc: int = 0,
                 up_out: str = "Container aios-m14-03-production-rehearsal-api-1  Running\n",
                 missing_local_images: frozenset[str] = frozenset(),
                 post_up_ps_health: dict[str, str] | None = None) -> None:
        self.engine_ready = engine_ready
        self.services = services
        self.ps_health = ps_health if ps_health is not None else {name: "healthy" for name in STACK_SERVICES}
        self.live_env = live_env
        self.live_image = live_image
        self.live_web_image = live_web_image
        self.web_port = web_port
        self.livekit_port = livekit_port
        self.up_rc = up_rc
        self.up_out = up_out
        # M14-39：本机缺失的本地镜像（docker image inspect rc=1 回放）；
        # post_up_ps_health = up 之后 ps 事实翻转（enforce 恢复绿灯回放）
        self.missing_local_images = frozenset(missing_local_images)
        self.post_up_ps_health = post_up_ps_health
        self.up_seen = False
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
        if argv[:3] == ("docker", "image", "inspect"):
            # M14-39 本地镜像预检回放：缺失集合 → rc=1（Error: No such image）
            if str(argv[3]) in self.missing_local_images:
                return pr.CommandResult(argv, 1, "", "Error: No such image")
            return pr.CommandResult(argv, 0, "sha256:0000000000000000000000000000000000000000\n", "")
        if argv[:2] == ("docker", "inspect") and ".Config.Env" in joined:
            if self.live_env is None:  # 模拟「api 容器不存在」（栈未起）
                return pr.CommandResult(argv, 1, "", "Error: No such object")
            return pr.CommandResult(argv, 0, self.live_env, "")
        if argv[:2] == ("docker", "inspect"):
            # 镜像事实：api 与 web 容器分别回放（M14-09 独立 tag）
            target = str(argv[-1])
            image = self.live_web_image if "-web-" in target else self.live_image
            if image is None:  # 模拟「镜像事实探测失败/缺失」
                return pr.CommandResult(argv, 1, "", "Error: No such object")
            return pr.CommandResult(argv, 0, image + "\n", "")
        if argv[:2] == ("docker", "port"):
            # M14-38：web → 宿主端口段；livekit → 宿主绑定 IP 段（拓扑事实，
            # 空串 = docker port 无输出 = 在线事实缺失）
            if "-livekit-" in str(argv[2]):
                return pr.CommandResult(argv, 0, self.livekit_port + "\n", "")
            return pr.CommandResult(argv, 0, self.web_port + "\n", "")
        if "ps" in argv and "--format" in argv:
            health = (self.post_up_ps_health if self.up_seen
                      and self.post_up_ps_health is not None else self.ps_health)
            rows = "".join(
                json.dumps({"Service": name, "State": "running", "Health": h}) + "\n"
                for name, h in sorted(health.items())
            )
            return pr.CommandResult(argv, 0, rows, "")
        if "up" in argv:
            self.up_seen = True
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
        f"AIOS_WEB_IMAGE_TAG=m14-03-prod-rehearsal\n"
        f"AIOS_APP_ENV=production\n"
        f"AIOS_WEB_PORT=3011\n"
        f"AIOS_AUTH_SECRET={MARK_AUTH}\n"
        f"AIOS_LIVEKIT_API_SECRET={MARK_LIVEKIT}\n"
        f"AIOS_BIND_IP=127.0.0.1\n"
        f"AIOS_LIVEKIT_BIND_IP=127.0.0.1\n"
        f"AIOS_PUBLIC_LIVEKIT_URL=ws://127.0.0.1:7880\n"
    )


def _matching_live_env() -> str:
    return (
        "APP_ENV=production\n"
        f"AUTH_SECRET={MARK_AUTH}\n"
        f"LIVEKIT_API_SECRET={MARK_LIVEKIT}\n"
        "S3_BUCKET=aios-objects\n"
        "HOST_BIND_IP=127.0.0.1\n"
        "PUBLIC_LIVEKIT_URL=ws://127.0.0.1:7880\n"
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


def test_pin_partial_live_missing_web_image_fact_refuses(tmp_path: Path) -> None:
    """M14-09：web 容器镜像 inspect 失败（AIOS_WEB_IMAGE_TAG 事实独立缺失，
    api 事实完好）→ ok=False，仅报 AIOS_WEB_IMAGE_TAG（键名可见、值不回显）。"""
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env(), live_web_image=None)
    log = pr.RunLog(echo=False)
    report = pr.check_pins(env_file, runner, "proj", log)
    assert not report.ok
    assert report.missing_live_keys == ("AIOS_WEB_IMAGE_TAG",)
    joined = "\n".join(log.lines)
    assert "AIOS_WEB_IMAGE_TAG" in joined and "事实缺失" in joined
    _assert_no_marker_leak(log)


def test_pin_web_tag_drift_reports_key_independent_of_api(tmp_path: Path) -> None:
    """M14-09：AIOS_WEB_IMAGE_TAG 与在线 web 镜像漂移（api tag 仍一致）→
    仅报 AIOS_WEB_IMAGE_TAG 不一致——web tag 独立锚点，不连坐 api。"""
    env_file = tmp_path / "drift-web.env"
    env_file.write_text(_matching_env_text().replace(
        "AIOS_WEB_IMAGE_TAG=m14-03-prod-rehearsal", "AIOS_WEB_IMAGE_TAG=m14-09-web-only"),
        encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env())
    log = pr.RunLog(echo=False)
    report = pr.check_pins(env_file, runner, "proj", log)
    assert not report.ok
    assert report.mismatched_keys == ("AIOS_WEB_IMAGE_TAG",)
    joined = "\n".join(log.lines)
    assert "AIOS_WEB_IMAGE_TAG" in joined and "拒绝" in joined
    _assert_no_marker_leak(log)


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
                                             "AIOS_IMAGE_TAG", "AIOS_WEB_PORT",
                                             "AIOS_BIND_IP", "AIOS_PUBLIC_LIVEKIT_URL"}
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
        "AIOS_WEB_IMAGE_TAG": "m14-03-prod-rehearsal",  # M14-09 独立键 → 非占位
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
        "AIOS_IMAGE_TAG=m14-03-prod-rehearsal\nAIOS_WEB_IMAGE_TAG=m14-03-prod-rehearsal\nAIOS_APP_ENV=production\nAIOS_WEB_PORT=3011\n"
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
        "AIOS_IMAGE_TAG=m14-03-prod-rehearsal\nAIOS_WEB_IMAGE_TAG=m14-03-prod-rehearsal\nAIOS_APP_ENV=production\nAIOS_WEB_PORT=3011\n"
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
    # M14-39：恢复路径整栈 up 口径——绝不 --no-deps/--no-recreate/pull/build
    # （--no-deps 是 M14-38 cutover 最小范围重建的分工语义，恢复路径不得
    # 借它隐藏 depends_on 漂移；--no-recreate 会隐藏计划重建）
    for literal in ('"--no-deps"', '"--no-recreate"', '"pull"', '"build"'):
        assert literal not in source, f"禁止出现的命令字面量: {literal}"
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
    """绿灯 enforce（栈全健康，M14-39）：pin 一致 → 健康栈无需 up（零 up
    构造）→ 健康门即过 → 语音不触碰 → OK。up 实际执行路径见
    test_e2e_unhealthy_stack_keeps_unique_up_semantics。"""
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env())  # ps 默认六服务全 healthy
    voice = FakeVoice(_healthy_voice())
    code, log = _run(runner, voice, dry_run=False, env_file=env_file)
    assert code == pr.EXIT_OK
    assert _up_calls(runner) == []  # 健康栈绝不构造 up
    assert any("健康栈无需 up" in line for line in log.lines)
    assert voice.starts == []  # healthy unmanaged-running 恒不触碰
    _assert_no_marker_leak(log)
    assert any("结果: OK" in line for line in log.lines)


def test_e2e_dry_run_healthy_stack_skips_up(tmp_path: Path) -> None:
    """dry-run（栈全健康，M14-39）：与 enforce 同语义跳过 up——只读快照
    可见 + 零 up 构造（健康栈 dry-run 不再产出「计划重建」）。"""
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env())
    voice = FakeVoice(_healthy_voice())
    code, log = _run(runner, voice, dry_run=True, env_file=env_file)
    assert code == pr.EXIT_OK
    assert _up_calls(runner) == []
    assert any("健康栈无需 up" in line for line in log.lines)
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


def test_e2e_compose_up_failure_visible(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """栈非全健康 → 走 up 路径：up rc=1 → compose-up 可见失败（M14-39 起
    up 仅在栈非全健康时构造，故本测试预制 api unhealthy）。"""
    _no_sleep(monkeypatch)
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env(), up_rc=1, up_out="",
                        ps_health={**{name: "healthy" for name in STACK_SERVICES},
                                   "api": "unhealthy"})
    voice = FakeVoice(_healthy_voice())
    code, log = _run(runner, voice, dry_run=False, env_file=env_file,
                     health_wait_seconds=0)
    assert code == pr.EXIT_ERROR
    assert len(_up_calls(runner)) == 1  # up 已构造且失败（非健康栈不跳过）
    assert any("compose-up" in line for line in log.lines)


# --------------------------- M14-38：拓扑键 pin（九键防漂移，LAN cutover 收口）
# 动机：compose 中 AIOS_LIVEKIT_BIND_IP 缺省回落 AIOS_BIND_IP→127.0.0.1——
# 不 pin 则恢复路径会把 LAN 拓扑静默重建回 loopback。在线事实：
# AIOS_BIND_IP/AIOS_PUBLIC_LIVEKIT_URL 取 api 容器 env（HOST_BIND_IP=/
# PUBLIC_LIVEKIT_URL=，空串也是事实）；AIOS_LIVEKIT_BIND_IP 取
# `docker port <livekit> 7880` 宿主绑定段。输出仅键名——拓扑值虽非密钥
# 也不回显（键名-only 纪律）。

LAN_IP = "192.168.8.3"
LOOPBACK_WS = "ws://127.0.0.1:7880"


def _lan_env_text() -> str:
    return (
        _matching_env_text()
        .replace("AIOS_BIND_IP=127.0.0.1", f"AIOS_BIND_IP={LAN_IP}")
        .replace("AIOS_LIVEKIT_BIND_IP=127.0.0.1", f"AIOS_LIVEKIT_BIND_IP={LAN_IP}")
        .replace(f"AIOS_PUBLIC_LIVEKIT_URL={LOOPBACK_WS}", f"AIOS_PUBLIC_LIVEKIT_URL=ws://{LAN_IP}:7880")
    )


def _lan_live_env() -> str:
    return (
        _matching_live_env()
        .replace("HOST_BIND_IP=127.0.0.1", f"HOST_BIND_IP={LAN_IP}")
        .replace(f"PUBLIC_LIVEKIT_URL={LOOPBACK_WS}", f"PUBLIC_LIVEKIT_URL=ws://{LAN_IP}:7880")
    )


def test_topology_pin_keys_exported_and_pinned() -> None:
    """三拓扑键已导出且纳入 PIN_KEYS（缺一即整体 pin 拒绝）。"""
    assert pr.TOPOLOGY_PIN_KEYS == ("AIOS_BIND_IP", "AIOS_LIVEKIT_BIND_IP",
                                    "AIOS_PUBLIC_LIVEKIT_URL")
    assert set(pr.TOPOLOGY_PIN_KEYS) <= set(pr.PIN_KEYS)
    assert len(pr.PIN_KEYS) == 9


def test_pin_topology_keys_missing_from_env_refuses(tmp_path: Path) -> None:
    """env 缺拓扑键 → 拒绝：恢复路径不得以 compose 缺省静默重建回 loopback。"""
    env_file = tmp_path / "pin.env"
    env_file.write_text(
        _matching_env_text()
        .replace("AIOS_BIND_IP=127.0.0.1\n", "")
        .replace("AIOS_LIVEKIT_BIND_IP=127.0.0.1\n", "")
        .replace(f"AIOS_PUBLIC_LIVEKIT_URL={LOOPBACK_WS}\n", ""),
        encoding="utf-8",
    )
    runner = FakeRunner(live_env=_matching_live_env())
    log = pr.RunLog(echo=False)
    report = pr.check_pins(env_file, runner, "proj", log)
    assert not report.ok
    assert set(pr.TOPOLOGY_PIN_KEYS) <= set(report.missing_keys)
    joined = "\n".join(log.lines)
    for key in pr.TOPOLOGY_PIN_KEYS:
        assert key in joined
    _assert_no_marker_leak(log)


def test_pin_lan_topology_all_match_ok(tmp_path: Path) -> None:
    """LAN cutover 拓扑（三键全 LAN）且与在线容器一致 → 放行：拓扑键不限定值，
    只锁定「env 声明 = 在线事实」。"""
    env_file = tmp_path / "lan.env"
    env_file.write_text(_lan_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_lan_live_env(), livekit_port=f"{LAN_IP}:7880")
    log = pr.RunLog(echo=False)
    report = pr.check_pins(env_file, runner, "proj", log)
    assert report.ok and not report.mismatched_keys and not report.missing_live_keys
    _assert_no_marker_leak(log)


def test_pin_livekit_bind_drift_refuses(tmp_path: Path) -> None:
    """env 已切 LAN 但在线 livekit 端口仍绑 loopback（重建不完整/漂移）→ 拒绝。"""
    env_file = tmp_path / "lan.env"
    env_file.write_text(_lan_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_lan_live_env(), livekit_port="127.0.0.1:7880")
    log = pr.RunLog(echo=False)
    report = pr.check_pins(env_file, runner, "proj", log)
    assert not report.ok
    assert report.mismatched_keys == ("AIOS_LIVEKIT_BIND_IP",)
    joined = "\n".join(log.lines)
    assert "AIOS_LIVEKIT_BIND_IP" in joined and "拒绝" in joined
    _assert_no_marker_leak(log)


def test_pin_public_url_empty_live_value_refuses(tmp_path: Path) -> None:
    """容器 PUBLIC_LIVEKIT_URL 为空串（compose 默认未注入）而 env 非空 →
    空串事实与非空声明不等 = 漂移可见拒绝（绝不按「缺失跳过」放行）。"""
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env().replace(
        f"PUBLIC_LIVEKIT_URL={LOOPBACK_WS}", "PUBLIC_LIVEKIT_URL="))
    log = pr.RunLog(echo=False)
    report = pr.check_pins(env_file, runner, "proj", log)
    assert not report.ok
    assert report.mismatched_keys == ("AIOS_PUBLIC_LIVEKIT_URL",)
    assert "AIOS_PUBLIC_LIVEKIT_URL" not in report.missing_live_keys  # 空串 ≠ 缺失
    _assert_no_marker_leak(log)


def test_pin_topology_live_facts_missing_refuse(tmp_path: Path) -> None:
    """在线拓扑事实缺失（api env 无 HOST_BIND_IP 行 + docker port livekit 无输出）
    → 事实缺失拒绝（fail-closed，不得按跳过放行）。"""
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(
        live_env=_matching_live_env().replace("HOST_BIND_IP=127.0.0.1\n", ""),
        livekit_port="",
    )
    log = pr.RunLog(echo=False)
    report = pr.check_pins(env_file, runner, "proj", log)
    assert not report.ok
    assert set(report.missing_live_keys) == {"AIOS_BIND_IP", "AIOS_LIVEKIT_BIND_IP"}
    joined = "\n".join(log.lines)
    assert "事实缺失" in joined
    _assert_no_marker_leak(log)


def test_pin_topology_drift_never_echoes_values(tmp_path: Path) -> None:
    """拓扑键漂移时输出仅键名——LAN IP/URL 虽非密钥也不回显（键名-only 纪律，
    防部署拓扑细节进入日志）。"""
    env_file = tmp_path / "lan.env"
    env_file.write_text(_lan_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env())  # 在线仍 loopback 全套
    log = pr.RunLog(echo=False)
    report = pr.check_pins(env_file, runner, "proj", log)
    assert not report.ok and report.mismatched_keys == pr.TOPOLOGY_PIN_KEYS
    joined = "\n".join(log.lines)
    assert LAN_IP not in joined and LOOPBACK_WS not in joined
    _assert_no_marker_leak(log)


def test_e2e_lan_topology_green_full_recovery(tmp_path: Path) -> None:
    """端到端：LAN 拓扑九键全绿 → OK（LAN 不是特殊分支，与 loopback 同码
    路径；默认 ps 全 healthy → M14-39 健康栈跳过 up）。"""
    env_file = tmp_path / "lan.env"
    env_file.write_text(_lan_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_lan_live_env(), livekit_port=f"{LAN_IP}:7880")
    voice = FakeVoice(_healthy_voice())
    code, log = _run(runner, voice, dry_run=False, env_file=env_file)
    assert code == pr.EXIT_OK
    assert any("结果: OK" in line for line in log.lines)
    joined = "\n".join(log.lines)
    assert LAN_IP not in joined  # 全绿路径同样不回显拓扑值
    _assert_no_marker_leak(log)


# --------------------------- M14-39：健康栈跳过 up + up 前本地镜像预检
# 动机：生产彩排栈六容器全 healthy 而本机缺 aios/minio 自建镜像（M14-13
# 起的本地构建锚点，registry 不可拉取）——旧语义在 pin 9/9 后无条件
# compose up -d --no-build，计划重建 api/livekit/minio 并因缺镜像 rc=1，
# 把「栈已健康」误报为恢复失败。固化恢复边界：startup recovery 恢复健康，
# 不做部署/config drift 收敛（拓扑变更由 M14-38 cutover 工具显式执行）。

def test_local_build_image_refs_pin_compose_anchor() -> None:
    """预检常量与 infra/docker-compose.yml 的 image: 锚点同步（升版漏改
    即失败——单一事实源交叉锁定）；预检集至少覆盖 minio 自建镜像。"""
    compose = (REPO_ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
    assert MINIO_IMAGE in pr.LOCAL_BUILD_IMAGE_REFS
    for ref in pr.LOCAL_BUILD_IMAGE_REFS:
        assert f"image: {ref}" in compose, f"预检镜像 {ref} 不再是 compose 锚点——两处需同步"


def test_snapshot_health_issues_only_readonly_compose_ps() -> None:
    """健康快照只读：唯一命令形态 = docker compose … ps --format json
    （M14-39 把快照提前到 up 决策之前，只读纪律不变——无 up/stop/restart）。"""
    runner = FakeRunner(live_env=None)
    health = pr.snapshot_health(runner, Path("c.yml"), "proj", "local")
    assert set(health) == set(STACK_SERVICES)
    ps_calls = [argv for argv in runner.calls if "ps" in argv]
    assert len(ps_calls) == 1 and len(runner.calls) == 1
    argv = ps_calls[0]
    assert argv[:2] == ("docker", "compose")
    assert argv[argv.index("ps") + 1:argv.index("ps") + 3] == ("--format", "json")


def test_stack_health_gaps_pure_judgement() -> None:
    """缺口判定纯函数（与 wait_stack_healthy 同口径）：缺失与未达分类、
    running 也算达标。"""
    health = {"api": "healthy", "web": "running", "minio": "starting"}
    missing, unhealthy = pr.stack_health_gaps(health, set(STACK_SERVICES))
    assert missing == {"postgres", "redis", "livekit"}
    assert unhealthy == {"minio"}  # api healthy / web running 均达标


def test_check_local_build_images_readonly_and_fail_vocabulary() -> None:
    """预检只读：命令恒为 docker image inspect（零 pull/build/stop）；
    缺失输出 minio-local-image-missing 固定词汇；在场则静默通过。"""
    missing_runner = FakeRunner(live_env=None,
                                missing_local_images=frozenset({MINIO_IMAGE}))
    log = pr.RunLog(echo=False)
    assert pr.check_local_build_images(missing_runner, log) == (MINIO_IMAGE,)
    assert missing_runner.calls, "预检必须真实探测（不得凭空假定在场）"
    for argv in missing_runner.calls:
        assert argv[:3] == ("docker", "image", "inspect")
        assert "pull" not in argv and "build" not in argv
    joined = "\n".join(log.lines)
    assert "minio-local-image-missing" in joined and MINIO_IMAGE in joined
    present_runner = FakeRunner(live_env=None)
    present_log = pr.RunLog(echo=False)
    assert pr.check_local_build_images(present_runner, present_log) == ()
    assert present_log.lines == []  # 在场零噪声


@pytest.mark.parametrize("dry_run", [True, False])
def test_e2e_unhealthy_stack_keeps_unique_up_semantics(tmp_path: Path, dry_run: bool) -> None:
    """栈非全健康（api starting）→ 保留原唯一 up 语义：恰一次
    up -d --no-build（dry-run 附加 --dry-run）——不加 --no-deps、不加
    --no-recreate、不隐藏漂移；up 后恢复健康（enforce 健康门过）→ OK。"""
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(
        live_env=_matching_live_env(),
        ps_health={**{name: "healthy" for name in STACK_SERVICES}, "api": "starting"},
        post_up_ps_health={name: "healthy" for name in STACK_SERVICES})
    voice = FakeVoice(_healthy_voice())
    code, log = _run(runner, voice, dry_run=dry_run, env_file=env_file)
    assert code == pr.EXIT_OK
    ups = _up_calls(runner)
    assert len(ups) == 1
    expected_tail = ("up", "-d", "--no-build", "--dry-run") if dry_run else ("up", "-d", "--no-build")
    assert ups[0][ups[0].index("up"):] == expected_tail
    assert "--no-deps" not in ups[0] and "--no-recreate" not in ups[0]
    assert "--build" not in ups[0]
    assert not any("健康栈无需 up" in line for line in log.lines)
    _assert_no_marker_leak(log)


@pytest.mark.parametrize("dry_run", [True, False])
def test_e2e_minio_image_missing_unhealthy_fail_closed(tmp_path: Path, dry_run: bool) -> None:
    """栈非全健康 + aios/minio 自建镜像本机缺失 → up 之前 fail-closed：
    输出 minio-local-image-missing、零 up 构造（绝不 pull/build——源码
    契约锁定）；dry-run 退出码镜像 enforce 拒绝。镜像引用为 repo 公开
    compose 锚点（非 env secret），可见；env secret 标记仍零泄漏。"""
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(
        live_env=_matching_live_env(),
        ps_health={**{name: "healthy" for name in STACK_SERVICES}, "minio": "unhealthy"},
        missing_local_images=frozenset({MINIO_IMAGE}))
    voice = FakeVoice(_healthy_voice())
    code, log = _run(runner, voice, dry_run=dry_run, env_file=env_file)
    assert code == pr.EXIT_ERROR
    assert _up_calls(runner) == []  # up 之前拒绝——零容器改动
    joined = "\n".join(log.lines)
    assert "minio-local-image-missing" in joined
    assert MINIO_IMAGE in joined
    _assert_no_marker_leak(log)


def test_e2e_healthy_stack_minio_image_missing_still_ok(tmp_path: Path) -> None:
    """栈全健康 + 自建镜像缺失 → 跳过 up 即不受预检阻断（预检只在 up
    路径上执行）：恢复结果不因「本机未构建 minio 镜像」被健康栈误伤。"""
    env_file = tmp_path / "pin.env"
    env_file.write_text(_matching_env_text(), encoding="utf-8")
    runner = FakeRunner(live_env=_matching_live_env(),
                        missing_local_images=frozenset({MINIO_IMAGE}))
    voice = FakeVoice(_healthy_voice())
    code, log = _run(runner, voice, dry_run=False, env_file=env_file)
    assert code == pr.EXIT_OK
    assert _up_calls(runner) == []
    joined = "\n".join(log.lines)
    assert "minio-local-image-missing" not in joined
    assert any("结果: OK" in line for line in log.lines)
    _assert_no_marker_leak(log)


def test_e2e_m14_38_surface_no_regression(tmp_path: Path) -> None:
    """M14-38 复用面不回归：九键 PIN_KEYS 恒 9、拓扑键子集不变；恢复
    编排零 up 构造时（健康栈）cutover 依赖的复用函数签名面原样可用。"""
    assert pr.PIN_KEYS == (
        "AIOS_IMAGE_TAG", "AIOS_WEB_IMAGE_TAG", "AIOS_APP_ENV", "AIOS_WEB_PORT",
        "AIOS_AUTH_SECRET", "AIOS_LIVEKIT_API_SECRET",
        "AIOS_BIND_IP", "AIOS_LIVEKIT_BIND_IP", "AIOS_PUBLIC_LIVEKIT_URL")
    assert set(pr.TOPOLOGY_PIN_KEYS) <= set(pr.PIN_KEYS)
    # cutover 复用的导出面在新语义下仍在（签名未变）
    for name in ("check_pins", "collect_live_pins", "placeholder_pin_keys",
                 "validate_compose", "wait_docker_engine", "wait_stack_healthy",
                 "snapshot_health"):
        assert callable(getattr(pr, name))
