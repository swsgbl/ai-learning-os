# 深开鸿 v2：学习冲刺作答闭环补全 + 考试全流程（一比一对齐 Android）

## 上一轮成果（v1 已合并 main）

v1 已交付：LearningApi.ets（14 端点）+ LearningSprintPage + TextbookWorkbenchPage
+ 深开鸿 VM 签名装机链（.hm-tmp 脚本已验证——VM 侧签名 + API 23 patch）。
commit 7a34104 已合并，从 origin/main 切新分支开发。

## v2 目标：两块缺口（按优先级）

### 缺口 A：学习冲刺作答闭环（v1 只有前后测——补齐三个作答型阶段）

Android `LearningSprintScreen.kt` 有而鸿蒙缺的：

1. **迁移检验作答**（transfer_check 阶段）：
   - 「开始迁移检验」按钮 → `GET /api/v1/learning/sprints/{id}/transfer/items`
   - 多题作答（选项+1-5 信心）→ `POST .../transfer/submit` `{responses:[{item_id,response,confidence}]}`
   - 结果显示分数与 `sprint_phase`（提交后自动推进 posttest）
2. **定向练习逐题流**（targeted_practice 阶段）：
   - 「定向练习」按钮 → `GET .../practice/session`（单题：{concept_id,step,strategy,rationale,item,passed_steps,target_steps}）
   - 选项+信心 → `POST .../practice/answer` `{item_id,response,confidence}`
   - 反馈：答对=步骤推进 / 答错=同步骤重试+显示 explanation；「下一题」按钮重拉 session
   - `practice_complete=true` 时提示已推进迁移检验
3. **间隔复习作答**（任意阶段，到期队列）：
   - `GET /api/v1/learning/review-queue`（队列：{id,concept_id,status,due_at,interval_days,overdue}）
   - 「开始复习」→ `GET /api/v1/learning/review-queue/{id}/item`（单题）
   - 选项+信心 → `POST /api/v1/learning/review-queue/{id}/attempt` `{item_id,response,confidence}`
   - 响应含 `last_correct`（true=答对）显示结果与下一轮间隔

**API 参照**：Android `LearningSprintViewModel.kt` 里的 startTransfer/submitTransfer/
startPracticeStep/submitPracticeAnswer/startReview/submitReviewAnswer 完整流程。

### 缺口 B：考试全流程（学习 Tab 核心功能——目前只有只读列表）

Android `ExamScreen.kt` + `ExamViewModel.kt` 的完整流程：

1. **开考**：StudyPane 卷卡「直接考试」→ `POST /api/v1/papers/{paper_id}/exams` → {exam_id}
2. **拉题**：`GET /api/v1/exams/{exam_id}` → {paper_title,questions:[{id,type,stem,options?,answer_index 不下发}],current_sequence,time_limit_seconds}
3. **逐题作答**：`PUT /api/v1/exams/{exam_id}/answers` `{sequence,question_id,answer}`（append-only，服务端返回新 sequence）
4. **交卷**：`POST /api/v1/exams/{exam_id}/submit` → 服务端判分
5. **报告**：`GET /api/v1/exams/{exam_id}/report` → {score,items:[{question_id,correct,expected?}],…}
   （**交卷后才显示 expected——答案隔离**）

**UI 要求**：新 `ExamPage.ets`（参照 VoiceExamPage.ets 的全屏模式）：题号指示 /
上一题下一题 / 单选多选作答 / 计时显示（服务端权威时间） / 交卷确认 / 报告页
（逐题对错+正确答案对比）。StudyPane 卷卡加「直接考试」按钮（已有「学习模式」
入口的卷卡上加）。

---

## 技术约定（v1 已建立，全部沿用）

- 网络层：复用 `AiosApi.ets` 的 `requestJson<T>`（bearer fail-closed + 服务端
  detail 原样透出）；新端点加到 `LearningApi.ets` 或新建 `ExamApi.ets`
- 答案隔离铁律：题目投影不含 answer 字段；判分只在提交后来自服务端
- 信心采集：所有作答面板带 1-5 信心选择（FilterChip 样式同 v1 前后测）
- 409/401/404 detail 原样显示
- 六 Tab 结构零改动；新页面走 router.pushUrl
- ArkTS 状态管理 @State；异步竞态防护（disposed + generation 计数器）

## 构建与验收

构建（apps/harmony 目录）：
```
hvigorw assembleHap --mode module -p product=default -p buildMode=release --no-daemon
```
**不要改 build-profile.json5 的 SDK 版本**（保持 6.1.1(24) 原值——上轮教训）。

装机链（v1 已固化）：
1. 宿主构建产物 → node 重打包 patch API 24→23（.hm-tmp/hm-patch-hap.cjs 可参考
   上轮脚本逻辑重新写一份放 .hm-tmp/）
2. VM 侧签名（/data/local/home/.ohos/signature，keyAlias 小写
   "openharmony application release"，profile 用 debug 模板 patch UDID）
3. `hdc -t 192.168.161.137:10178 install`

设备：HDC 192.168.161.137:10178（VMware 深开鸿 KaihongOS 6.1.2.08，API 23）
hdc 路径：`C:\Program Files\Huawei\DevEco Studio\sdk\default\openharmony\toolchains\hdc`

API 测试：生产 https://ndtool.cn/aios/api/v1 · qa_m15025 / Qa-M15-025!x9
（该账号有教材冲刺合同在早期阶段可用于推进验证；也有试卷可开考）

## 验收清单（真机逐项 + layout dump 证据）

1. 构建 BUILD SUCCESSFUL + VM 装机成功
2. 学习冲刺面板：若冲刺在 transfer_check 阶段 →「开始迁移检验」拉题作答提交分数
3. 定向练习：单题流（策略提示/答对推进/答错重试+解析/下一题）
4. 间隔复习：队列列表 → 开始复习 → 作答 → 结果反馈
5. StudyPane 卷卡「直接考试」→ 考试页拉题 → 逐题作答 → 交卷 → 报告页（逐题对错）
6. 六 Tab + v1 两入口卡不回归
7. 401 未登录态如实显示（若登录态难输入凭据则跳过登录态验证，结构级即可）

## 交付物

1. 新/改 ArkTS 源文件
2. .verify/hm-parity-v2/{VERIFY.md,DELIVERY.md} + layout dump 证据
3. 交付总结：文件清单、验收清单 pass/fail、已知限制
