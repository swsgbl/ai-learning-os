# M14-70 生产切换证据回填（docs-only）

## 结论

supervisor 已在 canonical main（`976798f2`，PR #156 merge——provider
smoke voice topology 合入后的 final main）口径完成**第三次生产最小化切
换**（继 M14-61、M14-69 之后）：生产 API/Web 容器切至
`m14-70-production` 镜像，七容器全 healthy，验收全过、零回滚。本任务把
该切换及切换后验证转为可审计仓库证据（docs-only 回填，2026-09-20
GMT+8）。

- **源基线**（baseline / post git-head + git-status）：切换前后 git HEAD
  均为 `976798f2567018552b2e892bc334a66098cb951a`、tracked-clean、
  untracked 仅 `.claude/`——同一 commit、切换全程源码树零漂移。
- **镜像 tag+digest 双锚**：api `aios/api:m14-70-production` =
  `sha256:9a4e4e160ac1e0191ece2cd86f83699cb7056fae91ced99d3f5728ff139179c8`
  （2026-09-19T21:03:10Z 新鲜构建，111,336,162 bytes）；web
  `aios/web:m14-70-production` =
  `sha256:083a525cfaf2838e462789dbbcc1475f7f79cbd1162dc9deb8301b2fe0f19006`
  （2026-09-19T18:30:15Z，缓存命中，见边界）。切换前 m14-69 代：api
  `sha256:8f509e22…c41d1`、web `sha256:7e885836…d4347`。
- **最小化切换**（compose project `aios-m14-03-production-rehearsal`）：
  `up -d --no-build --no-deps` 仅重建 api/web 两容器——api
  `3ddde9463a1f`→`95e45740cc08`、web `1dc9ec94435d`→`fcce394b1a56`，
  均 healthy，api 先过健康门禁再放行 web；compose 日志只含这两个
  service 的 Recreate→Start 序列。
- **五基础设施容器 ID 逐字节不变且 healthy**：postgres `f928410e404e`、
  redis `6006a4c551e1`、livekit `4aa604546c80`、minio `8e4f3d855ffb`、
  searxng `f46d1e120451`（digest pin
  `docker.io/searxng/searxng@sha256:6869f206…` 与 compose 定义一致，
  未变）。
- **生产 env key-only 手术**：777→777 bytes、15→15 行、15 键集合完全
  一致（`keys_identical=true`）；sha256
  `f2931cc2d4c5dd79e83debccef8c7653527873d80363f5d6e7e39d096fb578a5` →
  `ec6fc71d0bc7992ac10c659594a4782d7f268e870bb2a1ac4f35d878fff76312`；
  仅 `AIOS_IMAGE_TAG` / `AIOS_WEB_IMAGE_TAG` 两键各 1 处
  `m14-69-production`→`m14-70-production`（两值等长），全文件字节差异
  恰 4 处、全部落在两 tag 数字位；临时文件 + `os.replace` 原子写、
  CRLF 保持、env 值全程零回显（脚本只输出键名/字节/行数/sha256 与两个
  公开 tag）；gitignored 备份 `env-backup/env.production-recovery.bak`
  与源逐字节一致（从未打印，仅以 sha256+bytes 锚定）。
- **端点矩阵（全匿名，无凭据）**：`8000/health` **200**、
  `8000/api/v1/version` **200**（`version=0.1.0`）、`3011/` **200**、
  `3011/login` **200**、`8010/health`（funasr）**200**、`8878/healthz`
  （SearXNG）**200**、匿名 `POST 8000/api/v1/sources` **401**（预期
  认证边界）。
- **真实 Chromium 浏览器验收**（Playwright headless，desktop 1440x900 +
  mobile 390x844 各 `/`+`/login`）：**OVERALL=PASS**——title 均
  `AI Learning OS`、overflow 0、无缺失锚文本、0 page errors、0 意外
  console errors（匿名 401 console 2/1/2/1 预期容忍）。
- **恢复演练 dry-run**：`production_recovery.py --dry-run --no-log-file`
  exit=0，结束行 `=== 结果: OK（恢复完成/无需恢复，全部核查通过）===`；
  pin 一致键 9/9（值不回显）、健康快照 7 服务 healthy、6/6 healthy 按
  健康栈语义跳过 up、funasr/cosyvoice managed-running + /health 200 →
  leave（零触碰）。
