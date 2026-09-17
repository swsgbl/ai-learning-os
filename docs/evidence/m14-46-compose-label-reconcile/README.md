# M14-46 postgres/redis compose 标签受控调和证据回填（最小范围重建 + 收敛证明 + 应用面回归探针）

- 回填日期：2026-09-18（本地 GMT+8）；调和执行窗口 2026-09-18 04:45–04:46（+08:00），两次连续受控最小范围重建
- 回填基点：本 worktree `main@a54e1e583efe`（PR #123 merge；回填全程零生产触碰、零 Docker 操作、零代码改动）
- 本回填（M14-46）为 **docs-only**：只把 supervisor 已完成的受控调和事实转为可审计仓库证据；收口 M14-45 记录的「postgres/redis label drift 未解决」诚实边界
- 原始证据：主 checkout gitignored `.verify/m14-46-compose-label-reconcile/`（26 个文件 + `MANIFEST.txt` 全量 SHA-256 清单；无 ACCEPTANCE-REPORT.md，判定事实分散于 probe 文件）——**dump 与 MANIFEST 本体均不入 git**，本 README 仅摘录判定事实与关键哈希锚点
- 秘密边界：`infra/env.production-recovery` 内容全程未打印、未复制；本 README 无 secrets、无环境值、无真实绝对主机路径、无容器日志、无 SQL 数据行、无凭据、无原始输出
- 结论：**postgres/redis 的陈旧 compose 标签已受控收敛至主 checkout compose，六容器终态 healthy，最终 dry-run 无 recreate，仅主 checkout compose 在列**；本回填不声称 `production_ready=true`

## PASS 判定表

| # | 验收项 | 要求 | 实测 | 判定 |
|---|---|---|---|---|
| 1 | 漂移定性 | 仅标签陈旧、无行为/配置漂移 | 变更前 `up -d --dry-run`：六容器均 Running→Healthy，**无任何 Recreate 计划**（config hash 与主 checkout compose 对齐）；drift 仅限 postgres/redis 的 config-file/working-dir 不可变标签 | ✅ |
| 2 | 变更前备份 | PostgreSQL 自定义备份先行 | `postgres-before.dump` **70861 bytes**、SHA-256 `8E4F27EAFB9BFDE99B960D9D897F971F7D555FF8CB9D122329C353306FAD1D51`（见 postgres-backup-manifest.txt） | ✅ |
| 3 | 恢复范围 | 仅 postgres 再 redis，其余四容器零接触 | 两次 `up -d --no-deps --force-recreate`（先 postgres 后 redis）；api/web/minio/livekit **容器 ID 逐一不变**且全程 healthy | ✅ |
| 4 | 重建后健康 | 两容器均 healthy | postgres `f928410e404e…` 04:46:07 healthy；redis `6006a4c551e1…` 04:46:38 healthy | ✅ |
| 5 | 数据卷保留 | 数据零丢失 | postgres 命名卷 `aios-m14-03-production-rehearsal_postgres-data` 不变；redis 匿名卷 `2d88095b8c2e…` **前后同 ID**（未用 `--renew-anon-volumes`） | ✅ |
| 6 | 终态收敛 | dry-run 无 recreate | 最终 `up -d --dry-run`：六容器 Running→Healthy，**零 Recreate** | ✅ |
| 7 | compose 归属 | 仅主 checkout compose 在列 | `compose ls`：本项目 ConfigFiles **仅** 主 checkout `infra/docker-compose.yml` 一条 | ✅ |
| 8 | 应用面回归 | API/DB/redis 不回归 | API `/health` 200（前后一致）；unknown-user login probe 预期 401；临时表事务 create/insert/select/rollback 全过；redis `PING`→`PONG` | ✅ |

## 根因（变更前证据）

生产彩排栈（project `aios-m14-03-production-rehearsal`）六容器均 healthy，但 postgres 与 redis 两个容器的不可变 compose 标签（`com.docker.compose.project.config_files` / `…working_dir`）仍指向**已删除的 m14-06-production-resilience worktree** 下的 infra 路径：

| 容器 | 变更前 ID（前 12 位） | config_files / working_dir 标签 |
|---|---|---|
| postgres（postgres:17-alpine） | `d17d5a93a079` | 已删除的 m14-06 worktree infra 路径 |
| redis（redis:7-alpine） | `89f3ed0fd02a` | 已删除的 m14-06 worktree infra 路径 |
| api / web / minio / livekit | `b3e62b355703` / `5be2e19db2e7` / `8e4f3d855ffb` / `4aa604546c80` | 主 checkout infra（正常） |

