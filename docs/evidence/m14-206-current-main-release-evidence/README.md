# M14-206：current-main 发布证据刷新（PR #294 / M14-205 后；ci-main + release-check 真实重跑 + provider-smoke/long-soak 只读复用 + cockpit 零 blocker 聚合）

## 0. 交付与边界

- 切片：worktree `ai-learning-os-worktrees/m14-206-current-main-release-evidence`，
  分支 `docs/m14-206-current-main-release-evidence`，基于 main
  `e14d3f082dbbfd6a7f816439e20fcbbd92f0ac4f`（PR #294 merge = M14-205
  Cloudflare ingress 只读 preflight 工具合入，任务执行前 tracked-clean
  核验通过：branch/HEAD/porcelain 三项留痕 `logs/pre-head.log`）。交付为
  **单 local commit**；按任务边界 **不 push、不开 PR、不合并、零生产
  触碰**（本切片不是部署、不是发布审批、不是生产变更）。
- **刷新动因与定性（为什么 M14-205 之后触发）**：M14-190 的代码绑定门
  证据绑定 `ffd74cf`（M14-193 生产滚动证据切片记录的 supervisor 生产
  滚动之后的最近一次刷新），其后 `ffd74cf..e14d3f0` 为 **13 commits**
  （PR #291/#292/#293/#294 链），包含 **release-tooling 代码与测试面
  真实变更**：M14-205 `tools/ops/cloudflare_ingress_preflight.py`（+830）
  + `tests/ops/test_cloudflare_ingress_preflight.py`（+707）与 M14-196
  Harmony auth smoke 工具/测试。自 M14-185 证据基点 `8d52f00` 起算，
  全区间 `8d52f00..e14d3f0` = **43 commits、58 文件 +10497/−232**。按
  既有契约（M14-185 §0 / M14-190 §0），release-tools job 是 ci-main
  五 job 契约之一、`tests/ops` 计入 release-check 工具面，代码绑定门
  （ci-main / release-check）相对当前 main 漂移**非 docs-only**，必须在
  当前 HEAD `e14d3f0` 真实重执行两门；**绝不复用/搬运 M14-185 的
  run 36570439902 或 M14-190 的 run 36655040106 及其 release-check
  产物**。
- **区间不变量（如实、不夸大）**：`services/api/app/` 全树、
  `services/api/requirements*.txt`、`services/api/alembic/` 在
  `8d52f00..e14d3f0` 区间 **零变更**（name-only diff 为空）——**本切片
  不声称运行时生产代码（API 应用树）发生变化**；区间变更集中于
  release-tooling（tools/ops 七文件，含 M14-205 preflight + M14-186
  phase0 决策工具 + M14-194 monitor basePath 对齐）、`services/api/tests/`
  三文件（+136/−7，净增 19 个测试——§3 对账）、web 面 16 文件（M14-188/
  189 lint 修复 + base-path/gsap/sw）、Harmony 工具与测试、docs 证据。
  alembic head 仍 `0027_audit_chain`（区间无新迁移）。
- **no-infinite-refresh 边界（沿 M14-185/190 声明）**：代码绑定门证据
  只对其执行时点的树内容成立；本切片基点 `e14d3f0` 即刷新时点的
  current main。**只有下一个含非 docs 变更的合并才触发下一轮刷新**；
  docs-only 入库不构成刷新触发，本切片不递归追新，也不声称对
  `e14d3f0` 之后的树有效。
- provider-smoke / long-soak 按任务边界 **零重跑、零生产接触、零
  provider 生命周期接触**，只读核验出处与精确哈希后逐字节复用（§4）；
  **`release_ready=false` / `production_ready=false` 全程不变**——
  release-approval 是 human-only 门、从未发生，cockpit 按策略永不接受
  它（missing 呈现、不计 blocker）；turn-tls optional 不阻断。
- 零生产触碰（本切片全程）：零容器启停/重建、零 DB/MinIO/语音引擎
  接触、零生产日志写入、零 secrets/env 读取、零 soak 历史重跑、零
  发布审批接触、零部署、零 Cloudflare/DNS/VPS/SSH/隧道/公网入口操作、
  零 Android/USB/模拟器/Harmony 设备操作。唯一网络访问是 GitHub 只读
  API（`gh api` 双查询，零代理配置变更，stderr 捕获为空）+ worktree
  从零环境的包安装（uv/npm，日志归档 §7）。
