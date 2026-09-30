# M14-194：production monitor Web basePath 对齐

## 结论

| 项 | 结果 |
|---|---|
| 分支 / 基点 | `ops/m14-194-production-monitor-basepath`，基于 `50bd66a8c5695bae76917cc3fc2f686f72133687`；单 local commit，不 push、不开 PR |
| root 构建 | `--web-base-path ""`（CLI 默认）探测 `http://127.0.0.1:3012/` 与 `/login` |
| basePath 构建 | `--web-base-path /aios` 探测 `http://127.0.0.1:3012/aios` 与 `/aios/login` |
| 非法值 | 尾斜杠、query、fragment、深路径、大小写差异等在报告写入与采集前拒绝；错误输出不含原值 |
| sidecar 组合 | Web 保留当前 basePath，FunASR/CosyVoice 替换为 manifest 派生的 18010/18011 `/health` |
| 管道链路 | monitor 固定 argv 逐 token 包含 `--web-base-path /aios`；plan/execute 报告记录 basePath |
| 聚焦测试 | `test_production_monitor.py + test_monitoring_pipeline.py + test_m14_27_voice_health_cutover.py + tests/ops`：**557 passed** |
| 静态验证 | Ruff、compileall、`git diff --check`、新增行 secret 扫描、U+FFFD 扫描均通过 |

## 实现面

1. `tools/ops/production_monitor.py`
   - Web host/port 统一为 `127.0.0.1:3012`。
   - `WEB_BASE_PATHS` 仅含空串与精确 `/aios`；`build_endpoint_profile`
     生成 root 或 `/aios` 的五端点画像。
   - `build_sidecar_endpoints` 只替换语音端点，非语音端点来自当前 basePath
     画像。
   - `config.web_base_path` 入 JSON/Markdown 报告；所有生成 URL 仍走
     `validate_target_url`，sidecar 语音 URL 走专用校验。
2. `tools/ops/monitoring_pipeline.py`
   - monitor 白名单命令追加固定 `--web-base-path /aios`。
   - `command_identity("monitor")` 与 pipeline `config.monitor_web_base_path`
     同步记录该事实。
3. 契约测试覆盖 root/`/aios`、非法值零回显、`--only` 子集、FakeTransport
   host/port/path、sidecar 与 basePath 组合、pipeline argv/report 精确匹配。

## 验证命令

全部命令在专用 worktree 下用 canonical venv 解释器
`D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe` 执行：

```powershell
& "D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe" -m pytest `
  services/api/tests/test_production_monitor.py `
  services/api/tests/test_monitoring_pipeline.py `
  services/api/tests/test_m14_27_voice_health_cutover.py `
  tests/ops
```
结果：**557 passed in 5.10s**。
分项收集数：production monitor 256、monitoring pipeline 100、M14-27
sidecar 37、`tests/ops` 164。

Ruff 覆盖修改的 Python 工具与测试；compileall 覆盖同一批 Python 文件。
`git diff --check` 通过；314 条新增/证据行 secret 形态扫描 0 命中，
U+FFFD 扫描 0 命中。

## 诚实边界

- 本证据只覆盖合成测试与静态验证；未运行真实 production monitor execute，
  未请求 3012，未触碰 Docker/生产容器/计划任务。
- root 兼容是显式默认，不代表当前生产管道；生产管道固定 `/aios`。
- `production_ready=false` 不变；本修复不声明生产健康或发布门解除。
