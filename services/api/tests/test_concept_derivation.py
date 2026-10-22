"""M17-001 概念推导域层测试：格式校验/去重/上限/失败语义。"""
from __future__ import annotations

import json

import pytest

from app.domain.concept_derivation import (
    MAX_DERIVED_CONCEPTS,
    ConceptDerivationUnavailable,
    derive_concepts_from_outcomes,
)
from app.llm.gateway import LlmUnavailable

CONFIG = ("http://stub.local/v1", "sk-stub", "stub-model")


class _StubGateway:
    """按调用序返回预置响应（推导是单次调用）。"""

    def __init__(self, *, response: str | Exception) -> None:
        self._response = response

    def chat(self, messages, temperature=0.0, max_tokens=1024):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _patch_gateway(monkeypatch, response: str | Exception) -> None:
    import app.domain.concept_derivation as cd

    monkeypatch.setattr(
        cd, "LlmGateway", lambda **kwargs: _StubGateway(response=response)
    )


def test_derives_concepts_from_outcomes(monkeypatch) -> None:
    body = json.dumps(
        {
            "concepts": [
                {"id": "sql:group-by", "name": "分组聚合"},
                {"id": "sql:having", "name": "聚合过滤"},
            ]
        },
        ensure_ascii=False,
    )
    _patch_gateway(monkeypatch, body)
    derived = derive_concepts_from_outcomes(["能写带 HAVING 的聚合查询"], CONFIG)
    assert [c.concept_id for c in derived] == ["sql:group-by", "sql:having"]
    assert derived[0].display_name == "分组聚合"


def test_rejects_malformed_ids(monkeypatch) -> None:
    body = json.dumps(
        {
            "concepts": [
                {"id": "SQL Group", "name": "大写空格非法"},
                {"id": "sql:group-by", "name": "合法"},
            ]
        }
    )
    _patch_gateway(monkeypatch, body)
    derived = derive_concepts_from_outcomes(["目标"], CONFIG)
    assert [c.concept_id for c in derived] == ["sql:group-by"]


def test_dedupes_and_caps_concept_count(monkeypatch) -> None:
    entries = [
        {"id": f"sql:concept-{i}", "name": f"概念{i}"} for i in range(8)
    ] + [{"id": "sql:concept-0", "name": "重复"}]
    _patch_gateway(monkeypatch, json.dumps({"concepts": entries}, ensure_ascii=False))
    derived = derive_concepts_from_outcomes(["目标"], CONFIG)
    assert len(derived) == MAX_DERIVED_CONCEPTS
    assert len({c.concept_id for c in derived}) == MAX_DERIVED_CONCEPTS


def test_all_invalid_raises(monkeypatch) -> None:
    _patch_gateway(monkeypatch, json.dumps({"concepts": [{"id": "bad id", "name": ""}]}))
    with pytest.raises(ConceptDerivationUnavailable, match="全部未通过校验"):
        derive_concepts_from_outcomes(["目标"], CONFIG)


def test_unparseable_output_raises(monkeypatch) -> None:
    _patch_gateway(monkeypatch, "这不是 JSON")
    with pytest.raises(ConceptDerivationUnavailable, match="不是可解析"):
        derive_concepts_from_outcomes(["目标"], CONFIG)


def test_llm_failure_wrapped(monkeypatch) -> None:
    _patch_gateway(monkeypatch, LlmUnavailable("端点 HTTP 503"))
    with pytest.raises(ConceptDerivationUnavailable, match="LLM 调用失败"):
        derive_concepts_from_outcomes(["目标"], CONFIG)


def test_empty_outcomes_rejected(monkeypatch) -> None:
    with pytest.raises(ConceptDerivationUnavailable, match="无能力目标"):
        derive_concepts_from_outcomes([], CONFIG)


def test_missing_config_rejected(monkeypatch) -> None:
    with pytest.raises(ConceptDerivationUnavailable, match="未配置 LLM"):
        derive_concepts_from_outcomes(["目标"], (None, None, None))


def test_code_fenced_json_accepted(monkeypatch) -> None:
    body = "```json\n" + json.dumps({"concepts": [{"id": "stat:p-value", "name": "P 值"}]}) + "\n```"
    _patch_gateway(monkeypatch, body)
    derived = derive_concepts_from_outcomes(["能解释 P 值"], CONFIG)
    assert derived[0].concept_id == "stat:p-value"


def test_colonless_slug_accepted(monkeypatch) -> None:
    """live 实测回填：qwen3.5 常产出无冒号 slug（sql-aggregation）——接受。"""
    body = json.dumps(
        {
            "concepts": [
                {"id": "sql-aggregation", "name": "聚合函数"},
                {"id": "sql:grouping", "name": "分组操作"},
            ]
        },
        ensure_ascii=False,
    )
    _patch_gateway(monkeypatch, body)
    derived = derive_concepts_from_outcomes(["能写聚合查询"], CONFIG)
    assert [c.concept_id for c in derived] == ["sql-aggregation", "sql:grouping"]
