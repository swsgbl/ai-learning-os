# M11-08 release-check 本机实跑证据（无 DB URL vs 显式安全 DB）

采集方式：Codex 在本机用 repo venv 从 `services/api` 真实执行两次 `release-check --local-only`——Attempt 1 不带任何 DB URL，Attempt 2 显式指向一次性 gitignored SQLite 安全库；两轮执行前均清除 `DATABASE_URL` 与 `AIOS_PG_TEST_URL`。两次 run 各自落盘 `--json --output` 机器可读证据（gitignored `artifacts/m11-08/`），本文档记录命令口径、退出码、结果矩阵、产物 size/SHA256 与边界。

实跑日期：2026-09-05；分支：`feature/m11-08-release-check-local-evidence`（基于 main `062bb519a26565693b96f539dc2fd6003392026a`，即 PR #27 合并后 main）。

**结论：无 DB URL 时 local-only 本地七项只能 5/7（migration/backup 必然 fail，退出码 1）；显式安全本地 DB 后本地七项 7/7 全过、退出码 0——但两轮 `all_green` 均为 `false`（voice/license/e2e 三项 live 检查按 local-only 如实 not_executed）。local-only 是过程证据，不能替代 full release-check，更不等于 production readiness；`production_ready=false` 保持不变。**

## Attempt 1：无 DB URL（预期失败取证）

环境前置：本轮执行前清除 `DATABASE_URL` 与 `AIOS_PG_TEST_URL`（两者均未设置）。

工作目录 `services/api`，repo venv：

```
..\..\.venv\Scripts\python.exe -m app.ops.cli release-check --local-only --json --output D:\AI Learning OS\ai-learning-os\artifacts\m11-08\release-check-local.json
```

结果：**退出码 1**。证据文件 `artifacts/m11-08/release-check-local.json`（gitignored）：

| 项 | 值 |
| --- | --- |
| size | 2829 bytes |
| SHA256 | CBDB232E8B123795D8498699F1FFAD48D68984BA2A13743A24C00A018613C478 |
| generated_at | 2026-09-05T09:25:00.092553+00:00 |
| execution_scope | local-only |
| total / passed | 10 / 5 |
| failed_ids | migration、backup |
| not_executed_ids | voice、license、e2e |
| all_green | false |

失败原因（如实记录，不是环境噪音）：

- `migration`：数据库 URL 未配置（alembic `env.py` 的 `_database_url` 抛 `RuntimeError: 数据库 URL 未配置：请设置 DATABASE_URL 或通过程序化调用传入 sqlalchemy.url`）。
- `backup`：缺少 `--db-url` 或 `DATABASE_URL`。

五个纯本地检查项均 pass：api-lint、web-lint、web-typecheck、web-build、api-test（**1288 passed / 82 skipped**，177.50s）。

## 安全本地 DB 前置验证（两次 attempt 之间）

为让 Attempt 2 的 `--db-url` 指向「可丢弃、可验证」的目标，先用一次性 gitignored SQLite 做前置验证：

| 项 | 值 |
| --- | --- |
| 一次性库路径 | `artifacts/m11-08/release-check.sqlite`（gitignored，仅本次门禁用，不入 git） |
| alembic upgrade head | current == head == **0027_audit_chain** |
| backup smoke 输出 | `backup ok: aios-backup-v1 tables: 30 files: 1`（三件套落 `artifacts/m11-08/backup-smoke/`：`database.json` + `manifest.json` + `configs/`） |

该 SQLite 是本机临时安全库，不是任何生产/共享数据库；`--db-url` 始终未指向生产。

## Attempt 2：显式安全 DB（本地七项全过）

环境前置：与 Attempt 1 相同——执行前清除 `DATABASE_URL` 与 `AIOS_PG_TEST_URL`，DB 仅通过显式 `--db-url` 传入。

工作目录 `services/api`，repo venv：

```
..\..\.venv\Scripts\python.exe -m app.ops.cli release-check --local-only --db-url sqlite+aiosqlite:///../../artifacts/m11-08/release-check.sqlite --json --output D:\AI Learning OS\ai-learning-os\artifacts\m11-08\release-check-local-db.json
```

（`sqlite+aiosqlite:///../../artifacts/...` 为相对 `services/api` 的路径，落到仓库 `artifacts/m11-08/release-check.sqlite`。）

结果：**退出码 0**。证据文件 `artifacts/m11-08/release-check-local-db.json`（gitignored）：

| 项 | 值 |
| --- | --- |
| size | 2734 bytes |
| SHA256 | 66C1226E5698BE443D0F335488FF8709911E69EEE46A0ADFE21A5D34A538F896 |
| generated_at | 2026-09-05T09:29:34.459451+00:00 |
| execution_scope | local-only |
| total / passed | 10 / 7 |
| failed_ids | （空） |
| not_executed_ids | voice、license、e2e |
| all_green | **false** |

本地七项全部 pass；退出码 0 只表示「已执行的本地门禁全过」，不表示全绿。

## 结果矩阵（10 项 × 两轮）

| 检查 | kind | Attempt 1（无 DB URL） | Attempt 2（显式安全 DB） |
| --- | --- | --- | --- |
| api-lint | command | pass | pass |
| web-lint | command | pass | pass |
| web-typecheck | command | pass | pass |
| web-build | command | pass | pass |
| api-test | command | pass（1288 passed / 82 skipped，177.50s） | pass（**1288 passed / 82 skipped**，185.32s） |
| migration | command | **fail**（数据库 URL 未配置） | pass（current == head == 0027_audit_chain） |
| backup | command | **fail**（缺少 --db-url 或 DATABASE_URL） | pass（aios-backup-v1，30 tables，1 file） |
| voice | live | not_executed | not_executed |
| license | live | not_executed | not_executed |
| e2e | live | not_executed | not_executed |
| 合计 | — | 10 total / 5 passed / 2 failed / 3 not_executed，exit 1 | 10 total / 7 passed / 0 failed / 3 not_executed，exit 0 |
| all_green | — | false | **false** |

voice/license/e2e 是 live 检查项，`--local-only` 下如实进 `not_executed_ids`（status=not_executed，detail 注明「未执行，不构成完整门禁证据」）——不冒充 pass。

## 操作结论

1. **无 DB URL 时，local-only 不可能 7/7**：migration 与 backup 两项门禁硬依赖数据库 URL（环境变量或 `--db-url` 二选一），缺省即双双 fail、退出码 1——这是门禁语义的正确行为，不是误报。
2. **显式安全 DB 后，本地七项 7/7**：`--db-url` 指向一次性 gitignored SQLite 即可让 migration（current==head==0027_audit_chain）与 backup（aios-backup-v1，30 tables，1 file）转 pass，本地检查面全过、退出码 0。
3. **两轮 `all_green` 均 false**：live 三项（voice/license/e2e）始终 not_executed；`all_green=true` 只属于 full 模式 10 项全 pass。

## 边界（未做事项，勿视为已完成）

- **本切片是 local-only 过程证据**：live 三项（voice/license/e2e）未执行、`all_green=false`——**不能替代 full release-check**（full 未执行不得记 pass），**更不等于 production readiness**；`production_ready=false` 保持不变。
- 未连接、未写入任何生产 DB（Attempt 2 的 `--db-url` 仅指向本机一次性 gitignored SQLite）。
- 未部署、未打 git tag、未发 GitHub Release、未推镜像 registry。
- 未读取、未输出任何密钥。
- 两份 JSON 证据与一次性 SQLite、backup smoke 产物均留在 gitignored `artifacts/m11-08/` 本地，**不入 git**。
