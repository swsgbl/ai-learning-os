# M14-92：current-main 代码绑定门证据刷新 + evidence-cockpit 聚合（实现/文档切片）

- 切片：分支 `ops/m14-92-current-main-code-evidence`（独立 worktree
  `m14-92-current-main-code-evidence`，基于 main
  `ddec7df463d2ae7d751e82a0b4fd319c1439c391`（PR #178 merge，精确基点，
  非 origin/main 快照推断；基点核验：`git rev-parse main` ==
  `git rev-parse origin/main` == `ddec7df…`，fetch 后复核一致）），单次
  local commit（不推送、不建 PR）。
- 目标：对 current main `ddec7df` 只刷新**代码绑定**发布门
  （ci-main / release-check），并以新合入的 M14-91 evidence-cockpit 作为
  聚合器重新汇总发布证据；沿用 M14-86/M14-90 证据契约/流程。绝不手改
  gate JSON、绝不合成结果、绝不复制 M14-90 产物（CI run / 隔离环境 /
  release-check 运行全部本轮新执行）、绝不搬运旧生产状态证据冒充新执行；
  `release_ready=false` / `production_ready=false` 全程不变。
- 零生产触碰（本切片全程）：零容器启停/重建、零计划任务操作、零
  DB/MinIO/语音引擎接触、零生产日志写入、零 secrets/env 读取、零 soak
  历史/锚定读写、零发布审批接触、零部署。唯一网络访问是 GitHub 只读
  API（`gh api`，已认证账号；`git fetch origin main` 复核）+ worktree
  从零环境的包安装（uv/npm）。

## 1. 基点事实（全部 git/远端可复核）

| 事件 | commit | 说明 |
|------|--------|------|
| PR #176 merge（M14-88 provider-smoke 恢复；M14-90 刷新基点） | `7cca73f` | M14-90 canonical 所锚时点 |
| PR #177 merge（M14-90 代码绑定门证据刷新 r2） | `650b02d`（含 `fdf862f`） | 纯 docs 4 文件 |
| PR #178 merge（M14-91 release-evidence-cockpit）→ **当前 main** | `ddec7df463d2ae7d751e82a0b4fd319c1439c391`（含 `37f3c37` + `e0a450f`） | 本切片精确基点 |
| main push CI run 35670971994 创建（push 事件） | ddec7df | 2026-09-22T00:12:27Z（UTC）；本地 2026-09-22 08:12:24 +0800 |

- `git fetch origin main` 复核：origin/main == `ddec7df`（无更新提交）。
- `git diff --stat 7cca73f..ddec7df`：5 commits、8 文件、+1923——PR #177
  M14-90 证据（docs-only）+ PR #178 M14-91 evidence-cockpit（**真实
  运行时改动**：`services/api/app/ops/evidence_cockpit.py` +507、
  `services/api/app/ops/cli.py` +155、
  `services/api/tests/test_evidence_cockpit.py` +723，其余为 docs）。
  services/api 有真实代码与测试集变更——代码绑定门**必须真实重新执行**
  才对 ddec7df 成立，这是本切片的直接动因（M14-91 冒烟已如实报出
  ci-main/release-check 对新 main stale）。
- M14-90 canonical（@ 7cca73f 时点）自此对 ddec7df stale——保留为历史
  记录不改写；本切片不搬运其产物。M14-83（@ 5829ad9 生产只读门）、
  M14-85（@ f47a1e4 backup-restore）、M14-87（audit-chain-anchor 门
  闭合 + 伴生锚）、M14-88（provider-smoke 恢复）canonical 保留为其各自
  时点记录，本切片不搬运、不改写。

## 2. 刷新执行记录（命令与结果，全部真实可复跑；全部为本轮新执行）

### 2.1 ci-main（真实远端 CI，非本地重跑）

```bash
gh api "repos/swsgbl/ai-learning-os/actions/runs?head_sha=ddec7df463d2ae7d751e82a0b4fd319c1439c391&per_page=20"
gh api "repos/swsgbl/ai-learning-os/actions/runs/35670971994/jobs?per_page=30"
```

- 命中断言（断言脚本复核归档 raw 响应，非仅依赖任务简报；任一断言
  失败即非零退出、不产出证据）：`workflow_runs` 中 event=push、
  head_branch=main 恰 1 条（该 SHA 全部 run 也仅此 1 条）——run
  **35670971994**（run_number 476），workflow
  `.github/workflows/ci.yml`，head_sha `ddec7df…`，status=completed，
  **conclusion=success**（created 2026-09-22T00:12:27Z / updated
  2026-09-22T00:17:16Z，
  https://github.com/swsgbl/ai-learning-os/actions/runs/35670971994）。
