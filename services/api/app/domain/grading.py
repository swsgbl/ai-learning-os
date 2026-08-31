"""M2-08 Objective grader：mcq / multiple_select / true_false / short_answer 结构化判分。

golden set 语义（04 号文档 §2.6 + M2-01 QuestionSpec）：
- 题型名兼容两套：seed（mcq/tf/short）与 QuestionSpec（mcq/true_false/short_answer/...）；
- expected 兼容两种形态：legacy 字符串（"A"、"T"、"ABD"、"a|b|c"）与导入 JSON
  （multiple_select 的 option_indices、short_answer 的 accepted 列表）；
- given 服务端归一：大小写/空白不敏感；true_false 接受 T/F、true/false、对/错、是/否、yes/no；
- multiple_select 按集合等价（"ADB" 与 "ABD" 同对错）；
- 判不出的 objective 题型（math/coding/essay 等）返回 False，归 M2-09/M2-10 grader。

规则版本 objective-v2 记录在 submissions.rule_version；输入事件保留在 answer_events 可重放。
"""
from __future__ import annotations

import json
from typing import Final

OBJECTIVE_RULE_VERSION: Final = "objective-v2"

_MCQ_KEYS = "ABCDEFGH"


def normalize_type(question_type: str) -> str:
    """seed 命名与 QuestionSpec 命名统一为规范题型名。"""
    aliases = {
        "choice": "mcq",
        "tf": "true_false",
        "boolean": "true_false",
        "short": "short_answer",
    }
    return aliases.get(question_type.strip().lower(), question_type.strip().lower())


def _normalize_text(value: str) -> str:
    return "".join(value.split()).upper()


def _normalize_tf(value: str) -> str:
    text = value.strip().lower()
    if text in {"t", "true", "对", "正确", "是", "yes", "y"}:
        return "T"
    if text in {"f", "false", "错", "错误", "否", "no", "n"}:
        return "F"
    return _normalize_text(value)


def _expected_candidates(question_type: str, expected: str) -> list[str]:
    """展开 expected 的 legacy / JSON 两种形态为候选答案列表。"""
    if expected.strip().startswith("{"):
        try:
            data = json.loads(expected)
        except json.JSONDecodeError:
            return [expected]
        if question_type == "multiple_select" and isinstance(data, dict):
            indices = data.get("option_indices", [])
            return ["".join(_MCQ_KEYS[i] for i in sorted(indices))]
        if question_type == "short_answer" and isinstance(data, dict):
            return [str(item) for item in data.get("accepted", [])]
        return [json.dumps(data, ensure_ascii=False, sort_keys=True)]
    if question_type == "short_answer" and "|" in expected:
        return [alternative.strip() for alternative in expected.split("|")]
    return [expected]


def grade_answer(question_type: str, expected: str, given: str) -> bool:
    kind = normalize_type(question_type)
    if not given.strip():
        return False

    if kind == "mcq":
        return _normalize_text(given) == _normalize_text(expected)

    if kind == "multiple_select":
        expected_letters = _expected_candidates(kind, expected)
        if not expected_letters:
            return False
        given_letters = "".join(sorted(set(_normalize_text(given))))
        return any(
            given_letters == "".join(sorted(set(_normalize_text(candidate))))
            for candidate in expected_letters
        )

    if kind == "true_false":
        return _normalize_tf(given) == _normalize_tf(expected)

    if kind == "short_answer":
        normalized_given = _normalize_text(given).lower()
        return any(
            normalized_given == _normalize_text(candidate).lower()
            for candidate in _expected_candidates(kind, expected)
        )

    # math/coding/essay 等非 objective 题型：objective grader 不判，留给 M2-09/M2-10
    return False
