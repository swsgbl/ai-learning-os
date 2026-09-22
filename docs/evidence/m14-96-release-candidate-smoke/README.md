# M14-96：RC 本地彩排冒烟（实现/文档切片）

- 切片：分支 `ops/m14-96-release-candidate-smoke`（独立 worktree
  `m14-96-release-candidate-smoke`，基于 main
  `c9de72214fbe07f41f5d6706e1c72ccb7e60cac3`（PR #182 merge = M14-94
  证据合入，精确基点），单次 local commit（不推送、不建 PR）。
- 目标：为 API/Web 镜像提供**与运行中生产栈并存**的隔离 RC 本地彩排
  冒烟机制并真实执行一次；任何失败如实报告为失败——本切片的执行结论
  是 **build-failed:api（proxy-unavailable，诚实失败）**，见 §3。
- 零生产触碰（本切片全程）：未停/未启/未改任何生产容器、卷、网络、
  计划任务、语音引擎、CC Switch、代理与 daemon 配置；未读取
  `infra/env.production-recovery` 或任何 secret 文件；不推镜像仓库、
  不打 git 标签、不发 GitHub Release、不做 release-approval 声明；
  `release_ready=false` / `production_ready=false` 不变。

## 1. 机制（新增，默认 no-op）

- **`infra/docker-compose.rc-smoke.yml`**：任务自有隔离 compose——顶级
  `name: aios-m14-96-rc-smoke`（容器/网络/卷全部项目级命名，与生产
  彩排栈 `aios-m14-03-production-rehearsal` 及开发栈零共享）；恰好
  五服务 postgres/redis/minio/api/web（无 livekit/searxng/profile 面）；
  **零 build 段 + 全服务 `pull_policy: never`**（绝不隐式 pull）；api/web
  镜像 tag 经 `${AIOS_RC_SMOKE_API_TAG:?...}` / `${AIOS_RC_SMOKE_WEB_TAG:?...}`
  **必填变量注入**（缺 env 即渲染失败，绝不回落 local/生产 tag）；
  数据面三服务**零宿主端口**（健康经容器 healthcheck 证明），边缘面只绑
  `127.0.0.1:18096:8000` / `127.0.0.1:13096:3000`（与生产 compose 宿主
  端口全集 5433/6379/9000-9001/8000/3000/7880-7892/8878 **零交集**，
  契约测试静态断言）；**零 restart 策略**（一次性栈）；卷仅项目级
  postgres-data/minio-data（`down --volumes` 只删本项目）；api
  environment 自生产 compose **逐键逐字复制**（契约测试锁全量一致），
  唯一刻意差异 CORS_ORIGINS 默认指向冒烟 Web 端口 13096；healthcheck/
  depends_on 与生产 compose 逐块一致。**默认 no-op**：只有被 `-f` 显式
  引用才生效，生产 compose / RC 构建器 / 冒烟脚本零引用（测试断言）。
  不用 overlay 组合生产文件的原因：compose `ports` 合并语义为**拼接**，
  无法经 override 摘除生产文件的硬编码宿主端口（5433/6379/9000-9001/
  8000/3000 与生产栈冲突）——独立文件是唯一不漂移路径。
