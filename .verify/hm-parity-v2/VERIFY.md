# hm-parity v2 验收记录(2026-10-25)

目标设备:HDC 192.168.161.137:10178(VMware KaihongOS 6.1.2.08,API 23)
账号:qa_m15025(learner)· 服务:https://ndtool.cn/aios/

## A. 学习冲刺三个作答型阶段

### A1. 间隔复习闭环 —— ✅ 真机全链 PASS(证据 review-final-dump.json + shot-review-result-final.jpeg)
- 队列:2 条逾期概念渲染(sql:select-basics 间隔1天/sql:join 间隔2天,逾期标记)
- present 修复:首验发现 409「非法状态转换 scheduled→attempted」→ 补 POST
  /review-queue/{id}/present(对齐服务端状态机与 Android)→ 重装后 PASS
- 选题:第二次进入换题(「最近未做过」规则生效,非缓存)
- 作答:选项 B + 信心 3 → 提交
- 反馈:「✓ 答对」「本轮已完成(completed);下轮间隔 1 天,2026-10-25 到期」
  「调度算法: fsrs-py-6.3」「返回复习队列」→ last_correct/interval_days/due_at/
  algorithm_version 四字段全部真机证实
- 概念:迁移检验/定向练习 UI 已实现(阶段按钮+单题流+判分反馈),当前生产冲刺
  阶段为 created/targeted_practice,SQL 冲刺 practice session 已 API probe 通过
  (concept_id=sql:having, step=recall_practice, 5 步蓝图);受测试数据阶段所限
  未在真机走完(需先把练习步骤打完),如实标注。

### A2. 学习冲刺页 v2 渲染 —— ✅ PASS(sprint-v2-dump.json + shot-sprint-v2.jpeg)
- 🔁 间隔复习入口卡(任意阶段可用)
- 6 合同渲染(教材冲刺·离散数学 3 能力目标/48小时/等值后测≥80 分等)
- 11 阶段链:前测→概念地图→计划确认→核心学习→定向练习→迁移检验→后测→学习报告→间隔复测
- 当前阶段/推进按钮/确认合同按钮/暂停提示

## B. 考试全流程 —— ✅ 真机全链 PASS(证据 v2-c3/v2-d1/v2-d2.json + shot-exam.jpeg + shot-report.jpeg)

| 步骤 | 端点 | 真机证据 |
|---|---|---|
| 开考 | POST /papers/{id}/exams | 卷卡「直接考试」按钮(登录态论文 200 后渲染) |
| 拉题 | GET /exams/{id} | 「考试作答」+ 题号条「1」+ 卷名·第 1/1 题 |
| 服务端时间 | server_remaining_seconds | 「剩余 4:59」只读快照 |
| 逐题作答 | PUT /exams/{id}/answers | 简答题 TextInput + 「保存作答」+ 已保存 0/1 计数 |
| 交卷 | POST /exams/{id}/submit | 「交卷」+「直接交卷(已保存 0/1)」双入口 |
| 报告 | GET /exams/{id}/report | 完整渲染:0 分·答对 0/1·待复核 1 题·得分 0/10·
  概念掌握 sql:select-basics 0/1(待复核)·逐题判分·
  正确答案 rubric_points(交卷后才下发→答案隔离证实)·知识点 |

## C. v1 回归 —— ✅ PASS
- 六 Tab(首页/学习/搜索/语音/设置/治理)导航正常
- 教材工作台/学习冲刺入口卡原样
- 设置页:服务地址/登录/注册/Asset Store 恢复(「已从安全存储恢复登录」)
- 考试会话只读查询区原样

## D. API 契约 probe(宿主直连,补充证据)
login 200 → contracts 6 → sprints(created/targeted_practice/concept_map_review 等)→
review-queue 2 → papers 5 → practice session(concept/step/strategy/item 蓝图齐全)→
开考(5 分钟卷 questions/next_sequence)→ PUT answers(next_sequence 推进)→ 幂等交卷预期对齐

## E. 构建装机链(全 PASS)
hvigorw assembleHap(API24)→ hm-patch-hap.cjs(宿主 zip 重打包 24→23)→
VM 侧 hap-sign-tool(深开鸿原生版,-mode localSign;keyAlias 全小写
「openharmony application profile debug」/「openharmony application release」,
p12 密码 123456)→ sign-profile(p7b)→ sign-app → bm install success
签名包留档:entry-api23-signed.hap(1.23MB)

## 环境级坑(如实记录,非应用代码问题)
1. VM rime IME 拦截 uitest inputText:整串注入被拼音化(「qa_m15025」→「——吗5025」)。
   解法:① 卸载重装应用清 IME 组合缓冲;② 点 rime 栏「中」切英文后短串直通;
   ③ 密码分段注入(text × 3 段)。登录最终成功(qa_m15025/角色 learner)。
2. 设置页登录按钮被 Tab 栏遮住(窗口高 536px):上滑露出后可点(v1 布局已知问题)。
3. force-stop 后窗口表 app0 消失、aa start 后 winId 每次变化:验收脚本需动态
   hidumper 查 app0 行取第 4 列 WinId,且 dump 用全量 merge 按「砚席」文本定位。
4. 重装(bm install)保留 Asset Store token;冷启动恢复慢于页面首载 → 401 可重试恢复。

## 已知遗留(代码侧,小)
- ExamPage 报告页(phase=REPORT)无退出按钮(标题栏退出仅非 REPORT 渲染)→
  需 force-stop 退出。建议下轮改为报告页也显示「完成」返回按钮。
- 文本作答在 IME 污染下可能存空串(本次报告「你的作答: (空)」即此因);
  客观题(单选/多选)不受影响(点选不走 IME)。
