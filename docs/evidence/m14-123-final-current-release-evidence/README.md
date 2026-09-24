# M14-123：final-current 发布证据刷新（PR #209 后；ci-main + release-check 真实重跑 + provider-smoke/long-soak 契约内只读复用 + cockpit 零 blocker 聚合）

- 切片：分支 `ops/m14-123-final-current-release-evidence`（独立 worktree
  `m14-123-final-current-release-evidence`，基于 main
  `d7072fdcd97b233a58386693e27bcd8288975cca`（PR #209 merge = M14-122
  合入，精确基点 = 当前 HEAD），单 local commit，push 分支并向 main 开
  PR（任务书指令）。
- 动因与定性：M14-122 的证据绑定执行基点 2b1c2dd，其 supervisor 合并轮
  引入 origin/main `f67cbe2`（含 M14-121 真实测试文件
  `test_monitoring_alert_dispatch_runtime.py`）后，代码绑定门相对合并头
  即再 stale。本切片在**当前最终 HEAD `d7072fd`** 上真实重执行刷新——
  这是 final-current 口径：证据基点即本切片执行树基点，无任何中间
  merge 漂移。
- 目标：基于 M14-91/M14-116/M14-122 既有契约刷新 current main 的发布
  证据链。ci-main 与 release-check 两门**本轮真实重执行**（远端 CI 事实
  核验 + 干净 d7072fd 执行树上的隔离 full 跑）；**provider-smoke 只读
  复用 M14-117 生产切换后的真实三步冒烟聚合**（与 M14-122 同一源、同
  一 SHA256，源哈希与语义仍有效的契约内复用——生产仍运行
  m14-117-production 栈，无新切换，无新冒烟窗口指令）；long-soak 沿
  M14-107/M14-116/M14-122 同款只读复用（同哈希 `d939c652…`，原窗口
  时间边界如实呈现）。绝不手改 gate JSON、绝不合成 pass、绝不搬运与源
  哈希不符的旧 JSON 冒充 current、绝不重跑 provider/生产、绝不合成为
  切换后栈伪造的新 24h soak。**`release_ready=false` /
  `production_ready=false` 全程不变**——release-approval 是 human-only
  门、从未发生，cockpit 按策略永不接受它（not-staged 呈现、不计
  blocker）；turn-tls optional 不阻断。
- 零生产触碰（本切片全程）：零容器启停/重建、零计划任务操作、零
  DB/MinIO/语音引擎接触、零生产日志写入、零 secrets/env 读取、零
  soak 历史重跑/锚定写入、零发布审批接触、零部署、零 Ollama/FunASR/
  CosyVoice/SearXNG/代理/CC Switch 生命周期变更。唯一网络访问是
  GitHub 只读 API（`gh api`，已认证账号，经本地 HTTP 代理
  127.0.0.1:7892——网络 TLS/HTTP2 不稳时按任务书指定路径）+ worktree
  从零环境的包安装（uv/npm，日志归档 §5）。**provider-smoke 与
  long-soak 均不重跑**——两者为只读校验 + 哈希锁定复用（§2.3/§2.4），
  源文件零改动。
- 本 README 为唯一入库证据文件；canonical 原始证据（gitignored）位于
  worktree `.verify/artifacts/m14-123-final-current-release-evidence/`
  （8 份核心工件 + 执行日志/脚本，SHA256SUMS 索引 23 文件，自不含
  自哈希；§5）。

## 1. 基点事实（git/远端可复核）

| 事件 | commit | 说明 |
|------|--------|------|
| PR #206 merge（M14-119 合入） | `2b1c2dd4783166576f00faf5088e7391ab4cc173` | M14-122 执行基点 |
| PR #207 merge（M14-121 回环运行时闭环测试 +3） | `dcf0778` | 真实测试变更 |
| PR #208 merge（M14-120 Harmony current-main 回归证据） | `f67cbe2` | docs 为主（Harmony 侧证据，不在 services/api 测试面） |
| PR #209 merge（M14-122 证据入库 + supervisor 合并轮）→ **当前 main** | `d7072fdcd97b233a58386693e27bcd8288975cca` | 本切片精确基点 = final-current HEAD |
| main push CI run 35972347264 创建（push 事件） | d7072fd | 2026-09-24T07:56:11Z（UTC） |

- M14-122 证据 @ 2b1c2dd 对 d7072fd 已 stale（2b1c2dd..d7072fd 含
  M14-121 真实测试文件——`services/api/tests/`
  `test_monitoring_alert_dispatch_runtime.py` 3 个回环运行时测试，
  代码绑定门必须真实重执行）；M14-122 README 已如实预告本刷新
  （「刷新留待后续切片」），本切片即该收口。
