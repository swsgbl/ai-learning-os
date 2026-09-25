# M14-132：M14-129 drift watch 任务三次连续自然调度轮证据回填

## 1. 结论与边界

M14-132 是 docs-only 证据回填切片：把 M14-129（production drift watch
独立周期任务）在 2026-09-25 本地时间 **12:30、12:45、13:00** 的**三次
连续自然调度轮**固化为可审计仓库证据。三轮全部自然发生、全部
`drift=false`、全部 LastTaskResult=0。

- 本切片零代码、零测试、零配置、零 CI/workflow、零调度器改动、零
  Docker、零生产服务/DB/MinIO/语音/secret/设备变更；**不运行 drift
  watcher、不运行 scheduler 管理器、不触发任务**；对计划任务只做
  PowerShell 只读查询，对 gitignored 自然轮工件只做只读核验。
- **三轮连续自然成功仅证明「任务已安装 + 45 分钟窗口内三个连续
  PT15M 槽位自然调度端到端 ok」**：不证明长期稳定性、不证明持续调度
  可靠性、不证明夜间无人值守可靠性，不证明机器重启/Docker 重启/仓库
  重建后任务仍生效，不构成 `production_ready` 宣称，不解除任何
  release gate；`production_ready=false` 不变，release-approval 仍
  human-only。
- 序列第一轮（12:30）与 M14-131 已回填的首轮自然调度为**同一轮**；
  本切片将其纳入三连序列独立复核，并对 12:45 / 13:00 两轮做首次
  回填。M14-131 证据 README 保持原样，不改写历史。
- Harmony M14-126 attempt 2 保持 BLOCKED（hdc targets 空、
  127.0.0.1:5555 不可达）；本切片零移动端动作，不虚构进展。

## 2. 基点与前置事实（supervisor 验证 + 本地 git 复核）

| 项 | 值 |
|----|----|
| 本切片基点 | main `7f2a52863cea627a9e80c2dc9eabc41bfa40e63c`（= 当前 origin/main = PR #219 merge（M14-131 证据合入）；canonical main 同 SHA 复核一致） |
| M14-131 feature commit | `84a5bd5`（docs(m14-131) 证据回填，PR #219 head；本地 git log 复核） |
| 前置安装 | M14-131：supervisor 2026-09-25 以非提升 shell 真实 install 成功（status=installed/exact-owned），安装后基线 NextRunTime=`2026-09-25 12:30:00 +08:00`、LastTaskResult=`267011`（从未运行哨兵） |
| canonical 卫生 | 本切片全程 canonical 只读；既有未跟踪 canonical `.claude/` 未触碰 |

## 3. 计划任务只读核验（本切片，零触发）

本切片以 PowerShell `Get-ScheduledTask` + `Get-ScheduledTaskInfo`
对 `AIOS-Production-Drift-Watch` 做**只读**查询（2026-09-25 第三轮
结束后、13:15 槽位前；无 `/Run`、`/Change`、`/End`、`/Create`、
`/Delete`，不运行 schtasks 任何变更面）：

| 字段 | 值 |
|------|----|
| State | `Ready` |
| LastRunTime | `2026-09-25 13:00:01`（+08:00，= 第三轮报告 started_at） |
| LastTaskResult | `0`（成功） |
| NumberOfMissedRuns | `0` |
| NextRunTime | `2026-09-25 13:15:00`（+08:00，下一 PT15M 槽位） |

调度器侧证据链与工件侧吻合：LastRunTime 恰为第三轮自然执行时刻
（13:00:01），LastTaskResult=0 且 MissedRuns=0 表明截至查询时刻三个
已过槽位（12:30/12:45/13:00）无缺失、末轮成功。

## 4. 三轮自然性声明

对 M14-132 全程（以及 M14-131 安装至本轮窗口内），**零**以下操作：

