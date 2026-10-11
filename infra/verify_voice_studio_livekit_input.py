"""M14-254 VoiceStudio LiveKit 输入真实浏览器验收 harness（supervisor 运维）。

验证路径（对既有生产栈零重启、零数据播种、零凭据落盘）：
    登录真实 Web UI → 打开 /voice/<paperId> 权威 VoiceSession → 点击
    语音作答 → 懒连接（token → Room.connect → 麦克风轨 → publish）→
    data-mic-phase="recording"（M14-252 语义下 recording 只能在上述
    链全部成功后到达）→ 停止 utterance → 非空 WAV multipart 到达
    POST /api/v1/voice/transcribe → mic phase 回 idle。

复用 infra/verify_web_livekit_client.py 的全部加固模式（经 importlib
加载该模块，不复制易碎逻辑）：
    - API/LiveKit 只读 preflight（契约派生信令探测，fail-closed）；
    - 既有验收用户 login-only 认证（只调业务登录端点；绝不注册/
      播种用户——token 只进内存请求头）；
    - 真实 node 直启 next start + 精确 PID 树回收（绝不按端口/进程名
      扫杀，M14-36 模式）；
    - Chromium fake 麦克风 + 免授权 UI + 麦克风 permission；
    - AIOS_LIVEKIT_BROWSER_LOOPBACK 严格开关（default/controlled）；
    - JWT 正则脱敏 + 报告卫生（M14-35 R1 分窗 console 断言）。

网络事实只记净化元数据：method/path/status/timing、token 请求的
room、transcribe 的 multipart 字节数与顶层 content-type；multipart
体内 audio/wav part 与 answer.wav 文件名以内存子串断言（布尔入报告，
原始音频字节绝不保留）。token 响应体（房间 JWT）从不读取；
Authorization 头/cookie 绝不入报告。

判定（fail-closed，不伪造 PASS）：
    - token 恰一次且 room === `voice-<data-voice-session-id>`；
    - transcribe 恰一次、HTTP 200、multipart 含 audio/wav part 与
      answer.wav 文件名、字节数 > 0；
    - mic phase 到达过 recording 且最终回 idle（空 transcript/诚实
      clarify/error 可接受——仅当 transcribe HTTP 链成功；不构成
      流式 ASR 成功声明）；
    - 验证窗 console error/pageerror 为 0（登录窗预期噪声单独记录）；
    - DOM 与序列化报告零 JWT 形态。
    通过 → exit 0；硬断言失败 → exit 1；环境不可达 → exit 2。

真实栈运行保留给 supervisor（本仓库只交付 harness 与零网络契约
测试；本切片不预声明任何真实运行结果）。

用法（canonical 仓库 venv）：
    "D:/AI Learning OS/ai-learning-os/.venv/Scripts/python.exe" \
        infra/verify_voice_studio_livekit_input.py
环境变量：
    AIOS_API_BASE             默认 http://127.0.0.1:8000
    AIOS_VOICE_PAPER_ID       显式指定试卷 id（缺省确定性取第一份；
                              显式设置但为空/不存在 → ENV-BLOCKED）
    AIOS_WEB_PORT / AIOS_SKIP_BUILD / AIOS_OUT
                              同 verify_web_livekit_client.py
    AIOS_LIVEKIT_BROWSER_LOOPBACK / AIOS_PUBLIC_LIVEKIT_URL
                              同 verify_web_livekit_client.py（严格开关）
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- 复用邻居 harness 的加固模块（importlib 加载，与契约测试同模式） ---


def _load_neighbor():
    spec = importlib.util.spec_from_file_location(
        "verify_web_livekit_client", REPO_ROOT / "infra" / "verify_web_livekit_client.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VWLC = _load_neighbor()

API_BASE = VWLC.API_BASE
JWT_RE = VWLC.JWT_RE
LEARNER = VWLC.LEARNER
PASSWORD = VWLC.PASSWORD
TOKEN_PATH_SUFFIX = "/api/v1/voice/token"
TRANSCRIBE_PATH_SUFFIX = "/api/v1/voice/transcribe"
PAPER_ID_ENV = "AIOS_VOICE_PAPER_ID"
#: M14-254 DOM 契约（apps/web voice-studio.tsx 钉住）：根/麦克风阶段/
#: 权威 session_id——属性值只允许 micPhase 四态与 session UUID，非凭据。
VOICE_STUDIO_SELECTOR = "[data-voice-studio]"
MIC_PHASES = ("idle", "connecting", "recording", "transcribing")
#: harness 追加的 Chromium 参数：自动播放策略放开（headless 下让既有
#: TTS 朗读链真实完成，消除 autoplay 拒绝噪声——不代表生产浏览器行为，
#: 验收面是麦克风输入链）。
AUTOPLAY_FLAG = "--autoplay-policy=no-user-gesture-required"
#: 各等待阶段有界超时（毫秒）——绝不无限等待。
WAIT_SESSION_READY_MS = 30_000
WAIT_RECORDING_MS = 45_000
WAIT_IDLE_AFTER_STOP_MS = 60_000

CHECKS: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAIL: {label} {detail}")
    CHECKS.append(label)
    print(f"  PASS {label}")


# --- 可测纯函数（零网络/零浏览器；mock 契约测试逐项锁定） ---


def phase_valid(phase: str | None) -> bool:
    """DOM 读出的 mic phase 必须落在契约四态内（未知值 = 契约破坏）。"""
    return phase in MIC_PHASES


def expected_token_room(session_id: str) -> str:
    """DOM session id → 期待 token 房间名（M14-252 voice-<session_id>）。"""
    return f"voice-{session_id}"


def select_paper(papers: list[dict], override: str | None) -> dict:
    """确定性选卷：显式 override 精确命中；缺省取第一份；零卷 → ENV-BLOCKED。

    显式设置（非 None）但空串 → ENV-BLOCKED（配置错误当场暴露，与
    M14-38 override 语义同口径）；指定的 id 不在列表 → ENV-BLOCKED。
    绝不播种/变更数据——只读选择。
    """
    if override is not None:
        if not override:
            raise SystemExit(
                f"ENV-BLOCKED: {PAPER_ID_ENV} 显式设置为空——请移除该变量"
                "（确定性取第一份）或填有效试卷 id（fail-closed）"
            )
        for paper in papers:
            if paper.get("id") == override:
                return paper
        raise SystemExit(
            f"ENV-BLOCKED: {PAPER_ID_ENV} 指定的试卷不存在（id={override!r}，"
            f"现有 {len(papers)} 份——fail-closed，绝不静默改选）"
        )
    if not papers:
        raise SystemExit(
            "ENV-BLOCKED: 可用试卷为空（GET /api/v1/papers 返回空列表；"
            "本 harness 不播种数据，请先在业务流程中导入试卷）"
        )
    return papers[0]


def token_request_summary(method: str, path: str, status: int | None, room: str | None) -> dict:
    """token 请求的净化事实：只保留 method/path/status/room（白名单键）。

    请求体只解析出 room 后即丢弃；响应体（房间 JWT）从不进入本函数
    之外的任何保留面。
    """
    return {"kind": "token", "method": method, "path": path, "status": status, "room": room}


def transcribe_request_facts(
    method: str, path: str, status: int | None, content_type: str | None, body: bytes
) -> dict:
    """transcribe 请求的净化事实（白名单键 + 内存断言布尔）。

    multipart 体内顶层 content-type 只是 multipart/form-data——audio/wav
    part 头与 answer.wav 文件名在体内：以内存子串断言（wav_part/
    answer_wav_filename 布尔入报告），原始音频字节绝不返回/保留。
    """
    return {
        "kind": "transcribe",
        "method": method,
        "path": path,
        "status": status,
        "content_type": content_type,
        "byte_count": len(body),
        "multipart": bool(content_type) and "multipart/form-data" in (content_type or ""),
        "wav_part": b"audio/wav" in body,
        "answer_wav_filename": b'filename="answer.wav"' in body,
    }


def verify_network_facts(
    token_entries: list[dict], transcribe_entries: list[dict], session_id: str
) -> dict:
    """硬断言网络事实（fail-closed：必需元数据缺失即失败，不猜不补）。

    - token 恰一次、HTTP 200、room 恰为 voice-<session_id>（M14-252
      单控制器复用语义——重试/多房间即失败）；
    - transcribe 恰一次、HTTP 200、multipart 顶层类型 + 体内
      audio/wav part 与 answer.wav 文件名 + 字节数 > 0。
    """
    if len(token_entries) != 1:
        raise AssertionError(
            f"FAIL: token 请求恰一次（实测 {len(token_entries)} 次——"
            "M14-252 语义为单连接单轨复用，重试即异常）"
        )
    token = token_entries[0]
    want_room = expected_token_room(session_id)
    if token.get("status") != 200:
        raise AssertionError(f"FAIL: token 请求 HTTP {token.get('status')}（期待 200）")
    if token.get("room") != want_room:
        raise AssertionError(
            f"FAIL: token 请求 room={token.get('room')!r}（期待 {want_room!r}，"
            "房间必须绑定权威 session_id）"
        )
    if len(transcribe_entries) != 1:
        raise AssertionError(
            f"FAIL: transcribe 请求恰一次（实测 {len(transcribe_entries)} 次）"
        )
    tr = transcribe_entries[0]
    if tr.get("status") != 200:
        raise AssertionError(f"FAIL: transcribe 请求 HTTP {tr.get('status')}（期待 200）")
    if not tr.get("multipart"):
        raise AssertionError(
            f"FAIL: transcribe 非 multipart（content-type={tr.get('content_type')!r}）"
        )
    if not tr.get("byte_count") or tr["byte_count"] <= 0:
        raise AssertionError("FAIL: transcribe 请求体为空（音频字节缺失）")
    if not tr.get("wav_part") or not tr.get("answer_wav_filename"):
        raise AssertionError(
            "FAIL: transcribe multipart 缺 audio/wav part 或 answer.wav 文件名"
        )
    return {
        "token": {"count": 1, "room": token.get("room"), "expected_room": want_room, "status": 200},
        "transcribe": {
            "count": 1,
            "status": 200,
            "byte_count": tr["byte_count"],
            "content_type": tr.get("content_type"),
            "multipart": True,
            "wav_part": True,
            "answer_wav_filename": True,
        },
    }


def find_jwt(text: str) -> re.Match[str] | None:
    """文本中定位 JWT 三段形态（DOM/报告自检用；None = 干净）。"""
    return JWT_RE.search(text)


def report_has_jwt(value: object) -> bool:
    """递归检测报告对象序列化后是否含 JWT 形态（落盘前最后防线）。"""
    try:
        serialized = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return True  # 不可序列化的报告本身就是异常，保守视为不干净
    return JWT_RE.search(serialized) is not None


def sanitize_tree(value: object) -> object:
    """递归脱敏（str 全部过 JWT 正则替换后返回新对象，绝不改写原值）。"""
    if isinstance(value, str):
        return JWT_RE.sub("[REDACTED-JWT]", value)
    if isinstance(value, dict):
        return {key: sanitize_tree(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_tree(item) for item in value]
    return value


def finalize_results(raw: dict) -> tuple[dict, int]:
    """落盘前防线（顺序即语义）：**先对未脱敏报告做 JWT 自检**——命中
    即改判 failed + 记非 secret 自检标记；**然后**才递归脱敏返回可
    落盘树与退出码。检测必须先于脱敏：若先脱敏再检测，真实泄漏会
    被替换成占位符后静默通过（supervisor 修正 2 的缺陷根因），自检
    形同虚设。返回 (可落盘报告, 退出码)。
    """
    exit_code = 0 if raw.get("verdict") == "passed" else 1
    if report_has_jwt(raw):
        raw = {**raw, "verdict": "failed", "jwt_leak_self_check": "REDACTED-BY-SELF-CHECK"}
        exit_code = 1
    return sanitize_tree(raw), exit_code  # type: ignore[arg-type, return-value]


# --- 业务前置（只读 preflight + login-only 认证 + 契约探测） ---


def login_verification_learner() -> str:
    """既有验收用户 login-only 登录（零播种）→ 内存 access token。

    只调业务登录端点（与浏览器 UI 同一端点）；本 harness 绝不注册/
    播种用户（M14-254 边界：验收用户由既往合法验收建立，此处只
    认证）。失败（用户不存在/凭据失效/服务异常）→ ENV-BLOCKED
    fail-closed；token 只进内存供后续请求头使用，绝不落日志/报告。
    """
    status, payload = VWLC.api_call(
        "POST", "/api/v1/auth/login", body={"username": LEARNER, "password": PASSWORD}
    )
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if status != 200 or not token:
        raise SystemExit(
            f"ENV-BLOCKED: 验收用户登录失败（HTTP {status}，token 缺失——"
            "用户未注册或凭据失效；本 harness login-only 零播种，fail-closed）"
        )
    check("auth: 既有验收用户登录成功（login-only 零播种）", True)
    return str(token)


def choose_paper(api_token: str) -> dict:
    """GET /api/v1/papers（Bearer）→ 确定性选卷（select_paper 纯函数）。"""
    status, payload = VWLC.api_call(
        "GET", "/api/v1/papers", headers={"Authorization": f"Bearer {api_token}"}
    )
    papers = payload if isinstance(payload, list) else []
    if status != 200:
        raise SystemExit(f"ENV-BLOCKED: 试卷列表不可用（HTTP {status}，fail-closed）")
    return select_paper(papers, os.environ.get(PAPER_ID_ENV))


# --- 浏览器验收（真实 UI 路径 + 净化网络监听） ---


def run_browser(web_base: str, paper_id: str, shots: Path, loopback: bool) -> dict:
    from playwright.sync_api import sync_playwright

    token_entries: list[dict] = []
    transcribe_entries: list[dict] = []
    console_errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=[*VWLC.chromium_launch_args(loopback), AUTOPLAY_FLAG],
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            permissions=["microphone"],
        )
        page = context.new_page()
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: console_errors.append(str(err)))

        # 登录（真实 UI 表单 → HttpOnly cookie）
        page.goto(f"{web_base}/login")
        page.fill("#username", LEARNER)
        page.fill("#password", PASSWORD)
        page.locator("form").get_by_role("button", name="登录").click()
        page.wait_for_url(f"{web_base}/", timeout=15_000)
        check("web: UI 登录成功", True)

        # 分窗（R1 模式）：登录窗预期 auth 噪声单独记录，不掩蔽后续错误
        login_console_errors = list(console_errors)
        console_errors.clear()

        # 网络监听先于语音链的一切请求安装（requestfinished：status 可得）
        def on_request_finished(request) -> None:
            method = request.method
            path = urllib.parse.urlparse(request.url).path
            if not path.endswith((TOKEN_PATH_SUFFIX, TRANSCRIBE_PATH_SUFFIX)):
                return
            response = request.response()
            status = response.status if response is not None else None
            if path.endswith(TOKEN_PATH_SUFFIX) and method == "POST":
                room = None
                try:
                    body = json.loads(request.post_data or "")
                    room = body.get("room") if isinstance(body, dict) else None
                except (json.JSONDecodeError, TypeError):
                    room = None  # 解析失败 → room=None → 硬断言 fail-closed
                token_entries.append(token_request_summary(method, path, status, room))
            elif path.endswith(TRANSCRIBE_PATH_SUFFIX) and method == "POST":
                raw = request.post_data_buffer or b""
                transcribe_entries.append(
                    transcribe_request_facts(
                        method,
                        path,
                        status,
                        request.headers.get("content-type"),
                        raw,
                    )
                )

        page.on("requestfinished", on_request_finished)

        # 打开 VoiceStudio：权威 session_id 就绪 + 语音作答按钮出现
        page.goto(f"{web_base}/voice/{paper_id}")
        page.wait_for_function(
            """() => {
                const root = document.querySelector('[data-voice-studio]');
                if (!root) return false;
                if ((root.dataset.voiceSessionId || '') === '') return false;
                return [...root.querySelectorAll('button')].some(
                    (b) => b.getAttribute('aria-label') === '语音作答'
                );
            }""",
            timeout=WAIT_SESSION_READY_MS,
        )
        session_id = page.locator(VOICE_STUDIO_SELECTOR).get_attribute("data-voice-session-id") or ""
        check("ui: VoiceStudio 权威会话就绪", True, f"session_id 长度 {len(session_id)}")
        page.screenshot(path=str(shots / "01-initial-session.png"), full_page=True)

        # 点击语音作答 → recording 只能在 token→connect→mic→publish 全成功后到达
        page.get_by_role("button", name="语音作答").click()
        page.wait_for_function(
            """() => document.querySelector('[data-voice-studio]')?.dataset.micPhase === 'recording'""",
            timeout=WAIT_RECORDING_MS,
        )
        check("ui: mic phase 到达 recording（LiveKit 连接+发布链成功）", True)
        page.screenshot(path=str(shots / "02-recording.png"), full_page=True)

        # 保持 ~1.5s 产生非空音频，再点「停止并提交」
        page.wait_for_timeout(1500)
        page.get_by_role("button", name="停止录音并提交识别").click()
        page.wait_for_function(
            """() => document.querySelector('[data-voice-studio]')?.dataset.micPhase === 'idle'""",
            timeout=WAIT_IDLE_AFTER_STOP_MS,
        )
        check("ui: utterance 停止后 mic phase 回 idle（转写+提交链完成）", True)
        page.screenshot(path=str(shots / "03-post-transcribe.png"), full_page=True)

        final_phase = page.locator(VOICE_STUDIO_SELECTOR).get_attribute("data-mic-phase") or ""
        check("dom: 最终 mic phase 为契约值 idle", final_phase == "idle" and phase_valid(final_phase), final_phase)
        dom = page.content()
        check("hard: DOM 不含 JWT 形态", find_jwt(dom) is None)

        # 网络事实硬断言（fail-closed：缺元数据即失败）
        facts = verify_network_facts(token_entries, transcribe_entries, session_id)
        check("hard: token 恰一次且 room=voice-<session_id>", True, json.dumps(facts["token"]))
        check(
            "hard: transcribe 恰一次（200，multipart audio/wav answer.wav 非空）",
            True,
            json.dumps(facts["transcribe"]),
        )
        check(
            "hard: 验证窗 console/pageerror 零错误",
            len(console_errors) == 0,
            json.dumps([VWLC.sanitize(e)[:300] for e in console_errors[:10]]),
        )
        browser.close()

    return {
        "voice_session_id": session_id,
        "final_phase": final_phase,
        "network": facts,
        "console_error_count": len(console_errors),
        "console_errors_sanitized": [VWLC.sanitize(e)[:300] for e in console_errors[:20]],
        "login_console_error_count": len(login_console_errors),
        "login_console_errors_sanitized": [VWLC.sanitize(e)[:300] for e in login_console_errors[:20]],
        "screenshots": ["01-initial-session.png", "02-recording.png", "03-post-transcribe.png"],
    }


def main() -> int:
    out_dir = Path(os.environ.get("AIOS_OUT", REPO_ROOT / ".verify" / "m14-254-voice-livekit-real-browser"))
    shots = out_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    livekit_probe: dict = {}
    paper: dict = {}
    try:
        loopback = VWLC.browser_loopback_enabled()  # 严格开关（fail-closed 入口）
        VWLC.preflight()
        api_token = login_verification_learner()  # login-only 零播种（修正 1）
        livekit_probe = VWLC.resolve_livekit_health(api_token)
        paper = choose_paper(api_token)
        print(f"  paper: {paper.get('id')} / {paper.get('title')}")
    except SystemExit as cause:
        print(cause, file=sys.stderr)
        return 2

    port = int(os.environ.get("AIOS_WEB_PORT") or VWLC.free_port())
    if os.environ.get("AIOS_SKIP_BUILD") != "1":
        VWLC.build_web(out_dir)
    proc = None
    verdict = "failed"
    results: dict = {"verdict": verdict}
    try:
        proc = VWLC.start_web(port)
        results = run_browser(f"http://127.0.0.1:{port}", str(paper["id"]), shots, loopback=loopback)
        verdict = "passed"
    except AssertionError as cause:
        print(cause, file=sys.stderr)
        results = {"verdict": "failed", "error": VWLC.sanitize(str(cause))}
    except Exception as cause:  # noqa: BLE001 —— 验收工具必须兜底落盘后退出
        print(f"ERROR: {cause}", file=sys.stderr)
        results = {"verdict": "failed", "error": VWLC.sanitize(f"{type(cause).__name__}: {cause}")}
    finally:
        if proc is not None:
            VWLC.stop_process_tree(proc)  # 精确 PID 树回收（绝不按端口/进程名）
    results["verdict"] = verdict
    results["paper"] = {
        "id": paper.get("id"),
        "title": paper.get("title"),
        "selection": "env-override" if os.environ.get(PAPER_ID_ENV) else "first-available",
    }
    results["browser"] = {**VWLC.browser_report(loopback), "autoplay_flag": AUTOPLAY_FLAG}
    results["livekit_probe"] = livekit_probe
    results["checks_passed"] = CHECKS
    # 落盘前防线（修正 2）：先对未脱敏报告 JWT 自检（命中改判 failed +
    # 非 secret 标记），后递归脱敏——顺序保证真实泄漏不可能以 passed 落盘
    results, exit_code = finalize_results(results)
    (out_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"\nverdict={results['verdict']}  browser={results['browser']['mode']}"
        f"  ({len(CHECKS)} checks)  证据: {out_dir}"
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
