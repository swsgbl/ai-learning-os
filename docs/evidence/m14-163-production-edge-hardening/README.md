# M14-163：公共边缘安全头生产激活证据回填（Production Edge Hardening Evidence）

- 切片：分支 `m14-163-production-evidence`（worktree
  `m14-163-production-evidence`），基于 main
  `e2a916a7218720999a2a1c38baea8efebd4ffde5`（PR #252 merge =
  M14-163 模板/docs/tests 契约切片合入，精确基点）。
- 定性：**docs-only 证据回填**——边缘模板 reload、家机 env 落地、
  API 重启、公网复测、preflight 与验收账号清理均由 supervisor
  （Codex）于 2026-09-27 真实执行；本切片只把结果落档为 tracked
  文档，零运行时代码、零模板、零测试、零 compose、零 env、零
  远端/Nginx/Docker 变更，不启动/停止任何生产服务，不读写任何
  secret。
- 本 README 为唯一入库证据文件；supervisor 执行现场的原始工件
  位于仓库外 gitignored 目录（`<仓库盘>/.verify/m14-163-public-edge-hardening/`
  等），内容一律不回显、不入库。Windows 本地绝对路径在入库文档
  中以 `<仓库盘>` 占位（仓库既有惯例）。

## 1. 基点与 CI

| 项 | 值 |
| --- | --- |
| main 基点 | `e2a916a7218720999a2a1c38baea8efebd4ffde5`（PR #252 merge，M14-163 契约切片合入） |
| PR #252 | state=MERGED，head `m14-163-public-edge-hardening`，merge commit 即上表基点 |
| PR #252 checks | CI **5/5 绿**（五标准 job 契约：Android / Docker / Release tools / Web / API，全部 SUCCESS） |

生产激活路径与 M14-163 契约切片 runbook §9 预告完全一致：边缘
片段 reload（安全头生效）→ 家机 env 落地 `AIOS_CORS_ORIGINS`
并重启 API（api-cors 通过）→ 复跑正式 preflight。

## 2. 远端 Nginx 边缘部署（supervisor 执行）

| 项 | 值 / SHA256 |
| --- | --- |
| 远端 `/etc/nginx/aios-base-path.locations.conf` | 部署仓库 canonical 模板 `infra/edge/nginx.public-base-path.example.conf`（M14-163 契约切片定版版次），SHA256 `21d593cc490527dee653862df9aa3e69a2a48ac01b164ecbee2783595cdaa660` |
| 校验与生效 | `nginx -t` 成功，reload 成功，Nginx 服务 active |
| 回滚备份 | `/etc/nginx/aios-backups/m14-163/aios-base-path.locations.conf.before-m14-163`，SHA256 `359ac232ded45f5d7aea49ee24532f44511e85d913fc670d2bb9c4f9d84244f9` |

- 回滚备份路径为远端受控路径描述，备份内容不入库、不回显。
- 回滚纪律：远端 Nginx 变更失败时，必须用上表备份文件回滚（见
  §7）。

## 3. 生产 API 状态与 env（supervisor 执行）

| 对象 | 值 | 状态 |
| --- | --- | --- |
| 生产 API 镜像 | `aios/api:m14-124-production`（**未变更**，M14-124 以来锚点不动） | 容器 healthy |
| 非敏感 env 配置键 | `AIOS_CORS_ORIGINS`、`AIOS_AUTH_COOKIE_SAMESITE`、`AIOS_AUTH_COOKIE_SECURE` | 已生效（值遵循 runbook §8 定版口径：CORS allowlist 显式包含应用源 origin；Cookie 走 Secure + SameSite 公网形态） |
| 当前 env 文件 | SHA256 `96fef7b1b62b50e30cfcde8de33d61e7148da708d07fb3424bcd73c5c83d0278` | 内容不回显、不入库 |
| env 变更前备份 | SHA256 `02f5fc2d778431d7fc41559a1add321fcbe50db93ccd57c79fecd798724fb9c5` | 与 M14-160 §3 记录的当时 `infra/env.production-recovery` 哈希逐字节一致（变更前状态可追溯） |

- env 备份位于仓库外受控目录，路径与内容均不入库；生产 env 与
  全部 secret 只以哈希/键名留证。
- env 变更为运行时行为（重启 API 生效），仓库内
  `infra/env.production-recovery` 模板未被本切片触碰。

## 4. 公网复测矩阵（supervisor 实测，2026-09-27）

三安全头 = `Strict-Transport-Security`、`X-Content-Type-Options:
nosniff`、`Referrer-Policy: strict-origin-when-cross-origin`（M14-163
契约：五公共 location 逐 location 显式 + `always`）。

