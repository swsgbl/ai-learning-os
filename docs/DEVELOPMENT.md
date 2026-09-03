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
- 单元测试用 SQLite aiosqlite 内存替身；真实 PG 集成测试设 `AIOS_PG_TEST_URL` 后运行，
  **且必须指向隔离测试库（`ai_learning_os_test` 等）**——安全门控
  （`services/api/app/db/test_gate.py`，M10-04 返工）只放行隔离测试库名，
  主/共享库名（`ai_learning_os`）、维护库 `postgres`、缺库名、非 PG 驱动
  一律自动跳过并给出原因，被拒绝的测试**不建立任何连接**。
- 环境变量按用途隔离（M8-00 教训）：pytest 只认 `AIOS_PG_TEST_URL`；
  `DATABASE_URL` 属于 alembic / API 运行时，混入 pytest 会把「无 DB 503」
  测试翻成 DB 路径导致断言失败。

## 认证与角色（M9-01 / M9-04）

- `AUTH_SECRET` 未配置 = 认证关闭（本地单用户模式），`GET /api/v1/auth/status` 如实透出；
- 配置后全业务路径要求 Bearer token 或浏览器 HttpOnly cookie；**治理动作（source 登记/verify/license 改判、
  概念图发布、课程生成/导入、变式、试卷抽取草稿的 approve/reject、审计读取）仅 admin**：
  learner 返回 403（门禁先于 404，不暴露存在性）；
- Web 登录态：`POST /auth/login` 设置 `aios_auth` HttpOnly cookie（默认
  `SameSite=Lax`；HTTPS 反代部署设置 `AIOS_AUTH_COOKIE_SECURE=true`）。页面
  JavaScript 不保存 JWT；CLI/API 仍使用 Bearer token。
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

### 安全边界（M10-03 后更新）

- 已交付：认证基座、三域归属隔离、Web 登录 UI、角色授权+治理审计、
  私有语料与四类草稿归属、试卷 owner 可见性、Web HttpOnly cookie、治理工作台；
- 已知边界：generation/variant 草稿与 744 张历史试卷仍待人工归属分类
  （M10-04 第一切片已交付 `legacy-paper-report` 只读分类报告与
  `legacy-paper-migrate` 安全迁移 CLI，生产迁移待人工决策后显式 `--yes` 执行）；
  审计无防篡改哈希链；TURN 未内置；云 provider/LLM 真实 key 冒烟未执行。
- 部署绑定：所有端口默认 127.0.0.1；LAN/外网需 `AIOS_BIND_IP=0.0.0.0` 且必须同时
  设强 AUTH_SECRET + APP_ENV=production（启动 fail-closed），否则不要对外暴露。


- `AUTH_SECRET` 未配置 = 认证关闭，`GET /api/v1/auth/status` 如实透出 `auth_enabled=false`；
- 配置后（compose 已注入 dev 值）CLI/API 使用 `Authorization: Bearer <token>`，
  浏览器使用 `aios_auth` HttpOnly cookie；`register/login/status/logout` 与
  `/health`、`/api/v1/version`、`/docs` 豁免；
- 端点：`POST /api/v1/auth/register`（重复 409）、`POST /api/v1/auth/login`（失败统一
  「用户名或密码错误」防枚举）、`GET /api/v1/auth/me`；JWT HS256，默认 24h 过期。
- 生产部署用部署 secret 覆盖 `AIOS_AUTH_SECRET`；密码只存 bcrypt 哈希（72 字节上限）。
- HTTPS 反代部署同时设置 `AIOS_AUTH_COOKIE_SECURE=true`；`AIOS_AUTH_COOKIE_SAMESITE=none`
  仅用于跨站部署且会强制 Secure；`CORS_ORIGINS` 拒绝通配符 `*`（credentials 模式下
  `*` 会被反射成任意 Origin+凭据放行，启动即报错）。
- 数据归属（M9-02 资源/考试/语音 + M9-05 四类草稿与私有语料边界）已落地：个人数据严格 owner-scoped；当前真实边界见上方「安全边界」。

## 生产数据治理（M10-03 / M10-04）

- 只读盘点与验收清理：`python -m app.ops.cli data-inventory` / `acceptance-clean`
  （见 M10-03；清理默认 dry-run、精确行 ID 单事务、append-only 审计保留）。
- 历史无归属试卷（`owner_id IS NULL` 非 seed）：`legacy-paper-report` 只读分类
  （缺 DB fail-closed；默认摘要不含生产 paper ID；`--output` 强制 gitignore 的
  artifacts/temp 目录）；`legacy-paper-migrate {keep-public,assign-owner,export-delete}`
  默认 dry-run、`--yes` 才执行，只收精确 paper ID（未知 ID 整体拒绝），事务内
  复核行数/行状态、before/after 同事务写审计；export-delete 先导出校验 JSONL
  才删库，被历史考试引用的卷一律拒绝（人工处理，不级联删考试）。
