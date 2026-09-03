# 外部 coturn（TURN/STUN）部署模板 — M10-05

面向**对称 NAT / 严格防火墙**场景的可选语音中继组件。默认不部署——主栈
`infra/docker-compose.yml` 的 local/hybrid/cloud 任一 profile 渲染都**不含 coturn**；
本模板是独立 compose 文件，只有显式执行启动命令才会运行。

- 模板位置：`infra/coturn/`（compose + fail-closed entrypoint + 变量/配置示例）
- 自动化测试：`services/api/tests/test_coturn_template.py`（渲染、fail-closed、端口
  冲突、secret 不入库；`AIOS_COTURN_SMOKE=1` 门控本机监听冒烟）

## 1. 什么时候需要它

WebRTC 语音默认走 ICE/STUN 直连（LiveKit 内置 ICE，媒体面 UDP 7882-7892）。以下
情况直连会失败，需要 TURN 中继：

- 对称 NAT（mapping 依赖目标地址，STUN 打洞得到的映射不可复用）；
- 防火墙只放行 TCP/443 类出站，UDP 全禁；
- 企业网络策略拦截直连媒体流。

**判断方法**：浏览器开 <https://icetest.livekit.io>（或 LiveKit 内嵌连接测试），若
`UDP: Direct` 失败而 `TURN/RELAY` 可行，即需部署本模板。

## 2. fail-closed 设计（两层，模板不给「能起来但不安全」的默认值）

| 防线 | 位置 | 拦截内容 |
| --- | --- | --- |
| 第一层 | compose `:?` 插值 | `COTURN_STATIC_AUTH_SECRET` / `COTURN_EXTERNAL_IP` 缺失 → `docker compose config/up` 直接失败，无默认值兜底 |
| 第二层 | `infra/coturn/entrypoint.sh` | 占位/常见弱值/仓库开发占位 secret；secret 长度 < 32；external-ip 为占位或 loopback（生产）；relay 段与主栈 LiveKit UDP 7882-7892 同机冲突；启用 TLS 但证书文件不存在（**不做假 TLS 声明**） |

关键取值均**显式**，不允许静默猜测：

- `external-ip` 必填（coturn 对外通告地址；1:1 NAT 用 `PUBLIC/PRIVATE` 形式）；
- relay 端口段显式（默认 **50000-50099**，与 LiveKit UDP 7882-7892 错开；范围跟随
  env，compose 端口映射同源插值，不会出现「配置与映射不一致」）；
- listening 端口显式（默认 3478 UDP+TCP；TLS 端口默认 5349 TCP，仅
  `COTURN_TLS_ENABLED=true` 时 coturn 才真正监听）；
- secret 只经部署 env（`.env`（不入库）或部署 secret 注入）进入容器，运行时生成
  `/tmp/aios-turnserver.conf`（600 权限；官方镜像以非 root 运行，`/run` 不可写）；
  仓库内 `turnserver.conf.example` 只是**不含 secret** 的
  展示示例。

`COTURN_CONFIG_ONLY=true` 可让 entrypoint 只打印将生成的配置（secret 脱敏）并退出，
用于部署前排障与自动化测试。

## 3. 部署步骤

### 3.1 前置条件

1. 一台有**公网 IP** 的宿主（或与 LiveKit 同机的双栈宿主，见 3.4 冲突检查）；
2. LiveKit 生产联动需要**域名 + 有效 TLS 证书**（浏览器 `turns:` 连接会校验证书 CN
   与 LiveKit `turn.domain` 一致）——没有域名/证书就只部署非 TLS TURN，且**不要**打开
   LiveKit 的 `turn.enabled`（见第 4 节）；
3. 防火墙放行（默认端口）：

| 端口 | 协议 | 用途 | 放行范围 |
| --- | --- | --- | --- |
| 3478 | UDP + TCP | STUN/TURN listening | 客户端可达（公网部署=任意源） |
| 5349 | TCP | TURN/TLS listening（仅启用 TLS 时有服务） | 同上 |
| 50000-50099 | UDP + TCP | relay 媒体中继段 | 同上 |
| 出方向 | UDP + TCP 任意目标端口 | relay 转发到对端/LiveKit | 默认出站即可 |

4. 云安全组/宿主防火墙两层都要放行（安全组常被遗漏）。

### 3.2 准备变量

```bash
cd infra/coturn
cp .env.example .env          # .gitignore 已忽略 .env
openssl rand -hex 32          # 输出填入 COTURN_STATIC_AUTH_SECRET（与 LiveKit turn.secret 共用）
# 编辑 .env：COTURN_EXTERNAL_IP=<宿主公网 IP>；其余按需（默认值见 .env.example 注释）
```

**生产建议**：镜像 pin 具体版本（`COTURN_IMAGE=coturn/coturn:4.6.2`），relay 段按
并发放大（每路媒体占 1 个 relay 端口/方向；100 端口 ≈ 数十路并发语音）。

