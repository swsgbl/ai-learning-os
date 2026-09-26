# M14-149：current-main 发布证据刷新（PR #236 后；ci-main + release-check 真实重跑 + provider-smoke 升级为当前真实聚合的只读登记 + cockpit 零 blocker 聚合）

- 切片：分支 `ops/m14-149-current-main-release-evidence`（独立 worktree
  `m14-149-current-main-release-evidence`，基于 main
  `c948931b890fd562dac4f53bd7b5d9add4b0f020`（PR #236 merge = M14-148
  provider readiness recovery Round 1 合入，精确基点 = 当前 origin/main，
  起点即 tracked-clean 核验通过），单 local commit，**不 push、不开 PR
  （任务书指令）**。
- 动因与定性：M14-146 的证据绑定执行基点 `239b881`，其后
  `239b881..c948931` 共 7 个提交（PR #234/#235/#236），区间**含真实代码
  与测试面变更**——M14-148 provider readiness recovery Round 1（新增
  `tools/ops/searxng_egress_recovery.py` 336 行幂等 fail-closed 零子进程
  恢复 helper、`infra/env.production-recovery.example` 代理三槽位模板
  文档、compose 注释双载、新增 `test_searxng_egress_recovery.py` 28 项
  离线契约测试 + `test_compose_profiles.py` +2 渲染回归），另有
  M14-146/M14-142-Stage2 两轮 docs-only 证据入库。代码绑定门相对当前
  main 漂移**不再 docs-only**——本切片按 supervisor 指令在**当前 HEAD
  `c948931b`** 上真实重执行 ci-main 与 release-check 两门；**绝不复用/
  搬运 M14-146 的 ci-main/release-check 产物或计数**（run 36168686272
  与 239b881 上的 release-check 结果均不复用）。
- 目标：沿 M14-146/M14-125 既有契约刷新 current main 的发布证据链。
  **provider-smoke 本轮升级为只读登记当前真实聚合**：M14-146 及之前
  各轮复用的 M14-117 切换后聚合（`029ee84f…`，2026-09-23T23:29:35Z）
  已被更新的真实重跑产物取代——`artifacts/temp/provider-smoke/
  20260926T0106/`（M14-148 恢复轮按其 §6 序列真实执行的三步冒烟：
  search egress 恢复后 7496ms results 首过、local-voice 18442ms、
  llm 12213ms，聚合 generated_at 2026-09-26T01:14:14Z，SHA256
  `d589181e…`，含三份如实保留的失败 attempt）。本切片对该聚合**零
  改写、零拼装、零 provider 接触**，只做 schema/哈希/时间边界只读
  核验后逐字节复制登记；**long-soak 只读复用同哈希 `d939c652…`**
  （M14-106 运维轮 24h 窗口审计，M14-107/116/122/123/125/146 同款
  复用，原窗口时间边界如实呈现、不制造新窗口）。绝不手改 gate
  JSON、绝不合成 pass、绝不重跑 provider/生产。**
  `release_ready=false` / `production_ready=false` 全程不变**——
  release-approval 是 human-only 门、从未发生，cockpit 按策略永不接受
  它（not-staged 呈现、不计 blocker）；turn-tls optional 不阻断。
- 零生产触碰（本切片全程）：零容器启停/重建、零计划任务操作、零
  DB/MinIO/语音引擎接触、零生产日志写入、零 secrets/env 读取、零
  soak 历史重跑/锚定写入、零发布审批接触、零部署、零 Ollama/FunASR/
  CosyVoice/SearXNG/代理/CC Switch 生命周期变更、不安装 M14-141
  scheduler。唯一网络访问是 GitHub 只读 API（`gh api`，已认证账号，
  双查询直连成功、零代理配置变更）+ worktree 从零环境的包安装
  （uv/npm，日志归档 §5）。
- 零秘密政策：本 README 与全部 canonical 工件不含任何 token/key/
  password/带凭据 URL/env 值（§3 秘密扫描 0 命中；canonical 内的
  Windows 绝对路径为 gitignored 本地工件路径，非秘密）。
