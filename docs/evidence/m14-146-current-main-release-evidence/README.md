# M14-146：current-main 发布证据刷新（PR #233 后；ci-main + release-check 真实重跑 + provider-smoke/long-soak 契约内只读复用 + cockpit 零 blocker 聚合）

- 切片：分支 `ops/m14-146-current-main-release-evidence`（独立 worktree
  `m14-146-current-main-release-evidence`，基于 main
  `239b881b02514f50d8ad6177b61bcb84fb908ac5`（PR #233 merge = M14-142
  冒烟预热 R4 合入，精确基点 = 当前 HEAD，起点即 tracked-clean 核验
  通过），单 local commit，**不 push、不开 PR（任务书指令）**。
- 动因与定性：M14-125 的证据绑定执行基点 `e14be369`，其后
  `e14be369..239b881` 共 47 个提交（PR #215–#233），区间**含真实代码与
  测试面变更**——M14-129 漂移监视调度管理器、M14-133 历史审计工具、
  M14-135 告警分发、M14-136/M14-140 运行时闭环测试、M14-141 告警
  调度 readiness、M14-142 后端冒烟重复周期包装器 + 冒烟预热加固、
  M14-144 alert secret 模板与守卫测试（`services/api/tests/` 新增 8 个
  drift-watch 族测试文件），另有多轮 docs-only 证据回填
  （M14-128/130/131/132/134/138/139/145）。代码绑定门相对当前 main
  漂移**不再 docs-only**——本切片按 supervisor 指令在**当前 HEAD
  `239b881`** 上真实重执行 ci-main 与 release-check 两门；**绝不复用/
  搬运 M14-125 的 ci-main/release-check 产物或计数**
  （run 35992664886 与 e14be369 上的 release-check 结果均不复用）。
- 目标：沿 M14-125 既有契约刷新 current main 的发布证据链。
  **provider-smoke 只读复用 M14-117 生产切换后的真实三步冒烟聚合**
  （与 M14-122/M14-123/M14-125 同源、同一 SHA256——生产仍运行
  `m14-117-production` 栈，本切片零切换零 provider 接触，无新冒烟窗口
  指令）；**long-soak 只读复用同哈希 `d939c652…`**
  （M14-106 运维轮 24h 窗口审计，M14-107/M14-116/M14-122/M14-123/
  M14-125 同款复用，原窗口时间边界如实呈现）。绝不手改 gate JSON、
  绝不合成 pass、绝不搬运与源哈希不符的旧 JSON 冒充 current、绝不重跑
  provider/生产、绝不为切换后栈合成新 24h soak。
  **`release_ready=false` / `production_ready=false` 全程不变**——
  release-approval 是 human-only 门、从未发生，cockpit 按策略永不接受
  它（not-staged 呈现、不计 blocker）；turn-tls optional 不阻断。
- 零生产触碰（本切片全程）：零容器启停/重建、零计划任务操作、零
  DB/MinIO/语音引擎接触、零生产日志写入、零 secrets/env 读取、零
  soak 历史重跑/锚定写入、零发布审批接触、零部署、零 Ollama/FunASR/
  CosyVoice/SearXNG/代理/CC Switch 生命周期变更。唯一网络访问是
  GitHub 只读 API（`gh api`，已认证账号，双查询均直连成功、零代理
  配置变更）+ worktree 从零环境的包安装（uv/npm，日志归档 §5）。
  **provider-smoke 与 long-soak 均不重跑**——两者为只读校验 + 哈希
  锁定复用（§2.3/§2.4），源文件零改动。
- 零秘密政策：本 README 与全部 canonical 工件不含任何 token/key/
  password/带凭据 URL/env 值（§3 秘密扫描 0 命中；canonical 内的
  Windows 绝对路径为 gitignored 本地工件路径，非秘密）。
- 本 README 为唯一入库证据文件；canonical 原始证据（gitignored）位于
  worktree `.verify/artifacts/m14-146-current-main-release-evidence/`
  （8 份核心工件 + 执行日志/脚本，SHA256SUMS 索引 25 文件，自不含
  自哈希；§5）。

## 1. 基点事实（git/远端可复核）

