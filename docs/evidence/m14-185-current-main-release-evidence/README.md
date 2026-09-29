# M14-185：current-main 发布证据刷新（PR #275 后；ci-main + release-check 真实重跑 + provider-smoke/long-soak 只读复用 + cockpit 零 blocker 聚合）

- 切片：分支 `ops/m14-185-current-main-release-evidence`（复用
  `m14-180-public-edge-stability-probe` worktree，基于 main
  `8d52f009420df944f58f212bf6294e8977557ccd`（PR #275 merge = M14-184
  Phase 0 subprocess timeout 工具合入，精确基点 = 任务执行时直连核验的
  origin/main，起点即 tracked-clean 核验通过），单 local commit，
  **不 push、不开 PR（任务书指令）**。
- **动因与定性（为什么 M14-152 之后触发刷新）**：M14-152 的代码绑定门
  证据绑定执行基点 `7438227`，其后 `7438227..8d52f00` 区间 = **101
  commits**（PR #240–#275，m14-153 至 m14-184 各切片全部合并）——含
  **大量真实运行时代码与测试面**：`services/api/tests/` 11 文件
  **+6159/−2**（9 个新文件：public-edge 五件套/nginx base path/web
  gateway/edge templates/frpc controller/healthcheck base path + 2 个
  修改文件净增 2 测试），tools/ops、web/、platform、android/harmony
  与 docs 大量变更——代码绑定门（ci-main/release-check）相对当前 main
  漂移**远非 docs-only**，按既有契约在当前 HEAD `8d52f00` 上真实重执行
  两门；**绝不复用/搬运 M14-152 的 ci-main/release-check 产物或计数**
  （run 36219558569 与 7438227 上的 release-check 结果均不复用）。
- **区间关键不变量（对账与复用的前提，全部实测）**：
  `services/api/app/` 全树、`requirements.txt`/`requirements-dev.txt`、
  `alembic/` 在 `7438227..8d52f00` 区间**零变更**（name-only diff 为空）
  ——release-readiness evaluator、release-check 编排器与 CLI 合同与
  M14-152 时点逐字节一致，本切片可径用其已文档化契约；alembic head 仍
  `0027_audit_chain`（区间无新迁移）。
- **no-infinite-refresh 边界（沿 M14-152 声明）**：docs-only 入库不构成
  刷新触发——代码绑定门证据只对其执行时点的树内容成立；只有区间含
  运行时代码或测试面变更时才需要下一轮真实重执行。本切片基点
  `8d52f00` 即刷新时点的 current main；若 main 在本切片执行期间/之后再
  前移且含非 docs 变更，stale 由下一个刷新切片覆盖，本切片不递归追新
  （不做「无限刷新」），也不在证据链中声称对 8d52f00 之后的树有效。
- provider-smoke / long-soak 按任务边界**零重跑、零生产接触**，只读
  核验出处与精确哈希后逐字节复用（§2.3/§2.4）；**`release_ready=false`
  / `production_ready=false` 全程不变**——release-approval 是 human-only
  门、从未发生，cockpit 按策略永不接受它（not-staged 呈现、不计
  blocker）；turn-tls optional 不阻断。
- 零生产触碰（本切片全程）：零容器启停/重建、零计划任务操作、零
  DB/MinIO/语音引擎接触、零生产日志写入、零 secrets/env 读取、零 soak
  历史重跑/锚定写入、零发布审批接触、零部署、零 Ollama/FunASR/
  CosyVoice/SearXNG/代理/CC Switch/scheduler 生命周期变更。唯一网络
  访问是 GitHub 只读 API（`gh api` 双查询直连成功、零代理配置变更）
  + worktree 从零环境的包安装（uv/npm，日志归档 §5）。
- 零秘密政策：本 README 与全部 canonical 工件不含任何 token/key/
  password/带凭据 URL/env 值（§3 五类模式扫描 26 文件 0 命中；
  canonical 内的 Windows 绝对路径为 gitignored 本地工件路径，非秘密）。
- 本 README 为唯一入库证据文件；canonical 原始证据（gitignored）位于
  worktree `.verify/artifacts/m14-185-current-main-release-evidence/`
  （SHA256SUMS 索引全工件，自不含自哈希；§5）。

## 1. 基点事实（git/远端可复核）

