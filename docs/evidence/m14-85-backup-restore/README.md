# M14-85：current-main backup-restore 发布门演练（backup-restore drill）

- 切片：分支 `ops/m14-85-backup-restore-drill`（独立 worktree
  `m14-85-backup-restore-drill`，基于 main
  `f47a1e460db4a6aa06a815488300a2a4af200444`（PR #171 merge，M14-83 合入后
  的 current main，精确基点）），单次本地 commit（不推送、不建 PR）。
- 目标：按 M14-83 证据 README §7 的精确安全计划，使用**仓库既有工具**
  （M6-06 `backup`/`restore` CLI + M11-04 `backup-restore-evidence` 导出器
  + M10-11 `release-readiness`）执行一次真实的生产备份 → 一次性隔离恢复
  演练 → 门证据导出。只动 backup-restore 一个门；不手工拼装/修改任何
  gate JSON、不合成 pass、不伪称 production readiness
  （`release_ready=false` 全程不变，readiness exit 1 如实保留）。
- 生产边界（本切片全程遵守）：生产 DB（`ai_learning_os`）**只被读取**
  （备份反射 SELECT + 只读校验探针），零写入；恢复只落在一次性隔离库
  `ai_learning_os_drill_m14_85`（test_gate 白名单前缀变体，恢复前经
  URL 白名单门控 + `current_database()` 回连 + 空库三重验证，用后即删）；
  零容器启停/重建/删除/重配置、零计划任务、零 CC Switch/代理/FunASR/
  CosyVoice/Ollama/WSL 进程触碰；**未读取 `infra/env.production-recovery`
  或任何 secret 文件**，零 key/token/secret 值回显（S3 凭据取自 compose
  公开默认值，经环境变量注入、值不出现在命令行与输出）；绝不覆盖既有
  m14-75 备份目录（新备份落在 worktree gitignored 新目录）。

## 1. 基点事实（全部可复核）

| 项 | 值 | 来源 |
|----|----|------|
| 基点 commit | `f47a1e460db4a6aa06a815488300a2a4af200444`（PR #171 merge） | git |
| 生产 compose project | `aios-m14-03-production-rehearsal`（七容器） | `docker ps`（只读） |
| 生产 DB | `ai_learning_os` @ 宿主 `127.0.0.1:5433`（compose 公开连接串 `aios:aios`，非 secret，M14-83 §2 同款口径） | compose 第 24–30/100 行 |
| 对象存储 | MinIO `127.0.0.1:9000`（自建 RELEASE.2025-10-15），bucket `aios-objects`，凭据 compose 公开默认 | compose 第 50–72/103–106 行 |
| 演练工具代码 | `services/api/app/ops/backup.py`（M6-06）+ `backup_restore_evidence.py`（M11-04）+ `cli.py` + `app/db/test_gate.py`（M10-04）——全部 current main 既有实现，本切片零代码改动 | git |
| m14-75 先例 | backup-restore 门证据（manifest `6f1c171c…` + verified 回灌 32 行，2026-09-20T23:49Z）——对当前生产已 15+ 小时，本切片按 §7 计划重演练 | `.verify/artifacts/m14-75-production-closure/` |

## 2. 安全命令策略（不读 secret 的执行方式）

1. **执行代码 = current main**：worktree 从零环境（`uv venv .venv --python
   3.12` + `uv pip install -r services/api/requirements.txt -r
   requirements-dev.txt`）。
2. **生产 DB 只读**：连接串用 compose 公开硬编码值经宿主固定 loopback
   `127.0.0.1:5433`（M14-83 §2 同款）；备份 = ORM 反射全表 SELECT，
   另以 `audit-chain-verify`（只读）做演练前后生产状态一致性对照。
