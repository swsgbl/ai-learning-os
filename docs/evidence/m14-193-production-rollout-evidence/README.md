# M14-193：生产滚动与公开 basePath 验收证据（Production Rollout Evidence）

## 0. 交付与边界

- 切片：worktree `ai-learning-os-worktrees/docs-m14-193-production-rollout-evidence`，
  分支 `docs/m14-193-production-rollout-evidence`，原始 authoring 基于
  main `4993ffc96fae957249658cf314c6ace465857474`（PR #283 merge =
  M14-194 monitor basePath 修复，精确证据基点）；发布前分支已与当前
  main `a62ae76d18d999ebc847c5f13bb968bc85340d0a`（PR #284 merge =
  M14-194H AGC signing preflight）合并，合并不改变本切片 docs-only
  边界。
- 定性：**docs-only 切片**。本切片零 app/运行时/模板/测试代码变更，
  零 production 操作，只把 supervisor 已完成的生产滚动、备份、端点、
  公共浏览器与监控事实落档；supervisor 审查与 remote
  发布（push/PR/合并）在其后进行。
- 原始证据保存在 gitignored 的 canonical
  `artifacts/m14-193-production-cutover/`。本 README 是唯一入库证据文件；
  生产 env、compose 备份、数据库 JSON、上传对象与 APK 原文均不入库、
  不回显。备份只以 manifest 哈希和安全元数据留证。
- 本切片未执行 production、Docker、HTTP、调度任务、CC Switch 或其他
  用户进程操作；tracked 文档不包含 env、compose、数据库、上传对象或
  APK 原文。

## 1. 运行时锚点与滚动范围

| 项 | 值 |
| --- | --- |
| 运行时 build base | `50bd66a8c5695bae76917cc3fc2f686f72133687` |
| API 镜像 | `aios/api:m14-193-production` = `sha256:73dca646b6a455132a3d76806b2f0706b16515675509b5d7604af032311a8dae` |
| Web 镜像 | `aios/web:m14-193-production` = `sha256:f1131e6284147c30b382ff55aa8f745f33ffc4b21d9c571c71ce006028ff767f` |
| Web public API base | `NEXT_PUBLIC_API_BASE_URL=https://ndtool.cn/aios` |
| Web base path | `NEXT_PUBLIC_BASE_PATH=/aios` |
| Web image env health path | `/aios` |

滚动顺序为 API 然后 Web。两者均只以既有镜像执行
`--no-build --no-deps` recreate；Postgres、Redis、MinIO、LiveKit、
语音 sidecar 与其他编排依赖未被 recreate。该滚动是 supervisor 生产操作
事实，本 docs-only 切片不重复执行。

未执行回滚。若后验失败，回滚语义是：

1. 仅恢复 `AIOS_IMAGE_TAG` / `AIOS_WEB_IMAGE_TAG` 两个 image tag key；
2. API 回到 `aios/api:m14-124-production`，Web 回到
   `aios/web:m14-179-public-download-production`；
3. 仍只对 api/web 执行 `--no-build --no-deps` recreate；
4. 恢复后重跑 health、公共端点、浏览器验收与 monitor。

数据侧恢复材料见 §2；回滚本身未演练，不得宣称已验证。

## 2. 切换前保护

采纳的 pre-cutover backup 为 `aios-backup-v1`：

- 30 个表；
- 4 个文件（数据库、一个上传对象、公开 compose 与生产 env 备份）；
- manifest SHA256：
  `381C49875C23F4CF35F49CED864522A200D6B46614C72D42B52DFB34EB0612EC`；
- env pre-cutover SHA256：
  `7A8561A66531E427E824D0355651237BA067F7FF35795030A99449277CA5BA09`。

一次更早的宿主备份尝试失败，未触碰数据；后续成功完成的上述备份才是
pre-cutover backup。生产 env 值、compose 内容、数据库 JSON 与上传对象
原文均不进入本 README。

只读 production preflight 结果为 **5 pass / 0 pending / 0 fail /
0 not_configured**：数据库连通、Alembic current == head ==
`0027_audit_chain`、审计链 valid、历史治理计数为 0、库外锚定
up-to-date。`production-preflight.json` SHA256：
`C99CC6A24ED440AE4332902588E7DDF6AB92763C4BF5871C96DD67467FD1A1CB`。

## 3. 端点与公共工件

切换后验收的 HTTP 结果均为 **200**：

- local API health；
- local Web `/aios`、`/aios/login`、`/aios/download`；
- public `https://ndtool.cn/aios`、`/aios/health`、`/aios/download`、
  `/aios/download-manifest.json`、`/aios/manifest.webmanifest`。

