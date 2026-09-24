# M14-124：生产切换与发布验收证据

## 1. 结论与边界

M14-124 是一次已获人工批准并已实际完成的最小范围生产切换。

- 构建基点：`684865d1212ca565182fd04edcb3f6a21c9ef88b`
- 审批绑定 docs-only main：`e14be369a4a80267dcaafd41f7475cd44e2166ed`
- API 镜像：`aios/api:m14-124-production` =
  `sha256:c99e28c905208bffbc1576f0c5c9e042af18fd356881cee967078ee38323781f`
- Web 镜像：`aios/web:m14-124-production` =
  `sha256:d596f0c726ab690359b196a3c3911f9842b94218b4361ee452413d420a38194b`
- 回滚锚：`m14-117-production`

切换后生产栈 7/7 healthy。API 与 Web 容器镜像 digest 均与审批值一致；
Postgres、Redis、MinIO、LiveKit、SearXNG 五个基础设施容器未重建且保持
healthy。FunASR 与 CosyVoice 保持 managed-running，健康探测 200，未停止、
未重启、未修改。

官方只读 `release-readiness` 汇总结果：

- 10 个 required gates 全部 `pass`
- 0 pending / 0 blocked / 0 malformed / 0 tampered
- `not_pass_required=[]`
- `release_ready=true`
- optional `turn-tls=missing`

因此本证据的语义是：**本机/LAN 生产形态已达到发布验收口径**。
`turn-tls` 未执行，本证据不宣称公网语音/TURN/TLS 就绪。仓库既有
`evidence-cockpit` 仍按其策略输出 `production_ready=false`（该字段是
工具策略恒定值，且 cockpit 明确永不接收 release-approval）；本切片不
修改该工具，也不把它用作覆盖 `release-readiness` 结论的替代品。

## 2. 人工审批与绑定

审批原文由用户在会话中明确给出，并先逐字保存为：

- `RELEASE_APPROVAL.txt`
- SHA256：`547410067ae6a02194a2539c298c12dfdcb0a02ff13efc375e4c2ed7fbec41a2`

为进入 `release-readiness` 的机器校验，审批原文被转录为
`release-readiness-staging/release-approval.json`：

- SHA256：`c02f5e2715918097e3f92bc201da00db05a2e95e769e2db19450e494f16f2458`
- `approval_binding` 逐项保留 build base、docs-only main、API/Web digest、
  rollback tag、审批原文文件名与原文 SHA256。
- `note` 为用户审批原文，未改写语义。
- `gate_evidence` 绑定 M14-125 current-main 证据的 9 个 required gate 文件
  SHA256。

该 JSON 是**人工审批原文的机器转录记录**，不是工具代签。官方评估器只
验证结构、时间窗、覆盖面与哈希绑定，不做身份认证；这一限制在
`release-readiness.json` 的 `identity_note` 中原样保留。转录过程中两次
手抄 SHA256 被 fail-closed 判为 malformed/tampered，未作为通过证据；最终
记录以本地证据文件重新计算的实际哈希修正并重跑通过。

## 3. 切换前保护

### 3.1 baseline

canonical 目录保存切换前 git 与运行时基线：

- git HEAD：`97bbe3c77cbb83c1532cdedaeb9d952a89b1a531`
- git 状态：main 与 origin/main 同步，仅有既有未跟踪 `.claude/`
- 生产端点矩阵：7/7 HTTP 200
- `docker ps -a` 与 `docker images` 快照均已落盘

### 3.2 备份

备份目录：`artifacts/m14-124-production-cutover/pre-cutover-backup/`

- 格式：`aios-backup-v1`
- 30 个表
- 4 个文件（database、对象、公开 compose、生产 env 备份）
- manifest SHA256：
  `0901e3ed44318de11faf1e17cdc646ec711a6ebbf388038bd9ba68a4e7ea1570`
- 独立复核：missing=0、extra=0、hash_mismatch=0

canonical 只入备份 manifest；`database.json`、上传对象与生产 env 备份
原文不入库、不回显。

### 3.3 只读 preflight

`production-preflight.json` 结果：

- phase=`post-migration`
- 5 pass / 0 pending / 0 fail / 0 not_configured
- Alembic current == head == `0027_audit_chain`
- 审计链 valid，锚定 up-to-date
- 历史治理三类计数全 0

### 3.4 env 手术

`infra/env.production-recovery` 先备份，再仅修改两个键：

- `AIOS_IMAGE_TAG`
- `AIOS_WEB_IMAGE_TAG`

证明文件记录：

- 术前/术后均 785 bytes、15 行
- 键集合完全相同
- 仅 4 个字节位置变化
- 其他字节未触碰
- `docker compose config --quiet` 通过
- 术后重读哈希一致

## 4. 最小范围切换

切换命令保持 `--no-build --no-deps`，只重建 api 与 web：

1. API：`COMPOSE_EXIT=0`，容器重建并启动；健康轮询在 2026-09-24
   20:29:39 +08:00 达到 `healthy`，`/health=200`，运行镜像为
   `aios/api:m14-124-production`。
2. Web：`COMPOSE_EXIT=0`，容器重建并启动；健康轮询在 2026-09-24
   20:30:02 +08:00 达到 `healthy`，`/` 与 `/login` 均 200，运行镜像为
   `aios/web:m14-124-production`。

切换后容器锚点：

| 对象 | 容器 ID | 镜像 digest | 健康 |
|------|---------|-------------|------|
| API | `77bb87569d987a5d4bb78d3d53da177558c522ae05a05c0dbaf00c5db76f6592` | `c99e28c905208bffbc1576f0c5c9e042af18fd356881cee967078ee38323781f` | healthy |
| Web | `81004df58562e964b484870cc0acd629faebfd9862f38adc8354786405f133b1` | `d596f0c726ab690359b196a3c3911f9842b94218b4361ee452413d420a38194b` | healthy |

