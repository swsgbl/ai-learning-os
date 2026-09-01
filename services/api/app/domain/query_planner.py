"""M5-02 Query planner：查询词槽位识别 → 多源查询计划生成。

- 识别 6 槽位（backlog M5-02 原文）：学科 / 学校 / 年份 / 课程 / 题型 / 公开范围；
  纯规则（词表 + 正则），无 LLM 依赖——同输入恒同输出（与 ADR 27/29/30/31/34/38/39/41 同款幂等语义）；
- build_query_plan 为 M5-01 registry 逐源生成查询计划：enabled 源可直接执行，
  不可用源同样出计划并标注 unavailable_reason（延续「不虚报」边界，可预览可审计）；
- 计划生成与执行分离：本模块只产出计划，不执行不落库（执行仍走 POST /search/queries）。
"""
from __future__ import annotations

import re

# ---- 槽位词表（确定性：命中即取，按最长优先）----

SUBJECTS: tuple[str, ...] = (
    "数学", "物理", "化学", "英语", "语文", "生物", "历史", "地理", "政治",
)

COURSES: tuple[str, ...] = (
    "高等数学", "线性代数", "概率论与数理统计", "概率论", "离散数学",
    "大学物理", "普通物理", "数据结构", "操作系统", "计算机网络",
    "数据库原理", "机器学习", "有机化学", "分析化学", "中国近现代史纲要",
)

QUESTION_TYPES: tuple[str, ...] = (
    "名词解释", "单项选择题", "多项选择题", "选择题", "填空题", "判断题",
    "简答题", "论述题", "计算题", "解答题", "作文",
)

PUBLICITY_TERMS: tuple[str, ...] = (
    "公开课", "精品课", "公开", "校内", "内部",
)

_SCHOOL_RE = re.compile(r"[一-龥]{2,12}?(大学|中学|小学|学院|学校)")
_YEAR_RE = re.compile(r"(19|20)\d{2}")


def _find_term(text: str, terms: tuple[str, ...]) -> str | None:
    """最长优先匹配词表：返回首个命中项或 None。"""
    for term in sorted(terms, key=len, reverse=True):
        if term in text:
            return term
    return None


def parse_query_slots(text: str) -> dict:
    """从自然语言查询识别 6 槽位；未识别槽位为 None（不虚报）。"""
    subject = _find_term(text, SUBJECTS)
    # 课程词命中时不再把「数学」误标为学科（课程优先、含学科词）
    course = _find_term(text, COURSES)
    if subject and course and subject in course:
        subject = None
    q_type = _find_term(text, QUESTION_TYPES)
    publicity = _find_term(text, PUBLICITY_TERMS)
    school_match = _SCHOOL_RE.search(text)
    year_match = _YEAR_RE.search(text)
    year = year_match.group(0) if year_match else None
    if year_match:
        # 年份后缀「年」字会被学校正则吃进（「2024年清华大学」→「年清华大学」），
        # 故摘除年份片段及其紧邻的「年」字后再做学校匹配
        year_end = year_match.end()
        if year_end < len(text) and text[year_end] == "年":
            year_end += 1
        school_text = text[: year_match.start()] + text[year_end:]
        school_match = _SCHOOL_RE.search(school_text)
    school = school_match.group(0) if school_match else None
    return {
        "subject": subject,
        "school": school,
        "year": year,
        "course": course,
        "question_type": q_type,
        "publicity": publicity,
    }


def build_query_plan(slots: dict, raw_query: str, registry) -> list[dict]:
    """按 registry 逐源生成多源查询计划（不执行）。

    - 查询词 = 识别到的槽位词按固定顺序拼接；全空则回退原始查询词；
    - 不可用源同样出计划并带 unavailable_reason（预览可见，不虚报）。
    """
    parts = [
        slots.get("subject"),
        slots.get("course"),
        slots.get("question_type"),
        slots.get("publicity"),
        slots.get("school"),
        slots.get("year"),
    ]
    query_terms = " ".join(p for p in parts if p)
    effective_query = query_terms if query_terms else raw_query.strip()
    plan = []
    for entry in registry:
        plan.append(
            {
                "provider": entry.name,
                "query": effective_query,
                "enabled": entry.enabled,
                "unavailable_reason": entry.unavailable_reason,
            }
        )
    return plan
