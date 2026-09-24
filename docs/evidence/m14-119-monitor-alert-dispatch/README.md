# M14-119：监控告警外发分发最小闭环（实现/测试/文档切片）

- 切片：分支 `ops/m14-119-monitor-alert-dispatch`（独立 worktree
  `m14-119-monitor-alert-dispatch`，基于 main
  `3d2874b9fa84941b81a8ee424376ad7cfa9153a6`（PR #205 merge = M14-118
  合入，精确基点）），单次 local commit（不推送、不建 PR）。
- 目标：为既有生产监控家族（M14-12 production_monitor → M14-13
  history → M14-15 insights → M14-14 pipeline）补上**缺失的最小外部
  告警分发路径**——新工具 `tools/ops/monitoring_alert_dispatch.py` +
  契约测试 `services/api/tests/test_monitoring_alert_dispatch.py`。
  复用权威 monitor 报告 schema 与既有 alerts，**绝不重复阈值、绝不
  重分类健康**；fail-closed、可审计、幂等。
- **诚实边界（置顶）：本切片开发全程未联系任何真实外部端点**
  （zero real external endpoints contacted）——全部 execute 路径验证
  经注入 FakeTransport 完成（构造上零网络：出站 HTTP 仅经注入
  Transport，真实 Transport 在测试中绝不构造；socket+subprocess 双
  阻断下 plan 端到端照常成功）。真实外发（含回环 test-only 接收器
  联调）留待 supervisor 获准窗口。**`production_ready=false` 恒不变**；
  本工具不构成任何 release gate 的解除，不授权任何部署。

## 1. 工具契约（tools/ops/monitoring_alert_dispatch.py）

单文件、纯标准库（复用同仓 `monitoring_history` Store/常量与
`production_monitor` 异常分类/脱敏终防线——单一事实源，零平行
schema）；Store/Transport/Clock 全注入；真实 Transport 仅 execute
分支构造。

1. **双模式**：默认 **plan/validate**——零网络、零分发（Transport
   绝不构造，计数工厂测试结构性证明），读取并严格校验既有 monitor
   报告 + 分发判定 + secret 文件与 webhook URL 校验（全本地只读）；
   **execute** 需 `--execute` + `--confirm "EXECUTE MONITOR ALERT
   DISPATCH"`（一字不差）+ `--secret-file` 三者齐备，缺一即 exit 2
   且零 Transport 构造。
2. **只消费权威 schema**：报告身份/时间戳/stem 白名单复用
   `monitoring_history` 常量；校验身份 + 自洽性（counts ↔ alerts、
   overall ↔ counts/partial 重算对账——只对账不重判）；**分发判定
   = 既有 alerts 含 warn/critical 或 `overall_status=incomplete`**；
   ok 且零告警 → `skipped-no-alerts`（成功路径 exit 0，零发送零台账）。
3. **单一通用 HTTPS webhook sink**：URL 与可选 Bearer token 恒来自
   操作者 secret 文件（`{"url": ..., "token"?...}`；允许键集合恰为
   `{url, token}`，未知键拒绝；大小硬顶双检；gitignored 由操作者负
   责），绝不来自代码/git/evidence/日志/测试 fixture。URL fail-closed
   校验：生产恒 https；http 仅经显式 `--allow-loopback-http` test-only
   旗标放行且**仅限字面回环 IP**（DNS 名称/私网/公网 IP 恒不豁免）；
   字面回环/RFC1918/链路本地/未指定/组播/保留 IP（含
   169.254.169.254 元数据面）与 `localhost` 名称一律拒绝；userinfo/
   query/fragment/坏端口/无 host 拒绝。**URL 与 token 绝不回显、绝
   不进入任何输出**（stdout/报告/台账/payload，测试逐一断言）。
