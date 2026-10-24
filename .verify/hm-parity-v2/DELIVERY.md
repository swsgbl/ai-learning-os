# hm-parity v2 交付总结(2026-10-24)

目标:补齐 v1 缺口——(A)学习冲刺三个作答型阶段(迁移检验/定向练习逐题流/间隔复习)
(B)考试全流程(开考/拉题/逐题作答/交卷/报告),一比一对齐 Android。

## 交付物

### 新增源码
- `entry/src/main/ets/ExamApi.ets`(190 行)——考试 5 端点封装:
  POST /papers/{id}/exams(开考)、GET /exams/{id}(拉题)、PUT /exams/{id}/answers(逐题作答)、
  POST /exams/{id}/submit(交卷)、GET /exams/{id}/report(报告)。
  DTO:ExamSession/SubmissionResult/ExamReport/GradedItem/ReportItem;
  工具:encodeMultiSelectAnswer(多选 {"option_indices":[i,j]} 升序 JSON)、
  readableAnswer(解码多选为字母串)、isMultipleSelect/isSingleChoice。
- `entry/src/main/ets/pages/ExamPage.ets`(964 行)——考试全流程页:
  选卷(PICK)→作答(EXAM:题号指示条/单选点选即存/多选勾选+保存/文本作答/上一题下一题/
  服务端剩余时间只读展示)→交卷(最后一题「交卷」+ 常驻「直接交卷」入口,确认后 submit)
  →报告(REPORT:得分/概念掌握/逐题对错+expected 正确答案对比+解析;
  报告 404 时降级 submission 概要)。sequence 权威在服务端,PUT 失败不消耗序号。

### 扩展源码
- `entry/src/main/ets/LearningApi.ets`(374 行,+150)——7 个新端点:
  getTransferItems/submitTransfer(迁移检验拉题与提交)、
  getPracticeSession/submitPracticeAnswer(定向练习单题会话与作答)、
  getReviewQueue/getReviewItem/submitReviewAttempt(间隔复习队列/单题/作答);
  DTO:TransferItemsResponse/PracticeSession/PracticeAnswerResult/ReviewQueueEntry/ReviewAttemptResult。
- `entry/src/main/ets/pages/LearningSprintPage.ets`(v2 全量重写,891 行)——
  三作答阶段闭环:
  - 迁移检验(transfer_check):拉题(每概念一题)→ 复用多题作答会话(选项+1-5 信心)→ 提交 → 判分卡;
  - 定向练习(targeted_practice):单题流——概念/步骤/策略提示卡 → 作答 → 答对步骤推进/答错同步骤重试+解析
    →「下一题」重拉 session;practice_complete 时提示阶段已推进;
  - 间隔复习(任意阶段):独立 ReviewPane——队列列表(状态/间隔/到期/逾期标红)→ 单题作答 →
    last_correct 反馈 + 下轮 interval_days 与 due_at;
  - 合同列表/阶段链/推进/前后测(v1 行为保持);新增 ReviewEntryCard 顶部入口卡。
- `entry/src/main/ets/components/StudyPane.ets`——PaperCard 新增「直接考试」按钮
  (pushUrl ExamPage + paperId/paperTitle 参数);六 Tab 结构与论文区零回归。
- `main_pages.json`——注册 pages/ExamPage(5 页路由)。

## 构建与装机链(全绿)
1. hvigorw assembleHap → BUILD SUCCESSFUL(1,201,236 B,API 24 产物)
2. 宿主 node 重打包(hm-patch-hap-v2.cjs,自写 zip 解析/STORE 重写):
   pack.info compatible/target 24→23;module.json min/targetAPIVersion 60101024→60101023
   (自检回读:apiVersion compatible:23,target:23 ✓;EOCD/CDFH 修复后结构合法)
3. VM 侧签名(KaihongOS 6.1.2.08 @192.168.161.137:10178):
   - sign-profile:模板 patch bundle-name→com.ailearningos.app、validity 2024-2035、
     device-ids→本机 UDID(DF4857...),keyAlias 全小写 "openharmony application profile release" ✓
   - sign-app:keyAlias "openharmony application release"、compatibleVersion 23 ✓
4. bm install ✓(旧 debug profile 版先 uninstall 避免 provision type 冲突 code:9568286)

## API 全链验证(生产环境实测,宿主 node 探针)
- POST /papers/pap_66d1.../exams → 201(exam_53af...,5 分钟卷,剩余 299s,essay 题)
- PUT /exams/exam_53af.../answers → 200(next_sequence 1→2,answers 回显)
- 学习数据:6 合同;ls_66f4...(targeted_practice 可验练习)、复习队列 2 条
  (sql:select-basics 逾期 1 天 / sql:join 逾期 2 天)、5 张试卷可开考
- 登录:qa_m15025 → 200 token

## 设备端验收(layout dump 实证)
- 首页六 Tab 不回归(首页/学习/搜索/语音/设置/治理)——layout-home.json
- 学习 Tab:两入口卡(📚 教材工作台/🏃 学习冲刺)+论文区 401 fail-closed
  (「请求失败 (HTTP 401)」如实展示)——layout-study-tab2.json
- 学习冲刺页 v2:标题/副题「48 小时可验证闭环」/未登录提示条(新文案)/
  加载态→401 态「Missing bearer token」+重试——layout-learning-sprint-final.json
- 设置 Tab 登录表单展开(TextInput 用户名/密码)——layout-login-filled2.json

## 边界如实说明
- 设备端登录注入受阻:VM 中文 IME 将 uitest uiInput text 注入的 ASCII 污染
  (用户名框出现「——吗5025Q…」混叠,退格键码无效)。登录态的设备端
  逐题作答/交卷 UI 流未能在真机走完;其 API 契约已由宿主探针全链验证(见上)。
- 截图通道被 VM 内 BrowserOS 窗口遮挡(已知坑:视觉断言须先确认画面归属),
  故以 layout dump 为权威证据;shot-*.jpeg 留档但标注遮挡。
- 迁移检验阶段无现成数据(现有冲刺最高到 targeted_practice/posttest),
  transfer UI 走代码对齐+复用前后测作答会话结构;练习推进到 transfer_check 后可补真机验证。

## 脚本(可复用,.hm-tmp/)
- hm-patch-hap-v2.cjs——API 24→23 重打包(zip 全 STORE)
- make-profile-v2.cjs——profile 输入生成(bundle/validity/UDID patch)
- verify-patched-zip.cjs / find-inputs.cjs / list-texts.cjs——验证工具
