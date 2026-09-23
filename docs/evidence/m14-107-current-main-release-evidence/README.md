# M14-107：current-main 发布证据刷新（ci-main + release-check + 首次 staged long-soak + evidence-cockpit 聚合）

- 切片：分支 `ops/m14-107-current-main-evidence-refresh`（独立 worktree
  `m14-107-current-main-evidence-refresh`，基于 main
  `22a9cbf89472faf8cbcef3bbfc2fb2329371287e`（PR #194 merge = M14-106
  合入，精确基点），单 local commit（不推送、不建 PR）。
- 目标：基于 M14-105/M14-104 既有契约刷新 current main 的 ci-main 与
  release-check 两门，**首次 stage supervisor 已复核的 24h long-soak pass
  证据**，并以 M14-91 evidence-cockpit 重新聚合；绝不手改 gate JSON、
  绝不合成 pass、绝不搬运旧 JSON 冒充 current。`release_ready=false` /
  `production_ready=false` 全程不变——provider-smoke 无 current 聚合
  证据，按 not-staged-required 如实阻断。
- 零生产触碰（本切片全程）：零容器启停/重建、零计划任务操作、零
  DB/MinIO/语音引擎接触、零生产日志写入、零 secrets/env 读取、零
  soak 历史重跑/锚定写入、零发布审批接触、零部署、零 Ollama/FunASR/
  CosyVoice/SearXNG/代理/CC Switch 生命周期变更。唯一网络访问是
  GitHub 只读 API（`gh api`，已认证账号）+ worktree 从零环境的包安装
  （uv/npm）。**long-soak 不重跑**——只读校验并逐字节复制 supervisor
  已复核的 M14-106 产物（§2.3）。
- 本 README 为唯一入库证据文件；canonical 原始证据（gitignored）位于
  worktree `.verify/artifacts/m14-107-current-main-release-evidence/`
  （13 文件 + SHA256SUMS 索引，自不含自哈希；§5）。

## 1. 基点事实（git/远端可复核）

| 事件 | commit | 说明 |
|------|--------|------|
| PR #192 merge（M14-104 provider-smoke 证据刷新） | `e5d5d3a` | M14-105 基点 |
| PR #193 merge（M14-105 代码绑定门证据刷新） | `c11c066` | 本切片直接前驱 |
| PR #194 merge（M14-106 本地 loopback 语音 provider 与冒烟健康探测绕过环境代理）→ **当前 main** | `22a9cbf89472faf8cbcef3bbfc2fb2329371287e` | 本切片精确基点 |
| main push CI run 35811421324 创建（push 事件） | 22a9cbf | 2026-09-23T02:42:25Z（UTC）；本地 2026-09-23 10:42:25 +0800 |

- `e5d5d3a..22a9cbf` 两个 PR merge（#193 M14-105 证据 docs、#194
  M14-106 **真实代码变更**——本地语音 provider 与冒烟健康探测的
  loopback `trust_env=False` 加固 + 新增 6 个契约测试），M14-105
  canonical（ci-main @ e5d5d3a/run 35805140338、release-check @
  e5d5d3a/pytest 4227）自此对 22a9cbf **stale**——代码绑定门必须
  真实重新执行，这是本切片的直接动因。
- M14-105 canonical 保留为其时点历史记录不改写；本切片不搬运其产物。
  M14-83（@ 5829ad9 生产只读门）、M14-85（@ f47a1e4 backup-restore）、
  M14-87（@ audit-chain-anchor 门 + 伴生锚）canonical 按 M14-91 §5
  登记路径**原样 staging**（哈希复核见 §2.4）。M14-88/M14-98/M14-104
  的 provider-smoke 步证据**本轮不 stage**（§2.4 诚实语义）。
