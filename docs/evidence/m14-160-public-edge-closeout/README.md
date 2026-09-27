# M14-160：公网边缘生产证据收口（Public Edge Production Evidence Closeout）

- 切片：分支 `docs/m14-160-public-edge-closeout`（worktree
  `m14-160-public-edge-closeout`），基于 main
  `25bba2cb8c6ab7a12a41014e74a8d139a951dd73`（PR #248 merge =
  M14-160 healthcheck 配置切片合入，精确基点）。
- 定性：**docs-only 证据回填**——公网边缘上线、镜像构建、容器切换、
  远端 Nginx 变更均由 supervisor（Codex）于 2026-09-27 真实执行；本
  切片只把结果落档为 tracked 文档/证据，零运行时代码、零 compose、
  零基础设施、零部署状态、零 secrets 变更，不运行任何部署命令。
- 本 README 为唯一入库证据文件；supervisor 执行现场的原始工件位于
  仓库外 gitignored 目录（`<仓库盘>/.verify/m14-160-public-edge/` 等），
  内容一律不回显、不入库。Windows 本地绝对路径在入库文档中
  以 `<仓库盘>` 占位（仓库既有惯例）。

## 1. 基点与 CI（收口时 gh 只读复核）

| 项 | 值 | 复核 |
| --- | --- | --- |
| main | `25bba2cb8c6ab7a12a41014e74a8d139a951dd73`（PR #248 merge，M14-160 healthcheck 合入） | `git log`/`gh api commits` 一致 |
| PR #248 | state=MERGED，head `m14-160-public-web-healthcheck` | `gh pr view 248` |
| PR #248 checks | 五标准 job 全部 SUCCESS（Android / Docker / Release tools / Web / API） | `gh pr view --statusCheckRollup` |
| main push CI | run `36314059971`（run_number 627，event=push，head_branch=main，conclusion=success），5/5 jobs success，job 集合与既有五 job 契约精确一致 | `gh api actions/runs?head_sha=25bba2c…` + jobs 查询 |

## 2. 生产栈事实（委派证据 + 收口时 `docker ps` 只读复核）

| 对象 | 值 | 状态 |
| --- | --- | --- |
| 生产 Web 镜像 | `aios/web:m14-160-public-edge-beta` = `sha256:9f42d0e52d1ffb594844eb5ae9596cdd94af56118eb16ca7caffe68678177ee7` | 收口复核 image ID 一致 |
| 生产 Web 容器 | `cb32feb1d3868092916a5d8b2dfb85ec413aed2cf680d7d0ddf50442bf9aa4d6` | running + **healthy**（复核一致） |
| 生产 API | 未变更：`aios/api:m14-124-production` | 容器 `77bb87569d987a5d4bb78d3d53da177558c522ae05a05c0dbaf00c5db76f6592`，healthy（复核一致） |

M14-160 行为验证：`apps/web/Dockerfile` 持久化
`AIOS_WEB_HEALTH_PATH`，compose healthcheck 在容器内展开
`$${AIOS_WEB_HEALTH_PATH:-/}`——root 与 `/aios` 两种构建形态下容器
均 healthy（M14-159 镜像的 unhealthy 缺陷由此消除）。

生产恢复演练（supervisor 执行）：recovery dry-run 与 enforce 均
pass——pin 一致 **9/9**；API、LiveKit、MinIO、Postgres、Redis、Web
全部 healthy/running；healthy 栈跳过 `up`。

本地验收：端口 3011 与 3012 的 `/aios`、`/aios/login` 均返回 200；
API `/health` 返回 200；斜杠形态归一化到 `/aios` 生效。

## 3. 生产 env 备份（只记哈希，内容不回显）

| 文件 | bytes | SHA256 |
| --- | --- | --- |
| 切换前备份 `<仓库盘>/.verify/m14-160-public-edge/env.production-recovery.before-m14-160.backup` | 790 | `211F95AB4C7490414A06CDE837C4988B358CA484F845B31C253FE305EF9D266D` |
| 当前 `infra/env.production-recovery` | 790 | `02F5FC2D778431D7FC41559A1ADD321FCBE50DB93CCD57C79FECD798724FB9C5` |

收口时对两文件本地重算 SHA256，与上表**逐字节一致**。env 内容
（密钥/连接串等）绝不回显、不入库、不入任何 canonical 证据。

## 4. 公网边缘事实（frp 链路 + 远端 Nginx）

### 4.1 frp 链路

- 家机 `frpc` 于 2026-09-27 14:29（本地时间）启动；配置
  `<仓库盘>/.aios-public-edge/frpc-ndtool-wss-443.toml`（WSS/443
  入口形态）；
