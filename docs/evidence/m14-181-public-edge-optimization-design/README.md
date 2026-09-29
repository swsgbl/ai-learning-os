# M14-181：公网边缘优化设计（design-only 切片）

- 切片：worktree `ai-learning-os-worktrees/m14-180-public-edge-stability-probe`，
  分支 `ops/m14-181-public-edge-optimization-design`，基于 main
  `7bc9334bc16db0776f2a53d921f15c29c2822b04`（PR #271 merge = M14-180
  探针合入，精确基点）。
- 定性：**纯设计/决策文档切片**——零生产操作（不改 VPS / Nginx / frp /
  Docker / voice / 运行时应用文件）、**零新增公网请求**（全部数据引用
  M14-180 已落档证据）。本 README 是 M14-180 观测事实转化为优化决策的
  唯一载体：事实/假设分离、选项比较、带 go/no-go 门与预算的分阶段计划、
  生产变更前置证明与配置面清单。任何配置变更都留待后续独立切片执行，
  执行前必须过本文件 §4 的前置证明。

## 1. 观测事实（F）与假设（H）严格分离

### 事实（全部来自 M14-180 已落档证据，2026-09-29 时点，原始报告在
gitignored `.verify/m14-180-public-edge-stability-probe/`）

- **F1 直连快窗口**：manifest 5 样本全部 200/HTTP1.1/1392B/SHA256 与
  发布值一致；TTFB 151-361ms，其中派生 TLS 段 47-125ms、握手后等待段
  50-152ms。
- **F2 直连慢窗口（约两分钟后）**：4 样本仍全部 200/同 SHA/同
  remote_ip，但 TTFB 5112-10249ms，分解为 **TLS 握手段 2.36-6.16s +
  握手后等待段 2.55-4.83s**；DNS 99-113ms、TCP 0.02-90ms 正常。
  慢与快的差别不在连接建立前的解析/路由，而在 TLS 与其后。
- **F3 代理路径**：经系统代理 5 样本零失败，TTFB 263ms-6.5s（TLS
  202-3391ms + 等待 60-2456ms，DNS/TCP 近零——代理本地终结）。
- **F4 APK 单次校验下载**：15s 默认超时未完成；**握手各段全部正常**
  （TLS 73ms、等待 88ms、TTFB 248ms）——瓶颈在 body 传输（约 8MB
  经当前家宽↔VPS 路径的带宽），不在边缘处理。
- **F5 本机传输栈限制**：本机两套 curl（mingw64/System32）Features
  均无 HTTP2——全部观测为 HTTP/1.1；M14-179 记录的系统代理瞬态
  HTTP/2 错误在本切片既未复现也无法排除（F8）。
- **F6 边缘拓扑（来自 infra/edge/nginx.public-base-path.example.conf）**：
  `= /aios/download-manifest.json` 与 `^~ /android/` 由 **VPS Nginx
  本地磁盘直出（/var/www/aios-downloads/），不经 frp 家机隧道**；
  `/aios` 与 `/aios/api/` 经 frps(127.0.0.1:8080) → 家机。因此 F2 的
  慢段发生在 VPS 公网入站/边缘处理面，**与 frp 隧道无关**。
- **F7 缓存契约（既有有意设计）**：manifest `Cache-Control: no-store`
  （清单是 /download 页运行时事实源，新 APK 发布即时可见——M14-176
  定版）；APK `public, max-age=3600`（文件名带版本、目标不可覆盖，
  URL 内容不可变）。二者都是**契约**不是待修缺陷。
- **F8 M14-179 §6**：更早系统代理浏览器探针出现过瞬态 HTTP/2 / MIME
  资源错误（细节未采纳）；最终直连 Chrome 验收全绿。
- **F9 单一边缘入口**：观测期内 remote_ip 恒为 `113.45.64.145`——
  无多 POP/多入口分摊。

### 假设（未证明——Phase 0/1 的检验对象）

