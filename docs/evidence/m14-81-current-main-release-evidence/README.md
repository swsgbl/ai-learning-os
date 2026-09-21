# M14-81：current-main release 证据刷新（实现/文档切片）

- 切片：分支 `ops/m14-81-current-main-release-evidence`（独立 worktree
  `m14-81-current-main-release-evidence`，基于 main `e1f128be80fee736d5326272bcecebc1c729f0fb`
  （PR #168 merge，精确基点，非 origin/main 快照推断）），单次本地 commit
  （不推送、不建 PR）。
- 目标：沿用 M14-78 证据模式，对**新 main 基点 e1f128b** 只刷新**代码绑定**
  发布门（ci-main / release-check）并重新聚合 readiness；绝不手改 gate
  JSON、绝不合成结果、绝不搬运旧生产状态证据冒充 current、绝不伪称
  production readiness（`release_ready=false` / `production_ready=false`
  全程不变）。
- 零生产触碰（本切片全程）：零容器启停/重建、零计划任务操作、零 DB/MinIO/
  语音引擎接触、零生产日志写入、零 secrets/env 读取（未读取
  `infra/env.production-recovery`，未输出任何 token/key）、零 soak 历史
  读写、零发布审批接触、零部署。唯一网络访问是 GitHub 只读 API
  （`gh api`，已认证账号 swsgbl，本机直连可用——未用代理）+ worktree
  从零环境的包安装（uv/npm）。

## 1. 基点事实（全部 git/远端可复核）

| 事件 | commit | 说明 |
|------|--------|------|
| PR #165 merge（M14-78 证据切片，M14-78 刷新的基点） | `376c4ee` | b7db88c 之后 1 commit |
| PR #166 merge（M14-79 harmony gate-drift 审计 + 链哈希 fail-closed） | `40002dd`（含 `f7e3928`/`382f069`） | harmony 面 |
| PR #167 merge（M14-80 sign_hap 未变更输出 fail-closed 加固） | `b59ea49`（含 `5670a04`） | harmony 面 |
| PR #168 merge（M14-79 soak 恢复门：事件复盘 + 窗口锚定门 + log-error 时间界 + history 超时 45→90s）→ **当前 main** | `e1f128be80fee736d5326272bcecebc1c729f0fb` | 2026-09-21 18:39:09 +0800 |
| main push CI run 35589879598 创建（push 事件） | e1f128b | 2026-09-21T10:39:12Z（UTC） |

- `git diff --stat b7db88c..e1f128b`：10 commits、20 文件、+4474/−51——
  docs×6（CHANGELOG/PROJECT_STATUS/ROADMAP 微量 + 四份 evidence README）、
  `tools/ops/`×4（两个新工具 pipeline_incident_review/soak_window_gate +
  production_monitor 时间界修复 + monitoring_pipeline 超时上调及其 README）、
  `tools/harmony_release/`×2（sign_hap 加固 + agc_closure_manifest 新增）、
  `services/api/tests/`×4 + `tests/harmony_release/`×2（新增契约测试）。
  **运行时服务面零改动**（services/api/app 与 apps/web 无 diff），但代码
  绑定门证据只对其执行时点的树成立——不重新真实执行就不对 e1f128b 成立，
  这正是本切片如实刷新的边界。
- M14-78 canonical 证据（b7db88c 时点）自此对 e1f128b **stale**（ci-main
  锚 b7db88c/run 35548392898；release-check 为 b7db88c 树 2026-09-21T01:02:55Z
  隔离运行）——保留为历史记录，不改写。

## 2. 刷新执行记录（命令与结果，全部真实可复跑）

### 2.1 ci-main（真实远端 CI，非本地重跑）

```bash
gh api "repos/swsgbl/ai-learning-os/actions/runs?head_sha=e1f128be80fee736d5326272bcecebc1c729f0fb&per_page=20"
gh api "repos/swsgbl/ai-learning-os/actions/runs/35589879598/jobs?per_page=30"
```

- 命中断言（脚本复核 raw 响应，非仅依赖任务简报）：`workflow_runs` 中
  event=push、head_branch=main 恰 1 条——run **35589879598**，head_sha
  `e1f128be…`，status=completed，**conclusion=success**（created
  2026-09-21T10:39:12Z / updated 10:43:06Z，
  https://github.com/swsgbl/ai-learning-os/actions/runs/35589879598）。
- jobs 断言：`total_count=5` 且全部 conclusion=success——Web
  （test/typecheck/lint/build）、API（ruff/pytest/migration）、Docker
  （compose build+healthy+smoke）、Android、Release tools。与
  `.github/workflows/ci.yml` push 触发面一致。
