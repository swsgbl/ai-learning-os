# M14-200：Phase 1 Round A 仪表缺陷事故落档与 Round B 修正任务书（docs-only，零新采样）

- 切片：worktree `ai-learning-os-worktrees/m14-199-phase1-budget`，分支
  `docs/m14-200-phase1-incident`，基于 main
  `12e31ad1098c6eedbf145b855927c3fca584a5d6`（PR #288 merge =
  M14-199 任务书合入，精确基点；合并后 main CI 5/5 通过）。
- 定性：**纯文档事故落档**——如实记录 Phase 1 Round A 因 curl 仪表
  缺陷而 instrumentation-invalid/incomplete 的事实、预算账与根因，
  并落档有界的 Round B 修正任务书。本切片零 VPS/SSH/网络/生产
  访问、零新采样、零代码变更、零凭据接触。
- 事实口径：§1–§5 与 §9 的事实由 supervisor 提供并经本地日志审计
  核实（gitignored raw log，见 §6）；本文档只记录、不重新解读。

## 1. Round A 事实时间线

1. PR #288（M14-199 Phase 1 预算与任务书）合并入 main，合并后
   main CI 5/5 通过。
2. supervisor 于其后**单次**启动 M14-199 Round A 脚本（gitignored，
   位于 canonical checkout `.verify/m14-199-phase1-vps-attribution/
   round-a/run-round-a.sh`；原始输出同目录 `round-a.raw.log`）。
   本次是该脚本的唯一一次运行，**无任何重试**。
3. 脚本按 M14-199 §3 固定顺序推进：`nginx -T` 段执行恰 1 次 →
   DNS 段执行恰 3 次 `dig`（系统默认 + 两个公共解析器）→ 进入
   采样窗循环。
4. 采样段实际推进：A1 窗 8 样本全部执行；A2 窗执行至 idx 1 后，
   supervisor 目视检测到仪表缺陷，**中断 SSH 进程**；A3 窗未开始；
   `openssl s_time` 段未执行；脚本收尾锚点（`END_ROUND_A`）未打印。
5. 本地日志审计计数（如实落档）：

| 审计项 | 计数 |
| --- | --- |
| `SAMPLE_START` 标记 | 9 |
| `CURL_RC` 标记 | 9 |
| 成功 manifest HTTPS 响应（`code=200`） | 9（A1 idx 1–8 与 A2 idx 1） |
| 畸形格式行（`win=%s` 字面未展开） | 27（其中 18 行 `code=000`，见 §4） |
| 窗口标识解析失败（stderr 显式记录） | A1 = 8、A2 = 1 |
| 窗开始（`BEGIN_WINDOW_*`，剔除容器行） | 2（A1、A2） |
| 窗结束（`END_WINDOW_*`） | 1（仅 A1） |

## 2. Round A 判定：instrumentation-invalid / incomplete

- **不构成有效窗集**：3 窗结构未完成（A1 8/8、A2 1/8、A3 0/8）；
  全部 27 行 `-w` 输出的 win/idx 标签均为字面 `%s` 未展开，成功样本
  的窗归属只能靠 stdout 交错次序重建，不满足 M14-199 §4 的输出
  格式契约。
- **不计算 H1/H2**：Round A 产生零可用归因证据（显式声明见 §7）；
  9 行成功计时数据不参与任何 p50/p95/max 统计、不与 Phase 0 基线
  对照、不进入 M14-199 §7 决策矩阵任何分支。
- **预算已实耗**：尽管证据无效，9 个 manifest 请求已发出并消耗
  预算（§3）——无效证据不退预算。

## 3. 预算精确核算（M14-199 §2 口径，逐笔）

| 项 | 值 |
| --- | --- |
| Round A 轮预算上限（M14-181 分轮口径） | 40 个 manifest 请求 |
| 默认计划授权 | 24（3 窗 × 8 样本） |
| reserve 授权（第 25–40 次，上限 16） | 0（M14-199 §2.3） |
| Round A 实际消耗 manifest 请求 | **9**（每样本恰 1 个，全部 `code=200`） |
| 消耗后默认计划余量 | 24 − 9 = 15（第 10–24 次） |
| Round B 新授权（本文档 §8.1 落档） | 恰 **24** 个新 manifest 请求 |
| —— 其中默认计划余量 | 15（第 10–24 次） |
| —— 其中 reserve | 9（第 25–33 次；reserve 上限 16 内） |
| **轮内总计** | 9 + 24 = **33 ≤ 40**（硬上限内） |
| Round B 后轮内余量 | 7（第 34–40 次），未授权，预算门拒绝 |