3. **对象备份只读**：S3 四参数从 `infra/docker-compose.yml` 程序化提取后
   经环境变量（`S3_ENDPOINT/S3_BUCKET/S3_ACCESS_KEY/S3_SECRET_KEY`）注入
   CLI——`list_keys` + `get` 纯读；**恢复侧不带任何 S3 参数**，全程零
   `put`，生产 bucket 零写入。
4. **配置备份只取公开文件**：`infra/docker-compose.yml` +
   `infra/env.production-recovery.example`（仓库内模板）。真实 secret 文件
   `infra/env.production-recovery` 零接触（未读、未备份、未回显）。
5. **恢复目标一次性隔离**（三重验证后才执行恢复）：
   - `create_pg_test_db.py`（M10-04 sanctioned 脚本）：库名白名单门控、
     维护连接只落 `postgres` 维护库、只 CREATE 绝不 DROP 既有库、建后
     回连验证 `current_database()`；
   - 恢复前显式终验：`evaluate_pg_test_url` 白名单放行（纯解析零连接）+
     `current_database() == ai_learning_os_drill_m14_85` + 目标 public
     schema 为空（全新一次性目标，绝不指向生产库或既有数据）；
   - `backup-restore-evidence` 工具内建同一白名单门控独立复核。
6. **清理**：演练结束 guarded DROP（断言目标库存在且名称精确匹配后才
   `DROP DATABASE … WITH (FORCE)`，M11-05 测试同款范式），drop 后复核
   `pg_database` 恢复演练前集合。

## 3. 演练执行记录（命令与结果，全部真实；UTC 时间）

### 3.1 演练前生产健康基线（只读）

- `docker ps` + `docker inspect StartedAt`（aios project 七容器，17:03:46Z）：
  全部 `Up 13 hours (healthy)`，StartedAt 均为 `2026-09-21T04:29:17–22Z`
  （与 M14-83 观察一致，无重启）——归档
  `raw/pre-drill-production-health.txt`。
- `audit-chain-verify --json`（生产 DB 只读，17:04Z）：exit 0，
  `valid=true / entries=0 / audit_rows=0 / problems=[]`——归档
  `raw/pre-drill-audit-chain-verify.json`。

### 3.2 三件套备份（生产读 + 新隔离目录写）

```bash
export DATABASE_URL="postgresql+asyncpg://aios:aios@127.0.0.1:5433/ai_learning_os"
export S3_ENDPOINT="http://127.0.0.1:9000"
export S3_BUCKET/S3_ACCESS_KEY/S3_SECRET_KEY=<从 infra/docker-compose.yml 程序化提取，值不回显>
cd services/api
<worktree>/.venv/Scripts/python.exe -m app.ops.cli backup \
  --out <worktree>/artifacts/m14-85-backup \
  --config ../../infra/docker-compose.yml \
  --config ../../infra/env.production-recovery.example
# stdout: backup ok: aios-backup-v1 tables: 30 files: 4   （exit 0，17:05:04Z）
```

- 目标目录 `artifacts/m14-85-backup/` 预检确认不存在（全新目录，绝不
  覆盖 m14-75 既有备份）。
- manifest（`aios-backup-v1`）：30 表、总行数 **32**（与 m14-75 演练时
  回灌行数一致——生产数据自那时零变化，与 §3.1/§3.6 只读观察吻合）；
  文件 4 件：`database.json`、对象 1 件
  （`objects/uploads/c2/c20f45a3…`，与 m14-75 备份同款唯一对象）、
  公开配置 2 件。manifest sha256
  `3678f9d5c808d5692a254b95d628f9c79a287f7a6460306095cd1a59459e4b4c`。
- 首次调用曾误带 `--json`（backup 子命令无此参数）被 argparse 拒绝——
  拒绝发生在任何执行前，目标目录未创建，无副作用（如实记录）。

### 3.3 一次性隔离恢复目标（创建 + 三重隔离验证）

