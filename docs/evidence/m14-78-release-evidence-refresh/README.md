# M14-78：current-main release 证据刷新（实现/文档切片）

- 切片：分支 `ops/m14-78-release-evidence-refresh`（独立 worktree
  `m14-78-release-evidence-refresh`，基于 main `b7db88c3401a6f0ce821a9777fb8b815bd4cf837`
  （PR #164 merge，精确基点，非 origin/main 快照推断）），单次本地 commit（不推送、不建 PR）。
- 目标：对 m14-75 生产收口证据做**逐文件 staleness 审计**，并只用仓库既有工具/契约
  （`gh api` 真实远端数据 + `app.ops.cli release-check-isolated` +
  `app.ops.cli release-readiness`）刷新**当前可真实推导**的证据；
  绝不手改 gate JSON、绝不合成结果、绝不伪称 production readiness
  （`release_ready=false` / `production_ready=false` 全程不变）。
- 零生产触碰（本切片全程）：零容器启停/重建、零计划任务操作、零 wsl.exe、
  零语音进程接触、零 secrets/env 读取、零 soak 历史读写、零发布审批接触、零部署。
  唯一网络访问是经 `HTTPS_PROXY=socks5h://127.0.0.1:10808` 的 GitHub Actions
  只读 API 查询（`gh api`，已认证账号 swsgbl）。

## 1. 基线事实与时间线（全部 git/远端可复核）

| 事件 | commit | 时间（+08 本地） |
|------|--------|------|
| PR #162 merge（m14-75 收口的代码基点） | `2a0e911b110139a50966bac538be0017593be0c9` | 2026-09-21 07:45:38 |
| m14-75 收口证据生成窗口（UTC 23:49:50–23:56:02） | — | 2026-09-21 07:49–07:56 |
| PR #163 merge（m14-76 纯文档回填） | `4df2333bce794f624cf96c513c54d0a5dc107771` | 2026-09-21 08:24:55 |
| PR #164 merge（m14-77 watchdog 工具+测试）→ **当前 main** | `b7db88c3401a6f0ce821a9777fb8b815bd4cf837` | 2026-09-21 08:39:34 |
| main CI run 35548392898 创建（push 事件） | b7db88c | 2026-09-21 08:39:37（UTC 00:39:37） |

- `git diff --stat 2a0e911..b7db88c`：9 文件——docs×5（CHANGELOG/PROJECT_STATUS/
  ROADMAP/两份 evidence README）+ `tools/voice/`×3（watchdog 工具/VBS/README）+
  `services/api/tests/test_voice_sidecar_watchdog_task.py`。
  **运行面代码零改动**，但收口证据中「代码绑定门」的结论只对其执行时点的树成立，
  不重新真实执行就不对 b7db88c 成立——这正是本切片要如实刷新的边界。

## 2. m14-75 收口证据 staleness 审计（源目录
`.verify/artifacts/m14-75-production-closure/evidence/`，14 文件）

哈希为本次审计实测（与 m14-75 `release-readiness.json` 内嵌 sha256 逐一对上，
无篡改）；时点取 JSON 内部 `generated_at`（UTC）。

| 文件 | sha256（前 16） | 证据时点（UTC） | 绑定对象 | 对 b7db88c | 本切片处置 |
|------|------|------|------|------|------|
| `ci-main.json` | `36c7c6e363163398` | 2026-09-20 23:50 前后 | 代码（merge_commit=`2a0e911`，run 35545589956） | **stale**（main 已前移 5 commits） | **已刷新 → pass**（run 35548392898 @ b7db88c，§3.1） |
| `release-check.json` | `6a7284221738ee88` | 2026-09-20 23:53:59 | 代码（2a0e911 时代树，隔离 full 10 门） | **stale**（同上） | **已刷新 → pass**（b7db88c 隔离 full 重跑 10/10，§3.2） |
| `production-preflight.json` | `cac728b0d628c3e4` | 2026-09-20 23:54:22 | 生产 DB 状态（post-migration 5 检） | 生产未再切换，快照仍是最新生产状态记录；但**不证明** b7db88c 发布形态 | **blocked**（刷新需生产 DB 访问，本切片禁止） |
| `backup-restore.json` | `c84cd11d88e2e160` | 2026-09-20 23:49:50 | 备份演练（aios-backup-v1 + 回灌 32 行） | 同上（数据状态快照） | **blocked**（需下次运维窗口重演练） |
| `audit-chain-anchor.json` | `26f56212dfaa86c0` | 2026-09-20 23:55 前后 | 生产审计链/锚定/WORM（verify-only） | 同上 | **blocked**（需生产 DB/MinIO 访问） |
| `audit-anchor.jsonl` | `d2bfd877aa94632e` | 2026-09-17 02:28:50 | 锚文件副本（1 锚点，sequence 0） | 同上 | **blocked**（同上） |
| `audit-chain-verify.json` | `76984a12b14c8f46` | 2026-09-20 23:55 前后 | 生产审计链只读校验 | 同上 | **blocked**（同上） |
| `legacy-papers.json` | `6abbffbc3bd1bdc7` | 2026-09-20 23:51:03 | 生产 DB 只读治理报告 | 同上 | **blocked**（需生产 DB 访问） |
| `draft-ownership.json` | `a7808b809ccad148` | 2026-09-20 23:51:03 | 生产 DB 只读治理报告 | 同上 | **blocked**（同上） |
| `provider-smoke.json` | `c87a60fbe4b33df3` | 2026-09-20 17:02:46 | 生产拓扑 live 冒烟聚合（local） | 生产 provider 面未变更，快照仍最新；但不随 main 前移自动成立 | **blocked**（重跑涉生产语音/live 端点，本切片禁止） |
| `local-voice-smoke.json` | `c0c107d06628ec2e` | 2026-09-20 17:01:28 | live 冒烟明细 | 同上 | **blocked**（同上） |
| `search-smoke.json` | `69e9ff4c4516d25a` | 2026-09-20 17:02:13 | live 冒烟明细 | 同上 | **blocked**（同上） |
| `llm-smoke.json` | `e34122254cb6d6b9` | 2026-09-20 17:02:27 | live 冒烟明细 | 同上 | **blocked**（同上） |
| `release-readiness.json` | `9fd557d11016b13d` | 2026-09-20 23:56:02 | 收口时点聚合 manifest（8 pass/3 missing） | 历史收口记录，被 M14-78 聚合在 current-main 决策上取代 | 保留为收口存档，不改写 |

