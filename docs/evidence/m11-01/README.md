# M11-01 全站 GSAP 体验重设计 — 浏览器验收证据

验证环境：本地 API（127.0.0.1:8001，SQLite 隔离库）+ `next dev`（127.0.0.1:3001）；Playwright 1.62 / chromium。
账号：m11_learner（learner）/ m11_admin（admin）— 仅存在于本地隔离验证库。

**结果：250/250 通过，0 失败。**（初版 186/186；PR#20 审查修复后追加 `anonymous-header` 与三个 `dialog-a11y` 分组并全量重跑）

## 分组通过情况

| 分组 | 通过/总数 |
| --- | --- |
| desktop-1440x900 | 22/22 |
| tablet-820x1180 | 22/22 |
| android-390x844 | 30/30 |
| android-360x800 | 30/30 |
| keyboard | 10/10 |
| anonymous-header（PR#20） | 6/6 |
| exam-flow-desktop | 10/10 |
| exam-flow-mobile-390 | 11/11 |
| exam-flow-mobile-360 | 11/11 |
| dialog-a11y-desktop（PR#20） | 19/19 |
| dialog-a11y-mobile-390（PR#20） | 19/19 |
| dialog-a11y-mobile-390-short（PR#20） | 20/20 |
| governance | 13/13 |
| reduced-desktop-1440x900 | 11/11 |
| reduced-android-360x800 | 11/11 |
| voice | 5/5 |

## 截图索引

| 文件 | 内容 |
| --- | --- |
| desktop-1440x900-home.png | 首页编排（桌面） |
| desktop-1440x900-library.png | 学习库（桌面） |
| desktop-1440x900-progress.png | 工作台（桌面） |
| desktop-library-filtered.png | 学习库搜索+难度过滤（Flip 重排） |
| desktop-exam-dialog.png | 考场交卷确认对话框 |
| desktop-review.png | 审阅页得分与错题 |
| desktop-governance-audit.png | 治理-审计日志 |
| tablet-820x1180-home.png | 首页（平板） |
| tablet-820x1180-library.png | 学习库（平板） |
| tablet-820x1180-progress.png | 工作台（平板） |
| mobile-390x844-home.png | 首页（Android 390） |
| mobile-390x844-library.png | 学习库（Android 390） |
| mobile-390x844-progress.png | 工作台（Android 390） |
| mobile-390-governance.png | 治理（Android 390） |
| mobile-390-exam-dialog.png | 交卷确认（Android 390） |
| mobile-390-review.png | 审阅（Android 390） |
| mobile-390-voice.png | 语音陪练（Android 390） |
| android-360x800-home.png | 首页（Android 360 窄屏） |
| android-360x800-library.png | 学习库（Android 360） |
| android-360x800-progress.png | 工作台（Android 360） |
| mobile-360-exam-dialog.png | 交卷确认（Android 360） |
| mobile-360-review.png | 审阅（Android 360） |
| anon-header-mobile-390.png（PR#20） | 未登录 header「登录」入口（390，68×44 触控目标） |
| anon-header-mobile-360.png（PR#20） | 未登录 header「登录」入口（360，68×44 触控目标） |
| anon-header-desktop-1440.png（PR#20） | 未登录 header「登录」入口（桌面，68×36 紧凑密度） |
| desktop-dialog-focus-trap.png（PR#20） | 交卷对话框 Tab 焦点圈定（桌面） |
| mobile-390-dialog-focus-trap.png（PR#20） | 交卷对话框 Tab 焦点圈定（390） |
| mobile-390-short-dialog-focus-trap.png（PR#20） | 交卷对话框滚动锁定（390×568 短视口，背景 scrollY=85 锁定） |

每视口检查项：内容渲染、路由切换、控制台无错误、无横向溢出、触控目标 ≥44px、键盘焦点环、reduced-motion 全功能。
考场全流程（进入试卷→作答→交卷确认→提交→审阅）在桌面 / 390 / 360 三档真实浏览器执行通过。

## PR#20 审查修复追加检查（Codex 独立审查发现的两个交互缺口）

1. **匿名 header 登录入口触控目标**（`anonymous-header` 分组，未登录上下文）：
   - 移动端 390 / 360：登录链接 68×44（≥44px）；此前为 `min-h-9`（36px）不足触控标准。
   - 桌面 1440：68×36（`md:min-h-9` 紧凑密度保持，不因移动端修正变大）。
2. **交卷确认对话框焦点圈定 + 滚动锁定**（`dialog-a11y-desktop` / `dialog-a11y-mobile-390` / `dialog-a11y-mobile-390-short` 分组）：
   - 打开即聚焦标题（H2），焦点在 `role=dialog` 内；
   - Tab 循环：标题 → 首按钮「再看看」→ 尾按钮「提交审阅」→ 环回首按钮；Shift+Tab 从首按钮环回尾按钮（边界处理）；
   - 背景滚动锁定：打开期间 `body` overflow=hidden；短视口（390×568）下背景真实处于 scrollY=85，滚轮 +400 后仍为 85；关闭后 overflow 恢复、焦点回到「交卷」触发钮；
   - 提交中（拦截 submit 响应延迟 900ms 观察）：`aria-busy=true`、两按钮均 disabled、焦点回落对话框标题、Tab 仍圈定在对话框内（无可用 Tab 序时不外溢）、滚动保持锁定；随后正常提交进入审阅页；
   - 提交 API 与考试语义零改动（提交仍由 ExamStudio.submit 掌握）。