- 不计入 manifest 预算的项（各自口径见 §4）：额外 URL 的 DNS 解析
  尝试（非 HTTPS、未发出任何 HTTP 请求）、`nginx -T` 1 次（0 请求）、
  `dig` 3 次（打到解析器）、`s_time` 0 次（未执行）。

## 4. 请求分类学（request taxonomy，Round A 实际发出的全部流量）

| 类别 | 计数 | 计入 manifest 预算 | 性质 |
| --- | --- | --- | --- |
| manifest HTTPS 请求（`--resolve` 本地终结，M14-199 §3.3 意图内） | 9 | 是（9） | 全部 `code=200` |
| 额外 URL 解析尝试——窗口标识值（如 `A1`/`A2`） | 9 | 否 | 仪表缺陷产物；stderr 显式 "Could not resolve host"（A1×8、A2×1） |
| 额外 URL 解析尝试——索引值（如 `1`–`8`） | 9 | 否 | 仪表缺陷产物；`code=000` 失败尝试，具体错误形态未逐条审计 |
| `nginx -T` | 1 次 | 否（0 请求） | 只读配置 dump；完整输出仅在 gitignored raw log（§6） |
| `dig` A 记录查询 | 3 次 | 否（打到解析器） | 只读；答案内容不在本切片复制 |
| `openssl s_time` | 0 | — | 未执行（中断先于该段） |

- 18 次额外 URL 解析尝试（窗口标识值 9 + 索引值 9）全部失败
  （`code=000`）：**零个非预期 HTTP 请求实际发出**；但额外 URL 形态
  本身已超出 M14-199 §3 的闭合命令清单（§5）。
- 9 份 manifest 响应 body 因 `-o` 缺陷泄入会话 stdout（进入 raw
  log）——这是证据卫生问题（§6），不是额外请求。

## 5. 根因（root cause）

实际脚本中每样本的 curl 形态（结构性引用，沿 M14-199 §3.3 公开
命令面）：

```sh
curl --resolve ndtool.cn:443:127.0.0.1 --noproxy '*' -sS -m 15 \
  -o /dev/null \
  -w 'win=%s idx=%s code=%{http_code} …（其余字段同 M14-199 §3.3）…' \
  "$win" "$i" \
  https://ndtool.cn/aios/download-manifest.json
```

三重机制叠加：

1. **格式串语法错位**：curl `-w` 只识别 `%{var}` 变量，不识别
   printf 风格 `%s`——`win=%s idx=%s` 被原样字面输出（27 行全部
   畸形的直接来源）。
2. **位置参数被当 URL**：curl 把所有非选项位置参数视为 URL，
   `"$win"`（如 `A1`）与 `"$i"`（如 `1`）成为 URL1/URL2，manifest
   退为 URL3——这产生了对窗口标识/索引值的额外非 HTTPS DNS 解析
   尝试。
3. **`-o` 只作用于第一个 URL**：单个 `-o /dev/null` 被 URL1 消耗，
   URL3（manifest）的响应 body 输出到会话 stdout，混入采样输出。

与 M14-199 §3.3 样板的偏离：样板形态 `'win=<WID> idx='$i' …'`
（单引号分段，`$i` 由 shell 展开、`<WID>` 逐窗字面替换）本身正确；
实际脚本改写为 printf 风格 `%s` + 位置参数传值，引入全部三项缺陷。

后果定性：意图命令全部只读、未发出非预期 HTTP 请求、零配置/服务
变更；但闭合命令清单被畸形仪表违反（§9），且证据格式契约破裂
（§2）。缺陷自第一样本即存在，靠 supervisor 目视在 A2 idx 1 后
发现并中断——这推动 §8.3 的窗首 canary 预检设计（把同类缺陷的
预算暴露面从 9 个样本压到 1 个）。

## 6. 证据脱敏规则（本事故落档的入库面）

- 允许入库：本 README、§1–§5 与 §9 的计数/时间线/根因/预算账、
  命令形态的结构性描述、公共事实（状态码/字节数/协议版本类）。
- **禁止入库（永不）**：
  1. 完整 `nginx -T` 输出与任何宿主 server 块/upstream/站点名
     （该输出在 raw log 中，体积占其大部）；
  2. 证书/密钥文件路径明文、任何凭据/token/密码/Cookie 值；
  3. VPS 账户/SSH 别名/主机细节、VPS 公网 IP、supervisor 工作站
     本地绝对路径；
  4. 9 份 manifest 响应 body（因 `-o` 缺陷进入 raw log；即使该
     端点公开可取，本切片也不复制）；
  5. raw log / 脚本原文——原始工件只留 gitignored
     `.verify/m14-199-phase1-vps-attribution/round-a/`，入库面仅
     审计计数与结论摘录。
