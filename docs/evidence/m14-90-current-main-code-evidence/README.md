# M14-90：current-main 代码绑定门证据刷新（实现/文档切片，受控刷新 r2）

- 切片：分支 `ops/m14-90-current-main-code-evidence`（独立 worktree
  `m14-90-current-main-code-evidence`，基于 main `7cca73fa5b3ac8ec18846c39870657480ee1f455`
  （PR #176 merge，精确基点，非 origin/main 快照推断）），单次本地 commit
  （不推送、不建 PR）。
- **受控刷新说明（r2）**：本切片第一轮（r1）曾在 f2a21a6（PR #175 merge）
  上完成同样的两门刷新（run 35639993213 + 隔离 release-check）；r1 交付
  审查判定其为有效但 stale-based 的工作——r1 判定后本切片经受控 rebase
  前移到 7cca73f 并**全部重新真实执行**（新 CI run 事实、从零隔离环境、
  全新 release-check 运行、重新聚合 readiness），不复制任何 r1 产物。
  r1 的 f2a21a6-derived canonical 五文件整体移出 canonical，归档于
  `.verify/artifacts/m14-90-f2a21a6-superseded-audit/`（gitignored，仅供
  审计追溯）；本 README 与 canonical 只反映 7cca73f 树与本轮真实运行。
- 目标：沿用 M14-86/M14-81 证据契约/流程，对**新 main 基点 7cca73f** 只
  刷新**代码绑定**发布门（ci-main / release-check）并重新聚合 readiness；
  绝不手改 gate JSON、绝不合成结果、绝不搬运旧生产状态证据冒充 current、
  绝不伪称 production readiness（`release_ready=false` /
  `production_ready=false` 全程不变）。
- 零生产触碰（本切片全程，含 r1/r2 两轮）：零容器启停/重建、零计划任务
  操作、零 DB/MinIO/语音引擎接触、零生产日志写入、零 secrets/env 读取、
  零 soak 历史/锚定读写、零发布审批接触、零部署。唯一网络访问是 GitHub
  只读 API（`gh api`，已认证账号；r2 fetch origin 经一次性
  `socks5h://127.0.0.1:10808` 代理参数，未改全局 git 配置）+ worktree
  从零环境的包安装（uv/npm，r2 全部重建）。

## 1. 基点事实（全部 git/远端可复核）

| 事件 | commit | 说明 |
|------|--------|------|
| PR #173 merge（M14-86 证据切片，M14-86 刷新的基点） | `ddcaa22`（含 `1222ec4`） | M14-86 canonical 所锚时点 |
| PR #174 merge（M14-84 harmony 模拟器真实 API 冒烟） | `5ff1e68`（含 `2dc8558`） | harmony 面（含运行时改动） |
| PR #175 merge（M14-87 audit-chain-anchor current gate 闭合） | `f2a21a6`（含 `44767c4`） | r1 刷新的基点（已被本轮 superseded） |
| PR #176 merge（M14-88 本地 provider-smoke 三件套恢复）→ **当前 main** | `7cca73fa5b3ac8ec18846c39870657480ee1f455`（含 `e51e376`） | merged_at 2026-09-21T19:01:35Z（本地 2026-09-22 03:01:35 +0800） |
| main push CI run 35642190403 创建（push 事件） | 7cca73f | 2026-09-21T19:01:37Z（UTC） |

- `git fetch origin`（经一次性代理）复核：origin/main == `7cca73f`（无更新
  提交；M14-89 及更晚在 r2 提交时点无 PR、未合入——如实，不声称不预判）。
- `git diff --stat f1dfcbb..7cca73f`：8 commits、10 文件、+2678/−1——PR #173
  M14-86 证据 + PR #174 M14-84 harmony 冒烟（**真实运行时改动**：
  `tools/harmony_release/backend_smoke.py` +1142、
  `tests/harmony_release/test_backend_smoke.py` +536（归 CI Release tools
  job 单独执行）、`apps/harmony/.../AiosApi.ets` 4 行）+ PR #175 M14-87
  audit-chain 证据 + PR #176 M14-88 provider-smoke 恢复证据（后者 4 文件
  +276 纯 docs，f2a21a6→7cca73f 增量即它）。**services/api 与 apps/web
  零改动**——release-check api-test 计数与 M14-86 持平属预期对账；但
  代码绑定门证据只对其执行时点的树成立——不重新真实执行就不对 7cca73f
  成立，运行时面有改动更须真实重推导。