### 3.3 （可选，LiveKit 联动必需）启用 TLS

1. 取得覆盖 TURN 域名（如 `turn.example.com`）的证书（Let's Encrypt 等）；
2. 在 `docker-compose.coturn.yml` 取消 `tls` 卷挂载注释，证书/私钥放 `infra/coturn/tls/`
   （文件名与 `COTURN_CERT_FILE`/`COTURN_PKEY_FILE` 默认值一致，或改 env）；
3. `.env` 置 `COTURN_TLS_ENABLED=true`。

entrypoint 会校验证书/私钥真实存在，缺失即拒绝启动——不会出现「声明了 TLS 但
端口上没有 TLS 服务」的假声明形态。

### 3.4 启动与启动后验证

```bash
# 渲染检查（缺必填变量会在这里直接失败——预期行为）
docker compose -f infra/coturn/docker-compose.coturn.yml config >/dev/null && echo OK

# 启动（独立 project：ai-learning-os-coturn，与主栈互不影响）
docker compose -f infra/coturn/docker-compose.coturn.yml up -d

# 看启动日志（verbose=true 时可见 listening/relay 绑定明细）
docker compose -f infra/coturn/docker-compose.coturn.yml logs coturn
```

协议级验证——STUN Binding 探测（UDP 3478 有真实响应即服务在听）：

```bash
python - <<'PY'
import secrets, socket, struct
txid = secrets.token_bytes(12)
req = struct.pack('!HHI', 0x0001, 0, 0x2112A442) + txid
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
s.sendto(req, ('<COTURN_HOST>', 3478))
data, _ = s.recvfrom(2048)
assert data[:2] == b'\x01\x01' and data[8:20] == txid, data[:20].hex()
print('STUN binding response OK (type=0x0101, txid matched)')
PY
```

> 端口冲突提醒：与主栈 LiveKit 同机部署时，relay 段绝不可与 UDP 7882-7892 重叠
> （entrypoint 已硬检查，跨机部署可显式 `COTURN_ALLOW_LIVEKIT_PORT_OVERLAP=true` 豁免）。

## 4. 与 LiveKit / API 生产环境配合

数据流（**API 无需任何改动**）：

```
浏览器 --LiveKit token--> LiveKit signal(join)
                          LiveKit 按 server config turn 段下发 iceServers
                          （turns:<domain>:5349 + LiveKit 用 turn.secret 现算的
                           TURN REST 时间受限凭据）
浏览器 --turns(TLS)--> coturn（static-auth-secret 同一 secret 校验 HMAC 凭据）
浏览器 <==relay==> coturn <==媒体==> LiveKit
```

在**部署副本**（不提交 secret 入库，例如经部署环境覆盖 `AIOS_LIVEKIT_CONFIG` 指向的
文件）的 livekit 配置追加：

```yaml
turn:
  enabled: true
  domain: turn.example.com   # 必须解析到 coturn 宿主；证书 CN 匹配该域名
  tls_port: 5349             # 与 COTURN_TLS_PORT 一致
  secret: <与 COTURN_STATIC_AUTH_SECRET 同值——只进部署 env/覆盖文件，绝不入库>
```

注意：

- **不要设 `udp_port`**——那是 LiveKit 内嵌 TURN 服务器的开关；外部 coturn 方案下
  设了会双开 TURN、无意义且占端口；
- `turn.secret` 与 `COTURN_STATIC_AUTH_SECRET` 必须同值：LiveKit 按该 secret 现算
  TURN REST 凭据（username=过期时间戳，password=base64(HMAC-SHA1(secret, username))），
  coturn `static-auth-secret` 同算法校验，浏览器端零配置；
- 无域名/TLS 证书时**保持 `turn.enabled: false`**（仓库默认）。此时 coturn 仍可独立
  服务于自建测试客户端，但 LiveKit 不会（也不应）向浏览器通告非 TLS TURN 地址——
  这是如实边界，不是缺陷；
- secret 轮换：两端（coturn env 与 LiveKit turn.secret）同窗口更新即可，凭据是
  时间受限的，旧凭据自然过期。

## 5. 验证（按拥有资源分级，不虚报可用性）

### 5.1 无公网资源（只能验证「配置正确 + 服务在听」）

- 渲染/fail-closed/端口冲突/secret 不入库：`python -m pytest services/api/tests/test_coturn_template.py -q`；
- 本机隔离监听冒烟（loopback external-ip + 随机测试 secret + 仅绑 127.0.0.1，不碰
  主栈与生产库）：
  `AIOS_COTURN_SMOKE=1 python -m pytest services/api/tests/test_coturn_template.py -k smoke -q`；
- **边界声明**：以上不构成「TURN 可用」验收——未验证公网中继、未验证 TLS、未验证
  真实 NAT 穿透。