- 零秘密政策：本 README 与全部 canonical 工件不含任何 token/key/
  password/带凭据 URL/env 值（§6 五类模式扫描 31 文件 0 命中；canonical
  内的 Windows 绝对路径为 gitignored 本地工件路径，非秘密，如实计数
  登记）。
- 本 README 为唯一入库证据文件；canonical 原始证据（gitignored）位于
  worktree `.verify/artifacts/m14-206-current-main-release-evidence/`
  （`SHA256SUMS` 索引全工件，自不含自哈希；§7）。

## 1. 基点事实（git/远端可复核）

| 事件 | commit | 说明 |
|------|--------|------|
| M14-185 刷新基点 | `8d52f009420df944f58f212bf6294e8977557ccd` | PR #275 merge；ci-main run 36570439902 / release-check 5631/36 绑定于此 |
| M14-190 刷新基点（其后） | `ffd74cf84d211912f8605abe20e533ccfabf2468` | PR #279 merge；ci-main run 36655040106 / release-check 5631/36 绑定于此 |
| PR #276–#294 合并序列 | `8d52f00..e14d3f0` = 43 commits | m14-186 至 m14-205 各切片（含区间内 M14-190 自身） |
| PR #294 merge（M14-205 工具合入）→ **当前 main** | `e14d3f082dbbfd6a7f816439e20fcbbd92f0ac4f` | 本切片精确基点；tree `7833c47e…` |
| main push CI run 36814009797 创建（push 事件） | e14d3f0 | 2026-10-01T04:12:15Z（UTC） |

- 区间文件分类（58 文件）：docs/evidence 21 个 README + CHANGELOG/
  PROJECT_STATUS（docs-only 主体）；tools/ops 5 文件 + tests/ops 2 文件
  （release-tooling——本刷新的直接动因）；`services/api/tests/` 3 文件
  +136/−7（测试面，非运行时）；apps/web 16 文件（lint 修复与运行时
  组件）；Harmony 工具/测试/fixture 9 文件。`services/api/app/` 与
  requirements/alembic **零变更**（见 §0 不变量）。
- M14-185/M14-190 canonical 保留为其时点历史记录不改写（本切片
  long-soak 复用即逐字节读 M14-190 canonical §4，零修改）；本切片不
  搬运其 ci-main/release-check 产物。M14-83（@ 5829ad9 生产只读门三门）、
  M14-85（@ f47a1e4 backup-restore）、M14-87（@ audit-chain-anchor 门 +
  伴生锚）canonical 按 M14-91 §5 登记路径**原样 staging**（§5，staging
  前六源完整 sha256 复核 6/6 MATCH）。

## 2. ci-main（真实远端 CI，非本地重跑）

```bash
gh api "repos/swsgbl/ai-learning-os/actions/runs?head_sha=e14d3f082dbbfd6a7f816439e20fcbbd92f0ac4f"
gh api "repos/swsgbl/ai-learning-os/actions/runs/36814009797/jobs"
```

（双查询直连成功，未改任何代理配置；两份 stderr 捕获为空文件，0 bytes。
任务简报给定的 run 36814009797 / 5/5 success 经 raw 响应 live 复核后才
绑定——断言脚本 `logs/derive_ci_main.py` 复核归档 raw 响应而非仅信任务
简报，任一断言失败即非零退出、不产出 canonical。）

- 命中断言（11 项全过）：`workflow_runs` total_count=1、event=push、
  head_branch=main、head_sha 精确 `e14d3f0…` 恰 1 条——run
  **36814009797**（run_number **725**），status=completed，
  **conclusion=success**（created 2026-10-01T04:12:15Z / updated
  2026-10-01T04:15:54Z，workflow `.github/workflows/ci.yml`，
  https://github.com/swsgbl/ai-learning-os/actions/runs/36814009797）。
- jobs 断言：`total_count=5` 且全部 conclusion=success、head_sha 全部
  匹配、job 名集合精确且无重复——API（ruff/pytest/migration）、
  Android（unit test/lint/assemble）、Docker（compose build+healthy+
  smoke）、Release tools（Python tests）、Web（test/typecheck/lint/
  build），与 M14-116/122/123/125/146/149/152/185/190 同一五 job 契约。