| 事件 | commit | 说明 |
|------|--------|------|
| PR #239 merge = M14-152 执行基点 | `7438227ce1cd1a8059ef621293ba195e4896ce11` | M14-152 证据绑定头 |
| PR #240–#275 合并序列（101 commits） | `7438227..8d52f00` | m14-153 至 m14-184 各切片；16 个可见 PR 全部合并于 2026-09-28/29 |
| PR #275 merge（M14-184 工具合入）→ **当前 main** | `8d52f009420df944f58f212bf6294e8977557ccd` | 本切片精确基点 |
| main push CI run 36570439902 创建（push 事件） | 8d52f00 | 2026-09-29T12:47:02Z（UTC） |

- 区间**非 docs-only**（101 commits、110 文件 +30854/−40）：public-edge
  演进（M14-180 探针 → M14-181 设计 → M14-182 Phase 0 基线工具 →
  M14-184 超时派生修复）、android/harmony 发布链、monitoring/
  drift-watch、web 变更与对应测试面爆炸——但 `services/api/app/` 与
  依赖/迁移**零变更**（见开头不变量）。本切片 release-check 的 pytest
  计数较 M14-152 实测 **+510 passed（5121→5631）+3 skipped（33→36）**：
  净增 collected **513 = 9 个新文件 511 + 2 个修改文件净增 2**，
  且 513 = 510 + 3（passed+skipped 增量）自洽——区间测试面增长的精确
  对账（§2.2，实测吻合、无硬编码预期）。
- M14-152 canonical 保留为其时点历史记录不改写（本切片 long-soak 复用
  即逐字节读其 canonical §2.4，零修改）；本切片不搬运其 ci-main/
  release-check 产物。M14-83（@ 5829ad9 生产只读门三门）、M14-85
  （@ f47a1e4 backup-restore）、M14-87（@ audit-chain-anchor 门 +
  伴生锚）canonical 按 M14-91 §5 登记路径**原样 staging**（§2.5，
  staging 前六源完整 sha256 复核 6/6 MATCH）。

## 2. 刷新执行记录（ci-main/release-check 为本轮新执行；provider-smoke/long-soak 为只读核验登记复用）

### 2.1 ci-main（真实远端 CI，非本地重跑）

```bash
gh api "repos/swsgbl/ai-learning-os/actions/runs?head_sha=8d52f009420df944f58f212bf6294e8977557ccd"
gh api "repos/swsgbl/ai-learning-os/actions/runs/36570439902/jobs"
```

（双查询直连成功，未改任何代理配置；两份 stderr 捕获为空文件，
SHA256 `e3b0c442…` = 空文件标准值。任务简报给定的 run 36570439902 /
5/5 success 经 raw 响应 live 复核后才绑定。）

- 命中断言（断言脚本复核归档 raw 响应而非仅信任务简报；任一断言
  失败即非零退出、不产出证据）：`workflow_runs` 中 total_count=1、
  event=push、head_branch=main 恰 1 条——run **36570439902**
  （run_number **686**），head_sha `8d52f00…`，status=completed，
  **conclusion=success**（created 2026-09-29T12:47:02Z / updated
  2026-09-29T12:52:03Z，
  https://github.com/swsgbl/ai-learning-os/actions/runs/36570439902）。
- jobs 断言：`total_count=5` 且全部 conclusion=success——API
  （ruff/pytest/migration）、Docker（compose build+healthy+smoke）、
  Release tools（Python tests）、Android（unit test/lint/assemble）、
  Web（test/typecheck/lint/build）；每 job head_sha == 8d52f00、job 名
  集合精确匹配且无重复（与 M14-116/122/123/125/146/149/152 同一五
  job 契约）。
- 按 `_eval_ci_main` 契约**程序化派生** canonical `evidence/ci-main.json`
  （1324 bytes、SHA256 `c309323e…`）：断言驱动脚本从 raw 事实拼装 +
  溯源 source 串；未复用 M14-152 的 run 36219558569 或其 raw 文件。
- raw API 响应原样归档：`raw/gh-runs-8d52f00.json`（12267 bytes）、
  `raw/gh-jobs-36570439902.json`（13027 bytes；哈希见 §5）。

### 2.2 release-check（隔离本地 full 重跑，干净 8d52f00 执行树）

