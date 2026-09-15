# M14-31 语音引擎受控恢复验收（FunASR/CosyVoice + sidecar 状态回升 + 单轮只读监控全绿）

- 验收窗口：2026-09-15 22:56–23:08（+08:00），单回合顺序执行
- 仓库基点：`main@4d6e5c8fdde991941019e162c5472c570d2b7049`（任务前后同一 HEAD，`git status --porcelain` 计数 = 0，tracked 全程 clean）
- 原始证据：主仓库 gitignored `.verify/m14-31-voice-engine-recovery/`（ACCEPTANCE-REPORT.md、EXECUTION-LOG.md 与 `raw/` 16 个文件）——不入 git，本 README 仅摘录判定事实
- 结论：**PASS（10/10，本任务验收口径）**；判定依据见下表，每一项均有 raw 原始证据支撑

## PASS 判定表

| # | 验收项 | 要求 | 实测 | 判定 |
|---|---|---|---|---|
| 1 | FunASR 引擎 | managed-running | PID 3445，port 8010 listening（属主=3445），本地 /health **200**（sensevoice） | ✅ |
| 2 | CosyVoice 引擎 | managed-running | PID 3851，port 8011 listening（属主=3851），本地 /health **200**（Fun-CosyVoice3-0.5B-2512） | ✅ |
| 3 | sidecar PID 不变 | 保持 701 | 701（started_at 16:50:38+08 未变，未重启/未停止） | ✅ |
| 4 | sidecar 双端点 200 | 18010/18011 /health 200 | **200 / 200**，另 18011 `/health/live` **200**（alive+ready）；state `running-degraded [502,502]` → **`running-healthy`** | ✅ |
| 5 | 管道 rc | 0 | PIPELINE-RC=**0**（单次 EXECUTE，23:05:22–24） | ✅ |
| 6 | monitor 阈值 | 0 critical，目标 34/0/0 | **34 ok / 0 warn / 0 critical**，alerts=[]，partial=false | ✅ |
| 7 | history | ok + 刷新 | status ok；history.jsonl 追加本轮记录，`artifact_sha256` 与本轮 monitor SHA256 一致（mtime 23:05:23） | ✅ |
| 8 | insights | ok + 刷新 | status ok；`source_sha256` 与刷新后 history.jsonl 一致，`generated_from_newest_at=2026-09-15T15:05:22Z`（mtime 23:05:24） | ✅ |
| 9 | git tracked clean | porcelain=0 | HEAD `4d6e5c8`，porcelain_count=**0**（任务前后一致） | ✅ |
| 10 | secret 扫描 | 零实质命中 | 9 类模式 × 证据全目录 = **0 命中**（仅计数，未打印匹配） | ✅ |

## 基线与启动时序

基线（22:56）：双引擎 `stopped`（8010/8011 无监听）；sidecar PID **701**（bind 172.25.7.64、[18010,18011]）`running-degraded`（/health 探测 [502,502]）；compose 六服务（web/livekit/api/redis/postgres/minio）全部 `Up 22 hours (healthy)`。

两引擎均仅经允许的受管托管路径 `tools/voice/voice_service_control.py start` 启动（manifest 由工具原子写入、归属核验 ok）：

| 引擎 | start rc=0 时刻 | PID | 达健康耗时 | 中间相位 |
|---|---|---|---|---|
| FunASR | 22:56:53 | 3445 | **78s**（`managed-running` + /health 200） | managed-starting(health 000)，模型自本地 modelscope-cache 加载（无重下载） |
| CosyVoice | 22:59:21 | 3851 | **279s**（/health 200，20 分钟预算内） | 149s 时 `managed-running` + health **503**（服务进程已监听、模型仍在加载——工具文档明示的正常启动期相位，终态 200） |

