#!/usr/bin/env python
"""M14-157 生产 Web 回环网关 supervisor：plan / status / install / uninstall。

问题背景（supervisor 实证）：生产 Web 容器
``aios-m14-03-production-rehearsal-web-1`` 在 compose 网络
``aios-m14-03-production-rehearsal_default`` 上健康运行，但既有 host 映射
``127.0.0.1:3011`` 对 Docker Desktop 呈 TCP 空应答（API 8000 正常）。本
工具提供**稳定、可回滚**的本地回环入口：独立 pinned Caddy 容器加入同一
compose 网络，``Caddy :80 → web:3000``，只发布 ``127.0.0.1:<host-port>:80``
（默认 3012）——不重建/不重启既有生产栈。

Fail-closed 契约（承接 M14-06/M14-155/M14-156 纪律）：
- 默认 plan 零写入零探测零 Docker；status 只读（仅 inspect 查询，零变更）；
- install 需**同时**满足 ``--execute`` 与精确短语
  ``INSTALL PRODUCTION WEB GATEWAY``；uninstall 需 ``--execute`` 与
  ``UNINSTALL PRODUCTION WEB GATEWAY``——缺一即 confirm-gate 非零退出、
  零副作用；
- install preflight fail-closed：Docker 不可用 / 目标网络缺失 / 生产 Web
  容器缺失·未运行·非 healthy / host 端口被占 / config-dir 仓库内·符号
  链接·外来条目 / 同名容器已存在（无论归属——绝不覆盖）/ 既有网关配置
  漂移（镜像·标签·网络·绑定·端口任一不符）；
- host-port：1..65535 十进制整数；显式封锁生产/彩演已知端口集
  （3011/8000/39443 及 infra/docker-compose.yml 全部 host 发布位——
  3000/5433/6379/7880/7881/7882-7892/8878/9000/9001），默认 3012；
- 镜像恒为 pinned ``docker.io/library/caddy@sha256:6aeddd44…``（与边缘
  模板同 digest）；容器名恒 ``aios-production-web-gateway``，所有权标签
  ``io.aios.managed-by=production_web_gateway`` +
  ``io.aios.milestone=m14-157``；只 attach
  ``aios-m14-03-production-rehearsal_default``；只发布回环端口；
- install 后装后校验（inspect 精确复核 image/labels/network/bind/port/
  restart/**Caddyfile 挂载**——install 必须精确核对挂载 source 等于
  渲染产物；status 未给 --config-dir 时只校验挂载形状并明示"未精确
  验证"，绝不虚报）+ HTTP GET ``/`` 于 ``127.0.0.1:<host-port>`` 必须
  200 才报 pass；渲染原子写且写后自校验；
- status 分类 missing / installed / degraded（自有但配置漂移）/
  foreign（标签不属本工具），零变更；uninstall 只删**精确自有**容器
  （``docker rm -f``），绝不删除配置目录与证据，foreign/missing 各自
  拒绝/幂等；
- 证据 rehearsal 风格 JSON+MD 原子写（默认 gitignored
  ``.verify/artifacts/m14-157-production-web-gateway/``，显式与默认路径
  均过路径链符号链接检查）：result、host port、upstream、容器事实、
  回滚指引；挂载只记脱敏事实（source basename +
  exact_source_verified——本机绝对路径绝不入证据）；
  ``production_public_ready`` 恒 false；输出/证据/错误绝不包含 secret
  或本机绝对路径；
- Docker 与 HTTP 探测全部经注入 Runner/Prober 边界（测试零真实
  Docker 零网络）；**docker argv 白名单在 RealRunner 运行时强制**——
  仅 --version/network inspect/inspect/run/rm 五前缀可执行，其余 argv
  在触及 subprocess 前即拒绝（126），超时（124）/二进制缺失（127）均
  无 traceback；本工具绝不触碰生产栈容器的生命周期（不 stop/
  restart/recreate 任何既有容器）。

诚实边界：这是**本地回环恢复路径**——不修复 Docker Desktop 的坏映射、
不暴露公网流量、不改变生产边缘设计；``production_public_ready=false``
恒不变。

Exit codes：0 = 成功（含 plan/status 只读输出）；1 = 失败（稳定类别
confirm-gate / port-invalid / config-dir-unsafe / evidence-dir-unsafe /
docker-unavailable / network-missing / web-unhealthy / port-busy /
container-exists / gateway-drift / install-failed / verify-failed /
probe-failed / uninstall-failed / internal-error）；2 = 用法错误。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_NAME = "production_web_gateway"
SCHEMA = "aios-production-web-gateway/1"
TEMPLATE_PATH = REPO_ROOT / "infra" / "edge" / "production-web-gateway" / "Caddyfile.example"
DEFAULT_EVIDENCE_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-157-production-web-gateway"

CONTAINER_NAME = "aios-production-web-gateway"
PRODUCTION_NETWORK = "aios-m14-03-production-rehearsal_default"
PRODUCTION_WEB_CONTAINER = "aios-m14-03-production-rehearsal-web-1"
UPSTREAM = "web:3000"  # compose 服务别名 web → 生产 Web 容器 :3000
CADDY_IMAGE = ("docker.io/library/caddy@sha256:"
               "6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b")
OWNERSHIP_LABELS = {"io.aios.managed-by": "production_web_gateway",
                    "io.aios.milestone": "m14-157"}
RESTART_POLICY = "unless-stopped"
CONTAINER_HTTP_PORT = "80/tcp"

INSTALL_CONFIRM_PHRASE = "INSTALL PRODUCTION WEB GATEWAY"
UNINSTALL_CONFIRM_PHRASE = "UNINSTALL PRODUCTION WEB GATEWAY"

LOOPBACK = "127.0.0.1"
DEFAULT_HOST_PORT = 3012
# 封锁集（fail-closed）：生产 compose（infra/docker-compose.yml）全部 host
# 发布位 + 生产 Web 映射 3011（env.production-recovery 锚定）+ API 8000
# + M14-156 彩排入口 39443。网关绝不抢占任何既有服务端口。
BLOCKED_HOST_PORTS = frozenset(
    {3000, 3011, 8000, 39443, 5433, 6379, 7880, 7881, 8878, 9000, 9001}
    | set(range(7882, 7893))
)
CONFIG_DIR_ALLOWED_ENTRIES = frozenset({"Caddyfile"})

EXIT_OK = 0
EXIT_FAILURE = 1

CATEGORY_CONFIRM = "confirm-gate"
CATEGORY_PORT_ARG = "port-invalid"
CATEGORY_CONFIG_DIR = "config-dir-unsafe"
CATEGORY_EVIDENCE_DIR = "evidence-dir-unsafe"
CATEGORY_DOCKER = "docker-unavailable"
CATEGORY_NETWORK = "network-missing"
CATEGORY_WEB = "web-unhealthy"
CATEGORY_PORT = "port-busy"
CATEGORY_EXISTS = "container-exists"
CATEGORY_DRIFT = "gateway-drift"
CATEGORY_INSTALL = "install-failed"
CATEGORY_VERIFY = "verify-failed"
CATEGORY_PROBE = "probe-failed"
CATEGORY_UNINSTALL = "uninstall-failed"
CATEGORY_INTERNAL = "internal-error"

STATUS_INSTALLED = "installed"
STATUS_MISSING = "missing"
STATUS_DEGRADED = "degraded"
STATUS_FOREIGN = "foreign"

# docker argv 白名单（工具只发起这些前缀；测试锁定）：
#   docker --version / docker network inspect / docker inspect /
#   docker run / docker rm —— 绝无 stop/restart/recreate 生产容器面。
ALLOWED_DOCKER_PREFIXES = (
    ("docker", "--version"),
    ("docker", "network", "inspect"),
    ("docker", "inspect"),
    ("docker", "run"),
    ("docker", "rm"),
)


class GatewayError(Exception):
    """fail-closed 错误：携带稳定类别码；文本不含 secret/绝对路径。"""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class Runner(Protocol):
    def run(self, args: list[str]) -> tuple[int, str]:
        """执行平台命令，返回 (returncode, 合并输出文本)。"""


class RealRunner:
    """真实 Runner：argv 白名单**运行时强制**（R1 修正 3）+ 超时/缺省容错。

    只放行 ALLOWED_DOCKER_PREFIXES 的前缀（--version/network inspect/
    inspect/run/rm）——任何其他 argv 在触及 subprocess 之前即被拒绝
    （returncode=126，绝不执行）；docker 未安装（OSError）→ 127；
    子进程超时（TimeoutExpired）→ 124——两者都无 traceback 外溢。
    """

    def run(self, args: list[str]) -> tuple[int, str]:
        import subprocess

        if not any(tuple(args[: len(p)]) == p for p in ALLOWED_DOCKER_PREFIXES):
            return 126, "argv-not-allowed"
        creationflags = 0x08000000 if os.name == "nt" else 0
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=300, check=False, creationflags=creationflags,
            )
        except subprocess.TimeoutExpired:
            return 124, "TimeoutExpired"
        except OSError as cause:
            return 127, type(cause).__name__
        return result.returncode, (result.stdout or "") + (result.stderr or "")


class Prober(Protocol):
    def http_get(self, url: str, headers: dict[str, str], timeout: float) -> tuple[int, str]: ...

    def port_free(self, host: str, port: int) -> bool: ...


class RealProber:
    """真实 Prober：直连（禁代理）+ socket connect_ex 空闲判定。"""

    def http_get(self, url: str, headers: dict[str, str], timeout: float) -> tuple[int, str]:
        from urllib.error import HTTPError
        from urllib.request import ProxyHandler, Request, build_opener

        request = Request(url, headers=headers, method="GET")
        try:
            with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
                return int(response.status), response.read(2048).decode("utf-8", "replace")
        except HTTPError as cause:
            return int(cause.code), ""
        except (OSError, TimeoutError) as cause:
            raise GatewayError(CATEGORY_PROBE, f"探测连接失败: {type(cause).__name__}") from cause

    def port_free(self, host: str, port: int) -> bool:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(2.0)
            return sock.connect_ex((host, port)) != 0


# ---------------------------------------------------------------- 端口/目录防线


def parse_host_port(raw: str | None, default: int = DEFAULT_HOST_PORT) -> int:
    """host-port 解析：1..65535 十进制整数 + 封锁集拒绝；缺省用默认。"""
    if raw is None:
        return default
    candidate = raw.strip()
    if not re.fullmatch(r"[0-9]{1,5}", candidate) or not 1 <= int(candidate) <= 65535:
        raise GatewayError(CATEGORY_PORT_ARG, "--host-port 必须是 1..65535 的十进制整数端口")
    port = int(candidate)
    if port in BLOCKED_HOST_PORTS:
        raise GatewayError(
            CATEGORY_PORT_ARG,
            f"--host-port={port} 属生产/彩演已知端口集（封锁），网关不得抢占——换一个空闲高位端口",
        )
    return port


def _is_inside(path: Path, root: Path) -> bool:
    return path == root or str(path).startswith(str(root) + os.sep)


def _reject_symlink_in_chain(expanded: Path, category: str, label: str) -> None:
    """路径链防线：任何已存在组件是符号链接即拒绝（先于 resolve）；缺省尾部合法。"""
    parts = expanded.parts
    current = Path(parts[0]) if expanded.is_absolute() else Path(".")
    rest = parts[1:] if expanded.is_absolute() else parts
    for part in rest:
        current = current / part
        if current.is_symlink():
            raise GatewayError(category, f"{label} 路径链中存在符号链接组件（拒绝）")
        if not current.exists():
            return


def validate_config_dir(raw: str | None) -> Path:
    """config-dir 契约：显式绝对路径、仓库外、路径链无符号链接、非根；
    已存在时必须是纯目录且只含 Caddyfile（幂等覆写）。"""
    if not raw or not raw.strip():
        raise GatewayError(CATEGORY_CONFIG_DIR, "--config-dir 必须显式提供（绝对路径，仓库外）")
    candidate = Path(raw.strip()).expanduser()
    if not candidate.is_absolute():
        raise GatewayError(CATEGORY_CONFIG_DIR, "--config-dir 必须是绝对路径（相对路径使容器挂载不可解析）")
    _reject_symlink_in_chain(candidate, CATEGORY_CONFIG_DIR, "--config-dir")
    resolved = candidate.resolve()
    if resolved == Path(resolved.anchor):
        raise GatewayError(CATEGORY_CONFIG_DIR, "--config-dir 不得是文件系统根目录")
    if _is_inside(resolved, REPO_ROOT):
        raise GatewayError(CATEGORY_CONFIG_DIR, "--config-dir 不得位于仓库内（网关配置必须在仓库外）")
    if resolved.exists():
        if not resolved.is_dir():
            raise GatewayError(CATEGORY_CONFIG_DIR, "--config-dir 路径存在但不是目录")
        unexpected = sorted(p.name for p in resolved.iterdir()
                            if p.name not in CONFIG_DIR_ALLOWED_ENTRIES)
        if unexpected:
            raise GatewayError(
                CATEGORY_CONFIG_DIR,
                f"--config-dir 含非网关条目 {unexpected}（拒绝盲写无关目录；只允许 Caddyfile 幂等覆写）",
            )
        for entry in resolved.iterdir():
            if entry.is_symlink() or not entry.is_file():
                raise GatewayError(CATEGORY_CONFIG_DIR, f"--config-dir 条目 {entry.name} 必须是常规文件")
    return resolved


def resolve_evidence_dir(raw: str | None) -> Path:
    """证据目录：仓库外，或仓库 gitignored .verify/ 之下；拒绝 tracked 区。

    R1 修正 2：显式与默认路径都先做**路径链逐组件**符号链接检查（与
    config-dir 同款——末段检查会漏掉符号链接父目录）；默认 .verify 路径
    保持放行。
    """
    candidate = (Path(raw.strip()) if raw and raw.strip() else DEFAULT_EVIDENCE_DIR).expanduser()
    _reject_symlink_in_chain(candidate, CATEGORY_EVIDENCE_DIR, "证据目录")
    resolved = candidate.resolve()
    if resolved == Path(resolved.anchor):
        raise GatewayError(CATEGORY_EVIDENCE_DIR, "--evidence-dir 不得是文件系统根目录")
    verify_root = (REPO_ROOT / ".verify").resolve()
    if _is_inside(resolved, REPO_ROOT) and not _is_inside(resolved, verify_root):
        raise GatewayError(
            CATEGORY_EVIDENCE_DIR,
            "--evidence-dir 不得位于仓库 tracked 区（只允许仓库外或 .verify/ 之下）",
        )
    return resolved


# ---------------------------------------------------------------- 渲染与自检


def _atomic_write(path: Path, content: str, mode: int) -> None:
    """原子写（同目录 temp + os.replace + 显式 chmod）；失败不留半成品/.tmp。"""
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    except OSError:
        try:
            os.unlink(temp_name)
        finally:
            raise


def render_caddyfile(config_dir: Path) -> str:
    """渲染网关 Caddyfile（模板复制，原子写 0644）并自检不变量。"""
    try:
        content = TEMPLATE_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as cause:
        raise GatewayError(
            CATEGORY_INSTALL, f"网关模板不可读: {type(cause).__name__}"
        ) from cause
    validate_caddy_config(content)
    config_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(config_dir, 0o755)
    _atomic_write(config_dir / "Caddyfile", content, 0o644)
    written = (config_dir / "Caddyfile").read_text(encoding="utf-8")
    if written != content:
        raise GatewayError(CATEGORY_INSTALL, "渲染后回读不一致（原子写失败保真）")
    return "Caddyfile"


def validate_caddy_config(content: str) -> None:
    """Caddyfile 不变量：HTTP-only :80、无 443/tls、上游恒 web:3000。"""
    body = "\n".join(
        line for line in content.splitlines() if not line.lstrip().startswith("#")
    )
    for marker in ("auto_https off", "admin off", f"reverse_proxy {UPSTREAM}"):
        if marker not in body:
            raise GatewayError(CATEGORY_INSTALL, f"网关 Caddyfile 缺不变量: {marker}")
    if ":80 {" not in body or re.search(r"\b443\b", body) or re.search(r"^\s*tls\s", body, re.MULTILINE):
        raise GatewayError(CATEGORY_INSTALL, "网关 Caddyfile 必须保持 HTTP-only（:80 站点，无 443/tls）")


# ---------------------------------------------------------------- docker 查询与归属


def _inspect_json(runner: Runner, target: str, fmt: str) -> tuple[bool, Any]:
    """docker inspect --format 的 JSON 解析；不存在返回 (False, None)；
    查询失败/畸形 JSON 各自抛/返回可判定错误。"""
    code, out = runner.run(["docker", "inspect", target, "--format", fmt])
    if code != 0:
        if "no such object" in out.lower() or "not found" in out.lower():
            return False, None
        return False, "query-failed"
    try:
        return True, json.loads(out.strip())
    except json.JSONDecodeError:
        return True, "malformed"


def docker_available(runner: Runner) -> None:
    code, _ = runner.run(["docker", "--version"])
    if code != 0:
        raise GatewayError(CATEGORY_DOCKER, "docker CLI 不可用（returncode != 0）")


def require_production_network(runner: Runner) -> None:
    code, out = runner.run(["docker", "network", "inspect", PRODUCTION_NETWORK, "--format", "{{.Name}}"])
    if code != 0 or out.strip() != PRODUCTION_NETWORK:
        raise GatewayError(
            CATEGORY_NETWORK, f"目标网络 {PRODUCTION_NETWORK} 缺失或不可查询（不猜测）"
        )


def require_production_web_healthy(runner: Runner) -> dict[str, Any]:
    ok, state = _inspect_json(runner, PRODUCTION_WEB_CONTAINER, "{{json .State}}")
    if not ok or not isinstance(state, dict):
        raise GatewayError(
            CATEGORY_WEB, f"生产 Web 容器 {PRODUCTION_WEB_CONTAINER} 缺失或状态不可查询"
        )
    if state.get("Running") is not True:
        raise GatewayError(CATEGORY_WEB, "生产 Web 容器未运行（Running != true）")
    health = state.get("Health")
    if not isinstance(health, dict) or health.get("Status") != "healthy":
        status = health.get("Status") if isinstance(health, dict) else None
        raise GatewayError(CATEGORY_WEB, f"生产 Web 容器非 healthy（Status={status!r}）")
    return {"running": True, "health": "healthy"}


CADDYFILE_MOUNT_DESTINATION = "/etc/caddy/Caddyfile"


def _mount_facts(runner: Runner, expected_caddyfile: Path | None) -> dict[str, Any]:
    """Caddyfile bind mount 事实（R1 修正 1）：type/target/readonly/source 形状。

    expected_caddyfile 给出时精确比对 source（exact_source_verified=True）；
    未给出时只校验形状（bind、目标 /etc/caddy/Caddyfile、只读、source
    basename 为 Caddyfile）——exact_source_verified=False 如实记录，**绝不
    虚报精确验证**。事实只含脱敏字段（basename），绝不持久化绝对路径。
    漂移以 raise GatewayError(CATEGORY_DRIFT, ...) 上抛。
    """
    ok, mounts = _inspect_json(runner, CONTAINER_NAME, "{{json .Mounts}}")
    if not ok or not isinstance(mounts, list):
        raise GatewayError(CATEGORY_DOCKER, "网关容器 Mounts 不可查询/畸形")
    matched = [m for m in mounts if isinstance(m, dict)
               and m.get("Destination") == CADDYFILE_MOUNT_DESTINATION]
    if len(matched) != 1:
        raise GatewayError(CATEGORY_DRIFT, f"Caddyfile 挂载条目数 != 1（目标 {CADDYFILE_MOUNT_DESTINATION}）")
    mount = matched[0]
    source = str(mount.get("Source") or "")
    readonly = mount.get("RW") is False
    if (mount.get("Type") != "bind" or not readonly
            or Path(source).name != "Caddyfile"):
        raise GatewayError(
            CATEGORY_DRIFT,
            "Caddyfile 挂载形状不符（必须 bind + 只读 + source 名 Caddyfile）",
        )
    exact_source_verified = False
    if expected_caddyfile is not None:
        if source.replace("\\", "/") != str(expected_caddyfile).replace("\\", "/"):
            raise GatewayError(
                CATEGORY_DRIFT, "Caddyfile 挂载 source 与渲染产物不一致（bind 漂移）"
            )
        exact_source_verified = True
    return {"type": mount.get("Type"), "destination": CADDYFILE_MOUNT_DESTINATION,
            "readonly": readonly, "source_basename": "Caddyfile",
            "exact_source_verified": exact_source_verified}


def gateway_facts(runner: Runner, host_port: int,
                  expected_caddyfile: Path | None = None) -> tuple[str, dict[str, Any] | None]:
    """网关容器分类与事实：missing / installed / degraded / foreign。

    foreign = 所有权标签不精确属本工具；degraded = 自有但镜像/网络/端口
    绑定/重启策略/挂载形状任一漂移；installed = 全部匹配（挂载 source 的
    **精确**验证仅在 expected_caddyfile 给出时成立——见 _mount_facts）。
    """
    ok, image = _inspect_json(runner, CONTAINER_NAME, "{{json .Config.Image}}")
    if not ok:
        return STATUS_MISSING, None
    if image == "query-failed":
        raise GatewayError(CATEGORY_DOCKER, "网关容器查询失败（不猜测状态）")
    fields: dict[str, Any] = {"image": image if isinstance(image, str) else None}
    for key, fmt in (
        ("labels", "{{json .Config.Labels}}"),
        ("networks", "{{json .NetworkSettings.Networks}}"),
        ("ports", "{{json .HostConfig.PortBindings}}"),
        ("restart", "{{json .HostConfig.RestartPolicy}}"),
    ):
        ok, value = _inspect_json(runner, CONTAINER_NAME, fmt)
        if not ok or not isinstance(value, (dict, str)):
            raise GatewayError(CATEGORY_DOCKER, f"网关容器字段 {key} 不可查询/畸形")
        fields[key] = value
    labels = fields["labels"] if isinstance(fields["labels"], dict) else {}
    if any(labels.get(k) != v for k, v in OWNERSHIP_LABELS.items()):
        return STATUS_FOREIGN, fields
    try:
        fields["mount"] = _mount_facts(runner, expected_caddyfile)
    except GatewayError as cause:
        if cause.category == CATEGORY_DOCKER:
            raise  # 查询层失败不吞——不猜测状态
        return STATUS_DEGRADED, {**fields, "drift": ["bind"], "mount_error": str(cause)}
    networks = fields["networks"] if isinstance(fields["networks"], dict) else {}
    ports = fields["ports"] if isinstance(fields["ports"], dict) else {}
    restart = fields["restart"] if isinstance(fields["restart"], dict) else {}
    expected_ports = {CONTAINER_HTTP_PORT: [{"HostIp": LOOPBACK, "HostPort": str(host_port)}]}
    drift: list[str] = []
    if image != CADDY_IMAGE:
        drift.append("image")
    if set(networks) != {PRODUCTION_NETWORK}:
        drift.append("network")
    if ports != expected_ports:
        drift.append("bind/port")
    if restart.get("Name") != RESTART_POLICY:
        drift.append("restart")
    if drift:
        return STATUS_DEGRADED, {**fields, "drift": drift}
    return STATUS_INSTALLED, fields


# ---------------------------------------------------------------- 证据


EVIDENCE_JSON_NAME = "gateway-evidence.json"
EVIDENCE_MD_NAME = "gateway-report.md"


def new_report(command: str, host_port: int) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "tool": TOOL_NAME,
        "command": command,
        "container": CONTAINER_NAME,
        "network": PRODUCTION_NETWORK,
        "upstream": UPSTREAM,
        "host_port": host_port,
        "ingress": f"{LOOPBACK}:{host_port}",
        "image": CADDY_IMAGE,
        "labels": dict(OWNERSHIP_LABELS),
        "restart_policy": RESTART_POLICY,
        "stages": [],
        "container_facts": {},
        "web_container": PRODUCTION_WEB_CONTAINER,
        "result": "fail",
        "production_public_ready": False,
        "rollback": (
            f"uninstall：python tools/ops/{TOOL_NAME}.py uninstall "
            f'--confirm-phrase "{UNINSTALL_CONFIRM_PHRASE}" --execute'
            "（只删网关容器；配置目录与证据保留；生产栈零触碰）"
        ),
        "boundary": "本地回环恢复路径（Caddy→web:3000 over 生产 compose 网络）——"
                    "不修复 Docker Desktop、不暴露公网流量、不改生产边缘设计。",
    }


def build_markdown(report: dict[str, Any]) -> str:
    stages = "\n".join(f"- {s['name']}: {s['status']}（{s['detail']}）" for s in report["stages"])
    facts = report.get("container_facts") or {}
    drift = facts.get("drift")
    return f"""# M14-157 生产 Web 回环网关报告（{report['command']}）