从零构建全新隔离环境（**本切片重建** worktree `services/api/.venv`：
uv venv --python 3.12（CPython 3.12.14）+ uv pip install -r
requirements.txt -r requirements-dev.txt；根目录 `npm ci --no-audit
--no-fund` 411 packages，node v22.23.2 / npm 12.0.2——npm 提示
`unrs-resolver` postinstall 被 allowScripts 策略阻止，与 M14-92 起各轮
相同的已知提示，本轮 10 门全绿证实无影响）。

**执行树前置核验**（declared-head 诚实性前提）：运行前
`git rev-parse HEAD` == `8d52f00…` 且 `git status --porcelain` 为空
（tracked-clean；执行日志归档 `logs/pre-release-check-head.log`
留痕）——release-check 的实际执行树即精确干净 8d52f00，后续 cockpit
`--gate-declared-head release-check=8d52f00` 声明成立。

隔离编排器（一次性 SQLite → alembic upgrade head → 127.0.0.1 临时
uvicorn → /health 200 → full 10 门 → 原子落盘 → finally 关停临时 API；
环境剥离 AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL，显式
APP_ENV=development / VOICE_MODE=local——不连接任何生产面；新工作区
`artifacts/m14-185-isolated/`）：

```bash
cd services/api
"<worktree>/services/api/.venv/Scripts/python.exe" -m app.ops.cli release-check-isolated \
  --workdir "<worktree>/artifacts/m14-185-isolated" --json
# exit 0
```

- 结果：**all_green=true，10/10 pass**（generated_at
  2026-09-29T15:25:29.703838+00:00，execution_scope=full）——api-lint
  （ruff All checks passed）/ web-lint / web-typecheck / web-build /
  **api-test（pytest 5631 passed, 36 skipped, 1 warning in 302.10s；
  较 M14-152 @ 7438227 的 5121/33 恰 +510/+3——净增 collected 513
  = 区间 9 个新测试文件 511（public-edge/web-gateway/frpc/
  healthcheck 套件）+ 2 个修改文件净增 2 的精确对账，实测吻合、非
  断言预期）** / migration（alembic current == head ==
  0027_audit_chain）/ backup（aios-backup-v1 tables:30 files:1）/
  voice（local 合成 audio/wav 17324 bytes）/ license（api deps 15 /
  web ok / models 7 / sources 6）/ e2e（walkthrough 5 步，1970 ms）。
- `release-check-isolated.json` 逐字节复制改名为 canonical
  `evidence/release-check.json`（2491 bytes，SHA256 `46d99f83…`；文件
  自带 gate 自标识 `release-check`，非手改；复制后 filecmp 逐字节
  比对 IDENTICAL）。

### 2.3 provider-smoke（不重跑、不触碰生产/provider；只读核验 + 哈希锁定复用）

- 复用源：`artifacts/temp/provider-smoke/20260926T0106/provider-smoke.json`
  （主仓，564 bytes、SHA256
  `d589181e078de875faac11052e148d5ff32b0b6f4d7b41fb716b4a584c60a761`）
  ——与 M14-149/M14-152 登记哈希**逐字节精确一致**后才登记。该聚合是
  M14-148 恢复轮真实重跑产物，M14-149 §2.3 首次只读登记、M14-152 §2.3
  同源复用；本切片为同哈希链第三次只读登记。
- **只读核验（prepare 脚本，任一失败即非零退出不登记）**：聚合九键
  schema `provider-smoke-evidence-v1`、gate 自声明 `provider-smoke`、
  拓扑 `voice_mode=local`、三槽位 executed=true 且 result=pass
  （evidence_step local-voice-smoke / search-smoke / llm-smoke）；三份
  输入逐份核验（schema/step/executed/pass/exit_code=0/时间自洽
  started<completed≤聚合时刻）：search-smoke 7496ms、local-voice-smoke
  18442ms、llm-smoke 12213ms；**三份失败 attempt 如实保留未被改写**
  （仍 executed=true/result=fail/exit_code=1）：attempt1-envmiss 60ms、
  attempt2-querymiss 769ms、attempt3-timeout 10483ms——真实执行痕迹，
  本切片零改写。
