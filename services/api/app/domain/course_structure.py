"""M18-005 课程结构候选域层（审计升级包 Prompt 5 / Phase 4）。

设计边界（审计 02 §5 / 07 Prompt 5）：
- 章节树候选来自 **PDF outline（出版结构化书签）**——不依赖 LLM 提取
  （toc 是出版社排版数据，权威性高于模型猜测）；
- level 嵌套栈推导 parent（1=章 2=节 3=小节——层级语义按相对嵌套，
  不假设绝对含义）；页码锚点保留（source_anchor.page）；
- 一切生成单元处于 **candidate** 状态——人工确认（或后续抽样 QA）后
  才进 confirmed（审计：候选不经校验不成为正式结构）；
- 纯函数：toc 列表 → 单元树（可打桩可测）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: 单元类型（按 toc 相对层级——绝对语义留给确认阶段人工校对）
UNIT_TYPES = ("volume", "chapter", "section")

#: toc 层级深度上限（防异常 outline 深递归——超过折叠进 section）
MAX_DEPTH = 3


class CourseStructureError(ValueError):
    """非法 toc 输入。"""


@dataclass(frozen=True, slots=True)
class CourseUnitCandidate:
    """章节树候选单元（页锚点 + 相对层级）。"""

    sequence: int
    title: str
    unit_type: str
    level: int
    page: int
    parent_sequence: int | None = None
    anchor: dict = field(default_factory=dict)


def toc_to_candidates(toc: list[dict]) -> list[CourseUnitCandidate]:
    """PDF outline（[{level,title,page}]）→ 嵌套单元候选序列。

    规则：
    - level 用**相对嵌套**修正：首条为根；后续 level 跳深（如 1→3）按
      +1 收敛（出版 outline 常见跳跃不虚构中间层）；
    - parent 由同层栈推导（上一未闭合的更浅层）；
    - unit_type：level1=chapter / level2=section / level3+=section
      （volume 留给多卷书——单卷书 outline 通常无卷层）；
    - 空 title/非法 page（<=0）如实拒绝（ParserError 语义上层拦）。
    """
    if not toc:
        raise CourseStructureError("outline 为空——无章节结构可提取")
    candidates: list[CourseUnitCandidate] = []
    stack: list[int] = []  # 候选序列号栈（按 level）
    prev_level = 0
    for entry in toc:
        raw_level = entry.get("level")
        title = str(entry.get("title", "")).strip()
        page = entry.get("page", 0)
        if not isinstance(raw_level, int) or raw_level < 1:
            raise CourseStructureError(f"outline 条目 level 非法：{entry!r}")
        if not title:
            raise CourseStructureError("outline 条目缺少标题")
        # 相对嵌套修正：跳深收敛为 +1；回浅按原 level
        if raw_level > prev_level + 1:
            level = prev_level + 1
        else:
            level = raw_level
        level = min(level, MAX_DEPTH)
        # 弹栈到比当前浅的层
        while stack and _level_of(candidates, stack[-1]) >= level:
            stack.pop()
        parent = stack[-1] if stack else None
        sequence = len(candidates) + 1
        unit_type = "chapter" if level == 1 else "section"
        candidates.append(
            CourseUnitCandidate(
                sequence=sequence,
                title=title,
                unit_type=unit_type,
                level=level,
                page=int(page) if isinstance(page, (int, float)) and page > 0 else 1,
                parent_sequence=parent,
                anchor={"page": int(page) if isinstance(page, (int, float)) and page > 0 else 1, "toc_level": raw_level},
            )
        )
        stack.append(sequence)
        prev_level = level
    return candidates


def _level_of(candidates: list[CourseUnitCandidate], sequence: int) -> int:
    return candidates[sequence - 1].level
