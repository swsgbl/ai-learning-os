#!/usr/bin/env python
"""M14-40 MinIO 自建镜像：本机构建 + 一次性冒烟 + 生产采纳前置核查。

背景（M14-13/M14-39）：MinIO 社区版自 2025-10 起 source-only 分发，本仓以
pin 源码本地自建 ``aios/minio:RELEASE.2025-10-15T17-29-55Z``
（infra/minio/Dockerfile，compose ``image:`` 锚点）。M14-39 起恢复工具在栈
非全健康时对该镜像做 up 前预检（缺失 → 固定词汇 ``minio-local-image-missing``
fail-closed，绝不 pull/build）。本工具（supervisor 任务书获准窗口）收口
M14-39 留下的本机地面，三模式：

- **build**：只构建 compose 锚定的那一个镜像（``docker build -t <锚点>
  infra/minio``——纯 docker build，零 compose 操作、零生产触碰）。可选
  ``AIOS_MINIO_BUILD_HTTPS_PROXY``（操作者显式提供，严格 URL 形态）：值仅
  作为 Docker 预定义 ``HTTPS_PROXY`` build arg 传入（GOPROXY/Dockerfile/
  镜像 pin/报告日志均不变），未设置时默认无代理形态不变；
- **smoke**：固定形态一次性容器/卷（严格 ``aios-m14-40-`` 前缀 + UTC 时间戳
  生成名）起自建镜像 → 等待 ``/minio/health/cluster`` 200 → uid 1000 在
  ``/data`` 写/读/删数据探针 → 二进制 ``--version`` 报告 pin 的
  RELEASE/commit → finally 精确清理（仅本轮生成的两个名字）；
- **preflight**（只读生产面）：本地镜像元数据（在场/User 非 root/入口点/
  Id）+ 运行时 uid 探针（``id -u`` = 1000）+ 版本探针 + compose/Dockerfile
  锚点静态核验 + 生产六容器健康只读快照 + 生产 ``minio-data`` 卷属主探针
  （``:ro`` 只读挂载 + ``--network none`` 的 throwaway helper；顶层 + 递归
  UID 普查——采纳 pass 要求全部观察路径 uid 1000，普查不可完成即不确定）。**采纳判定
  fail-closed**：镜像缺失/身份或版本不符/服务不健康/属主非 uid 1000（需
  M14-13 README 注记的一次性迁移）/属主不可判 → ``adoption=blocked``。
  采纳本身（卷属主迁移 + ``up -d --no-build`` 固化）不在本工具。

设计（与 production_monitor.py / production_recovery.py 同款纪律：单文件、
纯标准库、零第三方依赖；子进程经 Runner 注入 + 结构性 argv 门、HTTP 经
Transport 注入、sleep 经 Sleeper 注入——开发回合零真实 Docker/零生产读取，
全部行为用 fake/stub 测试锁定；真实执行仅由 supervisor 在获准窗口运行）：

- 每模式需 ``--execute`` + 该模式精确确认短语（build/smoke/preflight 各一，
  一字不差），缺一即 EXIT 拒绝且零副作用（fail-closed）；无子命令 = plan
  （零 subprocess/零网络/零生产读取，仅打印三模式面并落 plan 报告）。
- argv 门（结构性，非逐字快照——冒烟名每轮生成）：一切 docker 命令逐 token
  匹配固定形态；镜像引用恒为模块常量 ``SELF_IMAGE_REF``；生产容器/卷名恒由
  校验过的 compose 项目名派生；生产卷只允许 ``:ro`` 挂载进
  ``--network none`` 的 ``--rm`` helper；一切 ``rm``/``volume rm`` 只允许
  本轮生成且带严格 ``aios-m14-40-`` 前缀的一次性名（清理面结构性窄化，
  绝无宽域进程/端口/容器/卷清理）；本工具零 ``docker compose``、零
  pull/stop/restart/kill/exec。
- 冒烟端口固定 loopback 127.0.0.1:19000/19001（与生产 9000/9001 恒不冲突）；
  启动前端口占用即拒绝（零冒烟副作用）；env 用 compose 同款 repo 公开开发
  占位（本工具不读取任何 env secret，含 infra/env.production-recovery）。
- 报告：schema 版本化 JSON + Markdown 原子写入（tmp + os.replace）；默认
  目录 ``.verify/artifacts/m14-40-minio-image-adoption``（gitignored）；
  symlink/越界 stem 拒绝；写盘前 redact_secrets 终防线。build 失败时保留
  输出尾行入报告（诊断可失 = 证据可失）。
- ``adoption=pass`` ≠ production ready；全局 ``production_ready=false`` 不变。

用法（仓库根）：
  python tools/ops/minio_image_adoption.py                       # plan
  python tools/ops/minio_image_adoption.py build --execute \
      --confirm "EXECUTE MINIO IMAGE BUILD"                     # 真实构建
  python tools/ops/minio_image_adoption.py smoke --execute \
      --confirm "EXECUTE MINIO IMAGE SMOKE"                     # 一次性冒烟
  python tools/ops/minio_image_adoption.py preflight --execute \
      --confirm "EXECUTE MINIO PREFLIGHT"                       # 只读盘点
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

EXIT_OK = 0
#: 2 = 非法/fail-closed/采集 incomplete/blocked（统一可见拒绝口径）
EXIT_REJECT = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"
DOCKERFILE = REPO_ROOT / "infra" / "minio" / "Dockerfile"
BUILD_CONTEXT = REPO_ROOT / "infra" / "minio"
ARTIFACT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-40-minio-image-adoption"
TAG = "[minio-adoption]"
REPORT_SCHEMA_VERSION = 1
MILESTONE = "M14-40"
TOOL_NAME = "tools/ops/minio_image_adoption.py"
USER_AGENT = "aios-m14-40-minio-image-adoption/1.0"

#: 各模式 execute 确认短语（一字不差）
CONFIRM_BUILD = "EXECUTE MINIO IMAGE BUILD"
CONFIRM_SMOKE = "EXECUTE MINIO IMAGE SMOKE"
CONFIRM_PREFLIGHT = "EXECUTE MINIO PREFLIGHT"

DEFAULT_PROJECT = "aios-m14-03-production-rehearsal"

#: 自建镜像锚点（与 compose image: / production_recovery.LOCAL_BUILD_IMAGE_REFS /
#: test_minio_selfbuild 常量契约交叉锁定；升版 = 四处同改）
MINIO_RELEASE = "RELEASE.2025-10-15T17-29-55Z"
MINIO_COMMIT = "9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a"
SELF_IMAGE_REF = f"aios/minio:{MINIO_RELEASE}"
EXPECTED_ENTRYPOINT_JSON = '["/usr/bin/minio"]'
#: 镜像内非 root 用户（Dockerfile USER 指令原样入 Config.User）
EXPECTED_CONFIG_USER = "minio:minio"
#: 运行时 uid（adduser -S -u 1000；id -u 探针必须回显）
EXPECTED_RUNTIME_UID = "1000"

#: 与 production_recovery.LOCAL_IMAGE_MISSING 同一固定词汇（日志可见锚点）
LOCAL_IMAGE_MISSING = "minio-local-image-missing"

#: 生产六受管服务（与 production_recovery.EXPECTED_STACK_SERVICES 同源）
STACK_SERVICES: tuple[str, ...] = ("postgres", "redis", "minio", "api", "web", "livekit")
PROD_SERVICE = "minio"
PROD_VOLUME_KEY = "minio-data"

#: 一次性资源命名（严格前缀 + UTC 时间戳；清理面结构性只认这些名字）
DISPOSABLE_PREFIX = "aios-m14-40-"
SMOKE_CONTAINER_STEM = "aios-m14-40-smoke-c-"
SMOKE_VOLUME_STEM = "aios-m14-40-smoke-v-"

#: 冒烟端口（loopback；与生产 9000/9001 恒不冲突）
SMOKE_API_PORT = 19000
SMOKE_CONSOLE_PORT = 19001

#: build 模式唯一读取的环境变量：操作者显式提供的 HTTPS 代理（如
#: socks5h://host.docker.internal:10808——构建容器无法直连 proxy.golang.org 时）。
#: 值只作为 Docker 预定义 HTTPS_PROXY build arg 传入；默认（未设置）恒不加代理。
BUILD_PROXY_ENV = "AIOS_MINIO_BUILD_HTTPS_PROXY"
_BUILD_PROXY_RE = re.compile(
    r"^(?:socks5h?|https?)://[A-Za-z0-9._-]+(?::(?:6553[0-5]|655[0-2][0-9]"
    r"|65[0-4][0-9]{2}|6[0-4][0-9]{3}|[1-5][0-9]{4}|[1-9][0-9]{0,3}))?/?$")


def validate_build_https_proxy(value: str) -> str | None:
    """构建代理值严格 URL 形态（scheme://host[:port]，port 为 1-65535 的
    语义合法 TCP 端口——纯 5 位数字如 99999 不接受）；被拒值不回显。"""
    if not value or not _BUILD_PROXY_RE.fullmatch(value):
        return "build-proxy-invalid"
    return None

#: uid 1000 数据探针脚本（写/读/删各一次；echo 内容为固定探针标记）
DATA_PROBE_SCRIPT = "echo m14-40-probe > /data/.m14-40-probe" \
    " && cat /data/.m14-40-probe && rm /data/.m14-40-probe"

#: 超时（秒）
ENGINE_TIMEOUT_SECONDS = 15.0
INSPECT_TIMEOUT_SECONDS = 30.0
RUN_PROBE_TIMEOUT_SECONDS = 60.0
DU_PROBE_TIMEOUT_SECONDS = 120.0
SMOKE_RUN_TIMEOUT_SECONDS = 60.0
BUILD_TIMEOUT_SECONDS = 1200.0

#: 冒烟健康轮询（/minio/health/cluster；mc ready 消费的同一就绪信号）
HEALTH_ATTEMPTS = 40
HEALTH_INTERVAL_SECONDS = 1.0
HEALTH_TIMEOUT_SECONDS = 3.0
PORT_PROBE_TIMEOUT_SECONDS = 0.5

#: build 失败时保留的输出尾行数（诊断不可失）
BUILD_OUTPUT_TAIL_LINES = 50


def prod_container_name(project: str, service: str) -> str:
    return f"{project}-{service}-1"


def prod_volume_name(project: str) -> str:
    return f"{project}_{PROD_VOLUME_KEY}"


def validate_project_name(name: str) -> str | None:
    """--project 唯一合法值 = DEFAULT_PROJECT（本工具有且仅有一个预期生产面；
    语法合法的其它项目名同样拒绝——被拒值不回显）。"""
    if name != DEFAULT_PROJECT:
        return "project-not-allowed"
    return None


def disposable_names(stamp: str) -> tuple[str, str]:
    """本轮一次性资源名（容器, 卷）——严格前缀 + 时间戳，绝不与既有资源同名。"""
    return f"{SMOKE_CONTAINER_STEM}{stamp}", f"{SMOKE_VOLUME_STEM}{stamp}"


# ---------------------------------------------------------------- Runner（注入点）


class RunnerError(RuntimeError):
    """平台命令执行失败（不可执行/超时）——调用方转为安全类别，绝不保留文本。"""


class CommandNotAllowedError(RunnerError):
    """非白名单命令——在任何执行之前拒绝（fail-closed）。"""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    def run(self, argv: tuple[str, ...] | list[str], *, timeout: float = 60.0,
            encoding: str | None = None) -> CommandResult: ...


def os_windows() -> bool:
    return os.name == "nt"


class RealRunner:
    """真实子进程执行：capture + UTF-8 + Windows 侧恒 CREATE_NO_WINDOW（无弹窗）。"""

    def run(self, argv: tuple[str, ...] | list[str], *, timeout: float = 60.0,
            encoding: str | None = None) -> CommandResult:
        text_argv = [str(item) for item in argv]
        kwargs: dict[str, object] = {
            "capture_output": True,
            "text": True,
            "encoding": encoding or "utf-8",
            "errors": "replace",
        }
        if os_windows():
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        try:
            result = subprocess.run(
                text_argv, check=False, timeout=timeout, **kwargs  # type: ignore[arg-type]
            )
        except (OSError, subprocess.TimeoutExpired) as cause:
            raise RunnerError(f"命令不可执行/超时: {text_argv[0]}: {type(cause).__name__}") from cause
        return CommandResult(tuple(text_argv), result.returncode, result.stdout or "", result.stderr or "")


def categorize_runner_exception(exc: BaseException) -> tuple[str, str]:
    """Runner 异常 →（安全类别, 异常类名）。绝不保留 str(exc) 文本。"""
    if isinstance(exc, CommandNotAllowedError):
        category = "argv-not-whitelisted"
    elif isinstance(exc, subprocess.TimeoutExpired):
        category = "command-timeout"
    elif isinstance(exc, (RunnerError, OSError)):
        category = "command-exec-error"
    else:
        category = "internal-error"
    return category, type(exc).__name__


# ---------------------------------------------------------------- argv 门（结构性白名单）


@dataclass(frozen=True)
class Surface:
    """argv 门的事实面：项目派生名 + 本轮生成的一次性名 + build 代理值（可选）。"""

    project: str
    disposable: frozenset[str] = frozenset()
    build_https_proxy: str | None = None

    @property
    def prod_containers(self) -> frozenset[str]:
        return frozenset(prod_container_name(self.project, svc) for svc in STACK_SERVICES)

    @property
    def prod_volume(self) -> str:
        return prod_volume_name(self.project)


def _exact_helper(argv: tuple[str, ...], head: tuple[str, ...], tail: tuple[str, ...]) -> bool:
    """head + 恰一个挂载 token + tail 的精确全长形态——头尾匹配不充分
    （插入 ``--privileged`` 等 token 可通过头尾匹配），必须全长逐 token 相等。"""
    return (len(argv) == len(head) + 1 + len(tail)
            and argv[: len(head)] == head and argv[len(head) + 1:] == tail)


def is_allowed_docker_argv(argv: tuple[str, ...] | list[str], surface: Surface) -> bool:
    """结构性白名单：逐 token 匹配固定形态；镜像/卷/端口/env 全为常量，
    一次性名只认本轮生成集。任何偏差（含 pull/stop/restart/kill/exec/
    compose/任意其它镜像或卷）一律 False。"""
    tokens = tuple(str(item) for item in argv)
    if len(tokens) < 3 or tokens[0] != "docker":
        return False
    sub = tokens[1]
    if sub == "version":
        return tokens == ("docker", "version", "--format", "{{.Server.Version}}")
    if sub == "build":
        # build 模式唯一无代理形态：只构建 compose 锚定镜像（零 compose 操作）
        if tokens == ("docker", "build", "-t", SELF_IMAGE_REF, str(BUILD_CONTEXT)):
            return True
        # 唯一代理形态：操作者经 AIOS_MINIO_BUILD_HTTPS_PROXY 提供的值只作为
        # Docker 预定义 HTTPS_PROXY build arg 传入（精确全长，值逐字匹配）
        return (surface.build_https_proxy is not None
                and len(tokens) == 7
                and tokens[:4] == ("docker", "build", "-t", SELF_IMAGE_REF)
                and tokens[4] == "--build-arg"
                and tokens[5] == f"HTTPS_PROXY={surface.build_https_proxy}"
                and tokens[6] == str(BUILD_CONTEXT))
    if sub == "image":
        return tokens in {
            ("docker", "image", "inspect", SELF_IMAGE_REF, "--format", "{{.Id}}"),
            ("docker", "image", "inspect", SELF_IMAGE_REF, "--format", "{{json .Config}}"),
        }
    if sub == "volume":
        if len(tokens) == 6 and tokens[2] == "inspect" and tokens[4:] == ("--format", "{{.Driver}}"):
            return tokens[3] == surface.prod_volume or tokens[3] in surface.disposable
        if len(tokens) == 4 and tokens[2] == "rm":
            return tokens[3] in surface.disposable
        return False
    if sub == "inspect":
        if len(tokens) != 5 or tokens[3] != "--format":
            return False
        name, fmt = tokens[2], tokens[4]
        if fmt == "{{json .State}}" and name in surface.prod_containers:
            return True
        return (fmt == "{{json .Config}}" and name == prod_container_name(surface.project, PROD_SERVICE))
    if sub == "run":
        return _run_shape_allowed(tokens, surface)
    if sub == "rm":
        return len(tokens) == 4 and tokens[2] == "--force" and tokens[3] in surface.disposable
    return False


def _mount_name(mount: str) -> str:
    return mount.split(":")[0] if ":" in mount else ""


def _run_shape_allowed(tokens: tuple[str, ...], surface: Surface) -> bool:
    """docker run 形态：helper 恒 --rm + --network none，且 head + 挂载 + tail
    精确全长（头尾之间插入任何 docker 选项如 ``--privileged`` 一律不匹配）；
    冒烟 server 唯一例外（-d + loopback 发布端口 + 一次性名，同样精确全长）。"""
    # 版本探针 / uid 探针：无卷、无端口、--network none
    if tokens == ("docker", "run", "--rm", "--network", "none",
                  "--entrypoint", "/usr/bin/minio", SELF_IMAGE_REF, "--version"):
        return True
    if tokens == ("docker", "run", "--rm", "--network", "none",
                  "--entrypoint", "/bin/sh", SELF_IMAGE_REF, "-c", "id -u"):
        return True
    # 卷挂载 helper：--rm + --network none；生产卷恒 :ro 只读（顶层属主 /
    # 递归 UID 普查 / 数据量探针）
    helper_head = ("docker", "run", "--rm", "--network", "none", "-v")
    ro_tails = (
        ("--entrypoint", "/bin/ls", SELF_IMAGE_REF, "-lan", "/probe"),
        # 递归普查：-A 含隐藏条目（.minio.sys）但排除 . / ..（容器根 / 属主
        # 不得污染普查）；busybox ls（alpine runtime）支持 -l -n -A -R
        ("--entrypoint", "/bin/ls", SELF_IMAGE_REF, "-lnAR", "/probe"),
        ("--entrypoint", "/bin/du", SELF_IMAGE_REF, "-sh", "/probe"),
    )
    for tail in ro_tails:
        if _exact_helper(tokens, helper_head, tail):
            mount = tokens[6]
            name = _mount_name(mount)
            if not mount.endswith(":/probe:ro"):
                return False
            return name == surface.prod_volume or name in surface.disposable
    # 数据探针（一次性卷 rw）：uid 1000 在 /data 写/读/删
    if _exact_helper(tokens, helper_head,
                     ("--entrypoint", "/bin/sh", SELF_IMAGE_REF, "-c", DATA_PROBE_SCRIPT)):
        mount = tokens[6]
        return mount.endswith(":/data") and _mount_name(mount) in surface.disposable
    # 冒烟 server：唯一非 --network none 形态（loopback 发布 + 一次性名，共 20 token）
    if tokens[:4] == ("docker", "run", "-d", "--name") and len(tokens) == 20:
        name, mount = tokens[4], tokens[14]
        vol = _mount_name(mount)
        expected_tail = ("-p", f"127.0.0.1:{SMOKE_API_PORT}:9000",
                         "-p", f"127.0.0.1:{SMOKE_CONSOLE_PORT}:9001",
                         "-e", "MINIO_ROOT_USER=aios", "-e", "MINIO_ROOT_PASSWORD=aios12345",
                         "-v", f"{vol}:/data", SELF_IMAGE_REF,
                         "server", "/data", "--console-address", ":9001")
        return (name in surface.disposable and vol in surface.disposable
                and tuple(tokens[5:]) == expected_tail)
    return False


class GateRunner:
    """结构性 argv 门：不在白名单内即在任何执行之前拒绝（fail-closed）。"""

    def __init__(self, inner: Runner, surface: Surface) -> None:
        self._inner = inner
        self._surface = surface

    def run(self, argv: tuple[str, ...] | list[str], *, timeout: float = 60.0,
            encoding: str | None = None) -> CommandResult:
        tokens = tuple(str(item) for item in argv)
        if not is_allowed_docker_argv(tokens, self._surface):
            raise CommandNotAllowedError("argv-not-whitelisted")
        return self._inner.run(tokens, timeout=timeout, encoding=encoding)


# ---------------------------------------------------------------- Transport / Sleeper / Clock


@dataclass(frozen=True)
class HttpResult:
    """单请求结果：状态码或安全错误类别（仅类别 + 异常类名，无文本）。"""

    status: int | None
    error_category: str | None
    error_class: str | None


class Transport(Protocol):
    def get(self, host: str, port: int, path: str, *, timeout: float) -> HttpResult: ...


class RealTransport:
    """http.client 直连（恒字面 loopback；从不读取 proxy 环境变量）。"""

    def get(self, host: str, port: int, path: str, *, timeout: float) -> HttpResult:
        try:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
            try:
                conn.request("GET", path, headers={"User-Agent": USER_AGENT})
                response = conn.getresponse()
                response.read()
                return HttpResult(response.status, None, None)
            finally:
                conn.close()
        except BaseException as cause:  # noqa: BLE001 —— 仅类别/类名入档
            if isinstance(cause, ConnectionRefusedError):
                category = "connection-refused"
            elif isinstance(cause, (TimeoutError, socket.timeout)):
                category = "timeout"
            else:
                category = "http-error"
            return HttpResult(None, category, type(cause).__name__)


class Sleeper(Protocol):
    def sleep(self, seconds: float) -> None: ...


class RealSleeper:
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class Clock(Protocol):
    def utc_now_iso(self) -> str: ...
    def stamp(self) -> str: ...


class RealClock:
    def utc_now_iso(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def stamp(self) -> str:
        return time.strftime("%Y%m%d-%H%M%S", time.gmtime())


# ---------------------------------------------------------------- 纯解析（无 IO）


def version_probe_ok(stdout: str) -> bool:
    """版本元数据断言：输出同时含 pin 的 RELEASE 与完整 commit。"""
    return MINIO_RELEASE in stdout and MINIO_COMMIT in stdout


def parse_ls_lan_ownership(stdout: str) -> dict[str, object]:
    """``ls -lan /probe`` → 顶层条目 uid 直方图（root=0 / minio=1000 判定输入）。

    跳过 ``total N`` 头与空行；字段序 = perms links uid gid size date name，
    uid 取第 3 列。解析不了的行如实计入 unparsed（绝不静默丢弃）。
    """
    uid_counts: dict[str, int] = {}
    entries = 0
    unparsed = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("total "):
            continue
        fields = line.split()
        if len(fields) >= 3 and fields[0] and fields[0][0] in "dlbcps-":
            uid_counts[fields[2]] = uid_counts.get(fields[2], 0) + 1
            entries += 1
        else:
            unparsed += 1
    return {"entries": entries, "uid_counts": uid_counts, "unparsed": unparsed}


def ownership_suitable(ownership: dict[str, object]) -> bool | None:
    """属主适合性：全部顶层条目 uid 1000 → True；任一非 1000 → False（需迁移）；
    空卷/无法解析 → None（不确定——fail-closed 按 blocked 处理）。"""
    entries = ownership.get("entries")
    if not isinstance(entries, int) or entries <= 0:
        return None
    uid_counts = ownership.get("uid_counts")
    if not isinstance(uid_counts, dict) or not uid_counts:
        return None
    return all(uid == EXPECTED_RUNTIME_UID for uid in uid_counts)


def parse_recursive_uid_census(stdout: str) -> dict[str, object]:
    """``ls -lnAR /probe`` → 递归 UID 普查（顶层 + 全部嵌套路径）。

    子目录头（``/probe/sub:`` 形态）与 ``total N``/空行跳过；其余行字段序 =
    perms links uid gid size date name，uid 取第 3 列。解析不了的行如实计入
    unparsed（普查不完整 → 判定不确定，绝不静默丢弃）。
    """
    uid_counts: dict[str, int] = {}
    entries = 0
    unparsed = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("total "):
            continue
        if line.endswith(":") and line[0] not in "dlbcps-":
            continue  # 子目录头（如 /probe/.minio.sys:）
        fields = line.split()
        if len(fields) >= 3 and fields[0] and fields[0][0] in "dlbcps-":
            uid_counts[fields[2]] = uid_counts.get(fields[2], 0) + 1
            entries += 1
        else:
            unparsed += 1
    return {"entries": entries, "uid_counts": uid_counts, "unparsed": unparsed}


def recursive_ownership_suitable(census: dict[str, object]) -> bool | None:
    """递归普查判定：全部观察路径 uid 1000 → True；任一非 1000 → False（需
    迁移）；空卷/存在未解析行/普查缺失 → None（不确定——fail-closed 按
    blocked 处理）。采纳 pass 要求递归普查确证 True，仅顶层全 1000 不足。"""
    entries = census.get("entries")
    unparsed = census.get("unparsed")
    uid_counts = census.get("uid_counts")
    if not isinstance(entries, int) or entries <= 0:
        return None
    if not isinstance(unparsed, int) or unparsed != 0:
        return None
    if not isinstance(uid_counts, dict) or not uid_counts:
        return None
    return all(uid == EXPECTED_RUNTIME_UID for uid in uid_counts)


def parse_du_size(stdout: str) -> str:
    token = stdout.strip().split()
    return token[0] if token else "unknown"


def _json_field(stdout: str, *keys: str) -> object:
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    for key in keys[:-1]:
        value = payload.get(key)
        if not isinstance(value, dict):
            return None
        payload = value
    return payload.get(keys[-1])


def _run_or_failure(runner: Runner, argv: list[str], *, timeout: float,
                    ) -> tuple[CommandResult | None, dict[str, str] | None]:
    try:
        return runner.run(tuple(argv), timeout=timeout), None
    except BaseException as cause:  # noqa: BLE001 —— 仅类别/类名入档
        category, klass = categorize_runner_exception(cause)
        return None, {"category": category, "class": klass}


def _check_engine(runner: Runner) -> dict[str, object]:
    result, failure = _run_or_failure(
        runner, ["docker", "version", "--format", "{{.Server.Version}}"],
        timeout=ENGINE_TIMEOUT_SECONDS)
    if failure is not None or result is None or result.returncode != 0:
        return {"status": "failed", "failure": failure or {"category": "engine-unavailable"}}
    return {"status": "ok", "server_version": result.stdout.strip()}


# ---------------------------------------------------------------- build 模式


def run_build(runner: Runner, https_proxy: str | None = None) -> dict[str, object]:
    """只构建 compose 锚定镜像（docker build，零 compose/零生产触碰）。

    ``https_proxy`` 为操作者经 ``AIOS_MINIO_BUILD_HTTPS_PROXY`` 提供的值时，
    仅作为 Docker 预定义 ``HTTPS_PROXY`` build arg 传入（GOPROXY/Dockerfile/
    镜像 pin 不变；值不入报告/日志）。构建返回 0 之后仍需 image inspect 回显
    非空镜像 Id——探针失败/无 Id 一律 fail-closed 记失败。
    """
    started = time.monotonic()
    argv = ["docker", "build", "-t", SELF_IMAGE_REF]
    if https_proxy:
        argv += ["--build-arg", f"HTTPS_PROXY={https_proxy}"]
    argv.append(str(BUILD_CONTEXT))
    result, failure = _run_or_failure(runner, argv, timeout=BUILD_TIMEOUT_SECONDS)
    elapsed = round(time.monotonic() - started, 1)
    facts: dict[str, object] = {"mode": "build", "elapsed_seconds": elapsed}
    if failure is not None:
        facts.update(ok=False, failure=failure)
        return facts
    assert result is not None
    output_tail = (result.stdout + result.stderr).strip().splitlines()[-BUILD_OUTPUT_TAIL_LINES:]
    facts["output_tail"] = output_tail
    if result.returncode != 0:
        facts.update(ok=False, returncode=result.returncode)
        return facts
    probe, probe_failure = _run_or_failure(
        runner, ["docker", "image", "inspect", SELF_IMAGE_REF, "--format", "{{.Id}}"],
        timeout=INSPECT_TIMEOUT_SECONDS)
    image_id: str | None = None
    if probe_failure is None and probe is not None and probe.returncode == 0:
        image_id = probe.stdout.strip() or None
    if image_id is None:
        # 构建后镜像不可见（inspect 失败/无 Id）→ fail-closed：绝不标记成功
        facts.update(ok=False, image_id=None, problem="post-build-inspect-failed",
                     failure=probe_failure or {"category": "post-build-inspect-failed"})
        return facts
    facts.update(ok=True, image_id=image_id)
    return facts


# ---------------------------------------------------------------- smoke 模式


def smoke_ports_free(transport: Transport) -> bool:
    """端口占用探测：任何成功响应/超时都按占用处理（fail-closed）。"""
    for port in (SMOKE_API_PORT, SMOKE_CONSOLE_PORT):
        probe = transport.get("127.0.0.1", port, "/", timeout=PORT_PROBE_TIMEOUT_SECONDS)
        if probe.error_category != "connection-refused":
            return False
    return True


def wait_for_cluster_health(transport: Transport, sleeper: Sleeper) -> tuple[bool, int]:
    """/minio/health/cluster 轮询至 200（attempts 上限）；返回（是否就绪, 尝试数）。"""
    for attempt in range(1, HEALTH_ATTEMPTS + 1):
        probe = transport.get("127.0.0.1", SMOKE_API_PORT, "/minio/health/cluster",
                              timeout=HEALTH_TIMEOUT_SECONDS)
        if probe.status == 200:
            return True, attempt
        sleeper.sleep(HEALTH_INTERVAL_SECONDS)
    return False, HEALTH_ATTEMPTS


def _cleanup_disposable(runner: Runner, container: str, volume: str,
                        problems: list[str]) -> None:
    """finally 恒清理：只删本轮生成的两个一次性名（前缀再断言一次）。"""
    if container.startswith(DISPOSABLE_PREFIX):
        result, failure = _run_or_failure(
            runner, ["docker", "rm", "--force", container], timeout=RUN_PROBE_TIMEOUT_SECONDS)
        if failure is not None or result is None or result.returncode != 0:
            problems.append("cleanup-container-failed")
    else:
        problems.append("cleanup-container-name-refused")
    if volume.startswith(DISPOSABLE_PREFIX):
        result, failure = _run_or_failure(
            runner, ["docker", "volume", "rm", volume], timeout=RUN_PROBE_TIMEOUT_SECONDS)
        if failure is not None or result is None or result.returncode != 0:
            problems.append("cleanup-volume-failed")
    else:
        problems.append("cleanup-volume-name-refused")


def run_smoke(runner: Runner, transport: Transport, sleeper: Sleeper,
              log: SafeLog, stamp: str) -> dict[str, object]:
    """一次性冒烟：生成名容器/卷 → cluster 健康 → uid 1000 数据探针 → 清理。"""
    container, volume = disposable_names(stamp)
    facts: dict[str, object] = {"mode": "smoke", "container": container, "volume": volume}
    problems: list[str] = []
    checks: dict[str, object] = {}
    if not smoke_ports_free(transport):
        log.say(f"冒烟拒绝: 127.0.0.1:{SMOKE_API_PORT}/{SMOKE_CONSOLE_PORT} 端口被占用"
                "（fail-closed，零冒烟副作用）")
        return {**facts, "status": "refused", "reason": "smoke-ports-busy", "problems": []}
    # 镜像在场预检：缺失即拒绝——杜绝 docker run 对缺失镜像的隐式 pull
    presence, failure = _run_or_failure(
        runner, ["docker", "image", "inspect", SELF_IMAGE_REF, "--format", "{{.Id}}"],
        timeout=INSPECT_TIMEOUT_SECONDS)
    if failure is not None or presence is None or presence.returncode != 0:
        log.say(f"冒烟拒绝: {LOCAL_IMAGE_MISSING}（先跑 build 模式；绝不隐式 pull）")
        return {**facts, "status": "refused", "reason": LOCAL_IMAGE_MISSING, "problems": []}
    try:
        argv = ["docker", "run", "-d", "--name", container,
                "-p", f"127.0.0.1:{SMOKE_API_PORT}:9000",
                "-p", f"127.0.0.1:{SMOKE_CONSOLE_PORT}:9001",
                "-e", "MINIO_ROOT_USER=aios", "-e", "MINIO_ROOT_PASSWORD=aios12345",
                "-v", f"{volume}:/data", SELF_IMAGE_REF,
                "server", "/data", "--console-address", ":9001"]
        result, failure = _run_or_failure(runner, argv, timeout=SMOKE_RUN_TIMEOUT_SECONDS)
        if failure is not None or result is None or result.returncode != 0:
            problems.append("container-start-failed")
            facts["failure"] = failure or {"category": "container-start-failed"}
            checks["container_start"] = False
        else:
            checks["container_start"] = True
            healthy, attempts = wait_for_cluster_health(transport, sleeper)
            facts["cluster_health_attempts"] = attempts
            checks["cluster_health_200"] = healthy
            if not healthy:
                problems.append("cluster-health-timeout")
        if checks.get("cluster_health_200"):
            probe, failure = _run_or_failure(
                runner, ["docker", "run", "--rm", "--network", "none", "-v",
                         f"{volume}:/data", "--entrypoint", "/bin/sh",
                         SELF_IMAGE_REF, "-c", DATA_PROBE_SCRIPT],
                timeout=RUN_PROBE_TIMEOUT_SECONDS)
            checks["uid1000_data_probe"] = failure is None and probe is not None and probe.returncode == 0
            if not checks["uid1000_data_probe"]:
                problems.append("uid1000-data-probe-failed")
        version, failure = _run_or_failure(
            runner, ["docker", "run", "--rm", "--network", "none",
                     "--entrypoint", "/usr/bin/minio", SELF_IMAGE_REF, "--version"],
            timeout=RUN_PROBE_TIMEOUT_SECONDS)
        checks["version_reports_pin"] = (failure is None and version is not None
                                         and version.returncode == 0
                                         and version_probe_ok(version.stdout))
        if not checks["version_reports_pin"]:
            problems.append("version-not-pinned")
        facts["checks"] = checks
        facts["problems"] = problems
        return facts
    finally:
        _cleanup_disposable(runner, container, volume, problems)
        cleanup_ok = not any(p.startswith("cleanup-") for p in problems)
        # 清理失败必须使冒烟整体失败（CLI 退 EXIT_REJECT）——运行时检查事实
        # 保留在 checks 里，但 ok 以含清理问题的最终 problems 集为准
        facts["cleanup_ok"] = cleanup_ok
        facts["problems"] = problems
        facts["ok"] = not problems
        log.say(f"冒烟清理: container={container} volume={volume} ok={cleanup_ok}")


# ---------------------------------------------------------------- preflight 模式


def collect_image_metadata(runner: Runner) -> dict[str, object]:
    """本地镜像面：在场事实 + Config.User/Entrypoint + Id。"""
    facts: dict[str, object] = {"status": "ok", "present": False}
    result, failure = _run_or_failure(
        runner, ["docker", "image", "inspect", SELF_IMAGE_REF, "--format", "{{.Id}}"],
        timeout=INSPECT_TIMEOUT_SECONDS)
    if failure is not None:
        facts.update(status="failed", failure=failure)
        return facts
    assert result is not None
    if result.returncode != 0:
        facts["missing_vocabulary"] = LOCAL_IMAGE_MISSING
        return facts
    facts["present"] = True
    facts["image_id"] = result.stdout.strip()
    result, failure = _run_or_failure(
        runner, ["docker", "image", "inspect", SELF_IMAGE_REF, "--format", "{{json .Config}}"],
        timeout=INSPECT_TIMEOUT_SECONDS)
    if failure is not None or result is None or result.returncode != 0:
        facts.update(status="failed", failure=failure or {"category": "inspect-failed"})
        return facts
    facts["config_user"] = _json_field(result.stdout, "User")
    facts["entrypoint"] = _json_field(result.stdout, "Entrypoint")
    return facts


def collect_runtime_uid(runner: Runner) -> dict[str, object]:
    """运行时 uid 探针：--network none 的 --rm 容器跑 id -u（期望 1000）。"""
    argv = ["docker", "run", "--rm", "--network", "none", "--entrypoint", "/bin/sh",
            SELF_IMAGE_REF, "-c", "id -u"]
    result, failure = _run_or_failure(runner, argv, timeout=RUN_PROBE_TIMEOUT_SECONDS)
    if failure is not None or result is None or result.returncode != 0:
        return {"status": "failed", "failure": failure or {"category": "uid-probe-failed"},
                "uid": None}
    return {"status": "ok", "uid": result.stdout.strip()}


def collect_anchor_config() -> dict[str, object]:
    """compose/Dockerfile 锚点静态核验（纯文本，零 subprocess）。"""
    checks: dict[str, bool] = {}
    try:
        compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
        dockerfile_text = DOCKERFILE.read_text(encoding="utf-8")
    except OSError:
        return {"status": "failed", "checks": checks}
    checks["compose_image_anchor"] = f"image: aios/minio:{MINIO_RELEASE}" in compose_text
    checks["compose_build_context"] = "context: ./minio" in compose_text
    checks["compose_volume_binding"] = f"- {PROD_VOLUME_KEY}:/data" in compose_text
    checks["dockerfile_release_arg"] = f"ARG MINIO_RELEASE={MINIO_RELEASE}" in dockerfile_text
    checks["dockerfile_commit_arg"] = f"ARG MINIO_COMMIT={MINIO_COMMIT}" in dockerfile_text
    checks["dockerfile_nonroot_user"] = "USER minio:minio" in dockerfile_text
    checks["dockerfile_entrypoint"] = 'ENTRYPOINT ["/usr/bin/minio"]' in dockerfile_text
    return {"status": "ok", "checks": checks}


def collect_stack_health(runner: Runner, project: str) -> dict[str, object]:
    """生产六容器健康只读快照（逐容器 docker inspect State）。"""
    facts: dict[str, object] = {"status": "ok", "services": {}}
    for service in STACK_SERVICES:
        argv = ["docker", "inspect", prod_container_name(project, service),
                "--format", "{{json .State}}"]
        result, failure = _run_or_failure(runner, argv, timeout=INSPECT_TIMEOUT_SECONDS)
        if failure is not None or result is None or result.returncode != 0:
            facts["status"] = "failed"
            facts["failure"] = failure or {"category": "stack-inspect-failed"}
            return facts
        facts["services"][service] = {  # type: ignore[index]
            "state": _json_field(result.stdout, "Status"),
            "health": _json_field(result.stdout, "Health", "Status") or "none",
        }
    return facts


def collect_prod_volume(runner: Runner, project: str) -> dict[str, object]:
    """生产卷只读盘点：元数据 + 属主（:ro + --network none helper）+ 数据量。"""
    volume = prod_volume_name(project)
    facts: dict[str, object] = {"status": "ok", "volume": volume}
    result, failure = _run_or_failure(
        runner, ["docker", "volume", "inspect", volume, "--format", "{{.Driver}}"],
        timeout=INSPECT_TIMEOUT_SECONDS)
    if failure is not None or result is None or result.returncode != 0:
        facts.update(status="failed", failure=failure or {"category": "volume-inspect-failed"})
        return facts
    facts["driver"] = result.stdout.strip()
    ls_argv = ["docker", "run", "--rm", "--network", "none", "-v", f"{volume}:/probe:ro",
               "--entrypoint", "/bin/ls", SELF_IMAGE_REF, "-lan", "/probe"]
    result, failure = _run_or_failure(runner, ls_argv, timeout=RUN_PROBE_TIMEOUT_SECONDS)
    if failure is not None or result is None or result.returncode != 0:
        facts.update(status="failed", failure=failure or {"category": "ownership-probe-failed"})
        return facts
    ownership = parse_ls_lan_ownership(result.stdout)
    facts["ownership"] = ownership
    # 递归 UID 普查（只读 + --network none）：顶层全 1000 不足以为据——嵌套
    # 路径任一非 1000 同样阻塞采纳；普查无法完成（helper 失败/未解析行）→
    # 不确定，fail-closed 按 blocked
    census_argv = ["docker", "run", "--rm", "--network", "none", "-v", f"{volume}:/probe:ro",
                   "--entrypoint", "/bin/ls", SELF_IMAGE_REF, "-lnAR", "/probe"]
    census: dict[str, object] | None = None
    result, failure = _run_or_failure(runner, census_argv, timeout=DU_PROBE_TIMEOUT_SECONDS)
    if failure is None and result is not None and result.returncode == 0:
        census = parse_recursive_uid_census(result.stdout)
    else:
        facts["ownership_recursive_failure"] = failure or {"category": "census-probe-failed"}
    if census is not None:
        facts["ownership_recursive"] = census
        facts["ownership_suitable"] = recursive_ownership_suitable(census)
    else:
        facts["ownership_recursive"] = None
        facts["ownership_suitable"] = None
    du_argv = ["docker", "run", "--rm", "--network", "none", "-v", f"{volume}:/probe:ro",
               "--entrypoint", "/bin/du", SELF_IMAGE_REF, "-sh", "/probe"]
    result, failure = _run_or_failure(runner, du_argv, timeout=DU_PROBE_TIMEOUT_SECONDS)
    facts["data_size_hint"] = (parse_du_size(result.stdout)
                               if failure is None and result is not None and result.returncode == 0
                               else "unknown")
    return facts


def evaluate_preflight(collectors: dict[str, dict[str, object]]) -> dict[str, object]:
    """只读事实 → 采纳判定（pass/blocked/incomplete + checks/counts）。

    fail-closed 面：镜像缺失/身份或入口点不符/运行时 uid≠1000/版本不符/
    锚点核验不过/六服务非全健康/卷属主非 uid 1000 或不可判 → blocked。
    """
    checks: list[dict[str, str]] = []
    partial = any(c.get("status") == "failed" for c in collectors.values())

    image = collectors.get("image", {})
    if image.get("present") is True:
        checks.append(_check("local-image-present", SELF_IMAGE_REF, "ok",
                             f"镜像在场（Id={image.get('image_id')}）——M14-39 恢复预检前置已解除"))
        user_ok = image.get("config_user") == EXPECTED_CONFIG_USER
        checks.append(_check("image-config-user", SELF_IMAGE_REF,
                             "ok" if user_ok else "critical",
                             f"Config.User={image.get('config_user')}"
                             f"（期望 {EXPECTED_CONFIG_USER}，adduser -S -u 1000 即 uid 1000）"))
        entrypoint = json.dumps(image.get("entrypoint"))
        entry_ok = image.get("entrypoint") == ["/usr/bin/minio"]
        checks.append(_check("image-entrypoint", SELF_IMAGE_REF,
                             "ok" if entry_ok else "critical",
                             f"Entrypoint={entrypoint}"))
    else:
        checks.append(_check("local-image-present", SELF_IMAGE_REF, "critical",
                             f"{LOCAL_IMAGE_MISSING}（先跑 build 模式：获准窗口构建后重试）"))

    uid = collectors.get("runtime_uid", {})
    if uid.get("status") == "ok":
        uid_ok = uid.get("uid") == EXPECTED_RUNTIME_UID
        checks.append(_check("runtime-uid", SELF_IMAGE_REF, "ok" if uid_ok else "critical",
                             f"id -u={uid.get('uid')}（期望 {EXPECTED_RUNTIME_UID}）"))
    else:
        checks.append(_check("runtime-uid", SELF_IMAGE_REF, "critical", "uid 探针失败"))

    version = collectors.get("version_probe", {})
    if version.get("matches_pin") is True:
        checks.append(_check("version-metadata", SELF_IMAGE_REF, "ok",
                             f"{MINIO_RELEASE} + commit pin 一致"))
    else:
        checks.append(_check("version-metadata", SELF_IMAGE_REF, "critical",
                             "版本探针与 pin 不一致（或探针失败）"))

    anchor = collectors.get("anchor_config", {})
    if anchor.get("status") == "ok":
        failed_anchors = [name for name, ok in anchor.get("checks", {}).items() if not ok]  # type: ignore[union-attr]
        checks.append(_check("anchor-config", "compose + Dockerfile",
                             "ok" if not failed_anchors else "critical",
                             "锚点核验 7/7 一致" if not failed_anchors
                             else f"锚点漂移: {','.join(failed_anchors)}"))
    else:
        checks.append(_check("anchor-config", "compose + Dockerfile", "critical",
                             "锚点文件不可读"))

    stack = collectors.get("stack_health", {})
    if stack.get("status") == "ok":
        services = stack.get("services", {})
        unhealthy = [svc for svc, fact in services.items()  # type: ignore[union-attr]
                     if not (isinstance(fact, dict) and fact.get("health") == "healthy")]
        checks.append(_check("stack-health", "six services",
                             "ok" if not unhealthy else "critical",
                             "6/6 healthy" if not unhealthy else f"非全健康: {','.join(unhealthy)}"))
    else:
        checks.append(_check("stack-health", "six services", "critical", "健康快照采集失败"))

    volume = collectors.get("prod_volume", {})
    if volume.get("status") == "ok":
        suitable = volume.get("ownership_suitable")
        census = volume.get("ownership_recursive")
        uids = (",".join(sorted(census.get("uid_counts", {})))  # type: ignore[union-attr]
                if isinstance(census, dict) else "none")
        if suitable is True:
            checks.append(_check("prod-volume-ownership", str(volume.get("volume")), "ok",
                                 "递归普查全部观察路径 uid 1000——无迁移前置"))
        elif suitable is False:
            checks.append(_check("prod-volume-ownership", str(volume.get("volume")), "critical",
                                 f"递归普查 uid 集={uids or 'none'} → 采纳 blocked：需一次性迁移"
                                 "（M14-13 README 采纳注记：先备份再 chown -R 1000:1000 或重建卷）"))
        else:
            checks.append(_check("prod-volume-ownership", str(volume.get("volume")), "critical",
                                 "递归属主普查不可判（空卷/普查失败/存在未解析行）——fail-closed 按 blocked"))
    else:
        checks.append(_check("prod-volume-ownership", prod_volume_name(DEFAULT_PROJECT),
                             "critical", "卷属主探针失败"))

    counts = {"ok": 0, "warn": 0, "critical": 0}
    for check in checks:
        counts[check["severity"]] += 1
    # 判定优先级：存在 critical → blocked（比 incomplete 更可行动）；
    # 无 critical 但采集不完整 → incomplete；否则 pass
    if counts["critical"]:
        verdict = "blocked"
    elif partial:
        verdict = "incomplete"
    else:
        verdict = "pass"
    return {"checks": checks, "counts": counts, "partial": partial, "adoption": verdict}


def _check(check_id: str, subject: str, severity: str, detail: str) -> dict[str, str]:
    return {"check_id": check_id, "subject": subject, "severity": severity, "detail": detail}


def run_preflight(runner: Runner, project: str) -> tuple[dict[str, object], dict[str, object]]:
    """只读盘点全部采集器 + 判定。镜像缺失时不跑 docker run（杜绝隐式 pull）。"""
    collectors: dict[str, dict[str, object]] = {"engine": _check_engine(runner)}
    if collectors["engine"].get("status") == "ok":
        collectors["image"] = collect_image_metadata(runner)
        if collectors["image"].get("present") is True:
            collectors["runtime_uid"] = collect_runtime_uid(runner)
            version, failure = _run_or_failure(
                runner, ["docker", "run", "--rm", "--network", "none",
                         "--entrypoint", "/usr/bin/minio", SELF_IMAGE_REF, "--version"],
                timeout=RUN_PROBE_TIMEOUT_SECONDS)
            collectors["version_probe"] = {
                "status": "ok",
                "matches_pin": (failure is None and version is not None
                                and version.returncode == 0
                                and version_probe_ok(version.stdout)),
            }
        else:
            collectors["runtime_uid"] = {"status": "failed",
                                         "failure": {"category": "image-missing"}}
            collectors["version_probe"] = {"status": "failed",
                                           "failure": {"category": "image-missing"}}
        collectors["anchor_config"] = collect_anchor_config()
        collectors["stack_health"] = collect_stack_health(runner, project)
        # 属主探针的 helper 以自建镜像运行——镜像缺失时不发起（杜绝隐式 pull）
        if collectors["image"].get("present") is True:
            collectors["prod_volume"] = collect_prod_volume(runner, project)
        else:
            collectors["prod_volume"] = {"status": "failed",
                                         "failure": {"category": "image-missing"}}
    results = evaluate_preflight(collectors)
    return collectors, results


# ---------------------------------------------------------------- 日志（防御性脱敏）


SECRET_SHAPE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"), "[REDACTED:token]"),
    (re.compile(r"(?i)(api[_-]?key|secret|password)\s*[=:]\s*\S{8,}"), "[REDACTED:credential]"),
)


def redact_secrets(text: str) -> str:
    for pattern, label in SECRET_SHAPE_PATTERNS:
        text = pattern.sub(label, text)
    return text


class SafeLog:
    """行式日志：stdout 输出前经 redact_secrets 防御性脱敏。"""

    def __init__(self, echo: bool = True) -> None:
        self.lines: list[str] = []
        self._echo = echo

    def say(self, message: str) -> None:
        line = redact_secrets(message)
        self.lines.append(line)
        if self._echo:
            print(f"{TAG} {line}", flush=True)


# ---------------------------------------------------------------- 报告（原子写）


REPORT_BOUNDARIES: tuple[str, ...] = (
    (
        "production surface is strictly read-only: docker version / image inspect / inspect /"
        " volume inspect on the pinned prod containers and volume; zero stop/rm/restart/up/down/"
        "exec, zero `docker compose`, zero pull"
    ),
    (
        "the prod volume is mounted only read-only (:ro at /probe) into --network none throwaway"
        " --rm helper containers which are removed after each probe"
    ),
    (
        "build mode runs exactly one docker build of the compose-pinned image from infra/minio;"
        " no compose project operation of any kind"
    ),
    (
        "smoke mode uses only generated disposable names with the strict aios-m14-40- prefix"
        " (container + volume, removed in a finally block); busy loopback ports or name collisions"
        " fail closed before any side effect; cleanup is scoped to exactly those two names"
    ),
    (
        "every docker argv must match one of the fixed structural shapes (constant image ref,"
        " derived prod names, generated disposable names); any deviation is rejected before"
        " execution"
    ),
    (
        "smoke env uses the repo-public compose dev placeholders; no env secret is ever read"
        " (including infra/env.production-recovery)"
    ),
    (
        "adoption is reported blocked on: missing image, wrong user/entrypoint/version,"
        " non-uid-1000 or uncertain volume ownership, or non-healthy stack — exactly the"
        " fail-closed set required by the M14-40 task"
    ),
    (
        "adoption=pass is not production readiness; the chown migration + up -d --no-build"
        " adoption operation itself is a later controlled slice; production_ready stays false"
    ),
    (
        "default artifact directory is .verify/artifacts/m14-40-minio-image-adoption under the"
        " repo root and is gitignored; a custom --artifact-dir is an explicit operator selection"
        " whose location and gitignore status is the operator's responsibility"
    ),
)


def artifact_dir_note(directory: Path) -> str:
    if directory == ARTIFACT_DIR:
        return "默认目录（gitignored，不入库）"
    return "自定义目录（操作者显式自选，位置与入库与否由操作者负责）"


def build_config(*, mode: str, project: str, stamp: str) -> dict[str, object]:
    container, volume = disposable_names(stamp)
    return {
        "mode": mode,
        "project": project,
        "compose_file": COMPOSE_FILE.name,
        "self_image_ref": SELF_IMAGE_REF,
        "minio_release": MINIO_RELEASE,
        "minio_commit": MINIO_COMMIT,
        "prod_container": prod_container_name(project, PROD_SERVICE),
        "prod_volume": prod_volume_name(project),
        "smoke_container_planned": container,
        "smoke_volume_planned": volume,
    }


def build_report(*, mode: str, started_utc: str, ended_utc: str, config: dict[str, object],
                 payload: dict[str, object] | None) -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "milestone": MILESTONE,
        "mode": mode,
        "started_at_utc": started_utc,
        "ended_at_utc": ended_utc,
        "config": config,
        "boundaries": list(REPORT_BOUNDARIES),
    }
    report["payload"] = payload if payload is not None else {"status": "planned"}
    return report


def render_markdown(report: dict[str, object]) -> str:
    config = report["config"]
    assert isinstance(config, dict)
    lines: list[str] = [
        f"# {report['milestone']} MinIO 镜像采纳报告（mode={report['mode']}）",
        "",
        f"- 工具：`{report['tool']}`（schema_version={report['schema_version']}）",
        f"- 开始（UTC）：{report['started_at_utc']}；结束（UTC）：{report['ended_at_utc']}",
        f"- 自建镜像锚点：`{config['self_image_ref']}`",
    ]
    payload = report["payload"]
    assert isinstance(payload, dict)
    if payload.get("status") == "planned":
        lines.append("- 三模式：build（构建锚定镜像）/ smoke（一次性容器冒烟）/"
                     " preflight（只读生产盘点 + 采纳判定）")
    elif report["mode"] == "preflight":
        counts = payload.get("counts", {})
        assert isinstance(counts, dict)
        lines += [
            f"- adoption=**{payload.get('adoption')}**；partial={payload.get('partial')}",
            f"- 检查计数：ok={counts.get('ok')} warn={counts.get('warn')} critical={counts.get('critical')}",
            "",
            "| 检查 | 对象 | 严重度 | 详情 |",
            "|---|---|---|---|",
        ]
        for check in payload.get("checks", []):
            assert isinstance(check, dict)
            lines.append(f"| {check['check_id']} | {check['subject']} | {check['severity']} | {check['detail']} |")
    else:
        lines.append(f"- 结果：ok={payload.get('ok', payload.get('status'))}"
                     f" problems={payload.get('problems')}")
    lines += ["", "边界："]
    lines += [f"- {item}" for item in report["boundaries"]]  # type: ignore[arg-type]
    return "\n".join(lines) + "\n"


class ReportPathError(RuntimeError):
    """报告路径非法（symlink 组件 / 越界 stem）——可见拒绝，零写入。"""


_STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _reject_symlinks(*paths: Path) -> None:
    for path in paths:
        if path.is_symlink():
            raise ReportPathError("symlink-target")
        for ancestor in path.parents:
            if ancestor.exists() and ancestor.is_symlink():
                raise ReportPathError("symlink-in-path")


def write_reports_atomic(report: dict[str, object], directory: Path,
                         stem: str) -> tuple[Path, Path]:
    """JSON + Markdown 双写：内容先经 redact_secrets 终防线，再同目录 tmp +
    os.replace 原子落盘；symlink/越界路径一律 ReportPathError（零写入）。"""
    if "/" in stem or "\\" in stem or ".." in stem or not _STEM_RE.match(stem):
        raise ReportPathError("bad-stem")
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{stem}.json"
    md_path = directory / f"{stem}.md"
    _reject_symlinks(json_path, md_path, directory)
    json_text = redact_secrets(json.dumps(report, ensure_ascii=False, indent=2))
    md_text = redact_secrets(render_markdown(report))
    for final_path, suffix, text in ((json_path, "json", json_text), (md_path, "md", md_text)):
        tmp_path = directory / f".{stem}.{suffix}.tmp"
        if tmp_path.is_symlink():
            raise ReportPathError("symlink-tmp")
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, final_path)
    return json_path, md_path


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minio_image_adoption.py",
        description="M14-40 MinIO 自建镜像：本机构建（build）/一次性冒烟（smoke）/生产采纳"
                    "前置核查（preflight）；默认无子命令 = plan（零副作用）；每模式需"
                    "--execute + 精确确认短语；绝不 pull/触碰生产容器/读取 env secret")
    parser.add_argument("--artifact-dir", type=Path, default=ARTIFACT_DIR,
                        help="报告目录（默认 .verify/artifacts/m14-40-minio-image-adoption，gitignored；"
                             "自定义路径为操作者显式自选，其位置与入库与否由操作者负责）")
    subparsers = parser.add_subparsers(dest="mode")
    for name, phrase, help_text in (
        ("build", CONFIRM_BUILD, "只构建 compose 锚定的自建镜像（docker build，零 compose 操作）"),
        ("smoke", CONFIRM_SMOKE, "一次性容器/卷冒烟：cluster 健康 + uid 1000 数据探针 + 版本核对 + 精确清理"),
        ("preflight", CONFIRM_PREFLIGHT, "只读生产盘点 + 采纳判定（blocked/pass，fail-closed）"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--execute", action="store_true", help=f"真实执行（必配 --confirm \"{phrase}\"）")
        sub.add_argument("--confirm", default="", help=f'精确确认短语："{phrase}"')
        sub.add_argument("--project", default=DEFAULT_PROJECT,
                         help=f"compose 项目名（唯一合法值 {DEFAULT_PROJECT}；"
                              "本工具只有一个预期生产面，其它值一律拒绝）")
    return parser


_PHRASES = {"build": CONFIRM_BUILD, "smoke": CONFIRM_SMOKE, "preflight": CONFIRM_PREFLIGHT}


def _write_report(log: SafeLog, args: argparse.Namespace, report: dict[str, object],
                  stamp: str) -> int:
    try:
        json_path, md_path = write_reports_atomic(
            report, args.artifact_dir, f"{args.mode}-{stamp}")
        log.say(f"报告: {json_path.name} / {md_path.name}（{artifact_dir_note(args.artifact_dir)}）")
        return EXIT_OK
    except (ReportPathError, OSError) as cause:
        log.say(f"报告写入失败（证据不可失——按拒绝处理）: {type(cause).__name__}")
        return EXIT_REJECT


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = SafeLog()
    clock = RealClock()
    if args.mode is None:
        log.say("=== M14-40 MinIO 镜像采纳 PLAN（零 subprocess / 零网络 / 零生产读取） ===")
        log.say(f"build: docker build -t {SELF_IMAGE_REF} infra/minio（compose 锚定镜像，零 compose 操作）")
        log.say(f"smoke: 一次性 aios-m14-40-* 容器/卷（127.0.0.1:{SMOKE_API_PORT}/{SMOKE_CONSOLE_PORT}）"
                "→ cluster 健康 → uid 1000 数据探针 → 版本核对 → finally 精确清理")
        log.say(f"preflight: 镜像元数据/运行时 uid/版本 + compose/Dockerfile 锚点 + 六容器健康"
                f"+ 卷 {prod_volume_name(DEFAULT_PROJECT)} 只读属主 → adoption 判定")
        for name, phrase in _PHRASES.items():
            log.say(f'执行 {name} 需: {name} --execute --confirm "{phrase}"')
        report = build_report(
            mode="plan", started_utc=clock.utc_now_iso(), ended_utc=clock.utc_now_iso(),
            config=build_config(mode="plan", project=DEFAULT_PROJECT, stamp=clock.stamp()),
            payload=None)
        try:
            write_reports_atomic(report, args.artifact_dir, f"plan-{clock.stamp()}")
            log.say(f"plan 报告目录: {args.artifact_dir}（{artifact_dir_note(args.artifact_dir)}）")
        except (ReportPathError, OSError) as cause:
            log.say(f"plan 报告写入失败（不影响退出码）: {type(cause).__name__}")
        return EXIT_OK
    project_problem = validate_project_name(args.project)
    if project_problem is not None:
        log.say(f"拒绝: --project 名非法（原因: {project_problem}）——被拒值不回显")
        return EXIT_REJECT
    if not args.execute:
        log.say(f"拒绝: {args.mode} 模式需 --execute（当前仅 plan 语义，零副作用）")
        return EXIT_REJECT
    if args.confirm != _PHRASES[args.mode]:
        log.say(f'拒绝: {args.mode} 必配 --confirm "{_PHRASES[args.mode]}"'
                "（精确匹配，当前不匹配）——零副作用")
        return EXIT_REJECT
    stamp = clock.stamp()
    config = build_config(mode=args.mode, project=args.project, stamp=stamp)
    started_utc = clock.utc_now_iso()
    log.say(f"=== M14-40 MinIO 镜像采纳 {args.mode.upper()}: project={args.project} ===")
    payload: dict[str, object]
    if args.mode == "build":
        # 唯一 env 读取点：AIOS_MINIO_BUILD_HTTPS_PROXY（严格形态校验，被拒值
        # 不回显；未设置 = 默认无代理形态不变）
        https_proxy = os.environ.get(BUILD_PROXY_ENV, "").strip() or None
        if https_proxy is not None and validate_build_https_proxy(https_proxy) is not None:
            log.say(f"拒绝: {BUILD_PROXY_ENV} 值形态非法（scheme://host[:port]）——被拒值不回显")
            return EXIT_REJECT
        surface = Surface(project=args.project, build_https_proxy=https_proxy)
        payload = run_build(GateRunner(RealRunner(), surface), https_proxy=https_proxy)
        ok = payload.get("ok") is True
    elif args.mode == "smoke":
        surface = Surface(project=args.project,
                          disposable=frozenset(disposable_names(stamp)))
        payload = run_smoke(GateRunner(RealRunner(), surface), RealTransport(),
                            RealSleeper(), log, stamp)
        ok = payload.get("ok") is True
    else:
        surface = Surface(project=args.project)
        collectors, results = run_preflight(GateRunner(RealRunner(), surface), args.project)
        payload = {**results, "collectors": collectors}
        ok = results["adoption"] == "pass"
        for check in results["checks"]:
            assert isinstance(check, dict)
            log.say(f"check: {check['check_id']} severity={check['severity']} {check['detail']}")
    ended_utc = clock.utc_now_iso()
    report = build_report(mode=args.mode, started_utc=started_utc, ended_utc=ended_utc,
                          config=config, payload=payload)
    write_rc = _write_report(log, args, report, stamp)
    if write_rc != EXIT_OK:
        return write_rc
    verdict = payload.get("adoption", payload.get("ok", payload.get("status")))
    log.say(f"=== 结果: {args.mode} {verdict}（≠ production ready） ===")
    return EXIT_OK if ok else EXIT_REJECT


if __name__ == "__main__":
    sys.exit(main())
