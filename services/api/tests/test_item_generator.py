"""M16-007 LLM 客观题生成器域层测试（升级方案 Prompt 3 题目生成校验）。

覆盖：合法题解析与 key 映射；schema 各失败维度逐条丢弃（选项数/空题干/
重复选项/answer 越界/未知概念）；同批题干去重；每概念数量上限；LLM
不可用/输出不可解析如实抛错；markdown 围栏剥离。
"""
from __future__ import annotations

import pytest

from app.domain.item_generator import (
    GeneratedItem,
    LlmItemGenerationUnavailable,
    _parse_json,
    _validate_item,
    generate_objective_items,
)

CONFIG = ("http://llm.local/v1", "sk-test-1234567890", "test-model")
CONCEPTS = {"SELECT 基础查询": "sql:select-basics", "表连接 JOIN": "sql:join"}


def _raw(stem="以下哪个语句用于查询数据？", concept="SELECT 基础查询", answer_index=0, options=None, difficulty=2) -> dict:
    return {
        "concept": concept,
        "stem": stem,
        "options": options or ["SELECT", "INSERT", "UPDATE", "DELETE"],
        "answer_index": answer_index,
        "difficulty": difficulty,
        "why": "SELECT 用于查询",
    }


def test_validate_item_happy_path() -> None:
    item = _validate_item(_raw(), CONCEPTS)
    assert isinstance(item, GeneratedItem)
    assert item.concept_id == "sql:select-basics"
    assert item.answer == "A"
    assert [o["key"] for o in item.options] == ["A", "B", "C", "D"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(stem="  "),
        lambda r: r.update(options=["只有一个选项"]),
        lambda r: r.update(options=["A", "A", "B", "C"]),  # 重复选项
        lambda r: r.update(answer_index=4),
        lambda r: r.update(answer_index="A"),  # 非整数
        lambda r: r.update(concept="不存在的概念"),
        lambda r: r.update(stem=None),
    ],
)
def test_validate_item_rejects_broken_schema(mutate) -> None:
    raw = _raw()
    mutate(raw)
    assert _validate_item(raw, CONCEPTS) is None


def test_difficulty_out_of_range_defaults_to_3() -> None:
    raw = _raw(difficulty=99)
    item = _validate_item(raw, CONCEPTS)
    assert item is not None and item.difficulty == 3


def test_generation_via_stub_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """打桩 LlmGateway.chat：合法+非法混合批——合法入库、非法计数丢弃。"""
    from app.llm import gateway as gw

    payload = {
        "items": [
            _raw(stem="题 1"),
            _raw(stem="题 1"),  # 重复题干 → 丢弃
            _raw(stem="题 2", concept="表连接 JOIN"),
            {**_raw(stem="题 3"), "options": ["只有一个"]},  # schema 丢弃
            _raw(stem="题 4"),  # 超每概念上限（per_concept=1）→ 丢弃
        ]
    }
    import json as jsonlib

    class _StubGateway:
        def __init__(self, **kwargs: object) -> None:
            pass

        def chat(self, messages: object, temperature: float = 0.0) -> str:
            return jsonlib.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(gw, "LlmGateway", _StubGateway)
    import app.domain.item_generator as ig

    monkeypatch.setattr(ig, "LlmGateway", _StubGateway)
    report = generate_objective_items(CONCEPTS, per_concept=1, config=CONFIG)
    assert len(report.items) == 2  # select 1 题 + join 1 题
    assert {i.concept_id for i in report.items} == {"sql:select-basics", "sql:join"}
    assert report.raw_count == 5
    assert report.dropped_count == 3


def test_unparseable_output_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.domain.item_generator as ig

    class _BadGateway:
        def __init__(self, **kwargs: object) -> None:
            pass

        def chat(self, messages: object, temperature: float = 0.0) -> str:
            return "不是 JSON"

    monkeypatch.setattr(ig, "LlmGateway", _BadGateway)
    with pytest.raises(LlmItemGenerationUnavailable, match="不是可解析"):
        generate_objective_items(CONCEPTS, per_concept=2, config=CONFIG)


def test_no_llm_config_raises() -> None:
    with pytest.raises(LlmItemGenerationUnavailable, match="未配置 LLM"):
        generate_objective_items(CONCEPTS, per_concept=2, config=(None, None, None))


def test_all_invalid_items_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import json as jsonlib

    import app.domain.item_generator as ig

    class _AllBadGateway:
        def __init__(self, **kwargs: object) -> None:
            pass

        def chat(self, messages: object, temperature: float = 0.0) -> str:
            return jsonlib.dumps({"items": [{"bogus": True}]}, ensure_ascii=False)

    monkeypatch.setattr(ig, "LlmGateway", _AllBadGateway)
    with pytest.raises(LlmItemGenerationUnavailable, match="全部未通过校验"):
        generate_objective_items(CONCEPTS, per_concept=2, config=CONFIG)


def test_parse_json_strips_fences() -> None:
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json('{"a": 1}') == {"a": 1}
    assert _parse_json("垃圾") is None
