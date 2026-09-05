# M11-09 隔离本地环境 full release-check 实跑证据

采集方式：Codex 在本机隔离环境先准备一次性 gitignored SQLite，再用 repo venv 起一个绑定 `127.0.0.1:8017` 的临时 API（development / auth-off / voice local），随后从 `services/api` 用 repo venv 真实执行 **full 模式** `release-check`（含 voice/license/e2e 三项 live 检查），以 `--json --output` 落盘机器可读证据（gitignored `artifacts/m11-09/`）。本文档记录前置过程（含一次如实失败的 alembic 尝试）、临时 API 口径、命令、退出码、结果矩阵、产物 size/SHA256、临时 API 关停与边界。

实跑日期：2026-09-05；分支：`feature/m11-09-release-check-full-local-evidence`（基于本地 `e545083d826dc1f009ce95a43b6ae6110cbe50f2` 创建——该提交 tree `5ec64753f1756178367ffba38116cc8d8dc1151a` 与远端 main `5f07592789eef31bbe71707d0a32efa28288c49b` 的 tree 完全一致）。

**结论：隔离本地环境下 full release-check 10/10 全 pass、`execution_scope=full`、`all_green=true`、退出码 0——live 三项（voice/license/e2e）本次真实执行且通过。但这是「隔离本地环境的 full 实跑」过程证据，不是生产环境发布门禁：运行面是 development / auth-off（AUTH_SECRET 清除）/ 一次性 SQLite / VOICE_MODE=local / 127.0.0.1 回环临时 API，不得把它当作 production readiness 或生产发布授权；`production_ready=false` 保持不变。**

## 前置：一次性安全本地 DB（含首次失败如实记录）

为让 migration/backup 两项门禁与临时 API 共用「可丢弃、可验证」的数据库，先建一次性 gitignored SQLite：

| 项 | 值 |
| --- | --- |
| 一次性库路径 | `artifacts/m11-09/release-check-full.sqlite`（gitignored，仅本次门禁用，不入 git） |
| 第一次 alembic upgrade head | **失败**——父目录 `artifacts/m11-09/` 不存在，alembic/SQLite 无法打开数据库文件；失败发生在打开数据库阶段，**未产生半成品库**（无部分迁移的库残留） |
| 修复 | 创建 `artifacts/m11-09/` 目录后重跑 `alembic upgrade head` |
| 第二次 alembic upgrade head | **成功**：current == head == **0027_audit_chain** |

该 SQLite 是本机临时安全库，不是任何生产/共享数据库；`DATABASE_URL` 与 `--db-url` 始终未指向生产。

## 临时 API：本机回环 uvicorn（development / auth-off / SQLite / voice local）

启动方式：repo venv 运行 `uvicorn app.main:app`，绑定 `http://127.0.0.1:8017`（本机回环，不对外）。

环境变量口径（如实记录，非生产面）：

| 环境变量 | 值 | 说明 |
| --- | --- | --- |
| `APP_ENV` | `development` | 开发环境配置面 |
| `VOICE_MODE` | `local` | 本地语音（非云语音） |
| `HOST_BIND_IP` | `127.0.0.1` | 仅回环绑定 |
| `AUTH_SECRET` | （清除） | 本地 auth-off 如实状态，不是生产鉴权面 |
| `DATABASE_URL` | 指向一次性 SQLite | `artifacts/m11-09/release-check-full.sqlite` |
| `AIOS_PG_TEST_URL` | （清除） | 不连接任何 PG |

启动后验证：`/health` 返回 status=ok；`/api/v1/voice/providers` 显示 voice_mode=local、ASR=fake、TTS=tone——与 `VOICE_MODE=local` 的本地语音口径一致。

## 门禁命令与总体结果

工作目录 `services/api`，repo venv：

```
..\..\.venv\Scripts\python.exe -m app.ops.cli release-check --api-base http://127.0.0.1:8017 --db-url sqlite+aiosqlite:///../../artifacts/m11-09/release-check-full.sqlite --json --output D:\AI Learning OS\ai-learning-os\artifacts\m11-09\release-check-full-local.json
```

