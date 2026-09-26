# M14-152：current-main 发布证据刷新（PR #239 后；ci-main + release-check 真实重跑 + provider-smoke/long-soak 只读复用 + cockpit 零 blocker 聚合）

- 切片：分支 `ops/m14-152-current-main-release-evidence`（复用
  `m14-151-release-approval-draft` worktree，基于 main
  `7438227ce1cd1a8059ef621293ba195e4896ce11`（PR #239 merge = M14-151
  release-approval-draft 合入，精确基点 = 任务执行时直连 fetch 核验的
  origin/main，起点即 tracked-clean 核验通过），单 local commit，
  **不 push、不开 PR（任务书指令）**。
- **动因与定性（为什么 M14-151 触发刷新）**：M14-149 的代码绑定门证据
  绑定执行基点 `c948931b`，其后 `c948931..7438227` 区间 = M14-151 单
  内容提交 `3852d2c`（+1198/−1，7 文件）——**含真实运行时代码**：
  `app/ops/release_approval_draft.py`（182 行新工具）、
  `app/ops/release_readiness.py` +39 行（`_eval_release_approval` 新增
  DRAFT 保留字段 fail-closed 防线，evaluator 本体区间内变更——M14-149
  时区间零变更的前提不再成立）、`app/ops/cli.py` +110 行（新子命令），
  以及 **+37 个测试**（test_release_approval_draft.py 23 +
  test_release_readiness.py 14）。代码绑定门（ci-main/release-check）
  相对当前 main 漂移**不再是 docs-only**——按既有契约在当前 HEAD
  `7438227` 上真实重执行两门；**绝不复用/搬运 M14-149 的 ci-main/
  release-check 产物或计数**（run 36213290023 与 c948931b 上的
  release-check 结果均不复用）。
- **no-infinite-refresh 边界（本切片明确声明）**：docs-only 入库（如
  M14-150 状态回填、证据 README 本身）不构成刷新触发——代码绑定门证据
  只对其执行时点的**树内容**成立，纯文档提交不改变被测代码；只有区间
  含运行时代码或测试面变更时才需要下一轮真实重执行。本切片基点
  `7438227` 即刷新时点的 current main；若 main 在本切片执行期间/之后再
  前移且含非 docs 变更，stale 由**下一个刷新切片**覆盖，本切片不递归
  追新（不做「无限刷新」），也不在证据链中声称对 7438227 之后的树有效。
- provider-smoke / long-soak 按任务边界**零重跑、零生产接触**，只读
  核验出处与精确哈希后逐字节复用（§2.3/§2.4）；**`release_ready=false`
  / `production_ready=false` 全程不变**——release-approval 是 human-only
  门、从未发生，cockpit 按策略永不接受它（not-staged 呈现、不计
  blocker）；turn-tls optional 不阻断。
- 零生产触碰（本切片全程）：零容器启停/重建、零计划任务操作、零
  DB/MinIO/语音引擎接触、零生产日志写入、零 secrets/env 读取、零 soak
  历史重跑/锚定写入、零发布审批接触、零部署、零 Ollama/FunASR/
  CosyVoice/SearXNG/代理/CC Switch/scheduler 生命周期变更。唯一网络
  访问是 GitHub 只读 API（`gh api` 双查询直连成功、零代理配置变更；
  git fetch origin main 同样直连成功）+ worktree 从零环境的包安装
  （uv/npm，日志归档 §5）。
- 零秘密政策：本 README 与全部 canonical 工件不含任何 token/key/
  password/带凭据 URL/env 值（§3 五类模式扫描 0 命中；canonical 内的
  Windows 绝对路径为 gitignored 本地工件路径，非秘密）。
- 本 README 为唯一入库证据文件；canonical 原始证据（gitignored）位于
  worktree `.verify/artifacts/m14-152-current-main-release-evidence/`
  （SHA256SUMS 索引全工件，自不含自哈希；§5）。

## 1. 基点事实（git/远端可复核）

