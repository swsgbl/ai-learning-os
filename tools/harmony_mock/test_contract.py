"""M13-02 / M13-05 / M13-06 / M13-07 / M13-08 HarmonyOS mock 后端契约测试脚本。

在宿主机上启动 mock 服务器并验证（契约测试共 54 项）：
- 6 个 Home GET 端点返回 200 与关键字段（M13-02）
- 1 个论文 GET 端点返回 200 与关键字段（M13-05 新增）
- 1 个检索 providers GET 端点返回 200 与固定形状（M13-06 新增：
  items 数组含启用项与禁用项,禁用项必须带非空 unavailable_reason）
- 1 个语音 providers GET 端点返回 200 且与 Android VoiceProvidersResponse
  逐字段精确相等（M13-07 新增:voice_mode=hybrid、ASR=fake 无回退、
  TTS 请求 cloud-openai-tts 回退 tone、隐私开关如实投影）
- 1 个考试会话 GET 端点返回 200 且与 Android ExamSessionResponse
  逐字段精确相等（M13-08 新增:exam-m13-08-001/paper-001/mode=exam/
  status=active/固定未来起止时间/server_remaining_seconds=900/
  两道公共选择题/answers 仅含已保存作答/next_sequence=2）
- POST/PUT/PATCH/DELETE 返回 METHOD_NOT_ALLOWED (404)
- POST /api/v1/papers 返回 404（M13-05 新增负断言）
- POST /api/v1/search/providers 返回 404（M13-06 新增负断言:仅 GET）
- POST 与 GET /api/v1/search/plan、/api/v1/search/queries 返回 404
  （M13-06 新增负断言:M12-04 其余 search 端点对 Harmony 保持关闭,
  GET 形式同样不允许）
- GET /api/v1/search/queries/9001 返回 404（M13-06 新增负断言:
  其余 search 只读路径不在 Harmony 允许清单）
- GET /api/v1/search/providers?foo=bar 与 GET /api/v1/search/providers?
  （空查询串）返回 404（M13-06 新增负断言:
  允许清单 GET 端点拒绝一切非预期查询串,空查询串同样拒绝;
  唯一例外 audit,仍由共享契约精确校验 limit=100）
- POST /api/v1/voice/providers 返回 404（M13-07 新增负断言:仅 GET）
- 其余 voice 端点全部 404（M13-07 新增负断言:token/sessions/transcribe/
  synthesize/trace 对 Harmony 保持关闭,含 sessions 列表、sessions/{id}
  详情与 trace/summary 的 GET 形式）
- GET /api/v1/voice/providers?foo=bar 与 GET /api/v1/voice/providers?
  （空查询串）返回 404（M13-07 新增负断言:与其他允许清单端点一致,
  拒绝一切非预期查询串）
- POST /api/v1/exams/exam-m13-08-001 返回 404（M13-08 新增负断言:仅 GET）
- PUT /api/v1/exams/exam-m13-08-001/answers 与 POST
  /api/v1/exams/exam-m13-08-001/submit 返回 404（M13-08 新增负断言:
  考试写端点对 Harmony 保持关闭）
- GET /api/v1/exams/exam-m13-08-001/submission、/report 与
  /learning-events 返回 404（M13-08 新增负断言:提交/报告只读路径
  不在 Harmony 允许清单）
- GET /api/v1/exams/exam-unknown-999 与 GET /api/v1/exams 返回 404
  （M13-08 新增负断言:未知 exam_id 与集合路径不在允许清单）
- POST /api/v1/papers/paper-001/exams 返回 404（M13-08 新增负断言:
  开考写端点对 Harmony 保持关闭）
- GET /api/v1/exams/exam-m13-08-001?foo=bar 与
  GET /api/v1/exams/exam-m13-08-001?（空查询串）返回 404
  （M13-08 新增负断言:与其他允许清单端点一致,拒绝一切查询串）
- HEAD/OPTIONS/FOO 等未支持 method 同样 404,不落入 501
- 未知 GET 路径返回 NOT_FOUND (404)
- audit 错误 query 返回 404
- GET /api/v1/audit?limit=100&foo=bar 与 GET /api/v1/audit?limit=100& 返回 404
  （audit 整串须精确为 ?limit=100,任何多余参数或尾随 & 均拒绝）

不依赖模拟器,纯 Python 标准库;可重复执行。
退出码:0=全通过,1=有失败。

用法:
    python tools/harmony_mock/test_contract.py [--port PORT] [--host HOST]
"""
from __future__ import annotations

