# M14-116：current-main 发布证据刷新（ci-main + release-check 真实重跑 + provider-smoke 首次 stage + long-soak 只读复用 + cockpit 零 blocker 聚合）

- 切片：分支 `ops/m14-116-current-main-release-evidence`（独立 worktree
  `m14-116-current-main-release-evidence`，基于 main
  `2619ea77f3db291901e48eacdeea064b6f9b6fdb`（PR #202 merge = M14-115
  合入，精确基点），单 commit（本切片按任务指令 push 分支并开 PR——与
  M14-105/M14-107 的不 push 口径不同，交付形态以任务书为准）。
- 目标：基于 M14-107/M14-115 既有契约刷新 current main 的发布证据链。
  ci-main 与 release-check 两门**本轮真实重执行**（远端 CI 事实核验 +
  干净 2619ea7 执行树上的隔离 full 跑）；**provider-smoke 首次以真实
  pass 证据进入 cockpit 聚合**（M14-115 supervisor 生产恢复轮 2026-09-23
  三步冒烟聚合产物，SHA256 逐字节一致复用，绝不改写源文件）；long-soak
  沿 M14-107 同款只读复用（同哈希 `d939c652…`）。绝不手改 gate JSON、
  绝不合成 pass、绝不搬运与源哈希不符的旧 JSON 冒充 current。
  **`release_ready=false` / `production_ready=false` 全程不变**——
  release-approval 是 human-only 门、从未发生，cockpit 按策略永不接受
  它（not-staged 呈现、不计 blocker）；本轮 9 门 pass 之下唯一未闭合的
  required 门正是该审批门，诚实语义保持。
- 零生产触碰（本切片全程）：零容器启停/重建、零计划任务操作、零
  DB/MinIO/语音引擎接触、零生产日志写入、零 secrets/env 读取、零
  soak 历史重跑/锚定写入、零发布审批接触、零部署、零 Ollama/FunASR/
  CosyVoice/SearXNG/代理/CC Switch 生命周期变更。唯一网络访问是
  GitHub 只读 API（`gh api`，已认证账号）+ worktree 从零环境的包安装
  （uv/npm，日志归档 §5）。**long-soak 与 provider-smoke 均不重跑**——
  两者为只读校验 + 哈希锁定复用（§2.3/§2.4），源文件零改动。
- 本 README 为唯一入库证据文件；canonical 原始证据（gitignored）位于
  worktree `.verify/artifacts/m14-116-current-main-release-evidence/`
  （18 文件 + SHA256SUMS 索引，自不含自哈希；§5）。

## 1. 基点事实（git/远端可复核）

| 事件 | commit | 说明 |
|------|--------|------|
| PR #201 merge（M14-114 China Bing 直连引擎） | `cf0a512` | M14-115 基点 |
| PR #202 merge（M14-115 provider-smoke 恢复固化 + preflight 超时分档）→ **当前 main** | `2619ea77f3db291901e48eacdeea064b6f9b6fdb` | 本切片精确基点 |
| main push CI run 35921247552 创建（push 事件） | 2619ea7 | 2026-09-23T21:15:02Z（UTC） |

- M14-107 证据 @ 22a9cbf 对 2619ea7 已 stale（22a9cbf..2619ea7 多个
  PR merge，含 PR #202 M14-115 的**真实代码变更**——preflight search
  探测超时 30s 分档 + 新增 1 个契约测试），代码绑定门必须真实重执行，
  这是本切片的直接动因。
- M14-107 canonical 保留为其时点历史记录不改写；本切片不搬运其
  ci-main/release-check 产物。M14-83（@ 5829ad9 生产只读门三门）、
  M14-85（@ f47a1e4 backup-restore）、M14-87（@ audit-chain-anchor 门 +
  伴生锚）canonical 按 M14-91 §5 登记路径**原样 staging**（§2.5）。
- **provider-smoke 首次具备与 current 生产状态对应的 pass 聚合证据**：
  M14-115 已把 supervisor 生产恢复（searxng 单容器 recreate + China
  Bing 聚合恢复）后的真实三步冒烟（search/voice/llm 全 pass）固化为
  `provider-smoke-aggregate` 三项 pass 产物（generated_at
  2026-09-23T18:57:57Z，拓扑 local，脱敏结果文件）。本切片只读校验其
  SHA256 与 M14-115 登记（`899feecb…`，564 bytes）逐字节一致后原样
  stage（§2.3）——这是 provider-smoke 门**首次以真实 pass 证据进入
  cockpit 聚合**（M14-107 时该门因 M14-104 失败轮未 stage 而为唯一
  blocker）。
