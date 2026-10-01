# M14-203：Phase 3 公网多入口/Cloudflare 调研与决策矩阵（docs-only，未执行）

- 切片：worktree `ai-learning-os-worktrees/m14-199-phase1-budget`，分支
  `docs/m14-203-phase3-ingress-research`，基于本地 `86227c0`
  （M14-202 落档 commit；其树与远端 main 合并态一致系任务书口径，
  本切片未联网复核）。
- 定性：**纯文档调研**——Phase 3（公网多入口/Cloudflare）候选选项
  对比矩阵、推荐实验顺序与 go/no-go 门、有界测量计划（未来执行）、
  未来变更门禁与批准清单。本切片零请求、零网络/生产/VPS/DNS/云
  操作、零凭据接触、零 Phase 3 授权；一切执行待 supervisor 另行
  立项批准。
- 输入：M14-199/M14-202 证据 README（仓库内既有证据）+ 4 份
  Cloudflare 官方文档（2026-10-01 在线核验，§2/§10）+ 其余标注为
  假设的事实（每项给出未来核验源）。

## 1. 目标与 H1 基线摘要

**目标**：为"任意手机可公开下载"的 manifest 与制品分发面，选择
下一个（组）公网入口路径并给出可判定的实验顺序——在不削弱现有
直连入口、不超出个人项目成本约束的前提下，改善 H1 已定位的
公网入站路径慢问题（尤其中国大陆/移动网络视角），且任何一步都
可回滚。

**H1 基线（已落档，不新增采样）**：

| 面 | 事实 | 来源 |
| --- | --- | --- |
| 公网 direct（家机观测点，+08:00） | 9 窗 / 72 样本，TTFB p50 286.648 / p95 5793.434 / max 12283.814 ms，慢窗 3/9≈33.3%，慢样本 10/72≈13.9%，恒 HTTP/1.1 | M14-198 §3（经 M14-199 §7 引用） |
| VPS 本地终结（Round B） | 24/24 干净，TTFB p50 26.783 / p95 35.793 / max 51.281 ms，TLS 段 p95 35.350，握手后等待段 p95 0.343 | M14-202 §4 |
| 判定 | **H1：慢在公网入站路径，非 VPS 处理面**（本地 p95 两个数量级优于公网）；Phase 2 Nginx 调优被矩阵关闭 | M14-202 §6 |
| 现有入口形态 | 单一 VPS 直连 HTTPS；证书为公开 CA 有效链（Round B `verify=0` × 24，保留主机名/SNI）；宿主 443 具备 h2 能力（`ver=2` 被动记录，未作因果判据） | M14-202 §3/§7 |
| manifest 事实 | `/aios/download-manifest.json`，已发布字节数 1392，`application/json` | M14-179/M14-199 §3.3 |

## 2. 调研方法与证据分级（V/A 纪律）

- **[V] 已核验事实**：仅两类——(i) 2026-10-01 在线抓取成功的
  Cloudflare 官方文档（V1–V4，§10）；(ii) 仓库内已落档证据
  （M14-198/199/202）。引用处标（V1）…（V4）或内部编号。
- **[A] 假设**：来自通用工程知识、未在本切片在线核验的事实。
  每项标注（A#）并在 §10 给出**未来核验源 URL**；执行任何含
  假设依据的实验前，须先核验对应条目并把结论回填证据。
- **在线核验不可用记录（如实）**：Origin CA 证书文档页在本切片
  抓取窗口内不可达（渲染页与唯一一次 `index.md` 尝试均失败）；
  Origin CA 的**存在性**及其与 Full (strict) 的兼容性已由 V2
  官方文字核验，其余细节（默认 15 年有效期、仅 Cloudflare 边缘
  信任等）按假设处理（A1）。
- 抓取时间与工具形态不作为证据等级依据；证据等级只看是否官方
  文档原文与是否抓取成功。

## 3. 候选选项与对比矩阵

### 3.1 选项定义

- **A（Cloudflare 代理 DNS 前置现有 origin）**：新增子域（示例
  占位 `<phase3-host>.ndtool.cn`，实际名由执行切片落定），A 记录
  开橙云代理指向现有 VPS；访客→Cloudflare 全球边缘→现有 VPS
  origin（V1）。origin 零改动即可起步：Full (strict) 兼容现有
  公共 CA 证书（V2）。
- **B（Cloudflare Tunnel 附加路径）**：VPS 上运行 cloudflared，
  以**出站**连接接入 Cloudflare 边缘，公共主机名路由到本地服务
  （A4 假设项）；独立子域，与 A 并存不替换。
