# M14-105：current-main 代码绑定门证据刷新 + evidence-cockpit 聚合（实现/文档切片）

- 切片：分支 `ops/m14-105-current-main-release-evidence`（独立 worktree
  `m14-105-current-main-release-evidence`，基于 main
  `e5d5d3a9580d355458a8159e5fa936d30e0b2a73`（PR #192 merge = M14-104
  合入，精确基点），单次 local commit（不推送、不建 PR）。
- 目标：对 current main `e5d5d3a` 只刷新**代码绑定**发布门
  （ci-main / release-check），并以 M14-91 evidence-cockpit 聚合器重新
  汇总发布证据；沿用 M14-97/M14-94 证据契约/流程。绝不手改 gate JSON、
  绝不合成结果、绝不复制 M14-97 产物（CI run / 隔离环境 /
  release-check 运行全部本轮新执行）、绝不搬运旧生产状态证据冒充新执行；
  `release_ready=false` / `production_ready=false` 全程不变。
- 零生产触碰（本切片全程）：零容器启停/重建、零计划任务操作、零
  DB/MinIO/语音引擎接触、零生产日志写入、零 secrets/env 读取、零 soak
  历史/锚定读写、零发布审批接触、零部署、零 Ollama/FunASR/CosyVoice/
  SearXNG/代理/CC Switch 生命周期变更。唯一网络访问是 GitHub 只读 API
  （`gh api`，已认证账号）+ worktree 从零环境的包安装（uv/npm）。
  本轮 provider-smoke 三件套**未重跑**（M14-104 于本基点真实重跑后
  search/llm 失败、aggregate 未运行、无 provider-smoke.json）——本切片
  cockpit 对 provider-smoke 按 required-未-stage 如实阻断，不搬运
  M14-88/M14-98 旧 JSON 冒充 current。

## 1. 基点事实（全部 git/远端可复核）

| 事件 | commit | 说明 |
|------|--------|------|
| PR #184 merge（M14-97 代码绑定门证据刷新） | `c0610e9`（含 M14-97 切片 commit） | M14-97 canonical 所锚执行时点基点为 b374aa5 |
| PR #185–#191 merge（M14-95/98/100/101/102/103/99） | `841b36f`…`aa4bb55` | 真实代码/测试变更 |
| PR #192 merge（M14-104 provider-smoke 证据刷新）→ **当前 main** | `e5d5d3a9580d355458a8159e5fa936d30e0b2a73` | 本切片精确基点 |
| main push CI run 35805140338 创建（push 事件） | e5d5d3a | 2026-09-23T01:09:40Z（UTC）；本地 2026-09-23 09:09:40 +0800 |

- `b374aa5..e5d5d3a` 共 **9 个 PR merge**（#184 M14-97、#185 M14-95、
  #186 M14-98、#187 M14-100、#188 M14-101、#189 M14-102、#190 M14-103、
  #191 M14-99、#192 M14-104），`git diff --stat` = 32 文件
  **+5049/−80**——其中 services/api 与 tools/harmony_release 测试集有
  大量真实变更（新增 `test_llm_gateway_timeout_budget.py` +353、
  `test_m14_101_monitoring_history_performance.py` +623、
  `test_m14_95_auth_smoke_server.py` +399、
  `tests/harmony_release/test_auth_smoke*.py` +266/+598 等），代码绑定门
  **必须真实重新执行**才对 e5d5d3a 成立——这是本切片的直接动因
  （M14-97 canonical @ b374aa5 对 e5d5d3a 已 stale）。
- M14-97 canonical（@ b374aa5 时点）自此对 e5d5d3a stale——保留为历史
  记录不改写；本切片不搬运其产物。M14-83（@ 5829ad9 生产只读门）、
  M14-85（@ f47a1e4 backup-restore）、M14-87（audit-chain-anchor 门
  闭合 + 伴生锚）canonical 保留为其各自时点记录，本切片按 M14-91 §5
  登记路径**原样 staging**（哈希复核见 §2.3）、不改写。M14-88
  provider-smoke 恢复 JSON **本轮不 staging**（见 §2.3 诚实语义）。