分类口径：**代码绑定门**（ci-main/release-check）随 main 前移即 stale，可离线/远端
真实重推导；**生产状态绑定门**（preflight/backup/audit-chain×3/governance×2/
smoke×4）锚定的是生产系统状态而非 git commit——生产自 m14-70 切换后未再变更，
快照仍是各自维度的最新真实记录，但重新证明需要生产/DB/语音访问（本切片与后续
文档切片均禁止），如实标 blocked 而非假装仍然 pass。

## 3. 刷新执行记录（命令与结果，全部真实可复跑）

### 3.1 ci-main（真实远端 CI，非本地重跑）

```bash
export HTTPS_PROXY=socks5h://127.0.0.1:10808
gh api "repos/swsgbl/ai-learning-os/actions/runs?head_sha=b7db88c3401a6f0ce821a9777fb8b815bd4cf837&per_page=20"
gh api "repos/swsgbl/ai-learning-os/actions/runs/35548392898/jobs?per_page=30"
```

- 命中断言（脚本复核 raw 响应）：`workflow_runs` 中 event=push、head_branch=main
  恰 1 条——run **35548392898**，head_sha `b7db88c…`，status=completed，
  **conclusion=success**（created 2026-09-21T00:39:37Z / updated 00:43:30Z，
  https://github.com/swsgbl/ai-learning-os/actions/runs/35548392898）。
- jobs 断言：`total_count=5` 且全部 conclusion=success——Web（test/typecheck/lint/build）、
  API（ruff/pytest/migration）、Docker（compose build+healthy+smoke）、Android、
  Release tools。与 `.github/workflows/ci.yml` push 触发面一致。
- 按 `_eval_ci_main` 契约写 `ci-main.json`（gate/step/tool 自标识 + run_id +
  merge_commit + conclusion + source 溯源串）；**未改任何 m14-75 文件**。
- raw API 响应原样归档：`raw/gh-runs-b7db88c.json`、`raw/gh-jobs-35548392898.json`。

### 3.2 release-check（隔离本地 full 重跑，b7db88c 代码）

worktree 环境从零搭建（均经代理）：

```bash
uv venv .venv --python 3.12
uv pip install -r services/api/requirements.txt -r services/api/requirements-dev.txt
npm ci --no-audit --no-fund
```

隔离编排器（一次性 SQLite → alembic upgrade head → 127.0.0.1 临时 uvicorn →
/health 就绪 → full 10 门 → 原子落盘 → finally 关停临时 API；环境剥离
AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL，显式 APP_ENV=development /
VOICE_MODE=local / HOST_BIND_IP=127.0.0.1——不连接任何生产面）：

```bash
cd services/api
../.venv/Scripts/python.exe -m app.ops.cli release-check-isolated \
  --workdir "<worktree>/artifacts/m14-78-isolated-r1" --json
# exit 0
```

- 结果：**all_green=true，10/10 pass**（generated_at 2026-09-21T01:02:55Z）——
  api-lint（ruff）/ web-lint / web-typecheck / web-build / api-test
  （**pytest 3939 passed, 33 skipped** in 265.24s；较 m14-75 的 3863 增加，
  含 PR #164 新增 watchdog 77 项契约测试）/ migration（current==head==
  0027_audit_chain）/ backup（aios-backup-v1 tables:30）/ voice
  （local 合成 audio/wav 17324 bytes）/ license（api 15/web ok/models 7/sources 6）/
  e2e（walkthrough 5 步 1225 ms）。
