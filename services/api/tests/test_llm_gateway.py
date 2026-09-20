"""M10-01 LLM 接入：gateway 协议 + rubric LLM judge fail-closed + 装配两态。

诚实边界（ADR 65）：本套件全部使用注入的 fake transport——验证协议格式、
响应解包与失败语义；不发起真实网络调用（真实端点冒烟需要部署 key，
由 infra/smoke_llm.sh 在 key 提供后执行）。
"""
from __future__ import annotations

import json

import pytest

from app.domain.rubric_grader import make_rubric_judge
from app.llm.gateway import ChatMessage, LlmGateway, LlmUnavailable
from app.llm.rubric_judge import LlmRubricJudge, _strip_and_parse, build_llm_judge


class FakeTransport:
    """脚本化响应队列：每次 post_json 弹出一个结果（status, body）或异常。"""

    def __init__(self, *results) -> None:
        self._results = list(results)
        self.calls: list[dict] = []

    def post_json(self, url: str, *, headers: dict[str, str], payload: dict) -> tuple[int, object]:
        self.calls.append({"url": url, "headers": headers, "payload": payload})
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result  # type: ignore[return-value]


def _gateway(transport: FakeTransport) -> LlmGateway:
    return LlmGateway(
        endpoint="http://llm.test/v1", api_key="test-key", model="test-model",
        transport=transport,
    )


def _ok_body(content: str) -> tuple[int, object]:
    return 200, {"choices": [{"message": {"role": "assistant", "content": content}}]}


_VALID_JSON = json.dumps(
    {
        "criteria": [
            {"point": "提到分治", "achieved": True, "evidence_id": None},
            {"point": "给出复杂度", "achieved": None, "evidence_id": None},
        ],
        "confidence": 0.8,
    },
    ensure_ascii=False,
)


# --- gateway 协议 ---


def test_gateway_sends_openai_compatible_payload_and_unwraps_content() -> None:
    transport = FakeTransport(_ok_body("判定文本"))
    gateway = _gateway(transport)
    out = gateway.chat((ChatMessage(role="user", content="你好"),), temperature=0.0)
    assert out == "判定文本"
    call = transport.calls[0]
    assert call["url"] == "http://llm.test/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer test-key"
    assert call["payload"]["model"] == "test-model"
    assert call["payload"]["messages"] == [{"role": "user", "content": "你好"}]


def test_gateway_maps_all_failures_to_llm_unavailable() -> None:
    cases = [
        FakeTransport((500, {"error": "boom"})),          # HTTP 500
        FakeTransport((200, {"choices": []})),            # 空 choices
        FakeTransport((200, {"unexpected": True})),       # 缺字段
        FakeTransport((200, "not-json-compatible")),      # 解包 TypeError
        FakeTransport(LlmUnavailable("网络中断")),          # 传输层异常
    ]
    for transport in cases:
        with pytest.raises(LlmUnavailable):
            _gateway(transport).chat((ChatMessage(role="user", content="q"),))


def test_gateway_requires_full_config() -> None:
    for kwargs in (
        {"endpoint": "", "api_key": "k", "model": "m"},
        {"endpoint": "http://x", "api_key": "", "model": "m"},
        {"endpoint": "http://x", "api_key": "k", "model": ""},
    ):
        with pytest.raises(ValueError):
            LlmGateway(**kwargs)  # type: ignore[arg-type]


# --- M14-71 num_ctx 请求级窗口提示 ---


def test_gateway_payload_omits_options_when_num_ctx_unset() -> None:
    """默认（None）payload 不带 options 键——与既有形态逐字节一致。"""
    transport = FakeTransport(_ok_body("ok"))
    _gateway(transport).chat((ChatMessage(role="user", content="q"),))
    assert "options" not in transport.calls[0]["payload"]


def test_gateway_payload_carries_options_num_ctx_when_set() -> None:
    transport = FakeTransport(_ok_body("ok"))
    LlmGateway(
        endpoint="http://llm.test/v1", api_key="k", model="m",
        transport=transport, num_ctx=4096,
    ).chat((ChatMessage(role="user", content="q"),))
    assert transport.calls[0]["payload"]["options"] == {"num_ctx": 4096}


@pytest.mark.parametrize("bad", [0, -1, True, "512", 3.5])
def test_gateway_rejects_invalid_num_ctx(bad) -> None:
    with pytest.raises(ValueError, match="num_ctx"):
        LlmGateway(
            endpoint="http://llm.test/v1", api_key="k", model="m", num_ctx=bad
        )


def test_settings_blank_llm_num_ctx_is_none() -> None:
    """compose `${AIOS_LLM_NUM_CTX:-}` 空串形态归一为 None（未配置语义）。"""
    from app.core.config import Settings

    for raw in (None, "", "   "):
        settings = Settings(_env_file=None, llm_num_ctx=raw)
        assert settings.llm_num_ctx is None


