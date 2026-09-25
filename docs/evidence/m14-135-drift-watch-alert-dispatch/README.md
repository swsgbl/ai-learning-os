# M14-135 Production Drift Watch 告警分发第一片（实现切片）

分支 `ops/m14-135-drift-watch-alert-dispatch`（独立 worktree，基于 main
`c55f915ffce8baf830f75d40ae9a83062bcc4917`（PR #222 merge = M14-134 证据
合入）），实现者执行、Codex supervisor 监督；本地 commit 后 supervisor
审查与 remote 发布（push/PR/合并）在其后进行。

目标：关闭文档化缺口「drift=true 告警送达未实证」的**第一个有界部分**——
交付只消费既有 M14-127 execute 模式 drift-watch 报告的告警分发工具
（工具就绪 only；不做调度集成、不做生产实证宣称）。

## 1. 交付物

| 文件 | 说明 |
|---|---|
| `tools/ops/production_drift_watch_alert_dispatch.py` | 新工具（单文件、纯标准库、零第三方依赖；零子进程/零 Docker 采集/零 env 读取） |
| `services/api/tests/test_production_drift_watch_alert_dispatch.py` | 契约测试（127 项，全部合成临时 fixtures + 注入 FakeTransport/FakeClock——零真实网络；含 supervisor R1 修正后的台账行全字段严格 schema 44 项聚焦测试） |
| `tools/ops/README.md` | 标题链追加 M14-135 + 工具段落（精确安全边界） |
| `docs/evidence/m14-135-drift-watch-alert-dispatch/README.md` | 本证据 |
| `docs/PROJECT_STATUS.md` / `docs/ROADMAP.md` / `docs/CHANGELOG.md` | 各 M14-135 条目（最小更新） |

## 2. 实现摘要

- **只消费权威报告，绝不重判漂移**：输入恰一份既有 M14-127
  drift-watch JSON 报告；本工具零 Docker/compose 采集、零 drift 重算——
  分发判定唯一依据是报告自身 `drift` 布尔。plan 模式源报告无权威
  drift 结论，恒拒绝（`report-mode`），绝不猜测。
- **报告校验（fail-closed，固定词汇 reason）**：文件名严格
  `drift-watch-YYYYMMDD-HHMMSS.json` 白名单；身份/schema 精确匹配
  （schema_version=1、`tools/ops/production_drift_watch.py`、M14-127、
  mode=execute）；UTC 时间戳严格形态且 ended>=started；文件名时间戳
  交叉校验（started ∈ [stamp, stamp+2s]，与 M14-133 历史审计器容差
  同值，匹配 M14-127 先取 stamp 后取 started 的实现序）；config
  （project 白名单形态 + api/web 锚点 expected_tag + sha256 digest
  形态）；counts ↔ checks 实际计数一致；drift ↔ (fail>0) 一致；
  drift_reasons **固定词汇封闭集合**（M14-127 `evaluate_drift` 可产出
  的 11 前缀 × 服务域全集逐项枚举：全服务前缀仅配七服务、锚点类
  前缀仅配 api/web；越域前缀/服务/未知串一律拒绝）且非空 ⟺ drift；
  条数硬顶 64（超顶 = 报告不可信，拒绝）。任何 malformed 或自洽性
  破损报告 exit 2 且零分发。
- **双模式 + execute 三重门禁**：默认 plan/validate（零网络、零分发、
  Transport 绝不构造——计数工厂测试结构性证明）；execute 需同时满足
  `--execute` + `--confirm "EXECUTE PRODUCTION DRIFT WATCH ALERT
  DISPATCH"`（一字不差；M14-119/M14-127 的短语与近似串均拒）+
  `--secret-file`，缺一/近似即 exit 2 且零 Transport 构造。
- **分发判定**：mode=execute 且 drift=true 才分发；合法 execute 报告
  drift=false → `skipped-no-alerts`（exit 0、零网络、零台账写入）。
- **M14-119 安全面同一实现对象复用**（非平行第二策略；契约测试对
  `validate_webhook_url` / `load_webhook_secret` / `RealTransport` /
  `RealStore` / `RealClock` / `DispatchResult` / `write_report_files` /
  `ensure_output_dir_safe` 逐一 `is` 同一断言）：单一通用 HTTPS
  webhook sink；URL/可选 Bearer token 恒来自操作者 gitignored secret
  JSON 且绝不回显/入任何输出面；SSRF fail-closed URL 校验（生产恒
  https；http 仅经显式 `--allow-loopback-http` test-only 旗标且仅限
  字面回环 IP；字面私网/链路本地/未指定/组播/保留 IP 与 localhost
  恒拒；userinfo/query/fragment/坏端口拒）。