- 按 `_eval_ci_main` 契约**程序化派生** canonical `evidence/ci-main.json`
  （1726 bytes、SHA256 `8679c34b…`）：断言驱动脚本从 raw 事实拼装 +
  溯源 source 串（明示区间 43 commits 的变更定性与对 M14-185/190 派生
  的取代关系）；未复用 M14-185 run 36570439902 或 M14-190 run
  36655040106 或其 raw 文件。
- raw API 响应原样归档：`raw/gh-runs-e14d3f0.json`（12221 bytes）、
  `raw/gh-jobs-36814009797.json`（13027 bytes；哈希见 §7）。

## 3. release-check（隔离本地 full 重跑，干净 e14d3f0 执行树）

从零构建全新隔离环境（**本切片重建** worktree `services/api/.venv`：
`uv venv --python 3.12`（CPython 3.12.14）+ `uv pip install -r
requirements.txt -r requirements-dev.txt`；根目录 `npm ci --no-audit
--no-fund` 411 packages，node v22.23.2 / npm 12.0.2——npm 提示
`unrs-resolver` postinstall 被 allowScripts 策略阻止，与 M14-92 起各轮
相同的已知提示，本轮 10 门全绿证实无影响；日志 `logs/env-setup.log`、
`logs/npm-ci.log`）。

**执行树前置核验**（declared-head 诚实性前提）：运行前
`git rev-parse HEAD` == `e14d3f0…` 且 `git status --porcelain` 为空
（tracked-clean；留痕 `logs/pre-release-check-head.log`）——release-check
的实际执行树即精确干净 e14d3f0，后续 cockpit
`--gate-declared-head release-check=e14d3f0` 声明成立。

隔离编排器（一次性 SQLite → alembic upgrade head → 127.0.0.1 临时
uvicorn → /health 200 → full 10 门 → 原子落盘 → finally 关停临时 API；
环境剥离 AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL，显式
APP_ENV=development / VOICE_MODE=local——不连接任何生产面；新工作区
`artifacts/m14-206-isolated/`）：

```bash
cd services/api
"<worktree>/services/api/.venv/Scripts/python.exe" -m app.ops.cli release-check-isolated \
  --workdir "<worktree>/artifacts/m14-206-isolated" --json
# exit 0（全量双流归档 logs/release-check-stdout.log / -stderr.log / -exit.txt）
```

- 结果：**all_green=true，10/10 pass**（generated_at
  2026-10-01T04:41:04.559743+00:00，execution_scope=full）——api-lint
  （ruff All checks passed）/ web-lint / web-typecheck / web-build /
  **api-test（pytest 5650 passed, 36 skipped, 1 warning in 296.11s）** /
  migration（current == head == 0027_audit_chain）/ backup
  （aios-backup-v1 tables:30 files:1）/ voice（local 合成 audio/wav
  17324 bytes）/ license（api deps 15 / web ok / models 7 / sources 6）/
  e2e（walkthrough 5 步，1130 ms）。
- **pytest 计数对账（较 M14-185/190 的 5631/36 恰 +19/+0，实测吻合、
  非断言预期）**：区间 `services/api/tests/` 三文件净增 19 个 collected
  测试 = 新增 7 个 test def（test_production_monitor.py 4 +
  test_m14_27_voice_health_cutover.py 2 + test_monitoring_pipeline.py 1，
  逐文件 diff 计数）+ 1 个新 def 的 `@parametrize("base_path", …)` 列表
  10 参数展开（10−1=9 额外）+ 既有 monitor argv parametrize 列表追加
  3 参数（M14-194 basePath 拒绝形态），7+9+3 = 19 = 5650−5631 精确
  闭合；三文件均在 M14-194（monitor basePath 对齐）与 M14-193（voice
  health cutover 测试）切片合入，与提交链一致。
- `release-check-isolated.json` 逐字节复制改名为 canonical
  `evidence/release-check.json`（2488 bytes，SHA256 `3f60fbee…`；文件
  自带 gate 自标识 `release-check`，非手改；复制后 filecmp 逐字节比对
  IDENTICAL，并由契约断言复核）。

## 4. 复用证据（零重跑、零生产接触；只读核验 + 哈希锁定逐字节复用）

### 4.1 provider-smoke（新真实聚合定位 + 原始输入在场核验）