| 事件 | commit | 说明 |
|------|--------|------|
| PR #211 merge（M14-124 回填）= M14-125 执行基点 | `e14be369a4a80267dcaafd41f7475cd44e2166ed` | M14-125 证据绑定头 |
| PR #212 merge（M14-125 证据入库） | `97bbe3c77cbb83c1532cdedaeb9d952a89b1a531` | docs-only |
| PR #215–#232（M14-126…M14-145 各轮合入） | … | 含真实代码（M14-129/133/135/136/140/141/142/144）与多轮 docs-only |
| PR #233 merge（M14-142 冒烟预热 R4）→ **当前 main** | `239b881b02514f50d8ad6177b61bcb84fb908ac5` | 本切片精确基点；`e14be369..239b881` 共 47 提交 |
| main push CI run 36168686272 创建（push 事件） | 239b881 | 2026-09-25T17:41:23Z（UTC） |

- 区间**非 docs-only**：`services/api/tests/` 新增 8 个 drift-watch 族
  测试文件（`test_production_drift_watch*.py` 全家族）+ M14-142/M14-144
  工具与守卫测试——本切片 release-check 的 pytest 计数较 M14-125
  实测 **+499（4555→5054）**，即真实测试面增长的精确对账（§2.2，
  实测吻合、无硬编码）；`services/api/app/ops/release_readiness.py`
  与 `app/ops/cli.py` 在区间**零变更**（`_eval_ci_main` 契约与
  M14-125 相同），alembic head 仍 `0027_audit_chain`（区间无新迁移）。
- M14-125 canonical 保留为其时点历史记录不改写（本切片 long-soak
  复用即逐字节读其 canonical §2.4，零修改）；本切片不搬运其
  ci-main/release-check 产物。M14-83（@ 5829ad9 生产只读门三门）、
  M14-85（@ f47a1e4 backup-restore）、M14-87（@ audit-chain-anchor 门
  + 伴生锚）canonical 按 M14-91 §5 登记路径**原样 staging**（§2.5）。
- **provider-smoke 复用源不变**：生产自 M14-117 切换后始终运行
  `m14-117-production` 栈，本切片零切换零 provider 接触，M14-117
  切换后聚合（`029ee84f…`，564 bytes，generated_at
  2026-09-23T23:29:35Z）仍是与当前生产状态对应的最新真实 pass 证据
  ——按「源哈希与语义仍有效才复用」口径，与 M14-122/M14-123/
  M14-125 同源同哈希复用（§2.3）。
- long-soak 复用口径与 M14-107/M14-116/M14-122/M14-123/M14-125 完全
  相同：canonical 817 bytes、SHA256
  `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec`
  （= M14-106 运维轮 supervisor 复核的 24h 窗口 pass 审计，窗口
  2026-09-22T02:00:01Z → 2026-09-23T02:00:01Z），只读校验后逐字节
  stage（§2.4）。**诚实提示：该窗口早于 M14-117 生产切换且执行于
  m14-70 旧栈**——切换后不存在新的 24h soak 窗口；切换后稳定性证据
  是 M14-117 §6.2 监控轮（34 ok/0 warn/0 critical）、M14-118 只读
  watch 与 M14-145 语音恢复验证回合，不是新的 soak。按任务边界本切片
  不重跑 soak、不制造新窗口。

## 2. 刷新执行记录（ci-main/release-check 为本轮新执行；provider-smoke/long-soak 为只读校验复用）

### 2.1 ci-main（真实远端 CI，非本地重跑）

```bash
gh api "repos/swsgbl/ai-learning-os/actions/runs?head_sha=239b881b02514f50d8ad6177b61bcb84fb908ac5"
gh api "repos/swsgbl/ai-learning-os/actions/runs/36168686272/jobs"
```

（双查询直连成功，未改任何代理配置；stderr 捕获为空文件。）

- 命中断言（断言脚本复核归档 raw 响应而非仅信任务简报；任一断言
  失败即非零退出、不产出证据）：`workflow_runs` 中 total_count=1、
  event=push、head_branch=main 恰 1 条——run **36168686272**
  （run_number **588**），head_sha `239b881…`，status=completed，
  **conclusion=success**（created 2026-09-25T17:41:23Z / updated
  2026-09-25T17:45:47Z，
  https://github.com/swsgbl/ai-learning-os/actions/runs/36168686272）。
