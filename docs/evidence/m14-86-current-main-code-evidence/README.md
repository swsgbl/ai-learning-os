# M14-86：current-main 代码绑定门证据刷新（实现/文档切片）

- 切片：分支 `ops/m14-86-current-main-code-evidence`（独立 worktree
  `m14-86-current-main-code-evidence`，基于 main `f1dfcbbbb2a64746aebd89d10ce38d4c65539e82`
  （PR #172 merge，精确基点，非 origin/main 快照推断）），单次本地 commit
  （不推送、不建 PR）。
- 目标：沿用 M14-81 证据契约/流程，对**新 main 基点 f1dfcbb** 只刷新**代码绑定**
  发布门（ci-main / release-check）并重新聚合 readiness；绝不手改 gate
  JSON、绝不合成结果、绝不搬运旧生产状态证据冒充 current、绝不伪称
  production readiness（`release_ready=false` / `production_ready=false`
  全程不变）。
- 零生产触碰（本切片全程）：零容器启停/重建、零计划任务操作、零 DB/MinIO/
  语音引擎接触、零生产日志写入、零 secrets/env 读取（未读取
  `infra/env.production-recovery`，未输出任何 token/key）、零 soak 历史/锚定
  读写、零发布审批接触、零部署。唯一网络访问是 GitHub 只读 API
  （`gh api`，已认证账号，本机直连，未用代理）+ worktree 从零环境的包安装
  （uv/npm）。

## 1. 基点事实（全部 git/远端可复核）

| 事件 | commit | 说明 |
|------|--------|------|
| PR #169 merge（M14-81 证据切片，M14-81 刷新的基点） | `e1f128b` | M14-81 canonical 所锚时点 |
| PR #170 merge（M14-82 harmony current-main 模拟器冒烟） | `5829ad9`（含 `2122077`） | harmony 面 |
| PR #171 merge（M14-83 生产只读证据刷新） | `f47a1e4`（含 `57a696b`） | 生产状态门只读重推导 |
| PR #172 merge（M14-85 backup-restore 发布门演练）→ **当前 main** | `f1dfcbbbb2a64746aebd89d10ce38d4c65539e82` | merged_at 2026-09-21T17:30:02Z（本地 2026-09-22 01:30:02 +0800） |
| main push CI run 35632399209 创建（push 事件） | f1dfcbb | 2026-09-21T17:30:05Z（UTC） |

- `git diff --stat e1f128b..f1dfcbb`：7 commits、7 文件、+1127/−0——
  全部为 docs（CHANGELOG/PROJECT_STATUS/ROADMAP 台账 + m14-81/m14-82/
  m14-83/m14-85 四份 evidence README）。**运行时服务面零改动**
  （services/api 与 apps/web 无任何 diff），零 Python/TS 源码/测试变更；
  但代码绑定门证据只对其执行时点的树成立——不重新真实执行就不对
  f1dfcbb 成立，这正是本切片如实刷新的边界（与 M14-81 对 b7db88c→e1f128b
  增量的口径一致：docs-only 增量同样要求真实重推导，不以"仅文档"为由跳过）。
- M14-81 canonical 证据（e1f128b 时点）自此对 f1dfcbb **stale**（ci-main
  锚 e1f128b/run 35589879598；release-check 为 e1f128b 树 2026-09-21T10:58:48Z
  隔离运行）——保留为历史记录，不改写。M14-83（@ 5829ad9 生产只读门）与
  M14-85（@ f47a1e4 backup-restore 门）canonical 保留为其各自时点记录，
  本切片不搬运、不改写。

## 2. 刷新执行记录（命令与结果，全部真实可复跑）

### 2.1 ci-main（真实远端 CI，非本地重跑）

```bash
gh api "repos/swsgbl/ai-learning-os/actions/runs?head_sha=f1dfcbbbb2a64746aebd89d10ce38d4c65539e82&per_page=20"
gh api "repos/swsgbl/ai-learning-os/actions/runs/35632399209/jobs?per_page=30"
```

- 命中断言（程序化断言脚本复核 raw 响应，非仅依赖任务简报；任一断言
  失败即非零退出、不产出证据）：`workflow_runs` 中 event=push、
  head_branch=main 恰 1 条——run **35632399209**，workflow
  `.github/workflows/ci.yml`，head_sha `f1dfcbbbb…`，status=completed，
  **conclusion=success**（created 2026-09-21T17:30:05Z / updated
  17:34:19Z，https://github.com/swsgbl/ai-learning-os/actions/runs/35632399209）。
