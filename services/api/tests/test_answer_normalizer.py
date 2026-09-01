"""M4-05 Answer normalizer：语音答案只生成规范化答案事件，判定由服务端执行。

- 域层：mcq 字母/序号规范化与选项校验、true_false 对错归一、short_answer 文本、
  essay 拒绝；无效一律 reason 明确 + is_valid=False（绝不猜答案）；
- API：POST /answers（04 文档契约）——event_id 幂等重放、不信任客户端
  normalized_answer、规范化失败走澄清不落库。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.domain.answer_normalizer import normalize_answer
from app.domain.grading import normalize_type
from app.domain.models import Option, Question
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _mcq(question_id: str = "q1", options=("A", "B", "C", "D")) -> Question:
    return Question(
        id=question_id,
        type="choice",
        stem="示例题",
        options=tuple(Option(key, f"选项 {key}") for key in options),
        answer="B",
        explanation="",
        angles=__import__("app.domain.models", fromlist=["Angles"]).Angles(
            concept="c", method="m", mistake="k", variant="v"
        ),
        knowledge=("k",),
    )


def _tf() -> Question:
    return Question(
        id="q_tf",
        type="tf",
        stem="判断题",
        options=(),
        answer="T",
        explanation="",
        angles=__import__("app.domain.models", fromlist=["Angles"]).Angles(
            concept="c", method="m", mistake="k", variant="v"
        ),
        knowledge=("k",),
    )


# ---------- 域层 ----------


def test_mcq_letter_normalized_and_validated() -> None:
    result = normalize_answer(_mcq(), letter="b", transcript="选 b", confidence=0.9)
    assert result.is_valid
    assert result.answer == "B"
    assert result.event["source"] == "voice"
    assert result.event["question_id"] == "q1"
    assert result.event["answer"] == "B"


def test_mcq_ordinal_maps_and_out_of_range_rejected() -> None:
    result = normalize_answer(_mcq(), ordinal=2, transcript="我选第二个")
    assert result.is_valid and result.answer == "B"

    bad = normalize_answer(_mcq(), ordinal=5, transcript="我选第五个")
    assert not bad.is_valid and bad.answer is None
    assert "超出" in bad.reason


def test_mcq_missing_option_letter_rejected() -> None:
    bad = normalize_answer(_mcq(options=("A", "B", "C")), letter="D", transcript="选 D")
    assert not bad.is_valid
    assert "不存在" in bad.reason and "A/B/C" in bad.reason


def test_mcq_without_slot_rejected() -> None:
    bad = normalize_answer(_mcq(), transcript="我选那个")
    assert not bad.is_valid and "没有听清" in bad.reason


def test_true_false_text_normalized() -> None:
    assert normalize_answer(_tf(), transcript="对").answer == "T"
    assert normalize_answer(_tf(), transcript="正确").answer == "T"
    assert normalize_answer(_tf(), transcript="错").answer == "F"
    assert normalize_answer(_tf(), transcript="错误").answer == "F"
    unclear = normalize_answer(_tf(), transcript="选 B")
    assert not unclear.is_valid and "判断" in unclear.reason


def test_short_answer_strips_text() -> None:
    question = Question(
        id="q_s",
        type="short",
        stem="填空",
        options=(),
        answer="X",
        explanation="",
        angles=__import__("app.domain.models", fromlist=["Angles"]).Angles(
            concept="c", method="m", mistake="k", variant="v"
        ),
        knowledge=("k",),
    )
    result = normalize_answer(question, transcript="  导数  ")
    assert result.is_valid and result.answer == "导数"


def test_essay_voice_rejected() -> None:
    question = Question(
        id="q_e",
        type="essay",
        stem="论述",
        options=(),
        answer="",
        explanation="",
        angles=__import__("app.domain.models", fromlist=["Angles"]).Angles(
            concept="c", method="m", mistake="k", variant="v"
        ),
        knowledge=("k",),
    )
    result = normalize_answer(question, letter="B", transcript="选 B")
    assert not result.is_valid
    assert "主观题" in result.reason


def test_normalize_type_aliases_consistent() -> None:
    """normalizer 与判分器使用同一套题型语义。"""
    assert normalize_type("choice") == "mcq"
    assert normalize_type("tf") == "true_false"
    assert normalize_type("short") == "short_answer"


def test_normalizer_is_deterministic() -> None:
    for kwargs in ({"letter": "b"}, {"ordinal": 2}, {"transcript": "对"}):
        assert normalize_answer(_mcq(), **kwargs) == normalize_answer(_mcq(), **kwargs)
        assert normalize_answer(_tf(), **kwargs) == normalize_answer(_tf(), **kwargs)


# ---------- API：/answers 端到端 ----------


def _start_exam(client: TestClient) -> str:
    response = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
    assert response.status_code == 201
    return response.json()["exam_id"]


def _waiting_session(client: TestClient, exam_id: str) -> str:
    session_id = client.post("/api/v1/voice/sessions", json={"exam_id": exam_id}).json()["session_id"]
    for command in ("start_reading", "question_read", "options_read"):
        assert client.post(
            f"/api/v1/voice/sessions/{session_id}/commands", json={"type": command}
        ).status_code == 200
    return session_id


def test_answer_endpoint_normalizes_and_persists() -> None:
    """「选 b」→ 规范化 B → 服务端确定性提交落库 + 事件留痕。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = _waiting_session(client, exam_id)
        response = client.post(
            f"/api/v1/voice/sessions/{session_id}/answers",
            json={"transcript": "选 b", "event_id": "evt-001", "confidence": 0.94},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["normalized_answer"] == "B"  # 大写归一
        assert body["idempotent"] is False
        assert body["session"]["status"] == "ANSWER_COMMITTED"

        exam_view = client.get(f"/api/v1/exams/{exam_id}").json()
        question_id = exam_view["questions"][0]["id"]
        assert exam_view["answers"].get(question_id) == "B"


def test_answer_event_id_is_idempotent() -> None:
    """同 event_id 重放：返回既有结果、不重复落库、状态不再迁移。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = _waiting_session(client, exam_id)
        url = f"/api/v1/voice/sessions/{session_id}/answers"
        payload = {"transcript": "选 B", "event_id": "evt-dup"}
        first = client.post(url, json=payload).json()
        replay = client.post(url, json=payload).json()

        assert replay["idempotent"] is True
        assert replay["normalized_answer"] == first["normalized_answer"]
        assert replay["session"]["status"] == "ANSWER_COMMITTED"  # 未再次迁移

        exam_view = client.get(f"/api/v1/exams/{exam_id}").json()
        assert exam_view["next_sequence"] == 2  # 只落了一次答案事件


def test_answer_distrusts_client_normalized_answer() -> None:
    """客户端伪造 normalized_answer=X：服务端以 transcript 重新规范化为准。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = _waiting_session(client, exam_id)
        response = client.post(
            f"/api/v1/voice/sessions/{session_id}/answers",
            json={"transcript": "选 B", "event_id": "evt-fake", "normalized_answer": "X"},
        )
        body = response.json()
        assert body["normalized_answer"] == "B"

        exam_view = client.get(f"/api/v1/exams/{exam_id}").json()
        question_id = exam_view["questions"][0]["id"]
        assert exam_view["answers"].get(question_id) == "B"


def test_answer_invalid_option_clarifies_without_commit() -> None:
    """选不存在的选项（第五个）→ 澄清不落库，事件留 accepted=false 审计。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = _waiting_session(client, exam_id)
        response = client.post(
            f"/api/v1/voice/sessions/{session_id}/answers",
            json={"transcript": "我选第五个", "event_id": "evt-bad"},
        )
        body = response.json()
        assert body["accepted"] is False
        assert body["normalized_answer"] is None
        assert body["clarified_question"]
        assert body["session"]["status"] == "CLARIFYING"

        exam_view = client.get(f"/api/v1/exams/{exam_id}").json()
        assert not exam_view["answers"]


def test_answer_after_clarify_recovers() -> None:
    """澄清后重新作答（新 event_id）→ 提交成功回 committed。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = _waiting_session(client, exam_id)
        url = f"/api/v1/voice/sessions/{session_id}/answers"
        bad = client.post(url, json={"transcript": "我选第五个", "event_id": "evt-b1"}).json()
        assert bad["session"]["status"] == "CLARIFYING"
        good = client.post(url, json={"transcript": "选 C", "event_id": "evt-g1"}).json()
        assert good["accepted"] is True
        assert good["session"]["status"] == "ANSWER_COMMITTED"


def test_answer_requires_event_id_and_session() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        assert (
            client.post(
                f"/api/v1/voice/sessions/{_waiting_session(client, exam_id)}/answers",
                json={"transcript": "选 B"},  # 缺 event_id
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/voice/sessions/vs_missing/answers",
                json={"transcript": "选 B", "event_id": "evt-x"},
            ).status_code
            == 404
        )


def test_answer_requires_database() -> None:
    with TestClient(create_app(None)) as client:
        response = client.post(
            "/api/v1/voice/sessions/vs_x/answers",
            json={"transcript": "选 B", "event_id": "evt-y"},
        )
        assert response.status_code == 503


# ---------- 安全：event_id 按会话域隔离（防跨会话读取与响应投毒） ----------


def test_answer_event_id_scoped_to_session() -> None:
    """跨会话复用 event_id → 409：不读他人事件、状态不迁移、答案不投毒。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_a = _waiting_session(client, exam_id)
        first = client.post(
            f"/api/v1/voice/sessions/{session_a}/answers",
            json={"transcript": "选 B", "event_id": "evt-shared"},
        ).json()
        assert first["accepted"] is True

        session_b = _waiting_session(client, exam_id)  # 同考试的第二个会话
        response = client.post(
            f"/api/v1/voice/sessions/{session_b}/answers",
            json={"transcript": "选 C", "event_id": "evt-shared"},  # 复用 A 的 event_id
        )
        assert response.status_code == 409
        assert "其他会话" in response.json()["detail"]

        after = client.get(f"/api/v1/voice/sessions/{session_b}").json()
        assert after["status"] == "WAITING_ANSWER"  # B 状态未被扰动
        exam_view = client.get(f"/api/v1/exams/{exam_id}").json()
        question_id = exam_view["questions"][0]["id"]
        assert exam_view["answers"].get(question_id) == "B"  # 只有 A 的提交生效

        # 正常重放（同会话）仍幂等
        replay = client.post(
            f"/api/v1/voice/sessions/{session_a}/answers",
            json={"transcript": "选 B", "event_id": "evt-shared"},
        ).json()
        assert replay["idempotent"] is True


def test_repo_rejects_cross_session_event_id() -> None:
    """repository 兜底：并发竞态下跨会话 event_id 同样拒绝（ValueError）。"""
    app = create_app(SQLITE_URL)
    with TestClient(app) as client:
        exam_id = _start_exam(client)
        session_a = _waiting_session(client, exam_id)
        client.post(
            f"/api/v1/voice/sessions/{session_a}/answers",
            json={"transcript": "选 B", "event_id": "evt-race"},
        )
        repo = app.state.voice_answer_events
        with pytest.raises(ValueError, match="其他会话"):
            asyncio.run(
                repo.record(
                    event_id="evt-race",
                    session_id="vs-attacker",
                    exam_id=exam_id,
                    question_id="q1",
                    normalized_answer="C",
                    intent="choose_option",
                    transcript="选 C",
                    confidence=0.0,
                    accepted=True,
                )
            )
