# M14-73 长稳审计接入发布门（long soak release gate）证据

分支 `ops/m14-73-long-soak-release-gate`（基于 main@05c7aea，即 PR #159
合并 M14-72 后的 main），单次本地提交 `ops: add long-soak release gate`
（不推送）。

## 交付物

- `tools/ops/soak_stability_audit.py`：`AUDIT_SCHEMA_VERSION` 1→**2**，
  `build_report` 顶层新增 `"gate": "long-soak"` 自声明字段；M14-72
  分类语义/输入拒绝语义/输出确定性完全不变（两次运行输出逐字节相同）。
- `services/api/app/ops/release_readiness.py`：新增必需 GateSpec
  `long-soak`（证据文件 `long-soak.json`，排序在 draft-ownership 之后、
  provider-smoke 之前）+ 严格评估器 `_eval_long_soak`。release-ready
  门槛从十门升至**十一门**（10 必需 + 1 可选 turn-tls），
  `GATE_IDS`/release-approval 哈希绑定自动扩展（approval 必须列出全部
  十一个 gate id + 证据 SHA-256，缺/多/错序即 tampered）。
- `services/api/app/ops/release_closure_manifest.py`：下一步指引更新为
  **七条**占位符命令——新增长稳条目「离线跑 soak 审计后把生成 JSON
  **逐字节复制重命名**为 `<evidence-dir>/long-soak.json`」；manifest
  保持只读聚合器，不重复实现 gate 语义。
- `services/api/tests/test_soak_stability_audit.py`、
  `services/api/tests/test_release_readiness.py`（新增 6b 节：pass/
  pending/blocked/30 例 malformed 矩阵/审批篡改）、
  `services/api/tests/test_release_closure_manifest.py` 更新。
- 文档：`docs/CHANGELOG.md`、`docs/ROADMAP.md`、`docs/PROJECT_STATUS.md`、
  `docs/DEVELOPMENT.md`、`tools/ops/README.md` 与本 README（唯一入库
  证据文件）。

## long-soak 评估器严格校验（fail-closed）

语义检查全部通过后才允许 pass/pending/blocked 映射，任一失配即
`malformed`：

- 报告骨架：18 键全集、`audit_schema_version==2`、顶层
  `gate=="long-soak"`、`tool.path=="tools/ops/soak_stability_audit.py"`。
- 精确策略：`settings` 恰为 window 1440 / interval 15 / max-gap 20 /
  retention 500——**任一策略漂移即 malformed**（防止调宽窗口伪造
  pass）。
- 输入锚定：`input.sha256` 为 64 位 hex 且 `input.bytes>0`；行不变量
  `analyzed + omitted == row_count`、`omitted == max(0, row_count-500)`、
  `selected <= analyzed`。
- 时间窗：`anchor_collected_at - window_start_collected_at == 1440 分钟`
  精确相等。
- 状态计数：`window_status_counts` 键恰为 ok/warn/critical、值非负、
  和 == `selected_row_count`；`window_non_ok_count ∈
  [warn+critical, selected_row_count]`（闭区间下界：非 ok 计数至少含
  warn+critical；上界：至多全窗口非 ok）。
- 分类判定（与工具 if/elif 单原因链**单向蕴含**一致）：
  - `pass` 仅当 reasons 为空 + selected≥97（闭区间 1440//15+1）+
    span==1440 + max_gap≤20 + non_ok==0 + warn==critical==0 +
    ok==selected。
  - `pending` 仅 `insufficient-clean-coverage` 或
    `insufficient-sample-count`（后者必须 selected<97）；pending 报告
    不得携带非干净/阻断类原因。
  - `blocked` 仅 `non-ok-status-in-window` 和/或
    `excessive-gap-in-window`，且单向蕴含成立（blocked+non-ok 原因 →
    non_ok>0；blocked+gap 原因 → max_gap>20）——工具每份报告恰发一条
    原因，验证不要求双向等价。
  - 未知原因/矛盾组合/计数自相矛盾 = `malformed`。

## 证据来源口径（绝不由手工构造）

`long-soak.json` 必须是本工具产物 `soak-audit-report.json` 的**逐字节
复制重命名**（`cp`，不做手工编辑/重序列化）：输入 sha256/bytes 与策略
字段绑定了生成时刻的真实历史，任何改动都会破坏骨架校验或输入锚定。

## 真实历史干跑（canonical，只读）

输入：主仓 canonical gitignored
`.verify/artifacts/m14-13-monitoring-history/history.jsonl`（只读绝对
路径，canonical 仓库零改动；监控仍在持续追加，以下为本次干跑时刻的
快照事实——对比 M14-72 时刻 391 行/571084 bytes/`4e416bbc…b0af4`，
历史在增长属预期）：

- 396 行，578635 bytes，SHA-256
  `65470fc6455c3fd03830002e5de765af0c46c410ca2e635cf55626c8eb7ab83c`
- 锚点（最新样本）`2026-09-20T06:15:01Z`；24h 窗口起点
  `2026-09-19T06:15:01Z`（差恰 1440 分钟）
- 窗口内：入选 97 行，ok=94 / warn=3 / critical=0；最大观测间隔
  15.033 分钟；入选跨度 1439.617 分钟；策略恰 1440/15/20/500
- 结果：**blocked（non-ok-status-in-window），exit 2**——窗口内 3 个
  warn 样本，真实历史不存在连续 24h 干净窗口
- **数据域 blocked 已落盘报告**（worktree gitignored
  `.verify/m14-73-long-soak-release-gate/`）：
  - `soak-audit-report.json`：856 bytes，SHA-256
    `9d2345c99b043432459bc63486a9667bfda3888df17aceb47ab54dc818a4887e`
  - `soak-audit-report.md`：1610 bytes，SHA-256
    `4f0116d236808e17f7eef3836ecdeb965cce908344d294c56de8b69ce3b4d9c7`
  - （两文件为确定性输出：相同输入重跑逐字节相同）
- **评估器直消费验证**：把上述真实 blocked 报告原样喂给
  `_eval_long_soak` → 状态 **blocked**（非 malformed），原因
  「真实 24h 窗口存在稳定性问题： 窗口内 3 个非 ok 样本（warn=3/
  critical=0）」——真实工具产物与门禁语义兼容得到实证。

**诚实结论：canonical 真实历史不存在连续 24h 干净窗口——本次干跑
绝不应也绝未报告 pass，`release_ready=false` 如实。** 绝不合成 soak
pass；待监控攒满真实 24h 干净窗口后离线重跑本工具、逐字节复制为
`long-soak.json` 即可得真实 pass 结论，无需改任何代码。

## 验证（全部在 worktree 内、canonical venv 解释器）

- `pytest -q services/api/tests/test_soak_stability_audit.py
  services/api/tests/test_release_readiness.py
  services/api/tests/test_release_closure_manifest.py` →
  **162 passed**
- `ruff check services/api tools/ops/soak_stability_audit.py` → 通过
- `py_compile tools/ops/soak_stability_audit.py` → 通过
- 真实历史干跑 → exit 2 / blocked / 报告已写出（见上）
- `git diff --check` → 干净

边界：本里程碑全程离线只读——不触碰生产容器/Docker/计划任务/服务/
env 文件/密钥/远端/PR/canonical 主工作树；不声称真实 24h soak 完成，
不构成 production readiness 宣称（`production_ready=false` 不变）。
