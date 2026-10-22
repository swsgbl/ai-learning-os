"""M16-007 LLM 客观题生成器（打通评估蓝图的内容层闭环）。

背景：蓝图/前后测依赖客观题池（可确定性判分），而课程生成多产写作
题——用户确认合同后题池常为空（M16 生产验证实测缺口）。本模块按
合同概念集用 LLM 生成四选一 mcq，经确定性校验后入题池卷。

校验（方案 Prompt 3「题目生成校验」的 MVP 集）：
- schema：题干非空、options ≥3 且 key 唯一、answer ∈ options keys；
- 去重：同批内 stem 归一后不重复；
- 难度：1-5 整数（越界归 3）。
非法题**逐条丢弃并如实计数**（不静默通过、不让一条坏题污染判分）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.llm.gateway import ChatMessage, LlmGateway, LlmUnavailable

KEYS = ("A", "B", "C", "D")

_SYSTEM_PROMPT = """你是出题专家。为给定概念各出几道四选一选择题。
只输出 JSON（不要 markdown 围栏、不要解释），结构：
{
 "items": [
  {"concept": "概念名", "stem": "题干", "options": ["选项1","选项2","选项3","选项4"],
   "answer_index": 0, "difficulty": 3, "why": "答案解析"}
 ]
}
约束：answer_index 是 0-3 整数（正确选项下标）；difficulty 1-5；
恰好 4 个选项且只有一个是正确的；干扰项要有迷惑性但明确错误；
题干自包含（不依赖其他题）；全部使用与概念相同的语言。"""


class LlmItemGenerationUnavailable(RuntimeError):
    """LLM 客观题生成不可用（无配置/调用失败/输出不可解析）。"""


@dataclass(frozen=True, slots=True)
class GeneratedItem:
    """校验通过的客观题（待入 questions 表）。"""

    concept_id: str
    stem: str
    options: list[dict[str, str]]  # [{key, text}]
    answer: str  # "A"-"D"
    explanation: str
    difficulty: int


@dataclass(frozen=True, slots=True)
class GenerationReport:
    """如实报告：生成/通过/丢弃（诚实计数，不虚报产出）。"""

    items: list[GeneratedItem]
    raw_count: int
    dropped_count: int
    drop_reasons: list[str]


def _normalize_stem(stem: str) -> str:
    return "".join(stem.split()).lower()


def _validate_item(raw: object, concept_map: dict[str, str]) -> GeneratedItem | None:
    """单题校验：非法返回 None（caller 记原因丢弃）。"""
    if not isinstance(raw, dict):
        return None
    concept_name = raw.get("concept")
    stem = raw.get("stem")
    options = raw.get("options")
    answer_index = raw.get("answer_index")
    if not isinstance(concept_name, str) or concept_name not in concept_map:
        return None
    if not isinstance(stem, str) or not stem.strip() or len(stem) > 2000:
        return None
    if not isinstance(options, list) or not (3 <= len(options) <= 4):
        return None
    texts: list[str] = []
    for option in options:
        if not isinstance(option, str) or not option.strip():
            return None
        texts.append(option.strip())
    if len({_normalize_stem(t) for t in texts}) != len(texts):
        return None  # 选项重复
    if not isinstance(answer_index, int) or isinstance(answer_index, bool) or not (0 <= answer_index < len(texts)):
        return None
    difficulty = raw.get("difficulty", 3)
    if not isinstance(difficulty, int) or isinstance(difficulty, bool) or not (1 <= difficulty <= 5):
        difficulty = 3
    why = raw.get("why", "")
    if not isinstance(why, str):
        why = ""
    return GeneratedItem(
        concept_id=concept_map[concept_name],
        stem=stem.strip(),
        options=[{"key": KEYS[i], "text": text} for i, text in enumerate(texts)],
        answer=KEYS[answer_index],
        explanation=why.strip(),
        difficulty=difficulty,
    )


def generate_objective_items(
    concepts: dict[str, str],
    per_concept: int,
    config: tuple[str | None, str | None, str | None],
    *,
    source_context: str = "",
) -> GenerationReport:
    """concepts: {概念显示名: concept_id}；config=(endpoint, api_key, model)。

    LLM 输出解析失败/不可用 → LlmItemGenerationUnavailable（端点如实，
    不虚构题目）；单题非法丢弃计数。
    """
    if not concepts:
        raise LlmItemGenerationUnavailable("概念集为空")
    endpoint, api_key, model = config
    if not (endpoint and api_key and model):
        raise LlmItemGenerationUnavailable("未配置 LLM（个人密钥或服务端网关）——无法生成客观题")
    # M16-010b：出题走部署超时（LLM_TIMEOUT_SECONDS，本地慢拓扑冷启动
    # 可 >30s——此前默认 30s 网关超时在本地 Ollama 上真实超时）
    from app.core.config import get_settings

    gateway = LlmGateway(
        endpoint=endpoint,
        api_key=api_key,
        model=model,
        timeout_seconds=get_settings().llm_timeout_seconds or 30.0,
    )
    listing = "\n".join(f"- {name}" for name in concepts)
    messages = (
        ChatMessage(role="system", content=_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=f"概念清单：\n{listing}\n每个概念出 {per_concept} 道题。{source_context}",
        ),
    )
    try:
        raw = gateway.chat(messages, temperature=0.3, max_tokens=4096)
    except LlmUnavailable as cause:
        raise LlmItemGenerationUnavailable(f"LLM 调用失败：{cause}") from cause
    parsed = _parse_json(raw)
    if parsed is None or not isinstance(parsed.get("items"), list):
        raise LlmItemGenerationUnavailable("LLM 输出不是可解析的题目 JSON（可重试）")
    raw_items = parsed["items"]
    valid: list[GeneratedItem] = []
    dropped: list[str] = []
    seen_stems: set[str] = set()
    per_concept_count: dict[str, int] = {}
    for entry in raw_items:
        item = _validate_item(entry, concepts)
        if item is None:
            dropped.append("schema 校验未通过")
            continue
        normalized = _normalize_stem(item.stem)
        if normalized in seen_stems:
            dropped.append(f"重复题干（{item.concept_id}）")
            continue
        # 每概念超过 per_concept 的多余题丢弃（数量约束）
        if per_concept_count.get(item.concept_id, 0) >= per_concept:
            dropped.append(f"超出每概念上限（{item.concept_id}）")
            continue
        seen_stems.add(normalized)
        per_concept_count[item.concept_id] = per_concept_count.get(item.concept_id, 0) + 1
        valid.append(item)
    if not valid:
        raise LlmItemGenerationUnavailable(
            f"生成的 {len(raw_items)} 题全部未通过校验（丢弃原因：{dropped[:3]}）"
        )
    return GenerationReport(
        items=valid,
        raw_count=len(raw_items),
        dropped_count=len(dropped),
        drop_reasons=dropped,
    )


def _parse_json(raw: str) -> dict | None:
    """剥离 markdown 围栏后解析 JSON；失败返回 None。"""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