- jobs 断言：`total_count=5` 且全部 conclusion=success——API
  （ruff/pytest/migration）、Docker（compose build+healthy+smoke）、
  Release tools（Python tests）、Android（unit test/lint/assemble）、
  Web（test/typecheck/lint/build）；每 job head_sha == 239b881、job 名
  集合精确匹配且无重复（与 M14-116/M14-122/M14-123/M14-125 同一五
  job 契约）。
- 按 `_eval_ci_main` 契约（`services/api/app/ops/release_readiness.py`，
  区间零变更）**程序化派生** canonical `evidence/ci-main.json`
  （1201 bytes、SHA256 `2883e0d6…`）：断言驱动脚本从 raw 事实拼装 +
  溯源 source 串；未复用 M14-125 的 run 35992664886 或其 raw 文件。
- raw API 响应原样归档：`raw/gh-runs-239b881.json`、
  `raw/gh-jobs-36168686272.json`（哈希见 §5；两份 stderr 捕获为空
  文件，SHA256 `e3b0c442…` = 空文件标准值）。

### 2.2 release-check（隔离本地 full 重跑，干净 239b881 执行树）

从零构建全新隔离环境（worktree `.venv`，不复用任何前驱切片产物）：

```bash
uv venv .venv --python 3.12          # CPython 3.12.14
uv pip install -r services/api/requirements.txt -r services/api/requirements-dev.txt   # 59 packages
npm ci --no-audit --no-fund          # 411 packages（node v22.23.2 / npm 12.0.2）
```

（npm 提示 `unrs-resolver` postinstall 被 allowScripts 策略阻止——与
M14-92/M14-94/M14-97/M14-105/M14-107/M14-116/M14-122/M14-123/M14-125
相同的已知提示，如实记录；该脚本产物非门禁依赖，本轮 10 门全绿证实
无影响。）

**执行树前置核验**（declared-head 诚实性前提）：运行前
`git rev-parse HEAD` == `239b881…` 且 `git status --porcelain` 为空
（tracked-clean，仅 gitignored `.verify/`/`artifacts/` 增量，执行日志
归档留痕）——release-check 的实际执行树即精确干净 239b881，后续
cockpit `--gate-declared-head release-check=239b881` 声明成立。

隔离编排器（一次性 SQLite → alembic upgrade head → 127.0.0.1 临时
uvicorn → /health 就绪 → full 10 门 → 原子落盘 → finally 关停临时
API；环境剥离 AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL，显式
APP_ENV=development / VOICE_MODE=local——不连接任何生产面；新工作区
`artifacts/m14-146-isolated/`）：

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli release-check-isolated \
  --workdir "<worktree>/artifacts/m14-146-isolated" --json
