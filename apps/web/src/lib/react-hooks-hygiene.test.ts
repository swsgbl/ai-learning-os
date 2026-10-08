// M14-189 TDD：react-hooks warning 清零（11 条）的行为保持契约。
// 仓库无 DOM 测试依赖（vitest node 环境，与 sw-contract / download-page
// 同思路）：能跑的用单元测试（unansweredCount 纯函数），组件形态用源码
// 契约钉住——每条断言对应一处可能的行为回归点，失败 = M14-189 修改被
// 意外破坏或语义漂移，review 前必须拦下。
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { draftQueueFeedback } from "../components/governance/draft-queue";
import { unansweredCount } from "../components/exam/submit-dialog";

function readComponent(...segments: string[]): string {
  return readFileSync(join(__dirname, "../components", ...segments), "utf8");
}

const GSAP_SOURCE = readFileSync(join(__dirname, "gsap.ts"), "utf8");
const NAV_SOURCE = readComponent("exam", "question-nav.tsx");
const PANEL_SOURCE = readComponent("exam", "question-panel.tsx");
const DIALOG_SOURCE = readComponent("exam", "submit-dialog.tsx");
const AUDIT_SOURCE = readComponent("governance", "audit-log.tsx");
const DRAFT_SOURCE = readComponent("governance", "draft-queue.tsx");
const LIBRARY_SOURCE = readComponent("library", "library-view.tsx");
const PICKER_SOURCE = readComponent("papers", "paper-picker.tsx");
const PROGRESS_SOURCE = readComponent("progress", "progress-workbench.tsx");
const VOICE_SOURCE = readComponent("voice-studio.tsx");
const DOWNLOAD_SOURCE = readComponent("download", "download-panel.tsx");

// M14-189 修改面：全部 11 个文件 —— 卫生底线：无任何 eslint-disable 掩盖
const M14_189_SOURCES: Array<[string, string]> = [
  ["gsap.ts", GSAP_SOURCE],
  ["question-nav.tsx", NAV_SOURCE],
  ["question-panel.tsx", PANEL_SOURCE],
  ["submit-dialog.tsx", DIALOG_SOURCE],
  ["audit-log.tsx", AUDIT_SOURCE],
  ["draft-queue.tsx", DRAFT_SOURCE],
  ["library-view.tsx", LIBRARY_SOURCE],
  ["paper-picker.tsx", PICKER_SOURCE],
  ["progress-workbench.tsx", PROGRESS_SOURCE],
  ["voice-studio.tsx", VOICE_SOURCE],
  ["download-panel.tsx", DOWNLOAD_SOURCE],
];

describe("M14-189 卫生底线（不引入 eslint-disable，不弱化配置）", () => {
  it("全部 11 个修改文件不含任何 eslint-disable", () => {
    for (const [name, source] of M14_189_SOURCES) {
      expect(source, name).not.toContain("eslint-disable");
    }
  });

  it("eslint.config.mjs 的 M14-05 降级配置原样保留（本切片只清代码不动配置）", () => {
    const config = readFileSync(join(__dirname, "../../eslint.config.mjs"), "utf8");
    expect(config).toContain('"react-hooks/refs": "warn"');
    expect(config).toContain('"react-hooks/set-state-in-effect": "warn"');
  });
});

