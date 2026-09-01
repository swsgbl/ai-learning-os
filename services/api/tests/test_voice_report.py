"""M4-08 Voice report：REPORT_READY 后的语音播报投影。

- 域层：build_voice_report 只投影不判定（分数/错题/补救来自 M2-11 判分）、
  spoken_text 短结论+总分、错题摘要字段完整、补救聚合到概念级、确定性；
- API：REPORT_READY 终态后 200、非终态 409、未提交判分 409、404/503、
  幂等恒同、错题讲解不泄露其他学生数据（单考试域）。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.report import (
    ConceptScore,
    ExamReport,
    MistakeEntry,
    RemediationTask,
    ReportItem,
)
from app.domain.voice_report import build_voice_report
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _report(
    *,
    mistakes: int = 1,
    score: int = 60,
    correct: int = 3,
    total: int = 5,
) -> ExamReport:
    """构造最小 ExamReport：mistakes 道错题 + (total-mistakes) 道对题。"""
    items = []
    mists = []
    for i in range(total):
        qid = f"q{i + 1}"
        is_wrong = i < mistakes
        items.append(
            ReportItem(
                question_id=qid,
                sequence=i + 1,
                question_type="mcq",
                stem=f"题目{i + 1}" + "很长" * 30,
                given="A" if is_wrong else "B",
                expected="B",
                correct=not is_wrong,
                score=0.0 if is_wrong else 1.0,
                max_score=1.0,
                explanation=f"解析{i + 1}",
                knowledge=("函数" if is_wrong else "导数",),
                evidence_ids=(),
                score_ratio=None,
            )
        )
        if is_wrong:
            mists.append(
                MistakeEntry(
                    question_id=qid,
                    stem=f"题目{i + 1}" + "很长" * 30,
                    given="A",
                    expected="B",
                    explanation=f"解析{i + 1}",
                    diagnosis="概念不清",
                    knowledge=("函数",),
                    evidence_ids=(),
                    remediation_task_ids=(0,),
                )
            )
    return ExamReport(
        exam_id="exam_x",
        paper_title="测试卷",
        mode="exam",
        score=score,
        score_earned=float(correct),
        score_max=float(total),
        correct_count=correct,
        total_count=total,
        reviewed_count=0,
        items=tuple(items),
        concepts=(
            ConceptScore(concept="函数", correct=0, total=mistakes, reviewed=0, ratio=0.0),
        ),
        mistakes=tuple(mists),
        remediation_tasks=(
            RemediationTask(
                kind="review_concept",
                title="复习 函数",
                detail="复习概念「函数」后完成变式练习",
                question_id=mists[0].question_id if mists else "q1",
            ),
        ),
        evidence_ids=(),
    )


# ---------- 域层 ----------


def test_spoken_text_first_pass_short_conclusion() -> None:
    """第一遍播报=短结论+总分+错题数（05 文档分层播报，避免一次性读长报告）。"""
    view = build_voice_report("exam_x", _report(mistakes=2, score=60, correct=3, total=5))
    text = view["spoken_text"]
    assert "60" in text and "答对 3" in text and "2 道错题" in text
    assert len(text) < 120  # 短结论：不展开逐题讲解
    assert view["written_report_url"] == "/api/v1/exams/{id}/report".replace("{id}", "exam_x")


def test_mistake_summary_fields() -> None:
    """错题摘要：题号/截断题干/你的答案/正确答案/概念/错因。"""
    view = build_voice_report("exam_x", _report(mistakes=1))
    assert len(view["mistake_summary"]) == 1
    m = view["mistake_summary"][0]
    assert m["question_id"] == "q1"
    assert m["stem_preview"].endswith("…")  # 长题干截断
    assert m["your_answer"] == "A" and m["correct_answer"] == "B"
    assert m["concepts"] == ["函数"]
    assert m["diagnosis"] == "概念不清"


def test_perfect_score_has_empty_summaries() -> None:
    """全对：spoken 含恭喜、摘要为空、补救为空。"""
    view = build_voice_report("exam_x", _report(mistakes=0, score=100, correct=5, total=5))
    assert "恭喜" in view["spoken_text"]
    assert view["mistake_summary"] == []
    assert view["remediation_summary"] == []


def test_remediation_aggregates_by_concept() -> None:
    """补救建议聚合到概念级：同概念多任务合并为一条 actions。"""
    view = build_voice_report("exam_x", _report(mistakes=2))
    remed = view["remediation_summary"]
    assert len(remed) == 1  # 两道错题同概念「函数」→ 聚合一条
    assert remed[0]["concept"] == "函数"
    assert remed[0]["actions"] == ["review_concept"]
    assert len(remed[0]["question_ids"]) == 2


def test_voice_report_is_deterministic() -> None:
    """同 (报告, exam_id) 恒同输出（幂等语义）。"""
    report = _report(mistakes=2)
    assert build_voice_report("exam_x", report) == build_voice_report("exam_x", report)


# ---------- API ----------


def _start_exam(client: TestClient) -> str:
    response = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
    assert response.status_code == 201
    return response.json()["exam_id"]


def _terminal_session(client: TestClient, exam_id: str) -> str:
    """推进语音会话到 REPORT_READY 并提交考试判分。"""
    session_id = client.post("/api/v1/voice/sessions", json={"exam_id": exam_id}).json()["session_id"]
    url = f"/api/v1/voice/sessions/{session_id}/commands"
    for command in ("start_reading", "question_read", "options_read"):
        assert client.post(url, json={"type": command}).status_code == 200
    question_id = client.get(f"/api/v1/exams/{exam_id}").json()["questions"][0]["id"]
    assert client.post(
        url, json={"type": "answer_proposed", "question_id": question_id, "answer": "B"}
    ).status_code == 200
    assert client.post(url, json={"type": "end"}).status_code == 200  # → REPORT_READY
    assert client.post(f"/api/v1/exams/{exam_id}/submit", json={}).status_code == 200
    return session_id


def test_report_after_terminal_and_submit() -> None:
    """终态+已提交：报告含 spoken_text 总分、错题摘要、补救建议、书面报告链接。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = _terminal_session(client, exam_id)
        body = client.get(f"/api/v1/voice/sessions/{session_id}/report").json()
        assert body["session_id"] == session_id and body["exam_id"] == exam_id
        assert "得分" in body["spoken_text"] and "分" in body["spoken_text"]
        assert body["written_report_url"] == f"/api/v1/exams/{exam_id}/report"
        # 书面报告可访问（深入讲解路径）
        written = client.get(body["written_report_url"])
        assert written.status_code == 200
        assert str(written.json()["score"]) == body["spoken_text"].split("得分 ")[1].split(" 分")[0]