- jobs 断言：`total_count=5` 且全部 conclusion=success——API
  （ruff/pytest/migration）、Docker（compose build+healthy+smoke）、
  Release tools（Python tests）、Android（unit test/lint/assemble）、
  Web（test/typecheck/lint/build）；每 job head_sha == ddec7df。
- 按 `_eval_ci_main` 契约（`services/api/app/ops/release_readiness.py`：
  gate/run_id/merge_commit/conclusion 白名单标量）**程序化派生**
  `evidence/ci-main.json`：断言驱动脚本 `derive_ci_main.py`（gitignored
  `artifacts/m14-92-derive/`，ruff check 通过 + py_compile 通过）从 raw
  事实拼装 + 溯源 source 串；断言不过即失败，无手写 pass。未复用
  M14-90 的 run 35642190403 或其 raw 文件。
- raw API 响应原样归档：`raw/gh-runs-ddec7df.json`、
  `raw/gh-jobs-35670971994.json`（哈希见 §4）。

### 2.2 release-check（隔离本地 full 重跑，干净 ddec7df 执行树）

从零构建全新隔离环境（不复用 M14-90 任何产物/环境；其 `.venv`/
`node_modules` 属 m14-90 worktree，本切片在 m14-92 worktree 独立重建）：

```bash
uv venv .venv --python 3.12          # CPython 3.12.14
uv pip install -r services/api/requirements.txt -r services/api/requirements-dev.txt
npm ci --no-audit --no-fund          # 411 packages（node v22.23.2 / npm 12.0.2）
```

（npm 提示 `unrs-resolver` postinstall 被 allowScripts 策略阻止——与
M14-90 相同的已知提示，如实记录；该脚本产物非门禁依赖，本轮 10 门全绿
证实无影响。）

**执行树前置核验**（declared-head 诚实性前提）：运行前
`git rev-parse HEAD` == `ddec7df…` 且 `git status --porcelain` 为空
（tracked-clean，仅 gitignored `.verify/`/`artifacts/` 增量）——
release-check 的实际执行树即精确干净 ddec7df，后续 cockpit
`--gate-declared-head release-check=ddec7df` 声明成立。

隔离编排器（一次性 SQLite → alembic upgrade head → 127.0.0.1 临时
uvicorn → /health 就绪 → full 10 门 → 原子落盘 → finally 关停临时 API；
环境剥离 AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL，显式
APP_ENV=development / VOICE_MODE=local——不连接任何生产面；worktree
绝对路径调用；新工作区 `artifacts/m14-92-isolated/`）：

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli release-check-isolated \
  --workdir "<worktree>/artifacts/m14-92-isolated" --json
# exit 0
```

- 结果：**all_green=true，10/10 pass**（generated_at
  2026-09-22T00:35:30Z，execution_scope=full）——api-lint（ruff All
  checks passed）/ web-lint / web-typecheck / web-build /
  api-test（**pytest 4054 passed, 33 skipped, 1 warning in 238.32s**；
  较 M14-90 @ 7cca73f 的 4024 恰 +30——PR #178 新增
  `tests/test_evidence_cockpit.py` 30 项的预期对账，非搬运复算）/
  migration（current == head == `0027_audit_chain`）/ backup
  （aios-backup-v1 tables:30 files:1）/ voice（local 合成 audio/wav
  17324 bytes）/ license（api deps 15 / web ok / models 7 / sources 6）/
  e2e（walkthrough 5 步 1259 ms）。
- `release-check-isolated.json` 逐字节复制改名为 canonical
  `evidence/release-check.json`（程序化比对 byte-identical=True；文件
  自带 gate 自标识 `release-check`，非手改）。

### 2.3 evidence-cockpit 聚合（M14-91 工具首次作为 current 刷新聚合器）

在 tracked docs 编辑**之前**、HEAD 仍为干净 `ddec7df` 时运行。canonical
源路径逐一取自 M14-91 README §5/`staged-inventory.txt` 与其 canonical
`cockpit-smoke.json` 的 gate_bindings 登记（不猜测）；staging 前先对 7
个既有生产状态源文件做 sha256 复核——与 M14-91 staged-inventory 登记
值逐一 **MATCH**（audit-chain-anchor `a8c54c5e…` / audit-anchor
`d2bfd877…` / backup-restore `ed0fc5b4…` / preflight `b3a2be66…` /
legacy `83cd62bd…` / draft `e90ae04c…` / provider-smoke
`87e76787…`）。

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli evidence-cockpit \
  --gate-source ci-main="<m14-92 canonical>/evidence/ci-main.json" \
  --gate-source release-check="<m14-92 canonical>/evidence/release-check.json" \
  --gate-source audit-chain-anchor="<主仓>/.verify/artifacts/m14-87-audit-chain-current-gate/evidence/audit-chain-anchor.json" \
  --gate-source production-preflight="…m14-83…/evidence/production-preflight.json" \
  --gate-source legacy-papers="…m14-83…/evidence/legacy-papers.json" \
  --gate-source draft-ownership="…m14-83…/evidence/draft-ownership.json" \
  --gate-source backup-restore="…m14-85…/evidence/backup-restore.json" \
  --gate-source provider-smoke="<m14-88 worktree>/.verify/artifacts/m14-88-provider-smoke-recovery/provider-smoke.json" \
  --anchor-companion "<主仓>/.verify/artifacts/m14-87-audit-chain-current-gate/evidence/audit-anchor.jsonl" \
  --gate-declared-head release-check=ddec7df463d2ae7d751e82a0b4fd319c1439c391 \
  --current-head ddec7df463d2ae7d751e82a0b4fd319c1439c391 \
  --staging-dir "<worktree>/artifacts/m14-92-cockpit-staging" \
  --json --output "<worktree>/artifacts/m14-92-cockpit-report.json"
# exit 1（cockpit_ready=false，如实——见下）
```