- M14-86 canonical（f1dfcbb 时点）与 r1 的 f2a21a6 时点工作自此对 7cca73f
  **stale**——均保留为历史记录（r1 五文件已移入 superseded 审计目录），
  不改写。M14-83（@ 5829ad9 生产只读门）、M14-85（@ f47a1e4
  backup-restore 门）、M14-87（audit-chain-anchor 门闭合）、M14-88
  （provider-smoke 恢复，@ 7cca73f 前序）canonical 保留为其各自时点记录，
  本切片不搬运、不改写。
- **M14-88 已在基点内**：其恢复证据（search/local-voice/llm 三件套真实
  冒烟 + provider-smoke.json）绑定其执行时点的生产/provider 面，属生产
  状态绑定门——M14-90 readiness 聚合不搬运它冒充 current（见 §2.3/§5）。

## 2. 刷新执行记录（命令与结果，全部真实可复跑；本轮全部为 r2 新执行）

### 2.1 ci-main（真实远端 CI，非本地重跑）

```bash
gh api "repos/swsgbl/ai-learning-os/actions/runs?head_sha=7cca73fa5b3ac8ec18846c39870657480ee1f455&per_page=20"
gh api "repos/swsgbl/ai-learning-os/actions/runs/35642190403/jobs?per_page=30"
```

- 命中断言（程序化断言脚本复核 raw 响应，非仅依赖任务简报；任一断言
  失败即非零退出、不产出证据）：`workflow_runs` 中 event=push、
  head_branch=main 恰 1 条（该 SHA 全部 run 也仅此 1 条）——run
  **35642190403**，workflow `.github/workflows/ci.yml`，head_sha
  `7cca73fa…`，status=completed，**conclusion=success**（created
  2026-09-21T19:01:37Z / updated 19:05:45Z，
  https://github.com/swsgbl/ai-learning-os/actions/runs/35642190403）。
- jobs 断言：`total_count=5` 且全部 conclusion=success——API
  （ruff/pytest/migration）、Docker（compose build+healthy+smoke）、
  Release tools（Python tests）、Android（unit test/lint/assemble）、
  Web（test/typecheck/lint/build）。与 `.github/workflows/ci.yml` push
  触发面一致（Release tools job 含 M14-84 新增的
  `pytest tests/harmony_release`，真实绿）。
- 按 `_eval_ci_main` 契约**程序化派生** `evidence/ci-main.json`（断言
  驱动脚本 `derive_ci_main.py` 从 raw 事实拼装 gate/run_id/merge_commit/
  conclusion 白名单字段 + source 溯源串；断言不过即失败，无手写 pass；
  脚本 ruff 检查通过）；**未改任何 M14-86/M14-83/M14-85/M14-87/M14-88
  文件**，未复用 r1 的 run 35639993213 或其 raw 文件。
- raw API 响应原样归档：`raw/gh-runs-7cca73f.json`、
  `raw/gh-jobs-35642190403.json`。

### 2.2 release-check（隔离本地 full 重跑，7cca73f 代码 = rebase 后树）

r2 为全新运行，不复用 r1 任何产物：`.venv` 与 `node_modules` 先删除后
从零重建（直连可用，未用代理）：

```bash
uv venv .venv --python 3.12          # CPython 3.12.14
uv pip install -r services/api/requirements.txt -r services/api/requirements-dev.txt
npm ci --no-audit --no-fund          # 411 packages（node v22.23.2）
```

（npm 提示 `unrs-resolver` postinstall 被 allowScripts 策略阻止——
如实记录；该脚本产物非门禁依赖，后续 10 门全绿证实无影响。）

隔离编排器（一次性 SQLite → alembic upgrade head → 127.0.0.1 临时 uvicorn →
/health 就绪 → full 10 门 → 原子落盘 → finally 关停临时 API；环境剥离
AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL，显式 APP_ENV=development /
VOICE_MODE=local——不连接任何生产面；worktree 绝对路径调用；新工作区
`artifacts/m14-90-isolated-r2/`，与 r1 的 r1 工作区并存供审计）：

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli release-check-isolated \
  --workdir "<worktree>/artifacts/m14-90-isolated-r2" --json
