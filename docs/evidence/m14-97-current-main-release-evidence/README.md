# M14-97：current-main 代码绑定门证据刷新 + evidence-cockpit 聚合（实现/文档切片）

- 切片：分支 `ops/m14-97-current-main-release-evidence`（独立 worktree
  `m14-97-current-main-release-evidence`，基于 main
  `b374aa5227aafbbbc94282fcb948dcf60223602a`（PR #183 merge，精确基点，
  非 origin/main 快照推断；基点核验：`git fetch origin` 后
  `git rev-parse origin/main` == `b374aa5…` 复核一致）），单次
  local commit（不推送、不建 PR）。
- 目标：对 current main `b374aa5` 只刷新**代码绑定**发布门
  （ci-main / release-check），并以 M14-91 evidence-cockpit 聚合器重新
  汇总发布证据；沿用 M14-94/M14-92 证据契约/流程。绝不手改 gate JSON、
  绝不合成结果、绝不复制 M14-94 产物（CI run / 隔离环境 /
  release-check 运行全部本轮新执行）、绝不搬运旧生产状态证据冒充新执行；
  `release_ready=false` / `production_ready=false` 全程不变。
- 零生产触碰（本切片全程）：零容器启停/重建、零计划任务操作、零
  DB/MinIO/语音引擎接触、零生产日志写入、零 secrets/env 读取、零 soak
  历史/锚定读写、零发布审批接触、零部署。唯一网络访问是 GitHub 只读
  API（`gh api`，已认证账号；`git fetch origin` 复核）+ worktree
  从零环境的包安装（uv/npm）。本切片不改 VPN/代理/CC Switch 状态；
  网络访问经宿主已在运行的 sing-box 回环端口以 shell 环境变量形式借用
  （未启停任何代理进程/系统代理设置，与 M14-96 披露的误启事件不同，
  本切片零 VPN/代理状态操作）。

## 1. 基点事实（全部 git/远端可复核）

| 事件 | commit | 说明 |
|------|--------|------|
| PR #181 merge（M14-89 harmony auth session；M14-94 刷新基点） | `001af38e8786c9578076143351978b96d0332874` | M14-94 canonical 所锚时点 |
| PR #182 merge（M14-94 代码绑定门证据刷新） | `c9de722`（含 `99b455f`） | 纯 docs 5 文件 |
| M14-96 RC 本地彩排冒烟（真实代码） | `55b2f2b` | tools + infra + tests |
| PR #183 merge（M14-96 RC 本地彩排冒烟）→ **当前 main** | `b374aa5227aafbbbc94282fcb948dcf60223602a` | 本切片精确基点 |
| main push CI run 35738367558 创建（push 事件） | b374aa5 | 2026-09-22T14:09:40Z（UTC）；本地 2026-09-22 22:09:40 +0800 |

- `git fetch origin` 复核：origin/main == `b374aa5`（无更新提交）。
- `git diff --stat 001af38..b374aa5`：4 commits、9 文件、+2847/−1——
  PR #182 M14-94 证据（docs 5 文件）+ PR #183 M14-96 RC 彩排冒烟
  （**真实代码**：`tools/ops/rc_smoke_rehearsal.py` +957、
  `services/api/tests/test_rc_smoke_rehearsal.py` +1126、
  `infra/docker-compose.rc-smoke.yml` +154，其余 docs）。services/api
  测试集有真实变更（+59 项）——代码绑定门**必须真实重新执行**才对
  b374aa5 成立，这是本切片的直接动因（M14-94 canonical 对 b374aa5
  已 stale）。
- M14-94 canonical（@ 001af38 时点）自此对 b374aa5 stale——保留为历史
  记录不改写；本切片不搬运其产物。M14-83（@ 5829ad9 生产只读门）、
  M14-85（@ f47a1e4 backup-restore）、M14-87（audit-chain-anchor 门
  闭合 + 伴生锚）、M14-88（provider-smoke 恢复）canonical 保留为其各自
  时点记录，本切片不搬运、不改写。

## 2. 刷新执行记录（命令与结果，全部真实可复跑；全部为本轮新执行）

### 2.1 ci-main（真实远端 CI，非本地重跑）

```bash
gh api "repos/swsgbl/ai-learning-os/actions/runs?head_sha=b374aa5227aafbbbc94282fcb948dcf60223602a&per_page=20"
gh api "repos/swsgbl/ai-learning-os/actions/runs/35738367558/jobs?per_page=30"
```

