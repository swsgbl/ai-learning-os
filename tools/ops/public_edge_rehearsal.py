#!/usr/bin/env python
"""M14-156 公网边缘本地彩排 supervisor：plan / execute / cleanup / status。

把 M14-153/154/155 的可渲染部署包推进为**可执行的本地回环彩排**：
probe → Caddy(127.0.0.1:39443) → frps vhost → frpc → host.docker.internal
的 API(8000)/Web(3012，M14-157 生产 Web 回环网关)——在单机上真实跑通 Caddy → frps → frpc → 家机
服务的 HTTP 反向隧道链路。**这不是公网部署**：HTTP-only、loopback-only、
无域名/证书/ACME/LiveKit/coturn，彩排通过 ≠ 公网生产可用。

Fail-closed 契约（承接 M14-153/154/155 纪律）：
- 默认 plan（零变更零探测零 Docker）；execute 需 **同时**满足
  ``--execute`` 与精确短语 ``EXECUTE PUBLIC EDGE REHEARSAL``，cleanup 需
  **同时**满足 ``--execute`` 与精确短语 ``CLEANUP PUBLIC EDGE REHEARSAL``
  ——缺一即 fail-closed（稳定错误类别 confirm-gate + 非零退出）；
- Docker 只经注入 Runner 边界（测试注入 FakeRunner，绝不触碰真实
  Docker）；HTTP/端口探测经注入 Prober 边界（测试零网络）；
- execute 前只读 preflight：docker CLI 与 compose 子命令可用、家机位
  API/Web 目标可达（默认 ``http://127.0.0.1:8000/health`` 与
  ``http://127.0.0.1:3012``（M14-157 回环网关），R2 可经 ``--host-api-port``/``--host-web-port``
  覆写为 1..65535 临时健康端口——仅限本地 Docker stale-port-forward
  绕行，host.docker.internal 固定不变，畸形/越界/相同端口一律
  port-invalid fail-closed；证据显式记录两端口与是否默认，覆写端口上
  的 pass 不构成对默认映射的验证）、唯一 host 发布端口 39443 空闲、
  work-dir 契约成立；
- work-dir 契约（确定性）：必须在仓库外、供给路径链中任何已存在组件
  均不得是符号链接（缺省叶/父目录合法）、非文件系统根；
  已存在时必须是纯目录且**只含本工具产物名**（docker-compose.yml/
  frps.toml/frpc.toml/Caddyfile/frps_token.txt——幂等覆写），出现任何
  其他条目/子目录/符号链接一律拒绝，绝不盲删无关内容；
- 一次性 token：secrets.token_hex(32) 只写进 work-dir 的 0600 文件
  （frps/frpc 以只读挂载共享同值同源文件），**绝不打印**、绝不进入
  日志/证据/命令行；证据只记长度事实；
- compose project 恒为 ``aios-m14-156-edge-rehearsal``（compose 文件
  ``name:`` 与 ``-p`` 双保险），服务带 ``io.aios.rehearsal=m14-156``
  标签；host 面唯一发布 ``127.0.0.1:39443:80``（回环+高位），frps 控制
  面/vhost 均不发布；Host 路由标签恒为 app.rehearsal.localhost /
  api.rehearsal.localhost（RFC 6761 保留域，永不解析）；
- scoped 清理（execute 无论成败恒定执行，cleanup 子命令可独立重放）：
  只按 ``label=com.docker.compose.project=aios-m14-156-edge-rehearsal``
  精确过滤本项目容器/网络，绝不触碰 aios-m14-03-production-rehearsal
  或任何其他项目；work-dir 只在通过上述契约校验后删除；
- 证据 rehearsal-evidence.json + rehearsal-report.md 原子写（同目录
  temp+replace；失败保真不落半成品、不留 .tmp），成功与失败路径都
  落盘；证据不含 secret 值、不含本机绝对路径；``public_ready`` 恒
  false（诚实边界）。

Exit codes：0 = 成功（含 plan/status 只读输出）；1 = 失败（稳定类别
confirm-gate / work-dir-unsafe / evidence-dir-unsafe / port-invalid /
docker-unavailable / target-unreachable / port-busy / render-failed /
compose-up-failed / probe-failed / cleanup-failed / evidence-write-failed /
internal-error）；2 = 用法错误（argparse）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import tomllib

TOOL_NAME = "public_edge_rehearsal"
SCHEMA = "aios-public-edge-rehearsal/1"
REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "infra" / "edge" / "rehearsal"
DEFAULT_EVIDENCE_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-156-edge-rehearsal"

COMPOSE_PROJECT = "aios-m14-156-edge-rehearsal"
EXECUTE_CONFIRM_PHRASE = "EXECUTE PUBLIC EDGE REHEARSAL"
CLEANUP_CONFIRM_PHRASE = "CLEANUP PUBLIC EDGE REHEARSAL"
# 其他项目的保护性断言（清理边界测试/运行期自检引用，绝不操作它们）
FORBIDDEN_PROJECTS = ("aios-m14-03-production-rehearsal",)

LOOPBACK = "127.0.0.1"
INGRESS_PORT = 39443  # 唯一 host 发布端口（回环 + 高位；Caddy 容器 :80）
FRPS_BIND_PORT = 7000  # compose 网络内控制面（不发布到 host）
FRPS_VHOST_PORT = 8080  # compose 网络内 vhost（不发布到 host）
APP_HOST = "app.rehearsal.localhost"
API_HOST = "api.rehearsal.localhost"
HOST_API_PORT = 8000  # 默认 host.docker.internal 目标：家机 API 同位 loopback
HOST_WEB_PORT = 3012  # M14-158：家机 Web 目标 = M14-157 生产 Web 回环网关
                      # （127.0.0.1:3012 → Caddy :80 → 生产 web:3000，已验证 200；
                      #  既有 3011 host 映射对 Docker Desktop 已知 stale，不再作为目标）
HOST_GATEWAY = "host.docker.internal"
TOKEN_BYTES = 32
TOKEN_FILE_NAME = "frps_token.txt"
COMPOSE_LABEL_KEY = "io.aios.rehearsal"
COMPOSE_LABEL_VALUE = "m14-156"
PROJECT_LABEL_FILTER = f"label=com.docker.compose.project={COMPOSE_PROJECT}"

TEMPLATE_NAMES = {
    "docker-compose.yml": "docker-compose.rehearsal.example.yml",
    "frps.toml": "frps.rehearsal.toml.example",
    "frpc.toml": "frpc.rehearsal.toml.example",
    "Caddyfile": "Caddyfile.rehearsal.example",
}
REHEARSAL_ARTIFACTS = frozenset(TEMPLATE_NAMES) | {TOKEN_FILE_NAME}
HIGH_PORT_MIN = 1024

EXIT_OK = 0
EXIT_FAILURE = 1

CATEGORY_CONFIRM = "confirm-gate"
CATEGORY_WORK_DIR = "work-dir-unsafe"
CATEGORY_EVIDENCE_DIR = "evidence-dir-unsafe"
CATEGORY_DOCKER = "docker-unavailable"
CATEGORY_TARGET = "target-unreachable"
CATEGORY_PORT = "port-busy"
CATEGORY_PORT_ARG = "port-invalid"
CATEGORY_RENDER = "render-failed"
CATEGORY_COMPOSE = "compose-up-failed"
CATEGORY_PROBE = "probe-failed"
CATEGORY_CLEANUP = "cleanup-failed"
CATEGORY_EVIDENCE = "evidence-write-failed"
CATEGORY_INTERNAL = "internal-error"


class RehearsalError(Exception):
    """fail-closed 错误：携带稳定类别码；文本不含 secret/绝对路径。"""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class Runner(Protocol):
    def run(self, args: list[str]) -> tuple[int, str]:
        """执行平台命令，返回 (returncode, 合并输出文本)。"""


class RealRunner:
    """真实 Runner：只与 Docker CLI 交互；Windows 下 CREATE_NO_WINDOW。

    docker 未安装/不可执行（FileNotFoundError 等 OSError）统一转
    returncode=127——调用方按 docker-unavailable 类别 fail-closed，
    绝不让原始异常带路径文本外溢。
    """

    def run(self, args: list[str]) -> tuple[int, str]:
        import subprocess

        creationflags = 0x08000000 if os.name == "nt" else 0
        try:
            result = subprocess.run(
                args, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=900, check=False, creationflags=creationflags,
            )
        except OSError as cause:
            return 127, type(cause).__name__
        return result.returncode, (result.stdout or "") + (result.stderr or "")


class Prober(Protocol):
    def http_get(self, url: str, headers: dict[str, str], timeout: float) -> tuple[int, str]:
        """HTTP GET（禁用系统代理），返回 (状态码, 有界响应体)。"""

    def port_free(self, host: str, port: int) -> bool:
        """端口空闲判定（无监听 = True）。"""


class RealProber:
    """真实 Prober：直连（显式禁代理）+ socket connect_ex 空闲判定。"""

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
            # 只透出异常类别——彩排 URL 是本工具固定常量（回环+高位端口），
            # 但保持与 M14-153 一致的原串剥离纪律
            raise RehearsalError(
                CATEGORY_TARGET, f"探测连接失败: {type(cause).__name__}"
            ) from cause

    def port_free(self, host: str, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(2.0)
            return sock.connect_ex((host, port)) != 0


# ---------------------------------------------------------------- 目录防线


def _is_inside(path: Path, root: Path) -> bool:
    return path == root or str(path).startswith(str(root) + os.sep)


def _reject_symlink_in_chain(expanded: Path, category: str, label: str) -> None:
    """R1 路径链防线：供给路径的**任何已存在组件**都不得是符号链接。

    逐组件检查先于 resolve()（resolve 会跟随链接，先解析会把链中符号链接
    藏掉——与 prepare R4 同款纪律）。首个不存在的组件之后的尾部本就不存在，
    无从构成符号链接，直接放行（缺省叶/缺省父目录合法）。
    """
    parts = expanded.parts
    current = Path(parts[0]) if expanded.is_absolute() else Path(".")
    rest = parts[1:] if expanded.is_absolute() else parts
    for part in rest:
        current = current / part
        if current.is_symlink():
            raise RehearsalError(category, f"{label} 路径链中存在符号链接组件（拒绝）")
        if not current.exists():
            return  # 尾部组件不存在——合法的缺省叶/父目录


def validate_work_dir(raw: str | None) -> Path:
    """work-dir 契约：仓库外、路径链无符号链接、非根；已存在时只含本工具产物名。"""
    if not raw or not raw.strip():
        raise RehearsalError(CATEGORY_WORK_DIR, "--work-dir 必须显式提供（仓库外目录）")
    candidate = Path(raw.strip()).expanduser()
    _reject_symlink_in_chain(candidate, CATEGORY_WORK_DIR, "work-dir")
    resolved = candidate.resolve()
    if resolved == Path(resolved.anchor):
        raise RehearsalError(CATEGORY_WORK_DIR, "work-dir 不得是文件系统根目录")
    if _is_inside(resolved, REPO_ROOT):
        raise RehearsalError(CATEGORY_WORK_DIR, "work-dir 不得位于仓库内（彩排目录必须在仓库外）")
    if resolved.exists():
        if not resolved.is_dir():
            raise RehearsalError(CATEGORY_WORK_DIR, "work-dir 路径存在但不是目录")
        unexpected = sorted(p.name for p in resolved.iterdir() if p.name not in REHEARSAL_ARTIFACTS)
        if unexpected:
            raise RehearsalError(
                CATEGORY_WORK_DIR,
                f"work-dir 含非彩排条目 {unexpected}（拒绝盲写/盲删无关目录；"
                f"只允许本工具产物名幂等覆写）",
            )
        for entry in resolved.iterdir():
            if entry.is_symlink() or not entry.is_file():
                raise RehearsalError(CATEGORY_WORK_DIR, f"work-dir 条目 {entry.name} 必须是常规文件")
    return resolved


def resolve_evidence_dir(raw: str | None) -> Path:
    """证据目录：仓库外或仓库 gitignored .verify/ 之下；路径链无符号链接。"""
    if raw is None or not raw.strip():
        return DEFAULT_EVIDENCE_DIR
    candidate = Path(raw.strip()).expanduser()
    _reject_symlink_in_chain(candidate, CATEGORY_EVIDENCE_DIR, "--evidence-dir")
    resolved = candidate.resolve()
    if resolved == Path(resolved.anchor):
        raise RehearsalError(CATEGORY_EVIDENCE_DIR, "--evidence-dir 不得是文件系统根目录")
    verify_root = (REPO_ROOT / ".verify").resolve()
    if _is_inside(resolved, REPO_ROOT) and not _is_inside(resolved, verify_root):
        raise RehearsalError(
            CATEGORY_EVIDENCE_DIR,
            "--evidence-dir 不得位于仓库 tracked 区（只允许仓库外或 .verify/ 之下）",
        )
    return resolved


# ---------------------------------------------------------------- 目标端口解析（R2）


def parse_host_port(raw: str | None, default: int, label: str) -> int:
    """--host-api-port/--host-web-port 解析：1..65535 纯数字整数，缺省用默认。

    R2：只允许端口数字——绝不提供 host/address/URL 覆写（host.docker.internal
    固定）；畸形/非数字/越界一律 port-invalid 类别 fail-closed（无 traceback，
    错误不回显原始输入——杜绝把 URL 片段注入任何探测/渲染面）。
    """
    if raw is None:
        return default
    candidate = raw.strip()
    if not re.fullmatch(r"[0-9]{1,5}", candidate) or not 1 <= int(candidate) <= 65535:
        raise RehearsalError(CATEGORY_PORT_ARG, f"{label} 必须是 1..65535 的十进制整数端口")
    return int(candidate)


def validate_target_ports(api_port: int, web_port: int) -> None:
    """两目标端口必须互异（家机 API/Web 是两个独立 loopback 服务）。"""
    if api_port == web_port:
        raise RehearsalError(
            CATEGORY_PORT_ARG, f"API/Web 目标端口不得相同（收到 {api_port}）"
        )


def resolve_target_ports(args: argparse.Namespace) -> tuple[int, int]:
    api_port = parse_host_port(args.host_api_port, HOST_API_PORT, "--host-api-port")
    web_port = parse_host_port(args.host_web_port, HOST_WEB_PORT, "--host-web-port")
    validate_target_ports(api_port, web_port)
    return api_port, web_port


def defaults_in_use(api_port: int, web_port: int) -> bool:
    return api_port == HOST_API_PORT and web_port == HOST_WEB_PORT


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


def render_rehearsal(work_dir: Path, token: str, api_port: int = HOST_API_PORT,
                     web_port: int = HOST_WEB_PORT) -> list[str]:
    """渲染彩排配置：四模板复制（frpc 按目标端口注入）+ 一次性 token 落 0600 文件。

    token 只进文件（frps/frpc 只读挂载共享同值），绝不进入任何模板文本、
    日志或证据。R2：frpc 的两个 localPort 由配置端口精确替换（每处必须恰替换
    一次，替换计数异常即 render-failed——防模板漂移）；host.docker.internal
    固定不变（无 host/address/URL 覆写面）。返回写出的文件名列表（确定性顺序）。
    """
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise RehearsalError(CATEGORY_RENDER, "token 必须是 64 位 hex（一次性随机材料）")
    written: list[str] = []
    for target_name, template_name in TEMPLATE_NAMES.items():
        try:
            content = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as cause:
            raise RehearsalError(
                CATEGORY_RENDER, f"彩排模板不可读: {template_name}: {type(cause).__name__}"
            ) from cause
        if target_name == "frpc.toml":
            for default_port, configured in ((HOST_WEB_PORT, web_port), (HOST_API_PORT, api_port)):
                needle, replacement = f"localPort = {default_port}", f"localPort = {configured}"
                if content.count(needle) != 1:
                    raise RehearsalError(
                        CATEGORY_RENDER,
                        f"frpc 模板 localPort = {default_port} 出现次数 != 1（模板漂移拒绝渲染）",
                    )
                content = content.replace(needle, replacement)
        _atomic_write(work_dir / target_name, content, 0o644)
        written.append(target_name)
    _atomic_write(work_dir / TOKEN_FILE_NAME, token + "\n", 0o600)
    written.append(TOKEN_FILE_NAME)
    return written


def validate_rendered_configs(work_dir: Path, api_port: int = HOST_API_PORT,
                              web_port: int = HOST_WEB_PORT) -> None:
    """渲染自检（compose up 之前）：全部隔离/安全不变量 fail-closed 复核。"""
    try:
        frps = tomllib.loads((work_dir / "frps.toml").read_text(encoding="utf-8"))
        frpc = tomllib.loads((work_dir / "frpc.toml").read_text(encoding="utf-8"))
        caddy = (work_dir / "Caddyfile").read_text(encoding="utf-8")
        compose = (work_dir / "docker-compose.yml").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as cause:
        raise RehearsalError(CATEGORY_RENDER, f"彩排产物不可读: {type(cause).__name__}") from cause
    except tomllib.TOMLDecodeError as cause:
        raise RehearsalError(CATEGORY_RENDER, f"彩排 TOML 不可解析: {cause}") from cause

    # frps：与生产同款 token-from-file + TLS force；端口不发布（compose 侧断言）
    if frps.get("bindPort") != FRPS_BIND_PORT or frps.get("vhostHTTPPort") != FRPS_VHOST_PORT:
        raise RehearsalError(CATEGORY_RENDER, "frps 端口不变量破坏（7000/8080）")
    auth = frps.get("auth", {})
    if auth.get("tokenSource", {}).get("type") != "file" or "token" in auth:
        raise RehearsalError(CATEGORY_RENDER, "frps 认证不变量破坏（token 只允许文件引用）")
    if frps.get("transport", {}).get("tls", {}).get("force") is not True:
        raise RehearsalError(CATEGORY_RENDER, "frps transport.tls.force != true")

    # frpc：TLS + token 文件；两代理精确锁定彩排标签与 host 回连目标
    frpc_auth = frpc.get("auth", {})
    if frpc_auth.get("tokenSource", {}).get("type") != "file" or "token" in frpc_auth:
        raise RehearsalError(CATEGORY_RENDER, "frpc 认证不变量破坏（token 只允许文件引用）")
    if frpc.get("transport", {}).get("tls", {}).get("enable") is not True:
        raise RehearsalError(CATEGORY_RENDER, "frpc transport.tls.enable != true")
    if frpc.get("serverPort") != FRPS_BIND_PORT or frpc.get("serverAddr") != "frps":
        raise RehearsalError(CATEGORY_RENDER, "frpc serverAddr/serverPort 不变（compose 网络内 frps:7000）")
    proxies = frpc.get("proxies", [])
    if len(proxies) != 2 or not all(p.get("type") == "http" for p in proxies):
        raise RehearsalError(CATEGORY_RENDER, "frpc 代理必须是恰好两个 http 代理")
    domains = {d for p in proxies for d in p.get("customDomains", [])}
    if domains != {APP_HOST, API_HOST}:
        raise RehearsalError(CATEGORY_RENDER, f"frpc customDomains 必须恰为彩排双标签: {sorted(domains)}")
    targets = {(str(p.get("localIP")), p.get("localPort")) for p in proxies}
    if targets != {(HOST_GATEWAY, web_port), (HOST_GATEWAY, api_port)}:
        raise RehearsalError(
            CATEGORY_RENDER, f"frpc 回连目标必须恰为 {HOST_GATEWAY} 的 {web_port}/{api_port}"
        )

    # Caddy：HTTP-only + 双标签 Host 路由 + frps vhost 上游（443/tls 检查只看
    # 去注释后的配置体——模板注释里对生产 443 的说明不是配置事实）
    for marker in ("auto_https off", "admin off", f"host {APP_HOST}", f"host {API_HOST}",
                   f"reverse_proxy frps:{FRPS_VHOST_PORT}"):
        if marker not in caddy:
            raise RehearsalError(CATEGORY_RENDER, f"Caddyfile 缺彩排不变量: {marker}")
    caddy_body = "\n".join(
        line for line in caddy.splitlines() if not line.lstrip().startswith("#")
    )
    if (":80 {" not in caddy_body or re.search(r"\b443\b", caddy_body)
            or re.search(r"^\s*tls\s", caddy_body, re.MULTILINE)):
        raise RehearsalError(CATEGORY_RENDER, "Caddyfile 必须保持 HTTP-only（:80 站点，无 443/tls 指令）")

    # compose：项目名恒定 + 唯一回环高位发布 + digest pin + 彩排标签
    if f"name: {COMPOSE_PROJECT}" not in compose:
        raise RehearsalError(CATEGORY_RENDER, f"compose 项目名必须恒为 {COMPOSE_PROJECT}")
    mappings = re.findall(r"-\s*\"?127\.0\.0\.1:(\d+):(\d+)\"?", compose)
    if mappings != [(str(INGRESS_PORT), "80")]:
        raise RehearsalError(
            CATEGORY_RENDER,
            f"host 发布必须唯一且为回环高位端口: 127.0.0.1:{INGRESS_PORT}:80（收到 {mappings}）",
        )
    if not INGRESS_PORT >= HIGH_PORT_MIN:
        raise RehearsalError(CATEGORY_RENDER, "发布端口必须是高位端口（>=1024）")
    images = re.findall(r"image:\s+(\S+)", compose)
    if len(images) != 3 or not all("@sha256:" in image for image in images):
        raise RehearsalError(CATEGORY_RENDER, "compose 镜像必须逐一 digest pin（不回退 :latest）")
    if f"{COMPOSE_LABEL_KEY}: {COMPOSE_LABEL_VALUE}" not in compose:
        raise RehearsalError(CATEGORY_RENDER, "compose 服务必须携带彩排标签 io.aios.rehearsal")
    for forbidden in FORBIDDEN_PROJECTS:
        if forbidden in compose or forbidden in caddy:
            raise RehearsalError(CATEGORY_RENDER, f"彩排产物引用了受保护项目名: {forbidden}")


# ---------------------------------------------------------------- preflight


def run_preflight(runner: Runner, prober: Prober, work_dir: Path, report: dict[str, Any],
                  log, api_port: int = HOST_API_PORT, web_port: int = HOST_WEB_PORT) -> None:
    """只读 preflight：docker/compose 可用、家机目标可达、端口空闲。任一失败即抛错。"""
    stages: list[dict[str, str]] = report["stages"]

    def pass_stage(name: str, detail: str) -> None:
        stages.append({"name": name, "status": "pass", "detail": detail})
        log(f"stage {name}: pass（{detail}）")

    code, _ = runner.run(["docker", "--version"])
    if code != 0:
        raise RehearsalError(CATEGORY_DOCKER, "docker CLI 不可用（returncode != 0）")
    code, _ = runner.run(["docker", "compose", "version"])
    if code != 0:
        raise RehearsalError(CATEGORY_DOCKER, "docker compose 子命令不可用（需 compose v2）")
    pass_stage("docker-cli", "docker --version + docker compose version 可用")

    status, _ = prober.http_get(f"http://{LOOPBACK}:{api_port}/health", {}, 10.0)
    if status != 200:
        raise RehearsalError(CATEGORY_TARGET, f"家机 API /health 不可达（状态 {status}，期望 200）")
    pass_stage("target-api", f"127.0.0.1:{api_port}/health = 200")
    status, _ = prober.http_get(f"http://{LOOPBACK}:{web_port}/", {}, 10.0)
    if status != 200:
        raise RehearsalError(CATEGORY_TARGET, f"家机 Web 入口不可达（状态 {status}，期望 200）")
    pass_stage("target-web", f"127.0.0.1:{web_port}/ = 200")

    if not prober.port_free(LOOPBACK, INGRESS_PORT):
        raise RehearsalError(CATEGORY_PORT, f"发布端口 {LOOPBACK}:{INGRESS_PORT} 已被占用")
    pass_stage("ingress-port-free", f"127.0.0.1:{INGRESS_PORT} 空闲")
    pass_stage("work-dir-contract", "仓库外/非符号链接/条目契约成立（幂等覆写）")


# ---------------------------------------------------------------- 探测


def wait_for_ingress(prober: Prober, timeout_seconds: int, log) -> bool:
    """入口健康等待：app 路由（Host 头）重试至 200 或超时。"""
    url = f"http://{LOOPBACK}:{INGRESS_PORT}/"
    deadline = time.monotonic() + max(0, timeout_seconds)
    while True:
        try:
            status, _ = prober.http_get(url, {"Host": APP_HOST}, 5.0)
        except RehearsalError:
            status = 0
        if status == 200:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))


def probe_routes(prober: Prober, report: dict[str, Any], log) -> None:
    """双路由 Host 头探测：app → Web、api /health → API（入口固定 39443）。"""
    routes = (
        ("app", "/", APP_HOST),
        ("api", "/health", API_HOST),
    )
    for route, path, host in routes:
        url = f"http://{LOOPBACK}:{INGRESS_PORT}{path}"
        status, _body = prober.http_get(url, {"Host": host}, 10.0)
        ok = status == 200
        report["probes"].append(
            {"route": route, "path": path, "host": host, "status_code": status, "ok": ok}
        )
        log(f"probe {route}（Host {host}）{path}: {status}{' OK' if ok else '（期望 200）'}")
    failed = [p for p in report["probes"] if not p["ok"]]
    if failed:
        raise RehearsalError(
            CATEGORY_PROBE,
            f"路由探测失败: {[p['route'] for p in failed]}（状态 {[p['status_code'] for p in failed]}）",
        )


# ---------------------------------------------------------------- scoped 清理


def scoped_cleanup(runner: Runner, work_dir: Path | None, log) -> dict[str, Any]:
    """只清理本项目（精确 compose project 标签过滤）+ 契约内 work-dir。

    绝不触碰任何其他 compose 项目/容器/网络；查询失败如实计入 problems
    （fail-closed 由调用方决定退出码）。
    """
    facts: dict[str, Any] = {"attempted": True, "containers_removed": 0, "networks_removed": 0,
                             "work_dir_removed": False, "problems": []}
    code, out = runner.run(["docker", "ps", "-a", "--filter", PROJECT_LABEL_FILTER, "-q"])
    if code != 0:
        facts["problems"].append("containers-query-failed")
    else:
        for container_id in out.split():
            rm_code, _ = runner.run(["docker", "rm", "-f", container_id])
            if rm_code != 0:
                facts["problems"].append("container-remove-failed")
            else:
                facts["containers_removed"] += 1
    code, out = runner.run(["docker", "network", "ls", "--filter", PROJECT_LABEL_FILTER, "-q"])
    if code != 0:
        facts["problems"].append("networks-query-failed")
    else:
        for network_id in out.split():
            rm_code, _ = runner.run(["docker", "network", "rm", network_id])
            if rm_code != 0:
                facts["problems"].append("network-remove-failed")
            else:
                facts["networks_removed"] += 1
    if work_dir is not None and work_dir.exists():
        try:
            shutil.rmtree(work_dir)
        except OSError:
            facts["problems"].append("work-dir-remove-failed")
        else:
            facts["work_dir_removed"] = True
    log(
        f"cleanup: containers={facts['containers_removed']} "
        f"networks={facts['networks_removed']} work-dir="
        f"{'removed' if facts['work_dir_removed'] else 'absent'}"
        + (f" problems={facts['problems']}" if facts["problems"] else "")
    )
    return facts


# ---------------------------------------------------------------- 证据


def build_markdown(report: dict[str, Any]) -> str:
    probes = "\n".join(
        f"| {p['route']} | `{p['host']}`{p['path']} | {p['status_code']} |"
        for p in report["probes"]
    ) or "|（未到达探测阶段）| | |"
    stages = "\n".join(f"- {s['name']}: {s['status']}（{s['detail']}）" for s in report["stages"])
    return f"""# M14-156 公网边缘本地彩排报告（{report['command']}）

