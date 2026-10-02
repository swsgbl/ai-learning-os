# M14-215：current-main 发布证据刷新（诚实失败收口）

## 0. 结论与边界

- 切片：worktree
  `ai-learning-os-worktrees/m14-215-current-main-release-evidence`，分支
  `docs/m14-215-current-main-release-evidence`，基于 main
  `dcb8d380f761cadaada86bc6f1e6e3ee9cc45f2e`（PR #303 merge）。执行前
  branch/HEAD/tracked-clean 核验留痕 `logs/pre-head.log`；本切片只入库
  本 README 与三份台账，交付单 local commit，不 push、不开 PR、不合并。
- 刷新动因：M14-206 代码绑定门绑定 `e14d3f0`。其后
  `e14d3f0..dcb8d380` 为 **21 commits / 26 files +4151/−28**，其中 13 个
  provider-smoke / API 运行时或测试文件 **+951/−27**（五个 `infra/smoke_*`
  wrapper、API Dockerfile、`services/api/app/ops/` 三个文件、三个 API
  测试文件、`tools/voice/smoke_local_voice.py`），另有 Android 公网真机
  smoke 工具与 753 项测试。该区间不是 docs-only，M14-206 的 ci-main 与
  release-check 证据均 stale，必须在当前 main 真实刷新。
- 诚实结果：**ci-main pass**（远端 run 36989559053，5/5 jobs success）；
  **release-check fail**（full isolated run，9/10，唯一失败 `api-test`）；
  **provider-smoke fail**（复用 M14-209 最新真实聚合：voice pass，
  search fail，llm fail）。cockpit 聚合为
  **cockpit_ready=false / blockers=[provider-smoke:blocked,
  release-check:blocked]**。
- 因此 `cockpit_ready=false`、`release_ready=false`、
  `production_ready=false`、`public_ready=false` 全程不变。本切片不是
  部署、不是发布审批、不是生产变更，也不授权任何发布或公网分发。
- release-check 的 25 个 pytest 失败经三组聚焦诊断同构指向本机
  Windows WSL bash relay 故障：
  `C:\WINDOWS\system32\bash.EXE` 报
  `execvpe(/bin/bash) failed: No such file or directory`。这是本机执行
  环境阻塞，不是本切片允许修复的产品代码缺陷；本切片不改 WSL、不重装
  bash、不触碰 WSL 容器或发行版。
- provider-smoke / long-soak / production-state 均零重跑、零生产接触、
  零 secrets/env 读取。provider-smoke 只读复用 M14-209 最新真实失败聚合；
  long-soak 只读复用既有 pass 窗口；六个 production-state 源按 bytes +
  SHA256 复核后原样 staging。
- 零生产/设备/代理/容器边界：零 Docker Desktop 或本地容器查询、启停、
  重建或修改；零生产服务与生产 DB/MinIO/语音 provider 生命周期接触；
  zero secrets；零 Android/USB/模拟器/ADB 操作；零 CC Switch 或本机代理
  修改。唯一网络访问是 GitHub Actions 只读 API 双查询，两个 stderr 捕获
  均为 0 bytes。
- 本 README 是唯一入库证据文件；canonical 原始证据在 gitignored
  `.verify/artifacts/m14-215-current-main-release-evidence/`，由
  `SHA256SUMS` 全量索引（索引不含自身哈希）。

## 1. 基点与区间事实

| 事件 | commit | 说明 |
|---|---|---|
| M14-206 代码绑定证据基点 | `e14d3f082dbbfd6a7f816439e20fcbbd92f0ac4f` | PR #294 merge；ci-main run 36814009797 / release-check 10/10 绑定于此 |
| 当前 main / 本切片基点 | `dcb8d380f761cadaada86bc6f1e6e3ee9cc45f2e` | PR #303 merge；parents `d5d2f0c` + `a6840e5` |
| 刷新区间 | `e14d3f0..dcb8d380` | 21 commits / 26 files +4151/−28；PR #295–#303 合并链 |

- 区间内 API requirements 与 alembic 树零变更；迁移 head 仍为
  `0027_audit_chain`。但 provider-smoke 的 wrapper、Dockerfile、API ops
  代码、local-voice 探针与测试面均真实变更，足以触发代码绑定门刷新。
- 证据生成期间该 worktree 存在另一个 evidence writer：先前的
  `release-check-isolated` 进程被保留到自然结束；随后该 writer 替换了
  `derive_ci_main.py` 并追加聚焦日志。本切片接受其最终 1417 bytes
  minimal-contract `ci-main.json`（11 项断言通过）与三组失败诊断日志，
  不回滚、不改写 gate JSON。