- **C（多地域入口 + DNS RR/GeoDNS + 健康检查）**：新增 ≥1 个
  异地节点运行完整分发栈（证书 + 制品服务），DNS 侧轮询或地理
  路由并配健康检查故障转移（A5 假设项）。
- **D（保持 VPS 直连）**：现状不动，全程对照臂与回滚基线。

### 3.2 对比矩阵

| 维度 | A 代理 DNS | B Tunnel | C 多节点 GeoDNS | D 直连（对照） |
| --- | --- | --- | --- | --- |
| 延迟假设 | 就近任播边缘终结回源请求；缓存未命中=边缘+回源两段（回源段与直连同路径，理论不优于直连同段）；**命中缓存=边缘终结**，TTFB 主要剩访客→边缘 RTT；跨境段仍受制（V4） | A 基础上再增 cloudflared 隧道跳（源→边缘出站连接），假设净延迟略增，以可用性/暴露面换 | 就近节点终结，理论最优；实际收益取决于节点选址 | 不变（H1 基线 p95 5793ms） |
| 可用性 | 边缘侧 HA + origin DDoS 防护（V1）；**origin 单点仍在**（VPS 挂则 A 同挂） | 同 A；连接器进程是新组件，多连接器可冗余（A4） | 跨节点故障转移；受 DNS TTL 传播与客户端缓存制约（A5/A7） | 单 VPS 单点，无冗余 |
| 安全 | 隐藏 origin IP（V1）+ 边缘防护（V1）+ Full (strict) 端到端加密（V2）；zone 激活前 pending 期有 IP 泄露窗口（V1a） | origin 零入站端口（A4），暴露面最小；新增常驻进程的供应链/凭据面 | 每节点完整暴露面 ×N，证书与加固运维成本高 | origin IP 公开暴露（V1 风险表述） |
| 运维复杂度 | **低**：DNS 面板 + SSL 模式 + 可选缓存规则；origin 可零变更 | 中：新增常驻进程（systemd/容器）、token 生命周期、连接器监控 | 高：新 VPS、证书、制品同步、故障转移配置与演练 | 无 |
| 成本 | **$0 起步**（Free 计划代理/缓存；现值核验 A11） | $0（Zero Trust 免费档，假设 A4/A11） | 最高：≥1 新 VPS + GeoDNS/LB 订阅（A5/A11） | $0 |
| 中国/移动可达 | 大陆访客走全球边缘（境外 PoP），跨境延迟/可靠性官方承认不可控（V4）；China Network 企业版+ICP 门槛排除（V4）；移动端标准 HTTPS 无已知特例（假设） | 同 A 跨境制约；隧道出站段同样跨境 | 唯一可境内终结的选项，但触发 ICP 备案/合规/监管义务（V4 引申）与成本 | Phase 0 家机视角已观测慢窗 33% |
| 回滚 | 删子域记录或切 DNS-only 即回直连（生效受 TTL 传播制约，A6/A7）；现有直连记录不动即天然回滚 | 停 cloudflared 即路径消失；清隧道主机名记录 | 节点下线 + DNS 改回；多节点回收成本高 | 即基线 |
| 证据强度 | **高**（V1–V4 官方已核验；仅缓存规则细节为假设） | 中（A4 全项未在线核验） | 低–中（架构推理 + A5/A7 假设） | 高（Phase 0 + Round B 实测） |

### 3.3 各选项事实/假设明细

**A（代理 DNS）**：
- 橙云后 DNS 解析返回 Cloudflare 任播 IP，请求先到边缘再回源；
  origin 获 DDoS 防护；服务器侧只见 Cloudflare IP，可能需放行
  Cloudflare IP 段（V1）。
- 官方建议提供 web 流量的 A/AAAA/CNAME 启用代理（V1）；仅
  HTTP/HTTPS 流量可代理、非标准端口需 Spectrum（V1a；443 不受
  约束）。
- zone 新增后最多 24h 处于 pending/DNS-only（期间返回源 IP）、
  官方建议激活后轮换 origin IP——两事实现位于限制页（V1a）；
  轮换属 VPS 侧变更，本切片仅记录建议不授权执行（§7/§9）。
- SSL 模式官方强烈建议 Full 或 Full (strict)；Full (strict)
  校验源证书，源证书可为公共 CA（如 Let's Encrypt）或 Cloudflare
  Origin CA（V2）。现有公开 CA 证书满足 Full (strict)，**无需
  换源证书即可起步**。