- **long-soak 首次具备可 stage 的 pass 证据**：M14-105 时 24h 审计尚未
  发生（blocker `long-soak:not-staged-required`）；supervisor 已于
  M14-106 轮完成到期审计重跑并复核（源位于主仓 gitignored
  `.verify/tmp/m14-106-supervisor-long-soak-rerun/evidence/long-soak.json`），
  本切片只读校验后将其字节原样 stage（§2.3）——这是 long-soak 门
  首次以真实 pass 证据进入 cockpit 聚合。

## 2. 刷新执行记录（命令与结果，全部真实可复跑；ci-main/release-check 为本轮新执行，long-soak 为只读校验复制）

### 2.1 ci-main（真实远端 CI，非本地重跑）

```bash
gh api "repos/swsgbl/ai-learning-os/actions/runs?head_sha=22a9cbf89472faf8cbcef3bbfc2fb2329371287e&per_page=20"
gh api "repos/swsgbl/ai-learning-os/actions/runs/35811421324/jobs?per_page=30"
```

- 命中断言（断言脚本 `derive_ci_main.py` 复核归档 raw 响应而非仅信
  任务简报；任一断言失败即非零退出、不产出证据）：`workflow_runs` 中
  total_count=1、event=push、head_branch=main 恰 1 条（该 SHA 全部 run
  也仅此 1 条）——run **35811421324**（run_number 509），workflow
  name=CI、path=`.github/workflows/ci.yml`，head_sha `22a9cbf…`，
  status=completed，**conclusion=success**（created
  2026-09-23T02:42:25Z / updated 2026-09-23T02:47:14Z，
  https://github.com/swsgbl/ai-learning-os/actions/runs/35811421324）。
- jobs 断言：`total_count=5` 且全部 conclusion=success——API
  （ruff/pytest/migration）、Docker（compose build+healthy+smoke）、
  Release tools（Python tests）、Android（unit test/lint/assemble）、
  Web（test/typecheck/lint/build）；每 job head_sha == 22a9cbf、job 名
  集合精确匹配且无重复（`len(set)==5==total_count`）。
- 按 `_eval_ci_main` 契约（`services/api/app/ops/release_readiness.py`：
  gate/run_id/merge_commit/conclusion 白名单标量）**程序化派生**
  canonical `evidence/ci-main.json`：断言驱动脚本从 raw 事实拼装 +
  溯源 source 串；断言不过即失败，无手写 pass。未复用 M14-105 的
  run 35805140338 或其 raw 文件。
- raw API 响应原样归档：`raw/gh-runs-22a9cbf.json`、
  `raw/gh-jobs-35811421324.json`（哈希见 §5）。

### 2.2 release-check（隔离本地 full 重跑，干净 22a9cbf 执行树）

从零构建全新隔离环境（不复用 M14-105 任何产物/环境；其 `.venv`/
`node_modules` 属 m14-105 worktree，本切片在 m14-107 worktree 独立重建）：

```bash
uv venv .venv --python 3.12          # CPython 3.12.14（uv 0.12.1）
NO_PROXY='*' uv pip install -r services/api/requirements.txt -r services/api/requirements-dev.txt
npm ci --no-audit --no-fund          # 411 packages（node v22 / npm 12）
```

（uv 安装以 `NO_PROXY='*'` 直连镜像完成（与 M14-104/M14-105 同法，
本机系统代理隧道失效的既有边界）；npm 提示 `unrs-resolver`
postinstall 被 allowScripts 策略阻止——与 M14-97/M14-94/M14-92/
M14-90/M14-105 相同的已知提示，如实记录；该脚本产物非门禁依赖，
本轮 10 门全绿证实无影响。）

**执行树前置核验**（declared-head 诚实性前提）：运行前
`git rev-parse HEAD` == `22a9cbf…` 且 `git status --porcelain` 为空
（tracked-clean，仅 gitignored `.verify/`/`artifacts/` 增量）——
release-check 的实际执行树即精确干净 22a9cbf，后续 cockpit
`--gate-declared-head release-check=22a9cbf` 声明成立。

