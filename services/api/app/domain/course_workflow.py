"""M5-07 Course generation workflow：Goal -> Competency -> DAG -> Resource ->
Outline -> Lessons -> Assessments -> Remediation（backlog M5-07）。

- 确定性管线：不依赖 LLM——目标概念匹配、先修闭包、拓扑排序、资源挂接、
  章节大纲、课程要点、习题模板、补救映射全部为可审计的规则步骤；
- 不虚报：目标未匹配概念时管线拒绝（抛 NoCompetencyMatch）；资源缺失
  时章节 resources=[] 并在 generation_note 汇总明示；习题为生成模板
  （assessment_kind="template"），不声称是真题；
- 拓扑排序确定性：Kahn 算法 + 候选集按节点 id 字典序，同图同输入恒同序。
"""
from __future__ import annotations

from app.domain.concept_dag import ConceptDag

ASSESSMENT_KIND_TEMPLATE = "template"


class NoCompetencyMatch(Exception):
    """目标文本未匹配到任何概念（fail-closed，不生成空课程）。"""


def match_competencies(goal: str, dag: ConceptDag) -> list[str]:
    """Goal -> Competency：goal 命中的概念 id（canonical_name/alias 子串匹配，保序去重）。"""
    goal_text = goal.strip()
    matched: list[str] = []
    for node in dag.nodes:
        names = [node.canonical_name, *(a for a in node.aliases)]
        if any(name and name in goal_text for name in names):
            matched.append(node.id)
    return matched


def prerequisite_closure(matched: list[str], dag: ConceptDag) -> list[str]:
    """DAG：目标概念集合 + 全部传递先修（边语义：from 是 to 的先修）。"""
    prereq_of: dict[str, list[str]] = {}
    for edge in dag.edges:
        prereq_of.setdefault(edge.concept_id, []).append(edge.prerequisite_id)
    closure: list[str] = []
    seen: set[str] = set()

    def _visit(concept_id: str) -> None:
        if concept_id in seen:
            return
        seen.add(concept_id)
        closure.append(concept_id)
        for pre in prereq_of.get(concept_id, []):
            _visit(pre)

    for concept_id in matched:
        _visit(concept_id)
    return closure


def _topological_order(concept_ids: list[str], dag: ConceptDag) -> list[str]:
    """Kahn 拓扑排序，候选集按 id 字典序（同图同输入恒同序）。"""
    selected = set(concept_ids)
    prereq_of = {cid: [] for cid in concept_ids}
    dependents_of: dict[str, list[str]] = {cid: [] for cid in concept_ids}
    for edge in dag.edges:
        if edge.concept_id in selected and edge.prerequisite_id in selected:
            prereq_of[edge.concept_id].append(edge.prerequisite_id)
            dependents_of[edge.prerequisite_id].append(edge.concept_id)
    indegree = {cid: len(set(prereq_of[cid])) for cid in concept_ids}
    import heapq

    ready = sorted(cid for cid in concept_ids if indegree[cid] == 0)
    heapq.heapify(ready)
    ordered: list[str] = []
    while ready:
        cid = heapq.heappop(ready)
        ordered.append(cid)
        for nxt in sorted(dependents_of[cid]):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(ready, nxt)
    if len(ordered) != len(concept_ids):
        raise ValueError("DAG 存在环，无法排序（发布时已校验，此处为防御性兜底）")
    return ordered


def generate_course_plan(
    goal: str,
    dag: ConceptDag,
    resources_by_concept: dict[str, list[dict]],
) -> dict:
    """八阶段确定性管线（M5-07 验收面），返回完整课程生成草稿。

    resources_by_concept：概念 id -> [{"resource_id", "title", "snippet"}]（路由层检索注入）。
    """
    goal_text = goal.strip()
    matched = match_competencies(goal_text, dag)
    if not matched:
        raise NoCompetencyMatch("目标未匹配到任何概念（DAG 中无相关内容），拒绝生成空课程")
    closure = prerequisite_closure(matched, dag)
    ordered = _topological_order(closure, dag)
    nodes_by_id = {node.id: node for node in dag.nodes}

    outline: list[dict] = []
    lessons: list[dict] = []
    assessments: list[dict] = []
    remediation: list[dict] = []
    missing_resources: list[str] = []

    position: dict[str, int] = {cid: i for i, cid in enumerate(ordered)}
    for index, cid in enumerate(ordered, start=1):
        node = nodes_by_id[cid]
        resources = resources_by_concept.get(cid, [])
        if not resources:
            missing_resources.append(node.canonical_name)
        chapter_no = index
        outline.append({
            "chapter_no": chapter_no,
            "concept_id": cid,
            "title": node.canonical_name,
            "difficulty": node.difficulty,
        })
        lessons.append({
            "chapter_no": chapter_no,
            "objectives": node.description or node.canonical_name,
            "resources": resources,
        })
        assessments.append({
            "chapter_no": chapter_no,
            "concept_id": cid,
            "assessment_kind": ASSESSMENT_KIND_TEMPLATE,
            "stem": f"请用自己的话解释什么是{node.canonical_name}，并举例说明。",
        })
        direct_prereqs = sorted({
            e.prerequisite_id
            for e in dag.edges
            if e.concept_id == cid and e.prerequisite_id in position
        })
        remediation.append({
            "chapter_no": chapter_no,
            "concept_id": cid,
            "review_chapters": [position[p] + 1 for p in direct_prereqs],
        })

    if missing_resources:
        note = (
            f"生成 {len(outline)} 章；以下概念暂无命中的平台资源: "
            + "、".join(missing_resources)
            + "（资源缺失不虚构，习题为生成模板非真题）"
        )
    else:
        note = f"生成 {len(outline)} 章；全部概念均有资源支撑（习题为生成模板非真题）"
    return {
        "goal": goal_text,
        "matched_competencies": matched,
        "ordered_concepts": ordered,
        "dag_version": dag.version,
        "outline": outline,
        "lessons": lessons,
        "assessments": assessments,
        "remediation": remediation,
        "generation_note": note,
    }
