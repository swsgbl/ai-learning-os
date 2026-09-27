# 公网边缘部署运维手册（VPS + frp + Caddy + LiveKit/coturn）— M14-153

面向**已批准研究定版**（`docs/PUBLIC_EDGE_DEPLOYMENT_RESEARCH.md`）的落地 runbook。
拓扑：任意手机浏览器 → VPS（Caddy TLS + frps + LiveKit + coturn）→ frp 反向隧道 →
家用 Windows（API/Web/AI/DB/MinIO/搜索/本地语音引擎）。**本仓库只提供模板、
校验与手册，不含任何真实域名/IP/证书/token；未完成真实公网验收前不宣称
公网生产可用。**

- 模板：`infra/edge/`（frps/frpc/Caddyfile/livekit 边缘配置/compose/env/分发清单；
  本地彩排模板在 `infra/edge/rehearsal/`，见 §3C）
- 自动化：`services/api/tests/test_edge_deployment_templates.py`（模板 fail-closed
  校验）、`tools/ops/public_edge_preflight.py`（公网验收 preflight，只打显式端点）、
  `tools/ops/public_edge_rehearsal.py`（本地回环彩排 supervisor，§3C）
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

## 3C. 本地彩排（public_edge_rehearsal，M14-156）

在取得真实 VPS/域名之前，用 `tools/ops/public_edge_rehearsal.py` 在**本机
回环**真实跑通 Caddy → frps → frpc → 家机 API/Web 的 HTTP 反向隧道链路
（模板 `infra/edge/rehearsal/`）。supervisor 精确序列：

```bash
# ① 只读计划（默认子命令；零变更零探测零 Docker）
python tools/ops/public_edge_rehearsal.py plan --work-dir <仓库外彩排目录>

# ② 真实彩排（需同时给 --execute 与精确短语，缺一即 fail-closed 拒绝；
#    前置只读 preflight：Docker CLI/compose 可用、家机 API /health 与
#    Web 入口 200、发布端口空闲、work-dir 契约成立。默认目标端口
#    API 8000/Web 3012（M14-157 生产 Web 回环网关）；本地 stale-port-forward
#    绕行时可加
#    --host-api-port/--host-web-port <1..65535>——见下方"目标端口"契约）
python tools/ops/public_edge_rehearsal.py execute --work-dir <仓库外彩排目录> \
    --confirm-phrase "EXECUTE PUBLIC EDGE REHEARSAL" --execute

# ③ 残留回收（execute 被中断时独立重放；同样需短语 + --execute）
python tools/ops/public_edge_rehearsal.py cleanup [--work-dir <仓库外彩排目录>] \
    --confirm-phrase "CLEANUP PUBLIC EDGE REHEARSAL" --execute

# ④ 只读状态（本项目容器/网络残留 + work-dir 状态）
python tools/ops/public_edge_rehearsal.py status [--work-dir <仓库外彩排目录>]
```

行为契约：

- **拓扑**：probe → Caddy（唯一 host 发布 `127.0.0.1:39443`，HTTP-only）
  → frps vhost（compose 网络内 8080）→ frpc → `host.docker.internal`
  的家机 API(8000)/Web(3012，M14-157 生产 Web 回环网关)；Host 路由
  标签 `app.rehearsal.localhost` /
  `api.rehearsal.localhost`（RFC 6761 保留域，探测用显式 Host 头，无 DNS）；
- **目标端口（M14-158 对齐）**：默认 API 8000 / Web **3012**（M14-157
  生产 Web 回环网关，已验证 200）。既有 3011 host 映射对 Docker
  Desktop 已知 stale（TCP 空应答），**3011 不再作为本机的公网/彩排
  Web 目标**（历史依据见 §3D 与 M14-156 证据注记的 39998 一次性
  绕行记录）。`--host-api-port`/`--host-web-port`（1..65535，畸形/
  越界/相同端口即 `port-invalid` fail-closed）保留作本地
  stale-port-forward 绕行：
  `--host-api-port`/`--host-web-port`（1..65535，畸形/越界/相同端口即
  `port-invalid` fail-closed）**仅**用于一种已文档化的本地场景：Docker
  Desktop 既有 host 端口映射陈旧（TCP 可连但应答为空、生产容器内部
  健康）时，把彩排指到临时健康的 host 端口完成链路验证。host.docker.internal
  与回环语义固定不变（无 host/address/URL 覆写面）；证据显式记录两端口
  与 `defaults_used`——**覆写端口上的通过不构成对默认映射的验证**，
  也不改变生产边缘设计、不构成任何公网就绪声明。入口恒为 39443；
