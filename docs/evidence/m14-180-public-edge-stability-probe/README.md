# M14-180：公网边缘稳定性探针（工具 + 时点探测证据）

- 切片：worktree `ai-learning-os-worktrees/m14-180-public-edge-stability-probe`，
  分支 `ops/m14-180-public-edge-stability-probe`，基于 main
  `38441625e909599c58627785aa6f147f30ab219d`（PR #270 merge = M14-179
  生产滚动证据合入，精确基点）。
- 定性：**诊断工具切片**——新增只读探针 `tools/ops/public_edge_stability_probe.py`
  与 fake-subprocess 测试套件 `tests/ops/`；不部署、不改任何生产服务、
  不动 app/API 代码。原始探针报告全部留在 gitignored 的
  `.verify/m14-180-public-edge-stability-probe/`，本 README 为唯一入库
  证据文件。
- 背景（动机）：M14-179 最终公共浏览器验收（直连 Chrome）全绿，但更早
  经系统代理的本地探针出现过瞬态 HTTP/2 / MIME 资源错误，且公共 manifest
  水合可超 5 秒（docs/evidence/m14-179-production-rollout-evidence/README.md
  §6）——这些现象当时只被"未采纳"，细节丢失。本切片把每一次尝试的
  事实结构化：状态码、实际 HTTP 版本、分段耗时（DNS/连接/TLS/TTFB/总）、
  字节数、SHA256、Content-Type、失败类别、探针模式。

## 1. 工具契约（fail-closed 摘要）

| 契约 | 实现 |
| --- | --- |
| 只读 | 仅 GET 公开静态资产；无认证/cookie；不跟随重定向；`--noproxy "*"`（直连）或 `-x` 钉扎（代理），永不读代理环境变量 |
| 零重试 | 一个样本 = 恰一次 curl 调用；失败如实记失败类别，测试用"超出脚本的调用即抛错"的假运行器结构性证明 |
| 负载有界 | `--samples` 默认 5 上限 50；`--interval` 默认 1.0s 下限 0.5s（样本间强制间隔）；单请求 `--timeout` 默认 15s |
| 小资产重复 / 大资产至多一次 | 重复采样只打 `--url` 指定的小静态资产；`--large-asset-url/sha256/size` 三参同时提供时在采样循环后**恰执行一次**校验下载（结构上不进入循环） |
| URL 入口校验 | 仅 https + 公网 host（loopback/私网/RFC 5737/2606 占位域全拒）+ 无 userinfo/query/fragment/dot-segment/编码字符；代理 URL 仅 scheme+host+显式 port，userinfo（凭据）入口拒绝 |
| HTTP 版本能力门 | 默认 HTTP/1.1（任何 curl 构建可靠）；显式 `--http-version 2` 先解析 `curl --version` Features，无 HTTP2 特性则**采样前 exit 2**，绝不产生 curl-exit-2（不支持的功能）冒充的传输失败样本 |
| `--ssl-no-revoke` 诊断模式 | 默认关闭；显式开启才透传（Schannel 吊销检查不可达的隔离诊断）；削弱证书校验强度，**只用于诊断不得用于验收**；每样本与 config 显式记录开关状态 |
| 计时口径诚实 | curl 的 time_* 全部是**累计**时间戳（appconnect 含 TCP、starttransfer 含 TLS）；报告保留 `elapsed_ms`/`ttfb_ms` 两个累计值，`dns_ms/tcp_ms/tls_ms/server_wait_ms` 一律相减派生（TLS=appconnect−connect、握手后等待=starttransfer−appconnect），绝不把累计值冒充阶段值；乱序/未到达的时间戳记 None |
| 部分传输记账 | 非零退出（如超时）可能已落部分字节——body dump 存在即记实际 `size_bytes/sha256` 且 `body_complete=false`；失败类别仍以传输错误为准（authoritative） |
| 报告脱敏 | 只含白名单字段；不含本机绝对路径、curl stderr 原文、响应头原文（只记解析后的 Content-Type）、任何 secret |
| Exit codes | 0 = 零失败类别；1 = 已完成但 ≥1 条失败类别（证据照常写出）；2 = 参数非法 / curl 缺失或版本 <7.75 / 请求的 HTTP/2 超出本机 curl 能力 / 运行器或报告写出故障 |

记录字段（每样本）：`index/asset/started_at/probe_mode/http_version_requested/
http_version/ssl_no_revoke/status/elapsed_ms/ttfb_ms/dns_ms/tcp_ms/tls_ms/
server_wait_ms/size_bytes/sha256/body_complete/content_type/remote_ip/
exit_code/failure_category`；汇总：失败类别/状态/实际协议版本分布、SHA
漂移集合、elapsed 与 TTFB 的 min/mean/max。