- 本 README 为唯一入库证据文件；canonical 原始证据（gitignored）位于
  worktree `.verify/artifacts/m14-149-current-main-release-evidence/`
  （8 份核心工件 + 执行日志/脚本，SHA256SUMS 索引 28 文件，自不含
  自哈希；§5）。

## 1. 基点事实（git/远端可复核）

| 事件 | commit | 说明 |
|------|--------|------|
| PR #233 merge = M14-146 执行基点 | `239b881b02514f50d8ad6177b61bcb84fb908ac5` | M14-146 证据绑定头 |
| PR #234 merge（M14-146 证据入库） | `bf28664…` | docs-only |
| PR #235 merge（M14-142 Stage 2 收口） | `a9b414b…` | docs-only（含 harmony 分支 merge `306f72a`） |
| PR #236 merge（M14-148 Round 1）→ **当前 main** | `c948931b890fd562dac4f53bd7b5d9add4b0f020` | 本切片精确基点；`239b881..c948931` 共 7 提交 |
| main push CI run 36213290023 创建（push 事件） | c948931b | 2026-09-26T02:57:11Z（UTC） |

- 区间**非 docs-only**：`services/api/tests/` 新增
  `test_searxng_egress_recovery.py`（28 项）+ `test_compose_profiles.py`
  +2 + `test_rc_smoke_rehearsal.py` 微调——本切片 release-check 的
  pytest 计数较 M14-146 实测 **+30（5054→5084）**，即真实测试面增长的
  精确对账（§2.2，实测吻合、无硬编码）；
  `services/api/app/ops/release_readiness.py` 与 `app/ops/cli.py` 在
  区间**零变更**（`_eval_ci_main` 契约与 M14-146 相同），alembic head
  仍 `0027_audit_chain`（区间无新迁移）。
- M14-146 canonical 保留为其时点历史记录不改写（本切片 long-soak
  复用即逐字节读其 canonical §2.4，零修改）；本切片不搬运其
  ci-main/release-check 产物。M14-83（@ 5829ad9 生产只读门三门）、
  M14-85（@ f47a1e4 backup-restore）、M14-87（@ audit-chain-anchor 门
  + 伴生锚）canonical 按 M14-91 §5 登记路径**原样 staging**（§2.5）。
- **provider-smoke 复用源变更（本轮关键差异）**：M14-146 复用的
  M14-117 切换后聚合（`029ee84f…`，2026-09-23T23:29:35Z）之后，
  生产 provider 面发生 M14-147 blocked → M14-148 诊断/恢复路径 →
  恢复轮真实重跑（见下）；`artifacts/temp/provider-smoke/` 当前唯一
  执行现场即 `20260926T0106/` 目录（7 份文件：3 输入 + 聚合 + 3 失败
  attempt，时间线 2026-09-26T01:07:26Z→01:14:14Z）。按任务书指令
  「优先只读核验当前真实聚合」，本切片登记该聚合为新 canonical
  provider-smoke 源（`d589181e…`，564 bytes）——它晚于旧聚合约 26
  小时、晚于 search egress 恢复，是对当前生产状态对应的最新真实
  pass 证据；旧聚合不再 stage（新 run 事实取代，非改写历史）。
- long-soak 复用口径与 M14-107/116/122/123/125/146 完全相同：canonical
  817 bytes、SHA256
  `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec`
  （= M14-106 运维轮 supervisor 复核的 24h 窗口 pass 审计，窗口
  2026-09-22T02:00:01Z → 2026-09-23T02:00:01Z），只读校验后逐字节
  stage（§2.4）。**诚实提示：该窗口早于 M14-117 生产切换且执行于
  m14-70 旧栈**——切换后不存在新的 24h soak 窗口；切换后稳定性证据
  是 M14-117 §6.2 监控轮（34 ok/0 warn/0 critical）、M14-118 只读
  watch、M14-145 语音恢复验证回合与 M14-148 恢复轮 provider 冒烟，
  不是新的 soak。按任务边界本切片不重跑 soak、不制造新窗口。

## 2. 刷新执行记录（ci-main/release-check 为本轮新执行；provider-smoke 为只读核验登记；long-soak 为只读校验复用）

### 2.1 ci-main（真实远端 CI，非本地重跑）