import argparse
import http.client
import json
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, ".")
from tools.harmony_mock import server as mock_server
from tools.harmony_mock.server import HarmonyMockServer


def build_base(host: str, port: int) -> str:
    return f"http://{host}:{port}"


# 六个 M13-01 Home GET 端点及关键字段断言
ENDPOINTS_200 = [
    ("GET", "/health", {"status": "ok"}),
    ("GET", "/api/v1/auth/status", {"auth_enabled": False}),
    ("GET", "/api/v1/system/privacy", {"model_route": "local"}),
    ("GET", "/api/v1/version", {"version": "0.12.5-mock"}),
    ("GET", "/api/v1/system/ops-snapshot", {"papers_total": 34}),
    ("GET", "/api/v1/audit?limit=100", None),  # array 响应，仅校验 200
    # M13-05 论文端点：顶层数组，校验首条关键字段
    (
        "GET",
        "/api/v1/papers",
        {
            "id": "paper-001",
            "title": "Attention Is All You Need",
            "subtitle": "Vaswani et al., NeurIPS 2017",
            "source": "NeurIPS",
            "subject": "Machine Learning",
            "difficulty": "medium",
            "duration_minutes": 45,
        },
    ),
    # M13-06 检索 providers 端点：{"items": [...]},校验启用项关键字段
    # (禁用项 cloud-web 的 enabled=False + 非空 unavailable_reason
    #  在 test_endpoint_200 的 providers 分支专项校验)
    (
        "GET",
        "/api/v1/search/providers",
        {
            "name": "local-corpus",
            "kind": "local-corpus",
            "enabled": True,
            "unavailable_reason": None,
        },
    ),
    # M13-07 语音 providers 端点：确定性快照,须与 Android
    # VoiceProvidersResponse 逐字段精确相等（不多不少）;exact 分支
    # 在 test_endpoint_200 的 voice providers 路径专项校验
    (
        "GET",
        "/api/v1/voice/providers",
        {
            "voice_mode": "hybrid",
            "asr": {"requested": None, "provider": "fake", "fallback": False},
            "tts": {
                "requested": "cloud-openai-tts",
                "provider": "tone",
                "fallback": True,
            },
            "privacy_store_audio": False,
            "privacy_send_context_to_cloud": True,
        },
    ),
    # M13-08 考试会话端点：确定性只读快照,须与 Android
    # ExamSessionResponse 逐字段精确相等（不多不少）;exact 分支
    # 在 test_endpoint_200 的 exam session 路径专项校验
    (
        "GET",
        "/api/v1/exams/exam-m13-08-001",
        {
            "exam_id": "exam-m13-08-001",
            "paper_id": "paper-001",
            "paper_title": "Attention Is All You Need",
            "mode": "exam",
            "status": "active",
            "server_started_at": "2027-01-01T00:00:00+00:00",
            "server_end_at": "2027-01-01T00:45:00+00:00",
            "server_remaining_seconds": 900,
            "questions": [
                {
                    "id": "q-m13-08-001",
                    "type": "mcq",
                    "stem": "In the Transformer architecture, the attention mechanism primarily replaces which component of prior sequence transduction models?",
                    "options": [
                        {"key": "A", "text": "Recurrent layers"},
                        {"key": "B", "text": "Convolutional layers"},
                        {"key": "C", "text": "Pooling layers"},
                        {"key": "D", "text": "Normalization layers"},
                    ],
                },
                {
                    "id": "q-m13-08-002",
                    "type": "mcq",
                    "stem": "Which position-encoding scheme does the original paper use so that the model can extrapolate to sequence lengths longer than any seen during training?",
                    "options": [
                        {"key": "A", "text": "Learned absolute embeddings"},
                        {"key": "B", "text": "Sinusoidal functions"},
                        {"key": "C", "text": "Relative offsets only"},
                        {"key": "D", "text": "Random projections"},
                    ],
                },
            ],
            "answers": {"q-m13-08-001": "A"},
            "next_sequence": 2,
        },
    ),
]

