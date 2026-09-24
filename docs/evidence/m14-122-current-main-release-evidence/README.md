# M14-122：current-main 发布证据刷新（PR #206 后；ci-main + release-check 真实重跑 + provider-smoke 切换后复用 + long-soak 同哈希复用 + cockpit 零 blocker 聚合）

- 切片：分支 `ops/m14-122-current-main-release-evidence`（独立 worktree
  `m14-122-current-main-release-evidence`，基于 main
  `2b1c2dd4783166576f00faf5088e7391ab4cc173`（PR #206 merge = M14-119
  合入，精确基点），单 local commit，不 push——交付以 supervisor（Codex）
  复核为准）。
- Supervisor 合并轮（2026-09-24，merge `f67cbe27a56d476fec2bdc23d51002b12c01219e`）：按指令
  fetch 并 merge current origin/main（PR #207/M14-121、PR #208/M14-120 已含），三本台账
  冲突按 M 号降序约定解决、三个切片条目全保留。**本切片证据不重跑、不迁移——
  ci-main/release-check 仍绑定执行基点 2b1c2dd，本 README 与 canonical 工件不构成
  f67cbe2 或合并头上的证据重跑，亦非 final-current 证据**（合并引入 M14-121 真实测试
  文件后，代码绑定门相对合并头即再 stale，刷新留待后续切片）；gitignored 原始证据
  目录原样保留；合并树上聚焦契约八套件复跑 **407 passed**（canonical venv、专用
  basetemp）+ `git diff --check` 干净；分支按 supervisor 指令 push 并向 main 开 PR。
- 目标：基于 M14-91/M14-116 既有契约刷新 current main 的发布证据链。
  ci-main 与 release-check 两门**本轮真实重执行**（远端 CI 事实核验 +
  干净 2b1c2dd 执行树上的隔离 full 跑）；**provider-smoke 只读复用
  M14-117 生产切换后的真实三步冒烟聚合**（切换后时点证据，SHA256
  逐字节一致复用，绝不改写源文件——比 M14-115 切换前聚合更贴近当前
  生产状态，是本切片对「语义仍有效」的诚实选择）；long-soak 沿
  M14-107/M14-116 同款只读复用（同哈希 `d939c652…`）。绝不手改 gate
  JSON、绝不合成 pass、绝不搬运与源哈希不符的旧 JSON 冒充 current。
  **`release_ready=false` / `production_ready=false` 全程不变**——
  release-approval 是 human-only 门、从未发生，cockpit 按策略永不接受
  它（not-staged 呈现、不计 blocker）；turn-tls optional 不阻断。
- 零生产触碰（本切片全程）：零容器启停/重建、零计划任务操作、零
  DB/MinIO/语音引擎接触、零生产日志写入、零 secrets/env 读取、零
  soak 历史重跑/锚定写入、零发布审批接触、零部署、零 Ollama/FunASR/
  CosyVoice/SearXNG/代理/CC Switch 生命周期变更。唯一网络访问是
  GitHub 只读 API（`gh api`，已认证账号）+ worktree 从零环境的包安装
  （uv/npm，日志归档 §5）。**provider-smoke 与 long-soak 均不重跑**
  ——两者为只读校验 + 哈希锁定复用（§2.3/§2.4），源文件零改动。
- 本 README 为唯一入库证据文件；canonical 原始证据（gitignored）位于
  worktree `.verify/artifacts/m14-122-current-main-release-evidence/`
  （8 份核心工件 + 执行日志/脚本，SHA256SUMS 索引 23 文件，自不含
  自哈希；§5）。

## 1. 基点事实（git/远端可复核）

