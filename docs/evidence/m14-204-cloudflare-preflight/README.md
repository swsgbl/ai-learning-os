# M14-204：Cloudflare/公网入口 E1 前置事实核验（docs-only / research-only）

- 切片：worktree `ai-learning-os-worktrees/m14-204-cloudflare-preflight`，
  分支 `docs/m14-204-cloudflare-preflight`，基于本地 `0dd8434`
  （M14-203 merge commit；远端 main 指向该 commit、PR #292 已合并由
  supervisor 经 GitHub API 核验，本切片依纠正意见未自行 fetch）。
- 定性：**纯文档核验**——把 M14-203 §10 假设清单中 E1 前置相关条目
  （A1/A2/A4/A5/A6/A7/A11）核验为当前官方文档事实，A8/A9/A10 拒绝
  清单做简要现状复核。本切片零请求（生产 manifest 请求预算 0）、
  零 DNS/Cloudflare/VPS/Nginx/Docker/生产服务变更、零凭据接触、
  零 Phase 3 执行授权；一切执行仍待 supervisor 另行立项批准
  （门禁沿 M14-203 §7，不变）。
- 输入：M14-203 证据 README（§2 事实分级、§10 假设清单）+
  M14-198/199/202 既有落档证据（经 M14-203 §1 引用）+ 本切片在线
  抓取的官方文档原文（§2/§4）。

## 1. 范围与零执行声明

- 范围：仅读取官方文档、仓库文件与公共网页；未调用 Cloudflare API、
  未登录任何控制台、未读取或输出任何凭据/token；未对生产 manifest
  发起任何请求（本轮预算 0，实际 0）；现有 apex/直连 DNS 记录未动；
  未修改 DNS、VPS、Nginx、Docker、生产服务、代理与任何工程文件。
- 交付：本 README + docs/CHANGELOG.md 与 docs/PROJECT_STATUS.md 的
  最小台账记录；单 local commit；supervisor 审查与 remote 发布
  （PR 开合/合并/release 门禁）在其后进行。

## 2. 方法、工具形态与来源抓取时间

- 抓取窗口：**2026-10-01 00:13–00:22 UTC（+08:00 08:13–08:22）**。
  所有引用均给出 URL；文档页自身的 "Last updated" 日期随引文记录。
- 工具形态（如实）：本机 WebFetch 因域名安全校验服务不可达而全部
  失败（本机外网受限，与 github 直连失败同因；未做网络诊断、未改
  代理/CC Switch，依 supervisor 纠正）；改用服务端网页抓取工具
  （web-reader / firecrawl）读取公开官方页面原文。跨工具缓存时点
  差异如实记录：firecrawl 对 Origin CA 旧 URL 的 404 判定来自其
  2026-09-30T23:34Z 缓存副本，其余页面为抓取时点直取；
  web-reader 无缓存时点元数据，以本切片抓取窗口为准。
- 证据等级沿 M14-203 §2 纪律：只看是否官方文档原文与是否抓取
  成功；抓取时间与工具形态不作等级依据。

## 3. 逐项核验结论

### A1 Origin CA 证书（M14-203 假设：默认 15 年、仅边缘信任）

**结论：主体核验通过；"默认 15 年"未获现行文档证实，保留未核验。**

- **旧 URL 已失效（新事实，解释 M14-203 不可达）**：
  `https://developers.cloudflare.com/ssl/origin-configuration/origin-ca-certificate/`
  返回 404——该页已迁移。M14-203 §2 记录的"抓取不可达"实为页面
  迁移而非临时网络故障。
- **新 URL（抓取成功，页面 Last updated 2026-08-14）**：
  `https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/`
- **信任边界（官方原文）**："These certificates only encrypt traffic
  between Cloudflare and your origin server, not traffic from client
  browsers to your origin."；且"Site visitors may see untrusted
  certificate errors if you pause Cloudflare or disable proxying on
  subdomains that use Cloudflare origin CA certificates."——仅
  Cloudflare↔origin 段加密受信，浏览器直连不受信。
- **与 Full (strict) 的关系（官方原文）**：origin CA 页 "Once
  deployed, these certificates are compatible with Strict SSL mode."；
  Full (strict) 页证书要求 "Issued by a publicly trusted certificate
  authority or Cloudflare's Origin CA."（后者复核了 M14-203 V2：
  公共 CA（如 Let's Encrypt）同样满足 Full (strict)，现有证书路径
  不变）。
