#!/usr/bin/env python
"""M14-231 生产恢复前置检查（restore preflight）+ 受控执行入口 —— 只读分类 + 守卫式 --apply。

场景：本机生产彩排栈（compose 项目 ``aios-m14-03-production-rehearsal``，
--profile local，六服务）疑似停摆（例：公网 404、127.0.0.1:8000 无监听、
Docker 正常但栈容器不在场）。本工具回答一个问题：**现在能不能安全地按
既有文档化恢复路径（tools/ops/production_recovery.py）把栈拉回来，还是
先有人工阻塞要解**。不做公网路由重设计（公网边缘验收另走
tools/ops/public_edge_preflight.py，见 docs/PUBLIC_EDGE_DEPLOYMENT.md §9）。

只读 preflight 契约（fail-closed）：
- env pin 文件形状（infra/env.production-recovery，gitignored；模板 .example）：
  在场性 + production_recovery.PIN_KEYS 九键齐全 + 无模板占位值（<...> 包裹）
  + 无 compose 开发默认 secret 回落值——**只输出键名/结论，绝不输出任何值**；
- compose config 有效性（--quiet 静态校验）与渲染服务集（--services）对
  profile local 六服务锚点的漂移；stderr 摘要经 env secret 防御性脱敏后才可见；
- 镜像预检（docker image inspect，只读）：env tag 派生的 aios/api、aios/web
  自建镜像锚点 + production_recovery 的 aios/minio 自建锚点缺失即阻塞
  （恢复路径 up -d --no-build 不会 pull 自建命名镜像；本工具绝不 pull/build）；
  registry 镜像（postgres/redis/livekit）缺失仅为提示（compose up 可自动拉取）；
- 当前容器状态（docker ps -a 按 compose project label 过滤，只读）分类：
  stack-absent（容器完全不在场）/ stack-stopped（齐但全未运行）/
  stack-partial（部分在场或部分运行）/ stack-degraded（齐且全运行但未达
  healthy）/ stack-healthy；
- API/Web 本地监听（socket TCP 探测注入面 + loopback HTTP /health 只读探测）：
  服务容器在运行而端口不通 → listener-missing（含 M14-157 已知的 Docker
  Desktop stale host 映射形态，动作指向 production_web_gateway.py）；服务
  未运行而端口被占 → port-conflict（up 会绑不上，人工排查，本工具不杀进程）；
- 公网边缘恒为 uncertain：本地恢复不验证、也不该验证公网 https://…/aios
  链路（frps/VPS Nginx/frpc 都在本地 Docker 面之外）；本工具**绝不重启
  frpc**、绝不触碰 frpc 计划任务/进程，公网验证按 §9 独立执行；
- 报告/日志零 secret：env 值只进内存；一切回显（含 compose stderr 摘要、
  恢复脚本输出尾部）经 secret 值 → 标签的防御性脱敏；报告不含本机绝对路径。

--apply 守卫契约（仅当能安全走既有文档化恢复路径时才动作）：
- 需要前置 verdict == restore-required **且** 精确短语
  ``--confirm-phrase "APPLY PRODUCTION RESTORE"``，缺一即 fail-closed 拒绝
  （confirm-gate，零动作）；
- blocked（env/compose/镜像/端口任何阻塞）→ 拒绝执行，零动作；
  healthy → 无需恢复，拒绝执行（零动作）；
- 执行 = 纯委托既有文档化路径 tools/ops/production_recovery.py：先
  ``--dry-run``（只读计划，非零即中止），后 enforce（幂等
  ``up -d --no-build`` + 健康等待 + 本地语音受控调和——该脚本自身的
  pin check/镜像预检/fail-closed 语义原样生效）；本工具自身**绝不构造**
  任何 docker up/down/stop/rm/kill/restart/pull/build argv；
- 绝不删除卷/容器、绝不 factory reset、绝不重建持久数据、绝不触碰无关
  项目（恢复脚本恒 -p 本项目）、绝不重启或触碰 frpc。

退出码：0 = preflight 干净（verdict 为 restore-required 或 healthy，可安全
交给恢复路径/无需动作），或 --apply 全程成功；1 = 存在阻塞（或 confirm-gate
拒绝/apply 中止）；2 = 参数错误。

用法（仓库根）：
  python tools/ops/production_restore_preflight.py            # 只读 preflight
  python tools/ops/production_restore_preflight.py --apply \
      --confirm-phrase "APPLY PRODUCTION RESTORE"             # 守卫式恢复（委托既有路径）
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_USAGE = 2

TOOL_NAME = "production_restore_preflight"
SCHEMA = "aios-production-restore-preflight/1"
TAG = "[restore]"
CONFIRM_PHRASE = "APPLY PRODUCTION RESTORE"

TOOLS_DIR = Path(__file__).resolve().parent
RECOVERY_SCRIPT = TOOLS_DIR / "production_recovery.py"

#: 纯提示（不阻塞本地恢复）：registry 镜像缺失由 compose up 自动拉取
NOTE_REGISTRY_PULL_REQUIRED = "registry-pull-required"
NOTE_PUBLIC_EDGE_UNCERTAIN = "public-edge-uncertain"

BLOCK_DOCKER_UNAVAILABLE = "docker-unavailable"
BLOCK_ENV_MISSING = "env-missing"
BLOCK_ENV_MISSING_KEYS = "env-missing-keys"
BLOCK_ENV_PLACEHOLDER_KEYS = "env-placeholder-keys"
BLOCK_ENV_DEV_DEFAULT_KEYS = "env-dev-default-keys"
BLOCK_COMPOSE_CONFIG_INVALID = "compose-config-invalid"
BLOCK_COMPOSE_SERVICES_DRIFT = "compose-services-drift"
BLOCK_LOCAL_IMAGE_MISSING = "local-image-missing"
BLOCK_PORT_CONFLICT = "port-conflict"        # 形如 port-conflict:api
BLOCK_LISTENER_MISSING = "listener-missing"  # 形如 listener-missing:web
BLOCK_CONFIRM_GATE = "confirm-gate"

STACK_ABSENT = "stack-absent"
STACK_STOPPED = "stack-stopped"
STACK_PARTIAL = "stack-partial"
STACK_DEGRADED = "stack-degraded"
STACK_HEALTHY = "stack-healthy"

VERDICT_BLOCKED = "blocked"
VERDICT_RESTORE_REQUIRED = "restore-required"
VERDICT_HEALTHY = "healthy"

#: compose 文件里 secret 槽位的开发默认回落值（repo 公开字面量，非 secret）——
#: 生产恢复 env 里出现即「形状违规」（等于拿开发默认值重建生产容器）
DEV_DEFAULT_SECRET_VALUES = frozenset({
    "aios-local-dev-secret-7d21b9e4c8a3",   # AIOS_AUTH_SECRET 回落
    "ailos-local-dev-secret-0f4c9a1e7b2d",   # AIOS_LIVEKIT_API_SECRET 回落
})
SECRET_SHAPE_KEYS = ("AIOS_AUTH_SECRET", "AIOS_LIVEKIT_API_SECRET")

#: registry 镜像锚点（compose 可自动拉取——缺失是提示不是阻塞；契约测试
#: 与 infra/docker-compose.yml 交叉锁定，升版同步改这里）
REGISTRY_IMAGE_ANCHORS: tuple[str, ...] = (
    "postgres:17-alpine",
    "redis:7-alpine",
    "livekit/livekit-server:latest",
)

API_HOST_PORT = 8000
TCP_PROBE_TIMEOUT = 2.0
HTTP_PROBE_TIMEOUT = 3.0


def _load_recovery_module() -> Any:
    """importlib 复用既有恢复编排的常量/Runner/工具函数（该模块无导入副作用）。"""
    spec = importlib.util.spec_from_file_location("production_recovery_for_restore_preflight", RECOVERY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


recovery = _load_recovery_module()

#: 与既有恢复编排共享的锚点（契约测试交叉锁定两侧一致）
PIN_KEYS: tuple[str, ...] = tuple(recovery.PIN_KEYS)
EXPECTED_SERVICES: frozenset[str] = frozenset(recovery.EXPECTED_STACK_SERVICES)
DEFAULT_PROJECT: str = recovery.DEFAULT_PROJECT
DEFAULT_PROFILE: str = recovery.DEFAULT_PROFILE
DEFAULT_ENV_FILE: Path = recovery.DEFAULT_ENV_FILE
COMPOSE_FILE: Path = recovery.COMPOSE_FILE
LOCAL_BUILD_IMAGE_REFS: tuple[str, ...] = tuple(recovery.LOCAL_BUILD_IMAGE_REFS)

Runner = recovery.Runner  # 协议：run(argv, timeout=...) -> CommandResult


class PreflightLog(recovery.RunLog):
    """复用 RunLog 的 redact/行缓冲语义，仅换本工具的 stdout 前缀。"""

    def say(self, message: str) -> None:
        line = self.redact(message)
        self.lines.append(line)
        if self._echo:
            print(f"{TAG} {line}", flush=True)


class Probe(Protocol):
    """本地监听探测注入面（测试注入 FakeProbe，零网络）。"""

    def tcp_open(self, host: str, port: int) -> bool: ...

    def http_get_status(self, host: str, port: int, path: str) -> int | None: ...


class RealProbe:
    """真实 loopback 探测：TCP connect + 禁代理 HTTP GET（只读）。"""

    def tcp_open(self, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=TCP_PROBE_TIMEOUT):
                return True
        except OSError:
            return False

    def http_get_status(self, host: str, port: int, path: str) -> int | None:
        request = Request(f"http://{host}:{port}{path}", method="GET")
        try:
            with build_opener(ProxyHandler({})).open(request, timeout=HTTP_PROBE_TIMEOUT) as response:
                return int(response.status)
        except HTTPError as cause:
            return int(cause.code)
        except (URLError, OSError, TimeoutError):
            return None


# ---------------------------------------------------------------- 纯判定逻辑


@dataclass(frozen=True)
class EnvShape:
    present: bool
    missing_keys: tuple[str, ...] = ()
    placeholder_keys: tuple[str, ...] = ()
    dev_default_keys: tuple[str, ...] = ()


def evaluate_env_shape(env_file: Path) -> tuple[EnvShape, dict[str, str]]:
    """env pin 文件形状（值只留在内存，绝不进入任何输出）。"""
    if not env_file.is_file():
        return EnvShape(present=False, missing_keys=tuple(PIN_KEYS)), {}
    try:
        values = recovery.parse_env_file(env_file)
    except OSError:
        return EnvShape(present=False, missing_keys=tuple(PIN_KEYS)), {}
    missing = tuple(key for key in PIN_KEYS if not values.get(key))
    placeholders = recovery.placeholder_pin_keys(values)
    dev_defaults = tuple(
        key for key in SECRET_SHAPE_KEYS if values.get(key) in DEV_DEFAULT_SECRET_VALUES
    )
    return EnvShape(True, missing, placeholders, dev_defaults), values


def derive_service_state(state: str, status: str) -> str:
    """docker ps 的 State/Status → 归一服务态（running-* 视为在服务）。"""
    lowered = state.strip().lower()
    if lowered not in ("running", "paused", "restarting", "exited", "dead", "created", "removing"):
        return "unknown"
    if lowered != "running":
        return "not-running"
    text = status.lower()
    if "(healthy)" in text:
        return "running-healthy"
    if "(unhealthy)" in text:
        return "running-unhealthy"
    if "health: starting" in text:
        return "running-health-starting"
    return "running-no-healthcheck"


def classify_stack(service_states: dict[str, str], expected: frozenset[str]) -> str:
    """容器在场/运行/健康三轴 → 五分类（与 recovery.stack_health_gaps 同口径：
    running 且无 healthcheck 事实同样视为达标）。"""
    present = set(service_states)
    if not present:
        return STACK_ABSENT
    running = {name for name, value in service_states.items() if value.startswith("running")}
    if present == expected and not running:
        return STACK_STOPPED
    if present != expected or len(running) != len(expected & present):
        return STACK_PARTIAL
    if running != expected:
        return STACK_PARTIAL
    ok = {name for name in expected if service_states.get(name) in ("running-healthy", "running-no-healthcheck")}
    return STACK_HEALTHY if ok == expected else STACK_DEGRADED


def listener_blockers(service_states: dict[str, str], api_open: bool, web_open: bool) -> tuple[str, ...]:
    """运行中而端口不通 → listener-missing；未运行而端口被占 → port-conflict。"""
    blockers: list[str] = []
    for service, opened in (("api", api_open), ("web", web_open)):
        running = service_states.get(service, "").startswith("running")
        if running and not opened:
            blockers.append(f"{BLOCK_LISTENER_MISSING}:{service}")
        elif not running and opened:
            blockers.append(f"{BLOCK_PORT_CONFLICT}:{service}")
    return tuple(blockers)


def compose_verdict(blockers: tuple[str, ...], stack_class: str) -> str:
    if blockers:
        return VERDICT_BLOCKED
    return VERDICT_HEALTHY if stack_class == STACK_HEALTHY else VERDICT_RESTORE_REQUIRED


def build_actions(blockers: tuple[str, ...], stack_class: str) -> tuple[str, ...]:
    """阻塞/状态 → 文档化操作命令（确定性顺序；不含任何 secret/本机绝对路径）。"""
    actions: list[str] = []
    codes = set(blockers)
    if BLOCK_ENV_MISSING in codes:
        actions.append(
            "env pin 文件缺失：自 infra/env.production-recovery.example 创建 "
            "infra/env.production-recovery 并按部署记录填入真实值（值不入库不回显）"
        )
    if {BLOCK_ENV_MISSING_KEYS, BLOCK_ENV_PLACEHOLDER_KEYS, BLOCK_ENV_DEV_DEFAULT_KEYS} & codes:
        actions.append(
            "按 blockers 中的键名补齐/修正 env pin 值（仅键名可见；占位/开发默认值不得用于恢复）"
        )
    if BLOCK_DOCKER_UNAVAILABLE in codes:
        actions.append("启动 Docker Desktop 并等待引擎就绪后重试 preflight")
    if BLOCK_COMPOSE_CONFIG_INVALID in codes:
        actions.append("修复 compose/env 插值错误（见脱敏后的 stderr 摘要）后重试")
    if BLOCK_COMPOSE_SERVICES_DRIFT in codes:
        actions.append(
            "渲染服务集与 profile local 六服务锚点漂移——人工裁决（恢复脚本自身仅告警，"
            "本工具 --apply 对漂移 fail-closed）"
        )
    if BLOCK_LOCAL_IMAGE_MISSING in codes:
        actions.append(
            "自建镜像本机缺失：在获准窗口构建缺失锚点（本工具与恢复路径均绝不 pull/build；"
            "见 docs/PUBLIC_EDGE_DEPLOYMENT.md §8）"
        )
    for code in blockers:
        if code.startswith(BLOCK_PORT_CONFLICT):
            actions.append(
                f"{code}：端口被未归属本栈的进程占用——人工排查占用方（本工具绝不杀进程/不碰无关项目）"
            )
        if code.startswith(BLOCK_LISTENER_MISSING):
            target = "web" if code.endswith(":web") else "api"
            if target == "web":
                actions.append(
                    "listener-missing:web：疑似 Docker Desktop stale host 映射（M14-157 已知形态）——"
                    "按 tools/ops/production_web_gateway.py 提供回环网关（先 plan/status，装/卸需短语）"
                )
            else:
                actions.append("listener-missing:api：容器运行而 8000 不通——人工核查容器日志/端口映射")
    if stack_class in (STACK_ABSENT, STACK_STOPPED, STACK_PARTIAL, STACK_DEGRADED) and not codes:
        actions.append(
            "恢复命令（既有文档化路径）：python tools/ops/production_recovery.py --dry-run 先看计划，"
            "确认后去掉 --dry-run enforce；或本工具 --apply --confirm-phrase \"APPLY PRODUCTION RESTORE\""
        )
    actions.append(
        "公网边缘恒 uncertain：本地恢复不验证 https://…/aios 公网链路；恢复后按 "
        "docs/PUBLIC_EDGE_DEPLOYMENT.md §9 跑 tools/ops/public_edge_preflight.py，"
        "frpc 状态用 tools/ops/frpc_windows_controller.py status 核查（本工具绝不重启 frpc）"
    )
    return tuple(actions)


# ---------------------------------------------------------------- 只读探测编排


def _compose_argv(compose_file: Path, project: str, profile: str,
                  env_file: Path | None, tail: list[str]) -> tuple[str, ...]:
    return tuple(recovery._compose_base(compose_file, project, profile, env_file) + tail)


def probe_engine(runner: Runner) -> tuple[bool, str]:
    result = runner.run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=30.0)
    version = result.stdout.strip()
    return (result.returncode == 0 and bool(version)), version or "unavailable"


def probe_compose(runner: Runner, compose_file: Path, project: str, profile: str,
                  env_file: Path | None) -> tuple[bool, str, frozenset[str] | None]:
    """compose config --quiet + --services（只读渲染）。返回 (ok, stderr摘要, 服务集)。"""
    quiet = runner.run(_compose_argv(compose_file, project, profile, env_file, ["config", "--quiet"]),
                       timeout=120.0)
    if quiet.returncode != 0:
        return False, quiet.stderr.strip()[-400:], None
    services_result = runner.run(
        _compose_argv(compose_file, project, profile, env_file, ["config", "--services"]), timeout=120.0
    )
    if services_result.returncode != 0:
        return False, services_result.stderr.strip()[-400:], None
    services = frozenset(
        line.strip() for line in services_result.stdout.splitlines() if line.strip()
    )
    return True, "", services


def probe_containers(runner: Runner, project: str) -> dict[str, str]:
    """docker ps -a 按 compose project label 过滤（只读）→ {service: 归一态}。"""
    result = runner.run(
        ["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
         "--format", "json"],
        timeout=60.0,
    )
    states: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        name = str(row.get("Names") or "")
        service = _service_from_container_name(name, project)
        if service:
            states[service] = derive_service_state(str(row.get("State") or ""), str(row.get("Status") or ""))
    return states


def _service_from_container_name(name: str, project: str) -> str:
    """<project>-<service>-<N> → <service>（recovery.container_name 同构）。"""
    prefix = f"{project}-"
    if not name.startswith(prefix):
        return ""
    rest = name[len(prefix):]
    head, _, tail = rest.rpartition("-")
    return head if tail.isdigit() else rest


def probe_local_images(runner: Runner, env_values: dict[str, str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(自建缺失, registry 缺失)——docker image inspect 只读；探测失败按缺失计。"""
    local_refs = list(LOCAL_BUILD_IMAGE_REFS)
    for key, name in (("AIOS_IMAGE_TAG", "aios/api"), ("AIOS_WEB_IMAGE_TAG", "aios/web")):
        tag = env_values.get(key)
        if tag:
            local_refs.append(f"{name}:{tag}")
    local_missing = tuple(
        ref for ref in local_refs
        if runner.run(["docker", "image", "inspect", ref, "--format", "{{.Id}}"],
                      timeout=30.0).returncode != 0
    )
    registry_missing = tuple(
        ref for ref in REGISTRY_IMAGE_ANCHORS
        if runner.run(["docker", "image", "inspect", ref, "--format", "{{.Id}}"],
                      timeout=30.0).returncode != 0
    )
    return local_missing, registry_missing