- 工作区产物（gitignored）：`artifacts/m14-78-isolated-r1/`（一次性
  release-check.sqlite + uvicorn.log + release-check-isolated.json）。
- `release-check-isolated.json` 逐字节复制改名为 canonical
  `evidence/release-check.json`（sha256 复核一致：`69c678c2416df73c…`），
  与 m14-75 同款流程；文件自带 gate 自标识 `release-check`，非手改。

### 3.3 release-readiness 聚合（M14-78 canonical 证据目录）

```bash
cd services/api
../.venv/Scripts/python.exe -m app.ops.cli release-readiness \
  --evidence-dir "D:/AI Learning OS/ai-learning-os/.verify/artifacts/m14-78-release-evidence-refresh/evidence" \
  --json --output "<worktree>/artifacts/m14-78-readiness/release-readiness.json"
# exit 1（release_ready=false，如实）
```

- manifest（generated_at 2026-09-21T01:07:06Z，evidence_dir 指向 canonical 目录）：
  **pass=2**（ci-main：run 35548392898 @ b7db88c conclusion=success；
  release-check：all_green 10/10）＋ **missing=9**（production-preflight /
  backup-restore / audit-chain-anchor / legacy-papers / draft-ownership /
  long-soak / provider-smoke / release-approval 为 required，turn-tls 为
  optional）；malformed=0、tampered=0；`not_pass_required` 八项如实列出；
  **release_ready=false，exit_code=1**。
- 设计口径：M14-78 证据目录**只放本切片真实重推导的两个代码绑定门**——生产状态
  绑定门不搬运 m14-75 旧文件冒充 current（搬运会让未重验证的生产快照在聚合里
  显示 pass，即过度宣称）。各生产门最新可用记录见 §2 表与 m14-75 收口存档。

## 4. canonical 证据清单（主仓 gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：`.verify/artifacts/m14-78-release-evidence-refresh/`

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/ci-main.json` | 752 | `d5a8b983246d3f3fefb69a2f8eae2e409ddc2f974e14795871034df88a57b1c3` |
| `evidence/release-check.json` | 2491 | `69c678c2416df73c60927335667196cbebd19306b314152f945f905dba438dfd` |
| `evidence/release-readiness.json` | 6841 | `2903203add363adaeb6dc8db7d991bf3354a8bc566f0c2ad34cd0c1e0ddd05d5` |
| `raw/gh-runs-b7db88c.json` | 12209 | `20f45ee2e8da105548cf83e1fe2deda43b4d97d2b349c05f1c01f286d72575b7` |
| `raw/gh-jobs-35548392898.json` | 12334 | `871c04332b2dee6b6d16db6b8b2e64b105fe2ca15392c3af6374a4a1f94c2eff` |

（worktree 侧 `artifacts/m14-78-isolated-r1/` 与 `artifacts/m14-78-readiness/`
为生成现场，gitignored；canonical 三件为逐字节复制，哈希逐一复核。）

## 5. 聚焦验证与静态检查（真实执行结果）

- 聚焦契约测试（本切片证据链所依赖的工具面，worktree venv）：

```bash
cd services/api && ../.venv/Scripts/python.exe -m pytest -q \
  tests/test_release_readiness.py tests/test_release_check_isolated.py tests/test_release_checklist.py
# 137 passed, 1 warning in 4.55s
```

- 静态：`git diff --check` 干净；canonical 三份 JSON `json.load` 解析通过
  （release-readiness 消费即契约校验：malformed=0/tampered=0）；
  `api-lint (ruff)` 已随 §3.2 隔离运行 pass（无代码改动，不重复跑全量）。
- 分支卫生：`git status --porcelain` 无未提交 tracked 变更（单 commit 后
  tracked-clean）；未 push、未建 PR。

## 6. 诚实边界与剩余阻塞（不伪称，逐项可执行收口）

1. **production-preflight / backup-restore / audit-chain-anchor /
   legacy-papers / draft-ownership / provider-smoke（含 4 份冒烟明细）**：
   生产状态绑定，最新记录仍是 m14-75 收口快照；重推导需要生产 DB/MinIO/
   语音 live 面访问与运维窗口——本切片按约束零生产触碰，全部 **blocked**。
   下一次生产切换窗口统一重执行（隔离 release-check 中的 backup 演练已在
   b7db88c 代码面真实通过，但不是 backup-restore 门的回灌证据形态）。
2. **long-soak**：仍无真实连续 24h 干净窗口证据（m14-75 起 missing；
   M14-77 交付的是看护 readiness，注册未发生，sidecar 断档后需新窗口起算）。
   soak 历史本切片零读写。
3. **release-approval**：人工审批从未发生，本切片明确不触碰、不代拟。
4. **turn-tls**（optional）：公网语音发布形态才必需，本机/LAN 形态如实 missing。
5. `release_ready=false`、`production_ready=false` 不变；本切片不授权任何部署。
6. GitHub Actions run 有平台保留期（默认 90 天）；到期后按 §3.1 命令重取即可。