- **隔离**：compose project 恒为 `aios-m14-156-edge-rehearsal`（文件
  `name:` 与 `-p` 双保险），清理只按本项目标签精确过滤——绝不触碰
  `aios-m14-03-production-rehearsal` 或任何其他项目；frps/frpc 之间保持
  token 文件 + TLS force 同生产不变量，token 为每次执行**一次性随机**
  0600 文件，绝不打印/入证据；镜像 digest pin——caddy/frps 与生产同
  digest，frpc 用 frp **官方 GHCR 源**（`ghcr.io/fatedier/frpc`，R3 实跑
  证实本地镜像加速器按白名单拒收 `docker.io/fatedier/frpc` 且直连
  Docker Hub 超时；官方 GHCR 镜像的 OCI index digest 与 Docker Hub
  逐字节一致 `sha256:99ece6a2…`，同一构建产物，非第三方镜像）；
- **work-dir 契约**（确定性）：仓库外、供给路径链中任何已存在组件均非
  符号链接（缺省叶/父目录合法）、非根；已存在时只允许恰为本工具产物名
  （幂等覆写），任何外来条目拒绝——绝不盲写盲删无关目录；
- **证据**：`rehearsal-evidence.json` + `rehearsal-report.md` 原子写（默认
  仓库 gitignored `.verify/artifacts/m14-156-edge-rehearsal/`，可用
  `--evidence-dir` 显式指定仓库外/.verify 下目录）；成败路径都落盘，
  `public_ready` 恒 false；
- 退出码：0 成功 / 1 失败（稳定类别 confirm-gate、work-dir-unsafe、
  evidence-dir-unsafe、**port-invalid**、docker-unavailable、
  target-unreachable、port-busy、render-failed、compose-up-failed、
  probe-failed、cleanup-failed、evidence-write-failed、internal-error）/
  2 用法错误。

诚实边界：本地彩排是 **HTTP-only、loopback-only 的链路验证**——无公网
域名/证书/ACME/LiveKit/coturn，frpc→frps 控制面走 compose 项目网络而非
公网。彩排通过只证明隧道链路与配置不变量在真实容器里成立，**不构成
公网部署、不证明公网生产可用**；公网验收仍走 §9 preflight + 人工清单。

### supervisor 真实彩排证据（M14-156 R4 回填）

supervisor 在 commit `aa35929509a4c742c06fc12279566701726fe601`（R3 后）
真实执行了一次 Docker 彩排并通过：

- 目标端口：API 走默认 `127.0.0.1:8000`；Web 走临时绕行
  `127.0.0.1:39998`（一次性临时 host 映射——既有生产 3011 映射 TCP
  可连但空应答，Web 容器本身健康；彩排结束后该临时映射即弃用）；
  Caddy 入口 `127.0.0.1:39443`；
- 路由探测：`app.rehearsal.localhost/` → 200（经 Caddy→frps→frpc→
  家机 Web）；`api.rehearsal.localhost/health` → 200（同链路到家机
  API）；
- compose 起了 Caddy + frps + frpc 三容器（frpc 镜像为 R3 的官方 GHCR
  digest pin 引用）；scoped 清理移除 3 个容器、1 个网络与彩排
  work-dir；
- 证据事实：`result=pass`、`public_ready=false`、`defaults_used=false`
  （覆写端口上的通过不构成对默认 Web 映射的验证——见 §3C 目标端口
  契约）；证据文件 SHA256：`rehearsal-evidence.json` =
  `D833D8D4DC7239B2174A6FBFFBDEDB51E4E332C317049B8D15C98E12C5267D9E`，
  `rehearsal-report.md` =
  `06501A69404B4656CDC8567B0B2B341658A89168F7B4C4E6551700D7B029CECB`。

边界重申：该证据只证明**本地回环彩排链路**（Caddy→frps→frpc→家机
API/Web）在真实容器中通过；没有真实域名/VPS/DNS/公网手机验收，**不
宣称公网生产可用**。

## 3D. 生产 Web 回环网关（production_web_gateway，M14-157）