漂移定性（关键区别于 M14-45 的 livekit bind-mount 失效）：

- **无行为/配置漂移**：变更前 dry-run 对六容器均无 Recreate 计划——运行中容器的 config hash 已与主 checkout compose 对齐，drift 仅是死路径标签
- **风险在出处追踪与后续运维**：标签是容器不可变属性，无法原地改写；若未来从错误目录/文件调用 `up`，可能被判定为不同配置而触发误 recreate
- **数据拓扑**：postgres 数据在项目命名卷（标签正确、与容器 recreate 解耦）；redis 数据在匿名卷（recreate 默认沿用，除非 `--renew-anon-volumes`）

## 受控调和（supervisor 已执行，命令语义；两次连续操作）

```
# 第一刀：仅 postgres
docker compose -p aios-m14-03-production-rehearsal \
  -f infra/docker-compose.yml \
  --env-file infra/env.production-recovery \
  --profile local up -d --no-build --no-deps --force-recreate postgres
# → postgres healthy（04:46:07 +08:00）后，第二刀：同参数仅服务名换 redis
docker compose … up -d --no-build --no-deps --force-recreate redis
# → redis healthy（04:46:38 +08:00）
```

- `-p aios-m14-03-production-rehearsal`：显式 project 名，对齐既有生产彩排栈、防并行栈
- `-f infra/docker-compose.yml --env-file infra/env.production-recovery`：主 checkout canonical compose + gitignored 生产恢复 env（env 内容全程未打印/未复制）
- `--no-build`：无构建面（postgres/redis 均纯 image 形态）
- `--no-deps --force-recreate`：最小范围，每次仅重建目标服务，四邻容器零接触
- **不加** `--renew-anon-volumes`：redis 匿名卷原卷沿用（数据保留的关键前提）

## 变更后状态（before → after）

| 项 | before | after |
|---|---|---|
| postgres 容器 | `d17d5a93a079e983c99a5481f381504885d5476e1a8c74e3d686902af21b9517`（healthy，标签指向已删 worktree） | `f928410e404eb59a39f71c1e36d77430b43e0fe7cb5908d6a520f15cd77d1fe5` Up (**healthy**)，config_files/working_dir 指向主 checkout |
| redis 容器 | `89f3ed0fd02ae7e5dfc86f326319a4d69a55f35ae35cf67b4f7eed93aa102bd5`（healthy，标签指向已删 worktree） | `6006a4c551e1616e27235314c285d87089eb3defc4333d2a3a3c37307ca95bf4` Up (**healthy**)，config_files/working_dir 指向主 checkout |
| postgres 数据卷 | 命名卷 `aios-m14-03-production-rehearsal_postgres-data` | 同一命名卷（recreate 不触碰卷） |
| redis 匿名卷 | `2d88095b8c2e237dfa6167f4e7e1ff73326116b87ae2cc5bcbc306195b5bb6f0` | **同一卷 ID**（重建前后 mounts 一致） |
| api / web / minio / livekit | healthy（ID `b3e62b355703` / `5be2e19db2e7` / `8e4f3d855ffb` / `4aa604546c80`） | healthy，**ID 逐一不变** |
| 六容器终态 | healthy×6（含两容器标签漂移） | **healthy×6**，标签全部指向主 checkout |
| compose dry-run | 无 Recreate（但两容器标签为死路径） | 无 Recreate（收敛，无任何待变更项） |
| compose ls（本项目） | —（变更前漂移即由容器标签证实） | 项目 running(6)，ConfigFiles **仅** 主 checkout `infra/docker-compose.yml` |

## 应用面证据（调和后回归探针）

| 探针 | 结果 | 说明 |
|---|---|---|
| API `/health` | **200**（before 与 final 一致，body `{"status":"ok","service":"ai-learning-os-api"}`） | 调和前后无回归 |
| unknown-user login probe | **HTTP 401**（04:46:07.96 +08:00，postgres 重建 healthy 后即刻） | 预期错误路径——证明 API→postgres 认证查询链路真实贯通（未触碰真实凭据） |
| 事务探针 | **pass**：BEGIN → CREATE TABLE → INSERT 0 1 → SELECT 返回 1 行 → ROLLBACK 全部成功 | 临时表事务，rollback 后不留任何数据 |
| redis `PING` | **PONG**（redis 重建 healthy 后） | 缓存链路贯通 |
| livekit 端口拓扑 | LAN IP `192.168.8.3` 上 TCP 7880/7881 + UDP 7882-7892 全 13 条不变 | M14-45 恢复成果零回归 |