# 应返回 404 的测试用例（含 M13-05 / M13-06 / M13-07 负断言）
ENDPOINTS_404 = [
    ("POST", "/health"),
    ("PUT", "/health"),
    ("PATCH", "/api/v1/version"),
    ("DELETE", "/api/v1/audit?limit=100"),
    ("POST", "/api/v1/papers"),  # M13-05 仅允许 GET
    ("GET", "/api/v1/nonexistent"),
    ("GET", "/api/v1/audit"),          # 缺 limit=100 → 404
    ("GET", "/api/v1/audit?limit=10"), # 非 100 → 404
    ("GET", "/api/v1/audit?foo=bar"),  # 多余参数 → 404
    ("GET", "/api/v1/audit?limit=100&foo=bar"),  # limit=100 之外多余参数 → 404
    ("GET", "/api/v1/audit?limit=100&"),         # 尾随 & → 404
    ("HEAD", "/health"),               # 未支持 method → 404 (无 body)
    ("OPTIONS", "/health"),            # 未支持 method → 404
    ("FOO", "/health"),                # 任意未支持 method → 404,而非 501
    ("POST", "/api/v1/search/providers"),  # M13-06 仅允许 GET
    ("POST", "/api/v1/search/plan"),       # M13-06: plan POST 对 Harmony 关闭
    ("POST", "/api/v1/search/queries"),    # M13-06: queries POST 对 Harmony 关闭
    ("GET", "/api/v1/search/queries/9001"),  # M13-06: 其余 search 路径不在允许清单
    ("GET", "/api/v1/search/providers?foo=bar"),  # M13-06: 允许清单端点拒绝非预期查询串
    ("GET", "/api/v1/search/plan"),       # M13-06: plan GET 同样对 Harmony 关闭
    ("GET", "/api/v1/search/queries"),    # M13-06: queries GET 同样对 Harmony 关闭
    ("GET", "/api/v1/search/providers?"),  # M13-06: 空查询串同样拒绝 (fail-closed)
    # M13-07 负断言:providers 仅 GET,其余 voice 端点对 Harmony 全部关闭
    ("POST", "/api/v1/voice/providers"),        # M13-07: 仅允许 GET
    ("GET", "/api/v1/voice/providers?foo=bar"),  # M13-07: 拒绝非预期查询串
    ("GET", "/api/v1/voice/providers?"),         # M13-07: 空查询串同样拒绝 (fail-closed)
    ("POST", "/api/v1/voice/token"),             # M13-07: token 写端点关闭
    ("POST", "/api/v1/voice/sessions"),          # M13-07: session 写端点关闭
    ("GET", "/api/v1/voice/sessions"),           # M13-07: sessions 列表 GET 同样关闭
    ("GET", "/api/v1/voice/sessions/vs-mock-001"),  # M13-07: session 详情 GET 关闭
    ("POST", "/api/v1/voice/transcribe"),        # M13-07: 转写端点关闭
    ("POST", "/api/v1/voice/synthesize"),        # M13-07: 合成端点关闭
    ("POST", "/api/v1/voice/trace"),             # M13-07: trace 写端点关闭
    ("GET", "/api/v1/voice/trace/summary"),      # M13-07: trace GET 形式同样关闭
    # M13-08 负断言:session 仅该 exam_id 的 GET,考试域其余路径/方法全部关闭
    ("POST", "/api/v1/exams/exam-m13-08-001"),        # M13-08: 仅允许 GET
    ("PUT", "/api/v1/exams/exam-m13-08-001/answers"),  # M13-08: 写答案端点关闭
    ("POST", "/api/v1/exams/exam-m13-08-001/submit"),  # M13-08: 提交端点关闭
    ("GET", "/api/v1/exams/exam-m13-08-001/submission"),  # M13-08: 提交结果只读路径关闭
    ("GET", "/api/v1/exams/exam-m13-08-001/report"),      # M13-08: 报告只读路径关闭
    ("GET", "/api/v1/exams/exam-m13-08-001/learning-events"),  # M13-08: 学习事件路径关闭
    ("GET", "/api/v1/exams/exam-unknown-999"),     # M13-08: 未知 exam_id 不在允许清单
    ("GET", "/api/v1/exams"),                      # M13-08: 集合路径不在允许清单
    ("POST", "/api/v1/papers/paper-001/exams"),    # M13-08: 开考写端点对 Harmony 关闭
    ("GET", "/api/v1/exams/exam-m13-08-001?foo=bar"),  # M13-08: 拒绝一切查询串
    ("GET", "/api/v1/exams/exam-m13-08-001?"),         # M13-08: 空查询串同样拒绝 (fail-closed)
]