- **生产监控一轮**：`overall_status=ok`、**ok=34 warn=0 critical=0**、
  `monitoring_ready=True`（工具自声明 ≠ production ready）；6 容器
  healthy、restart_count=0、error_total=0、端点全 200（延迟 2–13ms）。
- **secret 纪律**：`infra/env.production-recovery` 与备份的全部值从未
  打印/复制/回显；构建日志、recovery dry-run 日志、monitor 三产物对
  全量 env 值子串扫描均 `leaked_keys=NONE`；端点探测全匿名零凭据；
  零回滚（回滚预案已备未触发）。

## 证据文件与完整性锚点

源证据（gitignored，不入库；位于 canonical 检出
`artifacts/ops/m14-70-production-cutover/`）：**35 文件**；
`MANIFEST.txt`（`sha256  bytes  path`，自身不自锚）为逐文件完整性锚
点，**Codex 已独立核验 35/35 哈希与大小**。此处原文转录：

```
a643f35ee7af1cc79589bc57465d77cb48b37459bfb2752083c554afbb9e3dab  5510  00-baseline-container-inspect.json
0e3a67f81d6049c1fee65eb263be43dc0605900cab9ce0773c09c358e461eaaa  695  00-baseline-containers.txt
b72b5f17a5d73ebec005086a3ef24894c7c2cfa54e608d3fea01f33c838d9fb1  183  00-baseline-git-head.txt
fbc8443a3eede07cd3ac03854485af2170822f41071a8857ab6cfb4b982bbdb3  12  00-baseline-git-status.txt
cd265ef2c2a9b700081cb2266cfe826e6ccde5dd2ec7905cc847eb8853a51401  358  00-baseline-images.txt
0628b7f84c42a9793712c4931ddb5ee58694661f065788c418de59bc36f43b5b  998  00-env-backup-record.json
530898c82815a0587c62eb54b787fcbcecea28e41411135fbdb7c0986690b6ff  736  01-env-after-record.json
1a62323e8b6bcc5f7371d53ad5e4b9d91cacd9e0c24881ca4d56d7886abd7604  1729  01-env-cutover-proof.json
51bbd351047e33efdcb87efc528d6ca71256121028fb61adc61cdf7595349388  428  02-compose-cutover-api.log
da03e393ffb9bff30c1648bdd932365af408034f14ff15da3b3429d7a7fbed9d  428  02-compose-cutover-web.log
0f151bb101124042948326d51857dd246202624de00789404c7413fbae81e1bb  5510  03-post-container-inspect.json
ad8b4a7e3e1478b914b62a0938af3114e70889460645043bd5f0024833824cf1  705  03-post-cutover-containers.txt
b72b5f17a5d73ebec005086a3ef24894c7c2cfa54e608d3fea01f33c838d9fb1  183  03-post-git-head.txt
fbc8443a3eede07cd3ac03854485af2170822f41071a8857ab6cfb4b982bbdb3  12  03-post-git-status.txt
f5262a4a30442a2ea334e04a01b212d6b6d31f3b5dba8eef486092fdbba01be9  316  03-post-images.txt
d42e7d38adb93aa4f8ea9fda3ba91c21001f23bd65d9b819ffc9c0f0e58224d3  271  04-endpoint-matrix.txt
18468502fd5678456abc2da97310d0ee018f8361d0d232bb852ef9bcf2bddb07  113  04-version-body.json
3a7e31a803abe0b184c6d88eee55493856dd02005de6998c538f4490e8cac5dc  1377  05-recovery-dry-run.log
cd957e10ec7add73b92f33a9b51a3c126b4d08652ca0630a59afda6650b5c29d  11581  EXECUTION-REPORT.md
3856f0499b5ba11ab4d4d342e4c37dc4946eef918e7a8616ea4fe341460b0f87  2132  browser/BROWSER-REPORT.txt
05e13789849d5dca0e617370892d4d6694b2ea113bf0fe609f9029abd1475d52  90646  browser/desktop-home.png
5ccbe87573eaebe1a5988b5b782d69d2dba083a33ba0a0f11102afae0dde3711  37336  browser/desktop-login.png
8b23f1e5eec15aab99fe18e15952adb57224be3d55b814da120b26c61acc96f0  59998  browser/mobile-home.png
bcd0ae318c1bca5c69cd320b98b1ae319214176eebca8283da0d1f307ece5822  32131  browser/mobile-login.png
c73091cfe00a8acd7d4d869705678aed08e1abf22b7a9baa7aa83cecbaa14fd3  4369  build/build-api-web.log
d6bd30dde8f450fa6af06ea8b73e7fdd5128f5422e4e9ad112fbace45c78132f  72  build/build-log-secret-scan.txt
9f8e999e52ab9c2604b2f61ddecfc6c220f3b5ca4180e88d3c95057c155ad703  356  build/images-new.txt
f2931cc2d4c5dd79e83debccef8c7653527873d80363f5d6e7e39d096fb578a5  777  env-backup/env.production-recovery.bak
8308b02a319b772a663fdf55f9b37ddde9805fcc2f1d3ddd1a800c93c7f581e3  16212  monitor/monitor-20260919-211324.json
2193e9c2f4c320ab31e51e4439e43cdf9f20b1f23eeee25087468a1ddb2b75fe  4371  monitor/monitor-20260919-211324.md
e64514d1aa6095dc73df71c7fe8ad38e80d4a5804fb13cb6b9e997885edcc66a  640  monitor/monitor-run.log
8951f5061655c2527b4e4320701dc38b945bfb1b0663a24a1d0aafa9bf445445  4150  scripts/browser_acceptance.py
724bd2475054c0ddc221ff949db84a7c5cf70c480a919fb1dfa0561c94e2ca60  5630  scripts/env_surgery.py
97baa17fc72f552b68d854227b6281e471d78ecb8ebc43da4cbf8f8deb96770a  1978  scripts/inspect_containers.py
735b3181aeb2772985837cc78220405a67c360b672390680431414024ee4beb5  1900  scripts/scan_log_for_secrets.py
```