- **H1 TLS 段慢（2.36-6.16s）的成因**在 VPS 公网入站路径：候选 =
  中间网络对入站 TLS 的 QoS/限速、VPS 侧证书链验证路径（无 OCSP
  stapling 时的出站查询）、VPS CPU/负载。**未定**。
- **H2 等待段慢（2.55-4.83s）的成因**在 VPS 边缘处理：候选 = Nginx
  accept 队列/worker 饱和、TLS session 未复用导致逐连接全握手（探针
  与浏览器水合都是新连接）、宿主 server 块 keepalive/超时配置。**未定**。
- **H3 慢窗口频率/分布未知**：全部观测仅 ≤9 个直连样本、单会话单日
  时点——慢窗口可能是高峰期拥塞、周期性事件或罕见突发，当前样本量
  不足以给任何 SLO 下结论。
- **H4 APK 带宽瓶颈在家宽↔VPS 路径**：VPS 侧出口带宽与并发能力未测。
- **H5 M14-179 的系统代理瞬态错误与 HTTP/2 相关**：本机无法验证
  （F5），需带 nghttp2 的 curl 环境或 VPS 侧 h2 采样。

## 2. 选项比较

| 选项 | 针对 | 机制与预期收益 | 成本/风险 | 判定 |
| --- | --- | --- | --- | --- |
| A. 受控基线采样（复用 M14-180 探针，零配置变更） | H3（频率/分布） | 多时段、多模式（直连/代理）小预算采样，把"瞬态"变成分布与 SLO 基线；探针已具备全部能力（有界、零重试、阶段计时） | 仅请求预算内的只读流量；无生产风险 | **首选，Phase 0 立即可行** |
| B. VPS 侧参照采样（supervisor 在 VPS 执行，零配置变更） | H1/H2 分离 | VPS 上以 `curl --resolve ndtool.cn:443:127.0.0.1 https://ndtool.cn/aios/download-manifest.json`（**保留主机名/SNI 与证书验证**的本地终结采样）与 `openssl s_time -connect 127.0.0.1:443 -servername ndtool.cn` 握手计时、nginx stub_status——把"VPS 内部处理"与"公网入站路径"分开：本地快+公网慢 ⇒ H1（网络）；本地也慢 ⇒ H2（VPS 处理） | 需 supervisor 登 VPS 执行只读命令；本切片不执行；**采样启动依赖 Phase 0 go**（仅只读事实采集可提前，见 §3 Phase 1） | **Phase 1（采样在 Phase 0 go 之后；只读配置/事实收集可提前准备）** |
| C. Nginx TLS/session/keepalive 调优（**配置变更**） | H1(OCSP)/H2 | 宿主 server 块加 `ssl_session_cache shared:SSL:10m` + `ssl_session_tickets on`（复用握手）、`ssl_stapling on`（证书状态本地化，消除出站 OCSP 查询）、合理 `keepalive_timeout`；若宿主未开 `http2` 可评估（见风险） | 触碰**仓库外**宿主 server 块（共享站点面）；`http2 on` 影响 `/~!frp` WebSocket 长连接与既有站点，必须单独验证；任何变更需先过 §4 前置证明 | **Phase 2，仅在 Phase 1 判定 H1/H2 后** |
| D. Cloudflare/近旁 CDN（**结构性变更**） | H1（入站路径）+ F9（单入口） | 边缘 POP 终结 TLS，绕开跨境入站慢路径；全球 anycast | 影响面大：`/~!frp` WebSocket 经代理的超时/缓冲、证书面（边缘+源站双证书）、真实 IP 日志、DNS 运营迁移；引入新的第三方依赖 | **Phase 3 备选，仅在 Phase 2 证明 Nginx 调优无效后单独立项评估** |
| E. 公共静态资产缓存调优（**契约变更**） | F2 的等待段（边际） | manifest 改短 TTL（如 max-age=60）可让重复水合命中缓存——但违反 F7 的 no-store 契约（发布即时可见性），且 F2 慢在握手不在资产字节 | 发布可见性语义改变（最多 60s 陈旧），产品决策而非工程决策；收益仅限"同用户短时重复访问" | **不推荐现阶段做；列为低优先级产品选项** |
| F. DNS/边缘路径变化 | H1 的路径子集 | 换 DNS 解析路径/多入口（多 A 记录）对比——本机探针不支持 `--dns-servers`（mingw curl 无 c-ares），只能靠 VPS 侧 dig/解析对比与多入口引入 | 多 A 记录 = 新配置面与运维复杂度；收益未知 | **降级为 Phase 1 的辅助观测（VPS 侧解析对比），不单独成阶段** |