| 事件 | commit | 说明 |
|------|--------|------|
| PR #203 merge（M14-116 证据入库） | `6cc20df` | M14-117 文档基点 |
| PR #204 merge（M14-117 生产切换证据，**生产实际切至 m14-117-production**） | `1a89559` | docs-only 合入 |
| PR #205 merge（M14-118 post-cutover watch 工具 + 43 契约测试） | `3d2874b` | 真实代码/测试变更 |
| PR #206 merge（M14-119 告警分发工具 + 76 契约测试）→ **当前 main** | `2b1c2dd4783166576f00faf5088e7391ab4cc173` | 本切片精确基点 |
| main push CI run 35952447109 创建（push 事件） | 2b1c2dd | 2026-09-24T03:40:43Z（UTC） |

- M14-116 证据 @ 2619ea7 对 2b1c2dd 已 stale（2619ea7..2b1c2dd 含
  PR #205/#206 两轮**真实代码变更**——新增 `tools/ops/post_cutover_watch.py`
  与 `tools/ops/monitoring_alert_dispatch.py` 及对应 119 个契约测试），
  代码绑定门必须真实重执行，这是本切片的直接动因。
- M14-116 canonical 保留为其时点历史记录不改写；本切片不搬运其
  ci-main/release-check 产物。M14-83（@ 5829ad9 生产只读门三门）、
  M14-85（@ f47a1e4 backup-restore）、M14-87（@ audit-chain-anchor 门 +
  伴生锚）canonical 按 M14-91 §5 登记路径**原样 staging**（§2.5）。
- **provider-smoke 复用源更换为 M14-117 切换后聚合**：M14-116 复用的
  M14-115 聚合（`899feecb…`，2026-09-23T18:57:57Z）执行于 m14-70
  旧镜像栈上；M14-117 已于 2026-09-24 将生产 API/Web 切至
  `m14-117-production` 并在切换后重跑三步冒烟，固化为聚合产物
  `029ee84f…`（564 bytes，generated_at 2026-09-23T23:29:35Z）。本切片
  只读校验其 SHA256 与 M14-117 README §10 登记值逐字节一致后原样
  stage（§2.3）——按「源哈希与语义仍有效才复用」口径，这是对当前
  生产状态（m14-117-production）对应的最新真实 pass 证据。
- long-soak 复用口径与 M14-107/M14-116 完全相同：canonical 817 bytes、
  SHA256 `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec`
  （= M14-106 运维轮 supervisor 复核的 24h 窗口 pass 审计，窗口
  2026-09-22T02:00:01Z → 2026-09-23T02:00:01Z），只读校验后逐字节
  stage（§2.4）。**诚实提示：该窗口早于 M14-117 生产切换**——切换后
  不存在新的 24h soak 窗口；切换后稳定性证据是 M14-117 §6.2 监控轮
  （34 ok/0 warn/0 critical）与 M14-118 只读 watch，不是新的 soak。

## 2. 刷新执行记录（ci-main/release-check 为本轮新执行；provider-smoke/long-soak 为只读校验复用）

### 2.1 ci-main（真实远端 CI，非本地重跑）

```bash
gh api "repos/swsgbl/ai-learning-os/actions/runs?head_sha=2b1c2dd4783166576f00faf5088e7391ab4cc173"
gh api "repos/swsgbl/ai-learning-os/actions/runs/35952447109/jobs"
```

- 命中断言（断言脚本复核归档 raw 响应而非仅信任务简报；任一断言
  失败即非零退出、不产出证据）：`workflow_runs` 中 total_count=1、
  event=push、head_branch=main 恰 1 条——run **35952447109**
  （run_number **533**），head_sha `2b1c2dd…`，status=completed，
  **conclusion=success**（created 2026-09-24T03:40:43Z / updated
  2026-09-24T03:45:37Z，
  https://github.com/swsgbl/ai-learning-os/actions/runs/35952447109）。
- jobs 断言：`total_count=5` 且全部 conclusion=success——API
  （ruff/pytest/migration）、Docker（compose build+healthy+smoke）、
  Release tools（Python tests）、Android（unit test/lint/assemble）、
  Web（test/typecheck/lint/build）；每 job head_sha == 2b1c2dd、job 名
  集合精确匹配且无重复（与 M14-116 同一五 job 契约）。