```bash
# 预检：目标库名不存在（全新）；现存 ai_learning_os* = 主库 + drill + drill_m14_75（上一切片残留，不触碰）
<py> scripts/create_pg_test_db.py --name ai_learning_os_drill_m14_85
# [1/3] 维护连接 -> …/postgres（维护库，非主库）
# [2/3] CREATE DATABASE ai_learning_os_drill_m14_85（服务器级新建，不触碰既有库）  exit 0
# [3/3] 回连验证 current_database() = ai_learning_os_drill_m14_85

# 恢复前终验（三重，全部通过后才进入恢复）：
# [isolation] evaluate_pg_test_url -> 隔离测试库 ai_learning_os_drill_m14_85（白名单放行，纯解析零连接）
# [isolation] current_database() = ai_learning_os_drill_m14_85（≠ ai_learning_os）
# [isolation] public tables in target BEFORE restore: []（fresh/empty）
```

- 归档 `raw/drill-db-precheck.txt`、`raw/drill-db-create.txt`、
  `raw/drill-target-isolation-proof.txt`。

### 3.4 恢复演练（plain restore + 行数对账）

```bash
# 首次尝试（如实记录）：对未迁移空目标直接 restore -> fail-closed 拒绝
#   app.ops.backup.BackupIntegrityError: 目标库缺表 […30 表…]——先建表/迁移再恢复
#   （异常退出非 0；缺表检查先于任何 delete/insert，目标复核仍 0 表、零触碰；
#     详见 raw/restore-first-attempt-fail-closed-note.txt 事后备注）
# 正确顺序：
export DATABASE_URL="postgresql+asyncpg://aios:aios@127.0.0.1:5433/ai_learning_os_drill_m14_85"
<py> -m alembic upgrade head        # -> 0027_audit_chain，exit 0
unset DATABASE_URL
<py> -m app.ops.cli restore \
  --backup-dir <worktree>/artifacts/m14-85-backup \
  --db-url postgresql+asyncpg://aios:aios@127.0.0.1:5433/ai_learning_os_drill_m14_85
# stdout: restore ok: inserted 32 rows   （exit 0；无 S3 参数——对象零回传，无 --config-target——配置零回写）
```

- **行/manifest 一致性断言**（逐表 COUNT 比对，`raw/row-manifest-consistency.txt`）：
  `inserted_rows(32) == manifest.tables 求和(32)` ✓、`total_rows > 0` ✓、
  **30/30 表逐表计数全等、零 mismatch** → PASS。
- 恢复前 `verify_manifest` 完整性校验先行（文件集全等 + 逐文件 sha256，
  `BackupIntegrityError` fail-closed 语义在首次尝试中已被真实触发验证）。

### 3.5 canonical 门证据导出（M11-04 工具，verified=true）

```bash
<py> -m app.ops.cli backup-restore-evidence \
  --backup-dir <worktree>/artifacts/m14-85-backup \
  --restore-db-url postgresql+asyncpg://aios:aios@127.0.0.1:5433/ai_learning_os_drill_m14_85 \
  --output <worktree>/artifacts/m14-85-evidence/backup-restore.json --json
# exit 0（verified=true；护栏先行：备份目录/目标 URL 白名单/输出位置三重校验；
#        工具内 alembic upgrade head（已 head，幂等）+ run_restore（可重复全量替换）
#        + 只读全量导出与 database.json 逻辑数据全等比对 + inserted_rows 对账）
```

结果（stdout 纯 JSON，`raw/backup-restore-evidence-stdout.json`）：
`verified=true`、`inserted_rows=32`、manifest_sha256
`3678f9d5c808d569…59459e4b4c`（= 备份 manifest.json 文件字节 sha256，
绑定复核通过）、created_at `2026-09-21T17:08:27.873650+00:00`。

### 3.6 一次性目标完整清理 + 备份目录不变性