| 事件 | commit | 说明 |
|------|--------|------|
| PR #236 merge = M14-149 执行基点 | `c948931b890fd562dac4f53bd7b5d9add4b0f020` | M14-149 证据绑定头 |
| PR #237 merge（M14-149 证据入库） | `440bc2b9…` | docs-only |
| PR #238 merge（M14-150 状态回填） | `bed280d60…` | docs-only |
| PR #239 merge（M14-151 工具合入）→ **当前 main** | `7438227ce1cd1a8059ef621293ba195e4896ce11` | 本切片精确基点；`c948931..7438227` 实质区间恰为 M14-151 单内容提交 `3852d2c` |
| main push CI run 36219558569 创建（push 事件） | 7438227 | 2026-09-26T05:01:27Z（UTC） |

- 区间**非 docs-only**：M14-151 引入真实 ops 代码（新工具 182 行 +
  evaluator 防线 39 行 + CLI 子命令 110 行）与 37 个测试——本切片
  release-check 的 pytest 计数较 M14-149 实测 **+37（5084→5121）**，
  即 M14-151 测试面增长的精确对账（§2.2，实测吻合、无硬编码预期）；
  alembic head 仍 `0027_audit_chain`（区间无新迁移）；requirements/
  requirements-dev 区间零变更（worktree venv 从零重建语义等价）。
- M14-149 canonical 保留为其时点历史记录不改写（本切片 long-soak
  复用即逐字节读其 canonical §2.4，零修改）；本切片不搬运其 ci-main/
  release-check 产物。M14-83（@ 5829ad9 生产只读门三门）、M14-85
  （@ f47a1e4 backup-restore）、M14-87（@ audit-chain-anchor 门 +
  伴生锚）canonical 按 M14-91 §5 登记路径**原样 staging**（§2.5）。

## 2. 刷新执行记录（ci-main/release-check 为本轮新执行；provider-smoke 为只读核验登记复用；long-soak 为只读校验复用）

### 2.1 ci-main（真实远端 CI，非本地重跑）

```bash
gh api "repos/swsgbl/ai-learning-os/actions/runs?head_sha=7438227ce1cd1a8059ef621293ba195e4896ce11"
gh api "repos/swsgbl/ai-learning-os/actions/runs/36219558569/jobs"
```

（双查询直连成功，未改任何代理配置；两份 stderr 捕获为空文件，
SHA256 `e3b0c442…` = 空文件标准值。任务简报给定的 run 36219558569 /
run_number 601 / 5/5 success 经 raw 响应 live 复核后才绑定。）

- 命中断言（断言脚本复核归档 raw 响应而非仅信任务简报；任一断言
  失败即非零退出、不产出证据）：`workflow_runs` 中 total_count=1、
  event=push、head_branch=main 恰 1 条——run **36219558569**
  （run_number **601**），head_sha `7438227…`，status=completed，
  **conclusion=success**（created 2026-09-26T05:01:27Z / updated
  2026-09-26T05:06:28Z，
  https://github.com/swsgbl/ai-learning-os/actions/runs/36219558569）。
- jobs 断言：`total_count=5` 且全部 conclusion=success——API
  （ruff/pytest/migration）、Docker（compose build+healthy+smoke）、
  Release tools（Python tests）、Android（unit test/lint/assemble）、
  Web（test/typecheck/lint/build）；每 job head_sha == 7438227、job 名
  集合精确匹配且无重复（与 M14-116/122/123/125/146/149 同一五 job
  契约）。
- 按 `_eval_ci_main` 契约**程序化派生** canonical `evidence/ci-main.json`
  （1269 bytes、SHA256 `a21a69ef…`）：断言驱动脚本从 raw 事实拼装 +
  溯源 source 串；未复用 M14-149 的 run 36213290023 或其 raw 文件。
- raw API 响应原样归档：`raw/gh-runs-7438227.json`（12210 bytes）、
  `raw/gh-jobs-36219558569.json`（12334 bytes；哈希见 §5）。

### 2.2 release-check（隔离本地 full 重跑，干净 7438227 执行树）

从零构建全新隔离环境（**本切片重建** worktree `services/api/.venv`：
uv venv --python 3.12（CPython 3.12.14）+ uv pip install -r
requirements.txt -r requirements-dev.txt；根目录 `npm ci --no-audit
--no-fund` 411 packages，node v22.23.2 / npm 12.0.2——npm 提示
`unrs-resolver` postinstall 被 allowScripts 策略阻止，与 M14-92 起各轮
相同的已知提示，本轮 10 门全绿证实无影响）。