- 本 README 已按上述规则撰写；后续引用本事故只引计数与结论，
  不引原始工件。

## 7. 无 H1/H2 结论声明（显式）

- **Round A 零 H1/H2 结论**：本地终结侧证据无效（§2），M14-199
  §7 决策矩阵在本轮无输入，任何分支（H1/H2/混合/预算耗尽）均
  未被触发或支持。
- Phase 1 归因判定状态：**未决**——待 Round B（§8）按 M14-199
  §7 矩阵判定，或由 supervisor 关闭该调查线。
- 防误读条款：任何后续文档不得引用 Round A 的 9 行计时数值作为
  归因或性能证据；Round A 在台账中的合法用途仅限预算消耗事实
  （9/40）与本次事故教训。

## 8. Round B 修正任务书（有界授权 + 修正仪表）

### 8.1 授权语句（M14-199 §2.3 reserve 口径的单独授权落档）

- 本节授权由 supervisor 在 M14-200 切片委托中给出、由本文档落档：
  **Round B 允许恰 24 个新的干净 manifest 请求**（3 窗 × 8 样本），
  在 Round A 已消耗 9 个之后，轮内总计 33 ≤ 40。
- 构成：默认计划余量 15（第 10–24 次）+ reserve 9（第 25–33 次；
  reserve 上限 16 内）——即 M14-199 §2.3 要求的"单独授权 + 落档
  授权语句"就此满足，数额恰 24、不多付。
- **除上述 24 个外，Round B 授权 0 个其他 manifest 请求**；第 24
  个新请求完成后硬停止；轮内余量 7（第 34–40 次）未授权，预算门
  一律拒绝。
- 语义边界：本文档是预算授权落档，**不是启动指令**——Round B 的
  实际执行仍待 supervisor 审查本文档合并后的显式启动决定（与
  M14-199 §1 同语义）。

### 8.2 采样契约（沿 M14-199 §3.3/§4/§5 全部纪律，逐项重申）

| 项 | 值 |
| --- | --- |
| 窗结构 | 3 窗 × 8 样本（窗标识 **B1/B2/B3**，与 Round A 的 A 系标识区分） |
| 样本间隔 | ≥1s（`sleep 1`） |
| 单次超时 | 15s（`-m 15`） |
| 重试 | **零重试、零重采样**；失败/畸形只记录（M14-199 §5 定义逐字沿用） |
| 验证削弱 | `-k` / `--insecure` / `--ssl-no-revoke` 显式禁止；禁止裸 IP URL |
| 终结与主机语义 | `--resolve ndtool.cn:443:127.0.0.1` + `--noproxy '*'`；主机名/SNI/证书链验证全部保留；`remote_ip` 必须恒 `127.0.0.1`，否则该样本记失败 |
| 期望值 | `code=200`、`verify=0`、`bytes` = 已发布 manifest 字节数（M14-179 落档 1392）、`ctype=application/json` |
| 统计口径 | nearest-rank p50/p95/max + 慢样本频率，24 样本不支持 p99/成功率门（M14-199 §4） |

### 8.3 修正后的 curl 形态（强制，逐字采用）

```sh
curl --resolve ndtool.cn:443:127.0.0.1 --noproxy '*' -sS -m 15 \
  -o /dev/null \
  -w "win=${win} idx=${i} code=%{http_code} ver=%{http_version} ip=%{remote_ip} tcp_s=%{time_connect} tls_done_s=%{time_appconnect} ttfb_s=%{time_starttransfer} total_s=%{time_total} verify=%{ssl_verify_result} bytes=%{size_download} ctype=%{content_type}\n" \
  https://ndtool.cn/aios/download-manifest.json
```

执行前与运行中 checklist（逐项核对，任一失败不得开窗/立即终止）：

1. `-w` 格式串用双引号：win/idx 的值由 shell 在格式串**内**展开；
   格式串内**禁止出现 `%s`**（脚本落档后 grep 预检：curl `-w` 串
   不得含字面 `%s`）。
2. URL 恰好 **1 个**且位于 `-o /dev/null` 之后；curl 行不得再带
   任何其他位置参数（`"$win"`/`"$i"` 一类传值一律禁止）。
3. 窗标签以字面量注入循环（`for win in B1 B2 B3`），不做参数
   传递；脚本先过 `sh -n` 语法检查。
4. **窗首 canary**：B1 idx 1 执行后立即检查——该行必须匹配
   `win=B1 idx=1 code=200` 形态且该样本段 stdout 无 body 输出；
   canary 失败 → 立即终止整个 Round B 并如实落档（零重试），把
   仪表缺陷的预算暴露面压到 1 个请求。
