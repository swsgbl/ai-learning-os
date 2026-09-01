# """M6-05 Security suite: unauthorized access, SSRF, secret leakage,
#
# injection and sandbox escape all have regression tests.
# Probed first (ADR 49-52 style); composes focused regressions across
# the five security surfaces the backlog demands.
# """
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domain.grading import grade_answer, grade_math
from app.domain.web_gate import check_url_safety
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _started_exam(client: TestClient) -> tuple[str, dict]:
    r = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
    exam_id = r.json()["exam_id"]
    return exam_id, client.get(f"/api/v1/exams/{exam_id}").json()


def _voice_session(client: TestClient, exam_id: str) -> str:
    return client.post(
        "/api/v1/voice/sessions", json={"exam_id": exam_id}).json()["session_id"]


def _ready_for_answers(client: TestClient, sid: str) -> None:
    for command in ("start_reading", "question_read", "options_read"):
        assert client.post(
            f"/api/v1/voice/sessions/{sid}/commands", json={"type": command}
        ).status_code == 200


# ---------- A. SSRF: cloud metadata endpoints must be refused ----------


@pytest.mark.parametrize("url,resolver", [
    ("https://169.254.169.254/latest/meta-data/", None),
    ("http://metadata.google.internal/computeMetadata/", lambda h: ["10.0.0.5"]),
    ("https://internal.corp/secret", lambda h: ["172.16.0.9"]),
])
def test_ssrf_metadata_and_rebinding_refused(url: str, resolver) -> None:
    """Cloud metadata IPs and private-resolving hostnames are refused."""
    result = check_url_safety(url, resolver=resolver) if resolver else check_url_safety(url)
    assert result["allowed"] is False
    assert "拒绝" in result["reason"]


# ---------- B. sandbox escape: sympy whitelist holds ----------


@pytest.mark.parametrize("expr", [
    "__import__('os').system('id')",
    "open('/etc/passwd').read()",
    "().__class__.__bases__",
])
def test_math_grader_escape_attempts_land_in_review(expr: str) -> None:
    """Escape payloads never execute: character whitelist routes them to review."""
    assert grade_math("x+1", expr) is None
    assert grade_answer("math", "x+1", expr) is None


def test_sql_metachar_answer_fails_closed() -> None:
    """SQL metachars in answers are literal text: graded wrong, not parsed."""
    assert grade_answer("mcq", "B", "' OR 1=1 --") is False


# ---------- C. injection: API surfaces fail closed ----------


def test_sql_metachar_ids_return_404_and_db_survives() -> None:
    """Metachar ids -> clean 404 everywhere; subsequent requests still work."""
    with TestClient(create_app(SQLITE_URL)) as client:
        bad = "'%20OR%201%3D1%20--"
        for path in (f"/api/v1/resources/{bad}", f"/api/v1/exams/{bad}",
                     "/api/v1/papers/import-drafts/x%27%20OR%201%3D1%20--%27"):
            assert client.get(path).status_code == 404, path
        # int 路径参数：类型校验直接 422 拒绝（同样 fail-closed）
        assert client.get(f"/api/v1/search/queries/{bad}").status_code == 422
        assert client.get("/api/v1/search/providers").status_code == 200


def test_upload_filename_traversal_is_content_addressed() -> None:
    """User-supplied filenames never reach storage paths: keys are sha256 only."""
    with TestClient(create_app(SQLITE_URL)) as client:
        for evil_name in ("../../etc/passwd.json", r"..\..\windows\evil.json",
                          "normal-name.json"):
            r = client.post(
                "/api/v1/resources/upload",
                files={"file": (evil_name, b'[{"a":1}]', "application/json")},
            )
            assert r.status_code == 201
            key = r.json()["storage_key"]
            parts = key.split("/")
            assert len(parts) == 3 and parts[0] == "uploads"
            assert len(parts[1]) == 2 and len(parts[2]) == 64
            assert all(part.isalnum() for part in parts)  # 无任何用户路径成分


