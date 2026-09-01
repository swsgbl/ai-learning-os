"""M4-08 Voice report：REPORT_READY 后的语音播报投影（05 文档 §4 全卷报告）。

- 只投影不判定：分数/错题/补救全部来自 M2-11 build_report 的判分结果，
  语音层绝不重新判分（与 ADR 36「最终提交由服务端判定」同款边界）；
- 分层播报：第一遍 = 短结论 + 总分 + 错题数（避免一次性读长报告），
  深入讲解由客户端凭 written_report_url 拉取书面报告；
- 错题摘要按播报友好的字段裁剪（题号/截断题干/你的答案/正确答案/概念），
  补救建议聚合到概念级（同概念多任务合并为一条）；
- 纯函数：同 (报告, exam_id) 恒同输出——与 ADR 27/29/30/31/34/38 同款幂等语义。
"""
from __future__ import annotations

from app.domain.report import ExamReport, MistakeEntry, RemediationTask

_STEM_PREVIEW_LEN = 40


def build_voice_report(exam_id: str, report: ExamReport) -> dict:
    """从书面报告投影语音播报视图（纯函数，只裁剪不判定）。"""
    mistakes = report.mistakes
    mistake_count = len(mistakes)
    total = report.total_count
    correct = report.correct_count
    score = report.score

    if mistake_count == 0:
        conclusion = f"考试完成！本次答对 {correct} 题（共 {total} 题），得分 {score} 分。全部答对，恭喜！"
    else:
        conclusion = f"考试完成！本次答对 {correct} 题（共 {total} 题），得分 {score} 分，共有 {mistake_count} 道错题。需要讲解请查看书面报告或指定错题。"

    mistake_summary = [
        {
            "question_id": m.question_id,
            "stem_preview": m.stem[:_STEM_PREVIEW_LEN] + ("…" if len(m.stem) > _STEM_PREVIEW_LEN > 0 else ""),
            "your_answer": m.given or "未作答",
            "correct_answer": m.expected,
            "concepts": list(m.knowledge),
            "diagnosis": m.diagnosis,
        }
        for m in mistakes
    ]
    return {
        "spoken_text": conclusion,
        "mistake_summary": mistake_summary,
        "remediation_summary": _remediation_summary(mistakes, report.remediation_tasks),
        "written_report_url": f"/api/v1/exams/{exam_id}/report",
    }


def _remediation_summary(
    mistakes: tuple[MistakeEntry, ...],
    tasks: tuple[RemediationTask, ...],
) -> list[dict]:
    """补救建议聚合到概念级：同概念多任务合并一条，按错题顺序。"""
    concepts_in_mistake_order: list[str] = []
    for m in mistakes:
        for concept in m.knowledge:
            if concept not in concepts_in_mistake_order:
                concepts_in_mistake_order.append(concept)
    summary = []
    for concept in concepts_in_mistake_order:
        related = [t for t in tasks if concept in t.title or concept in t.detail]
        actions = dict.fromkeys(t.kind for t in related)
        summary.append(
            {
                "concept": concept,
                "actions": list(actions),
                "detail": related[0].detail if related else "复习该概念并完成变式练习",
                "question_ids": [m.question_id for m in mistakes if concept in m.knowledge],
            }
        )
    return summary