- jobs 断言：`total_count=5` 且全部 conclusion=success——API
  （ruff/pytest/migration）、Docker（compose build+healthy+smoke）、
  Release tools（Python tests）、Android（unit test/lint/assemble）、
  Web（test/typecheck/lint/build）。与 `.github/workflows/ci.yml` push
  触发面一致。
- 按 `_eval_ci_main` 契约**程序化派生** `evidence/ci-main.json`（脚本
  `derive_ci_main.py` 从 raw 事实拼装 gate/run_id/merge_commit/conclusion
  白名单字段 + source 溯源串；断言不过即失败，无手写 pass）；**未改任何
  M14-81/M14-83/M14-85 文件**。
- raw API 响应原样归档：`raw/gh-runs-f1dfcbb.json`、
  `raw/gh-jobs-35632399209.json`。

### 2.2 release-check（隔离本地 full 重跑，f1dfcbb 代码）

worktree 环境从零搭建（直连可用，未用代理）：

```bash
uv venv .venv --python 3.12          # CPython 3.12.14
uv pip install -r services/api/requirements.txt -r services/api/requirements-dev.txt
npm ci --no-audit --no-fund          # 411 packages
```

（npm 提示 `unrs-resolver` postinstall 被 allowScripts 策略阻止——
如实记录；该脚本产物非门禁依赖，后续 10 门全绿证实无影响。）

隔离编排器（一次性 SQLite → alembic upgrade head → 127.0.0.1 临时 uvicorn →
/health 就绪 → full 10 门 → 原子落盘 → finally 关停临时 API；环境剥离
AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL，显式 APP_ENV=development /
VOICE_MODE=local——不连接任何生产面；worktree 绝对路径调用）：

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli release-check-isolated \
  --workdir "<worktree>/artifacts/m14-86-isolated-r1" --json
# exit 0
```

- 结果：**all_green=true，10/10 pass**（generated_at
  2026-09-21T17:45:54Z）——api-lint（ruff）/ web-lint / web-typecheck /
  web-build / api-test（**pytest 4024 passed, 33 skipped** in 239.41s；
  与 M14-81 @ e1f128b 的 4024/33 **完全一致**——e1f128b..f1dfcbb 为纯
  docs 增量、零测试变更，计数持平属预期对账，无新增项可解释）/ migration
  （current == head == 0027_audit_chain）/ backup（aios-backup-v1
  tables:30 files:1）/ voice（local 合成 audio/wav 17324 bytes）/ license
  （api 15/web ok/models 7/sources 6）/ e2e（walkthrough 5 步 1390 ms）。
- 工作区产物（gitignored）：`artifacts/m14-86-isolated-r1/`（一次性
  release-check.sqlite + uvicorn.log + release-check-isolated.json）。
- `release-check-isolated.json` 逐字节复制改名为 canonical
  `evidence/release-check.json`（程序化比对 byte-identical=True，sha256
  复核见 §4 表），文件自带 gate 自标识 `release-check`，非手改。

### 2.3 release-readiness 聚合（M14-86 canonical 证据目录）

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli release-readiness \
  --evidence-dir "D:/AI Learning OS/ai-learning-os/.verify/artifacts/m14-86-current-main-code-evidence/evidence" \
  --json --output "<worktree>/artifacts/m14-86-readiness/release-readiness.json"
# exit 1（release_ready=false，如实）
```

- manifest（generated_at 2026-09-21T17:48:23Z，evidence_dir 指向 M14-86
  canonical 目录）：**pass=2**（ci-main：run 35632399209 @ f1dfcbb
  conclusion=success；release-check：all_green 10/10）＋ **missing=9**
  （production-preflight / backup-restore / audit-chain-anchor /
  legacy-papers / draft-ownership / long-soak / provider-smoke /
  release-approval 为 required，turn-tls 为 optional）；malformed=0、
  tampered=0；manifest 内嵌 ci-main/release-check 证据 sha256 与实际
  canonical 文件哈希程序化交叉核验一致；**release_ready=false，
  exit_code=1**。
- 设计口径（与 M14-81/M14-85 相同）：M14-86 证据目录**只放本切片真实
  重推导的两个代码绑定门**——生产状态绑定门不搬运 m14-75/m14-81/M14-83/
  M14-85 旧文件冒充 current（搬运会让绑定各自时点树/生产状态的快照在
  聚合里显示 pass，即过度宣称）。各生产门最新可用记录仍见：
  M14-83 canonical（@ 5829ad9，production-preflight 5/5、治理两门、
  audit-chain valid、provider-smoke **blocked** 如实）、M14-85 canonical
  （@ f47a1e4，backup-restore verified=true）。