**执行树前置核验**（declared-head 诚实性前提）：运行前
`git rev-parse HEAD` == `7438227…` 且 `git status --porcelain` 为空
（tracked-clean；执行日志归档 `logs/pre-release-check-head.log`
留痕）——release-check 的实际执行树即精确干净 7438227，后续 cockpit
`--gate-declared-head release-check=7438227` 声明成立。

隔离编排器（一次性 SQLite → alembic upgrade head → 127.0.0.1 临时
uvicorn → /health 200 → full 10 门 → 原子落盘 → finally 关停临时 API；
环境剥离 AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL，显式
APP_ENV=development / VOICE_MODE=local——不连接任何生产面；新工作区
`artifacts/m14-152-isolated/`）：

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli release-check-isolated \
  --workdir "<worktree>/artifacts/m14-152-isolated" --json
# exit 0
```

- 结果：**all_green=true，10/10 pass**（generated_at
  2026-09-26T05:29:31.627301+00:00，execution_scope=full）——api-lint
  （ruff All checks passed）/ web-lint / web-typecheck / web-build /
  **api-test（pytest 5121 passed, 33 skipped, 1 warning in 252.00s；
  较 M14-149 @ c948931 的 5084 恰 +37——恰为 M14-151 新增 37 测试
  （release-approval-draft 套件 23 + readiness 套件 14）的精确对账，
  实测吻合、非断言预期）** / migration（alembic current == head ==
  0027_audit_chain）/ backup（aios-backup-v1 tables:30 files:1）/
  voice（local 合成 audio/wav 17324 bytes）/ license（api deps 15 /
  web ok / models 7 / sources 6）/ e2e（walkthrough 5 步，1253 ms）。
- `release-check-isolated.json` 逐字节复制改名为 canonical
  `evidence/release-check.json`（2491 bytes，SHA256 `bd57af64…`；文件
  自带 gate 自标识 `release-check`，非手改；复制后 filecmp 逐字节
  比对 IDENTICAL）。

### 2.3 provider-smoke（不重跑、不触碰生产/provider；只读核验 + 哈希锁定复用）

- 复用源：`artifacts/temp/provider-smoke/20260926T0106/provider-smoke.json`
  （主仓，564 bytes、SHA256
  `d589181e078de875faac11052e148d5ff32b0b6f4d7b41fb716b4a584c60a761`、
  generated_at 2026-09-26T01:14:14.270193+00:00）——与任务书给定哈希
  **逐字节精确一致**后才登记。该聚合是 M14-148 恢复轮真实重跑产物，
  M14-149 §2.3 已只读登记为 canonical 源；本轮生产 provider 面无新
  执行（M14-150/M14-151 均未触碰 provider），故同源复用成立。
- **只读核验（prepare 脚本，任一失败即非零退出不登记）**：聚合九键
  schema `provider-smoke-evidence-v1`、gate 自声明 `provider-smoke`、
  拓扑 `voice_mode=local`、三槽位 executed=true 且 result=pass
  （evidence_step local-voice-smoke / search-smoke / llm-smoke）；三份
  输入逐份核验（schema/step/executed/pass/exit_code=0/时间自洽
  started<completed≤聚合时刻）：search-smoke 7496ms、local-voice-smoke
  18442ms、llm-smoke 12213ms；**三份失败 attempt 如实保留未被改写**
  （仍 executed=true/result=fail/exit_code=1）：attempt1-envmiss 60ms、
  attempt2-querymiss 769ms、attempt3-timeout 10483ms——真实执行痕迹，
  本切片零改写。
- 逐字节复制为 canonical `evidence/provider-smoke.json` 后重哈希
  **逐字节一致（564 bytes 同哈希）**。执行记录：
  `logs/prepare-reused-evidence.log`。
- **原始时间边界显式：2026-09-26T01:12:09Z→01:14:14Z**（M14-148
  恢复轮时点证据），距本切片 cockpit 聚合时点（2026-09-26T05:34:03Z，
  §2.5）约 4 小时 20 分，窗口外状态不承诺；易失性由后续刷新切片覆盖。

### 2.4 long-soak（不重跑、不触碰生产/计划任务；只读校验 + 逐字节复用）

- 复用口径与 M14-107/116/122/123/125/146/149 完全一致：源为 M14-149
  canonical（817 bytes），SHA256
  `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec`
  ——与任务书给定哈希**逐字节精确一致**后才复制。
- 复制到本切片 canonical `evidence/long-soak.json` 后**重新哈希 +
  JSON 断言**：逐字节一致（817 bytes 同哈希）；gate 自声明
  `long-soak`、audit_schema_version=2、tool
  `tools/ops/soak_stability_audit.py`、策略四值恰 1440/15/20/500、
  classification=**pass**、reasons=[]。
- 窗口事实（README 如实转述，非本切片重跑）：**24h 窗口
  2026-09-22T02:00:01Z → 2026-09-23T02:00:01Z（跨度恰 1440.0 分钟），
  97 样本全 ok / 0 warn / 0 critical，max_observed_gap_minutes=17.25
  ≤ 20**。**诚实边界：该窗口早于 M14-117 生产切换且执行于 m14-70
  栈**——切换后尚无 24h soak 窗口；本切片按任务边界不重跑、**不制造
  新窗口**，是否需要切换后新 soak 窗口由 supervisor 决策。

### 2.5 evidence-cockpit 聚合（四门刷新/复用后重新汇总：cockpit_ready=true）

在 tracked docs 编辑**之前**、HEAD 仍为干净 `7438227` 时运行。
canonical 源路径逐一取自 M14-91 README §5 / staged-inventory 登记与
M14-149 §2.5 同一路径（不猜测）；staging 前对 6 个既有生产状态源文件
做 sha256 复核——与 M14-91/M14-116/M14-122/M14-123/M14-146/M14-149
登记值逐一 **MATCH（6/6）**：audit-chain-anchor `a8c54c5e…` /
audit-anchor companion `d2bfd877…` / backup-restore `ed0fc5b4…` /
preflight `b3a2be66…` / legacy `83cd62bd…` / draft `e90ae04c…`
（执行记录 `logs/prepare-reused-evidence.log`）。

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli evidence-cockpit \
  --gate-source ci-main="<本切片 canonical>/evidence/ci-main.json" \
  --gate-source release-check="<本切片 canonical>/evidence/release-check.json" \
  --gate-source provider-smoke="<本切片 canonical>/evidence/provider-smoke.json" \
  --gate-source long-soak="<本切片 canonical>/evidence/long-soak.json" \
  --gate-source audit-chain-anchor="<主仓>/.verify/artifacts/m14-87-audit-chain-current-gate/evidence/audit-chain-anchor.json" \
  --gate-source production-preflight="…m14-83-production-read-only-evidence…/evidence/production-preflight.json" \
  --gate-source legacy-papers="…m14-83…/evidence/legacy-papers.json" \
  --gate-source draft-ownership="…m14-83…/evidence/draft-ownership.json" \
  --gate-source backup-restore="…m14-85-backup-restore…/evidence/backup-restore.json" \
  --anchor-companion "<主仓>/.verify/artifacts/m14-87-audit-chain-current-gate/evidence/audit-anchor.jsonl" \
  --gate-declared-head release-check=7438227ce1cd1a8059ef621293ba195e4896ce11 \
  --current-head 7438227ce1cd1a8059ef621293ba195e4896ce11 \
  --staging-dir "<worktree>/artifacts/m14-152-cockpit-staging" \
  --json --output "<worktree>/artifacts/m14-152-cockpit-report.json"
# exit 0（cockpit_ready=true）
```