- 按 `_eval_ci_main` 契约写 `evidence/ci-main.json`（gate/step/tool 自标识
  + run_id + merge_commit + conclusion + source 溯源串）；**未改任何
  M14-78/m14-75 文件**。
- raw API 响应原样归档：`raw/gh-runs-e1f128b.json`、
  `raw/gh-jobs-35589879598.json`。

### 2.2 release-check（隔离本地 full 重跑，e1f128b 代码）

worktree 环境从零搭建（直连可用，未用代理）：

```bash
uv venv .venv --python 3.12
uv pip install -r services/api/requirements.txt -r services/api/requirements-dev.txt
npm ci --no-audit --no-fund
```

隔离编排器（一次性 SQLite → alembic upgrade head → 127.0.0.1 临时 uvicorn →
/health 就绪 → full 10 门 → 原子落盘 → finally 关停临时 API；环境剥离
AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL，显式 APP_ENV=development /
VOICE_MODE=local——不连接任何生产面；worktree 绝对路径调用）：

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli release-check-isolated \
  --workdir "<worktree>/artifacts/m14-81-isolated-r1" --json
# exit 0
```

- 结果：**all_green=true，10/10 pass**（generated_at
  2026-09-21T10:58:48Z）——api-lint（ruff）/ web-lint / web-typecheck /
  web-build / api-test（**pytest 4024 passed, 33 skipped** in 228.85s；
  较 M14-78 的 3939 增加 85 = M14-79 ops 面 85 项新契约测试：monitor
  时间界 15 + pipeline 超时 pin 1 + incident_review 38 + soak_window_gate
  31，与 b7db88c..e1f128b 的测试 diff 精确对账）/ migration（head
  0027_audit_chain）/ backup（aios-backup-v1 tables:30）/ voice（local
  合成 audio/wav 17324 bytes）/ license（api 15/web ok/models 7/sources 6）/
  e2e（walkthrough 5 步 1070 ms）。
- 工作区产物（gitignored）：`artifacts/m14-81-isolated-r1/`（一次性
  release-check.sqlite + uvicorn.log + release-check-isolated.json）。
- `release-check-isolated.json` 逐字节复制改名为 canonical
  `evidence/release-check.json`（sha256 复核逐字节一致，见 §4 表），与
  M14-78 同款流程；文件自带 gate 自标识 `release-check`，非手改。

### 2.3 release-readiness 聚合（M14-81 canonical 证据目录）

```bash
cd services/api
"<worktree>/.venv/Scripts/python.exe" -m app.ops.cli release-readiness \
  --evidence-dir "D:/AI Learning OS/ai-learning-os/.verify/artifacts/m14-81-current-main-release-evidence/evidence" \
  --json --output "<worktree>/artifacts/m14-81-readiness/release-readiness.json"
# exit 1（release_ready=false，如实）
```

- manifest（generated_at 2026-09-21T11:02:15Z，evidence_dir 指向 M14-81
  canonical 目录）：**pass=2**（ci-main：run 35589879598 @ e1f128b
  conclusion=success；release-check：all_green 10/10）＋ **missing=9**
  （production-preflight / backup-restore / audit-chain-anchor /
  legacy-papers / draft-ownership / long-soak / provider-smoke /
  release-approval 为 required，turn-tls 为 optional）；malformed=0、
  tampered=0；`not_pass_required` 八项如实列出；**release_ready=false，
  exit_code=1**。
- 设计口径（与 M14-78 相同）：M14-81 证据目录**只放本切片真实重推导的
  两个代码绑定门**——生产状态绑定门不搬运 m14-75/m14-78 旧文件冒充
  current（搬运会让未重验证的生产快照在聚合里显示 pass，即过度宣称）。
  各生产门最新可用记录仍见 m14-75 收口存档。

## 3. 聚焦验证与静态检查（真实执行结果）

- 聚焦契约测试（本切片证据链所依赖的工具面，worktree venv）：

```bash
cd services/api && "<worktree>/.venv/Scripts/python.exe" -m pytest -q \
  tests/test_release_readiness.py tests/test_release_check_isolated.py tests/test_release_checklist.py