# exit 0
```

- 结果：**all_green=true，10/10 pass**（generated_at
  2026-09-25T20:35:10.557219+00:00，execution_scope=full）——api-lint
  （ruff All checks passed）/ web-lint / web-typecheck / web-build /
  **api-test（pytest 5054 passed, 33 skipped, 1 warning in 296.14s；
  较 M14-125 @ e14be369 的 4555 恰 +499——e14be369..239b881 区间
  drift-watch 族等真实测试面增长的精确对账，实测吻合、无硬编码）** /
  migration（alembic current == head == 0027_audit_chain）/ backup
  （aios-backup-v1 tables:30 files:1）/ voice（local 合成 audio/wav
  17324 bytes）/ license（api deps 15 / web ok / models 7 / sources
  6）/ e2e（walkthrough 5 步，1073 ms）。
- `release-check-isolated.json` 逐字节复制改名为 canonical
  `evidence/release-check.json`（2491 bytes；文件自带 gate 自标识
  `release-check`，非手改；复制后 filecmp 逐字节比对 IDENTICAL）。

### 2.3 provider-smoke（不重跑、不触碰生产/provider；只读校验 + 哈希锁定复用 M14-117 切换后聚合）

- 复用源：**M14-117 生产切换轮固化的切换后真实聚合产物**
  `provider-smoke.json`（M14-117 README §10 登记 SHA256
  `029ee84f671457e5a379f56be39cebd16b306ba4711e0717aa3a5f5bdcfd163b`、
  564 bytes；generated_at 2026-09-23T23:29:35.556863+00:00——晚于
  2026-09-23T23:14:45Z 的切换后 preflight，即执行于
  `m14-117-production` 新栈上）。**与 M14-122/M14-123/M14-125 所
  stage 的是同一源文件同一哈希**——生产栈自 M14-117 切换后未再变更，
  该源的语义有效性（对应当前生产状态）未被任何后续切换事件打破，
  按既有契约（「源哈希与语义仍有效才复用」）本切片复用成立。
- 本切片只读校验后逐字节复制为 canonical
  `evidence/provider-smoke.json`——复制后重哈希 **逐字节一致
  （564 bytes 同哈希）** + JSON 契约断言：schema
  `provider-smoke-evidence-v1`、gate 自声明 `provider-smoke`、拓扑
  `voice_mode=local`、voice/search/llm 三槽位 executed=true 且
  result=pass（evidence_step local-voice-smoke / search-smoke /
  llm-smoke）。执行记录：`logs/prepare-reused-evidence.log`。
- 该 pass 证据的执行与 supervisor 复核属 M14-117 切换轮（search 冒烟
  4783ms results=5、local-voice ASR 1312ms/42 bytes + TTS 3079ms/
  226604 bytes RIFF WAV、llm 11854ms 正文 12 chars rubric [True,True]
  conf=1.0，含如实保留的 4 个失败 attempt——详见 M14-117 README）；
  本切片零冒烟执行、零 provider 接触，只做来源哈希锁定 + 字节原样
  stage。provider 冒烟的易失性（时点证据不承诺未来状态）按 M14-115
  §7 / M14-116 §6.3 / M14-122/M14-123/M14-125 §2.3 口径保持——
  **原始时间边界显式：2026-09-23T23:29:35Z，距本切片聚合时点
  （2026-09-25T20:38:42Z）约 45 小时，窗口外状态不承诺**（切换后
  局部佐证另见 M14-145 本地真实语音冒烟 pass/17853ms，时点证据、
  不改变本门复用口径）。

### 2.4 long-soak（不重跑、不触碰生产/计划任务；只读校验 + 逐字节复制）

- 复用口径与 M14-107 §2.3 / M14-116 §2.4 / M14-122 §2.4 /
  M14-123 §2.4 / M14-125 §2.4 完全一致：canonical 817 bytes、
  SHA256
  `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec`
  （与 M14-125 §5 登记值一致，大小写不敏感）——即 M14-106 运维轮
  supervisor 复核的真实 24h 稳定窗口审计（M14-107 首次 stage、
  M14-116/M14-122/M14-123/M14-125 复用的同一证据；本切片直接读
  M14-125 canonical 复核后逐字节复制）。
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
  M14-118、M14-145）判断。本切片零 soak 接触：未运行
  `soak_stability_audit.py`、未触碰 history/锚定/计划任务/监控管道。

### 2.5 evidence-cockpit 聚合（四门刷新/复用后重新汇总：cockpit_ready=true 保持）

在 tracked docs 编辑**之前**、HEAD 仍为干净 `239b881` 时运行。
canonical 源路径逐一取自 M14-91 README §5 / staged-inventory 登记与
M14-125 §2.5 同一路径（不猜测）；staging 前先对 6 个既有生产状态源
文件做 sha256 复核——与 M14-91/M14-116/M14-122/M14-123/M14-125
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
  --gate-declared-head release-check=239b881b02514f50d8ad6177b61bcb84fb908ac5 \
  --current-head 239b881b02514f50d8ad6177b61bcb84fb908ac5 \
  --staging-dir "<worktree>/artifacts/m14-146-cockpit-staging" \
  --json --output "<worktree>/artifacts/m14-146-cockpit-report.json"
# exit 0（cockpit_ready=true）
```

- **current-head 与 release-check head 显式声明
  `239b881b02514f50d8ad6177b61bcb84fb908ac5`**；`--gate-declared-head
  release-check` 的合法性由 §2.2 前置核验支撑（实际执行树即干净
  239b881；ci-main 则由文件内嵌 merge_commit==current HEAD 自动
  current）。