- 默认缓存按**扩展名**判定（非 MIME）：APK 在默认缓存列表、
  **JSON 不在**；HTML 不缓存；`Cache-Control` 为
  private/no-store/no-cache/max-age=0 或存在 Set-Cookie 时不缓存；
  缓存文件上限 Free 档 512MB；`CF-Cache-Status` 头是缓存生效的
  观测手段（V3）。
- Origin CA 证书细节（默认有效期、信任域）在线核验不可用（§2），
  按 A1 假设处理；E1/E2 不依赖该假设（现有公共证书路径即可）。

**B（Tunnel）**：cloudflared 出站连接、origin 零入站端口、公共
主机名路由、Zero Trust 免费档可用、多连接器冗余——全部 A4 假设，
执行前须核验（§10）。

**C（多节点）**：健康检查/DNS 故障转移属 Cloudflare Load
Balancing 付费产品族、纯 DNS 无自动健康检查故障转移（A5）；
DNS TTL 语义为缓存上限、客户端/解析器可能超 TTL 缓存（A7）。
此二项是 C 的**结构性限制**，即使核验通过也不会消失，只影响
表述精度。

**D（直连）**：全部为已落档实测事实（§1），无假设项。

## 4. 明确拒绝清单

- **R1 ngrok 免费版**：对浏览器访客显示插页警告页，任意手机
  直接打开下载链接不满足"无预置 header/无人工点击"门；免费档
  隧道 URL 形态限制（A8）。拒绝为实验候选。
- **R2 serveo / localhost.run 类 SSH 通用隧道**：尽力而为可用性、
  共享容量、自定义域名付费墙（A10）；即便可用也不满足可回滚/
  可监控的运营门。拒绝。
- **R3 付费消费级隧道 / Tailscale Funnel**：付费方案（ngrok 等）
  在个人项目预算内无开放/运营契合；Tailscale Funnel 免费但绑定
  ts.net 域、端口受限、依赖 tailnet 控制面与节点常驻（A9），
  控制面可达性在目标网络环境下本身不可控。拒绝。
- **R4 家庭宽带端口暴露**：端口映射/DDNS 直接对公网提供服务。
  安全面（家网完整暴露、无 DDoS 缓冲、家庭 IP 泄露）、可靠性面
  （动态 IP/CGNAT）、策略面（住宅 ISP 条款）三重不满足；此为
  设计裁量，**永不进入实验**，不需要外部引用。

## 5. 推荐 Phase 3 实验顺序与 go/no-go 门

总原则：D 恒为对照臂；每步只引入一个变量；含假设依据的实验
先核验假设（§2/§10）；门为**描述性决策启发式**（小样本口径，
沿 M14-181/M14-199 §4，24 样本不支持 p99/成功率显著性）。

| 序 | 实验 | 内容 | 门（go / no-go） |
| --- | --- | --- | --- |
| E1 | A 最小代理 | 新子域橙云 + Full (strict)，**不加缓存规则**，隔离"纯代理"净效应 | **G1** go：实验臂 TTFB p95 < 对照臂 p95×0.7（改善≥30%）且零失败、`verify=0`、bytes/ctype 不变；部分收益（0<改善<30%）→ 进 E2 再判；no-go：实验臂 p95 ≥ 对照臂 p95，或出现可归因于 CF 路径的失败样本 |
| E2 | A + 缓存 | 制品走默认扩展名缓存（APK，V3）；manifest JSON 需缓存规则（A2），先落档 staleness 决策 | **G2** go：温样本观测到 `CF-Cache-Status=HIT`（≥1/8）且 bytes=1392/ctype 保持、制品 200；manifest 缓存仅在业务接受 Edge TTL 陈旧窗时启用，否则只缓存制品；no-go：规则不生效或出现不可接受陈旧/不一致证据 |
| E3 | B Tunnel（可选） | 独立子域隧道路径；触发条件：E1 部分收益但回源/跨境段仍瓶颈，或需源侧零入站端口加固 | **G3** go：隧道臂零失败且 TTFB p95 ≤ E1 实验臂 p95×1.5（隧道开销可接受）；no-go：失败>0 或开销超阈 |
| E4 | C 多节点（末位） | 仅当 A、B 双 no-go 且可达性痛点业务必要性成立 | **G4** 前置门：supervisor 批准新节点成本/运维预算后才启动；DNS 故障转移限制（A5/A7）必须在方案中显式接受 |

- E1 前置门 **G0**（对全部实验）：supervisor 批准执行切片 +
  测量预算授权（§6）；无授权零执行。
- 任一 no-go 后的动作顺序：先收口落档（含 CF-Cache-Status/失败
  形态结论），再由 supervisor 决定跳线（E1→E3）或关闭 Phase 3
  调查线；no-go 不自动触发下一实验。