- **payload 版本化 + 固定词汇 + 有界**：出站 JSON 仅含
  schema/tool/milestone/event=`drift-watch-alert`/dispatched_at + 报告
  身份（stem、SHA-256、起止 UTC、project）+ 状态摘要（drift 布尔、
  pass/fail 计数、drift_reasons ≤32 条 + 截断计数显式）+ 分发元数据；
  绝无原始日志/env 值/URL/token/容器体/DB URL/绝对路径/threshold
  detail 文本；ASCII-safe 编码后字节数硬顶（16384，复用 M14-119 界）
  复检。投毒测试：报告 detail 携带 secret 形态 marker → payload/
  ledger/dispatch 报告/stdout 全部零泄漏。
- **sanitized 独立台账（幂等 + 审计）**：
  `drift-dispatch-ledger.jsonl`（默认 gitignored
  `.verify/artifacts/m14-135-drift-watch-alert-dispatch/`）仅在成功
  分发后原子追加（整读 + 追加 + tmp+fsync+os.replace 重写）；行
  schema **全字段严格校验**（supervisor R1 修正）：键集与成功路径
  写入行精确一致（16 键，多键/少键一律拒绝）+ 逐字段类型/值域/固定
  词汇——schema_version=1（bool 不冒充 int）、tool/milestone/
  dispatch_status="sent"/sink="generic-https-webhook" 精确匹配、
  report_sha256/payload_sha256 恰 64 位小写 hex、report_stem 为被
  接受源文件名形态 + 可解析 stamp、dispatch_id 闭式
  `<YYYYMMDD-HHMMSS>-<8 位小写 hex>` + 可解析 stamp、dispatched_at/
  report_started_at_utc 严格 UTC 形态且真实日历时刻、drift 布尔、
  counts 恰 pass/fail 两键非负 int、drift_reasons_count 有界非负
  int、http_status ∈ 200..299、payload_bytes ∈ [1, 16384]；任何
  违规（含他工具台账行）→ `ledger-row-schema` fail-closed。同报告
  SHA-256 重复分发在**发送之前**拒绝（`duplicate-dispatch`，零
  Transport 调用）；台账 malformed 行 fail-closed。
- **失败与半失败恒可见**：失败分发（非 2xx/超时/连接异常）
  `dispatch_status=failed`、绝不入台账、绝不报告为 sent、exit 2；
  成功分发后台账写入失败 → `sent-ledger-unrecorded` 可见 + exit 2
  （事实已发送、幂等面破损如实入档，绝不谎报）。
- **输出工件**：每轮落 sanitized JSON+Markdown 报告
  （`plan-<stamp>-<rand>.json/.md` / `dispatch-<stamp>-<rand>.json/.md`；
  存在性探测 + 递增换名，绝不覆盖既有工件；写盘前 redact_secrets
  终防线）；输出目录 symlink 拒绝；源报告文件 symlink 拒绝。
- **首片刻意最小**：单 sink、单次发送、零重试、零调度集成（调度属
  后续切片）；退出码 0 = plan / execute 成功（含 skipped-no-alerts），
  2 = 一切拒绝。

## 3. 命令（实现者实际执行过的）

```
# 仓库根执行（canonical venv 解释器；全部合成 fixtures，零真实网络/零真实分发）
python -m pytest services/api/tests/test_production_drift_watch_alert_dispatch.py -q
python -m pytest services/api/tests/test_monitoring_alert_dispatch.py \
    services/api/tests/test_monitoring_alert_dispatch_runtime.py \
    services/api/tests/test_production_drift_watch.py \
    services/api/tests/test_production_drift_watch_task.py \
    services/api/tests/test_production_drift_watch_history.py -q
python -m py_compile tools/ops/production_drift_watch_alert_dispatch.py
python -m ruff check tools/ops/production_drift_watch_alert_dispatch.py \
    services/api/tests/test_production_drift_watch_alert_dispatch.py
python tools/ops/production_drift_watch_alert_dispatch.py --help   # 冒烟（零报告读取、零网络）
git diff --check
```

本切片开发期**仅**运行过上述验证命令与 `--help`；从未对任何真实
drift-watch 工件执行本工具、从未加载真实 secret、从未构造真实
Transport、从未联系任何外部端点（含回环接收器——M14-135 无 runtime
回环测试，全部 execute 路径经注入 FakeTransport 锁定）。

## 4. 合成-only 验证（实际执行结果）