- 命中断言（断言脚本复核归档 raw 响应，非仅依赖任务简报；任一断言
  失败即非零退出、不产出证据）：`workflow_runs` 中 total_count=1、
  event=push、head_branch=main 恰 1 条（该 SHA 全部 run 也仅此 1 条）——
  run **35738367558**（run_number 486），workflow
  `.github/workflows/ci.yml`，head_sha `b374aa5…`，status=completed，
  **conclusion=success**（created 2026-09-22T14:09:40Z / updated
  2026-09-22T14:13:40Z，
  https://github.com/swsgbl/ai-learning-os/actions/runs/35738367558）。
- jobs 断言：`total_count=5` 且全部 conclusion=success——API
  （ruff/pytest/migration）、Docker（compose build+healthy+smoke）、
  Release tools（Python tests）、Android（unit test/lint/assemble）、
  Web（test/typecheck/lint/build）；每 job head_sha == b374aa5、job 名
  集合精确匹配（重复即拒绝）。
- 按 `_eval_ci_main` 契约（`services/api/app/ops/release_readiness.py`：
  gate/run_id/merge_commit/conclusion 白名单标量）**程序化派生**
  `evidence/ci-main.json`：断言驱动脚本 `derive_ci_main.py`（gitignored
  `artifacts/m14-97-derive/`，ruff check 通过 + py_compile 通过）从 raw
  事实拼装 + 溯源 source 串；断言不过即失败，无手写 pass。未复用
  M14-94 的 run 35719771321 或其 raw 文件。
- raw API 响应原样归档：`raw/gh-runs-b374aa5.json`、
  `raw/gh-jobs-35738367558.json`（哈希见 §4）。

### 2.2 release-check（隔离本地 full 重跑，干净 b374aa5 执行树）

从零构建全新隔离环境（不复用 M14-94 任何产物/环境；其 `.venv`/
`node_modules` 属 m14-94 worktree，本切片在 m14-97 worktree 独立重建）：

```bash
uv venv .venv --python 3.12          # CPython 3.12.14（uv 0.12.1）
uv pip install -r services/api/requirements.txt -r services/api/requirements-dev.txt
npm ci --no-audit --no-fund          # 411 packages（node v22.23.2 / npm 12.0.2）
```

（npm 提示 `unrs-resolver` postinstall 被 allowScripts 策略阻止——与
M14-94/M14-92/M14-90 相同的已知提示，如实记录；该脚本产物非门禁依赖，
本轮 10 门全绿证实无影响。）

**执行树前置核验**（declared-head 诚实性前提）：运行前
`git rev-parse HEAD` == `b374aa5…` 且 `git status --porcelain` 为空
（tracked-clean，仅 gitignored `.verify/`/`artifacts/` 增量）——
release-check 的实际执行树即精确干净 b374aa5，后续 cockpit
`--gate-declared-head release-check=b374aa5` 声明成立。

隔离编排器（一次性 SQLite → alembic upgrade head → 127.0.0.1 临时
uvicorn → /health 就绪 → full 10 门 → 原子落盘 → finally 关停临时 API；
环境剥离 AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL，显式
APP_ENV=development / VOICE_MODE=local——不连接任何生产面；worktree
绝对路径调用；新工作区 `artifacts/m14-97-isolated/`）：

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli release-check-isolated \
  --workdir "<worktree>/artifacts/m14-97-isolated" --json
