"""M4-04 Intent parser：中文语音指令解析准确性（G 提示词验收）。

- 域层：G 提示词全部示例（选 B/第二个/改成 C/重复一遍）+ 命令集（跳过/暂停/结束/
  慢一点/继续）+ 含糊/未识别边界 + 序号映射 + 确定性；
- API：POST /intents 解析并一体化应用 FSM——答案提交落库、含糊走澄清、
  unknown/pause 不改状态（不虚报理解）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain.intent_parser import (
    INTENT_CHANGE,
    INTENT_CHOOSE,
    INTENT_END,
    INTENT_PAUSE,
    INTENT_REPEAT_OPTIONS,
    INTENT_REPEAT_QUESTION,
    INTENT_SKIP,
    INTENT_SLOW_DOWN,
    INTENT_UNKNOWN,
    ordinal_to_letter,
    parse,
)
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


# ---------- 域层：G 提示词示例与命令集 ----------


@pytest.mark.parametrize(
    ("transcript", "intent", "letter", "ordinal"),
    [
        ("选 B", INTENT_CHOOSE, "B", None),
        ("选 b", INTENT_CHOOSE, "B", None),  # 大写归一
        ("答案是 D", INTENT_CHOOSE, "D", None),
        ("B 选项", INTENT_CHOOSE, "B", None),
        ("就 A 吧", INTENT_CHOOSE, "A", None),
        ("我选第二个", INTENT_CHOOSE, None, 2),
        ("第一个", INTENT_CHOOSE, None, 1),
        ("第三个", INTENT_CHOOSE, None, 3),
        ("第十个", INTENT_CHOOSE, None, 10),
        ("我改成 C", INTENT_CHANGE, "C", None),
        ("改选 C", INTENT_CHANGE, "C", None),
        ("换成第四个", INTENT_CHANGE, None, 4),
    ],
)
def test_answer_slot_extraction(transcript: str, intent: str, letter: str | None, ordinal: int | None) -> None:
    parsed = parse(transcript)
    assert parsed.intent == intent
    assert parsed.letter == letter
    assert parsed.ordinal == ordinal
    assert not parsed.ambiguous
    assert parsed.fsm_command == "answer_proposed"


@pytest.mark.parametrize(
    ("transcript", "intent"),
    [
        ("重复一遍", INTENT_REPEAT_QUESTION),
        ("再来一遍", INTENT_REPEAT_QUESTION),
        ("再说一遍选项", INTENT_REPEAT_OPTIONS),
        ("重复选项", INTENT_REPEAT_OPTIONS),
        ("慢一点", INTENT_SLOW_DOWN),
        ("说慢点", INTENT_SLOW_DOWN),
        ("跳过", INTENT_SKIP),
        ("下一题", INTENT_SKIP),
        ("结束", INTENT_END),
        ("交卷", INTENT_END),
        ("做完了", INTENT_END),
    ],
)
def test_command_intents(transcript: str, intent: str) -> None:
    parsed = parse(transcript)
    assert parsed.intent == intent
    assert parsed.fsm_command == intent  # 命令类意图与 FSM 命令同名


def test_pause_resume_map_to_commands() -> None:
    """M4-06：pause/resume 解析后映射同名 FSM 命令（播报控制自环）。"""
    assert parse("暂停").intent == INTENT_PAUSE
    assert parse("暂停").fsm_command == "pause"
    assert parse("继续").intent == "resume"
    assert parse("继续").fsm_command == "resume"


def test_ambiguous_answer_goes_clarify() -> None:
    """提到选择但抽不出槽位 → ambiguous（上游应澄清，不猜答案）。"""
    for transcript in ("我选那个", "选这个", "我选那个蓝色的"):
        parsed = parse(transcript)
        assert parsed.ambiguous, transcript
    assert parse("我选那个").intent == INTENT_CHOOSE


def test_unrecognized_is_unknown() -> None:
    parsed = parse("今天天气不错")
    assert parsed.intent == INTENT_UNKNOWN
    assert parsed.fsm_command is None
    assert not parsed.ambiguous  # 未识别≠含糊答案
    assert parse("").intent == INTENT_UNKNOWN


def test_change_without_slot_is_ambiguous() -> None:
    parsed = parse("我要改成另一个")
    assert parsed.intent == INTENT_CHANGE
    assert parsed.ambiguous


def test_ordinal_mapping_needs_option_count() -> None:
    """序号→字母：1-based A 起；超界 None（由上游澄清）。"""
    assert ordinal_to_letter(1, 4) == "A"
    assert ordinal_to_letter(2, 4) == "B"
    assert ordinal_to_letter(4, 4) == "D"
    assert ordinal_to_letter(5, 4) is None
    assert ordinal_to_letter(0, 4) is None


def test_parser_is_deterministic() -> None:
    transcripts = ["选 B", "我选第二个", "我改成 C", "重复一遍", "跳过", "我选那个", "今天天气不错"]
    for transcript in transcripts:
        assert parse(transcript) == parse(transcript)


# ---------- API：解析 + 一体化 FSM 应用 ----------


def _start_exam(client: TestClient) -> str:
    response = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
    assert response.status_code == 201
    return response.json()["exam_id"]


def _waiting_session(client: TestClient, exam_id: str) -> dict:
    """创建会话并用 intents 推进到 WAITING_ANSWER。"""
    session_id = client.post("/api/v1/voice/sessions", json={"exam_id": exam_id}).json()["session_id"]
    url = f"/api/v1/voice/sessions/{session_id}/intents"
    for transcript in ("开始读题", "我选第二个"):
        client.post(url, json={"transcript": transcript})  # 读题命令未接 TTS，占位
    for command in ("start_reading", "question_read", "options_read"):
        assert client.post(
            f"/api/v1/voice/sessions/{session_id}/commands", json={"type": command}
        ).status_code == 200
    return client.get(f"/api/v1/voice/sessions/{session_id}").json()


def test_intent_choose_letter_commits_and_persists() -> None:
    """「选 B」→ 服务端确定性提交，exam answers 落库。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        waiting = _waiting_session(client, exam_id)
        response = client.post(
            f"/api/v1/voice/sessions/{waiting['session_id']}/intents",
            json={"transcript": "选 B"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["intent"] == "choose_option"
        assert body["fsm_applied"] is True
        assert body["session"]["status"] == "ANSWER_COMMITTED"

        exam_view = client.get(f"/api/v1/exams/{exam_id}").json()
        question_id = exam_view["questions"][0]["id"]
        assert exam_view["answers"].get(question_id) == "B"


def test_intent_ordinal_maps_via_option_count() -> None:
    """「我选第二个」→ 结合当前题选项数映射字母 B（G 提示词示例）。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        waiting = _waiting_session(client, exam_id)
        response = client.post(
            f"/api/v1/voice/sessions/{waiting['session_id']}/intents",
            json={"transcript": "我选第二个"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ordinal"] == 2
        assert body["session"]["status"] == "ANSWER_COMMITTED"

        exam_view = client.get(f"/api/v1/exams/{exam_id}").json()
        question_id = exam_view["questions"][0]["id"]
        assert exam_view["answers"].get(question_id) == "B"


def test_intent_change_answer_overwrites() -> None:
    """「我改成 C」→ 覆盖提交（追加覆盖事件）。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        waiting = _waiting_session(client, exam_id)
        url = f"/api/v1/voice/sessions/{waiting['session_id']}/intents"
        assert client.post(url, json={"transcript": "选 B"}).status_code == 200
        response = client.post(url, json={"transcript": "我改成 C"})
        assert response.status_code == 200
        assert response.json()["intent"] == "change_answer"
        assert response.json()["session"]["status"] == "ANSWER_COMMITTED"

        exam_view = client.get(f"/api/v1/exams/{exam_id}").json()
        question_id = exam_view["questions"][0]["id"]
        assert exam_view["answers"].get(question_id) == "C"
        assert exam_view["next_sequence"] >= 3


def test_intent_out_of_range_ordinal_clarifies() -> None:
    """序号超选项数（第五个 / 4 选项）→ CLARIFYING，绝不猜答案落库。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        waiting = _waiting_session(client, exam_id)
        response = client.post(
            f"/api/v1/voice/sessions/{waiting['session_id']}/intents",
            json={"transcript": "我选第五个"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["session"]["status"] == "CLARIFYING"
        assert body["clarified_question"]

        exam_view = client.get(f"/api/v1/exams/{exam_id}").json()
        assert not exam_view["answers"]  # 含糊答案不落库


def test_intent_ambiguous_transcript_clarifies() -> None:
    """「我选那个」→ 澄清而非提交。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        waiting = _waiting_session(client, exam_id)
        response = client.post(
            f"/api/v1/voice/sessions/{waiting['session_id']}/intents",
            json={"transcript": "我选那个"},
        )
        assert response.json()["session"]["status"] == "CLARIFYING"


def test_intent_commands_drive_session() -> None:
    """「跳过」→ NEXT_QUESTION；「结束」→ REPORT_READY。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        waiting = _waiting_session(client, exam_id)
        url = f"/api/v1/voice/sessions/{waiting['session_id']}/intents"
        skipped = client.post(url, json={"transcript": "跳过"})
        assert skipped.json()["session"]["status"] == "NEXT_QUESTION"
        ended = client.post(url, json={"transcript": "结束"})
        assert ended.json()["session"]["status"] == "REPORT_READY"


def test_intent_unknown_ignored_pause_applied_as_control_loop() -> None:
    """unknown 不应用 FSM；pause（M4-06）作为播报控制自环应用——状态均不变。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        waiting = _waiting_session(client, exam_id)
        url = f"/api/v1/voice/sessions/{waiting['session_id']}/intents"

        unknown = client.post(url, json={"transcript": "今天天气不错"}).json()
        assert unknown["fsm_applied"] is False
        assert unknown["session"] is None

        paused = client.post(url, json={"transcript": "暂停"}).json()
        assert paused["intent"] == "pause"
        assert paused["fsm_applied"] is True  # M4-06 接入
        assert paused["applied_event"] == "pause"
        assert paused["session"]["status"] == "WAITING_ANSWER"  # 自环不破坏状态
        assert paused["session"]["revision"] == waiting["revision"] + 1  # 审计痕迹

        after = client.get(f"/api/v1/voice/sessions/{waiting['session_id']}").json()
        assert after["status"] == "WAITING_ANSWER"
        assert after["revision"] == waiting["revision"] + 1


def test_intent_on_unknown_session_404() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        response = client.post(
            "/api/v1/voice/sessions/vs_missing/intents", json={"transcript": "选 B"}
        )
        assert response.status_code == 404


def test_intent_requires_database() -> None:
    with TestClient(create_app(None)) as client:
        response = client.post(
            "/api/v1/voice/sessions/vs_x/intents", json={"transcript": "选 B"}
        )
        assert response.status_code == 503