def test_report_idempotent_across_calls() -> None:
    """幂等只读：同状态多次获取恒同输出。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = _terminal_session(client, exam_id)
        first = client.get(f"/api/v1/voice/sessions/{session_id}/report")
        second = client.get(f"/api/v1/voice/sessions/{session_id}/report")
        assert first.json() == second.json()


def test_report_non_terminal_409() -> None:
    """非终态 409：全卷完成后才可获取报告。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = client.post("/api/v1/voice/sessions", json={"exam_id": exam_id}).json()["session_id"]
        assert client.get(f"/api/v1/voice/sessions/{session_id}/report").status_code == 409


def test_report_without_grading_409() -> None:
    """REPORT_READY 但未提交判分：409 提示先提交。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = client.post("/api/v1/voice/sessions", json={"exam_id": exam_id}).json()["session_id"]
        url = f"/api/v1/voice/sessions/{session_id}/commands"
        for command in ("start_reading", "question_read", "options_read"):
            client.post(url, json={"type": command})
        client.post(url, json={"type": "end"})
        assert client.get(f"/api/v1/voice/sessions/{session_id}/report").status_code == 409


def test_report_404_and_503() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        assert client.get("/api/v1/voice/sessions/vs_missing/report").status_code == 404

    with TestClient(create_app(None)) as client:
        assert client.get("/api/v1/voice/sessions/vs_x/report").status_code == 503
