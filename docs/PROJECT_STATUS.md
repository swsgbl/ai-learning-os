# 项目进度台账

唯一进度真相源。每完成一个任务立即更新本文件，再 commit + push。
验收依据：docs/delivery/ 定版文档与 docs/delivery/11_IMPLEMENTATION_BACKLOG.md。

## 当前里程碑

M0 Foundation（收尾中）

## 当前任务

M0-07 密钥与隐私配置 —— 进行中

## 已完成任务

| 任务 | 提交 | 测试证据 | 日期 |
|---|---|---|---|
| M0-01 生产 monorepo | 036cd6e | typecheck/lint/build/pytest/ruff 全绿 | 2026-08-30 |
| M0-02 Docker Compose 基础设施 | 036cd6e | compose 服务与 healthcheck、持久化卷就位 | 2026-08-30 |
| M0-03 FastAPI skeleton | 036cd6e | pytest 3 passed | 2026-08-30 |
| M0-04 Next.js skeleton | 036cd6e | build 成功，7 路由可渲染 | 2026-08-30 |
| 环境恢复（依赖安装） | 无代码变更 | baseline 全绿 | 2026-08-31 |
| 进度台账建立 | 129f638 | - | 2026-08-31 |
| M0-05 + M0-持久化（PostgreSQL repository + Alembic） | a6bd316, merge 2fa1a8c | pytest 12 passed（含真实 PG）；migration up/down/dry-run；全部门禁绿 | 2026-08-31 |
| M0-06 CI 门禁（GitHub Actions） | 1eb3436, merge 83bf7ea | Actions run 33329866289：Web 1m10s 绿 / API 34s 绿（真实 PG service container + migration check） | 2026-08-31 |

## 待办任务（按 backlog 顺序）

- [ ] M0-07 密钥与隐私配置（进行中）
- [ ] M0-02+ 运维验证：compose 全栈健康检查、重启数据仍在；MinIO healthcheck 补齐
- [ ] M2-04 Redis timeout worker（Redis 延迟队列 + DB 事务兜底）
- [ ] M1-01 Source Registry、M1-02 License State Machine
- [ ] M1-03~08 Content Ingestion（上传、parser adapter、chunk/Evidence、队列、质量报告）
- [ ] M2-01~11 题库 schema、试卷导入、grader、报告
- [ ] M3-01~07 Student Model
- [ ] M4-01~09 Voice
- [ ] M5-01~08 Search + 治理
- [ ] M6-01~07 评测/安全/备份
- [ ] M7-01~06 Release

## 阻塞与风险

| 类型 | 内容 | 状态 |
|---|---|---|
| 已修复 | asyncpg naive-datetime 时区 bug（曾致考试误判过期）；写库统一 aware UTC | 回归测试锁定 |
| 已修复 | sequence 跳号契约漏洞；两实现同步收紧为连续递增 | 契约测试锁定 |
| 环境 | postgres 容器运行中；push 偶发代理抖动，带 http_proxy=127.0.0.1:7892 重试即可 | 无阻塞 |
| 观测 | Actions 提示 actions/checkout 等目标 Node20 弃用（cosmetic） | 后续 hardening 升级 |

## 架构决策记录

| # | 决策 | 依据 | 日期 |
|---|---|---|---|
| 1 | Repository Protocol 先行，PG 等价替换且不改 API route | AGENTS.md 规则 3 | 2026-08-30 |
| 2 | 考试时间/客观判分/幂等提交服务端权威；active 响应不含答案解析 | docs/delivery/04 | 2026-08-30 |
| 3 | 答案事件 append-only，(exam_session_id, sequence) 唯一且连续递增；同载荷重放幂等、异载荷 409 | docs/delivery/04 §2.9/§6 | 2026-08-30 |
| 4 | 单元测试用 SQLite 替身；真实 PG 集成经 AIOS_PG_TEST_URL 门控 | AGENT_PROMPTS M0-06 | 2026-08-31 |
| 5 | 时间列统一存 aware UTC；读回统一 astimezone(UTC) | 实测修复 asyncpg 行为 | 2026-08-31 |
| 6 | SQLite 替身直接 create_all；PG 启动自动 alembic upgrade head | 开发者零迁移心智负担 | 2026-08-31 |
| 7 | CI api job 挂真实 PG service container，migration check 显式三步 | M0-06 验收 | 2026-08-31 |

## 下一任务

M0-07 完成后：compose 全栈运维验证（M0 收尾），随后进入 M1-01 Source Registry。
