# 项目进度台账

唯一进度真相源。每完成一个任务立即更新本文件，再 commit + push。
验收依据：`docs/delivery/02_PRD_FINAL.md` ~ `12_DEPLOYMENT_OPERATIONS_RUNBOOK.md`（v2.2 定版）与 `docs/delivery/11_IMPLEMENTATION_BACKLOG.md`。

## 当前里程碑

M0 Foundation（进行中）

## 当前任务

M0-持久化基线：PostgreSQL repository + Alembic migration（分支 `feature/M0-postgres-repository`）

## 已完成任务

| 任务 | 提交 | 测试证据 | 日期 |
|---|---|---|---|
| M0-01 生产 monorepo（apps/web + services/api + infra） | `036cd6e` Initial production foundation | typecheck/lint/build/pytest 3 passed/ruff 全绿 | 2026-08-30 |
| M0-02 Docker Compose 基础设施（PostgreSQL/Redis/MinIO + healthcheck + 卷） | `036cd6e` | compose 配置含 healthcheck 与持久化卷（运行期验证见 M0-持久化任务） | 2026-08-30 |
| M0-03 FastAPI skeleton（health、CORS、settings、内存仓储、考试 API 草案） | `036cd6e` | `pytest services/api` 3 passed；服务端时间权威与答案隐藏有测试 | 2026-08-30 |
| M0-04 Next.js skeleton（首页/考试/语音/复盘/课程库路由） | `036cd6e` | `npm run build` 成功，7 条路由可渲染 | 2026-08-30 |
| 环境恢复（npm install + .venv + pip install） | 无代码变更 | baseline 全绿：typecheck ✓ lint ✓ build ✓ pytest 3 passed ✓ ruff ✓ | 2026-08-31 |

## 待办任务（按 backlog 顺序）

- [ ] M0-持久化：PostgreSQL repository（SQLAlchemy async）+ Alembic migration + SQLite 测试替身
- [ ] M0-06 CI 门禁：lint / typecheck / unit / migration check
- [ ] M0-07 密钥与隐私配置：`.env.example` 补全、privacy mode（local/cloud/hybrid）可配置
- [ ] M0-02+ 运维验证：`docker compose up` 后健康检查、重启数据仍在（含 MinIO healthcheck 补齐）
- [ ] M2-03/04 ExamSession FSM 持久化 + Redis timeout worker（Redis 延迟队列 + DB 事务兜底）
- [ ] M1-01 Source Registry、M1-02 License State Machine
- [ ] M1-03~08 Content Ingestion（上传、parser adapter、chunk/Evidence、队列、质量报告）
- [ ] M2-01~11 题库 schema、试卷导入、客观/数值/主观 grader、报告
- [ ] M3-01~07 Student Model、Concept DAG、FSRS-like 调度、每日计划
- [ ] M4-01~09 LiveKit、ASR/TTS adapter、VoiceSession FSM、意图解析、latency tracing
- [ ] M5-01~08 Search + Source Registry 治理、课程/试卷导入
- [ ] M6-01~07 评测、安全、隐私、备份、负载
- [ ] M7-01~06 Release

## 阻塞与风险

| 类型 | 内容 | 状态 |
|---|---|---|
| 环境 | Docker Desktop 可用但当前未启动 compose 栈；PG/Redis 集成验证需先 `docker compose up -d` | 非阻塞，任务内处理 |
| 决策 | 无 | - |

## 架构决策记录

| # | 决策 | 依据 | 日期 |
|---|---|---|---|
| 1 | 仓储协议（`Repository` Protocol）先行，内存实现跑通契约，PostgreSQL 等价替换且不改 API route | AGENTS.md 规则 3；README 架构边界 | 2026-08-30 |
| 2 | 考试时间、客观判分、幂等提交由服务端权威控制；active 状态响应不含 answer/explanation/angles | docs/delivery/04 §4.5、§7 错误码；AGENTS.md 规则 1/2 | 2026-08-30 |
| 3 | 答案事件 append-only，`(exam_session_id, sequence)` 唯一，重复序号同载荷幂等、异载荷拒绝 | docs/delivery/04 §2.9、§6 | 2026-08-30 |
| 4 | PostgreSQL 单元测试用 SQLite aiosqlite 内存替身（prompt pack 允许），真实 PG 集成验证走 docker compose | docs/AGENT_PROMPTS.md M0-06；本机无常驻 PG | 2026-08-31 |

## 下一任务

完成 M0-持久化（PostgreSQL repository + Alembic）后，按序进入 M0-06 CI 门禁（GitHub Actions：lint / typecheck / unit / migration check），随后 M0-07 密钥与隐私配置。