- `--gate-declared-head release-check=ddec7df` 的合法性：§2.2 前置
  核验证明实际执行树即干净 ddec7df（gate JSON 本身不内嵌 head，由调用
  方显式声明；声明与事实一致方可用）。
- 结果（报告归档 canonical `cockpit-report.json`）：
  **evaluator pass=8 / pending=0 / blocked=0 / missing=3 / malformed=0
  / tampered=0**；**release_ready=false**、**production_ready=false**
  （恒 false）、cockpit_ready=false、**exit 1**。
- **code-bound 两门刷新生效**：ci-main stale=**current**（内嵌
  merge_commit=ddec7df == current HEAD）、release-check stale=
  **current**（flag 声明 ddec7df）——M14-91 冒烟时的
  `ci-main:stale` / `release-check:stale` 两项 blocker 消失。
  **blockers 仅剩 `long-soak:not-staged-required`**（required 门未
  stage，按收紧语义如实阻断）。
- production-state 五门（audit-chain-anchor / production-preflight /
  legacy-papers / draft-ownership / backup-restore / provider-smoke 六
  门 staged + anchor companion）undeclared 如实呈现不 block（M14-91
  §1.1 第 5 条口径：呈现性选择，效力判断留 supervisor + 声明通道）；
  release-approval「按策略永不接受，非 blocker」、turn-tls「optional：
  本机/LAN 范围不阻断」、long-soak「required 未 stage → blocker」三类
  摘要显式区分。
- **staged 9 文件与 source 逐字节一致**：工具内建写后重读断言 + 外部
  独立复核 **9/9 IDENTICAL**（cmp 语义逐字节比较）；7 个既有文件哈希
  与 M14-91 staged-inventory 登记值交叉一致、2 个新文件（ci-main
  `77cf8617…` 1112 bytes / release-check `02686872…` 2491 bytes）与
  本切片 canonical 实际哈希一致——来源零改动 + 逐字节 staging 端到端
  实证（清单归档 canonical `staged-inventory.txt`）。
- exit 1 是**诚实预期**：long-soak（24h 审计未发生）与
  release-approval（human-only 从未发生）缺席之下，cockpit 不可能
  ready；本切片不以任何方式强制 exit 0。

## 3. 聚焦验证与静态检查（真实执行结果）

- 聚焦契约测试（cockpit 三件套，本切片证据链所依赖的工具面）：

```bash
cd services/api && "<worktree>/.venv/Scripts/python.exe" -m pytest -q \
  tests/test_evidence_cockpit.py tests/test_release_readiness.py tests/test_release_closure_manifest.py
# 145 passed in 1.59s（与 M14-91 remediation 后记录一致）
```

- release-check 契约三件套（本切片执行了 release-check-isolated）：

```bash
cd services/api && "<worktree>/.venv/Scripts/python.exe" -m pytest -q \
  tests/test_release_readiness.py tests/test_release_check_isolated.py tests/test_release_checklist.py
# 137 passed, 1 warning in 2.64s（与 M14-90 记录一致）
```