- long-soak 复用口径与 M14-107 完全相同：canonical 817 bytes、SHA256
  `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec`
  （= M14-106 运维轮 supervisor 复核的 24h 窗口 pass 审计，窗口
  2026-09-22T02:00:01Z → 2026-09-23T02:00:01Z），只读校验后逐字节
  stage（§2.4）。

## 2. 刷新执行记录（ci-main/release-check 为本轮新执行；provider-smoke/long-soak 为只读校验复用）

### 2.1 ci-main（真实远端 CI，非本地重跑）

```bash
gh api "repos/swsgbl/ai-learning-os/actions/runs?head_sha=2619ea77f3db291901e48eacdeea064b6f9b6fdb"
gh api "repos/swsgbl/ai-learning-os/actions/runs/35921247552/jobs"
```

- 命中断言（断言脚本复核归档 raw 响应而非仅信任务简报；任一断言
  失败即非零退出、不产出证据）：`workflow_runs` 中 total_count=1、
  event=push、head_branch=main 恰 1 条——run **35921247552**
  （run_number **525**），head_sha `2619ea7…`，status=completed，
  **conclusion=success**（created 2026-09-23T21:15:02Z / updated
  2026-09-23T21:19:48Z，
  https://github.com/swsgbl/ai-learning-os/actions/runs/35921247552）。
- jobs 断言：`total_count=5` 且全部 conclusion=success——API
  （ruff/pytest/migration）、Docker（compose build+healthy+smoke）、
  Release tools（Python tests）、Android（unit test/lint/assemble）、
  Web（test/typecheck/lint/build）；每 job head_sha == 2619ea7、job 名
  集合精确匹配且无重复。
- 按 `_eval_ci_main` 契约（`services/api/app/ops/release_readiness.py`）
  **程序化派生** canonical `evidence/ci-main.json`（1193 bytes、
  SHA256 `ace1adfa…`）：断言驱动脚本从 raw 事实拼装 + 溯源 source 串；
  未复用 M14-107 的 run 35811421324 或其 raw 文件。
- raw API 响应原样归档：`raw/gh-runs-2619ea7.json`、
  `raw/gh-jobs-35921247552.json`（哈希见 §5）。

### 2.2 release-check（隔离本地 full 重跑，干净 2619ea7 执行树）

从零构建全新隔离环境（worktree `.venv`，不复用任何前驱切片产物）：

```bash
uv venv .venv --python 3.12          # CPython 3.12.14
uv pip install -r services/api/requirements.txt -r services/api/requirements-dev.txt   # 59 packages
npm ci --no-audit --no-fund          # 411 packages（node v22 / npm 12）
```

（npm 提示 `unrs-resolver` postinstall 被 allowScripts 策略阻止——与
M14-92/M14-94/M14-97/M14-105/M14-107 相同的已知提示，如实记录；该
脚本产物非门禁依赖，本轮 10 门全绿证实无影响。）

**执行树前置核验**（declared-head 诚实性前提）：运行前
`git rev-parse HEAD` == `2619ea7…` 且 `git status --porcelain` 为空
（tracked-clean，仅 gitignored `.verify/`/`artifacts/` 增量）——
release-check 的实际执行树即精确干净 2619ea7，后续 cockpit
`--gate-declared-head release-check=2619ea7` 声明成立。

隔离编排器（一次性 SQLite → alembic upgrade head → 127.0.0.1 临时
uvicorn（本轮动态端口 65364，已收尾 terminated）→ /health 就绪 →
full 10 门 → 原子落盘 → finally 关停临时 API；环境剥离
AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL，显式
APP_ENV=development / VOICE_MODE=local——不连接任何生产面；新工作区
`artifacts/m14-116-isolated/`）：

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli release-check-isolated \
  --workdir "<worktree>/artifacts/m14-116-isolated" --json