- 零 `schtasks /Run`、`/Change`、`/End`（无任何手动触发或参数变更）；
- 零任务重装/卸载（install/uninstall 均未发生；任务注册面保持
  M14-131 安装后的 installed/exact-owned 状态）；
- 零 Docker mutation（无 stop/start/restart/rm/kill/down/up/build/
  pull/exec）、零容器重启、零生产服务重启；
- 零生产部署（无镜像构建/推送/切换，API/Web 运行 digest 三轮恒定，
  见 §5）；
- 零手动 drift-watch execute。

三轮均由 Task Scheduler 触发器**自然且仅自然**发生：三份报告
`started_at_utc` 恰为三个 PT15M 槽位各 +1 秒（04:30:01Z / 04:45:01Z /
05:00:01Z，= 本地 12:30:01 / 12:45:01 / 13:00:01 +08:00），文件名
时间戳与正文时间戳一致，单轮历时约 1 秒（远低于 PT10M 执行时限与
330s 子进程预算）。

## 5. 自然轮报告工件与独立复核

报告落 canonical gitignored
`.verify/artifacts/m14-127-production-drift-watch/`（本切片只读核验，
不入库、不提交、不外传正文；下表事实均为脱敏摘要/哈希/计数）。本
切片以 `stat -c %s` + `sha256sum` 独立重算六工件字节数与哈希：

| 工件 | 本地轮次 | 字节数 | SHA-256 |
|------|----------|--------|---------|
| `drift-watch-20260925-043001.json` | 12:30 | 11023 | `5A6FA1BAC85772BA6B793BA3792E92FBF0D508C9A220FCF7623F853DEB53A80E` |
| `drift-watch-20260925-043001.md` | 12:30 | 5762 | `8405AA9391AE9F2788FC54794C679ED8885A2E869F733BF90CE4168ACA244AED` |
| `drift-watch-20260925-044501.json` | 12:45 | 11023 | `36EE9147B7191A10E3B02E0D12205D6C0C21CF51DECADAB31D2568298F1F0934` |
| `drift-watch-20260925-044501.md` | 12:45 | 5762 | `E02E5194F390C2BC0403156FCBCE68043B9E9F871DA4B9DBD6251853AAEE8FEF` |
| `drift-watch-20260925-050001.json` | 13:00 | 11023 | `543E091FE1CA1E07C962B08D70318EB6F0BEA2B80FEBAD6031813065C2A3BD7B` |
| `drift-watch-20260925-050001.md` | 13:00 | 5762 | `3C5E274D5CC01C76485B9E57C0138873E2BF3926CF9E6EFBC2BEDC28D46FA7FDF` |

第一轮 JSON/MD 哈希与 M14-131 已回填值逐字节一致（独立复算交叉
印证）。三份 JSON 脱敏事实摘要（逐份核验，三轮完全同构）：

| 字段 | 12:30 轮 | 12:45 轮 | 13:00 轮 |
|------|----------|----------|----------|
| `started_at_utc` | `2026-09-25T04:30:01Z` | `2026-09-25T04:45:01Z` | `2026-09-25T05:00:01Z` |
| `ended_at_utc` | `2026-09-25T04:30:02Z` | `2026-09-25T04:45:02Z` | `2026-09-25T05:00:02Z` |
| `drift` | `false` | `false` | `false` |
| `drift_reasons` | `[]` | `[]` | `[]` |
| checks 计数 | pass=28 / fail=0 | pass=28 / fail=0 | pass=28 / fail=0 |
| 七服务 health | 全 healthy/running | 全 healthy/running | 全 healthy/running |
| API digest | 见下，三轮一致 | 同 | 同 |
| Web digest | 见下，三轮一致 | 同 | 同 |

身份字段三轮一致：`schema_version=1`、
`tool=tools/ops/production_drift_watch.py`、`milestone=M14-127`、
`mode=execute`、project `aios-m14-03-production-rehearsal`、七服务
（postgres/redis/minio/api/web/livekit/searxng）。

