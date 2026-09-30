# M14-190：current-main 发布证据刷新（M14-189 后；ci-main + release-check 重跑 + 防御性复用 + cockpit 聚合）

## 0. 交付与边界

- 切片：分支 `ops/m14-190-current-main-release-evidence`，复用 worktree
  `ai-learning-os-worktrees/m14-188-web-warning-hygiene`，基于 current
  `origin/main` / `main` `ffd74cf84d211912f8605abe20e533ccfabf2468`
  （PR #279 merge = M14-187 Harmony current-main regression）。交付为
  **单 local commit**；按任务边界 **不 push、不开 PR、不触碰 production**。
- 刷新动因：M14-185 的代码绑定门证据绑定 `8d52f00`，其后
  `8d52f00..ffd74cf` 为 **13 commits**，包含 M14-186 Phase 0 工具与测试、
  M14-188/M14-189 web 运行时与测试、M14-187 Harmony release 工具与测试。
  该区间不是 docs-only，因此 ci-main 与 release-check 必须在
  `ffd74cf` 上重新绑定；本切片不复用 M14-185 的这两门产物。
- 区间不变量：`services/api/app/`、API requirements、`services/api/alembic/`
  在 `8d52f00..ffd74cf` 均零变更；alembic head 仍为 `0027_audit_chain`。
  九个聚焦契约套件计数与 M14-185 相同，是工具面未漂移的互证，不用于
  取代 full release-check。
- provider-smoke / long-soak / production-state 均按任务边界零重跑、零生产
  接触，只做哈希锁定复用；其中 provider 原始输入目录当前缺失，本切片使用
  M14-185 canonical 聚合 + M14-185 `SHA256SUMS` + M14-185 indexed 输入核验
  日志作为防御性输入链，**不声称本轮重新读取了原始输入文件**。
- `cockpit_ready=true` 只表示 9 个非人工审批必需门全部 pass 且无技术
  blocker；`release_ready=false`、`production_ready=false` 保持不变。
  `release-approval` 是 human-only required gate，本切片不生成、不代拟、
  不审批。
- 零生产触碰：无容器启停/重建、无 production API/DB/MinIO/voice/secrets
  访问，无 Docker、Android/USB、Harmony emulator/device、CC Switch、代理、
  scheduler 或其他无关进程操作。唯一网络访问是 GitHub Actions 只读 API；
  另有隔离 release-check 环境的 uv/npm 包安装。

## 1. ci-main