def test_settings_accepts_valid_and_rejects_invalid_llm_num_ctx() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    assert Settings(_env_file=None, llm_num_ctx="4096").llm_num_ctx == 4096
    for raw in ("0", "-1", "abc", "true", "3.5"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, llm_num_ctx=raw)


def test_build_llm_judge_passes_num_ctx_through(monkeypatch) -> None:
    """build_llm_judge 把 num_ctx 透传进 LlmGateway 构造（零网络验证）。"""
    captured: dict = {}

    class _SpyGateway:
        def __init__(self, *, endpoint, api_key, model, num_ctx=None) -> None:
            captured.update(
                endpoint=endpoint, api_key=api_key, model=model, num_ctx=num_ctx
            )

    monkeypatch.setattr("app.llm.rubric_judge.LlmGateway", _SpyGateway)
    judge = build_llm_judge(
        {"endpoint": "http://x/v1", "api_key": "k", "model": "m", "num_ctx": 2048}
    )
    assert judge is not None
    assert captured["num_ctx"] == 2048


def test_build_llm_judge_omits_num_ctx_when_unset(monkeypatch) -> None:
    captured: dict = {}

    class _SpyGateway:
        def __init__(self, *, endpoint, api_key, model, num_ctx=None) -> None:
            captured.update(num_ctx=num_ctx)

    monkeypatch.setattr("app.llm.rubric_judge.LlmGateway", _SpyGateway)
    assert (
        build_llm_judge(
            {"endpoint": "http://x/v1", "api_key": "k", "model": "m"}
        )
        is not None
    )
    assert captured["num_ctx"] is None


def test_build_llm_judge_num_ctx_alone_does_not_assemble() -> None:
    """num_ctx 不是装配条件：endpoint/key/model 任一缺席仍返回 None。"""
    assert (
        build_llm_judge(
            {"endpoint": None, "api_key": None, "model": None, "num_ctx": 4096}
        )
        is None
    )


# --- rubric LLM judge fail-closed ---


def _judge(transport: FakeTransport) -> LlmRubricJudge:
    return LlmRubricJudge(_gateway(transport))


def test_judge_parses_valid_model_output_with_server_side_provenance() -> None:
    judge = _judge(FakeTransport(_ok_body(_VALID_JSON)))
    judgement = judge.judge("简述快速排序", ("提到分治", "给出复杂度"), "分治递归排序")
    assert judgement is not None
    assert judgement.judge_model == "test-model"  # 服务端覆写，不信任模型自报
    assert judgement.prompt_hash
    assert [c.achieved for c in judgement.criteria] == [True, None]


def test_judge_strips_markdown_fence() -> None:
    fenced = f"```json\n{_VALID_JSON}\n```"
    assert _strip_and_parse(fenced) is not None


def test_judge_returns_none_on_any_failure_never_scores() -> None:
    """LLM 不可用 / 响应垃圾 / 结构非法 / 模型编造 evidence -> 一律 None（复核）。"""
    cases = [
        FakeTransport(LlmUnavailable("超时")),
        FakeTransport(_ok_body("对不起，我不知道")),
        FakeTransport(_ok_body(json.dumps({"criteria": "not-a-list", "confidence": 1}))),
        FakeTransport(_ok_body(json.dumps({"criteria": [], "confidence": 0.5}))),
    ]
    for transport in cases:
        assert _judge(transport).judge("stem", ("点1",), "answer") is None


def test_judge_nulls_fabricated_evidence_ids() -> None:
    """LLM 不持有 evidence 库：编造 evidence_id 一律置空（evidence gate）。"""
    fabricated = json.dumps(
        {
            "criteria": [{"point": "p", "achieved": True, "evidence_id": "ev_999"}],
            "confidence": 0.9,
        }
    )
    judgement = _judge(FakeTransport(_ok_body(fabricated))).judge("s", ("p",), "a")
    assert judgement is not None
    assert all(c.evidence_id is None for c in judgement.criteria)


# --- 装配两态 ---


def test_build_llm_judge_requires_all_slots() -> None:
    assert build_llm_judge({"endpoint": None, "api_key": None, "model": None}) is None
    assert build_llm_judge({"endpoint": "http://x", "api_key": None, "model": "m"}) is None


def test_make_rubric_judge_keeps_keyword_and_unknown_names() -> None:
    assert make_rubric_judge("keyword") is not None
    assert make_rubric_judge("nonsense") is None
    assert make_rubric_judge(None) is None


def test_llm_judge_end_to_end_needs_second_judge_when_model_absent() -> None:
    """rubric_judge=llm + 无 key -> judge=None -> essay 全部进复核（fail-closed 端到端）。"""
    from app.domain.rubric_grader import judge_question

    correct, result = judge_question(
        "essay", "stem", json.dumps({"rubric_points": ["点1"]}), "answer",
        rubric_judge=None,
    )
    assert correct is None  # 三态：不确定 -> 复核队列
    assert result is not None
    assert result.score_ratio is None  # 模型缺席绝不产生分数