- 按 `_eval_ci_main` 契约（`services/api/app/ops/release_readiness.py`）
  **程序化派生** canonical `evidence/ci-main.json`（1188 bytes、
  SHA256 `e55e57f5…`）：断言驱动脚本从 raw 事实拼装 + 溯源 source 串；
  未复用 M14-116 的 run 35921247552 或其 raw 文件。
- raw API 响应原样归档：`raw/gh-runs-2b1c2dd.json`、
  `raw/gh-jobs-35952447109.json`（哈希见 §5；两份 stderr 捕获为空文件）。

### 2.2 release-check（隔离本地 full 重跑，干净 2b1c2dd 执行树）

从零构建全新隔离环境（worktree `.venv`，不复用任何前驱切片产物）：

```bash
uv venv .venv --python 3.12          # CPython 3.12.14
uv pip install -r services/api/requirements.txt -r services/api/requirements-dev.txt   # 59 packages
npm ci --no-audit --no-fund          # 411 packages（node v22 / npm 12）
```

（npm 提示 `unrs-resolver` postinstall 被 allowScripts 策略阻止——与
M14-92/M14-94/M14-97/M14-105/M14-107/M14-116 相同的已知提示，如实
记录；该脚本产物非门禁依赖，本轮 10 门全绿证实无影响。）

**执行树前置核验**（declared-head 诚实性前提）：运行前
`git rev-parse HEAD` == `2b1c2dd…` 且 `git status --porcelain` 为空
（tracked-clean，仅 gitignored `.verify/`/`artifacts/` 增量）——
release-check 的实际执行树即精确干净 2b1c2dd，后续 cockpit
`--gate-declared-head release-check=2b1c2dd` 声明成立。

隔离编排器（一次性 SQLite → alembic upgrade head → 127.0.0.1 临时
uvicorn（本轮动态端口 53843，已收尾 terminated）→ /health 就绪 →
full 10 门 → 原子落盘 → finally 关停临时 API；环境剥离
AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL，显式
APP_ENV=development / VOICE_MODE=local——不连接任何生产面；新工作区
`artifacts/m14-122-isolated/`）：

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli release-check-isolated \
  --workdir "<worktree>/artifacts/m14-122-isolated" --json
