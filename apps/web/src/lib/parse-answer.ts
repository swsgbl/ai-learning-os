import type { QuestionOption } from "./types";

// M14-249：语音流程不再在客户端解析/猜测答案——transcript 原文上送，
// 解析与规范化全部由服务端 intent parser（M4-04）/ answer normalizer
// （M4-05）执行；本模块只保留朗读文案构造（分段播报）与展示标签
// （optionLabel/answerLabel，审阅页在用）。

/** 朗读第一段：题头 + 题干（题面朗读真实完成后才发服务端 question_read） */
export function questionHeadText(question: { stem: string }, index: number, total: number) {
  return `第 ${index + 1} 题，共 ${total} 题。${question.stem}`;
}

/** 朗读第二段：选项/作答指引（该段朗读真实完成后才发服务端 options_read） */
export function optionsTailText(question: { type?: string; options?: QuestionOption[] }) {
  if (!question.options?.length) return "请直接作答。";
  if (question.type === "tf") return "请判断正确还是错误。";
  const options = question.options.map((option) => `选项 ${option.key}，${option.text}`).join("。");
  return `${options}。请选择。`;
}

/** 选项点击 → 服务端可靠解析的 transcript 形态（选项点击即文字候选
 * 答案，走 api.voiceSessions.answer）：mcq 用「选 {key}」（服务端字母
 * 槽位模式），判断题用选项文本（true_false 规范化直接读对/错文本）。 */
export function optionClickTranscript(
  question: { type?: string; options?: QuestionOption[] },
  option: QuestionOption,
) {
  if (question.type === "tf") return option.text;
  return `选 ${option.key}`;
}

export function optionLabel(question: PublicQuestionLike, key: string) {
  const option = question.options?.find((item) => item.key === key);
  if (!option) return key;
  if (question.type === "tf") return option.text;
  return `${option.key}. ${option.text}`;
}

// 导入卷答案为 JSON 形态（numeric/math/short_answer），审阅页转可读文本
export function answerLabel(question: PublicQuestionLike, key: string) {
  const text = key.trim();
  if (text.startsWith("{")) {
    try {
      const data = JSON.parse(text) as Record<string, unknown>;
      if (typeof data.latex === "string") return data.latex;
      if ("value" in data) {
        const value = `${data.value}${data.unit ? ` ${String(data.unit)}` : ""}`;
        return "tolerance" in data ? `${value} ±${String(data.tolerance)}` : value;
      }
      if (Array.isArray(data.accepted)) return data.accepted.map(String).join(" / ");
      if (Array.isArray(data.rubric_points)) return data.rubric_points.map(String).join(" / ");
      if (Array.isArray(data.option_indices)) {
        return data.option_indices.map((index) => "ABCDEFGH"[Number(index)] ?? "?").join("");
      }
    } catch {
      // 非 JSON 原样返回
    }
  }
  return optionLabel(question, key);
}

type PublicQuestionLike = { type?: string; options?: QuestionOption[] };