describe("gsap.ts：usePrefersReducedMotion 改 useSyncExternalStore（语义等价）", () => {
  it("经 useSyncExternalStore 订阅（外部媒体查询即外部 store）", () => {
    expect(GSAP_SOURCE).toContain("useSyncExternalStore");
    expect(GSAP_SOURCE).toMatch(/addEventListener\("change", onChange\)/);
    expect(GSAP_SOURCE).toMatch(/removeEventListener\("change", onChange\)/);
  });

  it("SSR/水合快照恒 false（服务端渲染分支不变）", () => {
    expect(GSAP_SOURCE).toMatch(/getReducedMotionServerSnapshot\(\): boolean \{\s*return false;/);
  });

  it("查询串仍为 prefers-reduced-motion: reduce", () => {
    expect(GSAP_SOURCE).toContain('"(prefers-reduced-motion: reduce)"');
  });

  it("useMotion 的 matchMedia 双分支与 revert 兜底保留（动效语义不动）", () => {
    expect(GSAP_SOURCE).toContain('mm.add("(prefers-reduced-motion: reduce)"');
    expect(GSAP_SOURCE).toContain('mm.add("(prefers-reduced-motion: no-preference)"');
    expect(GSAP_SOURCE).toContain("mm.revert()");
  });
});

describe("download-panel.tsx：standalone 派生 + 安装事件订阅", () => {
  it("standalone 经 useSyncExternalStore 派生（display-mode 媒体查询）", () => {
    expect(DOWNLOAD_SOURCE).toContain("useSyncExternalStore");
    expect(DOWNLOAD_SOURCE).toContain('"(display-mode: standalone)"');
  });

  it("beforeinstallprompt 仍 preventDefault 并升级 promptable（安装按钮语义）", () => {
    expect(DOWNLOAD_SOURCE).toContain("beforeinstallprompt");
    expect(DOWNLOAD_SOURCE).toMatch(/e\.preventDefault\(\)/);
    expect(DOWNLOAD_SOURCE).toContain('kind: "promptable"');
  });

  it("300ms 探测收尾与 iOS/通用分流保留（无事件平台指引不变）", () => {
    expect(DOWNLOAD_SOURCE).toMatch(/setTimeout\(/);
    expect(DOWNLOAD_SOURCE).toContain("300");
    expect(DOWNLOAD_SOURCE).toContain("detectIOS");
    expect(DOWNLOAD_SOURCE).toContain('kind: "unsupported-ios"');
    expect(DOWNLOAD_SOURCE).toContain('kind: "unsupported"');
  });

  it("appinstalled 事件仍升级 installed", () => {
    expect(DOWNLOAD_SOURCE).toContain("appinstalled");
    expect(DOWNLOAD_SOURCE).toContain('kind: "installed"');
  });
});

describe("question-nav.tsx：脉冲动画由事件目标驱动（render 期不读 ref）", () => {
  it("pulse 接收点击元素，onClick 传 event.currentTarget", () => {
    expect(NAV_SOURCE).toMatch(/contextSafe\(\(el: HTMLElement, answered: boolean\)/);
    expect(NAV_SOURCE).toContain("pulse(event.currentTarget, answered)");
  });

  it("render 期不再读取 rootRef.current（ref 仅作 scope 绑定）", () => {
    const renderBody = NAV_SOURCE.slice(NAV_SOURCE.indexOf("const pulse"));
    expect(renderBody).not.toContain("rootRef.current");
  });
});

describe("question-panel.tsx：AnswerInput 草稿重置改 render 期官方模式", () => {
  it("prevValue 比较式当帧重置（题目切换草稿同步生效）", () => {
    expect(PANEL_SOURCE).toContain("prevValue !== value");
    expect(PANEL_SOURCE).toMatch(/setPrevValue\(value\);\s*setDraft\(value\);/);
  });

  it("不再有 effect 内同步 setDraft", () => {
    expect(PANEL_SOURCE).not.toMatch(/useEffect\(\(\) => \{\s*setDraft/);
  });

  it("非受控输入与保存按钮语义保留（disabled 随 draft 与 value 比较）", () => {
    expect(PANEL_SOURCE).toContain("disabled={!draft.trim() || draft === value}");
  });
});

describe("submit-dialog.tsx：未答题数纯派生（无 state 镜像）", () => {
  it("unansweredCount 纯函数：常规/边界（已答超计不下负数）", () => {
    expect(unansweredCount(10, 3)).toBe(7);
    expect(unansweredCount(10, 0)).toBe(10);
    expect(unansweredCount(10, 10)).toBe(0);
    expect(unansweredCount(5, 9)).toBe(0);
    expect(unansweredCount(0, 0)).toBe(0);
  });

  it("组件内不再有 unanswered state/effect 镜像", () => {
    expect(DIALOG_SOURCE).not.toContain("setUnanswered");
    expect(DIALOG_SOURCE).toContain("unansweredCount(total, answered)");
  });

  it("焦点圈定/滚动锁定/ref 同步等既有可访问性行为原样保留", () => {
    expect(DIALOG_SOURCE).toContain("keydown");
    expect(DIALOG_SOURCE).toContain("overflow");
    expect(DIALOG_SOURCE).toContain("submittingRef.current = submitting");
  });
});

describe("数据加载组件：load/reload 拆分（mount effect 零同步 setState）", () => {
  const cases: Array<[string, string, string]> = [
    ["audit-log.tsx", AUDIT_SOURCE, ".audit(100)"],
    ["draft-queue.tsx", DRAFT_SOURCE, ".load()"],
    ["library-view.tsx", LIBRARY_SOURCE, ".papers()"],
    ["paper-picker.tsx", PICKER_SOURCE, ".papers()"],
    ["progress-workbench.tsx", PROGRESS_SOURCE, "Promise.allSettled"],
  ];

  it.each(cases)("%s：加载走 promise 链回调，effect 体内零同步 setState", (_name, source, fetchCall) => {
    expect(source).toContain(fetchCall);
    // load 本体是 promise 链（then/catch 回调路径），非 async/await 直线
    const loadMatch = source.match(/const load(?:All)? = useCallback\(\(\) => \{[\s\S]*?\n  \}, \[/);
    expect(loadMatch).not.toBeNull();
    expect(loadMatch![0]).not.toContain("await ");
    // mount/auth effect 直接调 load（effect 体内不含下一个 effect / await）
    expect(source).toMatch(
      /useEffect\(\(\) => \{(?:(?!useEffect)[\s\S])*?\bload(?:All)?\(\);\s*\n\s*\}, \[/,
    );
  });

  it.each(cases)("%s：刷新/重试入口 reload 保留「先清旧反馈再加载」", (_name, source) => {
    const reloadMatch = source.match(
      /const reload(?:All)? = useCallback\(\(\) => \{[\s\S]*?\n  \}, \[load(?:All)?\]\);/,
    );
    expect(reloadMatch).not.toBeNull();
    expect(reloadMatch![0]).toMatch(/set(?:Error|Forbidden|PlanError|StatesError|PapersError)\(null\)/);
    expect(reloadMatch![0]).toMatch(/load(?:All)?\(\);/);
    // 用户入口（刷新按钮 / ErrorState onRetry）挂的是 reload（引用或箭头包装均可）
    expect(source).toMatch(/on(?:Retry|Click)=\{[^}]*\breload(?:All)?(?:\(\))?\s*\}/);
  });

  it("progress-workbench：auth 未定（null）不发受保护请求的门语义保留", () => {
    expect(PROGRESS_SOURCE).toMatch(
      /auth\?\.mode === "disabled" \|\| auth\?\.mode === "authenticated"/,
    );
  });

  it("audit-log / draft-queue：403 与错误分流语义保留", () => {
    expect(AUDIT_SOURCE).toMatch(/status === 403/);
    expect(DRAFT_SOURCE).toMatch(/status === 403/);
  });

  it("draft-queue：config 变化后异步结果覆盖旧互斥反馈", () => {
    // 上一轮 403，本轮普通失败：清 forbidden，仅保留 error。
    expect(draftQueueFeedback({ kind: "forbidden" })).toEqual({
      error: null,
      forbidden: true,
    });
    expect(
      draftQueueFeedback({
        kind: "failure",
        message: "changed config failed",
      }),
    ).toEqual({
      error: "changed config failed",
      forbidden: false,
    });

    // 上一轮普通失败，本轮成功：清 error，同时不得显示旧 forbidden。
    expect(draftQueueFeedback({ kind: "success" })).toEqual({
      error: null,
      forbidden: false,
    });
  });
});

// M14-249：voice-studio 接入服务端权威 VoiceSession 后的等价约束——
// 朗读仍全程事件化（启动/切题/重复读题按钮），唯一的 effect 是启动链；
// M14-250：朗读链切到服务端 TTS 通道（speechSynthesis 退出、收尾改
// stopServerTtsPlayback、通道身份经 providers 视图透出）。
// 行为级验证（create→resume 顺序、读题事件时序、event_id 幂等、409
// realign、REPORT_READY 收尾、trace session_id、服务端 TTS 通道契约）
// 在 voice-session-flow.test.ts 与 server-tts.test.ts。
describe("voice-studio.tsx：朗读事件化（自动朗读不经 effect）+ 服务端权威接入", () => {
  it("原「question?.id 变化即重读」的自动朗读 effect 已移除", () => {
    expect(VOICE_SOURCE).not.toMatch(/useEffect\(\(\) => \{\s*if \(question\)/);
  });

  it("唯一 effect 为启动链：startExam→create→resume 全在 promise 回调（effect 体内零同步状态更新）", () => {
    const effectMatch = VOICE_SOURCE.match(/useEffect\(\(\) => \{[\s\S]*?\n  \}, \[/);
    expect(effectMatch).not.toBeNull();
    expect(effectMatch![0]).toContain("startVoiceSession(paperId)");
    const beforeThen = effectMatch![0].slice(0, effectMatch![0].indexOf(".then"));
    expect(beforeThen).not.toMatch(/set[A-Z]\w*\(/);
  });

  it("首题自动朗读在启动完成回调内（resume 投影就绪即读）", () => {
    expect(VOICE_SOURCE).toMatch(/const first = initial\.question;/);
    expect(VOICE_SOURCE).toMatch(/void speakQuestion\(first, initial\.session\.question_index, initial\.questionTotal\)/);
  });

  it("切题自动朗读在 advanceFrom 的下一题分支内（完成分支不朗读）", () => {
    const advanceMatch = VOICE_SOURCE.match(/async function advanceFrom\(authoritative: VoiceSession\) \{[\s\S]*?\n  \}/);
    expect(advanceMatch).not.toBeNull();
    expect(advanceMatch![0]).toContain("void speakQuestion(upcoming, outcome.view.session.question_index, outcome.view.questionTotal)");
    const finishBranch = advanceMatch![0].slice(advanceMatch![0].indexOf('outcome.kind === "finished"'));
    expect(finishBranch).not.toContain("speakQuestion(");
  });

  it("重复读题按钮仍可用（speakQuestion 直接以当前题调用）", () => {
    expect(VOICE_SOURCE).toMatch(/onClick=\{\(\) => void speakQuestion\(question, view\.session\.question_index, view\.questionTotal\)\}/);
  });

  it("speaking 状态生命周期（开始/结束/失败文案）与提交时 TTS 停止保留", () => {
    expect(VOICE_SOURCE).toMatch(/setSpeaking\(true\)/);
    expect(VOICE_SOURCE).toMatch(/setSpeaking\(false\)/);
    expect(VOICE_SOURCE).toContain("语音朗读失败");
    expect(VOICE_SOURCE).toMatch(/stopServerTtsPlayback\(\)/);
  });

  it("M14-250：朗读链零浏览器 speechSynthesis/speakUtterance 残留（服务端 TTS 为唯一朗读通道）", () => {
    expect(VOICE_SOURCE).not.toContain("speechSynthesis");
    expect(VOICE_SOURCE).not.toContain("speakUtterance");
    expect(VOICE_SOURCE).toContain("stopServerTtsPlayback");
  });

  it("M14-250：通道身份徽标透出 providers 视图（tone 降级如实标注，不谎报真实语音）", () => {
    expect(VOICE_SOURCE).toContain("api.voiceProviders()");
    expect(VOICE_SOURCE).toContain('data-testid="tts-channel"');
    expect(VOICE_SOURCE).toContain("降级替身");
  });

  it("once-only 会话申请守卫保留（started ref 防重复开考）", () => {
    expect(VOICE_SOURCE).toContain("started.current");
  });
});
