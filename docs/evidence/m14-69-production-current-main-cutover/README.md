# M14-69 production current-main 切换证据回填（docs-only）

## 结论

supervisor 已在 current main（`99bcd4fe`，PR #154 merge——M14-68 收口
证据回填合入后的 final main）口径完成**第二次生产最小化切换**（继
M14-61 之后）：生产 API/Web 容器切至 `m14-69-production` 镜像，七容器全
healthy。本任务把该切换及切换后验证转为可审计仓库证据（docs-only 回填，
2026-09-20 GMT+8）。

- **源与构建基线**（baseline-git-head.txt / post-git-head.txt +
  同名 git-status.txt）：切换前后 canonical main HEAD 均为
  `99bcd4fe62b03742336eaf529f4dd6b4a199a5b1`（`## main...origin/main`，
  除未跟踪 `.claude/` 外 tracked-clean）——同一 commit 构建、切换全程
  源码树零漂移。
- **镜像 tag+digest 双锚**（post-images.txt）：api
  `sha256:8f509e227907ee3f43bfed98131190a4e33d3df852e321ecf19bfec7c56c41d1`
  （2026-09-19T18:32:20Z）、web
  `sha256:7e8858361ede707b8a865f32737d8e3a51d7d35e3e32ea5bf0f1576630dd4347`
  （2026-09-19T18:30:15Z），tag `aios/api:m14-69-production` /
  `aios/web:m14-69-production`。
- **最小化切换**（baseline-containers.txt、post-containers.txt、
  compose-cutover.log；compose project `aios-m14-03-production-rehearsal`）：
  仅 api/web 两容器重建——旧 api `8bb0ade786a6`
  （`aios/api:m14-60-production`，Up 7 hours）、旧 web `bb6ca07483f6`
  （`aios/web:m14-60-production`，Up 15 hours）→ 新 api `3ddde9463a1f`、
  新 web `1dc9ec94435d`（均 Up 2 minutes (healthy)）；compose 日志只出现
  这两个 service 的 Recreated/Started，且 api 过 healthcheck（Waiting→
  Healthy）后 web 才启动。**未动**：postgres `f928410e404e`
  （Up 46 hours）、redis `6006a4c551e1`（46 hours）、livekit
  `4aa604546c80`（46 hours）、minio `8e4f3d855ffb`（2 days）、searxng
  `f46d1e120451`（7 hours）——五者容器 ID 与连续运行时长证明零触碰
  （前四者 ID 与 M14-61 切换时相同，跨切换连续运行）。
- **切换前后端点**（baseline-http.txt / post-http.txt）：两轮各 6 URL
  全 200——API `/health` 与 `/api/v1/version`、Web `/` 与 `/login`、本地
  语音 `127.0.0.1:8010/health`、SearXNG `127.0.0.1:8878/healthz`；切换后
  追加匿名写探针 `POST /api/v1/sources` → **401**（预期认证边界）。
- **真实 Chromium 验收**（browser/BROWSER-REPORT.txt + 4 张 PNG）：
  desktop 1440x900 与 mobile 390x844 各加载 `/` 与 `/login`——title 均
  `AI Learning OS`、overflow_px=0、missing_text 空、page_errors 空、
  unexpected_console_errors 空；匿名 401 资源（每页 2–3 个）按预期容忍。
- **恢复编排只读干跑**（post-recovery-dry-run.log，
  `tools/ops/production_recovery.py --dry-run --no-log-file`）：docker
  engine 就绪（server 29.7.2）、compose config OK、pin 一致键 9/9（值不
  回显）、local-profile stack 6/6 服务 healthy/running——健康栈无需 up
  （dry-run 与 enforce 同语义跳过；健康快照同时记录 searxng healthy）；
  FunASR / CosyVoice 均 managed-running 且 /health 200 → leave
  （healthy untouched）；结果 OK。
- **生产 env 变更**（env-cutover-hashes.txt；本回填未打开任何 env
  文件）：切换前源 env 与 gitignored 备份 `env.production-recovery.bak`
  （777 bytes）SHA-256 一致，均为
  `1161A54C422E9FC294630827047992E5D4CD285EECA31774345A419A2C17A361`
  （与 MANIFEST.txt 锚定值互证）；仅 `AIOS_IMAGE_TAG` 与
  `AIOS_WEB_IMAGE_TAG` 两键改为 `m14-69-production`，行数 15→15（仅值
  变更），更新后 env SHA-256
  `F2931CC2D4C5DD79E83DEBCCEF8C7653527873D80363F5D6E7E39D096FB578A5`。
- **运行时 version 语义**：切换后 `/api/v1/version` 返回 200，但镜像内
  git 信息为 `not_available`——`.git` 有意排除于 Docker context。源头
  证明 = canonical source build（`99bcd4fe` tracked-clean）+ 镜像
  digest，不由运行时端点自证 commit。

## 证据文件与完整性锚点

源证据（gitignored，不入库；位于 canonical main 检出
`artifacts/ops/m14-69-production-cutover/`）：**19 文件 / 200,453
bytes**；`MANIFEST.txt`（`file|bytes|SHA256`，本身不自锚）为逐文件完整
性锚点，此处原文转录（路径分隔符归一为 posix）：