def fetch(method: str, path: str, host: str, port: int) -> tuple[int, str]:
    url = build_base(host, port) + path
    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body
    except urllib.error.URLError as e:
        return -1, f"Connection failed: {e.reason}"
    except (OSError, ValueError, http.client.HTTPException) as e:
        return -1, f"Error: {e}"


def test_endpoint_200(
    method: str, path: str, expected_fields: dict | None, host: str, port: int
) -> tuple[bool, str]:
    status, body = fetch(method, path, host, port)
    if status != 200:
        return False, f"HTTP {status}"

    if expected_fields is None:
        # audit 期望数组
        try:
            data = json.loads(body)
            if isinstance(data, list) and len(data) > 0:
                return True, f"200 OK ({len(data)} entries)"
            return False, f"Expected non-empty array, got: {body[:100]}"
        except json.JSONDecodeError as e:
            return False, f"JSON parse error: {e}"

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        return False, f"JSON parse error: {e}"

    # papers 端点：顶层数组，检查首条记录
    if path == "/api/v1/papers" and isinstance(data, list) and len(data) > 0:
        data = data[0]
    # M13-06 providers 端点：{"items": [...]},先按稳定 name/kind 定位禁用项
    # (cloud-web/web)校验其形状,再检查首条(启用项)
    elif path == "/api/v1/search/providers":
        items = data.get("items") if isinstance(data, dict) else None
        if not (isinstance(items, list) and len(items) >= 2):
            return False, f"Expected object with items array of >=2 providers, got: {body[:100]}"
        disabled = next(
            (
                p
                for p in items
                if isinstance(p, dict)
                and p.get("name") == "cloud-web"
                and p.get("kind") == "web"
            ),
            None,
        )
        if disabled is None or not (
            disabled.get("enabled") is False
            and isinstance(disabled.get("unavailable_reason"), str)
            and disabled["unavailable_reason"]
        ):
            return False, (
                f"Disabled provider must carry enabled=false "
                f"and non-empty unavailable_reason: {disabled}"
            )
        if not isinstance(items[0], dict):
            return False, f"Provider items[0] must be an object: {items[0]!r}"
        data = items[0]
    # M13-07 voice providers 端点：整包精确相等（字段不多不少,
    # 含嵌套 asr/tts 与 requested 的 null/字符串形态）
    elif path == "/api/v1/voice/providers":
        if not isinstance(data, dict) or data != expected_fields:
            return False, f"Expected exact voice providers fixture, got: {body[:200]}"
        return True, "200 OK (exact VoiceProvidersResponse fixture match)"
    # M13-08 exam session 端点：整包精确相等（字段不多不少,含嵌套
    # questions/options、answers 已作答投影与 next_sequence 权威序号）
    elif path == "/api/v1/exams/exam-m13-08-001":
        if not isinstance(data, dict) or data != expected_fields:
            return False, f"Expected exact exam session fixture, got: {body[:200]}"
        return True, "200 OK (exact ExamSessionResponse fixture match)"
    elif not isinstance(data, dict):
        return False, f"Expected object/array, got {type(data).__name__}: {body[:100]}"

    for key, val in expected_fields.items():
        if key not in data:
            return False, f"Missing field '{key}'"
        if data[key] != val:
            return False, f"Field '{key}' mismatch: expected {val!r}, got {data[key]!r}"

    return True, f"200 OK ({body[:80]}...)"


