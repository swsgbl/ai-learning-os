"""M14-246 API 可观测性契约：/readyz、结构化 JSON 日志、全局异常处理。

边界：
- /readyz 依赖检查可注入（app.state.readiness_checks + 假检查），绝不依赖
  真实 DB/Redis/voice/LiveKit；默认检查只验证启动装配完成；
- reason 安全面：安全文案/异常类名保留；连接串/URL/敏感标记/控制字符/
  超长 reason 一律固定安全回退——测试用自造无效哨兵验证不泄出；
- 日志断言只锚定格式（单行 JSON/固定字段）、请求关联（request_id）、
  敏感键脱敏与异常脱敏（类型+净化栈位置，无原始消息/traceback/全路径）；
- 全局异常处理与既有契约共存：HTTPException（401/404 detail 形状）、
  RequestValidationError（422 detail 列表）、request-id 中间件均不变弱；
  未捕获异常 500 兜底为固定脱敏 detail。
- 敏感样本说明：本文件一切凭据形态字符串均为自造无效哨兵，仅用于
  断言「不进响应/不进日志」，不对应任何真实凭据。
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import copy_context

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import ConflictError, DomainError, NotFoundError
from app.core.logging import JsonLogFormatter, configure_logging, request_id_var
from app.main import create_app
from app.ops.readiness import UNSAFE_REASON_FALLBACK, CheckOutcome, sanitize_reason

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture()
def _restore_root_logging():
    """隔离 root logger 状态：级别与 JsonLogFormatter handler 用后还原。"""
    root = logging.getLogger()
    before_level = root.level
    before_handlers = list(root.handlers)
    yield root
    root.setLevel(before_level)
    for handler in list(root.handlers):
        if handler not in before_handlers:
            root.removeHandler(handler)


def _make_record(logger_name: str = "app.obs.test", level: int = logging.INFO,
                 msg: str = "hello", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name=logger_name, level=level, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


# --- /readyz 契约 ---


def test_readyz_ready_by_default_sqlite() -> None:
    """默认检查（repository 接线）通过 = 200 ready；checks 逐项透出。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["repository"] == {"ready": True}


def test_readyz_ready_without_database() -> None:
    """内存模式（无任何 DB）同样 ready——就绪检查不依赖外部服务。"""
    with TestClient(create_app()) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_readyz_not_ready_with_injected_failing_check() -> None:
    """注入假失败检查 = 503 not_ready，reason 如实透出（可注入性契约）。"""
    app = create_app(SQLITE_URL)

    async def _db_down() -> CheckOutcome:
        return CheckOutcome(ready=False, reason="db unreachable")

    app.state.readiness_checks = {"db": _db_down}
    with TestClient(app) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["db"] == {"ready": False, "reason": "db unreachable"}


def test_readyz_supports_sync_checks_and_partial_failure() -> None:
    """同步检查可用；多项检查部分失败 = 整体 not_ready，单项结果保留。"""
    app = create_app(SQLITE_URL)

    def _cache_ok() -> CheckOutcome:
        return CheckOutcome(ready=True)

    async def _voice_down() -> CheckOutcome:
        return CheckOutcome(ready=False, reason="voice offline")

    app.state.readiness_checks = {"cache": _cache_ok, "voice": _voice_down}
    with TestClient(app) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["cache"] == {"ready": True}
    assert body["checks"]["voice"] == {"ready": False, "reason": "voice offline"}


def test_readyz_check_exception_is_contained_and_sanitized() -> None:
    """检查抛异常不炸端点：按 not_ready 记账，reason 只取异常类名——
    异常文本（可能含连接串/敏感值）绝不透出到响应。"""
    app = create_app(SQLITE_URL)

    async def _explodes() -> CheckOutcome:
        raise RuntimeError("postgres://user:secret@127.0.0.1:5433/db")

    app.state.readiness_checks = {"db": _explodes}
    with TestClient(app) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["checks"]["db"] == {"ready": False, "reason": "RuntimeError"}
    assert "secret" not in resp.text
    assert "postgres" not in resp.text