- guarded DROP：断言 `ai_learning_os_drill_m14_85` 存在 → `DROP DATABASE
  … WITH (FORCE)` → 复核 `pg_database`：剩余
  `['ai_learning_os', 'ai_learning_os_drill', 'ai_learning_os_drill_m14_75']`
  ——**精确等于演练前集合**（后两者为既有残留，本切片未触碰）。
- 备份不变性：manifest.json sha256 与证据内 `manifest_sha256` 一致 +
  `verify_manifest` 重跑 PASS（`raw/cleanup-and-immutable-check.txt`）。

### 3.7 演练后生产健康复核（对照 §3.1 基线）

- `docker ps` + StartedAt（17:09:02Z）：七容器 **ID/镜像/StartedAt 与演练前
  逐项相同**（diff 空）——零重启、零重建，全部 healthy。
- `audit-chain-verify`（只读）：输出与演练前 **byte-identical**
  （`valid=true / entries=0 / audit_rows=0`）——生产 DB 零写入佐证。

### 3.8 release-readiness 聚合（M14-85 canonical 证据目录）

```bash
<py> -m app.ops.cli release-readiness \
  --evidence-dir "D:/AI Learning OS/ai-learning-os/.verify/artifacts/m14-85-backup-restore/evidence" \
  --json --output <worktree>/artifacts/m14-85-evidence/release-readiness.json
# exit 1（release_ready=false，按任务要求如实保留）
```

manifest（generated_at 2026-09-21T17:09:42Z）：

| 门 | required | 状态 |
|----|----------|------|
| **backup-restore** | ✓ | **pass**（verified=true、回灌 32 行、manifest_sha256 绑定；证据文件 sha256 `ed0fc5b4…3fa09b`） |
| ci-main / release-check | ✓ | missing（代码绑定门，对 `f47a1e4` 未重推导——独立 evidence-refresh 切片 scope；M14-81 canonical（@ e1f128b）为最新真实记录，对现 main 已 stale 如实） |
| production-preflight / legacy-papers / draft-ownership | ✓ | missing（生产状态门，M14-83 canonical（@ 5829ad9 生产时点）为最新真实记录；本切片 scope 只 backup-restore，不搬运旧文件冒充 current） |
| audit-chain-anchor | ✓ | missing（完整 chain/anchor/worm 三块形态需运维窗口，verify-only 佐证在 M14-83 raw/） |
| provider-smoke | ✓ | missing（M14-83 记录 search/llm fail——需运维窗口恢复 SearXNG 出站与 Ollama 后重跑） |
| long-soak | ✓ | missing（24h 审计未发生；M14-79 权威 soak 锚最早审计时点 2026-09-22T15:00:01Z 届满后由 soak_stability_audit 真实判定） |
| release-approval | ✓ | missing（human-only，从未发生，不代拟） |
| turn-tls | optional | missing（公网语音发布形态才必需） |

**pass=1 / blocked=0 / required missing=9 / optional missing=1；
malformed=0、tampered=0；`release_ready=false`、exit_code=1**——本切片
不授权任何部署。

## 4. 聚焦验证与静态检查（真实执行结果）

- 聚焦契约测试（本切片证据链所依赖的全部工具面，worktree venv）：

```bash
cd services/api && <worktree>/.venv/Scripts/python.exe -m pytest -q \
  tests/test_backup_drill.py tests/test_backup_restore_evidence.py \
  tests/test_release_readiness.py
# 108 passed, 3 skipped in 6.04s
```

（3 skipped = 真实 PG 端到端门控项——未设 `AIOS_PG_TEST_URL` 时按设计
skip；真实 PG 全链路已由本切片 §3.4/§3.5 在一次性隔离库上真跑。）

- canonical 两 JSON `json.load` 解析通过；`release-readiness` 消费即契约
  校验（malformed=0 / tampered=0）；gate.data.manifest_sha256 与证据文件
  值交叉一致断言通过。
- 备份目录全部文件 sha256 清单 + raw 日志哈希清单归档
  `raw/inventory-hashes.txt`（README §5 哈希与之逐一对应）。
