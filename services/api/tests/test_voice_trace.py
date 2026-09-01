"""M4-09 Latency tracing：语音链路各阶段耗时观测。

- 域层：TRACE_STAGES 覆盖验收 7 阶段、summarize_spans p50/p95 分位计算、
  空阶段零缺省（不虚报）、确定性；
- API：客户端上报（source=client）、服务端自动埋点（asr/tts/intent/fsm 全链路
  过一遍 voice 端点后 summary 可见）、stage 白名单 422、duration 边界 422、
  观测不扰动业务状态、聚合只读幂等、无 DB 503。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.voice_trace import TRACE_STAGES, summarize_spans
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


# ---------- 域层 ----------


def test_stages_match_backlog_acceptance() -> None:
    """TRACE_STAGES 与验收原文一一对应：VAD/ASR/意图/FSM/LLM/TTS/首音频。"""
    assert TRACE_STAGES == ("vad", "asr", "intent", "fsm", "llm", "tts", "first_audio")


def test_percentile_known_samples() -> None:
    """已知样本精确验证 nearest-rank 分位：p50=中位、p95 收敛到最大。"""
    spans = [{"stage": "asr", "duration_ms": v} for v in (10, 20, 30, 40, 50)]
    view = summarize_spans(spans)
    assert view["asr"]["count"] == 5
    assert view["asr"]["avg_ms"] == 30.0
    assert view["asr"]["p50_ms"] == 30.0  # ceil(0.5*5)-1 = 2 → 30
    assert view["asr"]["p95_ms"] == 50.0  # ceil(0.95*5)=5 → 末位 50
    assert view["asr"]["max_ms"] == 50.0


def test_empty_stage_zeroed_not_fabricated() -> None:
    """无样本阶段 count=0 其余 0.0——不虚报无数据的阶段。"""
    view = summarize_spans([{"stage": "asr", "duration_ms": 5}])
    for stage in TRACE_STAGES:
        assert stage in view  # 全 7 阶段齐全
    assert view["asr"]["count"] == 1
    assert view["vad"] == {"count": 0, "avg_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}


def test_single_sample() -> None:
    """单样本：p50=p95=max=该值。"""
    view = summarize_spans([{"stage": "tts", "duration_ms": 120}])
    assert view["tts"]["count"] == 1
    assert view["tts"]["p50_ms"] == view["tts"]["p95_ms"] == view["tts"]["max_ms"] == 120.0

def test_summary_is_deterministic() -> None:
    """同输入恒同输出（幂等语义）。"""
    spans = [{"stage": s, "duration_ms": i * 10} for i, s in enumerate(("asr", "tts", "asr", "fsm"))]
    assert summarize_spans(spans) == summarize_spans(spans)


# ---------- API ----------


def test_client_report_and_summary() -> None:
    """客户端上报 vad/llm/first_audio → summary 可见且 source=client。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        for stage, ms in (("vad", 80), ("llm", 900), ("first_audio", 1200)):
            r = client.post(
                "/api/v1/voice/trace",
                json={"stage": stage, "duration_ms": ms, "exam_id": "exam_x"},
            )
            assert r.status_code == 201
            assert r.json()["source"] == "client"
        body = client.get("/api/v1/voice/trace/summary").json()
        assert body["item_count"] == 3
        assert body["stages"]["vad"]["count"] == 1
        assert body["stages"]["vad"]["p50_ms"] == 80.0
        assert body["stages"]["llm"]["count"] == 1
        assert body["stages"]["first_audio"]["p50_ms"] == 1200.0


def test_server_spans_auto_recorded() -> None:
    """服务端自动埋点：asr/tts/intent/fsm 四环节全链路过一遍后 summary 可见。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"}).json()["exam_id"]
        sid = client.post("/api/v1/voice/sessions", json={"exam_id": exam_id}).json()["session_id"]

        # asr：POST /transcribe（本地 FakeAsrProvider）
        r = client.post("/api/v1/voice/transcribe", files={"audio": ("a.wav", b"RIFF", "audio/wav")})
        assert r.status_code == 200
        summary = client.get("/api/v1/voice/trace/summary").json()
        assert summary["stages"]["asr"]["count"] >= 1

        # intent + fsm：POST /intents（「选 B」，内部 apply_voice_command 记 fsm span）
        url = f"/api/v1/voice/sessions/{sid}/commands"
        for cmd in ("start_reading", "question_read", "options_read"):
            assert client.post(url, json={"type": cmd}).status_code == 200
        r = client.post(f"/api/v1/voice/sessions/{sid}/intents", json={"transcript": "选 B"})
        assert r.status_code == 200 and r.json()["fsm_applied"] is True

        summary = client.get("/api/v1/voice/trace/summary").json()
        assert summary["stages"]["intent"]["count"] >= 1
        assert summary["stages"]["fsm"]["count"] >= 1

        # tts：POST /synthesize
        r = client.post("/api/v1/voice/synthesize", json={"text": "你好"})
        assert r.status_code == 200
        summary = client.get("/api/v1/voice/trace/summary").json()
        assert summary["stages"]["tts"]["count"] >= 1


def test_unknown_stage_422() -> None:
    """stage 白名单：未知阶段 422。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        r = client.post("/api/v1/voice/trace", json={"stage": "gpu", "duration_ms": 10})
        assert r.status_code == 422
        assert "未知阶段" in r.json()["detail"]


def test_duration_bounds_422() -> None:
    """duration_ms 边界：0 与超上限 422。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        assert client.post("/api/v1/voice/trace", json={"stage": "vad", "duration_ms": 0}).status_code == 422
        assert client.post("/api/v1/voice/trace", json={"stage": "vad", "duration_ms": 600_001}).status_code == 422


def test_report_does_not_mutate_business_state() -> None:
    """观测不判定：上报 trace 前后业务状态不变（session 不扰动）。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"}).json()["exam_id"]
        sid = client.post("/api/v1/voice/sessions", json={"exam_id": exam_id}).json()["session_id"]
        before = client.get(f"/api/v1/voice/sessions/{sid}").json()
        for _ in range(3):
            assert client.post("/api/v1/voice/trace", json={"stage": "vad", "duration_ms": 10}).status_code == 201
        after = client.get(f"/api/v1/voice/sessions/{sid}").json()
        assert before == after


def test_summary_idempotent_across_calls() -> None:
    """聚合只读幂等：同状态两次 summary 恒同。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        assert client.post("/api/v1/voice/trace", json={"stage": "asr", "duration_ms": 33}).status_code == 201
        first = client.get("/api/v1/voice/trace/summary").json()
        second = client.get("/api/v1/voice/trace/summary").json()
        assert first == second


def test_no_db_503() -> None:
    with TestClient(create_app(None)) as client:
        assert client.post("/api/v1/voice/trace", json={"stage": "vad", "duration_ms": 10}).status_code == 503
        assert client.get("/api/v1/voice/trace/summary").status_code == 503