def test_readyz_unsafe_reason_replaced_by_safe_fallback() -> None:
    """reason 含疑似敏感形态（连接串/URL/userinfo/敏感标记/控制字符/超长）
    时，未认证的 /readyz 只回固定安全文案，原值绝不进响应（自造无效
    哨兵样本，仅用于验证不泄出）。"""
    app = create_app(SQLITE_URL)

    async def _dsn() -> CheckOutcome:
        return CheckOutcome(ready=False, reason="postgres://user:secret@127.0.0.1:5433/db")

    async def _userinfo() -> CheckOutcome:
        return CheckOutcome(ready=False, reason="user:pass@db-host:5432 handshake failed")

    async def _tokenish() -> CheckOutcome:
        return CheckOutcome(ready=False, reason="auth token expired")

    def _control_chars() -> CheckOutcome:
        return CheckOutcome(ready=False, reason="bad\x00reason")

    async def _too_long() -> CheckOutcome:
        return CheckOutcome(ready=False, reason="x" * 500)

    app.state.readiness_checks = {
        "dsn": _dsn,
        "userinfo": _userinfo,
        "tokenish": _tokenish,
        "ctrl": _control_chars,
        "toolong": _too_long,
    }
    with TestClient(app) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    for name in ("dsn", "userinfo", "tokenish", "ctrl", "toolong"):
        assert body["checks"][name] == {"ready": False, "reason": UNSAFE_REASON_FALLBACK}
    # 自造哨兵原值（连接串/凭据形态）不进响应
    assert "secret" not in resp.text
    assert "://" not in resp.text
    assert "pass@" not in resp.text
    assert "auth token expired" not in resp.text
    assert "bad\x00" not in resp.text


def test_sanitize_reason_boundary() -> None:
    """净化面单元边界：安全文案（含非 ASCII）与异常类名保留，不安全
    形态一律固定回退，空白/None 归一为省略。"""
    assert sanitize_reason("db unreachable") == "db unreachable"
    assert sanitize_reason("RuntimeError") == "RuntimeError"
    assert sanitize_reason("KeyError") == "KeyError"  # 裸 "key" 不是敏感标记
    assert sanitize_reason("数据库暂时不可用") == "数据库暂时不可用"
    # 长但含空白的人类可读文案保留
    assert sanitize_reason("database connection pool exhausted after retry window") == (
        "database connection pool exhausted after retry window"
    )
    assert sanitize_reason(None) is None
    assert sanitize_reason("   ") is None
    assert sanitize_reason("postgres://u:p@h/db") == UNSAFE_REASON_FALLBACK
    assert sanitize_reason("user:pass@host") == UNSAFE_REASON_FALLBACK
    assert sanitize_reason("bearer rejected") == UNSAFE_REASON_FALLBACK
    assert sanitize_reason("line\ninjection") == UNSAFE_REASON_FALLBACK
    assert sanitize_reason("x" * 200) == UNSAFE_REASON_FALLBACK


def test_sanitize_reason_rejects_long_opaque_strings() -> None:
    """不透明长串（无敏感词/无 URL 语法、无空白、>48 字符）疑似随机
    凭据形态——保守拒绝为固定回退（哨兵为自造无效值）。"""
    opaque_sentinel = "sentinel-opaque-0123456789abcdefghijklmnopqrstuvwxyz"
    assert len(opaque_sentinel) > 48
    assert " " not in opaque_sentinel
    assert sanitize_reason(opaque_sentinel) == UNSAFE_REASON_FALLBACK
    # 端到端：经 /readyz 响应同样不泄出
    app = create_app(SQLITE_URL)

    async def _opaque() -> CheckOutcome:
        return CheckOutcome(ready=False, reason=opaque_sentinel)

    app.state.readiness_checks = {"cred": _opaque}
    with TestClient(app) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["checks"]["cred"]["reason"] == UNSAFE_REASON_FALLBACK
    assert opaque_sentinel not in resp.text


def test_readyz_semantics_distinct_from_health() -> None:
    """/health=liveness 恒 200；/readyz=readiness 可 503——语义分离。"""
    app = create_app(SQLITE_URL)

    async def _down() -> CheckOutcome:
        return CheckOutcome(ready=False, reason="dependency down")

    app.state.readiness_checks = {"dep": _down}
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/readyz").status_code == 503


def test_readyz_fails_closed_when_no_checks_registered() -> None:
    """检查注册表为空（{} 或 None）= 无任何就绪证明 → fail-closed 503，
    响应只带固定安全诊断条目（不暴露内部结构）；正常默认/注入检查不受影响。"""
    for empty in ({}, None):
        app = create_app(SQLITE_URL)
        app.state.readiness_checks = empty
        with TestClient(app) as client:
            resp = client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json() == {
            "status": "not_ready",
            "checks": {"readiness": {"ready": False, "reason": "no readiness checks registered"}},
        }


