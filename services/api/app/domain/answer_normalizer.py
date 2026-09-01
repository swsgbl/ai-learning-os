"""M4-05 Answer normalizer：语音答案 → 规范化答案事件（G 提示词）。

「语音答案只生成规范化答案事件，最终提交由服务端判定」：
- 本模块只做规范化（槽位/转写文本 → 标准答案串），不判对错、不落库；
- 规范化语义与判分器（app/domain/grading.py）一致：mcq 字母大写且必须存在于
  选项集、true_false 对/错归一 T/F、short_answer 去空白，essay 不支持语音作答；
- 输出规范化事件草案（question_id/answer/intent/transcript/source），由 API 层
  经 exam repository 确定性提交（M4-03 语义）并记录事件（event_id 幂等）。

槽位无效（选项不存在/序号超界/题型不支持）→ is_valid=False + reason，
上游转澄清——绝不猜答案、绝不提交半成品。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.domain.grading import normalize_type
from app.domain.models import Question

SOURCE_VOICE = "voice"

_QTYPE_LABELS = {
    "essay": "该题型为主观题，暂不支持语音作答，请使用书面作答",
}


@dataclass(frozen=True, slots=True)
class NormalizedAnswer:
    answer: str | None  # 规范化答案（mcq: "B"；true_false: "T"/"F"；short: 规范文本）
    is_valid: bool
    reason: str | None  # 无效原因（供澄清文案）；有效时为 None
    event: dict  # 规范化答案事件草案（不含判定结果）


def _invalid(question: Question, reason: str, transcript: str, intent: str, confidence: float) -> NormalizedAnswer:
    return NormalizedAnswer(
        answer=None,
        is_valid=False,
        reason=reason,
        event={
            "question_id": question.id,
            "answer": None,
            "intent": intent,
            "transcript": transcript,
            "source": SOURCE_VOICE,
            "confidence": confidence,
        },
    )


def normalize_answer(
    question: Question,
    *,
    letter: str | None = None,
    ordinal: int | None = None,
    transcript: str = "",
    intent: str = "choose_option",
    confidence: float = 0.0,
) -> NormalizedAnswer:
    """把解析槽位/转写规范化为标准答案；同输入恒同输出。

    - mcq：letter 大写且必须 ∈ 选项 keys；ordinal 1-based 映射字母且不得超界；
    - true_false：letter/ordinal 不适用，直接看转写文本（对/正确/T → T，错/错误/F → F）；
    - short_answer：转写去首尾空白作为答案草案（判定仍由服务端判分器执行）；
    - essay：不支持语音作答。
    """
    qtype = normalize_type(question.type)
    if qtype == "essay":
        return _invalid(question, _QTYPE_LABELS["essay"], transcript, intent, confidence)

    if qtype == "mcq":
        keys = {option.key.upper(): option.key for option in question.options}
        normalized_letter = letter.upper() if letter else None
        if normalized_letter is None and ordinal is not None:
            if ordinal < 1 or ordinal > len(question.options):
                return _invalid(
                    question,
                    f"选项序号 {ordinal} 超出本题 {len(question.options)} 个选项",
                    transcript,
                    intent,
                    confidence,
                )
            normalized_letter = chr(ord("A") + ordinal - 1)
        if normalized_letter is None:
            return _invalid(question, "没有听清具体选项", transcript, intent, confidence)
        if normalized_letter not in keys:
            valid = "/".join(sorted(keys))
            return _invalid(
                question, f"选项 {normalized_letter} 不存在，可选：{valid}", transcript, intent, confidence
            )
        answer = keys[normalized_letter]
    elif qtype == "true_false":
        answer = _normalize_tf_text(transcript)
        if answer is None:
            return _invalid(question, "没有听清判断（请说「对」或「错」）", transcript, intent, confidence)
    elif qtype == "short_answer":
        answer = transcript.strip()
        if not answer:
            return _invalid(question, "没有听清作答内容", transcript, intent, confidence)
    else:
        return _invalid(question, f"题型 {qtype} 暂不支持语音作答", transcript, intent, confidence)

    return NormalizedAnswer(
        answer=answer,
        is_valid=True,
        reason=None,
        event={
            "question_id": question.id,
            "answer": answer,
            "intent": intent,
            "transcript": transcript,
            "source": SOURCE_VOICE,
            "confidence": confidence,
        },
    )


def _normalize_tf_text(transcript: str) -> str | None:
    """对/错口语 → T/F；与 grading._normalize_tf 同语义。"""
    text = (transcript or "").strip().lower()
    if text in {"t", "true", "对", "正确", "是", "yes", "y"} or any(k in text for k in ("对", "正确")):
        return "T"
    if text in {"f", "false", "错", "错误", "否", "no", "n"} or any(k in text for k in ("错", "不正确", "不对")):
        return "F"
    return None