5. 每样本断言 `ip=127.0.0.1`（沿 M14-199 §3.3）；`verify=0` 是
   证书验证的唯一证据来源。

### 8.4 辅助命令（nginx -T / DNS / s_time）：默认零，严格需要才单列

- **`nginx -T`：0 次**。Round A 已执行恰 1 次（M14-199 §3.1 的
  "恰 1 次"授权已消耗），且 Round A 全程零配置/服务变更（§9），
  该次收集的事实仍然有效；Round B 不授权任何 `nginx -T`。
- **DNS（`dig`）：0 次**。Round A 已用满 M14-199 §3.2 的 ≤3 次
  授权；Round B 不授权任何 `dig`。
- **`openssl s_time`：默认 0 次**。M14-199 §3.4 的恰 1 次授权未被
  Round A 消耗（中断先于该段）；仅当 Round B 采样显示 TLS 段偏慢
  且 supervisor 在启动决定中显式要求独立握手计时交叉验证时，才按
  该既有授权执行**恰 1 次**（`-connect 127.0.0.1:443 -servername
  ndtool.cn -new -time 3`，报告连接数如实披露；s_time 不做证书
  验证、结论不用于安全判定）；未显式要求则为 0。
- 结论：**Round B 默认闭合清单 = 仅 §8.3 的 24 次 curl**；任何
  追加命令必须在启动决定中显式列出并给精确次数，否则预算门与
  闭合性双双拒绝。

### 8.5 执行后落档要求

- 预算账：恰 24（或如实记录的中途终止点与原因）；逐窗核对样本
  数、失败数、畸形数。
- 输出面：key=value 采样行 + 每窗 UTC 锚点；不落完整 JSON 报告、
  不落原始 body（M14-199 §4 格式）。
- H1/H2 判定：**仅以 Round B 的 24 样本为输入**，沿 M14-199 §7
  决策矩阵执行（Round A 的 9 行永不参与，§7）；Phase 0 公网基线
  引用不变、不新增公网采样。
- 清理证明：沿 M14-199 §8（只读零清理 + 进程/会话终止证明）；
  SSH 会话正常退出时间戳必须落档（与 Round A 的中断形态对照）。

## 9. Round A 无变更与清理声明

- **零配置/文件/服务变更**：Round A 全部意图命令只读；实际执行
  面除畸形仪表引入的 DNS 解析尝试（零 HTTP 请求发出）外无任何
  写操作；零 `systemctl`/`docker`/`frps`/`frpc`/`nginx -s` 调用，
  未触碰 `/etc/nginx`。
- **无需任何清理动作**：curl `-o` 意图形态为丢弃输出（body 泄漏
  仅入 gitignored 会话日志，无落盘生产文件）；nginx -T / dig 只读。
- 如实记录：SSH 进程被 supervisor **中断**终止，非脚本正常收尾
  （`END_ROUND_A` 锚点未打印）；中断时间点在 A2 窗 idx 1 之后。

## 10. 非目标（explicit non-goals）

- M14-199 §9 全部沿用：零 Phase 2 配置变更、零 Nginx
  reload/restart、零 frp/Docker/VPS 服务操作、零生产镜像滚动、零
  外部手机测试、零新增公网边缘采样、零 DNS 变更、零证书操作、
  零凭据接触。
- 本切片附加：零 VPS/SSH/网络访问、零新采样（Round B 未启动，
  §8.1）、零代码变更。

## 11. 诚实边界

1. 本 README 是事故落档与修正任务书，不是执行报告；截至落档，
   Round B 未授权启动、未执行。
2. §1 审计面为本地 raw log 的标记计数（grep 级核实）；raw log
   原文永不入库，计数结论以本 README 为准。
3. 9 行成功样本的计时数值未在本 README 复现——它们已被判定为
   无效窗集（§2），复制只会诱导误用（§7 防误读条款）。
4. Round A 的 `nginx -T` 段/DNS 段虽已执行，其结论摘录是否已由
   supervisor 提取落档不在本切片事实面内；本切片不复制任何
   nginx/DNS 内容（§6）。
5. Round B 三窗仍是单会话背靠背形态，只覆盖一个时段的负载状态
   （M14-199 §10.2 时段代表性限制对 Round B 同样适用）；24 样本
   只支持描述性结论。
6. 本切片 docs-only（本 README + 两处台账）；单 local commit 并
   推送远端分支；supervisor 审查与 remote 发布（PR 开合/合并/
   release 门禁）在其后进行。