def test_readyz_exempt_from_auth_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """认证开启时 /readyz 与 /health 同为基础设施豁免路径（探针无凭据）。"""
    from app.core.config import get_settings

    monkeypatch.setenv("AUTH_SECRET", "m14-246-readyz-secret-0123456789")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(SQLITE_URL)) as client:
            assert client.get("/readyz").status_code == 200
            assert client.get("/health").status_code == 200
            assert client.get("/api/v1/papers").status_code == 401
    finally:
        monkeypatch.delenv("AUTH_SECRET", raising=False)
        get_settings.cache_clear()


# --- 结构化 JSON 日志契约 ---


def test_json_formatter_fixed_fields() -> None:
    """单行 JSON、固定字段 ts/level/logger/message 齐全且可解析。"""
    line = JsonLogFormatter().format(_make_record(msg="structured line"))
    payload = json.loads(line)
    assert payload["message"] == "structured line"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.obs.test"
    assert isinstance(payload["ts"], str) and payload["ts"]
    assert " " not in line.strip("\n") or line.count("\n") == 0  # 单行


def test_json_formatter_correlates_request_id_from_record_and_context() -> None:
    """请求关联：record 属性优先，其次 ContextVar；两者皆无则省略键。"""
    fmt = JsonLogFormatter()
    # 1) record 显式携带（异常处理器路径）
    payload = json.loads(fmt.format(_make_record(request_id="req-from-record")))
    assert payload["request_id"] == "req-from-record"
    # 2) ContextVar（请求中间件路径）
    def _in_context() -> str:
        return fmt.format(_make_record())

    token = request_id_var.set("req-from-context")
    try:
        payload = json.loads(copy_context().run(_in_context))
        assert payload["request_id"] == "req-from-context"
    finally:
        request_id_var.reset(token)
    # 3) 无关联
    assert "request_id" not in json.loads(fmt.format(_make_record()))


def test_json_formatter_redacts_sensitive_extra_fields() -> None:
    """敏感键（authorization/cookie/token/secret/password/api_key 等）脱敏；
    非敏感附带上限为 JSON 可序列化原值。"""
    line = JsonLogFormatter().format(
        _make_record(
            extra_fields={
                "authorization": "Bearer sample-value",
                "api_key": "sample-value",
                "cookie": "session=sample",
                "retry_count": 3,
            }
        )
    )
    extra = json.loads(line)["extra"]
    assert extra["authorization"] == "***"
    assert extra["api_key"] == "***"
    assert extra["cookie"] == "***"
    assert extra["retry_count"] == 3


def test_configure_logging_idempotent_and_level_preserved(
    _restore_root_logging,
) -> None:
    """幂等安装：重复调用不叠加 handler；level 仅显式传入才设置。"""
    root = _restore_root_logging
    before_level = root.level
    handler = configure_logging()
    assert handler in root.handlers
    assert isinstance(handler.formatter, JsonLogFormatter)
    assert root.level == before_level  # 未传 level = 保留既有级别语义
    again = configure_logging()
    assert again is handler
    assert sum(
        isinstance(h, logging.StreamHandler) and isinstance(h.formatter, JsonLogFormatter)
        for h in root.handlers
    ) == 1
    configure_logging("WARNING")
    assert root.level == logging.WARNING


def test_log_level_settings_normalization_and_validation() -> None:
    """LOG_LEVEL 归一（空白→None、大小写不敏感）与非法值拒绝启动。"""
    assert Settings(log_level=" info ").log_level == "INFO"
    assert Settings(log_level="").log_level is None
    assert Settings().log_level is None
    with pytest.raises(ValueError, match="LOG_LEVEL"):
        Settings(log_level="LOUD")


# --- 全局异常处理契约 ---


def _app_with_boom_routes() -> object:
    app = create_app(SQLITE_URL)

    @app.get("/_test/boom/domain")
    async def _domain() -> dict[str, str]:
        raise DomainError("invalid domain state")

    @app.get("/_test/boom/notfound")
    async def _notfound() -> dict[str, str]:
        raise NotFoundError("entity missing")

    @app.get("/_test/boom/conflict")
    async def _conflict() -> dict[str, str]:
        raise ConflictError("state conflict")

    @app.get("/_test/boom/unhandled")
    async def _unhandled() -> dict[str, str]:
        raise RuntimeError("internal detail with secret-token")

    return app


