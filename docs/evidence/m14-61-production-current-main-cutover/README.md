# M14-61 production current-main 切换证据回填（docs-only）

## 结论

supervisor 已在 current main（`44af53fa`，PR #143 merge）口径完成生产栈
**最小化切换**——生产 API/Web 容器切至 `m14-60-production` 镜像，六服务
栈全 healthy，只读监控全绿。本任务把该切换及切换后验证转为可审计仓库
证据（docs-only 回填，2026-09-19 GMT+8）。

- **合入与 CI**：PR #143（Record Harmony current-main preflight
  evidence）于 merge commit `44af53fa73325d8f7fe600f70aa2e777619db990`
  合入 main（`pr-143.json`：merged_at 2026-09-19T02:58:05Z，base
  `1233ef0a`、head `c3f1124`；`git-state.json`：canonical main 检出
  head=remote_main=`44af53fa`、tracked-clean）。合入后 **main CI run
  `35417197684`**（head_branch main、head_sha `44af53fa`，
  2026-09-19T02:58:07Z–03:01:39Z）conclusion **success**，5 job 全部
  success：API（ruff / pytest / migration）、Web（test / typecheck /
  lint / build）、Docker（compose build + healthy + smoke）、Android
  （unit test / lint / assemble）、Release tools（Python tests）。PR CI
  run `35416912719` 口径来自 supervisor 交接指令（证据目录仅含 main
  run JSON，无该 run 工件）。
- **最小化切换**（`container-state.txt`，compose project
  `aios-m14-03-production-rehearsal`，profile local）：API 新容器
  `44076753e543`（`aios/api:m14-60-production`，
  `127.0.0.1:8000->8000`，Up 2 minutes (healthy)）、Web 新容器
  `bb6ca07483f6`（`aios/web:m14-60-production`，
  `127.0.0.1:3011->3000`，Up 2 minutes (healthy)）；**未动**：
  postgres `f928410e404e`（Up 30 hours）、redis `6006a4c551e1`
  （Up 30 hours）、livekit `4aa604546c80`（Up 31 hours）、minio
  `8e4f3d855ffb`（Up 45 hours）——四者均 healthy，连续运行时长证明
  切换未触碰有状态服务。
- **镜像 digest**（`image-state.txt`）：api
  `sha256:8e7e16e725fdeb191d793264a5cf271358bf22dc76b645ba2b4fca9aa14345c8`
  （2026-09-19T02:58:44Z）、web
  `sha256:7cf12f0d1a2e1938f141aeed9d4fbf745a9223d7fcc49a2f683510c24b5eb99c`
  （2026-09-19T03:02:38Z）。
- **切换后端点**（`endpoint-health.json`，checked_at 记录值
  2026-09-19T11:06:24）：9 URL 全 200——API `/health` 与
  `/api/v1/version`、Web `/` 与 `/login`、FunASR 直连
  `127.0.0.1:8010/health`、CosyVoice 直连 `127.0.0.1:8011/health`、
  sidecar `172.25.7.64:18010/health`、`18011/health`、
  `18011/health/live`。
- **认证边界**（`unauth-security-boundary.json`）：未认证 POST
  `/api/v1/search/queries`、`/api/v1/voice/sessions` 均 **401**；
  `/api/v1/version` 响应 `auth_enabled=true` 为 supervisor 交接口径
  （`endpoint-health.json` 只记 200，不含响应体）。
- **真实浏览器验收**（`browser-acceptance.json` + `browser-home.png`）：
  home/login 均 200；标题 `AI Learning OS`；首页标题含「把复杂留给
  系统，把简单留给你」/ 学习工作台 / 考场审阅 / 语音陪练；登录控件
  存在（username / current-password / 登录 / 注册）；未认证
  `/api/v1/auth/me` 401 ×2（预期）；blocking_page_errors=0。
- **恢复编排干跑**（`production-recovery-dry-run.log`，全程只读）：
  docker engine 就绪（server 29.7.2）、compose config OK、pin 一致键
  9/9（值不回显）、stack 6/6 服务 healthy——健康栈无需 up（dry-run
  与 enforce 同语义跳过）；FunASR / CosyVoice 均 managed-running 且
  /health 200 → leave（healthy untouched）；结果 OK。