通过 GitHub Actions 只读 API 查询 `ffd74cf` 的 push run 与 jobs，两份 raw
响应原样归档，stderr 捕获为空（SHA256
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`）。

断言后派生 canonical `evidence/ci-main.json`：

- run `36655040106`，run_number `696`，event `push`，head_branch `main`，
  head_sha `ffd74cf84d211912f8605abe20e533ccfabf2468`，
  status `completed`，conclusion `success`。
- created `2026-09-30T01:25:28Z`，updated `2026-09-30T01:30:27Z`。
- jobs `total_count=5`，5/5 success，head 全部匹配，job 名集合精确为：
  API（ruff / pytest / migration）、Android（unit test / lint / assemble）、
  Docker（compose build + healthy + smoke）、Release tools（Python tests）、
  Web（test / typecheck / lint / build）。

原始与 canonical：

| 文件 | bytes | SHA256 |
|------|-------|--------|
| `raw/gh-runs-ffd74cf.json` | 12205 | `b31ff8a9f50431a1368f202c4678322b986cc80a4181254c21ce26744d83207b` |
| `raw/gh-jobs-36655040106.json` | 13027 | `acd378da4739411ef31e8026b762a7dc6cf6b7e1bc7258be89819f00cd86eb08` |
| `evidence/ci-main.json` | 1378 | `cca4fe43f69f0c6211e36fac1bfb4651156f3993a2152e488785baabc88a4a0a` |

## 2. release-check

执行前核验：`git rev-parse HEAD == ffd74cf...`，tracked
`git status --porcelain` 为空；日志归档于
`logs/pre-release-check-head.log`。随后从零重建隔离环境：

- `services/api/.venv`：CPython 3.12.14，uv 安装 59 packages。
- root `npm ci --no-audit --no-fund`：411 packages。
- npm 保留既有 `unrs-resolver` install-scripts warning；release-check 10/10
  通过，未影响结果。

隔离 full 编排器实际运行于干净 tracked tree `ffd74cf`，结果：

- `execution_scope=full`，`all_green=true`，10/10 pass，failed/not_executed
  均为空，`generated_at=2026-09-30T05:30:49.563157+00:00`。
- api-lint、web-lint、web-typecheck、web-build 全 pass。
- api-test：`5631 passed, 36 skipped, 1 warning in 323.37s`。
- migration：`current == head == 0027_audit_chain`。
- backup：`aios-backup-v1 tables: 30 files: 1`。
- voice：local topology，`audio/wav` 17324 bytes。
- license：api deps 15 / web ok / models 7 / sources 6。
- e2e：walkthrough 5 steps，1192 ms。

canonical `evidence/release-check.json`（2488 bytes，SHA256
`13374e0973e0766983244a2439f69f138b3c35a1c409b6762ee8fc43c683ba7e`）与
isolated 原始报告逐字节一致。

## 3. 复用证据

### 3.1 provider-smoke

- canonical 聚合 564 bytes，SHA256
  `d589181e078de875faac11052e148d5ff32b0b6f4d7b41fb716b4a584c60a761`，
  与 M14-185 canonical 及其 `SHA256SUMS` 登记值一致。
- schema `provider-smoke-evidence-v1`，gate `provider-smoke`，拓扑
  `voice_mode=local`；voice/search/llm 三槽位均 `executed=true`、
  `result=pass`，对应 `local-voice-smoke` / `search-smoke` / `llm-smoke`。
  聚合生成时间为 `2026-09-26T01:14:14.270193+00:00`；本轮 prepare 时打印
  年龄 `4d 4h 18m`。
- 诚实边界：M14-185 当时引用的原始 provider 输入目录当前不存在。本轮未
  重跑 provider，也未重新读取这些原始输入；输入事实只能追溯到 M14-185
  `SHA256SUMS` 锁定的 `logs/prepare-reused-evidence.log`。该 indexed log 记录
  三个 pass 输入及耗时（search 7496ms、voice 18442ms、llm 12213ms），并记录
  三个失败 attempt 未被改写（envmiss 60ms、querymiss 769ms、timeout 10483ms）。
- 本轮 `logs/prepare-reused-evidence.log` 明确输出“original provider input
  directory is absent”，并以 M14-185 indexed verification log 作为输入链；
  contract 断言同时复核 M14-185 log 的 SHA256 与索引一致。

### 3.2 long-soak

canonical `evidence/long-soak.json`（817 bytes，SHA256
`d939c6528b9fe739270fe6ea706837ffa9dc13115665dc90740e6db53a1056ec`）为
M14-185 同哈希复用。报告为 `audit_schema_version=2`、classification
`pass`、97 ok / 0 warn / 0 critical、max gap 17.25 分钟、span 1440 分钟。

窗口为 `2026-09-22T02:00:01Z` 到 `2026-09-23T02:00:01Z`，早于生产切换且
来自 m14-70 时代栈。本切片不重跑、不制造新窗口；切换后的 24h soak 仍缺，
是否补做由 supervisor 决策。

### 3.3 production-state

六个历史 production-state 源从 M14-185 cockpit staging 只读复核并 staging，
本轮未重执行生产检查。哈希均为 MATCH：

| 源 | bytes | SHA256 |
|----|-------|--------|
| `audit-chain-anchor.json` | 1418 | `a8c54c5e316ea573ab10532a6f234eb60cba8463f724b6e52b2b14f2877b816a` |
| `audit-anchor.jsonl` | 354 | `d2bfd877aa94632e6f68932a4d4bef8d963a6eb61aca12de6429c5fb7873aa4e` |
| `production-preflight.json` | 2103 | `b3a2be66fd99b82c56942449ac32bb34248d290adbaab55438fbcc2ef55e1e51` |
| `legacy-papers.json` | 574 | `83cd62bdb0a336e485e9ad8a324691f2af9e4f002a53fcb031acd4535d0dbc16` |
| `draft-ownership.json` | 578 | `e90ae04c7ce34f3deff0a19d99a91674cd9d9e3149906a491d5446212c396aad` |
| `backup-restore.json` | 346 | `ed0fc5b4df983a05e880564bed5796843f9aea3808d5d2900db01c533e3fa09b` |

## 4. evidence-cockpit

在 tracked docs 编辑前重新聚合，显式 current head 与 release-check declared
head 均为 `ffd74cf...`。canonical
`cockpit-report.json`（19057 bytes，SHA256
`b9dd280359fa8e7d808a9a1b104cb89bfc0696535c521479912d9e0282ea3d99`）：

- readiness：pass 9 / pending 0 / blocked 0 / missing 2 / malformed 0 /
  tampered 0。
- missing：required human-only `release-approval`；optional `turn-tls`。
- `cockpit_ready=true`，`cockpit_blockers=[]`，`required_not_staged=[]`，
  tool exit 0。
- `readiness.release_ready=false`，顶层 `production_ready=false`；本切片不
  授权发布。
- ci-main 与 release-check 均 `stale_status=current`，head origin 分别为
  embedded / flag。
  - staged 9 门 + anchor companion 共 10 文件，内部校验和外部复核均为
  10/10 byte-identical；清单见 `staged-inventory.txt`（1211 bytes，SHA256
  `6e8bf7c13669582fe465a4f4bd0353851615cd3093e9f4c0f7571c367e80c4f6`）。

## 5. 验证

- 聚焦九套件：`444 passed, 1 warning in 9.19s`，命令与结果归档于
  `logs/focused-tests.log`。
- evidence 脚本 ruff：六个脚本 `All checks passed!`，日志
  `logs/ruff-evidence-scripts.log`。
- contract 断言：`ALL ASSERTIONS PASSED (95 checks)`，日志
  `logs/assert-contracts.log`；覆盖 raw run/jobs、精确五 job 集合、canonical
  hash、release-check 10/10 与计数、provider/long-soak、cockpit readiness、
  code-bound current、production-state 哈希、staging 10/10、M14-185 indexed
  provider 输入链。
- hygiene：canonical artifacts secret 扫描 0 命中；tracked docs 新增行
  secret / 本地绝对路径 / U+FFFD 扫描 0 命中；日志
  `logs/scan-hygiene.log`。
- `git diff --check` 通过。
- `SHA256SUMS` 以 POSIX 相对路径、LF 覆盖 canonical 目录全部文件并排除自身；
  单文件哈希以该清单为最终索引。

## 6. 剩余边界

1. provider-smoke 是 2026-09-26 时点证据，原始输入已缺失；本轮证据链只能
   证明 M14-185 聚合未被改动、M14-185 当时 indexed log 曾核验原始输入。
   不承诺窗口外 provider 状态，不伪称本轮新鲜执行。
2. long-soak 窗口早于生产切换且来自旧栈；切换后无新 24h 窗口。
3. production-state 是历史 canonical snapshot，只读呈现，不是本轮生产重执行。
4. `turn-tls` optional 未 stage；公网语音形态不能沿用本报告放行。
5. `release-approval` human-only 未发生，发布、回滚与观察期决策仍归
   supervisor。
6. ci-main/release-check 证据只对 `ffd74cf` 树内容成立；current main 之后的
   非 docs-only 变更会再次使代码绑定门 stale。docs-only 入库不触发递归刷新。
