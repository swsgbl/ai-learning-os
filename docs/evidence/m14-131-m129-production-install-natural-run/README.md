# M14-131：M14-129 drift watch 任务真实安装与首轮自然调度证据回填

## 1. 结论与边界

M14-131 是 docs-only 证据回填切片：把 M14-129（production drift watch
独立周期任务 readiness 管理器）交付后的两个后来观测事实——**supervisor
真实安装**与**首轮自然调度成功**——固化为可审计仓库证据，并修正开发期
「install 需提升令牌」的早期推论。

- 本切片零代码、零测试、零配置、零 CI/workflow、零调度器改动、零
  Docker、零生产服务/DB/MinIO/语音/secret/设备变更；**不运行 drift
  watcher、不运行 scheduler 管理器**；对 gitignored 自然轮工件只做
  只读核验。
- **一轮自然成功仅证明「任务已安装 + 首轮自然调度端到端 ok」**：不
  证明长期稳定性、不证明持续调度可靠性、不证明机器重启/Docker 重启/
  仓库重建后任务仍生效，不构成 `production_ready` 宣称，不解除任何
  release gate；`production_ready=false` 不变，release-approval 仍
  human-only。
- M14-129 证据 README（`docs/evidence/m14-129-production-drift-watch-task/
  README.md`）的开发期历史陈述保持原样，本切片以同一文件内**明确标注
  日期的生产后记**区分后观测事实，不改写历史。
- Harmony M14-126 attempt 2 保持 BLOCKED（hdc targets 空、
  127.0.0.1:5555 不可达）；本切片零移动端动作，不虚构进展。

## 2. 基点与前置事实（supervisor 验证 + 本地 git 复核）

| 项 | 值 |
|----|----|
| 本切片基点 | main `a12b2acb69d8c8cb5cc91a3fc1a730f846f9516f`（= 当前 origin/main；canonical main 同 SHA 复核一致） |
| a12b2acb 身份 | PR #218 merge（本地 git 复核：parents = `44a5a69ff59acf28cd68531943b8ec45fe8ea88e`（PR #217 = M14-129 合入）+ `f6e8c265cdbf0189ccc1090d312db0b48bec1492`） |
| M14-130 feature commit | `f6e8c265cdbf0189ccc1090d312db0b48bec1492`（docs-only 台账回填，本地 git log 主题 `docs(m14-130): backfill M14-129 ledger entries (docs-only)`） |
| PR #218 CI | PR CI run `36093021969` 五项 job 全部 success（supervisor 验证） |
| 合并后 main CI | push/main CI run `36093610080` 五项 job 全部 success（supervisor 验证） |
| canonical 卫生 | 安装时 canonical 与 origin/main 同为 `a12b2acb`；既有未跟踪 canonical `.claude/` 全程未触碰 |

## 3. 真实安装（supervisor，2026-09-25，非提升 shell）

以下安装事实为 supervisor 在获准窗口执行并验证（本切片不复运行安装
命令）：

- **执行环境**：canonical 主仓库检出（main `a12b2acb`）上的当前
  supervisor shell；安装前 PowerShell 角色检查返回
  `ElevatedAdministrator=False`——**非提升（non-elevated）令牌**。
- **执行命令**（exit 0）：

  ```
  .venv\Scripts\python.exe tools/ops/production_drift_watch_task.py install --confirm "EXECUTE PRODUCTION DRIFT WATCH SCHEDULER CHANGE"
  ```

- **安装后核验**：manager 只读 status 报告 **installed /
  exact-owned**——Action（`wscript.exe //B //Nologo` 指向仓库内
  `run_production_drift_watch_silent.vbs`）、Arguments、WorkingDirectory
  （repo）、Hidden、触发器（过去 StartBoundary 的 TimeTrigger）、
  PT15M 重复间隔、PT10M 执行时限逐项与 M14-129 就绪契约匹配。开发期
  未实证的「TimeTrigger/Repetition/StartBoundary 注册后归一化行为」
  （M14-129 README §4）随之获得真实安装实证。

