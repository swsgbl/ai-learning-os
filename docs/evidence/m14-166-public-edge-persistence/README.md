# M14-166：公共 PWA + FRPC WSS 生产证据收口（Public Edge Persistence Evidence）

- 切片：worktree `.claude/worktrees/m14-166-public-edge-evidence`，分支
  `m14-166-public-edge-evidence`，基于 main
  `978efec70641d50c61c59118553da8268ef8d7fe`（PR #255 merge =
  M14-165 frpc WSS 控制器合入，精确基点）。
- 定性：**docs-only 证据收口**——本切片零运行时代码、零模板、零测试、
  零 compose、零 env、零 infra、零生成资产变更；不部署、不重启、不
  停止、不查询任何生产服务，不读写任何 secret，不扩大生产 env。
  本 README 为唯一入库证据文件；supervisor 执行现场的原始工件位于
  仓库外 gitignored 目录，内容一律不回显、不入库。Windows 本地绝对
  路径在入库文档中以 `<仓库盘>` 占位（仓库既有惯例）。

## 1. 基点与 PR / CI 链

| 项 | 值 |
| --- | --- |
| main 基点 | `978efec70641d50c61c59118553da8268ef8d7fe`（PR #255 merge） |
| M14-164 通用 PWA 下载/安装入口 | PR **#254**，state **MERGED**；final head `586dd6297d81136b8af10c4a2230ba096474cb70`；merge commit `f71796cfddb0a82b7ecdbe9eb9eb97cd9292bc6e`；PR checks **5/5 SUCCESS**（Android / API / Docker / Release tools / Web） |
| M14-165 frpc WSS 控制器 | PR **#255**，state **MERGED**；final head `eded4964325f1c81376813cb708e0242971b2814`；merge commit 即上表基点；PR checks **5/5 SUCCESS** |
| merge-post main CI | run `36345231051`，head `978efec`，conclusion **SUCCESS**，5/5 标准 job 全部 SUCCESS |

## 2. M14-165 控制器实现事实（已合入 main，本切片只引用）

`tools/ops/frpc_windows_controller.py` 支持两种窄 preflight 模式：

- **direct**：公网 IPv4 + 端口 7000 + TLS；
- **WSS**：DNS serverAddr + 端口 443 + `transport.protocol = "wss"` +
  TLS + `tls.serverName == serverAddr`。

fail-closed 边界保持：inline token、非 loopback 后端、TLS 关闭、
SNI 不一致、混合端口/协议形态、占位域名、相对路径、未支持 transport
一律拒绝。token 文件只检查安全元数据（存在性/形态/权限/非符号链接），
内容既不读取也不打印。

审查轮事实：

- **R2**：修复中文 Windows `schtasks` 解码——GBK/cp936 的"任务不存在"
  消息正确分类为 missing；`/Query /XML` 的 UTF-16 BOM 输出正确解析；
  未知失败保持 fail-closed。
- **R3**：把解码与 RealRunner 测试钉到 Windows 分支，Linux CI 仍能
  练到 Windows 路径（CI 平台隔离）。

本地聚焦验证（reviewed worktree 内）：controller 测试 **59 passed**；
Ruff 与 `py_compile` 通过。

## 3. 生产 preflight 与计划任务事实（supervisor 执行，本切片只落档）

supervisor 用 canonical 控制器 preflight 对真实配置复核，观察到：

| 项 | 值 |
| --- | --- |
| 模式 | `wss` |
| server | `ndtool.cn` |
| proxies | `aios-public-web`、`aios-public-api` |
| token 文件 | 元数据（存在/形态）present |
| 输出纪律 | 无 token/secret 出现 |

Windows 计划任务 `AIOS-Edge-FRPC`：

