# M14-60 Harmony current-main 设备预检（dry-run）证据回填（docs-only）

## 结论

supervisor 已在 current main 口径完成 Harmony 设备预检工具的**只读干跑
验证**（plan 模式零执行 + check 门 fail-closed 拒绝 loopback 目标），本
任务把该验证转为可审计仓库证据（docs-only 回填，2026-09-19 GMT+8）。

- **plan 干跑**（目标 `127.0.0.1:5555`）：mode `plan`、status `planned`、
  exit 0；**2 条命令 planned、0 attempted、0 executed**；
  `hardware_touched=false`。
- **plan 干跑（同目标 + bundle `com.ailearningos.app`）**：**3 planned、
  0 attempted、0 executed**。
- **check 门（同目标）**：**blocked**、exit 2；failures
  `signature_report_unreadable`、`target_loopback_forbidden`；
  **0 条命令执行**——check 门对 loopback 目标不可覆盖 fail-closed 拒绝的
  安全语义在 current main 的行为实证。
- `hdc list targets`：仅 `127.0.0.1:15566` 与 `127.0.0.1:5555` 两个
  loopback 目标——本机无非 loopback 物理设备可供该路径验证。
- 全程 stderr 0 bytes（空文件）。

源码等价：`2839114..1233ef0` 区间恰 2 提交（`7e2d54f` docs(android)
回填 + `1233ef0` PR #142 merge，均为 docs），对 `apps/harmony`、
`tools/harmony_release` 的区间 diff 为**空**——验证所用 Harmony 源码与
本回填基点 `main@1233ef0` 完全一致，预检即 current-main 口径。被验工具
为 PR #139（Add Harmony device read-only preflight）引入、位于
`tools/harmony_release` 的 Harmony 设备只读预检工具。

## 证据文件与完整性锚点

源证据（gitignored，不入库；位于 canonical main 检出
`.verify/m14-60-harmony-current-main-preflight/`）：**5 文件**，逐文件
SHA-256 即完整性锚点（本回填不计算聚合清单哈希）：

```
12bd5454f1407e85b2ed348275822df6a5655415bf5f0328c86594eb6118b496    5067  plan-127.0.0.1-5555.json
854ffa389bd0b9a41c145ca81e004812eaa09022e253b2f3634be953132d4a95    5075  plan-127.0.0.1-5555-with-bundle.json
acaabc5b88671839d35e13d2e6299e0eed456e714790059e113bef3edb618e0c    3100  check-127.0.0.1-5555-failclosed.json
091c7de819c6d78011d2dca2920a54acc0d2e864465579ed7e06280b195ae0f0      33  hdc-list-targets.txt
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855       0  plan-127.0.0.1-5555.stderr.txt
```

- `plan-127.0.0.1-5555.json`（5,067 bytes）：mode `plan`、status
  `planned`、exit 0；2 commands planned / 0 attempted / 0 executed；
  `hardware_touched=false`。
- `plan-127.0.0.1-5555-with-bundle.json`（5,075 bytes）：bundle
  `com.ailearningos.app`；3 planned / 0 attempted / 0 executed。
- `check-127.0.0.1-5555-failclosed.json`（3,100 bytes）：blocked、
  exit 2；failures `signature_report_unreadable`、
  `target_loopback_forbidden`；0 commands executed。
- `hdc-list-targets.txt`（33 bytes）：仅 `127.0.0.1:15566` 与
  `127.0.0.1:5555`。
- `plan-127.0.0.1-5555.stderr.txt`（0 bytes）：空文件，SHA-256 即空串
  标准值 `e3b0c442…b855`。

本 README 为该验证唯一入库证据文件；原始 JSON/txt 产物 gitignored
不入库。

## 语义解读（工具安全设计在 current main 的行为实证）

- 预检工具 plan 模式仅**计划**命令：两次干跑（不带 / 带 bundle）均
  0 attempted / 0 executed、`hardware_touched=false`——零设备命令、
  零硬件触碰。
- check 门对 loopback 目标**不可覆盖地拒绝**（`target_loopback_forbidden`），
  且签名报告不可读（`signature_report_unreadable`）时一并 fail-closed
  （exit 2、0 条命令执行）——在具备可读签名报告与非 loopback 物理设备
  之前，检查路径不放行任何设备命令。

## 边界（不声称）

- **无 AGC / 签名 / 签名 HAP**：签名报告不可读，不声称任何签名链结论。
- **无非 loopback 物理设备**：`hdc list targets` 仅两个 loopback 目标，
  Harmony 真机路径未验证。
- **零设备命令执行**：plan 0 executed、check 0 executed——无设备身份 /
  日志采集、无安装 / 卸载、无运行时验收。
- **零生产访问**：验证与回填均未触碰生产。
- 全局判定不变：**`production_ready=false`**。
- 本回填 docs-only：零设备运行、零生产触碰、零代码改动、不 push、
  不建 PR；分支 `docs/m14-60-harmony-current-main-preflight` 基于
  `main@1233ef0`。