- **复用语义论证（区间零 provider 执行，三支柱）**：(a) 代码面——
  `services/api/app/` 全树（provider 运行时代码所在）区间
  `7438227..8d52f00` 零变更；(b) 合并面——区间 16 个可见 PR 全部合并于
  2026-09-28/29，集中于 android/harmony 发布链、public-edge 演进、
  monitoring/drift-watch 与 docs，无 provider 生命周期变更；(c) 生产面
  ——区间唯一生产操作为 M14-179 web-only 容器重建（2026-09-29），其
  README 明示 API/DB/SearXNG/voice/provider 服务全部零接触。磁盘上
  无更新的 provider-smoke 聚合（20260926T0106 仍为最新真实执行）。
- 逐字节复制为 canonical `evidence/provider-smoke.json` 后重哈希
  **逐字节一致（564 bytes 同哈希）**。执行记录：
  `logs/prepare-reused-evidence.log`。
- **原始时间边界显式：2026-09-26T01:12:09Z→01:14:14Z**（M14-148
  恢复轮时点证据），距本切片 cockpit 聚合时点（2026-09-29T15:28:29Z，
  §2.5）**约 3 天 14 小时**（较 M14-152 复用时的 4 小时 20 分显著
  老化），窗口外状态不承诺；易失性由后续刷新切片覆盖。年龄如实披露，
  不伪称新鲜。

### 2.4 long-soak（不重跑、不触碰生产/计划任务；只读校验 + 逐字节复用）

- 复用口径与 M14-107/116/122/123/125/146/149/152 完全一致：源为
  M14-152 canonical（其自身亦逐字节复用自 M14-149），817 bytes，
  SHA256
  `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec`
  ——同哈希链登记后才复制。
- 复制到本切片 canonical `evidence/long-soak.json` 后**重新哈希 +
  JSON 断言**：逐字节一致（817 bytes 同哈希）；gate 自声明
  `long-soak`、audit_schema_version=2、tool
  `tools/ops/soak_stability_audit.py`、策略四值恰 1440/15/20/500、
  classification=**pass**、reasons=[]。
- 窗口事实（README 如实转述，非本切片重跑）：**24h 窗口
  2026-09-22T02:00:01Z → 2026-09-23T02:00:01Z（跨度恰 1440.0 分钟），
  97 样本全 ok / 0 warn / 0 critical，max_observed_gap_minutes=17.25
  ≤ 20**。**诚实边界：该窗口早于 M14-117 生产切换且执行于 m14-70
  栈**——切换后尚无 24h soak 窗口；本切片按任务边界不重跑、**不制造
  新窗口**，是否需要切换后新 soak 窗口由 supervisor 决策。

### 2.5 evidence-cockpit 聚合（四门刷新/复用后重新汇总：cockpit_ready=true）

在 tracked docs 编辑**之前**、HEAD 仍为干净 `8d52f00` 时运行。
canonical 源路径逐一取自 M14-91 README §5 / staged-inventory 登记与
M14-152 §2.5 同一路径（不猜测）；staging 前对 6 个既有生产状态源文件
做**完整 sha256** 复核——与 M14-91/M14-116/M14-122/M14-123/M14-146/
M14-152/185 登记值逐一 **MATCH（6/6）**：audit-chain-anchor
`a8c54c5e…` / audit-anchor companion `d2bfd877…` / backup-restore
`ed0fc5b4…` / preflight `b3a2be66…` / legacy `83cd62bd…` / draft
`e90ae04c…`（执行记录 `logs/prepare-reused-evidence.log`）。

```bash
cd services/api
"<worktree>/services/api/.venv/Scripts/python.exe" -m app.ops.cli evidence-cockpit \
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
  --gate-declared-head release-check=8d52f009420df944f58f212bf6294e8977557ccd \
  --current-head 8d52f009420df944f58f212bf6294e8977557ccd \
  --staging-dir "<worktree>/artifacts/m14-185-cockpit-staging" \
  --json --output "<worktree>/artifacts/m14-185-cockpit-report.json"
# exit 0（cockpit_ready=true）
```

- **current-head 与 release-check head 显式声明
  `8d52f009420df944f58f212bf6294e8977557ccd`**；合法性由 §2.2 前置
  核验支撑（实际执行树即干净 8d52f00）。
