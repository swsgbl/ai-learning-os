#!/usr/bin/env python
"""M14-96 RC 本地彩排冒烟 runner：从当前 worktree 构建唯一 tag 的 API/Web
镜像，在任务自有隔离 compose 项目（infra/docker-compose.rc-smoke.yml，
项目名 aios-m14-96-rc-smoke，loopback 独占端口 18096/13096）里起栈、
证明健康与匿名保护写契约、然后只拆除本项目——全程与运行中的生产栈
零共享资源、零生产触碰。

与 tools/ops 既有纪律（M14-93 等）的异同：仍单文件、纯标准库、fail-closed
固定词汇拒绝；一切子进程/HTTP/端口探测/时钟经注入接口（契约测试全替身，
绝不触碰真实 docker/git/网络）。本工具**必须**真实执行 docker 构建与
compose 起拆（这就是它的用途），因此与纯审计工具不同：有子进程、有
loopback HTTP 探针、有墙钟时间戳（执行时刻即证据时刻）。

阶段（任一步失败即固定词汇拒绝/失败，证据照常落盘）：

1. 输出目录必须全新（已存在 = output-dir-exists 拒绝，零子进程调用）。
2. 前置校验（fail-closed）：
   - HEAD == --base-sha（base-sha-mismatch：镜像来源树漂移即拒绝）；
   - git status --porcelain 全量登记；脏文件路径必须全部落在镜像构建
     输入面之外（services/api/app|requirements.txt|alembic*、Dockerfile、
     VERSION、.dockerignore、package*.json、apps/web —— dirty-build-input
     拒绝：脏输入面上的改动会让「基点树构建」声明失真）；
   - 项目无残留容器/卷（stale-project-containers / stale-project-volumes）；
   - 冒烟 tag 镜像不存在（image-tag-exists：绝不覆盖既有镜像）；
   - infra 镜像（postgres/redis/minio，自 compose 文件解析 pin）本地存在
     （infra-image-missing：绝不隐式 pull）；
   - 127.0.0.1:18096 / 127.0.0.1:13096 可 bind（port-in-use）；
   - compose config 渲染通过、项目名/服务集精确（compose-config-invalid /
     compose-project-name-mismatch / compose-service-set-mismatch）。
3. 构建唯一 tag（m14-96-rc-smoke-<HEAD>，绝不与任何既有/生产 tag 重合）；
   Web 构建参数 NEXT_PUBLIC_API_BASE_URL 指向冒烟 API 127.0.0.1:18096。
4. 隔离起栈（docker compose -f <rc-smoke> -p aios-m14-96-rc-smoke up -d
   --no-build；compose 子进程 env 全量剥离宿主 AIOS_* 漂移变量、只注入
   两个冒烟 tag 变量）→ 逐服务健康轮询（postgres/redis/minio/api/web
   healthy）→ docker port 证明 api/web 恰为 loopback 独占映射。
5. 八项 loopback 探针：API /health、/api/v1/version==VERSION 文件、
   /api/v1/auth/status auth_enabled=true、匿名 GET /api/v1/papers 401、
   匿名 POST /api/v1/resources/upload 401（应用级 require_user 门禁契约：
   AUTH_SECRET 配置时非豁免路径无凭据一律 401）、Web / 与 /login 200、
   CORS preflight 回显冒烟 Web origin。
6. 无论成败恒拆本项目（down --volumes --remove-orphans，只影响项目级
   资源）并复核零残留；前后对比全机 docker ps -a / volume / network /
   compose ls 快照（external-container-drift 等：任何本项目之外的容器/
   卷/网络漂移即失败——零生产漂移的机器级证明）。
7. 证据 + SHA256SUMS 清单原子落盘（gitignored
   .verify/artifacts/m14-96-release-candidate-smoke/，默认路径见
   build_parser）；报告仅安全元数据（状态/固定词汇原因/时间戳/tag/
   镜像 ID/端口/健康/探针/快照/边界），绝不包含 env 值/secret/绝对路径。

退出码：0 pass / 1 refused（前置 fail-closed 拒绝）/ 2 failed（构建、
起栈、健康、探针、拆栈或快照等价失败——诚实失败证据照常落盘）/ 3 CLI
用法错误。

诚实边界：本地彩排冒烟，不是 production readiness 声明；release_ready /
production_ready 不变，发布审批 human-only；不推镜像仓库、不打 git 标签、
不发 GitHub Release；绝不触碰任何生产容器/卷/服务（所有 compose 调用
恒 -f 冒烟文件 + -p 任务项目名，assert_safe_argv 运行期护栏再锁一层）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.rc-smoke.yml"
DEFAULT_OUTPUT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-96-release-candidate-smoke"

TOOL_NAME = "tools/ops/rc_smoke_rehearsal.py"
SCHEMA_VERSION = 1
TAG = "[rc-smoke]"

PROJECT_NAME = "aios-m14-96-rc-smoke"
TASK_LABEL = "m14-96-rc-smoke"
API_HOST_PORT = 18096
WEB_HOST_PORT = 13096
LOOPBACK = "127.0.0.1"
API_BASE_URL = f"http://{LOOPBACK}:{API_HOST_PORT}"
WEB_BASE_URL = f"http://{LOOPBACK}:{WEB_HOST_PORT}"
SMOKE_SERVICES = ("postgres", "redis", "minio", "api", "web")
EXPECTED_PORT_MAPS = {
    "api": {"8000/tcp": [f"{LOOPBACK}:{API_HOST_PORT}"]},
    "web": {"3000/tcp": [f"{LOOPBACK}:{WEB_HOST_PORT}"]},
}

EXIT_PASS = 0
EXIT_REFUSED = 1
EXIT_FAILED = 2
EXIT_USAGE = 3

PROJECT_LABEL_FILTER = f"com.docker.compose.project={PROJECT_NAME}"
STATUS_PASS = "pass"
STATUS_REFUSED = "refused"
STATUS_FAILED = "failed"

BUILD_TIMEOUT_SECONDS = 2400
COMPOSE_TIMEOUT_SECONDS = 600
PROBE_TIMEOUT_SECONDS = 20
HEALTH_POLL_INTERVAL_SECONDS = 5.0
HEALTH_TIMEOUT_SECONDS = 300.0

#: 镜像构建输入面（services/api/Dockerfile + apps/web/Dockerfile 的 COPY 面）：
#: 脏文件落在其中任何路径 = 基点树镜像声明失真，fail-closed 拒绝
BUILD_INPUT_EXACT_FILES = frozenset({
    "VERSION", ".dockerignore", "package.json", "package-lock.json",
    "services/api/Dockerfile", "services/api/requirements.txt",
    "services/api/alembic.ini", "apps/web/Dockerfile",
})
BUILD_INPUT_PREFIXES = ("services/api/app/", "services/api/alembic/", "apps/web/")

#: 运行期护栏：docker 动词白名单之外的任何 mutation 一律 GuardError
FORBIDDEN_DOCKER_VERBS = frozenset({
    "stop", "rm", "kill", "restart", "prune", "push", "login", "tag",
    "system", "exec", "cp", "save", "load", "commit", "create", "run",
    "start", "pause", "unpause", "update", "wait", "events", "attach",
})
COMPOSE_SUBCOMMANDS = frozenset({"config", "ps", "up", "down", "logs", "version", "ls"})
COMPOSE_SUBCOMMANDS_WITHOUT_PROJECT_SCOPE = frozenset({"version", "ls"})

BOUNDARIES: tuple[str, ...] = (
    ("local rehearsal smoke only: this tool never claims release approval — "
     "release_ready stays false, production_ready stays false, and release "
     "approval remains human-only"),
    ("no registry push, no git tag, no GitHub Release, no production "
     "deployment; unique task tags never overwrite any existing or "
     "production image tag"),
    ("zero production touch: every compose call is pinned to the rc-smoke "
     "compose file and the task project name; teardown removes only "
     "project-scoped containers and disposable project volumes"),
    ("loopback-only edge ports (18096/13096) disjoint from every host port "
     "published by the production compose file; data services publish no "
     "host ports at all"),
    ("machine-level isolation proof: full docker ps -a / volume / network / "
     "compose-ls snapshots are compared before versus after — any drift "
     "outside the task project fails the rehearsal"),
    ("evidence is honest metadata only: fixed-vocabulary statuses and "
     "reasons, timestamps, tags, image ids, ports, health, probe results; "
     "never env values, secrets, or absolute paths"),
)

_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")


class RefusalError(RuntimeError):
    """fail-closed 前置拒绝（固定词汇原因；绝不携带文件内容/env 值）。"""


class FailureError(RuntimeError):
    """执行期失败（构建/起栈/健康/探针/拆栈/快照等价——固定词汇原因）。"""


class GuardError(RuntimeError):
    """运行期护栏：命令形态越界（绝不允许的 docker 动词/缺项目作用域）。"""


# ---------------------------------------------------------------- 注入接口


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class RealCommandRunner:
    """subprocess 执行器（utf-8 replace 解码，超时按 124 固定码回报）。"""

    def run(self, argv: list[str], env: dict[str, str] | None = None,
            cwd: Path | None = None, timeout: float | None = None
            ) -> CommandResult:
        try:
            completed = subprocess.run(
                argv, capture_output=True, env=env, cwd=cwd, timeout=timeout,
                check=False)
        except subprocess.TimeoutExpired:
            return CommandResult(124, "", f"timeout after {timeout}s")
        except OSError as cause:
            return CommandResult(127, "", type(cause).__name__)
        decode = lambda raw: raw.decode("utf-8", errors="replace")
        return CommandResult(completed.returncode, decode(completed.stdout or b""),
                             decode(completed.stderr or b""))


@dataclass(frozen=True)
class ProbeResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes


class RealProbeExecutor:
    """loopback HTTP 探针（urllib；HTTPError 也转为结构化响应）。"""

    def probe(self, method: str, url: str, headers: dict[str, str]) -> ProbeResponse:
        request = urllib.request.Request(url, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_SECONDS) as response:
                return ProbeResponse(response.status,
                                     {k.lower(): v for k, v in response.headers.items()},
                                     response.read())
        except urllib.error.HTTPError as cause:
            body = b""
            try:
                body = cause.read()
            except Exception:  # noqa: BLE001 — 证据采集不因读体失败崩溃
                body = b""
            return ProbeResponse(cause.code, {k.lower(): v for k, v in cause.headers.items()},
                                 body)


def real_binder(host: str, port: int) -> None:
    """端口占用预检：bind 失败即抛 OSError（绝不带 SO_REUSEADDR 假阴性）。"""
    probe_socket = socket.socket()
    try:
        probe_socket.bind((host, port))
    finally:
        probe_socket.close()


def real_sleep(seconds: float) -> None:
    time.sleep(seconds)


def real_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- 纯工具函数


def API_IMAGE_REF(head_sha: str) -> str:
    return f"aios/api:{TASK_LABEL}-{head_sha}"


def WEB_IMAGE_REF(head_sha: str) -> str:
    return f"aios/web:{TASK_LABEL}-{head_sha}"


def child_env(base_env: dict[str, str], *, api_tag: str, web_tag: str) -> dict[str, str]:
    """剥离宿主全部 AIOS_* 漂移变量，再显式注入两个冒烟 tag 变量。"""
    env = {k: v for k, v in base_env.items() if not k.startswith("AIOS_")}
    env["AIOS_RC_SMOKE_API_TAG"] = api_tag
    env["AIOS_RC_SMOKE_WEB_TAG"] = web_tag
    return env


def is_build_input_path(path: str) -> bool:
    if path in BUILD_INPUT_EXACT_FILES:
        return True
    return path.startswith(BUILD_INPUT_PREFIXES)


def parse_porcelain_paths(porcelain: str) -> list[str]:
    """porcelain 输出 → 有序路径列表（rename 两端都登记）。"""
    paths: list[str] = []
    for line in porcelain.split("\n"):
        if not line.strip():
            continue
        entry = line[3:] if len(line) > 3 else ""
        if " -> " in entry:
            old, new = entry.split(" -> ", 1)
            paths.extend([old, new])
        elif entry:
            paths.append(entry)
    return paths


def parse_service_images(compose_text: str) -> dict[str, str]:
    """compose 文本 → 字面量 image pin（含 ${...} 插值的服务排除）。"""
    images: dict[str, str] = {}
    current: str | None = None
    for line in compose_text.split("\n"):
        if line.startswith("  ") and not line.startswith("   ") and line.strip():
            current = line.strip().rstrip(":")
        elif line.startswith("    image: ") and current:
            value = line.strip()[len("image: "):].strip().strip('"').strip("'")
            if "$" not in value:
                images[current] = value
    return images


def parse_port_output(text: str) -> dict[str, list[str]]:
    """docker port 输出 → {容器侧: [宿主绑定...]}。"""
    mapping: dict[str, list[str]] = {}
    for line in text.split("\n"):
        line = line.strip()
        if " -> " not in line:
            continue
        container_side, host_side = line.split(" -> ", 1)
        mapping.setdefault(container_side.strip(), []).append(host_side.strip())
    return mapping


def parse_snapshot_lines(text: str) -> list[str]:
    return [line for line in text.split("\n") if line.strip()]


def parse_compose_ls_names(stdout: str) -> list[str]:
    try:
        rows = json.loads(stdout)
    except ValueError:
        return []
    if isinstance(rows, dict):
        rows = [rows]
    return sorted(str(row.get("Name")) for row in rows if isinstance(row, dict))


def snapshot_diff(before: list[str], after: list[str]) -> list[str]:
    """双向差集（安全元数据行：容器名+状态 / 卷名 / 网络名）。"""
    before_set, after_set = set(before), set(after)
    return sorted(f"-{line}" for line in before_set - after_set) + sorted(
        f"+{line}" for line in after_set - before_set)


def assert_safe_argv(argv: list[str]) -> None:
    """运行期护栏：本项目 runner 只允许白名单形态的 docker 命令。

    - 任何 FORBIDDEN_DOCKER_VERBS 动词（含 volume/network/image 子命令层）拒绝；
    - compose 子命令白名单 + 恒 -f <rc-smoke 文件> + -p <任务项目名>
      （version/ls 两个全局只读子命令豁免 -f/-p）。
    """
    if not argv or argv[0] != "docker":
        return  # git 等非 docker 命令不在此护栏职责内
    verb = argv[1] if len(argv) > 1 else ""
    if verb in FORBIDDEN_DOCKER_VERBS:
        raise GuardError(f"forbidden-docker-verb:{verb}")
    if verb in ("volume", "network"):
        sub = argv[2] if len(argv) > 2 else ""
        if sub != "ls":
            raise GuardError(f"forbidden-docker-verb:{verb} {sub}")
        return
    if verb == "image":
        sub = argv[2] if len(argv) > 2 else ""
        if sub != "inspect":
            raise GuardError(f"forbidden-docker-verb:image {sub}")
        return
    if verb == "compose":
        subcommands = [part for part in argv[2:] if not part.startswith("-")
                       and part not in (str(COMPOSE_FILE), PROJECT_NAME)]
        sub = subcommands[0] if subcommands else ""
        if sub not in COMPOSE_SUBCOMMANDS:
            raise GuardError(f"forbidden-compose-subcommand:{sub}")
        if sub in COMPOSE_SUBCOMMANDS_WITHOUT_PROJECT_SCOPE:
            return
        if str(COMPOSE_FILE) not in argv or PROJECT_NAME not in argv:
            raise GuardError("compose-call-not-project-scoped")
        return
    if verb not in ("build", "inspect", "ps", "version", "port"):
        raise GuardError(f"forbidden-docker-verb:{verb}")


# ---------------------------------------------------------------- 探针契约


@dataclass(frozen=True)
class ProbeSpec:
    name: str
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    expected_status: int = 200
    kind: str = "plain"  # plain | version | auth-status | cors
    expect_version: str | None = None
    expect_origin: str | None = None


def build_probe_specs() -> list[ProbeSpec]:
    version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    return [
        ProbeSpec("api-health", "GET", f"{API_BASE_URL}/health"),
        ProbeSpec("api-version", "GET", f"{API_BASE_URL}/api/v1/version",
                  kind="version", expect_version=version),
        ProbeSpec("auth-status", "GET", f"{API_BASE_URL}/api/v1/auth/status",
                  kind="auth-status"),
        ProbeSpec("anonymous-protected-read", "GET", f"{API_BASE_URL}/api/v1/papers",
                  expected_status=401),
        ProbeSpec("anonymous-protected-write", "POST",
                  f"{API_BASE_URL}/api/v1/resources/upload", expected_status=401),
        ProbeSpec("web-root", "GET", f"{WEB_BASE_URL}/"),
        ProbeSpec("web-login", "GET", f"{WEB_BASE_URL}/login"),
        ProbeSpec("cors-preflight", "OPTIONS", f"{API_BASE_URL}/api/v1/papers",
                  headers={"Origin": WEB_BASE_URL,
                           "Access-Control-Request-Method": "GET"},
                  kind="cors", expect_origin=WEB_BASE_URL),
    ]


def evaluate_probe(spec: ProbeSpec, response: ProbeResponse) -> tuple[bool, str]:
    """探针判定（纯函数）：状态码 + kind 专属校验，detail 只含安全元数据。"""
    if response.status_code != spec.expected_status:
        return False, f"status {response.status_code} != {spec.expected_status}"
    if spec.kind == "version":
        try:
            got = json.loads(response.body.decode("utf-8", errors="replace")).get("version")
        except ValueError:
            return False, "body not json"
        want = spec.expect_version or ""
        if got != want:
            return False, f"version {got!r} != expected {want!r}"
    elif spec.kind == "auth-status":
        try:
            enabled = json.loads(response.body.decode("utf-8", errors="replace")).get(
                "auth_enabled")
        except ValueError:
            return False, "body not json"
        if enabled is not True:
            return False, f"auth_enabled={enabled!r} (compose 注入 AUTH_SECRET 默认值时应为 true)"
    elif spec.kind == "cors":
        got = response.headers.get("access-control-allow-origin")
        if got != spec.expect_origin:
            return False, f"allow-origin {got!r} != {spec.expect_origin!r}"
    return True, "ok"


# ---------------------------------------------------------------- 报告


def build_report_payload(*, status: str, reasons: list[str],
                         task_block: dict, images_block: dict, ports_block: dict,
                         health_block: dict, probes_block: list,
                         isolation_block: dict, cleanup_block: dict,
                         timeline: list, generated_at: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": status,
        "reasons": reasons,
        "task": task_block,
        "images": images_block,
        "ports": ports_block,
        "services_health": health_block,
        "probes": probes_block,
        "isolation": isolation_block,
        "cleanup": cleanup_block,
        "evidence_files": [
            "git-head.txt", "git-status-porcelain.txt",
            "docker-context-before.txt", "docker-context-after.txt",
            "docker-build-api.txt", "docker-build-web.txt",
            "image-inspect-api.json", "image-inspect-web.json",
            "compose-config.json", "compose-up.txt", "compose-ps.json",
            "service-health.txt", "docker-port.txt", "probes.json",
            "compose-down.txt", "rc-smoke-report.json", "rc-smoke-report.md",
        ],
        "timeline": timeline,
        "generated_at": generated_at,
        "boundaries": list(BOUNDARIES),
    }


def render_markdown(report: dict) -> str:
    lines = [
        f"# M14-96 RC 本地彩排冒烟报告（schema_version={report['schema_version']}）",
        "",
        (f"- 状态：**{report['status']}**"
         + (f"（{', '.join(report['reasons'])}）" if report["reasons"] else "")),
        (f"- 基点：base_sha `{report['task']['base_sha']}` / head `{report['task']['head_sha']}`"
         f"（构建输入面干净：{report['task']['build_inputs_clean']}）"),
        (f"- 镜像 tag：`{report['images'].get('api', {}).get('tag', '-')}` / "
         f"`{report['images'].get('web', {}).get('tag', '-')}`"),
        (f"- compose 项目：`{report['task']['project_name']}`（文件 "
         f"`{report['task']['compose_file']}`）"),
        f"- 服务健康：{report['services_health'] or '-'}",
        (f"- 端口：{report['ports'].get('api_observed', {})} / "
         f"{report['ports'].get('web_observed', {})}（仅 loopback）"),
        (f"- 探针：{sum(1 for p in report['probes'] if p['ok'])}"
         f"/{len(report['probes'])} 通过"),
        (f"- 隔离证明：external_unchanged={report['isolation'].get('external_unchanged')}"
         f"，drift={report['isolation'].get('container_drift', [])}"),
        (f"- 清理：down_ok={report['cleanup'].get('down_ok')}"
         f"，残留容器={len(report['cleanup'].get('containers_remaining') or [])}"
         f"，残留卷={len(report['cleanup'].get('volumes_remaining') or [])}"),
        f"- 生成时间：{report['generated_at']}",
        "",
        "边界：",
    ]
    lines += [f"- {item}" for item in report["boundaries"]]
    return "\n".join(lines) + "\n"


def write_manifest(output_dir: Path) -> dict[str, dict]:
    """SHA256SUMS.txt：覆盖输出目录全部文件（自身除外），按名排序。"""
    manifest_path = output_dir / "SHA256SUMS.txt"
    entries: dict[str, dict] = {}
    for path in sorted(p for p in output_dir.iterdir() if p.is_file()
                       and p.name != manifest_path.name):
        data = path.read_bytes()
        entries[path.name] = {"sha256": hashlib.sha256(data).hexdigest(),
                              "byte_size": len(data)}
    lines = [f"{meta['sha256']}  {name}" for name, meta in entries.items()]
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return entries


# ---------------------------------------------------------------- 主管道


@dataclass
class _RehearsalState:
    output_dir: Path
    timeline: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    evidence_written: list = field(default_factory=list)


def _iso(now: Callable[[], datetime]) -> str:
    return now().isoformat(timespec="seconds")


def _write_evidence(state: _RehearsalState, name: str, text: str) -> None:
    (state.output_dir / name).write_text(text, encoding="utf-8")
    if name not in state.evidence_written:
        state.evidence_written.append(name)


def _phase(state: _RehearsalState, name: str, now: Callable[[], datetime]):
    started = _iso(now)

    class _Ctx:
        def __enter__(self_self) -> None:
            return None

        def __exit__(self_self, exc_type, exc, tb) -> None:
            state.timeline.append(
                {"phase": name, "started_at": started, "ended_at": _iso(now)})
    return _Ctx()


def run_rehearsal(*, cmd, http, binder, sleep, now, base_sha: str,
                  output_dir: Path, poll_interval: float = HEALTH_POLL_INTERVAL_SECONDS,
                  health_timeout: float = HEALTH_TIMEOUT_SECONDS
                  ) -> tuple[dict, int]:
    """主管道：详见模块 docstring。一切拒绝/失败都产出诚实报告与清单。"""
    if output_dir.exists():
        report = _refused_before_dir(base_sha, "output-dir-exists", now)
        return report, EXIT_REFUSED
    output_dir.mkdir(parents=True)
    state = _RehearsalState(output_dir=output_dir)
    api_tag = web_tag = ""
    compose_started = False
    task_block = {"label": TASK_LABEL, "project_name": PROJECT_NAME,
                  "compose_file": "infra/docker-compose.rc-smoke.yml",
                  "base_sha": base_sha, "head_sha": "", "dirty_paths": [],
                  "build_inputs_clean": None}
    images_block: dict = {"api": {}, "web": {}, "infra": {}}
    ports_block: dict = {}
    health_block: dict = {}
    probes_block: list = []
    isolation_block: dict = {}
    cleanup_block: dict = {"down_ok": None, "containers_remaining": None,
                           "volumes_remaining": None}
    status, exit_code = STATUS_REFUSED, EXIT_REFUSED

    def run(argv: list[str], env: dict | None = None, timeout: float | None = None
            ) -> CommandResult:
        assert_safe_argv(argv)
        return cmd.run(list(argv), env=env, cwd=REPO_ROOT, timeout=timeout)

    def docker_env() -> dict[str, str]:
        # compose 文件 image 已带 aios/api: / aios/web: 前缀——env 只注入纯
        # tag 后缀（m14-96-rc-smoke-<sha>），双重前缀会让 up 解析失败
        return child_env(dict(os.environ),
                         api_tag=f"{TASK_LABEL}-{task_block['head_sha']}",
                         web_tag=f"{TASK_LABEL}-{task_block['head_sha']}")

    try:
        with _phase(state, "preconditions", now):
            head = run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"])
            head_sha = head.stdout.strip()
            task_block["head_sha"] = head_sha
            _write_evidence(state, "git-head.txt", head.stdout)
            if head.returncode != 0 or head_sha != base_sha:
                raise RefusalError("head-sha-mismatch")

            porcelain = run(["git", "-C", str(REPO_ROOT), "status", "--porcelain"])
            _write_evidence(state, "git-status-porcelain.txt", porcelain.stdout)
            dirty = parse_porcelain_paths(porcelain.stdout)
            task_block["dirty_paths"] = dirty
            offenders = [p for p in dirty if is_build_input_path(p)]
            task_block["build_inputs_clean"] = not offenders
            if offenders:
                raise RefusalError("dirty-build-input")

            stale_containers = run(["docker", "ps", "-a", "--filter",
                                    PROJECT_LABEL_FILTER, "--format", "{{.Names}}"])
            if stale_containers.stdout.strip():
                raise RefusalError("stale-project-containers")
            stale_volumes = run(["docker", "volume", "ls", "--filter",
                                 PROJECT_LABEL_FILTER, "--format", "{{.Name}}"])
            if stale_volumes.stdout.strip():
                raise RefusalError("stale-project-volumes")

            api_tag = API_IMAGE_REF(head_sha)
            web_tag = WEB_IMAGE_REF(head_sha)
            for ref, kind in ((api_tag, "api"), (web_tag, "web")):
                if run(["docker", "image", "inspect", "--format", "{{json .}}",
                        ref]).returncode == 0:
                    raise RefusalError(f"image-tag-exists:{kind}")
            infra_images = parse_service_images(
                COMPOSE_FILE.read_text(encoding="utf-8"))
            missing = sorted(
                image for image in infra_images.values()
                if run(["docker", "image", "inspect", "--format", "{{json .}}",
                        image]).returncode != 0)
            if missing:
                raise RefusalError(f"infra-image-missing:{missing[0]}")

            for port in (API_HOST_PORT, WEB_HOST_PORT):
                try:
                    binder(LOOPBACK, port)
                except OSError:
                    raise RefusalError(f"port-in-use:{port}") from None

            before = _machine_snapshot(run)
            _write_evidence(state, "docker-context-before.txt", before["raw"])
            isolation_block.update({
                "containers_before": before["block"]["containers"],
                "volumes_before": before["block"]["volumes"],
                "networks_before": before["block"]["networks"],
                "compose_projects_before": before["block"]["compose_projects"],
            })

            config = run(["docker", "compose", "-f", str(COMPOSE_FILE), "-p",
                          PROJECT_NAME, "config", "--format", "json"],
                         env=docker_env(), timeout=COMPOSE_TIMEOUT_SECONDS)
            if config.returncode != 0:
                raise RefusalError("compose-config-invalid")
            try:
                rendered = json.loads(config.stdout)
            except ValueError:
                raise RefusalError("compose-config-invalid") from None
            _write_evidence(state, "compose-config.json", config.stdout)
            if rendered.get("name") != PROJECT_NAME:
                raise RefusalError("compose-project-name-mismatch")
            if set(rendered.get("services", {})) != set(SMOKE_SERVICES):
                raise RefusalError("compose-service-set-mismatch")

        with _phase(state, "build", now):
            api_build = run(["docker", "build", "-f",
                             str(REPO_ROOT / "services" / "api" / "Dockerfile"),
                             "-t", api_tag, str(REPO_ROOT)],
                            env=docker_env(), timeout=BUILD_TIMEOUT_SECONDS)
            _write_evidence(state, "docker-build-api.txt",
                            _cmd_log(api_build.returncode, api_build))
            if api_build.returncode != 0:
                raise FailureError("build-failed:api")
            web_build = run(["docker", "build", "-f",
                             str(REPO_ROOT / "apps" / "web" / "Dockerfile"),
                             "--build-arg",
                             f"NEXT_PUBLIC_API_BASE_URL={API_BASE_URL}",
                             "-t", web_tag, str(REPO_ROOT)],
                            env=docker_env(), timeout=BUILD_TIMEOUT_SECONDS)
            _write_evidence(state, "docker-build-web.txt",
                            _cmd_log(web_build.returncode, web_build))
            if web_build.returncode != 0:
                raise FailureError("build-failed:web")
            images_block["api"] = _image_meta(run, api_tag, state,
                                              "image-inspect-api.json")
            images_block["web"] = _image_meta(run, web_tag, state,
                                              "image-inspect-web.json")
            images_block["infra"] = {
                service: {"image": image, "image_id": _image_id(run, image)}
                for service, image in sorted(infra_images.items())}

        with _phase(state, "up", now):
            compose_started = True
            up = run(["docker", "compose", "-f", str(COMPOSE_FILE), "-p",
                      PROJECT_NAME, "up", "-d", "--no-build"],
                     env=docker_env(), timeout=COMPOSE_TIMEOUT_SECONDS)
            _write_evidence(state, "compose-up.txt", _cmd_log(up.returncode, up))
            if up.returncode != 0:
                raise FailureError("up-failed")
            ps = run(["docker", "compose", "-f", str(COMPOSE_FILE), "-p",
                      PROJECT_NAME, "ps", "--format", "json"],
                     env=docker_env(), timeout=COMPOSE_TIMEOUT_SECONDS)
            _write_evidence(state, "compose-ps.json", ps.stdout)

        with _phase(state, "health", now):
            for service in SMOKE_SERVICES:
                cid = run(["docker", "compose", "-f", str(COMPOSE_FILE), "-p",
                           PROJECT_NAME, "ps", "-q", service],
                          env=docker_env()).stdout.strip()
                if not cid:
                    raise FailureError(f"container-missing:{service}")
                status_value = _poll_health(
                    run, docker_env, cid, service, sleep,
                    poll_interval, health_timeout)
                health_block[service] = status_value
                if status_value != "healthy":
                    raise FailureError(f"service-unhealthy:{service}")
            _write_evidence(state, "service-health.txt", "\n".join(
                f"{service} {status_value}"
                for service, status_value in health_block.items()) + "\n")

        with _phase(state, "ports", now):
            port_lines: list[str] = []
            for kind in ("api", "web"):
                cid = run(["docker", "compose", "-f", str(COMPOSE_FILE), "-p",
                           PROJECT_NAME, "ps", "-q", kind],
                          env=docker_env()).stdout.strip()
                port_out = run(["docker", "port", cid])
                observed = parse_port_output(port_out.stdout)
                ports_block[f"{kind}_observed"] = observed
                port_lines.append(f"{kind} ({cid}):\n{port_out.stdout}")
                if observed != EXPECTED_PORT_MAPS[kind]:
                    raise FailureError(f"port-binding-mismatch:{kind}")
            _write_evidence(state, "docker-port.txt", "\n".join(port_lines) + "\n")

        with _phase(state, "probes", now):
            probe_failures: list[str] = []
            for spec in build_probe_specs():
                response = http.probe(spec.method, spec.url, dict(spec.headers))
                ok, detail = evaluate_probe(spec, response)
                probes_block.append({
                    "name": spec.name, "method": spec.method, "url": spec.url,
                    "expected": spec.expected_status,
                    "got": response.status_code, "ok": ok, "detail": detail})
                if not ok:
                    probe_failures.append(spec.name)
            _write_evidence(state, "probes.json",
                            json.dumps(probes_block, ensure_ascii=False, indent=2) + "\n")
            if probe_failures:
                raise FailureError(
                    "probe-failed:" + ",".join(probe_failures))

        status, exit_code = STATUS_PASS, EXIT_PASS
    except RefusalError as cause:
        state.reasons.append(str(cause))
        status, exit_code = STATUS_REFUSED, EXIT_REFUSED
    except FailureError as cause:
        state.reasons.append(str(cause))
        status, exit_code = STATUS_FAILED, EXIT_FAILED
    except GuardError as cause:
        state.reasons.append(f"guard:{cause}")
        status, exit_code = STATUS_FAILED, EXIT_FAILED

    if compose_started:
        with _phase(state, "teardown", now):
            down = run(["docker", "compose", "-f", str(COMPOSE_FILE), "-p",
                        PROJECT_NAME, "down", "--volumes", "--remove-orphans"],
                       env=docker_env(), timeout=COMPOSE_TIMEOUT_SECONDS)
            _write_evidence(state, "compose-down.txt",
                            _cmd_log(down.returncode, down))
            cleanup_block["down_ok"] = down.returncode == 0
            remaining_containers = run(
                ["docker", "ps", "-a", "--filter", PROJECT_LABEL_FILTER,
                 "--format", "{{.Names}}"]).stdout
            cleanup_block["containers_remaining"] = parse_snapshot_lines(
                remaining_containers)
            remaining_volumes = run(
                ["docker", "volume", "ls", "--filter", PROJECT_LABEL_FILTER,
                 "--format", "{{.Name}}"]).stdout
            cleanup_block["volumes_remaining"] = parse_snapshot_lines(
                remaining_volumes)
            if not cleanup_block["down_ok"]:
                state.reasons.append("teardown-failed")
                status, exit_code = STATUS_FAILED, EXIT_FAILED
            elif cleanup_block["containers_remaining"]:
                state.reasons.append("cleanup-containers-remaining")
                status, exit_code = STATUS_FAILED, EXIT_FAILED
            elif cleanup_block["volumes_remaining"]:
                state.reasons.append("cleanup-volumes-remaining")
                status, exit_code = STATUS_FAILED, EXIT_FAILED

        with _phase(state, "after-snapshot", now):
            after = _machine_snapshot(run)
            _write_evidence(state, "docker-context-after.txt", after["raw"])
            after_block = after["block"]
            isolation_block["containers_after"] = after_block["containers"]
            isolation_block["volumes_after"] = after_block["volumes"]
            isolation_block["networks_after"] = after_block["networks"]
            isolation_block["compose_projects_after"] = after_block["compose_projects"]
            drift = (snapshot_diff(isolation_block.get("containers_before", []),
                                   after_block["containers"])
                     + snapshot_diff(isolation_block.get("volumes_before", []),
                                     after_block["volumes"])
                     + snapshot_diff(isolation_block.get("networks_before", []),
                                     after_block["networks"])
                     + snapshot_diff(
                         isolation_block.get("compose_projects_before", []),
                         after_block["compose_projects"]))
            isolation_block["container_drift"] = drift
            isolation_block["external_unchanged"] = not drift
            if drift and status == STATUS_PASS:
                state.reasons.append("external-container-drift")
                status, exit_code = STATUS_FAILED, EXIT_FAILED

    report = build_report_payload(
        status=status, reasons=state.reasons, task_block=task_block,
        images_block=images_block, ports_block=ports_block,
        health_block=health_block, probes_block=probes_block,
        isolation_block=isolation_block, cleanup_block=cleanup_block,
        timeline=state.timeline, generated_at=_iso(now))
    _write_evidence(state, "rc-smoke-report.json",
                    json.dumps(report, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n")
    _write_evidence(state, "rc-smoke-report.md", render_markdown(report))
    write_manifest(output_dir)
    return report, exit_code


def _refused_before_dir(base_sha: str, reason: str,
                        now: Callable[[], datetime]) -> dict:
    """输出目录已存在：绝不写入任何文件，内存报告如实返回。"""
    return build_report_payload(
        status=STATUS_REFUSED, reasons=[reason],
        task_block={"label": TASK_LABEL, "project_name": PROJECT_NAME,
                    "compose_file": "infra/docker-compose.rc-smoke.yml",
                    "base_sha": base_sha, "head_sha": "", "dirty_paths": [],
                    "build_inputs_clean": None},
        images_block={}, ports_block={}, health_block={}, probes_block=[],
        isolation_block={}, cleanup_block={}, timeline=[],
        generated_at=_iso(now))


def _cmd_log(returncode: int, result: CommandResult) -> str:
    return (f"returncode: {returncode}\n--- stdout ---\n{result.stdout}"
            f"\n--- stderr ---\n{result.stderr}\n")


def _machine_snapshot(run) -> dict:
    """全机只读快照：容器（名+状态）/卷/网络/compose 项目 + 原始拼接文本。"""
    containers = run(["docker", "ps", "-a", "--format",
                      "{{.Names}}\t{{.Status}}"]).stdout
    volumes = run(["docker", "volume", "ls", "--format", "{{.Name}}"]).stdout
    networks = run(["docker", "network", "ls", "--format", "{{.Name}}"]).stdout
    compose_ls = run(["docker", "compose", "ls", "--format", "json"]).stdout
    compose_version = run(["docker", "compose", "version"]).stdout.strip()
    docker_version = run(["docker", "version", "--format", "{{.Server.Version}}"]).stdout.strip()
    sections = [
        "=== docker compose version ===", compose_version,
        "=== docker server version ===", docker_version,
        "=== docker ps -a --format '{{.Names}}\\t{{.Status}}' ===", containers,
        "=== docker volume ls ===", volumes,
        "=== docker network ls ===", networks,
        "=== docker compose ls ===", compose_ls,
    ]
    raw = "\n".join(sections) + "\n"
    return {
        "raw": raw,
        "block": {
            "containers": parse_snapshot_lines(containers),
            "volumes": parse_snapshot_lines(volumes),
            "networks": parse_snapshot_lines(networks),
            "compose_projects": parse_compose_ls_names(compose_ls),
        },
    }


def _poll_health(run, docker_env, cid: str, service: str, sleep,
                 poll_interval: float, health_timeout: float) -> str:
    deadline = time.monotonic() + health_timeout
    while True:
        status_value = run(["docker", "inspect", "--format",
                            "{{.State.Health.Status}}", cid],
                           env=docker_env()).stdout.strip()
        if status_value == "healthy":
            return status_value
        if time.monotonic() >= deadline:
            return status_value or "unknown"
        sleep(poll_interval)


def _image_id(run, ref: str) -> str:
    result = run(["docker", "image", "inspect", "--format", "{{json .}}", ref])
    if result.returncode != 0:
        return ""
    try:
        return str(json.loads(result.stdout).get("Id", ""))
    except ValueError:
        return ""


def _image_meta(run, ref: str, state: _RehearsalState, evidence_name: str) -> dict:
    result = run(["docker", "image", "inspect", "--format", "{{json .}}", ref])
    _write_evidence(state, evidence_name, result.stdout)
    if result.returncode != 0:
        return {"tag": ref, "image_id": "", "created": "", "size_bytes": 0}
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return {"tag": ref, "image_id": "", "created": "", "size_bytes": 0}
    return {"tag": ref, "image_id": str(data.get("Id", "")),
            "created": str(data.get("Created", "")),
            "size_bytes": int(data.get("Size", 0))}


# ---------------------------------------------------------------- CLI


class _UsageArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        sys.exit(EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    parser = _UsageArgumentParser(
        prog="rc_smoke_rehearsal.py",
        description=("M14-96 RC 本地彩排冒烟 runner：从当前树构建唯一 tag 的 "
                     "API/Web 镜像，在任务自有隔离 compose 项目里起栈、证明健康"
                     "与匿名保护写契约、只拆除本项目；零生产触碰。"),
    )
    parser.add_argument("--base-sha", required=True,
                        help=("基点 commit SHA（40 位 hex）：runner 会断言 HEAD "
                              "与之相等，漂移即拒绝"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help=("全新输出目录（必须不存在；默认为 canonical "
                              "gitignored 彩排证据目录）"))
    return parser


def run_cli(*, cmd, http, binder, sleep, base_sha: str, output_dir: Path,
            poll_interval: float = HEALTH_POLL_INTERVAL_SECONDS,
            health_timeout: float = HEALTH_TIMEOUT_SECONDS) -> int:
    report, exit_code = run_rehearsal(
        cmd=cmd, http=http, binder=binder, sleep=sleep, now=real_now,
        base_sha=base_sha, output_dir=output_dir,
        poll_interval=poll_interval, health_timeout=health_timeout)
    print(f"{TAG} 状态: {report['status']}"
          + (f"（{', '.join(report['reasons'])}）" if report["reasons"] else ""),
          flush=True)
    print(f"{TAG} 基点: {report['task']['base_sha']} / HEAD: "
          f"{report['task']['head_sha']}", flush=True)
    print(f"{TAG} 项目: {report['task']['project_name']}（compose 文件 "
          f"{report['task']['compose_file']}）", flush=True)
    print(f"{TAG} 报告: rc-smoke-report.json / rc-smoke-report.md / SHA256SUMS.txt",
          flush=True)
    print(f"{TAG} 边界: 本地彩排冒烟，不是 production readiness 声明；"
          "release_ready/production_ready 不变，发布审批 human-only", flush=True)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not _SHA40_RE.fullmatch(args.base_sha or ""):
        parser.error("--base-sha 必须是 40 位小写 hex commit SHA")
    return run_cli(cmd=RealCommandRunner(), http=RealProbeExecutor(),
                   binder=real_binder, sleep=real_sleep,
                   base_sha=args.base_sha, output_dir=args.output_dir)


if __name__ == "__main__":
    sys.exit(main())