- **定位**：磁盘唯一真实聚合目录 `artifacts/temp/provider-smoke/
  20260926T0106/`（主仓；无更新目录）——聚合 `provider-smoke.json`
  564 bytes、SHA256 `d589181e078de875faac11052e148d5ff32b0b6f4d7b41fb716b
  4a584c60a761`，与 M14-185/M14-149/M14-152/M14-190 登记哈希**逐字节
  精确一致**。该聚合是 M14-148 恢复轮真实重跑产物；M14-190 当时报告
  原始输入目录缺失而采用防御性输入链，**本轮该目录在场（7 文件），
  本切片直接逐份核验原始输入**——比 M14-190 的链路更强，不沿用其
  "输入缺失"叙述。
- **只读核验（prepare 脚本，任一失败即非零退出不登记；合计 40 项
  provider-smoke 相关全过）**：聚合九键 schema
  `provider-smoke-evidence-v1`、gate 自声明 `provider-smoke`、拓扑
  `voice_mode=local`、三槽位 executed=true 且 result=pass
  （evidence_step local-voice-smoke / search-smoke / llm-smoke）；三份
  pass 输入逐份核验（schema/step/executed/pass/exit_code=0/时间自洽
  started<completed≤聚合时刻）：search-smoke 7496ms、
  local-voice-smoke 18442ms、llm-smoke 12213ms；**三份失败 attempt
  如实保留未被改写**（仍 executed=true/result=fail/exit_code=1）：
  attempt1-envmiss 60ms、attempt2-querymiss 769ms、attempt3-timeout
  10483ms——真实执行痕迹，本切片零改写；M14-185/M14-190 canonical
  同哈希互证。
- **复用语义论证（区间零 provider 执行，三支柱如实更新）**：
  (a) 代码面——`services/api/app/` 全树（provider 运行时代码所在）
  区间 `8d52f00..e14d3f0` 零变更；(b) 合并面——区间 16 个可见 PR
  （#276–#294）集中于 phase0/phase1/phase3 调研 docs、web lint 修复、
  Harmony 发布链与 release-tooling，无 provider 生命周期变更；
  (c) 生产面——**区间内生产操作为 M14-193 记录的 supervisor 生产滚动
  （2026-09-30，API+Web 容器以既有镜像 `--no-build --no-deps`
  recreate，build base 50bd66a；Postgres/Redis/MinIO/LiveKit/语音
  sidecar 与其他编排依赖未被 recreate）**——provider 冒烟对象
  （FunASR/CosyVoice 语音 sidecar、SearXNG、Ollama）所在容器**均未被
  触碰**，且 app 树零变更使滚动镜像的 API 语义与基点一致；磁盘上无
  更新的 provider-smoke 聚合（20260926T0106 仍为最新真实执行）。
- 逐字节复制为 canonical `evidence/provider-smoke.json` 后重哈希
  **逐字节一致（564 bytes 同哈希）**。执行记录：
  `logs/prepare-reused-evidence.log`。
- **原始时间边界显式：2026-09-26T01:12:09Z→01:14:14Z**（M14-148
  恢复轮时点证据），距本切片复用时点（2026-10-01T04:46Z 聚合）**约
  5 天 3 小时 17 分**（M14-185 复用时 3 天 14 小时、M14-190 复用时
  4 天 4 小时，持续老化，如实披露），窗口外状态不承诺；且该窗口早于
  M14-193 生产滚动——滚动后未重跑 provider-smoke，是否需要新鲜证据
  由 supervisor 决策（本切片任务边界明确禁止重跑）。年龄如实披露，
  不伪称新鲜。

### 4.2 long-soak（不重跑、不触碰生产/计划任务；只读校验 + 逐字节复用）

- 复用口径与 M14-107/116/122/123/125/146/149/152/185/190 完全一致：
  源为 M14-190 canonical（其自身逐字节复用自 M14-185 链），817 bytes，
  SHA256 `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec`
  ——M14-185/M14-190 双源同哈希互证后才复制。
- 复制到本切片 canonical `evidence/long-soak.json` 后**重新哈希 + JSON
  断言**：逐字节一致（817 bytes 同哈希）；gate 自声明 `long-soak`、
  audit_schema_version=2、tool `tools/ops/soak_stability_audit.py`、
  策略四值恰 1440/15/20/500、classification=**pass**、reasons=[]。
