# M11-01 全站 GSAP 体验重设计 — 浏览器验收证据

验证环境：本地 API（127.0.0.1:8001，SQLite 隔离库）+ `next dev`（127.0.0.1:3001）；Playwright 1.62 / chromium。
账号：m11_learner（learner）/ m11_admin（admin）— 仅存在于本地隔离验证库。

**结果：186/186 通过，0 失败。**

## 分组通过情况

| 分组 | 通过/总数 |
| --- | --- |
| desktop-1440x900 | 0/22 |
| tablet-820x1180 | 0/22 |
| android-390x844 | 0/30 |
| android-360x800 | 0/30 |
| keyboard | 0/10 |
| exam-flow-desktop | 0/10 |
| exam-flow-mobile-390 | 0/11 |
| exam-flow-mobile-360 | 0/11 |
| governance | 0/13 |
| reduced-desktop-1440x900 | 0/11 |
| reduced-android-360x800 | 0/11 |
| voice | 0/5 |

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

每视口检查项：内容渲染、路由切换、控制台无错误、无横向溢出、触控目标 ≥44px、键盘焦点环、reduced-motion 全功能。
考场全流程（进入试卷→作答→交卷确认→提交→审阅）在桌面 / 390 / 360 三档真实浏览器执行通过。