```
3EBF63E0F95D6F792B53F020460594B3BB4F6800D2333EA8A1DD06E76E0EA508    709  baseline-containers.txt
C3FD04F36B0C38E21DD09BFC062974F42BE3830D252105A1E915F6FA29B75070     42  baseline-git-head.txt
C6182BE7990653E8C3A3EC9F0A5D4AE6A9000639C502527C949CD0882908A23E     36  baseline-git-status.txt
6BF11353F4072A59F1577C8CBA89D4A48A7CFAC80E7A21E717F72A1BB10EA976    248  baseline-http.txt
646CC04A292A78F0B35A28774D2E63D64143C0150A807D85BD9EA1B741442B14    876  browser/BROWSER-REPORT.txt
5750344AD7CC1FF1159C6B86771A96D12A8A5EA42A036F199A0439AEB4F967B0  62039  browser/desktop-home.png
5CCBE87573EAEBE1A5988B5B782D69D2DBA083A33BA0A0F11102AFAE0DDE3711  37336  browser/desktop-login.png
EF5147B1E590FCD829D9BC3603112813A17554EB907EA20772A20B031015F494  61198  browser/mobile-home.png
BCD0AE318C1BCA5C69CD320B98B1AE319214176EEBCA8283DA0D1F307ECE5822  32131  browser/mobile-login.png
F48FE698CF4BD7BDE5BEFE0B90D4932EF0A1D74F58E9A15067F6F8F129E89BA8    608  compose-cutover.log
9F9D15BA7D66CAFD0D98019CD678BF97356521AFBAB29E71F5171D21A5C4DA8B    341  env-cutover-hashes.txt
1161A54C422E9FC294630827047992E5D4CD285EECA31774345A419A2C17A361    777  env.production-recovery.bak
4D3D6556C598F4D05E75701281DDA296E963BBCDDB63EB3C22FC735A646B64FC    712  post-containers.txt
C3FD04F36B0C38E21DD09BFC062974F42BE3830D252105A1E915F6FA29B75070     42  post-git-head.txt
C6182BE7990653E8C3A3EC9F0A5D4AE6A9000639C502527C949CD0882908A23E     36  post-git-status.txt
E03AC15DF9A7841119B9EC63EF4AE7A940DCA77DAFAAD7695202BDBB28DD3264    277  post-http.txt
995F539A8EAE97950826CD5C3BB65DC2ED4DF7968D67B5B3AE007A8544DCFBF1    216  post-images.txt
3A7E31A803ABE0B184C6D88EEE55493856DD02005DE6998C538F4490E8CAC5DC   1377  post-recovery-dry-run.log
A20520B2BA82C2C017ECDCAEAB71131EE228CCD1185DCFCB9130C8065CFB0F83   1452  README.md
```

- baseline 与 post 的 git-head（`C3FD04F3…5070`）、git-status
  （`C6182BE7…A23E`）哈希两两相同——切换前后同一 commit、同一工作树
  状态。
- `env.production-recovery.bak` 为含密钥的切换前备份，本回填**零读取零
  打开**，仅以字节数与 SHA-256 锚定（与 env-cutover-hashes.txt 的
  source_before / backup 值互证一致）。
- 4 张 browser PNG 为真实 Chromium 截图，gitignored 不入库（事实由
  BROWSER-REPORT.txt 承载）。
- 本 README 为该切换唯一入库证据文件；原始产物 gitignored 不入库。

## 语义解读（最小化切换口径）

- **最小化**：compose 只重建 api/web 两容器（新容器捕获时 Up 2 minutes，
  api 先过健康门禁再放行 web）；postgres/redis/livekit/minio/searxng 五
  容器 ID 未动、连续运行 46h/46h/46h/2d/7h——数据面、会话面、对象存储
  面与搜索面零触碰；恢复编排只读干跑复核 pin 9/9 与 6/6 healthy 后按
  健康栈语义跳过 up，FunASR/CosyVoice 判 leave（不触碰）。
- **镜像即代码口径**：生产运行镜像以 tag `m14-69-production` + 不可变
  digest 双锚定（web 2026-09-19T18:30:15Z、api 18:32:20Z 创建），与
  canonical main `99bcd4fe` tracked-clean 构成同一代码口径的源码-构建-
  部署链——继 M14-61（`m14-60-production`）后第二次 current-main 切换。
- **认证边界**：匿名写路径 `POST /api/v1/sources` 401——认证边界在切换
  后容器上行为正确。
- **本地语音与搜索链**：FunASR 直连（8010）与 SearXNG（8878）在新
  API/Web 容器下 200。
- **运行时版本端点不承担溯源**：`/api/v1/version` 内 commit
  `not_available` 是 `.git` 有意不入镜像的结果，非异常；构建溯源以
  canonical 构建 + digest 为准。

## 边界（不声称）

- **不声称真实 provider 冒烟**：SEARCH / LLM / 云 ASR / 云 TTS 真实凭证
  与冒烟缺口维持 M14-67/M14-68 口径，本轮未重跑、未新增证据。
- **不声称 release readiness / cutover 审批**：release readiness 与
  cutover 正式证据仍未产出，本回填不构成发布批准；切换为运行时操作
  口径。
- **不声称长稳**：切换后验证为单轮即时快照（镜像 2026-09-19T18:30–
  18:32Z 创建、新容器捕获时 Up 2 minutes），无 soak / 长期稳定性证据。
- **不以运行时端点证明 commit**：`/api/v1/version` 为 `not_available`
  （`.git` 不在镜像内）；源头证明 = canonical source build + 镜像
  digest。
- **密钥文件零读取**：生产 env 与备份含密钥，本回填仅引用路径与
  SHA-256，未打开任何 env 文件。
- 全局判定不变：**`production_ready=false`**。
- 本回填 docs-only：零生产触碰、零代码改动、不 push、不建 PR；分支
  `docs/m14-69-production-current-main-cutover` 基于 `main@99bcd4f`
  （PR #154 merge），单 local commit。
