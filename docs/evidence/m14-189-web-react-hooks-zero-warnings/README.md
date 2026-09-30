# M14-189 Web react-hooks 零 warning — 交付证据归档

- 日期：2026-09-30（开发切片交付，待 supervisor 合并收口）
- 分支：`ops/m14-189-web-react-hooks-zero-warnings`（由 M14-188 本地 HEAD
  `aa4b6e1809a25c4d9e3b11c1a8b131d75c697a27` 创建，单本地 commit，未
  push、未开 PR）。基线 tree `b515cc429dd69b772ca7f239e344e51d88e28e4a`
  与 supervisor 核实的远端 main（`1578b4d706cd35e0c08edf3f8672fb257408c488`）
  逐字符一致。
- 目标：消除 `apps/web` lint 中全部 11 条 react-hooks warning
  （10 × `set-state-in-effect`，1 × `refs`），不 disable/降级规则、
  不新增 eslint-disable，保持行为与 motion/reduced-motion 语义。

## 逐 warning 根因与实现决策

规则机制（读 `eslint-plugin-react-hooks` v7 源码实证，非猜测）：
`set-state-in-effect` 由 React Compiler HIR 驱动——effect 体内**直线
同步可达**的 setState 被拦；promise `.then`/`.catch` 回调与事件订阅
回调里的 setState 是官方允许路径；**async/await 的 continuation 仍算
直线可达**（本切片中途实证：`void asyncLoad()` 形态仍报，promise 链
形态不报）。`refs` 规则拦 render 期间访问 `ref.current`。

| # | 文件:位置（改前） | 规则 | 根因 | 实现决策（React 19 推荐模式） |
| --- | --- | --- | --- | --- |
| 1 | `src/lib/gsap.ts:46` | set-state-in-effect | `usePrefersReducedMotion` 在 effect 体内同步 `setReduced(query.matches)` | 媒体查询即外部 store → **`useSyncExternalStore`**（订阅 change、快照 `matches`、server snapshot 恒 false）。SSR false / 水合后同步真实值 / 跟随系统设置——语义与原实现等价 |
| 2 | `components/exam/question-nav.tsx:25` | refs | `pulse` 闭包在 render 期创建时读 `rootRef.current?.querySelector(...)` | `pulse(el, answered)` 接收点击目标——`event.currentTarget` 与原 `querySelector([data-qnav=i])` 是同一按钮元素；`contextSafe` 包装保留（动画仍登记进 useGSAP context 随卸载清理） |
| 3 | `components/exam/question-panel.tsx:33` | set-state-in-effect | `AnswerInput` 用 effect 镜像 `value` prop 到 `draft` state | 官方 render 期重置模式（比较 `prevValue`、当帧 `setPrevValue+setDraft`）——消除一拍延迟的级联渲染，切题草稿与题目同帧生效 |
| 4 | `components/exam/submit-dialog.tsx:53` | set-state-in-effect | `unanswered` 用 effect 镜像 `total-answered` | 纯派生无需 state：render 期 `unansweredCount(total, answered)`（导出纯函数，下限 0 语义不变）；焦点圈定/滚动锁定/ref 同步等可访问性行为零改动 |
| 5 | `components/governance/audit-log.tsx:140` | set-state-in-effect | mount effect 调 `reload`，其同步前缀 `setError(null)` 等直线可达 | **load/reload 拆分**：`load` 纯加载（promise 链 `.then(setEntries).catch(...)`）；`reload`（先清旧反馈再 load）只挂用户入口（刷新按钮/ErrorState onRetry）。mount effect 只调 `load`——手动刷新/重试可见行为与原实现一致（点击瞬间清 error/forbidden） |
| 6 | `components/governance/draft-queue.tsx:89` | 同 #5 | 同 #5（同步前缀 reset） | 同 #5 模式；加载结果用纯 helper 完整覆盖互斥反馈（成功清 error/forbidden，403 清 error 并设 forbidden，普通失败清 forbidden 并设 error），config 变化不残留旧状态；审核动作 `act()` 零改动 |
| 7 | `components/library/library-view.tsx:42` | 同 #5 | `useEffect(load, [])`，load 同步段 `setError(null)` | 同 #5 模式；`onRetry` 挂 `reload`，Flip 布局过渡与过滤逻辑零改动 |
| 8 | `components/papers/paper-picker.tsx:26` | 同 #7 | 同 #7（完全同构组件） | 同 #7 |
| 9 | `components/progress/progress-workbench.tsx:121` | 同 #5 | auth 确认后 effect 调 `loadAll`，同步前缀三个 `setXxxError(null)` | 同 #5 模式（`Promise.allSettled(...).then(...)` 链）；**auth 门语义保留**（null 未定绝不发受保护请求）；三处 onRetry 挂 `reloadAll` |
| 10 | `components/voice-studio.tsx:112` | set-state-in-effect | `question?.id` 变化 effect 调 `speakCurrent`，同步段 `setSpeaking(true)` | **朗读事件化**（规则诊断的 Derived event pattern）：`speak(target, order, total)` 参数化，三个调用点全在事件/异步回调链——首题（`startExam().then`）、切题（`next()` 的 setIndex 分支；提交分支不朗读）、重复读题按钮。原 effect 语义（题目变化即重读）由前两条路径等价覆盖（题目只经这两条路径变化）；提交时 `speechSynthesis.cancel`、`started` 防重复开考守卫原样保留。顺带消除了 StrictMode 下自动朗读 effect 双执行 |
| 11 | `components/download/download-panel.tsx:55` | set-state-in-effect | standalone 检测在 effect 体内同步 `setState({kind:"installed"})` | standalone 改 **`useSyncExternalStore`** 派生（`display-mode: standalone` 订阅+快照，SSR false）；安装事件 effect 以 `standalone` 为依赖条件注册（体内零同步 setState）；`beforeinstallprompt` preventDefault、`appinstalled`、300ms 探测收尾与 iOS/通用分流全部保留 |