- frpc 代理目标：Web `127.0.0.1:3012`（M14-157 回环网关）、API
  `127.0.0.1:8000`——均 loopback，家机零新增入站；
- 内部路由标签：`app.internal.aios` 与 `api.internal.aios`（frps
  vhost Host 路由用，无 DNS、公网不可直达）；
- 远端 `aios-frps` PID 335753；loopback 控制端口 7000 与 vhost 端口
  8080 在线。

### 4.2 边缘配置源更正（supervisor 口径，必须遵守）

M14-159 工作目录残片
`<仓库盘>/.verify/m14-159-public-edge/aios-base-path.locations.conf`
**只有 110 字节且无效，不是部署源**（收口时复核确为 110 bytes），
任何证据不得把它记载为已部署配置。真实部署源是仓库 canonical 模板
`infra/edge/nginx.public-base-path.example.conf`（5235 bytes，
SHA256 `18B5DDE2AFDB0B51EBB3695EC6D43EAE694064274B125855A55F52B38F76D540`，
收口时本地重算一致）。

### 4.3 远端切换（2026-09-27 19:08 +08 完成）

| 项 | 值 / SHA256 |
| --- | --- |
| 远端 `/etc/nginx/aios-base-path.locations.conf` | `18B5DDE2AFDB0B51EBB3695EC6D43EAE694064274B125855A55F52B38F76D540`（与仓库模板逐字节同哈希） |
| 生效 sites-enabled 与 sites-available `ndtool` | `627ca88418d7664f7b8675f2d23e16957bbab0cb68fc05ee8722be7ea00d3df9` |
| 备份① `/root/aios-m14-160-nginx-backup/ndtool.sites-enabled.20260927_1908_m14_160` | `de020c9a25a0027ae7d230d0c6835b0a6272800942bca1dfadb81f9e034b6ec4` |
| 备份② `/etc/nginx/sites-available/ndtool.backup.20260927_1908_m14_160` | `3cbd1a6b0e6c97794c96666a2f309f84e1b44b73b854f98637c93516660978af` |
| 校验 | `nginx -t` 在 reload 前后均通过；reload 成功 |

## 5. 公网验收（委派时点 supervisor 实测；收口时 Claude 只读复核一致）

| 请求 | 结果 |
| --- | --- |
| `https://ndtool.cn/aios` | **200**（HTML 18031 bytes，引用 `/aios/_next/…`） |
| `https://ndtool.cn/aios/login` | **200** |
| 斜杠形态（canonical 入口 + 尾斜杠） | **301** 归一化到无尾斜杠 canonical 入口（一跳） |
| `/aios/_next/static/media/…woff2` | **200**，`Content-Type: font/woff2` |
| `https://ndtool.cn/aios/api/v1/system/privacy` | 预期认证响应 **401**，`WWW-Authenticate: Bearer`，含 API `X-Request-Id` |
| `https://ndtool.cn/aios/api/v1/voice/providers` | 预期认证响应 **401**，同上 |
| 既有站点 `/` | 切换前后均 **200** |
| 既有站点 `/health` | 切换前后均 **200** |
| 既有 `/api/v1/health` | 切换前后均 **404**（既有行为，非回归指标） |

- 401 + `WWW-Authenticate: Bearer` + API `X-Request-Id` 证明公网请求
  经远端 Nginx → frps vhost → frpc → 家机 FastAPI 的完整链路（真实
  后端语义，非边缘静态应答）。
- 收口复核（本切片执行时）：上表全部端点状态码/归一化行为/`font/woff2`
  /`WWW-Authenticate`/`X-Request-Id` 逐项一致；`/aios` HTML 复核为
  18603 bytes（Next 动态 RSC payload 字节数随请求时点浮动；委派时点
  记录值 18031 bytes，定性结论——200 + `/aios/_next/…` 资产引用——
  完全一致）。

## 6. 语音状态（委派时点快照 + supervisor followup 终态复核）

| 引擎 | 委派时点 | followup 复核（2026-09-27，PR #249 审查轮） |
| --- | --- | --- |
| FunASR | managed-running，PID 45921，`/health` 200，SenseVoice CPU 模型已加载 | `/health` 200（`models_loaded: ["sensevoice"]`，loopback 8010 只读复核） |
| CosyVoice | managed-running，PID 45978，端口监听，模型加载中；`/health` **503** | **`/health` 200**——模型加载完成（`Fun-CosyVoice3-0.5B-2512`，loopback 8011 只读复核） |

委派边界原话：「除非 Codex 后续提供更新证据，不宣称 CosyVoice 生产
就绪」。supervisor followup 已给出更新证据（CosyVoice 终态健康 200），
本切片收口时本地只读复核一致——健康缺口消除并如实落档（委派时点
503 保留为历史快照，不改写）。公网语音（TURN/TLS + 真实语音 E2E）
就绪口径仍不在本证据范围（turn-tls optional 缺口不变）。