# exit 0
```

- 结果：**all_green=true，10/10 pass**（generated_at
  2026-09-24T04:07:36Z，execution_scope=full）——api-lint（ruff All
  checks passed）/ web-lint / web-typecheck / web-build /
  **api-test（pytest 4552 passed, 33 skipped, 1 warning in 275.77s；
  较 M14-116 @ 2619ea7 的 4433 恰 +119——2619ea7..2b1c2dd 区间
  M14-118 新增 43 + M14-119 新增 76 契约测试的累计对账，实测吻合、
  无硬编码）** / migration（alembic head==0027_audit_chain）/ backup
  （aios-backup-v1 tables:30 files:1）/ voice（local 合成 audio/wav
  17324 bytes）/ license（api deps 15 / web ok / models 7 / sources
  6）/ e2e（walkthrough 5 步，1119 ms）。
- `release-check-isolated.json` 逐字节复制改名为 canonical
  `evidence/release-check.json`（2491 bytes；文件自带 gate 自标识
  `release-check`，非手改）。

### 2.3 provider-smoke（不重跑、不触碰生产/provider；只读校验 + 哈希锁定复用 M14-117 切换后聚合）

- 复用源：**M14-117 生产切换轮固化的切换后真实聚合产物**
  `provider-smoke.json`（M14-117 README §10 登记 SHA256
  `029ee84f671457e5a379f56be39cebd16b306ba4711e0717aa3a5f5bdcfd163b`、
  564 bytes；generated_at 2026-09-23T23:29:35.556863+00:00——晚于
  2026-09-23T23:14:45Z 的切换后 preflight，即执行于
  `m14-117-production` 新栈上）。
- 本切片只读校验后逐字节复制为 canonical `evidence/provider-smoke.json`
  ——复制后重哈希 **逐字节一致（564 bytes 同哈希）** + JSON 契约断言：
  schema `provider-smoke-evidence-v1`、gate 自声明 `provider-smoke`、
  拓扑 `voice_mode=local`、voice/search/llm 三槽位 executed=true 且
  result=pass（evidence_step local-voice-smoke / search-smoke /
  llm-smoke）。执行记录：`logs/prepare-reused-evidence.log`。
- 该 pass 证据的执行与 supervisor 复核属 M14-117 切换轮（search 冒烟
  4783ms results=5、local-voice ASR 1312ms/42 bytes + TTS 3079ms/
  226604 bytes RIFF WAV、llm 11854ms 正文 12 chars rubric [True,True]
  conf=1.0，含如实保留的 4 个失败 attempt——详见 M14-117 README）；
  本切片零冒烟执行、零 provider 接触，只做来源哈希锁定 + 字节原样
  stage。provider 冒烟的易失性（时点证据不承诺未来状态）按 M14-115
  §7 / M14-116 §6.3 口径保持。
- **为何不再复用 M14-115 聚合**：M14-116 所 stage 的 M14-115 聚合
  （`899feecb…`，2026-09-23T18:57:57Z）执行于 m14-70 旧栈；M14-117
  切换后生产面已变，旧聚合对「当前生产状态」的语义对应性失效。按
  任务口径「仅当既有源哈希与语义仍有效才复用」，本切片改用 M14-117
  切换后聚合——两源各自在其时点均为真实 pass，本切片不评判旧源真伪，
  只选择与当前生产状态对应的最新源。

### 2.4 long-soak（不重跑、不触碰生产/计划任务；只读校验 + 逐字节复制）

- 复用口径与 M14-107 §2.3 / M14-116 §2.4 完全一致：canonical 817
  bytes、SHA256
  `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec`
  （与 M14-116 §5 登记值一致，大小写不敏感）——即 M14-106 运维轮
  supervisor 复核的真实 24h 稳定窗口审计（M14-107 首次 stage、
  M14-116 复用的同一证据）。
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
- **诚实边界：该窗口早于 M14-117 生产切换**（m14-70 栈上的窗口），
  切换后尚无 24h soak 窗口；本切片按任务边界不重跑 soak，cockpit
  按 evaluator 契约消费该时点 pass 证据，其窗口外效力由 supervisor
  结合切换后监控/巡检证据（M14-117 §6.2、M14-118）判断。本切片零
  soak 接触：未运行 `soak_stability_audit.py`、未触碰
  history/锚定/计划任务/监控管道。

### 2.5 evidence-cockpit 聚合（四门刷新/复用后重新汇总：cockpit_ready=true 保持）

在 tracked docs 编辑**之前**、HEAD 仍为干净 `2b1c2dd` 时运行。canonical
源路径逐一取自 M14-91 README §5 / staged-inventory 登记与 M14-116
§2.5 同一路径（不猜测）；staging 前先对 6 个既有生产状态源文件做
sha256 复核——与 M14-91/M14-116 登记值逐一 **MATCH（6/6）**：
audit-chain-anchor `a8c54c5e…` / audit-anchor companion `d2bfd877…` /
backup-restore `ed0fc5b4…` / preflight `b3a2be66…` / legacy
`83cd62bd…` / draft `e90ae04c…`（执行记录
`logs/prepare-reused-evidence.log`）。

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
  --gate-declared-head release-check=2b1c2dd4783166576f00faf5088e7391ab4cc173 \
  --current-head 2b1c2dd4783166576f00faf5088e7391ab4cc173 \
  --staging-dir "<worktree>/artifacts/m14-122-cockpit-staging" \
  --json --output "<worktree>/artifacts/m14-122-cockpit-report.json"
# exit 0（cockpit_ready=true）
```