- no-infinite-refresh：本证据只对 `dcb8d380` 时点树内容成立；后续仅
  docs-only 合并不触发下一轮刷新，下一次非 docs-only 合并后再按契约刷新。

## 2. ci-main（远端真实 CI）

```bash
gh api "repos/swsgbl/ai-learning-os/actions/runs?head_sha=dcb8d380f761cadaada86bc6f1e6e3ee9cc45f2e"
gh api "repos/swsgbl/ai-learning-os/actions/runs/36989559053/jobs"
```

- raw 双查询成功，`gh-runs-dcb8d380.stderr.txt` 与
  `gh-jobs-36989559053.stderr.txt` 均为 0 bytes。
- run **36989559053**：run_number 745，event `push`，branch `main`，
  head 精确 `dcb8d380…`，workflow `.github/workflows/ci.yml`，
  `completed / success`，UTC `2026-10-02T09:24:07Z` → `09:29:07Z`。
- 五个 job 精确集合且全部 success：API、Android、Docker、Release tools、
  Web；每个 job `head_sha` 均匹配。
- 断言脚本 `logs/derive_ci_main.py` 复核 raw 事实而非任务简报，11 项
  断言全过后程序化派生 canonical `evidence/ci-main.json`（1417 bytes，
  SHA256 `a309e0d04bedb35fb27dc2191c3be9baba7bc416cdcea29f8ecf249fbc50a60c`）。
  该文件取代 M14-206 @ `e14d3f0` / run 36814009797 的旧派生。

## 3. release-check（full isolated 真实重跑，9/10 fail）

- 从零环境：`uv venv`（CPython 3.12.14，59 packages）与根目录
  `npm ci`（node v22.23.2 / npm 12.0.2，411 packages）；npm 仅保留既有
  `unrs-resolver` postinstall 被 allowScripts 阻止的已知警告。
- 运行前 `git rev-parse HEAD == dcb8d380…` 且 tracked porcelain 为空，
  留痕 `logs/pre-release-check-head.log`。隔离编排使用一次性 SQLite、
  `127.0.0.1` 临时 API 与 gitignored 工作区，不连接生产。

```powershell
cd services/api
.\.venv\Scripts\python.exe -m app.ops.cli release-check-isolated `
  --workdir "<worktree>\artifacts\m14-215-isolated" --json