- 结果（报告归档 canonical `cockpit-report.json`，generated_at
  2026-09-25T20:38:42.987404+00:00）：**evaluator pass=9 / pending=0
  / blocked=0 / missing=2 / malformed=0 / tampered=0**；
  **cockpit_ready=true、cockpit_blockers=[]、required_not_staged=[]、
  exit 0**。同时 **release_ready=false、production_ready=false（恒
  false）**：not_pass_required 恰为 `release-approval`（human-only
  从未发生，cockpit 按策略永不接受、不计入 blocker——「无技术
  blocker」与「不可放行」两件事同时成立），not_pass_optional 为
  `turn-tls`（optional：本机/LAN 发布形态不需要公网 TURN，不计入
  fail）。
- **code-bound 两门刷新生效**：ci-main stale=**current**（内嵌
  merge_commit=239b881 == current HEAD）、release-check stale=
  **current**（flag 声明 239b881）。
- **provider-smoke 以 M14-117 切换后聚合 staged 即 pass**（经
  `_eval_provider_smoke` 全语义校验）；long-soak staged pass（真实
  24h 稳定窗口，§2.4）。production-state 五门（audit-chain-anchor /
  production-preflight / legacy-papers / draft-ownership /
  backup-restore）+ anchor companion 以历史 canonical snapshot 原样
  staged、undeclared 如实呈现不 block（M14-91 §1.1 第 5 条口径）；
  release-approval「按策略永不接受，非 blocker」、turn-tls「optional
  不阻断」摘要显式区分。
- **production-preflight 门的取源说明（诚实记录，与
  M14-122/M14-123/M14-125 同）**：M14-117 切换后 preflight（5/5
  pass，2026-09-23T23:14:45Z）是更新的生产状态事实，但其 JSON
  **不含 evaluator 契约要求的 `gate` 自声明字段**，stage 它将触发
  MalformedEvidence fail-closed——本切片绝不手改补字段，故该门继续
  stage M14-83 注册 canonical（`b3a2be66…`，6/6 哈希复核通过），
  M14-117 切换后 preflight 作为「staged 各门语义在切换后仍成立」的
  支撑事实在 README 引用（其 5 检查在切换后复核了 audit-chain
  valid / legacy 三类计数零 / draft 两类计数零 / 锚定 up-to-date /
  alembic current==head，恰为 staged 五门所覆盖的生产状态面）。
- **staged 10 文件（9 门 + anchor companion）与 source 逐字节一致**：
  工具内建写后重读断言 + 外部独立复核 **10/10 IDENTICAL**（filecmp
  语义逐字节比较）；6 个既有文件哈希与 M14-91 staged-inventory 登记
  值交叉一致、本切片 4 个新 stage 文件（ci-main `2883e0d6…` 1201
  bytes / release-check `921b7e77…` 2491 bytes / provider-smoke
  `029ee84f…` 564 bytes / long-soak `d939c652…` 817 bytes）与
  canonical 实际哈希一致——来源零改动 + 逐字节 staging 端到端实证
  （清单归档 canonical `staged-inventory.txt`）。

## 3. 聚焦验证与静态检查（真实执行结果）

- 聚焦契约测试（cockpit/release/provider 八套件，本切片证据链所依赖
  的工具面）：

```bash
cd services/api && "<worktree>/.venv/Scripts/python.exe" -m pytest -q \
  tests/test_evidence_cockpit.py tests/test_release_readiness.py tests/test_release_closure_manifest.py \
  tests/test_release_check_isolated.py tests/test_release_checklist.py \
  tests/test_provider_smoke_evidence.py tests/test_provider_smoke_preflight.py tests/test_searxng_local_provider.py \
  --basetemp "<scratch>/m14-146-current-main-release-evidence/focused"   # Windows 需先 mkdir -p 预建父目录
# 407 passed, 1 warning in 7.12s
```

（Windows 已知形态：pytest `--basetemp` 不创建缺失父目录链，本切片
按 M14-116/M14-122/M14-123/M14-125 教训**先 `mkdir -p` 预建父目录**，
一次通过。计数与 M14-125 的 407 恰一致——evidence-chain 工具契约面
在 e14be369..239b881 区间零变更的实证。）
- `ruff check services/api`：All checks passed（tracked Python 零改动，
  基线复核；本切片无 Python 源码变更，py_compile 不适用——入库文件
  仅 docs/evidence README 与四本台账）。
