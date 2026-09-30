# M14-199：Phase 1 VPS 侧归因采样预算与 supervisor 任务书（docs-only，未执行）

- 切片：worktree `ai-learning-os-worktrees/m14-199-phase1-budget`，分支
  `docs/m14-199-phase1-budget`，基于 main
  `0603c61246487157cf1c9e50860abb65bc3bde53`（PR #287 merge =
  M14-198 Phase 0 收口合入，精确基点）。
- 定性：**纯文档切片**——本文档是 Phase 1（VPS 侧只读归因采样）的
  独立预算与 supervisor 任务书。本切片零采样、零 VPS/SSH/Docker/
  Nginx/frp/DNS/网络操作、零生产变更、零凭据；M14-181 §3 Phase 1
  定义的执行动作在本文档中只是被预算化与精确化，未被启动。

## 1. 授权状态（Phase 1 authorization status）

- **必要条件已满足**：M14-198 收口的 Phase 0 决策为 `phase0_go`
  （reason `slow_window_frequency_ge_go_threshold`，慢窗 3/9≈33.3%，
  决策工具输出 `phase1_sampling_authorized=true`；证据
  `docs/evidence/m14-198-phase0-third-date/README.md` §3）。
- **本文档零授权、零执行**：`phase0_go` 只是必要条件；本文档不构成
  supervisor 对任何 Phase 1 样本的批准——默认计划（§2）与命令清单
  （§3）是"待批准的预算"，reserve 需另行单独授权（§2.3）。截至本文档
  落档：**Phase 1 未启动、未授权任何样本、未执行任何 VPS 侧请求**。
- 启动条件：supervisor 显式批准本任务书（或其修订版）后，按 §3 顺序
  执行；批准记录与执行事实由 supervisor 落档（脱敏规则见 §6）。

## 2. Round A 预算（bounded，硬上限）

### 2.1 上限推导与硬停止

- 约束源：M14-181 §3 预算总原则——分轮采样**每轮 ≤ 40 次**请求
  （Phase 1 沿用分轮口径）；本任务书再加严：**默认计划 ≤ 24 次请求**，
  对齐 Phase 0 每日 24 次形态以便窗级可比。
- 预算单位：一次"边缘终结的 manifest HTTPS 请求"= 1 次（即 §3.3 的
  一次 curl 采样）。`nginx -T`（§3.1）、DNS 查询（§3.2）与
  `openssl s_time`（§3.4）不产生 manifest 请求，各自另有次数硬界
  （见各节），全部只读。
- **硬停止（hard stop）**：默认计划第 24 次请求完成后，无论证据是否
  充分，采样立即终止；预算门对任何追加窗自动拒绝（与 M14-186 决策
  工具对 Phase 0 预算门同语义）。

### 2.2 默认计划（default plan，恰 24 个 manifest 请求）

| 项 | 值 |
| --- | --- |
| 轮次 | Round A（单轮） |
| 窗数 × 样本 | 3 窗 × 8 样本 = 24 个 manifest 请求（窗标识 A1/A2/A3） |
| 窗结构 | 每窗 8 样本、样本间隔 ≥1s、单次超时 15s（对齐 Phase 0 探针契约） |
| 会话形态 | 单个 supervisor SSH 会话内背靠背三窗（诚实边界见 §10.2） |
| 本地终结采样 | 恰 24 次 curl（§3.3） |
| s_time | 恰 1 次、`-time 3` 有界（§3.4；TLS-only，不计入 24） |
| DNS 对比 | ≤3 次只读 A 记录查询（§3.2；0 个边缘请求） |
| nginx -T | 恰 1 次（§3.1；0 个请求） |

### 2.3 Reserve（第 25–40 次的余量）：默认计划之外，另行授权

- 轮内余量 = 40 − 24 = **16 个请求上限**；本文档**授权 0 个 reserve
  请求**。
- 消耗 reserve 的任何执行（补窗、失败重采样、h2 强制参照组等）必须
  先取得 supervisor 单独授权并在证据中落档授权语句，才可执行；未获
  授权时预算门对第 25 次起的请求一律拒绝。
- `openssl s_time` 的任何**追加**运行同样按 reserve 口径处理（默认
  计划只含恰 1 次）。

## 3. 只读命令清单与采集顺序（closed list；执行者 supervisor）

> 顺序固定：§3.1 → §3.2 → §3.3（A1 → A2 → A3）→ §3.4 → §3.5。
> 清单闭合：除本节列出的命令形态外，Round A 不执行任何其他命令
> （§9 非目标与 §8 无变更证明共同依赖该闭合性）。全程**零重试**：
> 任何命令失败按 §5 记录，不自动重跑。