```

- 结果 exit **1**，`all_green=false`，**9/10 pass**，唯一失败
  `api-test`。pytest 为 **25 failed / 5643 passed / 47 skipped / 4 warnings
  in 359.62s**。
- 其余门：api-lint、web-lint、web-typecheck、web-build、migration、
  backup、voice、license、e2e 全 pass；migration
  `current == head == 0027_audit_chain`；local voice 合成
  `audio/wav (17324 bytes)`；e2e walkthrough 5 steps / 1505 ms。
- 聚焦诊断结果：
  - `focused-voice-local-tests.log`：5 failed / 38 passed / 1 skipped；
  - `focused-bash-contract-tests.log`：17 failed / 66 passed / 1 skipped；
  - `focused-release-candidate-tests.log`：6 failed / 105 passed /
    12 skipped。
  三组失败均出现同一 WSL relay 签名，`voice-failures-focused.log`
  保留代表性完整 traceback。
- 编排器 stderr 的 reader thread 有一次 GBK `UnicodeDecodeError`，但 JSON
  报告已完整写出；canonical 仅逐字节复制工具产物
  `release-check-isolated.json`，不手改。最终文件 2647 bytes，SHA256
  `829e31719eb5ab0ed278b870354c101bb87e8329c6b193058612a87db7f63c25`。

## 4. 只读复用证据

### 4.1 provider-smoke（M14-209 最新真实失败聚合）

- 源：M14-209 worktree
  `artifacts/temp/provider-smoke/m14-209/20261001T092342607Z/`，聚合窗口
  `2026-10-01T11:55:13Z` 至 `11:57:12Z`。三份单步输入与聚合的 schema、
  executed/result/exit、时间序、注册哈希逐份复核。
- 结果：`voice=pass`、`search=fail`、`llm=fail`，拓扑
  `voice_mode=local`。这是当前最新真实执行，不能被旧 M14-148 三 pass
  聚合替代，也不能因失败而改写。
- canonical `evidence/provider-smoke.json` 564 bytes，SHA256
  `1c8c395970e052da518c3196236f05e65ef659c691818c2db44bc5220473756b`，
  与源逐字节一致。该门因此 blocked；恢复 search/LLM 并重新 export +
  aggregate 属于后续显式运维切片。

### 4.2 long-soak（历史 pass 窗口）

- 复用 M14-106/M14-206 链上的 817 bytes canonical，SHA256
  `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec`。
- 窗口 `2026-09-22T02:00:01Z` → `2026-09-23T02:00:01Z`，97 ok /
  0 warn / 0 critical，max gap 17.25，span 1440。窗口早于后续生产切换与
  滚动；本切片不重跑、不制造新窗口，语义仅为该历史时点 pass。

### 4.3 production-state 六源

- M14-83 三门、M14-85 backup-restore、M14-87 audit-chain-anchor 与
  anchor companion 在 staging 前按 bytes + SHA256 复核 6/6 MATCH。
- 执行/注册 commit 对账：M14-83 执行 base `5829ad9` / docs 注册
  `57a696b`；M14-85 执行 base `f47a1e4` / docs 注册 `1d777f5`；
  M14-87 执行 base `ddcaa229` / docs 注册 `44767c4`。
- staging 语义是历史 canonical 呈现，不是当前生产重执行。
- `prepare_reused_evidence.py` 最终 **54 checks passed**。首次尝试因在
  执行 base 读取 docs 注册 README 而失败，attempt1 stdout/exit 原样保留；
  修正为读取注册 commit 后通过，不改任何 gate JSON。

## 5. evidence-cockpit 聚合

第一次调用把 `--gate-source` 传成裸路径，工具 fail-closed exit 2：
“`--gate-source 需要 GATE=值 形态`”，stdout/stderr/exit 均保留在
`cockpit-attempt1*`。正确形态必须显式命名：

```text
--gate-source ci-main=<canonical>/evidence/ci-main.json
--gate-source release-check=<canonical>/evidence/release-check.json
```

正确调用在 tracked docs 编辑前完成，双 head 显式声明
`dcb8d380f761cadaada86bc6f1e6e3ee9cc45f2e`，报告 exit 1 是预期诚实失败。

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
  --gate-declared-head release-check=dcb8d380f761cadaada86bc6f1e6e3ee9cc45f2e \
  --current-head dcb8d380f761cadaada86bc6f1e6e3ee9cc45f2e \
  --staging-dir "<worktree>/artifacts/m14-215-cockpit-staging-20261002T101530254Z" \
  --json --output "<worktree>/artifacts/m14-215-cockpit-report-20261002T101530254Z.json"
# exit 1（cockpit_ready=false；stdout/stderr/exit 见 logs/cockpit-*）
```

- readiness summary：**pass=7 / pending=0 / blocked=2 / missing=2 /
  malformed=0 / tampered=0**。
- blockers：`provider-smoke:blocked`、`release-check:blocked`。
- not_pass_required：`release-check`、`provider-smoke`、
  `release-approval`；not_pass_optional：`turn-tls`。
- ci-main 与 release-check stale_status 均为 `current`；这仅说明代码绑定
  换绑到 `dcb8d380`，不表示 release-check 内容通过。
- staged 10 文件（9 gates + `audit-anchor.jsonl`）与 source 全部逐字节
  一致；`staged-inventory.txt` 10 条全 IDENTICAL。
- finalizer 初版在 `dict_values + list` 处失败，attempt1 保留。修复后的
  10:08 rerun 曾得到 19430 bytes / SHA256
  `a97735f15a73d0382076aaeec92252bb8152ddbcf115ae4a936b645cc1abb166`
  （见 `logs/finalize-cockpit.log`）；随后并发 evidence writer 将现场源与
  canonical 均覆盖为 10:15 rerun。该 10:08 字节副本已不存在，本切片不伪造
  JSON 回填，最终以现存可复核的 10:15 报告为 canonical：
  `cockpit-report.json` 与 `evidence/cockpit-report.json` 均为 19470 bytes /
  SHA256
  `1434d16b4de192f03e2495694b68dbc69573471a7f6386eb651e2d3b8a22a8de`，
  13 项断言通过，fail-closed 语义与 10:08 日志记录一致。

## 6. 验证与卫生

- 非 WSL 依赖的聚焦契约九套件（cockpit / readiness / closure manifest /
  isolated release-check / checklist / provider evidence / provider
  preflight / SearXNG provider / approval draft），basetemp 位于仓库外
  专用目录：**444 passed, 1 warning in 9.79s**，exit 0 见
  `logs/focused-contract-tests.exit.txt`。命令为：