- **可用性**：Availability 表 Free/Pro/Business/Enterprise 全部
  Yes——**Free 计划可用 Origin CA**。
- **有效期（如实）**：现行创建流程仅写 "Choose a Certificate
  Validity period"，未公布具体默认值；SSL 证书有效期参考页
  （certificate-validity-periods）覆盖 Universal/Advanced/Custom/
  SaaS，**不含 Origin CA**；已知限制节仅提示证书 "long-lived" 且
  "Cloudflare does not currently send expiration notifications for
  origin CA certificates"。**M14-203 假设中的"默认 15 年"在现行
  官方文档中未出现，未证实亦未证伪**（确切选项需登录控制台创建
  流程可见，属本轮禁止操作）。
- 其他：SAN ≤200（通配符仅一层，可多条并存；不支持 IP 作 SAN）。

### A2 Cache Rules（M14-203 假设：JSON 可缓存资格、Edge TTL、Free 规则数）

**结论：全部核验通过，E2 缓存路径无文档障碍。**

- **规则数（官方表格，Free 档）**：Availability 表 "Number of
  rules | Free 10 | Pro 25 | Business 50 | Enterprise 300"——
  **Free 档 10 条 Cache Rules**。来源：
  `https://developers.cloudflare.com/cache/how-to/cache-rules/`
  （Last updated 2026-08-14）。
- **前置条件（官方原文）**："Cache Rules require that you proxy the
  DNS records of your domain (or subdomain) through Cloudflare."
  ——与 E1 橙云前置一致，无额外约束。
- **缓存资格（JSON 路径）**：Cache Rules 总述 "allows you to make
  adjustments to **what is eligible to cache**, how long it should be
  cached and where"；设置页 Cache eligibility 提供 **Bypass cache**
  或 **Eligible for cache**（"if you want Cloudflare to attempt to
  cache them"）。结合 V3（默认按扩展名缓存、JSON 不在默认列表），
  **JSON（含 manifest）获得缓存资格的官方路径 = Cache Rules 的
  Eligible for cache 设置**——A2 假设成立。注意官方注记：Bypass
  设置下 `CF-Cache-Status` 可能显示 `DYNAMIC` 而非显式 bypass。
  来源：`.../cache-rules/settings/`（Available settings 页）。
- **Edge TTL 语义（官方原文，三模式）**："Edge Cache TTL refers to
  the maximum cache time-to-live (TTL), or how long an asset should
  be considered fresh or available to serve from Cloudflare's cache"
  ——(i) "Use cache control-header if present, bypass cache if not"；
  (ii) "Use cache-control header if present, use default Cloudflare
  caching behavior if not"；(iii) "Ignore cache-control header and
  use this TTL"（完全忽略源 cache-control，按规则指定时长缓存）。
  另有 **Status Code TTL**（按状态码区间覆盖，忽略源 cache-control）。
- **Browser TTL 语义（官方原文）**："Browser TTL refers to the
  maximum cache time-to-live (TTL) that an asset should be considered
  available to serve from the browser's cache"——可选 Bypass cache /
  Respect origin / Override origin；仅作用于浏览器侧缓存，不影响
  E1/E2 采样器（curl 形态）观测的边缘缓存行为。

### A4 Tunnel（M14-203 假设：出站连接、零入站端口、公共主机名路由、Zero Trust 免费档、多连接器冗余）

**结论：全部核验通过，E3 路径无文档障碍。**

- **出站连接（官方原文）**："a lightweight daemon in your
  infrastructure (`cloudflared`) creates outbound-only connections to
  Cloudflare's global network"；"cloudflared establishes outbound
  connections (tunnels) between your resources and Cloudflare's
  global network"。来源：
  `https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/`
  （Tunnel 概述，Last updated 2026-08-04）。
- **origin 零入站端口（官方原文）**："You can then configure your
  firewall to allow only these outbound connections and block all
  inbound traffic, effectively blocking access to your origin from
  anything other than Cloudflare."；且"Cloudflare Tunnel does not
  use an inbound listener on your origin"（该句同时说明
  Authenticated Origin Pulls 对隧道路由无效，隧道流量已由 connector
  凭据认证）。