4. **payload 版本化 + 固定词汇 + 有界**：出站 JSON 仅含
   schema_version(1)/tool/milestone/event/dispatched_at + 报告身份
   （白名单 stem + SHA-256 + canonical collected_at）与状态摘要
   （overall_status/partial/counts/`check_id:subject:severity` 形态
   alert codes ≤64 条、超界截断计数显式）。**绝无**原始日志/env 值/
   端点/token/容器体/DB URL/绝对本地路径/阈值 detail 文本。ASCII-safe
   紧凑编码，字节数硬顶 16 KiB 复检。
5. **fail-closed 面**：malformed 报告（身份/时间戳/类型/字段越界/
   counts-alerts 不自洽/overall 不自洽）、stem 非法、缺失/oversize/
   invalid-JSON secret、unsafe URL、webhook 非 2xx（含 3xx——不跟随
   重定向）/超时/连接异常、输出目录 symlink（自身+现存祖先）、报告
   工件名碰撞（探测 + 递增换名，绝不覆盖既有工件）、台账 malformed
   行/字段非法/重复记账——一律固定词汇可见拒绝。**失败的分发恒可见
   （`dispatch_status=failed` + http_status/transport_error 类别）且
   绝不入台账、绝不报告为 sent**；成功后台账写入失败的事实照实入档
   （`sent-ledger-unrecorded`，exit 2——绝不谎报完整成功）。
6. **sanitized 分发台账（幂等 + 审计）**：固定名
   `dispatch-ledger.jsonl`（gitignored 工件目录），仅在成功分发后原
   子追加（整读 + 追加 + tmp+fsync+os.replace 重写）；行 schema 版
   本化 + 固定词汇键集（dispatch_id/report_stem/report_sha256/
   overall_status/counts/alert_codes_count/dispatch_status/sink/
   http_status/payload_sha256/payload_bytes）；**同报告 SHA-256 已
   sent → `duplicate-dispatch` 拒绝且零 Transport 调用**（幂等门先
   于发送）。每轮另落 `dispatch-<stamp>-<CSPRNG 后缀>.json/.md` 报告
   （存在性探测 + 递增换名防碰撞）。
7. **首片刻意最小**：单 sink、单次发送、**零重试**（无退避/队列/
   重试风暴）、无 SMTP/Alertmanager/UI/调度集成。webhook 请求超时
   0.5–30s（默认 10s）；`http.client` 直连从不读取 proxy 环境变量
   （结构性旁路）；响应体不读取（取状态码即关）。
8. 退出码：0 plan / execute 成功（含 skipped-no-alerts）；2 一切
   拒绝（含分发失败）。

## 2. 验证（全部基于合成 fixture + 注入 FakeTransport/FakeClock——零真实网络）

- 聚焦契约测试：`python -m pytest
  services/api/tests/test_monitoring_alert_dispatch.py` → **76 passed**
  （结构契约 5：源码 import 白名单（禁 requests/urllib.request/
  urlopen/httpx/env 值读取）+ 常量单一事实源 + plan 零 Transport 构
  造计数工厂 + socket/subprocess 双阻断 plan 端到端 + ops README 文
  档化；门禁 2：execute 精确短语（缺/近似/多余空白参数化）+ 超时
  数值界；报告校验 15：缺失/stem 非法参数化/not-json/身份字段参数
  化/partial 类型/threshold 缺失/overall 词汇/counts-alerts 不自洽/
  overall 不自洽/alert 字段越界参数化/alerts 非列表；SSRF/unsafe
  URL 23：http 未豁免/回环未豁免/ftp/userinfo/query/fragment/
  localhost/RFC1918×3/链路本地元数据/未指定/组播/保留/IPv6 回环/
  IPv6 链路本地/坏端口×2/无 host 参数化 + 豁免面收紧（旗标下仅字面
  回环 IP）+ 公网 https 接受 + secret url 非法零 Transport；secret
  文件 2：缺失/8 类非法载荷参数化（均零 Transport）；成功路径 5：
  2xx sent（POST 恰一次、Authorization 头、payload 结构、ledger 恰
  一行、报告 sent 且无 URL/token）+ incomplete 报告分发 + ok 零告警
  skip + alert codes 界与截断计数 + loopback test-only 端到端；
  HTTP 失败 8：非 2xx（500/503/404/302/301）+ 异常（timeout/
  refused/OSError）→ failed、ledger 零追加、exit 2；幂等/台账 4：
  duplicate 拒绝零 Transport + ledger malformed 行拒绝 + 字段非法
  拒绝 + 不同报告正常追加；输出路径 3：防碰撞绝不覆盖既有工件（marker
  逐字节未动）+ 输出目录 symlink 拒绝 + 报告文件 symlink 拒绝；
  脱敏 4：投毒 detail marker 绝不进 payload/台账/报告/stdout +
  secret 值绝不回显 stdout + payload 键集精确 + ledger 行键集精确；
  plan 1：plan 报告落盘 planned 且台账零写入）。