## 3. 分阶段计划（预算 / SLO 提案 / 门 / 回滚 / 证据工件）

> SLO 数字为**提案阈值**，最终值由 supervisor 批准确认后才成为门禁。
> 预算总原则（分阶段口径，不再有全局"每阶段 ≤40"声明）：
> **Phase 0 总计 ≤ 72 次 manifest 请求、其中每日 ≤ 24 次（直连 + 可选
> 代理窗合计计入每日预算）**；**Phase 2 的每轮 A/B / 配置验证采样每轮
> ≤ 40 次**；**APK 大对象每阶段 ≤ 2 次**（§5）；每样本间隔 ≥1s
> （探针默认契约）。

### Phase 0 —— 受控基线采样（零配置变更，纯观测，**描述性**）

- 动作：用 `tools/ops/public_edge_stability_probe.py`（已在 CI 门禁内）
  按预算采样：每日 3 个时段窗 × 直连 manifest 8 样本 × 连续 3 天
  （合计 ≤ 72 请求，每日 ≤ 24；可选代理窗从每日预算内扣减）。产出
  TTFB / TLS 段 / 等待段的**描述性统计**（p50/p95、max、失败计数、
  慢窗口（TTFB > 2.5s）频率）。
- 统计诚实边界：**每日 ≤24 样本 / 总计 72 的样本量只支持描述性结论，
  不支持 p99 或 ≥99.5% 成功率这类尾部/低频事件的门禁**——Phase 0
  的门只用慢窗口频率与 p50/p95/max/失败计数表述。p99 ≤ 2.5s 与
  成功率 ≥ 99.5% 仅作为**未来稳态 SLO 提案**保留：成为门禁的前提是
  统计上充分的样本量（连续多周基线）或接入生产遥测（Nginx 访问日志
  /指标），Phase 0/本设计不宣称已具备该前提。
- go/no-go：**go** = 慢窗口频率 ≥ 5%（值得继续 Phase 1）且分布数据
  成型；**no-go/关闭** = 慢窗口频率 < 1% 且 p95 处于提案水平 ⇒ 记录
  为可接受瞬态，优化终止（写结论文档即收口）。
- 证据工件：`.verify/m14-181-*/phase0-baseline-*.json`（探针报告原样）
  + 阶段小结（追加进本 README 的后续修订或独立 evidence 目录）。
- 回滚：无需（零变更）。

### Phase 1 —— VPS 侧参照采样（零配置变更，supervisor 执行）

- 前置与依赖：**采样启动依赖 Phase 0 go**（依赖关系与选项表 B 一致）；
  提前允许的只有**只读配置/事实收集的准备工作**（`nginx -T` 摘录、
  dig/解析对比、命令清单演练），不含任何对外采样。
