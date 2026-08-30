# 开发计划 v2

计划周期：10 周  
默认团队：1 名全栈主力 + 1 名 AI/学习工程兼职 + 1 名测试/内容审核兼职；若单人开发，按里程碑延长而非跳过验收。  
代码库建议：从现有 `grok-workspace` 提取 UI/交互参考，但生产版新建 monorepo，避免把原型依赖当作生产基础。

## 1. 里程碑总览

| 里程碑 | 周期 | 交付 | Gate |
|---|---|---|---|
| M0 Foundation | Week 1 | 可运行 monorepo 和基础设施。 | 登录、健康检查、DB 持久化、CI 通过。 |
| M1 Content | Week 2 | 文档解析和证据入库。 | PDF 入库后可按页引用。 |
| M2 Exam | Week 3-4 | 题库、考试、评分、报告。 | 完整 MCQ 考试闭环。 |
| M3 Personalization | Week 5 | Student Model 和每日计划。 | 第二次任务受错题影响。 |
| M4 Voice | Week 6-7 | Voice Quiz 和 Voice Tutor。 | 语音完整做完一套题。 |
| M5 Search | Week 8 | 来源化课程/真题检索。 | 结果带授权状态。 |
| M6 Hardening | Week 9 | 测试、评测、安全。 | 关键指标达标。 |
| M7 Release | Week 10 | 本地部署和发布包。 | release checklist 全通过。 |

## 2. Week 1 Foundation

任务：

1. 建 monorepo：`apps/web`、`apps/api`、`services/learning-engine`、`services/agent-runtime`、`services/search`、`services/voice`、`services/grading`、`packages/schemas`、`infra`。
2. Docker Compose：PostgreSQL、Redis、MinIO。
3. FastAPI skeleton、health/readiness。
4. Next.js skeleton、登录页、课程占位页。
5. Alembic migration。
6. 统一配置与 secret 读取。
7. CI：lint、typecheck、unit test、migration check。

验收：

- `docker compose up` 后 Web/API/DB/Redis/MinIO 健康。
- 创建用户和课程后重启容器数据仍存在。
- 所有服务有 structured log request id。

## 3. Week 2 Content Ingestion

任务：

1. 上传 PDF/DOCX/PPTX。
2. Parser adapter：Docling/MinerU/Marker/olmOCR。
3. 文档 -> blocks -> chunks -> embedding。
4. Evidence Object 与页码定位。
5. 解析任务队列与重试。
6. 文件 hash 去重。
7. 解析质量报告。

验收：

- 一份含公式和表格的课程 PDF 能入库。
- 前端可以显示 chunk 并跳转页码。
- 同一文件重复上传不产生重复资源。
- 解析失败可换 parser 重跑。

## 4. Week 3 Question Bank + Exam

任务：

1. Question/Paper schema。
2. 手动录入、JSON 导入、试卷解析草稿。
3. ExamSession 状态机。
4. 服务端权威计时。
5. 答案自动保存与 append-only event。
6. 断线恢复。
7. 超时自动提交。
8. 题目导航、标记、跳过。

验收：

- 完成 20 道 MCQ mock exam。
- 刷新、断网、重复提交不破坏状态。
- 修改客户端时间无法延长考试。

## 5. Week 4 Grading + Review

任务：

1. objective deterministic grader。
2. numeric tolerance verifier。
3. short answer matcher。
4. 错因分类器。
5. 报告页面。
6. 补救任务生成。
7. golden grading set。

验收：

- 提交后得到总分、概念得分、错题列表和讲解。
- 每个分数能追溯到答案事件和规则版本。
- 主观题输出结构化 JSON 且低置信度进入复核。

## 6. Week 5 Student Model

任务：

1. StudentConceptState。
2. correctness、difficulty、response time、hint used 输入。
3. mastery 更新。
4. forgetting risk。
5. FSRS-like review scheduler。
6. misconception candidate。
7. daily planner。

验收：

- 第一次考试错的概念在明日任务中出现。
- 单次错误不会直接写成长期误解。
- 复习任务有 `next_review_at`。

## 7. Week 6 Voice Quiz