- 真实 CLI smoke（canonical venv、合成 fixture、临时目录）：plan
  模式 exit 0（判定「需要分发」、plan 报告落盘、stdout 无 URL/
  token）；execute 门禁错误短语 exit 2 零分发。
- 监控家族回归：`test_monitoring_alert_dispatch.py` +
  `test_production_monitor.py` + `test_monitoring_history.py` +
  `test_monitoring_insights.py` + `test_monitoring_pipeline.py` +
  `test_monitoring_pipeline_task.py` +
  `test_monitoring_history_query.py` +
  `test_monitoring_threshold_calibration.py` → 全绿（零既有行为改动
  ——本切片未触碰任何既有工具源码）。
- ruff（默认 + F,E9）/ py_compile / `git diff --check` / 新增行
  秘密扫描全净（见 §4）。

## 3. 诚实边界

- **未联系任何真实外部端点**（包括未对回环起接收器做真实 HTTP 交
  换）——真实 Transport 代码路径（http.client 直连）的运行时行为未
  在本切片被实证，只经契约锁定其构造面与结果处理面；首次真实外发
  留待 supervisor 获准窗口。
- webhook URL 的 SSRF 防护覆盖**字面 IP 面**与 `localhost` 名称；
  DNS 主机名不做解析 pin——解析后落私网的 TOCTOU 面不在本切片防护
  范围（诚实边界；后续切片可按需引入解析后校验或 IP pin）。
- 台账追加为「整读 + 追加 + 原子重写」；单机单操作者语义下的幂等门，
  无跨进程互斥（与仓库既有 gitignored 工件面假设一致——pipeline
  lock 类互斥留待调度集成切片再议）。
- 无重试语义是**刻意**的：失败即 exit 2 可见，重发由操作者对**新**
  报告（或人工归档台账后）显式决策——绝不自动风暴。
- 不解除 provider-smoke/long-soak/任何 release blocker；
  `release_ready=false` / `production_ready=false` 恒不变。

## 4. 验证命令清单（supervisor 可复跑）

```bash
cd <worktree>
# 聚焦契约测试
<venv>/Scripts/python.exe -m pytest services/api/tests/test_monitoring_alert_dispatch.py -q
# 监控家族回归（本切片涉及的监控家族套件）
<venv>/Scripts/python.exe -m pytest services/api/tests/test_production_monitor.py \
  services/api/tests/test_monitoring_history.py \
  services/api/tests/test_monitoring_insights.py \
  services/api/tests/test_monitoring_pipeline.py \
  services/api/tests/test_monitoring_pipeline_task.py \
  services/api/tests/test_monitoring_history_query.py \
  services/api/tests/test_monitoring_threshold_calibration.py \
  services/api/tests/test_monitoring_alert_dispatch.py -q
# lint / 编译 / 空白
<venv>/Scripts/python.exe -m ruff check tools/ops/monitoring_alert_dispatch.py \
  services/api/tests/test_monitoring_alert_dispatch.py
<venv>/Scripts/python.exe -m ruff check --select F,E9 tools/ops/monitoring_alert_dispatch.py \
  services/api/tests/test_monitoring_alert_dispatch.py
<venv>/Scripts/python.exe -m py_compile tools/ops/monitoring_alert_dispatch.py
git diff --check
```