- 窗口事实（README 如实转述，非本切片重跑）：**24h 窗口
  2026-09-22T02:00:01Z → 2026-09-23T02:00:01Z（跨度恰 1440.0 分钟），
  97 样本全 ok / 0 warn / 0 critical，max_observed_gap_minutes=17.25
  ≤ 20**。**诚实边界：该窗口早于 M14-117 生产切换、早于 M14-193 生产
  滚动，且执行于 m14-70 栈**——切换后尚无 24h soak 窗口；本切片按
  任务边界不重跑、**不制造新窗口**，是否需要新 soak 窗口由 supervisor
  决策。

### 4.3 production-state 六源（只读哈希复核，非重执行）

六个历史 production-state 源（M14-83 三门 / M14-85 backup-restore /
M14-87 audit-chain-anchor + 伴生锚）在 cockpit staging 前**完整
sha256 + 字节数复核**——与 M14-91/M14-116/…/M14-185/M14-190 登记值
逐一 **MATCH（6/6）**：audit-chain-anchor `a8c54c5e…`（1418B）/
audit-anchor companion `d2bfd877…`（354B）/ production-preflight
`b3a2be66…`（2103B）/ legacy-papers `83cd62bd…`（574B）/
draft-ownership `e90ae04c…`（578B）/ backup-restore `ed0fc5b4…`
（346B）（执行记录 `logs/prepare-reused-evidence.log`）。staged 语义
是"呈现"不是"重执行"（§8）。

## 5. evidence-cockpit 聚合（四门刷新/复用后重新汇总：cockpit_ready=true）

在 tracked docs 编辑**之前**、HEAD 仍为干净 `e14d3f0` 时运行。canonical
源路径逐一取自 M14-91 README §5 登记与 M14-185/M14-190 同一路径（不
猜测）；四门来自本切片 canonical、六源 + anchor companion 来自登记
路径（§4.3 已复核）：

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
  --gate-declared-head release-check=e14d3f082dbbfd6a7f816439e20fcbbd92f0ac4f \
  --current-head e14d3f082dbbfd6a7f816439e20fcbbd92f0ac4f \
  --staging-dir "<worktree>/artifacts/m14-206-cockpit-staging" \
  --json --output "<worktree>/artifacts/m14-206-cockpit-report.json"
# exit 0（cockpit_ready=true；双流归档 logs/cockpit-stdout/-stderr.log）
```

- **current-head 与 release-check declared head 显式声明
  `e14d3f082dbbfd6a7f816439e20fcbbd92f0ac4f`**；合法性由 §3 前置核验
  支撑（实际执行树即干净 e14d3f0）。
- 结果（报告归档 canonical `cockpit-report.json`，19002 bytes）：
  **evaluator pass=9 / pending=0 / blocked=0 / missing=2 / malformed=0
  / tampered=0**；**cockpit_ready=true、cockpit_blockers=[]、
  required_not_staged=[]、tool exit 0**。同时 **release_ready=false、
  production_ready=false（恒 false）**：not_pass_required 恰为
  `release-approval`（human-only 从未发生，cockpit 按策略永不接受、
  不计入 blocker——「无技术 blocker」与「不可放行」两件事同时成立），
  not_pass_optional 为 `turn-tls`（optional：本机/LAN 发布形态不需要
  公网 TURN，不计入 fail）。**9 个必需非审批门全部 pass**。
- **code-bound 两门刷新生效**：ci-main stale=**current**（内嵌
  merge_commit=e14d3f0 == current HEAD，declared_head_origin=
  embedded）、release-check stale=**current**（flag 声明 e14d3f0，
  origin=flag）。
- provider-smoke 以当前真实聚合（`d589181e…`）staged 即 pass；long-soak
  staged pass（真实 24h 稳定窗口，§4.2）；production-state 五门 +
  anchor companion 以历史 canonical snapshot 原样 staged、undeclared
  如实呈现不 block（M14-91 §1.1 第 5 条口径；production-preflight 门
  的取源说明与 M14-185/M14-190 相同：M14-117 切换后的 preflight JSON
  缺 `gate` 自声明不能直接 stage，契约 fail-closed，本切片不手改补
  字段——staged 源为 M14-83 canonical，其时点语义见 §8.5）。
- **staged 10 文件（9 门 + anchor companion）与 source 逐字节一致**：
  工具内建写后重读断言 + 外部独立复核（filecmp 语义逐字节比较）
  **10/10 IDENTICAL**（清单归档 canonical `staged-inventory.txt`，
  10 条全 IDENTICAL）。

## 6. 聚焦验证与静态检查（真实执行结果）

- 聚焦契约测试（cockpit/release/provider/approval-draft 九套件，本切片
  证据链所依赖的工具面）：

```bash
cd services/api && "<worktree>/services/api/.venv/Scripts/python.exe" -m pytest -q \
  tests/test_evidence_cockpit.py tests/test_release_readiness.py tests/test_release_closure_manifest.py \
  tests/test_release_check_isolated.py tests/test_release_checklist.py \
  tests/test_provider_smoke_evidence.py tests/test_provider_smoke_preflight.py tests/test_searxng_local_provider.py \
  tests/test_release_approval_draft.py \
  --basetemp "<仓库外专用预建父目录>/m14-206-current-main-release-evidence/focused"