- M14-122 canonical 保留为其时点历史记录不改写（其 worktree 只读取证，
  零修改）；本切片不搬运其 ci-main/release-check 产物——run
  35952447109 与 2b1c2dd 上的 release-check 结果均不复用。M14-83（@
  5829ad9 生产只读门三门）、M14-85（@ f47a1e4 backup-restore）、M14-87
  （@ audit-chain-anchor 门 + 伴生锚）canonical 按 M14-91 §5 登记路径
  **原样 staging**（§2.5）。
- **provider-smoke 复用源不变**：生产自 M14-117 切换后始终运行
  `m14-117-production` 栈，本切片零切换零 provider 接触，M14-117 切换后
  聚合（`029ee84f…`，564 bytes，generated_at 2026-09-23T23:29:35Z）
  仍是与当前生产状态对应的最新真实 pass 证据——按「源哈希与语义仍
  有效才复用」口径，与 M14-122 同源同哈希复用（§2.3）。
- long-soak 复用口径与 M14-107/M14-116/M14-122 完全相同：canonical
  817 bytes、SHA256
  `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec`
  （= M14-106 运维轮 supervisor 复核的 24h 窗口 pass 审计，窗口
  2026-09-22T02:00:01Z → 2026-09-23T02:00:01Z），只读校验后逐字节
  stage（§2.4）。**诚实提示：该窗口早于 M14-117 生产切换且执行于
  m14-70 旧栈**——切换后不存在新的 24h soak 窗口；切换后稳定性证据
  是 M14-117 §6.2 监控轮（34 ok/0 warn/0 critical）与 M14-118 只读
  watch，不是新的 soak。按任务边界本切片不重跑 soak、不制造新窗口。

## 2. 刷新执行记录（ci-main/release-check 为本轮新执行；provider-smoke/long-soak 为只读校验复用）

### 2.1 ci-main（真实远端 CI，非本地重跑）

```bash
gh api "repos/swsgbl/ai-learning-os/actions/runs?head_sha=d7072fdcd97b233a58386693e27bcd8288975cca"
gh api "repos/swsgbl/ai-learning-os/actions/runs/35972347264/jobs"
```

（均经本地 HTTP 代理 127.0.0.1:7892，stderr 捕获为空文件。）

- 命中断言（断言脚本复核归档 raw 响应而非仅信任务简报；任一断言
  失败即非零退出、不产出证据）：`workflow_runs` 中 total_count=1、
  event=push、head_branch=main 恰 1 条——run **35972347264**
  （run_number **539**），head_sha `d7072fd…`，status=completed，
  **conclusion=success**（created 2026-09-24T07:56:11Z / updated
  2026-09-24T08:01:23Z，
  https://github.com/swsgbl/ai-learning-os/actions/runs/35972347264）。
- jobs 断言：`total_count=5` 且全部 conclusion=success——API
  （ruff/pytest/migration）、Docker（compose build+healthy+smoke）、
  Release tools（Python tests）、Android（unit test/lint/assemble）、
  Web（test/typecheck/lint/build）；每 job head_sha == d7072fd、job 名
  集合精确匹配且无重复（与 M14-116/M14-122 同一五 job 契约）。
- 按 `_eval_ci_main` 契约（`services/api/app/ops/release_readiness.py`）
  **程序化派生** canonical `evidence/ci-main.json`（1202 bytes、
  SHA256 `d2a38c92…`）：断言驱动脚本从 raw 事实拼装 + 溯源 source 串；
  未复用 M14-122 的 run 35952447109 或其 raw 文件。
- raw API 响应原样归档：`raw/gh-runs-d7072fd.json`、
  `raw/gh-jobs-35972347264.json`（哈希见 §5；两份 stderr 捕获为空文件）。

### 2.2 release-check（隔离本地 full 重跑，干净 d7072fd 执行树）

从零构建全新隔离环境（worktree `.venv`，不复用任何前驱切片产物）：

```bash
uv venv .venv --python 3.12          # CPython 3.12.14
uv pip install -r services/api/requirements.txt -r services/api/requirements-dev.txt   # 59 packages
npm ci --no-audit --no-fund          # 411 packages（node v22.23.2 / npm 12.0.2）
```