中途修正（如实记录）：#5/#6/#9 第一版用 async/await 的 `load` 仍被规则
报告（3 条 residual）——await 的 continuation 在 HIR 中仍属 effect 直线
可达，遂改 promise 链形态后清零。

## 聚焦测试（先红后绿的契约 + 单元）

新增 `apps/web/src/lib/react-hooks-hygiene.test.ts`（37 用例，vitest node
环境，沿用 sw-contract / download-page 源码契约惯例 + 可跑的单元断言）：

- **卫生底线**：11 个修改文件零 `eslint-disable`；`eslint.config.mjs` 的
  M14-05 降级配置原样保留（本切片只清代码，不动配置）。
- **gsap**：useSyncExternalStore 形态、change 订阅/退订、server snapshot
  恒 false、查询串不变、`useMotion` 的 matchMedia 双分支与 revert 兜底。
- **download-panel**：standalone store 派生、beforeinstallprompt
  preventDefault→promptable、300ms 收尾、iOS/通用分流、appinstalled。
- **question-nav**：pulse 由 `event.currentTarget` 驱动、render 期不读
  `rootRef.current`。
- **question-panel**：prevValue 当帧重置形态、无 effect 内 setDraft、
  保存按钮 disabled 语义。
- **submit-dialog**：`unansweredCount` 纯函数单元测试（常规/零/满/超计/
  空卷边界）、无 setUnanswered 镜像、keydown/overflow/ref 同步保留。
- **5 个加载组件**：load 为 promise 链（无 await 直线）、mount/auth
  effect 只调 load、reload「先清旧反馈再加载」且用户入口挂 reload、
  progress 的 auth 门、audit/draft 的 403 分流。
- **draft-queue 状态迁移**：403 -> 普通失败会清 forbidden 并显示新
  error；普通失败 -> 成功会清 error，且不显示旧 forbidden。
- **voice-studio**：自动朗读 effect 已移除、首题朗读在 `.then` 内、切题
  朗读在 `next()` setIndex 分支（提交分支无 speak）、重复读题按钮、
  speaking 生命周期与失败文案、提交时 TTS cancel、`started` 守卫。

既有 130 用例全部保持通过（含 download-page.test.ts 对 download-panel
的全部既有断言——改动与之兼容）。

## 验证（worktree 内实测，2026-09-30）

| 命令 | 结果 |
| --- | --- |
| `npx eslint . --max-warnings=0`（cwd=`apps/web`） | ✅ **exit 0**（修改前：exit 1，11 warnings；`npm run lint -- --max-warnings=0` 在本机 npm 会因 `--max-warnings=0` 传参被拒绝，不用作证据） |
| `npm run test`（cwd=`apps/web`） | ✅ 11 files / **167 tests** 全通过（原 130 + 新 37） |
| `npm run typecheck` | ✅ exit 0（tsc --noEmit 无错误） |
| `npm run build`（cwd=`apps/web`） | ✅ exit 0：Compiled successfully、TypeScript 通过、11/11 静态页，**构建输出零 warning** |
| `git diff --check` | ✅ 干净（无空白错误） |

warning 清零证据：`npx eslint . --max-warnings=0` exit 0；逐文件
warning 定位清单（改前 11 条：download-panel 55:7、question-nav 25:29、
question-panel 33:5、submit-dialog 53:5、audit-log 140:10、draft-queue
89:10、library-view 42:13、paper-picker 26:13、progress-workbench
121:75、voice-studio 112:24、gsap 46:5）改后全部不再出现。

## 入库变更清单（git diff --numstat HEAD^ HEAD，见最终 commit）

- 11 个组件/lib 源文件（上表）+ 新增
  `apps/web/src/lib/react-hooks-hygiene.test.ts` + 本 README。
- 未触碰 `docs/PROJECT_STATUS.md` / `docs/ROADMAP.md`（M14-187H 并行
  更新台账，合并时由 supervisor 统一收口）；未动 eslint 配置。

## 诚实边界

- **无 DOM 测试依赖的仓库现状**：组件级行为以源码契约钉住（仓库既有
  惯例），唯一可执行的单元测试是 `unansweredCount` 纯函数。11 处改动
  的运行时行为等价性依据：模式映射到 React 官方推荐姿势 + 契约断言 +
  typecheck/build/test 全绿 + 逐处人工语义比对（见上表「实现决策」列）。
  未做浏览器端手工回归（本切片零部署、零网络）。
- **两处可感知时序差异（改进而非回归）**：AnswerInput 草稿重置由
  「effect 晚一帧」变「当帧」；voice-studio StrictMode 开发模式下自动
  朗读不再双执行。生产路径行为等价。
- **残留债务为零**：react-hooks 家族 warning 0 条；配置中 M14-05 的
  显式 warn 降级保留（未来新违规仍可见，未收紧为 error——超出本切片
  边界，收紧与否由 supervisor 决策）。
- 零生产触碰、零网络请求、无 key/token/secret 输出。