- 本切片零 Python 源码改动（docs-only），`git diff --check` 干净。
- 分支卫生：单 commit 后 tracked-clean（worktree 侧 `artifacts/` 生成现场
  gitignored 不入库）；未 push、未建 PR。

## 5. canonical 证据清单（主仓 gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：`.verify/artifacts/m14-85-backup-restore/`（2 文件）

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/backup-restore.json` | 346 | `ed0fc5b4df983a05e880564bed5796843f9aea3808d5d2900db01c533e3fa09b` |
| `evidence/release-readiness.json` | 6769 | `84a2da2e84d0d9b68875ce3b43ee2f513a3d589106029310bca1ec8f0facb9ff` |

worktree 侧生成现场（gitignored，含全部原始日志与哈希清单）：

| 目录 | 内容 |
|------|------|
| `artifacts/m14-85-backup/` | 备份三件套 + manifest（database.json `0bc69ca5…d61c3b`、manifest.json `3678f9d5…59459e4b4c`、对象 `c20f45a3…3a43acb`、configs 两件 `62b39b26…` / `dd297c3e…`；完整哈希见 `raw/inventory-hashes.txt`） |
| `artifacts/m14-85-evidence/` | 工具原始输出（与 canonical 逐字节一致，sha256 复核） |
| `artifacts/m14-85-raw/` | 演练前后健康快照 ×2、audit-chain-verify ×2、隔离证明 ×3、restore/evidence stdout、行数对账、清理与不变性检查、首次 fail-closed 拒绝事后备注、哈希清单（17 文件） |

## 6. 诚实边界与剩余阻塞（不伪称，逐项可执行收口）

1. **本证据只覆盖演练时点的备份目录**（M11-04 契约原文语义）：verified
   =「该 manifest 可恢复且逻辑数据全等」的点时点证明，不代表持续可
   恢复；生产后续变更不自动覆盖。
2. **恢复演练只回灌 DB**：`backup-restore-evidence` 契约即 DB-only
   （alembic + run_restore + dump 对账）；对象/配置的完整性由
   `verify_manifest` 哈希校验覆盖（首次 restore 尝试已真实触发该校验
   面），但「对象回传到隔离 bucket + 配置回写到隔离目录」的端到端
   恢复未在本切片演练（M11-05 测试亦同口径）。
3. **其余九个 required 门在 M14-85 证据目录仍 missing**（§3.8 表）：
   M14-83 canonical 的生产状态门（preflight/治理两门/provider-smoke
   blocked）与 M14-81 canonical 的代码绑定门仍是对各自生产/代码时点的
   最新真实记录，对 current main 已 stale——需独立 evidence-refresh
   切片真实重推导，本切片不搬运冒充。long-soak 待 2026-09-22T15:00:01Z
   后真实判定。
4. **生产 DB/容器零触碰已证**（§3.1/§3.7 对照），但生产 PG 服务器上
   存在历史遗留隔离库 `ai_learning_os_drill` 与 `ai_learning_os_drill_m14_75`
   （本切片之前已存在，未触碰、未清理——是否清理由 supervisor 决策）。
5. **备份产物位置**：原始备份三件套在 worktree gitignored 目录（worktree
   移除后即消失）；canonical 只保存工具生成的门证据（任务边界要求）。
   长期保留备份需运维将其另行归档（超出本切片边界）。
6. **worktree 首次 backup 调用误带 `--json` 被 argparse 拒绝**、**首次
   plain restore 对未迁移目标被 fail-closed 拒绝**——两次失败均如实
   记录（§3.2/§3.4），未伪装成功；后者恰为完整性护栏的真实验证。
7. `release_ready=false`、`production_ready=false` 不变；生产仍运行
   m14-70 镜像（2026-09-19 切换后未再变更）；本切片不授权任何部署。