- 结果（报告归档 canonical `cockpit-report.json`，18990 bytes）：
  **evaluator pass=9 / pending=0 / blocked=0 / missing=2 / malformed=0
  / tampered=0**；**cockpit_ready=true、cockpit_blockers=[]、
  required_not_staged=[]、exit 0**。同时 **release_ready=false、
  production_ready=false（恒 false）**：not_pass_required 恰为
  `release-approval`（human-only 从未发生，cockpit 按策略永不接受、
  不计入 blocker——「无技术 blocker」与「不可放行」两件事同时成立），
  not_pass_optional 为 `turn-tls`（optional：本机/LAN 发布形态不需要
  公网 TURN，不计入 fail）。**9 个必需非审批门全部 pass**。
- **code-bound 两门刷新生效**：ci-main stale=**current**（内嵌
  merge_commit=8d52f00 == current HEAD，declared_head_origin=
  embedded）、release-check stale=**current**（flag 声明 8d52f00，
  origin=flag）。
- provider-smoke 以当前真实聚合（`d589181e…`）staged 即 pass；long-soak
  staged pass（真实 24h 稳定窗口，§2.4）；production-state 五门 +
  anchor companion 以历史 canonical snapshot 原样 staged、undeclared
  如实呈现不 block（M14-91 §1.1 第 5 条口径；production-preflight
  门的取源说明与 M14-149/M14-152 §2.5 相同：M14-117 切换后 preflight
  JSON 缺 `gate` 自声明不能直接 stage，契约 fail-closed，本切片不手改
  补字段）。
- **staged 10 文件（9 门 + anchor companion）与 source 逐字节一致**：
  工具内建写后重读断言 + 外部独立复核 **10/10 IDENTICAL**（filecmp
  语义逐字节比较；清单归档 canonical `staged-inventory.txt`，10 条）。

## 3. 聚焦验证与静态检查（真实执行结果）

- 聚焦契约测试（cockpit/release/provider/approval-draft 九套件，本切片
  证据链所依赖的工具面）：

```bash
cd services/api && "<worktree>/services/api/.venv/Scripts/python.exe" -m pytest -q \
  tests/test_evidence_cockpit.py tests/test_release_readiness.py tests/test_release_closure_manifest.py \
  tests/test_release_check_isolated.py tests/test_release_checklist.py \
  tests/test_provider_smoke_evidence.py tests/test_provider_smoke_preflight.py tests/test_searxng_local_provider.py \
  tests/test_release_approval_draft.py \
  --basetemp "<scratch>/m14-185-current-main-release-evidence/focused"   # Windows 需先 mkdir -p 预建父目录
# 444 passed, 1 warning in 5.48s
```

（计数与 M14-152 的 444**恰相同**——九套件文件在
  `7438227..8d52f00` 区间零变更（区间新增 9 文件为 public-edge 类、
  修改 2 文件为 rehearsal/workflow-runtime 类，均不在九套件内），工具
  契约面实测未漂移，与 `services/api/app/` 零变更的静态结论互证。）
- `ruff check services/api`：All checks passed（本切片 tracked Python
  零改动——入库文件仅 docs/evidence README、CHANGELOG 与
  PROJECT_STATUS 条目，基线复核）。
- canonical 完整性：`SHA256SUMS` 全工件索引（29 文件，LF 文本模式，自
  不含自哈希）；canonical JSON `json.load` 解析全通过
  （ci-main/release-check/provider-smoke/long-soak/cockpit-report/
  两 raw）；JSON 契约断言脚本 **38 项全过**（ci-main run_id
  36570439902/merge_commit 8d52f00/conclusion、raw runs push@main 唯一/
  run_number 686、raw jobs 5/5 精确集合、release-check all_green 10/10
  + pytest 5631/36 + 对 M14-152 +510/+3 对账 + 净增 513=511+2=510+3
  自洽 + migration head + e2e 5 步、provider-smoke local 拓扑三槽位
  pass + 原始时间边界 2026-09-26T01:14:14Z + 同哈希、long-soak 策略
  四值 + 97 ok + max gap 17.25 + span 1440 + classification pass +
  同哈希、cockpit 9 pass/2 missing + not_pass_required 恰
  [release-approval] + not_pass_optional 恰 [turn-tls] +
  release_ready/production_ready false + 两 code-bound 门 current
  （embedded/flag）+ approval not-staged human-only + 六源 binding
  sha256 与登记一致 + staged 10/10 byte_identical + inventory 10 条全
  IDENTICAL）。
