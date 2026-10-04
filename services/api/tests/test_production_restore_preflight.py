r"""M14-231 tools/ops/production_restore_preflight.py 契约测试：只读分类与守卫式 --apply，零真实改动。

覆盖（全部离线：FakeRunner 回放 Docker/compose/recovery 子进程面，FakeProbe
回放 TCP/HTTP 探测——不启动/不停止任何容器、不碰真实 env、零网络）：
- env pin 形状分类：缺失/缺键/模板占位/开发默认 secret 值（键名-only，
  任何值绝不进入日志/报告）；
- compose 分类：config 无效（stderr 摘要脱敏）、渲染服务集漂移；
- 镜像分类：env tag 派生自建锚点（api/web）+ minio 自建锚点缺失 =
  local-image-missing（阻塞）；registry 镜像缺失仅提示（不阻塞）；
- 容器五分类：stack-absent / stack-stopped / stack-partial /
  stack-degraded / stack-healthy；
- 监听交叉分类：未运行而端口被占 = port-conflict；运行中而端口不通 =
  listener-missing（web 动作指向 production_web_gateway）；公网边缘恒
  uncertain（不阻塞、不探测）；
- verdict/exit：blocked→1，restore-required/healthy→0；
- secret 抑制：marker secret 值（env 值 + compose stderr 回显注入）绝不出现在
  任何日志行、stdout、JSON 报告；报告零本机绝对路径；
- --apply 守卫链：短语缺失/不匹配 = confirm-gate 零动作；blocked/healthy =
  拒绝零动作；restore-required = 纯委托既有 production_recovery.py（先
  --dry-run 门，非零即中止，enforce 不发起）；
- 源码契约：绝不出现 stop/rm/kill/down/restart/reset/pull/build 子命令字面量；
  恢复动作只经 RECOVERY_SCRIPT 委托；常量与 production_recovery /
  docker-compose.yml 交叉锁定。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "production_restore_preflight.py"
RECOVERY_SCRIPT = REPO_ROOT / "tools" / "ops" / "production_recovery.py"
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"
SECRET_PATTERNS = ("sk-", "AKIA", "ghp_", "xoxb_", "-----BEGIN")

PROJECT = "aios-m14-03-production-rehearsal"
STACK_SERVICES = ("api", "livekit", "minio", "postgres", "redis", "web")
SERVICE_REFS = (
    "aios/minio:RELEASE.2025-10-15T17-29-55Z",
    "aios/api:m14-211-production",
    "aios/web:m14-193-production",
)
MARK_AUTH = "ZX-markerauth-0123456789abcdef"
MARK_LIVEKIT = "ZX-markerlivekit-0123456789abcdef"
API_PORT = 8000
WEB_PORT = 3011


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


pr = _load_module(SCRIPT, "production_restore_preflight_under_test")


# ---------------------------------------------------------------- fakes

class FakeRunner:
    """伪命令面：按 argv 形态回放（docker/compose/recovery 子进程），记录全部调用。"""

    def __init__(self, *, engine_ready: bool = True, compose_ok: bool = True,
                 services: tuple[str, ...] = STACK_SERVICES,
                 compose_stderr: str = "",
                 containers: dict[str, tuple[str, str]] | None = None,
                 missing_images: frozenset[str] = frozenset(),
                 recovery_dry_rc: int = 0, recovery_enforce_rc: int = 0,
                 recovery_out: str = "[recovery] === 结果: OK ===\n") -> None:
        self.engine_ready = engine_ready
        self.compose_ok = compose_ok
        self.services = services
        self.compose_stderr = compose_stderr
        # service -> (State, Status)
        self.containers = {} if containers is None else dict(containers)
        self.missing_images = frozenset(missing_images)
        self.recovery_dry_rc = recovery_dry_rc
        self.recovery_enforce_rc = recovery_enforce_rc
        self.recovery_out = recovery_out
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, timeout: float = 60.0, encoding: str | None = None):
        argv = tuple(str(item) for item in argv)
        self.calls.append(argv)
        if argv[:2] == ("docker", "version"):
            if self.engine_ready:
                return pr.recovery.CommandResult(argv, 0, "27.5.1\n", "")
            return pr.recovery.CommandResult(argv, 1, "", "Cannot connect to the Docker daemon")
        if "compose" in argv and "config" in argv:
            if not self.compose_ok:
                return pr.recovery.CommandResult(argv, 1, "", self.compose_stderr)
            if "--services" in argv:
                return pr.recovery.CommandResult(
                    argv, 0, "\n".join(sorted(self.services)) + "\n", "")
            return pr.recovery.CommandResult(argv, 0, "", "")
        if argv[:2] == ("docker", "ps"):
            rows = "".join(
                json.dumps({"Names": f"{PROJECT}-{svc}-1", "State": state, "Status": status}) + "\n"
                for svc, (state, status) in sorted(self.containers.items())
            )
            return pr.recovery.CommandResult(argv, 0, rows, "")
        if argv[:3] == ("docker", "image", "inspect"):
            ref = str(argv[3])
            if ref in self.missing_images:
                return pr.recovery.CommandResult(argv, 1, "", "Error: No such image")
            return pr.recovery.CommandResult(argv, 0, "sha256:0000…\n", "")
        if str(argv[0]).endswith("python") or str(argv[0]) == sys.executable:
            is_dry = "--dry-run" in argv
            rc = self.recovery_dry_rc if is_dry else self.recovery_enforce_rc
            return pr.recovery.CommandResult(argv, rc, self.recovery_out, "")
        return pr.recovery.CommandResult(argv, 0, "", "")

    def recovery_calls(self) -> list[tuple[str, ...]]:
        return [c for c in self.calls if str(c[0]) == sys.executable]


class FakeProbe:
    """伪监听探测：预制开放端口与 /health 状态码（零网络）。"""

    def __init__(self, *, open_ports: frozenset[int] = frozenset(), health_status: int | None = None) -> None:
        self.open_ports = frozenset(open_ports)
        self.health_status = health_status

    def tcp_open(self, host: str, port: int) -> bool:
        return port in self.open_ports

    def http_get_status(self, host: str, port: int, path: str) -> int | None:
        return self.health_status if port in self.open_ports else None


def _env_text() -> str:
    return (
        "AIOS_IMAGE_TAG=m14-211-production\n"
        "AIOS_WEB_IMAGE_TAG=m14-193-production\n"
        "AIOS_APP_ENV=production\n"
        f"AIOS_WEB_PORT={WEB_PORT}\n"
        f"AIOS_AUTH_SECRET={MARK_AUTH}\n"
        f"AIOS_LIVEKIT_API_SECRET={MARK_LIVEKIT}\n"
        "AIOS_BIND_IP=127.0.0.1\n"
        "AIOS_LIVEKIT_BIND_IP=127.0.0.1\n"
        "AIOS_PUBLIC_LIVEKIT_URL=ws://127.0.0.1:7880\n"
    )


def _healthy_containers() -> dict[str, tuple[str, str]]:
    return {svc: ("running", "Up 3 hours (healthy)") for svc in STACK_SERVICES}


def _assess(runner: FakeRunner, probe: FakeProbe, env_file: Path, log: pr.PreflightLog | None = None):
    log = log or pr.PreflightLog(echo=False)
    report = pr.run_preflight(runner=runner, probe=probe, log=log, env_file=env_file)
    return report, log


# ---------------------------------------------------------------- env pin 形状

def test_env_ok_shape(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    shape, values = pr.evaluate_env_shape(env)
    assert shape.present and not shape.missing_keys and not shape.placeholder_keys
    assert not shape.dev_default_keys
    assert values["AIOS_WEB_PORT"] == str(WEB_PORT)  # 值只留内存（断言侧）


def test_env_missing_file_blocks(tmp_path: Path) -> None:
    report, _log = _assess(FakeRunner(), FakeProbe(), tmp_path / "absent.env")
    assert report["verdict"] == pr.VERDICT_BLOCKED
    assert pr.BLOCK_ENV_MISSING in report["blockers"]
    assert any("env.production-recovery.example" in a for a in report["actions"])


def test_env_missing_keys_reported_by_name_only(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(
        "AIOS_IMAGE_TAG=m14-211-production\nAIOS_APP_ENV=production\n", encoding="utf-8")
    shape, _ = pr.evaluate_env_shape(env)
    assert set(shape.missing_keys) == {
        "AIOS_WEB_IMAGE_TAG", "AIOS_WEB_PORT", "AIOS_AUTH_SECRET",
        "AIOS_LIVEKIT_API_SECRET", "AIOS_BIND_IP", "AIOS_LIVEKIT_BIND_IP",
        "AIOS_PUBLIC_LIVEKIT_URL",
    }
    report, _ = _assess(FakeRunner(), FakeProbe(), env)
    assert pr.BLOCK_ENV_MISSING_KEYS in report["blockers"]
    assert "AIOS_AUTH_SECRET" in report["env"]["missing_keys"]  # 键名可见
    assert MARK_AUTH not in json.dumps(report, ensure_ascii=False)  # 值不可见


def test_env_placeholder_keys_fail_closed(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(
        _env_text().replace(MARK_AUTH, "<部署时生成的真实值——绝不提交>"), encoding="utf-8")
    report, log = _assess(FakeRunner(), FakeProbe(), env)
    assert pr.BLOCK_ENV_PLACEHOLDER_KEYS in report["blockers"]
    assert report["env"]["placeholder_keys"] == ["AIOS_AUTH_SECRET"]  # 键名可见
    joined = "\n".join(log.lines)
    assert "占位" in joined
    assert "<部署时生成的真实值" not in joined  # 占位文本不回显


def test_env_dev_default_secret_values_rejected(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(
        _env_text()
        .replace(MARK_AUTH, "aios-local-dev-secret-7d21b9e4c8a3")
        .replace(MARK_LIVEKIT, "ailos-local-dev-secret-0f4c9a1e7b2d"),
        encoding="utf-8")
    shape, _ = pr.evaluate_env_shape(env)
    assert shape.dev_default_keys == ("AIOS_AUTH_SECRET", "AIOS_LIVEKIT_API_SECRET")
    report, _ = _assess(FakeRunner(), FakeProbe(), env)
    assert pr.BLOCK_ENV_DEV_DEFAULT_KEYS in report["blockers"]


# ---------------------------------------------------------------- compose 分类

def test_compose_config_invalid_blocks_with_redacted_stderr(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    runner = FakeRunner(compose_ok=False,
                        compose_stderr=f"error interpolating: {MARK_AUTH} invalid")
    report, log = _assess(runner, FakeProbe(), env)
    assert pr.BLOCK_COMPOSE_CONFIG_INVALID in report["blockers"]
    joined = "\n".join(log.lines) + json.dumps(report, ensure_ascii=False)
    assert MARK_AUTH not in joined
    assert "error interpolating" in report["compose"]["stderr_tail"]


def test_compose_services_drift_blocks(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    runner = FakeRunner(services=STACK_SERVICES[:5])  # 少一个服务
    report, _ = _assess(runner, FakeProbe(), env)
    assert pr.BLOCK_COMPOSE_SERVICES_DRIFT in report["blockers"]
    assert report["compose"]["drift"] == ["web"]


def test_docker_unavailable_blocks_without_compose_calls(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    runner = FakeRunner(engine_ready=False)
    report, _ = _assess(runner, FakeProbe(), env)
    assert pr.BLOCK_DOCKER_UNAVAILABLE in report["blockers"]
    assert report["verdict"] == pr.VERDICT_BLOCKED
    assert not any("compose" in " ".join(c) for c in runner.calls)


# ---------------------------------------------------------------- 镜像分类

def test_local_image_missing_blocks(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    runner = FakeRunner(missing_images=frozenset(SERVICE_REFS[:2]))
    report, _ = _assess(runner, FakeProbe(), env)
    assert pr.BLOCK_LOCAL_IMAGE_MISSING in report["blockers"]
    assert report["images"]["local_missing"] == [
        "aios/minio:RELEASE.2025-10-15T17-29-55Z", "aios/api:m14-211-production"]


def test_registry_image_missing_is_note_not_blocker(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    runner = FakeRunner(missing_images=frozenset(pr.REGISTRY_IMAGE_ANCHORS))
    report, _ = _assess(runner, FakeProbe(), env)
    assert pr.BLOCK_LOCAL_IMAGE_MISSING not in report["blockers"]
    assert pr.NOTE_REGISTRY_PULL_REQUIRED in report["notes"]
    assert set(report["images"]["registry_missing"]) == set(pr.REGISTRY_IMAGE_ANCHORS)


# ---------------------------------------------------------------- 容器五分类

def test_stack_absent_gives_restore_required(tmp_path: Path) -> None:
    """真实故障形态：容器完全不在场 + env/compose/镜像/端口全部就绪 → 可恢复。"""
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    report, _log = _assess(FakeRunner(), FakeProbe(open_ports=frozenset()), env)
    assert report["containers"]["classification"] == pr.STACK_ABSENT
    assert report["blockers"] == []
    assert report["verdict"] == pr.VERDICT_RESTORE_REQUIRED
    assert any("production_recovery.py --dry-run" in a for a in report["actions"])


def test_stack_stopped_classified(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    stopped = {svc: ("exited", "Exited (0) 2 hours ago") for svc in STACK_SERVICES}
    report, _ = _assess(FakeRunner(containers=stopped), FakeProbe(), env)
    assert report["containers"]["classification"] == pr.STACK_STOPPED
    assert report["verdict"] == pr.VERDICT_RESTORE_REQUIRED


def test_stack_partial_classified(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    partial = {svc: ("running", "Up (healthy)") for svc in STACK_SERVICES[:3]}
    probe = FakeProbe(open_ports=frozenset({API_PORT, WEB_PORT}))  # 3011 被无关进程占用
    report, _ = _assess(FakeRunner(containers=partial), probe, env)
    assert report["containers"]["classification"] == pr.STACK_PARTIAL
    # web 容器不在场而 3011 被占 → port-conflict:web
    assert "port-conflict:web" in report["blockers"]


def test_stack_degraded_classified(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    containers = _healthy_containers()
    containers["redis"] = ("running", "Up 3 hours (unhealthy)")
    report, _ = _assess(FakeRunner(containers=containers),
                        FakeProbe(open_ports=frozenset({API_PORT, WEB_PORT})), env)
    assert report["containers"]["classification"] == pr.STACK_DEGRADED
    assert report["verdict"] == pr.VERDICT_RESTORE_REQUIRED


def test_stack_healthy_with_listeners_gives_healthy(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    probe = FakeProbe(open_ports=frozenset({API_PORT, WEB_PORT}), health_status=200)
    report, _log = _assess(FakeRunner(containers=_healthy_containers()), probe, env)
    assert report["containers"]["classification"] == pr.STACK_HEALTHY
    assert report["verdict"] == pr.VERDICT_HEALTHY
    assert report["blockers"] == []
    assert report["listeners"]["api"]["health_status"] == 200


def test_stack_running_without_listener_blocks(tmp_path: Path) -> None:
    """M14-157 已知形态：容器 running+healthy 但 host 端口 stale 不通。"""
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    probe = FakeProbe(open_ports=frozenset({API_PORT}), health_status=200)  # web 不通
    report, _ = _assess(FakeRunner(containers=_healthy_containers()), probe, env)
    assert "listener-missing:web" in report["blockers"]
    assert report["verdict"] == pr.VERDICT_BLOCKED
    assert any("production_web_gateway" in a for a in report["actions"])


def test_stack_absent_with_foreign_listener_is_port_conflict(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    probe = FakeProbe(open_ports=frozenset({API_PORT}))  # 栈不在场但 8000 被占
    report, _ = _assess(FakeRunner(), probe, env)
    assert "port-conflict:api" in report["blockers"]
    assert report["verdict"] == pr.VERDICT_BLOCKED


# ---------------------------------------------------------------- 公网边缘

def test_public_edge_always_uncertain_and_never_blocks(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    report, _ = _assess(FakeRunner(containers=_healthy_containers()),
                        FakeProbe(open_ports=frozenset({API_PORT, WEB_PORT}), health_status=200), env)
    assert report["public_edge"]["status"] == pr.NOTE_PUBLIC_EDGE_UNCERTAIN
    assert report["public_edge"]["status"] not in report["blockers"]
    assert any("public_edge_preflight" in a for a in report["actions"])
    assert any("frpc" in a for a in report["actions"])


# ---------------------------------------------------------------- secret 抑制

def test_secrets_never_leak_to_logs_or_report(tmp_path, capsys) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    runner = FakeRunner(compose_ok=False, compose_stderr=f"boom {MARK_LIVEKIT}")
    log = pr.PreflightLog(echo=True)  # echo=True 才走 stdout
    report = pr.run_preflight(runner=runner, probe=FakeProbe(), log=log, env_file=env)
    blob = "\n".join(log.lines) + json.dumps(report, ensure_ascii=False) + capsys.readouterr().out
    assert MARK_AUTH not in blob
    assert MARK_LIVEKIT not in blob
    # run_preflight 自带 env secret 脱敏（不依赖调用方配置 redactions）
    assert "REDACTED:AIOS_LIVEKIT_API_SECRET" in report["compose"]["stderr_tail"]


def test_report_contains_no_absolute_paths(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    report, _ = _assess(FakeRunner(), FakeProbe(), env)
    blob = json.dumps(report, ensure_ascii=False)
    for needle in ("D:\\", "D:/", "/d/AI", str(tmp_path), str(REPO_ROOT)):
        assert needle not in blob


# ---------------------------------------------------------------- --apply 守卫

def _apply_args(env_file: Path, phrase: str | None) -> list[str]:
    argv = ["--apply", "--env-file", str(env_file)]
    if phrase is not None:
        argv += ["--confirm-phrase", phrase]
    return argv


def test_apply_confirm_gate_refuses_without_phrase(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    for phrase in (None, "wrong phrase"):
        runner = FakeRunner(containers=_healthy_containers())  # healthy：无短语必拒
        code = pr.main(_apply_args(env, phrase) if phrase else ["--apply", "--env-file", str(env)],
                       runner=runner, probe=FakeProbe(open_ports=frozenset({API_PORT, WEB_PORT})))
        assert code == pr.EXIT_BLOCKED
        assert runner.recovery_calls() == []


def test_apply_refuses_when_preflight_blocked(tmp_path: Path) -> None:
    runner = FakeRunner()  # stack-absent + 镜像全缺 → blocked
    code = pr.main(["--apply", "--confirm-phrase", pr.CONFIRM_PHRASE,
                    "--env-file", str(tmp_path / "absent.env")],
                   runner=runner, probe=FakeProbe())
    assert code == pr.EXIT_BLOCKED
    assert runner.recovery_calls() == []


def test_apply_refuses_when_healthy(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    runner = FakeRunner(containers=_healthy_containers())
    code = pr.main(_apply_args(env, pr.CONFIRM_PHRASE), runner=runner,
                   probe=FakeProbe(open_ports=frozenset({API_PORT, WEB_PORT}), health_status=200))
    assert code == pr.EXIT_OK  # 无需恢复 = 成功的零动作
    assert runner.recovery_calls() == []


def test_apply_happy_path_delegates_documented_recovery(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    runner = FakeRunner()  # stack-absent，其余就绪 → restore-required
    code = pr.main(_apply_args(env, pr.CONFIRM_PHRASE), runner=runner, probe=FakeProbe())
    assert code == pr.EXIT_OK
    calls = runner.recovery_calls()
    assert len(calls) == 2  # 先 dry-run 门，后 enforce
    first, second = calls
    assert "--dry-run" in first and "--dry-run" not in second
    for call in calls:
        assert str(pr.RECOVERY_SCRIPT) in call
        assert str(env) in call  # --env-file 前置事实逐字转发
        assert PROJECT in call and "local" in call


def test_apply_aborts_when_dry_run_gate_fails(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    runner = FakeRunner(recovery_dry_rc=1, recovery_out="[recovery] pin: 拒绝\n")
    code = pr.main(_apply_args(env, pr.CONFIRM_PHRASE), runner=runner, probe=FakeProbe())
    assert code == pr.EXIT_BLOCKED
    calls = runner.recovery_calls()
    assert len(calls) == 1 and "--dry-run" in calls[0]  # enforce 绝不发起


def test_apply_propagates_enforce_failure(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    runner = FakeRunner(recovery_enforce_rc=1)
    code = pr.main(_apply_args(env, pr.CONFIRM_PHRASE), runner=runner, probe=FakeProbe())
    assert code == pr.EXIT_BLOCKED
    assert len(runner.recovery_calls()) == 2


# ---------------------------------------------------------------- CLI / 源码契约

def test_parser_defaults() -> None:
    args = pr.build_parser().parse_args([])
    assert args.project == pr.DEFAULT_PROJECT == "aios-m14-03-production-rehearsal"
    assert args.profile == "local"
    assert args.env_file == pr.DEFAULT_ENV_FILE == pr.recovery.DEFAULT_ENV_FILE
    assert not args.apply and args.confirm_phrase == ""


def test_read_only_exit_codes(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    ok = pr.main(["--env-file", str(env)], runner=FakeRunner(), probe=FakeProbe())
    assert ok == pr.EXIT_OK  # restore-required → 0
    blocked = pr.main(["--env-file", str(tmp_path / "absent.env")],
                      runner=FakeRunner(), probe=FakeProbe())
    assert blocked == pr.EXIT_BLOCKED


def test_source_never_issues_destructive_subcommands() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for literal in ('"down"', '"stop"', '"kill"', '"rm"', '"restart"', '"reset"',
                    '"pull"', '"build"'):
        assert literal not in source, f"禁止出现的子命令字面量: {literal}"
    assert str(pr.RECOVERY_SCRIPT).endswith("production_recovery.py")
    assert "sys.executable" in source  # 恢复动作只经子进程委托
    assert "frpc" in source  # 边界声明在场（绝不重启 frpc）
    for pattern in SECRET_PATTERNS:
        assert pattern not in source


def test_constants_cross_locked_with_recovery_and_compose() -> None:
    recovery = _load_module(RECOVERY_SCRIPT, "production_recovery_for_xlock")
    assert set(pr.PIN_KEYS) == set(recovery.PIN_KEYS)
    assert set(pr.EXPECTED_SERVICES) == set(recovery.EXPECTED_STACK_SERVICES)
    assert pr.DEFAULT_PROJECT == recovery.DEFAULT_PROJECT
    assert pr.DEFAULT_PROFILE == recovery.DEFAULT_PROFILE
    assert set(pr.LOCAL_BUILD_IMAGE_REFS) == set(recovery.LOCAL_BUILD_IMAGE_REFS)
    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    for ref in pr.REGISTRY_IMAGE_ANCHORS:
        assert ref in compose, f"registry 锚点未在 compose 中: {ref}"
    for literal in pr.DEV_DEFAULT_SECRET_VALUES:
        assert literal in compose  # 开发默认回落值确为 compose 公开字面量
    for dev in ("aios-local-dev-secret-7d21b9e4c8a3", "ailos-local-dev-secret-0f4c9a1e7b2d"):
        assert dev in pr.DEV_DEFAULT_SECRET_VALUES


def test_service_name_derivation() -> None:
    assert pr._service_from_container_name(f"{PROJECT}-web-1", PROJECT) == "web"
    assert pr._service_from_container_name(f"{PROJECT}-api-2", PROJECT) == "api"
    assert pr._service_from_container_name("other-web-1", PROJECT) == ""