def _display_host(bind_ip: str) -> str:
    return bind_ip if bind_ip else "127.0.0.1"


def _redact_env_secrets(text: str, env_values: dict[str, str]) -> str:
    """env secret 值 → 标签（防御性脱敏；值只在内存，报告/日志永不落原值）。"""
    for key in SECRET_SHAPE_KEYS:
        value = env_values.get(key)
        if value and len(value) >= 8:
            text = text.replace(value, f"***REDACTED:{key}***")
    return text


def run_preflight(*, runner: Runner, probe: Probe, log: PreflightLog,
                  env_file: Path = DEFAULT_ENV_FILE, project: str = DEFAULT_PROJECT,
                  profile: str = DEFAULT_PROFILE,
                  compose_file: Path = COMPOSE_FILE) -> dict[str, Any]:
    """执行全部只读检查并返回报告 dict（含 verdict/blockers/actions）。"""
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "tool": TOOL_NAME,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inputs": {
            "project": project,
            "profile": profile,
            "env_file": env_file.name,
            "compose_file": compose_file.name,
        },
    }
    notes: list[str] = []

    # ① Docker 引擎（不可用则 Docker 面探测全部跳过——fail-closed）
    engine_ok, engine_version = probe_engine(runner)
    report["docker"] = {"available": engine_ok, "server_version": engine_version}
    if not engine_ok:
        log.say("docker engine: 不可用——compose/镜像/容器探测全部跳过（fail-closed）")

    # ② env pin 形状（键名-only）
    shape, env_values = evaluate_env_shape(env_file)
    report["env"] = {
        "present": shape.present,
        "missing_keys": list(shape.missing_keys),
        "placeholder_keys": list(shape.placeholder_keys),
        "dev_default_keys": list(shape.dev_default_keys),
    }
    if not shape.present:
        log.say(f"env pin: 缺失（{env_file.name}；模板 infra/env.production-recovery.example）")
    else:
        log.say(
            f"env pin: 在场；缺键 {len(shape.missing_keys)}、占位键 {len(shape.placeholder_keys)}、"
            f"开发默认值键 {len(shape.dev_default_keys)}（值不回显）"
        )

    # ③ compose config + 渲染服务集
    compose_ok: bool | None = None
    services: frozenset[str] | None = None
    compose_stderr = ""
    if engine_ok:
        env_for_compose = env_file if shape.present else None
        compose_ok, compose_stderr, services = probe_compose(
            runner, compose_file, project, profile, env_for_compose
        )
    drift: list[str] = []
    if services is not None:
        drift = sorted(str(s) for s in (set(EXPECTED_SERVICES) ^ set(services)))
    report["compose"] = {
        "config_ok": compose_ok,
        "services": sorted(services) if services is not None else None,
        "expected_services": sorted(EXPECTED_SERVICES),
        "drift": drift,
        "stderr_tail": _redact_env_secrets(compose_stderr, env_values) if compose_stderr else "",
    }

    # ④ 镜像预检（env tag 派生自建锚点 + minio 自建锚点 + registry 提示）
    local_missing: tuple[str, ...] = ()
    registry_missing: tuple[str, ...] = ()
    if engine_ok:
        local_missing, registry_missing = probe_local_images(runner, env_values)
    report["images"] = {
        "local_missing": list(local_missing),
        "registry_missing": list(registry_missing),
    }
    if registry_missing:
        notes.append(NOTE_REGISTRY_PULL_REQUIRED)

    # ⑤ 当前容器状态（compose project label 过滤，只读）
    service_states: dict[str, str] = probe_containers(runner, project) if engine_ok else {}
    stack_class = classify_stack(service_states, EXPECTED_SERVICES)
    report["containers"] = {
        "classification": stack_class,
        "service_states": dict(sorted(service_states.items())),
    }
    log.say(f"containers: {stack_class}（在场 {len(service_states)}/{len(EXPECTED_SERVICES)} 服务）")

    # ⑥ API/Web 本地监听（TCP + loopback HTTP /health 只读）
    bind_ip = env_values.get("AIOS_BIND_IP") or "127.0.0.1"
    web_port_raw = env_values.get("AIOS_WEB_PORT") or "3000"
    web_port = int(web_port_raw) if web_port_raw.isdigit() else 0
    display = _display_host(bind_ip)
    api_open = probe.tcp_open(display, API_HOST_PORT)
    web_open = probe.tcp_open(display, web_port) if web_port else False
    health_status = (
        probe.http_get_status(display, API_HOST_PORT, "/health") if api_open else None
    )
    loopback = display in ("127.0.0.1", "localhost", "::1")
    shown_host = display if loopback else "<non-loopback-bind>"
    report["listeners"] = {
        "bind_ip_loopback": loopback,
        "api": {"port": API_HOST_PORT, "tcp_open": api_open, "health_status": health_status},
        "web": {"port": web_port or None, "tcp_open": web_open},
    }
    log.say(
        f"listeners: api {shown_host}:{API_HOST_PORT} {'open' if api_open else 'closed'}"
        f"（/health={health_status}）；web {shown_host}:{web_port or '?'} "
        f"{'open' if web_open else 'closed'}"
    )

    # ⑦ 公网边缘恒 uncertain（本地面之外；绝不重启 frpc / 不探测公网）
    report["public_edge"] = {
        "status": NOTE_PUBLIC_EDGE_UNCERTAIN,
        "note": "本地恢复不验证公网 https://…/aios 链路（frps/VPS Nginx/frpc 在本地 Docker 面外）；"
                "恢复后按 §9 public_edge_preflight + frpc_windows_controller status 独立验收",
    }

    # ⑧ 汇总分类（纯函数可离线复测）
    blockers = _collect_blockers(shape, engine_ok, compose_ok, drift, local_missing,
                                 listener_blockers(service_states, api_open, web_open))
    verdict = compose_verdict(blockers, stack_class)
    report["notes"] = notes
    report["blockers"] = list(blockers)
    report["actions"] = list(build_actions(blockers, stack_class))
    report["verdict"] = verdict
    for code in blockers:
        log.say(f"blocker: {code}")
    log.say(f"verdict: {verdict}")
    return report


