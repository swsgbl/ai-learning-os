"""M6-02 Voice eval set：语音意图解析评测集（噪声/口音/打断/数字/公式/命令）。

- 30 案覆盖六类语音条件，每案 (case_id, category, transcript,
  expected_intent, expected_letter, expected_ordinal, expected_ambiguous)；
- expected 全部先经 parser 探针实测后固化——探针暴露两个真实缺口
  （「等等 我选的是A」→unknown 应为 choose A；「选项再念一遍」→unknown
  应为 repeat_options），先修 parser 再固化预期，不虚报；
- run_voice_eval 逐案 parse 对比四字段，accuracy=matched/total 可计算，
  per_category 统计，mismatch 全量透出（不虚报准确率）；
- 报告无时间戳无随机——同输入两次字节级一致（ADR 49 同款确定性）。
"""
from __future__ import annotations

from app.domain.intent_parser import parse

VOICE_EVAL_VERSION = "voice-eval-v1"

VOICE_CATEGORIES = ("noise", "accent", "interruption", "numeric", "formula", "command")

# (case_id, category, transcript, intent, letter, ordinal, ambiguous)
GOLDEN_VOICE_CASES: tuple[tuple, ...] = (
    # ---------- 噪声 6 ----------
    ("V-noise-01", "noise", "嗯……我选 B", "choose_option", "B", None, False),
    ("V-noise-02", "noise", "呃 那个 选 C", "choose_option", "C", None, False),
    ("V-noise-03", "noise", "[杂音] 选 D", "choose_option", "D", None, False),
    ("V-noise-04", "noise", "外面太吵 没听清 再说一遍", "repeat_question", None, None, False),
    ("V-noise-05", "noise", "嗯...那个...跳过", "skip", None, None, False),
    ("V-noise-06", "noise", "izzz 嗯", "unknown", None, None, False),
    # ---------- 口音/方言 4：方言词形不破坏槽位抽取 ----------
    ("V-accent-01", "accent", "俺选A", "choose_option", "A", None, False),
    ("V-accent-02", "accent", "咱选 C 吧", "choose_option", "C", None, False),
    ("V-accent-03", "accent", "咱选D", "choose_option", "D", None, False),
        ("V-accent-04", "accent", "偶选B", "choose_option", "B", None, False),
    # ---------- 数字 5：中文/阿拉伯序号 + 数值答案转澄清 ----------
    ("V-num-01", "numeric", "第五个", "choose_option", None, 5, False),
    ("V-num-02", "numeric", "第5个", "choose_option", None, 5, False),
    ("V-num-03", "numeric", "第十个", "choose_option", None, 10, False),
    ("V-num-04", "numeric", "我改成第十二个", "change_answer", None, 12, False),
    ("V-num-05", "numeric", "答案是42", "choose_option", None, None, True),
    # ---------- 公式 4：公式陈述不误标意图；公式干扰下槽位仍稳 ----------
    ("V-formula-01", "formula", "x的平方加一等于多少", "unknown", None, None, False),
    ("V-formula-02", "formula", "2x+1大于3 所以选C", "choose_option", "C", None, False),
    ("V-formula-03", "formula", "选B 因为 x²-1=(x-1)(x+1)", "choose_option", "B", None, False),
    ("V-formula-04", "formula", "答案选C 其中 x²-1", "choose_option", "C", None, False),
    # ---------- 命令 6：会话控制命令全谱（V-cmd-02 为探针缺口修复案） ----------
    # ---------- 打断 5：打断词先行不阻断后续意图与槽位（V-int-02 为探针缺口修复案） ----------
    ("V-int-01", "interruption", "打住 打住 改成D", "change_answer", "D", None, False),
    ("V-int-02", "interruption", "等等 我选的是A", "choose_option", "A", None, False),
    ("V-int-03", "interruption", "别念了 跳过", "skip", None, None, False),
    ("V-int-04", "interruption", "停一下 我改成C", "change_answer", "C", None, False),
    ("V-int-05", "interruption", "等等 重复一遍", "repeat_question", None, None, False),
    ("V-cmd-01", "command", "重复一遍", "repeat_question", None, None, False),
    ("V-cmd-02", "command", "选项再念一遍", "repeat_options", None, None, False),
    ("V-cmd-03", "command", "说慢一点", "slow_down", None, None, False),
    ("V-cmd-04", "command", "暂停", "pause", None, None, False),
        ("V-cmd-05", "command", "继续", "resume", None, None, False),
    ("V-cmd-06", "command", "交卷", "end", None, None, False),
)


def _case_dict(case: tuple) -> dict:
    return {
        "case_id": case[0], "category": case[1], "transcript": case[2],
        "expected": {"intent": case[3], "letter": case[4],
                     "ordinal": case[5], "ambiguous": case[6]},
    }


def judge_voice_case(case: tuple) -> dict:
    """单案判定：parse 后对比 (intent, letter, ordinal, ambiguous) 四字段。"""
    spec = _case_dict(case)
    parsed = parse(spec["transcript"])
    expected = spec["expected"]
    actual = {
        "intent": parsed.intent,
        "letter": parsed.letter,
        "ordinal": parsed.ordinal,
        "ambiguous": parsed.ambiguous,
    }
    matched = (
        actual["intent"] == expected["intent"]
        and actual["letter"] == expected["letter"]
        and actual["ordinal"] == expected["ordinal"]
        and actual["ambiguous"] == expected["ambiguous"]
    )
    return {"case_id": spec["case_id"], "matched": matched, "actual": actual}


def run_voice_eval(cases: tuple = GOLDEN_VOICE_CASES) -> dict:
    """全量评测：accuracy/per_category/mismatches；无时间戳无随机——确定性可重复。"""
    if not cases:
        raise ValueError("评测集不能为空")
    results = [judge_voice_case(case) for case in cases]
    matched_count = sum(1 for r in results if r["matched"])
    total = len(results)
    by_id = {c[0]: c for c in cases}
    per_category: dict[str, dict] = {}
    for case in cases:
        entry = per_category.setdefault(case[1], {"total": 0, "matched": 0})
        entry["total"] += 1
    for r in results:
        cat = by_id[r["case_id"]][1]
        per_category[cat]["matched"] += 1 if r["matched"] else 0
    mismatches = [
        {
            "case_id": r["case_id"],
            "category": by_id[r["case_id"]][1],
            "transcript": by_id[r["case_id"]][2],
            "expected": _case_dict(by_id[r["case_id"]])["expected"],
            "actual": r["actual"],
            "note": "expected 与实际 parse 不一致——评测集透明透出，不虚报准确率",
        }
        for r in results if not r["matched"]
    ]
    return {
        "rule_versions": {"intent": "rule-intent-v1", "eval": VOICE_EVAL_VERSION},
        "case_count": total,
        "categories_covered": sorted(per_category),
        "coverage_complete": set(VOICE_CATEGORIES).issubset(set(per_category)),
        "accuracy": matched_count / total,
        "per_category": per_category,
        "mismatches": mismatches,
    }