| 请求 | 状态码 | 三安全头 | 备注 |
| --- | --- | --- | --- |
| `https://ndtool.cn/aios` | **200** | 全在 | — |
| `https://ndtool.cn/aios/` | **301** | 全在 | 归一化到无尾斜杠 canonical 入口 |
| `https://ndtool.cn/aios/health` | **200** | 全在 | — |
| 既有站点 `/` | **200** | — | 未受影响 |
| 既有站点 `/health` | **200** | — | 未受影响 |
| 边缘 CORS 面 | — | — | **边缘零 CORS**（无任何 `Access-Control-*` 由边缘代答，与契约切片零 CORS 设计一致） |

## 5. 正式 preflight 报告（M14-163 终版）

- 报告：`<仓库盘>/.verify/m14-163-public-edge-hardening/public-edge-preflight.after-m14-163-final.json`
  （仓库外 gitignored，本切片收口时对报告 JSON 只读复核结构一致：
  `summary passed=6 / failed=0 / total=6`、`exit_code=3`、
  `generated_at=2026-09-27T12:49:21+00:00`、
  `manual_attestation.status=pending`）。
- 自动检查 **6/6 pass**：cert-chain（app + api 双端）、app-https
  （200 + 安全响应头齐全）、api-health（`/aios/health` 200）、
  api-cors（preflight 精确回显应用源 origin 且允许凭据）、
  api-cookie（登录 `Set-Cookie` Secure/SameSite/HttpOnly 属性
  契约）。
- `exit_code=3` **仅因人工 mobile checklist pending**（非自动项
  FAIL）：4G/5G 实网、跨源 cookie、考试全流程、公网语音、TURN
  relay、Android APK、HarmonyOS/iPhone PWA 等人工验收项尚未
  执行——这是 §9 冻结口径的直接原因。
- M14-160 §7 记录的 basePath preflight 结构性阻塞已由 M14-161
  （受控 base path 支持）解除，本报告即 basePath 拓扑下正式
  preflight 的首个全绿自动结果。

## 6. 一次性验收账号清理（supervisor 执行）

- preflight api-cookie/api-cors 验收所用一次性账号已从生产
  PostgreSQL 删除（`DELETE 1`，复查 `count=0`）。
- 仓库外受控凭据文件（`--login-credentials-file` 纪律对象）
  已清空（本切片收口时只读复核文件长度 0）。
- 用户名/密码等凭据值**绝不记录**、绝不入库。

## 7. 生产操作纪律（supervisor 口径，必须遵守）

1. 家机 compose 一切操作必须显式 `-p aios-m14-03-production-rehearsal`
   （项目名锚定，防误触其他栈）。
2. 远端 Nginx 变更失败时，必须用 §2 回滚备份
   （`/etc/nginx/aios-backups/m14-163/aios-base-path.locations.conf.before-m14-163`）
   回滚，不得现场手改。

## 8. 本切片的验证（docs-only 口径，真实执行）

- 基点与 PR #252 merge commit 一致（`git rev-parse` 只读复核）；
- preflight 终版报告 JSON 只读复核（§5 六项结构字段一致）；
  凭据文件长度 0 只读复核（§6）；
- 聚焦文档契约测试（零网络）：`test_public_edge_nginx_base_path.py`、
  `test_edge_deployment_templates.py`、
  `test_versioning_rollback.py::test_version_sources_in_sync`（CHANGELOG
  顶部版本守卫）、`test_production_monitor.py::test_r1_docs_commit_wording_sweep`
  （状态文档措辞守卫）——统计见 PROJECT_STATUS 条目；
- `git diff --check` 干净；新增行 secret / 本地绝对路径（盘符或
  根路径形态，正反斜杠变体）/ U+FFFD 扫描 **0 命中**（Windows
  路径均为 `<仓库盘>` 占位形态）。

## 9. 诚实边界

1. 本切片是**生产激活后的证据回填**：生产操作已由 supervisor
   真实执行且自动验收 6/6 通过，但本切片本身零生产触碰、零
   secret 读写。
2. **人工 mobile checklist 仍 pending**（§5）：按 runbook §9
   纪律与 M14-160 §9 冻结口径，仍不写「公网生产可用」/
   production ready——本证据记录的是「生产已执行、自动验收
   通过、人工清单 pending」状态，不夸大。
3. 验收均为时点证据，不承诺窗口外持续健康；公网入口可用性
   依赖家机 frpc 常驻与 VPS Nginx/frps 存活。
4. 公网语音（TURN/TLS + 真实语音 E2E）就绪口径不在本证据范围
   （turn-tls optional 缺口不变）；`release_ready` /
   `production_ready` 语义（cockpit 恒 false 口径）未被本切片
   触碰。
5. 生产 env 与全部 secret 只以哈希/脱敏事实留证，绝不回显内容；
   `<仓库盘>` 为占位符，非真实路径泄漏。
6. 回滚锚：env 备份（§3）+ 远端 Nginx 备份（§2）+ 镜像 tag 锚点
   机制（runbook §10）。
7. 本切片交付为 branch + PR，PR 创建即止；合并决策归 supervisor
   审查（supervisor 审查与 remote 发布在其后进行）。
