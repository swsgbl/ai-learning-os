import type { PublicQuestion } from "./types";

const KEY_WORDS: Record<string, string> = {
  a: "A",
  b: "B",
  c: "C",
  d: "D",
  甲: "A",
  乙: "B",
  丙: "C",
  丁: "D",
  一: "A",
  二: "B",
  三: "C",
  四: "D",
  第一个: "A",
  第二个: "B",
  第三个: "C",
  第四个: "D",
  对: "T",
  正确: "T",
  是: "T",
  true: "T",
  yes: "T",
  错: "F",
  错误: "F",
  否: "F",
  false: "F",
  no: "F",
};

export function parseSpokenAnswer(raw: string, question: PublicQuestion) {
  const text = raw.trim().toLowerCase().replace(/\s+/g, "");
  if (!text) return null;

  const letter = text.match(/(?:选项|选|option|choose|answer)?\s*([a-d甲乙丙丁abcd])/i);
  if (letter) {
    const key = KEY_WORDS[letter[1].toLowerCase()] ?? letter[1].toUpperCase();
    if (question.options?.some((option) => option.key === key)) {
      return { key, confidence: "high" as const };
    }
  }

  if (question.type === "tf") {
    if (/不对|不是|不正确|错误|false|no/.test(text)) return { key: "F", confidence: "high" as const };
    if (/对的|正确|true|yes|是的/.test(text)) return { key: "T", confidence: "high" as const };
  }

  for (const [word, key] of Object.entries(KEY_WORDS)) {
    if (text === word || text.includes(`选${word}`) || text.includes(`选${key.toLowerCase()}`)) {
      if (question.options?.some((option) => option.key === key)) return { key, confidence: "high" as const };
    }
  }

  const hits = question.options?.filter((option) => {
    const optionText = option.text.toLowerCase().replace(/\s+/g, "");
    return optionText && (text.includes(optionText) || optionText.includes(text));
  });
  if (hits?.length === 1) return { key: hits[0].key, confidence: "low" as const };

  return null;
}

export function optionLabel(question: PublicQuestion | { type?: string; options?: { key: string; text: string }[] }, key: string) {
  const option = question.options?.find((item) => item.key === key);
  if (!option) return key;
  if (question.type === "tf") return option.text;
  return `${option.key}. ${option.text}`;
}

export function speakableQuestion(question: PublicQuestion, index: number, total: number) {
  const head = `第 ${index + 1} 题，共 ${total} 题。${question.stem}`;
  if (!question.options?.length) return `${head}请直接作答。`;
  if (question.type === "tf") return `${head}请判断正确还是错误。`;
  const options = question.options.map((option) => `选项 ${option.key}，${option.text}`).join("。");
  return `${head} ${options}。请选择。`;
}