（npm 提示 `unrs-resolver` postinstall 被 allowScripts 策略阻止——与
M14-92/M14-94/M14-97/M14-105/M14-107/M14-116/M14-122 相同的已知提示，
如实记录；该脚本产物非门禁依赖，本轮 10 门全绿证实无影响。）

**执行树前置核验**（declared-head 诚实性前提）：运行前
`git rev-parse HEAD` == `d7072fd…` 且 `git status --porcelain` 为空
（tracked-clean，仅 gitignored `.verify/`/`artifacts/` 增量，执行日志
归档留痕）——release-check 的实际执行树即精确干净 d7072fd，后续
cockpit `--gate-declared-head release-check=d7072fd` 声明成立。

隔离编排器（一次性 SQLite → alembic upgrade head → 127.0.0.1 临时
uvicorn（本轮动态端口 62936，已收尾 terminated）→ /health 就绪 →
full 10 门 → 原子落盘 → finally 关停临时 API；环境剥离
AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL，显式
APP_ENV=development / VOICE_MODE=local——不连接任何生产面；新工作区
`artifacts/m14-123-isolated/`）：

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli release-check-isolated \
  --workdir "<worktree>/artifacts/m14-123-isolated" --json
# exit 0
```

- 结果：**all_green=true，10/10 pass**（generated_at
  2026-09-24T10:25:35.954945+00:00，execution_scope=full）——api-lint
  （ruff All checks passed）/ web-lint / web-typecheck / web-build /
  **api-test（pytest 4555 passed, 33 skipped, 1 warning in 281.16s；
  较 M14-122 @ 2b1c2dd 的 4552 恰 +3——2b1c2dd..d7072fd 区间 M14-121
  新增 `test_monitoring_alert_dispatch_runtime.py` 的 3 个回环运行时
  测试的精确对账，实测吻合、无硬编码；M14-120 为 Harmony 侧证据
  切片，不改变 services/api 测试面）** / migration（alembic
  head==0027_audit_chain）/ backup（aios-backup-v1 tables:30 files:1）/
  voice（local 合成 audio/wav 17324 bytes）/ license（api deps 15 /
  web ok / models 7 / sources 6）/ e2e（walkthrough 5 步，1093 ms）。
- `release-check-isolated.json` 逐字节复制改名为 canonical
  `evidence/release-check.json`（2491 bytes；文件自带 gate 自标识
  `release-check`，非手改；复制后 filecmp 逐字节比对 IDENTICAL）。

### 2.3 provider-smoke（不重跑、不触碰生产/provider；只读校验 + 哈希锁定复用 M14-117 切换后聚合）

- 复用源：**M14-117 生产切换轮固化的切换后真实聚合产物**
  `provider-smoke.json`（M14-117 README §10 登记 SHA256
  `029ee84f671457e5a379f56be39cebd16b306ba4711e0717aa3a5f5bdcfd163b`、
  564 bytes；generated_at 2026-09-23T23:29:35.556863+00:00——晚于
  2026-09-23T23:14:45Z 的切换后 preflight，即执行于
  `m14-117-production` 新栈上）。**与 M14-122 所 stage 的是同一源文件
  同一哈希**——生产栈自 M14-117 切换后未再变更，该源的语义有效性
  （对应当前生产状态）未被任何后续切换事件打破，按既有契约
  （「源哈希与语义仍有效才复用」）本切片复用成立。
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
  §7 / M14-116 §6.3 / M14-122 §6.3 口径保持——**原始时间边界显式：
  2026-09-23T23:29:35Z，距本切片聚合时点约 35 小时，窗口外状态不
  承诺**。

### 2.4 long-soak（不重跑、不触碰生产/计划任务；只读校验 + 逐字节复制）

- 复用口径与 M14-107 §2.3 / M14-116 §2.4 / M14-122 §2.4 完全一致：
  canonical 817 bytes、SHA256
  `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec`
  （与 M14-122 §5 登记值一致，大小写不敏感）——即 M14-106 运维轮
  supervisor 复核的真实 24h 稳定窗口审计（M14-107 首次 stage、
  M14-116/M14-122 复用的同一证据）。
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
  效力由 supervisor 结合切换后监控/巡检证据（M14-117 §6.2、M14-118）
  判断。本切片零 soak 接触：未运行 `soak_stability_audit.py`、未触碰
  history/锚定/计划任务/监控管道。

### 2.5 evidence-cockpit 聚合（四门刷新/复用后重新汇总：cockpit_ready=true 保持）

在 tracked docs 编辑**之前**、HEAD 仍为干净 `d7072fd` 时运行。canonical
源路径逐一取自 M14-91 README §5 / staged-inventory 登记与 M14-122
§2.5 同一路径（不猜测）；staging 前先对 6 个既有生产状态源文件做
sha256 复核——与 M14-91/M14-116/M14-122 登记值逐一 **MATCH（6/6）**：
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
  --gate-declared-head release-check=d7072fdcd97b233a58386693e27bcd8288975cca \
  --current-head d7072fdcd97b233a58386693e27bcd8288975cca \
  --staging-dir "<worktree>/artifacts/m14-123-cockpit-staging" \
  --json --output "<worktree>/artifacts/m14-123-cockpit-report.json"
# exit 0（cockpit_ready=true）
```

