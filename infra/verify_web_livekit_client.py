"""M14-35 Web 真实 LiveKit 客户端连接验收（Playwright Chromium + 复用生产栈）。

拓扑（不重启任何既有服务）：
    - API(:8000) / LiveKit(信令地址由契约派生，见「LiveKit 信令面探测」) /
      DB：复用当前运行的生产容器，只读探测 +
      合法 API 写入（注册/登录验收用户、签发房间 token——均为业务端点）；
    - web：本脚本在空闲端口启动【本分支构建】的 Next 服务（构建期注入
      NEXT_PUBLIC_API_BASE_URL="" + AIOS_ACCEPTANCE_API_PROXY=<api>），
      经同源 /api/* rewrite 网关访问上游 API（生产 CORS 是精确 allowlist，
      不为此验收放行新源）；验收结束即关闭该自建进程。

进程生命周期（M14-36）：自建 web 以真实 node 直启 next start（`node -p
process.execPath` 解析可执行文件，绕过 mise/npm shim——旧版以 npm 包装器为
Popen 对象、结束时只 terminate 包装器 PID，Windows 上进程终止不级联子进程，
跨次验收累计 26 个 node.exe/mise.exe 孤儿进程锁死 worktree 文件句柄）。结束
时按 Popen PID 精确树回收：Windows taskkill /PID <pid> /T /F；POSIX 以独立
进程组（start_new_session）启动并 SIGTERM→SIGKILL 该组。绝不按端口或进程名
扫杀（杜绝误伤生产进程）。

浏览器模式（M14-37，AIOS_LIVEKIT_BROWSER_LOOPBACK 严格开关）：
    背景（M14-35 遗留生产阻塞）：当前 compose 生产栈 LiveKit 以 --node-ip
    127.0.0.1 通告媒体地址且 UDP 端口只绑定 127.0.0.1；Chromium/WebRTC 默认
    不收集 loopback ICE candidate，默认浏览器直连存在间歇性 ICE 失败（Codex
    独立默认首跑 connect failed：could not establish pc connection；同构建
    复跑通过——拓扑级不确定，非可忽略抖动）。两种显式模式：
      - default（未设置/0）：绝不注入 loopback flag——代表生产用户默认
        浏览器拓扑；失败即真实生产阻塞证据，如实计数绝不虚报；
      - controlled（=1）：追加 LOOPBACK_FLAG 常量，使浏览器收集 loopback
        ICE candidate 与 LiveKit 的 127.0.0.1 媒体地址直接配对（M14-35 R2
        受控验收口径），通过仅代表受控拓扑而非默认拓扑；
      - 任何其他值：ENV-BLOCKED fail-closed（拒绝执行，绝不静默当默认）。
    results.json 的 browser 段记录模式与 Chromium argv 摘要（可审计，
    不含任何凭据）。

LiveKit 信令面探测（M14-38，契约派生 + 显式 override）：
    旧版 preflight 硬编码探测本机 loopback 信令端口——LAN cutover（媒体面
    绑本机 LAN IP）拓扑下探测目标错误。现改为：
    - 默认（AIOS_PUBLIC_LIVEKIT_URL 未设置）：登录验收用户后请求
      POST /api/v1/voice/token，以响应 ws_url 派生同源 http/https 健康 URL
      （与浏览器实际拿到的地址同源——探测即业务契约本身，不重造第二事实
      源）；loopback 拓扑派生本机 loopback 信令地址、LAN cutover 拓扑派生
      LAN 信令地址（地址/端口全部来自部署事实），两类拓扑零改动覆盖；
    - 显式 override（AIOS_PUBLIC_LIVEKIT_URL 已设置）：严格校验（仅 ws/wss
      + 非空主机名）后采用，优先于契约派生；空串/非法 scheme/缺主机名 →
      ENV-BLOCKED fail-closed（显式配置写错当场暴露，绝不静默回退）；
    - 派生/override 后探测不可达 → ENV-BLOCKED（禁止自行启动/重启服务）；
    - access_token 与房间 JWT 只进内存与请求头，绝不落日志/报告/DOM；
      results.json 的 livekit_probe 段只记 source/ws_url/health_url/
      http_status（派生事实可审计，无任何凭据）。

覆盖（真实浏览器行为断言）：
    1 登录后打开 /voice，出现 LiveKit 连接检测卡片；
    2 点击开始检测：token/connect/data/cleanup 四步必须 passed；
    3 麦克风 fake 设备 + 授权下发布音轨（mic passed）；
      环境不可用时降级 skipped-with-reason（verdict=passed-with-skip）；
    4 页面 DOM 不含 JWT 形态的 token（绝不渲染密钥）；
    5 ws_url 回显为浏览器可达地址（ws/wss）；
    6 console/pageerror 分窗硬断言（R1）：登录窗口的预期 401 单独记录
      不计入；进入 /voice 起的检测窗口 console error 与 pageerror 必须为 0。

判定：上述 1/2/4/5/6 全过且 mic passed|skipped → exit 0（results.json 区分
      passed / passed-with-skip）；任何硬断言失败 → exit 1（fail-closed，
      不伪造 PASS）。环境不可达 → exit 2。

用法（canonical 仓库 venv，已装 playwright + chromium）：
    "D:/AI Learning OS/ai-learning-os/.venv/Scripts/python.exe" infra/verify_web_livekit_client.py
环境变量：
    AIOS_API_BASE   默认 http://127.0.0.1:8000
    AIOS_WEB_PORT   指定 web 端口（默认自动挑空闲端口）
    AIOS_SKIP_BUILD=1 跳过构建（复用上一次验收构建产物）
    AIOS_OUT        证据目录，默认 .verify/m14-35-web-livekit-client
    AIOS_LIVEKIT_BROWSER_LOOPBACK  严格开关 0|1：默认（未设置/0）为 default
                    模式（绝不注入 loopback flag）；1 为 controlled 受控
                    模式；任何其他值 ENV-BLOCKED fail-closed（M14-37）
    AIOS_PUBLIC_LIVEKIT_URL  LiveKit 信令探测显式 override（M14-38）：未
                    设置 = 由 POST /api/v1/voice/token 响应 ws_url 契约派生；
                    设置 = 严格校验（ws/wss + 非空主机名）后采用；空串/
                    非法值 ENV-BLOCKED fail-closed
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_BASE = os.environ.get("AIOS_API_BASE", "http://127.0.0.1:8000")
LEARNER = "learner_m14_35"
PASSWORD = "password-123"
CHECK_TIMEOUT_MS = 90_000
# JWT 三段形态（headers.payload.signature）：用于脱敏与「DOM 不含 token」断言
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")

# --- 浏览器模式开关（M14-37：default 默认拓扑 / controlled 受控拓扑） ---

#: 严格开关 env：仅接受字面 "0"/"1"；未设置 = default；任何其他值 fail-closed。
LOOPBACK_ENV = "AIOS_LIVEKIT_BROWSER_LOOPBACK"

#: 受控模式专属 Chromium flag——全源码唯一字面量定义处（launch 只引用本常量，
#: 契约测试锁定字面量恰好出现一次，杜绝任何旁路硬编码把它带进 default 模式）。
LOOPBACK_FLAG = "--allow-loopback-in-peer-connection"

#: 基础 Chromium 启动参数（两种模式共用）：fake 麦克风设备 + 免授权 UI。
BASE_CHROMIUM_ARGS = (
    "--use-fake-device-for-media-stream",
    "--use-fake-ui-for-media-stream",
)


def browser_loopback_enabled(env: Mapping[str, str] | None = None) -> bool:
    """解析模式开关：未设置/0 → False（default）；仅字面 "1" → True（controlled）。

    任何其他值（空串/空白/"true"/"01"/带换行等变体）一律 SystemExit
    fail-closed——绝不静默当默认值，防止拼错的开关把受控拓扑误标成
    默认拓扑验收。
    """
    source: Mapping[str, str] = os.environ if env is None else env
    raw = source.get(LOOPBACK_ENV)
    if raw is None or raw == "0":
        return False
    if raw == "1":
        return True
    raise SystemExit(
        f"ENV-BLOCKED: {LOOPBACK_ENV} 仅接受 0|1（收到 {raw!r}），fail-closed"
    )


def chromium_launch_args(loopback: bool) -> list[str]:
    """Chromium 启动参数：default = 基础两条；controlled = 追加 loopback flag。"""
    args = list(BASE_CHROMIUM_ARGS)
    if loopback:
        args.append(LOOPBACK_FLAG)
    return args


def browser_report(loopback: bool) -> dict:
    """results.json 的 browser 段：模式与 argv 摘要（可审计，不含任何凭据）。"""
    return {
        "mode": "controlled" if loopback else "default",
        "loopback_env_var": LOOPBACK_ENV,
        "loopback_flag_present": loopback,
        "chromium_launch_args": chromium_launch_args(loopback),
    }

CHECKS: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAIL: {label} {detail}")
    CHECKS.append(label)
    print(f"  PASS {label}")


def sanitize(text: str) -> str:
    """证据落盘前统一脱敏：JWT 形态串替换为占位符。"""
    return JWT_RE.sub("[REDACTED-JWT]", text)


# --- 环境前置（只读探测生产栈） ---


def http_status(url: str, timeout: float = 5.0) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code  # 任何 HTTP 应答都证明端口活着
    except OSError:
        return None


def preflight() -> None:
    """API 面前置探测（只读）。

    M14-38 起 LiveKit 信令面探测不再硬编码 loopback 地址——由
    resolve_livekit_health 以契约/override 派生地址单独探测（两类拓扑零改动）。
    """
    status = http_status(f"{API_BASE}/health")
    if status is None:
        raise SystemExit("ENV-BLOCKED: 生产 API 不可达（禁止自行启动/重启服务，fail-closed）")
    check("env: 生产 API 可达", True, f"/health -> {status}")


# --- 验收用户（走业务端点，幂等） ---


def api_call(method: str, path: str, body: dict | None = None,
             headers: dict[str, str] | None = None):
    """业务端点调用（可带请求头——如登录后的 Bearer；头值绝不落日志/报告）。"""
    req = urllib.request.Request(API_BASE + path, method=method)
    data = json.dumps(body).encode() if body is not None else None
    if data:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, data) as response:
            payload = response.read()
            return response.status, json.loads(payload) if payload else {}
    except urllib.error.HTTPError as error:
        payload = error.read()
        return error.code, json.loads(payload) if payload else {}


def ensure_user() -> str:
    """注册/登录验收用户（幂等）→ access_token（M14-38）。

    token 只留在内存供后续请求头使用，绝不落日志/报告/DOM；失败仅报
    HTTP 状态码（失败响应体可能含敏感上下文，不回显）。
    """
    api_call("POST", "/api/v1/auth/register", body={"username": LEARNER, "password": PASSWORD})
    status, payload = api_call("POST", "/api/v1/auth/login",
                               body={"username": LEARNER, "password": PASSWORD})
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if status != 200 or not token:
        raise SystemExit(f"ENV-BLOCKED: 验收用户登录失败（HTTP {status}，token 缺失）")
    check("seed: 验收用户可登录", True)
    return str(token)


# --- LiveKit 信令面探测（M14-38：契约派生优先，显式 override 严格校验） ---

#: 显式 override env：设置时覆盖契约派生（用于探测目标与业务契约解耦的拓扑）。
LIVEKIT_OVERRIDE_ENV = "AIOS_PUBLIC_LIVEKIT_URL"

_WS_SCHEMES = ("ws", "wss")


def _validated_ws_url(raw: str, origin: str) -> str:
    """ws/wss + 非空主机名严格校验；失败 → ENV-BLOCKED（fail-closed）。

    URL 本身不是凭据，错误消息可含解析结果便于定位配置错误。
    """
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in _WS_SCHEMES or not parsed.hostname:
        raise SystemExit(
            f"ENV-BLOCKED: {origin} 非法（仅接受 ws://|wss:// + 非空主机名，"
            f"收到 scheme={parsed.scheme!r} host={parsed.hostname!r}——fail-closed）"
        )
    return raw


def livekit_override_url(env: Mapping[str, str] | None = None) -> str | None:
    """解析 AIOS_PUBLIC_LIVEKIT_URL 显式 override。

    未设置 → None（走 voice/token 契约派生）；设置 → 严格校验后返回原值；
    空串/非法 scheme/缺主机名 → ENV-BLOCKED（绝不静默回退派生路径——显式
    配置写错必须当场暴露）。与 LOOPBACK 开关同口径：不 strip，值必须精确。
    """
    source: Mapping[str, str] = os.environ if env is None else env
    raw = source.get(LIVEKIT_OVERRIDE_ENV)
    if raw is None:
        return None
    if not raw:
        raise SystemExit(
            f"ENV-BLOCKED: {LIVEKIT_OVERRIDE_ENV} 显式设置为空——请移除该变量"
            "（走契约派生）或填合法 URL（fail-closed）"
        )
    return _validated_ws_url(raw, LIVEKIT_OVERRIDE_ENV)


def ws_url_to_health_url(ws_url: str) -> str:
    """LiveKit 信令 ws/wss URL → 同源 http/https 健康探测 URL（纯函数）。

    ws→http、wss→https；保留 netloc 与路径（无路径取 /），丢弃 query/
    fragment；非 ws/wss 或缺主机名 → ENV-BLOCKED。
    """
    parsed = urllib.parse.urlparse(_validated_ws_url(ws_url, "LiveKit ws_url"))
    scheme = "https" if parsed.scheme == "wss" else "http"
    return f"{scheme}://{parsed.netloc}{parsed.path or '/'}"


def fetch_contract_ws_url(api_token: str) -> str:
    """经真实业务契约派生 LiveKit 信令地址：POST /api/v1/voice/token → ws_url。

    探测即浏览器同款契约（浏览器拿什么地址，探测就打什么地址）；api_token
    只进本次请求头；失败仅报 HTTP 状态码，绝不回显响应体（内含房间 JWT）。
    """
    room = f"preflight-{int(time.time())}"  # 满足 ^[A-Za-z0-9_-]{3,64}$
    status, payload = api_call(
        "POST", "/api/v1/voice/token",
        body={"room": room, "ttl_seconds": 60},
        headers={"Authorization": f"Bearer {api_token}"},
    )
    ws_url = payload.get("ws_url") if isinstance(payload, dict) else None
    if status != 200 or not ws_url:
        raise SystemExit(
            f"ENV-BLOCKED: 语音 token 契约不可用（HTTP {status}，ws_url 缺失"
            "——LiveKit 未配置或 API 异常，fail-closed）"
        )
    return str(ws_url)


def resolve_livekit_health(api_token: str) -> dict:
    """LiveKit 信令面探测编排（M14-38）：显式 override 优先，默认契约派生。

    返回 results.json 的 livekit_probe 段：source/ws_url/health_url/
    http_status——派生事实可审计，无任何凭据；探测不可达 → ENV-BLOCKED
    （禁止自行启动/重启服务，fail-closed）。
    """
    override = livekit_override_url()
    if override is not None:
        ws_url, source = override, "env-override"
    else:
        ws_url, source = fetch_contract_ws_url(api_token), "voice-token-contract"
    health_url = ws_url_to_health_url(ws_url)
    status = http_status(health_url)
    if status is None:
        raise SystemExit(
            f"ENV-BLOCKED: LiveKit 信令端口不可达（{health_url}，来源={source}；"
            "禁止自行启动/重启服务，fail-closed）"
        )
    check(f"env: LiveKit 信令端口可达（来源={source}）", True, f"{health_url} -> HTTP {status}")
    return {"source": source, "ws_url": ws_url, "health_url": health_url, "http_status": status}


# --- 本分支 web：构建 + 空闲端口自起自收 ---

#: POSIX 组杀信号：Windows 上 signal.SIGKILL 不存在，取等价数值保证可移植可测
_SIG_TERM = getattr(signal, "SIGTERM", 15)
_SIG_KILL = getattr(signal, "SIGKILL", 9)

#: next CLI 真实入口候选（node 脚本）：绕过 npm 包装链直接以 node 启动。
#: 本仓库为 npm workspaces——依赖提升到仓库根 node_modules（实际形态）；
#: 兼容 workspace 本地 node_modules。惰性解析：导入期不触发（CI 的 pytest
#: 环境无 node_modules 也能 import 本模块跑契约测试）。
NEXT_BIN_CANDIDATES = (
    REPO_ROOT / "node_modules" / "next" / "dist" / "bin" / "next",
    REPO_ROOT / "apps" / "web" / "node_modules" / "next" / "dist" / "bin" / "next",
)


def next_bin() -> Path:
    """解析 next CLI 真实入口（存在即用；都缺 → ENV-BLOCKED fail-closed）。"""
    for candidate in NEXT_BIN_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "ENV-BLOCKED: 未找到 next CLI（候选均不存在："
        f"{'; '.join(str(c) for c in NEXT_BIN_CANDIDATES)}；apps/web 依赖未安装？）"
    )


def _is_windows() -> bool:
    """平台判定唯一入口（测试注入替身，无需污染全局 os.name）。"""
    return os.name == "nt"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def npm() -> str:
    for name in ("npm", "npm.cmd"):
        path = shutil.which(name)
        if path:
            return path
    raise SystemExit("ENV-BLOCKED: 未找到 npm")


def resolve_node_executable() -> str:
    """解析真实 Node 可执行文件路径（`node -p process.execPath`）。

    mise/npm shim 只是短命引导；让它们做 Popen 父进程会在 Windows 上留下
    长命孤儿链（M14-36 缺陷根因）。解析出的真实 node 才直接作为 next
    start 的宿主进程，Popen PID 即最终长命进程本身。
    """
    try:
        result = subprocess.run(
            ["node", "-p", "process.execPath"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as cause:
        raise SystemExit(
            f"ENV-BLOCKED: node 不可用，无法解析真实可执行文件（{type(cause).__name__}）"
        ) from cause
    path = (result.stdout or "").strip()
    if result.returncode != 0 or not path:
        raise SystemExit("ENV-BLOCKED: `node -p process.execPath` 解析失败（真实 node 不可用）")
    return path


def build_web(out_dir: Path) -> None:
    env = dict(os.environ)
    # 空串（非未设置）：API_BASE 变相对路径 → 浏览器全程同源请求
    env["NEXT_PUBLIC_API_BASE_URL"] = ""
    env["AIOS_ACCEPTANCE_API_PROXY"] = API_BASE
    result = subprocess.run(
        [npm(), "run", "build"], cwd=REPO_ROOT / "apps" / "web", env=env,
        capture_output=True, text=True, timeout=900, check=False,
    )
    if result.returncode != 0:
        (out_dir / "build-stderr.log").write_text(sanitize(result.stderr[-20_000:]), encoding="utf-8")
        raise AssertionError(f"FAIL: web 构建失败（脱敏日志见 {out_dir / 'build-stderr.log'}）")
    check("build: 本分支 web 验收构建成功", True)


def start_web(port: int) -> subprocess.Popen:
    """直接以真实 node 启动 next start（不经 npm 包装链，M14-36）。

    Popen PID 即 next start 的 node 宿主进程本身（无中间 mise/cmd/npm shim）；
    POSIX 上以独立会话/进程组启动（pgid==pid），保证停止时可精确、有界地只
    回收本进程树。绝不按端口或进程名扫杀。
    """
    env = dict(os.environ)
    env["NEXT_PUBLIC_API_BASE_URL"] = ""
    env["AIOS_ACCEPTANCE_API_PROXY"] = API_BASE
    proc = subprocess.Popen(
        [resolve_node_executable(), str(next_bin()), "start", "-p", str(port)],
        cwd=REPO_ROOT / "apps" / "web", env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=not _is_windows(),
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        if proc.poll() is not None:
            raise AssertionError(f"FAIL: web 服务提前退出（code={proc.returncode}）")
        if http_status(f"{base}/login", timeout=2.0) is not None:
            check("web: 自建验收服务就绪", True, base)
            return proc
        time.sleep(1)
    raise AssertionError("FAIL: web 服务 60s 未就绪")


def stop_process_tree(proc: subprocess.Popen, *, grace_seconds: float = 15.0) -> None:
    """有界、精确的进程树回收（M14-36）：只针对本脚本 Popen 出的 PID 树。

    - 进程已退出：直接返回（不发起任何 kill）；
    - Windows：taskkill /PID <pid> /T /F——/T 恰好沿该 PID 的进程树递归终止，
      不按端口/进程名扫描（杜绝误伤生产进程）；
    - POSIX：start_web 已用 start_new_session 使其自成进程组（pgid==pid），
      先对组 SIGTERM、有界等待，超时再对组 SIGKILL；组已消亡被容忍。
    最后统一有界 wait() 回收 Popen 句柄。
    """
    if proc.poll() is not None:
        return
    if _is_windows():
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True, timeout=60, check=False,
        )
    else:
        try:
            os.killpg(proc.pid, _SIG_TERM)
        except OSError:
            pass  # 组已消亡
        try:
            proc.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, _SIG_KILL)
            except OSError:
                pass
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            pass


# --- 浏览器验收 ---


def run_browser(web_base: str, shots: Path, loopback: bool = False) -> dict:
    from playwright.sync_api import sync_playwright

    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            # M14-37：模式由 AIOS_LIVEKIT_BROWSER_LOOPBACK 严格开关决定——
            # default 模式绝不注入 loopback flag（代表生产用户默认浏览器）；
            # controlled 模式追加 LOOPBACK_FLAG 常量，使浏览器可收集 loopback
            # ICE candidate 与 LiveKit 的 127.0.0.1 媒体地址直接配对。
            args=chromium_launch_args(loopback),
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900}, locale="zh-CN",
            permissions=["microphone"],
        )
        page = context.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: console_errors.append(str(err)))

        # 登录（UI 表单 → HttpOnly cookie，同源 rewrite 生效即证明网关拓扑可用）
        page.goto(f"{web_base}/login")
        page.fill("#username", LEARNER)
        page.fill("#password", PASSWORD)
        page.locator("form").get_by_role("button", name="登录").click()
        page.wait_for_url(f"{web_base}/", timeout=15_000)
        check("web: UI 登录成功（同源 rewrite 网关生效）", True)

        # R1：采集窗口切分 —— 登录窗口（含未认证时 auth/me 的预期 401）
        # 单独记录、不计入检测错误；检测窗口从进入 /voice 起算，必须零错误。
        login_console_errors = list(console_errors)
        console_errors.clear()

        page.goto(f"{web_base}/voice")
        page.locator("[data-livekit-check]").wait_for(timeout=15_000)
        check("ui: voice 首页渲染检测卡片", True)
        page.screenshot(path=str(shots / "01-initial.png"), full_page=True)

        page.get_by_role("button", name="开始检测").click()
        page.wait_for_function(
            "document.querySelector('[data-livekit-check]')?.dataset.checkOutcome !== 'incomplete'",
            timeout=CHECK_TIMEOUT_MS,
        )
        page.wait_for_timeout(500)  # 收尾渲染（结果横幅/跳过原因）
        page.screenshot(path=str(shots / "02-result.png"), full_page=True)

        outcome = page.locator("[data-livekit-check]").get_attribute("data-check-outcome") or ""
        steps = {
            step: page.locator(f'[data-check-step="{step}"]').get_attribute("data-check-status") or ""
            for step in ("token", "connect", "data", "mic", "cleanup")
        }
        ws_url = page.locator("[data-check-ws-url]").first.get_attribute("data-check-ws-url") if page.locator("[data-check-ws-url]").count() else ""
        mic_skip = page.locator("[data-check-mic-skip]").get_attribute("data-check-mic-skip") if page.locator("[data-check-mic-skip]").count() else ""

        dom = page.content()
        check("hard: token/connect/data/cleanup 全部 passed",
              all(steps[s] == "passed" for s in ("token", "connect", "data", "cleanup")), json.dumps(steps))
        check("hard: 麦克风步 passed 或 skipped（不虚报）", steps["mic"] in ("passed", "skipped"), json.dumps(steps))
        check("hard: DOM 不含 JWT 形态 token", JWT_RE.search(dom) is None)
        check("hard: ws_url 回显为可达地址", bool(ws_url) and ws_url.startswith(("ws://", "wss://")), str(ws_url))
        check("hard: 结论与麦克风步一致",
              outcome in ("passed", "passed-with-skip")
              and (outcome == "passed") == (steps["mic"] == "passed"), outcome)
        check("hard: 检测窗口 console/pageerror 零错误",
              len(console_errors) == 0, json.dumps([sanitize(e)[:300] for e in console_errors[:10]]))
        if steps["mic"] == "skipped":
            check("soft: 麦克风跳过带明确原因", bool(mic_skip), str(mic_skip))
        browser.close()

    return {
        "outcome": outcome,
        "steps": steps,
        "ws_url": ws_url,
        "mic_skip_reason": mic_skip,
        # 检测窗口（进入 /voice 起）：硬断言必须为 0
        "console_error_count": len(console_errors),
        "console_errors_sanitized": [sanitize(e)[:300] for e in console_errors[:20]],
        # 登录窗口单独记录（未认证时 auth/me 的预期 401 等，不计入检测错误）
        "login_console_error_count": len(login_console_errors),
        "login_console_errors_sanitized": [sanitize(e)[:300] for e in login_console_errors[:20]],
    }


def main() -> int:
    out_dir = Path(os.environ.get("AIOS_OUT", REPO_ROOT / ".verify" / "m14-35-web-livekit-client"))
    shots = out_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    livekit_probe: dict = {}
    try:
        loopback = browser_loopback_enabled()  # M14-37 严格开关（fail-closed 入口）
        preflight()
        api_token = ensure_user()  # access_token 只进内存（M14-38）
        livekit_probe = resolve_livekit_health(api_token)
    except SystemExit as cause:
        print(cause, file=sys.stderr)
        return 2

    port = int(os.environ.get("AIOS_WEB_PORT") or free_port())
    if os.environ.get("AIOS_SKIP_BUILD") != "1":
        build_web(out_dir)
    proc = None
    verdict = "failed"
    results: dict = {"verdict": verdict}
    try:
        proc = start_web(port)
        results = run_browser(f"http://127.0.0.1:{port}", shots, loopback=loopback)
        verdict = "passed" if results["steps"].get("mic") == "passed" else "passed-with-skip"
    except AssertionError as cause:
        print(cause, file=sys.stderr)
        results = {"verdict": "failed", "error": sanitize(str(cause))}
    except Exception as cause:  # noqa: BLE001 —— 验收工具必须兜底落盘后退出
        print(f"ERROR: {cause}", file=sys.stderr)
        results = {"verdict": "failed", "error": sanitize(f"{type(cause).__name__}: {cause}")}
    finally:
        if proc is not None:
            stop_process_tree(proc)
    results["verdict"] = verdict
    results["browser"] = browser_report(loopback)  # 模式可审计（M14-37）
    results["livekit_probe"] = livekit_probe  # 探测来源/地址可审计（M14-38，无凭据）
    results["checks_passed"] = CHECKS
    (out_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"\nverdict={verdict}  browser={results['browser']['mode']}"
        f"  ({len(CHECKS)} checks)  证据: {out_dir}"
    )
    return 0 if verdict in ("passed", "passed-with-skip") else 1


if __name__ == "__main__":
    sys.exit(main())