- **公共主机名路由（官方原文）**："Cloudflare Tunnel allows you to
  publish local applications to the Internet via a public hostname.
  For example, you can add a published application route that points
  `docs.example.com` to `https://localhost:8080`."；路由经 DNS
  CNAME 或 LB pool 指向 `<UUID>.cfargotunnel.com`。来源：
  `.../connect-networks/routing-to-tunnel/`（Published applications，
  Last updated 2026-06-23）。
- **免费档公共发布（官方原文）**："You do not need a paid Cloudflare
  Access plan to publish an application via Cloudflare Tunnel. Access
  seats are only required if you want to secure the application using
  Access policies."——公共发布本身 $0（Access 席位仅在加访问策略时
  需要）。
- **多连接器冗余与其限制（官方原文 + 限额表）**："Within the same
  tunnel, you can run as many `cloudflared` processes (connectors) as
  needed. Each connector sends traffic to the nearest Cloudflare data
  center."；账号限额（Account limits 页，Cloudflare Tunnel 表）：
  **"Active cloudflared replicas per tunnel: 25"**、tunnels per
  account 1,000、routes per account 1,000。冗余上限 = 每 tunnel
  25 个活跃副本。来源：
  `https://developers.cloudflare.com/cloudflare-one/account-limits/`。
- **Zero Trust 免费档（与 A11 交叉核验）**：plans 页 SASE/Zero
  Trust 定价 **Free $0 forever（≤50 用户）**；Tunnel 限额表未按
  套餐分列（除 DEX 外为通用默认限额）。

### A5 Load Balancing（M14-203 假设：付费产品、健康检查/故障转移、纯 DNS 无自动故障转移）

**结论：付费属性与健康检查/故障转移语义核验通过；"纯 DNS 无自动
故障转移"现行文档无直接表述，按推导事实记录（结论不变）。**

- **付费产品（官方原文）**：概述页标注 **"Add-on feature"**；
  Enable 页 "Load balancing is an add-on for your account, meaning
  your account needs a billing profile... Choose your plan options
  and confirm payment."。来源：
  `https://developers.cloudflare.com/load-balancing/`（Last
  updated 2026-08-14）与 `.../get-started/enable-load-balancing/`。
- **健康检查/故障转移语义（官方原文）**："Distribute traffic evenly
  across your healthy endpoints, **automatically failing over when an
  endpoint is unhealthy or unresponsive**"（Load balancing and
  failover）；"Monitor your endpoints at configurable intervals and
  across multiple data centers"（Active monitoring）。
- **价格（A11 交叉）**：plans 页付费 add-on 区 "Load Balancing —
  Local and global traffic load balancing, geographic routing,
  health checks, and failover for continuous availability.
  **Starting at $5/mo**"。
- **"纯 DNS 无自动故障转移"（如实）**：本切片在 LB 文档树（概述/
  Enable/FAQ）与 DNS 产品文档中**未检索到现行直接表述**。按互补
  命题处理：健康检查与自动故障转移能力官方归属 LB add-on（付费），
  DNS 产品功能清单（plans 页 Free 档核心特性）不含此能力——
  **推导事实，非原文引用**。对 E4 的结构性限制表述成立，精度
  降级为"官方能力边界推导"。

### A6 DNS TTL（M14-203 假设：代理记录 Auto≈300s、非代理最低 60s）

**结论：全部核验通过，并获官方"本地缓存可能超 TTL"表述。**

来源：`https://developers.cloudflare.com/dns/manage-dns-records/reference/ttl/`。

- **代理记录（官方原文）**："By default, all proxied records have a
  TTL of **Auto**, which is set to 300 seconds. This value cannot be
  edited."；"recursive resolvers will not cache them for longer than
  300 seconds (five minutes)"。
- **客户端/解析器缓存语义（官方原文，关键新证据）**："It may take
  longer than 5 minutes for you to actually experience record
  changes, as **your local DNS cache may take longer to update**."
  ——官方承认本地缓存可能超过 TTL 才更新（A7 假设的官方侧印证）。
- **非代理记录（官方原文）**："For **DNS only** records, you can
  choose a TTL between **30 seconds** (Enterprise) or **60 seconds**
  (non-Enterprise) and **1 day**. A TTL of **Auto** is set to 300
  seconds."——M14-203 假设"非代理最低 60s"精确成立（细化：
  Enterprise 档可低至 30s）。

### A7 DNS TTL RFC 语义与客户端超期缓存

**结论：核验通过。**

