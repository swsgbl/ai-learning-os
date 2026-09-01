"""M4-03 VoiceSession FSM：8 状态全迁移 + 服务端确定性提交 + 打断边界。

- 域层：迁移矩阵完整（合法路径可达全部 8 状态、非法迁移显式拒绝、
  播报控制自环不破坏状态、committed 后 propose=覆盖）；
- API：session 生命周期端到端（SQLite），answer_proposed 服务端落库、
  响应不含对错判定（考试模式不泄露答案）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain import voice_session_fsm as fsm
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


# ---------- 域层：迁移矩阵 ----------


def test_happy_path_reaches_all_states() -> None:
    """单题完整路径：SESSION_READY→…→ANSWER_COMMITTED→NEXT_QUESTION→REPORT_READY。"""
    state = fsm.SESSION_READY
    state = fsm.transition(state, fsm.EV_START_READING)
    assert state == fsm.READING_QUESTION
    state = fsm.transition(state, fsm.EV_QUESTION_READ)
    assert state == fsm.READING_OPTIONS
    state = fsm.transition(state, fsm.EV_OPTIONS_READ)
    assert state == fsm.WAITING_ANSWER
    state = fsm.transition(state, fsm.EV_ANSWER_PROPOSED)
    assert state == fsm.ANSWER_COMMITTED
    state = fsm.transition(state, fsm.EV_COMMIT_CONFIRMED)
    assert state == fsm.NEXT_QUESTION
    state = fsm.transition(state, fsm.EV_REPORT_READY)
    assert state == fsm.REPORT_READY
    assert fsm.is_terminal(state)


def test_multiloop_path_reading_next_question() -> None:
    """NEXT_QUESTION + start_reading 回到 READING_QUESTION（下题循环）。"""
    state = fsm.transition(fsm.NEXT_QUESTION, fsm.EV_START_READING)
    assert state == fsm.READING_QUESTION


def test_propose_during_announcement_rejected() -> None:
    """打断不提交半成品答案：读题/读选项/初始态 propose 一律拒绝。"""
    for state in (fsm.SESSION_READY, fsm.READING_QUESTION, fsm.READING_OPTIONS):
        with pytest.raises(ValueError, match="answer_proposed"):
            fsm.transition(state, fsm.EV_ANSWER_PROPOSED)
        with pytest.raises(ValueError, match="answer_clarify"):
            fsm.transition(state, fsm.EV_ANSWER_CLARIFY)


def test_committed_propose_is_overwrite() -> None:
    """「我改成 C」：ANSWER_COMMITTED 后 propose 停留 committed（追加覆盖事件）。"""
    assert fsm.transition(fsm.ANSWER_COMMITTED, fsm.EV_ANSWER_PROPOSED) == fsm.ANSWER_COMMITTED
    assert fsm.transition(fsm.ANSWER_COMMITTED, fsm.EV_ANSWER_CLARIFY) == fsm.CLARIFYING


def test_clarifying_flow() -> None:
    """澄清环：WAITING→(含糊)→CLARIFYING→(仍含糊自环/明确)→ANSWER_COMMITTED。"""
    state = fsm.transition(fsm.WAITING_ANSWER, fsm.EV_ANSWER_CLARIFY)
    assert state == fsm.CLARIFYING
    assert fsm.transition(state, fsm.EV_ANSWER_CLARIFY) == fsm.CLARIFYING
    assert fsm.transition(state, fsm.EV_ANSWER_PROPOSED) == fsm.ANSWER_COMMITTED


def test_announcement_controls_are_self_loops() -> None:
    """repeat/slow_down 播报控制不破坏状态（M4-06 前置语义）。"""
    assert fsm.transition(fsm.READING_QUESTION, fsm.EV_REPEAT_QUESTION) == fsm.READING_QUESTION
    assert fsm.transition(fsm.READING_OPTIONS, fsm.EV_SLOW_DOWN) == fsm.READING_OPTIONS
    assert fsm.transition(fsm.WAITING_ANSWER, fsm.EV_REPEAT_OPTIONS) == fsm.WAITING_ANSWER
    assert fsm.transition(fsm.ANSWER_COMMITTED, fsm.EV_SLOW_DOWN) == fsm.ANSWER_COMMITTED


def test_pause_resume_are_self_loops_from_any_non_terminal() -> None:
    """M4-06：pause/resume 任意非终态自环——状态不变（TTS 暂停是客户端行为）。"""
    for state in fsm.VOICE_SESSION_STATES[:-1]:
        assert fsm.transition(state, fsm.EV_PAUSE) == state, state
        assert fsm.transition(state, fsm.EV_RESUME) == state, state


def test_barge_in_moves_announcement_to_listening() -> None:
    """M4-06：打断=停止播报进倾听（保留当前题）；倾听期自环。"""
    assert fsm.transition(fsm.READING_QUESTION, fsm.EV_BARGE_IN) == fsm.WAITING_ANSWER
    assert fsm.transition(fsm.READING_OPTIONS, fsm.EV_BARGE_IN) == fsm.WAITING_ANSWER
    assert fsm.transition(fsm.WAITING_ANSWER, fsm.EV_BARGE_IN) == fsm.WAITING_ANSWER
    assert fsm.transition(fsm.CLARIFYING, fsm.EV_BARGE_IN) == fsm.CLARIFYING


def test_barge_in_rejected_outside_announcement_and_listening() -> None:
    """打断在初始/已提交确认/下题环节/终态拒绝——语义不明绝不乱迁移。"""
    for state in (
        fsm.SESSION_READY,
        fsm.ANSWER_COMMITTED,
        fsm.NEXT_QUESTION,
        fsm.REPORT_READY,
    ):
        with pytest.raises(ValueError, match="barge_in"):
            fsm.transition(state, fsm.EV_BARGE_IN)


def test_terminal_rejects_new_control_events() -> None:
    """终态拒绝 pause/resume/barge_in（复用全事件拒绝语义，显式断言新事件）。"""
    for event in (fsm.EV_PAUSE, fsm.EV_RESUME, fsm.EV_BARGE_IN):
        with pytest.raises(ValueError, match="REPORT_READY"):
            fsm.transition(fsm.REPORT_READY, event)


def test_skip_goes_next_and_end_reaches_terminal_from_all() -> None:
    """skip→NEXT_QUESTION；end 从任意非终态直达 REPORT_READY。"""
    assert fsm.transition(fsm.WAITING_ANSWER, fsm.EV_SKIP) == fsm.NEXT_QUESTION
    assert fsm.transition(fsm.CLARIFYING, fsm.EV_SKIP) == fsm.NEXT_QUESTION
    for state in fsm.VOICE_SESSION_STATES[:-1]:
        assert fsm.transition(state, fsm.EV_END) == fsm.REPORT_READY


def test_terminal_rejects_all_events() -> None:
    for event in fsm.EVENTS:
        with pytest.raises(ValueError, match="REPORT_READY"):
            fsm.transition(fsm.REPORT_READY, event)


def test_unknown_state_and_event_rejected() -> None:
    with pytest.raises(ValueError, match="未知语音会话状态"):
        fsm.transition("ZOMBIE", fsm.EV_END)
    with pytest.raises(ValueError, match="不接受事件"):
        fsm.transition(fsm.WAITING_ANSWER, "dance")


def test_transition_is_deterministic() -> None:
    """同 (状态, 事件) 恒同输出（ADR 27/29/30/31 同款幂等语义）。"""
    for state in fsm.VOICE_SESSION_STATES:
        for event in fsm.EVENTS:
            try:
                first = fsm.transition(state, event)
            except ValueError:
                with pytest.raises(ValueError):
                    fsm.transition(state, event)
                continue
            assert fsm.transition(state, event) == first


# ---------- API：生命周期端到端 ----------


def _start_exam(client: TestClient) -> str:
    response = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
    assert response.status_code == 201
    return response.json()["exam_id"]


def _reading_session(client: TestClient, exam_id: str) -> dict:
    """推进到 WAITING_ANSWER 的会话。"""
    session_id = client.post(
        "/api/v1/voice/sessions", json={"exam_id": exam_id}
    ).json()["session_id"]
    for command in ("start_reading", "question_read", "options_read"):
        response = client.post(f"/api/v1/voice/sessions/{session_id}/commands", json={"type": command})
        assert response.status_code == 200, response.text
    return client.get(f"/api/v1/voice/sessions/{session_id}").json()


def _first_question_id(client: TestClient, exam_id: str) -> str:
    exam_view = client.get(f"/api/v1/exams/{exam_id}").json()
    return exam_view["questions"][0]["id"]


def test_session_lifecycle_end_to_end() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = client.post("/api/v1/voice/sessions", json={"exam_id": exam_id})
        assert session_id.status_code == 201
        body = session_id.json()
        assert body["status"] == "SESSION_READY"
        assert body["question_index"] == 0 and body["revision"] == 1

        # 推进到等待作答并提交答案
        waiting = _reading_session(client, exam_id)
        assert waiting["status"] == "WAITING_ANSWER"
        question_id = _first_question_id(client, exam_id)
        committed = client.post(
            f"/api/v1/voice/sessions/{waiting['session_id']}/commands",
            json={"type": "answer_proposed", "question_id": question_id, "answer": "B"},
        )
        assert committed.status_code == 200, committed.text
        assert committed.json()["session"]["status"] == "ANSWER_COMMITTED"
        raw = committed.text.lower()
        assert "correct" not in raw and "explanation" not in raw  # 考试模式不泄露答案

        # 服务端确定性落库（exam answers 可查）
        answers = client.get(f"/api/v1/exams/{exam_id}").json()
        assert answers["answers"].get(question_id) == "B"


def test_answer_overwrite_appends_new_event() -> None:
    """committed 后 propose=覆盖：「我改成 C」→ exam answers 更新、revision 递增。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        waiting = _reading_session(client, exam_id)
        session_id = waiting["session_id"]
        question_id = _first_question_id(client, exam_id)
        url = f"/api/v1/voice/sessions/{session_id}/commands"
        first = client.post(url, json={"type": "answer_proposed", "question_id": question_id, "answer": "B"})
        assert first.status_code == 200
        second = client.post(url, json={"type": "answer_proposed", "question_id": question_id, "answer": "C"})
        assert second.status_code == 200
        assert second.json()["session"]["status"] == "ANSWER_COMMITTED"
        assert second.json()["session"]["revision"] > first.json()["session"]["revision"]

        exam_view = client.get(f"/api/v1/exams/{exam_id}").json()
        assert exam_view["answers"].get(question_id) == "C"
        assert exam_view["next_sequence"] >= 3  # 追加覆盖事件而非原地改写


