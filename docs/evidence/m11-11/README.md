# M11-11 生产只读基线 + 备份完整性验证实跑证据

采集方式：Claude Code 在本机（Codex 监督）对**当前正在运行的生产-like 本地栈**（六个容器 `ai-learning-os-web-1` / `ai-learning-os-api-1` / `ai-learning-os-livekit-1` / `ai-learning-os-postgres-1` / `ai-learning-os-redis-1` / `ai-learning-os-minio-1`，全程保持运行且健康、未受打扰；本切片直接使用其中 web/api/postgres/minio，livekit/redis 保持运行但未被直接操作）执行三件只读/备份操作：① 既有 `production-preflight`（pre-migration，只读）；② 既有 `backup`（读生产-like DB + 对象存储，只写本地 gitignored 目录）+ 本地完整性校验；③ 免凭据只读 API 基线（`/health`、`/api/v1/version`）。生产 DB/S3 配置由临时环境注入 runner 从运行中 API 容器环境程序化读取（仅存内存），只翻译容器内服务地址的 host/port 为本机回环，库/bucket/凭据原样保留且从不显示。全部产物落在 gitignored `artifacts/m11-11/` 本地，不入 git。

实跑日期：2026-09-05；分支：`feature/m11-11-production-readonly-baseline`（基于 main `9b24880d8e9087748bb86de27bf1b22fd8200946`）；执行：repo venv，工作目录 `services/api`。

**结论：preflight exit 0（pass=1 / pending=4 / fail=0，全部 pending 如实保留为 pending、未包装成 pass）；备份成功（aios-backup-v1，28 表 / 6 文件）且本地完整性校验 6/6 SHA256 全部匹配；API 基线两个免凭据端点均 200。这是「只读基线 + 备份验证」的过程证据，不是 production readiness：`production_ready=false` 保持不变，本切片未执行任何迁移/治理/锚定/恢复/部署/启停。**

## 环境注入方式（密钥零暴露）

| 项 | 做法 |
| --- | --- |
| 配置来源 | 运行中 API 容器的环境（`docker inspect` 读取，stdout 由 runner 进程内存捕获——不打印、不落盘） |
| 注入方式 | 临时 Python runner（gitignored `temp/`，用后即删）构造子进程环境：先剥离继承环境中的 `DATABASE_URL`/`S3_*` 同名变量（保证来源唯一），再注入容器值 |
| 地址翻译 | 仅替换 URL 的 host/port 为本机回环（PG 服务地址 → `127.0.0.1:5433`，MinIO 服务地址 → `127.0.0.1:9000`）；userinfo/path/query 原样保留（库/bucket/凭据不动） |
| 命令行安全 | 任何密钥都不出现在可见命令行上：CLI 不带 `--db-url`/S3 参数，全部经环境注入；runner 只输出变量名与「是否做了回环翻译」布尔，不输出值 |
| 产物安全 | 备份内容/对象键名/业务 ID 不进入任何 git 文档或终端输出；runner 不落盘环境值 |

## 1. 生产只读 preflight（pre-migration）

命令（经环境注入，`DATABASE_URL` 注入、无可见 `--db-url`）：

```
python -m app.ops.cli production-preflight --phase pre-migration --json --output <artifacts>/m11-11/production-readonly-01/production-preflight.json
```

结果：**exit 0**（无 fail）。检查矩阵（五项，pending 全部如实保留）：

| 检查 | 状态 | 安全摘要 |
| --- | --- | --- |
| db-connect | pass | postgresql 连通正常 |
| alembic | **pending** | current=0026_paper_owner -> head=0027_audit_chain，待切换窗口人工执行迁移（本切片绝不 upgrade） |
| audit-chain | **pending** | pending_migration：链表缺失是 0027 未执行的迁移前预期形态（建链后须 verify valid） |
| legacy-governance | **pending** | 798 项待人工归属决策（无归属非 seed 卷 792 + generation 草稿 3 + variant 草稿 3；只输出聚合计数，无任何 ID） |
| audit-anchor | **pending** | expected_pending：锚定在 0027 迁移后执行（本切片不写锚、不动 WORM） |

汇总：pass=1 / pending=4 / fail=0 / not_configured=0。证据文件（gitignored）：

| 项 | 值 |
| --- | --- |
| 路径 | `artifacts/m11-11/production-readonly-01/production-preflight.json` |
| generated_at | 2026-09-05T15:01:05.033130+00:00 |
| size / SHA256 | 2354 bytes / `e03f1daca4e48176133c75252f0407f0750fb1e420bb154980cdfdb480016044`（本机对原文件重算一致） |