公共 edge preflight 为 **5/6 pass**。唯一 fail 是
`api-cookie:ndtool.cn`：未提供真实 credentials file 时工具拒绝猜测跨源
cookie 属性，属于 fail-closed 边界，不是已证实的生产缺陷。

Android release APK：

- URL：`https://ndtool.cn/android/ai-learning-os-0.1.0-release-signed.apk`；
- size：`8029570` bytes；
- SHA256：
  `1246C3EFB5DA5732088DD95DFFECF6F84FC1F14617EED45D38F701E28DD4634D`。

manifest 路径是宿主根 `/android/...`，不是 Web basePath 下的
`/aios/android/...`。本轮未做 Android 真机安装冒烟。

## 4. 公共浏览器验收

证据文件：`artifacts/m14-193-production-cutover/browser/browser-acceptance.json`。
Playwright 覆盖 desktop-home、mobile-home、mobile-download 三个 case，
整体 `passed=true`：

| 断言 | desktop home | mobile home | mobile download |
| --- | --- | --- | --- |
| HTTP | 200 | 200 | 200 |
| visible content | pass | pass | pass |
| screenshot non-blank | pass | pass | pass |
| page error | 0 | 0 | 0 |
| request failure | 0 | 0 | 0 |
| unexpected console error | 0 | 0 | 0 |
| horizontal overflow | 无 | 无 | 无 |

desktop-home 与 mobile-home 各有恰好 2 条匿名 resource-console 401，
均在预期列表内；mobile-download 无 console error。这些 401 是未登录访问
受保护资源时的认证边界，不是渲染缺陷。三张截图非空且已归档。

边界：这是 Playwright mobile emulation，不是物理手机人工验收；未证明
真实蜂窝网络、跨源登录 cookie、考试全流程或公网语音链路。

## 5. 监控闭环与 M14-194 修复

1. initial full monitor `2026-09-30T14:11:40Z`：28 ok / 2 warn /
   2 critical，`monitoring_ready=false`。两个 critical 均为 Web
   `web-root` / `web-login` 404，原因是旧 monitor hardcoded root routes，
   与 basePath Web 实际路由不匹配。
2. API-only 复核先出现无基线 full-tail warn；随后
   `2026-09-30T14:12:51Z` 为 **26 ok / 0 warn / 0 critical**，
   `monitoring_ready=true`，证明 critical 不是 API 运行故障。
3. M14-194 修复以 PR #283 合入，merge commit 为本 README 的原始
   authoring 基点 `4993ffc96fae957249658cf314c6ace465857474`；发布前
   分支另行与当前 main `a62ae76`（PR #284 merge）合并。
4. post-merge monitor 第一轮 `14:43:51Z` 在新目录建立基线，32 ok /
   2 warn / 0 critical；两 warn 均为无基线时的保守 restart/log 口径。
5. post-merge monitor 第二轮 `14:44:01Z` 为 **34 ok / 0 warn /
   0 critical**，partial=false，`monitoring_ready=true`。Web `/aios` 与
   `/aios/login`、API health、FunASR、CosyVoice 及日志/重启阈值全部 ok。

`monitoring_ready=true` 只表示该轮采集与阈值判断通过，不是持续可用性
承诺，也不改变仓库的 `production_ready=false` 契约。

## 6. 本切片验证

- 基线与编辑后均运行：
  `services/api/tests/test_versioning_rollback.py::test_version_sources_in_sync`
  与
  `services/api/tests/test_production_monitor.py::test_r1_docs_commit_wording_sweep`；
  编辑后最终结果为 **2 passed**。
- `git diff --check` 通过。
- tracked 新增行 secret 形态扫描、本地绝对用户路径扫描、U+FFFD 扫描均
  **0 命中**。

## 7. 诚实边界

1. M14-193 的生产滚动、HTTP、浏览器与 monitor 都是 2026-09-30 时点证据，
   不承诺窗口外持续健康。
2. 公共 edge 的 cookie 检查因缺少真实 credentials file 保持 fail-closed；
   本切片不伪造登录验收。
3. mobile browser case 是仿真视口，不覆盖物理 Android/Harmony/iPhone
   人工安装与实网体验。
4. 备份 manifest 证明备份清单与文件哈希，不等同于已执行恢复演练。
5. 本切片原始 authoring 为 branch + 基于 main `4993ffc` 的单 local
   docs commit；发布前已与当前 main `a62ae76`（PR #284 merge）合并，
   合并不改变 docs-only 边界；supervisor 审查与 remote 发布
   （push/PR/合并）在其后进行。
