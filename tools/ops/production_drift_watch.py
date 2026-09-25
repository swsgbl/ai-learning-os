#!/usr/bin/env python
"""M14-127 production drift watch：只读校验生产栈运行镜像是否仍锚定
M14-124 已批准发布镜像（补 M14-124 生产切换后「健康监控不校验发布镜像
锚点」的运维缺口）。

设计（与 tools/ops/production_monitor.py 同款纪律：单文件、纯标准库、
零第三方依赖；子进程经 Runner 注入 + 只读白名单门，UTC 时间经 Clock
注入——开发回合零真实 Docker、全部行为用 fake 测试锁定；真实采集仅由
supervisor 在获准窗口运行）：

- 双模式：默认 **plan（dry-run）**——零 subprocess、零 Docker、零网络、
  零 env 读取、零生产状态宣称（plan 报告只登记计划，不出现 drift 结论）；
  **execute** 需同时满足「旗标 + 精确确认短语」（``--execute`` +
  ``--confirm "EXECUTE READ-ONLY PRODUCTION DRIFT WATCH"`` 一字不差），
  缺一即 EXIT 拒绝且**零采集**（fail-closed）。compose 项目名严格白名单
  （ASCII 字母数字开头、仅字母数字/连字符/下划线、长度 ≤64；被拒值绝不
  回显）。
- 只读采集面（固定画像，不可经 CLI 注入任意目标/锚点）：compose project
  ``aios-m14-03-production-rehearsal``（--profile local + --profile
  search，覆盖七服务）——
  ① ``docker compose ps --format json``（七服务存在性 + Health/State；
  原始输出中的 Labels/Ports 等绝不保留）；
  ② 逐容器 ``docker inspect --format``（state/health/Config.Image tag/
  .Image 运行镜像 ID 四事实）；
  ③ API/Web 锚点镜像 ``docker image inspect --format {{.Id}}``（本地
  tag → image ID 解析，抓「tag 被移到新镜像」的漂移）。
- 镜像锚点（M14-124 已批准、烘烤为常量，绝不取自请求/env/文件）：
  API ``aios/api:m14-124-production`` =
  ``sha256:c99e28c905208bffbc1576f0c5c9e042af18fd356881cee967078ee38323781f``；
  Web ``aios/web:m14-124-production`` =
  ``sha256:d596f0c726ab690359b196a3c3911f9842b94218b4361ee452413d420a38194b``。
- digest fail-closed：运行镜像 ID 与 tag 解析 ID 都必须是
  ``sha256:<64 位小写 hex>`` 精确形态且逐字符等于锚点 digest——**tag
  相同绝不冒充 digest 通过**；digest 无法取得/不可解析（容器 inspect
  失败、image inspect 失败、形态非法）→ 恒判 drift（不可证明无漂移即
  视为有漂移，缺失绝不当作匹配）。
- 子进程白名单门（结构性）：一切 docker 命令必经
  ``ReadonlyRunner`` 的 ``is_readonly_docker_command`` 校验——仅接受
  ``compose ps --format json``、``docker inspect --format <容器四事实
  格式串> <单容器>``、``docker image inspect --format {{.Id}} <单引用>``
  三形态；stop/start/restart/rm/kill/down/exec/up/build/pull/logs 等
  一律在任何执行之前拒绝；无 shell=True；Windows 侧恒
  CREATE_NO_WINDOW。
- 数据边界：绝不读取/打印容器 env、secret、日志正文、DB/MinIO/voice
  数据；compose ps 原始行（含 Labels 里的绝对路径/端口绑定）与任何
  stderr 原文绝不入档——报告仅含服务名/健康词汇/tag/digest/固定词汇
  状态与原因，写盘前再经 redact_secrets 终防线。
- 判定：七 compose 服务全部存在且 Docker health=healthy（compose ps +
  逐容器 inspect 双通道）、七容器 state=running、API/Web 运行 tag ==
  锚点 tag、运行镜像 ID == 锚点 digest、tag 解析 ID == 锚点 digest，
  全部成立才 ``drift=false``（exit 0）；任一失败/缺失/采集失败 →
  ``drift=true`` + 固定词汇 drift_reasons（exit 2，诚实失败证据照常
  落盘，绝不修复/伪装生产状态）。
- 报告：schema 版本化 JSON + Markdown **原子写入**（同目录 tmp +
  fsync + os.replace；symlink 组件与越界 stem 一律拒绝零写入）；默认
  目录 ``REPO_ROOT/.verify/artifacts/m14-127-production-drift-watch``
  **gitignored**，``--artifact-dir`` 自定义路径为操作者显式自选覆盖
  （其位置与 gitignore 状态由操作者负责）；内容含 UTC 时间戳、配置、
  逐项检查、服务健康、镜像 tag/digest/identity 安全摘要、边界声明。
- 本工具是漂移检测器，不是生产健康宣称器：``drift=false`` 仅表示本轮
  采集范围内锚点全部吻合，不表示窗口外永远无漂移，也不构成
  production readiness；release-approval 仍是 human-only 门。

用法（仓库根）：
  python tools/ops/production_drift_watch.py                        # plan（默认，零采集）
  python tools/ops/production_drift_watch.py --execute \
      --confirm "EXECUTE READ-ONLY PRODUCTION DRIFT WATCH"          # execute（只读校验）

退出码：0 plan 成功 / execute 且 drift=false；2 非法或 fail-closed
（确认缺失、非法项目名、symlink/越界路径）或 execute 且 drift=true
（含任何采集失败与证据报告写入失败——证据不可失）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

EXIT_OK = 0
#: 2 = 非法/fail-closed/execute 且 drift=true（统一可见拒绝口径）
EXIT_REFUSE = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"
ARTIFACT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-127-production-drift-watch"
TAG = "[drift-watch]"
REPORT_SCHEMA_VERSION = 1
MILESTONE = "M14-127"
TOOL_NAME = "tools/ops/production_drift_watch.py"

#: execute 门禁之二：精确确认短语（一字不差）
CONFIRM_PHRASE = "EXECUTE READ-ONLY PRODUCTION DRIFT WATCH"

DEFAULT_PROJECT = "aios-m14-03-production-rehearsal"

#: 生产 compose 启动画像：local（六受管服务）+ search（searxng）——七服务全集
COMPOSE_PROFILES: tuple[str, ...] = ("local", "search")

#: 七 compose 服务（六受管 + searxng；与 M14-124 切换后 7/7 healthy 栈一致）
STACK_SERVICES: tuple[str, ...] = (
    "postgres", "redis", "minio", "api", "web", "livekit", "searxng",
)

#: 子进程命令超时（秒）
COMPOSE_PS_TIMEOUT_SECONDS = 60.0
INSPECT_TIMEOUT_SECONDS = 30.0


# ---------------------------------------------------------------- 镜像锚点（M14-124 已批准，常量烘烤）

#: digest 严格形态：sha256: + 恰 64 位小写 hex
_DIGEST_RE = re.compile(r"^sha256:([0-9a-f]{64})$")


@dataclass(frozen=True)
class ImageAnchor:
    """发布镜像锚点：服务名 + 预期 tag + 预期 sha256 digest（64 hex）。"""

    service: str
    tag: str
    digest_hex: str

    @property
    def digest_ref(self) -> str:
        return f"sha256:{self.digest_hex}"


#: M14-124 生产切换已批准并实证的镜像锚点（证据：
#: docs/evidence/m14-124-production-cutover/README.md §1/§4）
EXPECTED_ANCHORS: dict[str, ImageAnchor] = {
    "api": ImageAnchor(
        service="api",
        tag="aios/api:m14-124-production",
        digest_hex="c99e28c905208bffbc1576f0c5c9e042af18fd356881cee967078ee38323781f",
    ),
    "web": ImageAnchor(
        service="web",
        tag="aios/web:m14-124-production",
        digest_hex="d596f0c726ab690359b196a3c3911f9842b94218b4361ee452413d420a38194b",
    ),
}
for _anchor in EXPECTED_ANCHORS.values():
    assert _DIGEST_RE.match(_anchor.digest_ref), "锚点 digest 常量形态破损"


def normalize_image_digest(value: str) -> str | None:
    """镜像引用/ID → 64 位小写 hex；非 ``sha256:<64hex>`` 精确形态 → None
    （fail-closed：不可解析绝不冒充匹配）。"""
    match = _DIGEST_RE.match(value.strip()) if isinstance(value, str) else None
    return match.group(1) if match is not None else None


# ---------------------------------------------------------------- compose 项目名（严格白名单）

#: 项目名白名单：ASCII 字母数字开头，仅字母数字/连字符/下划线，长度 ≤64
#: （空白/控制/路径/换行/markdown 字符天然被白名单排除）
MAX_PROJECT_NAME_LENGTH = 64
PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def validate_project_name(name: str) -> str | None:
    """fail-closed 校验 ``--project``（任意 CLI 输入面）。返回拒绝原因
    （固定词汇，**绝不回显被拒值**）或 None（放行）。"""
    if not name:
        return "empty"
    if len(name) > MAX_PROJECT_NAME_LENGTH:
        return "too-long"
    if not (name[0].isascii() and name[0].isalnum()):
        return "first-char-not-alnum"
    if PROJECT_NAME_RE.match(name) is None:
        return "invalid-character"
    return None


# ---------------------------------------------------------------- Runner（注入点 + 只读白名单门）


class RunnerError(RuntimeError):
    """平台命令执行失败（不可执行/超时）——调用方转为安全类别，绝不保留文本。"""


class CommandNotAllowedError(RunnerError):
    """非白名单只读命令——在任何执行之前拒绝（fail-closed）。"""


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


#: 容器四事实格式串：state \t health \t Config.Image（运行 tag）\t .Image（运行镜像 ID）
CONTAINER_INSPECT_FORMAT = (
    "{{.State.Status}}\t"
    "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}\t"
    "{{.Config.Image}}\t{{.Image}}"
)
#: 镜像 ID 格式串（本地 tag → image ID 解析）
IMAGE_ID_FORMAT = "{{.Id}}"

#: compose 值选项（跳过后随值）；compose 旗标
_COMPOSE_VALUE_OPTIONS = frozenset({"-f", "-p", "--profile", "--env-file"})
_COMPOSE_FLAGS = frozenset({"--dry-run"})


def is_readonly_docker_command(argv: tuple[str, ...] | list[str]) -> bool:
    """结构性白名单：仅三只读形态放行——
    ① ``docker compose [opts] ps --format json``；
    ② ``docker inspect --format <CONTAINER_INSPECT_FORMAT> <单个容器>``；
    ③ ``docker image inspect --format <IMAGE_ID_FORMAT> <单个镜像引用>``。
    其余（含 stop/start/restart/rm/kill/down/exec/up/build/pull/logs 与
    任何旗标/参数变形）一律 False——拒绝发生在任何执行之前。"""
    tokens = [str(item) for item in argv]
    if len(tokens) < 4 or tokens[0] != "docker":
        return False
    sub = tokens[1]
    if sub == "compose":
        index, position = 2, None
        while index < len(tokens):
            token = tokens[index]
            if token in _COMPOSE_VALUE_OPTIONS:
                index += 2
                continue
            if token in _COMPOSE_FLAGS or (token.startswith("-") and token != "-"):
                index += 1
                continue
            position = token
            break
        if position != "ps":
            return False
        # ps 之后必须恰为 --format json（裸 ps / table / 附加旗标一律拒绝）
        return tokens[index + 1:] == ["--format", "json"]
    if sub == "inspect":
        index, format_count, names = 2, 0, 0
        while index < len(tokens):
            token = tokens[index]
            if token == "--format":
                if index + 1 >= len(tokens) or tokens[index + 1] != CONTAINER_INSPECT_FORMAT:
                    return False
                index += 2
                format_count += 1
                continue
            if token.startswith("-"):
                return False
            names += 1
            index += 1
        return format_count == 1 and names == 1
    if sub == "image":
        if tokens[2] != "inspect":
            return False
        index, format_count, names = 3, 0, 0
        while index < len(tokens):
            token = tokens[index]
            if token == "--format":
                if index + 1 >= len(tokens) or tokens[index + 1] != IMAGE_ID_FORMAT:
                    return False
                index += 2
                format_count += 1
                continue
            if token.startswith("-"):
                return False
            names += 1
            index += 1
        return format_count == 1 and names == 1
    return False


class ReadonlyRunner:
    """白名单门装饰器：非只读命令在任何执行之前拒绝（fail-closed）。"""

    def __init__(self, inner: Runner) -> None:
        self._inner = inner

    def run(self, argv: tuple[str, ...] | list[str], *, timeout: float = 60.0,
            encoding: str | None = None) -> CommandResult:
        if not is_readonly_docker_command(argv):
            raise CommandNotAllowedError("command-not-whitelisted")
        return self._inner.run(argv, timeout=timeout, encoding=encoding)


def categorize_runner_exception(exc: BaseException) -> tuple[str, str]:
    """Runner 异常 →（安全类别, 异常类名）。绝不保留 str(exc) 文本。"""
    if isinstance(exc, CommandNotAllowedError):
        category = "command-not-whitelisted"
    elif isinstance(exc, subprocess.TimeoutExpired):
        category = "command-timeout"
    elif isinstance(exc, (RunnerError, OSError)):
        category = "command-exec-error"
    else:
        category = "internal-error"
    return category, type(exc).__name__


# ---------------------------------------------------------------- Clock（注入点）


class Clock(Protocol):
    def utc_now_iso(self) -> str: ...

    def stamp(self) -> str: ...


class RealClock:
    def utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def stamp(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


# ---------------------------------------------------------------- 采集器


@dataclass(frozen=True)
class Failure:
    """采集器失败：安全类别 + 异常类名（非异常失败类名为空），无文本。"""

    category: str
    error_class: str

    def as_dict(self) -> dict[str, str | None]:
        return {"failure_category": self.category, "error_class": self.error_class or None}


def container_name(project: str, service: str, suffix: int = 1) -> str:
    return f"{project}-{service}-{suffix}"


def _run_or_failure(runner: Runner, argv: list[str], *, timeout: float) -> tuple[CommandResult | None, Failure | None]:
    try:
        return runner.run(argv, timeout=timeout), None
    except Exception as exc:  # noqa: BLE001 —— 类别化兜底，文本不保留
        category, klass = categorize_runner_exception(exc)
        return None, Failure(category, klass)


def parse_compose_ps_rows(stdout: str) -> list[dict[str, str]]:
    """解析 compose ps --format json：兼容 JSON 数组 / 单对象 / JSONL 三形态；
    仅提取 Service/Health/State（Labels/Ports/Command 等其余字段一律丢弃，
    绝不保留）。不可解析 → 空清单（调用方按 fail-closed 处理）。"""
    text = stdout.strip()
    if not text:
        return []
    raw_rows: list[object] = []
    try:
        whole = json.loads(text)
        raw_rows = whole if isinstance(whole, list) else [whole]
    except ValueError:
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                raw_rows.append(json.loads(line))
            except ValueError:
                continue
    rows: list[dict[str, str]] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        service = str(raw.get("Service") or "")
        if not service:
            continue  # 无 Service 键不可靠映射，fail-closed 跳过
        rows.append({
            "service": service,
            "health": str(raw.get("Health") or ""),
            "state": str(raw.get("State") or ""),
        })
    return rows


def collect_compose_ps(runner: Runner, *, compose_file: Path, project: str,
                       profiles: tuple[str, ...] = COMPOSE_PROFILES,
                       ) -> tuple[dict[str, dict[str, str]] | None, Failure | None]:
    """docker compose ps --format json → {service: {health, state}}（只读）。"""
    argv = ["docker", "compose", "-f", str(compose_file), "-p", project]
    for profile in profiles:
        argv += ["--profile", profile]
    argv += ["ps", "--format", "json"]
    result, failure = _run_or_failure(runner, argv, timeout=COMPOSE_PS_TIMEOUT_SECONDS)
    if failure is not None:
        return None, failure
    assert result is not None
    if result.returncode != 0:
        return None, Failure("compose-ps-nonzero-exit", "")
    rows = parse_compose_ps_rows(result.stdout)
    if not rows:
        return None, Failure("compose-ps-unparseable", "")
    services: dict[str, dict[str, str]] = {}
    for row in rows:
        services[row["service"]] = {"health": row["health"], "state": row["state"]}
    return services, None


@dataclass(frozen=True)
class ContainerFacts:
    """容器四事实（单容器 docker inspect；绝不包含 env/日志/Labels）。"""

    service: str
    name: str
    state: str
    health: str
    config_image: str
    image_id: str


def collect_container_facts(runner: Runner, *, project: str,
                            service: str) -> tuple[ContainerFacts | None, Failure | None]:
    """docker inspect --format <四事实> <container>（只读；单容器）。"""
    name = container_name(project, service)
    argv = ["docker", "inspect", "--format", CONTAINER_INSPECT_FORMAT, name]
    result, failure = _run_or_failure(runner, argv, timeout=INSPECT_TIMEOUT_SECONDS)
    if failure is not None:
        return None, failure
    assert result is not None
    if result.returncode != 0:
        return None, Failure("inspect-nonzero-exit", "")
    parts = result.stdout.strip().split("\t")
    if len(parts) != 4:
        return None, Failure("inspect-unparseable", "")
    return ContainerFacts(service, name, parts[0].strip(), parts[1].strip(),
                          parts[2].strip(), parts[3].strip()), None


def collect_image_identity(runner: Runner, *, image_ref: str,
                           ) -> tuple[str | None, Failure | None]:
    """docker image inspect --format {{.Id}} <tag>（只读）→ 本地 tag 当前
    解析的 image ID 原文；rc≠0（如本地无此 tag 镜像）→ 固定类别失败。"""
    argv = ["docker", "image", "inspect", "--format", IMAGE_ID_FORMAT, image_ref]
    result, failure = _run_or_failure(runner, argv, timeout=INSPECT_TIMEOUT_SECONDS)
    if failure is not None:
        return None, failure
    assert result is not None
    if result.returncode != 0:
        return None, Failure("image-inspect-nonzero-exit", "")
    value = result.stdout.strip()
    if not value:
        return None, Failure("image-inspect-empty", "")
    return value, None


# ---------------------------------------------------------------- 判定（纯）


def _check(check_id: str, subject: str, status: str, detail: str) -> dict[str, str]:
    return {"check_id": check_id, "subject": subject, "status": status, "detail": detail}


CHECK_PASS = "pass"
CHECK_FAIL = "fail"


def evaluate_drift(collectors: dict[str, object],
                   anchors: dict[str, ImageAnchor] = EXPECTED_ANCHORS,
                   services: tuple[str, ...] = STACK_SERVICES,
                   ) -> dict[str, object]:
    """纯函数：采集快照 + 锚点 → 逐项检查 + drift + 固定词汇 reasons。

    fail-closed：任何采集失败/事实缺失/digest 不可解析 → 对应检查恒
    fail（缺失绝不当作匹配；tag 相同绝不冒充 digest 通过）。"""
    checks: list[dict[str, str]] = []
    reasons: list[str] = []

    compose_ps = collectors["compose_ps"]
    assert isinstance(compose_ps, dict)
    ps_services = compose_ps.get("services")
    if compose_ps.get("status") != "ok" or not isinstance(ps_services, dict):
        checks.append(_check("collector:compose-ps", "compose-ps", CHECK_FAIL,
                             f"category={compose_ps.get('failure_category')}"))
        reasons.append("collector-failed:compose-ps")
        ps_services = {}
    else:
        checks.append(_check("collector:compose-ps", "compose-ps", CHECK_PASS, "ok"))

    containers = collectors["containers"]
    assert isinstance(containers, dict)
    per_service = containers.get("per_service")
    assert isinstance(per_service, dict)

    image_refs = collectors["image_refs"]
    assert isinstance(image_refs, dict)

    for service in services:
        row = ps_services.get(service)
        if not isinstance(row, dict):
            checks.append(_check("compose-service-present", service, CHECK_FAIL, "missing-from-compose-ps"))
            reasons.append(f"compose-service-missing:{service}")
        else:
            checks.append(_check("compose-service-present", service, CHECK_PASS, "present"))
            if row.get("health") != "healthy":
                checks.append(_check("compose-service-healthy", service, CHECK_FAIL,
                                     f"health={row.get('health')!r}"))
                reasons.append(f"compose-service-unhealthy:{service}")
            else:
                checks.append(_check("compose-service-healthy", service, CHECK_PASS, "health=healthy"))

        item = per_service.get(service)
        assert isinstance(item, dict)
        if item.get("status") != "ok":
            checks.append(_check("container-health", service, CHECK_FAIL, "facts-missing"))
            reasons.append(f"container-facts-missing:{service}")
        else:
            state = str(item.get("state"))
            health = str(item.get("health"))
            if state != "running" or health != "healthy":
                checks.append(_check("container-health", service, CHECK_FAIL,
                                     f"state={state} health={health}"))
                reasons.append(f"container-not-healthy:{service}")
            else:
                checks.append(_check("container-health", service, CHECK_PASS,
                                     "state=running health=healthy"))

    for service, anchor in anchors.items():
        item = per_service.get(service)
        assert isinstance(item, dict)
        if item.get("status") != "ok":
            for check_id in ("image-running-tag", "image-running-digest", "image-tag-resolution"):
                checks.append(_check(check_id, service, CHECK_FAIL, "facts-missing"))
            reasons.append(f"anchor-facts-missing:{service}")
            continue

        running_tag = str(item.get("config_image") or "")
        running_image_id = str(item.get("image_id") or "")
        if running_tag != anchor.tag:
            checks.append(_check("image-running-tag", service, CHECK_FAIL,
                                 f"running_tag={running_tag!r} expected_tag={anchor.tag!r}"))
            reasons.append(f"image-tag-mismatch:{service}")
        else:
            checks.append(_check("image-running-tag", service, CHECK_PASS,
                                 f"running_tag=={anchor.tag}"))

        running_digest = normalize_image_digest(running_image_id)
        if running_digest is None:
            checks.append(_check("image-running-digest", service, CHECK_FAIL,
                                 "running-digest-unobtainable"))
            reasons.append(f"image-digest-unobtainable:{service}")
        elif running_digest != anchor.digest_hex:
            checks.append(_check("image-running-digest", service, CHECK_FAIL,
                                 f"running_digest=sha256:{running_digest}"
                                 f" expected_digest={anchor.digest_ref}"))
            reasons.append(f"image-digest-mismatch:{service}")
        else:
            checks.append(_check("image-running-digest", service, CHECK_PASS,
                                 f"running_digest=={anchor.digest_ref}"))

        ref_item = image_refs.get(service)
        assert isinstance(ref_item, dict)
        if ref_item.get("status") != "ok":
            checks.append(_check("image-tag-resolution", service, CHECK_FAIL, "tag-resolution-missing"))
            reasons.append(f"image-tag-resolution-unavailable:{service}")
        else:
            resolved_digest = normalize_image_digest(str(ref_item.get("image_id") or ""))
            if resolved_digest is None:
                checks.append(_check("image-tag-resolution", service, CHECK_FAIL,
                                     "tag-resolution-digest-unobtainable"))
                reasons.append(f"image-digest-unobtainable:{service}")
            elif resolved_digest != anchor.digest_hex:
                checks.append(_check("image-tag-resolution", service, CHECK_FAIL,
                                     f"tag_resolved_digest=sha256:{resolved_digest}"
                                     f" expected_digest={anchor.digest_ref}"))
                reasons.append(f"image-tag-resolution-mismatch:{service}")
            else:
                checks.append(_check("image-tag-resolution", service, CHECK_PASS,
                                     f"tag {anchor.tag} -> {anchor.digest_ref}"))

    drift = any(check["status"] != CHECK_PASS for check in checks)
    counts = {
        "pass": sum(1 for c in checks if c["status"] == CHECK_PASS),
        "fail": sum(1 for c in checks if c["status"] == CHECK_FAIL),
    }
    return {"checks": checks, "counts": counts, "drift": drift,
            "drift_reasons": reasons}


def collect_snapshot(*, runner: Runner, compose_file: Path, project: str,
                     anchors: dict[str, ImageAnchor] = EXPECTED_ANCHORS,
                     services: tuple[str, ...] = STACK_SERVICES,
                     ) -> dict[str, object]:
    """只读采集主入口：compose ps + 七容器 inspect + 锚点镜像 ID 解析。
    部分失败如实入档（evaluate_drift 按缺失恒 fail）。"""
    ps_services, ps_failure = collect_compose_ps(
        runner, compose_file=compose_file, project=project)
    if ps_failure is not None:
        compose_ps: dict[str, object] = {"status": "failed", **ps_failure.as_dict(), "services": {}}
    else:
        assert ps_services is not None
        compose_ps = {"status": "ok", "failure_category": None, "error_class": None,
                      "services": ps_services}

    per_service: dict[str, dict[str, object]] = {}
    for service in services:
        facts, failure = collect_container_facts(runner, project=project, service=service)
        if failure is not None:
            per_service[service] = {"status": "failed", **failure.as_dict()}
        else:
            assert facts is not None
            per_service[service] = {
                "status": "ok", "failure_category": None, "error_class": None,
                "name": facts.name, "state": facts.state, "health": facts.health,
                "config_image": facts.config_image, "image_id": facts.image_id,
            }
    containers = {"per_service": per_service}

    image_refs: dict[str, dict[str, object]] = {}
    for service, anchor in anchors.items():
        image_id, failure = collect_image_identity(runner, image_ref=anchor.tag)
        if failure is not None:
            image_refs[service] = {"status": "failed", **failure.as_dict()}
        else:
            assert image_id is not None
            image_refs[service] = {"status": "ok", "failure_category": None,
                                   "error_class": None, "image_id": image_id}

    return {"compose_ps": compose_ps, "containers": containers, "image_refs": image_refs}


# ---------------------------------------------------------------- 脱敏（防御性）

SECRET_SHAPE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), "[REDACTED:token]"),
    (re.compile(r"ghp_[A-Za-z0-9]{8,}"), "[REDACTED:token]"),
    (re.compile(r"gho_[A-Za-z0-9]{8,}"), "[REDACTED:token]"),
    (re.compile(r"AKIA[0-9A-Z]{12,}"), "[REDACTED:key]"),
    (re.compile(r"xoxb-[A-Za-z0-9-]{8,}"), "[REDACTED:token]"),
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


# ---------------------------------------------------------------- 报告（原子写 + symlink/越界拒绝）


def build_config(*, project: str, profiles: tuple[str, ...],
                 compose_file: Path, services: tuple[str, ...],
                 anchors: dict[str, ImageAnchor]) -> dict[str, object]:
    return {
        "project": project,
        "profiles": list(profiles),
        "compose_file": compose_file.name,
        "services": list(services),
        "anchors": {
            service: {"expected_tag": anchor.tag,
                      "expected_digest": anchor.digest_ref}
            for service, anchor in anchors.items()
        },
    }


#: 报告边界注记（固定词汇表：绝不包含任何采集原文）
REPORT_BOUNDARIES: tuple[str, ...] = (
    (
        "read-only collection: docker compose ps --format json + docker inspect"
        " --format (four container facts) + docker image inspect --format"
        " {{.Id}} only; zero mutations (no stop/start/restart/rm/kill/down/"
        "exec/up/build/pull), no shell=True, no container env, no secrets, no"
        " log bodies, no DB/MinIO/voice data access"
    ),
    (
        "zero network and zero environment-variable reads; the image anchors"
        " are the M14-124 approved values baked as constants and are never"
        " taken from requests, env, or files"
    ),
    (
        "digest verification is fail-closed: the running image ID and the"
        " tag-resolved image ID must both parse as sha256:<64 lowercase hex>"
        " and match the approved digest character-for-character; a matching"
        " tag never substitutes for digest proof, and an unobtainable digest"
        " always counts as drift"
    ),
    (
        "compose ps raw rows (including Labels/Ports) and any stderr text are"
        " never persisted; reports carry service names, health vocabulary,"
        " tags, digests, and fixed-vocabulary statuses and reasons only"
    ),
    (
        "drift=true or any collection failure exits nonzero; the failure"
        " report is honest evidence and the tool never repairs or disguises"
        " production state"
    ),
    (
        "drift=false only means the anchors matched within this collection"
        " round; it is not a production-health or readiness claim, and"
        " release approval remains human-only"
    ),
    (
        "default artifact directory is .verify/artifacts/m14-127-production-drift-watch"
        " under the repo root and is gitignored; a custom --artifact-dir is an"
        " explicit operator selection whose location and gitignore status are"
        " the operator's responsibility"
    ),
)


def build_report(*, mode: str, started_utc: str, ended_utc: str,
                 config: dict[str, object],
                 collectors: dict[str, object] | None = None,
                 results: dict[str, object] | None = None) -> dict[str, object]:
    """schema 版本化报告（纯数据；写盘前再经 redact_secrets 终防线）。
    plan 报告零状态宣称：不含 collectors/results/drift。"""
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
    if collectors is not None:
        assert results is not None
        report["collectors"] = collectors
        report["checks"] = results["checks"]
        report["counts"] = results["counts"]
        report["drift"] = results["drift"]
        report["drift_reasons"] = results["drift_reasons"]
    else:
        report["collectors"] = {"status": "planned", "planned": [
            "compose-ps", "container-inspect", "image-ref-resolution",
        ]}
    return report


def render_markdown(report: dict[str, object]) -> str:
    """Markdown 摘要（固定词汇表 + 安全元数据 + 渲染后统一脱敏）。"""
    config = report["config"]
    assert isinstance(config, dict)
    lines: list[str] = [
        f"# {report['milestone']} production drift watch 报告（mode={report['mode']}）",
        "",
        f"- 工具：`{report['tool']}`（schema_version={report['schema_version']}）",
        f"- 开始（UTC）：{report['started_at_utc']}；结束（UTC）：{report['ended_at_utc']}",
        (f"- 项目：{config['project']}（profiles {', '.join(str(p) for p in config['profiles'])}，"
         f"compose {config['compose_file']}）"),
    ]
    anchors = config.get("anchors")
    if isinstance(anchors, dict):
        lines.append("- 锚点（M14-124 已批准，常量烘烤）：")
        for service, anchor in anchors.items():
            assert isinstance(anchor, dict)
            lines.append(f"  - {service}: `{anchor['expected_tag']}` = `{anchor['expected_digest']}`")
    if report["mode"] == "execute":
        counts = report["counts"]
        assert isinstance(counts, dict)
        lines += [
            "",
            (f"- drift=**{str(report['drift']).lower()}**；检查计数："
             f"pass={counts['pass']} fail={counts['fail']}"),
        ]
        drift_reasons = report["drift_reasons"]
        assert isinstance(drift_reasons, list)
        for reason in drift_reasons:
            lines.append(f"  - 原因：{reason}")
        lines += [
            "",
            "| 检查 | 对象 | 状态 | 详情 |",
            "|---|---|---|---|",
        ]
        checks = report["checks"]
        assert isinstance(checks, list)
        for check in checks:
            assert isinstance(check, dict)
            lines.append(f"| {check['check_id']} | {check['subject']} | {check['status']} | {check['detail']} |")
        collectors = report["collectors"]
        assert isinstance(collectors, dict)
        per_service = collectors.get("containers")
        if isinstance(per_service, dict):
            inner = per_service.get("per_service")
            if isinstance(inner, dict):
                lines += [
                    "",
                    "## 容器事实（docker inspect 四事实）",
                    "",
                    "| 服务 | 状态 | state | health | 运行 tag | 运行镜像 ID |",
                    "|---|---|---|---|---|---|",
                ]
                for service, item in inner.items():
                    assert isinstance(item, dict)
                    if item.get("status") != "ok":
                        lines.append(f"| {service} | failed | - | - | - | - |")
                        continue
                    lines.append(
                        f"| {service} | ok | {item.get('state')} | {item.get('health')} "
                        f"| `{item.get('config_image')}` | `{item.get('image_id')}` |")
        image_refs = collectors.get("image_refs")
        if isinstance(image_refs, dict):
            lines += [
                "",
                "## 锚点 tag 本地解析（docker image inspect）",
                "",
                "| 服务 | 状态 | tag→image ID |",
                "|---|---|---|",
            ]
            for service, item in image_refs.items():
                assert isinstance(item, dict)
                if item.get("status") != "ok":
                    lines.append(f"| {service} | failed | - |")
                    continue
                lines.append(f"| {service} | ok | `{item.get('image_id')}` |")
    lines += ["", "边界："]
    lines += [f"- {item}" for item in report["boundaries"]]  # type: ignore[arg-type]
    return "\n".join(lines) + "\n"


class ReportPathError(RuntimeError):
    """报告路径非法（symlink 组件 / 越界 stem）——可见拒绝，零写入。"""


_STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _reject_symlinks(*paths: Path) -> None:
    """拒绝 symlink：目标文件自身 + 目录的全部现存祖先组件。"""
    for path in paths:
        if path.is_symlink():
            raise ReportPathError("symlink-target")
        for ancestor in path.parents:
            if ancestor.exists() and ancestor.is_symlink():
                raise ReportPathError("symlink-in-path")


def write_reports_atomic(report: dict[str, object], directory: Path,
                         stem: str) -> tuple[Path, Path]:
    """JSON + Markdown 双写：内容先经 redact_secrets 终防线，再同目录 tmp +
    fsync + os.replace 原子落盘；symlink/越界路径一律 ReportPathError（零写入）。"""
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


def artifact_dir_note(directory: Path) -> str:
    """报告目录注记（固定词汇）：默认目录 gitignored；自定义路径为操作者
    显式自选——不对其 gitignore 状态作任何宣称。"""
    if directory == ARTIFACT_DIR:
        return "默认目录（gitignored，不入库）"
    return "自定义目录（操作者显式自选，位置与入库与否由操作者负责）"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="production_drift_watch.py",
        description=("M14-127 production drift watch：只读校验生产栈七服务健康与"
                     " API/Web 运行镜像是否仍锚定 M14-124 已批准 tag+sha256 digest"
                     "（默认 plan 零采集；execute 需旗标+精确确认短语；digest "
                     "fail-closed，tag 相同绝不冒充 digest 通过；drift=true 非零退出）"),
    )
    parser.add_argument("--execute", action="store_true",
                        help="真实只读采集（默认 plan：零 subprocess/零 Docker/零网络/零 env 读取）")
    parser.add_argument("--confirm", default="",
                        help=f'execute 必配 --confirm "{CONFIRM_PHRASE}"（精确匹配）')
    parser.add_argument("--project", default=DEFAULT_PROJECT,
                        help=f"compose 项目名（默认 {DEFAULT_PROJECT}）")
    parser.add_argument("--artifact-dir", type=Path, default=ARTIFACT_DIR,
                        help=("报告目录（默认 .verify/artifacts/"
                              "m14-127-production-drift-watch，gitignored；"
                              "自定义路径为操作者显式自选，其位置与入库与否由操作者负责）"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = SafeLog()
    # 1) compose 项目名严格白名单（plan/execute 共同；被拒值不回显）
    project_problem = validate_project_name(args.project)
    if project_problem is not None:
        log.say(f"拒绝: --project 名非法（原因: {project_problem}）——被拒值不回显")
        return EXIT_REFUSE
    clock = RealClock()
    config = build_config(project=args.project, profiles=COMPOSE_PROFILES,
                          compose_file=COMPOSE_FILE, services=STACK_SERVICES,
                          anchors=EXPECTED_ANCHORS)
    stamp = clock.stamp()
    # 2) plan 模式（默认）：零 subprocess、零 Docker、零网络、零 env 读取
    if not args.execute:
        log.say("=== M14-127 production drift watch PLAN（零 subprocess / 零 Docker / 零网络 / 零 env 读取） ===")
        log.say(f"compose project: {args.project}（profiles: {' '.join(COMPOSE_PROFILES)}）")
        for service in STACK_SERVICES:
            log.say(f"受管容器: {container_name(args.project, service)}")
        for service, anchor in EXPECTED_ANCHORS.items():
            log.say(f"镜像锚点[{service}]: {anchor.tag} = {anchor.digest_ref}")
        log.say("计划检查: 七服务 compose ps 存在+health=healthy → 七容器 inspect state/health → "
                "API/Web 运行 tag/运行镜像 ID/锚点 tag 本地解析 三重 digest 比对（fail-closed）")
        log.say(f'执行需: --execute --confirm "{CONFIRM_PHRASE}"')
        report = build_report(mode="plan", started_utc=clock.utc_now_iso(),
                              ended_utc=clock.utc_now_iso(), config=config)
        try:
            json_path, md_path = write_reports_atomic(report, args.artifact_dir, f"plan-{stamp}")
            log.say(f"plan 报告: {json_path.name} / {md_path.name}（{artifact_dir_note(args.artifact_dir)}）")
        except ReportPathError as cause:
            log.say(f"plan 报告路径非法: {type(cause).__name__}")
            return EXIT_REFUSE
        except OSError as cause:
            log.say(f"plan 报告写入失败（不影响退出码）: {type(cause).__name__}")
        return EXIT_OK
    # 3) execute 门禁：精确确认短语（缺一即拒，零采集）
    if args.confirm != CONFIRM_PHRASE:
        log.say(f'拒绝: --execute 必配 --confirm "{CONFIRM_PHRASE}"（精确匹配，当前不匹配）——零采集')
        return EXIT_REFUSE
    # 4) execute（只读采集；真实执行仅由 supervisor 在获准窗口运行）
    log.say(f"=== M14-127 production drift watch EXECUTE: project={args.project} ===")
    started_utc = clock.utc_now_iso()
    collectors = collect_snapshot(runner=ReadonlyRunner(RealRunner()),
                                  compose_file=COMPOSE_FILE, project=args.project)
    ended_utc = clock.utc_now_iso()
    results = evaluate_drift(collectors)
    report = build_report(mode="execute", started_utc=started_utc,
                          ended_utc=ended_utc, config=config,
                          collectors=collectors, results=results)
    try:
        json_path, md_path = write_reports_atomic(report, args.artifact_dir, f"drift-watch-{stamp}")
        log.say(f"报告: {json_path.name} / {md_path.name}（{artifact_dir_note(args.artifact_dir)}）")
    except (ReportPathError, OSError) as cause:
        log.say(f"报告写入失败（证据不可失——按拒绝处理）: {type(cause).__name__}")
        return EXIT_REFUSE
    counts = results["counts"]
    assert isinstance(counts, dict)
    log.say(f"检查计数: pass={counts['pass']} fail={counts['fail']}")
    for reason in results["drift_reasons"]:  # type: ignore[index]
        log.say(f"drift 原因: {reason}")
    log.say(f"=== 结果: drift={str(results['drift']).lower()}（drift=false 仅表示本轮锚点吻合，≠ production readiness） ===")
    return EXIT_OK if not results["drift"] else EXIT_REFUSE


if __name__ == "__main__":
    sys.exit(main())
