"""M10-02 Web 治理与工作台浏览器回归（真实 Chromium，Docker 栈）。

前置：compose 栈已启动（web/api 可达，AUTH_SECRET 已配置 = 认证开启）。
用法：
    python infra/smoke_web_governance.py
环境变量：
    AIOS_WEB_BASE      默认 http://127.0.0.1:3010（主机 3000 被占时用 AIOS_WEB_PORT=3010 起栈）
    AIOS_API_BASE      默认 http://127.0.0.1:8000
    AIOS_API_CONTAINER 默认 ai-learning-os-api-1（admin 提升走容器内 CLI，幂等）

覆盖（真实浏览器行为断言，非 DOM 快照）：
    A 未登录访问 /progress：停留 /progress、出现「请先登录」、受保护 API 请求数为 0
      （回归：认证未定时曾先发 3 个受保护请求 → 401 全局跳转 /login）
    B admin 在 /governance：HttpOnly cookie 生效、刷新仍登录、页面不保存 token；
      点击退出后 cookie 清空、用户徽章消失、登录入口出现、治理入口消失、
      路由离开治理页到 /login
      （回归：退出曾只 clearSession + router.refresh，pathname 不变不重探，徽章/入口残留）
    C 已登录 learner 的 /progress：seeded 今日任务（含分类）、薄弱概念、可练试卷入口
    D 角色入口差异：learner 无治理入口，admin 有

幂等：可重复运行；seed 侧（用户/试卷/考试/草稿）重复创建或复用已有数据。
退出码：全部通过 0，任何断言失败非 0。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid

WEB_BASE = os.environ.get("AIOS_WEB_BASE", "http://127.0.0.1:3010")
API_BASE = os.environ.get("AIOS_API_BASE", "http://127.0.0.1:8000")
API_CONTAINER = os.environ.get("AIOS_API_CONTAINER", "ai-learning-os-api-1")

LEARNER = "learner_m10"
ADMIN = "admin_m10"
PASSWORD = "password-123"
SOURCE_ID = "src_m10_02_browser"
MARKER_CONCEPT = "m10_concept_derivative"  # seed 卷绑定概念，薄弱概念断言用

PROTECTED_PATHS = ("/api/v1/student/daily-plan", "/api/v1/student/states", "/api/v1/papers")

PAPER = {
    "title": "M10-02 验证卷",
    "duration_seconds": 1800,
    "questions": [
        {
            "question": {
                "question_type": "mcq",
                "stem": "导数的基本定义中，f'(x) 等于哪个极限？",
                "options": ["lim h→0 [f(x+h)-f(x)]/h", "lim x→0 f(x)/x", "f(x+h)-f(x)", "[f(x+h)-f(x)]/h"],
                "answer": {"option_index": 0},
                "explanation": "导数是差商当 h 趋于 0 的极限。",
                "concept_ids": ["m10_concept_derivative", "m10_concept_limits"],
                "difficulty": 2,
            },
            "score": 5.0,
        },
        {
            "question": {
                "question_type": "mcq",
                "stem": "函数 f(x)=x^2 在 x=1 处的导数值是？",
                "options": ["1", "2", "0", "-1"],
                "answer": {"option_index": 1},
                "explanation": "f'(x)=2x，故 f'(1)=2。",
                "concept_ids": ["m10_concept_derivative"],
                "difficulty": 2,
            },
            "score": 5.0,
        },
        {
            "question": {
                "question_type": "mcq",
                "stem": "定积分的几何意义是曲边梯形的？",
                "options": ["周长", "面积（带符号）", "切线斜率", "极值"],
                "answer": {"option_index": 1},
                "explanation": "定积分表示曲边梯形面积的代数和。",
                "concept_ids": ["m10_concept_integral"],
                "difficulty": 1,
            },
            "score": 5.0,
        },
    ],
}

CHECKS: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAIL: {label} {detail}")
    CHECKS.append(label)
    print(f"  PASS {label}")


# --- API 侧 seed（幂等） ---


def call(method: str, path: str, token: str | None = None, body=None, raw=None, headers=None):
    req = urllib.request.Request(API_BASE + path, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if raw is not None:
        data = raw
    elif body is not None:
        data = json.dumps(body).encode()
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


def ensure_user(name: str) -> str:
    call("POST", "/api/v1/auth/register", body={"username": name, "password": PASSWORD})
    status, body = call("POST", "/api/v1/auth/login", body={"username": name, "password": PASSWORD})
    assert status == 200, (name, status, body)
    return body["access_token"]


def promote_admin() -> None:
    result = subprocess.run(
        ["docker", "exec", API_CONTAINER, "python", "-m", "app.ops.cli", "admin", "promote", ADMIN],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def upload_parsed(token: str) -> str:
    payload = json.dumps([{"question": "browserSeedMarker sinA", "answer": "a/sinA=b/sinB"}]).encode()
    boundary = uuid.uuid4().hex
    multipart = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="seed.json"\r\n'
        f"Content-Type: application/json\r\n\r\n"
    ).encode() + payload + (
        f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"source_id\"\r\n\r\n"
        f"{SOURCE_ID}\r\n--{boundary}--\r\n"
    ).encode()
    status, upload = call(
        "POST", "/api/v1/resources/upload", token=token, raw=multipart,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert status == 201, (status, upload)
    rid = upload["id"]
    status, _ = call("POST", f"/api/v1/resources/{rid}/parse", token=token)
    assert status == 200, status
    return rid


def seed() -> tuple[str, str]:
    learner_token = ensure_user(LEARNER)
    admin_token = ensure_user(ADMIN)
    promote_admin()
    check("seed: me 披露 admin", call("GET", "/api/v1/auth/me", token=admin_token)[1]["role"] == "admin")

    # learner 学习数据（重复跑会产生新考试，planner/states 投影保持非空）
    call("POST", "/api/v1/papers/import", token=learner_token, body=[PAPER])
    papers = call("GET", "/api/v1/papers", token=learner_token)[1]
    paper_id = next(p["id"] for p in papers if p["title"] == PAPER["title"])
    status, exam = call("POST", f"/api/v1/papers/{paper_id}/exams", token=learner_token, body={"mode": "exam"})
    assert status == 201, exam
    first = exam["questions"][0]
    call("PUT", f"/api/v1/exams/{exam['exam_id']}/answers", token=learner_token,
         body={"sequence": 1, "question_id": first["id"], "answer": first["options"][0]["key"]})
    status, _ = call("POST", f"/api/v1/exams/{exam['exam_id']}/submit", token=learner_token, body={})
    assert status == 200, status
    call("POST", "/api/v1/student/states/recompute", token=learner_token)
    plan = call("GET", "/api/v1/student/daily-plan", token=learner_token)[1]
    check("seed: planner 任务非空", plan["task_count"] > 0)
    states = call("GET", "/api/v1/student/states", token=learner_token)[1]
    check("seed: 学生状态含薄弱概念", MARKER_CONCEPT in states["weak_concepts"] or states["concept_count"] > 0)

    # 待审课程导入草稿（admin 建 OPEN_LICENSE source，learner 上传并建草稿）
    call("POST", "/api/v1/sources", token=admin_token, body={
        "id": SOURCE_ID, "name": SOURCE_ID, "source_type": "oer",
        "license_state": "OPEN_LICENSE", "trust_tier": "B", "authority_score": 5,
        "homepage": f"https://example.edu/{SOURCE_ID}",
    })
    rid = upload_parsed(learner_token)
    status, draft = call("POST", "/api/v1/courses/import-drafts", token=learner_token, body={"resource_id": rid})
    if status == 409:  # 该资源已有 pending 草稿（重跑）
        draft = next(
            d for d in call("GET", "/api/v1/courses/import-drafts?status=pending_review", token=learner_token)[1]
            if d["source_resource_id"] == rid
        )
    else:
        assert status == 201, (status, draft)
    return learner_token, admin_token


# --- 浏览器侧（真实 Chromium） ---


def login(page, username: str) -> None:
    page.goto(f"{WEB_BASE}/login")
    page.fill("#username", username)
    page.fill("#password", PASSWORD)
    page.locator("form").get_by_role("button", name="登录").click()
    page.wait_for_url(f"{WEB_BASE}/", timeout=15_000)


def scenario_a_anonymous_progress(context, shots: str) -> None:
    print("[A] 未登录 /progress：零受保护请求 + 登录空态")
    page = context.new_page()
    protected: list[str] = []
    page.on("request", lambda request: protected.append(request.url) if request.url.startswith(API_BASE) and any(p in request.url for p in PROTECTED_PATHS) else None)
    page.goto(f"{WEB_BASE}/progress")
    page.get_by_text("请先登录").wait_for(timeout=15_000)
    page.wait_for_timeout(1200)  # 给 probeAuth 留出「本不该发生」的请求窗口
    check("A1 停留在 /progress 未被跳转", "/progress" in page.url, page.url)
    check("A2 出现请先登录空态", page.get_by_text("请先登录").count() > 0)
    check("A3 受保护 API 请求数为 0", len(protected) == 0, str(protected))
    page.screenshot(path=f"{shots}/A-anonymous-progress.png", full_page=True)
    page.close()


def scenario_b_admin_logout(context, shots: str) -> None:
    print("[B] admin HttpOnly cookie 登录/刷新/退出")
    page = context.new_page()
    login(page, ADMIN)
    auth_cookie = next((cookie for cookie in context.cookies(API_BASE) if cookie["name"] == "aios_auth"), None)
    check("B0a 登录设置 HttpOnly auth cookie", bool(
        auth_cookie and auth_cookie["httpOnly"] and auth_cookie["sameSite"] in ("Lax", "lax")
    ), json.dumps(auth_cookie, ensure_ascii=False))
    check("B0b 页面 localStorage 无 access token", page.evaluate("localStorage.getItem('aios_token')") is None)
    page.reload()
    page.wait_for_timeout(1_000)
    check("B0c 刷新后仍保持登录", ADMIN in page.inner_text("header"))
    page.goto(f"{WEB_BASE}/governance")
    page.get_by_role("button", name="课程导入").wait_for(timeout=15_000)
    check("B0d 前置：admin 徽章与治理入口在位",
          ADMIN in page.inner_text("header") and "治理" in page.inner_text("header"))
    page.get_by_text("退出", exact=True).click()
    page.wait_for_url("**/login", timeout=15_000)
    page.wait_for_timeout(800)
    header = page.inner_text("header")
    check("B1 退出后离开治理页到 /login", page.url.endswith("/login"), page.url)
    check("B2 auth cookie 已清除", all(cookie["name"] != "aios_auth" for cookie in context.cookies(API_BASE)))
    check("B3 aios_token 仍为空", page.evaluate("localStorage.getItem('aios_token')") is None)
    check("B4 用户徽章消失", ADMIN not in header)
    check("B5 登录入口出现", "登录" in header)
    check("B6 治理入口消失", "治理" not in header)
    page.screenshot(path=f"{shots}/B-admin-logout.png", full_page=True)
    page.close()


def scenario_c_learner_workbench(context, shots: str) -> None:
    print("[C] learner /progress：seeded 任务/薄弱概念/试卷入口")
    page = context.new_page()
    login(page, LEARNER)
    page.goto(f"{WEB_BASE}/progress")
    page.get_by_text("新学", exact=True).first.wait_for(timeout=20_000)
    page.wait_for_timeout(1500)
    body = page.inner_text("main")
    check("C1 今日任务渲染（分类分组）", "今日任务" in body and "共 " in body)
    check("C2 薄弱概念来自学生模型", "薄弱概念" in body and MARKER_CONCEPT in body)
    check("C3 试卷入口（语音/考场）", "语音陪练" in body and "考场" in body)
    check("C4 learner 无治理入口", "治理" not in page.inner_text("header"))
    page.screenshot(path=f"{shots}/C-learner-workbench.png", full_page=True)
    page.close()


def scenario_d_role_entries(context) -> None:
    print("[D] 角色入口差异")
    learner_page = context.new_page()
    login(learner_page, LEARNER)
    learner_page.goto(f"{WEB_BASE}/")
    learner_page.wait_for_timeout(800)
    check("D1 learner 首页导航无治理入口", "治理" not in learner_page.inner_text("header"))
    learner_page.close()


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("需要 playwright（pip install playwright && playwright install chromium）", file=sys.stderr)
        return 2

    seed()
    shots = tempfile.mkdtemp(prefix="aios-smoke-web-")
    print(f"截图目录: {shots}")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        scenario_a_anonymous_progress(context, shots)
        scenario_b_admin_logout(context, shots)
        scenario_c_learner_workbench(context, shots)
        scenario_d_role_entries(context)
        browser.close()
    print(f"\nALL {len(CHECKS)} CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
