"""M14-35 Web 真实 LiveKit 客户端连接验收（Playwright Chromium + 复用生产栈）。

拓扑（不重启任何既有服务）：
    - API(:8000) / LiveKit(:7880) / DB：复用当前运行的生产容器，只读探测 +
      合法 API 写入（注册/登录验收用户、签发房间 token——均为业务端点）；
    - web：本脚本在空闲端口启动【本分支构建】的 Next 服务（构建期注入
      NEXT_PUBLIC_API_BASE_URL="" + AIOS_ACCEPTANCE_API_PROXY=<api>），
      经同源 /api/* rewrite 网关访问上游 API（生产 CORS 是精确 allowlist，
      不为此验收放行新源）；验收结束即关闭该自建进程。

受控验收条件（R2，非生产用户默认）：
    当前 compose 生产栈 LiveKit 以 --node-ip 127.0.0.1 通告媒体地址且 UDP
    端口只绑定 127.0.0.1；Chromium/WebRTC 默认不收集 loopback ICE candidate，
    ICE 配对依赖 Docker/Windows 网络路径时存在间歇性失败（Codex 独立默认
    首跑 connect failed：could not establish pc connection，失败房间所有
    ICE candidate pair failed；同构建复跑通过——拓扑级不确定，非可忽略抖动）。
    本验收因此显式加 --allow-loopback-in-peer-connection，使浏览器可收集
    loopback candidate 与 LiveKit 的 127.0.0.1 媒体地址直接配对。该 flag 是
    本机 loopback LiveKit 部署的受控验收条件，生产用户浏览器默认并不具备；
    本脚本 3/3 通过只代表受控拓扑，不代表默认浏览器直连拓扑稳定（默认拓扑
    的受控改造/验收是独立的生产阻塞项，见 docs/ROADMAP.md）。

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
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_BASE = os.environ.get("AIOS_API_BASE", "http://127.0.0.1:8000")
LEARNER = "learner_m14_35"
PASSWORD = "password-123"
CHECK_TIMEOUT_MS = 90_000
# JWT 三段形态（headers.payload.signature）：用于脱敏与「DOM 不含 token」断言
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")

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
    status = http_status(f"{API_BASE}/health")
    if status is None:
        raise SystemExit("ENV-BLOCKED: 生产 API 不可达（禁止自行启动/重启服务，fail-closed）")
    check("env: 生产 API 可达", True, f"/health -> {status}")
    lk = http_status("http://127.0.0.1:7880/")
    if lk is None:
        raise SystemExit("ENV-BLOCKED: LiveKit 7880 不可达（禁止自行启动/重启服务，fail-closed）")
    check("env: LiveKit 信令端口可达", True, f"HTTP {lk}")


# --- 验收用户（走业务端点，幂等） ---


def api_call(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(API_BASE + path, method=method)
    data = json.dumps(body).encode() if body is not None else None
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data) as response:
            payload = response.read()
            return response.status, json.loads(payload) if payload else {}
    except urllib.error.HTTPError as error:
        payload = error.read()
        return error.code, json.loads(payload) if payload else {}


def ensure_user() -> None:
    api_call("POST", "/api/v1/auth/register", body={"username": LEARNER, "password": PASSWORD})
    status, _ = api_call("POST", "/api/v1/auth/login", body={"username": LEARNER, "password": PASSWORD})
    if status != 200:
        raise SystemExit(f"ENV-BLOCKED: 验收用户登录失败（HTTP {status}）")
    check("seed: 验收用户可登录", True)


# --- 本分支 web：构建 + 空闲端口自起自收 ---


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
    env = dict(os.environ)
    env["NEXT_PUBLIC_API_BASE_URL"] = ""
    env["AIOS_ACCEPTANCE_API_PROXY"] = API_BASE
    proc = subprocess.Popen(
        [npm(), "run", "start", "--", "-p", str(port)],
        cwd=REPO_ROOT / "apps" / "web", env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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


# --- 浏览器验收 ---


def run_browser(web_base: str, shots: Path) -> dict:
    from playwright.sync_api import sync_playwright

    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
                # R2 受控验收条件（非生产用户默认）：当前 LiveKit --node-ip
                # 127.0.0.1 且 UDP 仅绑 loopback；Chromium/WebRTC 默认不收集
                # loopback ICE candidate，默认直连存在间歇性 ICE 失败（Codex
                # 独立实证）。此 flag 使浏览器收集 loopback candidate 与
                # 127.0.0.1 媒体地址直接配对——只代表受控拓扑，见模块 docstring。
                "--allow-loopback-in-peer-connection",
            ],
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
    try:
        preflight()
        ensure_user()
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
        results = run_browser(f"http://127.0.0.1:{port}", shots)
        verdict = "passed" if results["steps"].get("mic") == "passed" else "passed-with-skip"
    except AssertionError as cause:
        print(cause, file=sys.stderr)
        results = {"verdict": "failed", "error": sanitize(str(cause))}
    except Exception as cause:  # noqa: BLE001 —— 验收工具必须兜底落盘后退出
        print(f"ERROR: {cause}", file=sys.stderr)
        results = {"verdict": "failed", "error": sanitize(f"{type(cause).__name__}: {cause}")}
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
    results["verdict"] = verdict
    results["checks_passed"] = CHECKS
    (out_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nverdict={verdict}  ({len(CHECKS)} checks)  证据: {out_dir}")
    return 0 if verdict in ("passed", "passed-with-skip") else 1


if __name__ == "__main__":
    sys.exit(main())