def test_endpoint_404(method: str, path: str, host: str, port: int) -> tuple[bool, str]:
    status, _body = fetch(method, path, host, port)
    if status == 404:
        if method == "HEAD" and _body != "":
            return False, "HEAD 404 must not include a body"
        return True, "404 OK"
    return False, f"Expected 404, got HTTP {status}"


def fetch_json(
    method: str,
    path: str,
    host: str,
    port: int,
    body: bytes | None = None,
    headers: dict | None = None,
) -> tuple[int, str]:
    """带 body/header 的 HTTP 请求;连接层错误与 fetch() 同义(-1)。"""
    url = build_base(host, port) + path
    req = urllib.request.Request(url, data=body, method=method)
    for key, val in (headers or {}).items():
        req.add_header(key, val)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return -1, f"Connection failed: {e.reason}"
    except (OSError, ValueError, http.client.HTTPException) as e:
        return -1, f"Error: {e}"


class _Suite:
    """计数器:逐项打印 PASS/FAIL,绝不打印凭据或 token 值。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.passed = 0
        self.failed = 0

    def check(self, label: str, ok: bool, msg: str) -> None:
        status = "PASS" if ok else "FAIL"
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        print(f"  [{status}] {label}: {msg}")

    @property
    def total(self) -> int:
        return self.passed + self.failed


def _login(host: str, port: int) -> str | None:
    """执行一次正确登录;契约不符(空/带空白/缺字段 token)返回 None。

    凭据取自 server.AUTH_STATE(合成 mock 值),本函数与其调用方
    均不打印凭据或 token 值。
    """
    payload = json.dumps({
        "username": mock_server.AUTH_STATE["login_user"],
        "password": mock_server.AUTH_STATE["login_pass"],
    }).encode("utf-8")
    status, body = fetch_json(
        "POST", "/api/v1/auth/login", host, port,
        body=payload, headers={"Content-Type": "application/json"},
    )
    if status != 200:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    token = data.get("access_token")
    if not (isinstance(token, str) and token and token == token.strip()):
        return None
    if data.get("token_type") != "bearer":
        return None
    return token


def run_auth_off_suite(suite: _Suite, host: str, port: int) -> None:
    """M14-89 auth-off:status 如实 false,login/me 关闭,既有端点不受影响。"""
    print("\n--- M14-89 auth-off 契约 ---")
    ok, msg = test_endpoint_200(
        "GET", "/api/v1/auth/status", {"auth_enabled": False}, host, port
    )
    suite.check("GET /api/v1/auth/status (auth off)", ok, msg)

    creds = json.dumps({
        "username": mock_server.AUTH_STATE["login_user"],
        "password": mock_server.AUTH_STATE["login_pass"],
    }).encode("utf-8")
    status, _ = fetch_json(
        "POST", "/api/v1/auth/login", host, port,
        body=creds, headers={"Content-Type": "application/json"},
    )
    suite.check("POST /api/v1/auth/login (auth off)", status == 404, f"HTTP {status}")

    status, _ = fetch_json(
        "GET", "/api/v1/auth/me", host, port,
        headers={"Authorization": "Bearer whatever"},
    )
    suite.check("GET /api/v1/auth/me (auth off)", status == 404, f"HTTP {status}")

    ok, msg = test_endpoint_200("GET", "/health", {"status": "ok"}, host, port)
    suite.check("GET /health (ungated, auth off)", ok, msg)

    status, body = fetch_json("GET", "/api/v1/papers", host, port)
    suite.check(
        "GET /api/v1/papers (protected path, auth off)",
        status == 200 and body.lstrip().startswith("["),
        f"HTTP {status}",
    )


def run_auth_on_suite(suite: _Suite, host: str, port: int) -> None:
    """M14-89 auth-on:门禁 401、登录契约、me 投影与负断言。"""
    print("\n--- M14-89 auth-on 契约 ---")
    ok, msg = test_endpoint_200(
        "GET", "/api/v1/auth/status", {"auth_enabled": True}, host, port
    )
    suite.check("GET /api/v1/auth/status (auth on)", ok, msg)

    # 受保护 GET 无 bearer 一律 401(fail-closed)
    for path in (
        "/api/v1/system/privacy",
        "/api/v1/papers",
        "/api/v1/audit?limit=100",
        "/api/v1/search/providers",
        "/api/v1/exams/exam-m13-08-001",
    ):
        status, _ = fetch_json("GET", path, host, port)
        suite.check(f"GET {path} without bearer", status == 401, f"HTTP {status}")

    # 错误凭据 401(错误密码 / 错误用户名)
    for label, user, password in (
        ("wrong password", mock_server.AUTH_STATE["login_user"], "definitely-wrong-pass"),
        ("wrong username", "definitely-wrong-user", mock_server.AUTH_STATE["login_pass"]),
    ):
        payload = json.dumps({"username": user, "password": password}).encode("utf-8")
        status, _ = fetch_json(
            "POST", "/api/v1/auth/login", host, port,
            body=payload, headers={"Content-Type": "application/json"},
        )
        suite.check(f"POST /api/v1/auth/login ({label})", status == 401, f"HTTP {status}")

    # 畸形/缺失字段载荷 422(校验错误,不是认证失败)
    for label, raw in (
        ("missing password", json.dumps({"username": mock_server.AUTH_STATE["login_user"]})),
        ("missing username", json.dumps({"password": mock_server.AUTH_STATE["login_pass"]})),
        ("malformed json", "{not-json"),
        ("non-object payload", "[1,2,3]"),
    ):
        status, _ = fetch_json(
            "POST", "/api/v1/auth/login", host, port,
            body=raw.encode("utf-8"), headers={"Content-Type": "application/json"},
        )
        suite.check(f"POST /api/v1/auth/login ({label})", status == 422, f"HTTP {status}")

    # 成功登录契约:非空、无首尾空白 token + token_type=bearer(不打印值)
    token = _login(host, port)
    suite.check(
        "POST /api/v1/auth/login (success contract)",
        token is not None,
        "non-empty stripped access_token, token_type=bearer"
        if token is not None else "login contract violated",
    )

    # me:有效 bearer → 200 且与 MOCK_USER 整包精确一致
    status, body = fetch_json(
        "GET", "/api/v1/auth/me", host, port,
        headers={"Authorization": f"Bearer {token}"},
    )
    me_ok = status == 200
    detail = f"HTTP {status}"
    if me_ok:
        try:
            data = json.loads(body)
            me_ok = data == dict(mock_server.MOCK_USER)
            detail = "200 OK (exact MOCK_USER projection)" if me_ok else "projection mismatch"
        except json.JSONDecodeError:
            me_ok, detail = False, "JSON parse error"
    suite.check("GET /api/v1/auth/me with valid bearer", me_ok, detail)

    # me:无效 bearer → 401
    status, _ = fetch_json(
        "GET", "/api/v1/auth/me", host, port,
        headers={"Authorization": "Bearer not-the-mock-token"},
    )
    suite.check("GET /api/v1/auth/me with invalid bearer", status == 401, f"HTTP {status}")

    # me:任何查询串 → 404(fail-closed)
    status, _ = fetch_json(
        "GET", "/api/v1/auth/me?foo=bar", host, port,
        headers={"Authorization": f"Bearer {token}"},
    )
    suite.check("GET /api/v1/auth/me?foo=bar (query rejected)", status == 404, f"HTTP {status}")


def run_fault_suite(suite: _Suite, host: str, port: int) -> None:
    """M14-89 故障注入:客户端必须拒绝不可用 token;me_down 如实 401。"""
    print("\n--- M14-89 故障注入契约 ---")
    original = dict(mock_server.AUTH_STATE)
    try:
        for fault in ("blank_token", "whitespace_token", "malformed",
                      "wrong_token_type", "missing_token_type", "me_down"):
            mock_server.AUTH_STATE["fault"] = fault
            token = _login(host, port)
            if fault == "me_down":
                status, _ = fetch_json(
                    "GET", "/api/v1/auth/me", host, port,
                    headers={"Authorization": f"Bearer {token}"},
                )
                suite.check(
                    "fault=me_down: me 401 despite valid bearer",
                    status == 401, f"HTTP {status}",
                )
            else:
                suite.check(
                    f"fault={fault}: login contract rejected",
                    token is None,
                    "client refuses unusable token" if token is None else "unusable token accepted",
                )
    finally:
        mock_server.AUTH_STATE.clear()
        mock_server.AUTH_STATE.update(original)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="M13-02/05/06/07/08 + M14-89 mock contract tests"
    )
    parser.add_argument(
        "--port", type=int, default=0,
        help="listen port (0 = OS-assigned ephemeral loopback port, default)",
    )
    parser.add_argument("--host", type=str, default="127.0.0.1", help="bind address")
    args = parser.parse_args()

    m13 = _Suite("M13 read-only contract")
    m14 = _Suite("M14-89 auth contract")
    server: HarmonyMockServer | None = None
    original_state = dict(mock_server.AUTH_STATE)
    try:
        # 阶段一:auth off(模块默认)。--port 0 时由 OS 分配隔离回环端口。
        server = HarmonyMockServer(host=args.host, port=args.port).start()
        port = server.server.server_address[1] if args.port == 0 else args.port
        print(f"Mock server (auth=off) on {build_base(args.host, port)}", flush=True)
        time.sleep(0.3)

        print("\n--- 200 OK 端点 ---")
        for method, path, fields in ENDPOINTS_200:
            ok, msg = test_endpoint_200(method, path, fields, args.host, port)
            m13.check(f"{method} {path}", ok, msg)

        print("\n--- 404 端点 ---")
        for method, path in ENDPOINTS_404:
            ok, msg = test_endpoint_404(method, path, args.host, port)
            m13.check(f"{method} {path}", ok, msg)

        run_auth_off_suite(m14, args.host, port)
        server.stop()
        server = None

        # 阶段二:auth on(fault 默认空),独立回环端口。
        mock_server.AUTH_STATE["enabled"] = True
        server = HarmonyMockServer(host=args.host, port=0).start()
        auth_port = server.server.server_address[1]
        print(f"Mock server (auth=on) on {build_base(args.host, auth_port)}", flush=True)
        time.sleep(0.3)
        run_auth_on_suite(m14, args.host, auth_port)
        run_fault_suite(m14, args.host, auth_port)
    except OSError as exc:
        print(f"[FAIL] mock server bind/start error: {exc}")
        m14.failed += 1
    finally:
        # fail-closed 清理:无论如何停服并还原全局 auth 状态
        if server is not None:
            server.stop()
        mock_server.AUTH_STATE.clear()
        mock_server.AUTH_STATE.update(original_state)

    passed = m13.passed + m14.passed
    failed = m13.failed + m14.failed
    total = passed + failed
    print(f"\nM13 suite: {m13.passed}/{m13.total} passed, {m13.failed} failed")
    print(f"M14-89 auth suite: {m14.passed}/{m14.total} passed, {m14.failed} failed")
    print(f"Results: {passed}/{total} passed, {failed} failed")
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