```bash
gh api "repos/swsgbl/ai-learning-os/actions/runs?head_sha=c948931b890fd562dac4f53bd7b5d9add4b0f020"
gh api "repos/swsgbl/ai-learning-os/actions/runs/36213290023/jobs"
```

（双查询直连成功，未改任何代理配置；stderr 捕获为空文件。）

- 命中断言（断言脚本复核归档 raw 响应而非仅信任务简报；任一断言
  失败即非零退出、不产出证据）：`workflow_runs` 中 total_count=1、
  event=push、head_branch=main 恰 1 条——run **36213290023**
  （run_number **595**），head_sha `c948931b…`，status=completed，
  **conclusion=success**（created 2026-09-26T02:57:11Z / updated
  2026-09-26T03:02:19Z，
  https://github.com/swsgbl/ai-learning-os/actions/runs/36213290023）。
- jobs 断言：`total_count=5` 且全部 conclusion=success——API
  （ruff/pytest/migration）、Docker（compose build+healthy+smoke）、
  Release tools（Python tests）、Android（unit test/lint/assemble）、
  Web（test/typecheck/lint/build）；每 job head_sha == c948931b、job 名
  集合精确匹配且无重复（与 M14-116/122/123/125/146 同一五 job 契约）。
- 按 `_eval_ci_main` 契约（`services/api/app/ops/release_readiness.py`，
  区间零变更）**程序化派生** canonical `evidence/ci-main.json`
  （1201 bytes、SHA256 `da80ae9c…`）：断言驱动脚本从 raw 事实拼装 +
  溯源 source 串；未复用 M14-146 的 run 36168686272 或其 raw 文件。
- raw API 响应原样归档：`raw/gh-runs-c948931.json`（12208 bytes）、
  `raw/gh-jobs-36213290023.json`（12334 bytes；哈希见 §5；两份 stderr
  捕获为空文件，SHA256 `e3b0c442…` = 空文件标准值）。

### 2.2 release-check（隔离本地 full 重跑，干净 c948931b 执行树）

从零构建全新隔离环境（worktree `.venv`，不复用任何前驱切片产物）：

```bash
uv venv .venv --python 3.12          # CPython 3.12.14
uv pip install -r services/api/requirements.txt -r services/api/requirements-dev.txt   # 59 packages
npm ci --no-audit --no-fund          # 411 packages（node v22.23.2 / npm 12.0.2）
```

（npm 提示 `unrs-resolver` postinstall 被 allowScripts 策略阻止——与
M14-92/94/97/105/107/116/122/123/125/146 相同的已知提示，如实记录；
该脚本产物非门禁依赖，本轮 10 门全绿证实无影响。）

**执行树前置核验**（declared-head 诚实性前提）：运行前
`git rev-parse HEAD` == `c948931b…` 且 `git status --porcelain` 为空
（tracked-clean，仅 gitignored `.verify/`/`artifacts/` 增量，执行日志
归档 `logs/pre-release-check-head.log` 留痕）——release-check 的实际
执行树即精确干净 c948931b，后续 cockpit
`--gate-declared-head release-check=c948931b` 声明成立。

隔离编排器（一次性 SQLite → alembic upgrade head → 127.0.0.1 临时
uvicorn → /health 就绪 → full 10 门 → 原子落盘 → finally 关停临时
API；环境剥离 AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL，显式
APP_ENV=development / VOICE_MODE=local——不连接任何生产面；新工作区
`artifacts/m14-149-isolated/`）：

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli release-check-isolated \
  --workdir "<worktree>/artifacts/m14-149-isolated" --json