隔离编排器（一次性 SQLite → alembic upgrade head → 127.0.0.1 临时
uvicorn → /health 就绪 → full 10 门 → 原子落盘 → finally 关停临时 API；
环境剥离 AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL，显式
APP_ENV=development / VOICE_MODE=local——不连接任何生产面；worktree
绝对路径调用；新工作区 `artifacts/m14-107-isolated/`）：

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli release-check-isolated \
  --workdir "<worktree>/artifacts/m14-107-isolated" --json
# exit 0
```

- 结果：**all_green=true，10/10 pass**（generated_at
  2026-09-23T03:04:52Z，execution_scope=full）——api-lint（ruff All
  checks passed）/ web-lint / web-typecheck / web-build /
  **api-test（pytest 4233 passed, 33 skipped, 1 warning in 296.02s；
  较 M14-105 @ e5d5d3a 的 4227 恰 +6——PR #194（M14-106）新增 6 个
  契约测试的预期对账，实测吻合、无硬编码）** / migration（alembic
  head）/ backup（aios-backup-v1 tables:30 files:1）/ voice（local
  合成 audio/wav 17324 bytes）/ license（api deps 15 / web ok /
  models 7 / sources 6）/ e2e（walkthrough 5 步）。
- `release-check-isolated.json` 逐字节复制改名为 canonical
  `evidence/release-check.json`（程序化比对 byte-identical=True；文件
  自带 gate 自标识 `release-check`，非手改）。

### 2.3 long-soak（不重跑、不触碰生产/计划任务；只读校验 + 逐字节复制）

- 源（只读）：主仓 gitignored
  `D:\AI Learning OS\ai-learning-os\.verify\tmp\m14-106-supervisor-long-soak-rerun\evidence\long-soak.json`，
  SHA256 实测 `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec`
  ——与任务指定值 `D939C6528B9FE739270FE6EA706837FFA9DC13115665DC90740E6DB53A1056EC`
  一致（大小写不敏感）。
- 复制到本切片 canonical `evidence/long-soak.json` 后**重新哈希 +
  JSON 断言**：逐字节一致（817 bytes 同哈希）；gate 自声明
  `long-soak`、audit_schema_version=2、tool
  `tools/ops/soak_stability_audit.py`、策略四值恰
  1440/15/20/500（与 `_eval_long_soak` v2 契约一致）、
  classification=**pass**、reasons=[]。
- 窗口事实（README 如实转述，非本切片重跑）：**24h 窗口
  2026-09-22T02:00:01Z → 2026-09-23T02:00:01Z（跨度恰 1440.0 分钟），
  97 样本全 ok / 0 warn / 0 critical（window_non_ok_count=0），
  max_observed_gap_minutes=17.25 ≤ 20 上限**，selected_row_count=97
  （恰为 1440/15+1 闭区间最小样本数）。
- 本切片零 soak 接触：未运行 `soak_stability_audit.py`、未触碰
  history/锚定/计划任务/监控管道——该 pass 证据的执行与 supervisor
  复核属 M14-106 运维轮，本切片只做来源哈希锁定 + 字节原样 stage。

### 2.4 evidence-cockpit 聚合（两门刷新 + long-soak 首次 stage 后重新汇总）

在 tracked docs 编辑**之前**、HEAD 仍为干净 `22a9cbf` 时运行。canonical
源路径逐一取自 M14-91 README §5 / staged-inventory 登记与 M14-105 §2.3
同一路径（不猜测）；staging 前先对 6 个既有生产状态源文件做 sha256
复核——与 M14-91 登记值逐一 **MATCH（6/6）**：audit-chain-anchor
`a8c54c5e…` / audit-anchor companion `d2bfd877…` / backup-restore
`ed0fc5b4…` / preflight `b3a2be66…` / legacy `83cd62bd…` / draft
`e90ae04c…`。

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli evidence-cockpit \
  --gate-source ci-main="<本切片 canonical>/evidence/ci-main.json" \
  --gate-source release-check="<本切片 canonical>/evidence/release-check.json" \
  --gate-source long-soak="<本切片 canonical>/evidence/long-soak.json" \
  --gate-source audit-chain-anchor="<主仓>/.verify/artifacts/m14-87-audit-chain-current-gate/evidence/audit-chain-anchor.json" \
  --gate-source production-preflight="…m14-83-production-read-only-evidence…/evidence/production-preflight.json" \
  --gate-source legacy-papers="…m14-83…/evidence/legacy-papers.json" \
  --gate-source draft-ownership="…m14-83…/evidence/draft-ownership.json" \
  --gate-source backup-restore="…m14-85-backup-restore…/evidence/backup-restore.json" \
  --anchor-companion "<主仓>/.verify/artifacts/m14-87-audit-chain-current-gate/evidence/audit-anchor.jsonl" \
  --gate-declared-head release-check=22a9cbf89472faf8cbcef3bbfc2fb2329371287e \
  --current-head 22a9cbf89472faf8cbcef3bbfc2fb2329371287e \
  --staging-dir "<worktree>/artifacts/m14-107-cockpit-staging" \
  --json --output "<worktree>/artifacts/m14-107-cockpit-report.json"
# exit 1（cockpit_ready=false，如实——见下）
```