- canonical 完整性：`sha256sum -c SHA256SUMS` **25/25 OK**（本切片
  索引为 LF 行尾文本模式，直接核验通过；自不含自哈希）；canonical
  JSON `json.load` 解析全通过（ci-main/release-check/provider-smoke/
  long-soak/cockpit-report/两 raw/cockpit-stdout）；JSON 契约断言
  脚本 **22 项全过**（ci-main run_id/merge_commit/conclusion、raw
  runs push@main 唯一/run_number 588、raw jobs 5/5 精确集合、
  release-check all_green 10/10 + pytest 5054/33 + 对 M14-125 增量
  +499 对账、provider-smoke local 拓扑三槽位 pass + 原始时间戳边界、
  long-soak 策略四值 + 97 ok + max gap 17.25 + span 1440、cockpit
  9 pass/2 missing + approval not-staged + release_ready/
  production_ready false + 两 code-bound 门 current、
  staged-inventory 10/10 IDENTICAL）。
- 秘密扫描：canonical 25 文件对 credential 赋值 / OpenAI 风格 key /
  私钥块 / 带凭据 DB URL / URL userinfo 五类模式扫描 **0 命中**
  （报告/清单内的 Windows 绝对路径为 gitignored 本地工件路径，非
  秘密）。
- `git diff --check` 干净（docs-only）；新增行扫描：secret 值 / 本地
  绝对路径（盘符或根路径形态，正反斜杠变体）/ U+FFFD 替换字符对全部
  新增行 **0 命中**（本 README 与四台账内路径均为仓库相对或占位符
  形式）。
- 分支卫生：单 commit 后 tracked-clean（worktree 侧
  `.venv`/`node_modules`/`artifacts/`/`.verify/` 均 gitignored 未
  入库）。

## 4. 与 M14-125 的差异（同一契约下的净变化）

| 维度 | M14-125 @ e14be369 | M14-146 @ 239b881 |
|------|--------------------|-------------------|
| 证据定性 | current-main（docs-only 漂移后刷新） | **current-main（真实代码/测试面增长后刷新——47 提交区间非 docs-only）** |
| ci-main run | 35992664886（run_number 543） | **36168686272**（run_number 588，本轮新派生） |
| release-check pytest | 4555 passed / 33 skipped（277.38s） | **5054 passed / 33 skipped**（296.14s，+499 = drift-watch 族等真实测试面增长对账） |
| release-check e2e | 5 步 1086 ms | **5 步 1073 ms** |
| alembic head | 0027_audit_chain | **0027_audit_chain（区间无新迁移）** |
| provider-smoke 复用源 | M14-117 切换后聚合（`029ee84f…`） | **同源同哈希复用（距聚合约 45 小时，原始边界显式保留）** |
| long-soak | staged pass（同哈希 `d939c652…`） | **staged pass（同哈希只读复用；窗口早于切换且在 m14-70 栈，如实呈现）** |
| evaluator | pass=9 / missing=2 | **pass=9 / missing=2（不变）** |
| cockpit blockers | [] | **[]（无技术 blocker）** |
| cockpit_ready / exit | true / 0 | **true / 0（保持）** |
| release_ready / production_ready | false / false | **false / false（不变——release-approval human-only 缺席）** |
| production-state 五门 + anchor | 原样 staged（同源同哈希） | **原样 staged（同源同哈希，6/6 MATCH 复核）** |
| staged 文件 | 10（9 门 + anchor companion） | **10（同，10/10 IDENTICAL）** |
| 聚焦契约测试 | 407 passed | **407 passed（工具契约面区间零变更实证）** |
| canonical 索引 | SHA256SUMS 24 文件 | **SHA256SUMS 25 文件（多一份 env/npm/precheck 执行留痕日志）** |
| 交付形态 | 单 local commit → push 分支 + 开 PR #212 | **单 local commit，不 push、不开 PR（任务书指令）** |