# exit 0
```

- 结果：**all_green=true，10/10 pass**（generated_at
  2026-09-26T03:19:47.570417+00:00，execution_scope=full）——api-lint
  （ruff All checks passed）/ web-lint / web-typecheck / web-build /
  **api-test（pytest 5084 passed, 33 skipped, 1 warning in 257.74s；
  较 M14-146 @ 239b881 的 5054 恰 +30——239b881..c948931 区间
  searxng egress recovery（28）+ compose profiles（2）真实测试面增长
  的精确对账，实测吻合、无硬编码）** / migration（alembic current ==
  head == 0027_audit_chain）/ backup（aios-backup-v1 tables:30 files:1）/
  voice（local 合成 audio/wav 17324 bytes）/ license（api deps 15 /
  web ok / models 7 / sources 6）/ e2e（walkthrough 5 步，1210 ms）。
- `release-check-isolated.json` 逐字节复制改名为 canonical
  `evidence/release-check.json`（2491 bytes，SHA256 `2de5cbe4…`；文件
  自带 gate 自标识 `release-check`，非手改；复制后 filecmp 逐字节
  比对 IDENTICAL）。

### 2.3 provider-smoke（不重跑、不触碰生产/provider；只读核验当前真实聚合 + 哈希锁定登记）

- 登记源：`artifacts/temp/provider-smoke/20260926T0106/provider-smoke.json`
  （564 bytes、SHA256
  `d589181e078de875faac11052e148d5ff32b0b6f4d7b41fb716b4a584c60a761`、
  generated_at 2026-09-26T01:14:14.270193+00:00）——M14-148 恢复轮按
  其 README §6 建议序列（enforce 恢复 search egress → 复核 →
  preflight → export/aggregate）真实重跑的三步冒烟聚合产物；执行与
  恢复机制属该恢复轮（本切片范围外），本切片仅只读登记聚合事实。
- **只读核验（prepare 脚本，任一失败即非零退出不登记）**：
  - 聚合九键 schema `provider-smoke-evidence-v1`、gate 自声明
    `provider-smoke`、拓扑 `voice_mode=local`、voice/search/llm 三槽位
    executed=true 且 result=pass（evidence_step local-voice-smoke /
    search-smoke / llm-smoke）；
  - 三份输入逐份核验（schema/step/executed/pass/exit_code=0/时间自洽
    started<completed≤聚合时刻）：search-smoke 2026-09-26T01:12:09Z→
    01:12:17Z（7496 ms，`ecf2a4c4…`）、local-voice-smoke
    01:13:01Z→01:13:20Z（18442 ms，`2b67d540…`）、llm-smoke
    01:13:44Z→01:13:56Z（12213 ms，`a35e0674…`）；
  - **三份失败 attempt 如实保留且未被改写**（核验仍为
    executed=true/result=fail/exit_code=1，非 pass）：attempt1-envmiss
    01:07:26Z（60 ms）、attempt2-querymiss 01:11:26Z（769 ms）、
    attempt3-timeout 01:11:44Z（10483 ms）——真实执行痕迹（含第一次
    成功前的三次失败），本切片零改写。
- 逐字节复制为 canonical `evidence/provider-smoke.json` 后重哈希
  **逐字节一致（564 bytes 同哈希）**。执行记录：
  `logs/prepare-reused-evidence.log`。
- provider 冒烟的易失性（时点证据不承诺未来状态）按 M14-115 §7 /
  M14-116 §6.3 / M14-122/123/125/146 §2.3 口径保持——**原始时间边界
  显式：2026-09-26T01:12:09Z→01:14:14Z，距本切片 cockpit 聚合时点
  （2026-09-26T03:22:15Z）约 2 小时 08 分，窗口外状态不承诺**。该
  聚合晚于 M14-146 所复用 M14-117 聚合（2026-09-23T23:29:35Z）约
  26 小时，且执行于 search egress 恢复之后，是对当前生产 provider
  面的最新真实 pass 证据（较旧聚合更新，取代其成为 canonical 源；
  旧聚合作为历史时点记录不被改写）。

### 2.4 long-soak（不重跑、不触碰生产/计划任务；只读校验 + 逐字节复制）

- 复用口径与 M14-107 §2.3 / M14-116 §2.4 / M14-122 §2.4 /
  M14-123 §2.4 / M14-125 §2.4 / M14-146 §2.4 完全一致：canonical
  817 bytes、SHA256
  `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec`
  （与 M14-146 §5 登记值一致，大小写不敏感）——即 M14-106 运维轮
  supervisor 复核的真实 24h 稳定窗口审计（M14-107 首次 stage、
  M14-116/122/123/125/146 复用的同一证据；本切片直接读 M14-146
  canonical 复核后逐字节复制）。
- 复制到本切片 canonical `evidence/long-soak.json` 后**重新哈希 +
  JSON 断言**：逐字节一致（817 bytes 同哈希）；gate 自声明
  `long-soak`、audit_schema_version=2、tool
  `tools/ops/soak_stability_audit.py`、策略四值恰 1440/15/20/500（与
  `_eval_long_soak` v2 契约一致）、classification=**pass**、
  reasons=[]。
- 窗口事实（README 如实转述，非本切片重跑）：**24h 窗口
  2026-09-22T02:00:01Z → 2026-09-23T02:00:01Z（跨度恰 1440.0 分钟），
  97 样本全 ok / 0 warn / 0 critical（window_non_ok_count=0），
  max_observed_gap_minutes=17.25 ≤ 20 上限**，selected_row_count=97
  （恰为 1440/15+1 闭区间最小样本数）。
- **诚实边界：该窗口早于 M14-117 生产切换且执行于 m14-70 栈**，
  切换后尚无 24h soak 窗口；本切片按任务边界不重跑 soak、**不制造
  新窗口**，cockpit 按 evaluator 契约消费该时点 pass 证据，其窗口外
  效力由 supervisor 结合切换后监控/巡检证据（M14-117 §6.2、
  M14-118、M14-145、M14-148 恢复轮冒烟）判断。本切片零 soak 接触：
  未运行 `soak_stability_audit.py`、未触碰 history/锚定/计划任务/
  监控管道。

### 2.5 evidence-cockpit 聚合（四门刷新/登记/复用后重新汇总：cockpit_ready=true 保持）

在 tracked docs 编辑**之前**、HEAD 仍为干净 `c948931b` 时运行。
canonical 源路径逐一取自 M14-91 README §5 / staged-inventory 登记与
M14-146 §2.5 同一路径（不猜测）；staging 前先对 6 个既有生产状态源
文件做 sha256 复核——与 M14-91/M14-116/M14-122/M14-123/M14-146
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
  --gate-declared-head release-check=c948931b890fd562dac4f53bd7b5d9add4b0f020 \
  --current-head c948931b890fd562dac4f53bd7b5d9add4b0f020 \
  --staging-dir "<worktree>/artifacts/m14-149-cockpit-staging" \
  --json --output "<worktree>/artifacts/m14-149-cockpit-report.json"
# exit 0（cockpit_ready=true）
```