- baseline 与 post 的 git-head（`b72b5f17…d9fb1`）、git-status
  （`fbc8443a…bbdb3`）哈希两两相同——切换前后同一 commit、同一工作树
  状态。
- `env-backup/env.production-recovery.bak`（777 bytes，
  `f2931cc2…78a5`——与 env 手术前 sha256 互证一致）为含密钥的切换前
  备份，本回填**零读取零打开**，仅以字节数与 SHA-256 锚定（哈希安全，
  绝不回显内容）。
- 4 张 browser PNG 为真实 Chromium 截图，gitignored 不入库（事实由
  BROWSER-REPORT.txt 承载）。
- 本 README 为该切换唯一入库证据文件；原始产物 gitignored 不入库。

## 边界（不声称）

- **Web 内容字节等价**：构建区间 `99bcd4fe..976798f2` 内 `apps/web`
  零源码改动（区间 diff 为空）且 Web Dockerfile 未变，BuildKit 全层缓存
  命中保留原始 Created 时间戳（2026-09-19T18:30:15Z）——m14-70 web 镜像
  内容与 m14-69 字节一致（sha256 不同仅因 tag/label 元数据）；升级点仅
  在 api 侧（同区间 `services/api` 9 文件改动：ops CLI / evidence gap /
  provider smoke / release readiness 及测试）。
- **`/api/v1/version` 返回 `git_commit="not_available"` 属预期**：镜像构
  建上下文不含 `.git`（M14-69 起预存在行为，非本次回归）；源头证明 =
  canonical 构建 + 镜像 digest，不由运行时端点自证 commit。
- **不声称 provider pass / release approval / soak /
  production_ready=true**：真实 provider 冒烟、release readiness 与
  cutover 审批、soak 长稳均未产出，本回填不构成发布批准；移动端 body
  无品牌文本（`brand_text_in_body=false`）为预存在响应式布局（品牌校验
  由全视口 `<title>` 断言承载）；全局 **`production_ready=false`
  不变**。
- 本回填 docs-only：零生产触碰、零代码/测试/工作流改动、不 push、不建
  PR；分支 `docs/m14-70-production-cutover` 基于 `main@976798f`（PR
  #156 merge），单 local commit。