- **current-head 与 release-check head 显式声明
  `d7072fdcd97b233a58386693e27bcd8288975cca`**；`--gate-declared-head
  release-check` 的合法性由 §2.2 前置核验支撑（实际执行树即干净
  d7072fd；ci-main 则由文件内嵌 merge_commit==current HEAD 自动
  current）。
- 结果（报告归档 canonical `cockpit-report.json`，generated_at
  2026-09-24T10:29:13Z）：**evaluator pass=9 / pending=0 / blocked=0
  / missing=2 / malformed=0 / tampered=0**；**cockpit_ready=true、
  cockpit_blockers=[]、required_not_staged=[]、exit 0**。同时
  **release_ready=false、production_ready=false（恒 false）**：
  not_pass_required 恰为 `release-approval`（human-only 从未发生，
  cockpit 按策略永不接受、不计入 blocker——「无技术 blocker」与
  「不可放行」两件事同时成立），not_pass_optional 为 `turn-tls`
  （optional：本机/LAN 发布形态不需要公网 TURN，不计入 fail）。
- **code-bound 两门刷新生效**：ci-main stale=**current**（内嵌
  merge_commit=d7072fd == current HEAD）、release-check stale=
  **current**（flag 声明 d7072fd）。
- **provider-smoke 以 M14-117 切换后聚合 staged 即 pass**（经
  `_eval_provider_smoke` 全语义校验）；long-soak staged pass（真实
  24h 稳定窗口，§2.4）。production-state 五门（audit-chain-anchor /
  production-preflight / legacy-papers / draft-ownership /
  backup-restore）+ anchor companion 以历史 canonical snapshot 原样
  staged、undeclared 如实呈现不 block（M14-91 §1.1 第 5 条口径）；
  release-approval「按策略永不接受，非 blocker」、turn-tls「optional
  不阻断」摘要显式区分。
- **production-preflight 门的取源说明（诚实记录，与 M14-122 同）**：
  M14-117 切换后 preflight（5/5 pass，2026-09-23T23:14:45Z，canonical
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
  值交叉一致、本切片 4 个新 stage 文件（ci-main `d2a38c92…` 1202
  bytes / release-check `905a6a95…` 2491 bytes / provider-smoke
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
  --basetemp "D:/AI Learning OS/.pytest-tmp/m14-123-final-current-release-evidence/focused"
