# M14-167：生产重启自愈证据回填（Reboot Self-Heal Evidence）

- 切片：worktree
  `ai-learning-os-worktrees/m14-167-reboot-selfheal-evidence`，分支
  `m14-167-reboot-selfheal-evidence`，基于 main
  `9bbda837c18a498d9fef9a61d894fa4dfb71bdba`（PR #256 merge =
  M14-166 证据收口合入，精确基点）。
- 定性：**docs-only 证据回填**——本切片零运行时代码、零模板、零测试、
  零 compose、零 env、零 infra、零生成资产变更；不重启、不启动、不
  停止、不查询任何生产服务，不读写任何 secret。重启由 supervisor
  在本切片之外执行；本 README 只把 supervisor 已验证事实落档。本
  README 为唯一入库证据文件；恢复日志等原始工件位于 gitignored
  目录，内容不回显、不入库。Windows 本地绝对路径在入库文档中以
  `<仓库盘>` 占位（仓库既有惯例）。
- 核心结论：M14-166 遗留的最大开放项「**reboot 自愈尚未验证**」
  （原表述：受控重启后恰好一个控制器持有 frpc 实例启动且公网恢复
  才算完成）已于 2026-09-28 04:14 本地时间受控重启后**全部满足并
  验证**——恢复任务自愈成功、7/7 compose 服务 healthy、语音引擎
  受控拉起、恰好一个计划任务持有的 frpc 实例运行、公网四端点
  恢复 200。仅此一项关闭；其余开放项与 production_ready=false
  口径不变（见 §9）。

## 1. 基点与 PR 链

| 项 | 值 |
| --- | --- |
| main 基点 | `9bbda837c18a498d9fef9a61d894fa4dfb71bdba`（PR #256 merge = M14-166 证据收口） |
| M14-166 证据收口 | PR **#256**，state **MERGED**；merge commit 即上表基点；本切片为其直接后续 |
| 关联交付 | M14-06 生产恢复编排 + BootTrigger 自愈、M14-155/165 frpc 控制器与 `AIOS-Edge-FRPC` 计划任务、M14-164 公共 PWA 入口（事实引用，本切片零触碰） |

## 2. 重启事实（supervisor 执行，本切片只落档）

| 项 | 值 |
| --- | --- |
| LastBootUpTime | `2026-09-28 04:14:10` 本地时间 |
| 定性 | supervisor 发起的受控重启；重启动作发生在本切片之外，本切片零生产触碰 |

## 3. 恢复任务 `AIOS-Production-Recovery` 自愈触发（supervisor 观察）

| 项 | 值 |
| --- | --- |
| LastRunTime | `2026-09-28 04:14:27` 本地时间（重启完成后 BootTrigger 自动触发，无需人工） |
| LastTaskResult | `0`（成功） |

## 4. 恢复日志事实（`artifacts/recovery/recovery-20260928-041625.log`，supervisor 引用）

恢复日志记录的本次自愈执行（内容不入库，只落档结论）：

- 镜像 pin 校验 **9/9** 通过；
- `compose up -d --no-build` **幂等**（无重建、无漂移）；
- **6/6** profile 服务 healthy；
- 清理 stale voice manifest 并**受控启动** FunASR/CosyVoice
  （voice_service_control 既有纪律：manifest 即事实源、start 幂等、
  绝不误杀）；
- 恢复结果 **OK**。

## 5. 重启后健康检查（supervisor 观察，全部时点事实）

| 项 | 值 |
| --- | --- |
| compose 项目 `aios-m14-03-production-rehearsal` | **7 服务 healthy**：api、web、livekit、minio、postgres、redis、searxng |
| `127.0.0.1:8010/health`（FunASR） | **HTTP 200** |
| `127.0.0.1:8011/health`（CosyVoice） | **HTTP 200** |