- 动作（全部只读）：VPS 上以 **`curl --resolve ndtool.cn:443:127.0.0.1
  https://ndtool.cn/aios/download-manifest.json`** 做本地终结采样
  （--resolve 把主机钉到回环但**完整保留主机名/SNI 与证书链验证**——
  绝不使用 `-k`/`--insecure`，也不退化为裸 IP URL，那会丢 SNI 并使
  证书验证失真）；**`openssl s_time -connect 127.0.0.1:443 -servername
  ndtool.cn`** 握手计时（`-servername` 保 SNI；s_time 本身不做完整
  证书验证，只测握手耗时，结论不用于安全判定）；`nginx -T`（只读
  导出，核对宿主 server 块现状：ssl_session/keepalive/http2/OCSP
  实况——这是 H1/H2 判定与 Phase 2 设计的**事实输入**）；辅以
  dig/解析对比（选项 F 的降级形态）。本地终结采样 10 样本级，计入
  Phase 1 预算（沿用分轮 ≤ 40 口径）。
- go/no-go：本地终结 p95 ≤ 300ms 且公网慢窗口复现 ⇒ **H1 成立**
  （公网入站路径）→ 跳过 Phase 2 的 Nginx 内部调优、直接评估 Phase 3；
  本地终结也慢（TLS 或等待段 ≥1s）⇒ **H2 成立**（VPS 处理面）→
  进入 Phase 2。
- 证据工件：VPS 侧命令输出（supervisor 落档到 `.verify/` 或 evidence
  附录）；`nginx -T` 相关片段只入**结论摘录**（完整配置含宿主站点面，
  不整份入库）。
- 回滚：无需（零变更）。

### Phase 2 —— Nginx TLS/session/keepalive 调优（**首个配置变更阶段**）

- 前置：Phase 1 判定 H2；过 §4 前置证明；supervisor 批准。
- 动作（候选按最小步进逐项启用，每项独立 A/B）：①`ssl_session_cache
  shared:SSL:10m` + `ssl_session_tickets on`；②`ssl_stapling on` +
  `ssl_stapling_verify`（若 Phase 1 证实 OCSP 出站查询在慢路径上）；
  ③`keepalive_timeout` 对齐实际水合模式；④（独立评估，风险最高）
  宿主 `http2`——必须先在预发/复现环境验证 `/~!frp` WebSocket 与
  既有站点行为，不与 ①②③ 混在同一步。
- 预算：每项变更前后各一轮基线同款采样（**每轮 ≤ 40**，计入 §3 总则
  的分轮口径）。
- SLO 门（提案，仅用该样本量可辩护的统计量）：变更后 TTFB **p95**
  相对基线改善 ≥ 30%、**max 与失败计数不劣化**、慢窗口频率下降
  ≥ 50%。p99 与 ≥99.5% 成功率**不作为本轮门**（样本量不支持，见
  Phase 0 统计诚实边界），留待稳态 SLO（充分样本量或生产遥测）后
  再行门禁化。
- 回滚触发器（任一即回滚）：任一轮采样成功率 < 99%；`nginx -t`
  失败或 reload 后 5xx 出现；`/~!frp`（frpc 隧道）或既有站点任何
  行为异常；SLO 门未达。回滚 = 还原宿主配置（变更前 `nginx -T` 备份）
  + 一次复核采样确认回到基线。
- 证据工件：变更 diff（宿主配置前后摘录）、A/B 探针报告、回滚演练
  记录。

### Phase 3 —— 结构性选项（Cloudflare/CDN 或多入口，**仅立项评估**）

- 前置：Phase 1 判定 H1 且 Phase 2 无适用项（或 Phase 2 SLO 门未达）。
- 动作：单独立项写评估文档（证书面、`/~!frp` WS 兼容性验证方案、
  DNS 迁移步骤、回滚到直连的路径）；本设计不预设结论。
- 回滚触发器（若最终实施）：DNS 切回直连记录（TTL 预先调低）。

## 4. 生产变更前置证明 + 确切配置面清单

**任何生产配置变更（Phase 2 起）前必须证明并落档：**

1. Phase 0/1 的分布与归因证据（慢窗口频率 + H1/H2 判定）已入 evidence；
2. 目标变更逐项列出预期影响的指令与不影响的既有面（尤其 `/~!frp`
   WebSocket、宿主共享站点、CORS 透传语义——边缘零 CORS 契约不变）；