- **current-head 与 release-check head 显式声明
  `7438227ce1cd1a8059ef621293ba195e4896ce11`**；合法性由 §2.2 前置
  核验支撑（实际执行树即干净 7438227）。
- 结果（报告归档 canonical `cockpit-report.json`，18960 bytes）：
  **evaluator pass=9 / pending=0 / blocked=0 / missing=2 / malformed=0
  / tampered=0**；**cockpit_ready=true、cockpit_blockers=[]、
  required_not_staged=[]、exit 0**。同时 **release_ready=false、
  production_ready=false（恒 false）**：not_pass_required 恰为
  `release-approval`（human-only 从未发生，cockpit 按策略永不接受、
  不计入 blocker——「无技术 blocker」与「不可放行」两件事同时成立），
  not_pass_optional 为 `turn-tls`（optional：本机/LAN 发布形态不需要
  公网 TURN，不计入 fail）。**9 个必需非审批门全部 pass**。
- **code-bound 两门刷新生效**：ci-main stale=**current**（内嵌
  merge_commit=7438227 == current HEAD，declared_head_origin=
  embedded）、release-check stale=**current**（flag 声明 7438227，
  origin=flag）。
- provider-smoke 以当前真实聚合（`d589181e…`）staged 即 pass；long-soak
  staged pass（真实 24h 稳定窗口，§2.4）；production-state 五门 +
  anchor companion 以历史 canonical snapshot 原样 staged、undeclared
  如实呈现不 block（M14-91 §1.1 第 5 条口径；production-preflight
  门的取源说明与 M14-149 §2.5 相同：M14-117 切换后 preflight JSON 缺
  `gate` 自声明不能直接 stage，契约 fail-closed，本切片不手改补字段）。