## 2. 刷新执行记录（命令与结果，全部真实可复跑；全部为本轮新执行）

### 2.1 ci-main（真实远端 CI，非本地重跑）

```bash
gh api "repos/swsgbl/ai-learning-os/actions/runs?head_sha=e5d5d3a9580d355458a8159e5fa936d30e0b2a73&per_page=20"
gh api "repos/swsgbl/ai-learning-os/actions/runs/35805140338/jobs?per_page=30"
```

- 命中断言（断言脚本复核归档 raw 响应，非仅依赖任务简报；任一断言
  失败即非零退出、不产出证据）：`workflow_runs` 中 total_count=1、
  event=push、head_branch=main 恰 1 条（该 SHA 全部 run 也仅此 1 条）——
  run **35805140338**（run_number 505），workflow
  `.github/workflows/ci.yml`，head_sha `e5d5d3a…`，status=completed，
  **conclusion=success**（created 2026-09-23T01:09:40Z / updated
  2026-09-23T01:14:12Z，
  https://github.com/swsgbl/ai-learning-os/actions/runs/35805140338）。
- jobs 断言：`total_count=5` 且全部 conclusion=success——API
  （ruff/pytest/migration）、Docker（compose build+healthy+smoke）、
  Release tools（Python tests）、Android（unit test/lint/assemble）、
  Web（test/typecheck/lint/build）；每 job head_sha == e5d5d3a、job 名
  集合精确匹配（重复即拒绝）。
- 按 `_eval_ci_main` 契约（`services/api/app/ops/release_readiness.py`：
  gate/run_id/merge_commit/conclusion 白名单标量）**程序化派生**
  `evidence/ci-main.json`：断言驱动脚本 `derive_ci_main.py`（gitignored
  `artifacts/m14-105-derive/`，ruff check 通过 + py_compile 通过）从 raw
  事实拼装 + 溯源 source 串；断言不过即失败，无手写 pass。未复用
  M14-97 的 run 35738367558 或其 raw 文件。
- raw API 响应原样归档：`raw/gh-runs-e5d5d3a.json`、
  `raw/gh-jobs-35805140338.json`（哈希见 §4）。

### 2.2 release-check（隔离本地 full 重跑，干净 e5d5d3a 执行树）

从零构建全新隔离环境（不复用 M14-97 任何产物/环境；其 `.venv`/
`node_modules` 属 m14-97 worktree，本切片在 m14-105 worktree 独立重建）：

```bash
uv venv .venv --python 3.12          # CPython 3.12.14（uv 0.12.1）
NO_PROXY='*' uv pip install -r services/api/requirements.txt -r services/api/requirements-dev.txt
npm ci --no-audit --no-fund          # 411 packages（node v22 / npm 12）
```

（uv 安装以 `NO_PROXY='*'` 直连镜像完成（与 M14-104 同法，本机系统
代理隧道失效的既有边界）；npm 提示 `unrs-resolver` postinstall 被
allowScripts 策略阻止——与 M14-97/M14-94/M14-92/M14-90 相同的已知提示，
如实记录；该脚本产物非门禁依赖，本轮 10 门全绿证实无影响。）

**执行树前置核验**（declared-head 诚实性前提）：运行前
`git rev-parse HEAD` == `e5d5d3a…` 且 `git status --porcelain` 为空
（tracked-clean，仅 gitignored `.verify/`/`artifacts/` 增量）——
release-check 的实际执行树即精确干净 e5d5d3a，后续 cockpit
`--gate-declared-head release-check=e5d5d3a` 声明成立。

隔离编排器（一次性 SQLite → alembic upgrade head → 127.0.0.1 临时
uvicorn → /health 就绪 → full 10 门 → 原子落盘 → finally 关停临时 API；
环境剥离 AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL，显式
APP_ENV=development / VOICE_MODE=local——不连接任何生产面；worktree
绝对路径调用；新工作区 `artifacts/m14-105-isolated/`）：

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli release-check-isolated \
  --workdir "<worktree>/artifacts/m14-105-isolated" --json
