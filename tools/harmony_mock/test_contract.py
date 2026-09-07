"""M13-02 HarmonyOS mock 后端契约测试脚本。

在宿主机上启动 mock 服务器并验证（契约测试共 18 项）：
- 6 个 Home GET 端点返回 200 与关键字段
- POST/PUT/PATCH/DELETE 返回 METHOD_NOT_ALLOWED (404)
- HEAD/OPTIONS/FOO 等未支持 method 同样 404,不落入 501
- 未知 GET 路径返回 NOT_FOUND (404)
- audit 错误 query 返回 404

不依赖模拟器,纯 Python 标准库;可重复执行。
退出码:0=全通过,1=有失败。

用法:
    python tools/harmony_mock/test_contract.py [--port PORT]
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
]

# 应返回 404 的测试用例
ENDPOINTS_404 = [
    ("POST", "/health"),
    ("PUT", "/health"),
    ("PATCH", "/api/v1/version"),
    ("DELETE", "/api/v1/audit?limit=100"),
    ("GET", "/api/v1/nonexistent"),
    ("GET", "/api/v1/search/providers"),
    ("GET", "/api/v1/audit"),          # 缺 limit=100 → 404
    ("GET", "/api/v1/audit?limit=10"), # 非 100 → 404
    ("GET", "/api/v1/audit?foo=bar"),  # 多余参数 → 404
    ("HEAD", "/health"),               # 未支持 method → 404 (无 body)
    ("OPTIONS", "/health"),            # 未支持 method → 404
    ("FOO", "/health"),                # 任意未支持 method → 404,而非 501
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
    parser = argparse.ArgumentParser(description="M13-02 mock contract tests")
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