## 7. basePath preflight 阻塞（supervisor followup 记录）

§9 自动化验收工具 `tools/ops/public_edge_preflight.py` 对已上线的
basePath 公共入口（`https://ndtool.cn/aios` + `/aios/api/v1/…`）
**结构性不可用**，阻塞机理（源码与实测实证，非推测）：

- 端点解析 `parse_public_https_url` 是 **origin-only** 契约（Round 4
  加固：端点参数可能携带凭据，path/query/userinfo/fragment 一律
  fail-closed 在入口拒绝）——`--app-url https://ndtool.cn/aios` 直接触
  发「端点不得携带 path（origin-only）」；该契约由
  `test_public_edge_preflight.py` 参数化负例锁定（带 path URL 属
  Round 4 origin-only 拒绝集）；
- 探测路径固定为 origin 相对路径：`app_url` 探 `/`、`api_url` 探
  `/health` 与 `/api/v1/auth/login`——basePath 拓扑下 AIOS 的真实
  路径（`/aios`、`/aios/api/v1/…`）工具无法表达；
- 因此把裸 origin `https://ndtool.cn` 传给工具也不会得到有效验收，
  而是探测到**既有站点自身**：`/` 200、`/health` 200、
  `/api/v1/auth/login` GET 405、`/api/v1/system/privacy` 404（均为
  既有站点行为，收口时只读实测）——对既有站点是假阳性，对 AIOS
  basePath 入口零证明力。

结论：§9 正式自动 preflight 对路径制入口被阻塞，直到工具获得显式
basePath 支持（独立代码切片，超出本 docs-only 范围）；在此之前，该
入口的公网验收以本证据 §5 的显式端点矩阵 + 人工 4G/5G 清单为准，
「公网生产可用」宣告继续冻结。

## 8. 本切片的验证（docs-only 口径，真实执行）

- 基点/CI/容器/公网端点/三处 SHA256（env 备份、当前 env、仓库 Nginx
  模板）全部只读复核一致（§1-§5）；
- 聚焦文档契约测试：`test_public_edge_nginx_base_path.py`（含 runbook
  §3E 公共入口契约）、`test_edge_deployment_templates.py`（runbook
  主题契约）、`test_versioning_rollback.py::test_version_sources_in_sync`
  （CHANGELOG 顶部版本守卫）、`test_production_monitor.py::
  test_r1_docs_commit_wording_sweep`（状态文档措辞守卫）——全部通过
  （统计见 PR 描述与 PROJECT_STATUS 条目）；
- `git diff --check` 干净；新增行 secret / 本地绝对路径（盘符或根路径
  形态，正反斜杠变体）/ U+FFFD 扫描 **0 命中**（Windows 路径均为
  `<仓库盘>` 占位形态）；
- followup 轮（PR #249 审查）追加：FunASR/CosyVoice loopback 健康
  只读复核（8010/8011 双 200，§6）；preflight origin-only 契约源码
  与测试负例核验（§7）；裸 origin 既有站点行为只读实测（§7）；聚焦
  文档契约测试四套件复跑全过（40 passed，统计随 PR 更新）。

## 9. 诚实边界

1. 本切片是**上线后的证据回填**：公网边缘已由 supervisor 真实上线并
   通过上表验收，但本切片本身零部署、零生产触碰、零 secret 读写。
2. **§9 正式自动 preflight 对该入口结构性被阻塞（§7：工具
   origin-only + 探测路径固定，不能表达 basePath 拓扑）**，人工
   4G/5G 清单亦未执行——按 runbook §9 纪律，仍不写「公网生产
   可用」；本证据记录的是桌面/HTTP 层验收。
3. 验收均为时点证据，不承诺窗口外持续健康；公网入口可用性依赖家机
   frpc 常驻与 VPS Nginx/frps 存活。
4. CosyVoice 委派时点 `/health` 503 如实保留为历史快照，followup
   终态 200 已落档（§6，supervisor 更新证据 + 本地只读复核）；公网
   语音就绪口径不在本证据范围；turn-tls optional 缺口不变，
   `release_ready` / `production_ready` 语义（cockpit 恒 false 口径）
   未被本切片触碰。
5. 生产 env 与全部 secret 只以哈希/脱敏事实留证，绝不回显内容；
   `<仓库盘>` 为占位符，非真实路径泄漏。
6. 回滚锚：env 备份（§3）+ 两份 ndtool Nginx 备份（§4.3）+ 镜像
   tag 锚点机制（runbook §10）。