## 6. 有界测量计划（未来执行；本切片零请求、零预算消耗）

- **形态沿 M14-199 §3.3/§4**：每臂 3 窗 × 8 样本、样本间隔
  ≥1s、单次超时 15s、零重试、证书验证全保留（禁止
  `-k`/`--insecure`/`--ssl-no-revoke` 类削弱开关，沿 Phase 0/1
  纪律）、key=value 计时行（tcp/tls/ttfb/total 段分解）+
  `date -u` UTC 锚点、nearest-rank p50/p95/max 描述性统计。
- **臂定义**：实验臂 = Phase 3 新主机名；对照臂 = 现有直连主机
  名。同观测点、同窗背靠背；臂间交替次序由执行切片落定并落档。
- **预算（待批准）**：每臂每轮 ≤24 个 manifest 请求，单轮双臂
  合计 ≤48；**Phase 3 新账本**——不复用 Phase 1 轮内余量 7
  （该账属 Phase 1，M14-202 §5）；reserve 另行授权；第 48 次
  完成即硬停止。
- **记录扩展**：被动记录协议版本 `ver`（协议混杂沿 M14-202 §7
  纪律，跨臂比较不做协议因果结论）、`bytes`、`ctype`，及响应头
  结论摘录：`CF-Cache-Status`（缓存判定）；`cf-ray` 的 PoP 后缀
  若存在则作就近 PoP 免费事实记录（其存在性属假设，待实测确认，
  不作门输入）。
- **工件边界**：原始采样只进 gitignored `.verify/` 目录；入库仅
  结论摘录与统计（沿 M14-199 §6 脱敏规则）。
- **观测点**：supervisor 家机（与 Phase 0 同点，保证可比）；
  手机 4G/5G 人工验收单列清单（现场执行、不自动化、不进请求
  预算）。
- **本切片状态**：§6 仅为待批准计划——本切片零请求、零预算
  消耗、零授权。

## 7. 未来验证门与所需批准（DNS/云/VPS 变更门禁）

| 变更面 | 门禁 | 批准 |
| --- | --- | --- |
| 新增 DNS 记录 / 代理开关（仅限新子域） | 独立执行切片 + 显式批准；**现有 apex 与直连记录禁动**（对照臂完整性） | supervisor |
| Cloudflare 账号/zone 操作（SSL 模式、缓存规则、Tunnel token） | 独立执行切片 + 显式批准；token/凭据永不入库（§9） | supervisor |
| VPS 变更（nginx / Docker / cloudflared 安装） | 独立执行切片 + 显式批准 + 前置证明（沿 M14-181 §4 模式） | supervisor |
| 生产 manifest 请求（任何路径、任何次数） | 预算授权逐轮落档；**本切片授权 0** | supervisor |
| 手机人工验收（4G/5G 清单） | 清单制现场执行 | supervisor |
| 回滚动作（DNS-only 切换 / 停 cloudflared / 下节点） | 与正向变更同级——回滚也是变更，同门禁 | supervisor |

- 回滚预案（预先落档，执行仍需批准）：A 线 = 删子域记录或切
  DNS-only（生效受 TTL 传播制约，A6/A7）；B 线 = 停 cloudflared
  进程；C 线 = 节点下线 + DNS 改回。D 直连全程不动，任何时刻
  都是可用回滚面。

## 8. 诚实边界

1. 本切片是调研与决策准备，不是观测结果：所有延迟/可达性判断
   都是**假设或官方文档表述**，未做任何新采样。
2. 家机单观测点、单时段边界沿 Phase 0（M14-199 §10.2）；移动
   网络可达性只能由未来人工验收回答，本切片不做任何手机断言。
3. 事实分级如实：V 项仅 4 份官方文档 + 仓库既有证据；A 项在
   对应实验执行前必须核验并把结论回填；Origin CA 在线核验
   不可用已如实记录（§2）；价格/套餐现值随时变动（A11）。
4. 跨境不可控是官方表述（V4），但"大陆访客实际命中哪个全球
   PoP、质量如何"未实测——属 E1 观测项，本切片不预判结果。
5. §5 各门是描述性启发式；24 样本/臂只支持 p50/p95/max 与
   频率比较，不支持 p99/成功率门（沿 M14-181 统计诚实边界）。
6. 协议混杂（实验臂可能协商 h2/h3，对照臂 Phase 0 形态恒
   HTTP/1.1）：跨臂比较必须被动记录 `ver`、禁止协议因果结论
   （沿 M14-202 §7）。
7. 缓存引入 staleness 陈旧窗：manifest 与制品的新鲜度要求由
   业务决策落档后才能启用对应缓存（G2 内嵌决策点）。