- **current-head 与 release-check head 显式声明
  `22a9cbf89472faf8cbcef3bbfc2fb2329371287e`**；`--gate-declared-head
  release-check` 的合法性由 §2.2 前置核验支撑（实际执行树即干净
  22a9cbf；ci-main 则由文件内嵌 merge_commit==current HEAD 自动
  current）。
- **provider-smoke 本轮未传入（未 stage）**：本基点上 provider-smoke
  的最新真实执行即 M14-104 的 search/llm 失败轮（aggregate 未运行、
  无 provider-smoke.json）；M14-88（2026-09-21 恢复时点）/M14-98
  （2026-09-22 失败窗口）旧 JSON 均不得搬运冒充 current。provider-smoke
  为 required 门，按 cockpit 收紧语义如实产出
  `provider-smoke:not-staged-required` blocker（exit 1 的组成部分）。
- 结果（报告归档 canonical `cockpit-report.json`，generated_at
  2026-09-23T03:07:34Z）：
  **evaluator pass=8 / pending=0 / blocked=0 / missing=3 / malformed=0
  / tampered=0**；**release_ready=false**、**production_ready=false**
  （恒 false）、cockpit_ready=false、**exit 1**。
- **code-bound 两门刷新生效**：ci-main stale=**current**（内嵌
  merge_commit=22a9cbf == current HEAD）、release-check stale=
  **current**（flag 声明 22a9cbf）。
- **long-soak 首次 staged 即 pass**（真实 24h 稳定窗口：97 样本全 ok、
  最大间隔 17.25 分钟 ≤ 20、跨度恰 1440 分钟——§2.3 证据经
  `_eval_long_soak` 全语义校验）。**blockers =
  `provider-smoke:not-staged-required`（唯一）**——较 M14-105 的双
  blocker（long-soak + provider-smoke）收窄为单 blocker：long-soak
  缺口已由 supervisor 复核的 pass 证据闭合，provider-smoke 缺口
  仍在（其解除条件 = 出站网络 + Ollama 在场 + GPU 空闲窗口重跑三件
  套 + aggregate，是后续运维动作）。
- production-state 五门（audit-chain-anchor / production-preflight /
  legacy-papers / draft-ownership / backup-restore）+ anchor companion
  以历史 canonical snapshot 原样 staged、undeclared 如实呈现不 block
  （M14-91 §1.1 第 5 条口径：呈现性选择，效力判断留 supervisor +
  声明通道）；release-approval「按策略永不接受，非 blocker」、
  turn-tls「optional：本机/LAN 范围不阻断」、provider-smoke「required
  未 stage → blocker」摘要显式区分。