任务：

1. LiveKit server/token。
2. ASR/TTS provider adapter。
3. VoiceSession FSM。
4. 命令解析。
5. 答案规范化。
6. TTS 播报题目和选项。
7. barge-in。
8. 断线恢复。
9. latency tracing。

验收：

- 语音完成 20 道题。
- “选 B/第二个/改成 C/重复一遍”均正确处理。
- 会话中断后重连继续当前题。
- 每阶段 latency 有 trace。

## 8. Week 7 Voice Tutor

任务：

1. Tutor Policy。
2. hint ladder。
3. evidence-grounded explanation。
4. 多角度错题讲解。
5. 考试/练习权限切换。
6. 语音报告摘要。

验收：

- 用户说“不会”时逐步提示，不直接给答案。
- 考试模式禁止答案泄露、搜索和解决方案工具。
- 每个解释有 Evidence 或明确标记为通用推理。

## 9. Week 8 Web/Search

任务：

1. Search provider abstraction。
2. Source Registry。
3. Crawl4AI/Playwright adapter。
4. robots/rate limit/SSRF 防护。
5. license classifier。
6. 结果去重和排序。
7. course importer。
8. 人工审核队列。

验收：

- “找某大学某课程公开资料”返回来源化结果。
- S/A/B/C 来源可见。
- UNKNOWN 与 ACCESS_CONTROLLED 不进入公共复用池。
- 危险 URL 被拒绝。

## 10. Week 9 Evaluation / Hardening

任务：

1. 单元测试覆盖状态机和评分。
2. 集成测试覆盖考试全链路。
3. E2E：上传、构卷、考试、评分、复盘。
4. 语音评测集。
5. grading agreement。
6. RAG citation eval。
7. prompt injection 测试。
8. SSRF/权限/幂等测试。
9. backup/restore 演练。

最低指标：

| 指标 | MVP 目标 |
|---|---|
| 客观题判分正确率 | 100% on golden set。 |
| 考试状态一致性测试 | 0 个状态破损。 |
| 语音命令意图准确率 | >= 95% on local eval set。 |
| 语音完整套题完成率 | >= 90% in lab。 |
| 主观题评分一致性 | 建立基线，人工复核率可配置。 |
| 证据引用有效率 | >= 90% on sampled explanations。 |
| API p95 | <= 500ms excluding model calls。 |
| CI | lint/typecheck/unit/integration 全绿。 |

## 11. Week 10 Release

任务：

1. Docker Compose production profile。
2. 本地模型和云端模型配置文档。
3. 数据备份/恢复。
4. 隐私模式说明。
5. 内容授权说明。
6. seed 数据和演示课程。
7. release checklist。
8. 版本号和 changelog。

发布前必须验证：

- `npm run typecheck`
- `npm test`
- `npm run build`
- API tests
- E2E smoke
- Docker build
- database migration dry-run
- backup restore
- voice smoke with real microphone
- timeout submit
- license gate

## 12. 测试矩阵

| 层 | 覆盖 |
|---|---|
| Unit | FSM transition、答案规范化、客观判分、license gate、mastery update。 |
| Integration | DB migration、考试 API、语音 API、解析队列、评分任务。 |
| E2E | 上传 -> 构卷 -> 考试 -> 评分 -> 复盘 -> 次日计划。 |
| Voice | 命令、噪声、打断、断线、延迟。 |
| Security | 认证、越权、SSRF、prompt injection、sandbox escape。 |
| Data | 备份、恢复、去重、事件冲突。 |
| Education | 前测、后测、保持率、迁移题。 |

## 13. 现有原型迁移

可复用：

- `grok-workspace/src/lib/types.ts` 的业务类型思路。
- 课程、试卷、语音入口的 UI 结构。
- 本地判分和 session cache 的交互假设。

不直接复用：

- 未验证依赖和未迁移的启动流程。
- 浏览器端权威状态。
- 缺少服务端考试事务的实现。

迁移方式：

1. 新建生产 monorepo。
2. 按 schema 重写 API。
3. 将原型 UI 逐步迁移。
4. 每迁移一个流程必须补集成测试。