# 137 passed, 1 warning in 2.56s
```

- canonical 五份 JSON `json.load` 解析通过；release-readiness 消费即契约
  校验（malformed=0/tampered=0）；canonical `release-check.json` 与隔离
  运行原始产物**逐字节一致**（程序化比对 True）。
- 本切片零 Python 源码改动（docs-only），ruff/py_compile 无新对象；
  `git diff --check` 干净。
- 分支卫生：`git status --porcelain` 无未提交 tracked 变更（单 commit 后
  tracked-clean；worktree 侧运行日志与 `artifacts/` 产物均 gitignored/
  未入库）；未 push、未建 PR。

## 4. canonical 证据清单（主仓 gitignored `.verify`，不入 git；本 README 为唯一入库证据文件）

路径：`.verify/artifacts/m14-81-current-main-release-evidence/`

| 文件 | bytes | sha256 |
|------|-------|--------|
| `evidence/ci-main.json` | 757 | `74a2ab1d0525ebe43516318a1bd5c12d5f2a7162e0773c3031d753a5fe72b95b` |
| `evidence/release-check.json` | 2491 | `6cc960b96d15a3062a47aedb96ca2a1797dd6c0d70aab7d27f3d61aa988bbcb1` |
| `evidence/release-readiness.json` | 6846 | `e8bb059c795ba084372efc4bb98d087be4a14b95dde2009631dd43f0c298b68a` |
| `raw/gh-runs-e1f128b.json` | 12176 | `d3a0767e7ac9f1848cad0f3c2781743b71a9530ad590a767a9105c3d5109e5bb` |
| `raw/gh-jobs-35589879598.json` | 12334 | `e313a1b30bc5d55b52cbdd1c3d94da8d00e847f6150a60d04c17e8c67cd53ca2` |

（worktree 侧 `artifacts/m14-81-isolated-r1/` 与
`artifacts/m14-81-readiness/` 为生成现场，gitignored；canonical 三件为
逐字节复制，哈希逐一复核。）

## 5. 诚实边界与剩余阻塞（不伪称，逐项可执行收口）

1. **八个 required 生产状态门 + optional turn-tls 全部 missing（如实）**：
   production-preflight / backup-restore / audit-chain-anchor / legacy-papers
   / draft-ownership / provider-smoke / release-approval——重推导需要生产
   DB/MinIO/语音 live 面访问与运维窗口，本切片零生产触碰约束下全部如实
   missing，不搬运 m14-75/m14-78 旧证据冒充 current；turn-tls 仅公网语音
   发布形态必需。
2. **long-soak 仍 missing（权威锚定已落盘，24h 审计未发生）**：supervisor
   已运行 `python tools/ops/soak_window_gate.py --anchor` 正式锚定，
   **权威锚定记录**已写至 canonical
   `.verify/artifacts/m14-79-soak-window-anchor/soak-window-anchor.json`
   （1514 bytes，sha256 前缀 `3c2fdbccda6997ab…`；本切片只读复核字段
   一致）：**权威锚点 = 2026-09-21T15:00:01Z**（正式锚定时点的最新干净
   样本）、尾部 8 个干净样本、tail_max_gap 15.0 分钟、**最早审计时点 =
   2026-09-22T15:00:01Z**。早先的 14:45:01Z 是锚定前检查的显示值，不是
   固定起点；锚定记录已存在即按 anchor-exists 语义阻止重锚，后续样本
   不会移动锚点。修复后的干净尾部**与 M14-79 log-error 时间界修复的
   预期恢复方向一致**（consistent with intended recovery），不构成独立
   因果证明。**锚定 ≠ long-soak 通过、不授权任何 readiness**：canonical
   证据中不存在任何 24h 审计结果，readiness 的 long-soak 门因此**如实
   missing**；24h 窗口结局待 2026-09-22T15:00:01Z 后由
   `soak_stability_audit` 真实判定（通过才可把报告逐字节复制为
   long-soak.json 证据）。本切片零 soak 历史/锚定记录写入（锚定由
   supervisor 完成）。
3. **release-approval 从未发生**：人工审批 human-only，本切片明确不触碰、
   不代拟。
4. **M14-79/M14-80 运维面变更的验证边界**：log-error 时间界与 history
   超时 45→90s 已随 PR #168 进入 main；修复后 canonical history 尾部
   恢复 8 连续干净、窗口门 open 并完成正式锚定（见第 2 条）——与预期
   恢复方向一致，非独立因果证明；history 超时上调与 M14-80 harmony 面
   尚未有独立验收记录，不预宣称。生产仍运行 **m14-70 镜像**
   （2026-09-19 切换后未再变更），本切片零调度面触碰、不构成任何部署。
5. `release_ready=false`、`production_ready=false` 不变；本切片不授权任何
   部署。
6. GitHub Actions run 有平台保留期（默认 90 天）；到期后按 §2.1 命令
   重取即可。