- **current-head 与 release-check head 显式声明
  `2b1c2dd4783166576f00faf5088e7391ab4cc173`**；`--gate-declared-head
  release-check` 的合法性由 §2.2 前置核验支撑（实际执行树即干净
  2b1c2dd；ci-main 则由文件内嵌 merge_commit==current HEAD 自动
  current）。
- 结果（报告归档 canonical `cockpit-report.json`，generated_at
  2026-09-24T04:10:23Z）：**evaluator pass=9 / pending=0 / blocked=0
  / missing=2 / malformed=0 / tampered=0**；**cockpit_ready=true、
  cockpit_blockers=[]、required_not_staged=[]、exit 0**。同时
  **release_ready=false、production_ready=false（恒 false）**：
  not_pass_required 恰为 `release-approval`（human-only 从未发生，
  cockpit 按策略永不接受、不计入 blocker——「无技术 blocker」与
  「不可放行」两件事同时成立），not_pass_optional 为 `turn-tls`
  （optional：本机/LAN 发布形态不需要公网 TURN，不计入 fail）。
- **code-bound 两门刷新生效**：ci-main stale=**current**（内嵌
  merge_commit=2b1c2dd == current HEAD）、release-check stale=
  **current**（flag 声明 2b1c2dd）。
- **provider-smoke 以 M14-117 切换后聚合 staged 即 pass**（经
  `_eval_provider_smoke` 全语义校验）；long-soak staged pass（真实
  24h 稳定窗口，§2.4）。production-state 五门（audit-chain-anchor /
  production-preflight / legacy-papers / draft-ownership /
  backup-restore）+ anchor companion 以历史 canonical snapshot 原样
  staged、undeclared 如实呈现不 block（M14-91 §1.1 第 5 条口径）；
  release-approval「按策略永不接受，非 blocker」、turn-tls「optional
  不阻断」摘要显式区分。
- **production-preflight 门的取源说明（诚实记录）**：M14-117 切换后
  preflight（5/5 pass，2026-09-23T23:14:45Z，canonical
  `49635fab…`）是更新的生产状态事实，但其 JSON **不含 evaluator
  契约要求的 `gate` 自声明字段**（键集仅 checks/exit_code/
  generated_at/phase/read_only/summary），stage 它将触发
  MalformedEvidence fail-closed——本切片绝不手改补字段，故该门继续
  stage M14-83 注册 canonical（`b3a2be66…`，6/6 哈希复核通过），
  M14-117 切换后 preflight 作为「staged 各门语义在切换后仍成立」的
  支撑事实在 README 引用（其 5 检查在切换后复核了 audit-chain
  valid / legacy 三类计数零 / draft 两类计数零 / 锚定 up-to-date /
  alembic current==head，恰为 staged 五门所覆盖的生产状态面）。
- **staged 10 文件（9 门 + anchor companion）与 source 逐字节一致**：
  工具内建写后重读断言 + 外部独立复核 **10/10 IDENTICAL**（filecmp
  语义逐字节比较）；6 个既有文件哈希与 M14-91 staged-inventory 登记
  值交叉一致、本切片 4 个新 stage 文件（ci-main `e55e57f5…` 1188
  bytes / release-check `459932e3…` 2491 bytes / provider-smoke
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
  --basetemp "D:/AI Learning OS/.pytest-tmp/m14-122-current-main-release-evidence/focused"
