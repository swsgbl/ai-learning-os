# M14-242：current-main 发布证据刷新（M14-241 后代码绑定门换绑）

## 0. 结论与边界

- 切片：复用 worktree
  `ai-learning-os-worktrees/m14-241-public-device-http-error-classification`，
  新分支 `ops/m14-242-current-main-release-evidence`，基于 current main
  `82a8d02a6fe0c45785c9464fdf394cdcb05061d0`（PR #328 merge）。执行前
  tracked porcelain 为空，留痕 `logs/pre-head.log`；本切片只入库本
  README 与三份台账，交付单 local commit。
- 刷新动因：M14-219 的代码绑定发布证据绑定
  `df3d9969bb7195f819a5e2e4cf0ab36c083933eb`。其后 M14-241 修改
  `tools/android_release/public_device_smoke.py` 与测试，属于真实
  runtime/test 变更，所以旧 `ci-main` 与 `release-check` 均 stale，
  必须在 current main 重新建立证据。
- 诚实结果：**ci-main pass**（远端 main run 37380086771，5/5 jobs
  success）；**release-check pass**（full isolated 真实重跑，exit 0，
  all_green=true，10/10）；**provider-smoke fail**（只读复用 M14-209
  最新真实聚合：voice pass，search fail，llm fail）。cockpit 聚合为
  **cockpit_ready=false / blockers=[provider-smoke:blocked]**，readiness
  pass=8 / blocked=1 / missing=2。
- 因此 `cockpit_ready=false`、`release_ready=false`、
  `production_ready=false`、`public_ready=false` 全程不变。本切片不是
  部署、不是发布审批、不是生产变更，也不授权任何发布或公网分发；
  release-check 的本机隔离 all_green 同样不等于 production readiness。
- provider-smoke / long-soak / production-state 均零重跑、零生产接触、
  零 secrets/env 读取。provider-smoke 只读复用 M14-209 最新真实失败
  聚合；long-soak 只读复用既有历史 pass 窗口；六个 production-state
  源按 bytes + SHA-256 复核后原样 staging。
- 零生产/设备/代理/容器边界：零 Docker Desktop 或本地容器查询、启停、
  重建或修改；零生产服务与生产 DB/MinIO/语音 provider 生命周期接触；
  zero secrets；零 Android/USB/模拟器/ADB 操作；零 CC Switch 或本机
  代理修改。唯一网络访问是 GitHub Actions / PR / branch 只读 API 查询。
- 本 README 是唯一入库证据文件；canonical 原始证据在 gitignored
  `.verify/m14-242-current-main-release-evidence/`，由 `SHA256SUMS`
  全量索引（57 文件，索引不含自身与 `logs/__pycache__/`）。

## 1. ci-main（远端真实 CI）

- current main：`82a8d02a6fe0c45785c9464fdf394cdcb05061d0`，远端
  branch/main API 复核一致。
- main push run **37380086771**：run_number 796，event `push`，branch
  `main`，workflow `.github/workflows/ci.yml`，UTC
  `2026-10-05T22:04:30Z` → `22:09:30Z`，completed/success。
- 五个 job 精确集合且全部 success：API（ruff / pytest / migration）、
  Android（unit test / lint / assemble）、Docker（compose build +
  healthy + smoke）、Release tools（Python tests）、Web（test /
  typecheck / lint / build）；每个 job `head_sha` 均为 `82a8d02...`。
- PR #328 身份链：merged=true，merge commit `82a8d02...`，PR head
  `90a5a46f26b1637830b65956e3a0be2812ebe131`，base
  `7693c082369cb1eb6cf0ec93663369387fbf4726`。PR-head CI run
  **37379245926**（run_number 795，pull_request）同样 5/5 success。
- raw GitHub API JSON 与 stderr/exit 标记均归档在 `raw/`。派生脚本
  `logs/derive_ci_main.py` 只读取 raw 事实，**26/26 assertions passed**
  后程序化写出 canonical `evidence/ci-main.json`（1165 bytes，SHA256
  `7aa55330c0fde5342316e8194189b0b94ed64057955568dfc3485f7924aad48f`）。

## 2. release-check（full isolated 真实重跑，10/10 pass）

- 从零环境：`uv venv --python 3.12`（CPython 3.12.14），安装
  `requirements.txt` 与 `requirements-dev.txt`（59 packages）；根目录
  `npm ci`（411 packages，exit 0；仅保留 `unrs-resolver` postinstall
  被 allowScripts 阻止的既有警告）。
- 运行前 `git rev-parse HEAD == 82a8d02...` 且 tracked porcelain 为空，
  留痕 `logs/pre-release-check-head.log`。隔离编排使用一次性 SQLite、
  `127.0.0.1:52992` 临时 API（auth-off，收尾 terminated）与 gitignored
  工作区 `artifacts/m14-242-isolated-r1`，不连接生产。
- 命令：

```powershell
cd services/api
.\.venv\Scripts\python.exe -m app.ops.cli release-check-isolated `
  --workdir "<worktree>\artifacts\m14-242-isolated-r1" --json