def test_propose_during_reading_rejected_409() -> None:
    """播报期 propose → 409，且答案绝不落库。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = client.post("/api/v1/voice/sessions", json={"exam_id": exam_id}).json()["session_id"]
        question_id = _first_question_id(client, exam_id)
        response = client.post(
            f"/api/v1/voice/sessions/{session_id}/commands",
            json={"type": "answer_proposed", "question_id": question_id, "answer": "B"},
        )
        assert response.status_code == 409
        exam_view = client.get(f"/api/v1/exams/{exam_id}").json()
        assert not exam_view["answers"]  # 半成品绝不落库


def test_clarifying_command_flow() -> None:
    """ambiguous propose → CLARIFYING + 追问文案；明确回应后提交。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        waiting = _reading_session(client, exam_id)
        session_id = waiting["session_id"]
        question_id = _first_question_id(client, exam_id)
        url = f"/api/v1/voice/sessions/{session_id}/commands"
        clarify = client.post(
            url,
            json={"type": "answer_proposed", "question_id": question_id, "answer": "第二个", "ambiguous": True},
        )
        assert clarify.status_code == 200
        assert clarify.json()["session"]["status"] == "CLARIFYING"
        assert clarify.json()["clarified_question"]

        committed = client.post(
            url, json={"type": "answer_proposed", "question_id": question_id, "answer": "B"}
        )
        assert committed.status_code == 200
        assert committed.json()["session"]["status"] == "ANSWER_COMMITTED"


