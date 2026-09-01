"""M6-03 Citation eval：课程生成草稿引用的 evidence 有效率抽样评测。

- 引用来源：course_generation_drafts 的 lessons[].resources[]
  （resource_id + snippet，均来自 M5-07 检索命中，非虚构）；
- 判定：引用有效 = 资源存在且 snippet 确实出现在该资源的某个 chunk 中
  （资源重解析后 chunk 替换会导致失效——这正是评测要暴露的）；
- 抽样：按 (chapter_no, concept_id, resource_id, snippet) 排序后等步长取样，
  无随机——同库态两次运行恒同；validity_rate >= 0.9 记 passed；
- 无可抽样引用直接拒绝（不虚报有效率）；invalid 全字段透出。
"""
from __future__ import annotations

from collections.abc import Callable

CITATION_EVAL_VERSION = "citation-eval-v1"

CITATION_VALIDITY_TARGET = 0.9

DEFAULT_SAMPLE_SIZE = 20


def extract_citations(plans: list[dict]) -> list[dict]:
    """从课程生成草稿提取全部引用（chapter_no/concept_id/resource_id/snippet）。"""
    citations: list[dict] = []
    for plan in plans:
        chapter_concept = {
            o.get("chapter_no"): o.get("concept_id")
            for o in plan.get("outline", [])
        }
        for lesson in plan.get("lessons", []):
            chapter_no = lesson.get("chapter_no")
            for res in lesson.get("resources", []):
                citations.append({
                    "chapter_no": chapter_no,
                    "concept_id": lesson.get("concept_id") or chapter_concept.get(chapter_no),
                    "resource_id": res["resource_id"],
                    "snippet": res.get("snippet", ""),
                })
    return citations


def sample_citations(
    citations: list[dict], sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> list[dict]:
    """确定性等步长抽样：排序后每 stride 取一个；n<=sample_size 全取。"""
    if not citations:
        raise ValueError("无可抽样的引用（课程草稿没有挂接任何资源）")
    if sample_size < 1:
        raise ValueError("sample_size 必须 >= 1")
    ordered = sorted(
        citations,
        key=lambda c: (c["chapter_no"], c["concept_id"], c["resource_id"], c["snippet"]),
    )
    if len(ordered) <= sample_size:
        return ordered
    stride = -(-len(ordered) // sample_size)
    return ordered[::stride]


def check_citation(citation: dict, chunk_lookup: Callable[[str], list[str]]) -> dict:
    """单条引用判定：资源存在且 snippet 在该资源的 chunk 正文中。"""
    rid = citation["resource_id"]
    snippet = citation["snippet"]
    texts = chunk_lookup(rid)
    if not texts:
        return {**citation, "valid": False, "reason": "资源不存在或无 chunk"}
    if snippet and any(snippet in text for text in texts):
        return {**citation, "valid": True, "reason": "OK"}
    return {**citation, "valid": False, "reason": "snippet 不在资源任何 chunk 中（资源可能已重解析）"}


def run_citation_eval(
    citations: list[dict],
    chunk_lookup: Callable[[str], list[str]],
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> dict:
    """抽样评测：validity_rate/mismatches；同库态确定性可重复。"""
    sampled = sample_citations(citations, sample_size)
    checked = [check_citation(c, chunk_lookup) for c in sampled]
    valid_count = sum(1 for c in checked if c["valid"])
    invalid = [
        {
            "chapter_no": c["chapter_no"],
            "concept_id": c["concept_id"],
            "resource_id": c["resource_id"],
            "reason": c["reason"],
        }
        for c in checked if not c["valid"]
    ]
    rate = valid_count / len(checked)
    return {
        "rule_versions": {"eval": CITATION_EVAL_VERSION},
        "total_citations": len(citations),
        "sampled_count": len(checked),
        "valid_count": valid_count,
        "validity_rate": rate,
        "target": CITATION_VALIDITY_TARGET,
        "passed": rate >= CITATION_VALIDITY_TARGET,
        "invalid": invalid,
    }