- 秘密扫描：canonical 全部 26 工件对 credential 赋值 / OpenAI 风格
  key / 私钥块 / 带凭据 DB URL / URL userinfo 五类模式扫描 **0 命中**
  （报告/清单内的 Windows 绝对路径为 gitignored 本地工件路径，非
  秘密）。
- `git diff --check` 干净（docs-only）；新增行扫描：secret 值 / 本地
  绝对路径（盘符或根路径形态，正反斜杠变体）/ U+FFFD 替换字符对全部
  新增行 **0 命中**（本 README 与 CHANGELOG/PROJECT_STATUS 内路径均为
  仓库相对或 gitignored 工件占位符形式）。
- 分支卫生：单 commit 后 tracked-clean（worktree 侧
  `.venv`/`node_modules`/`artifacts/`/`.verify/` 均未入库）。

## 4. 与 M14-152 的差异（同一契约下的净变化）

| 维度 | M14-152 @ 7438227 | M14-185 @ 8d52f00 |
|------|-------------------|--------------------|
| 证据定性 | current-main（区间恰 M14-151 单提交：审批 DRAFT 工具 + evaluator 防线 + 37 测试） | **current-main（区间 101 commits：public-edge 演进 + android/harmony 发布链 + monitoring + 测试面 +6159 行；services/api/app 树区间零变更）** |
| ci-main run | 36219558569（run_number 601） | **36570439902**（run_number 686，本轮新派生） |
| release-check pytest | 5121 passed / 33 skipped（252.00s） | **5631 passed / 36 skipped**（302.10s，+510/+3 = 区间 9 新文件 511 + 2 修改净增 2 的精确对账） |
| release-check e2e | 5 步 1253 ms | **5 步 1970 ms** |
| alembic head | 0027_audit_chain | **0027_audit_chain（区间无新迁移）** |
| provider-smoke | 当前真实聚合 `d589181e…`（20260926T0106，复用时龄 4h20m） | **同源同哈希只读复用（区间零 provider 执行三支柱论证，§2.3；复用时龄约 3 天 14 小时，如实披露）** |
| long-soak | staged pass（同哈希 `d939c652…`） | **staged pass（同哈希第三次只读登记；窗口早于切换且在 m14-70 栈，如实呈现）** |
| evaluator 变更 | 区间有变更（M14-151 evaluator 防线 +37 测试覆盖） | **区间零变更（`services/api/app/` 全树 byte-identical，聚焦测试 444 恰同互证）** |
| evaluator 结果 | pass=9 / missing=2 | **pass=9 / missing=2（不变）** |
| cockpit blockers | [] | **[]（无技术 blocker）** |
| cockpit_ready / exit | true / 0 | **true / 0（保持）** |
| release_ready / production_ready | false / false | **false / false（不变——release-approval human-only 缺席）** |
| staged 文件 | 10（10/10 IDENTICAL） | **10（同，10/10 IDENTICAL；production-state 六源完整 sha256 6/6 MATCH）** |
| 聚焦契约测试 | 444 passed（九套件） | **444 passed（九套件恰同——区间零变更实证）** |
| 契约断言 | 35 项 | **38 项（断言面按本切片事实重钉）** |
| 交付形态 | 单 local commit，不 push、不开 PR | **单 local commit，不 push、不开 PR（任务书指令）** |