def _collect_blockers(shape: EnvShape, engine_ok: bool, compose_ok: bool | None,
                      drift: list[str], local_missing: tuple[str, ...],
                      listener_codes: tuple[str, ...]) -> tuple[str, ...]:
    """六类阻塞归并（确定性顺序）；栈状态本身不阻塞（进 verdict 语义）。"""
    blockers: list[str] = []
    if not engine_ok:
        blockers.append(BLOCK_DOCKER_UNAVAILABLE)
    if not shape.present:
        blockers.append(BLOCK_ENV_MISSING)
    else:
        if shape.missing_keys:
            blockers.append(BLOCK_ENV_MISSING_KEYS)
        if shape.placeholder_keys:
            blockers.append(BLOCK_ENV_PLACEHOLDER_KEYS)
        if shape.dev_default_keys:
            blockers.append(BLOCK_ENV_DEV_DEFAULT_KEYS)
    if compose_ok is False:
        blockers.append(BLOCK_COMPOSE_CONFIG_INVALID)
    if drift:
        blockers.append(BLOCK_COMPOSE_SERVICES_DRIFT)
    if local_missing:
        blockers.append(BLOCK_LOCAL_IMAGE_MISSING)
    blockers.extend(listener_codes)
    return tuple(blockers)


# ---------------------------------------------------------------- --apply 守卫