```bash
cd services/api && "<worktree>/services/api/.venv/Scripts/python.exe" -m pytest -q \
  tests/test_evidence_cockpit.py tests/test_release_readiness.py tests/test_release_closure_manifest.py \
  tests/test_release_check_isolated.py tests/test_release_checklist.py \
  tests/test_provider_smoke_evidence.py tests/test_provider_smoke_preflight.py tests/test_searxng_local_provider.py \
  tests/test_release_approval_draft.py \
  --basetemp "<仓库外专用预建父目录>/m14-215-current-main-release-evidence/focused"
```

并发 writer 的独立真实结果为 **332 passed in 8.66s**，原样保留在
`logs/focused-contract-tests-332.log` 与 `.exit.txt`，作为补充结果而非
替代 canonical 444/1 warning 计数。

- 证据脚本 `ruff check`：All checks passed；七个脚本 `py_compile` 全 OK。
- canonical 契约断言脚本复核 raw CI、四个 canonical gate JSON（ci-main、
  release-check、provider-smoke、long-soak）、cockpit、inventory、
  staged hashes、执行日志标记与 `SHA256SUMS`；通过计数见
  `logs/assert-contracts.log`。
- `SHA256SUMS` 索引的全部 canonical 文件（evidence、raw、logs、脚本、
  root/supplemental cockpit 与 inventory）执行 credential assignment /
  key 形态 / private key / 带凭据 URL / DB URL / raw UTF-8 U+FFFD 扫描；
  另扫描 staged docs 新增行。GBK 控制台捕获以 Latin-1 字节保留解码。
  最终 credential hits 0、需处置的 U+FFFD files 0；5 个原始 console log
  含历史 U+FFFD 字节，按“preserved console U+FFFD”如实记 note，不改写
  原始捕获。
- `git diff --check` 干净；tracked 变更仅本 README、PROJECT_STATUS、
  ROADMAP、CHANGELOG 四个 docs 文件。

## 7. canonical 核心清单

路径：`.verify/artifacts/m14-215-current-main-release-evidence/`

| 文件 | bytes | SHA256 |
|---|---:|---|
| `evidence/ci-main.json` | 1417 | `a309e0d04bedb35fb27dc2191c3be9baba7bc416cdcea29f8ecf249fbc50a60c` |
| `evidence/release-check.json` | 2647 | `829e31719eb5ab0ed278b870354c101bb87e8329c6b193058612a87db7f63c25` |
| `evidence/provider-smoke.json` | 564 | `1c8c395970e052da518c3196236f05e65ef659c691818c2db44bc5220473756b` |
| `evidence/long-soak.json` | 817 | `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec` |
| `raw/gh-runs-dcb8d380.json` | 12304 | `c34d13fd0d361d9a26c635963d71c94ee0dfba20c4be10c80cb33c4377f2cbcd` |
| `raw/gh-jobs-36989559053.json` | 13027 | `da2ba42219838cd2d6e41afe0826256002d42747292fe47acea60ff5069258b0` |
| `cockpit-report.json` | 19470 | `1434d16b4de192f03e2495694b68dbc69573471a7f6386eb651e2d3b8a22a8de` |
| `evidence/cockpit-report.json` | 19470 | `1434d16b4de192f03e2495694b68dbc69573471a7f6386eb651e2d3b8a22a8de` |
| `staged-inventory.txt` | 990 | `ed63a71157734c22ba08314626c15501bdf4ff26cf90f6a1ef6c3c7a909027b7` |

另含 `logs/` 下环境、release-check、cockpit、诊断测试、复用校验、
finalizer、聚焦契约测试、ruff、py_compile、卫生扫描、契约断言与脚本；
`raw/*.stderr.txt` 均为空。`SHA256SUMS` 索引上述完整 canonical 树，
仅排除自身与 `logs/__pycache__/`。

## 8. 未解决 blocker

1. 本机 WSL bash relay：`C:\WINDOWS\system32\bash.EXE` 无法 exec
   `/bin/bash`，导致 25 个 release-check pytest 与三组聚焦脚本面失败。
   修复 WSL/发行版超出本切片边界。
2. release-check 9/10：`api-test` 未通过，不能宣称全绿。
3. provider-smoke：M14-209 真实结果显示 search 与 LLM fail，voice pass。
4. release-approval：human-only 门从未发生，cockpit 按策略永不接受或
   代拟。
5. turn-tls：optional 缺席；公网语音发布形态仍需补齐。
6. long-soak pass 是历史窗口，不覆盖当前生产栈。