## 5. canonical 证据清单（gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：worktree `.verify/artifacts/m14-185-current-main-release-evidence/`

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/ci-main.json` | 1324 | `c309323ed22521c7b375bcfc59ca14a3938467de3e27a605ac73193b49ac589c` |
| `evidence/release-check.json` | 2491 | `46d99f83165823a5c80ea839f897a68c73652506aba4af6fbd04bd4be3606824` |
| `evidence/provider-smoke.json` | 564 | `d589181e078de875faac11052e148d5ff32b0b6f4d7b41fb716b4a584c60a761` |
| `evidence/long-soak.json` | 817 | `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec` |
| `raw/gh-runs-8d52f00.json` | 12267 | `ac3eb5afd5e5ef9b0e546514c52ba4c012e3b654b3a5c0fc6fd0e64732afe860` |
| `raw/gh-jobs-36570439902.json` | 13027 | `9f122c7040dc5ed366a492900b06b81a966d90125dbce8242dc9d03a07e64860` |
| `cockpit-report.json` | 18990 | `ebe664d39112436c101e19fb3b0c09f44c339436b820ea70d3b7d45d9f5950b6` |
| `staged-inventory.txt` | 1211 | `aea858afd6bead11070535693367dbec4143156d489da730113d6d3c3e188801` |

（另 `logs/` 下执行日志与派生/校验/断言脚本：env-setup / npm-ci /
pre-release-check-head / derive-ci-main 派生脚本 + log /
prepare-reused-evidence 校验脚本 + log / verify-staging 复核脚本 +
log / assert-contracts 断言脚本 + log / secret-scan 扫描脚本 + log /
focused-tests / release-check 双流 + exit 标记 / cockpit 双流 +
`raw/*.stderr.txt` 两份空捕获（SHA256 `e3b0c442…` = 空文件标准值）+
`SHA256SUMS` 全工件索引（29 文件），自不含自哈希；worktree 侧
`artifacts/m14-185-isolated/`、`artifacts/m14-185-cockpit-staging/`、
`artifacts/m14-185-cockpit-report.json` 为生成现场，gitignored，
不入库。）

## 6. 诚实边界与剩余门（不伪称，逐项可执行收口）

1. **cockpit_ready=true ≠ 可发布**：技术面 9 门全 pass、无 blocker，
   但 `release-approval` 是 required 门且 human-only——发布窗口/回滚/
   观察期人工审批从未发生，cockpit 永不接受它，本切片不触碰、不代拟、
   绝不合成；**`release_ready=false` / `production_ready=false`
   不变**，放行决定留 supervisor。真实审批必须由审批人从零组装并精确
   绑定本切片各门哈希。
2. **turn-tls 仍 optional 未 stage**：公网语音发布形态才必需，本机/
   LAN 范围不阻断；`release_ready` 不得解释为公网语音就绪。
3. **provider-smoke pass 是 M14-148 恢复轮的时点证据且年龄显著**
   （2026-09-26T01:12:09Z→01:14:14Z，距本切片聚合约 **3 天 14 小时**
   ——M14-149/152 复用时仅 4 小时 20 分）：证明该时点三 provider 冒烟
   通过，不承诺窗口外状态；复用有效性依赖 §2.3 三支柱论证（app 树零
   变更 + 区间无 provider 生命周期变更 + M14-179 web-only 重建零
   provider 接触），该论证覆盖可见合并记录与生产变更记录，但区间
   前段（PR #240–#257 部分）提交主题未逐条展开复核。若 supervisor
   需要新鲜 provider 证据，应下达独立 provider-smoke 重跑切片（本
   切片任务边界明确禁止）。本切片零冒烟执行、零 provider/生产接触。
4. **long-soak pass 是 M14-106 运维轮的时点证据且窗口早于生产切换**：
   窗口 2026-09-22→23（m14-70 栈）97 样本全 ok；切换后的 24h soak
   窗口尚不存在，按任务边界不重跑、**不制造新窗口**，是否需要由
   supervisor 决策。
5. **production-state 门是「呈现」不是「重执行」**：staged 的五门 +
   anchor companion 均为各切片 canonical 时点快照（M14-83 @ 5829ad9、
   M14-85 @ f47a1e4、M14-87），本切片零生产触碰约束下不重推导、不
   搬运冒充新执行；其对当前生产状态的语义效力由 M14-117 切换后
   preflight（5/5 pass）与 supervisor 结合生产变更记录判断。
6. **生产当前运行栈未经本切片任何变更**：区间内唯一生产操作为
   M14-179 web-only 容器重建（2026-09-29，provider 面零接触），本切片
   零调度面触碰、不构成任何部署、不授权任何部署；M14-124 登记的审批
   前镜像与回滚锚材料保持有效，等待 hash-bound human-only 审批绑定。
7. **代码绑定门证据只对其执行时点的树成立**：ci-main/release-check
   现对 8d52f00 current（= 本切片基点 = current main）；main 再前移
   即再 stale——**只有下一个非 docs-only 合并才触发下一轮刷新**
   （no-infinite-refresh 边界，见开头声明），docs-only 入库不触发。
8. **GitHub Actions run 有平台保留期**（默认 90 天）；到期后按 §2.1
   命令重取即可。