def execute_guarded_recovery(*, runner: Runner, log: PreflightLog, env_file: Path,
                             project: str, profile: str) -> int:
    """纯委托既有文档化恢复路径：先 --dry-run 计划（非零即中止），后 enforce。

    本函数不构造任何 docker argv——恢复动作面（幂等 up -d --no-build、健康
    等待、语音受控调和）完全由 production_recovery.py 自身的 fail-closed
    语义承担（pin check/自建镜像预检/绝不 stop/rm/down/restart/pull/build）。
    """
    base = [
        sys.executable, str(RECOVERY_SCRIPT),
        "--project", project, "--profile", profile, "--env-file", str(env_file),
    ]
    log.say("apply 1/2: production_recovery.py --dry-run（只读计划门，非零即中止）")
    dry = runner.run(base + ["--dry-run"], timeout=900.0)
    _echo_tail(log, dry.stdout, dry.stderr)
    if dry.returncode != 0:
        log.say(f"apply: dry-run 门未过（rc={dry.returncode}）——拒绝 enforce，零容器改动")
        return EXIT_BLOCKED
    log.say("apply 2/2: production_recovery.py enforce（幂等 up -d --no-build + 健康等待 + 语音受控调和）")
    enforce = runner.run(base, timeout=1800.0)
    _echo_tail(log, enforce.stdout, enforce.stderr)
    if enforce.returncode != 0:
        log.say(f"apply: enforce 返回 rc={enforce.returncode}——人工核查恢复日志后处置")
        return EXIT_BLOCKED
    log.say("apply: 既有恢复路径 enforce 完成（rc=0）——建议重跑本工具只读 preflight 复核终态")
    return EXIT_OK