- 边界：CLI 不提供按标题/来源模糊批量操作；报告/导出文件含生产 ID 只写 gitignore
  目录；生产执行必须运维逐批显式 `--yes`。

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

## 部署模式（M9-07）

**本机模式（默认）**——所有端口只绑 127.0.0.1，数据服务（postgres/redis/minio）永不公开：

```bash
docker compose -f infra/docker-compose.yml --profile local up -d --build
```

无需额外变量（AUTH_SECRET/LiveKit 凭据使用仓库内开发占位值，仅本机使用）。

**局域网模式**——其他设备访问 Web/语音（AIOS_BIND_IP 只影响 api/web/livekit，
postgres/redis/minio 始终固定 127.0.0.1 不公开）。必须同时设置：

```bash
AIOS_BIND_IP=0.0.0.0 AIOS_APP_ENV=production AIOS_AUTH_SECRET='<至少 32 字节随机串>' AIOS_LIVEKIT_API_KEY='<生产 key>' AIOS_LIVEKIT_API_SECRET='<至少 32 字节随机串>' AIOS_CORS_ORIGINS='http://<本机局域网IP或域名>:3000' AIOS_PUBLIC_API_BASE_URL='http://<本机局域网IP或域名>:8000' docker compose -f infra/docker-compose.yml --profile local up -d --build
```

fail-closed 规则：绑定非 loopback 时任一条件缺失（非 production / 弱 secret /
localhost-only CORS / **缺 PUBLIC_LIVEKIT_URL 或其仍是容器内部地址**）API 拒绝启动。
`AIOS_PUBLIC_API_BASE_URL` 是**构建期**注入 Web 的 API 地址（NEXT_PUBLIC_API_BASE_URL），
改值后必须 rebuild Web；`AIOS_CORS_ORIGINS` 必须包含实际 Web origin。

**语音（LiveKit）局域网/公开拓扑（M9-08）**：

- `PUBLIC_LIVEKIT_URL`（API env）：token 返回给浏览器的 ws 地址——局域网必配
  `ws://<LAN_IP>:7880`；本机模式留空，token 回退 `ws://127.0.0.1:7880`；
- 媒体面通告：`AIOS_LIVEKIT_EXTERNAL_IP=<LAN_IP>` → livekit `--node-ip`（局域网必配）；
  公网机器用 `AIOS_LIVEKIT_CONFIG=/etc/livekit/livekit-public.yaml`
  （`use_external_ip: true` 自动探测）；
- 防火墙：放行 TCP 7880(signal)/7881(rtc-tcp) + **UDP 7882-7892(媒体)**；
  对称 NAT/严格防火墙需自建 TURN（coturn），本项目默认未含 TURN 服务；
- 语音连通性验证（真实 livekit.rtc 客户端：token 鉴权 → Room.connect → CONN_CONNECTED
  → 数据通道）：
  `AIOS_MODE=local bash infra/smoke_voice.sh` /
  `AIOS_MODE=public AIOS_PUBLIC_HOST=<LAN_IP> bash infra/smoke_voice.sh`。

## 本地验证

```powershell
npm run typecheck
npm run lint
npm run build
ruff check services/api
# 真实 PG 集成测试：先创建隔离测试库（只查存在 + CREATE，不碰任何既有库），再指向它
Push-Location services/api
.venv\Scripts\python.exe scripts\create_pg_test_db.py
Pop-Location
$env:AIOS_PG_TEST_URL='postgresql+asyncpg://aios:aios@127.0.0.1:5433/ai_learning_os_test'
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
python -m pytest services/api -q
```

> **禁止把 5433/`ai_learning_os`（主/共享库）设为 `AIOS_PG_TEST_URL`**（M10-04 事故：
> 该用法曾让全量 pytest 每轮向主库写入 8 张「PG 验证卷」，6 批共 48 张永久污染，
> 只读分类报告 744/1397 -> 792/1487）。安全门控现在会对主/共享库名自动跳过
> 并给出原因，但请直接使用隔离库 `ai_learning_os_test`，不要依赖跳过兜底。
> 不设 `AIOS_PG_TEST_URL` 时全量 pytest 只跑 SQLite 单元路径，同样全绿。

Docker 生产本地版冒烟（全服务 healthy + 端点 + 上传重启读回）：

```bash
docker compose -f infra/docker-compose.yml --profile local up -d --build
bash infra/smoke_docker.sh
```