# 407 passed, 1 warning in 6.98s
```

（Windows 已知形态：pytest `--basetemp` 不创建缺失父目录链，本切片
按 M14-116 教训**先 `mkdir -p` 预建父目录**，一次通过。）
- `ruff check services/api`：All checks passed（tracked Python 零改动，
  基线复核）。
- canonical 完整性：`sha256sum -c SHA256SUMS` **23/23 OK**（本切片
  索引为 LF 行尾，直接核验通过）；canonical JSON `json.load` 解析
  全通过（ci-main/release-check/provider-smoke/long-soak/
  cockpit-report/两 raw/cockpit-stdout）；JSON 契约断言脚本
  **22 项全过**（ci-main run_id/merge_commit/conclusion、raw runs
  push@main 唯一/run_number 533、raw jobs 5/5 精确集合、release-check
  all_green 10/10 + pytest 4552/33 + 对 2619ea7 增量 +119 对账、
  provider-smoke local 拓扑三槽位 pass + 切换后时间戳、long-soak
  策略四值 + 97 ok + max gap 17.25 + span 1440、cockpit 9 pass/
  2 missing + approval not-staged + release_ready/production_ready
  false + 两 code-bound 门 current、staged-inventory 10/10
  IDENTICAL）。
- 秘密扫描：canonical 23 文件对 credential 赋值 / OpenAI 风格 key /
  私钥块 / 带凭据 DB URL / URL userinfo 五类模式扫描 **0 命中**
  （报告/清单内的 Windows 绝对路径为 gitignored 本地工件路径，非
  秘密）。
- `git diff --check` 干净（docs-only）；分支卫生：单 commit 后
  tracked-clean（worktree 侧 `.venv`/`node_modules`/`artifacts/`/
  `.verify/` 均 gitignored 未入库）。

## 4. 与 M14-116 的差异（同一契约下的净变化）

| 维度 | M14-116 @ 2619ea7 | M14-122 @ 2b1c2dd |
|------|-------------------|-------------------|
| 基点后生产状态 | m14-70 镜像（切换前） | **m14-117-production**（M14-117 已切换） |
| ci-main run | 35921247552（run_number 525） | **35952447109**（run_number 533，本轮新派生） |
| release-check pytest | 4433 passed / 33 skipped（269.80s） | **4552 passed / 33 skipped**（275.77s，+119 = M14-118 43 + M14-119 76 对账） |
| provider-smoke 复用源 | M14-115 聚合（切换前，`899feecb…`） | **M14-117 切换后聚合（`029ee84f…`，2026-09-23T23:29:35Z）** |
| long-soak | staged pass（首 stage 同哈希） | staged pass（同哈希 `d939c652…` 只读复用；窗口早于切换，如实呈现） |
| evaluator | pass=9 / missing=2 | **pass=9 / missing=2（不变）** |
| cockpit blockers | [] | **[]（无技术 blocker）** |
| cockpit_ready / exit | true / 0 | **true / 0（保持）** |
| release_ready / production_ready | false / false | **false / false（不变——release-approval human-only 缺席）** |
| production-state 五门 + anchor | 原样 staged（同源同哈希） | 原样 staged（同源同哈希，6/6 MATCH 复核；M14-117 切换后 preflight 5/5 作为语义支撑事实引用） |
| staged 文件 | 10（9 门 + anchor companion） | **10（同，10/10 IDENTICAL）** |
| 交付形态 | 单 commit + push 分支 + 开 PR | **单 local commit，不 push（待 Codex 复核）** |

## 5. canonical 证据清单（gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：worktree `.verify/artifacts/m14-122-current-main-release-evidence/`

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/ci-main.json` | 1188 | `e55e57f5335d16408833d9faebe92bf02929f590f9285de6e4519123cff0fd6c` |
| `evidence/release-check.json` | 2491 | `459932e323a0eefd3279b7096d9d1383f34a366ae80f23d8a5bdc0620404efd3` |
| `evidence/provider-smoke.json` | 564 | `029ee84f671457e5a379f56be39cebd16b306ba4711e0717aa3a5f5bdcfd163b` |
| `evidence/long-soak.json` | 817 | `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec` |
| `raw/gh-runs-2b1c2dd.json` | 12212 | `49e93808d2000bb2531ac2e854c4f1d8a59b919c138a7ab377d24ff0592cf8de` |
| `raw/gh-jobs-35952447109.json` | 12334 | `f255a98b7c37ac9227dd66d3385e28a4507d7d1f5f0ff9ca48793cfb841d2d52` |
| `cockpit-report.json` | 19002 | `e1517f2143c17300a66ba372a3c9513569946bfddf4484ef0b0972bbb36092f6` |
| `staged-inventory.txt` | 2778 | `3ba8fadb596711696093924b3a7f998b5ec5ece2da1234450307387d84a08a61` |