### 3.1 `nginx -T` 事实收集（恰 1 次，0 请求）

```bash
sudo nginx -T
```

记录面——结论摘录是唯一允许入库的 nginx 事实，逐项 checklist：

| 检查项 | 记录内容 |
| --- | --- |
| 443 server 监听形态 | `listen` 行是否含 `http2`/h2 语义 |
| `ssl_protocols` | 启用的协议列表 |
| `ssl_session_cache` / `ssl_session_tickets` | 有无及参数 |
| `ssl_stapling` | on/off |
| `keepalive_timeout` | 值 |
| AIOS 片段 include | `aios-base-path.locations.conf` 是否 include 于 443 server 内、§3E/§3G 八个 location 是否齐备 |
| 证书形态 | 仅记"证书文件引用存在（路径脱敏）"；有效期起止可记 |

禁止项：完整 `nginx -T` 输出、任何非 AIOS 宿主 server 块内容、宿主
站点名/upstream、证书/密钥文件路径明文、任何疑似凭据行。该摘录是
H1/H2 判定与 Phase 2 设计的事实输入（M14-181 §3 Phase 1）。

### 3.2 DNS/解析对比（辅助证据，≤3 次只读查询，0 边缘请求）

```bash
dig +short ndtool.cn A             # 系统默认解析器
dig +short @223.5.5.5 ndtool.cn A  # 公共解析器一（示例，可按 VPS 所在地替换）
dig +short @1.1.1.1 ndtool.cn A    # 公共解析器二（示例）
```

- 记录：各解析器返回的 A 记录答案与 VPS 公网 IP 的一致性结论
  （M14-181 选项 F 的降级形态——解析路径差异只作 H1 的辅助上下文，
  不单独成判定门）。
- 边界：只查 A 记录；不修改任何解析配置；`dig` 查询打到解析器而非
  ndtool.cn 边缘，不计入预算。

### 3.3 VPS 本地终结 manifest 采样（3 窗 × 8 样本 = 24 请求）

基准命令（M14-181 §3 Phase 1 原文，保留主机名/SNI 与证书验证）：

```bash
curl --resolve ndtool.cn:443:127.0.0.1 https://ndtool.cn/aios/download-manifest.json
```

采样形态（每窗重复 8 样本、间隔 ≥1s；`<WID>` 逐窗替换为 A1/A2/A3）：

```bash
for i in 1 2 3 4 5 6 7 8; do
  date -u +%Y-%m-%dT%H:%M:%SZ
  curl --resolve ndtool.cn:443:127.0.0.1 --noproxy '*' -sS -m 15 \
    -o /dev/null \
    -w 'win=<WID> idx='$i' code=%{http_code} ver=%{http_version} ip=%{remote_ip} tcp_s=%{time_connect} tls_done_s=%{time_appconnect} ttfb_s=%{time_starttransfer} total_s=%{time_total} verify=%{ssl_verify_result} bytes=%{size_download} ctype=%{content_type}\n' \
    https://ndtool.cn/aios/download-manifest.json
  sleep 1
done
```

- **禁止削弱验证**：`-k` / `--insecure` 显式禁止；`--ssl-no-revoke`
  同样禁止（沿用 Phase 0 纪律）；禁止退化为裸 IP URL——那会丢 SNI
  并使证书验证失真（M14-181 原文口径）。`--resolve` 把主机钉到回环
  但主机名/SNI/证书链验证全部保留。
- `--noproxy '*'` 仅用于排除 VPS 上可能存在的代理环境变量干扰，不
  改变验证语义；`remote_ip` 应恒为 `127.0.0.1`（否则该样本按 §5
  记为失败）。
- 协议不强制：被动记录协商出的 `http_version`（VPS curl 具备 h2
  能力时该字段揭示宿主 443 是否启用 h2——H5 的免费事实；与 Phase 0
  公网恒 HTTP/1.1 的可比性边界见 §10.4）。
- 期望值：`code=200`、`verify=0`、`bytes` 等于已发布 manifest 字节数
  （M14-179 落档 1392）、`ctype=application/json`。

### 3.4 TLS 握手计时（`openssl s_time`，恰 1 次，`-time 3` 有界）

```bash
date -u +%Y-%m-%dT%H:%M:%SZ
openssl s_time -connect 127.0.0.1:443 -servername ndtool.cn -new -time 3
```

