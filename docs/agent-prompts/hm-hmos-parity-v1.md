# 鸿蒙版学习冲刺 + 教材工作台开发任务（对齐 Android v0.5.1）

## 背景与目标

仓库：`D:\AI Learning OS\ai-learning-os`（当前分支从 origin/main 切出）
鸿蒙工程：`apps/harmony/`（ArkTS + hvigor，你此前 scaffold 过——
oh-package.json5 里有你的署名）。现有六 Tab 结构（首页/学习/搜索/
语音/设置/治理）**不得破坏**。

Android 端（`apps/android/`）在 v0.5.0/v0.5.1 已交付两大新面板，
鸿蒙端缺失。你的任务是补齐这两个面板，达到 Android v0.5.1 功能对等：

### 面板一：学习冲刺（LearningSprint）

用户旅程：合同列表 → 未确认合同点「确认」→ 有冲刺显示 11 阶段链
（合同已确认→前测→概念地图→计划确认→核心学习→定向练习→迁移检验→
后测→学习报告→间隔复测）→「推进」按钮逐阶段推进 → 阶段为
baseline_assessment/posttest 时「开始前测/后测」拉题作答（选项 +
1-5 信心）→ 提交服务端判分显示分数。

### 面板二：教材工作台（TextbookWorkbench）

用户旅程：目录搜索（输入关键词命中 ChinaTextbook 目录元数据）→
每条结果「导入」按钮（声明使用权后下载组装到私有空间）→「我的教材」
书架列表（标题/大小/解析态/概念计数）→ 分态操作按钮（解析 /
OCR 解析(15页/批) / 提取概念 / 一键开冲刺）。

**UI 结构建议**：学习 Tab 内 StudyPane 上方加两张入口卡（同
Android StudyScreen 的「教材工作台」「学习冲刺」入口），点击推入
新页面（参照 pages/VoiceExamPage.ets 的路由模式）。Index.ets 六
Tab 结构零改动。

---

## API 契约（全部相对 base URL，生产=https://ndtool.cn/aios/api/v1）

认证：登录后内存 bearer token（复用 AiosApi.ets 既有的
setBearerToken/hasBearerToken 机制），受保护请求带
`Authorization: Bearer <token>`。

### 学习冲刺端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/v1/learning/contracts | 合同列表（owner 的） |
| POST | /api/v1/learning/contracts | 建合同 {title,mode:"exam_sprint",target_outcomes:[{statement,criteria}],time_budget_hours:48,pass_criteria} |
| POST | /api/v1/learning/contracts/{id}/confirm | 确认合同 |
| GET | /api/v1/learning/contracts/{id}/sprints | 该合同的冲刺列表（取第一个） |
| POST | /api/v1/learning/contracts/{id}/sprints | 开冲刺 |
| POST | /api/v1/learning/sprints/{id}/advance | 推进 {to_phase:"下一阶段id"} |
| POST | /api/v1/learning/sprints/{id}/pause / resume | 暂停/恢复 |
| GET | /api/v1/learning/sprints/{id}/assessment?phase=baseline|posttest | 拉题（响应含 items[{item_id,concept_id,question_type,stem,options:[{key,text}]}]；**答案绝不下发**） |
| POST | /api/v1/learning/sprints/{id}/assessment/{phase}/submit | 提交 {responses:[{item_id,response,confidence}]} → 返回 {score,sprint_phase} |
| GET | /api/v1/learning/sprints/{id}/transfer/items + POST .../transfer/submit | 迁移检验（同 assessment 结构） |
| GET | /api/v1/learning/sprints/{id}/practice/session + POST .../practice/answer | 定向练习单题流 |
| GET | /api/v1/learning/review-queue + GET /review-queue/{id}/item + POST /review-queue/{id}/attempt | 间隔复习 |

### 教材工作台端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/v1/catalog/search?q=关键词&limit=30 | 目录搜索（支持中文如「一年级数学」——服务端已做结构化抽词） |
| GET | /api/v1/library/imports | 我的书架（含 title/parse_status/concept_counts） |
| POST | /api/v1/library/imports | 导入 {resource_id, permission_statement:true} |
| POST | /api/v1/library/imports/{job_id}/parse | 解析（扫描书加 ?ocr_max_pages=15） |
| POST | /api/v1/library/imports/{job_id}/concepts | 提取概念 |
| POST | /api/v1/library/imports/{job_id}/create-sprint | 一键开冲刺 → {contract_id,sprint_id,generated,blueprint_complete} |

