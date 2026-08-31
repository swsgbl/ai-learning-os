"""M3-04 Misconception candidate：误解候选的确定性判定（pack F 要求 2/3）。

- 误解模式签名 pattern = 规范化错误答案（strip/小写/折叠空白/截断）：
  同一概念下「答成同样的错误内容」即同一误解——MVP 可解释启发式，不训练分类模型。
- 独立证据单位 = 不同题目（distinct question_id）：同题重试不增强证据强度
  （pack F「同一误解在多个独立题中出现才提升置信度」）。
- 单次错误也只生成 candidate（status=candidate，低置信度），绝不直接写成长期画像；
  独立题数达到 CONFIRM_THRESHOLD 才升级 confirmed（05 号文档「重复误解升级长期画像」）。
- confidence = independent_count/(independent_count+K)：随独立证据单调提升，有上界。
- 空答案（留空占位）不是误解；复核（correctness=None）与答对不产生候选。
- 幂等：纯函数，同一 (事件集, now) 恒等输出（与 M3-03 同款重算语义）。
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.learning_events import LearningEventStream

STATUS_CANDIDATE = "candidate"
STATUS_CONFIRMED = "confirmed"
CONFIRM_THRESHOLD = 3  # 独立题数达到即升级长期画像
CONFIDENCE_K = 2.0
PATTERN_MAX_LEN = 64
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class MisconceptionCandidate:
    """单一误解模式候选：(concept_id, pattern) 唯一，可整体从事件重建。"""

    concept_id: str
    pattern: str
    status: str
    confidence: float
    independent_count: int  # 不同题目数（独立证据）
    occurrence_count: int  # 全部错误次数（含同题重试）
    question_ids: tuple[str, ...]
    first_seen_at: datetime
    last_seen_at: datetime
    updated_at: datetime  # 重算锚点（= now）


def normalize_pattern(answer: str) -> str:
    """误解模式签名：折叠空白、小写、截断（跨题型可比的确定性归一）。"""
    return _WHITESPACE.sub(" ", answer.strip().lower())[:PATTERN_MAX_LEN]


def derive_misconceptions(
    streams: Iterable[LearningEventStream], *, now: datetime
) -> dict[tuple[str, str], MisconceptionCandidate]:
    """从学习事件流确定性重算全部误解候选（纯函数：同输入恒同输出）。"""
    evidence: dict[tuple[str, str], dict[str, list]] = defaultdict(
        lambda: {"questions": [], "times": []}
    )
    for stream in streams:
        for event in stream.events:
            pattern = normalize_pattern(event.answer)
            if event.correctness is not False or not pattern or not event.concept_ids:
                continue  # 只有明确答错的非空作答才是误解证据
            for concept_id in event.concept_ids:
                if not concept_id:
                    continue  # 空 concept 归属不成立
                slot = evidence[(concept_id, pattern)]
                slot["questions"].append(event.question_id)
                slot["times"].append(event.occurred_at)
    # 事件已按流内序给出；times 逐键排序保证 first/last 与输入顺序无关
    now_utc = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    candidates: dict[tuple[str, str], MisconceptionCandidate] = {}
    for (concept_id, pattern), slot in evidence.items():
        questions = sorted(set(slot["questions"]))
        times = sorted(slot["times"])
        independent = len(questions)
        candidates[(concept_id, pattern)] = MisconceptionCandidate(
            concept_id=concept_id,
            pattern=pattern,
            status=STATUS_CONFIRMED if independent >= CONFIRM_THRESHOLD else STATUS_CANDIDATE,
            confidence=independent / (independent + CONFIDENCE_K),
            independent_count=independent,
            occurrence_count=len(slot["questions"]),
            question_ids=tuple(questions),
            first_seen_at=times[0],
            last_seen_at=times[-1],
            updated_at=now_utc,
        )
    return candidates