- schema：{report['schema']}（由 {report['tool']} 生成；result = **{report['result']}**）
- 网关：容器 `{report['container']}` on `{report['network']}`，
  `{report['ingress']}` → `{report['upstream']}`（HTTP-only，回环独占）
- 镜像：`{report['image']}`（pinned digest）；
  标签 `{report['labels']}`；restart `{report['restart_policy']}`
- 生产 Web 上游容器：`{report['web_container']}`（install 前置 healthy）
- host 端口：{report['host_port']}（默认 {DEFAULT_HOST_PORT}；封锁集含
  3011/8000/39443 与生产 compose 全部 host 发布位）

## 阶段

{stages}

## 容器事实（装后 inspect）

image={facts.get('image', 'n/a')}；networks={sorted(facts.get('networks', {}) or [])}；
ports={facts.get('ports', 'n/a')}；restart={facts.get('restart', 'n/a')}
mount={facts.get('mount', 'n/a')}
（mount 只记脱敏事实：source basename + 是否已精确核对；本机绝对路径绝不入证据）
{f"；drift={drift}" if drift else ""}

## 回滚

{report['rollback']}

## 诚实边界

{report['boundary']} `production_public_ready = {report['production_public_ready']}`。
"""


def write_evidence(evidence_dir: Path, report: dict[str, Any]) -> tuple[str, str]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        evidence_dir / EVIDENCE_JSON_NAME,
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        0o644,
    )
    _atomic_write(evidence_dir / EVIDENCE_MD_NAME, build_markdown(report), 0o644)
    return EVIDENCE_JSON_NAME, EVIDENCE_MD_NAME


# ---------------------------------------------------------------- 子命令


def _print_plan(log, host_port: int) -> None:
    log("plan（零写入零探测零 Docker）——真实执行需 install/uninstall + 精确短语：")
    log(f"  问题: 生产 Web 容器健康但既有 host 映射 {LOOPBACK}:3011 对 Docker Desktop 空应答")
    log(f"  网关: pinned Caddy 容器 {CONTAINER_NAME} 加入 {PRODUCTION_NETWORK}")
    log(f"  拓扑: {LOOPBACK}:{host_port} → Caddy :80 → {UPSTREAM}（HTTP-only，回环独占）")
    log(f"  镜像: {CADDY_IMAGE}")
    log(f"  标签: {OWNERSHIP_LABELS} / restart {RESTART_POLICY}")
    log(f"  端口: 默认 {DEFAULT_HOST_PORT}（封锁集 3011/8000/39443 + 生产 compose host 位）")
    log("  序列: preflight(只读五门) → 渲染(仓库外 config-dir + 原子写自检)")
    log("        → docker run → 装后 inspect 精确复核 → HTTP GET / 必须 200 → 证据 JSON+MD")
    log(f"  执行: python tools/ops/{TOOL_NAME}.py install --config-dir <绝对路径仓库外> \\")
    log(f'        --confirm-phrase "{INSTALL_CONFIRM_PHRASE}" --execute')
    log(f"  回收: python tools/ops/{TOOL_NAME}.py uninstall \\")
    log(f'        --confirm-phrase "{UNINSTALL_CONFIRM_PHRASE}" --execute')
    log("  边界: 本地回环恢复路径——不修复 Docker Desktop、不暴露公网流量、")
    log("        不重启/不重建生产栈；production_public_ready=false 恒不变")


def cmd_plan(args: argparse.Namespace, log) -> int:
    _print_plan(log, args.host_port)
    return EXIT_OK


def cmd_status(runner: Runner, args: argparse.Namespace, log) -> int:
    docker_available(runner)
    require_production_network(runner)
    expected = None
    if (args.config_dir or "").strip():
        expected = validate_config_dir(args.config_dir) / "Caddyfile"
    status, facts = gateway_facts(runner, args.host_port, expected)
    log(f"status: {status}")
    ok, state = _inspect_json(runner, PRODUCTION_WEB_CONTAINER, "{{json .State}}")
    web_note = "n/a"
    if ok and isinstance(state, dict):
        web_note = f"running={state.get('Running')} health={(state.get('Health') or {}).get('Status')}"
    log(f"  生产 Web 容器（只读）: {web_note}")
    if facts:
        drift = facts.get("drift")
        log(f"  image={facts.get('image')}")
        log(f"  networks={sorted(facts.get('networks', {}) or [])}")
        log(f"  ports={facts.get('ports')}")
        mount = facts.get("mount")
        if isinstance(mount, dict):
            verified = mount.get("exact_source_verified")
            note = "已精确验证" if verified else "未精确验证（形状校验通过；加 --config-dir 可精确核对 source）"
            log(f"  mount: {mount.get('type')} → {mount.get('destination')} readonly="
                f"{mount.get('readonly')} source_basename={mount.get('source_basename')}（{note}）")
        if facts.get("mount_error"):
            log(f"  mount 漂移: {facts.get('mount_error')}")
        if drift:
            log(f"  drift={drift}（先 uninstall 再 install 修正）")
    log("status 为只读分类；变更需 install/uninstall（短语 + --execute）")
    return EXIT_OK


def cmd_install(args: argparse.Namespace, runner: Runner, prober: Prober, log) -> int:
    if args.confirm_phrase != INSTALL_CONFIRM_PHRASE or not args.execute:
        print(
            f"[{TOOL_NAME}] FAIL {CATEGORY_CONFIRM}: install 需同时满足 --execute 与精确确认短语"
            f"（缺一即 fail-closed；只读计划见 plan 子命令）",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    report = new_report("install", args.host_port)
    failure: GatewayError | None = None
    evidence_dir = resolve_evidence_dir(args.evidence_dir)
    stages = report["stages"]

    def pass_stage(name: str, detail: str) -> None:
        stages.append({"name": name, "status": "pass", "detail": detail})
        log(f"stage {name}: pass（{detail}）")

    def fail_stage(name: str, detail: str) -> None:
        stages.append({"name": name, "status": "fail", "detail": detail})

    config_dir: Path | None = None
    try:
        config_dir = validate_config_dir(args.config_dir)
        docker_available(runner)
        pass_stage("docker-cli", "docker --version 可用")
        require_production_network(runner)
        pass_stage("network", f"{PRODUCTION_NETWORK} 存在")
        web = require_production_web_healthy(runner)
        report["web_facts"] = web
        pass_stage("web-healthy", f"{PRODUCTION_WEB_CONTAINER} running+healthy")
        if not prober.port_free(LOOPBACK, args.host_port):
            raise GatewayError(CATEGORY_PORT, f"host 端口 {LOOPBACK}:{args.host_port} 已被占用")
        pass_stage("host-port-free", f"{LOOPBACK}:{args.host_port} 空闲")
        status, facts = gateway_facts(runner, args.host_port)
        if status != STATUS_MISSING:
            label = {STATUS_INSTALLED: "已存在（本工具安装）", STATUS_DEGRADED: "已存在（自有但配置漂移）",
                     STATUS_FOREIGN: "已存在（非本工具归属）"}.get(status, status)
            raise GatewayError(
                CATEGORY_EXISTS, f"同名容器 {CONTAINER_NAME} {label}——绝不覆盖；先 uninstall"
            )
        pass_stage("no-existing-container", f"{CONTAINER_NAME} 不存在")
        written = render_caddyfile(config_dir)
        validate_caddy_config((config_dir / "Caddyfile").read_text(encoding="utf-8"))
        pass_stage("render", f"{written} 渲染 + 自检通过（HTTP-only :80 → {UPSTREAM}）")
        run_cmd = [
            "docker", "run", "-d",
            "--name", CONTAINER_NAME,
            "--network", PRODUCTION_NETWORK,
            "--publish", f"{LOOPBACK}:{args.host_port}:80",
            "--label", f"io.aios.managed-by={OWNERSHIP_LABELS['io.aios.managed-by']}",
            "--label", f"io.aios.milestone={OWNERSHIP_LABELS['io.aios.milestone']}",
            "--restart", RESTART_POLICY,
            "--mount", f"type=bind,src={config_dir / 'Caddyfile'},dst=/etc/caddy/Caddyfile,readonly",
            CADDY_IMAGE,
        ]
        code, _out = runner.run(run_cmd)
        if code != 0:
            raise GatewayError(CATEGORY_INSTALL, f"docker run 失败（returncode={code}，输出不回显）")
        pass_stage("docker-run", f"{CONTAINER_NAME} 已创建（pinned 镜像 + 所有权标签）")
        # R1 修正 1：装后校验必须**精确**核对 Caddyfile bind source（期望
        # = 渲染产物绝对路径）；证据只记脱敏事实（basename +
        # exact_source_verified），绝不持久化本机绝对路径
        status, facts = gateway_facts(runner, args.host_port, config_dir / "Caddyfile")
        mount = (facts or {}).get("mount") or {}
        if status != STATUS_INSTALLED or mount.get("exact_source_verified") is not True:
            drift = (facts or {}).get("drift", ["unknown"])
            report["container_facts"] = facts or {}  # 失败路径也记录脱敏事实（drift/mount_error）
            raise GatewayError(
                CATEGORY_VERIFY,
                f"装后校验失败（status={status}，drift={drift}，"
                f"exact_source_verified={mount.get('exact_source_verified')}）——"
                f"不自动删除，留 supervisor 处置",
            )
        report["container_facts"] = facts
        report["exact_source_verified"] = True
        pass_stage("verify-inspect", "image/labels/network/bind/port/restart/mount 精确匹配")
        probe_status, _body = prober.http_get(f"http://{LOOPBACK}:{args.host_port}/", {}, 10.0)
        if probe_status != 200:
            raise GatewayError(
                CATEGORY_PROBE, f"装后 HTTP GET / 返回 {probe_status}（期望 200）——不自动删除"
            )
        report["probe"] = {"path": "/", "status_code": probe_status, "ok": True}
        pass_stage("verify-http", f"GET http://{LOOPBACK}:{args.host_port}/ = 200")
        report["result"] = "pass"
    except GatewayError as cause:
        failure = cause
        fail_stage(cause.category, str(cause))
    except Exception as cause:  # noqa: BLE001 —— 兜底：任何意外异常也必须走证据路径
        failure = GatewayError(CATEGORY_INTERNAL, f"{type(cause).__name__}: {cause}")
        fail_stage(CATEGORY_INTERNAL, str(cause))

    evidence_failure: str | None = None
    report["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stages.append({"name": "evidence", "status": "pass",
                   "detail": f"{EVIDENCE_JSON_NAME} + {EVIDENCE_MD_NAME}"})
    try:
        write_evidence(evidence_dir, report)
        log(f"evidence: {EVIDENCE_JSON_NAME} + {EVIDENCE_MD_NAME}（原子写）")
    except (GatewayError, OSError) as cause:
        stages[-1]["status"] = "fail"
        evidence_failure = f"{type(cause).__name__}: {cause}"
        log(f"evidence 写出失败: {evidence_failure}")

    if failure is not None:
        print(f"[{TOOL_NAME}] FAIL {failure.category}: {failure}", file=sys.stderr)
        return EXIT_FAILURE
    if evidence_failure is not None:
        print(f"[{TOOL_NAME}] FAIL {CATEGORY_INSTALL}: 证据写出失败: {evidence_failure}", file=sys.stderr)
        return EXIT_FAILURE
    log(f"INSTALL OK（{LOOPBACK}:{args.host_port} → {UPSTREAM}；本地回环恢复路径——不暴露公网流量）")
    return EXIT_OK


def cmd_uninstall(args: argparse.Namespace, runner: Runner, log) -> int:
    if args.confirm_phrase != UNINSTALL_CONFIRM_PHRASE or not args.execute:
        print(
            f"[{TOOL_NAME}] FAIL {CATEGORY_CONFIRM}: uninstall 需同时满足 --execute 与精确确认短语"
            f"（缺一即 fail-closed，零变更）",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    docker_available(runner)
    status, _facts = gateway_facts(runner, args.host_port)
    if status == STATUS_MISSING:
        log(f"网关容器 {CONTAINER_NAME} 不存在（幂等：无需卸载；配置目录与证据不动）")
        return EXIT_OK
    if status == STATUS_FOREIGN:
        print(
            f"[{TOOL_NAME}] FAIL {CATEGORY_UNINSTALL}: 容器 {CONTAINER_NAME} 非本工具精确归属"
            f"（所有权标签不匹配）——绝不删除外来容器",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    code, _out = runner.run(["docker", "rm", "-f", CONTAINER_NAME])
    if code != 0:
        print(f"[{TOOL_NAME}] FAIL {CATEGORY_UNINSTALL}: docker rm 失败（returncode={code}）",
              file=sys.stderr)
        return EXIT_FAILURE
    log(f"UNINSTALL OK（只删 {CONTAINER_NAME}；配置目录与证据保留；生产栈零触碰）")
    return EXIT_OK


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None, *, runner: Runner | None = None,
         prober: Prober | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "生产 Web 回环网关 supervisor（plan/status/install/uninstall；fail-closed）。"
            "默认 plan 零写入零探测零 Docker；install/uninstall 需 --execute + 精确"
            "确认短语；本地回环恢复路径——不修复 Docker Desktop、不暴露公网流量，"
            "production_public_ready=false 恒不变。"
        ),
    )
    parser.add_argument("command", nargs="?", default="plan",
                        choices=["plan", "status", "install", "uninstall"],
                        help="子命令（默认 plan，零变更）")
    parser.add_argument("--config-dir", help="网关配置目录（绝对路径，仓库外；install 必需）")
    parser.add_argument("--host-port", type=str, default=None,
                        help=f"host 发布端口（1..65535，默认 {DEFAULT_HOST_PORT}；"
                             f"封锁 3011/8000/39443 与生产 compose host 位）")
    parser.add_argument("--confirm-phrase", help="执行确认短语（见 plan 输出/文档）")
    parser.add_argument("--execute", action="store_true", help="真实执行（默认拒绝一切变更）")
    parser.add_argument("--evidence-dir", type=str, default=None,
                        help=f"证据目录（默认仓库 gitignored {DEFAULT_EVIDENCE_DIR}）")
    args = parser.parse_args(argv)
    if args.command == "install" and not (args.config_dir or "").strip():
        parser.error("install 需要 --config-dir（绝对路径，仓库外）")
    if runner is None:
        runner = RealRunner()
    if prober is None:
        prober = RealProber()

    def log(message: str) -> None:
        print(f"[{TOOL_NAME}] {message}", flush=True)

    try:
        # 端口解析先于一切分发/探测——畸形/封锁端口即 port-invalid fail-closed
        args.host_port = parse_host_port(args.host_port)
        if args.command == "plan":
            return cmd_plan(args, log)
        if args.command == "status":
            return cmd_status(runner, args, log)
        if args.command == "install":
            return cmd_install(args, runner, prober, log)
        return cmd_uninstall(args, runner, log)
    except GatewayError as cause:
        print(f"[{TOOL_NAME}] FAIL {cause.category}: {cause}", file=sys.stderr)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