# exit 0
```

- 结果：**all_green=true，10/10 pass**（generated_at
  2026-09-21T19:19:47Z，execution_scope=full）——api-lint（ruff）/ web-lint /
  web-typecheck / web-build / api-test（**pytest 4024 passed, 33 skipped,
  1 warning in 243.25s**；与 M14-86 @ f1dfcbb 及 r1 @ f2a21a6 的 4024/33
  完全一致——f1dfcbb..7cca73f 对 services/api 零改动、api 测试集零变更，
  计数持平属预期对账；M14-84 新增的 tests/harmony_release 归 CI Release
  tools job 管辖，不入 api 套件）/ migration（current == head ==
  0027_audit_chain）/ backup（aios-backup-v1 tables:30 files:1）/ voice
  （local 合成 audio/wav 17324 bytes）/ license（api 15/web ok/models 7/
  sources 6）/ e2e（walkthrough 5 步 1198 ms）。
- 工作区产物（gitignored）：`artifacts/m14-90-isolated-r2/`（一次性
  release-check.sqlite + uvicorn.log + release-check-isolated.json；临时
  API 127.0.0.1 回环已 terminated 收尾）。
- `release-check-isolated.json` 逐字节复制改名为 canonical
  `evidence/release-check.json`（程序化比对 byte-identical=True，sha256
  复核见 §4 表），文件自带 gate 自标识 `release-check`，非手改。

### 2.3 release-readiness 聚合（M14-90 canonical 证据目录）

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli release-readiness \
  --evidence-dir "<worktree>/.verify/artifacts/m14-90-current-main-code-evidence/evidence" \
  --json --output "<worktree>/artifacts/m14-90-readiness-r2/release-readiness.json"
# exit 1（release_ready=false，如实）
```

- manifest（generated_at 2026-09-21T19:20:53Z，evidence_dir 指向 M14-90
  canonical 目录）：**pass=2**（ci-main：run 35642190403 @ 7cca73f
  conclusion=success；release-check：all_green 10/10）＋ **missing=9**
  （production-preflight / backup-restore / audit-chain-anchor /
  legacy-papers / draft-ownership / long-soak / provider-smoke /
  release-approval 为 required，turn-tls 为 optional）；malformed=0、
  tampered=0；manifest 内嵌 ci-main/release-check 证据 sha256 与实际
  canonical 文件哈希程序化交叉核验一致；**release_ready=false，
  exit_code=1**。