# exit 0
```

- 结果：**all_green=true，10/10 pass**（generated_at
  2026-09-23T21:45:36Z，execution_scope=full）——api-lint（ruff All
  checks passed）/ web-lint / web-typecheck / web-build /
  **api-test（pytest 4433 passed, 33 skipped, 1 warning in 269.80s；
  较 M14-107 @ 22a9cbf 的 4233 恰 +200——22a9cbf..2619ea7 区间
  M14-108..M14-115 各切片新增测试的累计对账，实测吻合、无硬编码）** /
  migration（alembic head==0027_audit_chain）/ backup
  （aios-backup-v1 tables:30 files:1）/ voice（local 合成 audio/wav
  17324 bytes）/ license（api deps 15 / web ok / models 7 / sources
  6）/ e2e（walkthrough 5 步，1003 ms）。
- `release-check-isolated.json` 逐字节复制改名为 canonical
  `evidence/release-check.json`（2491 bytes；文件自带 gate 自标识
  `release-check`，非手改）。

### 2.3 provider-smoke（不重跑、不触碰生产/provider；只读校验 + 哈希锁定复用）

- 复用源：M14-115 固化的 supervisor 生产恢复轮真实聚合产物
  `provider-smoke.json`（M14-115 README §6 登记 SHA256
  `899feecb67285ccc4bd940462c4dc1944afa6dcfb6bbc5cc864ad9ddaf7ecdf6`、
  564 bytes；generated_at 2026-09-23T18:57:57.852787+00:00）。
- 本切片只读校验后逐字节复制为 canonical `evidence/provider-smoke.json`
  ——复制后重哈希 **逐字节一致（564 bytes 同哈希）** + JSON 契约断言：
  schema `provider-smoke-evidence-v1`、gate 自声明 `provider-smoke`、
  拓扑 `voice_mode=local`、voice/search/llm 三槽位 executed=true 且
  result=pass（evidence_step local-voice-smoke / search-smoke /
  llm-smoke）。执行记录：`logs/prepare-reused-evidence.log`。
- 该 pass 证据的执行与 supervisor 复核属 M14-115 运维轮（search 冒烟
  10342ms results=5、local-voice ASR 2682ms/42 bytes + TTS 22490ms/
  241964 bytes RIFF WAV、llm MAX_TOKENS=1024 正文 12 chars rubric
  [True,True] conf=1.0，含如实保留的失败 attempt——详见 M14-115
  README）；本切片零冒烟执行、零 provider 接触，只做来源哈希锁定 +
  字节原样 stage。provider 冒烟的易失性（时点证据不承诺未来状态）
  按 M14-115 §7 口径保持。

### 2.4 long-soak（不重跑、不触碰生产/计划任务；只读校验 + 逐字节复制）

- 复用口径与 M14-107 §2.3 完全一致：canonical 817 bytes、SHA256
  `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec`
  （与任务指定值一致，大小写不敏感）——即 M14-106 运维轮 supervisor
  复核的真实 24h 稳定窗口审计（M14-107 首次 stage 的同一证据）。
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
- 本切片零 soak 接触：未运行 `soak_stability_audit.py`、未触碰
  history/锚定/计划任务/监控管道。

### 2.5 evidence-cockpit 聚合（四门刷新/复用后重新汇总：首次 cockpit_ready=true）

在 tracked docs 编辑**之前**、HEAD 仍为干净 `2619ea7` 时运行。canonical
源路径逐一取自 M14-91 README §5 / staged-inventory 登记与 M14-107
§2.4 同一路径（不猜测）；staging 前先对 6 个既有生产状态源文件做
sha256 复核——与 M14-91 登记值逐一 **MATCH（6/6）**：
audit-chain-anchor `a8c54c5e…` / audit-anchor companion `d2bfd877…` /
backup-restore `ed0fc5b4…` / preflight `b3a2be66…` / legacy
`83cd62bd…` / draft `e90ae04c…`。

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
  --gate-declared-head release-check=2619ea77f3db291901e48eacdeea064b6f9b6fdb \
  --current-head 2619ea77f3db291901e48eacdeea064b6f9b6fdb \
  --staging-dir "<worktree>/artifacts/m14-116-cockpit-staging" \
  --json --output "<worktree>/artifacts/m14-116-cockpit-report.json"
# exit 0（cockpit_ready=true，首次——见下）
```

- **current-head 与 release-check head 显式声明
  `2619ea77f3db291901e48eacdeea064b6f9b6fdb`**；`--gate-declared-head
  release-check` 的合法性由 §2.2 前置核验支撑（实际执行树即干净
  2619ea7；ci-main 则由文件内嵌 merge_commit==current HEAD 自动
  current）。
- 结果（报告归档 canonical `cockpit-report.json`，generated_at
  2026-09-23T21:51:31Z）：**evaluator pass=9 / pending=0 / blocked=0
  / missing=2 / malformed=0 / tampered=0**；**cockpit_ready=true、
  cockpit_blockers=[]、required_not_staged=[]、exit 0——M14-91
  cockpit 落地以来首次**。同时 **release_ready=false、
  production_ready=false（恒 false）**：not_pass_required 恰为
  `release-approval`（human-only 从未发生，cockpit 按策略永不接受、
  不计入 blocker——「无技术 blocker」与「不可放行」两件事同时成立），
  not_pass_optional 为 `turn-tls`（optional：本机/LAN 发布形态不需要
  公网 TURN，不计入 fail）。