- **current-head 与 release-check head 显式声明
  `c948931b890fd562dac4f53bd7b5d9add4b0f020`**；`--gate-declared-head
  release-check` 的合法性由 §2.2 前置核验支撑（实际执行树即干净
  c948931b；ci-main 则由文件内嵌 merge_commit==current HEAD 自动
  current）。
- 结果（报告归档 canonical `cockpit-report.json`，generated_at
  2026-09-26T03:22:15.050373+00:00）：**evaluator pass=9 / pending=0
  / blocked=0 / missing=2 / malformed=0 / tampered=0**；
  **cockpit_ready=true、cockpit_blockers=[]、required_not_staged=[]、
  exit 0**。同时 **release_ready=false、production_ready=false（恒
  false）**：not_pass_required 恰为 `release-approval`（human-only
  从未发生，cockpit 按策略永不接受、不计入 blocker——「无技术
  blocker」与「不可放行」两件事同时成立），not_pass_optional 为
  `turn-tls`（optional：本机/LAN 发布形态不需要公网 TURN，不计入
  fail）。
- **code-bound 两门刷新生效**：ci-main stale=**current**（内嵌
  merge_commit=c948931b == current HEAD）、release-check stale=
  **current**（flag 声明 c948931b）。
- **provider-smoke 以当前真实聚合（`d589181e…`）staged 即 pass**（经
  `_eval_provider_smoke` 全语义校验）；long-soak staged pass（真实
  24h 稳定窗口，§2.4）。production-state 五门（audit-chain-anchor /
  production-preflight / legacy-papers / draft-ownership /
  backup-restore）+ anchor companion 以历史 canonical snapshot 原样
  staged、undeclared 如实呈现不 block（M14-91 §1.1 第 5 条口径）；
  release-approval「按策略永不接受，非 blocker」、turn-tls「optional
  不阻断」摘要显式区分。