失败类别（确定性映射）：curl 退出码 → `dns-error/connect-error/timeout/
tls-error/http2-error/http3-error/proxy-dns-error/proxy-error/…`（未知码
回落 `curl-exit-N`）；exit 0 后派生判定 → `http-error-status（≥400）/
http-redirect（3xx，静态资产必须 200 直出）/content-type-mismatch/
byte-count-mismatch（body 实测 vs curl 报告）/size-mismatch/
checksum-mismatch（大资产期望值）/no-response`。

## 2. 本切片的验证（真实执行）

- 单元测试（fake subprocess，零外部网络）：
  `python -m pytest tests/ops` —— **96 passed**（CLI help / 非法输入零请求 /
  有界采样计数与间隔 / argv 形状（直连 vs 代理、1.1 vs 2、超时、
  ssl-no-revoke）/ 退出码→失败类别映射 / 派生类别 / write-out 不可解析与
  exitcode 不一致兜底 / 大资产恰一次与校验 / 序列化原子写与泄漏扫描 /
  curl 版本下限 / **版本行不可识别 fail-closed（run_probe 与 CLI 双路，
  零请求）** / HTTP2 能力门 / **累计计时→阶段派生（正常/无 TLS/乱序/
  样本集成）** / **部分传输字节记账（超时样本 size/sha/body_complete）** /
  汇总统计与 SHA 漂移可见性 / CLI 端到端 exit 0/1/2 与报告落盘）；
- **CI 门禁**：`.github/workflows/ci.yml` release-tools job 已纳入
  `compileall tools/ops/public_edge_stability_probe` 与
  `pytest tests/ops`（本工具的 PR 将被 CI 把关；tests/ops 仅
  pytest+stdlib，无需对外网络）；
- `python -m ruff check tools/ops/public_edge_stability_probe.py
  tests/ops/test_public_edge_stability_probe.py` —— **All checks passed**；
- `python -m compileall tools/ops/public_edge_stability_probe.py
  tests/ops/test_public_edge_stability_probe.py` —— 通过；
- CLI `--help` 冒烟（exit 0）；`git diff --check` 干净；新增行
  secret / 本地绝对路径 / IP / 密码 / token / U+FFFD 扫描 0 真实命中。

## 3. 真实时点探测（2026-09-29，只读，报告在 gitignored `.verify/`）

目标：`https://ndtool.cn/aios/download-manifest.json`（小静态资产，
1392 bytes，发布值 SHA256 `1e2ebb33…f3b5`）。本机传输栈：curl 8.21.0
（mingw64 Schannel 构建，**Features 无 HTTP2**，即 Python
`shutil.which("curl")` 解析到的那支；System32 curl 8.21.0 同样无
HTTP2）→ 按契约 HTTP/2 探测在本机不可靠，全部采样走默认 HTTP/1.1。
对公网边缘总请求数 **15**（direct manifest 9 + proxy manifest 5 + APK 1），
样本间隔 ≥1s。

| 运行 | 模式 | 样本 | 结果 |
| --- | --- | --- | --- |
| run1 `run1-direct-1.1-manifest5-apk1.json` | 直连 1.1 | manifest×5 + APK×1 | manifest **5/5 全绿**（200/1.1/1392B/sha 与发布值一致/application/json；elapsed 151-361ms，mean 252ms）；**APK 单次校验下载 15s 超时未完成**——握手各段全部正常（dns 4ms / tcp 83ms / tls 73ms / wait 88ms / ttfb 248ms），超时纯因 8MB body 传输超出默认超时 → 记 `timeout`，exit 1，**未重试**（v1 工具未记部分字节数，见 §3.5） |
| run2 `run2-direct-1.1-manifest4.json` | 直连 1.1 | manifest×4 | **4/4 全绿**（200/1.1/1392B/sha 一致），但 **TTFB 5.1-10.2s**（elapsed mean 8198ms）——分段定位见下 |
| run3 `run3-proxy-1.1-manifest5.json` | 代理 1.1（`http://127.0.0.1:7892`，本机 sing-box 在线） | manifest×5 | **5/5 全绿**（200/1.1/1392B/sha 一致）；elapsed 263ms-6.5s（mean 2392ms）：dns/tcp 近零（代理本地终结），波动集中在 tls 202-3391ms + wait 60-2456ms，零失败 |
| run4 `run4-http2-capability-gate.txt` | 直连，`--http-version 2` | 0 | **采样前 exit 2**："本机 curl 构建不含 HTTP2 特性"——能力门 fail-fast 实证，零对外请求 |

