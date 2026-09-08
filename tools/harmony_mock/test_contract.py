"""M13-02 / M13-05 / M13-06 / M13-07 HarmonyOS mock 后端契约测试脚本。

在宿主机上启动 mock 服务器并验证（契约测试共 42 项）：
- 6 个 Home GET 端点返回 200 与关键字段（M13-02）
- 1 个论文 GET 端点返回 200 与关键字段（M13-05 新增）
- 1 个检索 providers GET 端点返回 200 与固定形状（M13-06 新增：
  items 数组含启用项与禁用项,禁用项必须带非空 unavailable_reason）
- 1 个语音 providers GET 端点返回 200 且与 Android VoiceProvidersResponse
  逐字段精确相等（M13-07 新增:voice_mode=hybrid、ASR=fake 无回退、
  TTS 请求 cloud-openai-tts 回退 tone、隐私开关如实投影）
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


def main() -> int:
    parser = argparse.ArgumentParser(description="M13-02/M13-05/M13-06/M13-07 mock contract tests")
    parser.add_argument("--port", type=int, default=8765, help="listen port (default 8765)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="bind address")
    args = parser.parse_args()

    server = HarmonyMockServer(host=args.host, port=args.port)
    server.start()
    bind_url = build_base(args.host, args.port)
    print(f"Mock server started on {bind_url}", flush=True)
    time.sleep(0.3)

    passed = 0
    failed = 0
    results: list[str] = []

    print("\n--- 200 OK 端点 ---")
    for method, path, fields in ENDPOINTS_200:
        ok, msg = test_endpoint_200(method, path, fields, args.host, args.port)
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        results.append(f"  [{status}] {method} {path}: {msg}")
        print(results[-1])

    print("\n--- 404 端点 ---")
    for method, path in ENDPOINTS_404:
        ok, msg = test_endpoint_404(method, path, args.host, args.port)
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        results.append(f"  [{status}] {method} {path}: {msg}")
        print(results[-1])

    server.stop()
    total = passed + failed
    print(f"\nResults: {passed}/{total} passed, {failed} failed")
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