## 2. 生产备份 + 本地完整性校验

命令（经同一环境注入；读生产-like DB 与对象存储，只写本地 gitignored 备份目录）：

```
python -m app.ops.cli backup --out <artifacts>/m11-11/backup-01/
```

结果：**exit 0**，`backup ok: aios-backup-v1 tables: 28 files: 6`，created_at 2026-09-05T15:01:22.716183+00:00。未调用 restore（本切片无任何恢复动作）。

本地完整性校验（独立校验脚本，只输出安全聚合，不打印对象键名/文件名清单/行内容/业务 ID）：

| 校验项 | 结果 |
| --- | --- |
| manifest.json 可解析 | ✅ |
| manifest 引用文件全部存在 | ✅（6/6） |
| 每个路径都包含在备份目录内（无逃逸） | ✅ |
| 逐文件重算 SHA256 与 manifest 比对 | ✅ 6/6 全匹配 |
| 磁盘文件集 == manifest 集 + manifest.json | ✅（7 个文件 = 6 payload + manifest.json，无多余/缺失） |
| manifest 引用文件总字节 | 6,050,071 bytes（含 manifest.json 共 6,051,782 bytes） |
| 表聚合计数 | 28 表 / 共 12,966 行（逐表行数见 summary.json；仅 schema 级聚合） |
| manifest.json SHA256 | `fff6a4ea66c21fb5a42531ba1db4ca0b989a9f8507bb4d1fc3a8b096330c7eee` |

备份目录 `artifacts/m11-11/backup-01/`（gitignored，本地保留，不入 git）。

## 3. 只读 API 基线（免凭据端点）

仅未认证只读 GET，未做登录、未发任何状态变更请求：

| 端点 | 方法 | HTTP 状态 | 安全响应摘要 |
| --- | --- | --- | --- |
| `/health` | GET | **200** | `{"status":"ok","service":"ai-learning-os-api"}` |
| `/api/v1/version` | GET | **200** | version=0.1.0；git_commit/alembic_current/alembic_head 如实 `not_available`（该端点设计上不查 git/alembic） |

## 4. Codex 独立复核（2026-09-05）

Codex 对本切片证据的独立复核（与执行者自查相互独立）：

| 复核项 | 结果 |
| --- | --- |
| 备份/preflight 产物哈希与文件集独立校验 | **VERIFY OK** |
| 严格业务 ID 扫描（git 文档与输出） | **0 hits** |
| `python -m json.tool docs/evidence/m11-11/summary.json` | OK |
| 聚焦 pytest（同三测试文件） | **68 passed, 4 skipped in 14.58s** |
| `python -m ruff check services/api` | All checks passed |
| `git diff --check` | clean |
| `/health` 与 `/api/v1/version` 独立复测 | 均 **200** |

复核纠正一处准确性问题：容器证据由「四个容器」更正为**六个容器 web/api/livekit/postgres/redis/minio 全程保持运行且健康**（本切片直接使用 web/api/postgres/minio），与运行中本地栈实测一致。

## 边界（未做事项，勿视为已完成）

- **本切片是「生产-like 只读基线 + 备份与本地完整性验证」，不是 production readiness**：`production_ready=false` 保持不变；preflight 的 pending 状态是「生产仍需人工决策/执行」的如实提示，**未转换成 pass**，也不因备份成功而消解。
- **未执行任何生产业务数据写入**；**未执行任何生产迁移**（尤其 `alembic upgrade`——alembic 仍停在 0026_paper_owner，迁移留给切换窗口人工执行）。
- **未做治理清理/归属迁移**（798 项待决策计数原样保留）；**未做审计链锚定或 WORM 处理**（不写锚文件）。
- **未执行 restore**（备份只写本地 gitignored 目录，未恢复进任何生产或生产-like 源库）。
- **未部署、未启停/重启任何服务或容器**（六个容器 web/api/livekit/postgres/redis/minio 全程保持健康运行；本切片直接使用 web/api/postgres/minio）；**未打 git tag、未发 GitHub Release、未推镜像 registry、未创建 PR、未推送分支**。
- **生产连接值（DB URL/凭据/S3 配置）、备份内容与业务 ID 有意缺席**：不进 git、不进本文档、不进终端输出。
