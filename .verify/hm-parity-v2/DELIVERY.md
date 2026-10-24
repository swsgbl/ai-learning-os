# hm-parity v2 交付总结(2026-10-25)

分支 feat/hm-parity-v2 · 上游 v1(commit 7a34104 已合并)

## 交付物

### 源码(apps/harmony/entry/src/main/ets/)
| 文件 | 内容 |
|---|---|
| **ExamApi.ets**(新) | 考试 5 端点(startExam/getExam/putAnswer/submitExam/getExamReport)+ 全套 DTO(ExamSession/SubmissionResult/ExamReport 等)+ 多选编码工具(encodeMultiSelectAnswer/readableAnswer)|
| **LearningApi.ets**(+8 端点) | getTransferItems/submitTransfer/getPracticeSession/submitPracticeAnswer/getReviewQueue/presentReview/getReviewItem/submitReviewAttempt + DTO |
| **pages/LearningSprintPage.ets**(重写) | 三作答型阶段:迁移检验(多题会话+信心)、定向练习(单题流:策略提示→作答→判分→对推进/错重试+解析→下一题;practice_complete 提示)、间隔复习(队列→present→单题→last_correct+下轮间隔);11 阶段链+间隔复习入口卡;公用 ItemCard(选项+信心1-5);disposed+generation 防竞态 |
| **pages/ExamPage.ets**(新) | 考试全流程:PICK(选卷)/EXAM(单选点选即存/多选勾选+保存/文本输入+保存;题号指示条;服务端剩余时间;append-only sequence)/CONFIRM/REPORT(得分+概念掌握+逐题判分+expected 对比+解析+知识点);router 参数 paperId 直达;报告 404 降级 submission 概要 |
| **components/StudyPane.ets** | 论文卷卡新增「直接考试」按钮(pushUrl → ExamPage 携 paperId/paperTitle) |
| main_pages.json | 注册 pages/ExamPage |

### 不变式(全部保持)
- 答案隔离:作答页无正确答案;expected 仅交卷/报告下发(真机报告证实 rubric 交卷后才出现)
- requestJson fail-closed bearer;409/404/422 detail 原样透出(真机 409「非法复习状态转换」如实展示并据此修复)
- token 零持久化(内存)+ Asset Store 恢复(v1 机制复用,真机证实「已从安全存储恢复登录」)
- 服务端权威:时间/sequence/判分/FSRS 调度全部来自服务端

### 脚本(.hm-tmp/,可复用)
- hm-patch-hap.cjs — HAP API 24→23 宿主重打包(自写 zip 解析,STORE-only)
- vm-step1-profile.cjs / redeploy.cjs — VM 签名装机链(profile patch UDID/bundle → sign-profile → sign-app → bm install)
- probe-api*.cjs — 生产 API 全链 probe
- 验收驱动若干(uiInput click/dump 动态窗口定位)

### 关键修复(验收中发现)
**复习状态机缺 present 步骤**:首验 409「非法复习状态转换: 'scheduled' → 'attempted'」
→ 查服务端 learning.py 确认 scheduled→presented→attempted 状态机 →
LearningApi 补 presentReview + SprintPage.startReviewItem 在 scheduled 时先 present
→ 重建重装 → 真机 PASS(✓ 答对+completed+下轮间隔 1 天+fsrs-py-6.3)。

## 真机验收结论(详见 VERIFY.md)
| 项 | 结果 |
|---|---|
| B 考试全流程(开考→拉题→作答→保存→交卷→报告) | ✅ PASS |
| A1 间隔复习闭环(队列→present→作答→判分→FSRS 间隔) | ✅ PASS |
| A2 学习冲刺 v2 页(合同/阶段链/入口卡) | ✅ PASS |
| v1 回归(六 Tab/工作台/设置/Asset 恢复) | ✅ PASS |
| A 迁移检验/定向练习 | 代码+API probe ✅;真机全流程受测试数据阶段所限未走完(冲刺需先推进),UI 已渲染 |
| 构建→patch→VM 签名→装机 | ✅ PASS(签名包留档) |

## 后续建议
1. ExamPage 报告页补「完成」退出按钮(当前需 force-stop)
2. SettingsPane 登录按钮在矮窗口被 Tab 栏遮挡 → 布局压缩
3. 定向练习真机走完:先把 SQL 冲刺 5 步练习打完(可宿主 API 代打),再真机验迁移检验
4. exam mode 参数:当前固定 {mode:'exam'},可按需暴露 practice 模式入口