def _echo_tail(log: PreflightLog, stdout: str, stderr: str) -> None:
    for text in (stdout, stderr):
        for line in text.strip().splitlines()[-25:]:
            if line.strip():
                log.say(f"  | {line.strip()}")


def _write_report_atomic(path: Path, report: dict[str, Any]) -> None:
    directory = path.resolve().parent
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False
    ) as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temp_name = handle.name
    try:
        os.replace(temp_name, path)
    except OSError:
        try:
            os.unlink(temp_name)
        finally:
            raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "生产恢复前置检查（只读 fail-closed 分类）+ 守卫式 --apply（纯委托既有"
            " production_recovery.py：dry-run 门 + enforce）。公网边缘恒 uncertain；"
            "绝不删除卷/容器、绝不 factory reset、绝不重启 frpc。"
        ),
    )
    parser.add_argument("--apply", action="store_true",
                        help="守卫式恢复：前置 verdict=restore-required 且短语匹配才委托既有恢复路径")
    parser.add_argument("--confirm-phrase", default="",
                        help=f"--apply 需要的精确短语（{CONFIRM_PHRASE}）")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help=f"compose 项目名（默认 {DEFAULT_PROJECT}）")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help=f"compose profile（默认 {DEFAULT_PROFILE}）")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE,
                        help="部署 pin env 文件（默认 infra/env.production-recovery；模板 .example）")
    parser.add_argument("--compose-file", type=Path, default=COMPOSE_FILE,
                        help="compose 文件（默认 infra/docker-compose.yml）")
    parser.add_argument("--output", type=Path, help="JSON 报告输出路径（原子写，不含绝对路径）")
    return parser