| 项 | 值 |
| --- | --- |
| controller status | `installed` |
| Task Scheduler 状态 | Ready / Enabled |
| 触发器 | 系统启动（BootTrigger，带控制器实现的短延迟） |
| LogonType | S4U |
| RunLevel | least privilege |
| Command | `<仓库盘>\.aios-public-edge\bin\frpc.exe` |
| Arguments | `-c "<仓库盘>\.aios-public-edge\frpc-ndtool-wss-443.toml"` |
| 描述 marker | `urn:aios:m14-155:edge-frpc-controller` |
| LastRunTime | `1999/11/30` |
| LastTaskResult | `267011` |

- LastRunTime/LastTaskResult 的解释：该任务是 boot 触发，安装后机器
  尚未重启过，因此任务**还没有运行过**——这是预期状态，不是故障；
  reboot 自愈验证仍未完成（见 §7）。

## 4. 既有生产 frpc 进程未被重启（supervisor 观察）

| 项 | 值 |
| --- | --- |
| PID | `31256` |
| 进程路径 | `<仓库盘>\.aios-public-edge\bin\frpc.exe` |
| 启动时间 | `2026-09-27 22:45:02` 本地时间 |

- 计划任务安装**没有**重启该进程；公网链路在观察窗口内持续由该
  进程承载。

## 5. 公网回归检查（supervisor 实测，全部 HTTP 200）

| URL | Content-Type | 大小 |
| --- | --- | --- |
| `https://ndtool.cn/aios/download` | `text/html; charset=utf-8` | 20488 bytes |
| `https://ndtool.cn/aios/manifest.webmanifest` | `application/manifest+json` | 669 bytes |
| `https://ndtool.cn/aios/sw.js` | `application/javascript; charset=UTF-8` | 7284 bytes |
| `https://ndtool.cn/aios/health` | `application/json` | 46 bytes |

- 前三项即 M14-164 通用 PWA 入口的公网实证（下载页、manifest、
  service worker 均已从生产边缘可达）；第四项为既有公共健康端点。

## 6. 本切片的验证（docs-only 口径，真实执行）

- controller 测试套件：`pytest services/api/tests/test_frpc_windows_controller.py`
  ——**59 passed**（canonical venv）；
- 文档/版本守卫（近期限额证据 PR 同款、零网络）：
  `test_versioning_rollback.py::test_version_sources_in_sync`
  （VERSION == web package.json == CHANGELOG 顶部版本条目）、
  `test_production_monitor.py::test_r1_docs_commit_wording_sweep`
  （状态文档措辞守卫）——全部通过；
- `git diff --check` 干净；
- 新增行 secret / 本地绝对路径（盘符或根路径形态，正反斜杠变体）/
  U+FFFD 扫描 **0 命中**（Windows 路径均为 `<仓库盘>` 占位形态）。

## 7. 诚实边界

1. 本切片是 **docs-only 证据收口**：不对生产做任何变更，所有生产
   观察（preflight、计划任务、进程、公网检查）均为 supervisor 执行
   或复核的时点事实。
2. **reboot 自愈尚未验证**：BootTrigger 持久化不能宣称完成——直到
   一次受控重启后恰好启动一个控制器持有的 frpc 实例且公网端点恢复，
   才能关闭该项。
3. 既有 PID `31256` 保持运行；安装过程没有重启它。
4. §5 公网检查是时点证据，不构成持续可用性保证；公网入口可用性
   依赖家机 frpc 常驻与 VPS Nginx/frps 存活。
5. 仍开放项：真实 4G/5G 手机清单、TURN/TLS 与真实语音 E2E、
   Android 签名 release APK 分发、Harmony AGC 签名 HAP 分发、
   持续监控/告警、长 soak 生产证据。
6. 任何 token/密钥/密码/secret 值与生产 env 内容绝不写入；本地
   绝对路径以 `<仓库盘>` 占位。
7. 本切片交付为 branch + PR，PR 创建即止；合并决策归 supervisor
   审查（supervisor 审查与 remote 发布在其后进行）。