**API/Web 镜像锚点三轮恒定**（运行 image_id、image_refs tag 本地解析
digest、config expected_digest 三重一致，且恰为 M14-124 已批准发布
值——逐份逐字段核对无一偏移）：

```
aios/api:m14-124-production = sha256:c99e28c905208bffbc1576f0c5c9e042af18fd356881cee967078ee38323781f
aios/web:m14-124-production = sha256:d596f0c726ab690359b196a3c3911f9842b94218b4361ee452413d420a38194b
```

## 6. 本切片（M14-132）验证记录（docs-only）

全部在本 worktree 或对 canonical 只读执行；**不运行 pytest、不运行
drift watcher / scheduler 管理器 / schtasks 变更面**：

| 验证 | 结果 |
|------|------|
| 基点复核 | canonical HEAD == origin/main == `7f2a528`；worktree 分支 `docs/m14-132-m129-drift-watch-natural-runs` 自该 SHA 精确创建 |
| 计划任务只读查询 | State=Ready / LastRunTime=13:00:01 / LastTaskResult=0 / MissedRuns=0 / NextRunTime=13:15:00（§3） |
| 工件只读复核 | 三 JSON 各 11023 B、三 MD 各 5762 B，六 SHA-256 `stat` + `sha256sum` 独立重算（§5 表）；第一轮与 M14-131 回填值一致 |
| JSON 事实断言 | 三份逐份核验：时间戳、drift=false、drift_reasons 空、28 pass / 0 fail、七服务 healthy+running、API/Web digest 三重一致且恒等于 M14-124 批准值 |
| markdown 结构 sanity | 改动/新增 .md 代码围栏配对平衡、标题层级合法、无 markdown 链接（仓库惯例路径以反引号文本呈现） |
| 秘密扫描 | 改动文本（新增 README、三台账、ops README diff）五类模式扫描 0 命中 |
| `git diff --check` | 干净（含 staged 复核），post-commit tracked worktree clean |

## 7. 诚实边界与未实证事项

- **三轮证明边界**：45 分钟窗口内三个连续 PT15M 槽位自然成功 = 任务
  已安装 + 连续自然调度在观测窗口内 ok。**长期稳定性、夜间无人值守
  可靠性、更长时间跨度（含跨日/跨重启）的持续调度可靠性、机器重启 /
  Docker Desktop 重启 / 仓库重建后任务与 wrapper 的存活、drift=true
  时的告警路径，均未实证**——留待后续自然轮观测或专门切片。
- 本切片不做任何安装/卸载/注册动作，不构成对任何未来调度器变更的
  授权；uninstall / 回滚路径仍只有契约测试覆盖。
- `drift=false` 仅表示各轮采集范围内锚点全部吻合（M14-127 口径），
  不承诺窗口外状态，不构成生产健康/就绪宣称。
- 三轮窗口内生产容器持续运行（无重启），故本轮观测不覆盖「任务在
  生产栈重启后仍正确观测」的情形。
- `production_ready=false`、`release_ready=false` 不变；本切片不接触
  release-readiness / evidence-cockpit / SHA256SUMS canonical。
- Harmony M14-126 attempt 2 继续 BLOCKED，本切片零移动端动作。

## 8. 改动清单（本切片，恰一个 commit）

| 文件 | 类型 |
|------|------|
| `docs/evidence/m14-132-m129-drift-watch-natural-runs/README.md` | 新增（本证据） |
| `tools/ops/README.md` | 修改（M14-129 段末追加三轮自然调度后记） |
| `docs/PROJECT_STATUS.md` | 修改（M14-132 顶部条目） |
| `docs/ROADMAP.md` | 修改（M14-132 状态更新） |
| `docs/CHANGELOG.md` | 修改（Unreleased/Added 顶部条目） |

按任务书要求：commit 后 push 分支并经 GitHub API 开 PR（base 为
`7f2a52863cea627a9e80c2dc9eabc41bfa40e63c`），由 supervisor（Codex）
监督验收。
