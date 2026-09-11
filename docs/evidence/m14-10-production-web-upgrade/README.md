# M14-10 生产 Web-only 升级（M14-05 安全镜像上产）+ M14-09/M14-05 状态回填 — 验收证据归档

- 日期：2026-09-11
- 分支：`docs/m14-10-production-web-upgrade`（基于 `main@433d018`（PR #81 merge
  commit，本地 git 可验证），本切片 **docs-only**）
- 状态：M14-09 已随 **PR #81** 合并 main（feature `321fb50c422af7257b9995c2b13ebcf23918da7d`，
  merge `433d0184e0202bd3379c74517f7d3e69fe6e88f6`，本地 git 可验证——merge 第二父即
  feature commit）；本机生产栈 **Web-only 升级已完成**（生产 web 容器自
  `aios/web:m14-03-prod-rehearsal` 换为 `aios/web:m14-05-security`，运行时自报
  Next.js 16.3.4）
- 入库证据：本 README（唯一入库文件）；`artifacts/recovery/recovery-20260911-170957.log`
  为 gitignored 本机留存，不入库
- 结论：生产只换了 web 一个容器，api/数据面/语音引擎零触碰（容器 ID 与启动时间
  不变）；升级后全端点 200、compose 6/6 healthy；恢复任务 dry-run 与真实触发双绿。
  **单机生产栈口径，`production_ready=false` 不变**

## 动机

M14-05（next 15.5.24 → 16.3.4，清生产 audit 1 high + 1 moderate）已随 **PR #76**
合并 main（PR head `539dbe17df302f9d8b191a4bbc753948ee91d55d`，merge
`ebbb700045d6a3ec0305f841d34c079600cf875d`，本地 git 可验证），M14-09（独立
`AIOS_WEB_IMAGE_TAG` 锚点）已随 **PR #81** 合并 main——代码与配置两道前置都已
在 main，但生产 web 容器仍在 `m14-03-prod-rehearsal`（旧一代镜像）。本切片把
「首次 Web-only 生产升级」执行掉并把全部事实回填进唯一进度真相源（此前
PROJECT_STATUS/ROADMAP 仍记录 M14-05「未合并」、生产 Web 未升级——已过期）。

## 验证时间线（2026-09-11，均为 supervisor 给定事实 + 本地 git 可验证部分）

1. **合并与本地独立验证（M14-09 切片口径，PR #81 合并前）**：聚焦五套件
   （production_recovery / versioning_rollback / compose_restart_policy /
   release_candidate / release_readiness）**237 passed, 2 skipped**（skip 为既有
   git/真实 env 磁盘护栏）；`docker compose config --quiet` × {无 profile, local,
   hybrid, cloud} 4× exit 0（零容器改动）；ruff、`bash -n`、`py_compile`、
   `git diff --check` 全部通过。
2. **目标镜像预检**：`aios/web:m14-05-security`，image ID
   `sha256:793060d3210f93d331463bf86014f46943aa4b4e7fb8b2b32483f696d0115fc2`；
   临时预检容器 `/` 与 `/login` 双 HTTP 200，验证后即移除；容器运行日志自报
   **Next.js 16.3.4**。
3. **升级前生产基线**：compose 项目 `aios-m14-03-production-rehearsal` 六服务
   **6/6 healthy**；web 为 `aios/web:m14-03-prod-rehearsal`，旧 web 容器 ID
   `3a9a271fcf0c40134f1e44994b41eeebf1af481029163915234866f2850cc9ce`（started
   `2026-09-11T01:12:52.793545565Z`）；api 容器 ID
   `909f99fc20d497942e5152004c92a30578ba1074b4fa1e2ee780e595b6020262`（started
   `2026-09-11T01:12:47.107522172Z`）。
4. **env 准备（唯一一次非密钥键变更）**：supervisor 先把 gitignored 真实
   `infra/env.production-recovery` 备份到仓库外，再补
   `AIOS_WEB_IMAGE_TAG=m14-05-security`，`AIOS_IMAGE_TAG=m14-03-prod-rehearsal`
   保持不变——两锚点解耦即 M14-09 设计语义。**本 README 不含任何 env 原文、
   密钥值、token、password、key 或 SID**。