- **production-preflight 门的取源说明（诚实记录，与
  M14-122/123/125/146 同）**：M14-117 切换后 preflight（5/5 pass，
  2026-09-23T23:14:45Z）是更新的生产状态事实，但其 JSON**不含
  evaluator 契约要求的 `gate` 自声明字段**，stage 它将触发
  MalformedEvidence fail-closed——本切片绝不手改补字段，故该门继续
  stage M14-83 注册 canonical（`b3a2be66…`，6/6 哈希复核通过），
  M14-117 切换后 preflight 作为「staged 各门语义在切换后仍成立」的
  支撑事实在 README 引用。
- **staged 10 文件（9 门 + anchor companion）与 source 逐字节一致**：
  工具内建写后重读断言 + 外部独立复核 **10/10 IDENTICAL**（filecmp
  语义逐字节比较）；6 个既有文件哈希与 M14-91/M14-146
  staged-inventory 登记值交叉一致、本切片 4 个新 stage 文件（ci-main
  `da80ae9c…` 1201 bytes / release-check `2de5cbe4…` 2491 bytes /
  provider-smoke `d589181e…` 564 bytes / long-soak `d939c652…` 817
  bytes）与 canonical 实际哈希一致——来源零改动 + 逐字节 staging
  端到端实证（清单归档 canonical `staged-inventory.txt`）。

## 3. 聚焦验证与静态检查（真实执行结果）

- 聚焦契约测试（cockpit/release/provider 八套件，本切片证据链所依赖
  的工具面）：

```bash
cd services/api && "<worktree>/.venv/Scripts/python.exe" -m pytest -q \
  tests/test_evidence_cockpit.py tests/test_release_readiness.py tests/test_release_closure_manifest.py \
  tests/test_release_check_isolated.py tests/test_release_checklist.py \
  tests/test_provider_smoke_evidence.py tests/test_provider_smoke_preflight.py tests/test_searxng_local_provider.py \
  --basetemp "<scratch>/m14-149-current-main-release-evidence/focused"   # Windows 需先 mkdir -p 预建父目录（任务书指定父目录已预建）
# 407 passed, 1 warning in 6.11s
```

（Windows 已知形态：pytest `--basetemp` 不创建缺失父目录链，本切片
按 M14-116/122/123/146 教训**先 `mkdir -p` 预建父目录**，一次通过。
计数与 M14-146 的 407 恰一致——evidence-chain 工具契约面在
239b881..c948931 区间零变更的实证。）
- `ruff check services/api`：All checks passed（tracked Python 零改动，
  基线复核；本切片无 Python 源码变更——入库文件仅 docs/evidence
  README 与四台账/DEVELOPMENT 段落）。
- canonical 完整性：`SHA256SUMS` 逐文件复验 **28/28 OK**（本切片索引
  为 LF 行尾文本模式，自不含自哈希）；canonical JSON `json.load`
  解析全通过（ci-main/release-check/provider-smoke/long-soak/
  cockpit-report/两 raw/cockpit-stdout）；JSON 契约断言脚本 **22 项
  全过**（ci-main run_id/merge_commit/conclusion、raw runs push@main
  唯一/run_number 595、raw jobs 5/5 精确集合、release-check
  all_green 10/10 + pytest 5084/33 + 对 M14-146 增量 +30 对账、
  provider-smoke local 拓扑三槽位 pass + 原始时间戳边界 2026-09-26
  T01:14:14Z、long-soak 策略四值 + 97 ok + max gap 17.25 + span
  1440、cockpit 9 pass/2 missing + approval not-staged +
  release_ready/production_ready false + 两 code-bound 门 current、
  staged-inventory 10/10 IDENTICAL、秘密扫描 0 命中）。
- 秘密扫描：canonical 28 文件对 credential 赋值 / OpenAI 风格 key /
  私钥块 / 带凭据 DB URL / URL userinfo 五类模式扫描 **0 命中**
  （报告/清单内的 Windows 绝对路径为 gitignored 本地工件路径，非
  秘密）。
- `git diff --check` 干净（docs-only）；新增行扫描：secret 值 / 本地
  绝对路径（盘符或根路径形态，正反斜杠变体）/ U+FFFD 替换字符对全部
  新增行 **0 命中**（本 README 与台账内路径均为仓库相对或占位符
  形式）。