# exit 0
```

- 结果：**all_green=true，10/10 pass**（generated_at
  2026-09-23T01:32:37Z，execution_scope=full）——api-lint（ruff All
  checks passed）/ web-lint / web-typecheck / web-build /
  api-test（**pytest 4227 passed, 33 skipped, 1 warning in 313.06s**；
  较 M14-97 @ b374aa5 的 4158 恰 **+69**——§1 所列 9 个 PR 新增测试的
  预期对账，非搬运复算）/ migration（alembic head）/ backup
  （aios-backup-v1 tables:30 files:1）/ voice（local 合成 audio/wav
  17324 bytes）/ license（api deps 15 / web ok / models 7 / sources 6）/
  e2e（walkthrough 5 步 1446 ms）。
- `release-check-isolated.json` 逐字节复制改名为 canonical
  `evidence/release-check.json`（程序化比对 byte-identical=True；文件
  自带 gate 自标识 `release-check`，非手改）。

### 2.3 evidence-cockpit 聚合（代码绑定门刷新后重新汇总）

在 tracked docs 编辑**之前**、HEAD 仍为干净 `e5d5d3a` 时运行。canonical
源路径逐一取自 M14-91 README §5/`staged-inventory.txt` 登记（不猜测）；
staging 前先对 6 个既有生产状态源文件做 sha256 复核——与登记值逐一
**MATCH**（audit-chain-anchor `a8c54c5e…` / audit-anchor `d2bfd877…` /
backup-restore `ed0fc5b4…` / preflight `b3a2be66…` / legacy `83cd62bd…` /
draft `e90ae04c…`；cockpit 运行前后两次复核一致）。

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli evidence-cockpit \
  --gate-source ci-main="<本切片 canonical>/evidence/ci-main.json" \
  --gate-source release-check="<本切片 canonical>/evidence/release-check.json" \
  --gate-source audit-chain-anchor="<主仓>/.verify/artifacts/m14-87-audit-chain-current-gate/evidence/audit-chain-anchor.json" \
  --gate-source production-preflight="…m14-83-production-read-only-evidence…/evidence/production-preflight.json" \
  --gate-source legacy-papers="…m14-83…/evidence/legacy-papers.json" \
  --gate-source draft-ownership="…m14-83…/evidence/draft-ownership.json" \
  --gate-source backup-restore="…m14-85-backup-restore…/evidence/backup-restore.json" \
  --anchor-companion "<主仓>/.verify/artifacts/m14-87-audit-chain-current-gate/evidence/audit-anchor.jsonl" \
  --gate-declared-head release-check=e5d5d3a9580d355458a8159e5fa936d30e0b2a73 \
  --current-head e5d5d3a9580d355458a8159e5fa936d30e0b2a73 \
  --staging-dir "<worktree>/artifacts/m14-105-cockpit-staging" \
  --json --output "<worktree>/artifacts/m14-105-cockpit-report.json"
# exit 1（cockpit_ready=false，如实——见下）
```

- `--gate-declared-head release-check=e5d5d3a` 的合法性：§2.2 前置
  核验证明实际执行树即干净 e5d5d3a（gate JSON 本身不内嵌 head，由调用
  方显式声明；声明与事实一致方可用）。
- **provider-smoke 本轮未传入**（未 stage）：M14-104（已在本基点合入）
  于 2026-09-23T00:37–00:49Z 真实重跑三件套后 search/llm 真实失败、
  aggregate 未运行、无 provider-smoke.json——本轮不存在可 stage 的
  current provider-smoke 证据，且任务纪律禁止搬运 M14-88（2026-09-21
  恢复时点）/M14-98（2026-09-22 失败窗口）旧 JSON 冒充 current。
  provider-smoke 为 required 门，按 cockpit 收紧语义如实产出
  `provider-smoke:not-staged-required` blocker（exit 1 的组成部分）。
