"""M12-05 Android 治理只读面验证 · 本机 loopback mock API（仅绑定 127.0.0.1:8000）。

范围与边界：
- 只服务固定 JSON GET：/health、/api/v1/auth/status、/api/v1/system/privacy、
  /api/v1/version、/api/v1/system/ops-snapshot、/api/v1/audit?limit=100
  （审计查询参数须精确为 limit=100）；其余路径 / 方法 / 查询参数一律 404。
- 只读：本 mock 不实现任何写端点（POST/PUT/PATCH/DELETE 一律 404），
  不代理、不转发、不触网、不读任何真实 provider / 生产服务 / 数据库 / 密钥。
- 治理面全部为明显 mock 值（版本 0.12.5-mock / Git / Alembic / 快照计数 / 审计条目），
  不代表真实环境。
- 审计条目内嵌 before/after 载荷，含唯一标记
  M12_05_BEFORE_SHOULD_NOT_RENDER / M12_05_AFTER_SHOULD_NOT_RENDER，
  用于验证客户端 UI 不渲染载荷（DTO 不建模 before/after，应被忽略）。
- 每个请求向同目录 mock_requests.log 追加一行 JSON 日志：
  仅记录 UTC 时间 / method / path / body_len；不记录 header 与 body 内容。
- 纯 Python 标准库；默认 stderr 静默。
"""
from __future__ import annotations

import json
import os
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

HOST = "127.0.0.1"
PORT = 8000
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mock_requests.log")

# ---------- 固定响应 ----------

HEALTH = {"status": "ok", "service": "aios-mock-m12-05"}

AUTH_STATUS = {"auth_enabled": False}

PRIVACY = {
    "model_route": "local",
    "voice_mode": "local",
    "search_mode": "local",
    "store_audio": False,
    "send_context_to_cloud": False,
}

VERSION = {
    "version": "0.12.5-mock",
    "git_commit": "m1205mockgit",
    "alembic_current": "m1205mockrev",
    "alembic_head": "m1205mockrev",
}

OPS_SNAPSHOT = {
    "generated_at": "2026-09-07T12:00:00+00:00",
    "database_backend": "postgresql",
    "users_by_role": {"admin": 2, "learner": 128},
    "papers_total": 34,
    "resources_by_parse_status": {"parsed": 40, "pending": 3},
    "parse_jobs_by_status": {"succeeded": 33, "queued": 2},
    "pending_review_drafts": {
        "course_import": 1,
        "course_generation": 2,
        "paper_question": 4,
        "variant_question": 5,
    },
    "voice_sessions_by_status": {"completed": 90, "failed": 2},
    "search_queries_total": 456,
    "audit_entries_total": 1024,
    "worker_running": True,
}

# 审计：两条。一条 actor 非空 mock 值；一条 actor_id/actor_username 显式 null（系统动作）。
# 两条的 before/after 均内嵌唯一标记，验证 UI 不渲染载荷。
AUDIT = [
    {
        "id": 1001,
        "actor_id": "u-mock-admin-001",
        "actor_username": "mock_admin",
        "action": "paper.publish",
        "target_type": "paper",
        "target_id": "paper-m1205",
        "request_id": "req-m1205-1001",
        "created_at": "2026-09-07T12:00:01+00:00",
        "before": {"note": "M12_05_BEFORE_SHOULD_NOT_RENDER", "payload": "mock-before-1001"},
        "after": {"note": "M12_05_AFTER_SHOULD_NOT_RENDER", "payload": "mock-after-1001"},
    },
    {
        "id": 1002,
        "actor_id": None,
        "actor_username": None,
        "action": "worker.tick",
        "target_type": "job",
        "target_id": "job-m1205",
        "request_id": "req-m1205-1002",
        "created_at": "2026-09-07T12:00:02+00:00",
        "before": {"note": "M12_05_BEFORE_SHOULD_NOT_RENDER", "payload": "mock-before-1002"},
        "after": {"note": "M12_05_AFTER_SHOULD_NOT_RENDER", "payload": "mock-after-1002"},
    },
]

_lock = Lock()


def _log(handler: BaseHTTPRequestHandler, body: bytes) -> None:
    # 仅记录 UTC 时间 / method / path / body_len；不记录 header 与 body 内容
    line = json.dumps(
        {
            "utc": datetime.now(timezone.utc).isoformat(),
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

    def _send_json(self, payload, status: int = 200) -> None:
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

    # ---------- 路由（只读 GET；其余一律 404） ----------

    def _route(self, body: bytes) -> tuple[int, object]:
        path, _, raw_query = self.path.partition("?")
        if self.command != "GET":
            return 404, {"detail": "mock 只读：仅固定 GET 端点"}
        if path == "/health":
            return 200, HEALTH
        if path == "/api/v1/auth/status":
            return 200, AUTH_STATUS
        if path == "/api/v1/system/privacy":
            return 200, PRIVACY
        if path == "/api/v1/version":
            return 200, VERSION
        if path == "/api/v1/system/ops-snapshot":
            return 200, OPS_SNAPSHOT
        if path == "/api/v1/audit":
            # 规范只固定 GET /api/v1/audit?limit=100；其余查询参数一律 404
            if urllib.parse.parse_qs(raw_query).get("limit") == ["100"]:
                return 200, AUDIT
            return 404, {"detail": "Not Found（审计仅支持 limit=100）"}
        return 404, {"detail": "Not Found（mock 固定端点之外）"}

    def _handle(self) -> None:
        body = self._read_body()
        status, payload = self._route(body)
        _log(self, body)
        self._send_json(payload, status)

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_DELETE = _handle


class Server(ThreadingHTTPServer):
    """stderr 静默：吞掉 handler 之外异常（如客户端断连）的默认 traceback。"""

    def handle_error(self, request, client_address) -> None:
        pass


def main() -> None:
    server = Server((HOST, PORT), Handler)
    print(f"mock listening on http://{HOST}:{PORT} (GET-only fixed endpoints)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
