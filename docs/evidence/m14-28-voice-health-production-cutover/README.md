# M14-28：语音健康 sidecar 生产切换验收（merge commit 复跑收口）

- 验收窗口：2026-09-15 16:38:56 → 16:59:43 +08:00（Asia/Shanghai）
- 验收基点：主仓 `main@1b6d86235a3ab93f43f11635305d198bcf7d1cf1`（PR #108 merge，
  本地 git 可验证）；验收全程主仓 tracked clean、HEAD 不变（零 commit/零 push/零 PR）
- 原始证据：gitignored `.verify/m14-28-voice-health-production-cutover-rerun-1b6d862/`
  （ACCEPTANCE-REPORT.md + EXECUTION-LOG.md + 16 份 raw 产物，共 18 文件），按纪律
  不入 git、不在本文复制原始日志；更早的 halted 运行证据
  `.verify/m14-28-voice-health-production-cutover/` 未被触碰
- 结论：**9/9 步 PASS——sidecar 切换/生命周期/监控集成范围验收通过**；
  全局 `production_ready=false` 不变，`monitoring_ready=false` 如实保留

## 1. 谱系与 CI（supervisor 验收事实）

| 项 | 值 |
|---|---|
| M14-29 修复 feature commit | `527c454a0b28bcbe4011104b14fddf5bf73f8b14`（分支 `fix/m14-29-sidecar-status-path`） |
| PR #108 merge commit | `1b6d86235a3ab93f43f11635305d198bcf7d1cf1`（parents：`d19c296`（PR #107 merge）+ `527c454`，本地 git 可验证） |
| PR #108 CI run | `34933123493`——5/5 job SUCCESS |
| 合并后 main CI run | `34946366049`——5/5 job SUCCESS |
| 本验收执行点 | merge commit `1b6d862` 上的**整体复跑**（不复用任何 halted 运行步骤结论） |

## 2. 九步验收摘要

| # | 步骤 | 结果 |
|---|------|------|
| 1 | 基线观察（全新、不假设） | ✅ 期望 commit 上干净 stopped 状态：Ubuntu 未运行、引擎定义性停机、无 manifest；晨间中止根因（`unsafe-status-target`：目录被当 `--status-file`）在既有日志确认 |
| 2 | 受控 start | ✅ rc=0；**argv 恒传精确 status 文件**（M14-29 修复经 `/proc/703/cmdline` live 验证）；PID 703；manifest/status 有效且一致；bind `172.25.7.64`（WSL eth0/RFC1918）；18010/18011 单一属主 listener |
| 3 | 无代理 /health 探测 | ✅ 18010/18011 与 `/health/live` 全部**透明 502**（`{"detail":"upstream connection failed"}`，1–11ms）——如实劣化、绝不伪造 200；`127.0.0.1` 控制探测被拒（bind 拓扑正确） |
| 4 | start 幂等 | ✅ rc=0，`幂等跳过（不重复 spawn）` 且如实报 `[502, 502]`；恰一个进程、事实不变 |
| 5 | 受控 stop | ✅ rc=0；**仅回收 PID 703**（优雅 TERM）；listener 清除；manifest/status 清理；引擎与 WSL 发行版零触碰 |
| 6 | 伪造 manifest pid=867 | ✅ **rc=3 硬拒绝**（PID 867 属生产保护清单 FunASR 867/CosyVoice 26008）；零信号；伪造件 sha256 前后一致（`5a170f85…deb4b`）；完全隔离沙箱、canonical 工件零污染 |
| 7 | 重启并保持运行 | ✅ rc=0；PID 701；manifest/status/listener 一致；按要求保持运行供步骤 8 |
| 8 | 真实监控管道（只读 execute） | ✅ 按契约执行（lock acquired/released、固定白名单命令、原子报告）；monitor 完整采集 `partial=false` 并**以 `voice_health_source=sidecar` 从 canonical manifest 派生语音端点**（cutover 实证）；诚实结果 30 ok/2 critical → exit 2 → history/insights 按序 skipped |
| 9 | 终态快照 | ✅ HEAD 不变、porcelain clean；sidecar `running-degraded（[502, 502]）`；引擎零触碰；证据 secret 扫描零匹配 |

