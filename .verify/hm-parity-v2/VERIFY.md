# hm-parity v2 验收记录(2026-10-24)

目标设备:HDC 192.168.161.137:10178(VMware KaihongOS 6.1.2.08,API 23,
UDID DF485771FA6CA94F71E9224ABFADCD27FCD1B35430C1B53067FF246EE3FFF52)
生产 API:https://ndtool.cn/aios/api/v1(账号 qa_m15025)

## A. 学习冲刺三作答阶段(代码交付+API 实测)

### A1 迁移检验(transfer_check)
- [x] GET /learning/sprints/{id}/transfer/items 端点封装(LearningApi.getTransferItems)
- [x] POST /learning/sprints/{id}/transfer/submit(submitTransfer,responses[] 数组)
- [x] UI:「开始迁移检验」按钮(阶段=transfer_check 时)→ 复用多题作答会话
      (选项+信心 1-5)→ 提交判分卡(phase='transfer' 文案区分前后测)
- [~] 真机数据:现无 transfer_check 阶段冲刺;契约对齐服务端 learning.py(子代理提取)

### A2 定向练习(targeted_practice)逐题流
- [x] GET /practice/session → 200 实测{concept_id:"sql:having",step:"recall_practice",
      strategy:"advance",item(mcq 4 选项),passed_steps:1,target_steps:5}
- [x] POST /practice/answer 封装;UI 单题流:策略提示卡→作答→
      答对推进/答错同步骤重试+解析→下一题;practice_complete 提示推进
- [x] 按钮「定向练习(单题流)」(阶段=targeted_practice 时)——ls_66f4... 可验

### A3 间隔复习(review-queue)
- [x] GET /learning/review-queue → 200 实测 2 条(sql:select-basics overdue /
      sql:join overdue,interval_days 1/2,算法 fsrs-py-6.3/intervals-mvp-1)
- [x] GET /{id}/item + POST /{id}/attempt 封装(last_correct 三态)
- [x] UI:ReviewEntryCard 入口(任意阶段)→ 队列列表(逾期标红)→ 单题作答 →
      last_correct + 下轮 interval_days/due_at 反馈
- [~] 真机登录态队列渲染:未登录(401)如预期;登录注入受 IME 阻(见边界)

## B. 考试全流程(代码交付+API 全链实测)

- [x] ExamApi.ets 5 端点封装(开考/拉题/作答/交卷/报告)
- [x] POST /papers/{id}/exams → 201 实测(exam_53afe498...,status active,
      server_remaining_seconds 299,questions[].essay 无 options)
- [x] PUT /exams/{id}/answers → 200 实测(sequence 1,next_sequence→2,
      answers 回显 {qid:"test"})
- [x] POST /submit、GET /report 封装(报告 404=审阅未生成,UI 降级 submission 概要)
- [x] ExamPage.ets:选卷/作答(单选点选即存/多选勾选+保存+{"option_indices"}/
      文本题 TextInput/题号指示条/服务端剩余时间)/交卷确认/报告页
      (expected 只在报告下发,readableAnswer 解码多选为字母)
- [x] StudyPane PaperCard「直接考试」按钮(pushUrl+paperId 参数)
- [x] main_pages.json 注册 ExamPage

## C. 不回归验证(真机 layout dump)
- [x] 首页六 Tab:首页/学习/搜索/语音/设置/治理——layout-home.json
- [x] 学习 Tab 两入口卡:📚 教材工作台@287,267 / 🏃 学习冲刺@706,245
      +论文区(401 fail-closed 文案)——layout-study-tab2.json
- [x] 学习冲刺页 v2:标题+副题+未登录提示+加载/错误态——layout-learning-sprint-final.json
- [x] 设置 Tab 登录表单(TextInput 双框)——layout-login-filled2.json

## D. 构建/装机链
- [x] hvigorw assembleHap BUILD SUCCESSFUL(5s 856ms,1,201,236 B)
- [x] 重打包 patch:pack.info apiVersion {compatible:23,target:23} +
      module.json min/targetAPIVersion 60101023(回读自检 ✓)
- [x] VM sign-profile(p7b)+ sign-app(release 别名全小写)success
- [x] bm uninstall(旧 v1)+ bm install success + aa start success

## 边界(如实)
1. 设备端登录:uitest uiInput text 被 VM 中文 IME 污染(用户名框混入中文标点,
   keyEvent 退格无效),登录态真机 UI 流未走通;API 层全链已由宿主探针实测覆盖。
2. 视觉截图被 VM 内 BrowserOS 窗口遮挡,视觉断言不可信(已知坑);
   本轮以 layout dump 为权威,截图留档标注。
3. 迁移检验真机验证待冲刺推进到 transfer_check 后可补(端点+UI 已就绪)。

## 证据文件(本目录)
layout-home.json / layout-study-tab2.json / layout-learning-sprint-final.json /
layout-login-filled2.json / shot-learning-sprint.jpeg(遮挡)/ shot-settings-login.jpeg(遮挡)