- schema：{report['schema']}（由 {report['tool']} 生成；result = **{report['result']}**）
- compose project：`{report['compose_project']}`（标签 `{COMPOSE_LABEL_KEY}={COMPOSE_LABEL_VALUE}`）
- 入口：`{report['ingress']}`（HTTP-only，唯一 host 发布端口，回环+高位）
- 目标端口：API {report['targets']['api_port']} / Web {report['targets']['web_port']}
  （{'默认端口' if report['targets']['defaults_used'] else '**覆写端口（非默认——本报告不构成对默认映射的验证）**'}；
  默认值 {report['targets']['default_api_port']}/{report['targets']['default_web_port']}）
- Host 路由：`{APP_HOST}` → Web（{report['targets']['web']}）；
  `{API_HOST}` → API（{report['targets']['api']}）
- 一次性 token：{report['token_facts']['hex_length']} hex（{report['token_facts']['bytes']} 字节随机），
  只落 0600 文件 {report['token_facts']['file_name']}，值绝不打印/入证据

## 阶段

{stages}

## 路由探测（Host 头）

| 路由 | 探测目标 | 状态码 |
| --- | --- | --- |
{probes}

## 清理

容器 {report['cleanup']['containers_removed']} 个 / 网络 {report['cleanup']['networks_removed']} 个 /
work-dir {'已删除' if report['cleanup']['work_dir_removed'] else '不存在'}；
问题：{report['cleanup']['problems'] or '无'}（只按本项目标签 scoped 清理）