## 4. 首轮自然调度（零人工触发）

- **安装后、首轮调度前的调度器基线**（supervisor 只读观测）：
  LastRunTime `1999-11-30`（哨兵值：从未运行）、LastTaskResult
  `267011`（`0x41303`：任务尚未运行）、NextRunTime
  `2026-09-25 12:30:00 +08:00`、NumberOfMissedRuns `0`。
- **自然性**：安装与首轮调度之间零 `schtasks /Run`、`/Change`、
  `/End`；零 Docker mutation、零容器重启、零生产服务重启、零手动
  drift-watch execute。首轮运行由 Task Scheduler 触发器**自然且仅
  自然**发生。
- **首轮结果**（2026-09-25 12:30:01 +08:00 自然执行）：
  LastTaskResult=`0`、NextRunTime=`2026-09-25 12:45:00 +08:00`
  （下一 PT15M 槽位）、NumberOfMissedRuns=`0`。
- **事后只读复核**：manager 只读 status 仍为 installed /
  exact-owned——自然轮之后任务注册面无漂移。

## 5. 自然轮报告工件与独立复核

报告落 canonical gitignored
`.verify/artifacts/m14-127-production-drift-watch/`（本切片只读核验，
不入库、不提交、不外传正文；下表事实均为脱敏摘要/哈希/计数）：

| 工件 | 字节数 | SHA-256 |
|------|--------|---------|
| `drift-watch-20260925-043001.json` | 11023 | `5A6FA1BAC85772BA6B793BA3792E92FBF0D508C9A220FCF7623F853DEB53A80E` |
| `drift-watch-20260925-043001.md` | 5762 | `8405AA9391AE9F2788FC54794C679ED8885A2E869F733BF90CE4168ACA244AED` |

本切片以 `wc -c` + `sha256sum` 独立重算两工件字节数与哈希，与上表
（即 supervisor 给定值）逐字节一致；并以 canonical venv Python 对
JSON 做 30 项事实断言，全部通过。脱敏事实摘要：

- 身份：`schema_version=1`、`tool=tools/ops/production_drift_watch.py`、
  `mode=execute`；`started_at_utc=2026-09-25T04:30:01Z`（= 本地
  12:30:01 +08:00，与调度器观测时刻同一事件）、
  `ended_at_utc=2026-09-25T04:30:02Z`（单轮历时约 1 秒，远低于 PT10M
  执行时限与 330s 子进程硬顶预算）。
- 判定：`drift=false`、`drift_reasons=[]`；checks 计数
  **pass=28 / fail=0**（1 collector + 七服务 × 3 + 两锚点 × 3）。
- 七 compose 服务（postgres/redis/minio/api/web/livekit/searxng）
  全部 `health=healthy`、`state=running`；七容器逐项 facts 完整。
- API/Web 镜像锚点三重一致（运行 tag、运行 image ID、锚点 tag 本地
  解析 digest 全部匹配），digest 恰为 M14-124 已批准发布值：

  ```
  aios/api:m14-124-production = sha256:c99e28c905208bffbc1576f0c5c9e042af18fd356881cee967078ee38323781f
  aios/web:m14-124-production = sha256:d596f0c726ab690359b196a3c3911f9842b94218b4361ee452413d420a38194b
  ```

## 6. 对「install 需提升令牌」推论的修正

M14-129 证据 README §4 与 M14-130 台账回填均写有「`schtasks /Create`
需提升令牌（M14-06 生产实证）」。这是开发期从 M14-06（**LogonTrigger**
任务 `AIOS-Production-Recovery`，2026-09-11 在该机实测非提升被 UAC
拒绝）外推的一般性推论。2026-09-25 本机实测构成**后观测事实修正**：

- M14-129 的 **TimeTrigger + InteractiveToken/LeastPrivilege** 任务在
  同机非提升 supervisor shell 一次 `/Create` 成功（与 M14-54 审计归档
  任务 `AIOS-Audit-Archive-Readiness` 2026-09-18 非提权安装成功同形
  态）；