## 5. 切换后验收

### 5.1 端点矩阵

7/7 全部 HTTP 200：

- API `/health`、`/api/v1/version`
- Web `/`、`/login`
- FunASR `8010/health`
- CosyVoice `8011/health`
- SearXNG `127.0.0.1:8878/healthz`

证据：`03-endpoint-matrix.txt`，SHA256
`8ee66da91081894bbc983144c8a79beb941fadb854468d89be9e853512c84817`。

### 5.2 真实浏览器验收

复用 M14-70 的真实 Playwright Chromium 脚本，覆盖：

- desktop 1440x900 × `/`、`/login`
- mobile 390x844 × `/`、`/login`

结果 `OVERALL=PASS`：标题正确、必需文本齐全、无 page error、无非预期
console error、横向溢出 0px。4 张截图与 JSONL 报告已入 canonical。

报告 SHA256：
`3856f0499b5ba11ab4d4d342e4c37dc4946eef918e7a8616ea4fe341460b0f87`。

### 5.3 生产监控

第一轮在全新 artifact 目录执行，因没有历史基线而采用 full-tail 口径，
Postgres 7 条切换前历史错误导致 1 warn。第二轮以第一轮为时间界：

- 34 ok / 0 warn / 0 critical
- partial=false
- `monitoring_ready=true`
- 采集器 compose_ps / containers / endpoints / logs 全部 ok

第二轮证据：
`monitor/monitor-20260924-123442.json`，SHA256
`d3633e0ff39c77b81aa0d06be62cc537e7276d30a521ebe6b9a924ff9fe34bf6`。

### 5.4 恢复 dry-run

`production_recovery.py --dry-run --no-log-file` 结果：

- Docker engine ready
- compose config OK
- 9/9 pin 一致
- 6/6 服务 healthy/running，无需 up
- FunASR/CosyVoice healthy untouched

证据 SHA256：
`3a7e31a803abe0b184c6d88eee55493856dd02005de6998c538f4490e8cac5dc`。

### 5.5 日志与秘密扫描

API/Web 各保存 tail 200 日志：

- 错误样行：0 / 0
- 当前 env 与切换前 env 备份敏感值检查：各 22 项
- `leaked_keys=NONE`
- scanner exit 0

证据 SHA256：
`ea407b220acec6caade7a764e3bebeac3d365c4b6f1ec2fef610d36ce2d7095b`。

## 6. release-readiness 收口

staging 使用 M14-125 在 `e14be369` 重跑的 9 个 required gate 证据：

- ci-main：run `35992664886`，5/5 jobs success
- release-check：all_green=true，10/10
- production-preflight：5/5 pass
- backup-restore：verified=true，32 rows
- audit-chain-anchor：valid/up-to-date/WORM archived
- legacy-papers：0 pending
- draft-ownership：0 pending
- long-soak：24h 窗口 pass，97 样本全 ok
- provider-smoke：voice/search/llm 三槽位 pass

最终 `release-readiness.json` SHA256：
`f517e3bfd4547d9ea74bf89051b283f8b706f696dc8a33c5a37f6ee3a6b6683f`。

结果：required gates 10/10 pass，`release_ready=true`。该结论绑定
审批指定的 `e14be369` docs-only main 与 `684865d` 构建基点；本证据切片
及 PR #212 均为 docs-only，不在镜像构建输入面内。

## 7. 回滚路径

未回滚。若后验失败，按以下顺序执行：

1. 用 `artifacts/m14-124-production-cutover/env-backup/env.production-recovery.bak`
   恢复两键对应的生产 env。
2. 在 `infra` 执行同项目、同 profile 的
   `up -d --no-build --no-deps api web`。
3. 重跑 health、端点 7 项、browser、monitor 与 preflight。

回滚镜像：

- API M14-117：
  `sha256:a10b4b626b8ac8d7690607c5f2f543a1d5bd2a78aaccd3f5500f0912b8db37b5`
- Web M14-117：
  `sha256:95aff24f17fd7268b26ce16d4e87d64f3e0cede2aa72ab068d94d2012aacd03c`

## 8. canonical 证据形态

canonical 目录：

`worktree .verify/artifacts/m14-124-production-cutover/canonical/`

内容为 gitignored 本地证据，不入库。`SHA256SUMS` 索引 55 个安全文件，
自不含自身哈希；包含审批原文、机器转录审批、release-readiness staging、
baseline、切换/健康日志、端点矩阵、浏览器验收、两轮监控、恢复 dry-run、
日志扫描、post-cutover 状态与备份 manifest。

以下内容不入 canonical：生产数据库 JSON、上传对象、生产 env 备份原文、
备份 configs 目录。只以 manifest 哈希与安全元数据留证。

## 9. 诚实边界

1. `release_ready=true` 只表示本机/LAN 发布形态的 required gates 全过；
   `turn-tls` 缺席时不得解释为公网语音就绪。
2. 审批记录的 `approved_by` 不是身份认证；本工具仅校验结构与哈希绑定。
3. provider-smoke 与 long-soak 是既有真实时点证据，本切片未重跑，时间
   边界在对应证据中保留。
4. 浏览器、监控、恢复 dry-run 与日志扫描为切换后时点证据，不承诺窗口外
   永远健康；生产监控管道仍需继续运行。
5. 本切片是 docs-only 回填，不修改运行时代码、测试、compose 或生产 env。