- **code-bound 两门刷新生效**：ci-main stale=**current**（内嵌
  merge_commit=2619ea7 == current HEAD）、release-check stale=
  **current**（flag 声明 2619ea7）。
- **provider-smoke 首次 staged 即 pass**（M14-115 真实聚合证据经
  `_eval_provider_smoke` 全语义校验）；long-soak staged pass（真实
  24h 稳定窗口，§2.4）。production-state 五门（audit-chain-anchor /
  production-preflight / legacy-papers / draft-ownership /
  backup-restore）+ anchor companion 以历史 canonical snapshot 原样
  staged、undeclared 如实呈现不 block（M14-91 §1.1 第 5 条口径）；
  release-approval「按策略永不接受，非 blocker」、turn-tls「optional
  不阻断」摘要显式区分。
- **staged 10 文件（9 门 + anchor companion）与 source 逐字节一致**：
  工具内建写后重读断言 + 外部独立复核 **10/10 IDENTICAL**（filecmp
  语义逐字节比较）；6 个既有文件哈希与 M14-91 staged-inventory 登记
  值交叉一致、本切片 4 个新 stage 文件（ci-main `ace1adfa…` 1193
  bytes / release-check `a6576053…` 2491 bytes / provider-smoke
  `899feecb…` 564 bytes / long-soak `d939c652…` 817 bytes）与
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
  --basetemp "D:/AI Learning OS/.pytest-tmp/m14-116-current-main-release-evidence/focused2"
# 407 passed, 1 warning in 6.83s
```

（Windows 注意：pytest `--basetemp` 不会自动创建缺失的**父目录链**，
必须先 `mkdir -p` 到 basetemp 本身所在的父级，否则全部 tmp-fixture
测试以 FileNotFoundError WinError 3 setup-error——本切片实测先 270
errors、预建父目录后同命令全绿，环境性根因已定位，如实记录。）
- `ruff check services/api`：All checks passed（tracked Python 零改动，
  基线复核）。
- canonical 完整性：`tr -d '\r' < SHA256SUMS | sha256sum -c -` **18/18
  OK**（SHA256SUMS 为 CRLF 行尾，核验需先剥离——M14-50 已知形态）；
  canonical JSON `json.load` 解析通过 8 份（ci-main/release-check/
  provider-smoke/long-soak/cockpit-report/两 raw/cockpit-stdout）；
  JSON 契约断言脚本 **53 项全过**（ci-main run_id/merge_commit/
  conclusion、raw runs push@main 唯一/run_number 525、raw jobs 5/5
  精确集合、release-check all_green 10/10 + pytest 4433/33、
  provider-smoke local 拓扑三槽位 pass、long-soak 策略四值 + 97 ok +
  max gap 17.25 + span 1440、cockpit 9 pass/2 missing + approval
  not-staged + release_ready/production_ready false、staged-inventory
  10/10 IDENTICAL）。
- 秘密扫描：canonical 18 文件对 credential 赋值 / OpenAI 风格 key /
  私钥块 / 带凭据 DB URL / URL userinfo 五类模式扫描 **0 命中**
  （cockpit-report/staged-inventory 内的 Windows 绝对路径为 gitignored
  本地工件路径，非秘密）。
- `git diff --check` 干净（docs-only）；分支卫生：单 commit 后
  tracked-clean（worktree 侧 `.venv`/`node_modules`/`artifacts/`/
  `.verify/` 均 gitignored 未入库）。

## 4. 与 M14-107 的差异（同一契约下的净变化）

| 维度 | M14-107 @ 22a9cbf | M14-116 @ 2619ea7 |
|------|-------------------|-------------------|
| ci-main run | 35811421324（run_number 509） | **35921247552**（run_number 525，本轮新派生） |
| release-check pytest | 4233 passed / 33 skipped（296.02s） | **4433 passed / 33 skipped**（269.80s，+200 = 区间各切片新增测试累计对账） |
| provider-smoke | not-staged → 唯一 blocker | **staged pass**（M14-115 真实聚合证据复用，§2.3） |
| long-soak | staged pass（首 stage） | staged pass（同哈希 `d939c652…` 只读复用，§2.4） |
| evaluator | pass=8 / missing=3 | **pass=9 / missing=2** |
| cockpit blockers | `provider-smoke:not-staged-required`（唯一） | **[]（无技术 blocker）** |
| cockpit_ready / exit | false / 1（诚实预期） | **true / 0**（required 全 staged 且 pass） |
| release_ready / production_ready | false / false | **false / false（不变——release-approval human-only 缺席）** |
| production-state 五门 + anchor | 原样 staged（同源同哈希） | 原样 staged（同源同哈希，6/6 MATCH 复核） |
| staged 文件 | 9（8 门 + anchor companion） | **10**（9 门 + anchor companion，10/10 IDENTICAL） |
| 交付形态 | 单 local commit 不 push | 单 commit + push 分支 + 开 PR（任务书指令） |

## 5. canonical 证据清单（gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：worktree `.verify/artifacts/m14-116-current-main-release-evidence/`

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/ci-main.json` | 1193 | `ace1adfaef05914e999eda38a5b86c342e216fac727e168c0773dd8c87f0f604` |
| `evidence/release-check.json` | 2491 | `a6576053b99b0ece91df9e6c84e02da83dc27a24489afecdf541ef2eb2fd4268` |
| `evidence/provider-smoke.json` | 564 | `899feecb67285ccc4bd940462c4dc1944afa6dcfb6bbc5cc864ad9ddaf7ecdf6` |
| `evidence/long-soak.json` | 817 | `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec` |
| `raw/gh-runs-2619ea7.json` | 12212 | `db6a72e27937481135e737b548d0782d6d6922aefb203b2a6ca38cdc00b4177e` |
| `raw/gh-jobs-35921247552.json` | 12334 | `3a61723f8571010c1caa360f6492faf266dab71a029509bf74e9a16eeb604ead` |
| `cockpit-report.json` | 19002 | `c7a4209d8dc8e1eba9448d1b6ddcdbddff3e7d8352c53b68219b88b355cab921` |
| `staged-inventory.txt` | 2788 | `01c551d3e2e54c4167fe17dcd123a7375b48c8bad28d602a3b8249a31ee3c840` |

