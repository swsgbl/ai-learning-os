# M14-219：current-main 发布证据刷新（release-check 恢复全绿；provider-smoke 诚实 blocked）

## 0. 结论与边界

- 切片：worktree
  `ai-learning-os-worktrees/m14-219-current-main-release-evidence`，分支
  `docs/m14-219-current-main-release-evidence`，基于 current main
  `df3d9969bb7195f819a5e2e4cf0ab36c083933eb`（PR #307 merge）。执行前
  branch/HEAD/tracked-clean 核验留痕 `logs/pre-head.log`（UTC
  2026-10-02T22:35:01Z）；本切片只入库本 README 与三份台账，交付单
  local commit，不 push、不开 PR、不合并。
- 刷新动因：M14-215 代码绑定门绑定 `dcb8d380`。其后
  `dcb8d380..df3d9969` 为 **8 commits / 23 files +1571/−63**，其中
  M14-216 bash executor、M14-217 release-check UTF-8 capture 与
  M14-218 provider-smoke LLM 预算是真实的 API 运行时、编排与测试变更。
  该区间不是 docs-only，M14-215 的 ci-main 与 release-check 证据均
  stale，必须在当前 main 真实刷新。
- 诚实结果：**ci-main pass**（远端 run 37070706211，5/5 jobs success）；
  **release-check pass**（full isolated 真实重跑，exit 0，
  all_green=true，10/10）；**provider-smoke fail**（只读复用 M14-209
  最新真实聚合：voice pass，search fail，llm fail）。cockpit 聚合为
  **cockpit_ready=false / blockers=[provider-smoke:blocked]**，readiness
  pass=8 / blocked=1 / missing=2。
- 因此 `cockpit_ready=false`、`release_ready=false`、
  `production_ready=false`、`public_ready=false` 全程不变。本切片不是
  部署、不是发布审批、不是生产变更，也不授权任何发布或公网分发；
  release-check 在本机隔离环境 all_green 同样不等于 production
  readiness。
- provider-smoke / long-soak / production-state 均零重跑、零生产接触、
  零 secrets/env 读取。provider-smoke 只读复用 M14-209 最新真实失败
  聚合（M14-218 只修复了 LLM 冒烟脚本的默认预算误判面，未做任何真实
  provider 请求）；long-soak 只读复用既有 pass 窗口；六个
  production-state 源按 bytes + SHA256 复核后原样 staging。
- 零生产/设备/代理/容器边界：零 Docker Desktop 或本地容器查询、启停、
  重建或修改；零生产服务与生产 DB/MinIO/语音 provider 生命周期接触；
  zero secrets；零 Android/USB/模拟器/ADB 操作；零 CC Switch 或本机代理
  修改。唯一网络访问是 GitHub Actions / PR / branch 只读 API 查询，
  六个 `raw/*.stderr.txt` 捕获均为 `exit=0`（7 bytes）成功记录形态。
- 本 README 是唯一入库证据文件；canonical 原始证据在 gitignored
  `.verify/m14-219-current-main-release-evidence/`，由 `SHA256SUMS`
  全量索引（49 文件，索引不含自身与 `logs/__pycache__/`）。
- 控制台捕获边界：`logs/release-check-stdout.log`（2560 bytes）与
  `logs/release-check-stderr.log`（2582 bytes）是 **GBK 控制台捕获**，
  按 GBK 解码后内容与 canonical JSON 一致（STATUS 表与边界声明）；
  `logs/cockpit-stderr.log`（152 bytes）同理为 GBK 中文提示。canonical
  只逐字节采用工具程序化写出的 JSON 文件，不以捕获文本为证据源；
  上述捕获按原始字节保留，不改写、不转码。

## 1. 基点与区间事实

| 事件 | commit | 说明 |
|---|---|---|
| M14-215 代码绑定证据基点 | `dcb8d380f761cadaada86bc6f1e6e3ee9cc45f2e` | PR #303 merge；ci-main run 36989559053 / release-check 9/10（api-test blocked）绑定于此 |
| 当前 main / 本切片基点 | `df3d9969bb7195f819a5e2e4cf0ab36c083933eb` | PR #307 merge；parents `d689069a` + `3e46395b` |
| 刷新区间 | `dcb8d380..df3d9969` | 8 commits / 23 files +1571/−63；M14-215 docs 收口 + M14-216/217/218 三个修复切片及其 merge |