- 分支卫生：单 commit 后 tracked-clean（worktree 侧
  `.venv`/`node_modules`/`artifacts/`/`.verify/` 均 gitignored 未
  入库）。

## 4. 与 M14-146 的差异（同一契约下的净变化）

| 维度 | M14-146 @ 239b881 | M14-149 @ c948931b |
|------|--------------------|--------------------|
| 证据定性 | current-main（47 提交真实代码/测试面增长后刷新） | **current-main（7 提交区间非 docs-only：M14-148 恢复 helper+28 测试）** |
| ci-main run | 36168686272（run_number 588） | **36213290023**（run_number 595，本轮新派生） |
| release-check pytest | 5054 passed / 33 skipped（296.14s） | **5084 passed / 33 skipped**（257.74s，+30 = searxng egress recovery 28 + compose profiles 2 对账） |
| release-check e2e | 5 步 1073 ms | **5 步 1210 ms** |
| alembic head | 0027_audit_chain | **0027_audit_chain（区间无新迁移）** |
| provider-smoke 复用源 | M14-117 切换后聚合（`029ee84f…`，2026-09-23T23:29:35Z） | **当前真实聚合 20260926T0106（`d589181e…`，2026-09-26T01:14:14Z，M14-148 恢复轮真实重跑；三失败 attempt 如实保留核验）** |
| long-soak | staged pass（同哈希 `d939c652…`） | **staged pass（同哈希只读复用；窗口早于切换且在 m14-70 栈，如实呈现）** |
| evaluator | pass=9 / missing=2 | **pass=9 / missing=2（不变）** |
| cockpit blockers | [] | **[]（无技术 blocker）** |
| cockpit_ready / exit | true / 0 | **true / 0（保持）** |
| release_ready / production_ready | false / false | **false / false（不变——release-approval human-only 缺席）** |
| production-state 五门 + anchor | 原样 staged（同源同哈希） | **原样 staged（同源同哈希，6/6 MATCH 复核）** |
| staged 文件 | 10（9 门 + anchor companion） | **10（同，10/10 IDENTICAL）** |
| 聚焦契约测试 | 407 passed | **407 passed（工具契约面区间零变更实证）** |
| canonical 索引 | SHA256SUMS 25 文件 | **SHA256SUMS 28 文件** |
| 交付形态 | 单 local commit，不 push、不开 PR | **单 local commit，不 push、不开 PR（任务书指令）** |