（另 `logs/` 八份执行日志（venv-setup/npm-ci/derive-ci-main/
prepare-reused-evidence/release-check-isolated 双流/cockpit 双流）+
`raw/*.stderr.txt` 两份空捕获（SHA256 `e3b0c442…` = 空文件标准值）+
`SHA256SUMS` 18 文件索引，自不含自哈希；worktree 侧
`artifacts/m14-116-isolated/`、`artifacts/m14-116-cockpit-staging/`、
`artifacts/m14-116-cockpit-report.json` 为生成现场，gitignored，
不入库。）

## 6. 诚实边界与剩余门（不伪称，逐项可执行收口）

1. **cockpit_ready=true ≠ 可发布**：技术面 9 门全 pass、无 blocker，
   但 `release-approval` 是 required 门且 human-only——发布窗口/回滚/
   观察期人工审批从未发生，cockpit 永不接受它（M14-91 §1.1 第 6 条），
   本切片不触碰、不代拟、绝不合成；**`release_ready=false` /
   `production_ready=false` 不变**，放行决定留 supervisor。
2. **turn-tls 仍 optional 未 stage**：公网语音发布形态才必需，本机/
   LAN 范围不阻断；`release_ready` 不得解释为公网语音就绪。
3. **provider-smoke pass 是 M14-115 运维轮的时点证据**（2026-09-23
   18:57Z 三步冒烟聚合）：它证明该时点三 provider 冒烟通过，不承诺
   窗口外状态；易失性由后续刷新切片覆盖（M14-98 先例）。本切片零
   冒烟执行、零 provider/生产接触。
4. **long-soak pass 是 M14-106 运维轮的时点证据**：窗口
   2026-09-22T02:00:01Z → 2026-09-23T02:00:01Z 内 97 样本全 ok——
   证明该窗口的生产稳定性，不承诺窗口外状态，也不被本切片重新执行。
5. **production-state 门是「呈现」不是「重执行」**：staged 的五门 +
   anchor companion 均为各切片 canonical 时点快照（M14-83 @
   5829ad9、M14-85 @ f47a1e4、M14-87），本切片零生产触碰约束下不
   重推导、不搬运冒充新执行；其对 current 生产状态的效力由
   supervisor 结合生产变更记录判断，cockpit 提供声明通道显式表达。
6. **生产仍运行 m14-70 镜像**（2026-09-19 切换后未再变更），本切片
   零调度面触碰、不构成任何部署；不授权任何部署。
7. **代码绑定门证据只对其执行时点的树成立**：ci-main/release-check
   现对 2619ea7 current；main 再前移即再 stale（M14-91 冒烟已实证
   该语义），需下一个刷新切片真实重推导。
8. **GitHub Actions run 有平台保留期**（默认 90 天）；到期后按 §2.1
   命令重取即可。