生产 Web 容器 `aios-m14-03-production-rehearsal-web-1` 在 compose 网络
`aios-m14-03-production-rehearsal_default` 上健康运行，但既有 host 映射
`127.0.0.1:3011` 对 Docker Desktop 呈 TCP 空应答（API 8000 正常）。
`tools/ops/production_web_gateway.py` 提供稳定、可回滚的**本地回环恢复
入口**：pinned Caddy 容器加入同一生产 compose 网络，`Caddy :80 →
web:3000`，只发布 `127.0.0.1:<host-port>:80`（默认 3012）——不重启/
不重建既有生产栈。

```bash
# ① 只读计划（默认子命令；零写入零探测零 Docker）
python tools/ops/production_web_gateway.py plan
# ② 只读状态（missing/installed/degraded/foreign 分类）
python tools/ops/production_web_gateway.py status
# ③ 真实安装（需 --execute + 精确短语，缺一即 fail-closed）
python tools/ops/production_web_gateway.py install --config-dir <绝对路径仓库外> \
    --confirm-phrase "INSTALL PRODUCTION WEB GATEWAY" --execute
# ④ 回收（只删精确自有网关容器；配置目录与证据保留）
python tools/ops/production_web_gateway.py uninstall \
    --confirm-phrase "UNINSTALL PRODUCTION WEB GATEWAY" --execute
```