终态（23:07:56 复核）：双引擎 `managed-running`（PID 3445 / 3851）本地 8010/8011 health 200（funasr `{"status":"ok","device":"cpu","models_loaded":["sensevoice"]}`、cosyvoice `{"status":"ok","model":"Fun-CosyVoice3-0.5B-2512"}`）；sidecar PID 仍 701 `running-healthy`，18010/18011 `/health` 与 18011 `/health/live` 三探针均 200（alive+ready）；compose 六服务 `Up 22 hours (healthy)` 无重启；git HEAD `4d6e5c8` porcelain=0。

## 单轮只读监控管道与工件哈希链

单次 EXECUTE（23:05:22–23:05:24，`tools/ops/monitoring_pipeline.py --execute --confirm …`）：**rc=0**、`overall_status=ok`，monitor → history → insights 三步全部 `status=ok exit_code=0`（锁 pipeline.lock acquired/released）。

- monitor：**34 ok / 0 warn / 0 critical**，alerts=[]，partial=false；端点 web-root 200、web-login 200、api-health 200、**funasr-health 200（13.4ms）**、**cosyvoice-health 200（3.2ms）**（语音端点经 M14-26 sidecar 旁路 + M14-27 `voice_health_source=sidecar` 派生，健康恢复即自动回到 200）
- 工件（8 个，全记录于 raw/pipeline-artifacts.txt）关键哈希链：
  - `monitor-20260915-150522.json`（16189 B）SHA256 `81d6c28341c13aec4223d55b0da30afeda00c6103852bc2c20b7a2785bbeb861`
  - `history.jsonl`（173582 B，mtime 23:05:23）SHA256 `9c1f2ee4235ecb256810957918ea0f754e235b4465a882c3ed9d17d19125f4e0` —— 末记录 `artifact_sha256` = 上述 monitor SHA（history 引用 monitor）
  - `insights.json`（15313 B，mtime 23:05:24）SHA256 `3412dfe93ec267b1a3249a3e5d4f9e6bbfca3b5c1816093056a59c6656ed6e98` —— 头部 `source_sha256` = 上述 history SHA（insights 引用 history），sample_count=128

## secret 扫描

23:08:24，9 类模式（openai-key/aws-key/github-token/slack-token/jwt/private-key/bearer/apikey-assign/secret-assign）对整个证据目录扫描 **0 命中**（仅记录计数，未打印任何匹配内容）。Codex 独立复核亦 0 命中。

## 如实披露与诚实边界

1. **23:00:01 周期轮非本任务动作**：管道 restart_evaluation 的对比基线引用了 `monitor-20260915-150001`（23:00:01+08 的一次 monitor 记录）——本任务只执行了 23:05:22 一次管道，该轮次疑为周期性监控，其存在不影响本轮判定，如实披露、不计入本任务动作。
2. **503 为启动期中间相位**：cosyvoice 启动期出现 `managed-running` + health 503（服务进程已监听、模型仍在加载），属工具文档明示的正常行为，终态 200——非故障、非回归。
3. **单轮全绿 ≠ 长期稳定**：本 PASS 仅覆盖本任务列出的验收项；单轮恢复 + 单轮监控全绿**不证明长期稳定性、不构成全局 `production_ready=true` 的依据**，生产就位状态的升降级仍以 PROJECT_STATUS 既有口径为准（全局 `production_ready=false` 不变）。
4. **monitoring_ready 语义**：本轮 monitor 报告 `monitoring_ready=true`（单轮值——仅采集完整且零 warn/critical 的逐轮字段，≠ production ready）；不据此改写任何全局状态。
5. 未执行任何失败分支：无需保留半成功状态（两引擎均成功且保持运行）。

## 边界遵守声明

未 stop/restart sidecar 或引擎（sidecar PID 701 全程未重启/未停止）；未绕过受管生命周期或改写 manifest（manifest 均为 start 命令自身生成）；未触碰 Docker/WSL/wslrelay/CC Switch/代理/计划任务（docker/netstat/probe 均只读）；未修改 tracked 文件或开 PR；健康探测一律零代理（`--noproxy '*'`/工具内建零代理 opener）；全程未输出任何 key/token/env 值；所有证据仅落 `.verify/m14-31-voice-engine-recovery/`。