### 5.2 有公网 IP/域名/证书（真实可用性验收）

1. **trickle-ice（真实浏览器，先单独验 coturn）**：算一组 REST 凭据——
   ```bash
   python -c "import base64,hashlib,hmac,time,sys;u=str(int(time.time())+600);print(u, base64.b64encode(hmac.new(sys.argv[1].encode(),u.encode(),hashlib.sha1).digest()).decode())" '<COTURN_STATIC_AUTH_SECRET>'
   ```
   打开 <https://webrtc.github.io/samples/src/content/peerconnection/trickle-ice/>：
   - 非 TLS：URI `turn:<公网IP>:3478?transport=udp`（或 `?transport=tcp`），填入上面
     输出的 username/credential，Add Server 后应出现 `relay` 类型候选；
   - TLS：URI `turns:<域名>:5349?transport=tcp`，同样应得 `relay` 候选。
2. **LiveKit 联动 + 真实语音链路**：按第 4 节配置 LiveKit turn 段并重启 LiveKit，
   真实浏览器进入语音考试页发起会话，确认能建立连接；再跑
   `AIOS_MODE=public AIOS_PUBLIC_HOST=<域名> bash infra/smoke_voice.sh` 验证
   token 鉴权 → Room.connect → CONN_CONNECTED → 数据通道。
   注意：smoke_voice 验证的是 LiveKit 链路整体可达；「流量确实经 TURN 中继」要在
   **对称 NAT/禁 UDP 出站**的客户端环境（如手机热点/企业网）里用 trickle-ice 出
   `relay` 候选 + 通话成功来证明——正常网络下浏览器优先直连，不经 TURN 是预期行为。

## 6. 安全注意事项

- **别把旧版加固选项加回部署副本**：当前 `coturn/coturn` 镜像已移除
  `no-tlsv1`/`no-tlsv1_1`/`no-loopback-peers`/`no-cli`（写入只产生
  `Bad configuration format` 告警）；新版本默认等价防护——旧 TLS 禁用、loopback
  relay 目标拒绝、未设 `cli-password` 时 CLI 关闭。模板生成配置只保留仍有效的
  `no-multicast-peers`（已按容器实测校准）；
- **凭据**：static-auth-secret 模式下没有静态用户表；secret 泄露=任何人可中继，
  务必只存部署 secret 管理，长度 ≥32（entrypoint 强制），定期轮换；
- **中继滥用/open relay**：`lt-cred-mech` + secret 凭据已挡未授权使用；如需进一步
  限制 relay 目标段（防 TURN 被当作内网跳板/SSRF），在部署副本中加
  `denied-peer-ip=<内网段>`（注意会同时挡住「经 TURN 中继回内网 LiveKit」的路径，
  按拓扑取舍）；
- **资源**：并发大时放大 relay 段并加 `total-quota`/`user-quota`（部署副本）；
- **日志**：排障才开 `COTURN_VERBOSE=true`，长期开启会记录会话明细。

## 7. 常见故障

| 症状 | 排查 |
| --- | --- |
| `docker compose config/up/down/logs` 直接报 required variable | 预期 fail-closed：`:?` 插值先于一切子命令，`down`/`logs` 也需要必填变量在 env 中——把 `.env` 补齐或在命令前带上，见 3.2 |
| 容器启动即退出，日志 `FAIL-CLOSED` | 按 stderr 提示修改变量（占位 secret/loopback IP/端口冲突/缺证书） |
| trickle-ice 无 `relay` 候选 | 防火墙或安全组未放行 3478/relay 段；`turns:` 时查证书域名与 `turn.domain` 一致 |
| 语音仍走不通但 trickle-ice 有 relay | LiveKit `turn.secret` 与 coturn secret 不同值，或浏览器到 turn 域名 DNS/证书问题 |
| 与主栈同机部署端口相撞 | relay 段/监听端口落在 7882-7892——entrypoint 已拒绝；换 50000+ 段 |

## 8. 验收边界（截至 M10-05 交付）

- **Claude 已自测**：compose 渲染与 fail-closed 矩阵、entrypoint 校验矩阵、端口冲突
  检查、secret 不入仓库扫描，以及本机隔离监听冒烟（`AIOS_COTURN_SMOKE=1`：随机测试
  secret + loopback 豁免 + 仅绑 127.0.0.1 + 独立 compose project，容器真实启动、
  STUN Binding 请求得到 0x0101 响应且 transaction id 匹配、结束 down 清理）——全部
  在隔离环境完成，未连接生产库、未触碰主栈服务。
- **未覆盖（需生产环境人工验收）**：真实公网 IP/域名/证书下的 TURN/TLS 中继验收、
  真实对称 NAT 环境浏览器语音经 TURN 的端到端验证、LiveKit `turn.enabled` 生产联动
  实测。模板交付不等于 TURN 可用性已验收。