# 444 passed, 1 warning in 6.32s（logs/focused-tests.log）
```

（计数与 M14-185/M14-190 的 444**恰相同**——九套件文件在
  `8d52f00..e14d3f0` 区间零变更（区间 services/api/tests 仅 3 文件
  变更且均不在九套件内），工具契约面实测未漂移，与 `services/api/app/`
  零变更的静态结论互证。**basetemp 必须位于仓库外**：本轮曾误用仓库
  内 `artifacts/` 下目录，7 项 output-path 守卫测试因 `git
  check-ignore` 对仓库内 artifacts 路径放行而失败——已定位根因并改用
  仓库外专用目录后 444 全过，如实留痕。）
- `ruff check services/api`：All checks passed（本切片 tracked Python
  零改动——入库文件仅 evidence README 与三个 ledger 条目，基线复核）；
  证据脚本（derive_ci_main / prepare_reused_evidence /
  write_sha256sums / assert_contracts / scan_hygiene）ruff
  All checks passed + `py_compile` OK（`logs/ruff-evidence-scripts.log`）。
- canonical 完整性：`SHA256SUMS` 全工件索引（POSIX 相对路径、LF 文本
  模式、自不含自哈希；最终以双跑法定稿——末次运行不改动任何已索引
  文件）；canonical JSON `json.load` 解析全通过；**契约断言脚本
  `logs/assert_contracts.py` 57 项全过**（raw runs push@main 唯一 /
  run 36814009797 / run_number 725 / 五 job 精确集合、canonical
  ci-main 与 raw 一致、release-check 逐字节 + 10/10 + pytest 5650/36
  + +19 对账自洽 + migration head + e2e 5 步、provider-smoke 同哈希
  + 原始时间边界 + 与磁盘最新真实聚合逐字节、long-soak 策略四值 +
  97 ok + max gap 17.25 + span 1440 + classification pass + 同哈希、
  cockpit 9 pass/2 missing + not_pass_required 恰 [release-approval] +
  not_pass_optional 恰 [turn-tls] + release_ready/production_ready
  false + 两 code-bound 门 current（embedded/flag）+ 六源 binding
  sha256 与登记一致 + staged 10 文件集合精确 + inventory 10 条全
  IDENTICAL + SHA256SUMS 核心条目复哈希一致 + 前置日志留痕）。
- 秘密扫描（`logs/scan_hygiene.py`）：canonical 全部 31 个文本工件对
  credential 赋值 / OpenAI 风格 key / 私钥块 / 带凭据 DB URL / URL
  userinfo 五类模式 + U+FFFD 扫描 **0 命中**（报告内的 Windows 绝对
  路径为 gitignored 本地工件路径，非秘密，如实计数登记）。
- `git diff --check` 干净；新增 tracked 行扫描见 §7 前"提交卫生"。
- 分支卫生：单 commit 后 tracked-clean（worktree 侧
  `.venv`/`node_modules`/`artifacts/`/`.verify/` 均未入库）。

## 7. canonical 证据清单（gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：worktree `.verify/artifacts/m14-206-current-main-release-evidence/`

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/ci-main.json` | 1726 | `8679c34bf8fba697f43dafccf8d9cb3ffbb5ffaf2d8e1670388bf6e105b741f7` |
| `evidence/release-check.json` | 2488 | `3f60fbee48b6885f98f20250b91c3aa3ce43cf7f162b8483da17527df07cc66d` |
| `evidence/provider-smoke.json` | 564 | `d589181e078de875faac11052e148d5ff32b0b6f4d7b41fb716b4a584c60a761` |
| `evidence/long-soak.json` | 817 | `d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec` |
| `raw/gh-runs-e14d3f0.json` | 12221 | `19c9f75aa32e290cf58351de32ec07eee25e1c34dee4322b1aa6fb39c841d829` |
| `raw/gh-jobs-36814009797.json` | 13027 | `c83383098ce70ec25685305a4afb6d0c4b16caa5968dbdaacca644d2157a62b0` |
| `cockpit-report.json` | 19002 | `26c97ead12a4997c41b31a077d7d6922ee4afa2c1bd38b571314c4ba203c1534` |
| `staged-inventory.txt` | 1201 | `a04fe5061dd2b47d79f533d44bcd711ac4527dd1a96363d53aaac01e2d7f845f` |