- **staged 10 文件（9 门 + anchor companion）与 source 逐字节一致**：
  工具内建写后重读断言 + 外部独立复核 **10/10 IDENTICAL**（filecmp
  语义逐字节比较；清单归档 canonical `staged-inventory.txt`，10 条）。
- **M14-151 release-approval-draft 首次真实运行演示（合规边界内）**：
  在 staging 目录（证据目录之外）上执行
  `python -m app.ops.cli release-approval-draft --evidence-dir
  <staging> --output <worktree>/artifacts/m14-152-approval-draft.
  DRAFT.json`——exit 0，DRAFT 生成于 artifacts/ 内（文件名非
  release-approval.json），**staging/证据目录内零写入、零
  release-approval.json 创建**（复核确认）。该 DRAFT 是审批人的哈希
  核对底稿（10 门 sha256 + 必需覆盖缺口 + 人工必填字段清单），**不
  构成审批/签署/放行**；stdout 归档
  `logs/approval-draft-stdout.log`。

## 3. 聚焦验证与静态检查（真实执行结果）

- 聚焦契约测试（cockpit/release/provider/approval-draft 九套件，本切片
  证据链所依赖的工具面——较 M14-149 的八套件新增 M14-151 的
  test_release_approval_draft.py）：

```bash
cd services/api && "<worktree>/.venv/Scripts/python.exe" -m pytest -q \
  tests/test_evidence_cockpit.py tests/test_release_readiness.py tests/test_release_closure_manifest.py \
  tests/test_release_check_isolated.py tests/test_release_checklist.py \
  tests/test_provider_smoke_evidence.py tests/test_provider_smoke_preflight.py tests/test_searxng_local_provider.py \
  tests/test_release_approval_draft.py \
  --basetemp "<scratch>/m14-152-current-main-release-evidence/focused"   # Windows 需先 mkdir -p 预建父目录
# 444 passed, 1 warning in 4.93s
```

（计数 = M14-149 的 407 + M14-151 新增 37 = **444 恰对账**——
  evidence-chain 工具契约面在 c948931..7438227 区间的增长恰为 M14-151
  测试面的实证。basetemp 位于仓库外 scratch，预建父目录后一次通过。）
- `ruff check services/api`：All checks passed（本切片 tracked Python
  零改动——入库文件仅 docs/evidence README 与 ROADMAP 条目，基线
  复核）。
- canonical 完整性：`SHA256SUMS` 全工件索引（LF 文本模式，自不含自
  哈希）+ 终验逐文件复验 OK；canonical JSON `json.load` 解析全通过
  （ci-main/release-check/provider-smoke/long-soak/cockpit-report/
  两 raw）；JSON 契约断言脚本 **35 项全过**（ci-main run_id/
  merge_commit/conclusion、raw runs push@main 唯一/run_number 601、
  raw jobs 5/5 精确集合、release-check all_green 10/10 + pytest
  5121/33 + 对 M14-149 +37 对账 + migration head + e2e 5 步、
  provider-smoke local 拓扑三槽位 pass + 原始时间边界 2026-09-26
  T01:14:14Z、long-soak 策略四值 + 97 ok + max gap 17.25 + span 1440
  + classification pass、cockpit 9 pass/2 missing + not_pass_required
  恰 [release-approval] + not_pass_optional 恰 [turn-tls] +
  release_ready/production_ready false + 两 code-bound 门 current
  （embedded/flag）+ approval not-staged human-only + staged 10/10
  byte_identical）。