## 诚实边界

{report['boundary']} `public_ready = {report['public_ready']}`。
"""


EVIDENCE_JSON_NAME = "rehearsal-evidence.json"
EVIDENCE_MD_NAME = "rehearsal-report.md"


def write_evidence(evidence_dir: Path, report: dict[str, Any]) -> tuple[str, str]:
    """证据原子写（JSON + MD）；返回两文件名（不含绝对路径）。"""
    evidence_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        evidence_dir / EVIDENCE_JSON_NAME,
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        0o644,
    )
    _atomic_write(evidence_dir / EVIDENCE_MD_NAME, build_markdown(report), 0o644)
    return EVIDENCE_JSON_NAME, EVIDENCE_MD_NAME


def cleanup_not_attempted() -> dict[str, Any]:
    return {"attempted": False, "containers_removed": 0, "networks_removed": 0,
            "work_dir_removed": False, "problems": []}


def new_report(command: str, api_port: int = HOST_API_PORT,
               web_port: int = HOST_WEB_PORT) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "tool": TOOL_NAME,
        "command": command,
        "compose_project": COMPOSE_PROJECT,
        "ingress": f"{LOOPBACK}:{INGRESS_PORT}",
        "rehearsal_hosts": {"app": APP_HOST, "api": API_HOST},
        "targets": {
            "web": f"{HOST_GATEWAY}:{web_port}", "api": f"{HOST_GATEWAY}:{api_port}",
            "api_port": api_port, "web_port": web_port,
            # R2：显式记录是否默认端口——覆写端口上的 pass 不得伪装成
            # 对默认映射（可能已坏）的验证
            "defaults_used": defaults_in_use(api_port, web_port),
            "default_api_port": HOST_API_PORT, "default_web_port": HOST_WEB_PORT,
        },
        "token_facts": {"bytes": TOKEN_BYTES, "hex_length": TOKEN_BYTES * 2,
                        "file_name": TOKEN_FILE_NAME, "value_printed": False},
        "stages": [],
        "probes": [],
        "cleanup": cleanup_not_attempted(),
        "result": "fail",
        "public_ready": False,
        "boundary": "本地回环 HTTP-only 彩排（Caddy→frps→frpc→host API/Web）——"
                    "未发生任何真实公网部署，不证明公网生产可用。",
    }


# ---------------------------------------------------------------- 子命令


def _print_plan(log, work_dir_note: str, api_port: int = HOST_API_PORT,
                web_port: int = HOST_WEB_PORT) -> None:
    ports_note = (
        "默认端口" if defaults_in_use(api_port, web_port)
        else f"**覆写端口 {api_port}/{web_port}（本地 stale-port-forward 临时绕行，非默认）**"
    )
    log("plan（零变更零探测零 Docker）——真实执行需 execute + 精确短语：")
    log(f"  拓扑: probe → Caddy({LOOPBACK}:{INGRESS_PORT}) → frps vhost({FRPS_VHOST_PORT})")
    log(f"        → frpc → {HOST_GATEWAY}:{api_port}(API)/{web_port}(Web)（{ports_note}）")
    log(f"  compose project: {COMPOSE_PROJECT}（标签 {COMPOSE_LABEL_KEY}={COMPOSE_LABEL_VALUE}）")
    log(f"  Host 路由: {APP_HOST} → Web；{API_HOST} → API /health")
    log("  序列: preflight(只读) → 渲染(仓库外 work-dir + 一次性 token 0600)")
    log("        → docker compose up -d --wait → 入口健康等待 → 双路由 Host 探测")
    log("        → 证据 JSON+MD 原子写 → scoped 清理(仅本项目 + work-dir)")
    log(f"  执行: python tools/ops/{TOOL_NAME}.py execute --work-dir <仓库外目录> \\")
    log(f'        --confirm-phrase "{EXECUTE_CONFIRM_PHRASE}" --execute')
    log(f"  回收: python tools/ops/{TOOL_NAME}.py cleanup [--work-dir <仓库外目录>] \\")
    log(f'        --confirm-phrase "{CLEANUP_CONFIRM_PHRASE}" --execute')
    log("  边界: HTTP-only/loopback-only 彩排，彩排通过 ≠ 公网生产可用；")
    log(f"        绝不触碰 {'/'.join(FORBIDDEN_PROJECTS)} 或任何其他 compose 项目{work_dir_note}")


def cmd_plan(args: argparse.Namespace, log, api_port: int, web_port: int) -> int:
    note = ""
    if args.work_dir:
        validate_work_dir(args.work_dir)  # 只读校验，零变更
        note = "（--work-dir 契约校验通过）"
    _print_plan(log, note, api_port, web_port)
    return EXIT_OK


def cmd_status(runner: Runner, args: argparse.Namespace, log,
               api_port: int, web_port: int) -> int:
    code, out = runner.run(
        ["docker", "ps", "-a", "--filter", PROJECT_LABEL_FILTER, "--format", "{{.Names}}"]
    )
    if code != 0:
        raise RehearsalError(CATEGORY_DOCKER, "docker 查询失败（status 只读，不猜测状态）")
    containers = [line for line in out.splitlines() if line.strip()]
    net_code, net_out = runner.run(
        ["docker", "network", "ls", "--filter", PROJECT_LABEL_FILTER, "--format", "{{.Name}}"]
    )
    if net_code != 0:
        # R1：network ls 返回码同样 fail-closed——只读查询不猜测状态
        raise RehearsalError(CATEGORY_DOCKER, "docker network 查询失败（status 只读，不猜测状态）")
    networks = [line for line in net_out.splitlines() if line.strip()]
    log(f"status: compose project {COMPOSE_PROJECT}")
    log(f"  containers={len(containers)}{containers if containers else '（无残留）'}")
    log(f"  networks={len(networks)}{networks if networks else '（无残留）'}")
    ports_note = "默认端口" if defaults_in_use(api_port, web_port) else "覆写端口（非默认）"
    log(f"  targets: API {HOST_GATEWAY}:{api_port} / Web {HOST_GATEWAY}:{web_port}（{ports_note}）")
    if args.work_dir:
        work_dir = validate_work_dir(args.work_dir)
        log(f"  work-dir 存在: {work_dir.exists()}（契约校验通过）")
    log("status 为只读查询；残留回收用 cleanup 子命令（需短语 + --execute）")
    return EXIT_OK


def cmd_execute(args: argparse.Namespace, runner: Runner, prober: Prober, log,
                api_port: int, web_port: int) -> int:
    # 确认门先行：任一缺失即纯拒绝——零探测、零写入、零证据、零清理
    if args.confirm_phrase != EXECUTE_CONFIRM_PHRASE or not args.execute:
        print(
            f"[{TOOL_NAME}] FAIL {CATEGORY_CONFIRM}: execute 需同时满足 --execute 与精确确认短语"
            f"（缺一即 fail-closed；只读计划见 plan 子命令）",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    report = new_report("execute", api_port, web_port)
    work_dir: Path | None = None
    mutated = False  # work-dir 创建成功即 True——其后任何失败都必须进入 scoped 清理
    failure: RehearsalError | None = None
    evidence_dir = resolve_evidence_dir(args.evidence_dir)
    try:
        work_dir = validate_work_dir(args.work_dir)
        run_preflight(runner, prober, work_dir, report, log, api_port, web_port)
        work_dir.mkdir(parents=True, exist_ok=True)
        mutated = True  # R1：mkdir 成功即视为已产生变更——render/chmod 失败不得留下半成品 work-dir
        os.chmod(work_dir, 0o700)
        render_rehearsal(work_dir, secrets.token_hex(TOKEN_BYTES), api_port, web_port)
        validate_rendered_configs(work_dir, api_port, web_port)
        report["stages"].append({"name": "render", "status": "pass",
                                 "detail": "四模板 + 一次性 token（0600）渲染并自检通过"})
        log("stage render: pass（隔离配置 + 一次性 token 落盘，值不回显）")
        up_cmd = ["docker", "compose", "-f", str(work_dir / "docker-compose.yml"),
                  "-p", COMPOSE_PROJECT, "up", "-d", "--wait", "--wait-timeout", "300"]
        code, _out = runner.run(up_cmd)
        if code != 0:
            raise RehearsalError(CATEGORY_COMPOSE, f"compose up 失败（returncode={code}，输出不回显）")
        report["stages"].append({"name": "compose-up", "status": "pass",
                                 "detail": f"project {COMPOSE_PROJECT} up -d --wait"})
        log(f"stage compose-up: pass（project {COMPOSE_PROJECT}）")
        if not wait_for_ingress(prober, args.health_timeout, log):
            raise RehearsalError(
                CATEGORY_PROBE, f"入口健康等待超时（{args.health_timeout}s，Host {APP_HOST} 未达 200）"
            )
        report["stages"].append({"name": "ingress-health", "status": "pass",
                                 "detail": f"Host {APP_HOST} 入口达 200"})
        log(f"stage ingress-health: pass（Host {APP_HOST}）")
        probe_routes(prober, report, log)
        report["result"] = "pass"
    except RehearsalError as cause:
        failure = cause
        report["stages"].append({"name": cause.category, "status": "fail", "detail": str(cause)})
    except Exception as cause:  # noqa: BLE001 —— 兜底：任何意外异常也必须走证据+清理路径
        failure = RehearsalError(CATEGORY_INTERNAL, f"{type(cause).__name__}: {cause}")
        report["stages"].append({"name": CATEGORY_INTERNAL, "status": "fail", "detail": str(cause)})

    # —— scoped 清理先于证据（结果入档；只在已产生变更后执行：只本项目 +
    #    契约内 work-dir；未过 preflight 的拒绝不做任何 Docker 操作——
    #    残留回收走 cleanup 子命令）。scoped_cleanup 自身不抛错（问题入 facts）。——
    try:
        report["cleanup"] = scoped_cleanup(runner, work_dir, log) if mutated else cleanup_not_attempted()
    except Exception as cause:  # noqa: BLE001 —— pragma: no cover 防御性兜底
        report["cleanup"] = {**cleanup_not_attempted(), "problems": [f"internal-error: {type(cause).__name__}"]}

    # —— 证据收尾（门后成败都写，含清理事实；原子写失败如实升级退出码）——
    evidence_failure: str | None = None
    report["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["stages"].append({"name": "evidence", "status": "pass",
                             "detail": f"{EVIDENCE_JSON_NAME} + {EVIDENCE_MD_NAME}"})
    try:
        write_evidence(evidence_dir, report)
        log(f"evidence: {EVIDENCE_JSON_NAME} + {EVIDENCE_MD_NAME}（原子写）")
    except (RehearsalError, OSError) as cause:
        report["stages"][-1]["status"] = "fail"
        evidence_failure = f"{type(cause).__name__}: {cause}"
        log(f"evidence 写出失败: {evidence_failure}")

    if failure is not None:
        print(f"[{TOOL_NAME}] FAIL {failure.category}: {failure}", file=sys.stderr)
        return EXIT_FAILURE
    if evidence_failure is not None:
        print(f"[{TOOL_NAME}] FAIL {CATEGORY_EVIDENCE}: {evidence_failure}", file=sys.stderr)
        return EXIT_FAILURE
    if report["cleanup"]["problems"]:
        print(f"[{TOOL_NAME}] FAIL {CATEGORY_CLEANUP}: {report['cleanup']['problems']}", file=sys.stderr)
        return EXIT_FAILURE
    log("EXECUTE OK（本地回环彩排通过——不证明公网生产可用）")
    return EXIT_OK


def cmd_cleanup(args: argparse.Namespace, runner: Runner, log) -> int:
    if args.confirm_phrase != CLEANUP_CONFIRM_PHRASE or not args.execute:
        print(
            f"[{TOOL_NAME}] FAIL {CATEGORY_CONFIRM}: cleanup 需同时满足 --execute 与精确确认短语"
            f"（缺一即 fail-closed，零清理）",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    work_dir = validate_work_dir(args.work_dir) if args.work_dir else None
    if work_dir is not None:
        log("work-dir 契约校验通过（仅删本工具产物目录；条目幂等集）")
    facts = scoped_cleanup(runner, work_dir, log)
    if facts["problems"]:
        print(f"[{TOOL_NAME}] FAIL {CATEGORY_CLEANUP}: {facts['problems']}", file=sys.stderr)
        return EXIT_FAILURE
    log("CLEANUP OK（仅本项目 scoped）")
    return EXIT_OK


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None, *, runner: Runner | None = None,
         prober: Prober | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "公网边缘本地彩排 supervisor（plan/execute/cleanup/status；fail-closed）。"
            "默认 plan 零变更；execute/cleanup 需 --execute + 精确确认短语；"
            "彩排 HTTP-only/loopback-only，通过 ≠ 公网生产可用。"
        ),
    )
    parser.add_argument("command", nargs="?", default="plan",
                        choices=["plan", "execute", "cleanup", "status"],
                        help="子命令（默认 plan，零变更）")
    parser.add_argument("--work-dir", help="彩排工作目录（仓库外；契约见工具 docstring）")
    parser.add_argument("--confirm-phrase", help="执行确认短语（见 plan 输出/文档）")
    parser.add_argument("--execute", action="store_true", help="真实执行（默认拒绝一切变更）")
    parser.add_argument("--evidence-dir", type=str, default=None,
                        help="证据目录（默认仓库 gitignored .verify/artifacts/m14-156-edge-rehearsal）")
    parser.add_argument("--health-timeout", type=int, default=120,
                        help="入口健康等待超时秒数（默认 120）")
    parser.add_argument("--host-api-port", type=str, default=None,
                        help=f"家机 API 目标端口（1..65535，默认 {HOST_API_PORT}；"
                             f"仅本地 stale-port-forward 临时绕行用，见 runbook §3C）")
    parser.add_argument("--host-web-port", type=str, default=None,
                        help=f"家机 Web 目标端口（1..65535，默认 {HOST_WEB_PORT}；同上——"
                             f"host.docker.internal 固定，无 host/address/URL 覆写）")
    args = parser.parse_args(argv)
    if args.command == "execute" and not (args.work_dir or "").strip():
        parser.error("execute 需要 --work-dir（仓库外彩排目录）")
    if runner is None:
        runner = RealRunner()
    if prober is None:
        prober = RealProber()

    def log(message: str) -> None:
        print(f"[{TOOL_NAME}] {message}", flush=True)

    try:
        # R2：端口解析先于一切分发/探测——畸形/越界/相同端口即 port-invalid
        # fail-closed（稳定类别，无 traceback，零副作用）
        api_port, web_port = resolve_target_ports(args)
        if args.command == "plan":
            return cmd_plan(args, log, api_port, web_port)
        if args.command == "status":
            return cmd_status(runner, args, log, api_port, web_port)
        if args.command == "execute":
            return cmd_execute(args, runner, prober, log, api_port, web_port)
        return cmd_cleanup(args, runner, log)
    except RehearsalError as cause:
        print(f"[{TOOL_NAME}] FAIL {cause.category}: {cause}", file=sys.stderr)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