- 设计口径（与 M14-81/M14-85/M14-86/r1 相同）：M14-90 canonical 证据目录
  **只放本切片真实重推导的两个代码绑定门**——生产状态绑定门不搬运
  m14-75/m14-81/M14-83/M14-85/M14-86/M14-87/**M14-88** 旧文件冒充
  current（搬运会让绑定各自时点树/生产状态的快照在聚合里显示 pass，
  即过度宣称）。各生产门最新可用记录仍见：M14-83 canonical（@ 5829ad9，
  production-preflight 5/5、治理两门、audit-chain valid）、M14-85
  canonical（@ f47a1e4，backup-restore verified=true）、M14-87 canonical
  （audit-chain-anchor 门闭合：链 valid + 锚 up-to-date + WORM 双副本
  verified）、**M14-88 canonical（provider-smoke 三件套恢复——search
  results=5、ASR 真实转写、TTS WAV 226604 bytes、llm judge 通过；注意其
  诚实边界：宿主 sing-box/ollama serve 为易失性用户进程，重启后需再启
  动）**。

## 3. 聚焦验证与静态检查（真实执行结果，r2 新 venv）

- 聚焦契约测试（本切片证据链所依赖的工具面）：

```bash
cd services/api && "<worktree>/.venv/Scripts/python.exe" -m pytest -q \
  tests/test_release_readiness.py tests/test_release_check_isolated.py tests/test_release_checklist.py
# 137 passed, 1 warning in 4.33s（与 M14-86/r1 一致——三个测试文件零改动）
```

- ruff：本切片唯一触碰的 Python 工具（gitignored 断言派生脚本
  `derive_ci_main.py`）`ruff check` 通过（tracked Python 零改动）。
- canonical 五份 JSON `json.load` 解析通过；release-readiness 消费即契约
  校验（malformed=0/tampered=0）；canonical `release-check.json` 与 r2
  隔离运行原始产物**逐字节一致**（程序化比对 True）；canonical
  `release-readiness.json` 与 `artifacts/m14-90-readiness-r2/` 落盘
  manifest **逐字节一致**（程序化比对 True）。
- `git diff --check` 干净（docs-only）；无冲突标记；分支卫生：单 commit 后
  tracked-clean（worktree 侧运行日志与 `artifacts/` 产物均 gitignored/
  未入库）；未 push、未建 PR。

## 4. canonical 证据清单（主仓 gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：`.verify/artifacts/m14-90-current-main-code-evidence/`（r2 全量，
不含任何 r1 文件）

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/ci-main.json` | 1134 | `476d2b50bda3b53169df1a5744f03285c82ddcba7956a6b0095c10a4822b597b` |
| `evidence/release-check.json` | 2491 | `acd521d709ee5b35f11f4c99c3426ca11de2fb025ad354fb6cf3c4a2d978ed41` |
| `evidence/release-readiness.json` | 6888 | `8e4b7d15eccf9ceddf93e5f2a3fa41ed009fc1246865c9cfb82b789bd6af63d0` |
| `raw/gh-jobs-35642190403.json` | 12334 | `144b1505fab5e07eaa01b7c326fc0ba85489210916c8207c49830d27e5237991` |
| `raw/gh-runs-7cca73f.json` | 12206 | `dbbfac6a926000d3efe0e18fb00c1b780830e232d55c6b9b76c23193e0f82993` |

（worktree 侧 `artifacts/m14-90-isolated-r2/` 与
`artifacts/m14-90-readiness-r2/` 为 r2 生成现场，gitignored；canonical
三件为逐字节复制，哈希逐一程序化复核。r1 的 f2a21a6-derived 五文件与
r1 生成现场 `artifacts/m14-90-isolated-r1/`、`artifacts/m14-90-readiness/`
整体保留于 superseded 审计位置：
`.verify/artifacts/m14-90-f2a21a6-superseded-audit/` 与 `artifacts/`
原位——不入 canonical、不入 git，仅供审计追溯。）

## 5. 诚实边界与剩余阻塞（不伪称，逐项可执行收口）

1. **八个 required 生产状态门 + optional turn-tls 全部 missing（如实）**：
   production-preflight / backup-restore / audit-chain-anchor / legacy-papers
   / draft-ownership / provider-smoke / release-approval——重推导需要生产
   DB/MinIO/语音 live 面访问与运维窗口，本切片零生产触碰约束下全部如实
   missing，不搬运 m14-75/m14-81/M14-83/M14-85/M14-86/M14-87/M14-88 旧
   证据冒充 current；turn-tls 仅公网语音发布形态必需。各门最新真实记录
   见 §2.3 所列各切片 canonical 目录（**provider-smoke 最新恢复记录 =
   M14-88 canonical（已在基点 7cca73f 内）**，但属生产状态绑定证据，
   本切片不搬运；audit-chain-anchor 门最新闭合记录 = M14-87 canonical）。
2. **long-soak 仍 missing（权威锚已存在，24h 审计未发生）**：M14-79 权威
   锚定记录（canonical
   `.verify/artifacts/m14-79-soak-window-anchor/soak-window-anchor.json`）
   锚点 = 2026-09-21T15:00:01Z、最早审计时点 = 2026-09-22T15:00:01Z；
   本切片 r2 readiness 生成于 2026-09-21T19:20:53Z，距最早审计时点尚差
   约 19.5 小时，24h 窗口结局**未判定**，long-soak 门如实 missing，不
   预宣称。锚定 ≠ long-soak 通过、不授权任何 readiness。
3. **release-approval 从未发生**：人工审批 human-only，本切片明确不触碰、
   不代拟。
4. **provider-smoke 恢复的易失性（M14-88 如实记录）**：恢复依赖宿主
   sing-box 与 `ollama serve` 用户进程，重启后需再启动（恢复手册在
   M14-88 证据 README §7）；发布窗口前需按当时拓扑真实重推导，不以
   M14-88 时点快照充当永久 current。
5. **生产仍运行 m14-70 镜像**（2026-09-19 切换后未再变更），本切片零
   调度面触碰、不构成任何部署；`release_ready=false`、
   `production_ready=false` 不变，本切片不授权任何部署。
6. **M14-89 及更晚不在本切片范围内**：r2 提交时点无 PR、未合入——本
   切片不声称、不预判其结论；下一个代码绑定门刷新切片应基于届时的
   current main 重取。
7. GitHub Actions run 有平台保留期（默认 90 天）；到期后按 §2.1 命令
   重取即可。