### 11 阶段 id 与中文标签（本地映射）

```
created=已创建 contract_confirmed=合同已确认 baseline_assessment=前测
concept_map_review=概念地图 plan_confirm=计划确认 learning=核心学习
targeted_practice=定向练习 transfer_check=迁移检验 posttest=后测
report=学习报告 follow_up=间隔复测
```
推进合法链：每阶段只能推到下一个（服务端校验，409 如实显示错误）。

---

## 关键约束（违反即返工）

1. **ArkTS 规范**：遵循现有组件（StudyPane/AuthPane）的写法风格；
   状态用 @State/@Link；网络请求复用 AiosApi.ets 的 http 封装模式
   （先 UrlPolicy.validateBaseUrl；失败 ApiResult.ok=false + 中文
   错误文案，绝不渲染 [object Object]）。
2. **答案隔离不变式**：assessment/transfer/practice/review 的响应
   里没有 answer 字段——UI 也不得假设有；判分只在提交后来自服务端。
3. **token 零持久化**：不入 preferences、不写文件、不打日志（沿用
   TokenVault 约定）。
4. **六 Tab 结构零改动**：Index.ets 的 Tabs 布局不动；新面板通过
   Navigation/router 推入。
5. **错误如实**：409（非法推进/重复提交）、404、503 都要显示服务端
   detail 文案，不吞错。

---

## 构建与真机验收

### 构建命令（apps/harmony 目录下）

```
hvigorw clean --no-daemon
hvigorw assembleHap --mode module -p product=default -p buildMode=release --no-daemon
```
（可参考 tools/harmony_release/release_build.py 的编排）

### 目标设备（深开鸿 VM）

- HDC 地址：`192.168.161.137:10178`（已验证连通，KaihongOS 6.1.2.08）
- hdc 路径：`C:\Program Files\Huawei\DevEco Studio\sdk\default\openharmony\toolchains\hdc`
- 连接：`hdc tconn 192.168.161.137:10178`
- 安装：`hdc -t 192.168.161.137:10178 install <hap路径>`
- 截屏验证：`hdc -t 192.168.161.137:10178 shell snapshot_display -f /data/local/tmp/x.jpeg` 后 `hdc file recv`

### API 测试环境

- 生产 API：`https://ndtool.cn/aios/api/v1`
- 测试账号：用户名 `qa_m15025` / 密码 `Qa-M15-025!x9`（该账号已有
  确认过概念的教材冲刺数据可用于面板验证）
- 备用账号（干净书架）：`sim_strong` / `Sim-Learner-2026!`

### 验收清单（真机逐项过并截图存证）

1. 设置页配置服务器地址 `https://ndtool.cn/aios` → 连接测试通过
2. 登录 qa_m15025 成功
3. 学习 Tab 显示两张入口卡
4. 学习冲刺面板：合同列表渲染 → 阶段链显示 → 推进按钮工作
5. 前测拉题（若冲刺在前测阶段）：题目+选项渲染 → 作答+信心 → 提交显示分数
6. 教材工作台：搜索「数学」或「math」命中 → 书架列表渲染（qa 账号
   已有 3 本导入教材）
7. 退出登录 → 重新登录 → 会话保持正常

---

## 交付物

1. 新增 ArkTS 源文件（面板组件 + API 扩展 + 页面路由）
2. 构建成功的 HAP（release 模式）
3. 真机验收截图序列（≥5 张：学习Tab入口/冲刺面板/教材工作台/搜索结果/书架）
4. 简要交付说明：改了哪些文件、验收清单每项 pass/fail、已知限制

## 已知限制说明（如实）

- 本任务**不要求**签名发布（无 AGC 签名材料时交付未签名 HAP 即可）
- 扫描书 OCR 在服务端可能超时（60s 网关）——真机验收用已解析的
  教材（qa 账号书架里的「离散数学及其应用」已 parsed）
- 若 VM 断连：`hdc tconn` 重连即可；VM 是 VMware 里的深开鸿系统
