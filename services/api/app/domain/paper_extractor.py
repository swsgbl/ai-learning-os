"""M5-06 Paper extractor：从试卷解析产物（chunks）抽取题目草稿（backlog M5-06）。

- 确定性规则抽取：题型段标题 -> 题号行 -> 选项行 -> 归属页码；
- 题型映射显式：mcq / multiple_select / true_false / fill_blank / solved；
  无法归类时记 unknown（不虚报）；分值未识别时为 None（不虚报）；
- 页码保留：题目起始 chunk 的 page_start 到最后归属 chunk 的 page_end——
  题目草稿可回溯原文页码（验收要求）；
- 无题不虚报：识别不出题目时返回空列表并在 note 中明示。
"""
from __future__ import annotations

import re

# 题型段标题关键词 -> 规范题型名（显式映射，不猜测）
SECTION_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("单项选择题", "单选题", "选择题"), "mcq"),
    (("多项选择题", "多选题"), "multiple_select"),
    (("判断题",), "true_false"),
    (("填空题",), "fill_blank"),
    (("简答题", "解答题", "计算题", "证明题", "论述题", "分析题"), "solved"),
)

UNKNOWN_TYPE = "unknown"

# 中文数字序号的题型段标题前缀，如 "一、" "二．"
_CN_NUM_PREFIX = re.compile(r"^[一二三四五六七八九十]+\s*[、.．]\s*")

# 题号行：1. / 1、 / 1．开头
_QUESTION_NO = re.compile(r"^(\d{1,3})\s*[.、．]\s*(.*)$")

# 选项行：A. / A、 / A．开头（B-H 同理）
_OPTION = re.compile(r"^([A-H])\s*[.、．]\s*(.*)$")

# 段标题分值：每小题 3 分 / 每题 2分 / 每道题 5 分 / 2分/题
_SECTION_SCORE = re.compile(r"(?:每小题|每道题|每题|每空)\s*(\d+(?:\.\d+)?)\s*分|(\d+(?:\.\d+)?)\s*分\s*/\s*题")

# 题干内分值：（5分）/(5分)——覆盖段默认分值
_STEM_SCORE = re.compile(r"[（(]\s*(\d+(?:\.\d+)?)\s*分\s*[）)]")

_MAX_STEM_LEN = 500



# ---------- 题型段标题识别 ----------


def match_section(line: str) -> tuple[str, float | None] | None:
    """识别题型段标题行 -> (题型, 段默认分值)。

    判定条件（避免把题干误判为段标题）：
    - 含题型关键词，且满足以下之一：
      a) 有中文数字序号前缀（一、/二．）；
      b) 短行（<=30 字符）且不带题号前缀。
    分值提取：每小题/每题/每空 X 分 / X分/题；识别不出为 None（不虚报）。
    """
    stripped = line.strip()
    if not stripped:
        return None
    has_cn = bool(_CN_NUM_PREFIX.match(stripped))
    short_enough = len(stripped) <= 30 and not _QUESTION_NO.match(stripped)
    if not (has_cn or short_enough):
        return None
    for keywords, qtype in SECTION_KEYWORDS:
        for keyword in keywords:
            if keyword in stripped:
                return qtype, _extract_section_score(stripped)
    return None


def _extract_section_score(text: str) -> float | None:
    """段标题里的分值模式；识别不出返回 None（不虚报）。"""
    m = _SECTION_SCORE.search(text)
    if m is None:
        return None
    value = m.group(1) or m.group(2)
    return float(value)


# ---------- 题目抽取 ----------


def _append_stem(question: dict, line: str) -> None:
    """题干追加行（含行界空格），截断至 500 字符。"""
    stem = question["stem"]
    joined = (stem + " " + line.strip()) if stem else line.strip()
    question["stem"] = joined[:500]


def extract_questions(chunks: list[dict]) -> dict:
    """从 chunks（list_chunks 形态：text/page_start/page_end）抽取题目草稿。

    规则（确定性，不虚报）：
    - 逐行扫描：题型段标题切换 section；题号行开新题；
      选项行归当前题；其余非空行追加题干（截断 500 字符）；
    - 页码：起始行所在 chunk 的 page_start 至最后归属行的 chunk 页；
    - 题干内（X分）覆盖段默认分值；均无则 score=None；
    - 无 section 时题型 unknown；无题时 questions=[] 且 note 明示。
    """
    questions: list[dict] = []
    current: dict | None = None
    section_type = UNKNOWN_TYPE
    section_score: float | None = None

    for chunk in chunks:
        page = chunk.get("page_start")
        page_end = chunk.get("page_end")
        for raw_line in chunk.get("text", "").splitlines():
            line = raw_line.rstrip()
            if not line.strip():
                continue
            section = match_section(line)
            if section is not None:
                section_type, section_score = section
                current = None
                continue
            no_match = _QUESTION_NO.match(line)
            if no_match is not None:
                current = {
                    "question_no": int(no_match.group(1)),
                    "stem": no_match.group(2).strip(),
                    "question_type": section_type,
                    "score": section_score,
                    "page_start": page,
                    "page_end": page_end,
                    "options": [],
                }
                questions.append(current)
                continue
            if current is None:
                continue
            option = _OPTION.match(line)
            if option is not None:
                current["options"].append({
                    "label": option.group(1),
                    "text": option.group(2).strip(),
                })
                continue
            _append_stem(current, line)
            current["page_end"] = page_end
    # 题干内分值覆盖段默认：识别出才覆盖，识别不出保留段默认/None（不虚报）
    for question in questions:
        m = _STEM_SCORE.search(question["stem"])
        if m is not None:
            question["score"] = float(m.group(1))
    if not questions:
        note = "未识别出题目（可能不是试卷结构或解析质量不足）"
    else:
        note = f"识别出 {len(questions)} 题"
    return {"questions": questions, "extraction_note": note}