# 407 passed, 1 warning in 7.08s
```

（Windows 已知形态：pytest `--basetemp` 不创建缺失父目录链，本切片
按 M14-116/M14-122 教训**先 `mkdir -p` 预建父目录**，一次通过。）
- `ruff check services/api`：All checks passed（tracked Python 零改动，
  基线复核；本切片无 Python 源码变更，py_compile 不适用——入库文件
  仅 docs/evidence README 与三本台账）。
- canonical 完整性：`sha256sum -c SHA256SUMS` **23/23 OK**（本切片
  索引为 LF 行尾，直接核验通过；自不含自哈希）；canonical JSON
  `json.load` 解析全通过（ci-main/release-check/provider-smoke/
  long-soak/cockpit-report/两 raw/cockpit-stdout）；JSON 契约断言脚本
  **22 项全过**（ci-main run_id/merge_commit/conclusion、raw runs
  push@main 唯一/run_number 539、raw jobs 5/5 精确集合、release-check
  all_green 10/10 + pytest 4555/33 + 对 4552 增量 +3 对账、
  provider-smoke local 拓扑三槽位 pass + 原始时间戳边界、long-soak
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

## 4. 与 M14-122 的差异（同一契约下的净变化）

| 维度 | M14-122 @ 2b1c2dd | M14-123 @ d7072fd |
|------|-------------------|-------------------|
| 证据定性 | current-main（基点后被 supervisor 合并轮超越） | **final-current（证据基点 = 切片执行基点 = 当前 HEAD，无中间漂移）** |
| ci-main run | 35952447109（run_number 533） | **35972347264**（run_number 539，本轮新派生） |
| release-check pytest | 4552 passed / 33 skipped（275.77s） | **4555 passed / 33 skipped**（281.16s，+3 = M14-121 回环运行时测试对账） |
| provider-smoke 复用源 | M14-117 切换后聚合（`029ee84f…`） | **同源同哈希复用（生产栈未变，语义有效性保持）** |
| long-soak | staged pass（同哈希 `d939c652…`） | **staged pass（同哈希只读复用；窗口早于切换且在 m14-70 栈，如实呈现）** |
| evaluator | pass=9 / missing=2 | **pass=9 / missing=2（不变）** |
| cockpit blockers | [] | **[]（无技术 blocker）** |
| cockpit_ready / exit | true / 0 | **true / 0（保持）** |
| release_ready / production_ready | false / false | **false / false（不变——release-approval human-only 缺席）** |
| production-state 五门 + anchor | 原样 staged（同源同哈希） | **原样 staged（同源同哈希，6/6 MATCH 复核）** |
| staged 文件 | 10（9 门 + anchor companion） | **10（同，10/10 IDENTICAL）** |
| 交付形态 | 单 local commit → supervisor 合并轮 push + PR #209 | **单 local commit，本切片直接 push 分支 + 开 PR（任务书指令）** |

## 5. canonical 证据清单（gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：worktree `.verify/artifacts/m14-123-final-current-release-evidence/`

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/ci-main.json` | 1202 | `d2a38c92700b2563cee30cd387bac262afd6abf9647377ae71cb2da442cf0042` |
| `evidence/release-check.json` | 2491 | `905a6a95a963da42157d0b41eb48e21ded489ceefdf25251a0eb84de14b81359` |
| `evidence/provider-smoke.json` | 564 | `029ee84f671457e5a379f56be39cebd16b306ba4711e0717aa3a5f5bdcfd163b` |
| `evidence/long-soak.json` | 817 | `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec` |
| `raw/gh-runs-d7072fd.json` | 12238 | `a3ae376a00b188cdae0dfafa464b3aa1b01508985f12fcca1dff63f1bbd61c67` |
| `raw/gh-jobs-35972347264.json` | 12334 | `e1e40f88534f44823a0a56013faf566eb5e322323b10b3d7f4bc1f3f3e60e5ee` |
| `cockpit-report.json` | 19012 | `4f2f336d4f3fa5d9ea77d9716ee9c84b0fab8268cab66398d577c3919ed8ca09` |
| `staged-inventory.txt` | 2786 | `df3979683cf8ed261a20095858670eaacc4c6065e61adafe02719a0280ea977e` |

（另 `logs/` 下执行日志与断言/派生脚本 13 份（env-setup/
derive-ci-main + 派生脚本/prepare-reused-evidence + 校验脚本/
release-check-isolated 双流/cockpit 双流/verify-staging + 复核脚本/
assert-contracts + 断言脚本）+ `raw/*.stderr.txt` 两份空捕获
（SHA256 `e3b0c442…` = 空文件标准值）+ `SHA256SUMS` 23 文件索引，
自不含自哈希；worktree 侧 `artifacts/m14-123-isolated/`、
`artifacts/m14-123-cockpit-staging/`、
`artifacts/m14-123-cockpit-report.json` 为生成现场，gitignored，
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
   证明该时点三 provider 冒烟通过，不承诺窗口外状态（距本切片聚合
   已约 35 小时）；易失性由后续刷新切片覆盖（M14-98 先例）。本切片
   零冒烟执行、零 provider/生产接触。
4. **long-soak pass 是 M14-106 运维轮的时点证据且窗口早于生产切换**：
   窗口 2026-09-22T02:00:01Z → 2026-09-23T02:00:01Z（m14-70 栈）内
   97 样本全 ok——证明该窗口稳定性，不承诺窗口外、也不承诺切换后
   栈稳定性；切换后的 24h soak 窗口尚不存在，按任务边界本切片不
   重跑、**不制造新窗口**，是否需要切换后新 soak 窗口由 supervisor
   决策（当前切换后稳定性佐证：M14-117 §6.2 监控轮 34 ok + M14-118
   只读 watch）。
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
   现对 d7072fd current（= 本切片基点 = final-current）；main 再前移
   即再 stale（M14-91 冒烟已实证该语义），需下一个刷新切片真实重
   推导。
8. **GitHub Actions run 有平台保留期**（默认 90 天）；到期后按 §2.1
   命令重取即可。
