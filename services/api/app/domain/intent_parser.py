"""M4-04 Intent parser：中文语音指令 → FSM 命令与答案槽位（G 提示词）。

零 LLM 依赖的确定性规则解析——同 transcript 恒同输出（ADR 27/29/30/31 同款幂等语义）。
覆盖 G 提示词示例：「我选第二个」→ ordinal=2（M4-05 结合选项数映射字母）、
「选 B」→ letter=B、「我改成 C」→ change_answer letter=C、「重复一遍」→ repeat_question。

意图 → M4-03 FSM 命令映射（to_fsm_command，M4-06 补齐 pause/resume 接入）：
- choose_option / change_answer → answer_proposed（change 在 FSM 层即覆盖提交）；
- repeat_question / repeat_options / slow_down / skip / pause / resume / end → 同名命令；
- unknown（未识别）不应用 FSM——解析失败≠澄清答案，不虚报理解。

槽位：letter（选项字母，恒大写）或 ordinal（选项序号，1-based）；
答案意图但抽不出有效槽位（「我选那个」）→ ambiguous=True，上游走澄清。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

INTENT_CHOOSE = "choose_option"
INTENT_CHANGE = "change_answer"
INTENT_REPEAT_QUESTION = "repeat_question"
INTENT_REPEAT_OPTIONS = "repeat_options"
INTENT_SLOW_DOWN = "slow_down"
INTENT_SKIP = "skip"
INTENT_PAUSE = "pause"
INTENT_RESUME = "resume"
INTENT_END = "end"
INTENT_UNKNOWN = "unknown"

FSM_COMMANDS: dict[str, str | None] = {
    INTENT_CHOOSE: "answer_proposed",
    INTENT_CHANGE: "answer_proposed",
    INTENT_REPEAT_QUESTION: "repeat_question",
    INTENT_REPEAT_OPTIONS: "repeat_options",
    INTENT_SLOW_DOWN: "slow_down",
    INTENT_SKIP: "skip",
    INTENT_END: "end",
    INTENT_PAUSE: "pause",  # M4-06：播报控制自环，任意非终态可暂停/恢复
    INTENT_RESUME: "resume",
    INTENT_UNKNOWN: None,
}

_CHANGE_PATTERNS = (
    re.compile(r"(?:我)?(?:改[成选]|换成|换作|变更为)\s*第?\s*([a-zA-Z])\s*(?:个?选项?|答案)?"),
    re.compile(r"(?:我)?(?:改[成选]|换成|换作|变更为)\s*第\s*([一二三四五六七八九十\d]+)\s*[个项]"),
)
_CHOOSE_LETTER_PATTERNS = (
    re.compile(r"(?:我)?(?:选|选择|选定)\s*([a-zA-Z])\s*(?:个?选项?)?"),
    re.compile(r"([a-zA-Z])\s*(?:个?选项)"),
    re.compile(r"(?:我的)?(?:答案是?|答案选)\s*([a-zA-Z])"),
    re.compile(r"就\s*([a-zA-Z])\s*(?:吧|了)?"),
)
_ORDINAL_RE = re.compile(r"第\s*([一二三四五六七八九十\d]+)\s*[个项]")
_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_REPEAT_QUESTION_RE = re.compile(r"重复|再说?一?遍|再来?一?遍|没听清|再读一?遍|重来")
_REPEAT_OPTIONS_RE = re.compile(r"选项|答案项")
_SLOW_DOWN_RE = re.compile(r"慢(?:一?点|些)|说慢|太?快了")
_SKIP_RE = re.compile(r"跳过|下一[题道]|这题不会|不会.*跳过|pass", re.IGNORECASE)
_PAUSE_RE = re.compile(r"暂停|停一下|先停")
_RESUME_RE = re.compile(r"继续|恢复")
_END_RE = re.compile(r"结束|交卷|收卷|做完了|完成考试")
_ANSWER_HINT_RE = re.compile(r"选[个择定那这]|选\s*[a-zA-Z]|[a-zA-Z]\s*个?选项|答案|第.{0,2}[个项]|就选")


@dataclass(frozen=True, slots=True)
class ParsedIntent:
    intent: str
    letter: str | None  # 选项字母（恒大写）——choose/change 槽位
    ordinal: int | None  # 选项序号（1-based）——choose/change 槽位
    ambiguous: bool  # 答案意图但槽位无效 → 上游应澄清
    matched_text: str

    @property
    def fsm_command(self) -> str | None:
        return FSM_COMMANDS[self.intent]


def _ordinal_value(raw: str) -> int:
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    if raw == "十":
        return 10
    if raw.startswith("十"):
        return 10 + _CN_DIGITS.get(raw[1], 0)
    if raw.endswith("十"):
        return _CN_DIGITS.get(raw[0], 0) * 10
    if "十" in raw:
        tens, ones = raw.split("十", 1)
        return _CN_DIGITS.get(tens, 0) * 10 + _CN_DIGITS.get(ones, 0)
    return _CN_DIGITS.get(raw, 0)


def parse(transcript: str) -> ParsedIntent:
    """解析语音转写为意图+槽位；确定性：同输入恒同输出。

    扫描顺序即优先级：精确槽位抽取（改答案→选字母→选序号）> 改答案无槽位
    > 会话命令（结束/跳过/重复/慢速/暂停/继续）> 答案模糊回退 > unknown。
    命令类意图按关键词唯一命中；答案类意图按模式抽取槽位。
    """
    text = (transcript or "").strip()
    if not text:
        return ParsedIntent(INTENT_UNKNOWN, None, None, False, "")

    # 1) 精确槽位抽取：改答案（携带槽位）优先于普通选择
    for pattern in _CHANGE_PATTERNS:
        if match := pattern.search(text):
            return _answer_intent(INTENT_CHANGE, match, text)

    # 2) 选字母
    for pattern in _CHOOSE_LETTER_PATTERNS:
        if match := pattern.search(text):
            return _answer_intent(INTENT_CHOOSE, match, text)

    # 3) 选序号
    if ordinal_match := _ORDINAL_RE.search(text):
        return _answer_intent(INTENT_CHOOSE, ordinal_match, text)

    # 4) 改答案但没说改成什么
    if re.search(r"改[成选]|换成", text):
        return ParsedIntent(INTENT_CHANGE, None, None, True, text)

    # 5) 会话控制命令（唯一关键词命中；「重复选项」归 repeat_options）
    if _END_RE.search(text):
        return ParsedIntent(INTENT_END, None, None, False, text)
    if _SKIP_RE.search(text):
        return ParsedIntent(INTENT_SKIP, None, None, False, text)
    if _REPEAT_QUESTION_RE.search(text):
        if _REPEAT_OPTIONS_RE.search(text):
            return ParsedIntent(INTENT_REPEAT_OPTIONS, None, None, False, text)
        return ParsedIntent(INTENT_REPEAT_QUESTION, None, None, False, text)
    if _SLOW_DOWN_RE.search(text):
        return ParsedIntent(INTENT_SLOW_DOWN, None, None, False, text)
    if _PAUSE_RE.search(text):
        return ParsedIntent(INTENT_PAUSE, None, None, False, text)
    if _RESUME_RE.search(text):
        return ParsedIntent(INTENT_RESUME, None, None, False, text)

    # 6) 提到选择但抽不出槽位（「我选那个」「选这个」）→ 澄清
    if _ANSWER_HINT_RE.search(text):
        return ParsedIntent(INTENT_CHOOSE, None, None, True, text)

    return ParsedIntent(INTENT_UNKNOWN, None, None, False, text)


def _answer_intent(intent: str, match: re.Match, text: str) -> ParsedIntent:
    raw = match.group(1)
    if raw.isascii() and raw.isalpha():  # 仅 ASCII 字母算选项字母（中文「二」是序号）
        return ParsedIntent(intent, raw.upper(), None, False, match.group(0))
    ordinal = _ordinal_value(raw)
    if ordinal < 1:
        return ParsedIntent(intent, None, None, True, text)  # 序号无效 → 澄清
    return ParsedIntent(intent, None, ordinal, False, match.group(0))


def ordinal_to_letter(ordinal: int, option_count: int) -> str | None:
    """序号 → 选项字母（1-based，A 起）；超出选项数返回 None（由上游澄清）。"""
    if ordinal < 1 or ordinal > option_count:
        return None
    return chr(ord("A") + ordinal - 1)
