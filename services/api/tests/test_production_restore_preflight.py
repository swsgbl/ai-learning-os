r"""M14-231 tools/ops/production_restore_preflight.py 契约测试：只读分类与守卫式 --apply，零真实改动。

覆盖（全部离线：FakeRunner 回放 Docker/compose/recovery 子进程面，FakeProbe
回放 TCP/HTTP 探测——不启动/不停止任何容器、不碰真实 env、零网络）：
- env pin 形状分类：缺失/缺键/模板占位/开发默认 secret 值（键名-only，
  任何值绝不进入日志/报告）；
- compose 分类：config 无效（stderr 摘要脱敏）、渲染服务集漂移；
- 镜像分类：env tag 派生自建锚点（api/web）+ minio 自建锚点缺失 =
  local-image-missing（阻塞）；registry 镜像缺失仅提示（不阻塞）；
- 持久数据卷分类（Codex 评审修正）：postgres-data/minio-data 缺失 =
  persistent-volume-missing:<key> 阻塞且抑制恢复建议（up 会静默建空卷）；
  searxng-cache 缺失仅提示；docker volume ls / compose --volumes 查询失败
  fail-closed（volume-query-failed，不下任何卷结论）；compose 声明未分类
  卷 fail-closed；命名约定 <project>_<key> 与 M14-41/M14-46 证据交叉锁定；
  源码契约：唯一卷子命令 ls，绝不 rm/prune/create；
- 容器五分类：stack-absent / stack-stopped / stack-partial /
  stack-degraded / stack-healthy；**可选 profile 服务轮（M14-234）**：
  五分类只按必需六服务锚点判定——searxng（compose profiles: ["search"]，
  源码契约与 docker-compose.yml 交叉锁定）在场无论健康/停止/不健康都
  不把必需栈翻成 partial/restore-required（不健康仅提示
  optional-service-not-healthy:searxng，单独上报于 containers.
  optional_services）；未知多余服务仍 fail-closed 归 stack-partial；
  必需服务缺失/不健康仍照旧 fail-closed；
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
                 declared_volumes: tuple[str, ...] | None = ("postgres-data", "minio-data"),
                 existing_volumes: tuple[str, ...] | None = None,
                 volume_ls_rc: int = 0,
                 containers: dict[str, tuple[str, str]] | None = None,
                 missing_images: frozenset[str] = frozenset(),
                 recovery_dry_rc: int = 0, recovery_enforce_rc: int = 0,
                 recovery_out: str = "[recovery] === 结果: OK ===\n") -> None:
        self.engine_ready = engine_ready
        self.compose_ok = compose_ok
        self.services = services
        self.compose_stderr = compose_stderr
        self.declared_volumes = declared_volumes
        # 默认：三个生产卷全在（默认路径 = 卷面无阻塞）
        self.existing_volumes = (
            tuple(f"{PROJECT}_{key}" for key in ("postgres-data", "minio-data", "searxng-cache"))
            if existing_volumes is None else tuple(existing_volumes)
        )
        self.volume_ls_rc = volume_ls_rc
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
        if argv[:2] == ("docker", "volume"):
            if self.volume_ls_rc != 0:
                return pr.recovery.CommandResult(argv, self.volume_ls_rc, "", "daemon error")
            return pr.recovery.CommandResult(
                argv, 0, "\n".join(self.existing_volumes) + ("\n" if self.existing_volumes else ""), "")
        if "compose" in argv and "config" in argv:
            if not self.compose_ok:
                return pr.recovery.CommandResult(argv, 1, "", self.compose_stderr)
            if "--services" in argv:
                return pr.recovery.CommandResult(
                    argv, 0, "\n".join(sorted(self.services)) + "\n", "")
            if "--volumes" in argv:
                if self.declared_volumes is None:
                    return pr.recovery.CommandResult(argv, 1, "", "render failed")
                return pr.recovery.CommandResult(
                    argv, 0, "\n".join(sorted(self.declared_volumes)) + "\n", "")
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


# ---------------------------------------------------------------- 持久数据卷

def test_persistent_volume_absent_blocks_and_kills_recovery_hint(tmp_path: Path) -> None:
    """Codex 评审缺口：镜像重建后若卷仍缺，up 会静默建空生产数据卷——必须阻塞。"""
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    runner = FakeRunner(existing_volumes=())  # 三卷全缺（真实机器形态）
    report, _log = _assess(runner, FakeProbe(), env)
    assert "persistent-volume-missing:postgres-data" in report["blockers"]
    assert "persistent-volume-missing:minio-data" in report["blockers"]
    assert report["verdict"] == pr.VERDICT_BLOCKED
    joined = " ".join(report["actions"])
    assert "不要执行 compose up" in joined and "备份" in joined  # 绝不建议静默建空卷
    assert not any("production_recovery.py --dry-run" in a for a in report["actions"])


def test_cache_volume_absent_is_note_not_blocker(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    runner = FakeRunner(existing_volumes=(
        f"{PROJECT}_postgres-data", f"{PROJECT}_minio-data"))  # 仅缓存卷缺
    report, _ = _assess(runner, FakeProbe(), env)
    assert not any(b.startswith(pr.BLOCK_PERSISTENT_VOLUME_MISSING) for b in report["blockers"])
    assert pr.NOTE_CACHE_VOLUME_MISSING in report["notes"]
    assert report["volumes"]["cache_missing"] == ["searxng-cache"]


def test_volume_query_failure_fail_closed(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    runner = FakeRunner(volume_ls_rc=1)
    report, _ = _assess(runner, FakeProbe(), env)
    assert pr.BLOCK_VOLUME_QUERY_FAILED in report["blockers"]
    assert report["volumes"]["query_ok"] is False
    # 查询失败 ≠ 卷在场：不得把「查不出」伪装成缺失以外的任何结论
    assert report["volumes"]["persistent_missing"] == []
    assert report["verdict"] == pr.VERDICT_BLOCKED


def test_declared_volume_render_failure_fail_closed(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    runner = FakeRunner(declared_volumes=None)  # compose config --volumes 失败
    report, _ = _assess(runner, FakeProbe(), env)
    assert pr.BLOCK_VOLUME_QUERY_FAILED in report["blockers"]
    assert report["volumes"]["declared"] is None


def test_existing_volumes_allow_restore_required(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    runner = FakeRunner()  # 默认三卷全在
    report, _ = _assess(runner, FakeProbe(), env)
    assert report["volumes"]["persistent_missing"] == []
    assert report["volumes"]["cache_missing"] == []
    assert report["blockers"] == []
    assert report["verdict"] == pr.VERDICT_RESTORE_REQUIRED


def test_unclassified_declared_volume_fail_closed(tmp_path: Path) -> None:
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    runner = FakeRunner(declared_volumes=("postgres-data", "minio-data", "new-data"))
    report, _ = _assess(runner, FakeProbe(), env)
    assert "volume-classification-unknown:new-data" in report["blockers"]


def test_persistent_volume_undeclared_in_profile_fail_closed(tmp_path: Path) -> None:
    """local profile 渲染必须声明两个持久卷——未声明即拓扑漂移（unknown）。"""
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    runner = FakeRunner(declared_volumes=("postgres-data",))  # minio-data 未声明
    report, _ = _assess(runner, FakeProbe(), env)
    assert "volume-classification-unknown:minio-data" in report["blockers"]


def test_cache_volume_undeclared_in_local_profile_is_expected(tmp_path: Path) -> None:
    """searxng-cache 属 --profile search：local 渲染不声明是预期，非漂移。"""
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    runner = FakeRunner()  # declared 默认恰为两持久卷（真实 local 渲染形态）
    report, _ = _assess(runner, FakeProbe(), env)
    assert report["volumes"]["classification_unknown"] == []
    assert report["blockers"] == []  # 卷存在（默认三卷全在）→ restore-required


def test_volume_names_follow_project_underscore_convention() -> None:
    """M14-41/M14-46 实证约定：命名卷全名 = <project>_<key>（下划线）。"""
    assert pr.compose_volume_name(PROJECT, "minio-data") == \
        "aios-m14-03-production-rehearsal_minio-data"
    assert pr.compose_volume_name(PROJECT, "postgres-data") == \
        "aios-m14-03-production-rehearsal_postgres-data"


# ---------------------------------------------------------------- 容器五分类

def test_stack_absent_gives_restore_required(tmp_path: Path) -> None:
    """真实故障形态：容器完全不在场 + env/compose/镜像/卷/端口全部就绪 → 可恢复。"""
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


# ---------------------------------------------------------------- 可选 profile 服务（M14-234）

def _seven_container_states(searxng: tuple[str, str]) -> dict[str, tuple[str, str]]:
    """线上真实形态：必需六服务健康 + 同 project label 的可选 searxng。"""
    states = _healthy_containers()
    states["searxng"] = searxng
    return states


def test_optional_searxng_healthy_keeps_required_stack_healthy(tmp_path: Path) -> None:
    """七容器全健康（六必需 + search profile 的 searxng）= stack-healthy——
    M14-234 修复的误判形态：同 project label 的可选服务绝不把必需栈翻成
    stack-partial / restore-required。"""
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    probe = FakeProbe(open_ports=frozenset({API_PORT, WEB_PORT}), health_status=200)
    report, _ = _assess(
        FakeRunner(containers=_seven_container_states(("running", "Up 3 hours (healthy)"))),
        probe, env)
    assert report["containers"]["classification"] == pr.STACK_HEALTHY
    assert report["verdict"] == pr.VERDICT_HEALTHY
    assert report["blockers"] == []
    assert report["containers"]["optional_services"] == {"searxng": "running-healthy"}
    assert report["containers"]["unknown_services"] == []
    assert not any(note.startswith(pr.NOTE_OPTIONAL_SERVICE_NOT_HEALTHY) for note in report["notes"])


def test_optional_searxng_stopped_or_unhealthy_still_healthy_with_note(tmp_path: Path) -> None:
    """可选 searxng 停止/不健康：必需六服务健康 → verdict 仍 healthy，
    仅显式提示 optional-service-not-healthy:searxng（不阻塞、不翻 partial）。"""
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    probe = FakeProbe(open_ports=frozenset({API_PORT, WEB_PORT}), health_status=200)
    for searxng_state in (("exited", "Exited (0) 1 hour ago"),
                          ("running", "Up 3 hours (unhealthy)"),
                          ("running", "health: starting")):
        report, _ = _assess(
            FakeRunner(containers=_seven_container_states(searxng_state)), probe, env)
        assert report["containers"]["classification"] == pr.STACK_HEALTHY, searxng_state
        assert report["verdict"] == pr.VERDICT_HEALTHY, searxng_state
        assert report["blockers"] == [], searxng_state
        assert f"{pr.NOTE_OPTIONAL_SERVICE_NOT_HEALTHY}:searxng" in report["notes"], searxng_state
        assert report["containers"]["optional_services"] == {
            "searxng": pr.derive_service_state(*searxng_state)}, searxng_state


def test_unknown_extra_service_still_fail_closed_partial(tmp_path: Path) -> None:
    """未知多余服务（既非必需六服务也非可选 searxng）无豁免——照旧
    fail-closed 归 stack-partial（verdict restore-required，人工裁决）。"""
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    containers = _healthy_containers()
    containers["extra-worker"] = ("running", "Up 2 hours (healthy)")
    probe = FakeProbe(open_ports=frozenset({API_PORT, WEB_PORT}), health_status=200)
    report, _ = _assess(FakeRunner(containers=containers), probe, env)
    assert report["containers"]["classification"] == pr.STACK_PARTIAL
    assert report["verdict"] == pr.VERDICT_RESTORE_REQUIRED
    assert report["containers"]["unknown_services"] == ["extra-worker"]
    assert report["containers"]["optional_services"] == {}


def test_missing_required_service_still_partial_with_searxng_present(tmp_path: Path) -> None:
    """必需服务缺失不因可选 searxng 在场而放宽——照旧 fail-closed partial。"""
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    containers = {svc: ("running", "Up (healthy)") for svc in STACK_SERVICES[:5]}  # 缺 web
    containers["searxng"] = ("running", "Up (healthy)")
    probe = FakeProbe(open_ports=frozenset({API_PORT}), health_status=200)  # web 不在→不开不通
    report, _ = _assess(FakeRunner(containers=containers), probe, env)
    assert report["containers"]["classification"] == pr.STACK_PARTIAL
    assert report["verdict"] == pr.VERDICT_RESTORE_REQUIRED


def test_unhealthy_required_still_degraded_with_searxng_healthy(tmp_path: Path) -> None:
    """必需服务不健康不因可选 searxng 健康而放宽——照旧 stack-degraded。"""
    env = tmp_path / "pin.env"
    env.write_text(_env_text(), encoding="utf-8")
    containers = _seven_container_states(("running", "Up 3 hours (healthy)"))
    containers["redis"] = ("running", "Up 3 hours (unhealthy)")
    probe = FakeProbe(open_ports=frozenset({API_PORT, WEB_PORT}), health_status=200)
    report, _ = _assess(FakeRunner(containers=containers), probe, env)
    assert report["containers"]["classification"] == pr.STACK_DEGRADED
    assert report["verdict"] == pr.VERDICT_RESTORE_REQUIRED


def test_classify_stack_pure_optional_edges() -> None:
    """纯函数边界：仅可选服务在场 ≠ absent/healthy（partial）；必需齐而
    全停 + 可选在跑 = stopped（恢复路径可拉起必需六服务）。"""
    states = {"searxng": "running-healthy"}
    assert pr.classify_stack(states, pr.EXPECTED_SERVICES,
                             pr.OPTIONAL_PROFILE_SERVICES) == pr.STACK_PARTIAL
    stopped = {svc: "not-running" for svc in STACK_SERVICES}
    stopped["searxng"] = "running-healthy"
    assert pr.classify_stack(stopped, pr.EXPECTED_SERVICES,
                             pr.OPTIONAL_PROFILE_SERVICES) == pr.STACK_STOPPED
    # optional 为空时与既有判定逐分支等价（六健康即 healthy、七容器即 partial）
    six = {svc: "running-healthy" for svc in STACK_SERVICES}
    assert pr.classify_stack(six, pr.EXPECTED_SERVICES) == pr.STACK_HEALTHY
    seven = dict(six, searxng="running-healthy")
    assert pr.classify_stack(seven, pr.EXPECTED_SERVICES) == pr.STACK_PARTIAL


def test_optional_services_cross_locked_with_compose_profiles() -> None:
    """searxng ↔ compose profiles: ["search"] 双向锁定：可选分类的依据是
    compose 显式 profile 声明（不挂 local/hybrid/cloud），不是容器名猜测；
    升级 compose profile 归属必须同步改 OPTIONAL_PROFILE_SERVICES。"""
    assert pr.OPTIONAL_PROFILE_SERVICES == frozenset({"searxng"})
    assert not (pr.OPTIONAL_PROFILE_SERVICES & pr.EXPECTED_SERVICES)
    assert "searxng" not in STACK_SERVICES
    for name in pr.OPTIONAL_PROFILE_SERVICES:
        block = _compose_service_block(name)
        assert 'profiles: ["search"]' in block, f"{name} 未锚定 --profile search"
        profiles_value = block.split("profiles:", 1)[1].split("\n", 1)[0]
        assert '"local"' not in profiles_value, f"{name} 不得挂 local profile"
    # CACHE_VOLUME_KEYS 的 searxng-cache 与可选服务同名族（M14-66 搜索栈）
    assert "searxng-cache" in pr.CACHE_VOLUME_KEYS


def _compose_service_block(name: str) -> str:
    """按两空格缩进键边界截取 compose 服务块（top-level 键 0 缩进收尾）。"""
    lines = COMPOSE_FILE.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"  {name}:")
    block: list[str] = []
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip(" ")) <= 2:
            break
        block.append(line)
    return "\n".join(block)


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
    # 卷面只读纪律：唯一 docker volume 子命令是 ls（绝不 rm/prune/create）
    for literal in ('"volume", "rm"', '"volume", "create"', '"volume", "prune"',
                    '"prune"'):
        assert literal not in source, f"禁止出现的卷子命令字面量: {literal}"
    assert '"volume", "ls"' in source  # 唯一卷探测面（只读）
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
    # 卷分类与 compose volumes: 块交叉锁定：声明集恰为持久+缓存，无未分类卷
    volumes_block = compose.split("volumes:", 1)[1] if "volumes:" in compose else ""
    for key in pr.PERSISTENT_VOLUME_KEYS + pr.CACHE_VOLUME_KEYS:
        assert f"{key}:" in volumes_block, f"compose 未声明分类卷: {key}"
    assert set(pr.PERSISTENT_VOLUME_KEYS) | set(pr.CACHE_VOLUME_KEYS) == {
        "postgres-data", "minio-data", "searxng-cache"}
    # 命名约定与 M14-41 卷采纳工具、M14-46 历史证据同构（<project>_<key> 下划线）
    adoption = (REPO_ROOT / "tools" / "ops" / "minio_volume_adoption.py").read_text(encoding="utf-8")
    assert 'VOLUME_NAME = f"{PROJECT}_minio-data"' in adoption
    evidence46 = (REPO_ROOT / "docs" / "evidence" / "m14-46-compose-label-reconcile" / "README.md").read_text(encoding="utf-8")
    assert "aios-m14-03-production-rehearsal_postgres-data" in evidence46


def test_service_name_derivation() -> None:
    assert pr._service_from_container_name(f"{PROJECT}-web-1", PROJECT) == "web"
    assert pr._service_from_container_name(f"{PROJECT}-api-2", PROJECT) == "api"
    assert pr._service_from_container_name("other-web-1", PROJECT) == ""
