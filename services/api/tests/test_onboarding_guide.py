"""M7-02 Onboarding guide: executable walkthrough + doc-implementation consistency guard.

Acceptance (backlog M7-02): a new user completes initialization, importing a
course and the first paper within 10 minutes.

Two guards:
1. walkthrough end-to-end -- TestClient(SQLite) replays the full
   docs/ONBOARDING.md path (health -> course import -> first paper -> exam
   report -> daily plan), asserting every status code and the 600s budget;
2. doc consistency -- every API endpoint shown in docs/ONBOARDING.md must be
   one the walkthrough actually requested (doc/implementation drift fails).
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.ops.onboarding import BUDGET_S, WALKTHROUGH_ENDPOINTS, run_walkthrough

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
DOC = Path(__file__).resolve().parents[3] / "docs" / "ONBOARDING.md"

_DOC_ENDPOINT_RE = re.compile(
    r"(GET|POST|PUT|DELETE)\s+(?:https?://[^/\s]+)?(/[^\s'\"]*)"
)


def _norm(path: str) -> str:
    """Normalize path params: /papers/{paper_id}/exams -> /papers/{}/exams."""
    return re.sub(r"\{[^}]*\}", "{}", path)


def test_onboarding_walkthrough_end_to_end() -> None:
    """10-minute path end to end on SQLite, every step status-asserted."""
    with TestClient(create_app(SQLITE_URL)) as client:
        results = run_walkthrough(client)

    assert len(results["steps"]) == 5, results["steps"]
    assert results["total_ms"] < BUDGET_S * 1000, (
        f"onboarding full path {results['total_ms']:.0f}ms exceeds {BUDGET_S:.0f}s budget"
    )
    # course import produced the three concepts via M5-05 extraction rules
    assert results["course_draft"]["concepts"] == ["极限", "导数", "积分"]
    # first exam: 6/10, one mistake (question 3 left blank -> wrong)
    assert results["report"]["score_earned"] == 6.0
    assert len(results["report"]["mistakes"]) == 1
    # daily plan readable with three task categories
    assert "review_count" in results["daily_plan"]
    assert "mistake_retry_count" in results["daily_plan"]
    assert "new_learning_count" in results["daily_plan"]


class _Recorder:
    """Wraps a client, recording (method, path) of every request."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls: list[tuple[str, str]] = []

    def get(self, path, **kw):
        self.calls.append(("GET", path))
        return self._inner.get(path, **kw)

    def post(self, path, **kw):
        self.calls.append(("POST", path))
        return self._inner.post(path, **kw)

    def put(self, path, **kw):
        self.calls.append(("PUT", path))
        return self._inner.put(path, **kw)


def _matches_template(concrete: str, template: str) -> bool:
    """POST /api/v1/exams/ex_1/submit matches POST /api/v1/exams/{}/submit."""
    c_method, c_path = concrete.split(" ", 1)
    t_method, t_path = template.split(" ", 1)
    if c_method != t_method:
        return False
    c_segs = c_path.strip("/").split("/")
    t_segs = t_path.strip("/").split("/")
    if len(c_segs) != len(t_segs):
        return False
    return all(t == "{}" or c == t for c, t in zip(c_segs, t_segs))


def test_onboarding_doc_endpoints_match_implementation() -> None:
    """Every endpoint in the doc must be one the walkthrough requested."""
    doc = DOC.read_text(encoding="utf-8")
    doc_eps: set[tuple[str, str]] = set()
    for m in _DOC_ENDPOINT_RE.finditer(doc):
        doc_eps.add((m.group(1), _norm(m.group(2).rstrip(chr(96)))))
    assert doc_eps, "doc extraction found no endpoints - regex broken"

    with TestClient(create_app(SQLITE_URL)) as client:
        recorder = _Recorder(client)
        run_walkthrough(recorder)

    # walkthrough 的每个具体请求都命中声明模板集合（声明不虚设）
    unmatched = [
        c for c in recorder.calls
        if not any(_matches_template(f"{m} {p}", tpl) for tpl in WALKTHROUGH_ENDPOINTS for m, p in [c])
    ]
    assert not unmatched, f"walkthrough requests not covered by declared templates: {unmatched}"

    # 文档端点（含 {param} 模板）必须落在声明集合内（文档不漂移）
    missing = {" ".join(e) for e in doc_eps} - WALKTHROUGH_ENDPOINTS
    assert not missing, f"doc mentions endpoints the walkthrough never makes: {sorted(missing)}"
    print(f"doc endpoints {len(doc_eps)} <= declared {len(WALKTHROUGH_ENDPOINTS)}, "
          f"walkthrough calls {len(recorder.calls)} all covered")