def test_skip_next_and_report_ready() -> None:
    """skip→NEXT_QUESTION；report_ready→REPORT_READY 并透出题数。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        waiting = _reading_session(client, exam_id)
        session_id = waiting["session_id"]
        url = f"/api/v1/voice/sessions/{session_id}/commands"
        skipped = client.post(url, json={"type": "skip"})
        assert skipped.status_code == 200
        assert skipped.json()["session"]["status"] == "NEXT_QUESTION"

        report = client.post(url, json={"type": "report_ready"})
        assert report.status_code == 200
        assert report.json()["session"]["status"] == "REPORT_READY"
        assert report.json()["question_total"] > 0


def test_next_question_start_reading_advances_index() -> None:
    """下题循环：committed→确认→NEXT_QUESTION→start_reading→index+1。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        waiting = _reading_session(client, exam_id)
        session_id = waiting["session_id"]
        question_id = _first_question_id(client, exam_id)
        url = f"/api/v1/voice/sessions/{session_id}/commands"
        client.post(url, json={"type": "answer_proposed", "question_id": question_id, "answer": "B"})
        client.post(url, json={"type": "commit_confirmed"})
        reading = client.post(url, json={"type": "start_reading"})
        assert reading.status_code == 200
        assert reading.json()["session"]["status"] == "READING_QUESTION"
        assert reading.json()["session"]["question_index"] == 1