- 结果（报告归档 canonical `cockpit-report.json`，generated_at
  2026-09-23T01:36:16Z）：
  **evaluator pass=7 / pending=0 / blocked=0 / missing=4 / malformed=0
  / tampered=0**；**release_ready=false**、**production_ready=false**
  （恒 false）、cockpit_ready=false、**exit 1**。
- **code-bound 两门刷新生效**：ci-main stale=**current**（内嵌
  merge_commit=e5d5d3a == current HEAD）、release-check stale=
  **current**（flag 声明 e5d5d3a）。**blockers =
  `long-soak:not-staged-required` + `provider-smoke:not-staged-required`**
  （较 M14-97 的单 blocker 多出 provider-smoke——M14-97 当时 stage 的
  是 M14-88 历史恢复快照，本基点上该门最新真实执行即 M14-104 的失败
  轮，诚实语义从「呈现旧 pass」切换为「required 未 stage 阻断」）。
- production-state 五门（audit-chain-anchor / production-preflight /
  legacy-papers / draft-ownership / backup-restore）+ anchor companion
  以历史 canonical snapshot 原样 staged、undeclared 如实呈现不 block
  （M14-91 §1.1 第 5 条口径：呈现性选择，效力判断留 supervisor +
  声明通道）；release-approval「按策略永不接受，非 blocker」、
  turn-tls「optional：本机/LAN 范围不阻断」、long-soak 与
  provider-smoke「required 未 stage → blocker」摘要显式区分。
- **staged 8 文件与 source 逐字节一致**：工具内建写后重读断言 + 外部
  独立复核 **8/8 IDENTICAL**（cmp 语义逐字节比较）；6 个既有文件哈希
  与 M14-91 staged-inventory 登记值交叉一致、2 个新文件（ci-main
  `cecb0dbc…` 1125 bytes / release-check `c3f4cecc…` 2491 bytes）与
  本切片 canonical 实际哈希一致——来源零改动 + 逐字节 staging 端到端
  实证（清单归档 canonical `staged-inventory.txt`）。
- exit 1 是**诚实预期**：long-soak（24h 审计未发生）、provider-smoke
  （M14-104 真实失败 + aggregate 未运行）与 release-approval
  （human-only 从未发生）缺席之下，cockpit 不可能 ready；本切片不以
  任何方式强制 exit 0。

## 3. 聚焦验证与静态检查（真实执行结果）

- 聚焦契约测试（cockpit 三件套，本切片证据链所依赖的工具面）：

```bash
cd services/api && "<worktree>/.venv/Scripts/python.exe" -m pytest -q \
  tests/test_evidence_cockpit.py tests/test_release_readiness.py tests/test_release_closure_manifest.py \
  --basetemp "D:/AI Learning OS/.pytest-tmp/m14-105-current-main-release-evidence/cockpit3"
# 145 passed in 2.24s（与 M14-97/M14-91 记录一致）
```

- release-check 契约三件套（本切片执行了 release-check-isolated）：

```bash
cd services/api && "<worktree>/.venv/Scripts/python.exe" -m pytest -q \
  tests/test_release_readiness.py tests/test_release_check_isolated.py tests/test_release_checklist.py \
  --basetemp "D:/AI Learning OS/.pytest-tmp/m14-105-current-main-release-evidence/releasecheck3"
# 137 passed, 1 warning in 3.88s（与 M14-97/M14-90 记录一致）
```

- ruff：本切片唯一触碰的 Python 工具（gitignored 断言派生脚本
  `derive_ci_main.py`）`ruff check` All checks passed + `py_compile`
  通过；全量 `ruff check services/api` 亦 All checks passed（tracked
  Python 零改动，基线复核）。
- canonical JSON `json.load` 解析通过 7 份（ci-main/release-check/
  cockpit-report/两 raw/两 stdout）；canonical `release-check.json` 与
  隔离运行原始产物**逐字节一致**（程序化比对 True）。