- **RFC 1035 §4.1.3（官方文本原文）**："TTL a 32 bit signed
  integer that specifies the time interval that the resource record
  may be cached before the source of the information should again be
  consulted. Zero values are interpreted to mean that the RR can only
  be used for the transaction in progress, and should not be
  cached."——TTL 是缓存时间上限，零值仅本次事务有效。来源：
  `https://www.rfc-editor.org/rfc/rfc1035.txt`。
- **客户端/解析器超期缓存**：RFC 1035 定义的是 DNS 解析缓存语义；
  操作系统/浏览器等客户端侧缓存行为超出其范围。Cloudflare A6
  Note（本地缓存可能更久更新）作为官方侧印证。组合结论：TTL 传播
  上限受 RFC 约束、客户端实际可见时间可能更长——M14-203 C 线/
  回滚表述维持不变。

### A11 当前价格与套餐边界（Free / Zero Trust / Load Balancing）

**结论：核验通过（2026-10-01 现值，随时可变）。**

来源：`https://www.cloudflare.com/plans/`（页面标注 © 2026）。

- **Network & CDN 套餐**：Free **$0**（"For personal or hobby
  projects"）；Pro **$20/mo 年付或 $25/mo 月付**；Business
  **$200/mo 年付或 $250/mo 月付**；最高档现名 **Contract**
  （Custom，年付）——注意：docs 站多数页面仍用 "Enterprise" 名称
  （如 Cache Rules/LB 限额表），两名称并存指同一合同档。
- **Free 档包含**：Fast Easy-to-use DNS、Unmetered DDoS
  Protection、CDN、Universal SSL Certificate、Free Managed
  Ruleset、WAF、SSO——代理 DNS/缓存/Full (strict) 路径 $0 成立。
- **SASE / Zero Trust 套餐**：Free **$0 forever**（≤50 用户，
  社区支持，标准日志保留 24h）；Pay-as-you-go **$7/user/month**；
  Contract（自定义）。Zero Trust 独立定价 URL
  （`/products/zero-trust/pricing/`）已 404，定价并入 plans 页。
- **付费 add-on**：**Load Balancing $5/mo 起**；Smart Shield + Argo
  Smart Routing $5/mo 起；Advanced Certificate Manager $10/mo；
  Cache Reserve usage-based。
- **对 Phase 3 的边界结论**：E1（代理 DNS + Full strict）与 E2
  （Cache Rules ≤10 条）在 Free $0 内成立；E3 Tunnel 公共发布免费
  档成立；E4 的 LB 健康检查/故障转移需 ≥$5/mo 付费 add-on。

### A8 ngrok 免费版（R1 拒绝清单，简核）

**结论：拒绝维持，证据增强。**

- 官方 Free Plan Limits 页（
  `https://ngrok.com/docs/pricing-limits/free-plan-limits`）：
  "To deter phishing attacks, ngrok shows an **interstitial page in
  front of all HTML browser traffic on the free tier**."；点击
  Visit 后 cookie 对该域抑制 7 天；绕过方式仅客户端预置
  `ngrok-skip-browser-warning` 头或非标准 User-Agent。
- 免费档域名形态：仅 1 个自动分配 dev domain
  （`your-assigned-name.ngrok-free.app`，至多 3 个在线 endpoint）；
  自定义/自有/保留域名均付费解锁——M14-203 "免费档隧道 URL 形态
  限制"假设核验成立。
- **新事实（强化拒绝）**：插页绕过必须客户端预置 header/UA 或
  首次人工点击（7 天 cookie）——普通手机浏览器首次直接打开必然
  见插页，"无预置 header/无人工点击"门（R1）不满足，结论不变。

### A9 Tailscale Funnel（R3 拒绝清单，简核）

**结论：拒绝维持。**

官方 KB（`https://tailscale.com/kb/1223/funnel`，标注 beta）：
仅可使用 tailnet 域名（`tailnet-name.ts.net`）；仅监听端口
443/8443/10000；带宽限制不可配置；依赖 MagicDNS/HTTPS 证书/
tailnet policy（控制面）；Let's Encrypt 限额触发后可能等待 34
小时。M14-203 A9 假设全部核验成立（另增 beta 状态与带宽上限两
项新事实），运营不匹配拒绝结论不变。

### A10 serveo / localhost.run（R2 拒绝清单，简核）

**结论：拒绝维持；serveo 获强化拒绝新事实。**

