// M14-249：浏览器语音 IO 薄封装（自 voice-studio 抽出）。浏览器
// SpeechRecognition / speechSynthesis 只是输入输出设备——会话状态、
// 题面、答案规范化、报告全部以服务端 VoiceSession 为唯一权威，本模块
// 不做任何解析或判定。导出纯浏览器能力供 flow 层与组件注入使用。
type RecognitionEvent = {
  results: ArrayLike<ArrayLike<{ transcript: string }>>;
};

type Recognition = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  start: () => void;
  stop: () => void;
  onresult: ((event: RecognitionEvent) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
};

type RecognitionConstructor = new () => Recognition;

function recognitionConstructor(): RecognitionConstructor | null {
  if (typeof window === "undefined") return null;
  const target = window as Window & { SpeechRecognition?: RecognitionConstructor; webkitSpeechRecognition?: RecognitionConstructor };
  return target.SpeechRecognition ?? target.webkitSpeechRecognition ?? null;
}

/** 单段朗读：onend 才 resolve（真实完成），onerror/不支持则 reject——
 * 调用方（flow 层）只在 resolve 后发对应的读题完成事件，绝不谎报。 */
export function speakUtterance(text: string) {
  return new Promise<void>((resolve, reject) => {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      reject(new Error("当前浏览器不支持语音朗读"));
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "zh-CN";
    utterance.onend = () => resolve();
    utterance.onerror = () => reject(new Error("语音朗读失败"));
    window.speechSynthesis.speak(utterance);
  });
}

/** 单次听写：返回原始 transcript（不解析、不猜答案——解析与规范化
 * 由服务端 intent parser / answer normalizer 执行）。 */
export function listenOnce(): Promise<string> {
  return new Promise((resolve, reject) => {
    const Recognition = recognitionConstructor();
    if (!Recognition) {
      reject(new Error("当前浏览器不支持本地听写"));
      return;
    }
    const recognition = new Recognition();
    recognition.lang = "zh-CN";
    recognition.interimResults = false;
    recognition.continuous = false;
    recognition.onresult = (event) => resolve(event.results[0]?.[0]?.transcript ?? "");
    recognition.onerror = (event) => reject(new Error(event.error === "no-speech" ? "没有听到声音" : event.error));
    recognition.start();
  });
}