- 本修正**仅陈述本机实测**，不宣称普遍 Windows 行为：`schtasks
  /Create` 是否需要提升随主机 UAC 策略、任务 principal/触发器形态与
  组策略而异，安装前应以只读角色检查确认当前 shell 令牌；
- M14-06 的生产实证（其任务形态在该机需提升）不受影响、不被改写。

落地：`tools/ops/README.md` M14-129 段的 install 注释与段末状态已按
上述口径更新；M14-129 证据 README 以 §7 dated addendum 记录修正，§1–
§6 开发期历史原文保持不变；M14-130 既有台账条目作为历史记录保持
原样，由本切片（M14-131）台账条目作为最新事实源修正其「下一步」
中的提升预期。

## 7. 本切片（M14-131）验证记录（docs-only）

全部在本 worktree 或对 canonical 只读执行；**不运行 pytest、不运行
drift watcher / scheduler 管理器 / schtasks**：

| 验证 | 结果 |
|------|------|
| 基点复核 | canonical HEAD == origin/main == `a12b2acb`；`git cat-file` 复核 merge parents（`44a5a69` + `f6e8c26`） |
| 工件只读复核 | JSON 11023 B / SHA256 `5A6F…A80E`、MD 5762 B / SHA256 `8405…4AED`，`wc -c` + `sha256sum` 独立重算与 supervisor 给定值一致 |
| JSON 事实断言 | 30/30 通过（时间戳、drift、counts、七服务 healthy+running、28 检查全 pass、锚点 tag/digest、image_refs/容器运行 digest 三重一致） |
| markdown 结构 sanity | 改动/新增 .md 代码围栏配对平衡、标题层级合法、无 markdown 链接（仓库惯例路径以反引号文本呈现） |
| 秘密扫描 | 改动文本（含新增 README、三台账、addendum、tools/ops README diff）五类模式扫描 0 命中 |
| `git diff --check` | 干净（含 staged 复核），post-commit tracked worktree clean |

## 8. 诚实边界与未实证事项

- **单轮证明边界**：一次自然调度成功 = 任务已安装 + 首轮自然运行
  ok。长期稳定性、连续调度可靠性（多轮连续 LastTaskResult=0）、机器
  重启 / Docker Desktop 重启 / 仓库重建后任务与 wrapper 的存活、
  drift=true 时的告警路径，均未实证——留待后续自然轮观测或专门切片。
- 安装为 supervisor 获准行为；本切片不构成对任何未来安装/卸载的
  授权。uninstall / 回滚路径仍只有契约测试覆盖，未在真实调度器演练。
- 首轮报告 `drift=false` 仅表示本轮采集范围内锚点全部吻合（M14-127
  口径），不承诺窗口外状态，不构成生产健康/就绪宣称。
- CI run `36093021969` / `36093610080` 的五 job success 为 supervisor
  经 GitHub 核验的事实；本切片仅本地复核 git 侧 merge 关系，未重查
  GitHub Actions。
- `production_ready=false`、`release_ready=false` 不变；本切片不
  接触 release-readiness / evidence-cockpit / SHA256SUMS canonical。

## 9. 改动清单（本切片，恰一个 commit）

| 文件 | 类型 |
|------|------|
| `docs/evidence/m14-131-m129-production-install-natural-run/README.md` | 新增（本证据） |
| `docs/evidence/m14-129-production-drift-watch-task/README.md` | 修改（追加 §7 生产后记；§1–§6 原文保持） |
| `tools/ops/README.md` | 修改（M14-129 段：install 注释与段末状态按非提升实测口径更新） |
| `docs/PROJECT_STATUS.md` | 修改（M14-131 顶部条目） |
| `docs/ROADMAP.md` | 修改（M14-131 状态更新） |
| `docs/CHANGELOG.md` | 修改（Unreleased/Added 顶部条目） |

不 push、不开 PR、不合并（supervisor 只监督验收）。
