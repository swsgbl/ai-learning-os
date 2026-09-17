# M14-45 LiveKit 主仓库受控恢复证据回填（容器最小范围重建 + 真实浏览器 WebRTC 验收）

- 回填日期：2026-09-18（本地 GMT+8）；恢复执行窗口 2026-09-18 04:25:09–04:25:10（+08:00），单次受控操作
- 恢复执行基点：主仓库 `main@9c21311f00bc4bfee39d47ae0b0906242f9e5ec1`（PR #121 merge；恢复全程工作树 clean，未改动任何 tracked 文件）
- 本回填（M14-45）为 **docs-only**：零生产触碰、零 Docker 操作、零代码改动——只把 supervisor 已完成的受控恢复与真实浏览器验收事实转为可审计仓库证据
- 原始证据：主仓库 gitignored `.verify/m14-44-livekit-main-recovery/`（15 个文件，MANIFEST.txt 记录全量 SHA-256，见下表）——**不入 git**，本 README 仅摘录判定事实
- 秘密边界：`infra/env.production-recovery` 内容全程未打印、未复制；证据目录凭据类（KEY/SECRET/PASSWORD/TOKEN）子串扫描 **0 命中**（仅命中的 4 个值均为拓扑配置：APP_ENV / BIND_IP / LIVEKIT_BIND_IP / PUBLIC_LIVEKIT_URL——IP、URL、环境名，属验收脚本设计内可审计事实）
- 结论：**生产语音路径（LiveKit 容器 + 真实浏览器 WebRTC）恢复并经真实浏览器验收通过（verdict=passed，13/13 checks，exit 0）**；本回填不声称 `production_ready=true`

## PASS 判定表

| # | 验收项 | 要求 | 实测 | 判定 |
|---|---|---|---|---|
| 1 | 根因定位 | 旧容器失败机理明确 | 旧 livekit 容器 `49935f127ca0…` bind-mount 指向已删除的 M14-38 worktree YAML 路径（源路径已变目录）→ `OCI runtime create failed … not a directory`、ExitCode **127**、Status=exited | ✅ |
| 2 | 恢复范围 | 仅 livekit，其余五容器零接触 | 单命令 `up -d --no-deps --force-recreate livekit`；api/web/postgres/redis/minio **容器 ID 逐一不变**且 healthy | ✅ |
| 3 | 新容器健康 | running healthy | 新容器 `4aa604546c80…` Up (**healthy**)、ExitCode 0、State.Error 空、restart unless-stopped、健康检查连续 OK | ✅ |
| 4 | 挂载修正 | 指向主仓库 regular file | `D:\AI Learning OS\ai-learning-os\infra\livekit\livekit.yaml` / `livekit-public.yaml`（主仓库 regular file，ro） | ✅ |
| 5 | 端口发布 | M14-38 LAN cutover 拓扑完整 | `192.168.8.3` 上 TCP 7880/7881 + UDP 7882-7892 **全部 13 条**（docker port + netstat LISTENING 双确认） | ✅ |
| 6 | API 健康 | 恢复前后不回归 | API `/health` 200 → 200 | ✅ |
| 7 | 真实浏览器验收 | default 模式全过 | **verdict=passed，13/13 checks，exit 0**（详见下节） | ✅ |
| 8 | 秘密扫描 | 零凭据泄露 | 凭据类（KEY/SECRET/PASSWORD/TOKEN）扫描 **0 命中**（仅 4 个拓扑值，见元数据） | ✅ |

## 根因（变更前证据）

旧 livekit 容器（`49935f127ca0…`，M14-38 cutover 时以 worktree compose 文件创建）bind-mount 指向已删除的 worktree：

- Mounts Source：`D:\AI Learning OS\ai-learning-os-worktrees\m14-38-livekit-lan-cutover\infra\livekit\livekit.yaml` 及 `livekit-public.yaml`——worktree 删除后源路径变为目录
- State.Error：`OCI runtime create failed … error mounting … not a directory: Are you trying to mount a directory onto a file`，ExitCode **127**，Status=exited
- 端口 7880/7881 无监听（信令/媒体面整体不可用）；API `/health` 200（其余面正常）