## 5. canonical 证据清单（gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：worktree `.verify/artifacts/m14-149-current-main-release-evidence/`

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/ci-main.json` | 1201 | `da80ae9c384591ca36f74a69fc3426c771fbb0e5ea15da99b98dc42964dc9a64` |
| `evidence/release-check.json` | 2491 | `2de5cbe46875402007d6d72aedf32c6ed476d8e277eaef8e5cda6f1117c46316` |
| `evidence/provider-smoke.json` | 564 | `d589181e078de875faac11052e148d5ff32b0b6f4d7b41fb716b4a584c60a761` |
| `evidence/long-soak.json` | 817 | `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec` |
| `raw/gh-runs-c948931.json` | 12208 | `8fd4f8d95738f4f380be7d65c069c9b86b2bb674d563df25f085c4c180856b90` |
| `raw/gh-jobs-36213290023.json` | 12334 | `6b53e3e8af920ce2a4574f5eb3d4999fc5c2a2ab86fcd5bbd4153d4d06462e41` |
| `cockpit-report.json` | 19002 | `011af04da4f98f4791df13d7aa237dacc06572492539de5e45e2919f6bbc3a11` |
| `staged-inventory.txt` | 2779 | `b71dd49b414da97dd009740411599354b7a0e544bdc9cc1ebf64481b1f210791` |

（另 `logs/` 下执行日志与断言/派生/校验脚本 18 份（env-setup/
npm-ci/pre-release-check-head + derive-ci-main 派生脚本/
prepare-reused-evidence 校验脚本/verify-staging 复核脚本/
assert-contracts 断言脚本 + scan-added-lines 新增行扫描脚本 + 各自 log + focused-tests + release-check
双流 + cockpit 双流）+ `raw/*.stderr.txt` 两份空捕获（SHA256
`e3b0c442…` = 空文件标准值）+ `SHA256SUMS` 28 文件索引，自不含自
哈希；worktree 侧 `artifacts/m14-149-isolated/`、
`artifacts/m14-149-cockpit-staging/`、
`artifacts/m14-149-cockpit-report.json` 为生成现场，gitignored，
不入库。）

## 6. 诚实边界与剩余门（不伪称，逐项可执行收口）

1. **cockpit_ready=true ≠ 可发布**：技术面 9 门全 pass、无 blocker，
   但 `release-approval` 是 required 门且 human-only——发布窗口/回滚/
   观察期人工审批从未发生，cockpit 永不接受它（M14-91 §1.1 第 6 条），
   本切片不触碰、不代拟、绝不合成；**`release_ready=false` /
   `production_ready=false` 不变**，放行决定留 supervisor。M14-124
   审批轮的 `release-readiness` 10/10（2026-09-24 时点）是历史事实，
   不被本切片改写或延伸。
2. **turn-tls 仍 optional 未 stage**：公网语音发布形态才必需，本机/
   LAN 范围不阻断；`release_ready` 不得解释为公网语音就绪。
3. **provider-smoke pass 是 M14-148 恢复轮的时点证据**
   （2026-09-26T01:12:09Z→01:14:14Z 三步冒烟聚合，search egress
   恢复后、当前生产栈上真实执行，含三份如实保留的失败 attempt）：
   证明该时点三 provider 冒烟通过，不承诺窗口外状态（距本切片
   cockpit 聚合约 2 小时 08 分）；易失性由后续刷新切片覆盖。本切片
   零冒烟执行、零 provider/生产接触；恢复动作（enforce/recreate/
   模型驻留）的执行与授权属 M14-148 恢复轮，本切片不臆断其机制、
   不重复其动作。
4. **long-soak pass 是 M14-106 运维轮的时点证据且窗口早于生产切换**：
   窗口 2026-09-22T02:00:01Z → 2026-09-23T02:00:01Z（m14-70 栈）内
   97 样本全 ok——证明该窗口稳定性，不承诺窗口外、也不承诺切换后
   栈稳定性；切换后的 24h soak 窗口尚不存在，按任务边界本切片不
   重跑、**不制造新窗口**，是否需要切换后新 soak 窗口由 supervisor
   决策（切换后稳定性佐证：M14-117 §6.2 监控轮 34 ok、M14-118 只读
   watch、M14-145 语音恢复验证回合、M14-148 恢复轮 provider 冒烟）。
5. **production-state 门是「呈现」不是「重执行」**：staged 的五门 +
   anchor companion 均为各切片 canonical 时点快照（M14-83 @
   5829ad9、M14-85 @ f47a1e4、M14-87），本切片零生产触碰约束下不
   重推导、不搬运冒充新执行；其对当前生产状态的语义效力由
   M14-117 切换后 preflight（5/5 pass，§2.5）与 supervisor 结合
   生产变更记录判断，cockpit 提供声明通道显式表达。M14-117 的
   preflight JSON 因缺 `gate` 自声明不能直接作为门证据 stage（契约
   fail-closed，非本切片补改）。
6. **生产当前运行栈未经本切片任何变更**（M14-117 于 2026-09-24
   切换后运行至今；M14-145 恢复回合佐证 core compose 6/6 healthy
   且未 recreate；M14-148 恢复轮的 searxng egress 恢复与 LLM 模型
   驻留属该轮动作），本切片零调度面触碰、不构成任何部署；不授权
   任何部署。M14-124 登记的 `m14-124-production` 两枚审批前镜像与
   回滚锚材料保持有效，等待 hash-bound human-only 审批绑定。
   M14-141 scheduler 未安装（任务书边界，本切片不安装）。
7. **代码绑定门证据只对其执行时点的树成立**：ci-main/release-check
   现对 c948931b current（= 本切片基点 = current main）；main 再前移
   即再 stale，需下一个刷新切片真实重推导。
8. **GitHub Actions run 有平台保留期**（默认 90 天）；到期后按 §2.1
   命令重取即可。