## 3. 聚焦验证与静态检查（真实执行结果）

- 聚焦契约测试（本切片证据链所依赖的工具面，worktree venv）：

```bash
cd services/api && "<worktree>/.venv/Scripts/python.exe" -m pytest -q \
  tests/test_release_readiness.py tests/test_release_check_isolated.py tests/test_release_checklist.py
# 137 passed, 1 warning in 2.63s（与 M14-81 @ e1f128b 一致——三个测试文件在纯 docs 增量中零改动）
```

- canonical 五份 JSON `json.load` 解析通过；release-readiness 消费即契约
  校验（malformed=0/tampered=0）；canonical `release-check.json` 与隔离
  运行原始产物**逐字节一致**（程序化比对 True）。
- 本切片零 Python 源码改动（docs-only），`git diff --check` 干净。
- 分支卫生：`git status --porcelain` 无未提交 tracked 变更（单 commit 后
  tracked-clean；worktree 侧运行日志与 `artifacts/` 产物均 gitignored/
  未入库）；未 push、未建 PR。

## 4. canonical 证据清单（主仓 gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：`.verify/artifacts/m14-86-current-main-code-evidence/`

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/ci-main.json` | 904 | `2d179c9a219fd0499115de727fba03b8ae8d41a0fc47a1174b5ff7747176b57d` |
| `evidence/release-check.json` | 2491 | `4b2b38e09aeb38d2a070e156c91c9b8816d5f2f8216c99c5171a1b830cdd8b42` |
| `evidence/release-readiness.json` | 6843 | `5074b8b778cfbdc0937b81a29eede1e4f43c829dd056b8942b539f13d8991f24` |
| `raw/gh-runs-f1dfcbb.json` | 12199 | `3cf7c50676d63d45d517d2854da8457cc74a501be6ad175cc1fae7358c85c1b0` |
| `raw/gh-jobs-35632399209.json` | 12334 | `6e4ec9953aaf9da87ba5174536ffe0868acbcbbd22fb1c0c64834445bd4b05f5` |

（worktree 侧 `artifacts/m14-86-isolated-r1/` 与 `artifacts/m14-86-readiness/`
为生成现场，gitignored；canonical 三件为逐字节复制，哈希逐一程序化复核。）

## 5. 诚实边界与剩余阻塞（不伪称，逐项可执行收口）

1. **八个 required 生产状态门 + optional turn-tls 全部 missing（如实）**：
   production-preflight / backup-restore / audit-chain-anchor / legacy-papers
   / draft-ownership / provider-smoke / release-approval——重推导需要生产
   DB/MinIO/语音 live 面访问与运维窗口，本切片零生产触碰约束下全部如实
   missing，不搬运 m14-75/m14-81/M14-83/M14-85 旧证据冒充 current；
   turn-tls 仅公网语音发布形态必需。各门最新真实记录见 §2.3 所列各切片
   canonical 目录。
2. **long-soak 仍 missing（权威锚已存在，24h 审计未发生）**：M14-79 权威
   锚定记录（canonical
   `.verify/artifacts/m14-79-soak-window-anchor/soak-window-anchor.json`）
   锚点 = 2026-09-21T15:00:01Z、最早审计时点 = 2026-09-22T15:00:01Z；
   本切片 readiness 生成于 2026-09-21T17:48:23Z，距最早审计时点尚差约
   21 小时，24h 窗口结局**未判定**，long-soak 门如实 missing，不预宣称。
   锚定 ≠ long-soak 通过、不授权任何 readiness。
3. **release-approval 从未发生**：人工审批 human-only，本切片明确不触碰、
   不代拟。
4. **provider-smoke 已知未恢复（M14-83 如实发现）**：SearXNG 上游出站
   断开（search fail）与 Ollama inactive（llm fail）截至 M14-83 时点无
   恢复记录，本切片零生产触碰、不代验证恢复，其 canonical 状态保持
   blocked——发布前需运维真实收口后再行重推导。
5. **生产仍运行 m14-70 镜像**（2026-09-19 切换后未再变更），本切片零
   调度面触碰、不构成任何部署；`release_ready=false`、
   `production_ready=false` 不变，本切片不授权任何部署。
6. GitHub Actions run 有平台保留期（默认 90 天）；到期后按 §2.1 命令
   重取即可。
