# M14-142 Harmony 后端冒烟重复周期包装器 Stage 1 证据

## 范围

- 分支 `harmony/m14-142-backend-smoke-repeat`（worktree
  `m14-142-harmony-backend-smoke-repeat`，基于 `c2f0a27e`），单 local
  commit，不 push、不开 PR。
- 交付物：`tools/harmony_release/backend_smoke_repeat.py` +
  `tests/harmony_release/test_backend_smoke_repeat.py`（38 项）+ 本证据
  + DEVELOPMENT/ROADMAP/PROJECT_STATUS/CHANGELOG 台账条目。

## 工具契约（要点）

- 复用 `backend_smoke.py`（M14-84）为子进程；零 UI 驱动逻辑复制。
- cycles 1..5（默认 1）；计划模式（无 `--confirm-mutation`）零子进程；
  突变模式顺序执行、首个非零结果 fail-stop（后续周期记 planned）。
- 每周期独立 `cycle_<n>` 证据子目录；聚合 JSON + Markdown 原子写
  （temp + `os.replace`），写失败整体 failure。
- 结构性白名单：仅仓库内 `tools/harmony_release/backend_smoke.py`，
  argv 列表、无 shell；周期记录脱敏 argv（仅选项名，未知 token →
  `<value>`）、return code、秒数、相对证据引用；绝不记录
  secret/环境值/宿主绝对路径（`<repo-root>` 占位）。
- 退出码：0 ok/计划；1 任一周期失败或报告写失败；2 blocked
  （cycles 越界/白名单拒绝/证据目录不可用，零 spawn）。

## 验证记录（canonical venv，2026-09-23）

- 聚焦契约：`pytest tests/harmony_release/test_backend_smoke_repeat.py`
  → **38 passed**（周期边界、计划模式零子进程、三成功周期、第二周期
  非零 fail-stop、malformed 子结果（空/非 JSON/数组/null）、白名单
  拒绝（子 CLI 缺失/注入 resolver）、argv 无 shell、报告原子写顺序
  JSON→MD、写失败 fail-closed、无 .tmp 残留、脱敏（argv/路径/
  Markdown/结果 blob）、聚合退出码 0/1/2、单元（validate_cycles/
  atomic_write））。
- 全量：`pytest tests/harmony_release` → **562 passed, 1 skipped**
  （skip 为 Windows 宿主既有幂等跳过，与基线一致）。
- ruff `--select F,E9,W605`：All checks passed。
- `py_compile`（实现 + 测试）：通过。
- `git diff --check`：干净；新增行秘密/本地绝对路径扫描：0 命中。

## 诚实边界

本切片为 Stage 1 实现/测试/文档：**未启动模拟器、未启动后端、未构建
HAP、零设备/零网络/零生产接触**——不构成任何真实运行时或发布声明；
真实三周期模拟器执行（install→UI→home→uninstall × 3，聚合报告与
fail-stop 语义的真机验证）属 Stage 2。`production_ready=false` 不变。