- 秘密扫描：canonical 全部工件对 credential 赋值 / OpenAI 风格 key /
  私钥块 / 带凭据 DB URL / URL userinfo 五类模式扫描 **0 命中**
  （报告/清单内的 Windows 绝对路径为 gitignored 本地工件路径，非
  秘密）。
- `git diff --check` 干净（docs-only）；新增行扫描：secret 值 / 本地
  绝对路径（盘符或根路径形态，正反斜杠变体）/ U+FFFD 替换字符对全部
  新增行 **0 命中**（本 README 与 ROADMAP 内路径均为仓库相对或
  gitignored 工件占位符形式）。
- 分支卫生：单 commit 后 tracked-clean（worktree 侧
  `.venv`/`node_modules`/`artifacts/`/`.verify/` 均未入库）。

## 4. 与 M14-149 的差异（同一契约下的净变化）

| 维度 | M14-149 @ c948931 | M14-152 @ 7438227 |
|------|-------------------|--------------------|
| 证据定性 | current-main（7 提交区间非 docs-only：M14-148 恢复 helper+28 测试） | **current-main（区间恰 M14-151 单提交：审批 DRAFT 工具 + evaluator 防线 + CLI 子命令 + 37 测试）** |
| ci-main run | 36213290023（run_number 595） | **36219558569**（run_number 601，本轮新派生） |
| release-check pytest | 5084 passed / 33 skipped（257.74s） | **5121 passed / 33 skipped**（252.00s，+37 = M14-151 测试面精确对账） |
| release-check e2e | 5 步 1210 ms | **5 步 1253 ms** |
| alembic head | 0027_audit_chain | **0027_audit_chain（区间无新迁移）** |
| provider-smoke | 当前真实聚合 `d589181e…`（20260926T0106） | **同源同哈希只读复用（区间零 provider 执行，§2.3 核验）** |
| long-soak | staged pass（同哈希 `d939c652…`） | **staged pass（同哈希只读复用；窗口早于切换且在 m14-70 栈，如实呈现）** |
| evaluator 变更 | 区间零变更 | **区间有变更（M14-151 evaluator 防线 +37 测试覆盖，release-check 实测全绿）** |
| evaluator 结果 | pass=9 / missing=2 | **pass=9 / missing=2（不变）** |
| cockpit blockers | [] | **[]（无技术 blocker）** |
| cockpit_ready / exit | true / 0 | **true / 0（保持）** |
| release_ready / production_ready | false / false | **false / false（不变——release-approval human-only 缺席）** |
| staged 文件 | 10（10/10 IDENTICAL） | **10（同，10/10 IDENTICAL；production-state 六源 6/6 MATCH）** |
| 聚焦契约测试 | 407 passed（八套件） | **444 passed（九套件，+release-approval-draft 37）** |
| 契约断言 | 22 项 | **35 项（断言面按本切片事实重钉）** |
| 交付形态 | 单 local commit，不 push、不开 PR | **单 local commit，不 push、不开 PR（任务书指令）** |