- `-servername` 保留 SNI；`-new` = 逐连接全握手（不复用会话——对齐
  探针/浏览器"每请求新连接"的实际水合形态，检验 H2 的会话复用缺口
  假设）；`-time 3` = 采集窗口硬界 3 秒。
- **证书/安全局限（caveat）**：`s_time` 本身不做完整证书链验证，只测
  握手耗时——其结论**不得用于任何安全判定**；证书验证证据唯一来源
  是 §3.3 样本的 `verify=0`。
- 预算口径：s_time 连接是 VPS 回环 TLS-only 连接，不经公网入站路径
  与 HTTP location 层，不是 manifest 请求、不计入 24；但其**报告的
  连接数必须如实入档**（精确计数披露），且默认计划**只允许恰 1 次**
  运行（追加运行按 §2.3 reserve 授权）。
- 记录：s_time 汇总行（connections / avg 等），不记录冗长原始输出。

### 3.5 台账收口与清理证明（0 请求）

- 预算账：实际请求计数必须等于 24（或更少——中途终止时如实记录
  终止点与原因）；逐窗核对样本数、失败数、畸形数。
- 清理证明按 §8 逐项落档；SSH 会话关闭时间戳记录。

## 4. 计时字段、输出格式与时钟归一

- 计时字段（每样本一行 key=value，来自 curl `-w`，单位秒）：
  `tcp_s`（TCP 完成）、`tls_done_s`（TLS 握手完成）、`ttfb_s`
  （首字节 TTFB）、`total_s`；派生段在记录层计算：**TLS 段 =
  tls_done_s − tcp_s**、**握手后等待段 = ttfb_s − tls_done_s**
  （与 M14-180/181 的分解口径一致）。
- 输出格式：仅上述 key=value 行 + 每窗首尾 `date -u` UTC 锚点 +
  s_time 汇总行 + §3.1/§3.2 结论摘录；不落完整 JSON 报告，不落
  原始 body。
- 时钟归一：全部时长为 curl/openssl 内部计时（时长量，不受墙钟跳变
  影响）；墙钟锚点一律 `date -u`（UTC）记录，并在归档记录头部声明
  VPS 本地时区偏移一次。与 Phase 0（家机 +08:00）对比时**只比窗内
  次序统计量（nearest-rank p50/p95/max），不做跨机墙钟对齐**（观测
  点不同、时钟不同源）。
- 统计口径：沿 M14-181/M14-186——描述性 nearest-rank
  p50/p95/max、失败计数、慢样本频率（TTFB > 2500ms）；24 样本不
  支持 p99 或成功率门。

## 5. 失败与畸形样本处理（零重试）

- **失败样本**（任一命中即失败，计入预算、不重跑）：curl 退出码 ≠0；
  `code≠200`；`verify≠0`；`bytes` ≠ 已发布字节数；`ip≠127.0.0.1`；
  `tcp_s`/`tls_done_s`/`ttfb_s`/`total_s` 任一缺失或为 0（0 表示该
  阶段未完成）。
- **畸形窗**：样本数 ≠8、或窗台账与请求计数不一致；畸形窗不重开。
- 失败/畸形只记录不补救——默认计划内零重试、零重采样（Phase 0 同
  契约）；因失败导致证据不足时走 §7 的 mixed/incomplete 或
  budget-exhausted 分支，补救只能经 §2.3 reserve 授权。

## 6. 证据脱敏规则

- 允许入库：本 README 及其后续修订、§3 各节结论摘录、key=value
  采样行、汇总统计表、命令清单与退出码、DNS A 记录答案、公共事实
  （状态码/字节数/协议版本）。
- 禁止入库：完整 `nginx -T` 输出与任何非 AIOS 宿主站点面（server
  块/upstream/站点名）；证书/密钥文件路径明文；任何
  凭据/token/密码/Cookie 值；VPS 账户/SSH 别名/主机细节；supervisor
  工作站本地绝对路径；s_time 冗长原始输出。
- 原始工件（若 supervisor 落档）只进 gitignored 证据目录
  （`.verify/` 口径），入库面仅结论摘录与统计。

## 7. 决策矩阵（H1 公网入站路径 vs H2 VPS 处理面）

对照基线（Phase 0 已落档，**不新增公网采样**）：公网 direct 9 窗 /
72 样本，TTFB p50 286.648ms / p95 5793.434ms / max 12283.814ms，
慢窗 3/9≈33.3%、慢样本 10/72≈13.9%（M14-198 §3）。