- 区间内 alembic 树零变更，迁移 head 仍为 `0027_audit_chain`；但
  `services/api/app/ops/` 编排代码、release-check 捕获语义、
  `infra/smoke_llm.sh` 与多个测试文件均真实变更，足以触发代码绑定门
  刷新。
- M14-215 release-check 唯一失败 `api-test`（25 failed）的根因是宿主
  WSL bash relay 无法 exec `/bin/bash`。该根因已由 M14-216 修复：
  本切片运行前留痕 `logs/pre-release-check-head.log` 记录
  `resolve_bash -> D:\Git\usr\bin\bash.exe`（native Git Bash；WSL
  launcher 排除），本切片无需再修执行环境。
- no-infinite-refresh：本证据只对 `df3d9969` 时点树内容成立；后续仅
  docs-only 合并不触发下一轮刷新，下一次非 docs-only 合并后再按契约
  刷新。

## 2. ci-main（远端真实 CI）

```bash
gh api "repos/swsgbl/ai-learning-os/actions/runs?head_sha=df3d9969bb7195f819a5e2e4cf0ab36c083933eb"
gh api "repos/swsgbl/ai-learning-os/actions/runs/37070706211/jobs"
gh api "repos/swsgbl/ai-learning-os/pulls/307"
gh api "repos/swsgbl/ai-learning-os/branches/main"
```

- raw 查询成功；`gh-runs-df3d9969.stderr.txt` 与
  `gh-jobs-37070706211.stderr.txt` 等 6 个 stderr 捕获均为 `exit=0`
  记录形态（gh stderr 为空、退出码 0）。
- run **37070706211**：run_number 753，event `push`，branch `main`，
  head 精确 `df3d9969…`，workflow `.github/workflows/ci.yml`，
  `completed / success`，UTC `2026-10-02T22:06:48Z` → `22:11:44Z`。
- 五个 job 精确集合且全部 success：API（ruff / pytest / migration）、
  Android（unit test / lint / assemble）、Docker（compose build +
  healthy + smoke）、Release tools（Python tests）、Web（test /
  typecheck / lint / build）；每个 job `head_sha` 均匹配。
- PR #307「M14-218 provider-smoke LLM default budget」：merged=true，
  merge_commit_sha 精确 `df3d9969…`，PR head `3e46395b`，base `main`
  （base sha `d689069a` = PR #306 merge），merged_at UTC
  `2026-10-02T22:06:46Z`。PR 分支上的 CI run **37053544037**
  （run_number 752，event `pull_request`）同样 5/5 jobs success @
  `3e46395b`。
- 远端 main HEAD 经 `gh-branch-main.json` 复核仍为 `df3d9969…`。
- 断言脚本 `logs/derive_ci_main.py` 复核 raw 事实而非任务简报，
  **24 项断言全过**（run 唯一性/head 绑定/conclusion/job 精确集合/
  PR 身份链/分支 HEAD）后程序化派生 canonical `evidence/ci-main.json`
  （1530 bytes，SHA256
  `9be1cdf654b1e87882a847796e54cc5bfd4bd4d30b50ed3de00020ccefdc43cf`）。
  该文件取代 M14-215 @ `dcb8d380` / run 36989559053 的旧派生。

## 3. release-check（full isolated 真实重跑，10/10 pass）

- 从零环境：`uv venv --python 3.12`（CPython 3.12.14，替换旧 3.13
  venv，依赖安装后关键包 importable 留痕 `logs/env-setup-python.log`）
  与根目录 `npm ci`（411 packages，exit 0；仅保留既有
  `unrs-resolver` postinstall 被 allowScripts 阻止的已知警告）。
