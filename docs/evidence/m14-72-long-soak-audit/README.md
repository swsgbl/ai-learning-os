# M14-72 长稳审计（long soak stability audit）证据

分支 `ops/m14-72-long-soak-audit`（基于 main@8559c24），单次本地提交
`ops: add monitoring soak stability audit`（不推送）。

## 交付物

- `tools/ops/soak_stability_audit.py`（503 行）：离线只读审计工具——消费
  M14-13 `history.jsonl`（默认 canonical gitignored
  `.verify/artifacts/m14-13-monitoring-history/history.jsonl`，可用
  `--history` 覆盖为文件或目录），判定「真实连续 24 小时稳定窗口」。
  分类 pass（exit 0）/ pending（exit 1）/ blocked（exit 2），全程
  fail-closed；schema 复用 `monitoring_history` 单一事实源（常量/时间戳
  解析/project 白名单/Store/原子写/symlink 拒绝）。
  零子进程/零网络/零计划任务/零 env 读取/零墙钟（窗口起点与报告
  时间戳取自锚样本，两次运行输出逐字节相同）。
  输出确定性 JSON + Markdown 至 gitignored
  `.verify/m14-72-long-soak-audit/`（`--output-dir` 可覆盖）；绝无原始
  日志行/密钥/secret/env 值/URL/token/主机标识。
- `services/api/tests/test_soak_stability_audit.py`（604 行契约测试）
  与 `tools/ops/README.md` 新增 M14-72 文档节。

## 分类语义（数据域 blocked 与输入拒绝分离）

- **数据域 blocked**（warn/critical/partial 在窗口内
  `non-ok-status-in-window`、窗口内间隔超限 `excessive-gap-in-window`）：
  输入本身合法，是有效的审计结论——照常写出确定性 JSON+Markdown
  blocked 报告后 exit 2。
- **输入拒绝**（输入路径缺失/symlink、malformed 行/非法字段、时间戳
  重复、全局非时序、项目冲突、行数超硬顶、参数超界、输出写失败）：
  任何输出写出前即拒绝——零输出文件，exit 2。
- pass 的最小样本数为**闭区间**值 `window // interval + 1`（24h/15m
  即 97，含窗口两端）；96 行即使跨度完整覆盖窗口且间隔全部合规，仍
  pending（`insufficient-sample-count`）。

## pass 判定语义（诚实边界）

pass 仅源于窗口内逐样本 `overall_status=ok` 且 `partial=false` + 覆盖自
窗口起点 + 相邻间隔 ≤ max-gap + 样本数 ≥ 闭区间最小样本数（97）。
**绝不从总历史跨度、insights 聚合、合成 soak 时长或墙钟推导 pass**；
覆盖/样本数不足 = pending（非 pass）；warn/critical/partial/间隔超限 =
blocked（写报告）。**本审计只是长稳审计门禁本身，不构成真实 24h
soak 的完成，也不构成 production readiness 宣称（production_ready=
false）。**

## 真实历史干跑（canonical，只读）

输入：canonical `.verify/artifacts/m14-13-monitoring-history/history.jsonl`
（只读绝对路径，canonical 仓库零改动；监控仍在持续追加，以下为本次
干跑时刻的快照事实）：

- 391 行，571084 bytes，SHA-256
  `4e416bbc745cc41cfde281cfb5cee095369e7ddacb6a7b7ef2268dcd463b0af4`
- 锚点（最新样本）`2026-09-20T05:00:01Z`；24h 窗口起点
  `2026-09-19T05:00:01Z`
- 窗口内：入选 97 行，ok=94 / warn=3 / critical=0（非干净 3）；
  最大观测间隔 15.383 分钟；入选跨度 1440.0 分钟
- 结果：**blocked（non-ok-status-in-window），exit 2**；窗口内最后一次
  warn 为 `2026-09-19T21:15:01Z`，其后仅 465 分钟（7.75 小时）干净
  记录——不足 24h
- **数据域 blocked 已落盘报告**（worktree gitignored
  `.verify/m14-72-long-soak-audit/`）：
  - `soak-audit-report.json`：831 bytes，SHA-256
    `7638359f005fd505ce53705a3e3901fcbd18fda4f739afd8cf64ee373794f48c`
  - `soak-audit-report.md`：1469 bytes，SHA-256
    `a79c50f200ea6a55098bffa65eaef33e3af18d424752abe3dfd212385ef92361`
  - （两文件为确定性输出：相同输入重跑逐字节相同）

**诚实结论：canonical 真实历史不存在连续 24h 干净窗口——真实干跑
绝不应也绝未报告 pass。** 待监控持续运行攒满 24h 干净窗口后重跑即可
得出 pending→pass 的真实结论；无需改本工具。

## 验证（全部在 worktree 内、canonical venv 解释器）

- `pytest -q services/api/tests/test_soak_stability_audit.py
  services/api/tests/test_monitoring_history.py` → **169 passed**
- `ruff check services/api tools/ops/soak_stability_audit.py` → 通过
- `py_compile tools/ops/soak_stability_audit.py` → 通过
- 真实历史干跑 → exit 2 / blocked / 报告已写出（见上）
- `git diff --check` → 干净

边界：本里程碑全程离线只读——不触碰生产容器/Docker/计划任务/服务/
env 文件/密钥/远端/PR/canonical 主工作树。
