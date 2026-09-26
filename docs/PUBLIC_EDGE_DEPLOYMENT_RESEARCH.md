# 公网隧道与边缘部署定版调研（2026-09-26，M14-153 随分支入库版）

> 本文档是 supervisor 批准的研究定版的入库副本。相对原始调研的唯一实质修订：
> LiveKit 与外部 coturn 的联动语法按官方源码核实后改写为
> `rtc.turn_servers[] + secret_file`（原稿的"LiveKit turn.secret"表述对应
> 内嵌 TURN 开关，不适用于外部 coturn 拓扑；详见"语音生产设计"一节与
> <https://github.com/livekit/livekit/blob/v1.13.7/config-sample.yaml>）。
> 落地模板见 `infra/edge/`，运维手册见 `docs/PUBLIC_EDGE_DEPLOYMENT.md`。

## 结论

AI Learning OS 的公网生产入口采用 **VPS 边缘节点 + frp 反向隧道 + Caddy TLS + VPS LiveKit/coturn**。

```text
任意手机浏览器 / PWA / Android / HarmonyOS
    |
    | HTTPS 443
    v
VPS：Caddy + frps + LiveKit + coturn
    |                         |
    | frp HTTP vhost          | WebRTC UDP/TCP 媒体面
    v                         v
家用 Windows：API/Web/AI/DB/MinIO/搜索/本地语音引擎
```

这个拓扑把用户可见入口收敛为普通 HTTPS 域名，把家庭电脑保持为数据和算力中心，同时让 WebRTC 媒体面停留在真实公网 VPS，避免用 HTTP 反向隧道转发实时 UDP 媒体。

## 当前边界

1. 本机生产彩演栈 API/Web 仅绑定 loopback：API `127.0.0.1:8000`，Web `127.0.0.1:3011`。
2. 当前看到的公网出口来自系统代理，不是家用宽带的公网入站地址；不能通过路由器端口映射直接暴露本机。
3. Web 的 `NEXT_PUBLIC_API_BASE_URL` 是构建期注入，公网域名确定后必须重建 Web 镜像。
4. 公网语音必须补齐域名、受信 CA 证书、LiveKit 公网 candidate 与 TURN/TLS。当前 `turn-tls` 仍是 release readiness 的 optional 缺口。
5. Android 只有 debug APK，HarmonyOS 只有 unsigned HAP，均不满足公开分发条件。

## 候选项目对比（决策矩阵）

GitHub 数据核实时间：2026-09-26。