机理：compose 相对 bind-mount（`./livekit/*.yaml`）在容器创建时钉死到创建它的 compose 文件目录；M14-38 worktree 删除后 source path 失效为目录，容器无法再启动。主仓库 `infra/livekit/livekit.yaml`、`livekit-public.yaml` 均为 regular file（恢复前已确认）。

## 受控恢复（唯一一次操作，命令语义）

```
TS_BEFORE=2026-09-18T04:25:09+08:00
docker compose -p aios-m14-03-production-rehearsal \
  -f infra/docker-compose.yml \
  --env-file infra/env.production-recovery \
  --profile local up -d --no-deps --force-recreate livekit
TS_AFTER=2026-09-18T04:25:10+08:00
```

- `-p aios-m14-03-production-rehearsal`：显式 project 名，对齐既有生产彩排栈、防并行栈
- `-f infra/docker-compose.yml --env-file infra/env.production-recovery`：主仓库 canonical compose + gitignored 生产恢复 env（env 内容全程未打印/未复制）
- `--profile local`：livekit 为 profile-gated 服务；前置确认该组合解析出且仅解析出 6 个服务（api/livekit/minio/postgres/redis/web，raw/compose-services.txt）
- `up -d --no-deps --force-recreate livekit`：最小范围仅重建 livekit；livekit 无 build 段（纯 image，无构建面）；宿主机确认持有 LAN IP `192.168.8.3`

## 变更后状态（before → after）

| 项 | before | after |
|---|---|---|
| livekit 容器 | `49935f127ca0…` Exited(127) | `4aa604546c80d20dc9311b293e1a73f5043596b7a42bf290a752119cd559bd48` Up (**healthy**)，ExitCode 0，State.Error 空 |
| livekit 镜像 | — | `livekit/livekit-server:latest`，restart unless-stopped，健康检查连续 OK |
| livekit 挂载 | worktree 失效路径（目录） | 主仓库 `infra\livekit\livekit.yaml` / `livekit-public.yaml`（regular file，ro） |
| 端口发布 | 无监听 | `192.168.8.3` 上 TCP 7880/7881 + UDP 7882-7892 全部 13 条（docker port + netstat 双确认） |
| API /health | 200 | 200 |
| 其余五容器 | healthy | healthy，**容器 ID 逐一不变** |

不变容器 ID 清单：api `b3e62b355703`、web `5be2e19db2e7`、postgres `d17d5a93a079`、redis `89f3ed0fd02a`、minio `8e4f3d855ffb`。

## 真实浏览器 LiveKit 验收（default 模式 = 生产用户默认浏览器拓扑）

命令（canonical venv，代理已隔离，验收 web 由脚本按设计从当前分支自建、复用生产 API/LiveKit/DB）：

```
AIOS_OUT=.verify/m14-44-livekit-main-recovery/browser-acceptance-default \
  .venv/Scripts/python.exe infra/verify_web_livekit_client.py
```

结果：**verdict=passed，13/13 checks，exit 0**

| 验收项 | 结果 |
|---|---|
| token | passed |
| connect（真实 WebRTC 连接） | passed |
| data（数据通道） | passed |
| mic（fake 设备真实音轨发布） | passed（非 skipped） |
| cleanup | passed |
| DOM 不含 JWT 形态 token | passed |
| ws_url 回显可达 | passed（`ws://192.168.8.3:7880`） |
| 检测窗口 console/pageerror 零错误 | passed（0 错误；登录窗 1 个预期 401 不计入） |
| LiveKit 信令探测 | voice-token-contract 派生 → `http://192.168.8.3:7880/` HTTP 200 |
| 浏览器模式 | default（`AIOS_LIVEKIT_BROWSER_LOOPBACK` 未设置，未注入 loopback flag） |

## 证据清单（源相对路径 + SHA-256，摘自 MANIFEST.txt / results.json）