- **staged 9 文件（8 门 + anchor companion）与 source 逐字节一致**：
  工具内建写后重读断言 + 外部独立复核 **9/9 IDENTICAL**（filecmp
  语义逐字节比较）；6 个既有文件哈希与 M14-91 staged-inventory 登记
  值交叉一致、本切片 3 个新文件（ci-main `7bcacd80…` 1146 bytes /
  release-check `20b8891e…` 2491 bytes / long-soak `d939c652…` 817
  bytes）与 canonical 实际哈希一致——来源零改动 + 逐字节 staging
  端到端实证（清单归档 canonical `staged-inventory.txt`）。
- exit 1 是**诚实预期**：provider-smoke（M14-104 真实失败 +
  aggregate 未运行）与 release-approval（human-only 从未发生）缺席
  之下，cockpit 不可能 ready；本切片不以任何方式强制 exit 0。

## 3. 聚焦验证与静态检查（真实执行结果）

- 聚焦契约测试（cockpit 三件套，本切片证据链所依赖的工具面）：

```bash
cd services/api && "<worktree>/.venv/Scripts/python.exe" -m pytest -q \
  tests/test_evidence_cockpit.py tests/test_release_readiness.py tests/test_release_closure_manifest.py \
  --basetemp "D:/AI Learning OS/.pytest-tmp/m14-107-current-main-release-evidence/cockpit3"
# 145 passed in 2.17s（与 M14-105/M14-97/M14-91 记录一致）
```

- release-check 契约三件套（本切片执行了 release-check-isolated）：

```bash
cd services/api && "<worktree>/.venv/Scripts/python.exe" -m pytest -q \
  tests/test_release_readiness.py tests/test_release_check_isolated.py tests/test_release_checklist.py \
  --basetemp "D:/AI Learning OS/.pytest-tmp/m14-107-current-main-release-evidence/releasecheck3"
# 137 passed, 1 warning in 2.86s（与 M14-105/M14-97/M14-90 记录一致）
```

- ruff：本切片的 4 个 gitignored 断言/复核脚本
  （`verify_sources.py`/`derive_ci_main.py`/`stage_long_soak.py`/
  `verify_staging.py`，位于 `artifacts/m14-107-derive/`）`ruff check`
  All checks passed；全量 `ruff check services/api` 亦 All checks
  passed（tracked Python 零改动，基线复核）。
- canonical JSON `json.load` 解析通过 8 份（ci-main/release-check/
  long-soak/cockpit-report/两 raw/两 stdout）；canonical
  `release-check.json` 与隔离运行原始产物**逐字节一致**（程序化比对
  True）；canonical `long-soak.json` 与 M14-106 源**逐字节一致且
  SHA256 复核相等**。
- `git diff --check` 干净（docs-only）；分支卫生：单 commit 后
  tracked-clean（worktree 侧 `.venv`/`node_modules`/`artifacts/`/
  `.verify/` 均 gitignored 未入库）；未 push、未建 PR。

## 4. 与 M14-105 的差异（同一契约下的净变化）

| 维度 | M14-105 @ e5d5d3a | M14-107 @ 22a9cbf |
|------|-------------------|-------------------|
| ci-main run | 35805140338（run_number 505） | **35811421324**（run_number 509，本轮新派生） |
| release-check pytest | 4227 passed / 33 skipped（313.06s） | **4233 passed / 33 skipped**（296.02s，恰 +6 = M14-106 新增测试） |
| long-soak | not-staged → blocker | **staged pass**（M14-106 supervisor 复核的 24h 窗口，§2.3） |
| evaluator | pass=7 / missing=4 | **pass=8 / missing=3** |
| cockpit blockers | long-soak + provider-smoke（双） | **provider-smoke（唯一）** |
| production-state 五门 + anchor | 原样 staged（同源同哈希） | 原样 staged（同源同哈希，6/6 MATCH 复核） |