- **生产监控**（`production-monitor.json/.md`，mode=execute，
  2026-09-19T03:04:59Z–03:05:01Z）：**34 ok / 0 warn / 0 critical**、
  overall_status=ok、partial=false、monitoring_ready=true；端点延迟
  web-root 5.91ms、web-login 1.919ms、api-health 1.67ms、funasr
  2.493ms、cosyvoice 12.815ms；restart 增量评估 baseline=missing——
  按当轮增量计 ok（六容器 restart_count=0），累计 RestartCount 照实
  入档（M14-23 语义）。34/0/0 为该 **03:05 固定源快照**口径、非持续
  状态申明——supervisor 交接补充：其后 11:15 自然监控轮 warn-only，
  仅 API/Web 容器重建的预期差异（container-recreated /
  started_at_changed），六服务与五端点仍 healthy；该后续轮工件不在
  本 README 16 文件清单内。
- **审计归档 readiness**（`audit-readiness.json`，generated_for
  2026-09-19T03:05:11Z）：overall **fresh**、problems 空；anchor /
  offline_copy / worm_archive 三任务全 fresh（next_due 依次
  2026-10-17、2027-03-18、2026-12-18）；sidecar 哈希与正文一致。
- **生产 env 变更**（supervisor 交接口径；本回填未打开任何 env 文件）：
  仅 `AIOS_IMAGE_TAG` 与 `AIOS_WEB_IMAGE_TAG` 两键改为
  `m14-60-production`；切换前备份
  `artifacts/ops/m14-60-production-cutover/20260919-110341/env.backup`
  SHA-256
  `8CA2DA1FA58F6D86591676D311FC312533A2824139EA03419F62EE58B9DDD349`，
  更新后 env SHA-256
  `2AEFCE975DF45FAC87E84C485C002DE6F69C94E655CAAF9A6914D6899B6933D4`。
  env 与备份文件含密钥，全程只引用路径与哈希、零读取。

## 证据文件与完整性锚点

源证据（gitignored，不入库；位于 canonical main 检出
`.verify/m14-61-production-current-main-cutover/`）：**16 文件 /
52,203 bytes**，逐文件 SHA-256 即完整性锚点（本回填不计算聚合清单
哈希）：

```
5b13a32ca8ff6116f2cff03feaf139ed91e1b0974f58caa1fec578e3819040f4    955  audit-readiness.json
198a7e66d93aeac5f29d710c9112b8c9fc90dc5791cb6950ac81180507549b3b     87  audit-readiness.json.sha256
31dff1c092a37c19b7de54c61ad50e51ee27805ef74f774f5248497a3973b781    492  browser-acceptance.json
4499a58ebeec5e710198b244c4f8b4af8df93f98333d46af282fcd0877de1cb5  24815  browser-home.png
5e06a4d6862ff8c8a855cf7d7c6c6813d31f8050b956d76385c54f6e223c56b5    456  ci-main-jobs.json
7a4cb06e064a554c3e366401b219b684f7dd5714dc9460c28af2b9104459190f    294  ci-main-run.json
7ac5186a1c358f88f2787dedc0ffb611e6203cf0ed693fc58c35f62d13528d23   1119  container-state.txt
21de62862730a9b44ab4856a8b0f40e5b5112c968cd6b1e21626f58c352feb23    819  endpoint-health.json
632a4d41a6ef04f6421283bf2fc3a3aaa5f99ff5b011577926a4a0f7cc9e1ee0    270  git-state.json
96a30dff7be8212d9c58fbedeeee6d06ec78d92ce971ee35ce754888be9dc4bf    262  image-state.txt
2e49ef547cd6313de02d2d159385556365ee161049b1b1af0e00a4a43215f081    301  pr-143.json
8c20d46191b4b87edb4d86f4565992d1a730da5e76d92035b596f9f2a6f6faab  16124  production-monitor.json
32395a9d2bf915e4d3a3b63b12a89ff61eb481f191d0b163df4548a78500ee65   4369  production-monitor.md
f73fbff75612af3b5c1f6d8467e4f9e34c79fc46add0a2e6fdc720c51ff9ca5f   1211  production-recovery-dry-run.log
b87806b5fde367ab48289e6e9a81a87637c3aaedd5d098fe96d1d62848e13ba1    396  provider-config-presence.json
7b956159f85f2d1afd0740572ebaa36317906b746dc3c90e2e35c3232b16081d    233  unauth-security-boundary.json
```

