# 项目进度台账

唯一进度真相源。每完成一个任务立即更新本文件，再 commit + push。
验收依据：`docs/delivery/02_PRD_FINAL.md` ~ `12_DEPLOYMENT_OPERATIONS_RUNBOOK.md`（v2.2 定版）与 `docs/delivery/11_IMPLEMENTATION_BACKLOG.md`。

## 当前里程碑

M0 Foundation（收尾中）

## 当前任务

M0-06 CI 门禁（GitHub Actions：lint / typecheck / unit / migration check）——下一个任务

## 已完成任务

| 任务 | 提交 | 测试证据 | 日期 |
|---|---|---|---|
| M0-01 生产 monorepo（apps/web + services/api + infra） | `036cd6e` | typecheck/lint/build/pytest 3 passed/ruff 全绿 | 2026-08-30 |
| M0-02 Docker Compose 基础设施 | `036cd6e` | postgres/redis/minio 服务与 healthcheck、持久化卷就位 | 2026-08-30 |
| M0-03 FastAPI skeleton | `036cd6e` | pytest 3 passed | 2026-08-30 |
| M0-04 Next.js skeleton | `036cd6e` | build 成功，7 路由可渲染 | 2026-08-30 |
| 环境恢复（npm install + .venv + pip install） | -（无代码变更） | baseline 全绿 | 2026-08-31 |
| 台账建立 | `129f638` | - | 2026-08-31 |
| **M0-05 + M0-持久化：PostgreSQL repository + Alembic migration** | 本分支 | 见下方证据 | 2026-08-31 |

### M0-持久化测试证据

- 单元/集成：`pytest services/api` **12 passed**（内存/SQLite 替身契约对等测试、DB 驱动 API 全链路、migration up/down/幂等/dry-run、真实 PG 集成）
- 真实 PostgreSQL（docker compose postgres:17）验证：
  - `alembic upgrade head` → `downgrade -1` → `upgrade head` 全部成功；6 张表 + `alembic_version`（revision `0001_initial`）落库
  - API 全链路：建卷 → active 响应无 answer/explanation/angles → 答案 seq 1-3 全部 200 → 幂等提交 → 复盘读取一致
  - 跨进程持久化：第二次进程 seed 跳过，papers=2、questions=6 无重复
- ruff / typecheck / lint / build 全绿
- API route 零改动（仅 main.py 增加仓储选择与 lifespan）

## 待办任务（按 backlog 顺序）

- [ ] M0-06 CI 门禁：GitHub Actions（lint / typecheck / unit / migration check）
- [ ] M0-07 密钥与隐私配置：`.env.example` 补全、privacy mode（local/cloud/hybrid）可配置
- [ ] M0-02+ 运维验证：compose 全栈启动健康检查、重启数据仍在（含 MinIO healthcheck 补齐）
- [ ] M2-03/04 ExamSession FSM 持久化 + Redis timeout worker（Redis 延迟队列 + DB 事务兜底）
- [ ] M1-01 Source Registry、M1-02 License State Machine
- [ ] M1-03~08 Content Ingestion
- [ ] M2-01~11 题库 schema、试卷导入、grader、报告
- [ ] M3-01~07 Student Model
- [ ] M4-01~09 Voice
- [ ] M5-01~08 Search + 治理
- [ ] M6-01~07 评测/安全/备份
- [ ] M7-01~06 Release

## 阻塞与风险

| 类型 | 内容 | 状态 |
|---|---|---|
| 修复 | 发现并修复 asyncpg naive-datetime 按本机时区编码 timestamptz 的问题（曾导致考试被误判过期）；写库统一 aware UTC | 已解决，含回归测试 |
| 契约 | sequence 收紧为连续递增（docs/DEVELOPMENT.md 规则 3），内存与 PG 两个实现同步；旧内存实现允许跳号属契约漏洞 | 已解决，契约测试锁定 |
| 环境 | postgres 容器保持运行供后续任务复用；`AIOS_PG_TEST_URL` 仅本地运行时设置，不入库 | 无阻塞 |

## 架构决策记录

| # | 决策 | 依据 | 日期 |
|---|---|---|---|
| 1 | 仓储协议（`Repository` Protocol）先行，内存实现跑通契约，PostgreSQL 等价替换且不改 API route | AGENTS.md 规则 3 | 2026-08-30 |
| 2 | 考试时间、客观判分、幂等提交由服务端权威控制；active 响应不含 answer/explanation/angles | docs/delivery/04 §4.5、§7 | 2026-08-30 |
| 3 | 答案事件 append-only，`(exam_session_id, sequence)` 唯一；序号必须连续递增（不允许跳号），同载荷重放幂等、异载荷 409 | docs/delivery/04 §2.9、§6；DEVELOPMENT.md 规则 3 | 2026-08-30 |
| 4 | PostgreSQL 单元测试用 SQLite aiosqlite 替身；真实 PG 集成测试经 `AIOS_PG_TEST_URL` 门控 | docs/AGENT_PROMPTS.md M0-06；本机无常驻 PG | 2026-08-31 |
| 5 | 时间列统一存 aware UTC（asyncpg 会把 naive 按本机时区编码进 timestamptz）；读回统一 `astimezone(UTC)` | 实测发现，契约测试 + 真实 PG 集成测试锁定 | 2026-08-31 |
| 6 | `prepare_database`：SQLite 直接 create_all（测试替身），PostgreSQL 启动时自动 `alembic upgrade head` | 开发者零迁移心智负担；生产路径显式 | 2026-08-31 |

## 下一任务

M0-06 CI 门禁：`.github/workflows/ci.yml`，job 内执行 ruff + pytest + typecheck + lint + build + migration dry-run（SQLite），推 main/PR 触发。