def test_domain_error_http_mapping() -> None:
    """DomainError 族映射：NotFound→404、Conflict→409、其余→400；
    响应体与 HTTPException 同形 {"detail": ...}；request-id 中间件仍生效。"""
    with TestClient(_app_with_boom_routes()) as client:  # type: ignore[arg-type]
        for path, status, detail in (
            ("/_test/boom/notfound", 404, "entity missing"),
            ("/_test/boom/conflict", 409, "state conflict"),
            ("/_test/boom/domain", 400, "invalid domain state"),
        ):
            resp = client.get(path, headers={"X-Request-ID": "req-dom-1"})
            assert resp.status_code == status
            assert resp.json() == {"detail": detail}
            assert resp.headers["X-Request-ID"] == "req-dom-1"


def test_unhandled_exception_500_contract_and_log_correlation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """未捕获异常：500 固定脱敏 detail（异常细节不进响应）、X-Request-ID
    回填（ServerErrorMiddleware 层响应不经内层中间件）、服务端日志为
    结构化 JSON 且带同一 request_id 关联；异常只以「类型 + 脱敏栈位置」
    落日志——原始异常消息/traceback 文本与自造哨兵值不进任何序列化输出。"""
    with TestClient(  # type: ignore[arg-type]
        _app_with_boom_routes(), raise_server_exceptions=False
    ) as client:
        resp = client.get("/_test/boom/unhandled", headers={"X-Request-ID": "req-obs-500"})
    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal server error"}
    assert "secret-token" not in resp.text
    assert "internal detail" not in resp.text
    assert resp.headers["X-Request-ID"] == "req-obs-500"

    records = [r for r in caplog.records if r.name == "app.api.error_handlers"]
    assert records, "未捕获异常必须有服务端日志"
    payload = json.loads(JsonLogFormatter().format(records[0]))
    assert payload["request_id"] == "req-obs-500"
    assert payload["message"] == "unhandled_exception"
    assert payload["extra"]["exception_class"] == "RuntimeError"
    # 诊断保留：异常类型 + 脱敏栈位置（函数名 + 文件 basename + 行号）
    exc = payload["exc"]
    assert exc["type"] == "RuntimeError"
    assert exc["stack"], "脱敏栈位置必须保留（可定位）"
    assert all({"func", "file", "line"} == set(frame) for frame in exc["stack"])
    assert any(frame["func"] == "_unhandled" for frame in exc["stack"])
    assert all("/" not in frame["file"] and "\\" not in frame["file"] for frame in exc["stack"])
    # 原始异常文本/自造哨兵不进任何序列化日志
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "secret-token" not in serialized
    assert "internal detail" not in serialized


def test_json_formatter_sanitizes_exception_details() -> None:
    """exc_info 结构化脱敏：类型 + 栈位置保留；原始异常消息（含自造
    连接串哨兵）、完整 traceback 文本、完整文件路径一律不落日志。"""
    try:
        raise ValueError("leaky postgres://user:secret@127.0.0.1:5433/db")
    except ValueError:
        record = _make_record(level=logging.ERROR, msg="boom")
        record.exc_info = sys.exc_info()
    payload = json.loads(JsonLogFormatter().format(record))
    exc = payload["exc"]
    assert exc["type"] == "ValueError"
    assert exc["stack"]
    innermost = exc["stack"][-1]
    assert innermost["func"] == "test_json_formatter_sanitizes_exception_details"
    assert innermost["file"] == "test_api_observability.py"  # basename only
    assert isinstance(innermost["line"], int)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "leaky" not in serialized
    assert "secret" not in serialized
    assert "postgres" not in serialized
    assert ".py:" not in serialized  # 无 traceback 文本形态（file:line 串）


def test_existing_http_exception_contract_unchanged() -> None:
    """兼容：未注册 HTTPException/404 路由处理——既有 detail 形状不变。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        resp = client.get("/api/v1/definitely-not-a-route")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}


def test_existing_validation_error_contract_unchanged() -> None:
    """兼容：RequestValidationError 仍由 FastAPI 既有 422 处理（列表形状）。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        resp = client.post("/api/v1/auth/register", json={"username": "x", "password": "y"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, list) and detail
    assert {"loc", "msg", "type"} <= set(detail[0])