- serveo 官网（`https://serveo.net/`）：Free 档 $0 含 3 条活跃
  隧道与自定义子域；**Pro 档（$60/yr）卖点明确列出 "No
  interstitial warnings"——反证免费档存在插页**，与 ngrok 同类
  不满足"任意手机直接打开"门。M14-203 "自定义域名付费墙"表述
  需修正为"自定义子域免费含、但免费档有插页、高级特性付费"。
- localhost.run 官网（`https://localhost.run/`）：仅营销页
  （"Forever free tier"、自动 TLS），无 SLA/容量/运营承诺表述
  ——"尽力而为可用性"判断维持，无翻案事实。

## 4. 引用清单

**[V] 本切片在线核验成功（2026-10-01 00:13–00:22 UTC）**：

- V5 Origin CA（新位置，含信任边界/可用性/与 Full strict 关系）：
  https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/
- V5a Full (strict) 模式页（证书要求含公共 CA 与 Origin CA）：
  https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/
- V5b SSL 证书有效期参考页（不含 Origin CA——负证据）：
  https://developers.cloudflare.com/ssl/reference/certificate-validity-periods/
- V6 Cache Rules 总述（规则数表 Free=10、需代理 DNS）：
  https://developers.cloudflare.com/cache/how-to/cache-rules/
- V6a Cache Rules 设置（eligibility/Edge TTL/Browser TTL）：
  https://developers.cloudflare.com/cache/how-to/cache-rules/settings/
- V7 Tunnel 概述（outbound-only/零入站/多 connector）：
  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/
- V7a Published applications（公共主机名路由/免费发布）：
  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/routing-to-tunnel/
- V7b Zero Trust Account limits（每 tunnel 25 replicas 等）：
  https://developers.cloudflare.com/cloudflare-one/account-limits/
- V8 Load Balancing 概述（add-on/failover/monitor）：
  https://developers.cloudflare.com/load-balancing/
- V8a LB Enable（add-on 需计费档案/付费确认）：
  https://developers.cloudflare.com/load-balancing/get-started/enable-load-balancing/
- V8b LB FAQ（健康监视行为；无纯 DNS 表述——负证据）：
  https://developers.cloudflare.com/load-balancing/troubleshooting/load-balancing-faq/
- V9 DNS TTL（Auto=300s/最低 60s/本地缓存可能更久）：
  https://developers.cloudflare.com/dns/manage-dns-records/reference/ttl/
- V10 RFC 1035（TTL 定义原文）：
  https://www.rfc-editor.org/rfc/rfc1035.txt
- V11 Cloudflare plans（Free/Pro/Business/Contract、Zero Trust
  Free ≤50 用户、LB $5/mo 起）：
  https://www.cloudflare.com/plans/
- V12 ngrok Free Plan Limits（插页/7 天 cookie/免费域形态）：
  https://ngrok.com/docs/pricing-limits/free-plan-limits
- V13 Tailscale Funnel KB（ts.net/端口/带宽/控制面依赖）：
  https://tailscale.com/kb/1223/funnel
- V14 serveo 官网（Free 档与 Pro "No interstitial"）：
  https://serveo.net/
- V15 localhost.run 官网（仅营销页）：
  https://localhost.run/

**[核验失败/负结果（如实）]**：

- Origin CA 旧 URL（M14-203 A1 原核验源）：
  `https://developers.cloudflare.com/ssl/origin-configuration/origin-ca-certificate/`
  → 404（页面迁移；firecrawl 2026-09-30T23:34Z 缓存副本判定）。
- Zero Trust 独立定价页：
  `https://www.cloudflare.com/products/zero-trust/pricing/` → 404
  （定价并入 plans 页，已由 V11 覆盖）。

**[内部证据]**：M14-203 §2/§3/§5/§7/§10（V/A 纪律与假设清单、
E1–E4 门、变更门禁）；M14-198/199/202（经 M14-203 §1 引用）。

## 5. 对 E1/E2/E3 决策的影响

- **E1（A 最小代理）：无 blocker，前置事实全绿。** G0（supervisor
  授权 + 预算）仍是唯一前置门；G1 门与测量契约不变。A6 复核补充
  回滚预期管理：切回 DNS-only 或删记录后，resolver 侧传播上限
  300s，但客户端本地缓存可能更久（官方原文）——回滚生效时间在
  执行切片落档时按此表述。A1 细节（Origin CA 有效期）E1 不依赖
  （采用现有公共 CA 证书 + Full (strict)，V5a 复核兼容性成立）。