def test_broken_json_parse_degrades_safely() -> None:
    """Malformed upload: parse fails with explicit error, never a 500 crash."""
    with TestClient(create_app(SQLITE_URL)) as client:
        r = client.post(
            "/api/v1/resources/upload",
            files={"file": ("broken.json", b"not-json{{{{", "application/json")},
        )
        rid = r.json()["id"]
        p = client.post(f"/api/v1/resources/{rid}/parse")
        assert p.status_code in (200, 409, 422)
        assert p.status_code != 500


# ---------- D. secret leakage: views and repo configs stay clean ----------


SECRET_PATTERNS = ("sk-", "AKIA", "ghp_", "xoxb-", "-----BEGIN")


def test_provider_views_leak_no_secrets() -> None:
    """voice/search provider views expose no credential-like values."""
    with TestClient(create_app(SQLITE_URL)) as client:
        for path in ("/api/v1/voice/providers", "/api/v1/search/providers"):
            text = client.get(path).text
            for pattern in SECRET_PATTERNS:
                assert pattern not in text, f"{path} leaks secret shape {pattern}"


def test_repo_configs_contain_no_real_secrets() -> None:
    """Tracked configs carry only dev placeholders, never production key shapes."""
    targets = [
        REPO_ROOT / ".env.example",
        REPO_ROOT / "infra" / "docker-compose.yml",
        REPO_ROOT / "infra" / "livekit" / "livekit.yaml",
    ]
    for path in targets:
        assert path.exists(), path
        text = path.read_text(encoding="utf-8")
        for pattern in SECRET_PATTERNS:
            assert pattern not in text, f"{path} contains secret shape {pattern}"


# ---------- E. unauthorized access: lifecycle and session boundaries ----------


def test_voice_session_refused_after_exam_submitted() -> None:
    """Exam terminal state: no new voice sessions (409), answers stay sealed."""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id, view = _started_exam(client)
        q1 = view["questions"][0]["id"]
        client.put(f"/api/v1/exams/{exam_id}/answers", json={
            "sequence": 1, "question_id": q1, "answer": "B"})
        assert client.post(f"/api/v1/exams/{exam_id}/submit", json={}).status_code == 200
        r = client.post("/api/v1/voice/sessions", json={"exam_id": exam_id})
        assert r.status_code == 409
        assert "状态" in r.json()["detail"]


def test_cross_session_event_id_replay_refused() -> None:
    """Suite-level guard (M4-05 regression): replay across sessions -> 409."""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id, _ = _started_exam(client)
        sid_a = _voice_session(client, exam_id)
        _ready_for_answers(client, sid_a)
        first = client.post(
            f"/api/v1/voice/sessions/{sid_a}/answers",
            json={"transcript": "选 B", "event_id": "evt-shared-suite"},
        ).json()
        assert first["accepted"] is True
        sid_b = _voice_session(client, exam_id)
        _ready_for_answers(client, sid_b)
        replay = client.post(
            f"/api/v1/voice/sessions/{sid_b}/answers",
            json={"transcript": "选 C", "event_id": "evt-shared-suite"},
        )
        assert replay.status_code == 409
        assert client.get(f"/api/v1/voice/sessions/{sid_b}").json()["status"] == "WAITING_ANSWER"


def test_terminal_drafts_irreversible_across_endpoints() -> None:
    """approve then reject -> 409; reject then approve -> 409 (both directions)."""
    with TestClient(create_app(SQLITE_URL)) as client:
        for final_call in ("approve", "reject"):
            r = client.post(
                "/api/v1/resources/upload",
                files={"file": ("doc.json", b'[{"q":"x"}]', "application/json")},
            )
            rid = r.json()["id"]
            assert client.post(f"/api/v1/resources/{rid}/parse").status_code == 200
            draft = client.post(
                "/api/v1/papers/import-drafts", json={"resource_id": rid}).json()
            assert client.post(
                f"/api/v1/papers/import-drafts/{draft['id']}/{final_call}", json={}
            ).status_code == 200
            other = "reject" if final_call == "approve" else "approve"
            assert client.post(
                f"/api/v1/papers/import-drafts/{draft['id']}/{other}", json={}
            ).status_code == 409

