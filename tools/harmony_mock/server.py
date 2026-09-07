"""HarmonyOS mock 后端服务器 — 仅用于 M13-02 验收测试。

通过 tools.android_smoke.mock_contract.ReadOnlyMockContract().handle 路由,
禁止手写各端点 JSON 字符串。只暴露 M13-01 Home 六个 GET 端点;
未知路径、非 GET 与未支持 method (HEAD/OPTIONS/其它) 均 404,不落入
BaseHTTPRequestHandler 的 501;audit 仅精确 limit=100。

仅使用 Python 标准库;不触网、不读密钥、不保存任何凭据。

用法:
    python tools/harmony_mock/server.py [--port PORT] [--host HOST]

默认 host 127.0.0.1 (仅本机测试);
--host 0.0.0.0 仅限本机模拟器验收 (不要在生产环境使用)。
打印绑定地址;不记录 header/body。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Self

# 复用 Android 冒烟共享契约,避免重复定义
sys.path.insert(0, ".")
from tools.android_smoke.mock_contract import (
    METHOD_NOT_ALLOWED,
    NOT_FOUND,
    ReadOnlyMockContract,
)

# 只允许这六个 M13-01 Home GET 端点
ALLOWED_GET_PATHS = frozenset({
    "/health",
    "/api/v1/auth/status",
    "/api/v1/system/privacy",
    "/api/v1/version",
    "/api/v1/system/ops-snapshot",
    "/api/v1/audit",
})


class _HarmonyMockHandler(BaseHTTPRequestHandler):
    """HTTP 处理器:固定契约 + 静默日志。"""

    protocol_version = "HTTP/1.1"
    _contract = ReadOnlyMockContract()

    def send_error(
        self, code: int, message: str | None = None, explain: str | None = None
    ) -> None:
        # 未支持 method (HEAD/OPTIONS/FOO...) 会触发父类 501:
        # 统一改为 404 METHOD_NOT_ALLOWED。仅拦截 501 且非 GET;
        # 400 (malformed request) 等真实错误仍交给父类。
        if code == 501 and self.command and self.command != "GET":
            self._respond(404, METHOD_NOT_ALLOWED)
            return
        super().send_error(code, message, explain)

    def _respond(self, status: int, payload: object | None) -> None:
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        else:
            body = b""
        # 每次响应后关闭连接,避免非 GET 请求体残留在
        # HTTP/1.1 keep-alive 连接中污染下一个请求。
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        # HEAD 404 不写 body,但保留与 body 等长的 Content-Length
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _route(self, method: str) -> None:
        bare_path = self.path.split("?", 1)[0]

        # 非 GET 一律 METHOD_NOT_ALLOWED (404)
        if method != "GET":
            self._respond(404, METHOD_NOT_ALLOWED)
            return

        # 只暴露 M13-01 Home 六个端点
        if bare_path not in ALLOWED_GET_PATHS:
            self._respond(404, NOT_FOUND)
            return

        # 委托给共享契约处理（含 audit limit=100 校验）
        status, payload = self._contract.handle("GET", self.path)
        self._respond(status, payload)

    def do_GET(self) -> None:
        self._route("GET")

    def do_POST(self) -> None:
        self._route("POST")

    def do_PUT(self) -> None:
        self._route("PUT")

    def do_PATCH(self) -> None:
        self._route("PATCH")

    def do_DELETE(self) -> None:
        self._route("DELETE")

    def log_message(self, format: str, *args: object) -> None:
        return  # 静默 stderr


class HarmonyMockServer:
    """mock 服务器，默认绑定 127.0.0.1。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.host = host
        self.port = port
        self.server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> HarmonyMockServer:
        self.server = ThreadingHTTPServer((self.host, self.port), _HarmonyMockHandler)
        self.server.daemon_threads = True
        self._thread = threading.Thread(
            target=self.server.serve_forever,
            name="harmony-mock",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description="HarmonyOS mock backend for M13-02")
    parser.add_argument("--port", type=int, default=8765, help="listen port (default 8765)")
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="bind address (default 127.0.0.1, use 0.0.0.0 for emulator)",
    )
    args = parser.parse_args()

    server = HarmonyMockServer(host=args.host, port=args.port)
    server.start()
    bind_url = f"http://{args.host}:{args.port}"
    print(f"HarmonyOS mock server listening on {bind_url}", flush=True)
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        server.stop()
        return 0


if __name__ == "__main__":
    sys.exit(main())