- ruff：本切片唯一触碰的 Python 工具（gitignored 断言派生脚本
  `derive_ci_main.py`）`ruff check` All checks passed（tracked Python
  零改动）；`py_compile` 通过。
- canonical 六份文件 `json.load` 解析通过（cockpit 报告消费即契约
  校验：malformed=0/tampered=0）；canonical `release-check.json` 与
  隔离运行原始产物**逐字节一致**（程序化比对 True）。
- `git diff --check` 干净（docs-only）；分支卫生：单 commit 后
  tracked-clean（worktree 侧 `.venv`/`node_modules`/`artifacts/`/
  `.verify/` 均 gitignored 未入库）；未 push、未建 PR。

## 4. canonical 证据清单（gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：worktree `.verify/artifacts/m14-92-current-main-code-evidence/`

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/ci-main.json` | 1112 | `77cf861744bd5f7e615869619110ffc020ec39c017d240bcbbabcc3dbd934a49` |
| `evidence/release-check.json` | 2491 | `02686872cea9eb661f945b871b1396409bc36182382216819e2af8390dc21d2f` |
| `raw/gh-runs-ddec7df.json` | 12196 | `d7f91cba6fc836be16b6f2e356f306e301e495916087b7aeaa1a648b68d65969` |
| `raw/gh-jobs-35670971994.json` | 12334 | `50a613e4e71fb0fb0fea87e2c80a11679c9f31b13758ddd4c9628d83964988d0` |
| `cockpit-report.json` | 17337 | `308e66098b6aca97acb1239aa01457734fa7100a17c318a321388d9cdca92272` |
| `staged-inventory.txt` | 824 | `b95261c4a69895775ff895d47ee42aa911f14385d97dee7036ad6f4660f4060d` |

（worktree 侧 `artifacts/m14-92-isolated/`、`artifacts/m14-92-cockpit-staging/`、
`artifacts/m14-92-derive/` 与 stdout 日志为生成现场，gitignored，不入库。）

## 5. 诚实边界与剩余阻塞（不伪称，逐项可执行收口）

1. **cockpit blockers 仅剩 `long-soak:not-staged-required`，但这不是
   「接近发布」**：long-soak 24h 审计未发生——M14-79 权威锚定记录
   （canonical
   `.verify/artifacts/m14-79-soak-window-anchor/soak-window-anchor.json`）
   锚点 = 2026-09-21T15:00:01Z、最早审计时点 = 2026-09-22T15:00:01Z；
   本切片 cockpit 生成于 2026-09-22T00:3xZ，早于最早审计时点，24h 窗口
   结局未判定，long-soak 如实 not-staged，不预宣称。锚定 ≠ long-soak
   通过、不授权任何 readiness。
2. **release-approval 从未发生**：人工审批 human-only，cockpit 永不
   接受、本切片不触碰、不代拟；`release_ready=false` /
   `production_ready=false` 不变。
3. **production-state 门是「呈现」不是「重执行」**：staged 的六门 +
   anchor companion 均为各切片 canonical 时点快照（M14-83 @ 5829ad9、
   M14-85 @ f47a1e4、M14-87、M14-88），本切片零生产触碰约束下不重
   推导、不搬运冒充新执行；其对 current 生产状态的效力（尤其 M14-83
   三门时距）由 supervisor 结合生产变更记录判断，cockpit 提供声明通道
   显式表达。provider-smoke 恢复的易失性（依赖宿主 sing-box/ollama
   用户进程）仍如 M14-88 记录——发布窗口前需按当时拓扑真实重推导。
4. **turn-tls 仍 optional 未 stage**：公网语音发布形态才必需，本机/
   LAN 范围不阻断。
5. **生产仍运行 m14-70 镜像**（2026-09-19 切换后未再变更），本切片
   零调度面触碰、不构成任何部署；不授权任何部署。
6. **M14-89 不在本切片范围内**：其 harmony-auth-session worktree 存在
   但截至基点无 PR、未合入——本切片不声称、不预判其结论；下一个代码
   绑定门刷新切片应基于届时的 current main 重取。
7. **代码绑定门证据只对其执行时点的树成立**：ci-main/release-check
   现对 ddec7df current；main 再前移即再 stale（M14-91 冒烟已实证该
   语义），需下一个刷新切片真实重推导。
8. GitHub Actions run 有平台保留期（默认 90 天）；到期后按 §2.1 命令
   重取即可。