# exit 0
```

- 结果：**all_green=true，10/10 pass**（generated_at
  2026-09-22T14:37:52Z，execution_scope=full）——api-lint（ruff All
  checks passed）/ web-lint / web-typecheck / web-build /
  api-test（**pytest 4158 passed, 33 skipped, 1 warning in 261.05s**；
  较 M14-94 @ 001af38 的 4099 恰 +59——PR #183 新增
  `tests/test_rc_smoke_rehearsal.py` 59 项的预期对账，非搬运
  复算）/ migration（current == head == `0027_audit_chain`）/ backup
  （aios-backup-v1 tables:30 files:1）/ voice（local 合成 audio/wav
  17324 bytes）/ license（api deps 15 / web ok / models 7 / sources 6）/
  e2e（walkthrough 5 步 3301 ms）。
- `release-check-isolated.json` 逐字节复制改名为 canonical
  `evidence/release-check.json`（程序化比对 byte-identical=True；文件
  自带 gate 自标识 `release-check`，非手改）。

### 2.3 evidence-cockpit 聚合（代码绑定门刷新后重新汇总）

在 tracked docs 编辑**之前**、HEAD 仍为干净 `b374aa5` 时运行。canonical
源路径逐一取自 M14-91 README §5/`staged-inventory.txt`、M14-94 canonical
`staged-inventory.txt` 与其 `cockpit-report.json` 的 gate_bindings 登记
（不猜测）；staging 前先对 7 个既有生产状态源文件做 sha256 复核——与
登记值逐一 **MATCH**（audit-chain-anchor `a8c54c5e…` / audit-anchor
`d2bfd877…` / backup-restore `ed0fc5b4…` / preflight `b3a2be66…` /
legacy `83cd62bd…` / draft `e90ae04c…` / provider-smoke
`87e76787…`）。

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli evidence-cockpit \
  --gate-source ci-main="<m14-97 canonical>/evidence/ci-main.json" \
  --gate-source release-check="<m14-97 canonical>/evidence/release-check.json" \
  --gate-source audit-chain-anchor="<主仓>/.verify/artifacts/m14-87-audit-chain-current-gate/evidence/audit-chain-anchor.json" \
  --gate-source production-preflight="…m14-83-production-read-only-evidence…/evidence/production-preflight.json" \
  --gate-source legacy-papers="…m14-83…/evidence/legacy-papers.json" \
  --gate-source draft-ownership="…m14-83…/evidence/draft-ownership.json" \
  --gate-source backup-restore="…m14-85-backup-restore…/evidence/backup-restore.json" \
  --gate-source provider-smoke="<M14-88 worktree>/.verify/artifacts/m14-88-provider-smoke-recovery/provider-smoke.json" \
  --anchor-companion "<主仓>/.verify/artifacts/m14-87-audit-chain-current-gate/evidence/audit-anchor.jsonl" \
  --gate-declared-head release-check=b374aa5227aafbbbc94282fcb948dcf60223602a \
  --current-head b374aa5227aafbbbc94282fcb948dcf60223602a \
  --staging-dir "<worktree>/artifacts/m14-97-cockpit-staging" \
  --json --output "<worktree>/artifacts/m14-97-cockpit-report.json"
# exit 1（cockpit_ready=false，如实——见下）
```

- `--gate-declared-head release-check=b374aa5` 的合法性：§2.2 前置
  核验证明实际执行树即干净 b374aa5（gate JSON 本身不内嵌 head，由调用
  方显式声明；声明与事实一致方可用）。
- 结果（报告归档 canonical `cockpit-report.json`，generated_at
  2026-09-22T14:39:55Z）：
  **evaluator pass=8 / pending=0 / blocked=0 / missing=3 / malformed=0
  / tampered=0**；**release_ready=false**、**production_ready=false**
  （恒 false）、cockpit_ready=false、**exit 1**。
- **code-bound 两门刷新生效**：ci-main stale=**current**（内嵌
  merge_commit=b374aa5 == current HEAD）、release-check stale=
  **current**（flag 声明 b374aa5）。**blockers 仅剩
  `long-soak:not-staged-required`**（required 门未 stage，按收紧语义
  如实阻断）。
- production-state 五门（audit-chain-anchor / production-preflight /
  legacy-papers / draft-ownership / backup-restore / provider-smoke 六
  门 staged + anchor companion）undeclared 如实呈现不 block（M14-91
  §1.1 第 5 条口径：呈现性选择，效力判断留 supervisor + 声明通道）；
  release-approval「按策略永不接受，非 blocker」、turn-tls「optional：
  本机/LAN 范围不阻断」、long-soak「required 未 stage → blocker」三类
  摘要显式区分。
- **staged 9 文件与 source 逐字节一致**：工具内建写后重读断言 + 外部
  独立复核 **9/9 IDENTICAL**（cmp 语义逐字节比较）；7 个既有文件哈希
  与 M14-91/M14-94 staged-inventory 登记值交叉一致、2 个新文件（ci-main
  `6430b7bf…` 1123 bytes / release-check `c746fb7b…` 2491 bytes）与
  本切片 canonical 实际哈希一致——来源零改动 + 逐字节 staging 端到端
  实证（清单归档 canonical `staged-inventory.txt`）。
- exit 1 是**诚实预期**：long-soak（24h 审计未发生）与
  release-approval（human-only 从未发生）缺席之下，cockpit 不可能
  ready；本切片不以任何方式强制 exit 0。

## 3. 聚焦验证与静态检查（真实执行结果）

- 聚焦契约测试（cockpit 三件套，本切片证据链所依赖的工具面）：

```bash
cd services/api && "<worktree>/.venv/Scripts/python.exe" -m pytest -q \
  tests/test_evidence_cockpit.py tests/test_release_readiness.py tests/test_release_closure_manifest.py
# 145 passed in 3.02s（与 M14-91/M14-94 记录一致）
```

- release-check 契约三件套（本切片执行了 release-check-isolated）：

```bash
cd services/api && "<worktree>/.venv/Scripts/python.exe" -m pytest -q \
  tests/test_release_readiness.py tests/test_release_check_isolated.py tests/test_release_checklist.py
# 137 passed, 1 warning in 3.09s（与 M14-90/M14-94 记录一致）
```

