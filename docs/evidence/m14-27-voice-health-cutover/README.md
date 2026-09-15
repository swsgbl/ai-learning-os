# M14-27：语音健康监控切换 sidecar（code/contract cutover）

- 切片：`feat/m14-27-voice-health-cutover`，基于 `ec60a093`（PR #106 merge，
  本地 git 可验证）。**本切片仅做代码/契约切换，全部变更留在工作区未提交**，
  待 supervisor 审查后再行本地 commit 与 remote 发布（push/PR/合并）。
- 变更面（3 改 + 1 新增）：`tools/ops/production_monitor.py`（+187/−13：
  `--voice-health-source` 旗标 + canonical manifest 严格校验 + sidecar 端点
  派生/校验）、`tools/ops/monitoring_pipeline.py`（+2/−1：monitor argv 白名单
  固定 sidecar 形态）、`services/api/tests/test_monitoring_pipeline.py`
  （+7/−1：白名单契约同步）、新增 `services/api/tests/
  test_m14_27_voice_health_cutover.py`（308 行，35 项契约测试，未跟踪）。
  零改动 M14-26 sidecar/控制器、relay、引擎、compose。
- 背景：M14-26 §7 明确「监控侧端点切换（M14-25 建议 ③）留待其合并后由监控
  配置收口」。本切片即该收口——监控语音健康采集自 wslrelay loopback 直采
  （M14-25 实证不稳：12:00–13:30 七轮 overall failed，两 voice 端点 Windows
  侧 5s 超时而 WSL 内引擎存活）切换为经 M14-26 窄代理 sidecar 的 manifest
  派生端点。**仅代码/契约层切换，不含任何生产执行。**

## 1. production_monitor.py（--voice-health-source）

- **默认 `loopback` 既有行为不变**：取值 `loopback|sidecar`，默认
  `loopback`——固定五端点画像直采 8010/8011，既有调用零改动。
- **sidecar 仅读 canonical M14-26 manifest 常量**：
  `SIDECAR_MANIFEST_PATH = .verify/artifacts/m14-26-voice-health-sidecar/
  sidecar-manifest.json`（由 `voice_health_sidecar_control` start 原子落盘；
  恒为模块常量，无 CLI 路径参数、无注入面）。
- **清单严格校验（逐项 fail-closed，固定错误类别，绝不回显文件内容或
  异常串）**：符号链接→拒；缺失→拒；非普通文件→拒；不可读（OSError）→拒；
  `schema_version≠1`、`service≠"voice-health-sidecar"`、pid 非正整数、
  ports 非恰 `[18010, 18011]`、bind 非字面 IPv4 或非 RFC1918（10/8、
  172.16/12、192.168/16；显式排除 unspecified/loopback/link-local）→各自
  拒绝。
- **64 KiB 体积双检**（`SIDECAR_MANIFEST_MAX_BYTES=65536`）：读前按
  `stat().st_size` 预检（超顶零读取，绝不把失控文件读入内存）+ 读后按
  `len(data)` 复核（防 stat/读取间漂移）。
- **sidecar 语音 URL 恒为 `http://<bind>:18010/health` 与
  `http://<bind>:18011/health`**：`build_sidecar_endpoints` 派生（固定
  五端点顺序，web/api 原样保留）+ `validate_sidecar_voice_url` 二道校验
  （http-only、字面 IPv4 RFC1918、显式端口恒 18010/18011、精确路径
  `/health`、无 query/fragment/userinfo）。
- **web/api 端点恒字面 loopback 不变**（127.0.0.1 固定端口，原校验原样）。
- **无回退**：任一清单失败发生在报告写入与 Runner/Transport 构造之前，
  固定词表拒绝（EXIT_USAGE）——**零采集/零报告/零回退 loopback**。
  `REPORT_BOUNDARIES` 同步改写为三分表述（GET-only 总述 / web-api 恒字面
  loopback / sidecar 恒 manifest 派生 RFC1918:18010-18011 精确 `/health`，
  no loopback fallback）。

## 2. monitoring_pipeline.py

固定 monitor argv 白名单加入 `--voice-health-source sidecar`（唯一放行
形态）；显式 `--voice-health-source loopback`（回退）与旗标缺值形态均列入
拒绝样例——pipeline 侧结构性禁止回退。

## 3. 验证（2026-09-15，开发回合实测）

解释器：`D:\AI Learning OS\ai-learning-os\.venv\Scripts\python.exe`
（Python 3.11）。工作目录：本 worktree 仓库根。

| 项 | 结果 |
|----|------|
| M14-27 契约套件（`test_m14_27_voice_health_cutover.py`，35 项） | **35 passed** |
| 聚焦回归（监控族三套合计） | **332 passed** |
| 最终口径（监控家族六套件最终回归） | **635 passed** |
| supervisor 独立复验 | 六套件最终回归 635 passed、聚焦 332 passed；`ruff check services/api`；`py_compile` 两工具（production_monitor/monitoring_pipeline）；`git diff --check`——**全部通过** |

## 4. 边界（诚实口径）

- **零真实 WSL/零 HTTP/零 Docker/零生产进程**：全部行为经 fake/契约测试
  锁定；**sidecar 未启动**；**生产监控/管道未切换、未执行、未观察**——
  本回合产出的是可被 M14-28 验收的代码与契约，不是运行时事实。
- 生产切换与端到端验证（实跑 monitor/pipeline、真实 manifest、经 sidecar
  通道实采语音健康）**留待 M14-28 受控生产验收**（获准窗口）。
- `production_ready=false`；不构成语音链路 production readiness 宣称。
