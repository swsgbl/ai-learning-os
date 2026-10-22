"""M17-001 概念推导：合同能力目标 → 概念 ID（全新主题冷启动的源头）。

设计边界（升级方案 Phase 1/3）：
- 目标是「给定 X，能在标准 Z 下完成 Y」的可观察结果——概念是达成它
  所需的底层知识单元；推导只做目标→概念的拆解，不虚构掌握结论；
- concept_id 与题库既有形态一致（``学科:主题``，如 sql:group-by）——
  推导产物直接进出题管线（item_generator 的 concepts 映射）；
- LLM 输出逐条校验（格式/去重/上限），非法丢弃计数；不可用/全非法
  抛 :class:`ConceptDerivationUnavailable`（调用方 503/422 如实，
  绝不回退到编造概念清单）；
- 幂等语义：推导不落库——重复生成会再推导（同一合同同一目标在
  同温度下的漂移由调用侧去重吸收：出题管线按 concept_id 归并）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.llm.gateway import ChatMessage, LlmGateway, LlmUnavailable

#: concept_id 形态：小写字母数字中划线段，冒号分隔可选（题库既有
#: sql:group-by 为冒号形态；live 实测 LLM 常产出无冒号 slug
#: （sql-aggregation）——两种都接受，概念 ID 的本质是全链一致的
#: 稳定标识符（生成→入库 knowledge→蓝图聚合→掌握度均字符串匹配）
_CONCEPT_ID_PATTERN = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*(?::[a-z0-9]+(?:-[a-z0-9]+)*)?$"
)

#: 推导上限：更多概念摊薄每概念题量（蓝图每概念需 ≥4 题才有等值前后测）
MAX_DERIVED_CONCEPTS = 4
MIN_DERIVED_CONCEPTS = 1

#: 每概念显示名上限（超出截断；显示名只进出题 prompt 语境）
_MAX_NAME_CHARS = 40


class ConceptDerivationUnavailable(RuntimeError):
    """LLM 不可用 / 输出不可解析 / 全部概念非法——调用方如实失败。"""


@dataclass(frozen=True, slots=True)
class DerivedConcept:
    concept_id: str
    display_name: str


_SYSTEM_PROMPT = (
    "你是课程设计师。用户给出学习冲刺的能力目标（给定什么条件、要在什么标准下完成什么）。"
    "请把每个目标拆解成最少的底层知识概念（可测试的知识单元，不是任务或主题词）。"
    f"最多 {MAX_DERIVED_CONCEPTS} 个概念，宁少而精。"
    '输出 JSON：{"concepts": [{"id": "学科:主题", "name": "概念中文名"}]}。'
    "id 规则：全小写，学科与主题用冒号分隔，多词用连字符（如 sql:group-by、stat:hypothesis-testing）。"
    "只输出 JSON，不要解释。"
)


def _validate_entry(raw: object) -> tuple[str, str] | None:
    """单条概念校验：dict + id 格式 + name 非空 → (concept_id, name)。"""
    if not isinstance(raw, dict):
        return None
    concept_id = raw.get("id")
    name = raw.get("name")
    if not isinstance(concept_id, str) or not _CONCEPT_ID_PATTERN.match(concept_id.strip()):
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    return concept_id.strip().lower(), name.strip()[:_MAX_NAME_CHARS]


def derive_concepts_from_outcomes(
    outcomes: list[str],
    config: tuple[str | None, str | None, str | None],
    *,
    timeout_seconds: float = 30.0,
    ollama_native: bool = False,
) -> list[DerivedConcept]:
    """能力目标清单 → 概念清单（LLM 拆解 + 逐条校验）。

    outcomes 是合同 target_outcomes 的 statement 文本；config 是
    (endpoint, api_key, model) 三元组（BYOK active > 全局——调用侧
    已解析）。失败一律 :class:`ConceptDerivationUnavailable`。
    """
    if not outcomes:
        raise ConceptDerivationUnavailable("合同无能力目标——无从推导概念")
    endpoint, api_key, model = config
    if not (endpoint and api_key and model):
        raise ConceptDerivationUnavailable(
            "未配置 LLM（个人密钥或服务端网关）——无法从目标推导概念"
        )
    gateway = LlmGateway(
        endpoint=endpoint,
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        ollama_native=ollama_native,
    )
    listing = "\n".join(f"- {statement}" for statement in outcomes)
    messages = (
        ChatMessage(role="system", content=_SYSTEM_PROMPT),
        ChatMessage(role="user", content=f"能力目标：\n{listing}"),
    )
    try:
        raw = gateway.chat(messages, temperature=0.0, max_tokens=1024)
    except LlmUnavailable as cause:
        raise ConceptDerivationUnavailable(f"LLM 调用失败：{cause}") from cause
    parsed = _parse_json(raw)
    entries = parsed.get("concepts") if isinstance(parsed, dict) else None
    if not isinstance(entries, list):
        raise ConceptDerivationUnavailable("LLM 输出不是可解析的概念 JSON（可重试）")
    derived: list[DerivedConcept] = []
    seen: set[str] = set()
    dropped = 0
    for entry in entries:
        validated = _validate_entry(entry)
        if validated is None:
            dropped += 1
            continue
        concept_id, name = validated
        if concept_id in seen:
            dropped += 1
            continue
        if len(seen) >= MAX_DERIVED_CONCEPTS:
            dropped += 1
            continue
        seen.add(concept_id)
        derived.append(DerivedConcept(concept_id=concept_id, display_name=name))
    if not derived:
        raise ConceptDerivationUnavailable(
            f"推导的 {len(entries)} 个概念全部未通过校验（丢弃 {dropped}）"
        )
    return derived


def _parse_json(raw: str) -> dict | None:
    """宽容 JSON 提取（与 item_generator 同款：剥代码围栏后整串解析）。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)  # type: ignore[no-any-return]
    except ValueError:
        return None
