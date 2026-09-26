# 公网边缘部署运维手册（VPS + frp + Caddy + LiveKit/coturn）— M14-153

面向**已批准研究定版**（`docs/PUBLIC_EDGE_DEPLOYMENT_RESEARCH.md`）的落地 runbook。
拓扑：任意手机浏览器 → VPS（Caddy TLS + frps + LiveKit + coturn）→ frp 反向隧道 →
家用 Windows（API/Web/AI/DB/MinIO/搜索/本地语音引擎）。**本仓库只提供模板、
校验与手册，不含任何真实域名/IP/证书/token；未完成真实公网验收前不宣称
公网生产可用。**

- 模板：`infra/edge/`（frps/frpc/Caddyfile/livekit 边缘配置/compose/env/分发清单）
- 自动化：`services/api/tests/test_edge_deployment_templates.py`（模板 fail-closed
  校验）、`tools/ops/public_edge_preflight.py`（公网验收 preflight，只打显式端点）
- 外部 coturn 组件复用 `infra/coturn/`（M10-05，含 entrypoint fail-closed 校验）
- 官方文档：[frp](https://github.com/fatedier/frp)、
  [Caddy](https://caddyserver.com/docs/caddyfile)、
  [LiveKit](https://github.com/livekit/livekit)、
  [coturn](https://github.com/coturn/coturn)、
  [Let's Encrypt](https://letsencrypt.org/docs/)

## 0. 决策矩阵（研究定版摘录）

| 项目 | 结论 | 依据 |
| --- | --- | --- |
| 反向隧道 | **frp（frps/frpc）** 主方案 | Apache-2.0、v0.71.0 活跃、HTTP vhost + token/TLS 成熟 |
| TLS 入口 | **Caddy**（自动 ACME） | 自动 HTTPS/续期，Caddyfile 即文档 |
| 语音媒体面 | **VPS LiveKit + coturn**，不经 frp | HTTP 反向隧道不适合转发实时 UDP 媒体 |
| 备选 | pangolin/chisel/gost 等不采用 | 部署面/license/维护性劣势（详见研究文档） |
| 排除 | nps（维护停滞+安全问题）、bore（过简）、家庭宽带入站 | 不满足生产边界 |

域名拆分（占位，部署时替换为真实域名）：

| 域名 | 用途 |
| --- | --- |
| `app.example.com` | Web/PWA |
| `api.example.com` | API |
| `livekit.example.com` | LiveKit signal（WSS，经 Caddy） |
| `turn.example.com` | coturn TURN/TLS |
| `download.example.com` | APK/HAP/PWA 引导、SHA256 与版本清单 |

## 1. 外部资源前置（缺一不可开工）

1. **域名与 DNS 控制权**（五个子域 A 记录指向 VPS 公网 IP）；
2. **VPS 与 SSH 凭据**（2 vCPU / 2GB 起；语音并发预算见研究文档）；
3. **部署区域决策**：大陆正式生产（国内云 + ICP 备案）或香港 Beta（免备案、
   需实测三网延迟/丢包）；
4. **TURN/TLS 证书**（`turn.example.com`，DNS-01 签发，见 §6）；
5. **Android release keystore** 与 **HarmonyOS AGC 签名材料**（见
   `docs/MOBILE_DISTRIBUTION.md`）。

## 2. VPS 初始化

```bash
# 以 root 首登后的最小硬化序列（Ubuntu/Debian 为例）
adduser aios && usermod -aG sudo aios
# SSH：禁 root 密码登录、仅密钥（先确认 aios 可密钥登录再重启 sshd）
#   /etc/ssh/sshd_config: PermitRootLogin prohibit-password / PasswordAuthentication no
apt update && apt upgrade -y
apt install -y ca-certificates curl gnupg ufw unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades   # 自动安全更新
# Docker Engine（官方源）：https://docs.docker.com/engine/install/
```

- 时区/NTP 保持默认（证书与日志时间戳依赖正确时钟）；
- **不安装**任何面板/远程桌面；Docker daemon 只听 unix socket（默认），
  绝不暴露 TCP 2375/2376；
- VPS 上**不运行**任何业务数据（数据库/对象存储全部留在家机）。

## 3. 边缘栈部署（infra/edge/）

```bash
# VPS 上（示例部署目录 /opt/aios-edge，git 不入库）
cd /opt/aios-edge
# 从仓库 infra/edge/ 拷贝模板并去 .example 后缀：
cp frps.toml.example frps.toml               # 无需改（token 走 secrets 文件）
cp Caddyfile.example Caddyfile               # 换真实域名 + 运维邮箱
cp livekit.edge.yaml.example livekit.edge.yaml
cp docker-compose.edge.example.yml docker-compose.yml
cp .env.example .env && chmod 600 .env

# secret 文件（单值、600、不入库不入 git）：
install -m 600 /dev/null secrets/frps_token.txt
openssl rand -hex 32 | tee secrets/frps_token.txt >/dev/null
install -m 600 /dev/null secrets/turn.secret
openssl rand -hex 32 | tee secrets/turn.secret >/dev/null
# LiveKit 凭据文件（一行 "<api-key>: <api-secret>"，与家机 API 同值；0600 硬要求）
install -m 600 /dev/null secrets/livekit_keys
printf '%s: %s\n' "<api-key>" "<api-secret>" > secrets/livekit_keys
# .env 内五个必填变量按注释填好（TURN 同源双槽位自检命令见 .env.example）
mkdir -p download turn-tls

docker compose config        # 必填变量缺失/占位未换会直接失败（fail-closed）
docker compose up -d
```

镜像 digest pin（`docker-compose.edge.example.yml` 顶部注释有 tag 对应关系）；
升级 = 显式改 digest 并记录，绝不回退 `:latest`。

## 3A. 部署准备与渲染（public_edge_prepare，M14-154）

推荐路径：用显式 JSON manifest 驱动**校验 + 模板渲染**，再人工部署与验收。
流程恒为：**manifest → `--check-only` → `--render` → 部署（§3-§7）→
preflight（§9）→ 回滚预案（§10）**。渲染 ≠ 部署——工具零服务操作。

1. **manifest**：以 `tools/ops/public_edge_prepare.example.json` 为格式样板
   写真实清单（真实域名/VPS 公网 IPv4/ACME 邮箱/家机端口/仓库外输出目录/
   四个本地 secret 文件路径），并把 `acknowledge_real_inputs` 置 `true`
   确认输入为真。manifest **只允许 secret 文件引用**——内联 secret 值、
   高熵串、占位域（example.com）/RFC 5737 IP/私网地址一律被拒；
   `output_dir` 必须在仓库外（渲染产物绝不落进仓库）。
2. **`--check-only`**（零写入）：校验全部字段 + 四个 secret 文件
   （存在/UTF-8/长度 ≥32/非占位/非符号链接；错误只报类别，不回显路径）。
   附 `--dns-check` 可做只读 DNS 核对（五主机解析必须等于 VPS 公网 IP；
   与部署解耦的显式检查）。
3. **`--render`**：把 Caddyfile / `.env`（compose 变量；TURN secret 是显式
   回填标记，**绝不落值**）/ `frps.toml` / `frpc.windows.toml` /
   `PREFLIGHT.md`（含真实端点的验收命令 + 人工清单）原子写进 `output_dir`
   （同目录 temp+replace，0600/0644）。渲染自审计：任一产物含 secret 内容
   或高熵串即中止。同一 manifest 渲染结果字节确定（可重复校验）。
4. **部署**：把产物拷到 VPS/家机对应位置（§3 拷贝步骤用产物替代手抄模板），
   按 §4-§7 完成 DNS/ACME/防火墙；`.env` 中 TURN secret 在 VPS 上按
   §6 同源清单回填。
5. **preflight**：按产物里的 `PREFLIGHT.md` 命令执行（工具本体见 §9），
   人工 4G/5G 清单逐项签认后才可宣称公网生产可用。
6. **回滚**：预案见 §10；渲染产物本身可随时重出（确定性），回滚不依赖
   渲染目录存活。

## 3B. 部署操作包与 frpc 控制器（M14-155，可交接）

渲染之后、真实部署之前，用两个零依赖工具把配置推进到"可交接操作包"：

```bash
# ① 封包：校验五产物 + 生成 SHA256SUMS 与交接说明 DEPLOYMENT_PACKAGE.md
python tools/ops/public_edge_package.py inspect --dir <渲染目录>
python tools/ops/public_edge_package.py seal    --dir <渲染目录>
# ② 交接后复核（fail-closed：哈希漂移/占位回落/安全不变量破坏即非零）
python tools/ops/public_edge_package.py verify  --dir <包目录>

# ③ 家机 frpc 常驻：预检 + 计划（零写入零网络；frpc.exe verify 只计划不代跑）
python tools/ops/frpc_windows_controller.py preflight --frpc-exe <exe> --config <渲染 frpc.windows.toml>
python tools/ops/frpc_windows_controller.py plan      --frpc-exe <exe> --config <渲染 frpc.windows.toml>
# ④ 真实安装/卸载（supervisor 审查计划后；需精确短语 + --execute，缺一即 dry-run）
python tools/ops/frpc_windows_controller.py install   --frpc-exe <exe> --config <cfg> \
    --confirm-phrase INSTALL-AIOS-EDGE-FRPC [--execute]
python tools/ops/frpc_windows_controller.py uninstall --confirm-phrase INSTALL-AIOS-EDGE-FRPC [--execute]
```

纪律（与 M14-06 windows_startup_task 同源）：

- 包工具：目录必须在仓库外；条目恰为预期集（渲染后五件/封包后七件）；
  拒绝符号链接/多余条目/占位回落（*.example.com、私网/文档段 IP、
  TURN secret 落值）；seal 字节确定（同一渲染恒产同一封包文件）；
- frpc 控制器：install 前必先只读查询，任务名 `AIOS-Edge-FRPC` 已存在
  （无论归属）一律拒绝；uninstall 只删 Description 精确等于
  `urn:aios:m14-155:edge-frpc-controller` 的自有任务；绝不枚举/触碰任何
  其他计划任务；token 文件只做存在性/结构校验（0600/UTF-8/长度），内容
  绝不读取或展示；
- 两个工具都不 SSH、不上传、不启停任何服务——真实命令（上传产物、
  secrets 就位、compose up、DNS 变更、schtasks 执行）全部由 supervisor
  在取得真实资源后按 DEPLOYMENT_PACKAGE.md 清单执行。

退出码：包工具 `inspect/seal/verify` —— 0 成功 / 1 检查失败（fail-closed）/
2 用法错误；frpc 控制器 —— 0 成功（含 dry-run 计划输出）/ 1 预检失败或
拒绝执行 / 2 用法错误，`status` 专用 0=installed / 1=unknown / 2=missing /
3=foreign / 4=malformed。

## 4. DNS 与 Caddy ACME

1. DNS 控制台添加五条 A 记录 → VPS 公网 IP（TTL 先 300 便于调试，稳定后调大）；
2. Caddy 启动后自动经 **HTTP-01/TLS-ALPN** 为四个 HTTPS 站点申请 Let's Encrypt
   证书（80/443 必须先放行，见 §7）。证书落在 `caddy-data` 卷，重建容器不丢；
3. 验证：`docker compose logs caddy | grep -i 'certificate obtained'`；
4. **不要**为公网站点配置 `tls internal`（自签）——模板测试已禁用该形态。

注意：`turn.example.com` 的证书**不是** Caddy 签的那张——TURN/TLS 由 coturn
自行终结，证书经 DNS-01 单独签发（见 §6）。

## 5. frp 反向通道

### 5.1 VPS 侧（frps）

`frps.toml`（模板即最终态）要点：

- `bindPort = 7000` 公网控制面：**token 从文件读**
  （`auth.tokenSource.type = "file"` → `/run/secrets/frps_token`，compose
  secrets 挂载）+ `transport.tls.force = true`（拒绝一切非 TLS 连接）；
- `vhostHTTPPort = 8080` 只绑 `proxyBindAddr = "127.0.0.1"`——唯一调用方是
  本机 Caddy（`reverse_proxy 127.0.0.1:8080`），公网不可直达。

### 5.2 家用 Windows 侧（frpc）

1. 下载 frp release（与 frps 同版本，官方
   <https://github.com/fatedier/frp/releases>），解压 `frpc.exe` 到固定目录；
2. `frpc.windows.toml.example` → `frpc.windows.toml`：填 VPS 公网 IP；
   token 文件 `D:/AI Learning OS/secrets/frpc_token.txt`（内容与 VPS
   `frps_token.txt` 同值，NTFS 权限仅当前用户）；
3. 配置自检：`frpc.exe verify -c frpc.windows.toml`（解析失败/字段非法会
   非零退出）；
4. **以 Windows 服务或计划任务常驻**（二者择一，均以最小权限账户运行）：
   - 计划任务：开机触发 + 失败重启，动作
     `frpc.exe -c <绝对路径>\frpc.windows.toml`（仓库既有
     `tools/ops/windows_startup_task.py` 的注册模式可参考）；
   - 或 NSSM 包装为服务（`nssm install aios-frpc ...`，Stderr 日志落盘）；
5. 家机安全边界不变：API/Web 仍只绑 loopback（`127.0.0.1:8000` /
   `127.0.0.1:3011`），frpc 只是**出站**连接；不开路由器端口映射、不暴露
   Postgres/Redis/MinIO/SearXNG/远程桌面。

### 5.3 验证

- VPS：`docker compose logs frps`（看到 frpc login 成功）；
- 公网：`curl -I https://api.example.com/health`（经 Caddy→frps vhost→frpc→家机 API）。

## 6. 公网语音（LiveKit + coturn，Phase 4）

> 边界：没有有效 TURN/TLS 与 4G/5G 真实验收前，**不对外声明公网语音生产可用**
> （release readiness 的 turn-tls 门保持 optional 缺口口径）。

1. **LiveKit**（VPS，host 网络）：
   - signal 7880 只绑 loopback，`wss://livekit.example.com` 经 Caddy；
   - RTC TCP 7881 + 媒体 UDP 60000-60100 公网直连；`--node-ip` 显式通告
     VPS 公网 IP（compose 必填 `AIOS_EDGE_VPS_PUBLIC_IP`）。配套
     `rtc.use_external_ip: false`——v1.13.7 官方 config-sample 明示
     "use_external_ip takes precedence, for this to take effect, set
     use_external_ip to false"：显式 `node_ip` 只在该开关为 false 时生效，
     VPS 公网 IP 是确定值，显式通告优于 STUN 自动探测；
   - API 凭据走**部署密钥文件**（官方 `--key-file` flag，见
     [`cmd/server/main.go`](https://github.com/livekit/livekit/blob/v1.13.7/cmd/server/main.go)
     与[配置参考的 `key_file`](https://github.com/livekit/livekit/blob/v1.13.7/config-sample.yaml)）：
     文件一行 `<api-key>: <api-secret>`，与家机 API 的
     `LIVEKIT_API_KEY/LIVEKIT_API_SECRET` 同值（家机签 token / VPS 验
     token）。LiveKit 对 key 文件强制 0600（可被 other 读即拒绝启动）、
     凭据为空/缺失即拒绝启动（"one of key-file or keys must be
     provided"）；compose 侧 `:?` 必填 + 挂载显式 `mode: 0600`。**刻意
     不用 `--keys`/env 形态**——那会把 secret 暴露进进程参数
     （`docker inspect`/`ps` 可见），测试已禁止该形态回归；
   - **外部 TURN 通告语法（已对照官方源码核实）**：`rtc.turn_servers[]` +
     `secret_file`（LiveKit 按 TURN REST 凭据算法 HMAC-SHA1 派发
     `turns:` 用户名/密码）。**不使用**顶层 `turn:` 块描述外部 coturn——
     那是内嵌 TURN 开关（本部署 `turn.enabled: false`）。见
     <https://github.com/livekit/livekit/blob/v1.13.7/config-sample.yaml>。
2. **coturn**（VPS，复用 `infra/coturn/entrypoint.sh` 全部 fail-closed 校验）：
   - TURN secret 与 LiveKit `secret_file` **同值同源**（`.env.example` 内置
     五步同源部署清单 + `test "$(cat ...)" = "$AIOS_EDGE_COTURN_TURN_SECRET"`
     自检）；
   - TURN/TLS 证书：`turn.example.com` 经 **DNS-01** 签发（80/443 在 Caddy
     手里，HTTP-01 不可用）——acme.sh/lego/certbot-dns-* 任选，deploy 到
     `./turn-tls/{cert.pem,key.pem}`（600），自动续期任务就绪后置
     `AIOS_EDGE_COTURN_TLS_ENABLED=true`；证书 CN 必须与
     `rtc.turn_servers[].host` 一致；
   - 端口：3478 UDP+TCP（STUN/TURN）、5349 TCP（TURN/TLS）、
     relay 50000-50099 UDP+TCP——与 LiveKit 60000-60100 错开（测试锁定）。
3. 家机 API 生产 env 同步指向公网语音面（见 §8），LiveKit token 由家机签发。

## 7. 防火墙与云安全组（两层一致）

| 端口 | 协议 | 用途 | 来源 |
| --- | --- | --- | --- |
| 80 | TCP | ACME/跳转 | 任意 |
| 443 | TCP+UDP | Caddy HTTPS（含 HTTP/3） | 任意 |
| 7000 | TCP | frps 控制面（TLS+token） | 任意（家机出口 IP 可收紧） |
| 7881 | TCP | LiveKit RTC TCP | 任意 |
| 60000-60100 | UDP | LiveKit 媒体 | 任意 |
| 3478 | UDP+TCP | coturn STUN/TURN | 任意 |
| 5349 | TCP | TURN/TLS | 任意 |
| 50000-50099 | UDP+TCP | coturn relay | 任意 |

- **明确不暴露**：Postgres(5433)、Redis(6379)、MinIO(9000/9001)、
  SearXNG(8878)、Docker API(2375/2376)、SSH 换非默认端口或限源 IP；
- VPS `ufw` 与云厂商**安全组**两层都要配置且保持一致（安全组漏配是最常见事故）；
- 家机侧不开任何入站（frpc 为出站隧道）；
- 变更端口必须同步改 Caddyfile/livekit 配置/安全组/模板测试
  （`test_edge_deployment_templates.py` 端口矩阵锁定）。

## 8. 生产 env 与 Web 重建（家机）

公网切换时**显式设置并重建 Web**（`NEXT_PUBLIC_API_BASE_URL` 是构建期注入，
改 env 不重建 = 前端仍指向旧地址）：

```text
AIOS_APP_ENV=production
AIOS_AUTH_SECRET=<强随机值，>=32 字符，绝不复用仓库开发占位>
AIOS_AUTH_COOKIE_SECURE=true
AIOS_AUTH_COOKIE_SAMESITE=none
AIOS_CORS_ORIGINS=https://app.example.com
AIOS_PUBLIC_API_BASE_URL=https://api.example.com
AIOS_PUBLIC_LIVEKIT_URL=wss://livekit.example.com
AIOS_BIND_IP=127.0.0.1          # 维持 loopback：公网入口在 VPS
AIOS_LIVEKIT_API_KEY/SECRET     # 与 VPS 边缘 LiveKit 同值
```

```bash
# 家机：重建 API/Web 镜像并滚动替换（镜像 tag 锚点，支持回滚）
docker compose -f infra/docker-compose.yml --profile local up -d --build
# 验证：https://api.example.com/health 200；Web 首页可登录；跨源 cookie 生效
```

fail-closed 提醒：`AIOS_BIND_IP` 非 loopback 时 API 启动强制生产校验；
CORS 不得放宽为 `*`（preflight 断言精确回显）；cookie 非 Secure/SameSite=None
组合会被 preflight 判 FAIL。

## 9. 公网验收（preflight + 人工）

```bash
# 自动化（只打显式端点；fail-closed；报告 JSON 原子落盘）：
python tools/ops/public_edge_preflight.py \
  --app-url https://app.example.com \
  --api-url https://api.example.com \
  --livekit-url https://livekit.example.com \
  --turn-host turn.example.com \
  --livekit-token-file <token 文件> \
  --login-credentials-file <凭据 JSON> \
  --output preflight-report.json
# exit 0 = 自动+人工全过；1 = 有 FAIL；3 = 自动全过但人工清单待签认
```

人工 4G/5G 清单（脚本会输出同款清单；用 `--mobile-attested-file` 签认）：
真实蜂窝网络打开入口、跨源 cookie/CORS、考试全流程、语音连接、受限网络
relay 候选、Android APK/Harmony PWA。**在本清单全绿之前，任何文档/状态
不得写"公网生产可用"。**

## 10. 监控 / 备份 / 回滚

### 监控

- VPS：`docker compose ps` + 各服务 healthcheck（caddy 2019 admin、livekit
  7880）纳入现有巡检节奏；frpc 断线由 frps 日志与公网 `/health` 失败暴露；
- 家机：既有生产监控栈（`tools/ops/production_monitor.py` 等）不变；
- 证书：preflight 的 cert-chain 检查含剩余 <14 天 FAIL——纳入例行执行。

### 备份

- **家机**（数据面）：沿用既有备份演练（Postgres/MinIO），**VPS 不存业务数据，
  无需业务备份**；
- VPS 侧仅备份配置态：`/opt/aios-edge`（去掉 .env 与 secrets/ 的模板态 +
  secrets 文件离线保管）与 Caddy 证书卷（`caddy-data`）——丢失后果是重签
  证书与重发 token，无数据损失。

### 回滚

| 层 | 回滚动作 |
| --- | --- |
| Web/API 镜像 | 家机 `AIOS_IMAGE_TAG=<旧tag> up -d --no-build`（既有锚点机制） |
| 边缘 compose | `docker compose down && git checkout <旧模板> && up -d`（配置态在 /opt/aios-edge 版本化保管） |
| Caddy 证书 | 自动续期自愈；异常时清 `caddy-data` 重签（注意 ACME 限流） |
| DNS | TTL 300 期间可快速切回旧入口/摘除公网暴露 |
| 全量下线 | `docker compose down`（VPS）+ 停 frpc 服务（家机）→ 回到纯本机拓扑 |

## 11. 变更纪律

- 模板/端口/域名结构变更 → 先改 `infra/edge/` + 同步
  `test_edge_deployment_templates.py` 断言，再动部署目录；
- 任何 secret 泄漏嫌疑 → 立即轮换（frps token / TURN secret / LiveKit 凭据
  / AIOS_AUTH_SECRET），轮换按 §3/§6 重新走同源自检与 preflight；
- 本手册与研究文档（`docs/PUBLIC_EDGE_DEPLOYMENT_RESEARCH.md`）互为引用，
  事实变更两边同步改。