| 源相对路径（`.verify/m14-44-livekit-main-recovery/` 下） | SHA-256 |
|---|---|
| ACCEPTANCE-REPORT.md | `6e84727e2e999b906b0e1f938a6a44cf83f53a365bc48c1d82b51986b62d2415` |
| browser-acceptance-default/results.json | `6cdd7d37b8f13b977dd346015b85856044a507b770bf73e912b44452539c409a` |
| browser-acceptance-default/screenshots/01-initial.png | `6f2fd12e79cedb882ad4b84b990cc881404a124a0ead24c5b6529d34477a56b6` |
| browser-acceptance-default/screenshots/02-result.png | `748dbf6c610cf2d5447f398c62394e9e78434aef91431fa2940fb0a24b2a721e` |
| raw/browser-default-run.log | `a4b770862103e466d901bcbf5296d5f6378cc620b7f3e153baf0c3006b1c1985` |
| raw/compose-config-stderr.txt | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`（空文件） |
| raw/compose-livekit-resolved.json | `96e7ad6f8bd683ed39582f2bfc9ee7bdcb9f58ff2472159fc52b5f33fe665421` |
| raw/compose-services.txt | `425673ec1be8fabf114cc7e3ac95a0f7bb8cdacf0aec99b0323add2f0aa92e6e` |
| raw/post-livekit-health-wait.txt | `eddb5bd4025daff67d5b81168a85357ff46603b90ab1d03d4bca275207695dce` |
| raw/post-verify-all.txt | `24367096c830107b4d141e20f361df68c395495d10e598e55d2adf0edaad54db` |
| raw/pre-livekit-fullid.txt | `99959a44216b83b09b829d20cefee4aad5f2e5392bfffa811c5f79a3cee54262` |
| raw/pre-livekit-inspect.txt | `d5ffa91f8612f437738b8a9566210b21d260ae87cf46a94beb9d9c8f4707d83e` |
| raw/pre-ports-api-health.txt | `9b695c4e5c9b341b00dc7594b428564dddbb22d443ea0d1fedec25769b42ea29` |
| raw/recreate-livekit.txt | `e9393d5b830edbbea83bf232aaf63efe6d3c675552f701d85b2f6bdf24a0767f` |
| raw/secret-scan.txt | `cfefc26f8750adafef8427778d51ad2d3e84a71ed6ec078e4702a4322ce132f3` |

原始证据目录保持 gitignored，不入库；上表哈希即完整性锚点（与主仓库 `.verify/m14-44-livekit-main-recovery/MANIFEST.txt` 一致）。

## 如实披露与诚实边界

1. **生产 web 容器未重建、未直接测试**：验收 web 为脚本按设计从当前分支自建（复用生产 API/LiveKit/DB，不触碰生产 web 容器）；生产 web 容器（`aios/web:m14-05-security`）本流程未动、未测。
2. **LAN IP 静态假设**：恢复采用 `env.production-recovery` 的 LAN cutover 拓扑（媒体面绑 `192.168.8.3`）；若宿主机 LAN IP 变更（DHCP），livekit 绑定将失败，需以新 IP 更新部署 env 后重走本流程——DHCP 变更仍是风险。
3. **postgres/redis label drift 未解决**：生产 postgres/redis 容器仍携带指向已删除 m14-06 worktree 的 compose config-file/working-dir 标签（与主仓库 compose 配置漂移；其容器定义与主仓库 compose 功能等价）——本恢复按 `--no-deps` 最小范围语义未触碰两容器，该漂移未解决、留待后续运维切片。
4. **不隐含任何 provider smoke pass**：本验收只证明 LiveKit 信令/媒体路径与真实浏览器 WebRTC 连接；不重跑、不隐含 M14-33 式 ASR/TTS provider 冒烟通过。
5. **旧容器已被替换**：旧失败容器 `49935f127ca0…` 已被 compose 重建替换（未额外保留/清理其他资源）。
6. **M14-35 loopback 历史边界**：M14-35 记录的 loopback 拓扑 default 浏览器间歇性 ICE 问题在当前 LAN cutover 拓扑下未复现（default 模式一次通过）；该历史边界属 M14-38 工作范围，非本次恢复引入。
7. **单次恢复 + 单次验收 ≠ 长期稳定**：本 PASS 仅覆盖本文列出的验收项，不构成全局 `production_ready=true` 的依据；全局 `production_ready=false` 不变。

## 边界遵守声明（本回填回合）

本 M14-45 回填回合零 Docker/零生产/零服务操作（全部事实摘自 supervisor 已执行的 gitignored 原始证据）；未 commit/push；未打印或复制任何 secret/token/password（`env.production-recovery` 值零外泄，命令语义只含参数形态不含值）；原始证据 `.verify/m14-44-livekit-main-recovery/` 保持 gitignored 不入库；仅改动 docs 三文件（本 README + PROJECT_STATUS + ROADMAP）。
