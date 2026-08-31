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
import re
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
        if question_type == "mcq" and isinstance(data, dict):
            index = data.get("option_index")
            if isinstance(index, int) and 0 <= index < len(_MCQ_KEYS):
                return [_MCQ_KEYS[index]]
            return [expected]
        if question_type == "true_false" and isinstance(data, dict):
            value = data.get("value")
            if isinstance(value, bool):
                return ["T" if value else "F"]
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


def grade_answer(question_type: str, expected: str, given: str) -> bool | None:
    """判分三态：True/False；None = 不确定，进入人工复核（M2-09 math 等价表达式）。"""
    kind = normalize_type(question_type)
    if not given.strip():
        return False
    if kind == "numeric":
        return grade_numeric(expected, given)
    if kind == "math":
        return grade_math(expected, given)

    if kind == "mcq":
        candidates = _expected_candidates(kind, expected)
        return any(_normalize_text(given) == _normalize_text(c) for c in candidates)

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
        (expected_letter,) = _expected_candidates(kind, expected)
        return _normalize_tf(given) == _normalize_tf(expected_letter)

    if kind == "short_answer":
        normalized_given = _normalize_text(given).lower()
        return any(
            normalized_given == _normalize_text(candidate).lower()
            for candidate in _expected_candidates(kind, expected)
        )

    # coding/essay 等非自动判分题型：objective grader 不判，留给 M2-10
    return False


# ---------- M2-09 Numeric/math grader ----------

# 常用纲量：单位 -> (纲量, 换算到基准的倍率)；同纲量可互换单位比较，跨纲量判错
_UNIT_TABLE: dict[str, tuple[str, float]] = {
    "mm": ("length", 0.001), "cm": ("length", 0.01), "dm": ("length", 0.1),
    "m": ("length", 1.0), "km": ("length", 1000.0),
    "mg": ("mass", 0.001), "g": ("mass", 1.0), "kg": ("mass", 1000.0), "t": ("mass", 1_000_000.0),
    "ms": ("time", 0.001), "s": ("time", 1.0), "min": ("time", 60.0), "h": ("time", 3600.0),
}

_NUMBER_RE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*([a-zA-Zμ°]+)?$")


def _parse_quantity(text: str) -> tuple[float, str | None] | None:
    """解析「数值 + 可选单位」；解析失败返回 None。"""
    match = _NUMBER_RE.match(text.strip().replace("（", "(").replace("）", ")"))
    if not match:
        return None
    return float(match.group(1)), match.group(2)


def grade_numeric(expected: str, given: str) -> bool | None:
    """数值判分：容差 + 单位换算。expected 为 JSON {"value","tolerance","unit"?} 或裸数值。"""
    try:
        data = json.loads(expected) if expected.strip().startswith("{") else {"value": float(expected)}
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or "value" not in data:
        return None
    tolerance = float(data.get("tolerance", 0.0))
    expected_unit = data.get("unit")

    parsed = _parse_quantity(given)
    if parsed is None:
        return None  # 数值/单位无法解析 -> 复核
    given_value, given_unit = parsed

    try:
        target = float(data["value"])
    except (TypeError, ValueError):
        return None

    if expected_unit and given_unit:
        expected_meta = _UNIT_TABLE.get(expected_unit)
        given_meta = _UNIT_TABLE.get(given_unit)
        if expected_meta is None or given_meta is None:
            return None  # 未知单位 -> 复核
        if expected_meta[0] != given_meta[0]:
            return False  # 跨纲量（如 m vs s）必然错
        # 同纲量：两侧都换算到基准单位后按容差比较（容差作用于 expected 单位）
        given_base = given_value * given_meta[1]
        target_base = target * expected_meta[1]
        return abs(given_base - target_base) <= tolerance * expected_meta[1]
    if expected_unit and not given_unit:
        return None  # 期望带单位而作答无单位：无法确认 -> 复核
    if given_unit is not None and given_unit not in _UNIT_TABLE:
        return None  # 无法识别的单位 -> 复核

    return abs(given_value - target) <= tolerance


_LATEX_REPLACEMENTS = (
    ("\\left", ""), ("\\right", ""), ("\\,", ""), ("\\;", ""), ("\\!", ""),
    ("\\cdot", "*"), ("\\times", "*"), ("\\div", "/"),
    ("\\pi", "pi"), ("^", "**"),
)
_FRAC_RE = re.compile(r"\\frac\{([^{}]+)\}\{([^{}]+)\}")

# sympify 底层走 Python eval：进 eval 前强制字符白名单（数字/字母/四则/括号/逗号/空白），
# 拒绝 dunder、下标、引号、花括号等注入面；不满足白名单 → 复核，绝不 eval 不可信文本
_SAFE_EXPR_RE = re.compile(r"[0-9A-Za-z+\-*/().,\s]+")


def _latex_to_expression(latex: str) -> str:
    """把判分所需的简单 LaTeX 子集转为 sympy 可解析表达式文本。"""
    result = latex.strip()
    for old, new in _LATEX_REPLACEMENTS:
        result = result.replace(old, new)
    while True:
        replaced = _FRAC_RE.sub(r"((\1)/(\2))", result)
        if replaced == result:
            break
        result = replaced
    return result


def grade_math(expected: str, given: str) -> bool | None:
    """数学等价表达式：sympy 可用时判定恒等（a-b 化简为 0）；否则不确定进复核。

    expected 为 JSON {"latex": ...} 或裸 latex 串。
    """
    try:
        data = json.loads(expected) if expected.strip().startswith("{") else {}
    except json.JSONDecodeError:
        data = {}
    expected_latex = str(data.get("latex")) if isinstance(data, dict) and data.get("latex") else expected

    expected_expr = _latex_to_expression(expected_latex)
    given_expr = _latex_to_expression(given)
    if not _SAFE_EXPR_RE.fullmatch(expected_expr) or not _SAFE_EXPR_RE.fullmatch(given_expr):
        return None  # 白名单外字符（注入面）→ 复核，绝不进入 sympify/eval
    if expected_expr == given_expr:
        return True  # 归一后字面一致

    try:
        import sympy  # 延迟导入：未部署时降级为复核，不阻塞判分管线
    except ImportError:
        return None

    try:
        difference = sympy.sympify(expected_expr) - sympy.sympify(given_expr)
        return sympy.simplify(difference) == 0
    except (sympy.SympifyError, TypeError, ValueError):
        return None  # 解析失败 = 不确定，进入复核