def main(argv: list[str] | None = None, *, runner: Runner | None = None,
         probe: Probe | None = None) -> int:
    args = build_parser().parse_args(argv)
    # 防御性脱敏：env secret 值 → 标签（一切回显/报告写盘前替换；值只进内存）
    _, env_values = evaluate_env_shape(args.env_file)
    redactions = {
        env_values[key]: f"***REDACTED:{key}***"
        for key in SECRET_SHAPE_KEYS if env_values.get(key)
    }
    log = PreflightLog(redactions=redactions)
    log.say(
        f"=== 生产恢复 preflight: project={args.project} profile={args.profile} "
        f"env={args.env_file.name} apply={args.apply} ==="
    )
    active_runner = runner or recovery.RealRunner()
    active_probe = probe or RealProbe()
    report = run_preflight(
        runner=active_runner, probe=active_probe, log=log,
        env_file=args.env_file, project=args.project, profile=args.profile,
        compose_file=args.compose_file,
    )
    for action in report["actions"]:
        log.say(f"action: {action}")
    if args.output:
        try:
            _write_report_atomic(args.output, report)
            log.say(f"报告: {args.output.name}")
        except OSError as cause:
            log.say(f"报告写出失败: {type(cause).__name__}")
            return EXIT_BLOCKED

    if not args.apply:
        return EXIT_OK if report["verdict"] in (VERDICT_HEALTHY, VERDICT_RESTORE_REQUIRED) else EXIT_BLOCKED

    # --apply 守卫链：短语 → verdict → 委托
    if args.confirm_phrase != CONFIRM_PHRASE:
        log.say(f"apply: {BLOCK_CONFIRM_GATE}——需要精确 --confirm-phrase（零动作，未执行任何恢复）")
        return EXIT_BLOCKED
    if report["verdict"] == VERDICT_HEALTHY:
        log.say("apply: 栈已 healthy——无需恢复，拒绝执行（零动作）")
        return EXIT_OK
    if report["verdict"] != VERDICT_RESTORE_REQUIRED:
        log.say(f"apply: verdict={report['verdict']}（blockers: {', '.join(report['blockers'])}）——拒绝执行，零动作")
        return EXIT_BLOCKED
    return execute_guarded_recovery(
        runner=active_runner, log=log, env_file=args.env_file,
        project=args.project, profile=args.profile,
    )


if __name__ == "__main__":
    sys.exit(main())
