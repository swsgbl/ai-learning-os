"""Android 冒烟回环 mock 服务器（M12-06 第二步 A）。

把 :mod:`tools.android_smoke.mock_contract` 的只读固定契约通过
``ThreadingHTTPServer`` 暴露在本机回环地址上，供 Android 冒烟驱动做
端到端 HTTP 验证：

- 只绑定 ``127.0.0.1``，绝不对外网监听；
- GET/POST/PUT/PATCH/DELETE 全部进入固定契约，未知路径 / 写越界一律 404；
- 每个请求把 :func:`tools.android_smoke.sanitize.sanitize_request` 的
  脱敏结构（timestamp_utc / method / path / query_keys / body_len）
  以 JSONL 追加写入 ``log_path``（线程安全）；
  绝不记录 header、body 内容、query 取值或任何 token。

仅使用 Python 标准库；不访问设备、不触网。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from tools.android_smoke.mock_contract import ReadOnlyMockContract
from tools.android_smoke.sanitize import sanitize_request


class _MockHandler(BaseHTTPRequestHandler):
    """把所有方法转发给共享的只读契约，并写脱敏 JSONL 请求日志。"""

    # 由 LoopbackMockServer 注入
    contract: ReadOnlyMockContract
    log_lock: threading.Lock
    log_path: Optional[str]

    protocol_version = "HTTP/1.1"

    def _dispatch(self, method: str) -> None:
        length_header = self.headers.get("Content-Length")
        body = b""
        if length_header:
            try:
                body = self.rfile.read(int(length_header))
            except ValueError:
                body = b""

        target = self.path or "/"
        status, payload = self.contract.handle(method, target, body)
        self._append_log(method, target, body)
        self._respond(status, payload)

    def _append_log(self, method: str, target: str, body: bytes) -> None:
        entry = sanitize_request(method, target, body)
        line = json.dumps(entry, ensure_ascii=False, default=list)
        with self.log_lock:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def _respond(self, status: int, payload: Optional[dict]) -> None:
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else b""
        )
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # 静默 stderr 访问日志：请求记录只进脱敏 JSONL 文件
        return


class LoopbackMockServer:
    """只绑定 127.0.0.1 的回环 mock 服务器，支持 with 语句。"""

    def __init__(self, log_path: str, port: int = 0) -> None:
        self.log_path = log_path
        self.contract = ReadOnlyMockContract()
        self._log_lock = threading.Lock()

        handler = type(
            "BoundMockHandler",
            (_MockHandler,),
            {
                "contract": self.contract,
                "log_lock": self._log_lock,
                "log_path": self.log_path,
            },
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.server.daemon_threads = True

    # ---------- 生命周期 ----------

    @property
    def host(self) -> str:
        return self.server.server_address[0]

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def start(self) -> "LoopbackMockServer":
        thread = threading.Thread(
            target=self.server.serve_forever, name="loopback-mock", daemon=True
        )
        thread.start()
        self._thread = thread
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        thread = getattr(self, "_thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)

    def __enter__(self) -> "LoopbackMockServer":
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