**核心发现（把 M14-179 §6 的两个"未采纳"现象变成结构化事实）**：

1. **"manifest 水合可超 5 秒"被量化且分解为两个慢段**：run2 四个样本
   TTFB 5112/8407/10249/9021ms（派生阶段口径）：DNS 99-113ms、TCP
   0.02-90ms、remote_ip 恒为 `113.45.64.145`、sha 恒一致——DNS/TCP
   均正常；慢分解为 **TLS 握手段（appconnect−connect）2.36-6.16s** 与
   **握手后等待段（starttransfer−appconnect，服务器处理+首字节）**
   2.55-4.83s。同资产同 IP 与 run1（tls 47-125ms / wait 50-152ms）相隔
   约两分钟相差约一个数量级：**瞬态、非资产内容问题、非 DNS/TCP
   问题，且不止 TLS 一段**。
2. **代理路径零失败**：run3 经系统代理（M14-179 记录出现瞬态 HTTP/2 /
   MIME 错误的同一路径）本次 5/5 全绿、Content-Type 正确——M14-179 的
   瞬态错误未在本次 5 样本窗口内复现，且本机 curl 无 HTTP2 特性，本次
   代理采样实际全程 HTTP/1.1，不具备复现 HTTP/2 瞬态错误的传输条件。
3. **APK 校验下载在默认 15s 超时下不可完成**（当前带宽），fail-closed
   如实记 `timeout` 且不重试；依据 at-most-once 原则本次证据集内不再
   第二次下载（公网 APK 的完整 checksum 复核已由 M14-179 验收落档：
   8029570 bytes / SHA256 `1246c3ef…634d`）。

### 3.5 修正记录（supervisor review 后重算，零新公网请求）

初版（commit 前 review）把 curl **累计** time_appconnect 直接当作
"TLS 段"报告（v1 README 曾写 "TLS 握手段 2.5-6.3s"，实为含 DNS+TCP 的
累计值）。supervisor review 指出后：工具改为相减派生阶段时长
（`derive_phase_timings`，含乱序/未到达 fail-closed 与聚焦测试），
`.verify/` 三份既有报告用其原始累计值**原地重算**（报告内
`timing_recomputed` 注记；未发任何新请求）。重算同时揭示 v1 累计口径
掩盖的事实：慢窗口不止 TLS 段，**握手后等待段（server_wait）同为
2.5-4.8s 大头**。run1 APK 超时样本的部分 body 字节数未被 v1 工具记录
（当时仅 exit 0 才记账）；修正后的工具对部分传输同样记账
（size/sha/body_complete），此事实以边界如实呈现，不回填伪造值。

## 4. 诚实边界

1. **时点证据**：全部探测为 2026-09-29 单次会话窗口内的事实，不构成
   持续可用性或 SLA 保证；公网页面依赖家机 frpc 常驻与 VPS
   Nginx/frps 存活。
2. **HTTP/2 真实采样缺失**：本机两套 curl（mingw64/System32）Features
   均无 HTTP2，工具按契约拒绝 h2 采样（exit 2）——**M14-179 记录的
   瞬态 HTTP/2 错误在本切片中既未复现也无法排除**，其传输前提（h2
   over proxy）在本机不可得；需带 nghttp2 的 curl 环境才能补采。
3. **TLS 与等待段慢只定位到阶段，未定根因**：派生 TLS 段 2.36-6.16s、
   等待段 2.55-4.83s 的成因（边缘负载 / 证书链验证路径 / 中间网络 /
   服务器处理）不在本探针能力范围内，需在 VPS 侧或路径上进一步排查。
4. **APK checksum 未在本切片达成**：单次校验下载超时中止（部分字节
   已传输，v1 工具未记录其字节数），无完整 SHA256 重算值；不违反
   at-most-once（恰一次尝试），但校验目标未闭环，闭环引用 M14-179
   已落档的独立下载复核值。
5. **`--ssl-no-revoke` 诊断模式已实现并有测试覆盖（argv/记录/默认关），
   但本次真实探测未需要开启**（无 Schannel 吊销失败发生），其真实
   诊断效果未验证。
6. 测试为 fake-subprocess 单元/CLI 级；`tests/ops` 已纳入 CI
   release-tools 门禁，但真实传输行为仍以本 README §3 的时点证据为准。
7. 本切片交付为 branch + 单 commit；PR 创建即止；合并决策归 supervisor
   审查（supervisor 审查与 remote 发布在其后进行）。