```

- 结果 exit **0**，`all_green=true`，**10/10 pass**，`failed_ids=[]`。
  关键门：`api-test` **5812 passed / 42 skipped / 1 warning in 378.00s**；
  `migration` current == head == `0027_audit_chain`；`backup`
  aios-backup-v1 tables 30 / files 1；`voice` local synthesize
  `audio/wav (17324 bytes)`；license api deps 15 / web ok / models 7 /
  sources 6；e2e onboarding 5 steps。
- canonical `evidence/release-check.json`（2488 bytes，SHA256
  `92e90c390cd6dc76faac06dbb237dcb3aeababa330000c3b3b7304b2a3a0c1e7`）
  与工具产物 `artifacts/m14-242-isolated-r1/release-check-isolated.json`
  逐字节一致。stdout JSON 与 canonical JSON 语义一致；控制台捕获按
  原始字节保留，不改写、不转码。

## 3. 只读复用证据

- provider-smoke：复用 M14-209 最新真实聚合（564 bytes，SHA256
  `1c8c395970e052da518c3196236f05e65ef659c691818c2db44bc5220473756b`）。
  结果保持 `voice=pass`、`search=fail`、`llm=fail`；三份单步输入、
  execution/closeout commit 与“无更新聚合”事实均复核。
- long-soak：复用历史 pass 窗口（817 bytes，SHA256
  `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec`）。
  窗口 2026-09-22 02:00:01Z 至 2026-09-23 02:00:01Z，97 ok / 0 warn /
  0 critical，max gap 17.25 分钟，span 1440 分钟。该证据只覆盖历史
  时点，不覆盖当前生产栈。
- production-state 六源：M14-83 三门、M14-85 backup-restore、M14-87
  audit-chain-anchor 与 anchor companion 全部按 bytes + SHA-256 复核后
  staging。语义是历史 canonical 呈现，不是当前生产重执行。
- `logs/prepare_reused_evidence.py` 最终 **ALL 61 CHECKS PASSED**；
  canonical 复制均为 byte-identical 或 JSON 等价复核。

## 4. evidence-cockpit

- cockpit 在任何 tracked docs 修改前运行，current-head 与
  release-check declared head 均显式绑定
  `82a8d02a6fe0c45785c9464fdf394cdcb05061d0`。
- staging 目录：
  `artifacts/m14-242-cockpit-staging-20261005T222753103Z`；报告：
  `artifacts/m14-242-cockpit-report-20261005T222753103Z.json`。
- 结果 exit **1** 是预期诚实失败：`cockpit_ready=false`，唯一 blocker
  `provider-smoke:blocked`；readiness summary pass=8 / pending=0 /
  blocked=1 / missing=2 / malformed=0 / tampered=0。
- `ci-main` 与 `release-check` stale_status 均为 `current`；10 个 staged
  文件（9 gates + `audit-anchor.jsonl`）与 source 全部逐字节一致。
- `logs/finalize_cockpit.py` **14 assertions passed**；canonical
  `cockpit-report.json` 与 `evidence/cockpit-report.json` 均为 19374
  bytes，SHA256
  `4ed1bcea40ea12cb6f0a90291d8b7169176113edd2cbcdf6cf985736a28ee111`。
- release-approval 是 human-only 门，cockpit 永不接受、stage 或代拟；
  turn-tls optional 缺席，公网语音发布形态仍必须另行补齐。

## 5. 验证与卫生

- 六个证据脚本 `ruff` 全部通过，`py_compile` 全部通过；静态检查留痕
  `logs/static-checks.log`。
- 卫生扫描最终：indexed_files=57，credential_hits=0，
  replacement_char_files=0；3 条 Windows absolute path note 全部来自
  cockpit 报告内嵌 source/staging 路径，属 canonical 证据内容，不改写。
  首轮扫描发现复制脚本内字面 U+FFFD，已改为 `\\ufffd` 转义并保留
  `scan-hygiene.attempt1.log`。
- `logs/assert_contracts.py` 最终 **ALL 34 CONTRACT CHECKS PASSED**；
  前两轮脚本适配失败保留 `assert-contracts.attempt1.log`，不影响 gate
  证据。断言覆盖 raw CI 身份链、canonical gate、cockpit、inventory、
  exit marker、日志标记与 SHA 覆盖。
- 最终 `SHA256SUMS` 索引 57 个 canonical 文件；全量 rehash 结果
  `bad=0`、`coverage_delta=0`。索引不含自身与 `logs/__pycache__/`。
- 本 docs-only 收口不运行 pytest；运行面已由上述 full isolated
  release-check 覆盖。提交前执行 `git diff --check` 与新增行敏感值 /
  本地绝对路径 / Windows 用户名 / U+FFFD 扫描。

## 6. 未解决 blocker

1. provider-smoke：M14-209 真实结果显示 search 与 LLM fail，voice pass。
   恢复必须在外部上游/网络/容器条件修复后执行 fresh export + aggregate，
   不得手写或复用旧 JSON 改写结论。
2. release-approval：human-only 门从未发生，cockpit 永不代拟。
3. turn-tls：optional 缺席；公网语音发布形态必须补齐。
4. long-soak pass 是历史窗口，不覆盖当前生产栈。
5. ci-main 与 release-check current 只说明代码绑定门在
   `82a8d02...` 通过，不构成 production ready 或公网移动发布结论。