## 5. canonical 证据清单（gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：worktree `.verify/artifacts/m14-152-current-main-release-evidence/`

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/ci-main.json` | 1269 | `a21a69efef6143fe0eec0ec0bf6e8d5d1995072f4883ed68c2c0f5a149feb63b` |
| `evidence/release-check.json` | 2491 | `bd57af6454002d6f3e3667af1ab2e2727c4babb2c49db6bda67e3089e5f52590` |
| `evidence/provider-smoke.json` | 564 | `d589181e078de875faac11052e148d5ff32b0b6f4d7b41fb716b4a584c60a761` |
| `evidence/long-soak.json` | 817 | `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec` |
| `raw/gh-runs-7438227.json` | 12210 | `614a75a6e2f00cce203db9743703ca0fd98843e5dab1e5114fac65c86e2d6708` |
| `raw/gh-jobs-36219558569.json` | 12334 | `30891b20faceb3840d3cb48d7da7d1f44aae90ae5780f4f41d40e354e1c559bc` |
| `cockpit-report.json` | 18960 | `de8d1e765fc656367eb6e1911c636fc7cf75d6d4e63f97930d907b9e4fef4c6e` |
| `staged-inventory.txt` | 1201 | `6f1ba5003c04e287949ab985036759ed9d5525741b31f73ee329ae764ad42e10` |

（另 `logs/` 下执行日志与派生/校验/断言脚本：env-setup / npm-ci /
pre-release-check-head / derive-ci-main 派生脚本 + log /
prepare-reused-evidence 校验脚本 + log / verify-staging 复核脚本 +
log / assert-contracts 断言脚本 + log / secret-scan / focused-tests /
release-check 双流 / cockpit 双流 / approval-draft-stdout +
`raw/*.stderr.txt` 两份空捕获（SHA256 `e3b0c442…` = 空文件标准值）+
`SHA256SUMS` 全工件索引，自不含自哈希；worktree 侧
`artifacts/m14-152-isolated/`、`artifacts/m14-152-cockpit-staging/`、
`artifacts/m14-152-cockpit-report.json`、
`artifacts/m14-152-approval-draft.DRAFT.json` 为生成现场，gitignored，
不入库。）

## 6. 诚实边界与剩余门（不伪称，逐项可执行收口）

1. **cockpit_ready=true ≠ 可发布**：技术面 9 门全 pass、无 blocker，
   但 `release-approval` 是 required 门且 human-only——发布窗口/回滚/
   观察期人工审批从未发生，cockpit 永不接受它，本切片不触碰、不代拟、
   绝不合成；**`release_ready=false` / `production_ready=false`
   不变**，放行决定留 supervisor。M14-151 的 release-approval-draft
   工具（本轮 §2.5 演示）只产出 DRAFT 哈希底稿供审批人核对——
   **DRAFT 不是审批记录**，携带底稿元数据改名为 release-approval.json
   会被 evaluator fail-closed 拒绝（malformed）；真实审批必须由审批人
   从零组装并精确绑定本切片各门哈希。
2. **turn-tls 仍 optional 未 stage**：公网语音发布形态才必需，本机/
   LAN 范围不阻断；`release_ready` 不得解释为公网语音就绪。
3. **provider-smoke pass 是 M14-148 恢复轮的时点证据**
   （2026-09-26T01:12:09Z→01:14:14Z，距本切片聚合约 4 小时 20 分）：
   证明该时点三 provider 冒烟通过，不承诺窗口外状态；区间
   c948931..7438227 零 provider 执行（M14-149/150/151/152 均零接触），
   复用语义有效。本切片零冒烟执行、零 provider/生产接触。
4. **long-soak pass 是 M14-106 运维轮的时点证据且窗口早于生产切换**：
   窗口 2026-09-22→23（m14-70 栈）97 样本全 ok；切换后的 24h soak
   窗口尚不存在，按任务边界不重跑、**不制造新窗口**，是否需要由
   supervisor 决策。
5. **production-state 门是「呈现」不是「重执行」**：staged 的五门 +
   anchor companion 均为各切片 canonical 时点快照（M14-83 @ 5829ad9、
   M14-85 @ f47a1e4、M14-87），本切片零生产触碰约束下不重推导、不
   搬运冒充新执行；其对当前生产状态的语义效力由 M14-117 切换后
   preflight（5/5 pass）与 supervisor 结合生产变更记录判断。
6. **生产当前运行栈未经本切片任何变更**（M14-117 于 2026-09-24 切换
   后运行至今），本切片零调度面触碰、不构成任何部署、不授权任何
   部署；M14-124 登记的审批前镜像与回滚锚材料保持有效，等待
   hash-bound human-only 审批绑定。M14-141 scheduler 未安装。
7. **代码绑定门证据只对其执行时点的树成立**：ci-main/release-check
   现对 7438227 current（= 本切片基点 = current main）；main 再前移
   即再 stale——**只有下一个非 docs-only 合并才触发下一轮刷新**
   （no-infinite-refresh 边界，见开头声明），docs-only 入库不触发。
8. **GitHub Actions run 有平台保留期**（默认 90 天）；到期后按 §2.1
   命令重取即可。