def test_session_conflict_rules() -> None:
    """非法命令 422/409；expected_revision 并发冲突 409；终态后拒绝一切。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = client.post("/api/v1/voice/sessions", json={"exam_id": exam_id}).json()["session_id"]
        url = f"/api/v1/voice/sessions/{session_id}/commands"

        assert client.post(url, json={"type": "dance"}).status_code == 422
        assert client.post(url, json={"type": "answer_proposed"}).status_code == 422

        # expected_revision 并发校验
        stale = client.post(url, json={"type": "start_reading", "expected_revision": 99})
        assert stale.status_code == 409
        assert client.post(url, json={"type": "start_reading"}).status_code == 200

        client.post(url, json={"type": "end"})
        assert client.post(url, json={"type": "start_reading"}).status_code == 409
        assert client.post(url, json={"type": "skip"}).status_code == 409


def test_voice_session_requires_active_exam() -> None:
    """考试不存在 404；已提交考试 409。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        assert client.post("/api/v1/voice/sessions", json={"exam_id": "nope"}).status_code == 404

        exam_id = _start_exam(client)
        client.post(f"/api/v1/exams/{exam_id}/submit", json={})
        response = client.post("/api/v1/voice/sessions", json={"exam_id": exam_id})
        assert response.status_code == 409


def test_voice_session_requires_database() -> None:
    with TestClient(create_app(None)) as client:
        assert client.post("/api/v1/voice/sessions", json={"exam_id": "x"}).status_code == 503
        assert client.get("/api/v1/voice/sessions/vs_x").status_code == 503


def test_unknown_session_404() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        assert client.get("/api/v1/voice/sessions/vs_missing").status_code == 404
        response = client.post(
            "/api/v1/voice/sessions/vs_missing/commands", json={"type": "skip"}
        )
        assert response.status_code == 404


# ---------- M4-06 API：打断与播报控制 ----------


def test_barge_in_during_reading_moves_to_waiting_keeps_question() -> None:
    """读题播报中打断 → WAITING_ANSWER；当前题保留，提交落对题。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = client.post(
            "/api/v1/voice/sessions", json={"exam_id": exam_id}
        ).json()["session_id"]
        for command in ("start_reading", "question_read"):
            assert client.post(
                f"/api/v1/voice/sessions/{session_id}/commands", json={"type": command}
            ).status_code == 200

        response = client.post(
            f"/api/v1/voice/sessions/{session_id}/commands", json={"type": "barge_in"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["applied_event"] == "barge_in"
        assert body["session"]["status"] == "WAITING_ANSWER"
        assert body["session"]["question_index"] == 0  # 当前题保留

        # 打断后提交答案正常走 propose 路径落库
        assert client.post(
            f"/api/v1/voice/sessions/{session_id}/commands",
            json={"type": "answer_proposed", "question_id": _first_question_id(client, exam_id), "answer": "B"},
        ).status_code == 200
        exam_view = client.get(f"/api/v1/exams/{exam_id}").json()
        assert exam_view["answers"].get(_first_question_id(client, exam_id)) == "B"


def test_announcement_controls_via_commands_do_not_change_state() -> None:
    """pause/resume 命令：状态不变、revision 递增留审计痕迹。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        reading = client.post("/api/v1/voice/sessions", json={"exam_id": exam_id}).json()
        client.post(f"/api/v1/voice/sessions/{reading['session_id']}/commands", json={"type": "start_reading"})
        session_id = reading["session_id"]

        before = client.get(f"/api/v1/voice/sessions/{session_id}").json()
        paused = client.post(
            f"/api/v1/voice/sessions/{session_id}/commands", json={"type": "pause"}
        ).json()
        assert paused["session"]["status"] == "READING_QUESTION"  # 状态不变
        assert paused["session"]["revision"] == before["revision"] + 1  # 审计痕迹

        resumed = client.post(
            f"/api/v1/voice/sessions/{session_id}/commands", json={"type": "resume"}
        )
        assert resumed.status_code == 200
        assert resumed.json()["session"]["status"] == "READING_QUESTION"


def test_barge_in_rejected_outside_announcement_via_api() -> None:
    """SESSION_READY 打断 409（还没开始播报，打断语义不明）。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = client.post("/api/v1/voice/sessions", json={"exam_id": exam_id}).json()["session_id"]
        response = client.post(
            f"/api/v1/voice/sessions/{session_id}/commands", json={"type": "barge_in"}
        )
        assert response.status_code == 409
        assert "barge_in" in response.json()["detail"]