8. 本切片 docs-only（本 README + 两处台账）；单 local commit，
   不推送、不开 PR；supervisor 审查与 remote 发布（PR 开合/
   合并/release 门禁）在其后进行。

## 9. 非目标（explicit non-goals）

- **零 Phase 2 Nginx 调优**（已被 H1 判定关闭，M14-202 §6）。
- **本切片零变更**（同任务书）：生产代码、基础设施、workflows、
  DNS、VPS、Docker、Cloudflare、frp、CC Switch、代理、模拟器、
  手机设备一概不动；零生产 manifest 请求、零 SSH/云 API/部署
  动作。
- **不评估 China Network**：企业版 + ICP 备案门槛（V4）超出
  个人项目范围；全球边缘对中国访客的表现由 E1 实测回答。
- **不评估静态托管/对象存储迁移**：不同架构族（改变分发架构
  而非入口路径）；若入口实验全线 no-go，可另立项。
- **不执行 origin IP 轮换**：V1a 官方建议存在，但属 VPS 侧变更，
  须单独批准（§7）。
- **脱敏红线（沿 M14-199 §6）**：VPS IP/账户/SSH 别名/主机细节、
  supervisor 工作站本地绝对路径、任何凭据/token 值、raw log
  原文，永不入库。

## 10. 引用清单

**[V] 已核验（2026-10-01 在线抓取成功，官方文档原文）**：

- V1 代理 DNS 行为/任播 IP/DDoS 防护/建议代理 web 记录：
  https://developers.cloudflare.com/dns/proxy-status/
- V1a 代理 DNS 限制页（pending 最多 24h 且激活前 DNS-only、
  非标准端口需 Spectrum、激活后建议轮换 origin IP）：
  https://developers.cloudflare.com/dns/proxy-status/limitations/
  （V1/V1a 现行 URL 由 supervisor 勘误提供，本切片未联网复核；
  对应事实原文已在旧单页 URL 抓取成功时核验，§2）
- V2 SSL 模式（Full (strict) 推荐、Origin CA 兼容、Flexible
  明文回源）：
  https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/
- V3 默认缓存行为（扩展名列表含 APK 不含 JSON、Cache-Control
  交互、512MB 上限、CF-Cache-Status）：
  https://developers.cloudflare.com/cache/about/default-cache-behavior/
- V4 China Network（企业版专属订阅、ICP 备案要求、大陆境内
  JD Cloud 运营、跨境延迟/可靠性官方表述）：
  https://developers.cloudflare.com/china-network/

**[内部证据]**：M14-198 Phase 0 基线；M14-199 §4/§6/§7 采样契约
与决策矩阵；M14-202 §4/§6 Round B 统计与 H1 判定。

**[A] 假设项与未来核验源（执行前核验并回填）**：

- A1 Origin CA 细节（默认有效期、仅边缘信任）：
  https://developers.cloudflare.com/ssl/origin-configuration/origin-ca-certificate/
  （本切片抓取不可达；存在性与 Full (strict) 兼容性已由 V2 核验）
- A2 Cache Rules（JSON 缓存资格、Edge TTL、Free 档规则数）：
  https://developers.cloudflare.com/cache/how-to/cache-rules/
- A3 WebSocket 全套餐支持（下载面不依赖，仅入口族完整性）：
  https://developers.cloudflare.com/fundamentals/scope/
- A4 Tunnel（cloudflared 出站连接、零入站端口、公共主机名路由、
  Zero Trust 免费档、多连接器冗余）：
  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/
- A5 Load Balancing（付费订阅、健康检查监视器、纯 DNS 无自动
  故障转移）：
  https://developers.cloudflare.com/load-balancing/
- A6 DNS TTL（代理记录 Auto≈300s、非代理最低 60s）：
  https://developers.cloudflare.com/dns/manage-dns-records/reference/ttl/
- A7 DNS TTL 语义与客户端超期缓存：
  https://www.rfc-editor.org/rfc/rfc1035
- A8 ngrok 免费版浏览器插页/URL 形态：ngrok 官方文档 browser
  warning 页（执行前核验确切路径，入口 https://ngrok.com/docs/）
- A9 Tailscale Funnel（ts.net 域、端口 443/8443/10000、tailnet
  控制面依赖）：
  https://tailscale.com/kb/1223/funnel
- A10 serveo / localhost.run（可用性/容量/自定义域名付费墙）：
  https://serveo.net/ 、https://localhost.run/
- A11 各订阅/服务现价：
  https://www.cloudflare.com/plans/
