# 开发指南

## 里程碑状态

全部里程碑进度、任务台账与架构决策见 `docs/PROJECT_STATUS.md`（单一真相源，
本文件不再维护里程碑快照——历史快照曾停留在 M0，已删除以免误导）。

当前版本 `0.1.0`（真相源：仓库根 `VERSION`）。M0-M7 已交付；M8-00 为发布
真实性修复（CI 环境隔离、镜像版本打包、MinIO 持久化、Docker CI 门禁）。

## API 契约

```text
GET    /api/v1/papers
POST   /api/v1/papers/{paper_id}/exams
GET    /api/v1/exams/{exam_id}
PUT    /api/v1/exams/{exam_id}/answers
POST   /api/v1/exams/{exam_id}/submit
GET    /api/v1/exams/{exam_id}/submission
```

规则：

1. `server_started_at` 和 `server_end_at` 只由 API 写入。
2. `questions` 不包含 `answer`、`explanation`、`angles`。
3. `sequence` 必须从 1 开始并递增（不允许跳号）。
4. 重复提交返回同一 submission。
5. 交卷后答案事件被拒绝。

## 数据库

```powershell
docker compose -f infra/docker-compose.yml up -d postgres
# API 启动时自动 alembic upgrade head；手动执行：
alembic -c services/api/alembic.ini upgrade head
```

- PostgreSQL 仓储与内存仓储共用同一 `Repository` 协议，API route 不感知实现差异。
- 单元测试用 SQLite aiosqlite 内存替身；真实 PG 集成测试设 `AIOS_PG_TEST_URL` 后运行。
- 环境变量按用途隔离（M8-00 教训）：pytest 只认 `AIOS_PG_TEST_URL`；
  `DATABASE_URL` 属于 alembic / API 运行时，混入 pytest 会把「无 DB 503」
  测试翻成 DB 路径导致断言失败。

## 对象存储

- compose 的 api 服务注入 `S3_ENDPOINT/S3_BUCKET/S3_ACCESS_KEY/S3_SECRET_KEY`
  连接 minio；`MinioObjectStore` 启动时幂等确保 bucket 存在。
- 本地裸跑 API（无 S3_* 环境变量）回退内存实现——上传数据不持久，仅供契约调试。

## 本地验证

```powershell
npm run typecheck
npm run lint
npm run build
ruff check services/api
$env:AIOS_PG_TEST_URL='postgresql+asyncpg://aios:aios@127.0.0.1:5433/ai_learning_os'
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
python -m pytest services/api -q
```

Docker 生产本地版冒烟（全服务 healthy + 端点 + 上传重启读回）：

```bash
docker compose -f infra/docker-compose.yml --profile local up -d --build
bash infra/smoke_docker.sh
```