## 5. canonical 证据清单（gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：worktree `.verify/artifacts/m14-107-current-main-release-evidence/`

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/ci-main.json` | 1146 | `7bcacd8088c917eb3d3154c53ca90b523da3bba30fb8eb309cf3293dc15a51d3` |
| `evidence/release-check.json` | 2491 | `20b8891eb1480a6616e56b0807f9b383bf60703f71221c40ba36f8117d7a140f` |
| `evidence/long-soak.json` | 817 | `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec` |
| `raw/gh-runs-22a9cbf.json` | 12229 | `f1ee11956346224fe061f4041d8669c3f77eeb11c6b8146ee518315bb280208f` |
| `raw/gh-jobs-35811421324.json` | 12334 | `e3eec439a0c28c94672482a021bbcc7f4378ca1a68b1fe097f922dd9cc7d9821` |
| `cockpit-report.json` | 17722 | `a5a76545d533786f9e5961e866e08dae2c11a8fcf28b4195ee6f714b42befba0` |
| `staged-inventory.txt` | 810 | `e1006b5e0468717bad63851e2c40b768d79d148ad98eea4dcd677b90ec5e0e64` |

（另 `logs/` 四份执行日志 + `raw/*.stderr.txt` 两份空捕获 +
`SHA256SUMS` 13 文件索引，自不含自哈希；worktree 侧
`artifacts/m14-107-isolated/`、`artifacts/m14-107-cockpit-staging/`、
`artifacts/m14-107-derive/` 与 stdout/stderr 现场为生成现场，
gitignored，不入库。）

## 6. 诚实边界与剩余阻塞（不伪称，逐项可执行收口）

1. **cockpit 唯一 blocker = `provider-smoke:not-staged-required`**。
   本基点上该门最新真实执行即 M14-104 的失败轮（search：SearXNG
   上游境外网络不可达；llm：Ollama 端点进程未运行；aggregate 未
   运行、无 provider-smoke.json）。解除条件（恢复出站网络 + Ollama
   在场 + GPU 空闲窗口重跑三件套、随后 aggregate）是后续运维动作，
   留 supervisor 决策，本切片不代跑、不搬运 M14-88/M14-98 旧 pass。
2. **long-soak pass 是 M14-106 运维轮的时点证据**：窗口
   2026-09-22T02:00:01Z → 2026-09-23T02:00:01Z 内 97 样本全 ok、
   max gap 17.25 分钟——它证明该窗口的生产稳定性，不承诺窗口外
   状态，也不被本切片重新执行；其 supervisor 复核记录在主仓
   `.verify/tmp/m14-106-supervisor-long-soak-rerun/`。
3. **release-approval 从未发生**：人工审批 human-only，cockpit 永不
   接受、本切片不触碰、不代拟；`release_ready=false` /
   `production_ready=false` 不变。
4. **production-state 门是「呈现」不是「重执行」**：staged 的五门 +
   anchor companion 均为各切片 canonical 时点快照（M14-83 @
   5829ad9、M14-85 @ f47a1e4、M14-87），本切片零生产触碰约束下不
   重推导、不搬运冒充新执行；其对 current 生产状态的效力（尤其
   M14-83 三门时距）由 supervisor 结合生产变更记录判断，cockpit
   提供声明通道显式表达。
5. **turn-tls 仍 optional 未 stage**：公网语音发布形态才必需，本机/
   LAN 范围不阻断。
6. **生产仍运行 m14-70 镜像**（2026-09-19 切换后未再变更），本切片
   零调度面触碰、不构成任何部署；不授权任何部署。
7. **代码绑定门证据只对其执行时点的树成立**：ci-main/release-check
   现对 22a9cbf current；main 再前移即再 stale（M14-91 冒烟已实证
   该语义），需下一个刷新切片真实重推导。
8. **GitHub Actions run 有平台保留期**（默认 90 天）；到期后按 §2.1
   命令重取即可。