契约要点：install preflight 五门 fail-closed（Docker 可用/目标网络存在/
生产 Web 容器 running+healthy/host 端口空闲/config-dir 契约——绝对路径、
仓库外、路径链无符号链接、只允许 Caddyfile 幂等覆写）；同名容器已存在
（无论归属）绝不覆盖；host-port 封锁生产/彩演已知端口集（3011/8000/
39443 及 infra/docker-compose.yml 全部 host 发布位）；装后 inspect 精确
复核 image/labels/network/bind/port/restart/**Caddyfile bind mount**
（install 必须精确核对挂载 source = 渲染产物；status 未给 `--config-dir`
时只校验挂载形状——bind + 只读 + 目标 `/etc/caddy/Caddyfile` + source
basename，输出**明示未精确验证**，绝不虚报）+ `GET /` 必须 200 才报
pass；docker argv 白名单在 **RealRunner 运行时强制**（仅 --version/
network inspect/inspect/run/rm 五前缀可执行，其余 argv 在触及
subprocess 前即拒绝——绝无 stop/restart/recreate 生产容器面；超时/
缺省二进制均无 traceback）；证据 JSON+MD 原子写（默认 gitignored
`.verify/artifacts/m14-157-production-web-gateway/`，显式与默认路径均
过路径链符号链接检查），挂载只记脱敏事实（source basename +
exact_source_verified——**本机绝对路径绝不入证据**），
`production_public_ready=false` 恒不变。

诚实边界：网关是**本地回环恢复路径**——不修复 Docker Desktop 的坏
映射、不暴露任何公网流量、不改变生产边缘设计与公网验收口径；生产
公网 readiness 维持 false。

## 3E. 公共 Web base path（443 `/aios`，M14-159）

公网入口现状（supervisor 云/网络外检实证）：**8443 端口对公网不可达**
（云安全组/防火墙未放行），而 **443 已有可达的公共 HTTPS 服务**。因此
AIOS 公共 Beta 入口定为同域路径 **`https://ndtool.cn/aios`**（443），
不开新端口、不另申请证书。canonical 入口为**无尾斜杠**形态：Next
（`basePath=/aios` 构建）默认 `trailingSlash=false`，在 `/aios` 直接
渲染根页面；斜杠形态 `/aios/` 由边缘 **301 归一化到 `/aios`**（恰好
一跳）后经 canonical 入口到达 Next。

精确公共契约：

| 项 | 值 |
| --- | --- |
| 公共 Web 入口 | `https://ndtool.cn/aios`（无尾斜杠 canonical；`/aios/` 301 归一化到 `/aios`） |
| 公共 API 根 | `https://ndtool.cn/aios/api/v1/`（请求展开 `https://ndtool.cn/aios/api/v1/...`） |
| Web 构建参数 | `NEXT_PUBLIC_BASE_PATH=/aios`（构建期注入，改值必须重建） |
| API base | `NEXT_PUBLIC_API_BASE_URL=https://ndtool.cn/aios`（既有请求代码仍调 `${API_BASE}/api/v1/...`，展开即上表） |

Web 与 API 同域同源（页面 origin 与 API 路径前缀同在 `https://ndtool.cn`
下），跨源 cookie/CORS 不在关键路径；`AIOS_CORS_ORIGINS` 仍按 §8 精确
allowlist 纪律追加 `https://ndtool.cn`。

**边缘路由（Nginx 片段模板 `infra/edge/nginx.public-base-path.example.conf`）**：
该文件是 **location 片段**（非独立 server 块），include 进 VPS 上既有
公共 443 server（即承载 ndtool.cn 站点的那个 server；真实域名按仓库
纪律不入模板，部署副本自填）。片段只含六个 location，结构上不可能
遮蔽既有站点：

| location | 行为 |
| --- | --- |
| `= /~!frp` | frpc WebSocket 入口（既有行为保持）：升级头透传到 frps 控制面 `127.0.0.1:7000` |
| `= /aios` | canonical 入口直接代理：`proxy_pass http://127.0.0.1:8080;`（无 URI）+ Host 改写 `app.internal.aios`——完整 `/aios` 原样交给 Next（basePath 构建下 `/aios` 直接渲染，**零重定向**） |
| `= /aios/` | 301 → `/aios`——斜杠形态归一化到 canonical 无尾斜杠入口（恰好一跳，随后按上一行直接代理） |
| `= /aios/health`（M14-161） | 公共 API 健康端点精确路由：`proxy_pass http://127.0.0.1:8080/health;`（URI 显式 `/health`）+ Host 改写 `api.internal.aios`——转发 frps API vhost 与本地 API `/health`，`X-Forwarded-*` 与 API 前缀路由一致。精确匹配只接整串 `/aios/health`：不遮蔽 Web（`/aios/healthz`、`/aios/health/x` 仍走 `^~ /aios/`）、不遮蔽 `/aios/api/`（`/aios/api/health` 仍走 API 前缀）、不遮蔽既有站点 |
| `^~ /aios/api/` | `proxy_pass http://127.0.0.1:8080/api/;`（尾斜杠 URI）+ Host 改写 `api.internal.aios`——仅剥 `/aios` 一段，`/aios/api/v1/foo` → `/api/v1/foo` |
| `^~ /aios/` | `proxy_pass http://127.0.0.1:8080;`（无 URI）+ Host 改写 `app.internal.aios`——完整 `/aios/...` 路径原样交给 Next（basePath 构建） |

**公共 preflight base path 契约（M14-161）**：同源路径制拓扑下公共
验收端点带 base path——`--app-url https://ndtool.cn/aios`（探测精确
canonical `/aios`，非 `/`）、`--api-url https://ndtool.cn/aios`（健康
探测 `/aios/health`——上表精确路由的 canonical 端点；登录探测拼出
`/aios/api/v1/auth/login`；CORS 应用源恒为 endpoint origin
`https://ndtool.cn`，不含 base path）。preflight 端点解析保持严格
fail-closed：仅接受无 path、`/` 或精确 `/aios`（canonical 无尾斜杠），
白名单外 path（尾斜杠 `/aios/`、深路径、dot segments、编码斜杠/
反斜杠、双斜杠、任意其他前缀）一律入口拒绝；根 origin 端点行为不变
（app 探测 `/`、API 探测 `/health`）。

**公共边缘安全响应头契约（M14-163）**：Codex 用 M14-162 preflight
实测 `https://ndtool.cn/aios`——app-https FAIL（`/aios` 响应缺少
`Strict-Transport-Security`/`X-Content-Type-Options`/`Referrer-Policy`：
上游 Next 未发、边缘未加）。定版修复在边缘片段：除 `= /~!frp`（frpc
隧道控制面，非浏览器响应面）外的五个 location（`= /aios`、
`= /aios/`、`= /aios/health`、`^~ /aios/api/`、`^~ /aios/`）各显式
`add_header ... always` 恒定三头：`Strict-Transport-Security:
max-age=31536000`（≥ preflight 门槛 15552000；HSTS 是 host 级策略，
同 host 的 Web/API/重定向响应一并覆盖）、`X-Content-Type-Options:
nosniff`、`Referrer-Policy: strict-origin-when-cross-origin`。**为什么
逐 location 显式**：nginx 的 add_header 继承规则是"location 内出现
任何 add_header，server 级 add_header 全部失效"——片段 include 进
任意宿主 server，显式声明使安全头不依赖宿主配置；`always` 保证
301/4xx 等非 2xx 响应同样携带。**边界纪律**：不引入
Content-Security-Policy/X-Frame-Options 等内容策略头（不放宽也不
收紧，宿主站点与上游保持权威）；**边缘零 CORS**——不加任何
`Access-Control-*` 头，CORS 判定权在家机 API 的 `AIOS_CORS_ORIGINS`
精确 allowlist（§8），边缘只透传。

重定向语义（R3 修正，supervisor 阻断反馈）：Next 16 默认
`trailingSlash=false`——basePath 构建下 `/aios` 直接渲染、`/aios/`
返回 308 → `/aios`（Codex 在 `aios/web:m14-159-public-edge-beta`
镜像冒烟实证）。边缘若对 `/aios` 反向 301 到 `/aios/` 即构成互逆
重定向环（`/aios/` → `/aios` → `/aios/` 循环）。因此 canonical 入口
为无尾斜杠 `/aios`（直接代理、零重定向），`/aios/` 仅做单向 301
归一化——两个方向不互逆，Next 只被到达一次。

`app.internal.aios` / `api.internal.aios` 是**内部 vhost 路由标签**：仅作
frps vhost（loopback 8080）按 Host 路由到家机 frpc 代理之用（frpc 模板
`customDomains` 已含同名条目），无 DNS 解析、公网不可直达。精确匹配与
最长前缀语义保证 `/`、既有 `/api/v1/` 与既有静态资源仍由站点自身规则
服务。部署时把片段拷到 VPS（如 `/etc/nginx/aios-base-path.locations.conf`），
在既有 443 server 内 `include`，`nginx -t` 通过后 reload。

**家机侧公共 Beta 构建**（`NEXT_PUBLIC_*` 均构建期生效，改 env 必须重建
Web 镜像；若在 Git Bash/MSYS shell 里直接 `NEXT_PUBLIC_BASE_PATH=/aios`
本机构建，MSYS 会把 `/aios` 误转换成 Git 安装目录下的 Windows 路径——
须前置 `MSYS_NO_PATHCONV=1`。Docker `--build-arg` 路径不受影响）：

```bash
# docker build 直接注入：
docker build -f apps/web/Dockerfile --build-arg NEXT_PUBLIC_BASE_PATH=/aios \
  --build-arg NEXT_PUBLIC_API_BASE_URL=https://ndtool.cn/aios ...
# 或 compose（infra/docker-compose.yml）：
AIOS_PUBLIC_WEB_BASE_PATH=/aios \
AIOS_PUBLIC_API_BASE_URL=https://ndtool.cn/aios \
docker compose -f infra/docker-compose.yml --profile local up -d --build web
```

`NEXT_PUBLIC_BASE_PATH` 经 `apps/web/next.config.ts` 结构化校验（空/未设
= 根路径构建完全不变；唯一允许的非空值恰为 `/aios`，尾斜杠/查询串/
片段/其他路径构建期抛错 fail-closed）。全仓导航审计：`Link`/`router.*`
由 Next 自动处理 basePath；唯一绕过点是 `src/lib/api.ts` 的 401
`window.location` 跳转，已改为共用校验后的 `${BASE_PATH}/login`。Next
在 basePath 下自动为静态资源（`/aios/_next/...`）与 rewrite source 加
前缀，acceptance rewrite 写法不变。

**容器 healthcheck 对齐构建形态（M14-160）**：M14-159 生产切换后
supervisor 实证——`aios/web:m14-159-public-edge-beta` 镜像的 `/aios` 与
`/aios/login` 均 200、Next Ready，但 Docker health 一直 **unhealthy**：
compose web healthcheck 固定探测 `http://127.0.0.1:3000/`，而
basePath=`/aios` 构建下 `/` 是 404（canonical 健康路径是 `/aios`）。
修复原则：同一镜像/compose 在 root build 与 basePath build 下都健康，
无密钥、无生产手工特例：

- `apps/web/Dockerfile` run stage 固化**非敏感 runtime env**
  `AIOS_WEB_HEALTH_PATH=${NEXT_PUBLIC_BASE_PATH:-/}`——值唯一来源是
  构建期 build arg（经上述 normalizeBasePath 白名单校验：非法值构建期
  抛错、镜像不产出，故运行时只可能是 `/` 或 `/aios`）；
- compose web healthcheck 改为
  `wget -q -O- http://127.0.0.1:3000$${AIOS_WEB_HEALTH_PATH:-/} ...`：
  `$$` 在 compose 渲染后转义为字面 `$`，由容器内 CMD-SHELL 运行时展开
  镜像 ENV（宿主侧不插值，无注入面；env 未设时 shell 回落 `/`）；
- root 默认行为**逐字不变**（仍探测 `http://127.0.0.1:3000/`，与
  M14-159 之前的固定命令一致）；basePath 构建探测
  `http://127.0.0.1:3000/aios`；
- compose web `environment` 不提供 `AIOS_WEB_HEALTH_PATH` override 入口
  （探测路径与构建形态不可能漂移）；
- RC 冒烟 compose（`infra/docker-compose.rc-smoke.yml`，R1 同步）web
  healthcheck 与生产 compose **逐字一致**（同一 `$$` 转义命令——RC
  冒烟镜像 = root 构建，ENV=`/`，探测行为不变）；生产 compose 的
  LF 归一化 pin 在 `test_rc_smoke_rehearsal.py` `PRODUCTION_FILE_PINS`
  第四次显式更新（`cc735ffc…` → `b003d0fe…`，pin-history 见该文件
  注释），无静默漂移路径。

契约由 `services/api/tests/test_web_healthcheck_base_path.py`（21 项）
锁定：Dockerfile ENV 推导、`$$` 容器内展开、root 默认 `/` 回归锚、
basePath `/aios`、非法值构建期 fail-closed + 纵深防御（绕过构建注入
任意 ENV 也绝不可能命中 canonical 探测 URL）、无 secret/loopback-only。

**范围外不变项**：本地/回环开发构建（base path 为空）、彩排模板
（`infra/edge/rehearsal/`，域名路由制）、Caddy vhost 入口（§3）与
M14-157 回环网关（§3D）行为均不变；本节只新增 443 路径制公共入口。

诚实边界：本节与配套模板/测试是**配置/docs/tests 对齐切片**——不是
公网部署宣告；在 §9 preflight + 人工 4G/5G 清单对该入口全绿之前，
不宣称公共 Beta 生产可用。

## 3F. 公共边缘生产上线与验收记录（M14-160 收口，2026-09-27）

本节是 supervisor 真实执行公共边缘上线后的**证据回填**（docs-only）：
记录部署形态、公网验收矩阵与回滚锚点；上线操作不因本节重演。完整
事实与哈希清单见 `docs/evidence/m14-160-public-edge-closeout/README.md`。

**上线形态**：

| 项 | 值 |
| --- | --- |
| 仓库基点 | main `25bba2cb…`（PR #248 merge；PR 与 main CI 五标准 job 全绿） |
| 生产 Web 镜像 | `aios/web:m14-160-public-edge-beta` = `sha256:9f42d0e5…`（root 与 `/aios` 构建均 healthy） |
| 生产 Web 容器 | `cb32feb1d386…`，running + healthy |
| 生产 API | 未变更：`aios/api:m14-124-production`，容器 `77bb87569d98…` healthy |
| 家机 frpc | 2026-09-27 14:29（本地时间）启动，配置 `<仓库盘>/.aios-public-edge/frpc-ndtool-wss-443.toml`；目标 Web `127.0.0.1:3012`、API `127.0.0.1:8000`（内部路由标签 `app.internal.aios` / `api.internal.aios`，loopback、家机零新增入站） |
| 远端 frps | `aios-frps` PID 335753；loopback 控制端口 7000 与 vhost 端口 8080 在线 |

**边缘配置源更正（supervisor 口径）**：M14-159 工作目录残片
`aios-base-path.locations.conf`（110 字节）无效，**不是部署源**，
任何证据不得如此记载。部署源是仓库 canonical 模板
`infra/edge/nginx.public-base-path.example.conf`（5235 bytes，SHA256
`18B5DDE2…`）——与远端 `/etc/nginx/aios-base-path.locations.conf`
逐字节同哈希。

**远端切换（2026-09-27 19:08 +08 完成）**：生效 sites-enabled 与
sites-available `ndtool` SHA256 `627ca88418d7664f…`；两份备份——
`/root/aios-m14-160-nginx-backup/ndtool.sites-enabled.20260927_1908_m14_160`
（SHA256 `de020c9a…`）与
`/etc/nginx/sites-available/ndtool.backup.20260927_1908_m14_160`
（SHA256 `3cbd1a6b…`）；`nginx -t` 在 reload 前后均通过，reload 成功。

**公网验收（supervisor 实测 + 收口时只读复核一致）**：

| 请求 | 结果 |
| --- | --- |
| `https://ndtool.cn/aios` | 200（HTML 18031 bytes，引用 `/aios/_next/…`） |
| `https://ndtool.cn/aios/login` | 200 |
| 斜杠形态（canonical 入口 + 尾斜杠） | 301 归一化到无尾斜杠 canonical 入口（一跳） |
| `/aios/_next/static/media/…woff2` | 200，`font/woff2` |
| `/aios/api/v1/system/privacy` | 预期认证响应 401，`WWW-Authenticate: Bearer`，含 API `X-Request-Id` |
| `/aios/api/v1/voice/providers` | 预期认证响应 401，同上 |
| 既有站点 `/`、`/health` | 切换前后均 200 |
| 既有 `/api/v1/health` | 切换前后均 404（既有行为，非回归指标） |

401 + `WWW-Authenticate: Bearer` + API `X-Request-Id` 证明公网请求
经 Nginx → frps vhost → frpc → 家机 FastAPI 完整链路（真实后端语义，
非边缘静态应答）。本地验收：3011 与 3012 双端口 `/aios`、`/aios/login`
均 200，API `/health` 200，斜杠归一化生效；恢复 dry-run 与 enforce：
pin 一致 9/9，API/LiveKit/MinIO/Postgres/Redis/Web 全部
healthy/running，healthy 栈跳过 `up`。

**生产 env 与回滚锚**：切换前 env 备份
`<仓库盘>/.verify/m14-160-public-edge/env.production-recovery.before-m14-160.backup`
（SHA256 `211F95AB…`）；当前 `infra/env.production-recovery`
（SHA256 `02F5FC2D…`）。两文件内容均不回显、不入库，只以哈希留证。
回滚 = 恢复 env 备份 + 按 §10 镜像锚点切回旧 tag + 用两份 ndtool
备份还原 Nginx。

**语音状态（委派时点 + followup 终态）**：FunASR managed-running
（PID 45921，`/health` 200，SenseVoice CPU 模型已加载）；CosyVoice
managed-running（PID 45978），委派时点模型加载中、`/health` 503，
**supervisor followup 终态 `/health` 200**（模型
`Fun-CosyVoice3-0.5B-2512` 加载完成，收口时 loopback 8011 只读复核
一致）——健康缺口消除，如实落档；公网语音（TURN/TLS + 真实语音
E2E）就绪口径仍不在本证据范围。

诚实边界：本节是上线后的证据回填——**§9 自动 preflight 对该入口
结构性被阻塞**（`public_edge_preflight.py` 端点解析 origin-only
（Round 4 防凭据加固，带 path 的 `https://ndtool.cn/aios` 入口即被
拒），探测路径固定为 origin 相对路径（`/`、`/health`、
`/api/v1/auth/login`）；传裸 origin 只会探测到既有站点自身——假阳性
零证明力。解除阻塞需工具获得显式 basePath 支持，属独立代码切片），
人工 4G/5G 清单亦未执行，按 §9 纪律仍不写「公网生产可用」；公网
入口持续可用依赖家机 frpc 常驻与 VPS Nginx/frps 存活，验收是时点
证据，不承诺窗口外健康。

**M14-161 更新（阻塞解除路径落档）**：上述工具切片已交付——
preflight 端点接受受控 base path（精确 `/aios`），app 探测 canonical
`/aios`、API 探测 `/aios/health`、登录走 `/aios/api/v1/auth/login`
（§3E/§9 契约）；仓库模板已新增 `location = /aios/health` 精确路由
（§3E 路由表；模板内容自此前进，本节所载收口时点哈希为历史事实）。
**生产侧最终解除仍待 Codex 执行**：远端 Nginx 更新片段并 reload
（使 `/aios/health` 生效）+ 按新版 §9 命令对 `https://ndtool.cn/aios`
跑正式 preflight + 人工 4G/5G 清单——完成前「公网生产可用」口径
维持不变。

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
5. 家机安全边界不变：frpc 只回连 loopback——API `127.0.0.1:8000`，Web
   `127.0.0.1:3012`（M14-157 生产 Web 回环网关；M14-158 起 frpc 模板
   `localPort = 3012`，3011 已知 stale 不再作为公网目标），frpc 只是
   **出站**连接；不开路由器端口映射、不暴露
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
改 env 不重建 = 前端仍指向旧地址；公共 Beta 路径制入口 443 `/aios` 的
`NEXT_PUBLIC_BASE_PATH`/`NEXT_PUBLIC_API_BASE_URL` 精确组合见 §3E）：

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

**公共拓扑 CORS allowlist（M14-163 定版）**：API CORS 由家机
`AIOS_CORS_ORIGINS` **精确 allowlist 判定，边缘只透传**——Codex 用
M14-162 preflight 实测 `https://ndtool.cn/aios` 时 api-cors FAIL：
预检应答缺 `Access-Control-Allow-Origin`（API CORS 中间件已回
`Allow-Methods`/`Allow-Credentials`），根因即**公共 origin 未列入
allowlist**。同源路径制公共拓扑必须在生产 env 显式包含公共 origin：

```text
# 子域制拓扑（每子域一条）：
AIOS_CORS_ORIGINS=https://app.example.com[,https://<其他 origin>]
# 同源路径制公共拓扑（M14-159 §3E，ndtool.cn 以真实公共域名为准）：
AIOS_CORS_ORIGINS=https://ndtool.cn
```

要点：值是**应用源 origin**（scheme + host，无 path、无尾斜杠——与
preflight 的 endpoint origin 语义一致）；改该值是 API 运行时 env 变更
（非构建期），按部署纪律由 supervisor/Codex 在生产 env 落地后重启
API 生效；**边缘（Nginx 片段）绝不做 CORS**——不加任何
`Access-Control-*` 头（M14-163 边缘零 CORS 契约，见 §3E），allowlist
判定权唯一在家机 API。

**Cookie 验收凭据纪律**：preflight 的 `api-cookie` 检查（登录
`Set-Cookie` 的 Secure/SameSite=None/HttpOnly 属性验证）必须提供
`--login-credentials-file`（受控 JSON 文件：`{"username": ...,
"password": ...}`）才可证明——不提供即 FAIL（fail-closed 不跳过）。
凭据文件是**仓库外受控文件**：绝不写入仓库/文档/模板/测试，绝不把
真实凭据值提交进任何 tracked 内容；文件权限最小化，验收后按密钥
轮换纪律处置。

## 9. 公网验收（preflight + 人工）

```bash
# 自动化（只打显式端点；fail-closed；报告 JSON 原子落盘）。
# 公共 Beta 同源路径制拓扑（M14-159 §3E + M14-161）：端点带精确 /aios
# base path——app 探测 canonical /aios、API 健康探测 /aios/health
# （边缘 = /aios/health 精确路由）、登录走 /aios/api/v1/auth/login、
# CORS 应用源恒为 https://ndtool.cn（无 base path）：
python tools/ops/public_edge_preflight.py \
  --app-url https://ndtool.cn/aios \
  --api-url https://ndtool.cn/aios \
  --livekit-url https://livekit.example.com \
  --turn-host turn.example.com \
  --livekit-token-file <token 文件> \
  --login-credentials-file <凭据 JSON> \
  --output preflight-report.json

# 子域 origin 制拓扑（如彩排/未来形态）端点不带 path，行为不变：
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

端点解析契约（M14-161）：仅接受 https + 公网 host + port，可选唯一
受控 base path `/aios`（canonical 无尾斜杠；无 path/`/` 归一为根
origin）。白名单外 path（尾斜杠 `/aios/`、深路径、dot segments、编码
斜杠/反斜杠、双斜杠、任意其他前缀）与 userinfo/query/fragment 一律
入口拒绝且不回显原文；报告只存 canonical 重构值（origin 或
origin+`/aios`）。

检查通过的前置条件（M14-163 口径）：app-https 的三个安全响应头
（HSTS/X-Content-Type-Options/Referrer-Policy）由边缘片段恒定补齐
（§3E，待边缘 reload 生效）；api-cors 通过要求家机
`AIOS_CORS_ORIGINS` 已显式包含公共 origin（§8）且 API 已重启生效
——边缘只透传、不代答 CORS；api-cookie 证明需受控
`--login-credentials-file`（仓库外文件，真实凭据绝不入库）。

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
