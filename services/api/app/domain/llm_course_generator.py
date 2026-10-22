"""M15-019 LLM 动态课程生成：goal 无概念 DAG 匹配时的 fallback 路径。

定位：概念 DAG 匹配制只能生成「已发布概念图覆盖」的主题（生产仅
SQL 一张图）——任意主题（用户初衷「输入一个主题/学科/行业」）由
LLM 兜底：goal → LLM 产出概念集 + 章节大纲 + 每章评估 → plan dict
（与 DAG 版 generate_course_plan 同形态，materialize/判分链零改动）。

LLM 来源优先级：用户 BYOK active profile > 服务端全局 LLM 配置。
输出为严格 JSON（markdown 围栏剥离）；解析失败/LLM 不可用 →
LlmCourseUnavailable（端点 422 如实，不虚构课程）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.llm.gateway import ChatMessage, LlmGateway, LlmUnavailable

_SYSTEM_PROMPT = """你是课程设计师。根据学习目标设计一门自学课程。
只输出 JSON（不要 markdown 围栏、不要解释），结构：
{
 "concepts": ["概念名1", "概念名2"],
 "outline": [{"chapter_no": 1, "title": "章节标题", "difficulty": 1}],
 "assessments": [{"chapter_no": 1, "stem": "请用自己的话解释……并举例说明"}]
}
约束：4-8 章；difficulty 为 1-5 整数按由浅入深排列；章节间有先修顺序；
assessments 与 outline 章节一一对应；全部使用学习目标的语言（中文目标
用中文）。"""


class LlmCourseUnavailable(RuntimeError):
    """LLM 动态生成不可用（无 LLM 配置/调用失败/输出不可解析）。"""


@dataclass(frozen=True)
class LlmCourseConfig:
    """LLM 来源（BYOK active profile 或全局配置已解析后的值）。"""

    endpoint: str | None
    api_key: str | None
    model: str | None
    # M16-010b：部署超时（LLM_TIMEOUT_SECONDS；None = 网关默认 30s）
    timeout_seconds: float | None = None


def generate_course_plan_with_llm(goal: str, config: LlmCourseConfig) -> dict:
    """goal → LLM → plan dict（与 DAG 版同形态；dag_version=0 标记动态）。"""
    if not (config.endpoint and config.api_key and config.model):
        raise LlmCourseUnavailable("未配置 LLM（个人密钥或服务端网关）——无法动态生成课程")
    from app.core.config import get_settings

    gateway = LlmGateway(
        endpoint=config.endpoint,
        api_key=config.api_key,
        model=config.model,
        timeout_seconds=config.timeout_seconds or 30.0,
        ollama_native=get_settings().llm_provider == "ollama",
    )
    messages = (
        ChatMessage(role="system", content=_SYSTEM_PROMPT),
        ChatMessage(role="user", content=f"学习目标：{goal}"),
    )
    try:
        raw = gateway.chat(messages, temperature=0.2)
    except LlmUnavailable as cause:
        raise LlmCourseUnavailable(f"LLM 调用失败：{cause}") from cause
    parsed = _parse_plan(raw)
    if parsed is None:
        raise LlmCourseUnavailable("LLM 输出不是可解析的课程 JSON（可重试）")
    concepts = parsed["concepts"]
    outline = parsed["outline"]
    assessments = parsed["assessments"]
    by_no = {a["chapter_no"]: a["stem"] for a in assessments}
    lessons = [
        {
            "chapter_no": ch["chapter_no"],
            "objectives": f"理解并能够运用「{ch['title']}」",
            "resources": [],
        }
        for ch in outline
    ]
    remediation = [
        {
            "chapter_no": ch["chapter_no"],
            "concept_id": f"llm:{ch['chapter_no']}",
            "review_chapters": [ch["chapter_no"] - 1] if ch["chapter_no"] > 1 else [],
        }
        for ch in outline
    ]
    return {
        "goal": goal,
        "matched_competencies": concepts,
        "ordered_concepts": [f"llm:{ch['chapter_no']}" for ch in outline],
        "outline": [
            {
                "chapter_no": ch["chapter_no"],
                "concept_id": f"llm:{ch['chapter_no']}",
                "title": ch["title"],
                "difficulty": ch.get("difficulty", 3),
            }
            for ch in outline
        ],
        "lessons": lessons,
        "assessments": [
            {
                "chapter_no": ch["chapter_no"],
                "concept_id": f"llm:{ch['chapter_no']}",
                "assessment_kind": "llm_explain",
                "stem": by_no.get(ch["chapter_no"], f"请用自己的话解释什么是{ch['title']}，并举例说明。"),
            }
            for ch in outline
        ],
        "remediation": remediation,
        "dag_version": 0,
        "generation_note": f"LLM 动态生成 {len(outline)} 章（概念图未覆盖该主题；模型 {config.model}）",
    }


def _parse_plan(raw: str) -> dict | None:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    concepts = data.get("concepts")
    outline = data.get("outline")
    assessments = data.get("assessments")
    if not isinstance(concepts, list) or not concepts:
        return None
    if not isinstance(outline, list) or not (4 <= len(outline) <= 8):
        return None
    if not isinstance(assessments, list):
        return None
    chapters = []
    for index, ch in enumerate(outline, start=1):
        if not isinstance(ch, dict) or not str(ch.get("title", "")).strip():
            return None
        chapters.append(
            {
                "chapter_no": int(ch.get("chapter_no", index)),
                "title": str(ch["title"]).strip()[:120],
                "difficulty": _clamp_difficulty(ch.get("difficulty")),
            }
        )
    stems = []
    for index, item in enumerate(assessments, start=1):
        stems.append(
            {
                "chapter_no": int(item.get("chapter_no", index)) if isinstance(item, dict) else index,
                "stem": str(item.get("stem", "")).strip()[:300] if isinstance(item, dict) else "",
            }
        )
    return {
        "concepts": [str(c).strip()[:80] for c in concepts if str(c).strip()][:12],
        "outline": chapters,
        "assessments": stems,
    }


def _clamp_difficulty(value: object) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 3