（另 `logs/` 下执行日志与派生/校验/断言/扫描脚本：pre-head /
env-setup / npm-ci / pre-release-check-head / release-check 双流 +
exit 标记 / cockpit 双流 / derive-ci-main / prepare-reused-evidence /
focused-tests / ruff-evidence-scripts / assert-contracts / scan-hygiene
/ write-sha256sums + `raw/*.stderr.txt` 两份空捕获（0 bytes）+
`SHA256SUMS` 全工件索引，自不含自哈希；worktree 侧
`artifacts/m14-206-isolated/`、`artifacts/m14-206-cockpit-staging/`、
`artifacts/m14-206-cockpit-report.json` 为生成现场，gitignored，
不入库。）

## 8. 诚实边界与剩余门（不伪称，逐项可执行收口）

1. **cockpit_ready=true ≠ 可发布**：技术面 9 门全 pass、无 blocker，
   但 `release-approval` 是 required 门且 human-only——发布窗口/回滚/
   观察期人工审批从未发生，cockpit 永不接受它，本切片不触碰、不代拟、
   绝不合成；**`release_ready=false` / `production_ready=false`
   不变**，放行决定留 supervisor。真实审批必须由审批人从零组装并精确
   绑定本切片各门哈希。
2. **turn-tls 仍 optional 未 stage**：公网语音发布形态才必需，本机/
   LAN 范围不阻断；本报告不得解释为公网语音就绪。
3. **provider-smoke pass 是 M14-148 恢复轮的时点证据且年龄显著**
   （2026-09-26T01:12:09Z→01:14:14Z，距本切片聚合约 **5 天 3 小时
   17 分**，逐轮持续老化；窗口亦早于 M14-193 生产滚动）：证明该时点
   三 provider 冒烟通过，不承诺窗口外状态；复用有效性依赖 §4.1 三
   支柱论证（app 树零变更 + 区间无 provider 生命周期变更 + M14-193
   滚动未触碰 provider 容器），该论证覆盖可见合并记录与生产变更
   记录。若 supervisor 需要新鲜 provider 证据（尤其滚动后），应下达
   独立 provider-smoke 重跑切片（本切片任务边界明确禁止）。本切片
   零冒烟执行、零 provider/生产接触。
4. **long-soak pass 是 M14-106 运维轮的时点证据且窗口早于生产切换与
   滚动**：窗口 2026-09-22→23（m14-70 栈）97 样本全 ok；切换后的
   24h soak 窗口尚不存在，按任务边界不重跑、**不制造新窗口**，是否
   需要由 supervisor 决策。
5. **production-state 门是「呈现」不是「重执行」**：staged 的五门 +
   anchor companion 均为各切片 canonical 时点快照（M14-83 @ 5829ad9、
   M14-85 @ f47a1e4、M14-87），本切片零生产触碰约束下不重推导、不
   搬运冒充新执行；其对**当前**生产状态的语义效力有限（早于 M14-117
   切换与 M14-193 滚动），由 supervisor 结合 M14-193 滚动证据与生产
   变更记录判断。
6. **生产当前运行栈未经本切片任何变更**：区间内生产操作为 M14-193
   supervisor 生产滚动（2026-09-30，API+Web 容器 recreate；其回滚
   锚与备份材料见该切片 README）；本切片零调度面触碰、不构成任何
   部署、不授权任何部署。
7. **代码绑定门证据只对其执行时点的树成立**：ci-main/release-check
   现对 e14d3f0 current（= 本切片基点 = current main）；main 再前移
   即再 stale——**只有下一个非 docs-only 合并才触发下一轮刷新**
   （no-infinite-refresh 边界，见 §0 声明），docs-only 入库不触发。
8. **GitHub Actions run 有平台保留期**（默认 90 天）；到期后按 §2
   命令重取即可。