- `audit-readiness.json.sha256`（87 bytes）内容恰为正文文件
  `5b13a32c…0f4  audit-readiness.json`——sidecar 与独立计算的正文
  SHA-256 一致（matching sidecar）。
- `production-monitor.json`（16,124 bytes）为权威结构化结果、
  `production-monitor.md`（4,369 bytes）为人机可读报告；两者为同一轮
  只读采集（read-only collection：compose ps / docker inspect /
  docker logs --tail / GET-only HTTP，零变更）。
- `browser-home.png`（24,815 bytes）为浏览器验收截图，原始 PNG
  gitignored 不入库（事实由 `browser-acceptance.json` 承载）。
- `provider-config-presence.json`：`checked_key_names_only=true`，
  仅键名存在性、值绝不读取——SEARCH/LLM/云 ASR/云 TTS 共 11 键全
  UNSET。

本 README 为该切换唯一入库证据文件；原始 JSON/txt/PNG 产物
gitignored 不入库。

## 语义解读（最小化切换口径）

- **最小化**：仅 API/Web 两容器重建切换（捕获时 Up 2 minutes），
  postgres / redis / livekit / minio 连续运行 30–45 小时未动——数据
  面与会话面零触碰；恢复编排只读干跑复核 pin 9/9 与 6/6 healthy 后
  按健康栈语义跳过 up，FunASR / CosyVoice 判 leave（不触碰）。
- **镜像即代码口径**：生产运行镜像以 tag `m14-60-production` +
  不可变 digest 双锚定（api 02:58:44Z / web 03:02:38Z 推送），与
  PR #143 merge 后 main CI run `35417197684`（03:01:39Z 完成、5 job
  全 success）构成同一代码口径的合入-验证-部署链；canonical main
  head=remote_main=`44af53fa` 且 tracked-clean——切换即 current-main
  口径。
- **认证与安全边界**：未认证写路径 POST 401 ×2、浏览器内未认证
  `/api/v1/auth/me` 401 ×2——认证边界在切换后容器上行为正确。
- **本地语音生产链**：FunASR 直连（8010）、CosyVoice 直连（8011）、
  WSL sidecar 双端口（18010/18011）与 `/health/live` 全 200——本机
  语音链在新 API/Web 容器下健康。
- **monitoring_ready ≠ production_ready**：34/0/0 全绿（03:05 固定源
  快照、非持续状态）与 monitoring_ready=true 只证明采集面就绪；监控
  工具边界明示 never claims production ready。

## 边界（不声称）

- **不声称真实 provider 冒烟**：部署 API 的 SEARCH / LLM / 云 ASR /
  云 TTS 凭证与端点 11 键全部 UNSET（`provider-config-presence.json`，
  `checked_key_names_only=true`）——本轮只证明本地语音生产链，不证明
  真实 search / LLM / 云语音 provider 可用性。
- **不声称长稳**：切换后验证为单轮即时快照（镜像推送 02:58–03:02
  UTC、监控 03:05 UTC 固定源快照、端点复检 checked_at
  2026-09-19T11:06:24 记录原值），无 soak / 长期稳定性证据；其后
  11:15 自然轮 warn-only（API/Web 容器重建预期差异、六服务五端点仍
  healthy）同属快照口径，不构成持续状态证据。
- **不声称 release readiness / cutover 审批**：release readiness 与
  cutover 正式证据仍未产出，本回填不构成发布批准；切换为运行时操作
  口径。
- **Harmony 真机收口仍开放**：无签名 HAP、无可读签名验证报告、无
  非 loopback 物理目标（沿用 M14-60 口径）。
- **密钥文件零读取**：`infra/env.production-recovery` 与
  `artifacts/ops/m14-60-production-cutover/` 下 env 备份含密钥，本
  回填仅引用路径与 SHA-256，未打开任何 env 文件。
- **supervisor 口径项**：PR CI run `35416912719`、`auth_enabled=true`
  与 11:15 自然监控轮 warn-only 结论来自交接指令，证据目录不含对应
  工件。
- 全局判定不变：**`production_ready=false`**。
- 本回填 docs-only：零生产触碰、零代码改动、不 push、不建 PR；分支
  `docs/m14-61-production-current-main-cutover` 基于 `main@44af53f`
  （PR #143 merge），单 local commit。
