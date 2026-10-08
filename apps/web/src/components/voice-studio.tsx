"use client";

// M14-249：voice-studio 接入服务端权威 VoiceSession（M14-248 契约层的
// 第一个消费者）。题面、会话状态、已提交答案、规范化结果、澄清文案、
// 语音报告全部以服务端 resume/commands/answers/report 的返回为唯一权威
// ——本组件不维护答案序号、不复制 FSM 转移表、不预解析答案、不自行
// 评分。M14-250：朗读链切到服务端 TTS 通道（speakWithServerTts：
// POST /synthesize → WAV → 播放真实完成才 resolve，读题回执事件时序
// 语义不变；R1：零客户端 tts 埋点——tts 时延由服务端 /synthesize 内
// 自动埋点权威记录，客户端零 /voice/trace 请求）；通道身份经
// GET /providers 透出（tone 降级如实标注，不谎报真实语音）；收尾停止
// 服务端播放。听写（ASR）仍为浏览器原生输入设备（tracedListen 埋点）。
// M14-189 朗读事件化约束保持：朗读只在启动完成回调、切题回调、重复
// 读题按钮的事件链中发起，无 effect 驱动的自动朗读，无 set-state-in-effect。
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Mic, Pause, Play, Repeat } from "lucide-react";
import { ApiError, api } from "@/lib/api";
import { optionClickTranscript } from "@/lib/parse-answer";
import type {
  ExamSession,
  VoiceResumeQuestion,
  VoiceSession,
  VoiceSessionReport,
} from "@/lib/types";
import {
  advanceFlow,
  describeVoiceStartError,
  finishVoiceExam,
  reconcileSession,
  speakQuestionWithEvents,
  startVoiceSession,
  submitAnswerForSession,
  tracedListen,
  voiceUnavailableText,
  type VoiceFlowView,
} from "@/lib/voice-session-flow";
import { speakWithServerTts, stopServerTtsPlayback } from "@/lib/server-tts";
import { ErrorState, LoadingState } from "@/components/states";
import { Waveform } from "@/components/voice/waveform";
import { Button } from "./ui/button";
import { Card } from "./ui/card";