## 3. 关键事实

- **端口/拓扑**：sidecar 18010（FunASR 面）/18011（CosyVoice 面）绑定 WSL eth0
  `172.25.7.64`（RFC1918 私网，非 `0.0.0.0`/loopback，Windows loopback 不可达）；
  引擎原生端口 8010/8011 全程无 listener（引擎停机）。Server 头
  `VoiceHealthSidecar/1 Python/3.14.4`。
- **HTTP**：web-root/web-login/api-health 全 200（4.96/11.99/11.47ms）；
  funasr-health/cosyvoice-health **502**（11.20/10.94ms，经 sidecar 透传上游失联）。
- **监控管道**：报告 `pipeline-20260915-085503`（monitor 子工件
  `monitor-20260915-085502.json`，16235 B）；6 个 compose 服务
  （postgres/redis/minio/api/web/livekit）全 healthy、restart 增量 0；
  阈值 30 ok/0 warn/2 critical（两个 critical 即两 voice 502）→
  `overall_status=critical`、`monitoring_ready=false` → monitor `EXIT_CRITICAL=2`
  → `pipeline_rc=1`；history/insights 按序列契约 skipped。
- **保护语义**：PROTECTED_PIDS（FunASR 867/CosyVoice 26008）先于一切探测；
  伪造 manifest 指向 867 时结构化拒绝（rc=3、零信号、证据保全）。
- **上下文**：同日 16:45 计划任务轮同样 monitor exit 2（非本复跑引起、也未被掩盖）。

## 4. fail-closed 语义（不是工具缺陷）

pipeline overall failed 是**引擎停机窗口的诚实结果**：monitor 把 sidecar 透传的
502 如实判为 critical，管道自身行为完全符合契约（重叠锁、序列语义、失败可见
不遮蔽、原子报告）。M14-28 的通过判据是 sidecar cutover/生命周期/监控集成
范围，该范围 9/9 PASS——**不能把 pipeline overall failed 说成 M14-28/M14-29
失败**。

## 5. 明确不 claims 的边界（诚实口径）

- **不宣称语音链路全绿**：FunASR/CosyVoice 在验收窗口内处于 stopped（验收政策
  不启动引擎）；sidecar 对两个语音端点如实返回 502，监控 2 个 critical 即来源
  于此。受控启动引擎（经其自身托管路径）与全绿复验是**独立后续任务**。
- `monitoring_ready=false` 如实保留；全局 `production_ready=false` 不变。
- 引擎从未被本验收启动/停止/发信号；Docker Desktop/容器、WSL 发行版、
  wslrelay、CC Switch、本地代理零触碰。
- 证据全目录 secret 模式扫描零匹配（sk-/ghp_/gho_/AKIA/xoxb-/bearer/api_key=）。

## 6. 复现命令（摘要）

```
.venv/Scripts/python.exe tools/voice/voice_health_sidecar_control.py start   # 步骤 2/7（rc 0）
curl.exe --noproxy '*' -m 10 -i http://172.25.7.64:18010/health              # 步骤 3（502 透明）
.venv/Scripts/python.exe tools/voice/voice_health_sidecar_control.py stop    # 步骤 5（rc 0）
.venv/Scripts/python.exe tools/voice/voice_health_sidecar_control.py \
  --artifacts-dir <隔离沙箱> stop                                            # 步骤 6（rc 3 受保护拒绝）
.venv/Scripts/python.exe tools/ops/monitoring_pipeline.py \
  --execute --confirm "EXECUTE READ-ONLY MONITORING PIPELINE"                # 步骤 8（rc 1 诚实失败）
```

完整命令行/输出/时序见 gitignored EXECUTION-LOG.md 与 `raw/step1`–`step9`。
