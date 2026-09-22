"""HarmonyOS mock 后端服务器 — M13-02/05/06/07/08 验收 + M14-89 认证会话验收测试。

M14-89 认证模式(默认关闭,--auth 开启):
    --auth                开启认证门禁(auth_enabled=true):受保护端点
                          要求 Authorization: Bearer <mock_token>;
    --login-user USER     登录用户名(默认 aiosstudent)
    --login-pass PASS     登录密码(默认 aios-pass-1234)
    --mock-token TOKEN    合成 bearer token(默认 aios-mock-token-89)
    --fault MODE          故障注入:blank_token(login 返回空 token)/
                          whitespace_token(返回含空白 token)/
                          me_down(登录成功但 /me 一律 401)/
                          malformed(login 200 返回非契约 JSON)。
凭据为本地合成测试值,不是任何真实 secret;日志不记录 header/body。


通过 tools.android_smoke.mock_contract.ReadOnlyMockContract().handle 路由,
禁止手写各端点 JSON 字符串。暴露 M13-01 Home 六个 GET 端点 + M13-05
GET /api/v1/papers(确定性论文列表)+ M13-06
GET /api/v1/search/providers(检索 provider 固定列表;M12-04 其余
search 端点 plan/queries/queries/{id} 不在允许清单,一律 404)+ M13-07
GET /api/v1/voice/providers(语音 provider 确定性快照:voice_mode=hybrid、
ASR=fake、TTS 请求 cloud-openai-tts 回退 tone;其余 voice 端点
token/sessions/transcribe/synthesize/trace 等不在允许清单,一律 404,
含其 GET 形式)+ M13-08
GET /api/v1/exams/exam-m13-08-001(考试会话确定性只读快照,与 Android
ExamSessionResponse 逐字段对应;其余 exam 路径——unknown exam_id、
/answers、/submit、/submission、/report、/learning-events、集合路径——
与 POST /api/v1/papers/{paper_id}/exams 一律 404);
未知路径、非 GET 与未支持 method (HEAD/OPTIONS/其它) 均 404,不落入
BaseHTTPRequestHandler 的 501;允许清单端点拒绝一切查询串
(fail-closed;唯一例外 audit,整串须精确为 ?limit=100)。

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

# 只允许这些 M13-01 Home GET 端点 + M13-05 papers + M13-06 search providers
# + M13-07 voice providers + M13-08 exam session 端点(其余 exam 路径一律 404)
ALLOWED_GET_PATHS = frozenset({
    "/health",
    "/api/v1/auth/status",
    "/api/v1/system/privacy",
    "/api/v1/version",
    "/api/v1/system/ops-snapshot",
    "/api/v1/audit",
    "/api/v1/papers",
    "/api/v1/search/providers",
    "/api/v1/voice/providers",
    "/api/v1/exams/exam-m13-08-001",
})


# M14-89: auth 开启时受门禁保护的既有只读端点(无有效 bearer 一律 401)
PROTECTED_GET_PATHS = frozenset({
    "/api/v1/system/privacy",
    "/api/v1/system/ops-snapshot",
    "/api/v1/audit",
    "/api/v1/papers",
    "/api/v1/search/providers",
    "/api/v1/voice/providers",
    "/api/v1/exams/exam-m13-08-001",
})

# 模块级 auth 配置(由 main() / 测试注入;默认 auth 关闭 —— 既有行为不变)
AUTH_STATE = {
    "enabled": False,
    "login_user": "aiosstudent",
    "login_pass": "aios-pass-1234",
    "mock_token": "aios-mock-token-89",
    "fault": "",
}

MOCK_USER = {
    "id": "u-m14-89-0001",
    "username": "aiosstudent",
    "role": "learner",
    "created_at": "2026-09-24T00:00:00",
}


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

        # M14-89: login/me 专用路由(先于非 GET 404 门)
        if method == "POST" and bare_path == "/api/v1/auth/login":
            if not AUTH_STATE["enabled"]:
                self._respond(404, NOT_FOUND)
                return
            self._handle_auth_login()
            return
        if method == "GET" and bare_path == "/api/v1/auth/me":
            if not AUTH_STATE["enabled"]:
                self._respond(404, NOT_FOUND)
                return
            if "?" in self.path:
                self._respond(404, NOT_FOUND)
                return
            self._handle_auth_me()
            return

        # 非 GET 一律 METHOD_NOT_ALLOWED (404)
        if method != "GET":
            self._respond(404, METHOD_NOT_ALLOWED)
            return

        # 只暴露允许的路径
        if bare_path not in ALLOWED_GET_PATHS and bare_path != "/api/v1/auth/me":
            self._respond(404, NOT_FOUND)
            return

        # 允许清单端点一律拒绝任何查询串（fail-closed）。
        # 唯一例外 audit：整串须精确为 ?limit=100——共享契约仅检查 limit
        # 取值（limit=100&foo=bar 等仍会 200），故在委托前先行收紧。
        if bare_path == "/api/v1/audit":
            if self.path != "/api/v1/audit?limit=100":
                self._respond(404, NOT_FOUND)
                return
        elif "?" in self.path:
            self._respond(404, NOT_FOUND)
            return

        # M14-89 auth 门禁:开启时,受保护端点必须带有效 Bearer token
        if AUTH_STATE["enabled"] and bare_path in PROTECTED_GET_PATHS:
            if not self._bearer_ok():
                self._respond(401, {"detail": "Missing bearer token"})
                return

        # auth/status 如实透出 auth 开关(M14-89:覆盖共享契约的固定值)
        if bare_path == "/api/v1/auth/status":
            self._respond(200, {"auth_enabled": AUTH_STATE["enabled"]})
            return

        # 委托给共享契约处理（含 audit limit=100 校验 + papers / providers 端点）
        status, payload = self._contract.handle("GET", self.path)
        self._respond(status, payload)

    def _bearer_ok(self) -> bool:
        """Bearer 校验(仅比对合成 token;绝不记录 header 值)。"""
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        supplied = auth[len("Bearer "):].strip()
        return supplied == AUTH_STATE["mock_token"]

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _handle_auth_login(self) -> None:
        """POST /api/v1/auth/login(M14-89)。凭据不落日志。"""
        try:
            payload = json.loads(self._read_body().decode("utf-8"))
        except (ValueError, UnicodeError):
            self._respond(422, {"detail": "invalid json"})
            return
        if not isinstance(payload, dict):
            self._respond(422, {"detail": "invalid payload"})
            return
        user = payload.get("username")
        password = payload.get("password")
        # M14-89: 缺失/非字符串凭据字段是载荷校验错误(422),
        # 与"字段齐全但值错误"的认证失败(401)严格区分。
        if not isinstance(user, str) or not isinstance(password, str):
            self._respond(422, {"detail": "invalid payload"})
            return
        if user != AUTH_STATE["login_user"] or password != AUTH_STATE["login_pass"]:

            self._respond(401, {"detail": "用户名或密码错误"})
            return
        fault = AUTH_STATE["fault"]
        if fault == "blank_token":
            self._respond(200, {"access_token": "", "token_type": "bearer"})
            return
        if fault == "whitespace_token":
            self._respond(200, {"access_token": " bad token ", "token_type": "bearer"})
            return
        if fault == "wrong_token_type":
            # R16: token_type 契约故障 —— 客户端必须拒绝建立会话
            self._respond(200, {"access_token": AUTH_STATE["mock_token"], "token_type": "mac"})
            return
        if fault == "missing_token_type":
            # R16: token_type 缺失 —— 同样必须拒绝
            self._respond(200, {"access_token": AUTH_STATE["mock_token"]})
            return
        if fault == "malformed":
            self._respond(200, {"unexpected": "shape"})
            return
        self._respond(200, {
            "access_token": AUTH_STATE["mock_token"],
            "token_type": "bearer",
        })

    def _handle_auth_me(self) -> None:
        """GET /api/v1/auth/me(M14-89):me_down 故障注入 401。"""
        if AUTH_STATE["fault"] == "me_down" or not self._bearer_ok():
            self._respond(401, {"detail": "Missing bearer token"})
            return
        self._respond(200, dict(MOCK_USER))

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
    parser = argparse.ArgumentParser(description="HarmonyOS mock backend for M13/M14-89")
    parser.add_argument("--port", type=int, default=8765, help="listen port (default 8765)")
    parser.add_argument("--auth", action="store_true", help="enable auth gate (M14-89)")
    parser.add_argument("--login-user", default="aiosstudent")
    parser.add_argument("--login-pass", default="aios-pass-1234")
    parser.add_argument("--mock-token", default="aios-mock-token-89")
    parser.add_argument("--fault", default="", choices=["", "blank_token", "whitespace_token", "wrong_token_type", "missing_token_type", "me_down", "malformed"])
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="bind address (default 127.0.0.1, use 0.0.0.0 for emulator)",
    )
    args = parser.parse_args()

    AUTH_STATE.update({
        "enabled": args.auth,
        "login_user": args.login_user,
        "login_pass": args.login_pass,
        "mock_token": args.mock_token,
        "fault": args.fault,
    })

    server = HarmonyMockServer(host=args.host, port=args.port)
    server.start()
    bind_url = f"http://{args.host}:{args.port}"
    print(
        f"HarmonyOS mock server listening on {bind_url} "
        f"(auth={'on' if AUTH_STATE['enabled'] else 'off'}, "
        f"fault={AUTH_STATE['fault'] or 'none'})",
        flush=True,
    )
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        server.stop()
        return 0


if __name__ == "__main__":
    sys.exit(main())
