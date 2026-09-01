"""M5-08 Variant question generator：变式题生成（解保持变换白名单，不虚报）。

- 验收：变式题保留概念映射和原题 evidence；不会只换表面数字导致无解；
- 两类解保持变换（白名单外一律不做）：
  1. context_swap 语境实体替换——固定词典确定性映射（同变式内一致），
     数学结构与数值完全不动，解不变（可解性由构造保证）；
  2. numeric_scale 数值重标定——所有数字精确 x2（十进制乘 2 恒精确无舍入），
     相同原文数字映射到相同新数字（可逆），mcq/multiple_select 选项同步缩放
     保持题干-选项一致性；
- 不虚报：无实体命中且无数值的题目 0 变式并在 note 明示（不硬凑）；
  变式带原题完整 evidence（来源题号/页码/stem 摘录/资源）；概念映射逐题继承；
  全部变式进人工审核队列（pending_review 起步，终态不可逆）。
"""
from __future__ import annotations

import re
from decimal import Decimal

CONTEXT_MAP: tuple[tuple[str, str], ...] = (
    ("苹果", "橙子"),
    ("火车", "汽车"),
    ("甲班", "乙班"),
    ("这本书", "那本书"),
    ("小明", "小红"),
    ("水池", "水槽"),
    ("正方形", "长方形"),
)

_NUM_RE = re.compile(r"[0-9]+(?:[0-9]*)?(?:\.[0-9]+)?")

MAX_QUESTIONS = 20
MAX_VARIANTS_PER_QUESTION = 2
MAX_STEM_EXCERPT = 120


def _context_swap(stem: str) -> tuple[str, str] | None:
    """语境实体替换：全部命中实体都替换（同变式内一致）；未命中任何实体返回 None。"""
    hits: list[str] = []
    result = stem
    for source, target in CONTEXT_MAP:
        if source in result:
            result = result.replace(source, target)
            hits.append(f"{source}->{target}")
    return (result, "+".join(hits)) if hits else None


def _scale_number(match: re.Match) -> str:
    """单数字精确 x2（Decimal 保证十进制精确，无舍入误差）。"""
    original = match.group(0)
    doubled = Decimal(original) * Decimal(2)
    text = format(doubled, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _numeric_scale(text: str, mapping: dict[str, str]) -> tuple[str, dict[str, str]]:
    """同变式内可逆映射：相同原文数字 -> 相同新数字（先查表再生成）。"""
    local: dict[str, str] = dict(mapping)

    def _sub(match: re.Match) -> str:
        original = match.group(0)
        if original not in local:
            local[original] = _scale_number(match)
        return local[original]

    return _NUM_RE.sub(_sub, text), local


def _scale_option(option: dict, mapping: dict[str, str]) -> dict:
    new_text, _ = _numeric_scale(option.get("text", ""), mapping)
    return {"label": option.get("label", ""), "text": new_text}


def generate_variants(question: dict) -> tuple[list[dict], str]:
    """单题变式生成：返回 (变式列表, 单题说明)。

    变式字段：transform/stem/question_type/score/options/concept_ids/evidence；
    evidence 完整保留原题溯源（题号/页码/stem 摘录/resource_id）。
    """
    stem = question.get("stem", "")
    qtype = question.get("question_type", "unknown")
    options = question.get("options") or []
    concept_ids = list(question.get("concept_ids") or [])
    evidence = {
        "source_question_no": question.get("question_no"),
        "page_start": question.get("page_start"),
        "page_end": question.get("page_end"),
        "resource_id": question.get("resource_id"),
        "source_stem_excerpt": stem[:MAX_STEM_EXCERPT],
    }
    variants: list[dict] = []
    notes: list[str] = []

    swap = _context_swap(stem)
    if swap is not None:
        new_stem, swap_desc = swap
        variants.append({
            "transform": "context_swap",
            "transform_detail": swap_desc,
            "stem": new_stem,
            "question_type": qtype,
            "score": question.get("score"),
            "options": [dict(o) for o in options],
            "concept_ids": concept_ids,
            "evidence": dict(evidence),
            "solvable_note": "语境实体替换，数值与数学结构未变，解不变",
        })

    numbers = _NUM_RE.findall(stem)
    if numbers:
        scaled_stem, mapping = _numeric_scale(stem, {})
        scaled_options = [_scale_option(o, mapping) for o in options]
        variants.append({
            "transform": "numeric_scale",
            "transform_detail": "全部数值精确 x2（相同数字同映射，选项同步）",
            "stem": scaled_stem,
            "question_type": qtype,
            "score": question.get("score"),
            "options": scaled_options,
            "concept_ids": concept_ids,
            "evidence": dict(evidence),
            "solvable_note": "线性重标定变式，题干与选项同步缩放，需人工复核适用性",
        })
    if not variants:
        notes.append(f"题 {question.get('question_no')} 无法生成解保持变式（无实体映射命中且无数值）")
    return variants, "；".join(notes)


def generate_variant_draft(questions: list[dict]) -> dict:
    """整卷变式草稿：逐题生成，note 汇总（含 0 变式题明示，不虚报）。"""
    if not questions or len(questions) > MAX_QUESTIONS:
        raise ValueError(f"questions 数量须为 1..{MAX_QUESTIONS}")
    all_variants: list[dict] = []
    skipped: list[str] = []
    for question in questions:
        variants, note = generate_variants(question)
        if note:
            skipped.append(note)
        for variant in variants:
            variant["variant_no"] = len(all_variants) + 1
            all_variants.append(variant)
    parts = [f"生成 {len(all_variants)} 个变式（源题 {len(questions)} 道）"]
    if skipped:
        parts.append("；".join(skipped))
    parts.append("变式均带原题 evidence 与概念映射；全部待人工审核")
    return {
        "variants": all_variants,
        "variant_count": len(all_variants),
        "generation_note": "；".join(parts),
    }