- **`tools/ops/rc_smoke_rehearsal.py`**：M14-93 同款单文件纯标准库
  fail-closed runner（子进程/HTTP/端口探测/时钟全部注入可测）。阶段：
  全新输出目录 → 前置校验（HEAD==`--base-sha`；porcelain 脏文件必须
  全部落在镜像构建输入面 `services/api/{app,requirements.txt,alembic*}`/
  Dockerfile/VERSION/.dockerignore/package*.json/`apps/web/` **之外**；
  项目零残留容器/卷；冒烟 tag 镜像不存在（绝不覆盖）；infra 镜像
  本地存在（自 compose 文件解析 pin，绝不 pull）；127.0.0.1:18096/13096
  可 bind；compose config 渲染且项目名/服务集精确）→ 构建唯一 tag
  `aios/{api,web}:m14-96-rc-smoke-<HEAD 40hex>`（Web 构建参数
  NEXT_PUBLIC_API_BASE_URL 指向冒烟 API）→ 隔离 up（compose 子进程 env
  全量剥离宿主 `AIOS_*` 漂移变量、只注入两个纯 tag 后缀）→ 逐服务
  健康轮询 → `docker port` 证明恰为 loopback 独占映射 → 八项 loopback
  探针（/health、/api/v1/version==VERSION 文件、auth/status
  auth_enabled=true、匿名 GET /api/v1/papers 401、**匿名 POST
  /api/v1/resources/upload 401**（应用级 `require_user` 门禁既有契约：
  AUTH_SECRET 配置时非豁免路径无凭据一律 401——属"已契约定义"的匿名
  保护写断言）、Web / 与 /login、CORS preflight 回显）→ 无论成败恒拆
  本项目（down --volumes --remove-orphans）+ 残留复核 → **前后全机
  docker ps -a/volume/network/compose-ls 快照等价证明**（本项目之外
  任何漂移即失败）→ 证据 + SHA256SUMS 清单。运行期护栏
  `assert_safe_argv`：compose 调用恒 `-f` 冒烟文件 + `-p` 任务项目名，
  docker 动词白名单之外（stop/rm/kill/restart/prune/push/system/…）
  一律 GuardError。退出码 0 pass / 1 refused / 2 failed / 3 用法错误。
- **生产默认零漂移回归**：`infra/docker-compose.yml`、
  `infra/build_release_candidate.sh`、`infra/smoke_docker.sh` 三文件以
  LF 归一化 sha256 **字节级 pin** 到基点 c9de722 的 git blob 哈希
  （d2185291…/3efa0258…/5f346e6b…，测试内登记）——任何对生产面文件
  的改动都击穿 pin，必须显式改 pin 才能通过；另断言生产语义不变式
  （name=ai-learning-os、7 服务 restart: unless-stopped 原样）。

## 2. 真实执行（attempt-1，2026-09-22T13:46:06Z–13:46:10Z）

runner 以 `--base-sha c9de722…` 在本 worktree 真实执行，逐阶段证据
（gitignored `.verify/artifacts/m14-96-release-candidate-smoke/`，7 文件
+ SHA256SUMS，哈希见 §5）：

- **前置校验全部真实通过**（timeline: preconditions 13:46:06Z→13:46:08Z）：
  `git rev-parse HEAD` == 基点 c9de722（git-head.txt）；porcelain 恰为
  本切片 3 个 tooling 新文件（infra compose / runner / 测试），全部在
  镜像构建输入面之外 → `build_inputs_clean=true`（git-status-porcelain.txt
  ——API/Web 镜像构建输入（services/api/app、apps/web、package*.json、
  VERSION 等）与基点树逐字节同源）；冒烟项目零残留容器/卷；唯一 tag
  `aios/{api,web}:m14-96-rc-smoke-c9de722…` 此前不存在（绝不覆盖
  v0.1.0/生产 tag）；infra 三镜像本地在库；18096/13096 bind 预检通过；
  **真实 `docker compose config` 渲染通过**（compose-config.json：name
  =aios-m14-96-rc-smoke、恰五服务、api=127.0.0.1:18096→8000、web=
  127.0.0.1:13096→3000、数据面零发布端口）；全机 before 快照留档
  （docker-context-before.txt：生产栈 7 容器全部 healthy Up 33 hours）。
- **构建诚实失败**（timeline: build 13:46:08Z→13:46:10Z）：
  `docker build -f services/api/Dockerfile` 在 `FROM python:3.12-slim`
  的 metadata 解析即失败——本机 Docker daemon 配置了 registry mirror
  `docker.m.daocloud.io` + 静态系统 HTTPS 代理 `http://127.0.0.1:7892`，
  该代理端口当时无监听（连接拒绝），任何 registry 元数据请求均不可达
  （docker-build-api.txt 全量留档）。**本地镜像库经只读核查无
  `python:3.12-slim` / `node:22-alpine`**（`docker images` 无 python/
  node 条目）→ 离线/no-pull 构建前提不成立。supervisor 纠正令明确
  禁止启停 VPN/sing-box/代理或改动 daemon/mirror 配置（超出本任务
  边界），故**不再重试构建，诚实停止**：最终结论
  **status=failed，reasons=[build-failed:api]（runner exit 2）**，
  见 rc-smoke-report.json/.md。
