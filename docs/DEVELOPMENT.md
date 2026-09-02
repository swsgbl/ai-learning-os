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

## 认证与角色（M9-01 / M9-04）

- `AUTH_SECRET` 未配置 = 认证关闭（本地单用户模式），`GET /api/v1/auth/status` 如实透出；
- 配置后全业务路径要求 Bearer token；**治理动作（source 登记/verify/license 改判、
  概念图发布、课程生成/导入、变式、试卷抽取草稿的 approve/reject、审计读取）仅 admin**：
  learner 返回 403（门禁先于 404，不暴露存在性）；
- 个人数据（私有资源、考试、语音会话、搜索记录）严格 owner-scoped（他人 404）；
- 角色：`role` 默认 learner，无默认管理员账号——首个 admin 由持有数据库访问权的
  运维经 CLI 提升：`python -m app.ops.cli admin promote|demote|list <username> --db-url ...`；
  每次变更写审计（actor/before/after/request_id）；
- 生产 AUTH_SECRET fail-closed：APP_ENV=production 时未配置 / <32 字节 / 公开默认占位值
  一律拒绝启动；其他环境配置了但 <32 字节也明确报错；
- CORS 与 Web 端口联动：`AIOS_WEB_PORT` 自定义时默认跟随，`AIOS_CORS_ORIGINS` 可完整覆盖
  （冒烟验证 preflight 一致性）。

## 审计（M9-04）

治理动作（license 变更、DAG 发布、四类草稿 approve/reject、角色提升/降级）写
`audit_log`：actor、action、target、before/after、时间、request id（响应头
X-Request-ID 可关联）。读取 `GET /api/v1/audit` 仅 admin（auth off 本地模式可读）。

### M9 里程碑边界（如实陈述，2026-09-02，M9-05 后更新）

- 已交付：认证基座、三域归属隔离、Web 登录 UI、角色授权+治理审计、
  **私有语料边界（search/course generation 只见自有+public 语料）**、
  **四类草稿 owner 归属（create/list/get）**、**审计与业务同事务（fail-closed）**；
- 已知边界：generation/variant 草稿的历史 NULL 归属行 auth on 时仅 admin 可治理读取；
  papers 为公共题库无归属；token 存 localStorage（无 BFF/cookie 刷新机制）；
  审计无防篡改哈希链；Web 侧草稿治理界面未做（API 能力已就绪）。
- 部署绑定：所有端口默认 127.0.0.1；LAN/外网需 `AIOS_BIND_IP=0.0.0.0` 且必须同时
  设强 AUTH_SECRET + APP_ENV=production（启动 fail-closed），否则不要对外暴露。


- M9-01 认证基座、M9-02 三域归属隔离、M9-03 Web 登录 UI、M9-04 角色授权+治理审计已交付；
- **已知边界（非完整多用户系统）**：部分草稿类实体（课程生成/导入、变式、试卷抽取）
  的**创建**仍是全局共享（无 owner 列）——审核已 admin 门禁但创建未按用户隔离；
  papers 为公共题库无归属；token 存 localStorage（无 BFF/cookie 刷新机制）；
  审计无防篡改链（append-only 靠约束约定而非哈希链）。


- `AUTH_SECRET` 未配置 = 认证关闭，`GET /api/v1/auth/status` 如实透出 `auth_enabled=false`；
- 配置后（compose 已注入 dev 值）全业务路径要求 `Authorization: Bearer <token>`，
  `register/login/status` 与 `/health`、`/api/v1/version`、`/docs` 豁免；
- 端点：`POST /api/v1/auth/register`（重复 409）、`POST /api/v1/auth/login`（失败统一
  「用户名或密码错误」防枚举）、`GET /api/v1/auth/me`；JWT HS256，默认 24h 过期。
- 生产部署用部署 secret 覆盖 `AIOS_AUTH_SECRET`；密码只存 bcrypt 哈希（72 字节上限）。
- 数据归属拆分（资源/考试/语音按 user_id 隔离）是 M9-02 演进项，当前认证是全局门禁而非租户隔离。

## LLM 接入（M10-01）

- OpenAI 兼容 gateway（`app/llm/gateway.py`）：`LLM_ENDPOINT/LLM_API_KEY/LLM_MODEL`
  三槽位齐备才装配；key 只放本机 .env 或部署 secret，不入库不入码不入日志。
- 接入点：主观题 rubric LLM judge（`app/llm/rubric_judge.py`，实现 M2-10 预留的
  `RubricJudge` 协议）。`RUBRIC_JUDGE=llm` 启用；默认 `keyword` 保持确定性判分。
- fail-closed：LLM 不可用 / 响应非法 / 模型编造 evidence_id → judge 返回 None →
  essay 进复核三态。模型缺席绝不产生分数；`judge_model`/`prompt_hash` 服务端覆写留痕。
- 真实端点冒烟（需要部署 key，无 key 明确失败不虚报）：`bash infra/smoke_llm.sh`。
- 单测全部 fake transport（协议格式 / 失败语义 / 装配两态），不发起网络调用。
- 已知取舍（ADR 65）：判分管线是同步域函数，gateway 用同步 httpx——单用户本地版
  可接受；判分并发化是后续演进。

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