（另 `logs/` 下执行日志与断言/派生脚本 11 份（env-setup/
derive-ci-main + 派生脚本/prepare-reused-evidence + 校验脚本/
release-check-isolated 双流/cockpit 双流/verify-staging + 复核脚本/
assert-contracts + 断言脚本）+ `raw/*.stderr.txt` 两份空捕获
（SHA256 `e3b0c442…` = 空文件标准值）+ `SHA256SUMS` 23 文件索引，
自不含自哈希；worktree 侧 `artifacts/m14-122-isolated/`、
`artifacts/m14-122-cockpit-staging/`、
`artifacts/m14-122-cockpit-report.json` 为生成现场，gitignored，
不入库。）

## 6. 诚实边界与剩余门（不伪称，逐项可执行收口）

1. **cockpit_ready=true ≠ 可发布**：技术面 9 门全 pass、无 blocker，
   但 `release-approval` 是 required 门且 human-only——发布窗口/回滚/
   观察期人工审批从未发生，cockpit 永不接受它（M14-91 §1.1 第 6 条），
   本切片不触碰、不代拟、绝不合成；**`release_ready=false` /
   `production_ready=false` 不变**，放行决定留 supervisor。
2. **turn-tls 仍 optional 未 stage**：公网语音发布形态才必需，本机/
   LAN 范围不阻断；`release_ready` 不得解释为公网语音就绪。
3. **provider-smoke pass 是 M14-117 切换轮的时点证据**
   （2026-09-23T23:29:35Z 三步冒烟聚合，m14-117-production 栈）：
   证明该时点三 provider 冒烟通过，不承诺窗口外状态；易失性由后续
   刷新切片覆盖（M14-98 先例）。本切片零冒烟执行、零 provider/
   生产接触。
4. **long-soak pass 是 M14-106 运维轮的时点证据且窗口早于生产切换**：
   窗口 2026-09-22T02:00:01Z → 2026-09-23T02:00:01Z（m14-70 栈）内
   97 样本全 ok——证明该窗口稳定性，不承诺窗口外、也不承诺切换后
   栈稳定性；切换后的 24h soak 窗口尚不存在，按任务边界本切片不
   重跑，是否需要切换后新 soak 窗口由 supervisor 决策（当前切换后
   稳定性佐证：M14-117 §6.2 监控轮 34 ok + M14-118 只读 watch）。
5. **production-state 门是「呈现」不是「重执行」**：staged 的五门 +
   anchor companion 均为各切片 canonical 时点快照（M14-83 @
   5829ad9、M14-85 @ f47a1e4、M14-87），本切片零生产触碰约束下不
   重推导、不搬运冒充新执行；其对 M14-117 切换后当前生产状态的
   语义效力由 M14-117 切换后 preflight（5/5 pass，§2.5）与 supervisor
   结合生产变更记录判断，cockpit 提供声明通道显式表达。M14-117 的
   preflight JSON 因缺 `gate` 自声明不能直接作为门证据 stage（契约
   fail-closed，非本切片补改）。
6. **生产当前运行 m14-117-production**（M14-117 于 2026-09-24 切换），
   本切片零调度面触碰、不构成任何部署；不授权任何部署。
7. **代码绑定门证据只对其执行时点的树成立**：ci-main/release-check
   现对 2b1c2dd current；main 再前移即再 stale（M14-91 冒烟已实证
   该语义），需下一个刷新切片真实重推导。
8. **GitHub Actions run 有平台保留期**（默认 90 天）；到期后按 §2.1
   命令重取即可。