（`sqlite+aiosqlite:///../../artifacts/...` 为相对 `services/api` 的路径，落到仓库 `artifacts/m11-09/release-check-full.sqlite`。）

结果：**退出码 0**。证据文件 `artifacts/m11-09/release-check-full-local.json`（gitignored）：

| 项 | 值 |
| --- | --- |
| size | 2564 bytes |
| SHA256 | 84BAE45C25B7AD5C27565F84AE0B4424C3ADE5F6F9461FE439281E5B618B76D3 |
| generated_at | 2026-09-05T09:52:03.389634+00:00 |
| execution_scope | full |
| total / passed | 10 / 10 |
| failed_ids | （空） |
| not_executed_ids | （空） |
| all_green | **true** |

SHA256 与 size 已在本机对该 JSON 原文件重算核对一致（2564 bytes、`84BAE45C…B76D3`）。

## 结果矩阵（10 项全 pass）

| 检查 | kind | 结果 | detail |
| --- | --- | --- | --- |
| api-lint | command | pass | All checks passed |
| web-lint | command | pass | — |
| web-typecheck | command | pass | — |
| web-build | command | pass | — |
| api-test | command | pass | **1288 passed / 82 skipped** in 183.90s |
| migration | command | pass | current == head == 0027_audit_chain |
| backup | command | pass | aios-backup-v1，30 tables，1 file |
| voice | live | pass | voice_mode=local，synthesize audio/wav（17324 bytes） |
| license | live | pass | api deps 15、web ok、models 7、sources 6 |
| e2e | live | pass | walkthrough 5 steps，1347 ms |
| 合计 | — | 10 total / 10 passed / 0 failed / 0 not_executed，exit 0 | — |
| all_green | — | **true** | — |

与 M11-08 的 local-only 实跑对照：live 三项（voice/license/e2e）在 local-only 下如实 not_executed、`all_green=false`；本次 full 模式下三项真实执行并通过（voice 走本地合成、license 四面清点、e2e 走临时 API 回放 walkthrough）。

## 临时 API 关停

门禁结束后，Codex 对临时 API 进程正常 Ctrl+C 关闭，uvicorn 日志显示 shutdown complete；随后检查确认 8017 端口无监听——不留常驻进程、不留占用端口。

## 操作结论

1. **full 门禁链路在隔离本地环境可端到端真实跑通**：一次性 SQLite 前置（alembic upgrade head 至 0027_audit_chain）+ 回环临时 API（/health ok、voice providers 如实显示 local）就绪后，10 项门禁全 pass、退出码 0、`all_green=true`。
2. **前置首次 alembic 失败是可复现的环境事实**：父目录不存在导致 SQLite 无法打开数据库（未产生半成品库），创建目录后重跑即成功——如实记录为过程证据，不影响门禁结果本身。
3. **`all_green=true` 的含义限定在本次运行面**：它证明「这套隔离本地运行面上 10 项门禁全部真实通过」，不外推到生产运行面（见下节边界）。

## 边界（未做事项，勿视为已完成）

- **本切片是「隔离本地环境的 full release-check 实跑」，不是生产环境发布门禁**：虽然 `execution_scope=full` 且 `all_green=true`，但运行面是 development 配置、auth-off（AUTH_SECRET 清除）、一次性 gitignored SQLite、VOICE_MODE=local（ASR=fake / TTS=tone，非真实云语音）、127.0.0.1 回环临时 API——**不得把它当作 production readiness 或生产发布授权**；`production_ready=false` 保持不变。
- 未连接、未写入任何生产 DB（`DATABASE_URL` / `--db-url` 仅指向本机一次性 gitignored SQLite）。
- 未部署、未打 git tag、未发 GitHub Release、未推镜像 registry。
- 未读取、未输出任何密钥；不输出任何业务 ID。
- JSON 证据与一次性 SQLite 留在 gitignored `artifacts/m11-09/` 本地，**不入 git**。