export function VoiceStudio({ paperId }: { paperId: string }) {
  const router = useRouter();
  const [exam, setExam] = useState<ExamSession | null>(null);
  const [view, setView] = useState<VoiceFlowView | null>(null);
  const [report, setReport] = useState<VoiceSessionReport | null>(null);
  const [heard, setHeard] = useState("");
  const [clarify, setClarify] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // M14-250：服务端 TTS 通道身份（providers 视图只读透出；尽力而为，
  // 失败缺省 null——徽标缺席不阻塞作答，也不伪造通道事实）
  const [ttsChannel, setTtsChannel] = useState<{ provider: string; fallback: boolean } | null>(null);
  const started = useRef(false);
  const submitted = useRef(false);
  // 异步回调链读取最新权威上下文（M14-247 同款 ref 模式；render 期不读
  // ref——会话/题目展示一律走 view state，ref 只供回调链取最新值）
  const examIdRef = useRef<string | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const sessionRef = useRef<VoiceSession | null>(null);
  const questionTotalRef = useRef(0);

  // 服务端权威状态进 view（含 session/当前题/已提交答案/总题数）
  const applyServerView = useCallback((next: VoiceFlowView) => {
    sessionRef.current = next.session;
    questionTotalRef.current = next.questionTotal;
    setView(next);
  }, []);

  // 仅替换权威 session（commands/answers 响应回传的最新状态）
  const applyServerSession = useCallback((session: VoiceSession) => {
    sessionRef.current = session;
    setView((current) => (current ? { ...current, session } : current));
  }, []);

  // 收尾（验收 7）：权威语音报告进入视图模型（可断言渲染），保留既有
  // 跳转 review 体验；提交路径停止服务端 TTS 播放（M14-250：离场后
  // 未决播放不再结算、不发读题回执，替代浏览器朗读时代的取消语义）。
  const completeExam = useCallback((authoritativeReport: VoiceSessionReport) => {
    submitted.current = true;
    setReport(authoritativeReport);
    stopServerTtsPlayback();
    const examId = examIdRef.current;
    if (examId) router.push(`/review/${examId}`);
  }, [router]);

  // 终态会话收尾：REPORT_READY 后 submitExam + 权威报告（不自行评分）
  const finishFlow = useCallback(async () => {
    const examId = examIdRef.current;
    const sessionId = sessionIdRef.current;
    if (!examId || !sessionId || submitted.current) return;
    try {
      const outcome = await finishVoiceExam(examId, sessionId);
      completeExam(outcome.report);
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 404) {
        setError(voiceUnavailableText(cause));
        return;
      }
      setError(cause instanceof Error ? cause.message : "提交失败");
    }
  }, [completeExam]);

  // 409 后重新对齐（验收 6）：resume 权威回读，本地不覆盖 FSM；终态分流
  // 收尾；404 如实提示不可用
  const realignSession = useCallback(async () => {
    const sessionId = sessionIdRef.current;
    if (!sessionId) return;
    try {
      const reconciled = await reconcileSession(sessionId);
      if (reconciled.kind === "active") {
        applyServerView(reconciled.view);
        setError("服务端状态已变化，已按服务端返回重新对齐当前题。");
        return;
      }
      await finishFlow();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 404) {
        setError(voiceUnavailableText(cause));
        return;
      }
      setError(cause instanceof Error ? cause.message : "重新对齐失败");
    }
  }, [applyServerView, finishFlow]);

  const handleFlowError = useCallback(async (cause: ApiError) => {
    if (cause.status === 409) {
      await realignSession();
      return;
    }
    if (cause.status === 404) {
      setError(voiceUnavailableText(cause));
      return;
    }
    setError(cause.message);
  }, [realignSession]);

  // M14-189（保持）：朗读为参数化异步入口，仅在启动完成回调、切题回调、
  // 重复读题按钮的事件链调用——无 effect 驱动。M14-249：朗读拆为题面/
  // 选项两段，各段真实完成后才由 flow 层发 question_read/options_read；
  // 朗读失败/中断不发事件不谎报（flow 层保证）。M14-250 R1：朗读直连
  // 服务端 TTS 通道（speakWithServerTts，零客户端 tts 埋点——tts 时延
  // 由服务端 /synthesize 自动埋点权威记录）。
  const speakQuestion = useCallback(
    async (target: VoiceResumeQuestion, order: number, total: number) => {
      const sessionId = sessionIdRef.current;
      const current = sessionRef.current;
      if (!sessionId || !current) return;
      setSpeaking(true);
      setClarify(null);
      try {
        const session = await speakQuestionWithEvents({
          sessionId,
          status: current.status,
          question: target,
          index: order,
          total,
          speakPhase: (text) => speakWithServerTts(text),
        });
        if (session) applyServerSession(session);
      } catch (cause) {
        if (cause instanceof ApiError) await handleFlowError(cause);
        else setError(cause instanceof Error ? cause.message : "语音朗读失败");
      } finally {
        setSpeaking(false);
      }
    },
    [applyServerSession, handleFlowError],
  );

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    // 启动顺序（验收 1）：startExam 成功 → 创建权威 VoiceSession →
    // resume 对齐当前题/已提交答案；404/失败如实暴露（ErrorState），
    // 不伪造可用状态。状态更新全在 promise 回调内（M14-189 约束保持）。
    startVoiceSession(paperId)
      .then(({ exam: startedExam, view: initial }) => {
        examIdRef.current = startedExam.exam_id;
        sessionIdRef.current = initial.session.session_id;
        setExam(startedExam);
        applyServerView(initial);
        // M14-250：服务端 TTS 通道身份（providers 只读视图）——尽力
        // 而为，失败缺省（徽标缺席不阻塞作答）；promise 回调内更新，
        // 非 effect 同步 setState
        void api.voiceProviders().then(
          (providers) => setTtsChannel({ provider: providers.tts.provider, fallback: providers.tts.fallback }),
          () => setTtsChannel(null),
        );
        // M14-189（保持）：首题自动朗读在启动完成回调内（题目就绪即读）
        const first = initial.question;
        if (first) void speakQuestion(first, initial.session.question_index, initial.questionTotal);
      })
      .catch((cause: Error) => setError(describeVoiceStartError(cause)));
  }, [paperId, applyServerView, speakQuestion]);

  const question = view?.question ?? null;

  // 答案提交后的推进（验收 5/7）：以 answers 响应回传的权威 session 为
  // 准——先离开作答状态（commit_confirmed/skip 由 flow 层按服务端
  // status 选择），再按服务端 index/total 判定读下题或收尾（REPORT_READY
  // → submitExam + 权威语音报告）。
  async function advanceFrom(authoritative: VoiceSession) {
    const examId = examIdRef.current;
    const sessionId = sessionIdRef.current;
    if (!examId || !sessionId) return;
    try {
      const outcome = await advanceFlow({
        examId,
        sessionId,
        session: authoritative,
        questionTotal: questionTotalRef.current,
      });
      if (outcome.kind === "question") {
        applyServerView(outcome.view);
        setHeard("");
        setClarify(null);
        const upcoming = outcome.view.question;
        // M14-189（保持）：切题自动朗读在事件回调内（完成分支不朗读）
        if (upcoming) {
          void speakQuestion(upcoming, outcome.view.session.question_index, outcome.view.questionTotal);
        }
        return;
      }
      if (outcome.kind === "finished") {
        // submitExam 与权威语音报告均已成功（flow 层顺序保证）
        completeExam(outcome.outcome.report);
      }
    } catch (cause) {
      if (cause instanceof ApiError) await handleFlowError(cause);
      else setError(cause instanceof Error ? cause.message : "推进失败");
    }
  }

  // 语音与文字候选答案统一走服务端 /answers（验收 4/5）：transcript
  // 原文上送（选项点击也转成服务端可靠解析的 transcript），解析/规范化/
  // 判定全部由服务端执行；event_id 由 flow 层一次逻辑答案恰生成一次
  // （网络重试同键）。accepted → 服务端 normalized_answer 即已提交答案
  // （回读展示 + 自动推进，与原 UX 一致）；accepted=false → 服务端澄清
  // 文案，客户端不猜答案。
  async function submitTranscript(transcript: string) {
    const current = view;
    if (!current?.question) return;
    const trimmed = transcript.trim();
    if (!trimmed) {
      setError("没有识别出有效答案，可以重说或用文字提交。");
      return;
    }
    try {
      const result = await submitAnswerForSession({
        sessionId: current.session.session_id,
        transcript: trimmed,
        questionId: current.question.id,
      });
      if (result.session) applyServerSession(result.session);
      if (!result.accepted) {
        setClarify(result.clarified_question ?? "没有听清，请再说一遍具体选项。");
        return;
      }
      const committed = result.normalized_answer ?? null;
      setView((latest) => (latest ? { ...latest, committedAnswer: committed } : latest));
      setClarify(null);
      const authoritative = result.session ?? sessionRef.current;
      if (authoritative) await advanceFrom(authoritative);
      else await realignSession();
    } catch (cause) {
      if (cause instanceof ApiError) await handleFlowError(cause);
      else setError(cause instanceof Error ? cause.message : "答案同步失败");
    }
  }

  async function listen() {
    const current = view;
    if (!current?.question || busy) return;
    setBusy(true);
    setError(null);
    try {
      // M14-249（验收 8）：asr span 携带真实 voice session_id（create
      // 回传，不再缺省）；trace 尽力而为不影响作答主流程
      const transcript = await tracedListen({
        session_id: current.session.session_id,
        exam_id: examIdRef.current ?? undefined,
        question_id: current.question.id,
      });
      setHeard(transcript);
      await submitTranscript(transcript);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "听写失败");
    } finally {
      setBusy(false);
    }
  }

  async function next() {
    const current = sessionRef.current;
    if (!current || submitted.current) return;
    await advanceFrom(current);
  }

  if (error && !exam) return <ErrorState title="无法开始语音练习" detail={error} />;
  if (!exam || !view || !question)
    return <LoadingState title="正在准备语音练习" detail="正在向服务端申请语音会话。" />;

  const submitTyped = async () => {
    await submitTranscript(heard);
  };

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs text-muted">语音陪练 · 服务端权威会话</p>
          <h1 className="font-display text-2xl">{exam.paper_title}</h1>
        </div>
        <div className="rounded-lg bg-surface px-3 py-2 text-right shadow-border">
          <p className="text-[10px] text-muted">进度</p>
          <p className="font-mono text-lg leading-none tabular-nums">
            {view.session.question_index + 1}/{view.questionTotal}
          </p>
          <p className="mt-1 text-[10px] text-muted">{view.session.status}</p>
          {ttsChannel && (
            <p className="mt-1 text-[10px] text-muted" data-testid="tts-channel">
              TTS {ttsChannel.provider}
              {ttsChannel.fallback ? "（降级替身，非真实语音）" : ""}
            </p>
          )}
        </div>
      </div>

      <Card className="p-5 sm:p-6">
        <p className="text-xs text-muted">{question.type === "tf" ? "判断" : "选择"}</p>
        <p className="mt-3 font-display text-xl leading-snug">{question.stem}</p>
        <ul className="mt-5 space-y-2">
          {question.options?.map((option) => {
            const active = view.committedAnswer === option.key;
            return (
              <li key={option.key}>
                <button
                  type="button"
                  aria-pressed={active}
                  onClick={() => void submitTranscript(optionClickTranscript(question, option))}
                  className={`flex w-full items-start gap-3 rounded-lg px-4 py-3 text-left text-sm shadow-border transition-colors duration-150 outline-offset-2 ${
                    active
                      ? "bg-accent text-accent-fg"
                      : "bg-surface hover:bg-surface-2"
                  }`}
                >
                  <span className="font-medium">{option.key}</span>
                  <span>{option.text}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </Card>

      <Card className="p-4">
        <Waveform active={busy} className="mb-3" />
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="icon"
            onClick={() => void speakQuestion(question, view.session.question_index, view.questionTotal)}
            disabled={speaking}
            aria-label={speaking ? "正在朗读" : "重复读题"}
          >
            <Repeat className={speaking ? "animate-pulse" : undefined} aria-hidden="true" />
          </Button>
          <Button onClick={() => void listen()} disabled={busy} className="flex-1">
            {busy ? <Pause aria-hidden="true" /> : <Mic aria-hidden="true" />}
            {busy ? "聆听中" : "语音作答"}
          </Button>
          <Button variant="outline" onClick={() => void next()}>
            <Play aria-hidden="true" />
            下一题
          </Button>
        </div>
        <form
          className="mt-3 flex flex-col gap-2 sm:flex-row"
          onSubmit={(event) => {
            event.preventDefault();
            void submitTyped();
          }}
        >
          <input
            value={heard}
            onChange={(event) => setHeard(event.target.value)}
            placeholder="也可以打字：选 A / 第二个 / 正确"
            aria-label="文字作答"
            className="h-11 w-full rounded-lg bg-surface px-3 text-sm shadow-border placeholder:text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:flex-1"
          />
          <Button type="submit" variant="outline" disabled={!heard.trim()}>
            提交
          </Button>
        </form>
        {clarify && (
          <p className="mt-2 text-sm text-muted" role="status">
            {clarify}
          </p>
        )}
        {error && (
          <p role="alert" className="mt-2 text-sm text-bad">
            {error}
          </p>
        )}
      </Card>

      {report && (
        <Card className="p-4" data-testid="voice-report">
          <p className="text-xs text-muted">语音报告（服务端权威投影）</p>
          <p className="mt-2 text-sm leading-relaxed">{report.spoken_text}</p>
          {report.mistake_summary.length > 0 && (
            <p className="mt-2 text-xs text-muted">
              错题 {report.mistake_summary.length} 道 · 补救概念 {report.remediation_summary.length} 个 ·
              书面报告 {report.written_report_url}
            </p>
          )}
        </Card>
      )}
    </div>
  );
}