- 运行前 `git rev-parse HEAD == df3d9969…` 且 tracked porcelain 为空，
  留痕 `logs/pre-release-check-head.log`。隔离编排使用一次性 SQLite、
  `127.0.0.1:50666` 临时 API（auth-off，收尾 terminated）与 gitignored
  工作区 `artifacts/m14-219-isolated-r1`，不连接生产。

```powershell
cd services/api
.\.venv\Scripts\python.exe -m app.ops.cli release-check-isolated `
  --workdir "<worktree>\artifacts\m14-219-isolated-r1" --json
```

- 结果 exit **0**，`all_green=true`，**10/10 pass**，`failed_ids=[]`。
  关键门明细：
  - `api-test`：pytest **5698 passed / 36 skipped / 2 warnings in
    356.84s**（M14-215 的 25 failed WSL relay 失败面已消失）；
  - `migration`：`current == head == 0027_audit_chain`；
  - `backup`：`aios-backup-v1` tables 30 / files 1；
  - `voice`：`voice_mode=local`，synthesize `audio/wav (17324 bytes)`；
  - `license`：api deps 15 / web ok / models 7 / sources 6；
  - `e2e`：onboarding walkthrough **5 steps, 6338 ms**。
- canonical `evidence/release-check.json` 2489 bytes，SHA256
  `ebc29a4f100f1de60e37d07c290ecd219491e2a7a7bcb1836b4b2b3a23d879fb`，
  与 gitignored 工具产物
  `artifacts/m14-219-isolated-r1/release-check-isolated.json`
  **逐字节一致**（契约断言复核）；canonical 只逐字节复制工具产物，
  不手改。
- 边界：all_green 是本机隔离门禁（本地 SQLite + 回环临时 API +
  auth-off），不是 production readiness；`production_ready=false`
  不变。

## 4. 只读复用证据

### 4.1 provider-smoke（M14-209 最新真实失败聚合）

- canonical `evidence/provider-smoke.json` 564 bytes，SHA256
  `1c8c395970e052da518c3196236f05e65ef659c691818c2db44bc5220473756b`，
  generated_at `2026-10-01T11:57:12Z`，拓扑 `voice_mode=local`。
- 结果：`voice=pass`、`search=fail`、`llm=fail`。这是当前最新真实
  执行聚合，不能被更早的三 pass 聚合替代，也不能因失败而改写。
- M14-218 只修复了 LLM 冒烟脚本默认预算的误判面（256 → 1024，离线
  契约验证），**未做任何真实 provider 请求**；因此本门仍如实 blocked。
  恢复 search/LLM 并重新 export + aggregate 属于后续显式运维切片。

### 4.2 long-soak（历史 pass 窗口）

- 复用 817 bytes canonical，SHA256
  `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec`。
- 窗口 `2026-09-22T02:00:01Z` → `2026-09-23T02:00:01Z`，97 ok /
  0 warn / 0 critical，max gap 17.25 分钟（上限 20），span 恰 1440
  分钟。窗口早于后续生产切换与滚动；本切片不重跑、不制造新窗口，
  语义仅为该历史时点 pass，不覆盖当前生产栈。

### 4.3 production-state 六源

- M14-83 三门（production-preflight / legacy-papers /
  draft-ownership）、M14-85 backup-restore、M14-87 audit-chain-anchor
  与 anchor companion 在 staging 前按 bytes + SHA256 复核 6/6 MATCH。
- `prepare_reused_evidence.py` 最终 **ALL 61 CHECKS PASSED**（源存在、
  commit 对账、复制逐字节一致等），exit 0 见
  `logs/prepare-reused-evidence.log`。
- staging 语义是历史 canonical 呈现，不是当前生产重执行。

## 5. evidence-cockpit 聚合

正确调用在 tracked docs 编辑前完成，双 head 显式声明
`df3d9969bb7195f819a5e2e4cf0ab36c083933eb`，staging 目录
`artifacts/m14-219-cockpit-staging-20261002T225955596Z`（时间戳留痕
`logs/cockpit-timestamp.txt`），报告 exit 1 是预期诚实失败。

```bash
cd services/api
"<worktree>/services/api/.venv/Scripts/python.exe" -m app.ops.cli evidence-cockpit \
  --gate-source ci-main="<本切片 canonical>/evidence/ci-main.json" \
  --gate-source release-check="<本切片 canonical>/evidence/release-check.json" \
  --gate-source provider-smoke="<本切片 canonical>/evidence/provider-smoke.json" \
  --gate-source long-soak="<本切片 canonical>/evidence/long-soak.json" \
  --gate-source audit-chain-anchor="<主仓>/.verify/artifacts/m14-87-audit-chain-current-gate/evidence/audit-chain-anchor.json" \
  --gate-source production-preflight="<主仓>/.verify/artifacts/m14-83-production-read-only-evidence/evidence/production-preflight.json" \
  --gate-source legacy-papers="<主仓>/.verify/artifacts/m14-83-production-read-only-evidence/evidence/legacy-papers.json" \
  --gate-source draft-ownership="<主仓>/.verify/artifacts/m14-83-production-read-only-evidence/evidence/draft-ownership.json" \
  --gate-source backup-restore="<主仓>/.verify/artifacts/m14-85-backup-restore/evidence/backup-restore.json" \
  --anchor-companion "<主仓>/.verify/artifacts/m14-87-audit-chain-current-gate/evidence/audit-anchor.jsonl" \
  --gate-declared-head release-check=df3d9969bb7195f819a5e2e4cf0ab36c083933eb \
  --current-head df3d9969bb7195f819a5e2e4cf0ab36c083933eb \
  --staging-dir "<worktree>/artifacts/m14-219-cockpit-staging-20261002T225955596Z" \
  --json --output "<worktree>/artifacts/m14-219-cockpit-report-20261002T225955596Z.json"
