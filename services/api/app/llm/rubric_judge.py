"""M10-01 rubric LLM judge：实现 RubricJudge 协议的模型判分（prompt pack E 同款 schema）。

fail-closed（ADR 65）：LLM 不可用 / 响应非法 / 结构校验不过 → judge() 返回 None，
沿 M2-10 管线语义进复核——模型缺席绝不产生分数，绝不猜分。judge_model 与
prompt_hash 由服务端覆写（不信任模型自报），判分留痕随 submission 可回放。
"""
from __future__ import annotations

import hashlib
from dataclasses import replace

from app.domain.rubric_grader import RubricCriterion, RubricJudgement, parse_judgement
from app.llm.gateway import ChatMessage, LlmGateway, LlmUnavailable

_SYSTEM_PROMPT = """你是严格的结构化阅卷器。只输出一个 JSON 对象，不要任何其他文本。
JSON schema：
{"criteria": [{"point": <评分点原文>, "achieved": true|false|null, "evidence_id": null}, ...], "confidence": <0到1的小数>}
规则：
1. criteria 必须逐点覆盖用户消息给出的全部评分点，point 原文照抄；
2. achieved：完全达成=true，未达成=false，依据不足无法判断=null；
3. 不引用学生答案之外的证据，evidence_id 恒为 null；
4. confidence 反映整体判定把握。"""


def _prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class LlmRubricJudge:
    """经 gateway 调用 LLM 的 rubric judge；None 永远表示「进复核」。"""

    def __init__(self, gateway: LlmGateway, *, temperature: float = 0.0) -> None:
        self._gateway = gateway
        self._temperature = temperature
        self._prompt_hash = _prompt_hash(_SYSTEM_PROMPT)

    @property
    def judge_model(self) -> str:
        return self._gateway.model

    def judge(
        self, stem: str, rubric_points: tuple[str, ...], given: str
    ) -> RubricJudgement | None:
        points_block = "\n".join(f"- {point}" for point in rubric_points)
        user = (
            f"题目：{stem}\n\n评分点：\n{points_block}\n\n学生答案：{given}\n\n"
            "按系统指令输出 JSON。"
        )
        messages = (
            ChatMessage(role="system", content=_SYSTEM_PROMPT),
            ChatMessage(role="user", content=user),
        )
        try:
            raw = self._gateway.chat(messages, temperature=self._temperature)
        except LlmUnavailable:
            return None
        judgement = _strip_and_parse(raw)
        if judgement is None:
            return None
        # 服务端覆写留痕字段：judge_model 用部署配置的真实模型名，不信任模型自报
        return replace(
            judgement,
            judge_model=self.judge_model,
            prompt_hash=self._prompt_hash,
            raw=raw,
        )


def _strip_and_parse(raw: str) -> RubricJudgement | None:
    """剥掉模型可能包裹的 ```json 围栏后再结构校验；非法一律 None。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    judgement = parse_judgement(text)
    if judgement is None:
        return None
    # rubric 场景 evidence gate：LLM 不持有 evidence 库，任何非空 evidence_id 无效
    return replace(
        judgement,
        criteria=tuple(
            RubricCriterion(point=c.point, achieved=c.achieved, evidence_id=None)
            for c in judgement.criteria
        ),
    )


def build_llm_judge(config: dict) -> LlmRubricJudge | None:
    """从 settings 槽位装配；endpoint/key/model 任一缺席返回 None（复核兜底 + 日志）。

    config: {"endpoint": str|None, "api_key": str|None, "model": str|None}
    """
    import logging

    logger = logging.getLogger(__name__)
    endpoint, api_key, model = (
        config.get("endpoint"),
        config.get("api_key"),
        config.get("model"),
    )
    if not (endpoint and api_key and model):
        logger.warning(
            "rubric_judge=llm 但 LLM 槽位未配齐（endpoint=%s, model=%s）——essay 全部进复核",
            "set" if endpoint else "missing",
            model or "missing",
        )
        return None
    return LlmRubricJudge(LlmGateway(endpoint=endpoint, api_key=api_key, model=model))
