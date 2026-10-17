# M15-001 证据：产品闭环第一批（定制课程 + 首页搜索 + 首用引导 + materialize）

- 分支 `feat/m15-001-web-product-loop`（两段提交分别经 PR #371
  `5f7784e` / PR #372 `ae6673c5` 合并），基于 M14-277 后 main。

## 背景：全面产品审计（用户旅程视角）

用户批评逐条核实（全部成立）：注册功能（Web 有登录页双 tab 但
发现性弱；**Android/Harmony 无**）、首页搜索（无直达，藏在学习库
内）、定制课程（后端 LLM 生成流完整但**无用户入口页**）、用户
引导（无 Onboarding）、下载后第一步（裸报「无法连接 API」）、
API 配置暴露给最终用户（自托管形态泄漏）。**技术切片驱动缺产品
旅程闭环**——本批开始系统性补齐。

## 交付面（第一批：Web 四项）

1. **`/create` 定制课程页**（`CourseCreator` + `course-creator.tsx`）：
   目标输入（示例 chips）→ 生成草稿 → 大纲审阅（章节/评估/生成
   备注 details）→ **双动作** → 成功卡；历史草稿列表（点击回看）。
2. **`materialize` 端点**（`course_workflow.py`）：
   `POST /api/v1/courses/generation-drafts/{id}/materialize`——
   草稿 plan.assessments → essay 模板题（rubric_points
   ["说明该概念的核心含义","给出一个具体例子"]）组 practice 卷
   （generation_method=ai_generated、concept_ids 挂题、
   estimated_seconds=300、score=10/题、时长 300s×题数），归属
   current_owner_id；权限 = admin 或草稿创建者（repo.get_owner
   比对；auth off 本地模式草稿无主放行）；**不要求 approved**
   （自用练习材料 ≠ 全局课程库入库——治理 approve 的 admin-only
   语义零变化，双路径并存）。
3. **首页改造**（`home-dashboard.tsx`）：搜索直达（form 提交 →
   `/library?q=`，LibraryView useSearchParams 初始查询 + page
   Suspense 边界）；「定制我的课程」主 CTA 卡（border-accent）；
   首次使用三步引导（已登录 + papers 空 + localStorage 未关闭 →
   定制课程→练习→报告卡，X 关闭记忆）。
4. **导航**：BASE_NAV 加「定制课程」（Sparkles，第二位）。

## 验证（全部真实执行）

| 层 | 结果 |
| --- | --- |
| course_workflow 单测（+2 materialize） | **12 passed**（materialize 201→papers 可见→/exams 201；404/空评估 422） |
| Web 全量 | typecheck/lint/**334 tests**/build 全绿 |
| 本地浏览器全链（隔离 PG 栈 + Playwright） | /create 生成→**4 章审阅**→materialize→「练习卷已生成 🎉 4 题」→点「立即开考」→**考场 19:56 倒计时 4 题进行中** |
| 首页三新元素 | 搜索框/CTA/引导渲染 ✓；搜索跳转 `/library?q=%E5%87%BD%E6%95%B0` URL 编码正确 ✓ |
| **生产栈 learner 全链** | 登录（验收账号）→ 草稿 **201**（4 章，真 LLM）→ materialize **201**（pap_9109…，4 题）→ 开考 **201** |

过程发现并修复：课程概念匹配为 canonical_name **子串匹配**——
goal 需含概念名（SQL 案例验证）；approve 后无课程→试卷路径（本批
materialize 修复）；concept DAG 需 admin 发布（生产已发布 v1 验收
用 SQL 概念图 4 节点）。

## 生产落地

- **API `m15-001-production`（`2cfc1b15`）+ Web `m15-001-production`
  （`7c2595dc`）双双上线**，7/7 healthy；
- 生产验收数据（隔离可辨识）：e2e_loop_* 验收账号、SQL 概念图
  v1（note 标注 M15-001 验收）、2 份草稿 + 2 份 materialize 卷。

## 待办（后续批，非本批）

- **第二批**：Android 注册 UI + 首启连接引导（代替裸报无法连接）；
  Harmony 注册 + 首启引导；
- **第三批**：设置分区（baseUrl/API 密钥移「高级/自托管」折叠区，
  最终用户默认无感）；登录页注册 tab 发现性强化。

## 文档同步

- CHANGELOG / PROJECT_STATUS 同步（M14-277 转前一任务快照）。