5. **生产替换（只动 web）**：canonical
   `docker compose -f infra/docker-compose.yml --env-file infra/env.production-recovery
   up -d --no-build --no-deps web`——compose 输出只 recreated/started `web`。
   新 web 容器 ID
   `5be2e19db2e745af96a0d08b2dfaa64f0529b34d80e549e639a021b7c74294e3`（started
   `2026-09-11T09:09:05.420392796Z`），镜像 `aios/web:m14-05-security`，
   **healthy**。
6. **升级后端点复测**：Web `http://127.0.0.1:3011/` = 200、`/login` = 200；API
   `/health` = 200；FunASR `/health` = 200；CosyVoice `/health` = 200；compose
   `ps` 六服务 api/livekit/minio/postgres/redis/web 全部 running healthy。

## 生产只影响 web 的证据（unchanged-service evidence）

- **api 容器零触碰**：容器 ID `909f99fc…` 与启动时间
   `2026-09-11T01:12:47.107522172Z` 在部署后与恢复任务触发后**两次核对均不变**。
- **其余 compose 服务**（livekit/minio/postgres/redis）全程 healthy/running，
   compose 输出只涉及 `web`。
- **语音边界（绝不写成被重启）**：`voice_service_control status` 显示 FunASR 与
   CosyVoice 为 unmanaged-running 且 healthy，owner PID 分别为 **1183** 与 **2061**，
   全程不变；恢复 dry-run 与真实任务两次均选择 **leave/untouched**——本切片
   绝未重启两引擎。

## 恢复任务（AIOS-Production-Recovery）结果

- **dry-run（canonical 口径）**：六键 pin **6/6** 一致（含新增
  `AIOS_WEB_IMAGE_TAG`）；compose 栈快照 6/6 healthy；compose up 仅「计划」未执行；
  语音 untouched；result **OK**。
- **真实触发**：官方 `Start-ScheduledTask` 于 **2026-09-11 17:09:55（本地时间）**
  触发；任务返回 **Ready**，`LastTaskResult=0`。编排日志
  `artifacts/recovery/recovery-20260911-170957.log`（gitignored 本机留存）报告：
  六键 pin **6/6**、compose `up` 幂等 **no-op**（web 已是新镜像，零重建）、
  6/6 healthy、FunASR/CosyVoice untouched、result **OK**。
- M14-09 预判的「合并后缺 `AIOS_WEB_IMAGE_TAG` 键 → enforce 可见拒绝」fail-closed
  行为**未发生事故**：supervisor 已按预判先行补键（步骤 4），首次 enforce 即绿灯。

## CI 外部失败（如实记录，不以本地通过掩盖）

PR #81 CI run **`34582538887`**：五个 job 全部失败且 `step_count=0`——与 PR #78
（`34563126434`）/PR #79（`34564346333`）及 aef48f6 提交信息所载同源的已知外部
GitHub Actions/billing 路径故障形态。**非代码回归**；同时**不隐藏 CI 未运行**
这一事实——远端 CI 当前不构成门禁事实，门禁依据为上述本地独立验证。

## 回滚锚点

Web-only 回滚 = recovery env 把 `AIOS_WEB_IMAGE_TAG` 改回
`m14-03-prod-rehearsal`，重跑同一条 canonical compose 命令
（`up -d --no-build --no-deps web`）；api 锚点 `AIOS_IMAGE_TAG` 全程不动。旧镜像
`aios/web:m14-03-prod-rehearsal`（旧容器 ID `3a9a271f…` 同源镜像）仍在本机，
无需重建。M14-09 的独立双锚点设计（PR #81）即为此路径而设。

## 边界

- **`production_ready=false` 不变**：本次为单机生产栈的 Web-only 替换与恢复任务
  验证，不等于全局生产就绪。剩余阻塞至少包括：长稳/并发 soak、真实客户端
  （真实浏览器/真机/生产流量）验收、监控/告警收口、GitHub Actions 外部 0-step
  故障恢复、HarmonyOS AGC 签名与真机发布链。
- **无密钥边界（硬性）**：本 README 与对应 commit 不含任何 secret 值、env 文件
  原文、token、password、key 或 SID；真实 recovery env 为 gitignored 文件，仅
  supervisor 在仓库外备份与修改。
- 本切片（M14-10 回填）零代码/测试/workflow/env/容器/服务/计划任务改动，
  不运行 pytest、不触碰 Docker、不修改 untracked `.claude/`。