- 新契约测试 `test_production_drift_watch_alert_dispatch.py`：**127
  passed**（结构契约 6：import 白名单/零子进程/零 Docker 采集命令
  token、常量单一事实源、M14-119 安全面逐一 `is` 复用、plan 零
  Transport 构造、socket+subprocess 双阻断 plan 端到端、ops README
  文档化；门禁 3；报告校验拒绝 25+：stem/身份/时间戳/文件名时间戳
  容差边界（+2s 收、+5s 拒、先于 stamp 拒）/project/锚点/collectors/
  check 字段/counts↔checks/drift↔fail/reasons 词汇封闭集（放行全集
  + 越域 7 类拒绝参数化）/非空⟺drift/输入硬顶/plan 源报告双模式
  拒绝；SSRF/secret 5；成功路径 4：sent 全量断言、skipped-no-alerts、
  截断计数、回环旗标；HTTP 失败 8；幂等/台账 6：重复前置拒绝、
  malformed/坏字段/他工具行/第二报告追加/sent-ledger-unrecorded；
  输出路径 3；脱敏/固定词汇 6；**台账行全字段严格 schema 44（R1
  修正）：canonical 行接受 1 + 写入行↔校验器读写同构 1 + 逐族越界
  参数化拒绝 40（多余键/缺失键/schema_version=2 或 bool/他工具
  tool/milestone/dispatch_status/sink 词汇/dispatched_at 形态与
  不可能日历/report_started_at_utc/双 sha256 指纹（大写/短长/非
  hex）/counts（缺键/多键/负数/bool/字符串/非 dict）/drift（字符串/
  int）/drift_reasons_count（负/bool/超界）/http_status（500/199/
  字符串/bool）/payload_bytes（0/负/超硬顶/字符串）/report_stem
  （形态/不可解析 stamp）/dispatch_id（畸形/不可解析 stamp/非 hex
  后缀/多余段））+ 端到端 2（多余键、http_status 类型违规——均零
  Transport 调用）**）。
- 邻居回归（canonical venv）：M14-119 双套（contract + loopback
  runtime）+ M14-127 双套（watcher + task）+ M14-133 历史审计 =
  **304 passed**；六套合跑 **431 passed**。
- `py_compile` 通过；ruff（canonical venv，含 ISC/FURB 规则集）
  **All checks passed**；`git diff --check` 干净；新增/修改行秘密
  扫描 0 命中。

## 5. 诚实边界

- 本切片是 **alert delivery 工具就绪第一片**：只证明「给定一份合法
  M14-127 execute 且 drift=true 的报告 + 操作者 secret，工具会以受控
  方式向单一 HTTPS sink 发送一份固定词汇 payload 并留幂等台账」。
  **不证明 alert delivery 已在生产实证**（零真实端点接触、零真实
  分发、零真实 webhook 送达验证——未运行任何回环或外部接收器）、
  **不证明生产调度集成**（刻意未做——属后续切片）、不证明端到端
  「drift 发生 → 告警到达值班人」闭环。
- `production_ready=false` 不变；release-approval 仍是 human-only 门；
  真实 execute 分发仅由 supervisor 在获准窗口运行。
- drift_reasons 固定词汇与 M14-127 `evaluate_drift` 当前产出形态
  绑定：M14-127 词汇演进时本工具校验将 fail-closed 拒绝（需同步
  更新，绝不静默放行）。
- Harmony M14-126 attempt 2 继续 BLOCKED（hdc targets 空、
  127.0.0.1:5555 不可达），不得虚构通过。

## 6. 变更与验证清单

- [x] 新工具 `tools/ops/production_drift_watch_alert_dispatch.py`
      （零子进程/零 Docker 采集/零网络默认/零 env 读取）
- [x] 契约测试 127 项全绿（FakeTransport/FakeClock 注入 only；含
      supervisor R1 修正的台账行全字段严格 schema 44 项聚焦测试）
- [x] 邻居回归 304 项全绿（M14-119 ×2 / M14-127 ×2 / M14-133）
- [x] `py_compile` / ruff（含 ISC/FURB）/ `git diff --check` 全过
- [x] 新增/修改行秘密扫描 0 命中（marker/secret 值零泄漏测试锁定）
- [x] `tools/ops/README.md` 标题链 + M14-135 段
- [x] PROJECT_STATUS / ROADMAP / CHANGELOG 各 M14-135 条目
- [x] 本证据 README（docs-only 事实，绝无运行期产物入库；默认工件
      目录 gitignored）
- [x] 单一本地 commit 于 `ops/m14-135-drift-watch-alert-dispatch`；
      零 push、零 PR、零合并（supervisor 后续步骤）