# exit 1（cockpit_ready=false；stdout/stderr/exit 见 logs/cockpit-*）
```

- readiness summary：**pass=8 / pending=0 / blocked=1 / missing=2 /
  malformed=0 / tampered=0**。
- blockers：仅 `provider-smoke:blocked`（M14-215 的
  `release-check:blocked` 已随 10/10 消失）。
- not_pass_required：`provider-smoke`、`release-approval`；
  not_pass_optional：`turn-tls`。
- ci-main 与 release-check stale_status 均为 `current`：代码绑定门
  换绑到 `df3d9969`，release-check 内容本次真实通过。
- staged 10 文件（9 gates + `audit-anchor.jsonl`）与 source 全部
  逐字节一致；`staged-inventory.txt` 10 条全 IDENTICAL。
- `cockpit-report.json` 与 `evidence/cockpit-report.json` 均为 19314
  bytes / SHA256
  `8db99dc119c71dab6550e796a5a13e78951bd363a62b77e173649f07c9516380`；
  `finalize_cockpit.py` 14 项断言通过（head/blocker 契约、readiness
  计数、双副本一致、inventory 对账），exit 0。
- fail-closed 语义保持：`cockpit_ready=false`、
  `release_ready=false`、`production_ready=false`，工具 exit 1。
- release-approval 是 human-only 门，cockpit 按策略永不接受、stage
  或代拟；turn-tls 为 optional 缺席，公网语音发布形态必须另行补齐，
  `release_ready` 不得解释为公网语音就绪。

## 6. 验证与卫生

- 证据脚本静态检查：`ruff` 首次 1 error（可自动修复，attempt1 原样
  保留）→ 修正后 **All checks passed**；六个脚本 `py_compile` 全 OK
  （`logs/static-checks.log`）。
- canonical 契约断言 `logs/assert_contracts.py`：首次运行 2 项 FAIL
  （当时 `SHA256SUMS` 覆盖 stale，attempt1 stdout/exit 原样保留）；
  `write_sha256sums.py` 重写索引后最终 **ALL 34 CONTRACT CHECKS
  PASSED**（exit 0），覆盖 raw CI 身份链、canonical gate JSON、
  cockpit 契约、inventory 对账、exit 标记与 SHA256SUMS 完整性。
- `SHA256SUMS` 最终索引 **49 个 canonical 文件**（全树减自身与
  `logs/__pycache__/`）；契约的 coverage / digest 复核按设计排除
  `assert-contracts.log` 自身以避免自哈希循环（该 log 仍在索引内，
  其登记哈希对应最终通过版内容）。
- 卫生扫描 `logs/scan_hygiene.py`：运行时点索引口径 indexed_files=47
  （早于 `SHA256SUMS` 最终 49 文件重写；最终契约断言在 49 文件版上
  通过）；**credential_hits=0、replacement_char_files=0**；
  windows_path_notes=3（cockpit 报告内嵌 staging 目录的 Windows 绝对
  路径，属 canonical 证据内容，不改写）；GBK 控制台捕获按原始字节
  保留。
- 本 docs 收口切片提交前复查（只读、零 canonical 改写）：
  `assert_contracts.py` 无重定向重跑 **33/34 PASS**，唯一 FAIL 是
  `SHA256SUMS covers the full canonical tree` 的 self-log 时序语义——
  该断言的设计形态是运行时重定向写自身 log（源码注释明确 self
  exclusion 与「post-run console rehash closes that loop」），成功
  运行发生在索引补登完成 log 之前，事后非重定向重跑此项恒 FAIL，
  不是覆盖缺口。等效复核通过：全量 rehash **49/49 文件存在且哈希
  一致（bad=0）**，且索引集合与 canonical 树集合双向差为空；其余
  33 项（raw 身份链、gate JSON、cockpit、inventory、exit 标记、
  digest 登记）全部 PASS。新增/变更行敏感值、本地绝对路径与
  U+FFFD 扫描；`git diff --check` 干净。

## 7. canonical 核心清单

路径：`.verify/m14-219-current-main-release-evidence/`（gitignored）

| 文件 | bytes | SHA256 |
|---|---:|---|
| `evidence/ci-main.json` | 1530 | `9be1cdf654b1e87882a847796e54cc5bfd4bd4d30b50ed3de00020ccefdc43cf` |
| `evidence/release-check.json` | 2489 | `ebc29a4f100f1de60e37d07c290ecd219491e2a7a7bcb1836b4b2b3a23d879fb` |
| `evidence/provider-smoke.json` | 564 | `1c8c395970e052da518c3196236f05e65ef659c691818c2db44bc5220473756b` |
| `evidence/long-soak.json` | 817 | `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec` |
| `cockpit-report.json` | 19314 | `8db99dc119c71dab6550e796a5a13e78951bd363a62b77e173649f07c9516380` |
| `evidence/cockpit-report.json` | 19314 | `8db99dc119c71dab6550e796a5a13e78951bd363a62b77e173649f07c9516380` |
| `staged-inventory.txt` | 990 | `ad916f93ff058a12f518dbd8f243d000f165999fe3908db798a3bcca2ed2b295` |

另含 `raw/` 六组 GitHub API 响应与 stderr 捕获、`logs/` 下环境搭建、
release-check 捕获与 exit 标记、cockpit 捕获、复用校验、finalizer、
静态检查、卫生扫描、契约断言与生成脚本；`SHA256SUMS`（4562 bytes）
索引上述完整 canonical 树，仅排除自身与 `logs/__pycache__/`。

## 8. 未解决 blocker

1. provider-smoke：M14-209 真实结果显示 search 与 LLM fail，voice
   pass；这是当前唯一 cockpit blocker。M14-218 修复了 LLM 冒烟预算
   误判面但未真实重跑；恢复需外部上游/网络/容器环境修复后由显式
   切片执行 fresh export + aggregate，不得复用旧 JSON 或手工拼装。
2. release-approval：human-only 门从未发生，cockpit 按策略永不接受
   或代拟。
3. turn-tls：optional 缺席；公网语音发布形态仍需补齐。
4. long-soak pass 是历史窗口，不覆盖当前生产栈。
5. release-check 10/10 与 ci-main 5/5 均为 current，但代码绑定门
   current 不等于 release_ready；发布仍需 provider-smoke 恢复与
   human approval。