## 5. canonical 证据清单（gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：worktree `.verify/artifacts/m14-146-current-main-release-evidence/`

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/ci-main.json` | 1201 | `2883e0d66ac7160e7f348b8b416bac8f73be71639db2a26a55de18a818f2da2a` |
| `evidence/release-check.json` | 2491 | `921b7e77543c280594cfb0b995ec0d43ee1599b34a0d5defa506e2abeb3bad20` |
| `evidence/provider-smoke.json` | 564 | `029ee84f671457e5a379f56be39cebd16b306ba4711e0717aa3a5f5bdcfd163b` |
| `evidence/long-soak.json` | 817 | `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec` |
| `raw/gh-runs-239b881.json` | 12201 | `266f36c8f855aa988446c1ee98784476215dcaef0e47804f563ddf2c335ec27d` |
| `raw/gh-jobs-36168686272.json` | 12334 | `ce8d46214ccd1e0d6c382ff274b97a9d4271450cb2313e752534cb925b80534f` |
| `cockpit-report.json` | 19002 | `57115656d6a5771a46a08c88c389301d93117c84d3433e5b95cd109bd1dac831` |
| `staged-inventory.txt` | 2778 | `0ba68742b24d63030092dc1609a68fb0b95532b4b6073cb50ce4ecb381dbfcf2` |

（另 `logs/` 下执行日志与断言/派生/校验脚本 15 份（env-setup/
npm-ci/pre-release-check-head + derive-ci-main 派生脚本/
prepare-reused-evidence 校验脚本/verify-staging 复核脚本/
assert-contracts 断言脚本 + 各自 log + release-check-isolated 双流/
cockpit 双流）+ `raw/*.stderr.txt` 两份空捕获（SHA256 `e3b0c442…` =
空文件标准值）+ `SHA256SUMS` 25 文件索引，自不含自哈希；worktree 侧
`artifacts/m14-146-isolated/`、`artifacts/m14-146-cockpit-staging/`、
`artifacts/m14-146-cockpit-report.json` 为生成现场，gitignored，
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
3. **provider-smoke pass 是 M14-117 切换轮的时点证据**
   （2026-09-23T23:29:35Z 三步冒烟聚合，m14-117-production 栈）：
   证明该时点三 provider 冒烟通过，不承诺窗口外状态（距本切片聚合
   约 45 小时）；易失性由后续刷新切片覆盖（M14-98 先例）。本切片
   零冒烟执行、零 provider/生产接触。M14-145 本地真实语音冒烟
   （2026-09-26 00:34 +08，pass/17853ms）是另一时点局部佐证，不
   构成本门的替代或更新。
4. **long-soak pass 是 M14-106 运维轮的时点证据且窗口早于生产切换**：
   窗口 2026-09-22T02:00:01Z → 2026-09-23T02:00:01Z（m14-70 栈）内
   97 样本全 ok——证明该窗口稳定性，不承诺窗口外、也不承诺切换后
   栈稳定性；切换后的 24h soak 窗口尚不存在，按任务边界本切片不
   重跑、**不制造新窗口**，是否需要切换后新 soak 窗口由 supervisor
   决策（切换后稳定性佐证：M14-117 §6.2 监控轮 34 ok、M14-118 只读
   watch、M14-145 语音恢复验证回合）。
5. **production-state 门是「呈现」不是「重执行」**：staged 的五门 +
   anchor companion 均为各切片 canonical 时点快照（M14-83 @
   5829ad9、M14-85 @ f47a1e4、M14-87），本切片零生产触碰约束下不
   重推导、不搬运冒充新执行；其对 M14-117 切换后当前生产状态的
   语义效力由 M14-117 切换后 preflight（5/5 pass，§2.5）与 supervisor
   结合生产变更记录判断，cockpit 提供声明通道显式表达。M14-117 的
   preflight JSON 因缺 `gate` 自声明不能直接作为门证据 stage（契约
   fail-closed，非本切片补改）。
6. **生产当前运行 m14-117-production**（M14-117 于 2026-09-24 切换；
   M14-145 恢复回合佐证 core compose 6/6 healthy 且未 recreate），
   本切片零调度面触碰、不构成任何部署；不授权任何部署。M14-124
   登记的 `m14-124-production` 两枚审批前镜像与回滚锚材料保持有效，
   等待 hash-bound human-only 审批绑定。
7. **代码绑定门证据只对其执行时点的树成立**：ci-main/release-check
   现对 239b881 current（= 本切片基点 = current main）；main 再前移
   即再 stale（M14-91 冒烟已实证该语义），需下一个刷新切片真实重
   推导。
8. **GitHub Actions run 有平台保留期**（默认 90 天）；到期后按 §2.1
   命令重取即可。