- `git diff --check` 干净（docs-only）；分支卫生：单 commit 后
  tracked-clean（worktree 侧 `.venv`/`node_modules`/`artifacts/`/
  `.verify/` 均 gitignored 未入库）；未 push、未建 PR。

## 4. canonical 证据清单（gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：worktree `.verify/artifacts/m14-105-current-main-release-evidence/`

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/ci-main.json` | 1125 | `cecb0dbc6b8e19f0e353fc92d417cec11ed3f96ebfe4288773fc8af596d77fda` |
| `evidence/release-check.json` | 2491 | `c3f4cecc75477cde3af472f5a62c322976401e571151eb4121279e9483aff785` |
| `raw/gh-runs-e5d5d3a.json` | 12225 | `f1b01adeddf66bea9f6abbf0565c96d49df86c472bde6a4d177037ffc8b27468` |
| `raw/gh-jobs-35805140338.json` | 12334 | `b033a1e5beab94ce16c772c13c7c2d68fe61b1e493bab92f3408cb777d34df72` |
| `cockpit-report.json` | 16102 | `41caf14ce4a8a6ee0be42c0f6d7ace3716b353a3276a5a6c1ec4dd52f595aca9` |
| `staged-inventory.txt` | 845 | `9e10584e0e2e3776ad87e0ea38765152054f5d8e147d6361a0347c6dd4448452` |

（另 `logs/` 五份执行日志 + `raw/*.stderr.txt` 两份空捕获 +
`SHA256SUMS` 13 文件索引，自不含自哈希；worktree 侧
`artifacts/m14-105-isolated/`、`artifacts/m14-105-cockpit-staging/`、
`artifacts/m14-105-derive/` 与 stdout/stderr 现场为生成现场，gitignored，
不入库。）

## 5. 诚实边界与剩余阻塞（不伪称，逐项可执行收口）

1. **cockpit blockers = `long-soak:not-staged-required` +
   `provider-smoke:not-staged-required`**。long-soak：24h 审计未发生
   （M14-79 锚定 ≠ 通过、不授权任何 readiness；M14-93 到期审计/导出
   runner 已合入，其对真实生产 history/锚的执行是后续运维动作，本切片
   零 soak 接触、不预判其结果）。provider-smoke：M14-104（本基点合入）
   的最新真实执行中 search（SearXNG 上游境外网络不可达）与 llm
   （Ollama 端点进程未运行）真实失败、aggregate 未运行——解除条件
   （恢复出站网络 + Ollama 在场 + GPU 空闲窗口重跑三件套、随后
   aggregate）是后续运维动作，留 supervisor 决策，本切片不代跑、
   不搬运旧 pass。
2. **release-approval 从未发生**：人工审批 human-only，cockpit 永不
   接受、本切片不触碰、不代拟；`release_ready=false` /
   `production_ready=false` 不变。
3. **production-state 门是「呈现」不是「重执行」**：staged 的五门 +
   anchor companion 均为各切片 canonical 时点快照（M14-83 @ 5829ad9、
   M14-85 @ f47a1e4、M14-87），本切片零生产触碰约束下不重推导、不搬运
   冒充新执行；其对 current 生产状态的效力（尤其 M14-83 三门时距）
   由 supervisor 结合生产变更记录判断，cockpit 提供声明通道显式表达。
4. **turn-tls 仍 optional 未 stage**：公网语音发布形态才必需，本机/
   LAN 范围不阻断。
5. **生产仍运行 m14-70 镜像**（2026-09-19 切换后未再变更），本切片
   零调度面触碰、不构成任何部署；不授权任何部署。
6. **代码绑定门证据只对其执行时点的树成立**：ci-main/release-check
   现对 e5d5d3a current；main 再前移即再 stale（M14-91 冒烟已实证该
   语义），需下一个刷新切片真实重推导。
7. **GitHub Actions run 有平台保留期**（默认 90 天）；到期后按 §2.1
   命令重取即可。