- 6/6（§4 恢复任务 profile 口径）与 7 服务（§5 重启后 compose 项目
  整体口径，含 searxng）为两次独立观察，均如实记录、不相互调和。

## 6. 边缘自愈：`AIOS-Edge-FRPC` 与 frpc 进程（M14-166 开放项关闭证据）

| 项 | 值 |
| --- | --- |
| 计划任务 LastRunTime | `2026-09-28 04:14:55` 本地时间 |
| 计划任务 State | `Running` |
| 计划任务 LastTaskResult | `267009`（`0x41301`——任务正在运行，长驻任务预期状态码，**非失败**） |
| frpc 进程 | PID `17324`，CreationDate `2026-09-28 04:14:55` 本地时间 |
| 实例唯一性 | 全机**恰好 1 个** frpc 实例（由计划任务持有，无重复/孤儿实例） |
| controller status | `installed` |

- LastTaskResult `267009` 是 Task Scheduler「任务当前正在运行」
  状态码：frpc 为长驻进程，任务持续 Running 是预期形态；与
  M14-166 时代安装后未运行的 `1999/11/30` + `267011` 形成对照——
  BootTrigger 已在真实重启中生效。
- 对照 M14-166 §7 第 2 条关闭条件：受控重启 ✅（§2）、**恰好一个**
  控制器持有的 frpc 实例启动 ✅（本节）、公网端点恢复 ✅（§7）——
  reboot 自愈验证**完成**。

## 7. 公网回归检查（supervisor 实测，全部 HTTP 200）

| URL | 大小 |
| --- | --- |
| `https://ndtool.cn/aios/download` | 20488 bytes |
| `https://ndtool.cn/aios/manifest.webmanifest` | 669 bytes |
| `https://ndtool.cn/aios/sw.js` | 7284 bytes |
| `https://ndtool.cn/aios/health` | 46 bytes |

- 四项字节数与 M14-166 §5 记录逐一一致——重启后公网入口内容
  无漂移；前三项即 M14-164 PWA 入口，第四项为公共健康端点。

## 8. 本切片的验证（docs-only 口径，真实执行）

- 文档/版本守卫（近期限额证据 PR 同款、零网络）：
  `test_versioning_rollback.py::test_version_sources_in_sync`
  （VERSION == web package.json == CHANGELOG 顶部版本条目）、
  `test_production_monitor.py::test_r1_docs_commit_wording_sweep`
  （状态文档措辞守卫）——全部通过；
- `git diff --check` 干净；
- 新增行 secret / 本地绝对路径（盘符或根路径形态，正反斜杠变体）/
  U+FFFD 扫描 **0 命中**（Windows 路径均为 `<仓库盘>` 占位形态）。

## 9. 诚实边界

1. 本切片是 **docs-only 证据回填**：所有生产观察（重启、计划任务、
   恢复日志、进程、健康检查、公网检查）均为 supervisor 执行或复核
   的时点事实，本切片零生产变更、零 secret 读写。
2. reboot 自愈验证（BootTrigger → 恢复任务 → 边缘 frpc → 公网恢复
   全链）**本项关闭**；这是**单次受控重启**的实证，不构成对未来
   每次重启必成功的持续保证。
3. §5/§7 健康与公网检查是时点证据；公网入口可用性依赖家机 frpc
   常驻与 VPS Nginx/frps 存活。
4. **production_ready=false 不变**，仍开放项：真实 4G/5G 手机清单、
   TURN/TLS 与真实语音 E2E、Android 签名 release APK 分发、
   Harmony AGC 签名 HAP 分发、持续监控/告警、长 soak 生产证据。
5. 任何 token/密钥/密码/secret 值与生产 env 内容绝不写入；本地
   绝对路径以 `<仓库盘>` 占位；恢复日志原文不入库。
6. 本切片交付为 branch + PR，PR 创建即止；合并决策归 supervisor
   审查（supervisor 审查与 remote 发布在其后进行）。