3. `nginx -t` 通过 + 变更前 `nginx -T` 备份已存（仓库外安全位置）；
4. 回滚步骤与触发器（§3 Phase 2）已写明并可在 ≤10 分钟内执行；
5. 请求预算与采样计划（前后对照）已获 supervisor 批准；
6. APK 大对象路径按 §5 独立预算，绝不混入验证采样。

**后续会触碰的确切配置面（本切片一概不动）：**

| 面 | 位置 | 性质 |
| --- | --- | --- |
| VPS 宿主 443 server 块（`listen`/`ssl_*`/`keepalive`/`http2`/OCSP） | VPS `/etc/nginx/conf.d/<真实站点>.conf`（仓库外运维面，实际内容以 Phase 1 `nginx -T` 摘录为准） | 仓库外，supervisor 执行 |
| AIOS 边缘 location 片段（若需下沉 http2/缓存指令到片段内） | 仓库内 `infra/edge/nginx.public-base-path.example.conf` + VPS `/etc/nginx/aios-base-path.locations.conf` 实际部署副本 | 仓库内模板先行，VPS 同步 |
| manifest/APK 缓存契约（若选项 E 被产品决策采纳） | 同上片段 ⑦⑧ 的 `Cache-Control` 行 + `docs/PUBLIC_EDGE_DEPLOYMENT.md` §3G | 契约变更，需产品+supervisor 双批 |
| DNS/CDN（Phase 3） | DNS 运营商控制面（ndtool.cn 记录） | 仓库外，独立立项 |
| 探针工具自身（如需 resolver 选择/h2 能力） | 仓库内 `tools/ops/public_edge_stability_probe.py` + `tests/ops/` | 常规代码切片（CI 已门禁） |

## 5. APK 大对象独立预算路径（不并入 manifest 冒烟）

- APK（约 8MB）与 manifest（1.4KB）**物理量级差 4 个数量级**，混采
  会互相污染时序观测（F4 证明 APK 瓶颈在带宽而非边缘处理）。
- 规则：**manifest 冒烟循环永不包含 APK**（探针结构上已保证：大资产
  只经 `--large-asset-*` 三参触发、恰执行一次、不进采样循环）；
  APK 校验下载在任何阶段每轮 ≤ 2 次、单次超时单独设 60s（不复用
  manifest 的 15s）、独立报告文件命名（`*-apk.json`）。
- APK 侧 SLO 提案：60s 超时内完整下载成功率 ≥ 99%、SHA256 与发布值
  一致；带宽劣化本身（F4 观测）是**路径事实**，不由边缘配置修复，
  若 Phase 3 引入 CDN 才可能改善 APK 下载体验（届时 APK 专项预算
  单列）。

## 6. 诚实边界

1. 本切片**零新增公网请求、零生产变更**：全部 F 系事实引用 M14-180
   已落档证据；H 系假设全部未证明。
2. 样本量极小（≤9 直连样本、单日时点）——SLO 阈值是提案不是结论，
   Phase 0 存在的意义就是把样本量补到能下结论。
3. Phase 1 的 VPS 侧命令、Phase 2/3 的一切配置与 DNS 操作都归
   supervisor 执行；本文件只是设计与门禁定义。
4. HTTP/2 维度（F5/F8/H5）在本机不可验证；若 Phase 1 的 VPS 侧
   `nginx -T` 显示宿主已具备 h2，可在 Phase 1 顺带补一组 VPS 本地
   h2 参照采样（仍属只读、计入预算）。
5. 选项 E（manifest 缓存）与 F7 契约冲突，本设计明确不推荐现阶段
   采纳，最终归产品决策。
6. 本切片交付为 branch + 单 commit；PR 创建即止；合并决策归
   supervisor 审查（supervisor 审查与 remote 发布在其后进行）。