- **E2（A + 缓存）：无 blocker。** manifest JSON 缓存资格路径确认
  （Cache Rules "Eligible for cache"）；Edge TTL 三模式可精确控制
  陈旧窗（含忽略源 cache-control 模式）；Free 档 10 条规则对 E2
  所需（manifest 1 条 + 制品默认扩展名缓存 0 条）充裕。G2 门与
  staleness 业务决策点不变。**新增执行前检查项**：确认 origin 对
  `/aios/download-manifest.json` 响应的 Cache-Control 现值（决定
  respect/override 模式选择与陈旧窗大小）——属 E2 执行切片首步，
  不是本轮范围。注意 Bypass/规则交互下 `CF-Cache-Status=DYNAMIC`
  的官方注记，G2 的 HIT 观测口径不变。
- **E3（B Tunnel 可选）：无 blocker。** 出站连接/origin 零入站端口/
  公共主机名路由/免费档公共发布/多连接器（≤25 replicas/tunnel）
  全部官方核验；G3 门不变；cloudflared 安装与 tunnel token 仍属
  VPS 变更与凭据门禁（M14-203 §7），需独立批准。
- **E4（C 多节点，末位）：结构性限制确认，位置不变。** LB 为付费
  add-on（$5/mo 起）已核验；"纯 DNS 无自动故障转移"降级为推导
  事实（§3 A5）；TTL 上限与客户端超期缓存（A6/A7）确认。G4 前置
  门（双 no-go + 成本批准）不变。
- **拒绝清单（R1/R2/R3）：无翻案事实。** R1（ngrok）与 R2（serveo）
  获插页类强化证据；R3（Funnel）限制全部官方证实。A10 中 serveo
  "自定义域名付费墙"表述按 §3 修正（自定义子域免费含、插页为否
  决项）。
- **本切片不产生任何执行授权**：上述全部实验仍处 G0 待批状态，
  门禁表（M14-203 §7）不变。

## 6. 仍未核验项与 blocker

| 项 | 状态 | 影响 |
| --- | --- | --- |
| A1 Origin CA 默认有效期具体数字 | 现行文档未公布（仅"选择有效期"）；控制台创建流程可见但属登录操作，本轮禁止 | 无 E1–E3 影响（不采用 Origin CA 路径）；若未来采用，届时于授权切片核验 |
| A5 "纯 DNS 无自动故障转移" 现行直接原文 | 文档树未检索到；按互补命题推导（§3 A5） | 仅影响 E4 表述精度，不影响结构判断 |
| A3 WebSocket 全套餐支持 | 本轮范围外（下载面不依赖），维持 M14-203 假设 | 无 |
| origin manifest 响应 Cache-Control 现值 | 服务器侧事实，属 E2 执行切片检查项 | E2 执行首步，非 blocker |
| cf-ray PoP 后缀存在性（就近 PoP 观测） | 属 E1 实测项（M14-203 §6 遗留），非文档核验 | E1 观测时确认 |
| Cloudflare/第三方价格与限额后续变动 | 本轮为 2026-10-01 时点快照 | 执行切片开工前若隔期较久应抽查关键值 |

**E1 blocker：无。** 全部 E1 前置文档事实已核验或确认不依赖；
唯一前置门仍为 G0（supervisor 批准执行切片与测量预算）。

## 7. 诚实边界

1. 本切片是文档核验，不是观测：所有价格/限额/规则数为抓取时点
   快照，随时可能变动；引用以抓取窗口（§2）为准。
2. 工具形态偏差如实：本机 WebFetch 不可用，改用服务端抓取；一处
   404 判定来自 firecrawl 前日缓存副本（§2）；web-reader 抓取无
   缓存元数据。证据等级沿 M14-203 §2，不以工具形态升降级。
3. A1"默认 15 年"未证实：未在现行文档找到该数字，不做任何虚构；
   保留为未核验并说明获取路径受限（需登录控制台）。
4. A5 推导事实显式标注：未将推导冒充官方原文引用。
5. A8/A10 的插页证据改变 M14-203 两处表述精度（ngrok 7 天
   cookie；serveo 免费档含自定义子域但有插页），已在 §3 修正，
   拒绝结论均不变。
6. 本切片 docs-only（证据 README + 两处台账）；单 local commit，
  不推送、不开 PR；supervisor 审查与 remote 发布（PR 开合/合并/
  release 门禁）在其后进行。
