"""M6-02 Voice eval set：六类语音条件评测 + parser 缺口修复回归。

- 域层：覆盖完整性、全量 accuracy==1.0、确定性字节级一致、
  mismatch 注入透明、空集拒绝、探针缺口修复回归（选的是A/选项再念一遍）；
- API：report 无 DB 恒可用、runs 落库回查、无 DB 503。
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.domain.intent_parser import parse
from app.domain.voice_eval import GOLDEN_VOICE_CASES, VOICE_CATEGORIES, run_voice_eval
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def test_coverage_and_structure() -> None:
    """30 案唯一 id、六类全覆盖（噪声/口音/打断/数字/公式/命令）。"""
    ids = [c[0] for c in GOLDEN_VOICE_CASES]
    assert len(ids) == 30 and len(set(ids)) == 30
    cats = {c[1] for c in GOLDEN_VOICE_CASES}
    assert cats == set(VOICE_CATEGORIES)


def test_full_pass_accuracy_one() -> None:
    """全案命中：accuracy==1.0、无 mismatch、per_category 全 matched。"""
    report = run_voice_eval()
    assert report["accuracy"] == 1.0
    assert report["mismatches"] == []
    assert report["coverage_complete"] is True
    assert all(v["matched"] == v["total"] for v in report["per_category"].values())


def test_deterministic_report() -> None:
    """同输入两次运行报告字节级一致（无时间戳无随机）。"""
    a = json.dumps(run_voice_eval(), ensure_ascii=False, sort_keys=True)
    b = json.dumps(run_voice_eval(), ensure_ascii=False, sort_keys=True)
    assert a == b


def test_mismatch_transparent() -> None:
    """注入坏 case：accuracy 下降且 mismatch 全字段透出（不虚报）。"""
    bad_case = ("V-x", "noise", "选B", "end", None, None, False)
    injected = tuple(list(GOLDEN_VOICE_CASES) + [bad_case])
    report = run_voice_eval(injected)
    assert report["accuracy"] == 30 / 31
    assert len(report["mismatches"]) == 1
    m = report["mismatches"][0]
    assert m["case_id"] == "V-x" and m["category"] == "noise"
    assert m["transcript"] == "选B"
    assert m["expected"]["intent"] == "end"
    assert m["actual"]["intent"] == "choose_option" and m["actual"]["letter"] == "B"


def test_empty_rejected() -> None:
    """空评测集拒绝。"""
    import pytest
    with pytest.raises(ValueError):
        run_voice_eval(())


def test_parser_gap_fix_regressions() -> None:
    """探针缺口修复回归：选的是A->choose A、选项再念一遍->repeat_options、
    重复选项不回归。"""
    r = parse("等等 我选的是A")
    assert (r.intent, r.letter, r.ordinal, r.ambiguous) == ("choose_option", "A", None, False)
    r2 = parse("选项再念一遍")
    assert (r2.intent, r2.letter, r2.ordinal, r2.ambiguous) == ("repeat_options", None, None, False)
    r3 = parse("重复选项")
    assert r3.intent == "repeat_options"


def test_api_report_no_db_200_and_deterministic() -> None:
    """report 无 DB 恒可用 200，两次一致。"""
    with TestClient(create_app(None)) as client:
        r1 = client.get("/api/v1/eval/voice/report")
        r2 = client.get("/api/v1/eval/voice/report")
        assert r1.status_code == r2.status_code == 200
        assert r1.json() == r2.json()
        assert r1.json()["accuracy"] == 1.0
        assert r1.json()["coverage_complete"] is True
        assert r1.json()["rule_versions"]["eval"] == "voice-eval-v1"


def test_api_run_record_and_query() -> None:
    """POST runs 落库(kind=voice) -> GET runs -> 详情一致 -> 404。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        r = client.post("/api/v1/eval/voice/runs")
        assert r.status_code == 201, r.text
        run = r.json()
        assert run["kind"] == "voice"
        assert run["case_count"] == 30
        assert run["agreement"] == 1.0  # agreement 列承载 accuracy
        assert run["rule_version"] == "voice-eval-v1"
        assert run["report"]["coverage_complete"] is True

        listed = client.get("/api/v1/eval/voice/runs").json()
        assert any(item["id"] == run["id"] for item in listed)

        detail = client.get(f"/api/v1/eval/voice/runs/{run['id']}").json()
        assert detail["created_at"][:19] == run["created_at"][:19]
        assert {k: v for k, v in detail.items() if k != "created_at"} == {
            k: v for k, v in run.items() if k != "created_at"
        }

        assert client.get("/api/v1/eval/voice/runs/99999").status_code == 404


def test_api_runs_no_db_503() -> None:
    """落库类端点无 DB 503（report 不受影响）。"""
    with TestClient(create_app(None)) as client:
        assert client.post("/api/v1/eval/voice/runs").status_code == 503
        assert client.get("/api/v1/eval/voice/runs").status_code == 503