| 结果形态 | 判定条件（本地终结侧） | 结论 | 后续 |
| --- | --- | --- | --- |
| 本地快 + 公网慢 | 本地 TTFB p95 ≤ 300ms 且 TLS 段 p95 与等待段 p95 均 < 300ms（M14-181 Phase 1 门），对照上述公网慢基线成立 | **H1：慢在公网入站路径**（网络 QoS/入站限速等），非 VPS 处理 | 跳过 Phase 2 Nginx 内部调优，直接评估 Phase 3（Cloudflare/多入口，单独立项） |
| 本地也慢 | 本地 TLS 段或等待段 p95 ≥ 1s（M14-181 阈值） | **H2：慢在 VPS 处理面**（accept 队列/TLS 会话复用缺口/keepalive 等） | 进入 Phase 2（首个配置变更阶段：独立切片 + M14-181 §4 前置证明 + supervisor 批准） |
| 混合/不完整 | 样本部分失败/窗畸形，或本地 p95 落在 300ms–1s 灰区，或 TLS 段与等待段方向矛盾 | **无法下 H1/H2 结论** | 如实记录；补救采样仅经 §2.3 reserve 授权；不得擅自扩窗 |
| 预算耗尽 | 24/24 默认预算（或含授权 reserve 至 40 上限）耗尽而证据仍不足以支撑任一结论 | **无 H 结论**（预算硬停止） | 停止；由 supervisor 决定追加授权或关闭该调查线 |

- 判定输入不含任何新的公网侧采样（§9 非目标）；公网侧事实全部引用
  Phase 0 已落档证据。

## 8. 清理（无需清理——全部只读，仍落档证明）

- **无需任何清理动作**：§3 全部命令只读，不产生配置/文件/服务变更，
  也没有临时文件（curl `-o /dev/null`，无落盘中间产物）。
- 落档证明（supervisor 执行记录必须含）：
  1. 各阶段进程终止证明：每窗 8 个 curl 逐样本退出码、s_time 单次
     运行退出码（0 或如实记录的非零值与原因）；
  2. 会话终止证明：SSH 会话正常退出的 UTC 时间戳；
  3. 无文件/配置变更证明：命令闭合性声明（执行面恰为 §3 清单、零
     写入模式命令）、未触碰 `/etc/nginx`、零 `systemctl`/`docker`/
     `frps`/`frpc` 调用（与 §9 非目标一致）。

## 9. 非目标（explicit non-goals，Round A 全程禁止）

- **零 Phase 2 配置变更**：不改任何 Nginx 指令/模板/宿主配置；
- **零 Nginx reload/restart**：不执行 `systemctl reload/restart
  nginx`、不执行 `nginx -s *`；Round A 零配置变更，故也无需
  `nginx -t` 验证环；
- **零 frp/Docker/VPS 服务操作**：不触碰 frps/frpc、不执行任何
  `docker` 命令、不启停任何 VPS 服务；
- **零生产镜像滚动**：家机 API/Web 镜像与生产 env 一概不动；
- **零外部手机测试**：4G/5G 人工验收清单不在本轮范围；
- **零新增公网边缘采样**：Phase 0 预算 72/72 已全额消耗（M14-198
  §3），公网侧不再发任何请求；同时零 DNS 变更、零证书操作、零
  凭据接触。

## 10. 诚实边界

1. 本文档是预算与任务书，不是执行报告：截至落档，Phase 1 零授权、
   零执行、零样本。
2. 默认计划三窗为单会话背靠背形态，只覆盖一个时段的负载状态
   （Phase 0 三窗同形态的对应诚实声明见 M14-198 §4.2）；跨时段覆盖
   需 reserve 授权，本计划不虚称时段代表性。
3. 24 样本只支持描述性结论（nearest-rank p50/p95/max + 频率），
   不设 p99/成功率门（沿 M14-181 统计诚实边界）。
4. 协议差异边界：VPS curl 可能协商 h2（被动记录 `http_version`），
   而 Phase 0 公网观测恒 HTTP/1.1；若本地为 h2，本地-公网差值含
   协议因素，h2 归因需 §2.3 授权的 h2 强制参照组，默认计划不包含。
5. `s_time` 只测握手耗时、不做证书验证（§3.4 caveat）；其结果不用于
   任何安全判定。
6. 本切片 docs-only（本 README + 两处台账）；单 local commit 并推送
   远端分支；supervisor 审查与 remote 发布（PR 开合/合并/release
   门禁）在其后进行。
