"""M18-006 概念候选域层（审计升级包 Prompt 5 后半）。

设计边界（审计 02 §5 / 07 Prompt 5）：
- 概念图正式面已有（M3-02 Concept DAG——admin 版本化不可变发布）；
  本域层补 **AI 候选路径**：候选节点/边永不直接进 DAG——人工确认后
  经既有发布管道（全量快照）成为正式结构（审计硬边界：模型提议边
  开始是 candidate，不是事实）；
- 多类型关系（审计枚举）：prerequisite_of / part_of / similar_to /
  often_confused_with / applies_to——正式 DAG 只收 prerequisite
  （既有语义）；其余类型候选确认后留待 DAG 模型扩展（如实：本期
  正式图不消费非先修边）；
- 概念 id 形态对齐既有 ConceptNodeIn（^[a-z0-9_:\\-]+$——与学习引擎
  的 sql:group-by 同族，跨体系一致）；
- confidence ∈ [0,1]（LLM 自评——展示用，不是事实强度）；
  provenance 保留章节锚点（页码/章节 id——可追溯）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 概念 id（与 M3-02 ConceptNodeIn 同形态——跨体系一致）
CONCEPT_ID_PATTERN = re.compile(r"^[a-z0-9_:\-]{2,64}$")

#: 关系类型（审计 02 §5 枚举）
RELATION_TYPES: tuple[str, ...] = (
    "prerequisite_of",
    "part_of",
    "similar_to",
    "often_confused_with",
    "applies_to",
)

#: 候选状态机：candidate → confirmed（入 DAG 发布集）/ rejected
CANDIDATE_STATUSES: tuple[str, ...] = ("candidate", "confirmed", "rejected")


class ConceptCandidateError(ValueError):
    """非法候选输入。"""


@dataclass(frozen=True, slots=True)
class ConceptCandidate:
    """概念节点候选（LLM 提取——确认后才进正式 DAG）。"""

    concept_id: str
    canonical_name: str
    subject: str = ""
    description: str = ""
    difficulty: int = 3
    confidence: float = 0.0
    provenance: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConceptEdgeCandidate:
    """概念边候选（typed relation + 置信 + 章节溯源）。"""

    from_concept: str
    to_concept: str
    relation: str
    confidence: float = 0.0
    provenance: dict = field(default_factory=dict)


def validate_concept_candidate(raw: object) -> ConceptCandidate:
    """LLM 输出条目 → 候选（非法抛错——逐条校验不静默吞）。"""
    if not isinstance(raw, dict):
        raise ConceptCandidateError("候选必须是对象")
    concept_id = raw.get("id") or raw.get("concept_id")
    name = raw.get("canonical_name") or raw.get("name")
    if not isinstance(concept_id, str) or not CONCEPT_ID_PATTERN.match(concept_id):
        raise ConceptCandidateError(f"概念 id 非法：{concept_id!r}（须 ^[a-z0-9_:-]{{2,64}}$）")
    if not isinstance(name, str) or not name.strip():
        raise ConceptCandidateError(f"{concept_id} 缺少 canonical_name")
    difficulty = raw.get("difficulty", 3)
    if not isinstance(difficulty, int) or not 1 <= difficulty <= 5:
        difficulty = 3
    confidence = _clamp_confidence(raw.get("confidence"))
    return ConceptCandidate(
        concept_id=concept_id,
        canonical_name=name.strip()[:256],
        subject=str(raw.get("subject", ""))[:64],
        description=str(raw.get("description", ""))[:2048],
        difficulty=difficulty,
        confidence=confidence,
        provenance=_provenance(raw.get("provenance")),
    )


def validate_edge_candidate(raw: object, known_ids: set[str]) -> ConceptEdgeCandidate:
    """边候选校验（端点须在候选集或既有 DAG——防幽灵概念）。"""
    if not isinstance(raw, dict):
        raise ConceptCandidateError("边候选必须是对象")
    source = raw.get("from") or raw.get("from_concept")
    target = raw.get("to") or raw.get("to_concept")
    relation = raw.get("relation") or raw.get("type")
    if not isinstance(source, str) or not isinstance(target, str):
        raise ConceptCandidateError("边候选缺少 from/to")
    if source not in known_ids or target not in known_ids:
        raise ConceptCandidateError(f"边端点不在概念集：{source} → {target}")
    if source == target:
        raise ConceptCandidateError("自环边无意义")
    if relation not in RELATION_TYPES:
        raise ConceptCandidateError(f"关系类型非法：{relation!r}（合法：{RELATION_TYPES}）")
    return ConceptEdgeCandidate(
        from_concept=source,
        to_concept=target,
        relation=relation,
        confidence=_clamp_confidence(raw.get("confidence")),
        provenance=_provenance(raw.get("provenance")),
    )


def _clamp_confidence(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _provenance(value: object) -> dict:
    if isinstance(value, dict):
        return {"page": value.get("page"), "unit": value.get("unit"), "raw": value}
    return {}