- ruff：本切片唯一触碰的 Python 工具（gitignored 断言派生脚本
  `derive_ci_main.py`）`ruff check` All checks passed（tracked Python
  零改动）；`py_compile` 通过。
- canonical 五份 JSON `json.load` 解析通过（cockpit 报告消费即契约
  校验：malformed=0/tampered=0）；canonical `release-check.json` 与
  隔离运行原始产物**逐字节一致**（程序化比对 True）。
- `git diff --check` 干净（docs-only）；分支卫生：单 commit 后
  tracked-clean（worktree 侧 `.venv`/`node_modules`/`artifacts/`/
  `.verify/` 均 gitignored 未入库）；未 push、未建 PR。

## 4. canonical 证据清单（gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：worktree `.verify/artifacts/m14-97-current-main-release-evidence/`

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/ci-main.json` | 1123 | `6430b7bfb45e2e144d32640324d8919587ac340106ef88480d1be3207c7f82ad` |
| `evidence/release-check.json` | 2491 | `c746fb7bdd6999e388864c4c5682957118bdaaed77702c9563e1a82d0faf728f` |
| `raw/gh-runs-b374aa5.json` | 12155 | `16a290709f6e1163d59f83d5aa8d061b920df6fc5e8a52b6626c1c902654866a` |
| `raw/gh-jobs-35738367558.json` | 12334 | `fe5abe20750f8433b0caa3e4d6710fde0025dccfcc435f435ac89ae2c089b442` |
| `cockpit-report.json` | 17355 | `0343936ac3e75321a54e0a1a1216e4b33f9d44680579295d1e75a1fc87d60190` |
| `staged-inventory.txt` | 824 | `7c66bd353a12e34e980741e7b3e6f8db86cc26838e29007123161bf0aa2a736e` |

（worktree 侧 `artifacts/m14-97-isolated/`、`artifacts/m14-97-cockpit-staging/`、
`artifacts/m14-97-derive/` 与 stdout/stderr 日志为生成现场，gitignored，不入库。）

## 5. 诚实边界与剩余阻塞（不伪称，逐项可执行收口）

1. **cockpit blockers 仅剩 `long-soak:not-staged-required`**：long-soak
   24h 审计未发生——M14-79 权威锚定记录锚点 = 2026-09-21T15:00:01Z、
   最早审计时点 = 2026-09-22T15:00:01Z；本切片 cockpit 生成于
   2026-09-22T14:39:55Z，早于最早审计时点约 20 分钟，24h 窗口结局
   未判定，long-soak 如实 not-staged，不预宣称。锚定 ≠ long-soak
   通过、不授权任何 readiness。已合入的 M14-93 到期审计/导出 runner
   （`tools/ops/long_soak_release_window.py`）是窗口到期后真实执行的
   既定工具，但其对真实生产 history/锚的执行是后续运维动作，本切片
   零 soak 接触、不预判其结果。
2. **release-approval 从未发生**：人工审批 human-only，cockpit 永不
   接受、本切片不触碰、不代拟；`release_ready=false` /
   `production_ready=false` 不变。
3. **production-state 门是「呈现」不是「重执行」**：staged 的六门 +
   anchor companion 均为各切片 canonical 时点快照（M14-83 @ 5829ad9、
   M14-85 @ f47a1e4、M14-87、M14-88），本切片零生产触碰约束下不重
   推导、不搬运冒充新执行；其对 current 生产状态的效力（尤其 M14-83
   三门时距）由 supervisor 结合生产变更记录判断，cockpit 提供声明通道
   显式表达。provider-smoke 恢复的易失性（依赖宿主 sing-box/ollama
   用户进程）仍如 M14-88 记录——发布窗口前需按当时拓扑真实重推导。
4. **turn-tls 仍 optional 未 stage**：公网语音发布形态才必需，本机/
   LAN 范围不阻断。
5. **生产仍运行 m14-70 镜像**（2026-09-19 切换后未再变更），本切片
   零调度面触碰、不构成任何部署；不授权任何部署。
6. **M14-96 已随 PR #183 合入本基点**：其 RC 彩排冒烟机制 + 诚实
   失败执行（build-failed:api，daemon mirror 经代理不可达）属基点
   增量一部分（§1），CI 5/5 与 release-check 10/10 已覆盖其合入后
   状态；本切片不额外声称其镜像构建/起栈结论（以 M14-96 自身
   canonical 证据为准——机制未改、待出站面可用后复跑补齐）。
7. **代码绑定门证据只对其执行时点的树成立**：ci-main/release-check
   现对 b374aa5 current；main 再前移即再 stale（M14-91 冒烟已实证该
   语义），需下一个刷新切片真实重推导。
8. **GitHub Actions run 有平台保留期**（默认 90 天）；到期后按 §2.1
   命令重取即可。