## 证据完整性锚定

- 变更前备份：`postgres-before.dump` = **70861 bytes**，SHA-256 `8E4F27EAFB9BFDE99B960D9D897F971F7D555FF8CB9D122329C353306FAD1D51`
- 全量清单：主 checkout `.verify/m14-46-compose-label-reconcile/MANIFEST.txt`（26 文件逐文件 SHA-256），MANIFEST 自身 SHA-256 = `501edd6ba68c20beda6ead7115d11d7a45ae8aa0872ba071f619616e48418dbc`
- 关键判定文件哈希（摘自 MANIFEST.txt）：

| 源相对路径 | SHA-256 |
|---|---|
| containers-before.txt | `DC80145AC7F8A2CBCBBF0CBB42F809F2282E301A0FAC4716490686C5A2231355` |
| compose-dry-run-before.txt | `FB93DB1335DEA63EA1EDD195516C43FECF2F94D1A76AAD23F0EF04A59A07B243` |
| postgres-backup-manifest.txt | `E73A0DEFFFCE4762C4F79C073EE37181F629C13E2AF7E8B19565E2B173CDE5C6` |
| postgres-volume-before.txt | `8ABC354FB22D9A45DBBBC50FBDE4014855BA5C0589AFC3EF84C87F0C7A97E789` |
| redis-mounts-before.txt | `C15C5B333AA99764D5FDE8977B8C679F822319A0E367E4DFD4E4A9BFEC68A68B` |
| recreate-postgres.txt | `8A012BF10DC9B08A058A714F9B45FA4B37F4364AA00D55BAEFEE83570016B574` |
| postgres-after.txt | `C8A4816EEB4E515D00B1CC7851682AF7A348C3A4BD792FB5AA916ECA81564364` |
| recreate-redis.txt | `C6F66012807628A7F6B6933D7E1017C30290AD80AF77E0A88DDBA4F5EC405411` |
| redis-after.txt | `1548202007028712C38720A55710852E5446D4C9DF8D68090C434309ADD9A314` |
| containers-final.txt | `F76204362AA67C09367ADB5AB76405BC2BE738FF479D937171495DB4DA28E955` |
| compose-dry-run-final.txt | `1A343F7C772DB42F5BD2BD91AB71007B92C3423A79DF41EE42F1D772FB5A6A3B` |
| compose-ls-final.json | `66DDAA9692A76484B3C1E6EB96DFA5E649FAE91D19A09EE671BF9BE286E5DDEF` |

原始证据目录（含 dump 与 MANIFEST 本体）保持 gitignored，不入库；上表哈希即完整性锚点。

## 如实披露与诚实边界（residuals）

1. **休眠并行 compose 项目未删除**：宿主机上与生产彩排栈并存的休眠 compose 项目（exited 容器 + 自有数据卷）仍保留，未清理——误操作风险仍在，留待独立运维切片。
2. **不隐含 provider smoke**：本次调和只证明基础设施收敛与应用面回归，不重跑、不隐含 ASR/TTS provider 冒烟通过。
3. **生产 web 容器未做浏览器验收**：本切片探针为 API/DB/redis 面生产 web 容器本身未经浏览器验收。
4. **WORM 离线第二副本 / 定期归档 / 长稳就绪仍开放**：审计链离线第二副本、定期归档调度与长期运行就绪证据仍缺位（M14-44 起开放项）。
5. **静态 LAN IP 假设**：生产拓扑仍绑定 LAN IP `192.168.8.3`；DHCP 变更会使 livekit 绑定失效——静态 IP 假设风险不变。
6. **release/cutover 批准**：生产发布与正式 cutover 仍未获批、未执行。

单次受控调和 + 单轮回归探针 ≠ 长期稳定：本 PASS 仅覆盖本文列出的验收项，不构成全局就绪依据；全局 **`production_ready=false` 不变**。

## 边界遵守声明（本回填回合）

本 M14-46 回填回合零 Docker/零生产/零服务操作、零代码改动（全部事实摘自 supervisor 已执行的 gitignored 原始证据）；未 commit/push；未打印或复制任何 secret/token/password（`env.production-recovery` 值零外泄，命令语义只含参数形态不含值）；dump 与原始 MANIFEST 不入 git；无真实绝对主机路径、无容器日志、无 SQL 数据行、无凭据、无原始输出；仅改动 docs 三文件（本 README + PROJECT_STATUS + ROADMAP）。