- **fail-closed 语义按设计生效**：失败发生在 build 阶段，compose 从未
  up——零容器/零卷创建（事后只读复核：项目 label 容器数 0），无需
  清理也无清理动作执行；生产栈全程未受影响（before 快照即执行时点
  生产状态，其后零 Docker 变更来自本任务）。镜像 tag 从未存在故无
  覆盖面；未推任何仓库。
- **执行暴露并已修复的真实缺陷**：attempt-1 的 compose 渲染证据显示
  runner 曾把完整 ref 注入 `AIOS_RC_SMOKE_API_TAG`（compose 文件 image
  已带 `aios/api:` 前缀 → 渲染出双重前缀镜像名；栈未起故零影响）。
  已修复为只注入纯 tag 后缀，并以回归测试锁定（up 调用 env ==
  `m14-96-rc-smoke-<sha>`，见 test_runner_compose_env_carries_tags_only）。
- **VPN 事件披露**：supervisor 纠正前，本切片曾误调用 `vpn-manager on`
  （被打断，但 sing-box 已被拉起、7892 端口处于监听）。收到纠正令后
  **零 VPN/代理/daemon 操作**；该进程状态未再改动（会话 Stop 钩子按
  环境规约兜底清理）。此后未借道该代理做任何构建重试。

## 3. 诚实边界

- 本切片交付的是**机制 + 一次真实（失败）执行**：构建因本机 daemon
  出站面（mirror+静态代理）不可用而失败；**compose 起栈/健康/探针/
  拆栈证明未发生**（构建是起栈的前置）。不声称任何 readiness。
- `release_ready=false` / `production_ready=false` 不变；发布审批
  human-only；生产仍运行 m14-70 镜像。
- 冒烟机制对生产的并存性由静态契约（端口零交集/项目名隔离/字节级
  生产 pin/命令白名单护栏）+ before 快照 + 零容器事实共同支撑；
  **运行时并存（并行起栈）尚未被真实执行证明**——待出站面可用后
  由 runner 一次执行补齐（机制零改动可复跑）。
- 匿名保护写断言只覆盖既有 `require_user` 契约面（401），未造新契约。

## 4. 测试与验证

- 聚焦契约 **59 passed**（`services/api/tests/test_rc_smoke_rehearsal.py`：
  compose 静态契约 15 + 真实 compose 渲染契约 3（有 Docker 时）+ 生产
  字节级 pin/语义回归 5 + runner 契约 36）。
- 邻域回归 **176 passed, 3 skipped**（test_release_candidate /
  test_release_check_isolated / test_compose_profiles /
  test_compose_restart_policy——既有 RC 路径与 compose 契约零回归）。
- `ruff check services/api tools/ops/rc_smoke_rehearsal.py` 全绿
  （CI 同款命令 + tools 面）；`py_compile` 通过；`git diff --check`
  干净。

## 5. 证据清单（gitignored canonical 目录 + SHA-256）

`.verify/artifacts/m14-96-release-candidate-smoke/`（attempt-1，失败
证据如实保留；manifest 逐文件哈希由 runner 写出后独立复核一致）：

| 文件 | sha256 |
|---|---|
| git-head.txt | 5f71e091050da208b828623ec0b072b216921d82b77a3e9a2e7fd1b5472aeae8 |
| git-status-porcelain.txt | 01f3d077c91a35af8539afeaacb74a7f0a3b1814c36719960330670c52a431db |
| docker-context-before.txt | c8797f0ac3eb3e341c43313e775d6332863d990b3cd7375471ce68399ac71fdb |
| docker-build-api.txt | 70a058f2f4400dd6321fd7f3c6f32e9fd14e21495ff1bd17f7320053050240c4 |
| compose-config.json | c9cfbc93c800f3ee1a93bbd1521c960c945a8826204f98655c048e9b473e60ce |
| rc-smoke-report.json | e4bfe7367b7a24522b961cc9e0176c9948ffd538472168444c29bce38410a2da |
| rc-smoke-report.md | 47a5ef753bf7f2b0a7fccfd90035ef0c7c7e011010c96a98d3d62bebaa8f689b |

注：attempt-1 报告内 `evidence_files` 列出的其余文件名（build-web/
up/ps/health/port/probes/down/after 快照等）因失败点在 build:api 而
未产生——列名单是 runner 的固定申报面，非存在性声明。