| 项目 | Stars / License | 活跃度 | 判断 |
| --- | --- | --- | --- |
| [`fatedier/frp`](https://github.com/fatedier/frp) | 109,631 / Apache-2.0 | v0.71.0，2026-08-14 发布，2026-09-15 仍有 push | **主方案**。成熟、稳定，支持 HTTP/HTTPS/TCP/UDP、token/OIDC（含 token 从文件读取的 `auth.tokenSource`）、TLS force、HTTP vhost。 |
| `fosrl/pangolin` + `newt` | 22,927 / NOASSERTION；newt 909 / AGPL-3.0 | 2026-09 活跃 | 备选。有身份、资源管理和控制台，但部署面更大，license 需专项审阅。 |
| `jpillora/chisel` | 16,583 / MIT | v1.12.0，2026-08-29 | 轻量备选。适合少量 TCP/UDP 手工隧道，缺少域名路由和管理生态。 |
| `rathole-org/rathole` | 14,260 / Apache-2.0 | 最近有 push，但 release 停在 v0.5.0（2023） | 极简高性能备选，不作为第一生产方案。 |
| `go-gost/gost` | 7,538 / MIT | v3.3.0，2026-08-30 | 功能多、配置面大，审计和安全运维成本高。 |
| `zhboner/realm` | 2,599 / MIT | v2.9.6，2026-08-30 | relay/port forward，不能单独解决家宽 NAT 后的反向公网入口。 |
| `ehang-io/nps` | 34,238 / GPL-3.0 | release 停在 2021，维护停滞 | **排除**。存在已知安全问题，不用于生产。 |
| `boringproxy` | 1,385 / MIT | 2024 后不活跃 | **排除**生产主线。 |
| `ekzhang/bore` | 11,509 / MIT | v0.6.0，2025-06 | 只适合简单 TCP 隧道，不适合公网 API/Web/语音生产。 |
| [Cloudflare Tunnel / `cloudflared`](https://github.com/cloudflare/cloudflared) | 15,916 / Apache-2.0 | 2026-09 活跃 | **Web Beta 方案**。outbound-only、CDN/WAF 便利；匿名公网 UDP/WebRTC 不适合作为生产语音入口，大陆访问质量需实测。 |
| Tailscale Funnel | 36,875 / BSD-3-Clause（客户端） | 2026-09 活跃 | 个人演示/运维方案。公网监听仅 443/8443/10000，不适合匿名手机用户生产入口。 |
| Headscale / NetBird / ZeroTier / OpenZiti | 均活跃 | 活跃 | 私有 mesh / Zero Trust。适合运维通道，不适合任意手机浏览器直接访问。 |

## 域名与端口规划

建议域名拆分：

| 域名 | 用途 |
| --- | --- |
| `app.example.com` | Web/PWA |
| `api.example.com` | API |
| `livekit.example.com` | LiveKit signal/WSS |
| `turn.example.com` | coturn TURN/TLS |
| `download.example.com` | APK/HAP/PWA 安装说明、SHA256、版本清单 |

VPS 建议入口：

| 端口 | 协议 | 用途 |
| --- | --- | --- |
| 80 | TCP | ACME/跳转 |
| 443 | TCP+UDP | Caddy HTTPS：Web/API/LiveKit signal/download（UDP 为 HTTP/3） |
| 7000 | TCP | frps 控制通道；仅允许 frp TLS 认证连接 |
| 7881 | TCP | LiveKit RTC TCP |
| 3478 | TCP+UDP | coturn STUN/TURN |
| 5349 | TCP | TURN/TLS |
| 50000-50099 | UDP+TCP | coturn relay |
| 60000-60100 | UDP | LiveKit media UDP；生产边缘配置需与本机小端口段区分 |

云安全组和 VPS 主机防火墙必须一致。不要暴露 Postgres、Redis、MinIO、SearXNG、Windows 远程桌面或 Docker API。

## frp 配置骨架

以下均为安全模板（入库路径 `infra/edge/frps.toml.example` /
`infra/edge/frpc.windows.toml.example`），真实 token 只进部署 secret 文件，不进 Git。

### VPS `frps.toml`

```toml
bindAddr = "0.0.0.0"
bindPort = 7000
proxyBindAddr = "127.0.0.1"
vhostHTTPPort = 8080

auth.method = "token"
auth.tokenSource.type = "file"
auth.tokenSource.file.path = "/run/secrets/frps_token"

transport.tls.force = true
```

frp 官方支持 token/OIDC，token 可经 `auth.tokenSource`（ValueSource）从文件读取
（与 inline `auth.token` 互斥，文件内容自动去首尾空白；实现见
`pkg/config/v1/{server,client}.go`）；`transport.tls.enable` 自 v0.50.0 起默认
开启，服务端可强制 `transport.tls.force = true`。vhost HTTP 监听随
`proxyBindAddr` 绑定——`127.0.0.1` 即"只有本机 Caddy 能进"。生产建议 token
长度不低于 32 字符。

### 家用 Windows `frpc.toml`

```toml
serverAddr = "<VPS 公网 IP>"
serverPort = 7000

auth.method = "token"
auth.tokenSource.type = "file"
auth.tokenSource.file.path = "D:/AI Learning OS/secrets/frpc_token.txt"

transport.tls.enable = true

[[proxies]]
name = "aios-web"
type = "http"
localIP = "127.0.0.1"
localPort = 3011
customDomains = ["app.example.com"]

[[proxies]]
name = "aios-api"
type = "http"
localIP = "127.0.0.1"
localPort = 8000
customDomains = ["api.example.com"]
```

### VPS `Caddyfile`

```text
app.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8080
}

api.example.com {
    request_body {
        max_size 64MB
    }
    reverse_proxy 127.0.0.1:8080
}

livekit.example.com {
    reverse_proxy 127.0.0.1:7880
}

download.example.com {
    root * /srv/aios-download
    file_server
    header /android/* Cache-Control "public, max-age=300"
}
```

Caddy 负责 443/TLS 和基础限流，frps `8080` 只监听 loopback，由 Caddy 转入。Web/API 请求经 frp 回到家用 Windows。

## 语音生产设计

LiveKit 官方生产要求域名、受信 CA 证书和真实公网 candidate；TURN/TLS 提供最强受限网络兼容性。本项目采用：

1. VPS 运行 LiveKit：`wss://livekit.example.com`，RTC TCP `7881`，media UDP `60000-60100`。
2. VPS 运行 coturn：`turns:turn.example.com:5349`，relay `50000-50099`。
3. 家机 API 仍负责业务与 LiveKit token 签发；媒体面不经 frp。
4. **coturn 联动（语法修订，已对照官方实现核实）**：`COTURN_STATIC_AUTH_SECRET`
   与 LiveKit `rtc.turn_servers[].secret_file` 指向的文件**同值同源**，只进部署
   secret。LiveKit 按 TURN REST 凭据算法（HMAC-SHA1）为每个参与者派发
   `turns:` 用户名/密码（实现见
   [`pkg/service/roommanager.go` 的 iceServersForParticipant](https://github.com/livekit/livekit/blob/v1.13.7/pkg/service/roommanager.go)；
   配置字段 `host/port/protocol/secret/secret_file/ttl` 见
   [`pkg/config/config.go` 的 TURNServer](https://github.com/livekit/livekit/blob/v1.13.7/pkg/config/config.go)）。
   顶层 `turn:` 块是**内嵌** TURN 开关（本部署保持 `turn.enabled: false`，
   启用会与外部 coturn 抢 5349 端口）。
5. 没有有效 TURN/TLS 验收前，不对外声明公网语音生产可用。

对应边缘配置骨架（`infra/edge/livekit.edge.yaml.example`）：

```yaml
port: 7880
bind_addresses: ["127.0.0.1"]
rtc:
  tcp_port: 7881
  port_range_start: 60000
  port_range_end: 60100
  # v1.13.7 官方语义：node_ip（compose 必传 --node-ip <VPS 公网 IP>）只在
  # use_external_ip=false 时生效（use_external_ip takes precedence）
  use_external_ip: false
  # 部署副本按需启用外部 coturn 通告（secret 只进 secret 文件）：
  # turn_servers:
  #   - host: turn.example.com
  #     port: 5349
  #     protocol: tls
  #     secret_file: /run/secrets/turn_secret
turn:
  enabled: false
```

## VPS 与备案选择

1. **正式面向中国大陆用户**：国内云 VPS + 已备案域名。体验最可控，但必须完成 ICP 备案。
2. **快速 Beta/小规模生产**：香港 VPS，无需 ICP，重点实测大陆移动/联通/电信延迟和丢包。
3. **不采用**：依赖当前代理出口或直接开放家庭宽带端口。

最低配置建议：

| 场景 | 建议 |
| --- | --- |
| frp + Caddy + coturn | 2 vCPU / 2GB，带宽优先 |
| frp + Caddy + LiveKit + coturn | 2 vCPU / 2GB 起 |
| 1-3 路并发语音 | 稳定 5-10Mbps 以上 |
| 10 路左右并发语音 | 20Mbps 以上，评估 CPU/流量费用 |

## 生产环境变量

公网切换时必须显式设置并重建 Web：

```text
AIOS_APP_ENV=production
AIOS_AUTH_SECRET=<强随机值>
AIOS_AUTH_COOKIE_SECURE=true
AIOS_AUTH_COOKIE_SAMESITE=none
AIOS_CORS_ORIGINS=https://app.example.com
AIOS_PUBLIC_API_BASE_URL=https://api.example.com
AIOS_PUBLIC_LIVEKIT_URL=wss://livekit.example.com
```

不得使用仓库默认 LiveKit secret。所有 secret 长度不低于 32，只存部署 secret/env，不写入日志。

## 分阶段实施

### Phase 1：VPS 与域名

1. 采购 VPS，准备 `app/api/livekit/turn/download` DNS 记录。
2. 初始化 Docker、防火墙、SSH key、自动安全更新。
3. 部署 Caddy ACME 证书。

### Phase 2：frp 反向通道

1. VPS 部署 frps，token 文件权限 600，强制 TLS。
2. 家用 Windows 以 Windows 服务或受控进程运行 frpc。
3. 本机仅暴露 API/Web loopback，不开放数据库和存储。
4. 验证 `https://app.example.com` 与 `https://api.example.com/health`。

### Phase 3：生产配置重建

1. 更新生产 env 与 Web 构建参数。
2. 重建 API/Web 镜像并滚动替换。
3. 验证跨域 cookie、登录、题库、考试、语音 token 签发。

### Phase 4：公网语音

1. VPS 部署 LiveKit 与 coturn。
2. 配置域名证书、真实公网 candidate、TURN/TLS。
3. 用 4G/5G 手机完成 LiveKit connect 与受限网络 TURN relay 验收。

### Phase 5：移动分发

1. Android 构建 release signed APK，下载站提供 APK、SHA256、版本清单和更新检查。
2. HarmonyOS 完成 AGC 证书与 Profile，生成 signed HAP，走 AppGallery 发布；官网提供引导页。
3. iPhone/其他手机优先 PWA：manifest、icons、service worker、HTTPS 安装体验。

## 验收标准

生产可用必须同时满足：

1. 4G/5G 手机打开 `https://app.example.com`，不依赖用户 VPN/Tailscale/WARP。
2. 登录后跨域 cookie 有效，API 返回 200，无 CORS 错误。
3. 考试计时、交卷、评分、错题复盘全流程可用。
4. `wss://livekit.example.com` 可连接，语音 token 有效。
5. trickle-ice 在受限网络下得到 `relay` 候选，真实语音可用。
6. Android release APK 可下载、校验 SHA256、安装并连通公网 API。
7. HarmonyOS signed HAP/AGC 发布链路可解释、可回滚。
8. VPS 和家机均有日志、监控、备份、回滚演练。

自动化验收面由 `tools/ops/public_edge_preflight.py` 覆盖（HTTPS/证书链/健康/
CORS/cookie/LiveKit WSS/TURN-TLS/安全头 + 人工 4G/5G 清单签认，fail-closed）。

## 需要外部资源

Codex/Claude 可以完成模板、脚本、CI 和手册，但真实上线还必须提供：

1. 一个域名和 DNS 控制权。
2. 一台 VPS 与 SSH 凭据。
3. 中国大陆正式生产或香港 Beta 的选择。
4. Android release keystore。
5. HarmonyOS AGC 发布证书、Profile 与签名材料。
6. `turn.example.com` 的 DNS-01 证书签发通道（云 DNS API 凭据）。

在这些资源落地前，不能宣称已经公网生产上线。
