"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Mic, Pause, Play, Repeat } from "lucide-react";
import { api } from "@/lib/api";
import { optionLabel, parseSpokenAnswer, speakableQuestion } from "@/lib/parse-answer";
import type { ExamSession } from "@/lib/types";
import { Button } from "./ui/button";
import { Card } from "./ui/card";

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

function speakLocal(text: string) {
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

function listenOnce(): Promise<string> {
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

export function VoiceStudio({ paperId }: { paperId: string }) {
  const router = useRouter();
  const [session, setSession] = useState<ExamSession | null>(null);
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [heard, setHeard] = useState("");
  const [candidate, setCandidate] = useState<{ key: string; confidence: "high" | "low" } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);
  const sequence = useRef(0);
  const submitted = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    api
      .startExam(paperId, "voice")
      .then((result) => {
        setSession(result);
        setAnswers(result.answers);
      })
      .catch((cause: Error) => setError(cause.message));
  }, [paperId]);

  const question = session?.questions[index];

  const speakCurrent = useCallback(async () => {
    if (!session || !question) return;
    try {
      await speakLocal(speakableQuestion(question, index, session.questions.length));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "语音朗读失败");
    }
  }, [index, question, session]);

  useEffect(() => {
    if (question) void speakCurrent();
  }, [question, speakCurrent]);

  async function saveAnswer(questionId: string, key: string) {
    if (!session) return;
    sequence.current += 1;
    const nextAnswers = { ...answers, [questionId]: key };
    setAnswers(nextAnswers);
    setCandidate(null);
    try {
      const saved = await api.saveAnswer(session.exam_id, sequence.current, questionId, key);
      setSession(saved);
      setAnswers(saved.answers);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "答案同步失败");
    }
  }

  async function next() {
    if (!session) return;
    if (index + 1 < session.questions.length) {
      setIndex(index + 1);
      setHeard("");
      return;
    }
    if (submitted.current) return;
    submitted.current = true;
    try {
      await api.submitExam(session.exam_id);
      window.speechSynthesis?.cancel();
      router.push(`/review/${session.exam_id}`);
    } catch (cause) {
      submitted.current = false;
      setError(cause instanceof Error ? cause.message : "提交失败");
    }
  }

  async function listen() {
    if (!question) return;
    setBusy(true);
    setError(null);
    try {
      const transcript = await listenOnce();
      setHeard(transcript);
      const parsed = parseSpokenAnswer(transcript, question);
      if (!parsed) {
        setCandidate(null);
        setError("没有识别出有效答案，可以重说或用文字提交。");
      } else if (parsed.confidence === "low") {
        setCandidate(parsed);
      } else {
        await saveAnswer(question.id, parsed.key);
        await next();
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "听写失败");
    } finally {
      setBusy(false);
    }
  }

  if (error && !session) return <Card className="p-6 text-sm text-bad">{error}</Card>;
  if (!session || !question) return <Card className="p-6 text-sm text-muted">正在准备语音练习。</Card>;

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs text-muted">语音陪练</p>
          <h1 className="font-display text-2xl">{session.paper_title}</h1>
        </div>
        <div className="rounded-lg bg-surface px-3 py-2 text-right shadow-border">
          <p className="text-[10px] text-muted">进度</p>
          <p className="font-mono text-lg leading-none tabular-nums">
            {index + 1}/{session.questions.length}
          </p>
        </div>
      </div>

      <Card className="p-5 sm:p-6">
        <p className="text-xs text-muted">{question.type === "tf" ? "判断" : "选择"}</p>
        <p className="mt-3 font-display text-xl leading-snug">{question.stem}</p>
        <ul className="mt-5 space-y-2">
          {question.options?.map((option) => (
            <li key={option.key}>
              <button
                type="button"
                onClick={() => void saveAnswer(question.id, option.key)}
                className={`flex w-full items-start gap-3 rounded-lg px-4 py-3 text-left text-sm shadow-border transition-colors ${
                  answers[question.id] === option.key ? "bg-accent text-accent-fg" : "bg-surface hover:bg-surface-2"
                }`}
              >
                <span className="font-medium">{option.key}</span>
                <span>{option.text}</span>
              </button>
            </li>
          ))}
        </ul>
      </Card>

      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="icon" onClick={() => void speakCurrent()} aria-label="重复读题">
            <Repeat />
          </Button>
          <Button onClick={() => void listen()} disabled={busy} className="flex-1">
            {busy ? <Pause /> : <Mic />}
            {busy ? "聆听中" : "语音作答"}
          </Button>
          <Button variant="outline" onClick={() => void next()}>
            <Play />
            下一题
          </Button>
        </div>
        <form
          className="mt-3 flex gap-2"
          onSubmit={async (event) => {
            event.preventDefault();
            if (!candidate) return;
            await saveAnswer(question.id, candidate.key);
            await next();
          }}
        >
          <input
            value={heard}
            onChange={(event) => setHeard(event.target.value)}
            placeholder="也可以打字：A / 正确 / 选项 C"
            className="h-11 flex-1 rounded-lg bg-surface px-3 text-sm shadow-border placeholder:text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <Button
            type="button"
            variant="outline"
            onClick={async () => {
              const parsed = parseSpokenAnswer(heard, question);
              if (!parsed) {
                setError("没有识别出有效答案。");
                return;
              }
              await saveAnswer(question.id, parsed.key);
              await next();
            }}
          >
            提交
          </Button>
        </form>
        {candidate && (
          <p className="mt-2 text-sm text-muted">
            听起来是 {optionLabel(question, candidate.key)}，点击提交确认。
          </p>
        )}
        {error && <p className="mt-2 text-sm text-bad">{error}</p>}
      </Card>
    </div>
  );
}
