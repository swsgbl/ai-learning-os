"""M12-04 Android 搜索冒烟 · 本机 loopback mock API（仅 127.0.0.1:8000）。

范围与边界：
- 只服务固定 JSON：GET /health、GET /api/v1/auth/status、GET /api/v1/system/privacy、
  GET /api/v1/papers、GET /api/v1/search/providers、POST /api/v1/search/plan、
  POST /api/v1/search/queries、GET /api/v1/search/queries/9001；其余一律 404。
- 不代理、不转发、不触网、不读任何真实 provider/生产服务/密钥；
- 查询词原样回显（echo），槽位/计划/结果/记录全部固定；
- 结果 URL 固定为长 ASCII：
  https://localhost/m12-04/smoke-source?trace=local&suite=android-360
- 每个请求追加一行日志到同目录 mock_requests.log（冒烟证据）。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

HOST = "127.0.0.1"
PORT = 8000
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mock_requests.log")

RESULT_URL = "https://localhost/m12-04/smoke-source?trace=local&suite=android-360"
CREATED_AT = "2026-09-07T00:00:00+00:00"

RESULTS = [
    {
        "title": "2024 清华大学 高等数学（甲）期末选择题 第 1 套",
        "url": RESULT_URL,
        "snippet": "固定 mock 结果 A：用于冒烟验证结果 URL 展示断行、打开入口与 ACTION_VIEW 浏览器接管，不代表真实检索内容。",
        "source": "mock-local-corpus",
        "provider": "local-corpus",
        "authority": "official",
        "rank_reason": "本地语料命中：年份+学校+学科+题型全部匹配",
    },
    {
        "title": "2024 高等数学 选择题汇编（m12-04 冒烟样例 B）",
        "url": RESULT_URL,
        "snippet": "固定 mock 结果 B：第二条结果证明列表渲染与去重展示由客户端原样投影，不重排不改写。",
        "source": "mock-local-corpus",
        "provider": "local-corpus",
        "authority": "community",
        "rank_reason": "本地语料命中：学科+题型匹配，年份次之",
    },
]

PROVIDERS = {
    "items": [
        {
            "name": "local-corpus",
            "kind": "local-corpus",
            "enabled": True,
            "unavailable_reason": None,
        },
        {
            "name": "cloud-web",
            "kind": "web",
            "enabled": False,
            "unavailable_reason": "未配置云端检索通道（mock 固定禁用，验证不可用原因如实展示）",
        },
    ]
}

SKIPPED = [{"provider": "cloud-web", "reason": "不可用：未配置云端检索通道（mock 固定禁用）"}]

SLOTS = {
    "subject": "高等数学",
    "school": "清华大学",
    "year": "2024",
    "course": None,
    "question_type": "选择题",
    "publicity": None,
}

_lock = Lock()


def _log(handler: BaseHTTPRequestHandler, body: bytes) -> None:
    line = json.dumps(
        {
            "t": datetime.now(timezone.utc).isoformat(),
            "method": handler.command,
            "path": handler.path,
            "body_len": len(body),
        },
        ensure_ascii=False,
    )
    with _lock:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ---------- 工具 ----------

    def _send_json(self, payload: dict, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def log_message(self, fmt: str, *args) -> None:  # 静默默认 stderr 日志
        pass

    # ---------- 路由 ----------

    def _route(self, body: bytes) -> tuple[int, dict | None]:
        path = self.path.split("?", 1)[0]
        method = self.command

        if method == "GET" and path == "/health":
            return 200, {"status": "ok", "service": "aios-mock-m12-04"}

        if method == "GET" and path == "/api/v1/auth/status":
            return 200, {"auth_enabled": False}

        if method == "GET" and path == "/api/v1/system/privacy":
            return 200, {
                "model_route": "loopback-mock",
                "voice_mode": "mock",
                "search_mode": "mock-fixed-json",
                "store_audio": False,
                "send_context_to_cloud": False,
            }

        if method == "GET" and path == "/api/v1/papers":
            return 200, []

        if method == "GET" and path == "/api/v1/search/providers":
            return 200, PROVIDERS

        if method == "POST" and path == "/api/v1/search/plan":
            query = ""
            try:
                query = json.loads(body.decode("utf-8") or "{}").get("query", "")
            except Exception:
                query = ""
            return 200, {
                "query": query,
                "slots": SLOTS,
                "plan": [
                    {
                        "provider": "local-corpus",
                        "query": query,
                        "enabled": True,
                        "unavailable_reason": None,
                    },
                    {
                        "provider": "cloud-web",
                        "query": query,
                        "enabled": False,
                        "unavailable_reason": "未配置云端检索通道（mock 固定禁用）",
                    },
                ],
            }

        if method == "POST" and path == "/api/v1/search/queries":
            query = ""
            try:
                query = json.loads(body.decode("utf-8") or "{}").get("query", "")
            except Exception:
                query = ""
            return 200, {
                "query_id": 9001,
                "query": query,
                "providers_requested": ["local-corpus", "cloud-web"],
                "results": RESULTS,
                "skipped": SKIPPED,
                "result_count": len(RESULTS),
                "duration_ms": 12,
            }

        if method == "GET" and path == "/api/v1/search/queries/9001":
            return 200, {
                "id": 9001,
                "query": "2024 tsinghua advanced math mcq",
                "providers_requested": ["local-corpus", "cloud-web"],
                "providers_skipped": SKIPPED,
                "result_count": len(RESULTS),
                "duration_ms": 12,
                "results": RESULTS,
                "created_at": CREATED_AT,
            }

        return 404, {"detail": "not found (m12-04 loopback mock)"}

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def _handle(self):
        body = self._read_body()
        _log(self, body)
        try:
            status, payload = self._route(body)
        except Exception as exc:  # noqa: BLE001
            payload = {"detail": f"mock error: {exc}"}
            status = 500
        self._send_json(payload, status)


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"m12-04 loopback mock listening on http://{HOST}:{PORT} (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
